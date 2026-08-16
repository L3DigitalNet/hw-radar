"""The executable MS-1e ratification gate (design §7) plus the DB-backed proof that
the Approach-A harness runs end to end against the production resolver.

Two distinct jobs live here:

1. `test_ms1_ratification_gate` IS the gate. Until the live harvest lands there is
   no `corpus.jsonl`, so it skips — deliberately, and only for a genuinely absent
   corpus. A present corpus is always evaluated and asserted; INSUFFICIENT_CORPUS
   fails the test rather than skipping it (SA-005), because "too small to judge"
   must never read as "fine".
2. The parametrized `_corpus` cases below prove the gate cannot silently pass:
   each writes a temporary corpus with a known defect and asserts the composite
   refuses it. They run through the same load → evaluate → report path as the real
   gate, so a regression in that path breaks them too.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.db import transaction

from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Condition,
    DriveSpec,
    Listing,
    Manufacturer,
    MediaType,
    OfferSnapshot,
    ProductAlias,
    ProductFamily,
    ProductModel,
    RetentionClass,
)
from hw_radar.matching.eval.corpus import (
    CorpusEntry,
    CorpusMeta,
    load_corpus,
    load_meta,
    select_audit_sample,
)
from hw_radar.matching.eval.evaluate import (
    Prediction,
    UnknownManufacturerError,
    evaluate_corpus,
    prediction_matches,
)
from hw_radar.matching.eval.report import (
    EvalReport,
    Rung0Status,
    Verdict,
    build_report,
    ms1_ratification_gate,
)
from hw_radar.matching.ladder import Outcome
from hw_radar.matching.normalize import canonicalize_title, normalize_alias_text
from hw_radar.matching.types import Grain

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "matching_corpus"
SYNTHETIC_JSONL = FIXTURE_DIR / "synthetic.jsonl"
SYNTHETIC_META = FIXTURE_DIR / "synthetic.meta.json"
# Written by the deferred owner-in-the-loop ratification step (design §6), not by
# this milestone. Its absence is what the skip below is for.
CORPUS_JSONL = FIXTURE_DIR / "corpus.jsonl"
CORPUS_META = FIXTURE_DIR / "corpus.meta.json"

ALL_SOURCES = ("serverpartdeals", "goharddrive", "wd-recertified", "seagate-recertified", "ebay")

# One resolvable listing shape reused for the generated gate cases: it hits the
# seeded ST16000NM001G alias at rung 1 and carries a factory-recert condition, so
# the resolver lands on variant grain.
RESOLVABLE_TITLE = (
    "Seagate Exos X16 16TB ST16000NM001G SATA Factory Recertified Enterprise Hard Drive"
)
RESOLVABLE_TARGET: dict[str, Any] = {
    "manufacturer_key": "seagate",
    "family": "Exos X16",
    "model_number": "ST16000NM001G",
    "variant": {
        "condition": "recertified",
        "packaging": "unknown",
        "recert_channel": "factory",
        "warranty_channel": "unknown",
    },
}


def _manufacturer(key: str, name: str) -> Manufacturer:
    manufacturer, _ = Manufacturer.objects.get_or_create(
        normalized_name=key, defaults={"name": name}
    )
    return manufacturer


def _seed_model(
    manufacturer_key: str,
    manufacturer_name: str,
    model_number: str,
    *,
    family: str | None = None,
    capacity_tb: str | None = None,
    interface: str = "",
) -> ProductModel:
    """Seed a catalog-authoritative model the way the MS-1c refdata importer does:
    identity anchor + spec + an MPN alias written THROUGH the shared normalizer."""
    manufacturer = _manufacturer(manufacturer_key, manufacturer_name)
    product_family: ProductFamily | None = None
    if family is not None:
        product_family, _ = ProductFamily.objects.get_or_create(
            manufacturer=manufacturer,
            normalized_name=canonicalize_title(family),
            defaults={"category_id": 1, "name": family},
        )
    model, _ = ProductModel.objects.get_or_create(
        manufacturer=manufacturer,
        normalized_model_number=normalize_alias_text(model_number),
        defaults={
            "model_number": model_number,
            "product_family": product_family,
            "retention_class": RetentionClass.MANUFACTURER_REFERENCE,
        },
    )
    DriveSpec.objects.update_or_create(
        product_model=model,
        defaults={
            "media_type": MediaType.HDD,
            "capacity_tb": Decimal(capacity_tb) if capacity_tb is not None else None,
            "interface": interface,
            "retention_class": RetentionClass.MANUFACTURER_REFERENCE,
        },
    )
    ProductAlias.objects.get_or_create(
        alias_type=AliasType.MPN,
        normalized_alias_text=normalize_alias_text(model_number),
        source_site=None,
        defaults={
            "product_model": model,
            "source_kind": AliasSourceKind.CATALOG_AUTHORITATIVE,
            "retention_class": RetentionClass.MANUFACTURER_REFERENCE,
        },
    )
    return model


@pytest.fixture
def seeded_catalog(db: None) -> None:
    """The catalog the synthetic fixture is labeled against.

    Manufacturers without models exist on purpose: rung-2 decodes materialize
    provisional families under them, and every label's `manufacturer_key` must be
    seeded or `evaluate_corpus` refuses to run.
    """
    _manufacturer("seagate", "Seagate")
    _manufacturer("western_digital", "Western Digital")
    _manufacturer("hgst", "HGST")
    _manufacturer("toshiba", "Toshiba")
    _seed_model(
        "seagate",
        "Seagate",
        "ST16000NM001G",
        family="Exos X16",
        capacity_tb="16",
        interface="SATA 6Gb/s",
    )
    _seed_model(
        "seagate",
        "Seagate",
        "ST12000NE0008",
        family="IronWolf Pro",
        capacity_tb="12",
        interface="SATA 6Gb/s",
    )
    _seed_model(
        "western_digital",
        "Western Digital",
        "WUH721816ALE6L4",
        family="Ultrastar DC HC550",
        capacity_tb="18",
        interface="SATA 6Gb/s",
    )
    _seed_model("hgst", "HGST", "HUS724040ALS640", capacity_tb="4", interface="SAS 6Gb/s")


def _evaluate(
    entries: Sequence[CorpusEntry], meta: CorpusMeta
) -> tuple[list[Prediction], EvalReport]:
    predictions = evaluate_corpus(entries, meta)
    return predictions, build_report(entries, predictions, meta)


# ---------------------------------------------------------------------------
# End-to-end harness proof on the committed synthetic fixture
# ---------------------------------------------------------------------------


def test_synthetic_fixture_resolves_exactly_as_labeled(seeded_catalog: None) -> None:
    """Every synthetic label is what the PRODUCTION resolver actually produces —
    including the poison entry, which must land at grain none. A label drifting
    from resolver behavior would quietly turn this fixture into fiction."""
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)
    predictions, report = _evaluate(entries, meta)
    mismatched = [
        prediction.entry_id
        for prediction, entry in zip(predictions, entries, strict=True)
        if not prediction_matches(prediction, entry.label)
    ]
    assert mismatched == []
    assert report.family_floor_met is True
    assert report.audit_gate is Verdict.PASS


def test_poison_contradiction_lands_in_review_not_price_history(seeded_catalog: None) -> None:
    """ADR-0019 rule 1's whole point: an exact alias hit whose capacity contradicts
    the title is vetoed to REVIEW, so it never enters the auto-accept denominator."""
    entries = [entry for entry in load_corpus(SYNTHETIC_JSONL) if entry.id == "sea-0002"]
    meta = load_meta(SYNTHETIC_META)
    predictions = evaluate_corpus(entries, meta)
    assert predictions[0].outcome is Outcome.REVIEW
    assert predictions[0].grain is Grain.NONE


def test_variant_identity_splits_one_model_into_two_sellable_products(
    seeded_catalog: None,
) -> None:
    """The recert/new pair (spd-0001/spd-0002) shares one ProductModel and must
    resolve to two distinct variants — the identity a model-collapsed key hides."""
    entries = [
        entry for entry in load_corpus(SYNTHETIC_JSONL) if entry.id in {"spd-0001", "spd-0002"}
    ]
    meta = load_meta(SYNTHETIC_META)
    predictions = evaluate_corpus(entries, meta)
    assert {prediction.model_norm for prediction in predictions} == {"st16000nm001g"}
    conditions = {
        prediction.variant_tuple[0] for prediction in predictions if prediction.variant_tuple
    }
    assert conditions == {Condition.RECERTIFIED, Condition.NEW}


def test_snapshots_are_stamped_at_the_manifest_instant(seeded_catalog: None) -> None:
    """Determinism: the corpus, not the wall clock, fixes observation time — and the
    USD-only rule keeps the FX stamp an identity, independent of the rate cache."""
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)
    evaluate_corpus(entries, meta)
    snapshots = list(OfferSnapshot.objects.all())
    assert {snapshot.observed_at for snapshot in snapshots} == {meta.observed_at}
    # fx.stamp received the manifest date, not today's: the rate date proves it.
    assert {snapshot.fx_rate_date for snapshot in snapshots} == {meta.observed_at.date()}
    assert {snapshot.fx_source for snapshot in snapshots} == {"identity"}


def test_repeat_evaluation_in_rolled_back_savepoints_is_identical(seeded_catalog: None) -> None:
    """PA-001: the caller owns isolation, and honoring that contract makes the
    harness reproducible.

    Each run happens inside its own savepoint that is rolled back afterwards, which
    is how the ratification runbook is meant to invoke it. If state leaked between
    runs, the fixed `observed_at` would make the second run re-observe the same
    listings — a rung-0 inheritance and a snapshot-key collision — so identical
    predictions and reports are direct evidence that nothing leaked.
    """
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)

    def run() -> tuple[list[Prediction], EvalReport]:
        savepoint = transaction.savepoint()
        try:
            return _evaluate(entries, meta)
        finally:
            transaction.savepoint_rollback(savepoint)

    first_predictions, first_report = run()
    second_predictions, second_report = run()
    assert second_predictions == first_predictions
    assert second_report == first_report
    assert not Listing.objects.exists()  # both runs were fully rolled back


def test_listing_attrs_reach_the_snapshot_verbatim(seeded_catalog: None) -> None:
    """SA-004: the resolver's structured-MPN hook reads attrs_json, so the corpus's
    attrs must arrive unaltered or the harness is testing a different input."""
    entries = [entry for entry in load_corpus(SYNTHETIC_JSONL) if entry.id == "spd-0001"]
    meta = load_meta(SYNTHETIC_META)
    evaluate_corpus(entries, meta)
    snapshot = OfferSnapshot.objects.get()
    assert snapshot.attrs_json == entries[0].listing.attrs


def test_an_unseeded_manufacturer_key_aborts_the_run(db: None) -> None:
    """A seeding or labeling mistake must stop the evaluation, not silently score
    every affected entry as a precision miss."""
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)
    with pytest.raises(UnknownManufacturerError):
        evaluate_corpus(entries, meta)


def test_synthetic_fixture_cannot_reach_a_passing_gate(seeded_catalog: None) -> None:
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)
    _, report = _evaluate(entries, meta)
    assert report.precision_verdict is Verdict.INSUFFICIENT_CORPUS
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


# ---------------------------------------------------------------------------
# Generated corpora: the gate must refuse each defect
# ---------------------------------------------------------------------------


def _write_corpus(
    tmp_path: Path,
    entries: list[dict[str, Any]],
    *,
    corpus_version: str = "generated-v1",
    audit_rollup: dict[str, int] | None = None,
) -> tuple[Path, Path]:
    counts: dict[str, int] = {}
    rollup: dict[str, int] = {}
    for entry in entries:
        counts[entry["source"]] = counts.get(entry["source"], 0) + 1
        status = entry["label"]["audit_status"]
        rollup[status] = rollup.get(status, 0) + 1
    jsonl = tmp_path / "corpus.jsonl"
    jsonl.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    meta = tmp_path / "corpus.meta.json"
    meta.write_text(
        json.dumps(
            {
                "corpus_version": corpus_version,
                "harvested_from": "2026-07-01",
                "harvested_to": "2026-07-02",
                "observed_at": "2026-07-02T00:00:00Z",
                "source_counts": counts,
                "matcher_version": "2026.07.3",
                "audit_rollup": audit_rollup if audit_rollup is not None else rollup,
            }
        ),
        encoding="utf-8",
    )
    return jsonl, meta


def _generated_entry(
    index: int, source: str, *, correct: bool = True, audit_status: str = "owner_confirmed"
) -> dict[str, Any]:
    target = dict(RESOLVABLE_TARGET)
    if not correct:
        # Same grain, wrong model: the miss that a grain-only check would pass.
        target = {**target, "model_number": "ST12000NE0008", "family": "IronWolf Pro"}
    return {
        "id": f"gen-{index:04d}",
        "source": source,
        "title": RESOLVABLE_TITLE,
        "listing": {
            "source_listing_key": f"gen-key-{index:04d}",
            "url": f"https://example.test/gen/{index:04d}",
            "price": "179.00",
            "currency": "USD",
            "condition_label": "Recertified",
            "attrs": {},
        },
        "label": {
            "expected_grain": "variant",
            "expected_target": target,
            "oem_dual_label": False,
            "audit_status": audit_status,
            "notes": "",
        },
    }


def _generated_corpus(
    count: int, *, sources: tuple[str, ...] = ALL_SOURCES, wrong: int = 0
) -> list[dict[str, Any]]:
    return [
        _generated_entry(
            index,
            sources[index % len(sources)],
            correct=index >= wrong,
            # Mislabeled entries disagree with the matcher, so E-5 requires them
            # owner-audited; otherwise the audit gate — not precision — would be
            # what fails, and the case would prove the wrong thing.
            audit_status="owner_corrected" if index < wrong else "owner_confirmed",
        )
        for index in range(count)
    ]


PASSING_COUNT = 101  # one over MIN_AUTO_ACCEPTS, so the denominator floor is clear


def test_generated_passing_corpus_reaches_a_passing_gate(
    seeded_catalog: None, tmp_path: Path
) -> None:
    jsonl, meta_path = _write_corpus(tmp_path, _generated_corpus(PASSING_COUNT))
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert report.precision_verdict is Verdict.PASS
    assert report.family_floor_met is True
    assert report.audit_gate is Verdict.PASS
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.PASS


def test_generated_corpus_below_the_floor_is_insufficient_and_fails(
    seeded_catalog: None, tmp_path: Path
) -> None:
    jsonl, meta_path = _write_corpus(tmp_path, _generated_corpus(10))
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert report.precision_verdict is Verdict.INSUFFICIENT_CORPUS
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


def test_generated_corpus_below_precision_fails(seeded_catalog: None, tmp_path: Path) -> None:
    jsonl, meta_path = _write_corpus(tmp_path, _generated_corpus(PASSING_COUNT, wrong=2))
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert report.correct_auto_accepts == PASSING_COUNT - 2
    assert report.precision_verdict is Verdict.FAIL
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


def test_generated_corpus_missing_a_source_fails_despite_passing_precision(
    seeded_catalog: None, tmp_path: Path
) -> None:
    """SA-NEW-002: one source stuck below family grain is a catalog/extraction gap
    that a precision PASS would otherwise hide."""
    jsonl, meta_path = _write_corpus(
        tmp_path, _generated_corpus(PASSING_COUNT, sources=ALL_SOURCES[:4])
    )
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert report.precision_verdict is Verdict.PASS
    assert report.per_source_family_floor["ebay"] is False
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


def test_generated_corpus_with_draft_labels_fails_the_audit_gate(
    seeded_catalog: None, tmp_path: Path
) -> None:
    entries = [
        {**entry, "label": {**entry["label"], "audit_status": "claude_draft"}}
        for entry in _generated_corpus(PASSING_COUNT)
    ]
    jsonl, meta_path = _write_corpus(tmp_path, entries)
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert report.precision_verdict is Verdict.PASS
    assert report.audit_gate is Verdict.FAIL
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


def test_generated_corpus_with_an_unaudited_disagreement_fails_the_audit_gate(
    seeded_catalog: None, tmp_path: Path
) -> None:
    entries = _generated_corpus(PASSING_COUNT, wrong=1)
    entries[0]["label"]["audit_status"] = "claude_draft"
    jsonl, meta_path = _write_corpus(tmp_path, entries)
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert report.audit.unaudited_disagreement_ids == ("gen-0000",)
    assert report.audit_gate is Verdict.FAIL
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


def test_generated_corpus_with_the_selected_sample_undercovered_fails_the_audit_gate(
    seeded_catalog: None, tmp_path: Path
) -> None:
    """E-5 sample coverage, not just a naive audited-count: the owner must audit the
    reproducible `select_audit_sample` draw specifically, so auditing enough OTHER
    entries to clear a naive 20% count does not substitute for covering the actual
    sample (mirrors unit test_one_unaudited_entry_inside_the_sample_fails_the_gate)."""
    entries = _generated_corpus(PASSING_COUNT)
    sample = select_audit_sample((entry["id"] for entry in entries), "generated-v1")
    entries = [
        {**entry, "label": {**entry["label"], "audit_status": "claude_draft"}}
        if entry["id"] == sample[0]
        else entry
        for entry in entries
    ]
    jsonl, meta_path = _write_corpus(tmp_path, entries)
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert report.audit.unaudited_sample_ids == (sample[0],)
    assert report.audit_gate is not Verdict.PASS
    assert ms1_ratification_gate(report, Rung0Status.PASS) is not Verdict.PASS


def test_generated_corpus_with_a_stale_manifest_rollup_fails_the_audit_gate(
    seeded_catalog: None, tmp_path: Path
) -> None:
    entries = _generated_corpus(PASSING_COUNT)
    jsonl, meta_path = _write_corpus(
        tmp_path, entries, audit_rollup={"owner_confirmed": PASSING_COUNT - 1, "claude_draft": 1}
    )
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert report.audit.rollup_consistent is False
    assert report.audit_gate is Verdict.FAIL
    assert ms1_ratification_gate(report, Rung0Status.PASS) is Verdict.FAIL


@pytest.mark.parametrize(
    ("rung0", "expected"),
    [
        (Rung0Status.PASS, Verdict.PASS),
        (Rung0Status.FAIL, Verdict.FAIL),
        (Rung0Status.NOT_RUN, Verdict.INCOMPLETE),
    ],
)
def test_composite_gate_folds_in_the_rung0_suite_result(
    seeded_catalog: None, tmp_path: Path, rung0: Rung0Status, expected: Verdict
) -> None:
    """E-2b: the corpus measures rungs 1-2 only, so ratification also requires the
    rung-0 regression suite — supplied here, never assumed."""
    jsonl, meta_path = _write_corpus(tmp_path, _generated_corpus(PASSING_COUNT))
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert ms1_ratification_gate(report, rung0) is expected


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


def test_ms1_ratification_gate(seeded_catalog: None) -> None:
    """THE ratification gate (design §6 step 4).

    Skips only while the corpus is genuinely absent — the live harvest, labeling,
    and owner audit are the deferred owner-in-the-loop step (E-1). Once
    `corpus.jsonl` exists this test evaluates it and asserts the corpus side of the
    composite; it never degrades a present-but-weak corpus into a skip (SA-005).
    The rung-0 half of the composite is the separate regression suite, run
    alongside this one per the §6 runbook.
    """
    if not CORPUS_JSONL.exists():
        pytest.skip("corpus not yet harvested")
    entries = load_corpus(CORPUS_JSONL)
    meta = load_meta(CORPUS_META)
    _, report = _evaluate(entries, meta)
    assert report.precision_verdict is Verdict.PASS, report
    assert report.family_floor_met, report.per_source_family_floor
    assert report.audit_gate is Verdict.PASS, report.audit
    assert report.corpus_gate is Verdict.PASS, report
