"""The condition reader (vocab) and the CPU vendor-lock reader (rules.cpu)
implement one shared negation window. They keep separate code, so this pins
that they spend the window identically: a contraction is one token and the
same qualifiers are free."""

from __future__ import annotations

import pytest

from hw_radar.matching import vocab
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
