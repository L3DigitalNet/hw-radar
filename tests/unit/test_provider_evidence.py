"""MS2-D-10/11: provider run evidence, the completeness gate, and the local provider.

The gate is the ADR-0021 / D5 guarantee that a truncated, budget-stopped, or failed
collection run is never evidence of absence. These tests pin its full truth table
so that a later provider (Slice D's Apify Actor) cannot widen it silently.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hw_radar.acquisition.contracts import (
    CollectionProvider,
    DelistScope,
    ParsedListing,
    ProviderRunEvidence,
    RawBatch,
    RawItem,
)
from hw_radar.acquisition.providers import (
    REASON_ABSENCE_NOT_EVALUATED,
    REASON_ADAPTER_SWEEP_COMPLETE,
    REASON_ADAPTER_SWEEP_INCOMPLETE,
    REASON_COMPLETENESS_NOT_ASSERTED,
    LocalCollectionProvider,
    counts_toward_sweep_continuity,
    gate_delist_scope,
)
from hw_radar.catalog.models import ProviderKind, RunCompleteness, RunKind

OBSERVED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _evidence(
    completeness: RunCompleteness,
    *,
    eligible: bool,
    kind: ProviderKind = ProviderKind.LOCAL,
) -> ProviderRunEvidence:
    return ProviderRunEvidence(
        provider_kind=kind,
        provider_key=str(kind),
        completeness=completeness,
        completeness_reason="test",
        stale_absence_eligible=eligible,
    )


def _scope(*, complete: bool) -> DelistScope:
    return DelistScope(
        seen_keys=frozenset({"k-1", "k-2"}),
        observed_at=OBSERVED_AT,
        complete=complete,
        absence_grace=timedelta(hours=6),
    )


def test_completeness_values_are_the_adr0021_taxonomy() -> None:
    assert set(RunCompleteness.values) == {"complete", "truncated", "partial_failure", "failed"}


def test_evidence_is_frozen_and_forbids_extra() -> None:
    evidence = _evidence(RunCompleteness.COMPLETE, eligible=True)
    with pytest.raises(ValidationError):
        evidence.completeness = RunCompleteness.FAILED  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError):
        ProviderRunEvidence(
            provider_kind=ProviderKind.LOCAL,
            provider_key="local",
            completeness=RunCompleteness.COMPLETE,
            completeness_reason="test",
            stale_absence_eligible=True,
            unexpected="x",  # pyright: ignore[reportCallIssue]
        )


def test_evidence_version_is_pinned() -> None:
    assert _evidence(RunCompleteness.COMPLETE, eligible=True).evidence_version == 1
    with pytest.raises(ValidationError):
        ProviderRunEvidence(
            evidence_version=2,  # pyright: ignore[reportArgumentType]
            provider_kind=ProviderKind.LOCAL,
            provider_key="local",
            completeness=RunCompleteness.COMPLETE,
            completeness_reason="test",
            stale_absence_eligible=True,
        )


@pytest.mark.parametrize("reason", ["", "   "])
def test_empty_reason_rejected(reason: str) -> None:
    with pytest.raises(ValidationError):
        ProviderRunEvidence(
            provider_kind=ProviderKind.LOCAL,
            provider_key="local",
            completeness=RunCompleteness.TRUNCATED,
            completeness_reason=reason,
            stale_absence_eligible=True,
        )


def test_remote_provider_cannot_be_stale_absence_eligible() -> None:
    with pytest.raises(ValidationError):
        _evidence(RunCompleteness.TRUNCATED, eligible=True, kind=ProviderKind.APIFY)


@pytest.mark.parametrize("scope_complete", [True, False])
@pytest.mark.parametrize("eligible", [True, False])
@pytest.mark.parametrize("completeness", list(RunCompleteness))
def test_gate_delist_scope(
    completeness: RunCompleteness, *, eligible: bool, scope_complete: bool
) -> None:
    scope = _scope(complete=scope_complete)
    gated = gate_delist_scope(scope, _evidence(completeness, eligible=eligible))
    if completeness is RunCompleteness.COMPLETE:
        # A complete run may only keep the scope unchanged when the scope
        # itself claims completeness, or the evidence is stale-absence
        # eligible (local-only, per test_remote_provider_cannot_be_stale_absence_eligible).
        # complete=False with an ineligible (i.e. remote) evidence is exactly
        # the gap this test pins: it must fall through to None, not the
        # unchanged scope.
        if scope_complete or eligible:
            assert gated is scope
        else:
            assert gated is None
    elif completeness is RunCompleteness.TRUNCATED and eligible:
        assert gated == replace(scope, complete=False)
    else:
        assert gated is None


def test_gate_remote_complete_evidence_with_incomplete_scope_is_none() -> None:
    # ADR 0021: a remote run must never prove absence except by a complete
    # enumeration. COMPLETE evidence from a remote provider whose own scope
    # says complete=False is a provider lying about (or misreporting) its
    # sweep, not a legitimate stale-absence claim — the gate must fail closed.
    scope = _scope(complete=False)
    evidence = _evidence(RunCompleteness.COMPLETE, eligible=False, kind=ProviderKind.APIFY)
    assert gate_delist_scope(scope, evidence) is None


@pytest.mark.parametrize("completeness", list(RunCompleteness))
def test_gate_passes_none_scope_through(completeness: RunCompleteness) -> None:
    assert gate_delist_scope(None, _evidence(completeness, eligible=True)) is None


@pytest.mark.parametrize("eligible", [True, False])
@pytest.mark.parametrize("completeness", list(RunCompleteness))
def test_local_continuity_mapping_unchanged(
    completeness: RunCompleteness, *, eligible: bool
) -> None:
    # Local provider: `scope` is never consulted, matching the pre-existing
    # mapping (a local run's own eligibility/completeness already carries the
    # decision). Pass a scope that would fail the remote branch to prove that.
    expected = completeness is RunCompleteness.COMPLETE or (
        completeness is RunCompleteness.TRUNCATED and eligible
    )
    evidence = _evidence(completeness, eligible=eligible)
    for scope in (None, _scope(complete=False), _scope(complete=True)):
        assert counts_toward_sweep_continuity(evidence, scope) is expected


@pytest.mark.parametrize(
    ("scope_complete", "expected"),
    [(False, False), (True, True)],
)
def test_remote_complete_evidence_with_incomplete_or_missing_scope_does_not_count(
    *, scope_complete: bool, expected: bool
) -> None:
    # ADR 0021 / F-04 residual: a remote COMPLETE run only extends continuity
    # when its own scope also claims complete=True — otherwise a chain of
    # under-scoped remote runs could keep continuous_since alive for a lane
    # they never actually swept.
    evidence = _evidence(RunCompleteness.COMPLETE, eligible=False, kind=ProviderKind.APIFY)
    assert counts_toward_sweep_continuity(evidence, _scope(complete=scope_complete)) is expected
    if not scope_complete:
        assert counts_toward_sweep_continuity(evidence, None) is False


@pytest.mark.parametrize("completeness", [RunCompleteness.PARTIAL_FAILURE, RunCompleteness.FAILED])
def test_remote_non_complete_evidence_never_counts_toward_continuity(
    completeness: RunCompleteness,
) -> None:
    evidence = _evidence(completeness, eligible=False, kind=ProviderKind.APIFY)
    for scope in (None, _scope(complete=False), _scope(complete=True)):
        assert counts_toward_sweep_continuity(evidence, scope) is False


class _PlainAdapter:
    """A SourceAdapter with no DelistDetector capability (most sources today)."""

    name = "plain"
    site_key = "demo"
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0

    def __init__(self) -> None:
        self.fetched = 0
        self.parsed_batches: list[RawBatch] = []

    async def fetch(self) -> RawBatch:
        self.fetched += 1
        return RawBatch(
            source=self.name,
            fetched_at=OBSERVED_AT,
            items=[RawItem(url="https://demo.invalid/a", payload_json={"sku": "a"})],
        )

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        self.parsed_batches.append(batch)
        return [
            ParsedListing(
                source_listing_key="k-1",
                url="https://demo.invalid/a",
                title="Demo 8TB",
                price=Decimal("99.99"),
            )
        ]


class _DelistingAdapter(_PlainAdapter):
    """Implements DelistDetector structurally, with a configurable completeness claim."""

    def __init__(self, *, complete: bool) -> None:
        super().__init__()
        self._complete = complete

    def delist_scope(self, batch: RawBatch, parsed: list[ParsedListing]) -> DelistScope | None:
        return DelistScope(
            seen_keys=frozenset(p.source_listing_key for p in parsed),
            observed_at=batch.fetched_at,
            complete=self._complete,
            absence_grace=timedelta(hours=6),
        )


def _collect(provider: LocalCollectionProvider) -> tuple[RawBatch, list[ParsedListing]]:
    batch = asyncio.run(provider.fetch())
    return batch, provider.parse(batch)


def test_local_provider_delegates_fetch_and_parse() -> None:
    adapter = _PlainAdapter()
    provider = LocalCollectionProvider(adapter)
    batch, parsed = _collect(provider)
    assert adapter.fetched == 1
    assert adapter.parsed_batches == [batch]
    assert [p.source_listing_key for p in parsed] == ["k-1"]
    assert (provider.site_key, provider.run_kind, provider.expects_json) == (
        "demo",
        RunKind.FULL,
        True,
    )
    assert (provider.provider_kind, provider.provider_key) == (ProviderKind.LOCAL, "local")
    assert provider.delist_scope(batch, parsed) is None


def test_local_evidence_complete_when_adapter_proves_complete() -> None:
    provider = LocalCollectionProvider(_DelistingAdapter(complete=True))
    batch, parsed = _collect(provider)
    scope = provider.delist_scope(batch, parsed)
    assert scope is not None
    assert scope.complete is True
    evidence = provider.run_evidence(batch, parsed, scope, run_kind=RunKind.FULL)
    assert evidence.completeness is RunCompleteness.COMPLETE
    assert evidence.completeness_reason == REASON_ADAPTER_SWEEP_COMPLETE
    assert evidence.provider_kind is ProviderKind.LOCAL
    assert gate_delist_scope(scope, evidence) is scope


def test_local_evidence_truncated_eligible_when_adapter_sweep_incomplete() -> None:
    provider = LocalCollectionProvider(_DelistingAdapter(complete=False))
    batch, parsed = _collect(provider)
    scope = provider.delist_scope(batch, parsed)
    assert scope is not None
    evidence = provider.run_evidence(batch, parsed, scope, run_kind=RunKind.FULL)
    assert evidence.completeness is RunCompleteness.TRUNCATED
    assert evidence.completeness_reason == REASON_ADAPTER_SWEEP_INCOMPLETE
    assert evidence.stale_absence_eligible is True
    # The eBay truncated-sweep path is preserved: the gate hands the scope
    # through untouched (already complete=False) and continuity still counts.
    assert gate_delist_scope(scope, evidence) == scope
    assert counts_toward_sweep_continuity(evidence, scope) is True


def test_local_evidence_truncated_eligible_when_adapter_has_no_delist_detector() -> None:
    provider = LocalCollectionProvider(_PlainAdapter())
    batch, parsed = _collect(provider)
    evidence = provider.run_evidence(batch, parsed, None, run_kind=RunKind.FULL)
    assert evidence.completeness is RunCompleteness.TRUNCATED
    assert evidence.completeness_reason == REASON_COMPLETENESS_NOT_ASSERTED
    assert evidence.stale_absence_eligible is True
    # Scope-less sources must keep advancing lane continuity exactly as before.
    assert counts_toward_sweep_continuity(evidence, None) is True


@pytest.mark.parametrize("run_kind", [RunKind.HEARTBEAT, RunKind.PROBE])
def test_local_evidence_for_non_full_run_records_absence_not_evaluated(run_kind: RunKind) -> None:
    provider = LocalCollectionProvider(_DelistingAdapter(complete=True))
    batch, parsed = _collect(provider)
    evidence = provider.run_evidence(batch, parsed, None, run_kind=run_kind)
    assert evidence.completeness is RunCompleteness.TRUNCATED
    assert evidence.completeness_reason == REASON_ABSENCE_NOT_EVALUATED


def test_local_provider_satisfies_protocol() -> None:
    # The annotated assignment is the real assertion: basedpyright (strict, run
    # over tests/) rejects it if LocalCollectionProvider drifts from the Protocol.
    provider: CollectionProvider = LocalCollectionProvider(_PlainAdapter())
    assert provider.provider_kind is ProviderKind.LOCAL
