"""Matcher regressions from the round-4 cross-agent review (s7).

R3-A residual: a structured MPN whose raw text is not MPN-shaped ("WD40 EFPX")
must not hide the title's MANUFACTURER_MPN occurrence of the same token. R4-A:
the identifier set an automated prior was decided on (ladder
.identity_identifiers; the resolver side is tests/db/test_resolver_prior_
identifiers.py). R4-B: a review-only retail PN whose catalog model contradicts
the proposed target vetoes it at every rung. R4-C: repair/spares preambles after
a leading "for" are not reference spans. R4-D: coordinated Xeon model mentions
set the CPU multi_model marker."""

from __future__ import annotations

import pytest

from hw_radar.matching import grammars, ladder, mpn, vocab
from hw_radar.matching.normalize import (
    DRIVE_REFERENCE_PHRASE,
    canonicalize_title,
    mask_reference_spans,
)
from hw_radar.matching.rules import cpu
from hw_radar.matching.types import Grain, TokenKind

_TB = 1_000_000_000_000
_RED_PLUS = ("western_digital", "red plus")
_ULTRASTAR = ("western_digital", "ultrastar")


def _hit(
    normalized: str,
    model_id: int,
    *,
    brand: str = "western_digital",
    review_only: bool = False,
    hard_attrs: ladder.HardAttrs | None = None,
    family_id: int = 3,
) -> ladder.AliasHit:
    return ladder.AliasHit(
        target=ladder.TargetRef(grain=Grain.MODEL, family_id=family_id, model_id=model_id),
        source_kind="catalog_authoritative",
        alias_type="retail_pn" if review_only else "mpn",
        brand=brand,
        hard_attrs=hard_attrs or ladder.HardAttrs(),
        candidate_kind=TokenKind.UNKNOWN_CODE if review_only else TokenKind.MANUFACTURER_MPN,
        candidate_vendor="" if review_only else brand,
        candidate_normalized=normalized,
        candidate_review_only=review_only,
    )


def _drive_decide(
    title: str,
    hits: list[ladder.AliasHit],
    *,
    prior: ladder.PriorResolution | None = None,
    structured_mpn: str | None = None,
) -> ladder.Verdict:
    canonical = canonicalize_title(title)
    candidates = mpn.extract_candidates(canonical, structured_mpn=structured_mpn)
    decoded = next(
        (d for c in candidates if (d := grammars.decode(c.normalized)) is not None), None
    )
    return ladder.decide(
        vocab.extract(canonical), candidates, prior, hits, decoded, distinct_mpn_guard=True
    )


# R3-A residual: the title occurrence's kind survives deduplication.


def test_spaced_structured_mpn_keeps_the_title_occurrence_kind() -> None:
    candidates = mpn.extract_candidates(
        canonicalize_title("WD Red Plus WD40EFPX/WD40EFZX 4TB"), structured_mpn="WD40 EFPX"
    )
    efpx = next(c for c in candidates if c.normalized == "wd40efpx")
    # The structured occurrence wins (higher confidence) and its spaced raw
    # text is not MPN-shaped; the title's classification rides alongside.
    assert efpx.from_structured_field
    assert efpx.kind is TokenKind.UNKNOWN_CODE
    assert efpx.title_kind is TokenKind.MANUFACTURER_MPN


def test_spaced_structured_mpn_does_not_hide_the_second_title_mpn() -> None:
    verdict = _drive_decide(
        "WD Red Plus WD40EFPX/WD40EFZX 4TB", [_hit("wd40efpx", 40)], structured_mpn="WD40 EFPX"
    )
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.evidence["multiple_mpns"] == ["wd40efpx", "wd40efzx"]


def test_spaced_structured_mpn_alone_still_accepts() -> None:
    verdict = _drive_decide(
        "WD Red Plus 4TB NAS", [_hit("wd40efpx", 40)], structured_mpn="WD40 EFPX"
    )
    assert verdict.outcome is ladder.Outcome.ACCEPT


def test_structured_mpn_absent_from_the_title_has_no_title_kind() -> None:
    (only,) = mpn.extract_candidates(
        canonicalize_title("WD Red Plus 4TB"), structured_mpn="WD40EFPX"
    )
    assert only.title_kind is None


# R4-A: the identity-bearing identifier set.


