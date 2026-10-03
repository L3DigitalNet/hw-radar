"""CPU extraction for the s8 owner EPYC audit: the cpu-0082 recall shapes (AMD
qualifier words before the number, an OPN fused with a condition word), the
explicit vendor-lock evidence, and the `cpu.vendor_lock` clause truth table."""

from __future__ import annotations

import pytest

from hw_radar.catalog.models import EligibilityVerdict
from hw_radar.eligibility.evaluate import CategoryPolicy, EvidenceTier, vendor_lock_clause
from hw_radar.matching import ladder
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import cpu
from hw_radar.matching.types import Attribute

_CPU_0082 = "AMD EPYC GENOA SP5 ZEN4 9354 32-Core 3.25GHz Processor CPU 100-000000798Open"


def _keys(title: str) -> set[str]:
    return {c.normalized for c in cpu.extract_candidates(canonicalize_title(title))}


def _attrs(title: str) -> cpu.CpuAttributes:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    return payload


def _lock(title: str) -> str | None:
    lock = _attrs(title).vendor_lock
    return None if lock is None else lock.value


# ── A: cpu-0082 recall shapes ────────────────────────────────────────────────


def test_cpu_0082_emits_the_name_and_the_fused_opn() -> None:
    keys = _keys(_CPU_0082)
    assert {"epyc9354", "9354", "100000000798"} <= keys


def test_plain_opn_still_emits() -> None:
    assert "100000000798" in _keys("AMD EPYC 9354 100-000000798")


@pytest.mark.parametrize(
    "title",
    [
        # A longer digit run is another code, not the 9354 OPN plus noise.
        "AMD EPYC 100-0000007981",
        # AMD's boxed-part shape: letters continuing the digits are part of the code.
        "AMD EPYC 100-000000798WOF",
        # A fused word followed by a suffix is not the bare OPN.
        "AMD EPYC 100-000000798open-04",
        # A word outside the closed condition set.
        "AMD EPYC 100-000000798X2",
    ],
)
def test_suffixed_or_extended_opn_is_not_collapsed(title: str) -> None:
    assert "100000000798" not in _keys(title)


def test_sample_opn_suffix_is_still_sample_vetoed() -> None:
    title = "100-000000798-04 AMD EPYC GENOA SP5 ZEN4 9354 CPU"
    assert "100000000798" not in _keys(title)
    extracted = cpu.extract(canonicalize_title(title))
    assert "sample" in cpu.veto(extracted, ladder.HardAttrs())


@pytest.mark.parametrize(
    "title",
    [
        # cpu-0016, verbatim: the seller tagged the retail OPN with its vendor.
        "Processor AMD EPYC 9354P 32-Core 3.25GHz 256MB 280W 100-000000805-DELL",
        "AMD EPYC 7763 100-000000312-Lenovo",
        "AMD EPYC 7763 100-000000312-HPE",
        "AMD EPYC 7763 100-000000312-cisco CPU",
    ],
)
def test_oem_brand_opn_suffix_is_not_a_sample(title: str) -> None:
    extracted = cpu.extract(canonicalize_title(title))
    assert "sample" not in cpu.veto(extracted, ladder.HardAttrs())
    # Branding is not a lock either.
    assert _lock(title) is None


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7763 100-000000312-ES",
        "AMD EPYC 7763 100-000000312-QS",
        # A brand word only exempts itself, not a longer token that starts with it.
        "AMD EPYC 7763 100-000000312-DELLX",
        "AMD EPYC 7763 100-000000312-HP04",
    ],
)
def test_other_letter_opn_suffixes_still_read_as_samples(title: str) -> None:
    extracted = cpu.extract(canonicalize_title(title))
    assert "sample" in cpu.veto(extracted, ladder.HardAttrs())


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC GENOA SP5 ZEN4 9354P 32-Core CPU 100-000000805",
        "AMD EPYC SP5 9354P 32-Core CPU",
        "AMD EPYC Zen 4 9354P CPU 100-000000805Open",
    ],
)
def test_p_variant_stays_distinct_with_qualifier_words(title: str) -> None:
    keys = _keys(title)
    assert "epyc9354p" in keys
    assert not {"epyc9354", "9354", "100000000798"} & keys


@pytest.mark.parametrize(
    "title",
    ["AMD EPYC SP5 GENOA 9354", "AMD EPYC Zen 4 9354", "AMD EPYC Milan SP3 7763"],
)
def test_qualifier_words_in_any_order(title: str) -> None:
    number = title.rsplit(" ", 1)[1].lower()
    assert f"epyc{number}" in _keys(title)


def test_a_free_word_still_breaks_the_name_phrase() -> None:
    # Only AMD's own qualifier vocabulary is skipped; 'server' is not in it.
    assert "epyc9354" not in _keys("AMD EPYC server 9354")


