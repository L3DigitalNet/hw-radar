"""MS-1e corpus schema invariants (design §3, §7): grain/target-key consistency,
the SA-002 source-key whitelist, loader normalization (SA-003), and malformed-line
rejection. No DB, no matcher — the schema layer is pure validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hw_radar.acquisition.sources import ADAPTERS
from hw_radar.matching.eval.corpus import (
    CORPUS_SOURCE_KEYS,
    AuditStatus,
    CorpusEntry,
    CorpusFormatError,
    load_corpus,
    load_meta,
    select_audit_sample,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "matching_corpus"
SYNTHETIC_JSONL = FIXTURE_DIR / "synthetic.jsonl"
SYNTHETIC_META = FIXTURE_DIR / "synthetic.meta.json"


def _entry(**label_overrides: Any) -> dict[str, Any]:
    label: dict[str, Any] = {
        "expected_grain": "model",
        "expected_target": {
            "manufacturer_key": "seagate",
            "family": "Exos X18",
            "model_number": "ST18000NM000J",
            "variant": None,
        },
        "oem_dual_label": False,
        "audit_status": "claude_draft",
        "notes": "",
    }
    label.update(label_overrides)
    return {
        "id": "spd-0001",
        "source": "serverpartdeals",
        "title": "Seagate Exos X18 ST18000NM000J 18TB SATA Recertified Enterprise HDD",
        "listing": {
            "source_listing_key": "spd-18tb-x18",
            "url": "https://example.test/spd-18tb-x18",
            "price": "199.00",
            "currency": "USD",
            "condition_label": "Recertified",
            "attrs": {"sku": "SPD-18TB", "variant_title": "18TB"},
        },
        "label": label,
    }


def _target(**overrides: Any) -> dict[str, Any]:
    target: dict[str, Any] = {
        "manufacturer_key": "seagate",
        "family": "Exos X18",
        "model_number": "ST18000NM000J",
        "variant": None,
    }
    target.update(overrides)
    return target


VARIANT = {
    "condition": "recertified",
    "packaging": "bulk",
    "recert_channel": "factory",
    "warranty_channel": "seller",
}


@pytest.mark.parametrize(
    ("grain", "target"),
    [
        ("none", _target(manufacturer_key=None, family=None, model_number=None)),
        ("family", _target(model_number=None)),
        ("model", _target()),
        ("variant", _target(variant=VARIANT)),
    ],
)
def test_valid_entry_at_every_grain(grain: str, target: dict[str, Any]) -> None:
    entry = CorpusEntry.model_validate(_entry(expected_grain=grain, expected_target=target))
    assert str(entry.label.expected_grain) == grain


@pytest.mark.parametrize(
    ("grain", "target"),
    [
        # Each row is one consistency violation the loader must refuse (design §3).
        ("variant", _target()),  # variant grain without the variant object
        ("model", _target(model_number=None)),  # model grain without a model number
        ("family", _target(family=None, model_number=None)),  # family grain without a family
        ("model", _target(manufacturer_key=None)),  # non-none grain without the controlled key
        ("none", _target(family=None, model_number=None)),  # none grain still naming a maker
        ("none", _target(manufacturer_key=None, model_number=None)),  # none grain naming a family
        ("family", _target()),  # family grain carrying a model number
        ("model", _target(variant=VARIANT)),  # model grain carrying variant identity
    ],
)
def test_grain_target_inconsistency_is_rejected(grain: str, target: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CorpusEntry.model_validate(_entry(expected_grain=grain, expected_target=target))


@pytest.mark.parametrize("source", ["demo", "amazon", "serverpartdeals ", "ServerPartDeals"])
def test_unknown_source_key_is_rejected(source: str) -> None:
    """SA-002: no parallel short-name namespace — only the five real registry keys."""
    payload = _entry()
    payload["source"] = source
    with pytest.raises(ValidationError):
        CorpusEntry.model_validate(payload)


def test_source_whitelist_tracks_the_adapter_registry() -> None:
    """Cross-file contract with `acquisition.sources.ADAPTERS`: the corpus keys are
    the real registry keys minus the `demo` test adapter, which never harvests."""
    assert frozenset(ADAPTERS) - {"demo"} == CORPUS_SOURCE_KEYS
    assert len(CORPUS_SOURCE_KEYS) == 5


def test_unknown_audit_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CorpusEntry.model_validate(_entry(audit_status="owner_glanced_at_it"))


def test_loader_normalizes_display_values_through_production_normalizers() -> None:
    """SA-003: labels hold display values; the normalized join keys come from
    `canonicalize_title` / `normalize_alias_text`, never a hand-authored form."""
    entry = CorpusEntry.model_validate(_entry())
    assert entry.label.expected_target.family_norm == "exos x18"
    assert entry.label.expected_target.model_norm == "st18000nm000j"
    # manufacturer_key is already the stored controlled key — never canonicalized.
    assert entry.label.expected_target.manufacturer_key == "seagate"


def test_malformed_json_line_is_rejected_with_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    good = SYNTHETIC_JSONL.read_text(encoding="utf-8").splitlines()[0]
    path.write_text(f"{good}\n{{not json\n", encoding="utf-8")
    with pytest.raises(CorpusFormatError, match="line 2"):
        load_corpus(path)


def test_invalid_entry_reports_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text('{"id": "x", "source": "amazon"}\n', encoding="utf-8")
    with pytest.raises(CorpusFormatError, match="line 1"):
        load_corpus(path)


def test_duplicate_entry_ids_are_rejected(tmp_path: Path) -> None:
    """Ids key the prediction↔label join in the evaluator; a duplicate would make
    one entry silently shadow another in the precision denominator."""
    path = tmp_path / "dupes.jsonl"
    line = CorpusEntry.model_validate(_entry()).model_dump_json()
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(CorpusFormatError, match="duplicate"):
        load_corpus(path)


def test_duplicate_source_listing_keys_are_rejected(tmp_path: Path) -> None:
    """SA-004: a repeated (source, source_listing_key) would make upsert_listing
    reuse one Listing, scoring the second entry as a rung-0 re-observation instead
    of a first observation — and collide on the snapshot's observation key."""
    path = tmp_path / "same-key.jsonl"
    first = _entry()
    second = _entry()
    second["id"] = "spd-0002"
    line_a = CorpusEntry.model_validate(first).model_dump_json()
    line_b = CorpusEntry.model_validate(second).model_dump_json()
    path.write_text(f"{line_a}\n{line_b}\n", encoding="utf-8")
    with pytest.raises(CorpusFormatError, match="duplicate \\(source, source_listing_key\\)"):
        load_corpus(path)


