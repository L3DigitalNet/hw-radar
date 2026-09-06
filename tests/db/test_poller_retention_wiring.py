"""Scheduled polls must carry the adapter's DR-001 retention onto persisted rows.

The gap these pin is invisible to the adapter's own tests: tests/db/test_source_ebay.py
passes retention_class/expires_policy to run_source by hand, so it stays green even
when the POLLER — the only caller in production — forgets them and run_source falls
back to indefinite merchant_fact. eBay evidence written that way never expires and
the DR-001 sweeper can never reclaim it, breaking IR-002/DR-008.
"""

import asyncio
from collections import Counter
from collections.abc import Iterator
from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from hw_radar.acquisition import sources
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.acquisition.sources.ebay import (
    _TOKEN_CACHE,  # pyright: ignore[reportPrivateUsage]
    EbayAdapter,
)
from hw_radar.catalog.management.commands.purge_expired import SweepReport
from hw_radar.catalog.models import (
    LifecycleState,
    Listing,
    OfferSnapshot,
    RetentionClass,
    SourceConfig,
)
from hw_radar.poller.service import (
    build_scheduler,
    load_schedules,
    poll_source,
    recovery_probe_job,
    retention_sweep_job,
)

# transaction=True: run_source writes from sync_to_async threads.
# serialized_rollback preserves the migration-0005 seed (see test_pipeline.py).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

ITEM_KEY = "v1|110500000001|0"
# USD-only so the run needs no seeded FX rate; the retention assertions below do
# not depend on anything else in the body.
SEARCH_BODY: dict[str, object] = {
    "itemSummaries": [
        {
            "itemId": ITEM_KEY,
            "title": "Seagate Exos X18 18TB Recertified SAS",
            "itemWebUrl": "https://www.ebay.com/itm/110500000001",
            "price": {"value": "199.99", "currency": "USD"},
            "shippingOptions": [{"shippingCost": {"value": "0.00", "currency": "USD"}}],
            "itemLocation": {"country": "US"},
            "seller": {"username": "diskdeals_us"},
        }
    ],
    "total": 1,
}
TOKEN_BODY = {"access_token": "SYNTH-TOKEN", "expires_in": 7200, "token_type": "Application"}


@pytest.fixture(autouse=True)
def ebay_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Creds are OpenBao-injected at runtime; tests supply throwaway values.
    # _TOKEN_CACHE is process-global, so a token minted here must not leak.
    monkeypatch.setenv("EBAY_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("EBAY_API_BASE", "https://api.ebay.com")
    _TOKEN_CACHE.clear()
    yield
    _TOKEN_CACHE.clear()


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            return httpx.Response(200, json=TOKEN_BODY)
        if request.url.path == "/buy/browse/v1/item_summary/search":
            return httpx.Response(200, json=SEARCH_BODY)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _register_ebay(monkeypatch: pytest.MonkeyPatch) -> BucketRegistry:
    adapter = EbayAdapter(client=httpx.AsyncClient(transport=_transport()))
    monkeypatch.setitem(sources.ADAPTERS, "ebay", lambda: adapter)
    registry = BucketRegistry()
    registry.configure_source("ebay", rate_per_min=60.0, burst=3, now_s=0.0)
    return registry


def _ebay_config() -> SourceConfig:
    return SourceConfig.objects.select_related("source_site").get(
        source_site__normalized_name="ebay"
    )


def _assert_bounded_ebay_rows() -> None:
    listing = Listing.objects.get(source_listing_key=ITEM_KEY)
    assert listing.retention_class == RetentionClass.EBAY_LISTING_OBSERVATION
    expires_at = listing.expires_at
    assert expires_at is not None, "no TTL: the poller dropped the adapter's expires_policy"
    # DR-008 caps the window at 6h from the batch's fetch time, which is moments
    # before now — a generous lower bound keeps the assertion clock-robust.
    assert timedelta(hours=5) < expires_at - timezone.now() <= timedelta(hours=6)
    snapshot = OfferSnapshot.objects.get(listing=listing)
    assert snapshot.retention_class == RetentionClass.EBAY_LISTING_OBSERVATION
    assert snapshot.expires_at is not None


def test_scheduled_full_poll_persists_bounded_ebay_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _register_ebay(monkeypatch)
    SourceConfig.objects.filter(source_site__normalized_name="ebay").update(
        enabled=True, lifecycle_state=LifecycleState.ACTIVE
    )
    scheduler = build_scheduler(registry, load_schedules([_ebay_config()]))
    asyncio.run(poll_source("ebay", registry, scheduler))
    _assert_bounded_ebay_rows()


def test_retention_sweep_job_logs_redaction_counts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # CR-004 redaction is destructive merchant-content removal and must be
    # observable per cycle; sweep_expired is stubbed so the assertion pins the
    # log line's shape rather than re-deriving a real redaction scenario
    # (already covered by tests/db/test_purge_expired.py and test_listing_delist.py).
    report = SweepReport(
        dry_run=False,
        counts=Counter({"catalog.OfferSnapshot": 2}),
        redactions=Counter({"catalog.Listing": 1}),
    )
    monkeypatch.setattr("hw_radar.poller.service.sweep_expired", lambda: report)

    with caplog.at_level("INFO", logger="hw_radar.poller.service"):
        asyncio.run(retention_sweep_job())

    assert any(
        "redacted" in record.getMessage() and "'catalog.Listing': 1" in record.getMessage()
        for record in caplog.records
    )


def test_recovery_probe_persists_bounded_ebay_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    # The ADR-0017 probe replays the full pipeline against a paused source, so it
    # persists real rows and needs the same forwarding as the scheduled poll.
    registry = _register_ebay(monkeypatch)
    SourceConfig.objects.filter(source_site__normalized_name="ebay").update(
        enabled=True, lifecycle_state=LifecycleState.PAUSED_PENDING_FIX
    )
    asyncio.run(recovery_probe_job(registry))
    _assert_bounded_ebay_rows()
