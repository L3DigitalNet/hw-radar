"""Ratification metrics and verdicts (design §5): precision, coverage, the
declared-source floor (OQ32), the refdata pin, the OEM spot-check rate, the
audit-validity gate, and the composite MS-1 ratification gate.

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

3. **Evaluated catalog vs. declared catalog.** Which drive catalog the run used is
   also supplied by the caller (`evaluated_refdata_drive_digest`), because only the
   caller knows what it imported; the report compares it with the manifest's pin.

Tri-state precision exists so a thin corpus cannot pass: fewer than
MIN_AUTO_ACCEPTS decisions is INSUFFICIENT_CORPUS, never PASS (SA-005). Both
INSUFFICIENT_CORPUS and FAIL block the ADR flip.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from hw_radar.matching.eval.corpus import (
    CORPUS_SOURCE_KEYS,
    AuditStatus,
    CorpusEntry,
    CorpusMeta,
    select_audit_sample,
    validate_declared_sources,
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

# OQ32 (owner decision 2026-09-26): a ratification corpus must declare at least this
# many independent, currently admissible validation sources. Replaces the historical
# "all five sources" floor, which retirements made unsatisfiable.
MIN_RATIFICATION_SOURCES: Final = 3

# Only owner review turns a label into ratification evidence (design E-5): a
# claude_draft label that happens to agree with the matcher proves nothing about
# the matcher, since the draft may itself have been read off the matcher.
_OWNER_RATIFIED: Final = frozenset({AuditStatus.OWNER_CONFIRMED, AuditStatus.OWNER_CORRECTED})

# TODO(s7-integration): replace this placeholder with
# `from hw_radar.acquisition.admission import RETIRED_SOURCES` once the admission
# module lands on dev (being written by a parallel leg); the value is the same two
# keys. Until then this local copy is the single retired-source authority here.
_RETIRED_SOURCES_PLACEHOLDER: Final[frozenset[str]] = frozenset(
    {"serverpartdeals", "seagate-recertified"}
)


def retired_source_keys() -> frozenset[str]:
    """Sources that may no longer be declared for a ratification.

    Retired sources stay in CORPUS_SOURCE_KEYS so historical corpora still load;
    declaring one fails the source floor rather than the load.
    """
    return _RETIRED_SOURCES_PLACEHOLDER


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
class SourceFloor:
    """OQ32 floor evidence for ONE declared source."""

    source: str
    retired: bool
    # Entries from this source that are a rung-1/2 ACCEPT, correct against their
    # label, labeled at family grain or better, and owner-ratified. Correctness is
    # the point: the pre-OQ32 floor only checked the predicted grain, so a source
    # whose every accept was WRONG still "met" it.
    qualifying_ids: tuple[str, ...]

    @property
    def met(self) -> bool:
        return not self.retired and bool(self.qualifying_ids)


@dataclass(frozen=True)
class SourceFloorFindings:
    """OQ32 declared-source floor.

    `declared` is read from the manifest only — sorted and de-duplicated, empty
    when the manifest predates OQ32 — so a corpus can never inherit a declared
    set from code.
    """

    declared: tuple[str, ...]
    per_source: Mapping[str, SourceFloor]

    @property
    def enough_sources(self) -> bool:
        return len(self.declared) >= MIN_RATIFICATION_SOURCES

    @property
    def retired_declared(self) -> tuple[str, ...]:
        return tuple(source for source in self.declared if self.per_source[source].retired)

    @property
    def met(self) -> bool:
        return self.enough_sources and all(floor.met for floor in self.per_source.values())


@dataclass(frozen=True)
class EvalReport:
    """Corpus-derived results only — see the module docstring on why the rung-0
    suite result is deliberately absent."""

    total_entries: int
    auto_accepts: int
    correct_auto_accepts: int
    precision: float | None
    precision_verdict: Verdict
    # Reported, not gated: keyed by all five historical source keys, a source
    # absent from the corpus reporting 0.0.
    per_source_coverage: Mapping[str, float]
    source_floor: SourceFloorFindings
    oem_dual_label_rate: float | None
    audit: AuditFindings
    # The catalog the run evaluated against (supplied by the caller) and the one
    # the manifest pins. Both must be present and equal for the corpus gate.
    evaluated_refdata_drive_digest: str | None
    declared_refdata_drive_digest: str | None

    @property
    def per_source_family_floor(self) -> Mapping[str, bool]:
        """Declared source → floor met (OQ32); only declared sources appear."""
        return {source: floor.met for source, floor in self.source_floor.per_source.items()}

    @property
    def family_floor_met(self) -> bool:
        return self.source_floor.met

    @property
    def refdata_pinned(self) -> bool:
        return (
            self.declared_refdata_drive_digest is not None
            and self.declared_refdata_drive_digest == self.evaluated_refdata_drive_digest
        )

    @property
    def audit_gate(self) -> Verdict:
        return self.audit.gate

    @property
    def corpus_gate(self) -> Verdict:
        """Everything the corpus run alone can decide: precision, the declared-source
        floor, audit validity, and that the catalog evaluated is the one pinned."""
        passing = (
            self.precision_verdict is Verdict.PASS
            and self.family_floor_met
            and self.audit_gate is Verdict.PASS
            and self.refdata_pinned
        )
        return Verdict.PASS if passing else Verdict.FAIL


def ms1_ratification_gate(report: EvalReport, rung0_status: Rung0Status) -> Verdict:
    """Compose the corpus result with the externally supplied rung-0 suite result.

    PASS requires all of: precision PASS, the declared-source floor met, audit gate
    PASS, the refdata pin matching, and rung0_status PASS. A corpus-side failure outranks an unknown rung-0
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


