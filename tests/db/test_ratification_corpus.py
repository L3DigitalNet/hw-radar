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

Two catalogs, deliberately (owner decision 2026-09-26, ledger L6). The composite
gate and the opt-in measurement evaluate against `production_catalog`: a clean
migrated DB plus `import_refdata` over the committed seeds, exactly what
production runs. The hand-built `seeded_catalog` stays for the harness-mechanics
and generated-defect cases only — it once gave the opposite verdict from
production refdata on the real draft corpus, so it is never authoritative for
ratification. `_composite_run` re-checks the catalog before evaluating and a
structural test pins which fixture the composite paths request, so pointing the
gate back at a fixture fails a test.

Two opt-in measurements ride the same composite path and never gate anything:
`test_ms1e_corpus_measurement` (production behavior) and
`test_category_would_accept_measurement`, which lifts one non-drive category's
`auto_accept=False` inside the test only, to show which reviews are merely the
R4 flag and which are real vetoes. Both skip unless their env vars are set.
"""

from __future__ import annotations

import inspect
import io
import json
import os
from collections.abc import Sequence
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.db import transaction

from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Condition,
    DriveSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    MediaType,
    OfferSnapshot,
    ProductAlias,
    ProductFamily,
    ProductModel,
    RetentionClass,
)
from hw_radar.matching import categories
from hw_radar.matching.eval.corpus import (
    CorpusEntry,
    CorpusMeta,
    load_corpus,
    load_meta,
    select_audit_sample,
    validate_declared_sources,
)
from hw_radar.matching.eval.evaluate import (
    Prediction,
    UnknownManufacturerError,
    evaluate_corpus,
    prediction_matches,
)
from hw_radar.matching.eval.refdata_pin import drive_seed_digest
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
from hw_radar.refdata.loader import load_seed_documents

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "matching_corpus"
SYNTHETIC_JSONL = FIXTURE_DIR / "synthetic.jsonl"
SYNTHETIC_META = FIXTURE_DIR / "synthetic.meta.json"
# Written by the deferred owner-in-the-loop ratification step (design §6), not by
# this milestone. Its absence is what the skip below is for.
CORPUS_JSONL = FIXTURE_DIR / "corpus.jsonl"
CORPUS_META = FIXTURE_DIR / "corpus.meta.json"

# The currently admissible sources (OQ32); generated corpora declare these.
ADMISSIBLE_SOURCES = ("ebay", "goharddrive", "wd-recertified")

# Identity token for the hand-built `seeded_catalog`, which has no seed files to
# digest. All zeros is not the refdata_pin digest of any real seed set, so a
# corpus pinned to it can never pass the composite gate, which pins against
# drive_seed_digest() of the production seeds.
FIXTURE_CATALOG_DIGEST = "0" * 64

# Opt-in measurement (test_ms1e_corpus_measurement).
MEASUREMENT_CORPUS_ENV = "HW_RADAR_MS1E_CORPUS"
MEASUREMENT_REPORT_ENV = "HW_RADAR_MS1E_REPORT"
# Opt-in would-accept measurement (test_category_would_accept_measurement); it
# reads the corpus and report paths from the two variables above.
CATEGORY_WOULD_ACCEPT_ENV = "HW_RADAR_CATEGORY_WOULD_ACCEPT"

# Evidence keys that say why an edge is REVIEW rather than ACCEPT. Cross-file
# contract: these are the keys `ladder.decide` and `resolver._apply_category_gates`
# (plus the resolver's error fallback) write. A new gate key missing here shows up
# as an empty `review_reason` on a REVIEW row, never as a wrong reason.
REVIEW_REASON_KEYS = (
    "veto",
    "no_brand_evidence",
    "brand_contradicts_exact_alias",
    "conflicting_targets",
    "brand_contradicts_decode",
    "family_contradicts_decode",
    "multiple_mpns",
    "conflicting_alias_models",
    "prior_model_not_named",
    "review_only_alias_conflict",
    "cross_category",
    "acceptance_policy",
    "auto_accept_disabled",
    "error",
)

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
    """Harness run against `seeded_catalog` — never the composite gate's path."""
    predictions = evaluate_corpus(entries, meta)
    return predictions, build_report(
        entries, predictions, meta, evaluated_refdata_drive_digest=FIXTURE_CATALOG_DIGEST
    )


