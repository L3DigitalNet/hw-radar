"""Category rules registry (MS2-D-02): maps a category slug to the pure matching
functions the resolver runs for that category. Pure — no ORM, no Django, no
acquisition imports — so `acquisition.contracts` can import the slug pattern
without an import cycle.

Contract:
- `dispatch_category(hint)` names the category a listing resolves under. A
  `None` hint is the LEGACY DEFAULT, drive: every MS-1 source is drive-only by
  construction and never sets a hint (MS2-D-03). The trap is the converse — a
  multi-category source that forgets to hint an item silently resolves it as a
  drive, so every such source must hint every item. A malformed hint raises
  rather than falling back to drive.
- `rules_for(slug)` returns `None` for a slug with no registered rules; the
  resolver turns that into an `unsupported_category` none-edge and never runs
  drive rules on it.
- Slice A registers `drive` only, bound to the existing ADR-0019 objects BY
  IDENTITY (`vocab.extract`, `mpn.extract_candidates`, `grammars.decode`,
  `ladder.contradictions`): dispatch is a seam, not a second drive matcher.

The ORM-bound half of a category — reading catalog specs into `ladder.HardAttrs`
— lives in `resolver._SPEC_READERS`, whose keys must equal
`registered_categories()` (pinned by a DB test). Register both ends together.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

from hw_radar.matching import grammars, ladder, mpn, vocab
from hw_radar.matching.types import DecodeResult, ExtractedAttributes, MpnCandidate

DRIVE: Final = "drive"
LEGACY_DEFAULT_CATEGORY: Final = DRIVE
# The `Category.slug` shape (SlugField, max_length=50) narrowed to lowercase
# hyphen-separated words. Shared with the ParsedListing.category_hint validator.
CATEGORY_SLUG_RE: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CATEGORY_SLUG_MAX_LENGTH: Final = 50


class CandidateExtractor(Protocol):
    def __call__(
        self, title: str, *, structured_mpn: str | None = None, source_key: str = ""
    ) -> list[MpnCandidate]: ...


@dataclass(frozen=True)
class CategoryRules:
    slug: str
    extract: Callable[[str], ExtractedAttributes]
    extract_candidates: CandidateExtractor
    decode: Callable[[str], DecodeResult | None]
    veto: ladder.Veto


def _drive_rules() -> CategoryRules:
    return CategoryRules(
        slug=DRIVE,
        extract=vocab.extract,
        extract_candidates=mpn.extract_candidates,
        decode=grammars.decode,
        veto=ladder.contradictions,
    )


# Factories, not prebuilt CategoryRules: the module attributes are read on every
# rules_for call, so a patched `vocab.extract` (the resolver's crash-path tests
# patch it) reaches the resolver exactly as the direct call it replaced did. A
# prebuilt instance would freeze the import-time function objects and silently
# route around any such patch.
_REGISTRY: Final[dict[str, Callable[[], CategoryRules]]] = {DRIVE: _drive_rules}


def rules_for(slug: str) -> CategoryRules | None:
    factory = _REGISTRY.get(slug)
    return factory() if factory is not None else None


def registered_categories() -> frozenset[str]:
    return frozenset(_REGISTRY)


def dispatch_category(hint: str | None) -> str:
    """Return the category slug a listing resolves under.

    `None` means no hint and dispatches to the legacy drive default. Raises
    ValueError for a hint that is not a valid category slug — a garbled hint is
    an upstream defect, and defaulting it to drive would hide it.
    """
    if hint is None:
        return LEGACY_DEFAULT_CATEGORY
    if len(hint) > CATEGORY_SLUG_MAX_LENGTH or not CATEGORY_SLUG_RE.fullmatch(hint):
        raise ValueError(f"invalid category slug: {hint!r}")
    return hint
