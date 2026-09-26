"""Reference-context masking (ADR-0019 false-merge asymmetry): an MPN or brand
word cited only as the OBJECT of a comparison phrase ("comparable to X") names a
different product than the one listed, so it must never become identity
evidence. Pins MS-1e corpus row ebay-0021, an owner-confirmed matcher false
positive: a white-label drive auto-accepted at rung 2 as Seagate/Exos."""

import pytest

from hw_radar.matching import grammars, ladder, mpn, vocab
from hw_radar.matching.normalize import canonicalize_title, mask_reference_spans
from hw_radar.matching.types import Grain, MpnCandidate

# The ebay-0021 title verbatim (docs/evidence/2026-09-25-ms1e-draft-corpus.jsonl).
EBAY_0021 = 'WL OEM 16TB 7.2K RPM SAS 12Gb/s 3.5" HDD - Comparable to ST16000NM002G'


def _candidates(title: str, *, structured_mpn: str | None = None) -> dict[str, MpnCandidate]:
    found = mpn.extract_candidates(canonicalize_title(title), structured_mpn=structured_mpn)
    return {c.normalized: c for c in found}


def _drive_verdict(title: str) -> ladder.Verdict:
    """The drive pipeline minus the DB: no prior and no alias hits, so the
    verdict rests on the extraction layers and the rung-2 grammar decode."""
    canonical = canonicalize_title(title)
    candidates = mpn.extract_candidates(canonical)
    decoded = next(
        (d for c in candidates if (d := grammars.decode(c.normalized)) is not None), None
    )
    return ladder.decide(vocab.extract(canonical), candidates, None, [], decoded)


def test_comparison_only_mpn_yields_no_candidate_and_no_decode() -> None:
    assert _candidates(EBAY_0021) == {}
    verdict = _drive_verdict(EBAY_0021)
    assert verdict.outcome is ladder.Outcome.NONE
    assert verdict.grain is Grain.NONE
    assert "vendor_hint" not in verdict.evidence  # nothing reached the grammar decode


def test_direct_identity_mpn_still_decodes_to_family() -> None:
    title = 'Seagate 16TB 7.2K RPM SAS 12Gb/s 3.5" HDD ST16000NM002G'
    found = _candidates(title)
    assert found["st16000nm002g"].vendor_hint == "seagate"
    verdict = _drive_verdict(title)
    assert verdict.outcome is ladder.Outcome.ACCEPT
    assert verdict.rung == 2
    assert verdict.target is not None
    assert verdict.target.family_key == ("seagate", "exos")


@pytest.mark.parametrize(
    ("title", "kept", "dropped"),
    [
        # The comparison object is a server, not a drive: the identity MPN
        # before the phrase must survive untouched.
        (
            "Seagate Exos X18 ST18000NM000J 18TB compatible with Dell PowerEdge R740",
            "st18000nm000j",
            None,
        ),
        (
            "Seagate ST16000NM001G 16TB SATA replacement for ST16000NM002G",
            "st16000nm001g",
            "st16000nm002g",
        ),
        # A clause boundary (" - ") ends the reference span, so an identity MPN
        # AFTER the cited one is kept.
        (
            "Equivalent to ST16000NM002G - Seagate Exos ST18000NM000J 18TB",
            "st18000nm000j",
            "st16000nm002g",
        ),
        # Parentheses close the span too.
        (
            "(compatible with ST16000NM002G) Seagate ST18000NM000J 18TB",
            "st18000nm000j",
            "st16000nm002g",
        ),
    ],
)
def test_identity_mpn_survives_beside_a_reference_phrase(
    title: str, kept: str, dropped: str | None
) -> None:
    found = _candidates(title)
    assert kept in found
    if dropped is not None:
        assert dropped not in found


@pytest.mark.parametrize(
    "phrase",
    [
        "Comparable to",
        "Compatible with",
        "Replacement for",
        "Equivalent to",
        "Equiv to",
        "Alternative to",
        "Substitute for",
        "Replaces",
    ],
)
def test_every_reference_phrase_masks_its_object(phrase: str) -> None:
    title = f"WL 16TB SAS HDD - {phrase} Seagate Exos X16 ST16000NM002G"
    assert "st16000nm002g" not in _candidates(title)
    assert vocab.extract(canonicalize_title(title)).brand is None


def test_brand_word_inside_the_span_supplies_no_brand() -> None:
    extracted = vocab.extract(
        canonicalize_title("WL 16TB HDD - Comparable to Seagate Exos X16 ST16000NM002G")
    )
    assert extracted.brand is None
    # Non-identity attributes outside the span are still read normally.
    assert extracted.capacity_bytes is not None
    assert extracted.capacity_bytes.value == 16_000_000_000_000


def test_brand_outside_the_span_is_kept() -> None:
    extracted = vocab.extract(
        canonicalize_title("Seagate Exos 16TB HDD compatible with Dell PowerEdge R740")
    )
    assert extracted.brand is not None and extracted.brand.value == "seagate"


def test_reference_span_masks_oem_gate_words() -> None:
    # 'dell' only inside the span must not open the Dell/EMC OEM gate for a
    # bare 9-digit number elsewhere in the title.
    found = _candidates("Drive 005049070 compatible with Dell PowerEdge R740")
    assert "005049070" not in found


def test_structured_mpn_is_unaffected_by_reference_context() -> None:
    found = _candidates(EBAY_0021, structured_mpn="ST16000NM002G")
    assert found["st16000nm002g"].from_structured_field is True


@pytest.mark.parametrize(
    "title",
    [
        # 'replacement' without 'for' is ordinary listing prose (see test_mpn).
        "EMC 005049070 replacement drive",
        # 'compatible' alone is not a reference phrase.
        "Seagate ST16000NM001G 16TB SATA compatible",
        # A word merely CONTAINING a phrase must not trigger (\b guard).
        "Seagate ST16000NM001G noncomparable to nothing",
    ],
)
def test_non_reference_titles_are_unchanged(title: str) -> None:
    canonical = canonicalize_title(title)
    assert mask_reference_spans(canonical) == canonical


def test_mask_preserves_length_and_text_outside_spans() -> None:
    canonical = canonicalize_title("Seagate ST16000NM001G replacement for ST16000NM002G - 16TB")
    masked = mask_reference_spans(canonical)
    assert len(masked) == len(canonical)
    assert masked.startswith("seagate st16000nm001g ")
    assert masked.endswith(" - 16tb")
    assert "st16000nm002g" not in masked
    assert mask_reference_spans(masked) == masked  # idempotent