# ── B: vendor-lock evidence ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 9354 32C 280W - Dell Locked",
        "AMD EPYC 7763 *Lenovo Locked*",
        "AMD EPYC 7763 100-000000312 CPU (*locked*) (*Pulled from Cisco UCS C245 M6*)",
        "AMD EPYC 7763 vendor locked",
        "AMD EPYC 7763 PSB locked",
        "AMD EPYC 7763 locked to HPE",
        "AMD EPYC 7763 256MB 280W CPU 100-000000312 **LENOVO ONLY**",
        "AMD EPYC 7763 100-000000312 locked",
    ],
)
def test_explicit_lock_wording_is_locked(title: str) -> None:
    assert _lock(title) == cpu.VENDOR_LOCKED


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 9354 Processor *UNLOCKED*",
        "AMD EPYC 7763 NO VENDOR LOCK",
        "AMD EPYC Milan 7763 NO VENDOR LOCKED",
        "AMD EPYC 7763 not vendor locked",
        # Negation must not leave the bare word behind to read as locked.
        "AMD EPYC 7763 not locked",
        "AMD EPYC 7763 not locked to Dell",
        "AMD EPYC 7763 non-locked",
        "(No lock) AMD EPYC 7763",
    ],
)
def test_explicit_unlock_wording_is_unlocked(title: str) -> None:
    assert _lock(title) == cpu.VENDOR_UNLOCKED


@pytest.mark.parametrize(
    "title",
    [
        "AMD EPYC 7763 64-Core Dell",
        "AMD EPYC 7763 Lenovo ThinkSystem",
        "AMD EPYC 7763 Pulled from Cisco UCS C245 M6",
        "AMD EPYC 7763 HPE P38689-001",
        "AMD EPYC 7763 64-Core 2.45GHz SP3",
        # Both asserted: contradictory, so unknown rather than first-wins.
        "AMD EPYC 7763 Unlocked (was Dell Locked)",
        # Not in the explicit vocabulary.
        "AMD EPYC 9354 Processor Unlock",
    ],
)
def test_branding_or_silence_or_contradiction_is_unknown(title: str) -> None:
    assert _lock(title) is None


def test_lock_never_vetoes_identity() -> None:
    locked = cpu.extract(canonicalize_title("AMD EPYC 7742 64-Core SP3 Dell Locked"))
    spec = ladder.HardAttrs(category=cpu.CpuHard(socket="sp3", cores=64))
    assert cpu.veto(locked, spec) == []
    assert "epyc7742" in _keys("AMD EPYC 7742 64-Core SP3 Dell Locked")


# ── C: clause truth table ────────────────────────────────────────────────────

_POLICY = CategoryPolicy()


def _lock_attr(value: str, confidence: float = 0.9) -> Attribute[str]:
    return Attribute(value=value, confidence=confidence, layer="test", source_text=value)


@pytest.mark.parametrize(
    ("required", "lock", "outcome"),
    [
        (True, _lock_attr(cpu.VENDOR_UNLOCKED), EligibilityVerdict.MATCH),
        (True, _lock_attr(cpu.VENDOR_LOCKED), EligibilityVerdict.NO_MATCH),
        (True, None, EligibilityVerdict.UNKNOWN),
        # Below the policy threshold is no evidence at all.
        (True, _lock_attr(cpu.VENDOR_UNLOCKED, 0.5), EligibilityVerdict.UNKNOWN),
        (False, _lock_attr(cpu.VENDOR_UNLOCKED), EligibilityVerdict.MATCH),
        (False, _lock_attr(cpu.VENDOR_LOCKED), EligibilityVerdict.MATCH),
        (False, None, EligibilityVerdict.MATCH),
    ],
)
def test_vendor_lock_clause_truth_table(
    required: bool, lock: Attribute[str] | None, outcome: EligibilityVerdict
) -> None:
    result = vendor_lock_clause(required, lock, _POLICY, {})
    assert result.clause == "cpu.vendor_lock"
    assert result.outcome is outcome
    if not required:
        assert result.evidence_tier is EvidenceTier.NONE
        assert result.detail == "no constraint"


@pytest.mark.parametrize(
    "title",
    [
        "Intel Core i9-13900K Unlocked Desktop Processor",
        "Intel Xeon Gold 6448Y locked multiplier server CPU",
        "Unlocked CPU 16 cores",  # no brand: scope unknown, never read
    ],
)
def test_vendor_lock_is_read_only_on_amd_parts(title: str) -> None:
    # Intel "Unlocked"/"locked" is the multiplier, not a PSB vendor lock.
    assert _lock(title) is None


def test_amd_vendor_lock_still_read_with_brand_in_a_masked_span() -> None:
    assert _lock("OEM Version of AMD EPYC 7763 unlocked") == cpu.VENDOR_UNLOCKED
