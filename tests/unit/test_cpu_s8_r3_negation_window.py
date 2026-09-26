"""Codex s8 round-3 item 2: vendor-lock negation is one window rule, not a
list of phrase patches.

A lock/unlock assertion is negated when a negator sits within three tokens
before it, lock qualifiers and articles not counting; a negated assertion is
denied evidence (a veto), never a reading. Phrases whose own wording carries
the negator ('no vendor lock', 'not locked') stay positive unlock evidence.
"""

from __future__ import annotations

import pytest

from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import cpu

_BASE = "AMD EPYC 7763 64-Core SP3 CPU"


def _lock(title: str) -> str | None:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    return None if payload.vendor_lock is None else payload.vendor_lock.value


U, L = cpu.VENDOR_UNLOCKED, cpu.VENDOR_LOCKED

# (suffix appended to _BASE, expected vendor_lock). Unknown (None) is the
# conservative answer for every denied unlock: require_vendor_unlocked treats
# it as not satisfied.
_TABLE = [
    # The Codex r3 reproductions and the brief's shapes: never unlocked.
    ("no longer unlocked", None),
    ("not a Dell PSB-unlocked CPU", None),
    ("NOT VENDOR-UNLOCKED", None),
    ("not an unlocked", None),
    ("never unlocked", None),
    ("without unlock", None),
    ("without unlocked", None),
    # Contracted and stacked-qualifier negators.
    ("isn't a vendor unlocked part", None),
    ("aren't unlocked", None),
    ("not the Lenovo vendor-unlocked version", None),
    ("not un-locked", None),
    # A negated positive-negator phrase denies the unlock it states.
    ("not a no-vendor-lock CPU", None),
    # A denied unlock beside an affirmative one: the title contradicts itself.
    ("Unlocked - no longer vendor-unlocked", None),
    # A denied unlock with explicit lock wording standing reads locked.
    ("no longer unlocked, Dell locked", L),
    # A denied lock proves no unlock by itself, and vetoes a locked reading.
    ("never locked", None),
    ("no longer vendor locked", None),
    ("without vendor lock", None),
    ("Dell Locked, not a Dell locked CPU", None),
    # Outside the window (three counted tokens back): the negator is about
    # something else, so the affirmative unlock stands.
    ("not used 2 years Unlocked", U),
    # Positive phrases carrying their own negator stay unlocked, and the
    # negator they claim does not negate a following affirmative unlock.
    ("NO VENDOR LOCK", U),
    ("No Vendor Locked", U),
    ("not vendor locked", U),
    ("non-locked", U),
    ("NO VENDOR LOCK Unlocked", U),
    ("(No lock)", U),
    # Unchanged affirmatives.
    ("Unlocked", U),
    ("PSB-unlocked", U),
    ("Dell Locked", L),
    ("vendor-locked", L),
]


@pytest.mark.parametrize(("suffix", "expected"), _TABLE)
def test_negation_window_table(suffix: str, expected: str | None) -> None:
    assert _lock(f"{_BASE} {suffix}") == expected


_UNLOCK_DENIALS = [
    "no longer unlocked",
    "not a Dell PSB-unlocked CPU",
    "NOT VENDOR-UNLOCKED",
    "not an unlocked",
    "never unlocked",
    "without unlock",
    "no unlock",
    "without unlocked",
    "isn't a vendor unlocked part",
    "aren't unlocked",
    "not the Lenovo vendor-unlocked version",
    "not un-locked",
    "not a no-vendor-lock CPU",
]


@pytest.mark.parametrize("suffix", _UNLOCK_DENIALS)
@pytest.mark.parametrize("boundary", [" ", " | ", ", ", " - "])
def test_a_denied_unlock_vetoes_an_affirmative_one(suffix: str, boundary: str) -> None:
    # An affirmative 'Unlocked' elsewhere must not rescue a denial, whichever
    # side of it the denial sits on.
    assert _lock(f"{_BASE} Unlocked{boundary}{suffix}") is None
    assert _lock(f"{_BASE} {suffix}{boundary}Unlocked") is None


def test_a_bare_unlock_is_never_a_reading() -> None:
    assert _lock(f"{_BASE} unlock code included") is None
    assert _lock(f"{_BASE} Unlocked, unlock code included") == U


@pytest.mark.parametrize(
    "suffix",
    [
        "never locked",
        "no longer vendor locked",
        "without vendor lock",
        # Codex s8 r5: an unregistered boilerplate negator borrowed by the
        # window must not turn explicit lock wording into support for unlocked.
        "No Heatsink Dell Locked",
        "No Fan PSB Locked",
    ],
)
def test_a_window_denied_lock_blocks_an_unlocked_reading(suffix: str) -> None:
    """Superseded s8 r3 rule: a window-denied lock used to be consistent with
    'Unlocked'. The window cannot tell a real denial from a borrowed negator,
    and unlocked satisfies a hard requirement, so it is unknown now."""
    assert _lock(f"{_BASE} Unlocked | {suffix}") is None
    assert _lock(f"{_BASE} {suffix} Unlocked") is None


@pytest.mark.parametrize("phrase", ["no vendor lock", "not vendor locked", "non-locked"])
def test_negated_lock_phrases_stay_unlock_evidence(phrase: str) -> None:
    assert _lock(f"{_BASE} Unlocked {phrase}") == U
