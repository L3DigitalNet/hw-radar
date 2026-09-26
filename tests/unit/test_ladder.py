"""C.3.2 ladder golden verdict table — pure, no DB. Each case is one spec rule."""

from hw_radar.matching import ladder
from hw_radar.matching.types import (
    Attribute,
    DecodeResult,
    ExtractedAttributes,
    Grain,
    MpnCandidate,
    Provenance,
    TokenKind,
)

_TB = 1_000_000_000_000


def _attr[T](value: T) -> Attribute[T]:
    return Attribute(value=value, confidence=0.9, layer="test")


def _cand(token: str, kind: TokenKind = TokenKind.MANUFACTURER_MPN) -> MpnCandidate:
    return MpnCandidate(raw=token, normalized=token, kind=kind, confidence=0.9)


_MODEL_TARGET = ladder.TargetRef(grain=Grain.MODEL, family_id=1, model_id=10)
_SPEC_16TB = ladder.HardAttrs(capacity_bytes=16 * _TB, interface="sata")


def _model_hit(
    source_kind: str = "catalog_authoritative",
    alias_type: str = "mpn",
    brand: str = "seagate",
    hard: ladder.HardAttrs = _SPEC_16TB,
    target: ladder.TargetRef = _MODEL_TARGET,
    candidate_kind: TokenKind = TokenKind.MANUFACTURER_MPN,
    candidate_vendor: str = "seagate",
) -> ladder.AliasHit:
    return ladder.AliasHit(
        target=target,
        source_kind=source_kind,
        alias_type=alias_type,
        brand=brand,
        hard_attrs=hard,
        candidate_kind=candidate_kind,
        candidate_vendor=candidate_vendor,
    )


def test_rung0_clean_reobservation_inherits() -> None:
    prior = ladder.PriorResolution(target=_MODEL_TARGET, confidence=0.98, hard_attrs=_SPEC_16TB)
    extracted = ExtractedAttributes(capacity_bytes=_attr(16 * _TB))
    v = ladder.decide(extracted, [], prior, [], None)
    assert v.outcome is ladder.Outcome.ACCEPT
    assert v.rung == 0 and v.method == "source_alias"
    assert v.target == _MODEL_TARGET and v.confidence == 0.98


def test_rung0_contradiction_forces_review_survives_relist_abuse() -> None:
    prior = ladder.PriorResolution(target=_MODEL_TARGET, confidence=0.98, hard_attrs=_SPEC_16TB)
    extracted = ExtractedAttributes(capacity_bytes=_attr(14 * _TB))
    v = ladder.decide(extracted, [], prior, [], None)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.rung == 0
    assert v.evidence["veto"] == ["capacity"]


def test_rung1_exact_alias_accepts_at_alias_grain() -> None:
    extracted = ExtractedAttributes(brand=_attr("seagate"), capacity_bytes=_attr(16 * _TB))
    v = ladder.decide(extracted, [_cand("st16000nm001g")], None, [_model_hit()], None)
    assert v.outcome is ladder.Outcome.ACCEPT
    assert v.rung == 1 and v.method == "exact_alias"
    assert v.grain is Grain.MODEL and v.confidence == 0.98


def test_rung1_capacity_contradiction_never_merges() -> None:
    extracted = ExtractedAttributes(capacity_bytes=_attr(14 * _TB))
    v = ladder.decide(extracted, [_cand("st16000nm001g")], None, [_model_hit()], None)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.evidence["veto"] == ["capacity"]


def test_rung1_capacity_tolerance_absorbs_rounding_only() -> None:
    almost = ExtractedAttributes(
        capacity_bytes=_attr(16_000_000_000_000 - 1_000_000)  # rounding noise
    )
    v = ladder.decide(almost, [_cand("st16000nm001g")], None, [_model_hit()], None)
    assert v.outcome is ladder.Outcome.ACCEPT


def test_rung1_conflicting_targets_go_to_review() -> None:
    other = ladder.TargetRef(grain=Grain.MODEL, family_id=2, model_id=20)
    v = ladder.decide(
        ExtractedAttributes(),
        [_cand("dellpn123")],
        None,
        [_model_hit(), _model_hit(target=other)],
        None,
    )
    assert v.outcome is ladder.Outcome.REVIEW


