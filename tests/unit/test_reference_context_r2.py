"""Reference masking, round 2 (review findings F1, F4 against matcher 2026.09.2).

F1: reference spans must respect raw clause punctuation (",", ";", "|"), which
canonicalization used to erase before masking ran, and must treat a
parenthesized comparison object as part of the span. F4: the offer terms that
become variant identity (condition, packaging, recert and warranty channel)
must not be read from a reference span, while the seller's structured
condition label stays asserted evidence."""

import json
import re
import unicodedata
from pathlib import Path

import pytest

from hw_radar.matching import mpn, vocab
from hw_radar.matching.normalize import (
    canonicalize_listing_text,
    canonicalize_title,
    mask_reference_spans,
    reference_phrase_pattern,
)

_REPO = Path(__file__).resolve().parents[2]
_CORPORA = (
    _REPO / "tests" / "fixtures" / "matching_corpus" / "synthetic.jsonl",
    _REPO / "docs" / "evidence" / "2026-09-25-ms1e-draft-corpus.jsonl",
    _REPO / "docs" / "evidence" / "2026-09-26-cpu-epyc-draft-corpus.jsonl",
)


def _normalized(title: str) -> set[str]:
    return {c.normalized for c in mpn.extract_candidates(canonicalize_title(title))}


def _brand(text: str) -> str | None:
    brand = vocab.extract(canonicalize_title(text)).brand
    return None if brand is None else brand.value


def test_parenthesized_comparison_object_is_masked() -> None:
    # F1(a): ST12000NE0008 is an exact alias in the committed IronWolf Pro seed,
    # so a leak here is a rung-1 false merge, not merely a stray candidate.
    title = "WL 12TB comparable to (Seagate ST12000NE0008)"
    assert _normalized(title) == set()
    assert _brand(title) is None
    extracted = vocab.extract(canonicalize_title(title))
    assert extracted.capacity_bytes is not None  # veto fields still read the title
    assert extracted.capacity_bytes.value == 12_000_000_000_000


def test_comma_ends_the_span_and_the_listed_mpn_survives() -> None:
    # F1(b): before the fix the comma was erased first and BOTH MPNs vanished.
    title = "Compatible with ST16000NM002G, Seagate ST18000NM000J"
    assert _normalized(title) == {"st18000nm000j"}
    assert _brand(title) == "seagate"


@pytest.mark.parametrize("sep", [",", ";", "|", " - "])
def test_every_clause_separator_ends_the_span(sep: str) -> None:
    title = f"WD 12TB replaces Seagate ST12000NE0008{sep} WD120EFBX"
    assert _normalized(title) == {"wd120efbx"}
    assert _brand(title) == "western_digital"


def test_thousands_comma_is_not_a_clause_boundary() -> None:
    # "1,000" between digits is a number; were it a boundary, the MPN after it
    # would escape the span.
    assert _normalized("WL drive equivalent to 1,000GB ST1000NM0001") == set()


def test_phrase_inside_parentheses_ends_at_the_closing_paren() -> None:
    title = "Seagate ST12000NE0008 12TB (compatible with Dell R740) Retail"
    canonical = canonicalize_title(title)
    masked = mask_reference_spans(canonical)
    assert "dell" not in masked and "r740" not in masked
    assert masked.startswith("seagate st12000ne0008 12tb (")
    assert masked.endswith(") retail")
    assert _normalized(title) == {"st12000ne0008"}
    packaging = vocab.extract(canonical).packaging
    assert packaging is not None and packaging.value == "retail"


def test_dell_inside_parentheses_does_not_open_the_oem_gate() -> None:
    assert "005049070" not in _normalized("Drive 005049070 (compatible with Dell R740)")


def test_phrase_at_title_start() -> None:
    title = "Compatible with Dell PowerEdge R740 - Seagate ST12000NE0008 12TB"
    assert _normalized(title) == {"st12000ne0008"}
    assert _brand(title) == "seagate"


def test_two_phrases_each_mask_their_own_clause() -> None:
    title = (
        "WD WD120EFBX 12TB - comparable to Seagate ST12000NE0008 - "
        "replacement for Toshiba MG07ACA12TE - NAS"
    )
    masked = mask_reference_spans(canonicalize_title(title))
    assert masked.endswith("- nas")
    assert _normalized(title) == {"wd120efbx"}
    assert _brand(title) == "western_digital"


