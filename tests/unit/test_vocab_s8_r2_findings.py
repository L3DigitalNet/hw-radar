"""Codex s8 round-2 findings 3 (partial), B and D on the pure vocab/ladder
layer: compact item-tied lot counts, memory markers anywhere in a RAM-brand
title, and negated condition words."""

from __future__ import annotations

import pytest

from hw_radar.matching import ladder, vocab
from hw_radar.matching.normalize import canonicalize_listing_text, canonicalize_title


def _lot(title: str) -> int | None:
    lot = ladder.lot_quantity(vocab.extract(canonicalize_title(title)).quantity)
    return None if lot is None else int(str(lot["quantity"]))


# ── Finding 3 (partial): compact "NxTB" is the same lot as "Nx TB" ──────────


@pytest.mark.parametrize(
    ("title", "count"),
    [
        ("New Seagate ST12000NE0008 2x12TB HDD", 2),
        ("2X16TB Seagate Exos X16 ST16000NM001G", 2),
        ("4x 16TB Seagate Exos X16 ST16000NM001G", 4),
        ("Seagate Exos 2x 18TB ST18000NM000J", 2),
    ],
)
def test_compact_count_before_a_tb_capacity_is_a_lot(title: str, count: int) -> None:
    assert _lot(title) == count


@pytest.mark.parametrize(
    "title",
    [
        # RAM kit shapes stay below the bar, compact or spaced.
        "Kingston 2x16GB DDR4 2666 ECC",
        "Samsung 32GB 2x16GB RDIMM",
        # Thread counts are not unit counts.
        "AMD EPYC 7B13 64-Core 128-Thread 32x 3.25GHz SP3 CPU",
        "AMD EPYC 7B13 32x3.25GHz SP3 CPU",
    ],
)
def test_compact_non_tb_counts_stay_below_the_lot_bar(title: str) -> None:
    quantity = vocab.extract_quantity(canonicalize_title(title))
    assert quantity is None or quantity.confidence < vocab.LOT_MIN_CONFIDENCE


# ── Finding B: a memory marker anywhere in the title keeps a kit a kit ───────


@pytest.mark.parametrize(
    "title",
    [
        "DDR4 RAM kit 2x Kingston 16GB 2666 ECC",
        "RDIMM 2x Samsung 32GB 4800",
        "Server Memory: 2 x Micron 16GB",
        # The pre-existing after-the-brand form stays covered.
        "2x Kingston 16GB DDR4 2666 ECC RDIMM Server Memory",
    ],
)
def test_memory_marker_before_or_after_the_brand_is_not_a_trusted_count(title: str) -> None:
    canonical = canonicalize_title(title)
    for quantity in (vocab.extract_quantity(canonical), vocab.offer_quantity(canonical)):
        assert quantity is None or quantity.confidence < vocab.LOT_MIN_CONFIDENCE


def test_memory_brand_drive_multipack_without_a_memory_marker_still_ties() -> None:
    assert _lot("2x Samsung 870 EVO 1TB SATA SSD") == 2


# ── Finding D: a negated condition word asserts nothing ─────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "WD Red Plus WD20EFPX 2TB Recertified NOT NEW",
        "WD Red Plus WD20EFPX 2TB Recertified - not new",
        "WD Red Plus WD20EFPX 2TB Recertified no longer new",
    ],
)
def test_agreeing_negated_clarification_keeps_store_provenance(title: str) -> None:
    extracted = vocab.extract(canonicalize_title(title))
    assert extracted.condition_conflict is None
    folded = vocab.with_source_offer_terms(extracted, "wd-recertified")
    assert vocab.source_offer_conflict(folded, "wd-recertified") is None
    assert folded.condition is not None and folded.condition.value == "recertified"
    assert folded.recert_channel is not None and folded.recert_channel.value == "factory"


def test_negated_declared_condition_never_earns_the_store_channel() -> None:
    # Only the collector learning polarity would let this through: the
    # first-match pick would still read the negated "recertified", and the
    # fold would then add factory to a listing that says it is used.
    extracted = vocab.extract(canonicalize_title("WD Red Plus WD20EFPX 2TB NOT RECERTIFIED Used"))
    assert extracted.condition is not None and extracted.condition.value == "used"
    folded = vocab.with_source_offer_terms(extracted, "wd-recertified")
    assert folded.recert_channel is None
    assert folded.condition is not None and folded.condition.value == "used"


def test_never_used_is_not_used() -> None:
    extracted = vocab.extract(canonicalize_title("Seagate ST12000NE0008 12TB never used"))
    assert extracted.condition is None


@pytest.mark.parametrize(
    ("title", "label"),
    [
        # Two positive assertions still conflict (round-1 finding 5).
        ("WD Red Plus WD20EFPX 2TB Recertified", "Used"),
        ("WD Red Plus WD20EFPX 2TB Recertified NOT NEW", "Used"),
    ],
)
def test_positive_conflicts_still_conflict(title: str, label: str) -> None:
    extracted = vocab.extract(canonicalize_listing_text(title, label))
    assert vocab.source_offer_conflict(extracted, "wd-recertified") == ("recertified", "used")


def test_not_working_is_still_for_parts() -> None:
    # "not working" is itself the for-parts phrase, not a negated condition.
    condition = vocab.extract(
        canonicalize_title("Seagate ST12000NE0008 12TB not working")
    ).condition
    assert condition is not None and condition.value == "for_parts"