def test_rung1_oem_fanout_within_one_family_collapses_to_family() -> None:
    sibling = ladder.TargetRef(grain=Grain.MODEL, family_id=1, model_id=11)
    hits = [
        _model_hit(alias_type="oem_pn", source_kind="listing_derived"),
        _model_hit(alias_type="oem_pn", source_kind="listing_derived", target=sibling),
    ]
    v = ladder.decide(ExtractedAttributes(), [_cand("x477ar6", TokenKind.OEM_PN)], None, hits, None)
    assert v.outcome is ladder.Outcome.ACCEPT
    assert v.grain is Grain.FAMILY
    assert v.target is not None and v.target.family_id == 1


def test_rung1_brand_mismatch_on_every_hit_reviews() -> None:
    # A contradicted exact alias is a conflict, not a miss (review F3; the full
    # grammar-fallthrough case lives in test_ladder_brand_contradiction.py).
    extracted = ExtractedAttributes(brand=_attr("toshiba"))
    v = ladder.decide(extracted, [_cand("st16000nm001g")], None, [_model_hit()], None)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.rung == 1


def test_rung1_brandless_unknown_code_collision_reviews_never_accepts() -> None:
    # CR-003: a bare normalized-text collision from an unknown-shaped token with
    # NO brand evidence must not enter the price history as an exact alias.
    hit = _model_hit(candidate_kind=TokenKind.UNKNOWN_CODE, candidate_vendor="")
    v = ladder.decide(
        ExtractedAttributes(), [_cand("abc123xyz99", TokenKind.UNKNOWN_CODE)], None, [hit], None
    )
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.evidence["no_brand_evidence"] is True


def test_rung1_vendor_shaped_token_is_brand_evidence_without_title_brand() -> None:
    # 'ST16000NM001G' implies Seagate by shape — that satisfies the C.3.2
    # 'brand + MPN' trigger even when the title never says 'seagate'.
    v = ladder.decide(ExtractedAttributes(), [_cand("st16000nm001g")], None, [_model_hit()], None)
    assert v.outcome is ladder.Outcome.ACCEPT
    assert v.rung == 1


def test_brand_equivalence_wd_hgst_sandisk() -> None:
    assert ladder.brands_consistent("western_digital", "hgst")
    assert ladder.brands_consistent("hgst", "western_digital")
    assert not ladder.brands_consistent("seagate", "toshiba")
    assert ladder.brands_consistent(None, "seagate")  # unknown never vetoes


def test_rung2_decode_attaches_at_family_provisional() -> None:
    decoded = DecodeResult(
        vendor="seagate",
        family_name="exos",
        capacity_bytes=16 * _TB,
        generation="g",
        provenance=Provenance.CORROBORATED_COMMUNITY,
        rule="st:nm",
    )
    extracted = ExtractedAttributes(capacity_bytes=_attr(16 * _TB))
    v = ladder.decide(extracted, [_cand("st16000nm999x")], None, [], decoded)
    assert v.outcome is ladder.Outcome.ACCEPT
    assert v.rung == 2 and v.method == "mpn_decode"
    assert v.grain is Grain.FAMILY
    assert v.target is not None and v.target.family_key == ("seagate", "exos")
    assert v.confidence == 0.85
    assert v.evidence["provisional"] is True


def test_rung2_decoder_vs_title_capacity_conflict_reviews() -> None:
    decoded = DecodeResult(
        vendor="seagate",
        family_name="exos",
        capacity_bytes=16 * _TB,
        generation=None,
        provenance=Provenance.CORROBORATED_COMMUNITY,
        rule="st:nm",
    )
    extracted = ExtractedAttributes(capacity_bytes=_attr(14 * _TB))
    v = ladder.decide(extracted, [_cand("st16000nm999x")], None, [], decoded)
    assert v.outcome is ladder.Outcome.REVIEW


def test_no_signals_yields_none_with_hypothesis() -> None:
    v = ladder.decide(ExtractedAttributes(), [_cand("st16000nm001g")], None, [], None)
    assert v.outcome is ladder.Outcome.NONE
    assert v.grain is Grain.NONE
    assert v.evidence["mpn_hypothesis"] == "st16000nm001g"
