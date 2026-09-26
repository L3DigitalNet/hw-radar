"""Codex s8 round-1 findings 3, 4, 5 and 7 on the pure vocab/ladder layer:
contextual "Nx" lot forms, auction-lot identifiers and conflicting quantity
statements, conflicting condition assertions against source provenance, and
percentage/cosmetic "new" wording."""

from __future__ import annotations

import pytest

from hw_radar.matching import ladder, vocab
from hw_radar.matching.normalize import canonicalize_listing_text, canonicalize_title
from hw_radar.matching.rules import cpu


def _quantity(title: str) -> int | None:
    lot = ladder.lot_quantity(vocab.extract(canonicalize_title(title)).quantity)
    return None if lot is None else int(str(lot["quantity"]))


# ── Finding 3: an explicit count tied to the item is a lot ───────────────────


@pytest.mark.parametrize(
    ("title", "count"),
    [
        ("2x Seagate ST12000NE0008 12TB", 2),
        ("2 x Seagate ST12000NE0008 12TB", 2),
        ("2x New Seagate ST12000NE0008 12TB HDD", 2),
        ("3x Brand New WD Red Plus WD40EFPX", 3),
        ("2X 16TB Seagate Exos X16 ST16000NM001G", 2),
        ("4x HGST HUH721010ALE604 10TB", 4),
        ("3x hard drives WD Red Plus WD40EFPX", 3),
        ("10x AMD EPYC 7763 64-Core SP3 CPUs", 10),
    ],
)
def test_count_tied_to_the_item_is_a_lot(title: str, count: int) -> None:
    assert _quantity(title) == count


def test_cpu_extract_reads_the_contextual_lot_form() -> None:
    extracted = cpu.extract(canonicalize_title("10x AMD EPYC 7763 64-Core SP3 CPUs"))
    lot = ladder.lot_quantity(extracted.quantity)
    assert lot is not None and lot["quantity"] == 10


@pytest.mark.parametrize(
    "title",
    [
        # Thread count and speed (MS-1e cpu-0010): not a unit count.
        "AMD EPYC 7B13 64-Core 128-Thread 32x 3.25GHz SP3 CPU",
        "AMD EPYC 7763 64-Core Processor",
        # Family names never read as a count.
        "Seagate Exos X16 ST16000NM001G 16TB",
        "Seagate Exos X18 ST18000NM000J 18TB",
        # An explicit single unit.
        "1x Seagate ST12000NE0008 12TB",
    ],
)
def test_non_lot_counts_never_veto(title: str) -> None:
    assert _quantity(title) is None


# ── Finding 4: auction identifiers and conflicting counts ────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "Auction Lot 42: Seagate ST12000NE0008 12TB HDD (1pc)",
        "Lot #42 Seagate ST12000NE0008 12TB HDD",
        "Lot No. 42 Seagate ST12000NE0008 12TB HDD",
        "Lot no 42 Seagate ST12000NE0008 12TB HDD",
    ],
)
def test_auction_lot_identifier_is_not_a_quantity(title: str) -> None:
    quantity = vocab.extract(canonicalize_title(title)).quantity
    assert quantity is None or quantity.value == 1
    assert _quantity(title) is None


def test_auction_lot_with_an_explicit_single_unit_is_one_unit() -> None:
    quantity = vocab.extract(
        canonicalize_title("Auction Lot 42: Seagate ST12000NE0008 12TB HDD (1pc)")
    ).quantity
    assert quantity is not None
    assert quantity.value == 1
    assert quantity.confidence >= vocab.LOT_MIN_CONFIDENCE


def test_conflicting_counts_review_but_are_not_a_trusted_divisor() -> None:
    canonical = canonicalize_title("Lot of 2 Seagate ST12000NE0008 12TB HDD (1pc)")
    # Identity: the title cannot prove a single unit, so the lot review fires.
    lot = ladder.lot_quantity(vocab.extract(canonical).quantity)
    assert lot is not None and lot["quantity"] == 2
    # Pricing: below every divisor bar, so eligibility never divides by it.
    priced = vocab.offer_quantity(canonical)
    assert priced is not None
    assert priced.confidence < 0.85