def test_unclosed_parenthesized_object_stops_at_the_condition_boundary() -> None:
    canonical = canonicalize_listing_text("WL 12TB comparable to (Seagate ST12000NE0008", "New")
    assert mpn.extract_candidates(canonical) == []
    condition = vocab.offer_terms(canonical).condition
    assert condition is not None and condition.value == "new"


def test_parenthetical_qualifier_does_not_end_the_span() -> None:
    # F1 residual: "(Seagate)" only qualifies the comparison object; ending the
    # span at its ")" exposed the exact-alias MPN to rung 1.
    canonical = canonicalize_listing_text("WL 12TB comparable to (Seagate) ST12000NE0008", "New")
    assert mpn.extract_candidates(canonical) == []
    assert vocab.extract(canonical).brand is None
    condition = vocab.offer_terms(canonical).condition
    assert condition is not None and condition.value == "new"


def test_span_past_a_parenthetical_still_stops_at_a_clause_boundary() -> None:
    title = "WD 12TB comparable to (Seagate) ST12000NE0008, WD120EFBX"
    assert _normalized(title) == {"wd120efbx"}
    assert _brand(title) == "western_digital"


def test_parenthetical_condition_qualifier_creates_no_recertified_variant() -> None:
    # F4 residual: "recertified" outranks the seller's "New" label, so leaving
    # "recertified drives" unmasked after "(factory)" filed a recertified variant.
    canonical = canonicalize_listing_text(
        "Seagate ST12000NE0008 12TB comparable to (factory) recertified drives", "New"
    )
    assert "recertified" not in mask_reference_spans(canonical)
    for extracted in (vocab.extract(canonical), vocab.offer_terms(canonical)):
        assert extracted.condition is not None and extracted.condition.value == "new"
        assert extracted.recert_channel is None
    assert {c.normalized for c in mpn.extract_candidates(canonical)} == {"st12000ne0008"}


def test_unclosed_paren_later_in_the_object_keeps_the_condition_label() -> None:
    canonical = canonicalize_listing_text("WL 12TB comparable to Seagate (ST12000NE0008", "New")
    assert mpn.extract_candidates(canonical) == []
    condition = vocab.offer_terms(canonical).condition
    assert condition is not None and condition.value == "new"


def test_category_phrase_preserves_clause_punctuation() -> None:
    # N2: "oem version of" is CPU-local, so a trigger reading only the shared
    # phrases erased the ";" and the CPU span swallowed the asserted QS marker.
    canonical = canonicalize_title("AMD EPYC 7763 OEM version of 7B13; QS 64-Core SP3")
    assert canonical == "amd epyc 7763 oem version of 7b13 - qs 64-core sp3"
    masked = mask_reference_spans(canonical, reference_phrase_pattern("oem version of"))
    assert "7b13" not in masked
    assert "qs" in masked.split()
    assert canonicalize_title(canonical) == canonical  # idempotent


def test_unregistered_category_phrase_is_rejected() -> None:
    # An extra phrase outside the canonicalization registry would silently lose
    # its clause boundaries (the N2 failure), so building its pattern fails.
    with pytest.raises(ValueError, match="_CATEGORY_REFERENCE_PHRASES"):
        reference_phrase_pattern("successor to")


def test_reference_offer_terms_create_no_variant_identity() -> None:
    # F4: 'factory recertified' ranks above 'new' in the condition table, so an
    # unmasked read turned a New listing into a factory-recertified variant.
    canonical = canonicalize_listing_text(
        "Seagate ST12000NE0008 12TB comparable to factory recertified drives", "New"
    )
    for extracted in (vocab.extract(canonical), vocab.offer_terms(canonical)):
        assert extracted.condition is not None and extracted.condition.value == "new"
        assert extracted.recert_channel is None
    assert _normalized("Seagate ST12000NE0008 12TB comparable to factory recertified drives") == {
        "st12000ne0008"
    }


@pytest.mark.parametrize(
    ("title", "field"),
    [
        ("Seagate ST12000NE0008 equivalent to retail boxed kits", "packaging"),
        ("Seagate ST12000NE0008 replacement for drives with manufacturer warranty", "warranty"),
    ],
)
def test_packaging_and_warranty_channel_in_a_span_are_not_read(title: str, field: str) -> None:
    extracted = vocab.extract(canonicalize_title(title))
    value = extracted.packaging if field == "packaging" else extracted.warranty_channel
    assert value is None