def _source_floor(
    entries: Sequence[CorpusEntry],
    by_id: Mapping[str, Prediction],
    matched: Mapping[str, bool],
    meta: CorpusMeta,
) -> SourceFloorFindings:
    declared = tuple(sorted(set(meta.ratification_sources or ())))
    retired = retired_source_keys()
    per_source: dict[str, SourceFloor] = {}
    for source in declared:
        qualifying = tuple(
            entry.id
            for entry in entries
            if entry.source == source
            and by_id[entry.id].outcome is Outcome.ACCEPT
            and by_id[entry.id].rung in AUTO_ACCEPT_RUNGS
            and matched[entry.id]
            and GRAIN_ORDER[entry.label.expected_grain] >= _FAMILY_OR_BETTER
            and entry.label.audit_status in _OWNER_RATIFIED
        )
        per_source[source] = SourceFloor(
            source=source, retired=source in retired, qualifying_ids=qualifying
        )
    return SourceFloorFindings(declared=declared, per_source=per_source)


def build_report(
    entries: Sequence[CorpusEntry],
    predictions: Sequence[Prediction],
    meta: CorpusMeta,
    *,
    evaluated_refdata_drive_digest: str | None,
) -> EvalReport:
    """Aggregate predictions into the corpus-derived ratification results.

    Predictions and entries must correspond one-to-one by id; a mismatch raises
    rather than scoring the intersection, since a silently dropped entry moves the
    precision denominator. Raises CorpusFormatError if an entry comes from a
    source the manifest does not declare (corpus.validate_declared_sources).

    `evaluated_refdata_drive_digest` is the refdata_pin digest of the catalog the
    predictions were produced against, or None when that catalog is not the
    production import (a test fixture) — None can never satisfy the pin. It is
    keyword-only with no default for the same reason as the rung-0 status: an
    unpinned run must be stated, not assumed.
    """
    validate_declared_sources(entries, meta)
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
    for source in sorted(CORPUS_SOURCE_KEYS):
        ranks = [GRAIN_ORDER[by_id[entry.id].grain] for entry in entries if entry.source == source]
        coverage[source] = (
            sum(1 for rank in ranks if rank >= _MODEL_OR_BETTER) / len(ranks) if ranks else 0.0
        )

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
        source_floor=_source_floor(entries, by_id, matched, meta),
        oem_dual_label_rate=oem_rate,
        audit=_audit_findings(entries, matched, meta),
        evaluated_refdata_drive_digest=evaluated_refdata_drive_digest,
        declared_refdata_drive_digest=meta.refdata_drive_digest,
    )