def test_identity_identifiers_cover_mpns_hits_and_decodable_tokens() -> None:
    canonical = canonicalize_title("WD Ultrastar WUH722424ALE6L1 / 0F62796 24TB SATA X9Z9Q9")
    candidates = mpn.extract_candidates(canonical, structured_mpn="ST12000 NE0008")
    hits = [_hit("0f62796", 4, review_only=True)]
    # MPN-shaped title token, alias-hitting retail PN, decodable (though not
    # MPN-shaped) structured token; the hitless code token says nothing.
    assert ladder.identity_identifiers(candidates, hits, grammars.decode) == [
        "0f62796",
        "st12000ne0008",
        "wuh722424ale6l1",
    ]


def test_identity_identifiers_ignore_cosmetic_title_edits() -> None:
    def ids(title: str) -> list[str]:
        candidates = mpn.extract_candidates(canonicalize_title(title))
        return ladder.identity_identifiers(candidates, [], grammars.decode)

    assert ids("Seagate ST12000NE0008 12TB") == ids("Seagate IronWolf Pro ST12000NE0008 12TB NAS")
    assert ids("Seagate ST12000NE0008 12TB") != ids("Seagate ST12000NE0009 12TB")


# R4-B: a review-only hit vetoes an incompatible target at every rung.

_HC580_24TB = ladder.HardAttrs(capacity_bytes=24 * _TB, family=_ULTRASTAR)


def test_retail_pn_of_another_family_vetoes_a_grammar_accept() -> None:
    # EFZX is unseeded, so the grammar proposes Red Plus 4TB at rung 2, while
    # the authoritative 0F62796 alias names a 24TB Ultrastar HC580.
    verdict = _drive_decide(
        "WD Red Plus WD40EFZX 4TB 0F62796",
        [_hit("0f62796", 4, review_only=True, hard_attrs=_HC580_24TB)],
    )
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 2
    assert verdict.evidence["review_only_alias_conflict"] == {
        "identifiers": ["0f62796"],
        "fields": ["capacity", "family"],
    }


def test_retail_pn_of_another_family_vetoes_a_family_prior() -> None:
    prior = ladder.PriorResolution(
        target=ladder.TargetRef(grain=Grain.FAMILY, family_id=7),
        confidence=0.85,
        hard_attrs=ladder.HardAttrs(family=_RED_PLUS),
    )
    verdict = _drive_decide(
        "WD Red Plus 4TB NAS 0F62796",
        [_hit("0f62796", 4, review_only=True, hard_attrs=_HC580_24TB)],
        prior=prior,
    )
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 0
    assert verdict.evidence["review_only_alias_conflict"] == {
        "identifiers": ["0f62796"],
        "fields": ["family"],
    }


def test_retail_pn_of_another_family_vetoes_an_exact_accept() -> None:
    # The exact EFPX hit names the only model among model-carrying hits whose
    # identifier grounds an accept; the retail PN names another family's model.
    # conflicting_alias_models would also fire (two models); the new veto adds
    # its own reason regardless of which other guard fires.
    verdict = _drive_decide(
        "WD Red Plus WD40EFPX 4TB 0F62796",
        [
            _hit(
                "wd40efpx",
                40,
                hard_attrs=ladder.HardAttrs(capacity_bytes=4 * _TB, family=_RED_PLUS),
            ),
            _hit("0f62796", 4, review_only=True, hard_attrs=_HC580_24TB),
        ],
    )
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 1
    assert verdict.evidence["review_only_alias_conflict"] == {
        "identifiers": ["0f62796"],
        "fields": ["capacity", "family"],
    }


def test_compatible_retail_pn_does_not_veto_the_grammar_accept() -> None:
    # Same line and capacity as the decode: the retail PN corroborates it (and
    # still never grounds the accept itself — rung 2 is the grammar's).
    verdict = _drive_decide(
        "WD Red Plus WD40EFZX 4TB 0F99999",
        [
            _hit(
                "0f99999",
                41,
                review_only=True,
                hard_attrs=ladder.HardAttrs(capacity_bytes=4 * _TB, family=_RED_PLUS),
            )
        ],
    )
    assert verdict.outcome is ladder.Outcome.ACCEPT
    assert verdict.rung == 2


