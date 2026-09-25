"""D10 *Local transactions* (ED-04): the local persist step commits all or nothing.

Before D10 the local path wrote statement by statement in autocommit, so a crash
mid-persist left the rows before it persisted. The persist step is now one
transaction; a crash rolls back the whole batch and the next poll repairs it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from hw_radar.acquisition import stages
from hw_radar.acquisition.contracts import NullResolver, ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.pipeline import run_source
from hw_radar.catalog.models import (
    Listing,
    OfferSnapshot,
    RawPayload,
    RunKind,
    RunStatus,
    SourceSite,
    SourceType,
)

# transaction=True: run_source commits from sync_to_async threads, and the
# rollback under test must be a real one, not a test-wrapper savepoint.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

SITE = "d10tx"
KEYS = ["a", "b", "c"]


class _Adapter:
    name = "d10tx"
    site_key = SITE
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0

    async def fetch(self) -> RawBatch:
        return RawBatch(
            source=self.name,
            fetched_at=datetime.now(UTC),
            items=[RawItem(url="https://tx.invalid/page", payload_json={"keys": KEYS})],
        )

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return [
            ParsedListing(
                source_listing_key=key,
                url=f"https://tx.invalid/{key}",
                title="Drive",
                price=Decimal(100),
                raw_url="https://tx.invalid/page",
            )
            for key in KEYS
        ]


def _counts(site: SourceSite) -> tuple[int, int, int]:
    return (
        Listing.objects.filter(source_site=site).count(),
        OfferSnapshot.objects.filter(listing__source_site=site).count(),
        RawPayload.objects.filter(endpoint="https://tx.invalid/page").count(),
    )


def test_local_crash_mid_persist_rolls_back_whole_batch_and_next_poll_repairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = SourceSite.objects.create(name=SITE, normalized_name=SITE, source_type=SourceType.OTHER)
    real = stages.observe_listing
    calls = 0

    def crash_on_second(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("crash mid-persist")
        return real(*args, **kwargs)

    monkeypatch.setattr(stages, "observe_listing", crash_on_second)
    run, _ = asyncio.run(run_source(_Adapter(), NullResolver()))

    assert run.status == RunStatus.FAILED
    # The first listing, its snapshot, and the raw payload were written before
    # the crash; all of it rolled back with the transaction.
    assert _counts(site) == (0, 0, 0)

    monkeypatch.setattr(stages, "observe_listing", real)
    repaired, _ = asyncio.run(run_source(_Adapter(), NullResolver()))

    assert repaired.status == RunStatus.SUCCESS
    assert _counts(site) == (3, 3, 1)
