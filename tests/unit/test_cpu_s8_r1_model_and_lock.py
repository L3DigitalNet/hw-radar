"""Codex s8 round-1 findings 8 and 9 on the pure CPU rules.

8: a title that explicitly names a CPU model other than the selected target
(a suffix variant such as 9354P or the unseeded 9354F over the seeded 9354's
OPN) is a contradiction, so the `model` veto fires whatever candidate reached
the target. With 9354P seeded, the live resolver usually reviews such a title
on the rung-1 target conflict first (tests/db/test_resolver_cpu_s8_r1_model.py);
the veto is the guard whenever the named model reaches no alias.
9: vendor-lock wording is negation-aware, ignores lock words that describe a
referenced product rather than the offered unit, and reads hyphen/spacing
variants.
"""

from __future__ import annotations

import pytest

from hw_radar.matching import ladder
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import cpu

_EPYC_9354 = ladder.HardAttrs(category=cpu.CpuHard(socket="sp5", cores=32, model="9354"))


def _veto(title: str, target: ladder.HardAttrs = _EPYC_9354) -> list[str]:
    return cpu.veto(cpu.extract(canonicalize_title(title)), target)


def _lock(title: str) -> str | None:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    return None if payload.vendor_lock is None else payload.vendor_lock.value


# ── 8: explicit model name vs the selected target ────────────────────────────


@pytest.mark.parametrize(
    "number",
    ["EPYC 9354", "EPYC 9654", "EPYC 7763", "EPYC 7742"],
)
def test_epyc_target_model_key_is_the_bare_number(number: str) -> None:
    assert cpu.model_key(number) == number.split()[1].lower()


@pytest.mark.parametrize(
    ("number", "key"),
    [
        ("Xeon Gold 6448Y", "6448y"),
        ("Xeon Platinum 8480+", "8480+"),
        ("Xeon Gold 6548Y+", "6548y+"),
        ("Xeon Gold 6338", "6338"),
        ("Core i9-13900K", "i913900k"),
        # Not one recognizable model: the target cannot veto on model.
        ("Mystery CPU", None),
        ("EPYC 7742/7702", None),
    ],
)
def test_other_line_target_model_keys(number: str, key: str | None) -> None:
    assert cpu.model_key(number) == key


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC GENOA SP5 ZEN4 9354P 32-Core 3.25GHz Processor CPU 100-000000798Open",
        "AMD EPYC GENOA SP5 ZEN4 9354P 32-Core 3.25GHz Processor CPU 100-000000798",
        "AMD EPYC 9354F 32-Core SP5 100-000000798",
        "AMD EPYC 9354X SP5 CPU",
        "AMD EPYC 9654 96-Core SP5 CPU",
    ],
)
def test_title_naming_a_different_model_vetoes_the_target(title: str) -> None:
    assert "model" in _veto(title)


@pytest.mark.parametrize(
    "title",
    [
        # cpu-0082 itself: the name agrees with the OPN's target.
        "AMD EPYC GENOA SP5 ZEN4 9354 32-Core 3.25GHz Processor CPU 100-000000798Open",
        "AMD EPYC 9354 32-Core SP5 CPU 100-000000798",
        # No model named at all: only the OPN speaks, nothing contradicts it.
        "AMD 100-000000798 Processor",
    ],
)
def test_agreeing_or_absent_model_name_never_vetoes(title: str) -> None:
    assert _veto(title) == []


def test_unknown_target_model_cannot_veto() -> None:
    # A family-grain or spec-less target carries no model: no contradiction.
    family = ladder.HardAttrs(category=cpu.CpuHard(socket="sp5", cores=32))
    assert _veto("AMD EPYC 9354P SP5 100-000000798", family) == []


def test_xeon_suffix_variant_vetoes_the_plain_target() -> None:
    target = ladder.HardAttrs(category=cpu.CpuHard(model="6338"))
    assert "model" in _veto("Intel Xeon Gold 6338N 32-Core LGA4189", target)
    assert _veto("Intel Xeon Gold 6338 32-Core LGA4189", target) == []


def test_model_mentioned_only_in_a_reference_span_is_not_the_listing_model() -> None:
    # "compatible with EPYC 9654" cites another part; the listing names 9354.
    assert _veto("AMD EPYC 9354 SP5 CPU, compatible with EPYC 9654 boards") == []


# ── 9: vendor-lock scope, negation and spelling ──────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7763 NOT UNLOCKED",
        "AMD EPYC 7763 isn't unlocked",
        "AMD EPYC 7763 is not unlocked",
        "AMD EPYC 7763 non-unlocked",
        "AMD EPYC 7763 not un-locked",
        # A plain unlock elsewhere cannot outvote the negation.
        "Unlocked? AMD EPYC 7763 NOT UNLOCKED",
    ],
)
def test_negated_unlock_is_never_unlocked(title: str) -> None:
    assert _lock(title) is None


def test_negated_unlock_beside_explicit_lock_wording_is_locked() -> None:
    assert _lock("AMD EPYC 7763 Dell Locked - NOT UNLOCKED") == cpu.VENDOR_LOCKED


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7763 replacement for an unlocked processor",
        "AMD EPYC 7763 compatible with Dell locked servers",
        "AMD EPYC 7763 for use with unlocked boards",
        "AMD EPYC 7763 comparable to vendor locked EPYC 7713",
    ],
)
def test_lock_wording_about_a_referenced_product_is_ignored(title: str) -> None:
    assert _lock(title) is None


def test_lock_wording_outside_the_reference_span_still_counts() -> None:
    title = "AMD EPYC 7763 Unlocked, replacement for Dell R7525 CPU"
    assert _lock(title) == cpu.VENDOR_UNLOCKED


@pytest.mark.parametrize(
    "title",
    [
        # The OEM-version phrase is kept readable on purpose: a retail EPYC has
        # no PSB lock, so lock wording there describes the offered OEM unit.
        "AMD EPYC Milan 7J13 CPU 64 Core 2.45GHz unlocked , OEM Version of AMD EPYC 7763",
        "2pcs AMD EPYC Milan 7T83 CPU 64 core 2.45 Ghz OEM Version of EPYC 7763 unlocked",
    ],
)
def test_oem_version_unlock_still_reads_unlocked(title: str) -> None:
    assert _lock(title) == cpu.VENDOR_UNLOCKED


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7763 no vendor-lock",
        "AMD EPYC 7763 no-vendor-lock",
        "AMD EPYC 7763 non vendor locked",
        "AMD EPYC 7763 not vendor-locked",
        "AMD EPYC 7763 no PSB-lock",
        "AMD EPYC 7763 isn't locked",
    ],
)
def test_hyphen_and_spacing_unlock_variants(title: str) -> None:
    assert _lock(title) == cpu.VENDOR_UNLOCKED


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7763 vendor-locked",
        "AMD EPYC 7763 PSB-locked",
        "AMD EPYC 7763 vendor-lock",
        "AMD EPYC 7763 vendor lock",
    ],
)
def test_hyphen_and_spacing_lock_variants(title: str) -> None:
    assert _lock(title) == cpu.VENDOR_LOCKED


def test_intel_scope_is_unchanged() -> None:
    assert _lock("Intel Core i9-13900K not unlocked") is None
    assert _lock("Intel Core i9-13900K no vendor-lock") is None
