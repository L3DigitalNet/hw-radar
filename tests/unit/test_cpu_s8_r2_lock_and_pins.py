"""Codex s8 round-2 findings 9 (residual), A and C on the pure CPU rules.

9: a negation separated from 'unlocked' by a lock qualifier ('NOT
VENDOR-UNLOCKED', 'not PSB unlocked') is still a negation, never an unlock.
A: a lock-local reference span ('works with', 'for use with', 'for use in')
ends at the clause boundary, and a mask can only turn a lock reading into
unknown: wording it hides that contradicts the reading still vetoes it.
C: a pin count ('4094-pin', 'SP3 4094') is socket context, not a model number,
so it neither contradicts the OPN's target nor makes a second model.
"""

from __future__ import annotations

import pytest

from hw_radar.matching import ladder
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import cpu

_EPYC_7763 = ladder.HardAttrs(category=cpu.CpuHard(socket="sp3", cores=64, model="7763"))
_EPYC_9354 = ladder.HardAttrs(category=cpu.CpuHard(socket="sp5", cores=32, model="9354"))


def _attrs(title: str) -> cpu.CpuAttributes:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    return payload


def _lock(title: str) -> str | None:
    lock = _attrs(title).vendor_lock
    return None if lock is None else lock.value


# ── 9: qualified negations ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7763 NOT VENDOR-UNLOCKED",
        "AMD EPYC 7763 not vendor unlocked",
        "AMD EPYC 7763 not PSB unlocked",
        "AMD EPYC 7763 isn't vendor-unlocked",
        "AMD EPYC 7763 NOT PSB-UNLOCKED",
        "AMD EPYC 7763 non-vendor-unlocked",
        "AMD EPYC 7763 not Dell unlocked",
        "AMD EPYC 7763 not an unlocked CPU",
        # Qualified negation beside an affirmative unlock elsewhere: still no
        # unlocked reading, the title contradicts itself.
        "AMD EPYC 7763 Unlocked - NOT VENDOR-UNLOCKED",
    ],
)
def test_qualified_negated_unlock_is_unknown(title: str) -> None:
    assert _lock(title) is None


def test_qualified_negated_unlock_beside_lock_wording_is_locked() -> None:
    assert _lock("AMD EPYC 7763 not vendor unlocked, Dell locked") == cpu.VENDOR_LOCKED


@pytest.mark.parametrize(
    "title",
    ["AMD EPYC 7763 Vendor Unlocked", "AMD EPYC 7763 PSB-unlocked", "AMD EPYC 7763 Unlocked"],
)
def test_affirmative_qualified_unlock_still_reads_unlocked(title: str) -> None:
    assert _lock(title) == cpu.VENDOR_UNLOCKED


# ── A: lock-local reference spans end at the clause boundary ─────────────────

_LOCK_PHRASES = ("works with", "work with", "for use with", "for use in")
_BOUNDARIES = (",", ";", " - ", "|")


@pytest.mark.parametrize("phrase", _LOCK_PHRASES)
@pytest.mark.parametrize("boundary", _BOUNDARIES)
def test_later_standalone_not_unlocked_survives_a_lock_reference(
    phrase: str, boundary: str
) -> None:
    """The Codex r2 counterexample, for every lock-local phrase and boundary:
    the span must not swallow the disqualifying clause after it."""
    title = f"AMD EPYC 7763 Unlocked, {phrase} Dell R7525{boundary} NOT UNLOCKED"
    assert _lock(title) is None


@pytest.mark.parametrize("phrase", _LOCK_PHRASES)
@pytest.mark.parametrize("boundary", _BOUNDARIES)
def test_later_standalone_locked_stays_visible(phrase: str, boundary: str) -> None:
    assert _lock(f"AMD EPYC 7763 {phrase} Dell R7525{boundary} LOCKED") == cpu.VENDOR_LOCKED
    assert _lock(f"AMD EPYC 7763 Unlocked {phrase} Dell R7525{boundary} LOCKED") is None


