"""The condition reader (vocab) and the CPU vendor-lock reader (rules.cpu)
implement one shared negation window. They keep separate code, so this pins
that they spend the window identically: a contraction is one token and the
same qualifiers are free, and both stop at the one registry of
negator-owning phrases."""

from __future__ import annotations

import re

import pytest

from hw_radar.matching import normalize, vocab
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import cpu


def _lock(title: str) -> str | None:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    return None if payload.vendor_lock is None else payload.vendor_lock.value


def _denied(title: str) -> tuple[str, ...]:
    denied = vocab.extract(canonicalize_title(title)).denied_conditions
    return () if denied is None else denied.value


@pytest.mark.parametrize(
    ("gap", "negated"),
    [
        ("", True),
        ("x ", True),
        ("x y ", True),
        ("x y z ", False),
        ("hpe dell a x y ", True),
    ],
)
def test_both_readers_spend_the_window_alike(gap: str, negated: bool) -> None:
    assert (_lock(f"AMD EPYC 7763 isn't {gap}unlocked") is None) is negated
    assert (_denied(f"Seagate ST12000NM0008 12TB isn't {gap}used") == ("used",)) is negated


def test_the_qualifier_sets_are_one_set() -> None:
    assert cpu._WINDOW_QUALIFIERS == vocab._WINDOW_QUALIFIERS  # pyright: ignore[reportPrivateUsage]


def test_both_readers_use_the_one_negator_owning_registry() -> None:
    assert vocab.NEGATOR_OWNING_PHRASE is normalize.NEGATOR_OWNING_PHRASE
    assert cpu.NEGATOR_OWNING_PHRASE is normalize.NEGATOR_OWNING_PHRASE
    assert cpu._UNLOCK_PHRASES is normalize.NEGATED_LOCK_PHRASE  # pyright: ignore[reportPrivateUsage]


# One title spelling per registry entry. test_every_registry_entry_has_a_sample
# fails when an entry is added without one, so no phrase can be a barrier in
# one reader and untested in the other.
_OWNING_SAMPLES = [
    "no warranty",
    "no reserve",
    "no returns",
    "not tested",
    "not working",
    "no tray",
    "no caddy",
    "no os",
    "no cables",
]


def test_every_registry_entry_has_a_sample() -> None:
    for wording in normalize.NEGATOR_OWNING_WORDING:
        assert any(re.fullmatch(wording, s) for s in _OWNING_SAMPLES), wording


@pytest.mark.parametrize("phrase", _OWNING_SAMPLES)
def test_an_owning_phrase_negates_nothing_in_either_reader(phrase: str) -> None:
    # Barrier in the lock reader: the lock after the phrase stands, so it
    # contradicts the unlock before it.
    assert _lock(f"AMD EPYC 7763 Unlocked {phrase} Dell Locked") is None
    assert _lock(f"AMD EPYC 7763 {phrase} Unlocked") == "unlocked"
    # Barrier in the condition reader: the condition after it is asserted.
    assert _denied(f"Seagate ST12000NM0008 12TB {phrase} used") == ()


@pytest.mark.parametrize("phrase", ["no vendor lock", "not dell locked", "non-locked"])
def test_a_lock_owning_phrase_negates_no_condition(phrase: str) -> None:
    assert _denied(f"AMD EPYC 7763 {phrase} used") == ()