def test_less_specific_family_is_compatible_with_a_retail_pn_series() -> None:
    # An HGST-prefix decode proposes generic "ultrastar"; an HC580 retail PN
    # under the same family name is a refinement, not a contradiction.
    verdict = _drive_decide(
        "WD Ultrastar WUH722424ALE6L4 0F62796",
        [_hit("0f62796", 4, review_only=True, hard_attrs=_HC580_24TB)],
    )
    assert verdict.outcome is ladder.Outcome.ACCEPT
    assert verdict.rung == 2


# R4-C: repair/spares preambles are not reference spans.


@pytest.mark.parametrize(
    "title",
    [
        "For spares or repair: Seagate ST12000NE0008 12TB HDD",
        "FOR SPARES Seagate ST12000NE0008 12TB",
        "For parts or repair Seagate ST12000NE0008 12TB",
        "For repair - Seagate ST12000NE0008 12TB",
    ],
)
def test_repair_preamble_keeps_identity(title: str) -> None:
    canonical = canonicalize_title(title)
    assert "st12000ne0008" in {c.normalized for c in mpn.extract_candidates(canonical)}
    brand = vocab.extract(canonical).brand
    assert brand is not None and brand.value == "seagate"


def test_leading_for_before_a_brand_still_masks() -> None:
    canonical = canonicalize_title("FOR Seagate Exos X14 12TB SATA ST12000NM0008 NEW")
    assert "st12000nm0008" not in mask_reference_spans(canonical, DRIVE_REFERENCE_PHRASE)


# R4-D: coordinated CPU model mentions.


def _multi_model(title: str) -> str | None:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    return payload.multi_model.value if payload.multi_model is not None else None


@pytest.mark.parametrize(
    ("title", "models"),
    [
        ("Intel Xeon Gold 6338 / Platinum 8358 32-Core LGA4189", "6338 8358"),
        ("Intel Xeon Gold 6338/6348 LGA4189", "6338 6348"),
        ("Intel Xeon Silver 4314 or Xeon Silver 4316 LGA4189", "4314 4316"),
        ("Intel Xeon Platinum 8480+ / Gold 6448Y LGA4677", "6448y 8480+"),
        ("Intel Xeon E5-2680 v4 / E5-2690 v4 LGA2011-3", "e52680v4 e52690v4"),
        ("Intel Core i7-12700K / Core i9-12900K LGA1700", "i712700k i912900k"),
    ],
)
def test_coordinated_cpu_models_set_the_marker(title: str, models: str) -> None:
    assert _multi_model(title) == models


@pytest.mark.parametrize(
    "title",
    [
        "Intel Xeon Gold 6338 32-Core LGA4189",
        "Intel Xeon Gold 6338 2.0GHz 32C/64T 205W LGA4189",
        "2x Intel Xeon Gold 6338 Gold 6338 LGA4189",
        "Intel Xeon Platinum 8480+ 56-Core LGA4677",
        "Intel Xeon E5-2680 v4 14-Core",
        "AMD EPYC 7763 64-Core SP3",
    ],
)
def test_one_cpu_model_sets_no_marker(title: str) -> None:
    assert _multi_model(title) is None


def test_coordinated_xeon_models_veto_every_rung() -> None:
    title = canonicalize_title("Intel Xeon Gold 6338 / Platinum 8358 32-Core LGA4189")
    candidates = cpu.extract_candidates(title)
    hits = [
        _hit("xeongold6338", 6338, brand="intel"),
        _hit("6338", 6338, brand="intel"),
    ]
    extracted = cpu.extract(title)
    rung1 = ladder.decide(extracted, candidates, None, hits, None, veto=cpu.veto)
    assert rung1.outcome is ladder.Outcome.REVIEW
    assert rung1.evidence["veto"] == ["multi_model"]
    prior = ladder.PriorResolution(
        target=ladder.TargetRef(grain=Grain.MODEL, family_id=3, model_id=6338),
        confidence=0.98,
        hard_attrs=ladder.HardAttrs(),
    )
    rung0 = ladder.decide(extracted, candidates, prior, hits, None, veto=cpu.veto)
    assert rung0.outcome is ladder.Outcome.REVIEW
    assert rung0.rung == 0
    assert rung0.evidence["veto"] == ["multi_model"]