@pytest.fixture
def production_catalog(db: None) -> str:
    """The composite gate's catalog: the clean migrated test DB plus the canonical
    production import (`import_refdata` with no `--seed-dir`, i.e. the committed
    seeds). Returns the refdata_pin digest of the drive seeds it imported."""
    call_command("import_refdata", stdout=io.StringIO())
    return drive_seed_digest()


CatalogRows = tuple[set[tuple[str, str]], set[tuple[str, str, str, str]]]


def _seed_derived_catalog() -> CatalogRows:
    """(models, aliases) the committed seeds define, read through the same loader
    `import_refdata` uses — the expected catalog, independent of the DB."""
    models: set[tuple[str, str]] = set()
    aliases: set[tuple[str, str, str, str]] = set()
    for doc in load_seed_documents():
        for model in doc.models:
            key = (doc.manufacturer_key, normalize_alias_text(model.model_number))
            models.add(key)
            aliases.update((alias.alias_type, alias.normalized, *key) for alias in model.aliases)
    return models, aliases


def _db_catalog() -> CatalogRows:
    models = {
        (str(maker), str(number))
        for maker, number in ProductModel.objects.values_list(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType] - django-types leaves values_list's element type Unknown
            "manufacturer__normalized_name", "normalized_model_number"
        )
    }
    aliases = {
        (str(kind), str(text), str(maker), str(number))
        for kind, text, maker, number in ProductAlias.objects.values_list(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType] - django-types leaves values_list's element type Unknown
            "alias_type",
            "normalized_alias_text",
            "product_model__manufacturer__normalized_name",
            "product_model__normalized_model_number",
        )
    }
    return models, aliases


def _assert_catalog_is_production_refdata() -> None:
    """Refuse to ratify against anything but the production import.

    Runs before evaluation, while the catalog is still pristine: the resolver
    itself adds rows (rung-2 provisional families, variants), so the comparison
    is only meaningful before the first entry resolves. Any extra fixture row —
    e.g. `seeded_catalog` layered on top — or a missing seed row fails here.
    """
    expected_models, expected_aliases = _seed_derived_catalog()
    actual_models, actual_aliases = _db_catalog()
    assert actual_models == expected_models, (
        f"gate catalog models diverge from the committed seeds: "
        f"extra={sorted(actual_models - expected_models)} "
        f"missing={sorted(expected_models - actual_models)}"
    )
    assert actual_aliases == expected_aliases, (
        f"gate catalog aliases diverge from the committed seeds: "
        f"extra={sorted(actual_aliases - expected_aliases)} "
        f"missing={sorted(expected_aliases - actual_aliases)}"
    )


