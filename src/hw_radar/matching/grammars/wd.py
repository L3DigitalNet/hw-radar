"""WD grammars: modern WD retail/enterprise strings + HGST-lineage Ultrastar.

WD publishes NO current master decoder (warranty-verification research):
- 2/3-digit capacity blocks are corroborated_community (family product pages:
  WD20→2TB, WD120→12TB, WD201→20TB).
- The 4-digit block (WD4004FRYZ) and every HGST-style capacity read are
  ambiguous → capacity is asserted as None (inferred tier) and the N2 vocab
  capacity carries the claim. Never guess (ADR-0019 rule 3)."""

from __future__ import annotations

import re

from hw_radar.matching.types import DecodeResult, Provenance

_TB = 1_000_000_000_000

_WD = re.compile(r"^wd(\d{2,4})([a-z]{4})$")
_HGST = re.compile(r"^(wuh|wus|huh|hus|hdn)(\d{6})([a-z0-9]{4,8})$")

# First two suffix letters → family, per WD family product pages. Every name
# must equal canonicalize_title() of the seed family_name for the same line
# (refdata/seeds/wd-*.json): a mismatch splits one product line into a rung-2
# provisional family and a seeded family (pinned by
# tests/unit/test_grammar_seed_family_consistency.py).
#
# `ef` is deliberately absent: it spans two WD families. WD's first-party
# documents list WD20/30/40/60EFAX as "WD Red" (SMR; WD Red product brief)
# and EFPX/EFZX/EFZZ/EFGX/EFBX as "WD Red Plus" (CMR; WD Red Plus datasheet,
# Sept 2025), while older CMR EFAX capacities and the EFRX line were sold as
# Red before WD renamed its CMR drives Red Plus. `ef` → "red" decoded Red Plus
# drives into a family WD does not sell them as. Only the full suffixes that
# the Red Plus datasheet table lists are mapped (_EF_SUFFIX_FAMILIES); EFAX,
# EFRX and any other `ef..` suffix decode with no family. Rejected: a
# per-model list (catalog data belongs in the seeds, which give rung-1 exact
# aliases) and a capacity split of EFAX (no first-party table states one).
_SUFFIX_FAMILIES: dict[str, str] = {
    "kf": "red pro",
    "kr": "gold",
    "fr": "gold",
    "pu": "purple",
    "ez": "blue",
}

_EF_SUFFIX_FAMILIES: dict[str, str] = dict.fromkeys(
    ("efpx", "efzx", "efzz", "efgx", "efbx"), "red plus"
)

_HGST_FAMILIES: dict[str, str] = {
    "wuh": "ultrastar",
    "wus": "ultrastar",
    "huh": "ultrastar",
    "hus": "ultrastar",
    "hdn": "deskstar nas",
}


def decode(token: str) -> DecodeResult | None:
    m = _WD.fullmatch(token)
    if m is not None:
        digits, suffix = m.group(1), m.group(2)
        family = _SUFFIX_FAMILIES.get(suffix[:2]) or _EF_SUFFIX_FAMILIES.get(suffix)
        if len(digits) == 2:
            capacity: int | None = int(digits) * _TB // 10  # WD20 → 2 TB
        elif len(digits) == 3:
            capacity = int(digits[:2]) * _TB  # WD120 → 12 TB, WD201 → 20 TB
        else:
            capacity = None  # 4-digit encoding is ambiguous — never guessed
        provenance = Provenance.INFERRED if capacity is None else Provenance.CORROBORATED_COMMUNITY
        if family is None and capacity is None:
            return None  # neither field derivable: not a useful decode
        return DecodeResult(
            vendor="western_digital",
            family_name=family,
            capacity_bytes=capacity,
            generation=None,
            provenance=provenance,
            rule=f"wd:{suffix[:2]}",
        )
    m = _HGST.fullmatch(token)
    if m is not None:
        prefix = m.group(1)
        return DecodeResult(
            vendor="western_digital" if prefix.startswith("w") else "hgst",
            family_name=_HGST_FAMILIES[prefix],
            capacity_bytes=None,  # HGST capacity digits ambiguous (4040→4TB, 1816→16TB)
            generation=None,
            provenance=Provenance.CORROBORATED_COMMUNITY,
            rule=f"hgst:{prefix}",
        )
    return None