@pytest.mark.parametrize("phrase", _LOCK_PHRASES)
def test_lock_wording_inside_the_reference_span_is_still_no_evidence(phrase: str) -> None:
    assert _lock(f"AMD EPYC 7763 {phrase} unlocked Dell servers") is None
    assert _lock(f"AMD EPYC 7763 {phrase} Dell locked servers") is None


@pytest.mark.parametrize(
    "title",
    [
        # No punctuation at all: the span cannot know where the clause ends,
        # so the hidden negation must still veto the unlocked reading.
        "AMD EPYC 7763 Unlocked works with Dell R7525 NOT UNLOCKED",
        "AMD EPYC 7763 Unlocked for use with Dell R7525 locked",
        "AMD EPYC 7763 Unlocked compatible with Dell locked servers",
        "AMD EPYC 7763 Unlocked replacement for a not unlocked part",
    ],
)
def test_masked_contradiction_turns_unlocked_into_unknown(title: str) -> None:
    assert _lock(title) is None


def test_masked_unlock_turns_locked_into_unknown() -> None:
    assert _lock("AMD EPYC 7763 Dell Locked, replacement for an unlocked processor") is None


@pytest.mark.parametrize("phrase", [*_LOCK_PHRASES, "oem version of", "compatible with", "fit for"])
def test_every_masked_phrase_keeps_clause_punctuation(phrase: str) -> None:
    assert canonicalize_title(f"EPYC 7763 {phrase} Dell R7525; locked") == (
        f"epyc 7763 {phrase} dell r7525 - locked"
    )


# ── C: pin counts are socket context, not a model ────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 100-000000312 SP3 4094-pin CPU",
        "AMD EPYC 100-000000312 SP3 4094 pin CPU",
        "AMD EPYC 100-000000312 SP3 4094pin CPU",
        "AMD EPYC 100-000000312 4094-pins Socket SP3",
        "AMD EPYC 100-000000312 SP3 4094 CPU",
        "AMD EPYC 100-000000312 Socket SP3 (4094) CPU",
        "AMD EPYC 7763 SP3 4094-pin 64-Core CPU",
        "AMD EPYC 7763 LGA 4094 CPU",
    ],
)
def test_pin_count_is_neither_a_model_nor_a_contradiction(title: str) -> None:
    attrs = _attrs(title)
    assert attrs.multi_model is None
    assert attrs.model is None or attrs.model.value == "7763"
    assert "model" not in cpu.veto(cpu.extract(canonicalize_title(title)), _EPYC_7763)


@pytest.mark.parametrize(
    "title",
    ["AMD EPYC 9354 SP5 6096-pin CPU", "AMD EPYC 9354 SP5 6096 CPU", "AMD EPYC 9354 LGA 6096"],
)
def test_sp5_pin_count_is_not_a_model(title: str) -> None:
    assert _attrs(title).model is not None
    assert _attrs(title).multi_model is None
    assert "model" not in cpu.veto(cpu.extract(canonicalize_title(title)), _EPYC_9354)


@pytest.mark.parametrize(
    "title",
    [
        "Intel Xeon Gold 6338 LGA 4189 CPU",
        "Intel Xeon Gold 6338 4189-pin CPU",
        "Intel Xeon Platinum 8480+ LGA 4677",
        "Intel Xeon Platinum 8480+ 4677-pin",
    ],
)
def test_xeon_pin_count_is_not_a_coordinated_model(title: str) -> None:
    assert _attrs(title).multi_model is None


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC GENOA SP5 ZEN4 9354P 32-Core 3.25GHz Processor CPU 100-000000798",
        "AMD EPYC 100-000000798 9354P Processor SP5",
        "AMD EPYC 9354P SP5 6096-pin CPU",
    ],
)
def test_named_suffix_model_still_vetoes_the_opn_target(title: str) -> None:
    assert "model" in cpu.veto(cpu.extract(canonicalize_title(title)), _EPYC_9354)


def test_a_model_is_still_read_beside_its_socket() -> None:
    # The socket word blanks only a following pin count, not a model after it.
    assert _attrs("AMD EPYC SP3 7763 64-Core").model is not None
