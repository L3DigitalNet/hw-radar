"""OQ34: family-scoped category ratification must never silently broaden.

Pure checks on matching.categories: how a ratified family is keyed, that an
empty or look-alike scope can never read as "all families", that a scope can
only sit on a category that has a policy and auto-accept on, and the shipped
registry (CPU = AMD EPYC only; gpu/ram flag-off; drive and the basic-watch
categories unscoped, exactly as before OQ34)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hw_radar.matching import categories
from hw_radar.matching.categories import CPU_RATIFIED_FAMILIES, FamilyKey, RatifiedFamilies
from hw_radar.refdata.loader import load_seed_documents

_EPYC = FamilyKey("amd", "epyc")


def test_of_normalizes_like_the_seed_importer() -> None:
    assert FamilyKey.of("amd", "EPYC") == _EPYC
    assert FamilyKey.of("intel", "Xeon Scalable") == FamilyKey("intel", "xeon scalable")


@pytest.mark.parametrize(
    ("manufacturer", "family"),
    [
        ("AMD", "epyc"),  # manufacturer not a normalized key
        ("", "epyc"),
        ("amd", "EPYC"),  # family not canonical
        ("amd", " epyc"),
        ("amd", ""),
    ],
)
def test_unnormalized_key_is_rejected(manufacturer: str, family: str) -> None:
    with pytest.raises(ValueError, match="not a normalized"):
        FamilyKey(manufacturer, family)


def test_empty_scope_is_rejected_not_read_as_everything() -> None:
    with pytest.raises(ValueError, match="at least one family"):
        RatifiedFamilies(frozenset())


@pytest.mark.parametrize(
    "key",
    [
        None,  # a target with no family
        FamilyKey("intel", "epyc"),  # same family text, other manufacturer
        FamilyKey("amd", "epyc embedded"),  # longer name is not a prefix match
        FamilyKey("amd", "ryzen"),
        FamilyKey("amd", "ryzen threadripper"),
        FamilyKey("intel", "xeon scalable"),
    ],
)
def test_scope_permits_only_exact_keys(key: FamilyKey | None) -> None:
    assert CPU_RATIFIED_FAMILIES.permits(key) is False


def test_scope_permits_the_ratified_key() -> None:
    assert CPU_RATIFIED_FAMILIES.permits(_EPYC) is True
    assert CPU_RATIFIED_FAMILIES.keys == frozenset({_EPYC})


def test_scope_needs_a_policy_and_auto_accept() -> None:
    drive = categories.rules_for(categories.DRIVE)
    gpu = categories.rules_for("gpu")
    cpu = categories.rules_for("cpu")
    assert drive is not None and gpu is not None and cpu is not None
    with pytest.raises(ValueError, match="needs an AcceptancePolicy"):
        replace(drive, ratified_families=CPU_RATIFIED_FAMILIES)  # drive has no policy
    with pytest.raises(ValueError, match="needs an AcceptancePolicy"):
        replace(gpu, ratified_families=CPU_RATIFIED_FAMILIES)  # gpu flag is off
    with pytest.raises(ValueError, match="needs an AcceptancePolicy"):
        replace(cpu, auto_accept=False)  # switching CPU off must drop the scope too


def test_registered_scopes() -> None:
    cpu = categories.rules_for("cpu")
    assert cpu is not None and cpu.auto_accept is True
    assert cpu.ratified_families == CPU_RATIFIED_FAMILIES
    for slug in ("gpu", "ram"):
        rules = categories.rules_for(slug)
        assert rules is not None
        assert rules.auto_accept is False
        assert rules.ratified_families is None
    drive = categories.rules_for(categories.DRIVE)
    assert drive is not None
    assert (drive.auto_accept, drive.acceptance, drive.ratified_families) == (True, None, None)
    for slug in ("nic", "hba", "motherboard", "server"):
        rules = categories.rules_for(slug)
        assert rules is not None
        assert rules.auto_accept is True
        assert rules.ratified_families is None


def test_cpu_scope_names_exactly_the_epyc_seed_family() -> None:
    """The ratified key is the family the shipped EPYC seed creates; no other
    shipped CPU seed family (Intel Xeon today) is in scope."""
    seeded = {
        FamilyKey.of(d.manufacturer_key, d.family_name)
        for d in load_seed_documents()
        if d.category == "cpu"
    }
    assert _EPYC in seeded
    assert seeded & CPU_RATIFIED_FAMILIES.keys == {_EPYC}
    assert FamilyKey("intel", "xeon scalable") in seeded - CPU_RATIFIED_FAMILIES.keys
