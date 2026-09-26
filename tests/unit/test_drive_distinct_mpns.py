"""A drive title carrying MPNs of two different models reviews (s7 D3).

MS-1e ebay-0445 "Western Digital Red Plus WD40EFPX/WD40EFZX 4TB" auto-accepted
WD40EFPX: the ladder attached whichever token hit first, an arbitrary pick
between two products. The drive analog of the CPU multi-model guard. It must
not fire on one MPN beside an OEM/customer part number, a repeated MPN, or an
MPN's own dash-suffixed form."""

import pytest

from hw_radar.matching import categories, grammars, ladder, mpn, vocab
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.types import Grain


def _run(
    title: str, hits: list[ladder.AliasHit] | None = None, *, guard: bool = True
) -> tuple[ladder.Verdict, list[str]]:
    canonical = canonicalize_title(title)
    candidates = mpn.extract_candidates(canonical)
    decoded = next(
        (d for c in candidates if (d := grammars.decode(c.normalized)) is not None), None
    )
    verdict = ladder.decide(
        vocab.extract(canonical),
        candidates,
        None,
        hits or [],
        decoded,
        distinct_mpn_guard=guard,
    )
    return verdict, ladder.distinct_mpns(candidates, hits or [])


def _hit(normalized: str, model_id: int) -> ladder.AliasHit:
    return ladder.AliasHit(
        target=ladder.TargetRef(grain=Grain.MODEL, family_id=3, model_id=model_id),
        source_kind="catalog_authoritative",
        alias_type="mpn",
        brand="western_digital",
        hard_attrs=ladder.HardAttrs(),
        candidate_vendor="western_digital",
        candidate_normalized=normalized,
    )


_EBAY_0445 = 'Western Digital Red Plus WD40EFPX/WD40EFZX 4TB 3.5" 7.2K SATA Hard Drive'


def test_two_model_mpns_review_even_when_one_hits_an_exact_alias() -> None:
    verdict, mpns = _run(_EBAY_0445, [_hit("wd40efpx", 40)])
    assert mpns == ["wd40efpx", "wd40efzx"]
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 1
    assert verdict.evidence["multiple_mpns"] == ["wd40efpx", "wd40efzx"]


def test_two_model_mpns_review_at_rung_two() -> None:
    verdict, _ = _run(_EBAY_0445)
    assert verdict.outcome is ladder.Outcome.REVIEW
    assert verdict.rung == 2
    assert verdict.evidence["multiple_mpns"] == ["wd40efpx", "wd40efzx"]


def test_two_spellings_of_one_catalog_model_are_one_model() -> None:
    hits = [_hit("wd40efpx", 40), _hit("wd40efzx", 40)]
    verdict, mpns = _run(_EBAY_0445, hits)
    assert mpns == []
    assert verdict.outcome is ladder.Outcome.ACCEPT


@pytest.mark.parametrize(
    "title",
    [
        # WD's own orderable part number beside the MPN.
        'WD Ultrastar DC HC550 18TB SAS 12GB/s 3.5" HDD WUH721818AL5204 0F38353',
        # Dell DP/N and a Seagate part number beside a Seagate MPN.
        "Seagate IronWolf Pro 12TB ST12000NE0008 Dell DP/N 0YVJ9Y 1FN201-003",
        # The MPN repeated.
        "WD Red Plus WD40EFPX 4TB NAS - WD40EFPX",
        # The MPN plus its own dash-suffixed form.
        "WD Red Plus 8TB WD80EFPX-68C4ZN0 NAS HDD",
    ],
)
def test_single_model_titles_do_not_trip_the_guard(title: str) -> None:
    verdict, mpns = _run(title)
    assert mpns == []
    assert verdict.outcome is ladder.Outcome.ACCEPT
    assert "multiple_mpns" not in verdict.evidence


def test_guard_is_drive_only() -> None:
    # CPU extraction emits an OPN and its bare model number for ONE product;
    # the guard would review every such title, so only drive enables it.
    verdict, _ = _run(_EBAY_0445, guard=False)
    assert verdict.outcome is ladder.Outcome.ACCEPT
    enabled = {
        slug
        for slug in categories.registered_categories()
        if (rules := categories.rules_for(slug)) is not None and rules.distinct_mpn_guard
    }
    assert enabled == {categories.DRIVE}
