"""Injectable ladder veto (MS2-D-02): `decide(..., veto=...)` consults the supplied
veto at all three sites — rung-0 prior, rung-1 single target, rung-1 OEM fan-out —
and defaults to the drive `contradictions` veto so drive callers are unchanged."""

import inspect

from hw_radar.matching import ladder
from hw_radar.matching.types import (
    Attribute,
    ExtractedAttributes,
    Grain,
    MpnCandidate,
    TokenKind,
)

_TB = 1_000_000_000_000
_MODEL_TARGET = ladder.TargetRef(grain=Grain.MODEL, family_id=1, model_id=10)
_SPEC_16TB = ladder.HardAttrs(capacity_bytes=16 * _TB, interface="sata")


def _probe(extracted: ExtractedAttributes, catalog: ladder.HardAttrs) -> list[str]:
    return ["probe"]


def _hit(*, alias_type: str = "mpn", target: ladder.TargetRef = _MODEL_TARGET) -> ladder.AliasHit:
    return ladder.AliasHit(
        target=target,
        source_kind="catalog_authoritative",
        alias_type=alias_type,
        brand="seagate",
        hard_attrs=_SPEC_16TB,
        candidate_vendor="seagate",
    )


def _cand(token: str, kind: TokenKind = TokenKind.MANUFACTURER_MPN) -> MpnCandidate:
    return MpnCandidate(raw=token, normalized=token, kind=kind, confidence=0.9)


# Every input below ACCEPTs under the default veto (the extracted capacity agrees
# with the catalog), so a REVIEW can only come from the injected probe.
_AGREEING = ExtractedAttributes(
    brand=Attribute(value="seagate", confidence=0.9, layer="test"),
    capacity_bytes=Attribute(value=16 * _TB, confidence=0.9, layer="test"),
)


def test_custom_veto_is_consulted_at_rung1() -> None:
    args = (_AGREEING, [_cand("st16000nm001g")], None, [_hit()], None)
    assert ladder.decide(*args).outcome is ladder.Outcome.ACCEPT
    v = ladder.decide(*args, veto=_probe)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.rung == 1
    assert v.evidence["veto"] == ["probe"]


def test_custom_veto_is_consulted_at_rung0_prior() -> None:
    prior = ladder.PriorResolution(target=_MODEL_TARGET, confidence=0.98, hard_attrs=_SPEC_16TB)
    assert ladder.decide(_AGREEING, [], prior, [], None).outcome is ladder.Outcome.ACCEPT
    v = ladder.decide(_AGREEING, [], prior, [], None, veto=_probe)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.rung == 0
    assert v.evidence["veto"] == ["probe"]


def test_custom_veto_filters_oem_fanout() -> None:
    sibling = ladder.TargetRef(grain=Grain.MODEL, family_id=1, model_id=11)
    hits = [_hit(alias_type="oem_pn"), _hit(alias_type="oem_pn", target=sibling)]
    args = (_AGREEING, [_cand("x477ar6", TokenKind.OEM_PN)], None, hits, None)
    accepted = ladder.decide(*args)
    assert accepted.outcome is ladder.Outcome.ACCEPT
    assert accepted.grain is Grain.FAMILY
    # A veto that trips on every hit leaves no clean fan-out member, so the
    # family collapse is refused and the conflict goes to review.
    v = ladder.decide(*args, veto=_probe)
    assert v.outcome is ladder.Outcome.REVIEW
    assert v.rung == 1
    assert v.evidence["conflicting_targets"] == 2


def test_default_veto_is_contradictions() -> None:
    assert inspect.signature(ladder.decide).parameters["veto"].default is ladder.contradictions
