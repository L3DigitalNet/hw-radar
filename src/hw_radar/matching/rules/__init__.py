"""Per-category matching rules for the non-drive categories (MS2-D-05, plan B3).

Each module (`gpu`, `ram`, `cpu`, `basic`) supplies the pure half of a
`categories.CategoryRules`: `extract`, `extract_candidates`, and `veto`. Drive
keeps its ADR-0019 functions (`vocab`, `mpn`, `grammars`, `ladder.contradictions`)
untouched; nothing here is imported by the drive path.

Shared contract for every module:
- Input is `normalize.canonicalize_title()` output, as for drive.
- Extraction is deterministic and category-local: brand tables, part-number
  shapes, and attribute patterns live in the category's own module. There is no
  fuzzy or cross-category fallback. A value the title states ambiguously (two
  distinct VRAM sizes, two sockets) is UNKNOWN (None), never the first match,
  because a guessed value could veto a correct hit or, worse, satisfy a watch.
- Candidates only feed the rung-1 exact-alias lookup. Every accept still needs
  an exact `product_alias` hit that passes the resolver's acceptance policy
  (MS2-D-21); attribute evidence alone never produces a target.
- Condition, packaging, and warranty come from `vocab.offer_terms`, so all
  categories share the drive vocabulary for offer terms.
- No grammar decode: the new categories use rungs 0 and 1 only, so `decode`
  always returns None.

Pure: no Django and no ORM, like the rest of the matching library.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

from hw_radar.matching import mpn
from hw_radar.matching.normalize import normalize_alias_text
from hw_radar.matching.types import Attribute, DecodeResult, MpnCandidate, TokenKind

LAYER = "category_rules"

# Copied from the drive N3 fallback (mpn._CODE_SHAPE) rather than imported, so a
# drive tuning change cannot silently move non-drive candidates. Together with the
# two-letter/two-digit test below, plain words and bare numbers never qualify.
_CODE_SHAPE = re.compile(r"\b[a-z0-9][a-z0-9./-]{5,23}\b")
_TWO_ALPHA = re.compile(r"[a-z].*[a-z]")
_TWO_DIGIT = re.compile(r"\d.*\d")


def no_decode(_token: str) -> DecodeResult | None:
    """The `decode` of every non-drive category: no MPN grammar, so no rung 2."""
    return None


class CandidateSet:
    """Accumulates candidates keyed by the alias join key, keeping the most
    confident candidate per key (the same dedupe rule as mpn.extract_candidates)."""

    def __init__(self) -> None:
        self._out: dict[str, MpnCandidate] = {}

    def add(
        self,
        raw: str,
        kind: TokenKind,
        *,
        vendor: str = "",
        confidence: float,
        structured: bool = False,
    ) -> None:
        normalized = normalize_alias_text(raw)
        if not normalized:
            return
        existing = self._out.get(normalized)
        if existing is None or confidence > existing.confidence:
            self._out[normalized] = MpnCandidate(
                raw=raw,
                normalized=normalized,
                kind=kind,
                vendor_hint=vendor,
                confidence=confidence,
                from_structured_field=structured,
            )
        elif (
            existing.from_structured_field
            and not structured
            and existing.title_kind is not TokenKind.MANUFACTURER_MPN
        ):
            # Same title-kind rule as mpn.extract_candidates.
            self._out[normalized] = replace(existing, title_kind=kind)

    def __contains__(self, normalized: str) -> bool:
        return normalized in self._out

    def result(self) -> list[MpnCandidate]:
        return sorted(self._out.values(), key=lambda c: -c.confidence)


def add_structured(
    out: CandidateSet, structured_mpn: str | None, kind: TokenKind, vendor: str
) -> None:
    """A merchant-asserted structured MPN (JSON-LD `mpn`): outranks title tokens
    and counts as brand evidence in the ladder, exactly as on the drive path."""
    if structured_mpn and structured_mpn.strip():
        out.add(structured_mpn, kind, vendor=vendor, confidence=0.98, structured=True)


def add_house_skus(out: CandidateSet, title: str, source_key: str) -> None:
    # The per-source house-SKU registry is source-scoped, not category-scoped, so
    # it is shared with the drive extractor rather than copied per category.
    for prefix in mpn.HOUSE_SKU_PREFIXES.get(source_key, ()):
        for m in _CODE_SHAPE.finditer(title):
            if m.group(0).startswith(prefix):
                out.add(m.group(0), TokenKind.HOUSE_SKU, confidence=0.7)


def add_code_tokens(out: CandidateSet, title: str, vocab_tails: re.Pattern[str]) -> None:
    """Low-confidence UNKNOWN_CODE candidates for code-shaped tokens no category
    pattern claimed. They can hit an exact alias, but the ladder still demands
    brand evidence and the resolver still applies the acceptance policy."""
    for m in _CODE_SHAPE.finditer(title):
        token = m.group(0)
        if normalize_alias_text(token) in out:
            continue
        if not (_TWO_ALPHA.search(token) and _TWO_DIGIT.search(token)):
            continue
        if vocab_tails.search(token):
            continue
        out.add(token, TokenKind.UNKNOWN_CODE, confidence=0.3)


def sole_value[T](matches: Iterable[tuple[T, str]], confidence: float) -> Attribute[T] | None:
    """The attribute when every match agrees; None when there are none or they
    disagree. Disagreement is UNKNOWN by design: '24GB 48GB' in one title is a
    comparison or a bundle, and either guess could feed a wrong veto."""
    found = list(matches)
    if not found or len({value for value, _ in found}) != 1:
        return None
    value, source = found[0]
    return Attribute(value=value, confidence=confidence, layer=LAYER, source_text=source)


def sole_pattern(
    title: str, table: tuple[tuple[re.Pattern[str], str], ...], confidence: float
) -> Attribute[str] | None:
    """`sole_value` over a (pattern → canonical value) table: every table entry
    that matches contributes its value."""
    return sole_value(
        ((value, m.group(0)) for pattern, value in table for m in pattern.finditer(title)),
        confidence,
    )
