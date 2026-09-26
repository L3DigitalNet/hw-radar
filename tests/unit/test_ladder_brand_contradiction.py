"""A brand contradiction never falls through to a weaker rung (review F3).

Pins the exact-alias precedence guarantee: once an exact alias hit exists, the
listing's brand disagreeing with it is a conflict for review, never 'no catalog
hit' that lets rung 2 accept the same token's grammar family."""

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
_MPN = "st12000ne0008"
_IRONWOLF_PRO_MODEL = ladder.TargetRef(grain=Grain.MODEL, family_id=7, model_id=70)
# The committed IronWolf Pro seed's exact model is a 12TB SATA drive; the SAS
# listing below contradicts it, which only the rung-1 veto would ever see.
_SPEC_12TB_SATA = ladder.HardAttrs(capacity_bytes=12 * _TB, interface="sata")
_DECODE = DecodeResult(
    vendor="seagate",
    family_name="ironwolf pro",
    capacity_bytes=12 * _TB,
    generation=None,
    provenance=Provenance.VENDOR_OFFICIAL,
    rule="seagate:ne",
)


def _attr[T](value: T) -> Attribute[T]:
    return Attribute(value=value, confidence=0.9, layer="test")


def _cand() -> MpnCandidate:
    return MpnCandidate(
        raw=_MPN,
        normalized=_MPN,
        kind=TokenKind.MANUFACTURER_MPN,
        confidence=0.9,
        vendor_hint="seagate",
    )


def _seagate_hit() -> ladder.AliasHit:
    return ladder.AliasHit(
        target=_IRONWOLF_PRO_MODEL,
        source_kind="catalog_authoritative",
        alias_type="mpn",
        brand="seagate",
        hard_attrs=_SPEC_12TB_SATA,
        candidate_vendor="seagate",
    )


def _extracted(brand: str, interface: str) -> ExtractedAttributes:
    return ExtractedAttributes(
        brand=_attr(brand), capacity_bytes=_attr(12 * _TB), interface=_attr(interface)
    )


def test_brand_contradicted_exact_alias_goes_to_review_not_grammar() -> None:
    # 'Toshiba ST12000NE0008 12TB SAS': before the fix the Seagate hit was
    # filtered out and rung 2 accepted Seagate/IronWolf Pro on capacity alone.
    v = ladder.decide(_extracted("toshiba", "sas"), [_cand()], None, [_seagate_hit()], _DECODE)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.rung == 1
    assert v.target is None
    assert v.evidence["brand_contradicts_exact_alias"] == {
        "brand": "toshiba",
        "alias_brands": ["seagate"],
    }


def test_brand_contradicted_exact_alias_reviews_even_without_decode() -> None:
    v = ladder.decide(_extracted("toshiba", "sata"), [_cand()], None, [_seagate_hit()], None)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.rung == 1


def test_brand_consistent_exact_alias_still_accepts_at_rung1() -> None:
    v = ladder.decide(_extracted("seagate", "sata"), [_cand()], None, [_seagate_hit()], _DECODE)
    assert v.outcome is ladder.Outcome.ACCEPT
    assert v.rung == 1 and v.method == "exact_alias"
    assert v.target == _IRONWOLF_PRO_MODEL


def test_brand_consistent_exact_alias_with_contradicted_interface_reviews() -> None:
    v = ladder.decide(_extracted("seagate", "sas"), [_cand()], None, [_seagate_hit()], _DECODE)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.rung == 1
    assert v.evidence["veto"] == ["interface"]


def test_rung2_decode_contradicting_extracted_brand_reviews() -> None:
    v = ladder.decide(_extracted("toshiba", "sata"), [_cand()], None, [], _DECODE)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.rung == 2
    assert v.target is None
    assert v.evidence["brand_contradicts_decode"] == {"brand": "toshiba", "vendor": "seagate"}


def test_rung2_decode_accepts_with_consistent_or_absent_brand() -> None:
    for extracted in (
        _extracted("seagate", "sata"),
        ExtractedAttributes(capacity_bytes=_attr(12 * _TB)),
    ):
        v = ladder.decide(extracted, [_cand()], None, [], _DECODE)
        assert v.outcome is ladder.Outcome.ACCEPT
        assert v.rung == 2
        assert v.target is not None
        assert v.target.family_key == ("seagate", "ironwolf pro")


def test_rung2_brand_equivalence_group_is_not_a_contradiction() -> None:
    # WD absorbed HGST: an HGST-shaped decode under a WD-branded title attaches.
    hgst = DecodeResult(
        vendor="hgst",
        family_name="ultrastar",
        capacity_bytes=None,
        generation=None,
        provenance=Provenance.VENDOR_OFFICIAL,
        rule="hgst:test",
    )
    extracted = ExtractedAttributes(brand=_attr("western_digital"))
    v = ladder.decide(extracted, [_cand()], None, [], hgst)
    assert v.outcome is ladder.Outcome.ACCEPT
    assert v.rung == 2
