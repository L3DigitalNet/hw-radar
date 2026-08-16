"""Ratification metrics and verdicts (design §5): precision, coverage, the
per-source family floor, the OEM spot-check rate, the audit-validity gate, and the
composite MS-1 ratification gate.

Two deliberate separations, both anti-false-green measures:

1. **Corpus-derived vs. externally supplied.** `EvalReport` holds only what the
   corpus itself can prove. The rung-0 regression suite (E-2b) is a different
   experiment, so `ms1_ratification_gate` takes its status as an explicit
   argument. It is never inferred, defaulted, or discovered — a gate that assumes
   an unrun suite passed is worse than no gate.
2. **Precision vs. readiness (SA-NEW-002).** `precision_verdict` answers "are the
   auto-accepts accurate?"; it is not readiness. One source stuck at `grain = none`
   means a catalog or extraction gap that a precision PASS actively hides, so the
   composite requires the floor too.

Tri-state precision exists so a thin corpus cannot pass: fewer than
MIN_AUTO_ACCEPTS decisions is INSUFFICIENT_CORPUS, never PASS (SA-005). Both
INSUFFICIENT_CORPUS and FAIL block the ADR flip.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from hw_radar.matching.eval.corpus import (
    CORPUS_SOURCE_KEYS,
    AuditStatus,
    CorpusEntry,
    CorpusMeta,
    select_audit_sample,
)
from hw_radar.matching.eval.evaluate import Prediction, prediction_matches
from hw_radar.matching.ladder import Outcome
from hw_radar.matching.types import GRAIN_ORDER, Grain

# The denominator floor (ingestion-design §MS-1e): the gate must not be passable on
# a handful of lucky matches. A corpus resolving 12 listings reports
# INSUFFICIENT_CORPUS, never PASS.
MIN_AUTO_ACCEPTS = 100
PASS_PRECISION_NUMERATOR = 995
PASS_PRECISION_DENOMINATOR = 1000  # 99.5% — compared in integers, never in floats
PASS_PRECISION = PASS_PRECISION_NUMERATOR / PASS_PRECISION_DENOMINATOR

# Rung 0 is a re-observation inheritance and cannot appear in a corpus of distinct
# first observations (E-2b); an accept at rung 0 here would mean the corpus lost
# its first-observation premise, so it is excluded from the denominator rather
# than counted as an easy win.
AUTO_ACCEPT_RUNGS = frozenset({1, 2})

# ADR-0019 rule 7's dual-label spot check is only meaningful where OEM
# cross-reference tokens actually appear in titles (design §5).
OEM_SPOT_CHECK_SOURCES = frozenset({"serverpartdeals", "ebay"})

_FAMILY_OR_BETTER = GRAIN_ORDER[Grain.FAMILY]
_MODEL_OR_BETTER = GRAIN_ORDER[Grain.MODEL]


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT_CORPUS = "insufficient_corpus"
    # Composite-only: a required input was not supplied, so the gate is unknown
    # rather than failed. Non-pass either way — it must never open the ADR flip.
    INCOMPLETE = "incomplete"


class Rung0Status(StrEnum):
    """Result of the E-2b rung-0 regression suite, supplied by the caller.

    NOT_RUN is a first-class value on purpose: "we did not run it" must be
    distinguishable from "it passed", and it can only ever produce a non-pass
    composite.
    """

    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class AuditFindings:
    """Evidence behind `EvalReport.audit_gate` (design E-5).

    The gate answers "are these labels trustworthy enough to ratify against?" —
    without it, a corpus of unaudited drafts could ratify ADR-0019 against
    labels the matcher itself effectively authored.
    """

    sample_ids: tuple[str, ...]
    unaudited_sample_ids: tuple[str, ...]
    # Entries where the label disagrees with the prediction. E-5 requires owner
    # review of EVERY one: a disagreement is either a matcher error (the finding
    # the gate is for) or a label error, and only a human can tell them apart.
    unaudited_disagreement_ids: tuple[str, ...]
    rollup_consistent: bool

    @property
    def gate(self) -> Verdict:
        if self.unaudited_sample_ids or self.unaudited_disagreement_ids:
            return Verdict.FAIL
        return Verdict.PASS if self.rollup_consistent else Verdict.FAIL


@dataclass(frozen=True)
class EvalReport:
    """Corpus-derived results only — see the module docstring on why the rung-0
    suite result is deliberately absent."""

    total_entries: int
    auto_accepts: int
    correct_auto_accepts: int
    precision: float | None
    precision_verdict: Verdict
    # Keyed by all five source keys; a source absent from the corpus reports 0.0
    # coverage and False floor, because "no listings" is a gap, not a pass.
    per_source_coverage: Mapping[str, float]
    per_source_family_floor: Mapping[str, bool]
    family_floor_met: bool
    oem_dual_label_rate: float | None
    audit: AuditFindings

    @property
    def audit_gate(self) -> Verdict:
        return self.audit.gate

    @property
    def corpus_gate(self) -> Verdict:
        """Everything the corpus alone can decide: precision, floor, audit validity."""
        passing = (
            self.precision_verdict is Verdict.PASS
            and self.family_floor_met
            and self.audit_gate is Verdict.PASS
        )
        return Verdict.PASS if passing else Verdict.FAIL


def ms1_ratification_gate(report: EvalReport, rung0_status: Rung0Status) -> Verdict:
    """Compose the corpus result with the externally supplied rung-0 suite result.

    PASS requires all four: precision PASS, per-source family floor met, audit gate
    PASS, and rung0_status PASS. A corpus-side failure outranks an unknown rung-0
    result (definite FAIL beats INCOMPLETE); an otherwise-passing corpus with an
    unrun suite is INCOMPLETE, which is non-pass — the ADR flip keys off PASS only.
    """
    if report.corpus_gate is not Verdict.PASS or rung0_status is Rung0Status.FAIL:
        return Verdict.FAIL
    if rung0_status is Rung0Status.NOT_RUN:
        return Verdict.INCOMPLETE
    return Verdict.PASS


def _precision_verdict(auto_accepts: int, correct: int) -> Verdict:
    if auto_accepts < MIN_AUTO_ACCEPTS:
        return Verdict.INSUFFICIENT_CORPUS
    # Integer comparison: at exactly 99.5% a float round-trip could decide the
    # gate, and the gate is never loosened by rounding.
    passing = correct * PASS_PRECISION_DENOMINATOR >= PASS_PRECISION_NUMERATOR * auto_accepts
    return Verdict.PASS if passing else Verdict.FAIL


def _audit_findings(
    entries: Sequence[CorpusEntry], matched: Mapping[str, bool], meta: CorpusMeta
) -> AuditFindings:
    audited = {
        entry.id for entry in entries if entry.label.audit_status is not AuditStatus.CLAUDE_DRAFT
    }
    sample = select_audit_sample((entry.id for entry in entries), meta.corpus_version)
    tally: dict[AuditStatus, int] = {}
    for entry in entries:
        status = entry.label.audit_status
        tally[status] = tally.get(status, 0) + 1
    declared = {status: count for status, count in meta.audit_rollup.items() if count}
    return AuditFindings(
        sample_ids=sample,
        unaudited_sample_ids=tuple(eid for eid in sample if eid not in audited),
        unaudited_disagreement_ids=tuple(
            entry.id for entry in entries if not matched[entry.id] and entry.id not in audited
        ),
        rollup_consistent=tally == declared,
    )


def build_report(
    entries: Sequence[CorpusEntry], predictions: Sequence[Prediction], meta: CorpusMeta
) -> EvalReport:
    """Aggregate predictions into the corpus-derived ratification results.

    Predictions and entries must correspond one-to-one by id; a mismatch raises
    rather than scoring the intersection, since a silently dropped entry moves the
    precision denominator.
    """
    by_id = {prediction.entry_id: prediction for prediction in predictions}
    if len(by_id) != len(predictions) or by_id.keys() != {entry.id for entry in entries}:
        raise ValueError("predictions and corpus entries do not correspond one-to-one")

    matched = {entry.id: prediction_matches(by_id[entry.id], entry.label) for entry in entries}
    auto_accept_ids = [
        entry.id
        for entry in entries
        if by_id[entry.id].outcome is Outcome.ACCEPT and by_id[entry.id].rung in AUTO_ACCEPT_RUNGS
    ]
    auto_accepts = len(auto_accept_ids)
    correct = sum(1 for entry_id in auto_accept_ids if matched[entry_id])

    coverage: dict[str, float] = {}
    floor: dict[str, bool] = {}
    for source in sorted(CORPUS_SOURCE_KEYS):
        ranks = [GRAIN_ORDER[by_id[entry.id].grain] for entry in entries if entry.source == source]
        coverage[source] = (
            sum(1 for rank in ranks if rank >= _MODEL_OR_BETTER) / len(ranks) if ranks else 0.0
        )
        floor[source] = any(rank >= _FAMILY_OR_BETTER for rank in ranks)

    oem_pool = [entry for entry in entries if entry.source in OEM_SPOT_CHECK_SOURCES]
    oem_rate = (
        sum(1 for entry in oem_pool if entry.label.oem_dual_label) / len(oem_pool)
        if oem_pool
        else None
    )

    return EvalReport(
        total_entries=len(entries),
        auto_accepts=auto_accepts,
        correct_auto_accepts=correct,
        precision=correct / auto_accepts if auto_accepts else None,
        precision_verdict=_precision_verdict(auto_accepts, correct),
        per_source_coverage=coverage,
        per_source_family_floor=floor,
        family_floor_met=all(floor.values()),
        oem_dual_label_rate=oem_rate,
        audit=_audit_findings(entries, matched, meta),
    )
