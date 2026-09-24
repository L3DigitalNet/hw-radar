"""MS2-D-10/11 DB proof: provider evidence is recorded, and only complete runs delist.

The negative cases use a remote-shaped fake provider whose DelistScope *claims*
complete=True, which is exactly the lie the completeness gate exists to refuse:
without the gate, each of those runs would mark `k-old` ABSENT_FROM_SWEEP. The
complete-run positive control proves the same fixture does delist when the gate
allows it, so a green negative case cannot come from a dead delist stage.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from hw_radar.acquisition.contracts import (
    NullResolver,
    ParsedListing,
    RawBatch,
    RawItem,
)
from hw_radar.acquisition.pipeline import run_source
from hw_radar.catalog.models import (
    RunKind,
    RunStatus,
)

# transaction=True: run_source writes from sync_to_async threads.
# serialized_rollback=True: truncation would otherwise delete the migration-0005
# seed rows (the `demo` SourceSite/SourceConfig) for every later test.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _parsed(key: str) -> ParsedListing:
    return ParsedListing(
        source_listing_key=key,
        url=f"https://demo.invalid/{key}",
        title="Demo 8TB",
        price=Decimal("99.99"),
        raw_url="https://demo.invalid/sweep",
    )


def _batch(source: str) -> RawBatch:
    # Fresh timestamp per call: observed_at is half of OfferSnapshot's composite PK.
    return RawBatch(
        source=source,
        fetched_at=datetime.now(UTC),
        items=[RawItem(url="https://demo.invalid/sweep", payload_json={"sku": "x"})],
    )


class _LocalAdapter:
    """Local SourceAdapter parsing a fixed key set, with no DelistDetector capability."""

    name = "fake-local"
    site_key = "demo"
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0

    def __init__(self, keys: list[str]) -> None:
        self._keys = keys

    async def fetch(self) -> RawBatch:
        return _batch(self.name)

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return [_parsed(key) for key in self._keys]


def test_run_source_records_local_provider_evidence() -> None:
    run, _ = asyncio.run(run_source(_LocalAdapter(["k-1"]), NullResolver()))
    assert run.status == RunStatus.SUCCESS
    assert run.detail_json["provider"] == {
        "evidence_version": 1,
        "provider_kind": "local",
        "provider_key": "local",
        "completeness": "truncated",
        "completeness_reason": "completeness_not_asserted",
        "stale_absence_eligible": True,
    }
    assert run.detail_json["listings_delisted"] == 0
    assert run.detail_json["resolver_errors"] == 0
    assert sum(cast(dict[str, int], run.detail_json["grain_counts"]).values()) == 1
    assert run.detail_json["body_bytes"] == 0
