"""GPU/accelerator rules (MS2-D-04/-05): listing-side extraction, rung-1 candidates,
and the hard-attribute veto against `gpu_spec`.

Brand is the CHIP vendor (nvidia/amd/intel), not the board partner. The v1
catalog is seeded from chip-vendor pages (MS2-D-06), so the model manufacturer a
hit is brand-gated against is the chip vendor; a PNY- or ASUS-branded listing
still names the chip it carries. Board-partner names are therefore not brands
here, and a title that names two chip vendors has no brand at all.

Name candidates (A100, RTX 4090, MI250X, ...) are emitted only when the title
names the matching chip vendor or product line: short names like `t4` or `a2`
are otherwise ordinary tokens. They are MANUFACTURER_MPN candidates with the
chip vendor as the vendor hint, so they can only reach `mpn`/`retail_pn`/
`region_pn` aliases. Each name is also emitted extended by the VRAM/interface
qualifiers and the chip-vendor word the title writes contiguously around it,
because the seeded data-center aliases are those longer spellings ('A100 80GB
PCIe'); tests/db/test_category_seed_reachability.py pins every seeded alias.

Veto fields: chip_vendor, vram_gb, interface, cooling. tdp_w is not vetoed —
listings quote board power, configurable limits, or nothing, so a TDP mismatch
is not evidence of a different product.
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

SLUG = "gpu"


@dataclass(frozen=True)
class GpuAttributes(CategoryAttributes):
    chip_vendor: Attribute[str] | None = None  # gpu_spec.chip_vendor literals
    vram_gb: Attribute[int] | None = None
    interface: Attribute[str] | None = None  # pcie | sxm | oam | mxm
    cooling: Attribute[str] | None = None  # active | passive | liquid


@dataclass(frozen=True)
class GpuHard(CategoryHardAttrs):
    """`gpu_spec` values for the veto; None = the catalog does not know."""

    chip_vendor: str | None = None
    vram_gb: int | None = None
    interface: str | None = None
    cooling: str | None = None


_VENDORS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bnvidia\b|\bgeforce\b|\bquadro\b|\btesla\b"), "nvidia"),
    (re.compile(r"\bamd\b|\bradeon\b|\binstinct\b"), "amd"),
    (re.compile(r"\bintel\b"), "intel"),
)
# '24gb' but not '12gb/s'. Every size in the title counts: a lone size is VRAM.
_VRAM = re.compile(r"\b(\d{1,3})\s?gb\b(?!/s)")
_INTERFACES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsxm\d?\b"), "sxm"),
    (re.compile(r"\boam\b"), "oam"),
    (re.compile(r"\bmxm\b"), "mxm"),
    (re.compile(r"\bpci-?e\b|\bpcie\s?\d|\bpci express\b"), "pcie"),
)
_COOLING: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bpassive(?:ly cooled)?\b|\bfanless\b"), "passive"),
    (re.compile(r"\bliquid(?:[- ]cooled)?\b|\bwater[- ]?cooled\b"), "liquid"),
    (re.compile(r"\bactive(?:ly)?[- ]cooled\b|\bactive cooling\b|\bblower\b"), "active"),
)

# (chip vendor, name pattern). Group 1 is the product name without line words,
# emitted alongside the full match so both 'geforce rtx 4090' and 'rtx 4090'
# alias spellings are reachable.
_NAMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("nvidia", re.compile(r"\b(?:geforce\s+)?((?:rtx|gtx)\s?\d{4}(?:\s?(?:ti|super))?)\b")),
    ("nvidia", re.compile(r"\b(?:quadro\s+)?(rtx\s?(?:a\d{4}|\d{4}\s?ada))\b")),
    ("nvidia", re.compile(r"\bquadro\s+((?:rtx\s?)?[a-z]?\d{4})\b")),
    ("nvidia", re.compile(r"\b(?:tesla\s+)?([abhlptv]\d{1,3}s?)\b")),
    ("amd", re.compile(r"\b(?:instinct\s+)?(mi\d{2,3}[a-z]?)\b")),
    ("amd", re.compile(r"\bradeon\s+(?:pro\s+)?((?:rx\s?)?\d{4}(?:\s?xtx?)?|w\d{4})\b")),
    ("intel", re.compile(r"\barc\s+([ab]\d{3}m?)\b")),
)
# A VRAM size or interface word directly after a product name. Data-center
# parts are catalogued by the name plus these qualifiers ('A100 80GB PCIe',
# 'H100 PCIe', 'Tesla V100 PCIe (32GB)'), and the alias join is exact, so the
# bare name alone can never reach those aliases. Only qualifiers written
# contiguously after a vendor-gated name extend it, in title order: nothing is
# reordered or synthesized, so 'Tesla V100 32GB PCIe' never becomes the seeded
# 'Tesla V100 PCIe (32GB)' key (it joins only the seed's own 'NVIDIA Tesla V100
# (32GB, PCIe)' alias, via the vendor prefix below).
_QUALIFIER = re.compile(r"\s*,?\s*\(?(?:\d{1,3}\s?gb(?!/s)|pci-?e|sxm\d?)\)?(?![a-z0-9])")
_MAX_QUALIFIERS = 2  # at most one VRAM size and one interface
# The chip-vendor word written directly before a name. Seeds also alias the
# vendor-prefixed datasheet spelling ('NVIDIA RTX A6000', 'NVIDIA Tesla V100
# (32GB, PCIe)'), which the vendor-less name spans can never join.
_VENDOR_PREFIX = re.compile(r"\b(nvidia|amd|intel)\s$")

# NVIDIA board part numbers (699-/900- prefixed), matched without a vendor gate:
# the four-group shape is specific enough on its own.
_NVIDIA_PN = re.compile(r"\b(?:699|900)-[0-9a-z]{5}-[0-9a-z]{4}-[0-9a-z]{3}\b")
_VOCAB_TAILS = re.compile(r"(?:\d(?:gb|mb|tb|w)|gb/s|pcie\d?)$")


def _chip_vendor(title: str) -> Attribute[str] | None:
    return sole_pattern(title, _VENDORS, 0.9)


def extract(title: str) -> ExtractedAttributes:
    chip_vendor = _chip_vendor(title)
    payload = GpuAttributes(
        chip_vendor=chip_vendor,
        vram_gb=sole_value(((int(m.group(1)), m.group(0)) for m in _VRAM.finditer(title)), 0.85),
        interface=sole_pattern(title, _INTERFACES, 0.9),
        cooling=sole_pattern(title, _COOLING, 0.85),
    )
    return replace(vocab.offer_terms(title), brand=chip_vendor, category_attrs=payload)


def extract_candidates(
    title: str, *, structured_mpn: str | None = None, source_key: str = ""
) -> list[MpnCandidate]:
    out = CandidateSet()
    vendor = _chip_vendor(title)
    add_structured(out, structured_mpn, TokenKind.MANUFACTURER_MPN, vendor.value if vendor else "")
    for m in _NVIDIA_PN.finditer(title):
        out.add(m.group(0), TokenKind.MANUFACTURER_MPN, vendor="nvidia", confidence=0.9)
    if vendor is not None:
        for name_vendor, pattern in _NAMES:
            if name_vendor != vendor.value:
                continue
            for m in pattern.finditer(title):
                starts = [m.start(), m.start(1)]
                prefix = _VENDOR_PREFIX.search(title, 0, m.start())
                if prefix is not None and prefix.group(1) == name_vendor:
                    starts.append(prefix.start())
                end = m.end()
                for _ in range(_MAX_QUALIFIERS + 1):
                    for start in starts:
                        out.add(
                            title[start:end],
                            TokenKind.MANUFACTURER_MPN,
                            vendor=name_vendor,
                            confidence=0.8,
                        )
                    qualifier = _QUALIFIER.match(title, end)
                    if qualifier is None:
                        break
                    end = qualifier.end()
    add_house_skus(out, title, source_key)
    add_code_tokens(out, title, _VOCAB_TAILS)
    return out.result()


def veto(extracted: ExtractedAttributes, catalog: HardAttrs) -> list[str]:
    """Fields where the listing and `gpu_spec` are both known and disagree."""
    listing = extracted.category_attrs
    spec = catalog.category
    if not isinstance(listing, GpuAttributes) or not isinstance(spec, GpuHard):
        return []
    vetoed: list[str] = []
    for name in ("chip_vendor", "vram_gb", "interface", "cooling"):
        attr: Attribute[object] | None = getattr(listing, name)
        known: object = getattr(spec, name)
        if attr is not None and known is not None and attr.value != known:
            vetoed.append(name)
    return vetoed
