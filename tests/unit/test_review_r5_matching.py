"""Matcher regressions from the round-5 cross-agent review (s7).

R4-C residual: the leading-"for" exclusion and the for_parts condition
vocabulary must accept the same repair/spares inflections ("For repairs:" lost
the whole identity because only singular "repair" was excluded). R4-D residual:
numeric Xeon alternatives coordinated by "or", "and", a comma or "&" (the last
two canonicalize to a bare space) set the CPU multi_model marker. The R5-A
variant-attribute inheritance cases need the ORM and live in
tests/db/test_resolver_prior_identifiers.py."""

from __future__ import annotations

import pytest

from hw_radar.matching import mpn, vocab
from hw_radar.matching.normalize import (
    DRIVE_REFERENCE_PHRASE,
    canonicalize_title,
    mask_reference_spans,
)
from hw_radar.matching.rules import cpu

# Every inflection a seller writes after a leading "for". Each must both keep
# the drive's identity (not a reference span) and assert for_parts.
_REPAIR_PREAMBLES = [
    "For repairs: Seagate ST12000NE0008 12TB HDD",
    "For repair: Seagate ST12000NE0008 12TB HDD",
    "For spares: Seagate ST12000NE0008 12TB HDD",
    "For spare: Seagate ST12000NE0008 12TB HDD",
    "For spares or repairs: Seagate ST12000NE0008 12TB HDD",
    "For spares or repair: Seagate ST12000NE0008 12TB HDD",
    "For parts: Seagate ST12000NE0008 12TB HDD",
    "FOR REPAIRS Seagate ST12000NE0008 12TB",
]


@pytest.mark.parametrize("title", _REPAIR_PREAMBLES)
def test_repair_preamble_keeps_identity_and_asserts_for_parts(title: str) -> None:
    canonical = canonicalize_title(title)
    assert "st12000ne0008" in mask_reference_spans(canonical, DRIVE_REFERENCE_PHRASE)
    assert "st12000ne0008" in {c.normalized for c in mpn.extract_candidates(canonical)}
    extracted = vocab.extract(canonical)
    assert extracted.condition is not None
    assert extracted.condition.value == "for_parts"


@pytest.mark.parametrize(
    "title",
    [
        "FOR Seagate Exos X14 12TB SATA ST12000NM0008 NEW",
        # "repairman"/"spareparts" are not the condition words: the shared
        # definition is bounded, so these still open a reference span.
        "For repairman Seagate ST12000NM0008",
    ],
)
def test_leading_for_before_other_words_still_masks(title: str) -> None:
    canonical = canonicalize_title(title)
    assert "st12000nm0008" not in mask_reference_spans(canonical, DRIVE_REFERENCE_PHRASE)


def _multi_model(title: str) -> str | None:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    return payload.multi_model.value if payload.multi_model is not None else None


@pytest.mark.parametrize(
    "title",
    [
        "Intel Xeon Gold 6338 or 8358 32-Core LGA4189",
        "Intel Xeon Gold 6338 / 8358 32-Core LGA4189",
        "Intel Xeon Gold 6338, 8358 32-Core LGA4189",
        "Intel Xeon Gold 6338 & 8358 32-Core LGA4189",
        "Intel Xeon Gold 6338 and 8358 32-Core LGA4189",
        # A reference phrase keeps the comma as the canonical " - " boundary.
        "Intel Xeon Gold 6338, 8358 (compatible with Dell R750)",
    ],
)
def test_coordinated_numeric_xeon_alternatives_set_the_marker(title: str) -> None:
    assert _multi_model(title) == "6338 8358"


def test_three_coordinated_alternatives_are_all_read() -> None:
    assert _multi_model("Intel Xeon Gold 6338, 6348 or 8358 LGA4189") == "6338 6348 8358"


@pytest.mark.parametrize(
    "title",
    [
        # Numbers followed by a unit are specs, not models.
        "Intel Xeon Gold 6248 2666 MHz 20-Core LGA3647",
        "Intel Xeon Gold 6248 2666MHz 20-Core LGA3647",
        "Intel Xeon Gold 6338 1000W LGA4189",
        "Intel Xeon Silver 4210 2400 MT/s LGA3647",
        "Intel Xeon Gold 6338 32-Core 2.0GHz LGA4189",
    ],
)
def test_unit_bearing_numbers_after_a_xeon_model_set_no_marker(title: str) -> None:
    assert _multi_model(title) is None


def test_numeric_alternatives_are_never_candidates() -> None:
    candidates = {
        c.normalized
        for c in cpu.extract_candidates(
            canonicalize_title("Intel Xeon Gold 6338 or 8358 32-Core LGA4189")
        )
    }
    assert "8358" not in candidates
    assert "xeongold8358" not in candidates
