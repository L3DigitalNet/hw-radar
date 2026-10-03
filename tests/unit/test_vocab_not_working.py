"""The broken-unit spellings read as for_parts in every category (matcher
2026.10.1). Before it, only the exact text "not working" did, so "Processor
non-working" (CPU corpus cpu-0069) asserted no condition and auto-accepted a
broken EPYC at model grain."""

from __future__ import annotations

import pytest

from hw_radar.matching import vocab
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import cpu


def _condition(title: str) -> str | None:
    attr = vocab.extract(canonicalize_title(title)).condition
    return None if attr is None else attr.value


@pytest.mark.parametrize(
    "title",
    [
        # cpu-0069, verbatim.
        "100-000000805 - D3 AMD EPYC 9354P 3.25GHz 32-Core Gen-4 Processor non-working re",
        "Seagate ST12000NM0008 12TB Non Working",
        "WD Red Plus 4TB NONWORKING",
        "AMD EPYC 7763 not-working",
        "Seagate 16TB non-functional",
        "Seagate 16TB non functional",
        "Seagate 16TB nonfunctional",
        "Seagate 16TB NOT FUNCTIONAL",
        "Seagate 16TB not working",
    ],
)
def test_broken_unit_spellings_read_as_for_parts(title: str) -> None:
    assert _condition(title) == "for_parts"


@pytest.mark.parametrize(
    ("title", "condition"),
    [
        # Near words that assert no breakage keep their old reading.
        ("Seagate 16TB fully functional", None),
        ("Seagate 16TB functional tested", None),
        ("Seagate 16TB working pull", "used"),
        ("Seagate 16TB tested working used", "used"),
    ],
)
def test_working_wording_is_not_for_parts(title: str, condition: str | None) -> None:
    assert _condition(title) == condition


def test_for_parts_outranks_a_following_used_and_denies_nothing() -> None:
    # "non" owns its phrase, so it does not reach "used" as a negator.
    extracted = vocab.extract(canonicalize_title("Seagate 16TB non-working used"))
    assert extracted.condition is not None and extracted.condition.value == "for_parts"
    assert extracted.denied_conditions is None


@pytest.mark.parametrize(
    "title",
    ["AMD EPYC 7763 non-working Unlocked", "AMD EPYC 7763 nonfunctional Unlocked"],
)
def test_broken_unit_wording_does_not_negate_an_unlock(title: str) -> None:
    payload = cpu.extract(canonicalize_title(title)).category_attrs
    assert isinstance(payload, cpu.CpuAttributes)
    assert payload.vendor_lock is not None and payload.vendor_lock.value == "unlocked"
