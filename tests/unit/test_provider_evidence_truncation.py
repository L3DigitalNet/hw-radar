"""MS2-D-11 revision 5: ProviderRunEvidence.truncation_reason and its serialization.

The reason qualifies remote TRUNCATED evidence only. It is optional on the model
(the provider_run CHECK is the enforcement point; see the ProviderRunEvidence
docstring for why), forbidden everywhere else, and omitted from the JSON when
None so local run evidence stays byte-identical to Slice A's.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hw_radar.acquisition.contracts import ProviderRunEvidence
from hw_radar.catalog.models import ProviderKind, RunCompleteness, TruncationReason


def _evidence(
    completeness: RunCompleteness,
    *,
    kind: ProviderKind = ProviderKind.APIFY,
    reason: TruncationReason | None = None,
) -> ProviderRunEvidence:
    return ProviderRunEvidence(
        provider_kind=kind,
        provider_key=str(kind),
        completeness=completeness,
        completeness_reason="test",
        stale_absence_eligible=kind is ProviderKind.LOCAL,
        truncation_reason=reason,
    )


@pytest.mark.parametrize("reason", list(TruncationReason))
def test_remote_truncated_evidence_carries_its_cap_into_json(reason: TruncationReason) -> None:
    dumped = _evidence(RunCompleteness.TRUNCATED, reason=reason).model_dump(mode="json")

    assert dumped["truncation_reason"] == reason.value


def test_remote_truncated_evidence_without_a_reason_is_still_valid() -> None:
    # Pinned deliberately: the frozen remote fakes in
    # tests/db/test_collection_provider.py build exactly this evidence.
    evidence = _evidence(RunCompleteness.TRUNCATED)

    assert evidence.truncation_reason is None


@pytest.mark.parametrize(
    "completeness", [c for c in RunCompleteness if c is not RunCompleteness.TRUNCATED]
)
def test_reason_is_forbidden_for_every_other_completeness(completeness: RunCompleteness) -> None:
    with pytest.raises(ValidationError, match="truncated evidence only"):
        _evidence(completeness, reason=TruncationReason.ITEM_LIMIT)


def test_local_truncated_evidence_carries_no_reason() -> None:
    with pytest.raises(ValidationError, match="local truncated evidence"):
        _evidence(
            RunCompleteness.TRUNCATED, kind=ProviderKind.LOCAL, reason=TruncationReason.PAGE_LIMIT
        )


@pytest.mark.parametrize("kind", list(ProviderKind))
@pytest.mark.parametrize("completeness", list(RunCompleteness))
def test_absent_reason_is_omitted_from_json(
    kind: ProviderKind, completeness: RunCompleteness
) -> None:
    dumped = _evidence(completeness, kind=kind).model_dump(mode="json")

    assert set(dumped) == {
        "evidence_version",
        "provider_kind",
        "provider_key",
        "completeness",
        "completeness_reason",
        "stale_absence_eligible",
    }


def test_serialized_evidence_round_trips() -> None:
    for evidence in (
        _evidence(RunCompleteness.TRUNCATED, reason=TruncationReason.RESOURCE_LIMIT),
        _evidence(RunCompleteness.COMPLETE),
    ):
        assert ProviderRunEvidence.model_validate(evidence.model_dump(mode="json")) == evidence
