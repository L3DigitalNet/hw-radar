"""MS2-D-10/11: provider run evidence, the completeness gate, and the local provider.

The gate is the ADR-0021 / D5 guarantee that a truncated, budget-stopped, or failed
collection run is never evidence of absence. These tests pin its full truth table
so that a later provider (Slice D's Apify Actor) cannot widen it silently.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from hw_radar.acquisition.contracts import DelistScope, ProviderRunEvidence
from hw_radar.acquisition.providers import counts_toward_sweep_continuity, gate_delist_scope
from hw_radar.catalog.models import ProviderKind, RunCompleteness

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
        assert gated is scope
    elif completeness is RunCompleteness.TRUNCATED and eligible:
        assert gated == replace(scope, complete=False)
    else:
        assert gated is None


@pytest.mark.parametrize("completeness", list(RunCompleteness))
def test_gate_passes_none_scope_through(completeness: RunCompleteness) -> None:
    assert gate_delist_scope(None, _evidence(completeness, eligible=True)) is None


@pytest.mark.parametrize("eligible", [True, False])
@pytest.mark.parametrize("completeness", list(RunCompleteness))
def test_continuity_counts_only_complete_or_eligible_truncated(
    completeness: RunCompleteness, *, eligible: bool
) -> None:
    expected = completeness is RunCompleteness.COMPLETE or (
        completeness is RunCompleteness.TRUNCATED and eligible
    )
    assert counts_toward_sweep_continuity(_evidence(completeness, eligible=eligible)) is expected