def test_agreeing_counts_are_not_a_conflict() -> None:
    canonical = canonicalize_title("Lot of 2 - 2pcs Seagate ST12000NE0008 12TB")
    for quantity in (vocab.extract(canonical).quantity, vocab.offer_quantity(canonical)):
        assert quantity is not None
        assert quantity.value == 2
        assert quantity.confidence >= vocab.LOT_MIN_CONFIDENCE


def test_offer_quantity_matches_identity_quantity_without_a_conflict() -> None:
    for title in ("Lot 10 Supermicro Seagate ST2000NX0253", "32x 3.25GHz AMD EPYC", "Seagate"):
        canonical = canonicalize_title(title)
        assert vocab.offer_quantity(canonical) == vocab.extract(canonical).quantity


# ── Finding 5: a store listing that also asserts another condition ───────────


@pytest.mark.parametrize(
    ("title", "label"),
    [
        ("WD Red Plus WD20EFPX 2TB Recertified", "Used"),
        ("WD Red Plus WD20EFPX 2TB Recertified New Pull", ""),
        ("WD Red Plus WD20EFPX 2TB Recertified - pulled", ""),
    ],
)
def test_store_provenance_does_not_apply_under_a_condition_conflict(title: str, label: str) -> None:
    extracted = vocab.extract(canonicalize_listing_text(title, label))
    folded = vocab.with_source_offer_terms(extracted, "wd-recertified")
    assert vocab.source_offer_conflict(folded, "wd-recertified") is not None
    assert folded.recert_channel is None
    # Neither the declared nor either asserted condition is proven.
    assert folded.condition is None


def test_marketplace_first_match_precedence_is_unchanged() -> None:
    extracted = vocab.extract(
        canonicalize_listing_text("WD Red Plus WD20EFPX 2TB Recertified", "Used")
    )
    assert extracted.condition is not None
    assert extracted.condition.value == "recertified"
    assert vocab.source_offer_conflict(extracted, "demomarket") is None


def test_store_listing_asserting_only_recertified_keeps_provenance() -> None:
    extracted = vocab.extract(canonicalize_title("WD Red Plus WD20EFPX 2TB - Factory Recertified"))
    folded = vocab.with_source_offer_terms(extracted, "wd-recertified")
    assert vocab.source_offer_conflict(folded, "wd-recertified") is None
    assert folded.recert_channel is not None
    assert folded.recert_channel.value == "factory"


# ── Finding 7: percentage and cosmetic "new" ────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "Seagate ST12000NE0008 12TB NEW 90%",
        "Seagate ST12000NE0008 12TB new 95 %",
        "Seagate ST12000NE0008 12TB 90 % NEW",
        "Seagate ST12000NE0008 12TB 90%NEW",
        "Seagate ST12000NE0008 12TB like-new",
        "Seagate ST12000NE0008 12TB Like-New",
        "Seagate ST12000NE0008 12TB LIKE NEW",
    ],
)
def test_percentage_and_cosmetic_new_assert_nothing(title: str) -> None:
    assert vocab.extract(canonicalize_title(title)).condition is None


@pytest.mark.parametrize(
    "title",
    [
        "NEW Seagate ST12000NE0008 12TB",
        "Brand New Seagate ST12000NE0008 12TB",
        "Seagate ST12000NE0008 12TB New 2 pack",
    ],
)
def test_plain_new_still_asserts_new(title: str) -> None:
    condition = vocab.extract(canonicalize_title(title)).condition
    assert condition is not None
    assert condition.value == "new"


@pytest.mark.parametrize(
    "title",
    [
        "2x Kingston 16GB DDR4 2666 ECC RDIMM Server Memory",
        "2x Samsung 32GB DDR5 4800 RDIMM",
        "2 x Crucial 8GB SODIMM laptop RAM",
    ],
)
def test_memory_kit_count_is_not_a_trusted_lot(title: str) -> None:
    # One kit of N modules: the count must stay below the lot/pricing bar,
    # or eligibility would divide the kit price by the module count.
    quantity = vocab.extract_quantity(canonicalize_title(title))
    assert quantity is None or quantity.confidence < vocab.LOT_MIN_CONFIDENCE


def test_memory_brand_drive_multipack_still_ties_the_count() -> None:
    quantity = vocab.extract_quantity(canonicalize_title("2x Samsung 870 EVO 1TB SATA SSD"))
    assert quantity is not None
    assert (quantity.value, quantity.confidence >= vocab.LOT_MIN_CONFIDENCE) == (2, True)
