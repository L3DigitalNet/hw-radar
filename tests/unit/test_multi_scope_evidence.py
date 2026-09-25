"""MS-2 F1: the multi-scope delist seam stays local-only and record-only at run level."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hw_radar.acquisition.contracts import (
    DelistScope,
    ParsedListing,
    ProviderRunEvidence,
    RawBatch,
    ScopeSweepReport,
)
from hw_radar.acquisition.pipeline import (
    _scope_reports,  # pyright: ignore[reportPrivateUsage]
)
from hw_radar.acquisition.providers import (
    REASON_ADAPTER_SWEEP_COMPLETE,
    REASON_ADAPTER_SWEEP_INCOMPLETE,
    REASON_COMPLETENESS_NOT_ASSERTED,
    LocalCollectionProvider,
    aggregate_local_evidence,
)
from hw_radar.acquisition.sources.ebay import EbayAdapter
from hw_radar.catalog.models import ProviderKind, RunCompleteness, RunKind

OBSERVED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _local(completeness: RunCompleteness, reason: str) -> ProviderRunEvidence:
    return ProviderRunEvidence(
        provider_kind=ProviderKind.LOCAL,
        provider_key="local",
        completeness=completeness,
        completeness_reason=reason,
        stale_absence_eligible=True,
    )


COMPLETE = _local(RunCompleteness.COMPLETE, REASON_ADAPTER_SWEEP_COMPLETE)
TRUNCATED = _local(RunCompleteness.TRUNCATED, REASON_ADAPTER_SWEEP_INCOMPLETE)
NOT_ASSERTED = _local(RunCompleteness.TRUNCATED, REASON_COMPLETENESS_NOT_ASSERTED)


def test_single_scope_evidence_is_returned_unchanged() -> None:
    # A legacy-only eBay run must record exactly the single-scope evidence.
    assert aggregate_local_evidence([TRUNCATED]) is TRUNCATED


def test_run_is_complete_only_when_every_scope_is() -> None:
    assert aggregate_local_evidence([COMPLETE, COMPLETE]) == COMPLETE
    mixed = aggregate_local_evidence([COMPLETE, TRUNCATED])
    assert mixed.completeness is RunCompleteness.TRUNCATED
    assert mixed.completeness_reason == REASON_ADAPTER_SWEEP_INCOMPLETE
    unasserted = aggregate_local_evidence([COMPLETE, NOT_ASSERTED])
    assert unasserted.completeness is RunCompleteness.TRUNCATED
    assert unasserted.completeness_reason == REASON_ADAPTER_SWEEP_INCOMPLETE


def test_aggregation_refuses_remote_or_empty_evidence() -> None:
    remote = ProviderRunEvidence(
        provider_kind=ProviderKind.APIFY,
        provider_key="actor",
        completeness=RunCompleteness.COMPLETE,
        completeness_reason="actor_complete",
        stale_absence_eligible=False,
    )
    with pytest.raises(ValueError, match="only local"):
        aggregate_local_evidence([COMPLETE, remote])
    with pytest.raises(ValueError, match="at least one"):
        aggregate_local_evidence([])


def test_report_scope_must_match_its_key() -> None:
    # The pipeline applies absence under report.scope_key but delists with
    # report.scope; a mismatch would delist one scope on another's sweep.
    scope = DelistScope(
        seen_keys=frozenset({"k"}),
        observed_at=OBSERVED_AT,
        complete=True,
        absence_grace=timedelta(hours=6),
        scope_key="ebay:gpu:rtx-3090",
    )
    with pytest.raises(ValueError, match="scope_key"):
        ScopeSweepReport(scope_key="ebay:ram:ddr4", scope=scope, pages=1, reason="complete")
    with pytest.raises(ValueError, match="scope_key"):
        ScopeSweepReport(scope_key=None, scope=scope, pages=1, reason="complete")


class _RemoteWithScopes:
    """Remote-shaped provider that grew a delist_scopes method anyway."""

    provider_kind = ProviderKind.APIFY
    provider_key = "actor"
    site_key = "ebay"
    run_kind = RunKind.FULL
    expects_json = True

    def delist_scopes(self, batch: RawBatch, parsed: list[ParsedListing]) -> list[object]:
        raise AssertionError("a remote provider must never be asked for per-scope reports")

    async def fetch(self) -> RawBatch:
        raise NotImplementedError

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return []

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope | None:
        return None

    def run_evidence(
        self,
        batch: RawBatch,
        parsed: list[ParsedListing],
        scope: DelistScope | None,
        *,
        run_kind: RunKind,
    ) -> ProviderRunEvidence:
        raise NotImplementedError


def test_only_local_providers_reach_the_multi_scope_path() -> None:
    batch = RawBatch(source="ebay", fetched_at=OBSERVED_AT)
    assert _scope_reports(_RemoteWithScopes(), batch, []) is None
    # A local adapter without the capability stays on the single-scope path too.
    assert LocalCollectionProvider(_PlainAdapter()).delist_scopes(batch, []) is None
    assert _scope_reports(LocalCollectionProvider(EbayAdapter()), batch, []) == []


class _PlainAdapter:
    name = "plain"
    site_key = "demo"
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0

    async def fetch(self) -> RawBatch:
        raise NotImplementedError

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return []
