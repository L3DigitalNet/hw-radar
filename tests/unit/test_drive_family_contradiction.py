"""A title naming a sibling product line of the target's family reviews (s7 D2).

MS-1e rows ebay-0337 ("IronWolf Pro" title, ST16000VN001 decoding IronWolf) and
ebay-0451 ("Red Plus" title, WD40EZRX decoding Blue) auto-accepted the decoded
family. The rule is general, mirroring the brand-contradiction gate: at every
rung, a same-maker family in the title that is not the target's family, nor a
less specific name of it, is a conflict. A less specific title family
("IronWolf" for an IronWolf Pro drive, "Red" for Red Plus) stays compatible."""

import pytest

from hw_radar.matching import grammars, ladder, mpn, vocab
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.types import ExtractedAttributes, Grain

_TB = 1_000_000_000_000


def _extract(title: str) -> ExtractedAttributes:
    return vocab.extract(canonicalize_title(title))


def _verdict(
    title: str,
    hits: list[ladder.AliasHit] | None = None,
    prior: ladder.PriorResolution | None = None,
) -> ladder.Verdict:
    canonical = canonicalize_title(title)
    candidates = mpn.extract_candidates(canonical)
    decoded = next(
        (d for c in candidates if (d := grammars.decode(c.normalized)) is not None), None
    )
    return ladder.decide(
        vocab.extract(canonical), candidates, prior, hits or [], decoded, distinct_mpn_guard=True
    )


@pytest.mark.parametrize(
    ("title", "decoded_family", "title_family"),
    [
        (
            "Seagate IronWolf Pro 16TB ST16000VN001 HDD 256MB BRAND NEW SEALED ORIGINAL BOX",
            "ironwolf",
            "ironwolf pro",
        ),
        (
            'Western Digital Red Plus 4TB 3.5" NAS SATA Internal HDD (WD40EZRX-00SPEBO)',
            "blue",
            "red plus",
        ),
    ],
)
def test_title_sibling_family_contradicting_the_decode_reviews(
    title: str, decoded_family: str, title_family: str
) -> None:
    verdict = _verdict(title)
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.grain is Grain.NONE
    assert verdict.rung == 2
    assert verdict.evidence["family_contradicts_decode"] == {
        "title_families": [title_family],
        "decoded_family": decoded_family,
    }


@pytest.mark.parametrize(
    ("title", "family_key"),
    [
        # Less specific title family: compatible, the decode stands.
        ("Seagate IronWolf 12TB NAS HDD ST12000NE0008", ("seagate", "ironwolf pro")),
        ("WD Red 4TB NAS Hard Drive WD40EFPX", ("western_digital", "red plus")),
        # Same family.
        ("WD Red Plus 4TB WD40EFPX", ("western_digital", "red plus")),
        ("HGST Ultrastar 7K4000 4TB HUS724040ALE640", ("hgst", "ultrastar")),
        # No family named: unknown never contradicts.
        ("Seagate 16TB NAS drive ST16000NE000", ("seagate", "ironwolf pro")),
    ],
)
def test_compatible_or_absent_title_family_keeps_the_decode(
    title: str, family_key: tuple[str, str]
) -> None:
    verdict = _verdict(title)
    assert verdict.outcome is ladder.Outcome.ACCEPT
    assert verdict.target is not None
    assert verdict.target.family_key == family_key


def test_family_named_only_in_a_reference_span_is_not_a_title_family() -> None:
    # "comparable to IronWolf Pro" cites another product; this drive's own
    # IronWolf decode must not be reviewed for it.
    verdict = _verdict("Seagate 8TB ST8000VN004 NAS HDD - comparable to IronWolf Pro")
    assert verdict.outcome is ladder.Outcome.ACCEPT
    assert verdict.target is not None
    assert verdict.target.family_key == ("seagate", "ironwolf")


def test_wd_colour_words_count_only_next_to_the_wd_brand() -> None:
    assert _extract("WD Gold 4TB WD4004FRYZ").family_mentions is not None
    # "gold" as a seller adjective is not the WD Gold line.
    assert _extract("Gold seller Seagate 4TB ST4000VN008").family_mentions is None


def _hit(family: tuple[str, str] | None) -> ladder.AliasHit:
    return ladder.AliasHit(
        target=ladder.TargetRef(grain=Grain.MODEL, family_id=7, model_id=70),
        source_kind="catalog_authoritative",
        alias_type="mpn",
        brand="seagate",
        hard_attrs=ladder.HardAttrs(capacity_bytes=12 * _TB, family=family),
        candidate_vendor="seagate",
        candidate_normalized="st12000nm0008",
    )


def test_exact_alias_hit_whose_family_the_title_contradicts_reviews() -> None:
    # Rung 1 must not bypass the rule: an exact seeded Exos alias in a title
    # that names IronWolf Pro is a conflict, never an accept (and never a
    # fall-through to rung 2).
    verdict = _verdict("Seagate IronWolf Pro 12TB ST12000NM0008", [_hit(("seagate", "exos"))])
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 1
    assert verdict.evidence["veto"] == ["family"]


def test_exact_alias_hit_with_a_compatible_title_family_accepts() -> None:
    verdict = _verdict("Seagate Exos X14 12TB ST12000NM0008", [_hit(("seagate", "exos"))])
    assert verdict.outcome is ladder.Outcome.ACCEPT
    assert verdict.rung == 1


def test_inherited_prior_whose_family_the_title_contradicts_reviews() -> None:
    prior = ladder.PriorResolution(
        target=ladder.TargetRef(grain=Grain.FAMILY, family_id=7),
        confidence=0.85,
        hard_attrs=ladder.HardAttrs(family=("seagate", "ironwolf")),
    )
    verdict = _verdict("Seagate IronWolf Pro 16TB ST16000VN001", prior=prior)
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 0
    assert verdict.evidence["veto"] == ["family"]


def test_another_makers_family_is_left_to_the_brand_gate() -> None:
    extracted = _extract("Seagate IronWolf Pro 16TB")
    assert ladder.family_conflicts(extracted, "western_digital", "red plus") == []


@pytest.mark.parametrize(
    ("title_family", "target_family", "compatible"),
    [
        ("ironwolf", "ironwolf pro", True),
        ("red", "red plus", True),
        ("ultrastar", "ultrastar", True),
        ("ironwolf pro", "ironwolf", False),
        ("red plus", "red pro", False),
        ("red", "redshift", False),  # whole-word prefix only
    ],
)
def test_family_compatibility_is_equal_or_less_specific(
    title_family: str, target_family: str, compatible: bool
) -> None:
    assert ladder.family_compatible(title_family, target_family) is compatible
