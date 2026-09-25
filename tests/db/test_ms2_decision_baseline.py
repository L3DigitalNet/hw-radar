"""MS-2 Slice A before/after oracle: pinned drive decisions (plan task A0).

Slice A adds category dispatch seams (registry, injectable ladder veto, category
hint, resolver dispatch) that must not change a single drive decision. These
snapshots were recorded on the UNMODIFIED pre-Slice-A code; every later Slice A
commit must pass them WITHOUT `--snapshot-update`. A diff here is a drive
decision change, never a snapshot to refresh.

The seeding helpers are copied verbatim from `test_ratification_corpus.py`
rather than imported, so this oracle cannot drift when that frozen file does.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from django.db import transaction
from syrupy.assertion import SnapshotAssertion

from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    DriveSpec,
    Manufacturer,
    MediaType,
    ProductAlias,
    ProductFamily,
    ProductModel,
    RetentionClass,
)
from hw_radar.matching import ladder
from hw_radar.matching.eval.corpus import load_corpus, load_meta
from hw_radar.matching.eval.evaluate import evaluate_corpus
from hw_radar.matching.normalize import canonicalize_title, normalize_alias_text
from hw_radar.matching.types import (
    Attribute,
    DecodeResult,
    ExtractedAttributes,
    Grain,
    MpnCandidate,
    Provenance,
    TokenKind,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "matching_corpus"
SYNTHETIC_JSONL = FIXTURE_DIR / "synthetic.jsonl"
SYNTHETIC_META = FIXTURE_DIR / "synthetic.meta.json"


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


def test_synthetic_corpus_decisions_baseline(
    seeded_catalog: None, snapshot: SnapshotAssertion
) -> None:
    """Every synthetic entry's production-resolver decision, end to end."""
    entries = load_corpus(SYNTHETIC_JSONL)
    meta = load_meta(SYNTHETIC_META)
    savepoint = transaction.savepoint()
    try:
        predictions = evaluate_corpus(entries, meta)
    finally:
        transaction.savepoint_rollback(savepoint)
    assert snapshot == [
        (
            p.entry_id,
            p.outcome,
            p.grain,
            p.rung,
            p.manufacturer_key,
            p.family_norm,
            p.model_norm,
            p.variant_tuple,
        )
        for p in predictions
    ]


# The ladder inputs below re-declare every `ladder.decide(...)` case in the frozen
# `tests/unit/test_ladder.py`, so the golden table is pinned field by field here
# rather than only through that file's partial assertions.
_TB = 1_000_000_000_000


def _attr[T](value: T) -> Attribute[T]:
    return Attribute(value=value, confidence=0.9, layer="test")


def _cand(token: str, kind: TokenKind = TokenKind.MANUFACTURER_MPN) -> MpnCandidate:
    return MpnCandidate(raw=token, normalized=token, kind=kind, confidence=0.9)


_MODEL_TARGET = ladder.TargetRef(grain=Grain.MODEL, family_id=1, model_id=10)
_SIBLING_TARGET = ladder.TargetRef(grain=Grain.MODEL, family_id=1, model_id=11)
_OTHER_TARGET = ladder.TargetRef(grain=Grain.MODEL, family_id=2, model_id=20)
_SPEC_16TB = ladder.HardAttrs(capacity_bytes=16 * _TB, interface="sata")


def _model_hit(
    source_kind: str = "catalog_authoritative",
    alias_type: str = "mpn",
    target: ladder.TargetRef = _MODEL_TARGET,
    candidate_kind: TokenKind = TokenKind.MANUFACTURER_MPN,
    candidate_vendor: str = "seagate",
) -> ladder.AliasHit:
    return ladder.AliasHit(
        target=target,
        source_kind=source_kind,
        alias_type=alias_type,
        brand="seagate",
        hard_attrs=_SPEC_16TB,
        candidate_kind=candidate_kind,
        candidate_vendor=candidate_vendor,
    )


def _decoded(generation: str | None) -> DecodeResult:
    return DecodeResult(
        vendor="seagate",
        family_name="exos",
        capacity_bytes=16 * _TB,
        generation=generation,
        provenance=Provenance.CORROBORATED_COMMUNITY,
        rule="st:nm",
    )


_PRIOR = ladder.PriorResolution(target=_MODEL_TARGET, confidence=0.98, hard_attrs=_SPEC_16TB)

