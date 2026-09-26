"""Seagate ST-number grammar.

Structure per Seagate's official ST Model Number Cheat Sheet (vendor_official):
st + capacity-in-GB digits + 2-letter segment + 3-digit attributes + generation.
The 3-digit attributes block is explicitly variable and NEVER decoded, and the
trailing character is only a generation counter (cheat sheet SC504.1-1102US,
Feb 2011; enterprise quick-reference guide, Oct 2012) — never a family signal.
The segment→family map rides family pages, not the cheat sheet — that tier is
corroborated_community, and asserting a family drops provenance to it.

The `nm` segment asserts NO family. First-party Seagate datasheets show there is
no structural Exos boundary inside it: the same literals (ST4000NM0035,
ST8000NM0055) are listed in the 2016 Enterprise Capacity 3.5 HDD v5 datasheet
(DS1882) and the 2017 Exos 7E8 datasheet (DS1957), while ST1000NM0001 — same
digit-only shape as Exos 7E8's ST1000NM0055 — is Constellation ES (ES.1 product
manual). Mapping `nm` to Exos auto-accepted MS-1e row ghd-0006 (a Constellation
ES.3-titled listing) as Seagate/Exos, an owner-confirmed false merge. An `nm` token
still decodes (vendor, capacity, generation at vendor_official provenance) but
cannot attach at rung 2; family identity for these drives comes only from exact
catalog aliases at rung 1. Rejected alternatives:
- letter-suffix = Exos: digit-suffixed Exos models (ST12000NM0007, ST8000NM0055)
  refute it, and the suffix is documented as a generation counter;
- capacity threshold: Exos 7E8 ships at 1-8 TB (ST1000NM0055 up), overlapping
  the 0.5-4 TB Constellation ES/ES.3 range;
- a literal-MPN allowlist here: a model list is catalog data and belongs in
  first-party refdata via the canonical import path, where it produces rung-1
  exact aliases with citations; a grammar-side copy would drift from it.
The datasheet numbers above (all seagate.com) are the durable citations.

The remaining segment mappings (ne, vn, vx, dm) had no first-party evidence either
way in the research behind this change. They stay until such evidence shows a
segment spanning a pre-rebrand line (e.g. NAS HDD vs IronWolf); the same rule
then applies — drop the mapping, never special-case a model."""

from __future__ import annotations

import re

from hw_radar.matching.types import DecodeResult, Provenance

_GB = 1_000_000_000

_ST = re.compile(r"^st(\d{3,6})([a-z]{2})(\d{3})([a-z0-9]?)$")

# Family pages, not the official cheat sheet → corroborated_community tier.
# `nm` is deliberately absent — see the module docstring before re-adding it.
_SEGMENT_FAMILIES: dict[str, str] = {
    "ne": "ironwolf pro",
    "vn": "ironwolf",
    "vx": "skyhawk",
    "dm": "barracuda",
}


def decode(token: str) -> DecodeResult | None:
    m = _ST.fullmatch(token)
    if m is None:
        return None
    family = _SEGMENT_FAMILIES.get(m.group(2))
    return DecodeResult(
        vendor="seagate",
        family_name=family,
        capacity_bytes=int(m.group(1)) * _GB,
        generation=m.group(4) or None,
        provenance=(Provenance.CORROBORATED_COMMUNITY if family else Provenance.VENDOR_OFFICIAL),
        rule=f"st:{m.group(2)}",
    )