def test_condition_label_survives_a_span_running_to_the_title_end() -> None:
    # Without the boundary canonicalize_listing_text places before the label,
    # the span would run on through "new" and the seller's condition would be lost.
    canonical = canonicalize_listing_text(
        "Seagate ST12000NE0008 12TB compatible with Synology DS1821+", "New"
    )
    condition = vocab.offer_terms(canonical).condition
    assert condition is not None and condition.value == "new"
    assert canonical.endswith(" - new")


def test_reference_title_canonicalization_is_idempotent() -> None:
    for title in (
        "Compatible with ST16000NM002G, Seagate ST18000NM000J; retail | New",
        "WL 12TB comparable to (Seagate ST12000NE0008)",
        "compatible,with ST16000NM002G, Seagate",
    ):
        once = canonicalize_title(title)
        assert canonicalize_title(once) == once


# The canonicalization shipped before this change, restated from its source so
# the no-phrase invariant is checked against the real historical output rather
# than against the new code path. Identical regexes to normalize.py's.
_HIST_DASHES: dict[int, str] = dict.fromkeys(
    (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212), "-"
)
_HIST_NOISE = re.compile(r"[^a-z0-9 .\-/()\"+%#]")
_HIST_BOILERPLATE = re.compile(
    r"\b(?:l@@k|wow|free\s+(?:fast\s+)?shipping|fast\s+ship(?:ping)?|"
    r"ships?\s+(?:fast|free|today|same\s+day)|best\s+offer|top\s+seller|"
    r"us\s+seller|hot\s+deal)\b"
)
_HIST_PHRASE = re.compile(
    r"\b(?:comparable to|compatible with|replacement for|equivalent to|equiv to|"
    r"alternative to|substitute for|replaces|oem version of)\b"
)


def _historical_canonical(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_HIST_DASHES).casefold()
    cleaned = _HIST_NOISE.sub(" ", _HIST_BOILERPLATE.sub(" ", folded))
    return re.sub(r"\s+", " ", cleaned).strip()


def _corpus_rows() -> list[tuple[Path, str, str]]:
    rows: list[tuple[Path, str, str]] = []
    for path in _CORPORA:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows.append((path, row["title"], row["listing"].get("condition_label", "")))
    return rows


def _no_mask(text: str) -> str:
    return text


def _extraction(canonical: str) -> tuple[object, ...]:
    return (
        vocab.extract(canonical),
        vocab.offer_terms(canonical),
        mpn.extract_candidates(canonical),
    )


def test_phrase_free_corpus_titles_are_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over the synthetic oracle corpus and the MS-1e and CPU EPYC draft
    corpora: a title with no reference phrase (shared or category-local) gets the historical canonical text (with and without a
    condition label), and brand, offer terms and MPN candidates equal what the
    extractors return with masking switched off entirely — the pre-change
    behavior for these titles."""
    # The draft corpus carries no condition labels; join a few onto every title
    # so the " | " separator is proven invisible on phrase-free text.
    extra_labels = ("", "New", "Used - Good", "Manufacturer Refurbished")
    cases: list[tuple[str, str]] = []
    contributing: set[Path] = set()
    phrase_titles: set[str] = set()
    for path, title, label in _corpus_rows():
        if _HIST_PHRASE.search(_historical_canonical(title)) is not None:
            phrase_titles.add(title)
            continue
        contributing.add(path)
        cases.extend((title, extra) for extra in dict.fromkeys((label, *extra_labels)))

    after: list[tuple[object, ...]] = []
    for title, label in cases:
        assert canonicalize_title(title) == _historical_canonical(title)
        joined = canonicalize_listing_text(title, label)
        assert joined == _historical_canonical(f"{title} {label}".strip())
        assert mask_reference_spans(joined) == joined
        after.append(_extraction(joined))

    monkeypatch.setattr(vocab, "mask_reference_spans", _no_mask)
    monkeypatch.setattr(mpn, "mask_reference_spans", _no_mask)
    before = [_extraction(_historical_canonical(f"{t} {lbl}".strip())) for t, lbl in cases]
    assert after == before

    # Every corpus contributes, and phrase rows exist (ebay-0021; the CPU
    # corpus's "oem version of" titles), so the partition is not vacuous in
    # either direction.
    assert contributing == set(_CORPORA)
    assert phrase_titles
