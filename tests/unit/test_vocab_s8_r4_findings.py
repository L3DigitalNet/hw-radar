"""Codex s8 round-4 findings 1, 4 and 6 at the pure layers: negator-owning
phrases as window barriers in both readers, the source fold refusing any
listing that carries negative condition evidence, and denied recertification
channels kept as negative evidence of their own."""

from __future__ import annotations

import pytest

from hw_radar.matching import vocab
from hw_radar.matching.normalize import canonicalize_listing_text, canonicalize_title
from hw_radar.matching.rules import cpu
from hw_radar.matching.types import Attribute


def _lock(title: str) -> str | None:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    return None if payload.vendor_lock is None else payload.vendor_lock.value


def _values(attr: Attribute[tuple[str, ...]] | None) -> tuple[str, ...]:
    return () if attr is None else attr.value


def _folded(title: str, label: str = "", source: str = "wd-recertified") -> tuple[object, ...]:
    e = vocab.with_source_offer_terms(
        vocab.extract(canonicalize_listing_text(title, label)), source
    )
    return (
        None if e.condition is None else e.condition.value,
        None if e.recert_channel is None else e.recert_channel.value,
        _values(e.denied_conditions),
        _values(e.denied_recert_channels),
    )


# ── Finding 1: a negator-owning phrase negates nothing after it ─────────────


def test_no_warranty_does_not_negate_a_later_lock() -> None:
    # "no" belongs to "No Warranty"; the later "Dell Locked" stands, so the
    # title asserts both unlocked and locked and is unknown.
    assert _lock("AMD EPYC 7763 Unlocked No Warranty Dell Locked") is None
    assert _lock("AMD EPYC 7763 No Warranty Dell Locked") == "locked"
    assert _lock("AMD EPYC 7763 No Warranty Unlocked") == "unlocked"


# Every phrase the shared registry must hold, in a spelling a title uses.
_OWNING = [
    "No Warranty",
    "No Reserve",
    "No Returns",
    "No Return",
    "Not Tested",
    "No Tray",
    "No Caddy",
    "No OS",
    "No Cables",
    "No Cable",
    "No-Reserve",
]


@pytest.mark.parametrize("phrase", _OWNING)
def test_owning_phrase_is_a_barrier_in_both_readers(phrase: str) -> None:
    assert _lock(f"AMD EPYC 7763 Unlocked {phrase} Dell Locked") is None
    assert _lock(f"AMD EPYC 7763 {phrase} Dell Locked") == "locked"
    e = vocab.extract(canonicalize_title(f"Seagate ST12000NM0008 12TB {phrase} Used"))
    assert e.condition is not None and e.condition.value == "used"
    assert e.denied_conditions is None


def test_lock_phrase_owning_its_negator_is_a_condition_barrier() -> None:
    # "no vendor lock" is the CPU reader's positive unlock phrase; its "no"
    # must not deny the condition after it either.
    e = vocab.extract(canonicalize_title("AMD EPYC 7763 No Vendor Lock Used"))
    assert e.condition is not None and e.condition.value == "used"
    assert e.denied_conditions is None


def test_a_negator_before_the_phrase_still_negates_it_only() -> None:
    # The barrier does not rescue a real negation right before an assertion.
    assert _lock("AMD EPYC 7763 No Warranty not unlocked") is None
    e = vocab.extract(canonicalize_title("Seagate 12TB No Tray never used"))
    assert e.condition is None
    assert _values(e.denied_conditions) == ("used",)


# ── Finding 4: a suppressed condition is not an omission ────────────────────


@pytest.mark.parametrize(
    ("title", "label"),
    [
        # "screws" is deliberately NOT a registered phrase: the structural
        # rule must hold for boilerplate the registry does not cover.
        ("WD Red Plus WD20EFPX 2TB No Screws Used", ""),
        ("WD Red Plus WD20EFPX 2TB No Screws", "Used"),
    ],
)
def test_denied_condition_blocks_the_source_fold(title: str, label: str) -> None:
    assert _folded(title, label) == (None, None, ("used",), ())


@pytest.mark.parametrize(
    ("title", "label"),
    [("WD Red Plus WD20EFPX 2TB No tray Used", ""), ("WD Red Plus WD20EFPX 2TB No tray", "Used")],
)
def test_registered_boilerplate_reads_the_used_condition(title: str, label: str) -> None:
    assert _folded(title, label) == ("used", None, (), ())


def test_omitted_condition_still_folds() -> None:
    assert _folded("WD Red Plus WD20EFPX 2TB") == ("recertified", "factory", (), ())
    assert _folded("WD Red Plus WD20EFPX 2TB Recertified") == ("recertified", "factory", (), ())


# ── Finding 6: a denied channel survives a generic recertified ──────────────


def test_denied_factory_channel_is_negative_evidence() -> None:
    title = "Seagate ST12000NE0008 12TB Recertified NOT Factory Recertified"
    e = vocab.extract(canonicalize_title(title))
    assert e.condition is not None and e.condition.value == "recertified"
    assert e.recert_channel is None
    assert e.denied_conditions is None
    assert _values(e.denied_recert_channels) == ("factory",)


def test_asserted_channel_is_never_also_denied() -> None:
    title = "Seagate 12TB factory recertified - not factory recertified"
    assert vocab.extract(canonicalize_title(title)).denied_recert_channels is None


def test_the_fold_never_adds_a_denied_channel() -> None:
    title = "WD Red Plus WD20EFPX 2TB Recertified NOT Factory Recertified"
    assert _folded(title) == ("recertified", None, (), ("factory",))
    # The generic seller channel is denied too, like the factory one.
    seller = vocab.extract(canonicalize_title("WD 2TB refurbished, not seller refurbished"))
    assert _values(seller.denied_recert_channels) == ("seller",)