def _composite_run(
    entries: Sequence[CorpusEntry], meta: CorpusMeta
) -> tuple[list[Prediction], EvalReport]:
    """THE composite-gate evaluation path, shared by the gate and the opt-in
    measurement so the two can never measure different things. Callers must
    request `production_catalog` (pinned by a structural test below)."""
    _assert_catalog_is_production_refdata()
    # Before any DB write: an undeclared source is a manifest defect, not a result.
    validate_declared_sources(entries, meta)
    predictions = evaluate_corpus(entries, meta)
    return predictions, build_report(
        entries, predictions, meta, evaluated_refdata_drive_digest=drive_seed_digest()
    )


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
    # The fixture predates OQ32's retirements and declares all five sources.
    assert report.source_floor.retired_declared == ("seagate-recertified", "serverpartdeals")
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
    ratification_sources: Sequence[str] = ADMISSIBLE_SOURCES,
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
                "ratification_sources": list(ratification_sources),
                "refdata_drive_digest": FIXTURE_CATALOG_DIGEST,
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
    count: int, *, sources: tuple[str, ...] = ADMISSIBLE_SOURCES, wrong: int = 0
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
    """SA-NEW-002 / OQ32: a declared source with no ratified accept is a
    catalog/extraction gap that a precision PASS would otherwise hide."""
    jsonl, meta_path = _write_corpus(
        tmp_path, _generated_corpus(PASSING_COUNT, sources=ADMISSIBLE_SOURCES[:2])
    )
    _, report = _evaluate(load_corpus(jsonl), load_meta(meta_path))
    assert report.precision_verdict is Verdict.PASS
    assert report.per_source_family_floor["wd-recertified"] is False
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


def test_ms1_ratification_gate(production_catalog: str) -> None:
    """THE ratification gate (design §6 step 4).

    Skips only while the corpus is genuinely absent — the live harvest, labeling,
    and owner audit are the deferred owner-in-the-loop step (E-1). Once
    `corpus.jsonl` exists this test evaluates it through `_composite_run` against
    the production refdata import and asserts the corpus side of the composite;
    it never degrades a present-but-weak corpus into a skip (SA-005). The rung-0
    half of the composite is the separate regression suite, run alongside this
    one per the §6 runbook.
    """
    if not CORPUS_JSONL.exists():
        pytest.skip("corpus not yet harvested")
    entries = load_corpus(CORPUS_JSONL)
    meta = load_meta(CORPUS_META)
    _, report = _composite_run(entries, meta)
    assert report.precision_verdict is Verdict.PASS, report
    assert report.refdata_pinned, (
        f"corpus pins refdata {report.declared_refdata_drive_digest}, "
        f"evaluated against {production_catalog}"
    )
    assert report.family_floor_met, report.source_floor
    assert report.audit_gate is Verdict.PASS, report.audit
    assert report.corpus_gate is Verdict.PASS, report


def _report_payload(
    entries: Sequence[CorpusEntry], predictions: Sequence[Prediction], report: EvalReport
) -> dict[str, Any]:
    by_id = {entry.id: entry for entry in entries}
    return {
        "report": asdict(report),
        # Derived verdicts are properties, which asdict does not carry.
        "verdicts": {
            "precision_verdict": report.precision_verdict,
            "family_floor_met": report.family_floor_met,
            "per_source_family_floor": dict(report.per_source_family_floor),
            "enough_sources": report.source_floor.enough_sources,
            "retired_declared": report.source_floor.retired_declared,
            "refdata_pinned": report.refdata_pinned,
            "audit_gate": report.audit_gate,
            "corpus_gate": report.corpus_gate,
            "composite_rung0_not_run": ms1_ratification_gate(report, Rung0Status.NOT_RUN),
        },
        "predictions": [
            {
                **asdict(prediction),
                "label_grain": by_id[prediction.entry_id].label.expected_grain,
                "label_audit_status": by_id[prediction.entry_id].label.audit_status,
                "matches_label": prediction_matches(prediction, by_id[prediction.entry_id].label),
            }
            for prediction in predictions
        ],
    }


