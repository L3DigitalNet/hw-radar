"""N3 code-shaped token extraction (spec C.3.1).

Manufacturer-MPN shapes match anywhere in the canonical title. OEM shapes are
CONTEXT-GATED (vendor word present, or label-adjacent like 'dp/n <tok>') —
they are short/ambiguous, and Dell's 5-char DP/N or NetApp's X-prefix would
otherwise fire on arbitrary tokens (the NetApp pattern requires 3-4 digits
precisely so 'Exos X16' can never read as a NetApp part). House SKUs are
recognized via the per-source prefix registry — SOURCE-LOCAL aliases only,
never canonical (ADR-0019 rule 2); the registry is empty until MS-1d
connectors observe real SKU shapes. Structured-field MPNs (JSON-LD `mpn`)
outrank every title-mined token.

Every title-mined pass reads the title with reference spans masked
(normalize.mask_reference_spans): an MPN or OEM vendor word cited as "comparable
to X" names another product, and mining it would hand the ladder a rung-1 alias
hit or a rung-2 decode for the wrong item (MS-1e ebay-0021). The structured
field is exempt — the merchant asserted it as this item's MPN."""

from __future__ import annotations

import re

from hw_radar.matching.normalize import mask_reference_spans, normalize_alias_text
from hw_radar.matching.types import MpnCandidate, TokenKind

_MFR_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("seagate", re.compile(r"\bst\d{3,6}[a-z]{2}\d{3}[a-z0-9]?\b")),
    ("western_digital", re.compile(r"\bwd\d{2,4}[a-z]{4}\b")),
    ("western_digital", re.compile(r"\b(?:wuh|wus|huh|hus|hdn)\d{6}[a-z0-9]{4,8}\b")),
    ("toshiba", re.compile(r"\b(?:mg|mn)\d{2}[a-z]{3}\d{1,3}t?[a-z]{0,3}\b")),
    ("samsung", re.compile(r"\bmz-?[a-z0-9]{6,9}(?:/[a-z0-9]{2,4})?\b")),
)

# (vendor, token pattern with ONE capture group, gate pattern or None).
# gate=None means the token pattern itself is label-adjacent (dp/n, fru).
_OEM_RULES: tuple[tuple[str, re.Pattern[str], re.Pattern[str] | None], ...] = (
    ("dell_emc", re.compile(r"\b(00[45]\d{6})\b"), re.compile(r"\b(?:emc|dell)\b")),
    ("dell_emc", re.compile(r"\bdp/?n[: ]*0?([0-9a-z]{5})\b"), None),
    ("hpe", re.compile(r"\b(\d{6}-[a-z0-9]{3})\b"), re.compile(r"\b(?:hpe|hp|hewlett|proliant)\b")),
    ("netapp", re.compile(r"\b(x\d{3,4}[a-z]?(?:-r\d)?)\b"), re.compile(r"\bnetapp\b")),
    ("lenovo_ibm", re.compile(r"\bfru[: ]*([0-9a-z]{7})\b"), None),
)

# Per-source house-SKU prefixes (lowercase, matched against canonical tokens).
# Empty by design at MS-1b: MS-1d connector work observes real SKU shapes and
# fills this in (spec C.3.1: house SKUs → source-local aliases, never canonical).
HOUSE_SKU_PREFIXES: dict[str, tuple[str, ...]] = {}

_CODE_SHAPE = re.compile(r"\b[a-z0-9][a-z0-9./-]{5,23}\b")
_TWO_ALPHA = re.compile(r"[a-z].*[a-z]")
_TWO_DIGIT = re.compile(r"\d.*\d")
# Vocab-owned tokens that are code-shaped but never MPN candidates.
_VOCAB_TAILS = re.compile(r"(?:\d(?:\.\d+)?(?:tb|gb|mb|rpm)|gb/s|512e|512n|4kn|inch)$")


def _classify_shape(token: str) -> tuple[TokenKind, str]:
    for vendor, pattern in _MFR_SHAPES:
        if pattern.fullmatch(token):
            return TokenKind.MANUFACTURER_MPN, vendor
    return TokenKind.UNKNOWN_CODE, ""


def extract_candidates(
    title: str, *, structured_mpn: str | None = None, source_key: str = ""
) -> list[MpnCandidate]:
    out: dict[str, MpnCandidate] = {}

    def add(candidate: MpnCandidate) -> None:
        existing = out.get(candidate.normalized)
        if existing is None or candidate.confidence > existing.confidence:
            out[candidate.normalized] = candidate

    if structured_mpn:
        canonical = structured_mpn.casefold().strip()
        kind, vendor = _classify_shape(canonical)
        add(
            MpnCandidate(
                raw=structured_mpn,
                normalized=normalize_alias_text(structured_mpn),
                kind=kind,
                vendor_hint=vendor,
                confidence=0.98,
                from_structured_field=True,
            )
        )

    # Masked AFTER the structured field and BEFORE every title pass, including
    # the OEM vendor gates: "compatible with Dell PowerEdge" must not open the
    # Dell/EMC gate for a bare number elsewhere in the title.
    title = mask_reference_spans(title)
    for vendor, pattern in _MFR_SHAPES:
        for m in pattern.finditer(title):
            add(
                MpnCandidate(
                    raw=m.group(0),
                    normalized=normalize_alias_text(m.group(0)),
                    kind=TokenKind.MANUFACTURER_MPN,
                    vendor_hint=vendor,
                    confidence=0.9,
                )
            )

    for vendor, pattern, gate in _OEM_RULES:
        if gate is not None and not gate.search(title):
            continue
        for m in pattern.finditer(title):
            add(
                MpnCandidate(
                    raw=m.group(1),
                    normalized=normalize_alias_text(m.group(1)),
                    kind=TokenKind.OEM_PN,
                    vendor_hint=vendor,
                    confidence=0.8,
                )
            )

    for prefix in HOUSE_SKU_PREFIXES.get(source_key, ()):
        for m in _CODE_SHAPE.finditer(title):
            if m.group(0).startswith(prefix):
                add(
                    MpnCandidate(
                        raw=m.group(0),
                        normalized=normalize_alias_text(m.group(0)),
                        kind=TokenKind.HOUSE_SKU,
                        vendor_hint="",
                        confidence=0.7,
                    )
                )

    for m in _CODE_SHAPE.finditer(title):
        token = m.group(0)
        normalized = normalize_alias_text(token)
        if normalized in out or not normalized:
            continue
        if not (_TWO_ALPHA.search(token) and _TWO_DIGIT.search(token)):
            continue
        if _VOCAB_TAILS.search(token):
            continue
        add(
            MpnCandidate(
                raw=token,
                normalized=normalized,
                kind=TokenKind.UNKNOWN_CODE,
                vendor_hint="",
                confidence=0.3,
            )
        )

    return sorted(out.values(), key=lambda c: -c.confidence)
