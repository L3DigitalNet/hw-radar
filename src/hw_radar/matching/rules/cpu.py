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

Veto fields: socket and cores, plus the listing-only `sample` marker (see
below). tdp_w is extracted but never vetoes, because configurable TDP makes a
listing's quoted wattage an unreliable identity signal.

Identity evidence (candidates and brand) is read from the title after
`mask_reference_spans` with the CPU phrase set, exactly as the drive layers
mask theirs: a model cited as "compatible with X" or "OEM version of X" is not
the listed part. Physical attributes read the unmasked title. Two EPYC-specific
guards follow the owner's F6 rule that a CPU auto-accept needs exact
authoritative identity:
  - A title naming more than one EPYC model ('7742/7702', '7B13 ... epyc
    7763') yields no title-derived candidate at all, so it can never pick one
    of them: an unseeded second model would otherwise leave a lone alias hit
    on the seeded one and read as unambiguous.
  - An AMD OPN followed by a '-NN' suffix ('100-000000314-04', a QS sample
    marking) is not the OPN, so it is not a candidate.

Engineering and qualification samples ('ES', 'QS', 'engineering sample',
'pre-production', a suffixed OPN) are different parts from the retail SKU:
their own OPNs, stepping, clocks and often locked or unfinished firmware. A
sample title still names the retail model ('EPYC 7763 QS'), so candidate
filtering alone would leave that name as an exact alias hit. The marker is
instead extracted as the `sample` attribute and always vetoes, which routes
any alias hit to review (a contradiction) rather than an auto-accept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from hw_radar.matching import vocab
from hw_radar.matching.ladder import CategoryHardAttrs, HardAttrs
from hw_radar.matching.normalize import mask_reference_spans, reference_phrase_pattern
from hw_radar.matching.rules import (
    LAYER,
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
    # The sample marker as the title printed it; None = no marker (retail or
    # unstated). Unlike the other fields this is never compared to the catalog.
    sample: Attribute[str] | None = None


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
    # (?!-\w): a suffixed OPN ('100-000000314-04') is a sample/stepping
    # marking, not the part number, so its stem must not reach the alias table.
    ("amd", re.compile(r"\b100-\d{9}\b(?!-\w)")),
)
_SSPEC = re.compile(r"\bsr[a-z0-9]{3}\b")
_VOCAB_TAILS = re.compile(r"(?:\d(?:ghz|mhz|mb|gb|w)|lga\d+|\dc/\d+t|\d-?cores?)$")

# CPU-local reference phrase on top of the shared list. For a CPU, "7J13 OEM
# version of 7763" names a different SKU (its own OPN, clocks and firmware).
# Kept out of the shared list: drive masking is pinned by the A0 decision
# oracle and tests/unit/test_reference_context.py, and no drive evidence says
# the phrase names a different drive there (an OEM-relabelled drive can be
# physically the cited model). Local, it leaves drive behavior unchanged by
# construction.
_REFERENCE = reference_phrase_pattern("oem version of")
# An EPYC model number as titles print it: generation digit first, last digit
# the generation (1-5), letters allowed inside (7H12, 72F3, 9V84) and a short
# SKU suffix (9354P, 9684X, 4584PX). Excluded because they are not models:
# x00y series names ('7003 series'), and numbers followed by a unit. Sockets
# are blanked before the scan ('LGA 4094').
_EPYC_MODEL = re.compile(
    r"\b(?![3-9]00\d)[3-9][0-9a-z]{2}[1-5](?:px|hs|p|f|x|s)?\b"
    r"(?!\s?(?:ghz|mhz|mt/s|gb|mb|tb|w\b))"
)
_EPYC_NAME = re.compile(r"\bepyc\b")
# Sample markers, matched as whole alphanumeric tokens: the lookarounds are on
# [a-z0-9], not \b, so 'es' inside 'series', 'esxi', 'tested' or 'e5' never
# fires while '7763-es' and 'es/qs' do. 'sample' alone covers the
# 'engineering sample' and 'qualification sample' phrases. A suffixed AMD OPN is
# the QS/ES marking itself even with no word beside it.
_SAMPLE = re.compile(
    r"(?<![a-z0-9])(?:es[12]?|qs|samples?|pre-?production|pre\s+production"
    r"|100-\d{9}-[a-z0-9]+)(?![a-z0-9])"
)


def socket_key(value: str) -> str:
    """The comparison form of a socket name: lowercase alphanumerics with any
    'socket' word and Intel 'fc' package prefix dropped ('FCLGA4677' and
    'LGA 4677' → 'lga4677'). The one normalizer for both sides of the veto."""
    key = re.sub(r"[^a-z0-9+]", "", value.casefold()).removeprefix("socket")
    return key.removeprefix("fc")


def _brand(title: str) -> Attribute[str] | None:
    return sole_pattern(title, _BRANDS, 0.9)


def _identity_text(title: str) -> str:
    return mask_reference_spans(title, _REFERENCE)


def _sample(identity: str) -> Attribute[str] | None:
    m = _SAMPLE.search(identity)
    if m is None:
        return None
    return Attribute(value=m.group(0), confidence=0.9, layer=LAYER, source_text=m.group(0))


def _names_several_epyc_models(identity: str) -> bool:
    """Whether an EPYC title names more than one distinct model number.

    Read from the reference-masked title, so a model cited only as a
    reference object does not count. Suffixes count as distinct models
    ('9354' and '9354p' are two parts).
    """
    if _EPYC_NAME.search(identity) is None:
        return False
    unsocketed = identity
    for pattern in _SOCKETS:
        unsocketed = pattern.sub(lambda m: " " * len(m.group(0)), unsocketed)
    return len({m.group(0) for m in _EPYC_MODEL.finditer(unsocketed)}) > 1


def extract(title: str) -> ExtractedAttributes:
    sockets = [(socket_key(m.group(0)), m.group(0)) for p in _SOCKETS for m in p.finditer(title)]
    cores = [(int(m.group(1) or m.group(2)), m.group(0)) for m in _CORES.finditer(title)]
    # The sample marker is identity evidence, so it reads the reference-masked
    # title: "replacement for an engineering sample" does not make the listed
    # part one.
    identity = _identity_text(title)
    payload = CpuAttributes(
        socket=sole_value(sockets, 0.9),
        cores=sole_value(cores, 0.85),
        tdp_w=sole_value(((int(m.group(1)), m.group(0)) for m in _TDP.finditer(title)), 0.7),
        sample=_sample(identity),
    )
    brand = _brand(identity)
    return replace(vocab.offer_terms(title), brand=brand, category_attrs=payload)


def extract_candidates(
    title: str, *, structured_mpn: str | None = None, source_key: str = ""
) -> list[MpnCandidate]:
    out = CandidateSet()
    title = _identity_text(title)
    brand = _brand(title)
    add_structured(out, structured_mpn, TokenKind.MANUFACTURER_MPN, brand.value if brand else "")
    if _names_several_epyc_models(title):
        # Only the merchant-asserted structured MPN survives: every title token
        # (names, OPNs, code tokens, house SKUs) could belong to either model.
        return out.result()
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
    """Fields where the listing and `cpu_spec` are both known and disagree, plus
    'sample' whenever the listing carries a sample marker, whatever the target."""
    listing = extracted.category_attrs
    if not isinstance(listing, CpuAttributes):
        return []
    vetoed: list[str] = []
    # Every catalog CPU is a retail SKU (cpu_spec has no sample field and no
    # seed lists a sample part), so a sample listing contradicts any target,
    # including one with no cpu_spec at all. Checked before the spec guard for
    # that reason; a missing spec must not let a sample through to accept.
    if listing.sample is not None:
        vetoed.append("sample")
    spec = catalog.category
    if not isinstance(spec, CpuHard):
        return vetoed
    if (
        listing.socket is not None
        and spec.socket is not None
        and listing.socket.value != spec.socket
    ):
        vetoed.append("socket")
    if listing.cores is not None and spec.cores is not None and listing.cores.value != spec.cores:
        vetoed.append("cores")
    return vetoed