def test_ms1e_corpus_measurement(production_catalog: str) -> None:
    """Opt-in measurement of an arbitrary corpus through the EXACT composite path.

    Skipped unless `HW_RADAR_MS1E_CORPUS` names a corpus `.jsonl`; its manifest is
    read from the sibling `.meta.json` (`x.jsonl` → `x.meta.json`). The full
    EvalReport, the derived verdicts, and every per-entry prediction are written
    as JSON to `HW_RADAR_MS1E_REPORT`, and the verdicts are printed (run with -s).
    It never asserts PASS — a measurement of a draft corpus is expected to fail
    the gate — so it only fails on a harness error or a missing report path. The
    composite printed here has the rung-0 suite NOT_RUN, so it can be at best
    INCOMPLETE; run tests/db/test_rung0_regression.py for that half.

        HW_RADAR_MS1E_CORPUS=/tmp/probe/corpus.jsonl \\
        HW_RADAR_MS1E_REPORT=/tmp/probe/report.json \\
        uv run pytest tests/db/test_ratification_corpus.py -k measurement -s
    """
    corpus_path = os.environ.get(MEASUREMENT_CORPUS_ENV)
    if not corpus_path:
        pytest.skip(f"{MEASUREMENT_CORPUS_ENV} not set")
    report_path = os.environ.get(MEASUREMENT_REPORT_ENV)
    if not report_path:
        pytest.fail(f"{MEASUREMENT_REPORT_ENV} must name the JSON report output path")
    jsonl = Path(corpus_path)
    entries = load_corpus(jsonl)
    meta = load_meta(jsonl.with_suffix(".meta.json"))
    predictions, report = _composite_run(entries, meta)
    payload = _report_payload(entries, predictions, report)
    payload["evaluated_against"] = production_catalog
    Path(report_path).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            payload["verdicts"]
            | {
                "auto_accepts": report.auto_accepts,
                "correct_auto_accepts": report.correct_auto_accepts,
                "precision": report.precision,
            },
            indent=2,
            default=str,
        )
    )


def _corpus_category(entries: Sequence[CorpusEntry]) -> str:
    hints = {entry.listing.category_hint for entry in entries}
    slug = next(iter(hints)) if len(hints) == 1 else None
    if slug is None or slug == categories.DRIVE:
        pytest.fail(
            f"would-accept measurement needs one non-drive category_hint on every entry, "
            f"got {sorted(str(hint) for hint in hints)}"
        )
    return slug


def _review_reason(entry: CorpusEntry) -> dict[str, object]:
    """The gate keys on the entry's current edge; empty when there is no edge (a
    first-time miss writes none) or the edge is not a review."""
    edge = ListingResolution.objects.filter(
        listing__source_site__normalized_name=entry.source,
        listing__source_listing_key=entry.listing.source_listing_key,
        is_current=True,
    ).first()
    if edge is None:
        return {}
    return {key: edge.evidence[key] for key in REVIEW_REASON_KEYS if key in edge.evidence}


