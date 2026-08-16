"""MS-1e harness math (design §5, §7) over synthetic predictions and the synthetic
fixture: tri-state precision boundaries, the per-source family floor, the OEM
spot-check rate, variant identity, the audit-validity gate, and the composite
ratification gate.

These tests exercise the HARNESS, never matcher quality — the real gate is the
Approach-A DB run in tests/db/test_ratification_corpus.py, and the two never
substitute for each other (design §8). Predictions are therefore constructed
directly rather than resolved.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from hw_radar.matching.eval.corpus import (
    AuditStatus,
    CorpusEntry,
    CorpusMeta,
    load_corpus,
    load_meta,
    select_audit_sample,
)
from hw_radar.matching.eval.evaluate import Prediction, prediction_matches
from hw_radar.matching.eval.report import (
    MIN_AUTO_ACCEPTS,
    EvalReport,
    Rung0Status,
    Verdict,
    build_report,
    ms1_ratification_gate,
)
from hw_radar.matching.ladder import Outcome
from hw_radar.matching.types import Grain

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "matching_corpus"
SYNTHETIC_JSONL = FIXTURE_DIR / "synthetic.jsonl"
SYNTHETIC_META = FIXTURE_DIR / "synthetic.meta.json"

ALL_SOURCES = ("serverpartdeals", "goharddrive", "wd-recertified", "seagate-recertified", "ebay")

RECERT = {
    "condition": "recertified",
    "packaging": "unknown",
    "recert_channel": "factory",
    "warranty_channel": "unknown",
}
NEW = {
    "condition": "new",
    "packaging": "unknown",
    "recert_channel": "unknown",
    "warranty_channel": "unknown",
}


def make_entry(
    entry_id: str,
    source: str = "serverpartdeals",
    *,
    grain: str = "model",
    manufacturer_key: str | None = "seagate",
    family: str | None = "Exos X16",
    model_number: str | None = "ST16000NM001G",
    variant: dict[str, str] | None = None,
    oem_dual_label: bool = False,
    audit_status: str = "owner_confirmed",
) -> CorpusEntry:
    target: dict[str, Any] = {
        "manufacturer_key": manufacturer_key,
        "family": family,
        "model_number": model_number,
        "variant": variant,
    }
    return CorpusEntry.model_validate(
        {
            "id": entry_id,
            "source": source,
            "title": f"listing {entry_id}",
            "listing": {
                "source_listing_key": f"key-{entry_id}",
                "url": f"https://example.test/{entry_id}",
                "price": "100.00",
                "currency": "USD",
                "condition_label": "",
                "attrs": {},
            },
            "label": {
                "expected_grain": grain,
                "expected_target": target,
                "oem_dual_label": oem_dual_label,
                "audit_status": audit_status,
                "notes": "",
            },
        }
    )


def make_prediction(
    entry: CorpusEntry,
    *,
    correct: bool = True,
    outcome: Outcome = Outcome.ACCEPT,
    rung: int | None = 1,
    grain: Grain | None = None,
) -> Prediction:
    """Mirror the entry's label back as a prediction; `correct=False` perturbs the
    target while KEEPING the grain, which is the miss a grain check alone misses."""
    target = entry.label.expected_target
    variant_tuple = target.variant.as_tuple() if target.variant is not None else None
    if not correct and variant_tuple is not None:
        variant_tuple = ("used", *variant_tuple[1:])
    return Prediction(
        entry_id=entry.id,
        source=entry.source,
        grain=grain if grain is not None else entry.label.expected_grain,
        manufacturer_key=target.manufacturer_key,
        family_norm=target.family_norm,
        model_norm=target.model_norm if correct else "st9999nm999x",
        variant_tuple=variant_tuple,
        rung=rung,
        outcome=outcome,
    )


def make_meta(
    entries: list[CorpusEntry],
    *,
    corpus_version: str = "v1",
    audit_rollup: dict[str, int] | None = None,
) -> CorpusMeta:
    counts: dict[str, int] = {}
    rollup: dict[str, int] = {}
    for entry in entries:
        counts[entry.source] = counts.get(entry.source, 0) + 1
        status = str(entry.label.audit_status)
        rollup[status] = rollup.get(status, 0) + 1
    return CorpusMeta.model_validate(
        {
            "corpus_version": corpus_version,
            "harvested_from": "2026-07-01",
            "harvested_to": "2026-07-02",
            "observed_at": "2026-07-02T00:00:00Z",
            "source_counts": counts,
            "matcher_version": "2026.07.3",
            "audit_rollup": audit_rollup if audit_rollup is not None else rollup,
        }
    )


def build_corpus(
    accepts: int, *, wrong: int = 0, sources: tuple[str, ...] = ALL_SOURCES
) -> tuple[list[CorpusEntry], list[Prediction]]:
    """A corpus of `accepts` rung-1 auto-accepts, `wrong` of them mispredicted,
    spread round-robin over `sources`."""
    entries = [make_entry(f"e-{n:04d}", sources[n % len(sources)]) for n in range(accepts)]
    predictions = [
        make_prediction(entry, correct=index >= wrong) for index, entry in enumerate(entries)
    ]
    return entries, predictions


def report_for(entries: list[CorpusEntry], predictions: list[Prediction]) -> EvalReport:
    return build_report(entries, predictions, make_meta(entries))


def test_precision_is_correct_auto_accepts_over_auto_accepts() -> None:
    entries, predictions = build_corpus(200, wrong=1)
    report = report_for(entries, predictions)
    assert report.auto_accepts == 200
    assert report.correct_auto_accepts == 199
    assert report.precision == pytest.approx(0.995)


def test_exactly_the_threshold_passes() -> None:
    entries, predictions = build_corpus(1000, wrong=5)  # 99.5%
    assert report_for(entries, predictions).precision_verdict is Verdict.PASS


def test_ninety_nine_point_four_percent_fails() -> None:
    entries, predictions = build_corpus(1000, wrong=6)  # 99.4%
    report = report_for(entries, predictions)
    assert report.precision == pytest.approx(0.994)
    assert report.precision_verdict is Verdict.FAIL


def test_below_the_denominator_floor_is_insufficient_never_pass() -> None:
    """SA-005: a handful of lucky matches must never look like ratification."""
    entries, predictions = build_corpus(MIN_AUTO_ACCEPTS - 1)  # every one correct
    report = report_for(entries, predictions)
    assert report.precision == pytest.approx(1.0)
    assert report.precision_verdict is Verdict.INSUFFICIENT_CORPUS
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


def test_an_empty_corpus_is_insufficient_not_a_division_error() -> None:
    report = build_report([], [], make_meta([]))
    assert report.precision is None
    assert report.precision_verdict is Verdict.INSUFFICIENT_CORPUS


def test_review_and_none_outcomes_stay_out_of_the_denominator() -> None:
    """The poison case's shape: a contradicted alias hit lands in review, so it can
    neither be counted correct nor pad the denominator."""
    entries, predictions = build_corpus(MIN_AUTO_ACCEPTS)
    poison = make_entry(
        "poison",
        "ebay",
        grain="none",
        manufacturer_key=None,
        family=None,
        model_number=None,
    )
    entries.append(poison)
    predictions.append(make_prediction(poison, outcome=Outcome.REVIEW, rung=1, grain=Grain.NONE))
    report = report_for(entries, predictions)
    assert report.auto_accepts == MIN_AUTO_ACCEPTS
    assert report.total_entries == MIN_AUTO_ACCEPTS + 1
    assert report.precision_verdict is Verdict.PASS


def test_rung_zero_accepts_are_excluded_from_the_denominator() -> None:
    """E-2b: rung 0 is a re-observation inheritance the first-observation corpus
    cannot legitimately contain; it is never an easy win in the precision math."""
    entries, predictions = build_corpus(MIN_AUTO_ACCEPTS)
    inherited = make_entry("inherited")
    entries.append(inherited)
    predictions.append(make_prediction(inherited, rung=0))
    assert report_for(entries, predictions).auto_accepts == MIN_AUTO_ACCEPTS


def test_a_source_stuck_at_grain_none_misses_the_family_floor() -> None:
    entries, predictions = build_corpus(MIN_AUTO_ACCEPTS)
    stuck = make_entry(
        "stuck", "goharddrive", grain="none", manufacturer_key=None, family=None, model_number=None
    )
    # Every goharddrive entry now resolves to nothing: drop its accepts entirely.
    keep = [
        (entry, prediction)
        for entry, prediction in zip(entries, predictions, strict=True)
        if entry.source != "goharddrive"
    ]
    entries = [entry for entry, _ in keep] + [stuck]
    predictions = [prediction for _, prediction in keep] + [
        make_prediction(stuck, outcome=Outcome.NONE, rung=None, grain=Grain.NONE)
    ]
    report = report_for(entries, predictions)
    assert report.per_source_family_floor["goharddrive"] is False
    assert report.family_floor_met is False


def test_a_source_absent_from_the_corpus_misses_the_floor() -> None:
    entries, predictions = build_corpus(MIN_AUTO_ACCEPTS, sources=ALL_SOURCES[:4])
    report = report_for(entries, predictions)
    assert report.per_source_family_floor["ebay"] is False
    assert report.per_source_coverage["ebay"] == 0.0
    assert report.family_floor_met is False


def test_floor_miss_fails_the_composite_despite_a_precision_pass() -> None:
    """SA-NEW-002: precision must never masquerade as MS-1 readiness."""
    entries, predictions = build_corpus(MIN_AUTO_ACCEPTS, sources=ALL_SOURCES[:4])
    report = report_for(entries, predictions)
    assert report.precision_verdict is Verdict.PASS
    assert report.audit_gate is Verdict.PASS
    assert report.corpus_gate is Verdict.FAIL
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


def _passing_report() -> EvalReport:
    entries, predictions = build_corpus(MIN_AUTO_ACCEPTS)
    report = report_for(entries, predictions)
    assert report.corpus_gate is Verdict.PASS
    return report


@pytest.mark.parametrize(
    ("rung0", "expected"),
    [
        (Rung0Status.PASS, Verdict.PASS),
        (Rung0Status.FAIL, Verdict.FAIL),
        (Rung0Status.NOT_RUN, Verdict.INCOMPLETE),
    ],
)
def test_composite_requires_an_explicit_rung0_result(rung0: Rung0Status, expected: Verdict) -> None:
    """The rung-0 suite result is an input, never an assumption: an unrun suite
    yields INCOMPLETE, which is non-pass."""
    assert ms1_ratification_gate(_passing_report(), rung0) is expected


def test_coverage_counts_model_grain_or_better_per_source() -> None:
    entries = [
        make_entry("a", "ebay"),
        make_entry("b", "ebay", grain="family", model_number=None),
        make_entry("c", "serverpartdeals"),
    ]
    predictions = [make_prediction(entry) for entry in entries]
    report = report_for(entries, predictions)
    assert report.per_source_coverage["ebay"] == pytest.approx(0.5)
    assert report.per_source_coverage["serverpartdeals"] == pytest.approx(1.0)
    assert report.per_source_family_floor["ebay"] is True


def test_oem_rate_is_measured_over_serverpartdeals_and_ebay_only() -> None:
    entries = [
        make_entry("a", "serverpartdeals", oem_dual_label=True),
        make_entry("b", "serverpartdeals"),
        make_entry("c", "ebay", oem_dual_label=True),
        make_entry("d", "ebay"),
        # goHardDrive dual labels exist but are out of the spot-check pool: counting
        # them would move the reported rate without changing what was checked.
        make_entry("e", "goharddrive", oem_dual_label=True),
    ]
    predictions = [make_prediction(entry) for entry in entries]
    assert report_for(entries, predictions).oem_dual_label_rate == pytest.approx(0.5)


def test_oem_rate_is_none_when_no_spot_check_sources_are_present() -> None:
    entries = [make_entry("a", "goharddrive")]
    predictions = [make_prediction(entries[0])]
    assert report_for(entries, predictions).oem_dual_label_rate is None


def test_variant_identity_is_compared_not_collapsed_to_the_model() -> None:
    """SA-003: two entries share one ProductModel and differ only by condition —
    swapping their predictions must be TWO misses, not two hits."""
    recert = make_entry("v-recert", grain="variant", variant=RECERT)
    brand_new = make_entry("v-new", grain="variant", variant=NEW)
    swapped = [
        Prediction(
            entry_id=recert.id,
            source=recert.source,
            grain=Grain.VARIANT,
            manufacturer_key="seagate",
            family_norm="exos x16",
            model_norm="st16000nm001g",
            variant_tuple=("new", "unknown", "unknown", "unknown"),
            rung=1,
            outcome=Outcome.ACCEPT,
        ),
        Prediction(
            entry_id=brand_new.id,
            source=brand_new.source,
            grain=Grain.VARIANT,
            manufacturer_key="seagate",
            family_norm="exos x16",
            model_norm="st16000nm001g",
            variant_tuple=("recertified", "unknown", "factory", "unknown"),
            rung=1,
            outcome=Outcome.ACCEPT,
        ),
    ]
    assert not prediction_matches(swapped[0], recert.label)
    assert not prediction_matches(swapped[1], brand_new.label)
    report = build_report([recert, brand_new], swapped, make_meta([recert, brand_new]))
    assert report.correct_auto_accepts == 0


def test_grain_must_match_exactly() -> None:
    entry = make_entry("g", grain="model")
    coarser = make_prediction(entry, grain=Grain.FAMILY)
    assert not prediction_matches(coarser, entry.label)


def test_an_asserted_family_is_compared_at_model_grain() -> None:
    entry = make_entry("f", grain="model", family="Exos X16")
    prediction = make_prediction(entry)
    wrong_family = replace(prediction, family_norm="ironwolf pro")
    assert prediction_matches(prediction, entry.label)
    assert not prediction_matches(wrong_family, entry.label)


def test_an_unasserted_family_is_not_compared_at_model_grain() -> None:
    """A seeded ProductModel may legitimately carry no family FK; the label author
    decides whether to assert one."""
    entry = make_entry("f2", grain="model", family=None)
    prediction = replace(make_prediction(entry), family_norm="anything at all")
    assert prediction_matches(prediction, entry.label)


def test_all_draft_labels_fail_the_audit_gate() -> None:
    entries = [make_entry(f"d-{n}", audit_status="claude_draft") for n in range(10)]
    predictions = [make_prediction(entry) for entry in entries]
    report = report_for(entries, predictions)
    assert report.audit.unaudited_sample_ids
    assert report.audit_gate is Verdict.FAIL


def test_an_unaudited_disagreement_fails_the_audit_gate() -> None:
    """E-5: every label that disagrees with the matcher needs owner eyes — it is
    either a matcher finding or a label error, and only a human tells them apart."""
    entries = [make_entry(f"d-{n}") for n in range(10)]
    entries.append(make_entry("disputed", audit_status="claude_draft"))
    predictions = [make_prediction(entry) for entry in entries[:-1]]
    predictions.append(make_prediction(entries[-1], correct=False))
    report = report_for(entries, predictions)
    assert report.audit.unaudited_disagreement_ids == ("disputed",)
    assert report.audit_gate is Verdict.FAIL
    assert report.corpus_gate is Verdict.FAIL


def test_an_audited_disagreement_passes_the_audit_gate() -> None:
    entries = [make_entry(f"d-{n}") for n in range(10)]
    entries.append(make_entry("disputed", audit_status="owner_corrected"))
    predictions = [make_prediction(entry) for entry in entries[:-1]]
    predictions.append(make_prediction(entries[-1], correct=False))
    report = report_for(entries, predictions)
    assert report.audit.unaudited_disagreement_ids == ()
    assert report.audit_gate is Verdict.PASS


def test_one_unaudited_entry_inside_the_sample_fails_the_gate() -> None:
    entries = [make_entry(f"s-{n:02d}") for n in range(20)]
    sample = select_audit_sample((entry.id for entry in entries), "v1")
    assert len(sample) == 4
    downgraded = [
        make_entry(entry.id, audit_status="claude_draft") if entry.id == sample[0] else entry
        for entry in entries
    ]
    predictions = [make_prediction(entry) for entry in downgraded]
    report = build_report(downgraded, predictions, make_meta(downgraded))
    assert report.audit.unaudited_sample_ids == (sample[0],)
    assert report.audit_gate is Verdict.FAIL


def test_audit_outside_the_sample_does_not_substitute_for_it() -> None:
    """Selected-sample coverage is the requirement: auditing a different 20% is not
    the same experiment, because the sample is what makes the check unbiased."""
    entries = [make_entry(f"s-{n:02d}") for n in range(20)]
    sample = set(select_audit_sample((entry.id for entry in entries), "v1"))
    flipped = [
        make_entry(
            entry.id, audit_status="claude_draft" if entry.id in sample else "owner_confirmed"
        )
        for entry in entries
    ]
    predictions = [make_prediction(entry) for entry in flipped]
    report = build_report(flipped, predictions, make_meta(flipped))
    assert set(report.audit.unaudited_sample_ids) == sample
    assert report.audit_gate is Verdict.FAIL


def test_a_stale_manifest_rollup_fails_the_audit_gate() -> None:
    entries = [make_entry(f"d-{n}") for n in range(10)]
    predictions = [make_prediction(entry) for entry in entries]
    stale = make_meta(entries, audit_rollup={"owner_confirmed": 9, "claude_draft": 1})
    report = build_report(entries, predictions, stale)
    assert report.audit.rollup_consistent is False
    assert report.audit_gate is Verdict.FAIL


def test_report_is_deterministic_for_identical_input() -> None:
    entries, predictions = build_corpus(MIN_AUTO_ACCEPTS)
    meta = make_meta(entries)
    assert build_report(entries, predictions, meta) == build_report(entries, predictions, meta)


def test_predictions_and_entries_must_correspond_one_to_one() -> None:
    entries, predictions = build_corpus(3)
    with pytest.raises(ValueError, match="one-to-one"):
        build_report(entries, predictions[:-1], make_meta(entries))


def test_synthetic_fixture_reports_insufficient_corpus() -> None:
    """The committed fixture is 10 entries: it proves the harness runs, and by
    construction it can never report PASS."""
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)
    predictions = [make_prediction(entry) for entry in entries]
    report = build_report(entries, predictions, meta)
    assert report.precision_verdict is Verdict.INSUFFICIENT_CORPUS
    assert report.family_floor_met is True  # all five sources resolve in the fixture
    assert report.oem_dual_label_rate == pytest.approx(0.25)
    assert report.audit_gate is Verdict.PASS
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


def test_synthetic_fixture_audit_sample_is_the_owner_confirmed_pair() -> None:
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)
    audited = {
        entry.id for entry in entries if entry.label.audit_status is not AuditStatus.CLAUDE_DRAFT
    }
    assert set(select_audit_sample((e.id for e in entries), meta.corpus_version)) == audited
