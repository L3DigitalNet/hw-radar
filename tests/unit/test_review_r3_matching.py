"""Matcher regressions from the round-3 cross-agent review (s7).

F1/F4 residual: an unclosed "(" opened inside a reference span let the first
internal comma end the span. N3/R3-A: an accept must not stand when the
listing's identifiers hit aliases of two different catalog models. R3-B: a
leading "for sale" is a sales preamble. R3-D: CPU name candidates need the
complete model token. R3-E: spaced "mother board" is a board listing."""

from __future__ import annotations

import pytest

from hw_radar.matching import grammars, ladder, mpn, vocab
from hw_radar.matching.normalize import (
    DRIVE_REFERENCE_PHRASE,
    canonicalize_listing_text,
    canonicalize_title,
    mask_reference_spans,
)
from hw_radar.matching.rules import cpu
from hw_radar.matching.types import Grain, TokenKind


def _mpns(text: str) -> set[str]:
    return {c.normalized for c in mpn.extract_candidates(text)}


# F1/F4 residual: an unmatched "(" inside a reference span.


def test_unclosed_paren_comma_does_not_release_the_cited_mpn() -> None:
    title = "WL 12TB comparable to (Seagate, ST12000NE0008"
    assert _mpns(canonicalize_title(title)) == set()
    assert _mpns(canonicalize_listing_text(title, "New")) == set()
    assert vocab.extract(canonicalize_title(title)).brand is None


def test_unclosed_paren_comma_does_not_release_condition_evidence() -> None:
    canonical = canonicalize_listing_text(
        "Seagate ST12000NE0008 12TB comparable to (used, factory recertified drives", "New"
    )
    assert _mpns(canonical) == {"st12000ne0008"}
    terms = vocab.offer_terms(canonical)
    assert terms.condition is not None and terms.condition.value == "new"
    assert terms.recert_channel is None


def test_unclosed_paren_without_a_label_masks_to_the_end() -> None:
    canonical = canonicalize_title("Seagate ST12000NE0008 comparable to (used, factory recertified")
    assert mask_reference_spans(canonical).rstrip() == "seagate st12000ne0008"


def test_label_closing_paren_only_for_phrase_bearing_titles() -> None:
    # The historical canonical text of a title with no reference phrase is
    # unchanged, unclosed parenthesis or not.
    assert canonicalize_listing_text("Seagate ST12000NE0008 (12TB", "New") == (
        canonicalize_title("Seagate ST12000NE0008 (12TB New")
    )


# R3-B: a leading "for sale" preamble.


@pytest.mark.parametrize(
    "title",
    [
        "For sale: Seagate ST12000NE0008 12TB",
        "FOR SALE Seagate ST12000NE0008 12TB",
        "For Sale - Seagate ST12000NE0008 12TB",
    ],
)
def test_for_sale_preamble_keeps_identity(title: str) -> None:
    canonical = canonicalize_title(title)
    assert _mpns(canonical) == {"st12000ne0008"}
    brand = vocab.extract(canonical).brand
    assert brand is not None and brand.value == "seagate"


def test_leading_for_still_masks_a_compatible_part() -> None:
    canonical = canonicalize_title("FOR Seagate Exos X14 12TB SATA ST12000NM0008 NEW")
    assert "st12000nm0008" not in _mpns(canonical)
    assert "st12000nm0008" not in mask_reference_spans(canonical, DRIVE_REFERENCE_PHRASE)


# N3 + R3-A: identifiers of two different catalog models never accept.


def _hit(
    normalized: str, model_id: int, *, brand: str = "western_digital", review_only: bool = False
) -> ladder.AliasHit:
    return ladder.AliasHit(
        target=ladder.TargetRef(grain=Grain.MODEL, family_id=3, model_id=model_id),
        source_kind="catalog_authoritative",
        alias_type="mpn",
        brand=brand,
        hard_attrs=ladder.HardAttrs(),
        candidate_vendor=brand,
        candidate_normalized=normalized,
        candidate_review_only=review_only,
    )


