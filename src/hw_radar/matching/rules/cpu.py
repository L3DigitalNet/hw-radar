"""CPU rules (MS2-D-04/-05): listing-side extraction, rung-1 candidates, and the
hard-attribute veto against `cpu_spec`.

Candidates are the identifiers MS2-D-06 names as first-party aliases: the
processor name ('Xeon Gold 6448Y', 'EPYC 9654'), Intel's box/tray ordering codes
and S-spec codes, and AMD's OPN ('100-000000xxx'). A processor name is emitted
both whole ('xeongold6448y') and as its bare number ('6448y'), because seeds
alias both spellings. The bare number is only ever emitted from inside a matched
product-line phrase, never from a free-standing number, so '6338' in an
unrelated title can never reach the alias table.

Socket values are compared by `socket_key`, which drops the package prefix and
separators: Intel ARK states 'FCLGA4677' while listings say 'LGA 4677', and
those are one socket. `cpu_spec.socket` stays the seed's lowercase token; only
the comparison is normalized.

Veto fields: socket and cores. tdp_w is extracted but never vetoes, because
configurable TDP makes a listing's quoted wattage an unreliable identity signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from hw_radar.matching import vocab
from hw_radar.matching.ladder import CategoryHardAttrs, HardAttrs
from hw_radar.matching.rules import (
    CandidateSet,
    add_code_tokens,
    add_house_skus,
    add_structured,
    sole_pattern,
    sole_value,
)
from hw_radar.matching.types import (
    Attribute,
    CategoryAttributes,
    ExtractedAttributes,
    MpnCandidate,
    TokenKind,
)

SLUG = "cpu"


@dataclass(frozen=True)
class CpuAttributes(CategoryAttributes):
    socket: Attribute[str] | None = None  # socket_key form, e.g. 'lga4677', 'sp5'
    cores: Attribute[int] | None = None
    tdp_w: Attribute[int] | None = None


@dataclass(frozen=True)
class CpuHard(CategoryHardAttrs):
    """`cpu_spec` values for the veto; None = the catalog does not know. The
    socket is already in socket_key form."""

    socket: str | None = None
    cores: int | None = None


_BRANDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bintel\b|\bxeon\b|\bcore\s+(?:i[3579]|ultra)\b"), "intel"),
    (re.compile(r"\bamd\b|\bepyc\b|\bryzen\b|\bthreadripper\b"), "amd"),
)

_SOCKETS = (
    re.compile(r"\b(?:fc)?lga\s?-?\d{3,4}\b"),
    re.compile(r"\b(?:socket\s+)?(?:am[2-5]\+?|sp[3-6]|s?trx4|str5|swrx8|fm2\+?)\b"),
)
_CORES = re.compile(r"\b(\d{1,3})[- ]?cores?\b|\b(\d{1,3})c/\d{1,3}t\b")
_TDP = re.compile(r"\b(\d{2,3})\s?w\b")

# (vendor, pattern, bare-number group or None). Group 0 is the full product-line
# phrase; the named group is the bare model number emitted alongside it.
_NAMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "intel",
        re.compile(r"\bxeon\s+(?:platinum|gold|silver|bronze)\s+(?P<num>\d{4}[a-z]{0,2}\+?)"),
    ),
    ("intel", re.compile(r"\bxeon\s+(?P<num>(?:e[357]|w|d)-?\s?\d{4,5}[a-z]{0,2}(?:\s+v\d)?)\b")),
    ("intel", re.compile(r"\bcore\s+(?P<num>i[3579]-?\s?\d{4,5}[a-z]{0,3})\b")),
    ("intel", re.compile(r"\bcore\s+ultra\s+[3579]\s+(?P<num>\d{3}[a-z]{0,2})\b")),
    ("amd", re.compile(r"\bepyc\s+(?P<num>\d{4}[a-z]{0,2})\b")),
    (
        "amd",
        re.compile(
            r"\bryzen\s+(?:threadripper\s+)?(?:pro\s+)?[3579]?\s*(?P<num>\d{4}[a-z0-9]{0,3})\b"
        ),
    ),
    ("amd", re.compile(r"\bthreadripper\s+(?:pro\s+)?(?P<num>\d{4}[a-z]{0,2})\b")),
)
# Ordering and marking codes. Intel box/tray codes ('bx8071513900k',
# 'pk8071305074801') and AMD OPNs ('100-000000789') are self-identifying shapes;
# the 5-character S-spec ('srmgd') is not, so it needs an Intel line word.
_ORDERING: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("intel", re.compile(r"\b(?:bx|bxc|bv|cm|cd|pk)\d{6,}[a-z0-9]*\b")),
    ("amd", re.compile(r"\b100-\d{9}\b")),
)
_SSPEC = re.compile(r"\bsr[a-z0-9]{3}\b")
_VOCAB_TAILS = re.compile(r"(?:\d(?:ghz|mhz|mb|gb|w)|lga\d+|\dc/\d+t|\d-?cores?)$")


def socket_key(value: str) -> str:
    """The comparison form of a socket name: lowercase alphanumerics with any
    'socket' word and Intel 'fc' package prefix dropped ('FCLGA4677' and
    'LGA 4677' → 'lga4677'). The one normalizer for both sides of the veto."""
    key = re.sub(r"[^a-z0-9+]", "", value.casefold()).removeprefix("socket")
    return key.removeprefix("fc")


def _brand(title: str) -> Attribute[str] | None:
    return sole_pattern(title, _BRANDS, 0.9)


def extract(title: str) -> ExtractedAttributes:
    sockets = [(socket_key(m.group(0)), m.group(0)) for p in _SOCKETS for m in p.finditer(title)]
    cores = [(int(m.group(1) or m.group(2)), m.group(0)) for m in _CORES.finditer(title)]
    payload = CpuAttributes(
        socket=sole_value(sockets, 0.9),
        cores=sole_value(cores, 0.85),
        tdp_w=sole_value(((int(m.group(1)), m.group(0)) for m in _TDP.finditer(title)), 0.7),
    )
    brand = _brand(title)
    return replace(vocab.offer_terms(title), brand=brand, category_attrs=payload)


def extract_candidates(
    title: str, *, structured_mpn: str | None = None, source_key: str = ""
) -> list[MpnCandidate]:
    out = CandidateSet()
    brand = _brand(title)
    add_structured(out, structured_mpn, TokenKind.MANUFACTURER_MPN, brand.value if brand else "")
    for vendor, pattern in _ORDERING:
        for m in pattern.finditer(title):
            out.add(m.group(0), TokenKind.MANUFACTURER_MPN, vendor=vendor, confidence=0.9)
    if brand is not None and brand.value == "intel":
        for m in _SSPEC.finditer(title):
            out.add(m.group(0), TokenKind.MANUFACTURER_MPN, vendor="intel", confidence=0.85)
    for vendor, pattern in _NAMES:
        for m in pattern.finditer(title):
            out.add(m.group(0), TokenKind.MANUFACTURER_MPN, vendor=vendor, confidence=0.85)
            out.add(m.group("num"), TokenKind.MANUFACTURER_MPN, vendor=vendor, confidence=0.75)
    add_house_skus(out, title, source_key)
    add_code_tokens(out, title, _VOCAB_TAILS)
    return out.result()


def veto(extracted: ExtractedAttributes, catalog: HardAttrs) -> list[str]:
    """Fields where the listing and `cpu_spec` are both known and disagree."""
    listing = extracted.category_attrs
    spec = catalog.category
    if not isinstance(listing, CpuAttributes) or not isinstance(spec, CpuHard):
        return []
    vetoed: list[str] = []
    if (
        listing.socket is not None
        and spec.socket is not None
        and listing.socket.value != spec.socket
    ):
        vetoed.append("socket")
    if listing.cores is not None and spec.cores is not None and listing.cores.value != spec.cores:
        vetoed.append("cores")
    return vetoed
