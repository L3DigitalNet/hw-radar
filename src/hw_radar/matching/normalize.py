"""N1 text canonicalization + the single-normalizer alias key (ADR-0019 rule 1).

- canonicalize_title() is the N1 pass every extraction layer reads from.
- mask_reference_spans() blanks comparison/reference clauses out of a canonical
  title so the drive identity layers (mpn.extract_candidates, the vocab brand
  table) never read a cited product as the listed one. See its docstring.
- normalize_alias_text() is the JOIN KEY for product_alias. Catalog ingest
  (MS-1c refdata) and listing-side candidates MUST both call it; the CI parity
  test in tests/db/test_resolver.py asserts that. Never fork a second
  normalizer — two drifting normalizers are the classic silent killer of
  alias joins (ADR-0019).
canonicalize_title() and normalize_alias_text() are idempotent (property-tested
in tests/unit/test_normalize.py)."""

from __future__ import annotations

import re
import unicodedata

# Unicode dash variants (HYPHEN, DASH, FIGURE DASH, EN DASH, EM DASH, HORIZONTAL BAR,
# MINUS SIGN) → ASCII hyphen so MPNs like "MZ‑77E1T0B/AM" keep their separator through  # noqa: RUF003
# canonicalization (NFKC alone leaves U+2011 untouched).
_DASHES: dict[int, str] = dict.fromkeys(
    (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212), "-"
)
# Keep the characters MPNs, capacities, and form factors use; drop emoji/decorations.
_NOISE = re.compile(r"[^a-z0-9 .\-/()\"+%#]")
_WS = re.compile(r"\s+")
_ALNUM_ONLY = re.compile(r"[^a-z0-9]")

# Marketplace decoration with ZERO attribute signal. Deliberately tiny: anything
# that could carry condition/warranty/lot meaning ("brand new", "no warranty",
# "for parts") stays in the text for the N2 vocab layer.
_BOILERPLATE = re.compile(
    r"\b(?:l@@k|wow|free\s+(?:fast\s+)?shipping|fast\s+ship(?:ping)?|"
    r"ships?\s+(?:fast|free|today|same\s+day)|best\s+offer|top\s+seller|"
    r"us\s+seller|hot\s+deal)\b"
)


def canonicalize_title(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_DASHES).casefold()
    # Boilerplate BEFORE noise-stripping: patterns like 'l@@k' contain characters
    # the noise pass removes — the other order makes them unreachable.
    cleaned = _BOILERPLATE.sub(" ", folded)
    cleaned = _NOISE.sub(" ", cleaned)
    return _WS.sub(" ", cleaned).strip()


def normalize_alias_text(text: str) -> str:
    """Alias join key: NFKC → casefold → strip every non-alphanumeric.

    'MZ-77E1T0B/AM', 'mz 77e1t0b/am', and 'MZ_77E1T0B.AM' all become
    'mz77e1t0bam' — separator styling never splits an alias join."""

    return _ALNUM_ONLY.sub("", unicodedata.normalize("NFKC", text).casefold())


# Reference phrases whose OBJECT names a product other than the one listed.
# Deliberately closed and unambiguous: every entry is pinned by
# tests/unit/test_reference_context.py. Rejected as too ambiguous: bare
# 'replacement' ("EMC 005049070 replacement drive" is the drive itself), bare
# 'compatible' / 'for' / 'fits' / 'works with' (routinely describe the listed
# drive's own use: "NAS drive for Synology").
_REFERENCE_PHRASES: tuple[str, ...] = (
    "comparable to",
    "compatible with",
    "replacement for",
    "equivalent to",
    "equiv to",
    "alternative to",
    "substitute for",
    "replaces",
)


def reference_phrase_pattern(*extra: str) -> re.Pattern[str]:
    """The shared reference-phrase pattern, widened by category-local phrases.

    For a category whose titles cite other products in a phrase the shared
    (drive) list must not carry. Build it once at import and pass it to
    mask_reference_spans(phrases=...); with no extras it is the shared pattern.
    """
    alternation = "|".join(re.escape(p) for p in (*_REFERENCE_PHRASES, *extra))
    return re.compile(rf"\b(?:{alternation})\b")


_REFERENCE_PHRASE = reference_phrase_pattern()
# Clause boundaries that end a reference span. Only " - ", "(" and ")" survive
# canonicalize_title; ",", "|" and ";" are listed so the rule still holds on a
# non-canonical caller, but _NOISE turns them into spaces first (see the trap
# in mask_reference_spans).
_CLAUSE_BOUNDARY = re.compile(r" - |[()|,;]")


def mask_reference_spans(title: str, phrases: re.Pattern[str] = _REFERENCE_PHRASE) -> str:
    """Blank every reference span of a canonical title with spaces.

    A span runs from a reference phrase ("comparable to", "compatible with",
    "replacement for", ...) up to, not including, the next clause boundary
    (" - ", "(", ")") or the end of the title. Text outside spans is
    returned unchanged and the length is preserved, so offsets and word
    boundaries elsewhere are stable. A title with no phrase is returned as is.
    `phrases` defaults to the shared list every drive layer uses; a category
    passes reference_phrase_pattern(...) to add its own.

    Identity-only contract: callers mask before mining IDENTITY evidence (MPN
    candidates, OEM vendor gates, brand words) and never before reading
    physical attributes, so a span that over-reaches costs identity alone.

    Known trap: canonicalization erases ",", "|" and ";", so in the resolver a
    span runs past what were clause breaks in the raw title — and past the
    condition label the resolver appends — to the next surviving boundary or
    the end. "Compatible with ST16000NM002G, Seagate ST18000NM000J" therefore
    masks BOTH MPNs. That over-reach is the intended failure direction: an
    unresolved listing queues, while a false merge poisons a model's price
    history (ADR-0019)."""

    pieces: list[str] = []
    pos = 0
    while (phrase := phrases.search(title, pos)) is not None:
        boundary = _CLAUSE_BOUNDARY.search(title, phrase.end())
        end = boundary.start() if boundary is not None else len(title)
        pieces.append(title[pos : phrase.start()])
        pieces.append(" " * (end - phrase.start()))
        pos = end
    pieces.append(title[pos:])
    return "".join(pieces)
