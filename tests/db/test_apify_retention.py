"""MS-2 Slice D task D11: source retention of remote imports (MS2-D-25).

A remote import has no adapter to read a retention from, so the registry
(acquisition.retention_policy) is its only source. These tests pin the three
properties the plan names: a bounded source's import stamps its class and
expiry on every row it writes and the DR-001 sweeper then removes them all; an
unregistered source is rejected rather than persisted under the merchant_fact
default; and the registry agrees with every local adapter.

Every Apify call is served by an httpx.MockTransport, so no test touches the
network.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Coroutine
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import httpx
import pytest
from django.utils import timezone

from hw_radar.acquisition import retention_policy
from hw_radar.acquisition.apify.client import ApifyClient
from hw_radar.acquisition.apify.importer import import_provider_run
from hw_radar.acquisition.contracts import AdapterRetention, NullResolver, adapter_retention
from hw_radar.acquisition.retention_policy import SOURCE_RETENTION, source_retention
from hw_radar.acquisition.sources import ADAPTERS
from hw_radar.catalog.management.commands.purge_expired import sweep_expired
from hw_radar.catalog.models import (
    Category,
    Listing,
    OfferSnapshot,
    ProviderRun,
    RawPayload,
    RetentionClass,
    SourceSite,
    Watch,
    WatchEvaluation,
)
from hw_radar.catalog.models.provider import AdmissionClass, ImportState
from hw_radar.eligibility.requirements import DriveRequirementSpec, save_requirement

# transaction=True: the importer writes from sync_to_async threads.
# serialized_rollback=True keeps the migration-seeded SourceSite and Category rows.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

FIXTURE_DIR: Final = Path(__file__).resolve().parents[1] / "fixtures" / "apify_contract" / "v1"
SITE: Final = "synthetic"
ACTOR: Final = "hw-radar-synthetic-collector"
DATASET: Final = "ds-d11-retention"
KV_STORE: Final = "kv-d11-retention"
TTL: Final = timedelta(hours=6)

# A stand-in bounded class with a 6 h TTL. Not eBay's class: eBay listings are
# delete-on-delist, which the sweeper keeps and redacts rather than deletes, and
# this test pins that every row of the import is removed.
BOUNDED: Final = AdapterRetention(
    retention_class=RetentionClass.AMAZON_EPHEMERAL,
    expires_policy=lambda at: at + TTL,
)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _fixture() -> dict[str, Any]:
    # Retargeted at the seeded `drive` category so the production evaluator
    # binds a real watch and writes WatchEvaluation rows.
    fixture = json.loads((FIXTURE_DIR / "complete.json").read_text(encoding="utf-8"))
    fixture = copy.deepcopy(fixture)
    for row in fixture["datasetItems"]:
        row["categoryHint"] = "drive"
    return fixture


def _transport(fixture: dict[str, Any]) -> httpx.MockTransport:
    rows = fixture["datasetItems"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/v2/datasets/{DATASET}/items":
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            return httpx.Response(
                200,
                json=rows[offset : offset + limit],
                headers={"X-Apify-Pagination-Total": str(len(rows))},
            )
        if request.url.path == f"/v2/key-value-stores/{KV_STORE}/records/OUTPUT":
            return httpx.Response(200, json=fixture["output"])
        return httpx.Response(500, json={"error": {"type": "unexpected", "message": "path"}})

    return httpx.MockTransport(handler)


def _provider_run(site: SourceSite, fixture: dict[str, Any], started_at: datetime) -> ProviderRun:
    admitted = fixture["admitted"]["queryScope"]
    return ProviderRun.objects.create(
        source_site=site,
        external_run_id="run-d11-retention",
        import_idempotency_key="apify:run-d11-retention",
        actor_ref=ACTOR,
        contract_schema_version="hw-radar-run/v1",
        query_scope=admitted,
        scope_key=admitted["collectionScope"],
        memory_mb=256,
        timeout_s=120,
        max_items=admitted["maxItems"],
        max_pages=admitted["maxPages"],
        admission_class=AdmissionClass.WATCH_REFRESH,
        admitted_at=started_at - timedelta(minutes=1),
        remote_status="SUCCEEDED",
        started_at=started_at,
        dataset_id=DATASET,
        kv_store_id=KV_STORE,
        storage_cleanup_due_at=started_at + TTL / 2,
    )


def _import(row: ProviderRun, fixture: dict[str, Any]) -> ImportState:
    client = ApifyClient("apify_api_d11_test_token", transport=_transport(fixture))
    return _run(
        import_provider_run(row.pk, client=client, actor_name=ACTOR, resolver=NullResolver())
    )


@pytest.fixture
def site() -> SourceSite:
    return SourceSite.objects.create(name="Synthetic", normalized_name=SITE)


def test_bounded_source_import_retains_listings_snapshots_raw_and_evaluations(
    site: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Registered through the table itself, so the provider, the importer, and
    # anything else that asks source_retention() all see the bounded class.
    monkeypatch.setattr(
        retention_policy, "SOURCE_RETENTION", MappingProxyType({**SOURCE_RETENTION, SITE: BOUNDED})
    )
    watch = Watch.objects.create(name="d11", category=Category.objects.get(slug="drive"))
    save_requirement(watch, DriveRequirementSpec(max_unit_price_usd=Decimal("1000.00")))
    fixture = _fixture()
    started = timezone.now().replace(microsecond=0) - timedelta(minutes=10)
    row = _provider_run(site, fixture, started)

    assert _import(row, fixture) is ImportState.FINALIZED

    expires = started + TTL
    rows = len(fixture["datasetItems"])
    listings = Listing.objects.filter(source_site=site)
    snapshots = OfferSnapshot.objects.filter(listing__source_site=site)
    raw = RawPayload.objects.filter(endpoint__startswith=f"apify-dataset:{DATASET}/")
    evaluations = WatchEvaluation.objects.filter(watch=watch)
    stamped = RetentionClass.AMAZON_EPHEMERAL.value
    for label, rows_of in (
        ("listing", listings),
        ("snapshot", snapshots),
        ("raw payload", raw),
        ("evaluation", evaluations),
    ):
        assert rows_of.count() == rows, label
        assert set(rows_of.values_list("retention_class", "expires_at")) == {(stamped, expires)}, (
            label
        )

    report = sweep_expired(now=expires + timedelta(seconds=1))

    assert report.total > 0
    assert not listings.exists()
    assert not snapshots.exists()
    assert not raw.exists()
    assert not evaluations.exists()


def test_unregistered_source_import_rejected_not_merchant_fact() -> None:
    unregistered = SourceSite.objects.create(
        name="Unregistered", normalized_name="d11-unregistered"
    )
    fixture = _fixture()
    row = _provider_run(unregistered, fixture, timezone.now() - timedelta(minutes=10))

    assert _import(row, fixture) is ImportState.REJECTED

    row.refresh_from_db()
    assert row.stage_detail["reject_reason"] == "unknown_retention"
    # Never persisted under run_collection's merchant_fact default: nothing at all.
    assert row.dataset_read_count == 0
    assert not Listing.objects.filter(source_site=unregistered).exists()
    assert not OfferSnapshot.objects.filter(listing__source_site=unregistered).exists()
    assert not RawPayload.objects.filter(endpoint__startswith=f"apify-dataset:{DATASET}/").exists()


@pytest.mark.parametrize("site_key", sorted(ADAPTERS))
def test_registry_matches_every_local_adapter_retention(site_key: str) -> None:
    # The local path reads adapter_retention(adapter) in MS-2 and a remote
    # import reads the registry; if they drifted, the same site's rows would
    # expire differently depending on who collected them.
    assert source_retention(site_key) == adapter_retention(ADAPTERS[site_key]())
