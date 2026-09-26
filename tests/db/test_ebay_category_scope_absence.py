"""Review r2 N1: an unprovably complete eBay category scope never drives absence delisting.

Owner invariant: never enable delist semantics on an unprovably complete scope.
A CPU sweep capped at one page whose page carries `next` (or any other
_category_verdict defect) is incomplete however long it keeps polling; before
the fix, LocalCollectionProvider mapped it to stale-absence-eligible TRUNCATED
and apply_delist marked the omitted listing ABSENT_STALE once the 6h grace and
scope continuity were met. Evidence expiry (the 6h DR-008 TTL read by
Listing.objects.active()) is what hides such a stale offer instead.

Bodies are synthetic (no live network), in the shapes the adapter's Browse
parser reads; the legacy drive GET is always one truncated page so the NULL
scope never delists here either.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from datetime import timedelta
from typing import cast

import httpx
import pytest
from django.db import transaction
from django.utils import timezone

from hw_radar.acquisition.contracts import DelistScope, NullResolver
from hw_radar.acquisition.pipeline import run_source
from hw_radar.acquisition.sources.ebay import (
    _TOKEN_CACHE,  # pyright: ignore[reportPrivateUsage]
    DELIST_ABSENCE_GRACE,
    CategorySweep,
    EbayAdapter,
)
from hw_radar.acquisition.stages import apply_delist
from hw_radar.catalog.models import (
    DelistReason,
    Listing,
    RetentionClass,
    ScopeSweepContinuity,
    ScraperRun,
    SourceConfig,
)

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

# The production shape the finding names: one page, EPYC 7763 in category 164.
CPU = CategorySweep(
    slug="cpu", query_id="epyc-7763-164", category_id="164", q="EPYC 7763", max_pages=1
)
ABSENT = "cpu-b"

TOKEN_BODY = {"access_token": "SYNTH-TOKEN", "expires_in": 7200}


def _page(keys: Sequence[str], *, total: int, more: bool = False) -> dict[str, object]:
    body: dict[str, object] = {
        "itemSummaries": [
            {
                "itemId": k,
                "title": f"AMD EPYC 7763 listing {k}",
                "itemWebUrl": f"https://www.ebay.com/itm/{k}",
                "price": {"value": "900.00", "currency": "USD"},
                "itemLocation": {"country": "US"},
            }
            for k in keys
        ],
        "total": total,
    }
    if more:
        body["next"] = "https://api.ebay.com/buy/browse/v1/item_summary/search?offset=200"
    return body


# Every incomplete verdict a one-page CPU sweep can reach with listings on it.
INCOMPLETE_PAGES = {
    "page_cap": _page(["cpu-a"], total=450, more=True),
    "total_exceeds_seen": _page(["cpu-a"], total=3),
}


class Browse:
    def __init__(self, cpu_page: dict[str, object]) -> None:
        self.cpu_page = cpu_page

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            return httpx.Response(200, json=TOKEN_BODY)
        if request.url.params.get("category_ids") is None:
            return httpx.Response(200, json=_page(["drive-a"], total=50))
        return httpx.Response(200, json=self.cpu_page)

    def adapter(self) -> EbayAdapter:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return EbayAdapter(client=client, category_sweeps=[CPU])


@pytest.fixture(autouse=True)
def ebay_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("EBAY_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("EBAY_API_BASE", "https://api.ebay.com")
    _TOKEN_CACHE.clear()
    yield
    _TOKEN_CACHE.clear()


@pytest.fixture(scope="module")
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    lo = asyncio.new_event_loop()
    asyncio.set_event_loop(lo)
    yield lo
    asyncio.set_event_loop(None)
    lo.close()


def _run(loop: asyncio.AbstractEventLoop, browse: Browse) -> ScraperRun:
    run, _ = loop.run_until_complete(
        run_source(
            browse.adapter(),
            NullResolver(),
            retention_class=EbayAdapter.retention_class,
            expires_policy=EbayAdapter.expires_policy,
        )
    )
    return run


def _cpu_outcome(run: ScraperRun) -> dict[str, object]:
    entries = cast("list[dict[str, object]]", run.detail_json["scopes"])
    (outcome,) = (e for e in entries if e["scope_key"] == CPU.scope_key)
    return outcome


def _age_out_absent_and_backdate_continuity() -> None:
    # Everything the stale path asks for, and then some: ABSENT unseen (and
    # its TTL lapsed, as it would be in production) for longer than the
    # grace, and the scope polled continuously for a whole day.
    Listing.objects.filter(source_listing_key=ABSENT).update(
        last_seen=timezone.now() - DELIST_ABSENCE_GRACE - timedelta(hours=1),
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    ScopeSweepContinuity.objects.filter(collection_scope=CPU.scope_key).update(
        continuous_since=timezone.now() - timedelta(hours=24)
    )


@pytest.mark.parametrize("reason", sorted(INCOMPLETE_PAGES))
def test_listing_absent_from_incomplete_cpu_scope_is_never_delisted(
    loop: asyncio.AbstractEventLoop, reason: str
) -> None:
    browse = Browse(_page(["cpu-a", ABSENT], total=2))
    first = _run(loop, browse)
    assert _cpu_outcome(first)["complete"] is True
    assert Listing.objects.get(source_listing_key=ABSENT).collection_scope == CPU.scope_key

    browse.cpu_page = INCOMPLETE_PAGES[reason]
    _age_out_absent_and_backdate_continuity()
    # Several runs past the grace, so no single run's timing can explain a pass.
    for _ in range(3):
        run = _run(loop, browse)
        outcome = _cpu_outcome(run)
        assert (outcome["complete"], outcome["reason"], outcome["delisted"]) == (
            False,
            reason,
            0,
        )
        # The scope was genuinely polled, so continuity is still recorded;
        # it simply can no longer authorize anything for this scope.
        assert outcome["continuity"] == "recorded"
        assert run.detail_json["listings_delisted"] == 0

    absent = Listing.objects.get(source_listing_key=ABSENT)
    assert absent.delisted_at is None
    assert absent.delist_reason != DelistReason.ABSENT_STALE
    row = ScopeSweepContinuity.objects.get(collection_scope=CPU.scope_key)
    assert row.continuous_since is not None
    assert row.continuous_since < timezone.now() - DELIST_ABSENCE_GRACE
    # Expiry, not a delist, is what stops showing the stale offer.
    active = Listing.objects.active().filter(collection_scope=CPU.scope_key)
    assert set(active.values_list("source_listing_key", flat=True)) == {"cpu-a"}


def test_listing_absent_from_complete_cpu_scope_is_delisted_at_once(
    loop: asyncio.AbstractEventLoop,
) -> None:
    browse = Browse(_page(["cpu-a", ABSENT], total=2))
    _run(loop, browse)

    browse.cpu_page = _page(["cpu-a"], total=1)
    run = _run(loop, browse)

    outcome = _cpu_outcome(run)
    assert (outcome["complete"], outcome["reason"], outcome["delisted"]) == (True, "complete", 1)
    assert Listing.objects.get(source_listing_key=ABSENT).delist_reason == (
        DelistReason.ABSENT_FROM_SWEEP
    )


def test_apply_delist_refuses_an_incomplete_scope_without_stale_absence() -> None:
    # The stage-level guard on its own, for a caller that bypasses the gate:
    # continuity and grace are both amply met, yet nothing is marked.
    site = SourceConfig.objects.get(source_site__normalized_name="ebay").source_site
    now = timezone.now()
    listing = Listing.objects.create(
        source_site=site,
        source_listing_key=ABSENT,
        canonical_url=f"https://www.ebay.com/itm/{ABSENT}",
        url_hash=ABSENT.ljust(64, "0"),
        title_raw="AMD EPYC 7763",
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_at=now + timedelta(hours=1),
        collection_scope=CPU.scope_key,
    )
    Listing.objects.filter(pk=listing.pk).update(last_seen=now - timedelta(hours=12))
    scope = DelistScope(
        seen_keys=frozenset({"cpu-a"}),
        observed_at=now,
        complete=False,
        absence_grace=DELIST_ABSENCE_GRACE,
        scope_key=CPU.scope_key,
        stale_absence_allowed=False,
    )
    with transaction.atomic():
        assert apply_delist(site, scope, continuous_since=now - timedelta(hours=24)) == 0
    listing.refresh_from_db()
    assert listing.delisted_at is None
