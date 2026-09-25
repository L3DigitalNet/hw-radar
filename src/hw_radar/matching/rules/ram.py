"""Memory-module rules (MS2-D-04/-05): listing-side extraction, rung-1 candidates,
and the hard-attribute veto against `ram_spec`.

Brand is the module maker or the OEM whose part number the listing carries, but
only when the title names exactly one: server memory is routinely dual-labeled
("HPE 815100-B21 ... Samsung M393A4K40CB2-CTD"), and picking either brand would
brand-gate away the other label's exact hit. Vendor-shaped part numbers carry
their own vendor hint, so a dual-labeled title keeps brand evidence per token.

Kits: '2x32GB' reads as modules_per_kit=2, module_capacity_gb=32. Without the
kit form, one stated size is the module size and the kit count stays unknown —
'64GB' alone cannot say whether it is one module or a 2x32 kit.

Veto fields: generation, module_type, ecc, module_capacity_gb, speed_mts, ranks.
Each is part of what a module part number identifies. modules_per_kit is not
vetoed: a listing selling two single-module parts as '2x32GB' is a quantity,
not a different part.
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

SLUG = "ram"


@dataclass(frozen=True)
class RamAttributes(CategoryAttributes):
    generation: Attribute[str] | None = None  # ddr3 | ddr4 | ddr5
    module_type: Attribute[str] | None = None  # udimm | rdimm | lrdimm | sodimm
    ecc: Attribute[bool] | None = None
    module_capacity_gb: Attribute[int] | None = None
    modules_per_kit: Attribute[int] | None = None
    speed_mts: Attribute[int] | None = None
    ranks: Attribute[int] | None = None


@dataclass(frozen=True)
class RamHard(CategoryHardAttrs):
    """`ram_spec` values for the veto; None = the catalog does not know."""

    generation: str | None = None
    module_type: str | None = None
    ecc: bool | None = None
    module_capacity_gb: int | None = None
    speed_mts: int | None = None
    ranks: int | None = None


VETO_FIELDS = ("generation", "module_type", "ecc", "module_capacity_gb", "speed_mts", "ranks")

_BRANDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmicron\b|\bcrucial\b"), "micron"),
    (re.compile(r"\bsamsung\b"), "samsung"),
    (re.compile(r"\bsk\s?hynix\b|\bhynix\b"), "sk_hynix"),
    (re.compile(r"\bkingston\b"), "kingston"),
    (re.compile(r"\bhpe\b|\bhp\b|\bhewlett\b"), "hpe"),
    (re.compile(r"\bdell\b"), "dell"),
    (re.compile(r"\blenovo\b"), "lenovo"),
)

_GENERATION: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bddr3l?\b|\bddr3-\d{4}|\bpc3l?-\d{4,5}"), "ddr3"),
    (re.compile(r"\bddr4\b|\bddr4-\d{4}|\bpc4-\d{4,5}"), "ddr4"),
    (re.compile(r"\bddr5\b|\bddr5-\d{4}|\bpc5-\d{4,5}"), "ddr5"),
)
_MODULE_TYPE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\blrdimm\b|\bload[- ]reduced\b"), "lrdimm"),
    (re.compile(r"\brdimm\b|\bregistered\b"), "rdimm"),
    (re.compile(r"\budimm\b|\bunbuffered\b"), "udimm"),
    (re.compile(r"\bso-?dimm\b"), "sodimm"),
)
_NON_ECC = re.compile(r"\bnon[- ]?ecc\b")
# 'ecc' not preceded by 'non-'/'non ', so 'non-ecc' never also reads as ECC.
_ECC = re.compile(r"(?<!non-)(?<!non )\becc\b")
_KIT = re.compile(r"\b(\d{1,2})\s?x\s?(\d{1,3})\s?gb\b")
_SIZE = re.compile(r"\b(\d{1,3})\s?gb\b(?!/s)")
_SPEED_MTS = re.compile(r"\bddr[345]l?-(\d{4})\b|\b(\d{4})\s?(?:mhz|mt/?s)\b")
# PC module names: 'pc4-25600' is bandwidth in MB/s (8 bytes per transfer), while
# 'pc4-2933y' is the JEDEC speed-bin form that states MT/s directly.
_SPEED_PC = re.compile(r"\bpc[345]l?-(\d{4,5})[a-z]{0,3}\b")
_RANKS_RX = re.compile(r"\b([1248])rx(?:4|8|16)\b")
_RANKS_WORD = re.compile(r"\b(single|dual|quad|octal)[- ]rank\b")
_RANK_WORDS = {"single": 1, "dual": 2, "quad": 4, "octal": 8}

# (vendor, manufacturer part-number shape), matched without a brand gate: each
# shape is distinctive enough on its own.
_MFR_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "micron",
        re.compile(r"\bmt[ac]\d{1,2}[a-z]{1,4}\d{1,2}g\d{2}[a-z0-9]{1,6}(?:-[a-z0-9]{2,8})?\b"),
    ),
    ("micron", re.compile(r"\bmtc\d{2}[a-z]\d{4}[a-z0-9]{4,10}\b")),
    ("micron", re.compile(r"\bct\d{1,3}g\d[a-z0-9]{3,12}\b")),
    ("samsung", re.compile(r"\bm[34]\d{2}[a-z]\d[a-z0-9]{4,8}-[a-z0-9]{3,4}\b")),
    ("sk_hynix", re.compile(r"\bhm[a-z]{1,2}\d[a-z0-9]{6,10}-[a-z0-9]{2,3}\b")),
    ("kingston", re.compile(r"\bk(?:sm|vr|th)\d{2}[a-z0-9]{2,6}/\d{1,3}[a-z0-9]{0,6}\b")),
)
# (vendor, OEM part-number pattern with ONE capture group, gate or None). OEM
# spares numbers are short and generic, so the HPE form needs the vendor word;
# Dell's SNP-prefixed form is self-identifying.
_OEM_RULES: tuple[tuple[str, re.Pattern[str], re.Pattern[str] | None], ...] = (
    ("hpe", re.compile(r"\b(p?\d{5,6}-[a-z0-9]{3})\b"), re.compile(r"\bhpe?\b|\bhewlett\b")),
    ("dell", re.compile(r"\b(snp[a-z0-9]{5,6}/\d{1,3}g)\b"), None),
    ("dell", re.compile(r"\b(a\d{7})\b"), re.compile(r"\bdell\b")),
)
_VOCAB_TAILS = re.compile(
    r"(?:\d(?:gb|mb|mhz)|mt/s|pc[345]l?-\d+[a-z]{0,3}(?:-[a-z])?|ddr[345]l?-\d+|\drx\d+)$"
)


def _speed(title: str) -> Attribute[int] | None:
    found: list[tuple[int, str]] = []
    for m in _SPEED_MTS.finditer(title):
        found.append((int(m.group(1) or m.group(2)), m.group(0)))
    for m in _SPEED_PC.finditer(title):
        value = int(m.group(1))
        found.append((value // 8 if value >= 10_000 else value, m.group(0)))
    return sole_value(found, 0.85)


def _ranks(title: str) -> Attribute[int] | None:
    found = [(int(m.group(1)), m.group(0)) for m in _RANKS_RX.finditer(title)]
    found += [(_RANK_WORDS[m.group(1)], m.group(0)) for m in _RANKS_WORD.finditer(title)]
    return sole_value(found, 0.9)


def _ecc(title: str) -> Attribute[bool] | None:
    found = [(False, m.group(0)) for m in _NON_ECC.finditer(title)]
    found += [(True, m.group(0)) for m in _ECC.finditer(title)]
    return sole_value(found, 0.9)


def _capacity(title: str) -> tuple[Attribute[int] | None, Attribute[int] | None]:
    kits = list(_KIT.finditer(title))
    if kits:
        per_kit = sole_value(((int(m.group(1)), m.group(0)) for m in kits), 0.9)
        size = sole_value(((int(m.group(2)), m.group(0)) for m in kits), 0.9)
        return size, per_kit
    size = sole_value(((int(m.group(1)), m.group(0)) for m in _SIZE.finditer(title)), 0.8)
    return size, None


def extract(title: str) -> ExtractedAttributes:
    size, per_kit = _capacity(title)
    payload = RamAttributes(
        generation=sole_pattern(title, _GENERATION, 0.9),
        module_type=sole_pattern(title, _MODULE_TYPE, 0.9),
        ecc=_ecc(title),
        module_capacity_gb=size,
        modules_per_kit=per_kit,
        speed_mts=_speed(title),
        ranks=_ranks(title),
    )
    return replace(
        vocab.offer_terms(title),
        brand=sole_pattern(title, _BRANDS, 0.9),
        category_attrs=payload,
    )


def extract_candidates(
    title: str, *, structured_mpn: str | None = None, source_key: str = ""
) -> list[MpnCandidate]:
    out = CandidateSet()
    if structured_mpn:
        folded = structured_mpn.casefold().strip()
        vendor = next((v for v, p in _MFR_SHAPES if p.fullmatch(folded)), "")
        kind = TokenKind.MANUFACTURER_MPN if vendor else TokenKind.UNKNOWN_CODE
        add_structured(out, structured_mpn, kind, vendor)
    for vendor, pattern in _MFR_SHAPES:
        for m in pattern.finditer(title):
            out.add(m.group(0), TokenKind.MANUFACTURER_MPN, vendor=vendor, confidence=0.9)
    for vendor, pattern, gate in _OEM_RULES:
        if gate is not None and not gate.search(title):
            continue
        for m in pattern.finditer(title):
            out.add(m.group(1), TokenKind.OEM_PN, vendor=vendor, confidence=0.8)
    add_house_skus(out, title, source_key)
    add_code_tokens(out, title, _VOCAB_TAILS)
    return out.result()


def veto(extracted: ExtractedAttributes, catalog: HardAttrs) -> list[str]:
    """Fields where the listing and `ram_spec` are both known and disagree."""
    listing = extracted.category_attrs
    spec = catalog.category
    if not isinstance(listing, RamAttributes) or not isinstance(spec, RamHard):
        return []
    vetoed: list[str] = []
    for name in VETO_FIELDS:
        attr: Attribute[object] | None = getattr(listing, name)
        known: object = getattr(spec, name)
        if attr is not None and known is not None and attr.value != known:
            vetoed.append(name)
    return vetoed