def test_same_listing_key_on_a_different_source_is_allowed(tmp_path: Path) -> None:
    """Listing keys are source-local: the DB unique constraint is per source site."""
    path = tmp_path / "cross-source.jsonl"
    first = _entry()
    second = _entry()
    second["id"] = "ghd-0001"
    second["source"] = "goharddrive"
    line_a = CorpusEntry.model_validate(first).model_dump_json()
    line_b = CorpusEntry.model_validate(second).model_dump_json()
    path.write_text(f"{line_a}\n{line_b}\n", encoding="utf-8")
    assert len(load_corpus(path)) == 2


def test_fixture_source_listing_keys_are_unique() -> None:
    entries = load_corpus(SYNTHETIC_JSONL)
    pairs = {(e.source, e.listing.source_listing_key) for e in entries}
    assert len(pairs) == len(entries)


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "padded.jsonl"
    line = CorpusEntry.model_validate(_entry()).model_dump_json()
    path.write_text(f"\n{line}\n\n", encoding="utf-8")
    assert len(load_corpus(path)) == 1


def test_synthetic_fixture_loads_and_covers_every_source_and_grain() -> None:
    entries = load_corpus(SYNTHETIC_JSONL)
    assert {e.source for e in entries} == CORPUS_SOURCE_KEYS
    assert {str(e.label.expected_grain) for e in entries} == {"none", "family", "model", "variant"}


def test_synthetic_meta_matches_the_fixture() -> None:
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.source] = counts.get(entry.source, 0) + 1
    assert meta.source_counts == counts
    assert sum(meta.audit_rollup.values()) == len(entries)
    assert meta.harvested_to >= meta.harvested_from


def _meta_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "corpus_version": "v1",
        "harvested_from": "2026-07-01",
        "harvested_to": "2026-07-02",
        "observed_at": "2026-07-02T00:00:00Z",
        "source_counts": {"serverpartdeals": 1},
        "matcher_version": "2026.07.3",
        "audit_rollup": {"claude_draft": 1},
    }
    payload.update(overrides)
    return payload


def _write_meta(tmp_path: Path, **overrides: Any) -> Path:
    path = tmp_path / "x.meta.json"
    path.write_text(json.dumps(_meta_payload(**overrides)), encoding="utf-8")
    return path


def test_meta_rejects_a_source_count_outside_the_whitelist(tmp_path: Path) -> None:
    with pytest.raises(CorpusFormatError):
        load_meta(_write_meta(tmp_path, source_counts={"amazon": 1}))


def test_meta_requires_a_timezone_aware_observed_at(tmp_path: Path) -> None:
    """A naive stamp would be interpreted against the runner's local zone, which is
    exactly the wall-clock dependency observed_at exists to remove."""
    with pytest.raises(CorpusFormatError):
        load_meta(_write_meta(tmp_path, observed_at="2026-07-02T00:00:00"))


def test_meta_requires_observed_at(tmp_path: Path) -> None:
    payload = _meta_payload()
    del payload["observed_at"]
    path = tmp_path / "no-observed-at.meta.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusFormatError):
        load_meta(path)


@pytest.mark.parametrize("currency", ["EUR", "GBP", "CAD"])
def test_non_usd_entry_is_rejected(currency: str) -> None:
    """Corpus v1 is USD-only: an FX-dependent price would make the verdict depend
    on the rate cache at run time."""
    payload = _entry()
    payload["listing"]["currency"] = currency
    with pytest.raises(ValidationError):
        CorpusEntry.model_validate(payload)


def test_audit_sample_selection_is_reproducible_and_order_independent() -> None:
    ids = [f"e-{n:03d}" for n in range(23)]
    sample = select_audit_sample(ids, "v1")
    assert len(sample) == 5  # ceil(0.20 * 23)
    assert set(sample) <= set(ids)
    assert select_audit_sample(reversed(ids), "v1") == sample  # file order is irrelevant
    assert select_audit_sample(ids, "v2") != sample  # a new revision draws a new sample


def test_audit_sample_of_the_synthetic_fixture_is_covered() -> None:
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)
    sample = select_audit_sample((e.id for e in entries), meta.corpus_version)
    assert len(sample) == 2  # ceil(0.20 * 10)
    audited = {e.id for e in entries if e.label.audit_status is not AuditStatus.CLAUDE_DRAFT}
    assert set(sample) <= audited


def test_audit_status_values_are_the_design_set() -> None:
    assert {str(status) for status in AuditStatus} == {
        "claude_draft",
        "owner_confirmed",
        "owner_corrected",
    }