def _prior(model_id: int) -> ladder.PriorResolution:
    return ladder.PriorResolution(
        target=ladder.TargetRef(grain=Grain.MODEL, family_id=3, model_id=model_id),
        confidence=0.98,
        hard_attrs=ladder.HardAttrs(),
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


def test_structured_duplicate_keeps_the_title_occurrence() -> None:
    # R3-A bypass 1: dedup kept only the structured EFPX, so the title looked
    # like it named one MPN (EFZX) and the EFPX exact hit accepted.
    verdict = _drive_decide(
        "WD Red Plus WD40EFPX/WD40EFZX 4TB", [_hit("wd40efpx", 40)], structured_mpn="WD40EFPX"
    )
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.evidence["multiple_mpns"] == ["wd40efpx", "wd40efzx"]


def test_structured_mpn_alone_is_still_one_assertion() -> None:
    verdict = _drive_decide(
        "WD Red Plus 4TB NAS", [_hit("wd40efpx", 40)], structured_mpn="WD40EFPX"
    )
    assert verdict.outcome is ladder.Outcome.ACCEPT


def test_wd_retail_part_number_is_a_candidate() -> None:
    # WD/HGST orderable part numbers ("0F62796") are seeded retail_pn aliases;
    # with one letter they failed the code-token two-letter test and never
    # reached the alias table.
    candidates = mpn.extract_candidates(
        canonicalize_title("WD Ultrastar WUH722424ALE6L1 / 0F62796 24TB SATA")
    )
    by_key = {c.normalized: c for c in candidates}
    assert by_key["0f62796"].kind is TokenKind.UNKNOWN_CODE
    assert by_key["0f62796"].review_only
    assert by_key["wuh722424ale6l1"].kind is TokenKind.MANUFACTURER_MPN
    assert not by_key["wuh722424ale6l1"].review_only


def test_retail_pn_alone_never_grounds_an_accept() -> None:
    # Review-only: "Compatible WD Ultrastar DC HC560 0F38785" (MS-1e ebay-0388,
    # a look-alike labeled none) would otherwise accept at rung 1.
    verdict = _drive_decide(
        "Compatible WD Ultrastar DC HC560 0F38785 20TB", [_hit("0f38785", 5, review_only=True)]
    )
    assert verdict.outcome is ladder.Outcome.NONE


def test_retail_pn_of_another_model_blocks_the_inherited_prior() -> None:
    # R3-A bypass 2: 0F62796 is WUH722424ALE6L4's retail PN; rung 0 inherited
    # the ALE6L1 prior without looking at it.
    verdict = _drive_decide(
        "WD Ultrastar WUH722424ALE6L1 / 0F62796 24TB SATA",
        [_hit("wuh722424ale6l1", 1), _hit("0f62796", 4, review_only=True)],
        prior=_prior(1),
    )
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 0
    assert verdict.evidence["conflicting_alias_models"] == ["0f62796", "wuh722424ale6l1"]


def test_same_model_aliases_still_accept() -> None:
    hits = [_hit("wuh722424ale6l1", 1), _hit("0f62795", 1, review_only=True)]
    title = "WD Ultrastar WUH722424ALE6L1 / 0F62795 24TB SATA"
    assert _drive_decide(title, hits, prior=_prior(1)).outcome is ladder.Outcome.ACCEPT
    rung1 = _drive_decide(title, hits)
    assert rung1.outcome is ladder.Outcome.ACCEPT
    assert rung1.rung == 1


def test_unknown_retail_pn_beside_the_mpn_still_accepts() -> None:
    verdict = _drive_decide(
        "WD Ultrastar DC HC550 18TB WUH721818AL5204 0F38353", [_hit("wuh721818al5204", 7)]
    )
    assert verdict.outcome is ladder.Outcome.ACCEPT


def test_oem_fanout_of_one_identifier_is_not_a_conflict() -> None:
    # One OEM part number aliasing two models of one family is the N:N fan-out
    # rung 1 already attaches at family grain, not two identities.
    hits = [
        ladder.AliasHit(
            target=ladder.TargetRef(grain=Grain.MODEL, family_id=9, model_id=model_id),
            source_kind="catalog_authoritative",
            alias_type="oem_pn",
            brand="seagate",
            hard_attrs=ladder.HardAttrs(),
            candidate_kind=TokenKind.OEM_PN,
            candidate_normalized="005049070",
        )
        for model_id in (1, 2)
    ]
    verdict = ladder.decide(
        vocab.extract(canonicalize_title("Seagate EMC 005049070")), [], None, hits, None
    )
    assert verdict.outcome is ladder.Outcome.ACCEPT
    assert verdict.grain is Grain.FAMILY


def test_two_cpu_opns_block_the_inherited_prior() -> None:
    # N3 residual: two OPNs naming 9354 and 9654 carry no EPYC name token, so
    # the multi_model marker is empty; the alias rule must still refuse rung 0.
    title = canonicalize_title("AMD 100-000000798 / 100-000000789 SP5 processors")
    candidates = cpu.extract_candidates(title)
    hits = [_hit("100000000798", 9354, brand="amd"), _hit("100000000789", 9654, brand="amd")]
    verdict = ladder.decide(cpu.extract(title), candidates, _prior(9354), hits, None, veto=cpu.veto)
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 0
    assert verdict.evidence["conflicting_alias_models"] == ["100000000789", "100000000798"]


def test_cpu_name_and_bare_number_of_one_model_accept() -> None:
    title = canonicalize_title("Intel Xeon Gold 6338 32-Core LGA4189")
    candidates = cpu.extract_candidates(title)
    hits = [_hit("xeongold6338", 6338, brand="intel"), _hit("6338", 6338, brand="intel")]
    verdict = ladder.decide(cpu.extract(title), candidates, None, hits, None, veto=cpu.veto)
    assert verdict.outcome is ladder.Outcome.ACCEPT


# R3-D: complete CPU model tokens only.


def _cpu_keys(title: str) -> set[str]:
    return {c.normalized for c in cpu.extract_candidates(canonicalize_title(title))}


@pytest.mark.parametrize(
    ("title", "prefix_keys"),
    [
        ("Intel Xeon Gold 63380 32-Core LGA4189", {"xeongold6338", "6338"}),
        ("Intel Xeon Platinum 84800 LGA4677", {"xeonplatinum8480", "8480"}),
        ("AMD EPYC 77630 SP3", {"epyc7763", "7763"}),
        ("AMD Ryzen 79500 AM5", {"9500"}),
    ],
)
def test_near_model_string_emits_no_prefix_candidate(title: str, prefix_keys: set[str]) -> None:
    assert _cpu_keys(title).isdisjoint(prefix_keys)


@pytest.mark.parametrize(
    ("title", "keys"),
    [
        ("Intel Xeon Gold 6338 32-Core LGA4189", {"xeongold6338", "6338"}),
        ("Intel Xeon Gold 6448Y 32-Core LGA4677", {"xeongold6448y", "6448y"}),
        ("Intel Xeon Platinum 8480+ 56-Core LGA4677", {"xeonplatinum8480", "8480"}),
        ("Intel Xeon Gold 6338N LGA4189", {"xeongold6338n", "6338n"}),
        ("AMD Ryzen 9 7950X AM5", {"7950x"}),
        ("AMD Ryzen 7950X AM5", {"7950x"}),
    ],
)
def test_complete_model_tokens_still_emit(title: str, keys: set[str]) -> None:
    assert keys <= _cpu_keys(title)


# R3-E: spaced board spellings.


@pytest.mark.parametrize(
    "title",
    [
        "Supermicro H12SSL-i Mother Board + AMD EPYC 7763 64-Core SP3",
        "ASRock Rack ROMED8-2T Main Board with AMD EPYC 7763 SP3",
        "Supermicro H12SSL-i Mother-Board AMD EPYC 7763 SP3",
    ],
)
def test_spaced_board_spelling_is_a_bundle(title: str) -> None:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    assert payload.bundle is not None
    assert "bundle" in cpu.veto(cpu.extract(canonicalize_title(title)), ladder.HardAttrs())
