"""MS2-D-30 ED-02: a current-eligible observation bumps last_seen exactly as the MS-1 upsert did.

Listing.last_seen is auto_now, and the stale-absence cut-off reads it. The
guarded save writes an explicit field list, so a list that forgot last_seen
would silently stop the bump; the frozen tests backdate last_seen by hand and
cannot see that. These tests pin it on the import path (the stages the
importer composes) and on the local path (run_source end to end).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from ordering_support import HOUR, T0, listing, make_site, observe, record

from hw_radar.acquisition.contracts import (
    DelistScope,
    NullResolver,
    ParsedListing,
    RawBatch,
    RawItem,
)
from hw_radar.acquisition.pipeline import run_source
from hw_radar.catalog.models import (
    Listing,
    RunKind,
    RunStatus,
    SchedulingLane,
    SourceLaneState,
    SourceSite,
)

# transaction=True: run_source writes from sync_to_async threads.
# serialized_rollback=True: truncation would otherwise drop the seeded sites.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

SITE = "d10seen"
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


class _Adapter:
    """Local adapter whose sweep is truncated (complete=False) with a one-hour grace."""

    name = "d10seen"
    site_key = SITE
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0

    def __init__(self, keys: list[str], *, fetched_at: datetime | None = None) -> None:
        self.keys = keys
        self.fetched_at = fetched_at

    async def fetch(self) -> RawBatch:
        return RawBatch(
            source=self.name,
            fetched_at=self.fetched_at or datetime.now(UTC),
            items=[RawItem(url="https://seen.invalid/page", payload_json={"k": self.keys})],
        )

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return [
            ParsedListing(
                source_listing_key=key,
                url=f"https://seen.invalid/{key}",
                title="Drive",
                price=Decimal(100),
                raw_url="https://seen.invalid/page",
            )
            for key in self.keys
        ]

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope:
        return DelistScope(
            seen_keys=frozenset(p.source_listing_key for p in parsed),
            observed_at=batch.fetched_at,
            complete=False,
            absence_grace=HOUR,
        )


def _backdate_last_seen(site: SourceSite, key: str) -> None:
    Listing.objects.filter(source_site=site, source_listing_key=key).update(last_seen=LONG_AGO)


@pytest.mark.parametrize("path", ["import", "local"])
def test_current_eligible_observation_bumps_last_seen_and_ineligible_does_not(path: str) -> None:
    site = make_site(SITE)
    now = timezone.now()
    newer, older = now, now - HOUR
    if path == "import":
        observe(site, newer, [record("x")])
    else:
        asyncio.run(run_source(_Adapter(["x"], fetched_at=newer), NullResolver()))

    _backdate_last_seen(site, "x")
    if path == "import":
        observe(site, older, [record("x")])
    else:
        asyncio.run(run_source(_Adapter(["x"], fetched_at=older), NullResolver()))
    assert listing(site, "x").last_seen == LONG_AGO

    if path == "import":
        observe(site, newer + timedelta(seconds=1), [record("x")])
    else:
        asyncio.run(run_source(_Adapter(["x"]), NullResolver()))
    assert listing(site, "x").last_seen > LONG_AGO


def test_listing_observed_in_previous_run_is_not_stale_delisted_within_grace() -> None:
    site = make_site(SITE)
    asyncio.run(run_source(_Adapter(["x", "y"]), NullResolver()))
    # Establish long polling continuity, and make x look long unseen before run N.
    SourceLaneState.objects.filter(
        source_config__source_site=site, lane=SchedulingLane.FULL
    ).update(continuous_since=T0)
    _backdate_last_seen(site, "x")

    run_n, _ = asyncio.run(run_source(_Adapter(["x", "y"]), NullResolver()))
    run_n1, _ = asyncio.run(run_source(_Adapter(["y"]), NullResolver()))

    assert run_n.status == run_n1.status == RunStatus.SUCCESS
    assert run_n1.detail_json["listings_delisted"] == 0
    assert listing(site, "x").delisted_at is None