type _LadderCase = tuple[
    ExtractedAttributes,
    list[MpnCandidate],
    ladder.PriorResolution | None,
    list[ladder.AliasHit],
    DecodeResult | None,
]

_LADDER_CASES: dict[str, _LadderCase] = {
    "rung0_clean_reobservation": (
        ExtractedAttributes(capacity_bytes=_attr(16 * _TB)),
        [],
        _PRIOR,
        [],
        None,
    ),
    "rung0_contradiction": (
        ExtractedAttributes(capacity_bytes=_attr(14 * _TB)),
        [],
        _PRIOR,
        [],
        None,
    ),
    "rung1_exact_alias": (
        ExtractedAttributes(brand=_attr("seagate"), capacity_bytes=_attr(16 * _TB)),
        [_cand("st16000nm001g")],
        None,
        [_model_hit()],
        None,
    ),
    "rung1_capacity_contradiction": (
        ExtractedAttributes(capacity_bytes=_attr(14 * _TB)),
        [_cand("st16000nm001g")],
        None,
        [_model_hit()],
        None,
    ),
    "rung1_capacity_tolerance": (
        ExtractedAttributes(capacity_bytes=_attr(16_000_000_000_000 - 1_000_000)),
        [_cand("st16000nm001g")],
        None,
        [_model_hit()],
        None,
    ),
    "rung1_conflicting_targets": (
        ExtractedAttributes(),
        [_cand("dellpn123")],
        None,
        [_model_hit(), _model_hit(target=_OTHER_TARGET)],
        None,
    ),
    "rung1_oem_fanout": (
        ExtractedAttributes(),
        [_cand("x477ar6", TokenKind.OEM_PN)],
        None,
        [
            _model_hit(alias_type="oem_pn", source_kind="listing_derived"),
            _model_hit(alias_type="oem_pn", source_kind="listing_derived", target=_SIBLING_TARGET),
        ],
        None,
    ),
    "rung1_brand_mismatch": (
        ExtractedAttributes(brand=_attr("toshiba")),
        [_cand("st16000nm001g")],
        None,
        [_model_hit()],
        None,
    ),
    "rung1_brandless_unknown_code": (
        ExtractedAttributes(),
        [_cand("abc123xyz99", TokenKind.UNKNOWN_CODE)],
        None,
        [_model_hit(candidate_kind=TokenKind.UNKNOWN_CODE, candidate_vendor="")],
        None,
    ),
    "rung1_vendor_shaped_token": (
        ExtractedAttributes(),
        [_cand("st16000nm001g")],
        None,
        [_model_hit()],
        None,
    ),
    "rung2_decode": (
        ExtractedAttributes(capacity_bytes=_attr(16 * _TB)),
        [_cand("st16000nm999x")],
        None,
        [],
        _decoded("g"),
    ),
    "rung2_capacity_conflict": (
        ExtractedAttributes(capacity_bytes=_attr(14 * _TB)),
        [_cand("st16000nm999x")],
        None,
        [],
        _decoded(None),
    ),
    "no_signals": (
        ExtractedAttributes(),
        [_cand("st16000nm001g")],
        None,
        [],
        None,
    ),
}


def _target_identity(
    target: ladder.TargetRef | None,
) -> tuple[Grain, int | None, int | None, int | None, tuple[str, str] | None] | None:
    # Explicit identity fields, not the dataclass repr: Slice A adds an additive
    # TargetRef field that the ladder itself never sets, and a repr-based pin would
    # flag that as a decision change when no decision moved.
    if target is None:
        return None
    return (target.grain, target.family_id, target.model_id, target.variant_id, target.family_key)


def test_ladder_golden_baseline(snapshot: SnapshotAssertion) -> None:
    """Every golden ladder case's full verdict: outcome, grain, rung, method,
    target identity, confidence, and the complete evidence mapping."""
    pinned = {
        name: (
            verdict.outcome,
            verdict.grain,
            verdict.rung,
            verdict.method,
            _target_identity(verdict.target),
            verdict.confidence,
            sorted(verdict.evidence.items(), key=lambda item: item[0]),
        )
        for name, (extracted, candidates, prior, hits, decoded) in _LADDER_CASES.items()
        for verdict in [ladder.decide(extracted, candidates, prior, hits, decoded)]
    }
    assert snapshot == pinned
