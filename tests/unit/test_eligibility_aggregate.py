"""MS2-D-08 aggregate: any no_match => no_match; else any unknown => unknown;
else match. FR-014: unknown never counts as match."""

from __future__ import annotations

from itertools import product

import pytest

from hw_radar.catalog.models import EligibilityVerdict
from hw_radar.eligibility.evaluate import ClauseResult, EvidenceTier, aggregate

M, N, U = EligibilityVerdict.MATCH, EligibilityVerdict.NO_MATCH, EligibilityVerdict.UNKNOWN


def _clause(outcome: EligibilityVerdict) -> ClauseResult:
    return ClauseResult("c", outcome, EvidenceTier.CATALOG, None, None, "")


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ((M,), M),
        ((U,), U),
        ((N,), N),
        ((M, M, M), M),
        ((M, U), U),
        ((U, M), U),
        ((M, N), N),
        ((U, N), N),
        ((N, U, M), N),
    ],
)
def test_aggregate_table(
    outcomes: tuple[EligibilityVerdict, ...], expected: EligibilityVerdict
) -> None:
    assert aggregate([_clause(o) for o in outcomes]) is expected


def test_exhaustive_three_clause_combinations() -> None:
    # Every combination up to three clauses, checked against the rule restated
    # independently: the verdict is match only when every clause is match.
    for size in (1, 2, 3):
        for outcomes in product((M, N, U), repeat=size):
            verdict = aggregate([_clause(o) for o in outcomes])
            if N in outcomes:
                assert verdict is N
            elif U in outcomes:
                assert verdict is U
            else:
                assert verdict is M


def test_unknown_never_passes() -> None:
    assert aggregate([_clause(M)] * 10 + [_clause(U)]) is not M


def test_empty_clause_list_is_rejected_not_a_vacuous_match() -> None:
    with pytest.raises(ValueError, match="at least one clause"):
        aggregate([])