def test_category_would_accept_measurement(
    production_catalog: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opt-in: what a non-drive category corpus WOULD auto-accept with its R4
    `auto_accept=False` lifted, through the exact composite path.

    Skipped unless `HW_RADAR_CATEGORY_WOULD_ACCEPT=1`; corpus and report paths
    come from `HW_RADAR_MS1E_CORPUS` / `HW_RADAR_MS1E_REPORT` as in
    `test_ms1e_corpus_measurement`. Every entry must carry the same non-drive
    `category_hint`; only that category's `auto_accept` is forced True, and only
    via monkeypatch inside this test, so production behavior and every other test
    are untouched. The AcceptancePolicy, cross-category guard and vetoes still
    run, so an ACCEPT here is exactly what a ratified flip would accept. Each
    prediction gains `review_reason` (the gate keys on its current edge, e.g.
    `{"veto": ["sample"]}`). Evidence for an owner audit, never a ratification.

        HW_RADAR_CATEGORY_WOULD_ACCEPT=1 \\
        HW_RADAR_MS1E_CORPUS=/tmp/probe/corpus.jsonl \\
        HW_RADAR_MS1E_REPORT=/tmp/probe/report.json \\
        uv run pytest tests/db/test_ratification_corpus.py -k would_accept -s
    """
    if os.environ.get(CATEGORY_WOULD_ACCEPT_ENV) != "1":
        pytest.skip(f"{CATEGORY_WOULD_ACCEPT_ENV} not set to 1")
    corpus_path = os.environ.get(MEASUREMENT_CORPUS_ENV)
    report_path = os.environ.get(MEASUREMENT_REPORT_ENV)
    if not corpus_path or not report_path:
        pytest.fail(f"{MEASUREMENT_CORPUS_ENV} and {MEASUREMENT_REPORT_ENV} must both be set")
    jsonl = Path(corpus_path)
    entries = load_corpus(jsonl)
    meta = load_meta(jsonl.with_suffix(".meta.json"))
    slug = _corpus_category(entries)
    production_rules = categories.rules_for(slug)
    if production_rules is None:
        pytest.fail(f"category {slug!r} has no registered rules")
    production_rules_for = categories.rules_for

    def would_accept_rules_for(requested: str) -> categories.CategoryRules | None:
        rules = production_rules_for(requested)
        if rules is None or requested != slug:
            return rules
        return replace(rules, auto_accept=True)

    # The resolver reads `categories.rules_for` as a module attribute on every
    # listing (the registry holds factories for the same reason), so patching the
    # function reaches it; the assertion below proves the patch took effect.
    monkeypatch.setattr(categories, "rules_for", would_accept_rules_for)
    predictions, report = _composite_run(entries, meta)
    reasons = {entry.id: _review_reason(entry) for entry in entries}
    still_disabled = sorted(
        entry_id for entry_id, reason in reasons.items() if "auto_accept_disabled" in reason
    )
    assert still_disabled == [], (
        f"auto_accept override did not reach the resolver: {still_disabled}"
    )
    payload = _report_payload(entries, predictions, report)
    rows: list[dict[str, Any]] = payload["predictions"]
    for row in rows:
        row["review_reason"] = reasons[row["entry_id"]]
    payload["evaluated_against"] = production_catalog
    payload["would_accept"] = {
        "category": slug,
        "production_auto_accept": production_rules.auto_accept,
        "measured_auto_accept": True,
    }
    Path(report_path).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    reason_counts: dict[str, int] = {}
    for reason in reasons.values():
        for key in reason:
            reason_counts[key] = reason_counts.get(key, 0) + 1
    print(
        json.dumps(
            {
                "category": slug,
                "would_accepts": report.auto_accepts,
                "correct_would_accepts": report.correct_auto_accepts,
                "precision": report.precision,
                "review_reasons": reason_counts,
            },
            indent=2,
            default=str,
        )
    )


# ---------------------------------------------------------------------------
# The gate's catalog is the production import, and only that
# ---------------------------------------------------------------------------


def test_production_catalog_is_exactly_the_committed_seed_import(production_catalog: str) -> None:
    models, aliases = _seed_derived_catalog()
    assert models, "the committed seeds define no models"
    assert _db_catalog() == (models, aliases)
    assert production_catalog == drive_seed_digest()


def test_the_catalog_guard_refuses_a_fixture_row_on_top_of_refdata(
    production_catalog: str,
) -> None:
    """The divergence the owner decision closed: any hand-seeded row beside the
    production import (here the `seeded_catalog` HGST model) must stop the run."""
    _seed_model("hgst", "HGST", "HUS724040ALS640", capacity_tb="4", interface="SAS 6Gb/s")
    with pytest.raises(AssertionError, match="HUS724040ALS640".lower()):
        _composite_run([], load_meta(SYNTHETIC_META))


@pytest.mark.parametrize(
    "gate",
    [
        test_ms1_ratification_gate,
        test_ms1e_corpus_measurement,
        test_category_would_accept_measurement,
    ],
)
def test_composite_paths_request_the_production_catalog(gate: object) -> None:
    """Structural pin: a future edit pointing the gate at a test fixture fails here
    even while the committed corpus is absent and the gate itself skips."""
    assert callable(gate)
    parameters = inspect.signature(gate).parameters
    assert "production_catalog" in parameters
    assert "seeded_catalog" not in parameters
    assert "_composite_run" in inspect.getsource(gate)
