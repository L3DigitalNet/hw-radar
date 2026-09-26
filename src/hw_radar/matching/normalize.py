"""N1 text canonicalization + the single-normalizer alias key (ADR-0019 rule 1).

- canonicalize_title() is the N1 pass every extraction layer reads from;
  canonicalize_listing_text() is the title + seller-condition-label form the
  resolver and the eligibility evaluator read.
- mask_reference_spans() blanks comparison/reference clauses out of a canonical
  title so the drive identity layers (mpn.extract_candidates; the vocab brand
  and offer-term tables) never read a cited product as the listed one. It is
  span detection, not a normalizer. See its docstring.
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


# Reference phrases whose OBJECT names a product other than the one listed.
# Deliberately closed and unambiguous: every entry is pinned by
# tests/unit/test_reference_context.py. Rejected as too ambiguous: bare
# 'replacement' ("EMC 005049070 replacement drive" is the drive itself), bare
# 'compatible' / 'for' / 'fits' / 'works with' (routinely describe the listed
# drive's own use: "NAS drive for Synology"). The drive layers mask bare 'for'
# only as the title's first token (_CATEGORY_LEADING_REFERENCE_WORDS).
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


# Category-local reference phrases: masked only by the category that passes
# them to reference_phrase_pattern(), but registered HERE because
# canonicalize_title must know every phrase any category masks. Its
# punctuation-preserving branch triggers on this full set; a phrase missing from
# it gets its clause punctuation erased, and the category's span then runs on
# through asserted evidence ("oem version of 7b13; qs ..." lost the QS sample
# marker — review finding N2). A static tuple, not registration at call time:
# canonical text must not depend on which rule modules happen to be imported.
#   "oem version of" — rules/cpu.py (a different CPU SKU, e.g. 7J13 vs 7763).
#   "fit for", "suitable for" — drive (DRIVE_REFERENCE_PHRASE): eBay resellers
#       title compatible or look-alike stock "FIT FOR Seagate Exos ..." (MS-1e
#       ebay-0263, ebay-0463). Drive-local, not shared: the CPU and other
#       categories' masking (and so their pinned decisions) stay unchanged.
_CATEGORY_REFERENCE_PHRASES: tuple[str, ...] = ("oem version of", "fit for", "suitable for")

# Words that open a reference span only as the FIRST token of the canonical
# title, registered like _CATEGORY_REFERENCE_PHRASES and for the same reason.
#   "for" — drive: "FOR Seagate Exos X14 ... ST12000NM0008 NEW" sells a
#       compatible part, not the cited drive (MS-1e ebay-0190/0240/0387).
#       Mid-title "for" stays unmasked: "ST4000NM000A 4TB for Dell server" is
#       the drive itself. "for parts" is condition vocabulary (vocab
#       _CONDITIONS), never a reference, so it is excluded. So is "for sale"
#       ("For sale: Seagate ST12000NE0008"): a sales preamble whose object is
#       the listed item itself, and masking it erased the whole identity.
_CATEGORY_LEADING_REFERENCE_WORDS: tuple[str, ...] = ("for",)
_LEADING_EXCLUSIONS = r"(?!\s+(?:parts|sale)\b)"

# Non-ASCII reference phrases, folded to their registered ASCII form BEFORE
# noise stripping, which would otherwise erase them and hand the cited
# product's MPN to identity extraction ("全新 适用于 Seagate ST2000NX0253",
# MS-1e ebay-0294). 适用于 = "suitable for".
_PHRASE_FOLDS: tuple[tuple[str, str], ...] = (("适用于", " suitable for "),)


def _phrase_pattern(phrases: tuple[str, ...], leading: tuple[str, ...] = ()) -> re.Pattern[str]:
    alternation = "|".join(re.escape(p) for p in phrases)
    pattern = rf"\b(?:{alternation})\b"
    if leading:
        # `^` without MULTILINE matches only at index 0 even when
        # mask_reference_spans resumes the search at a later `pos`, so a
        # leading word can open at most the first span.
        words = "|".join(re.escape(w) for w in leading)
        pattern = rf"^(?:{words})\b{_LEADING_EXCLUSIONS}|{pattern}"
    return re.compile(pattern)


def reference_phrase_pattern(*extra: str, leading: tuple[str, ...] = ()) -> re.Pattern[str]:
    """The shared reference-phrase pattern, widened by category-local phrases.

    For a category whose titles cite other products in a phrase the shared
    list must not carry. Build it once at import and pass it to
    mask_reference_spans(phrases=...); with no extras it is the shared pattern.
    `leading` words open a span only as the title's first token. Raises
    ValueError for an extra or leading word not registered in
    _CATEGORY_REFERENCE_PHRASES / _CATEGORY_LEADING_REFERENCE_WORDS, since
    canonicalize_title would not preserve its clause boundaries.
    """
    unregistered = [p for p in extra if p not in _CATEGORY_REFERENCE_PHRASES]
    unregistered += [w for w in leading if w not in _CATEGORY_LEADING_REFERENCE_WORDS]
    if unregistered:
        raise ValueError(
            f"reference phrases {unregistered!r} must be added to "
            "normalize._CATEGORY_REFERENCE_PHRASES or _CATEGORY_LEADING_REFERENCE_WORDS"
        )
    return _phrase_pattern((*_REFERENCE_PHRASES, *extra), leading)


_REFERENCE_PHRASE = reference_phrase_pattern()
# The drive identity layers' set (mpn.extract_candidates, vocab.extract): the
# shared phrases plus the drive-local ones registered above.
DRIVE_REFERENCE_PHRASE = reference_phrase_pattern("fit for", "suitable for", leading=("for",))
# The canonicalization trigger: every phrase ANY category masks.
_ANY_REFERENCE_PHRASE = _phrase_pattern(
    (*_REFERENCE_PHRASES, *_CATEGORY_REFERENCE_PHRASES), _CATEGORY_LEADING_REFERENCE_WORDS
)
# Raw clause punctuation that _NOISE would erase. A comma between two digits is
# a thousands separator ("1,000GB"), not a clause break.
_CLAUSE_PUNCT = re.compile(r"[;|]|(?<!\d),|,(?!\d)")
# The canonical clause boundary: the one separator _NOISE lets through.
_CANONICAL_BOUNDARY = " - "


def _strip_noise(text: str) -> str:
    return _WS.sub(" ", _NOISE.sub(" ", text)).strip()


def canonicalize_title(text: str) -> str:
    """Return the N1 canonical form every extraction layer reads.

    NFKC, dash folding, casefold, non-ASCII reference-phrase folding
    (_PHRASE_FOLDS), boilerplate removal, then every character
    outside the MPN/capacity alphabet becomes a space. One exception keeps
    reference masking honest: when the result contains a reference phrase
    (shared or category-local, or a registered leading word as the first
    token), raw clause punctuation (",", ";", "|") is
    rewritten to " - " instead of being erased, so mask_reference_spans can
    still see where the cited clause ends. A title with no reference phrase is canonicalized exactly as
    if the exception did not exist."""

    folded = unicodedata.normalize("NFKC", text).translate(_DASHES).casefold()
    for phrase, ascii_form in _PHRASE_FOLDS:
        folded = folded.replace(phrase, ascii_form)
    # Boilerplate BEFORE noise-stripping: patterns like 'l@@k' contain characters
    # the noise pass removes — the other order makes them unreachable.
    cleaned = _BOILERPLATE.sub(" ", folded)
    plain = _strip_noise(cleaned)
    # Conditional on purpose. Keeping clause punctuation in EVERY title would
    # change the canonical text of every comma-bearing listing — the persisted
    # title_normalized, provisional family keys built by refdata.persist, and
    # patterns that read across the old space ("2, pack" is "2 pack" today) —
    # to serve a masking step that only runs when a phrase is present.
    # Detection reads `plain` so a phrase that only appears once noise is
    # stripped ("compatible*with") still gets its boundaries; the output is
    # idempotent because a second pass sees the same phrase and no raw
    # punctuation left to rewrite.
    if _ANY_REFERENCE_PHRASE.search(plain) is None:
        return plain
    return _strip_noise(_CLAUSE_PUNCT.sub(_CANONICAL_BOUNDARY, cleaned))


def canonicalize_listing_text(title: str, condition_label: str = "") -> str:
    """Return the canonical text of a listing title plus its seller condition
    label — the one string the resolver and the eligibility evaluator read.

    The label is joined behind a clause boundary so a reference span running
    to the end of the title stops before it: the seller's structured condition
    is asserted evidence about this item, never part of a cited product. When
    such a title leaves a "(" unclosed, the missing ")" is appended to the
    title before the boundary, so the label is never inside the group. For a
    title with no reference phrase the result equals the historical
    canonicalize_title(f"{title} {condition_label}".strip())."""

    if title.strip() and condition_label.strip():
        # mask_reference_spans runs an unclosed "(" opened inside a span to the
        # end of the text, so without this close the label would be masked
        # with the cited product. Closing here, not in _span_end, is what lets
        # the span ignore every internal boundary: _span_end cannot tell an
        # internal comma from this label boundary, since both canonicalize to
        # " - " (round-3 F1/F4 residual: "comparable to (Seagate,
        # ST12000NE0008" leaked the MPN through the comma). Gated on a phrase
        # so a phrase-free title keeps its historical canonical text.
        canonical_title = canonicalize_title(title)
        if _ANY_REFERENCE_PHRASE.search(canonical_title) is not None:
            title += ")" * _open_paren_depth(canonical_title)
    return canonicalize_title(" | ".join(p for p in (title, condition_label) if p.strip()))


def normalize_alias_text(text: str) -> str:
    """Alias join key: NFKC → casefold → strip every non-alphanumeric.

    'MZ-77E1T0B/AM', 'mz 77e1t0b/am', and 'MZ_77E1T0B.AM' all become
    'mz77e1t0bam' — separator styling never splits an alias join."""

    return _ALNUM_ONLY.sub("", unicodedata.normalize("NFKC", text).casefold())


# Clause boundaries that end a reference span outside parentheses. Canonical
# titles carry only " - " (canonicalize_title rewrites ",", ";" and "|" to it
# when a phrase is present); the raw characters are accepted too so the rule
# holds on text that skipped canonicalization.
_SPAN_BOUNDARY = re.compile(r" - |[,;|]")


def _open_paren_depth(text: str) -> int:
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
    return depth


def _span_end(title: str, start: int, *, inside_parens: bool) -> int:
    """Return the exclusive end of a reference span whose phrase ends at `start`."""

    # Parentheses opened inside the span are part of the comparison object and
    # never end it: "comparable to (Seagate ST12000NE0008)" masks the whole
    # group (review finding F1), and "comparable to (Seagate) ST12000NE0008"
    # masks on past the ")" to the clause boundary, because the group may only
    # qualify the object. Stopping at that ")" leaked the cited exact-alias MPN
    # (F1 residual) and let "(factory) recertified drives" become the listing's
    # condition (F4 residual). Over-masking only costs identity evidence
    # (unresolved), whereas under-masking is a false merge.
    depth = 0
    for i in range(start, len(title)):
        ch = title[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth:
                depth -= 1
            elif inside_parens:
                # "(compatible with Dell R740)": the enclosing group is the clause.
                return i
        elif depth == 0 and _SPAN_BOUNDARY.match(title, i):
            return i
    # Reaching here with depth > 0 means a "(" the span opened was never
    # closed, so every later boundary sits inside the comparison object and
    # the span runs to the end. Ending at the first boundary after the "("
    # released "(Seagate, ST12000NE0008" and "(used, factory recertified
    # drives" as listing evidence (round-3 F1/F4 residual). The seller's
    # condition label is protected upstream: canonicalize_listing_text closes
    # the group before appending it.
    return len(title)


def mask_reference_spans(title: str, phrases: re.Pattern[str] = _REFERENCE_PHRASE) -> str:
    """Blank every reference span of a canonical title with spaces.

    A span starts at a reference phrase ("comparable to", "compatible with",
    "replacement for", ...) and ends, exclusive, at the first of:

    - a clause boundary (" - ", or a raw ",", ";", "|") outside any parentheses
      the span itself opened;
    - the ")" closing the group the phrase sits in, when the phrase is inside
      parentheses ("(compatible with Dell R740)");
    - the end of the title.

    Parentheses the span itself opens never end it: a group right after the
    phrase ("comparable to (Seagate) ST12000NE0008") is part of the comparison
    object, and the span runs past its ")" to the clause boundary. If such a
    group is never closed, the span runs to the end of the text: every later
    boundary is inside the group. canonicalize_listing_text closes the group
    before the seller's condition label, so the label stays outside.

    Text outside spans is returned unchanged and the length is preserved, so
    offsets and word boundaries elsewhere are stable. A title with no phrase
    is returned as is. `phrases` defaults to the shared list every drive layer
    uses; a category passes reference_phrase_pattern(...) to add its own.

    Contract with the callers: every field that establishes product or variant
    identity reads the masked text — MPN candidates and OEM vendor gates
    (mpn.extract_candidates), brand and the offer terms that
    resolver._materialize turns into a variant (vocab). Hard-attribute veto
    fields read the unmasked title; see vocab.extract for why.

    This is span DETECTION over canonical text, not a second normalizer: it
    produces no join key and never feeds normalize_alias_text's alias
    comparison (ADR-0019 rule 1). It depends on canonicalize_title keeping
    clause punctuation as " - " for phrase-bearing titles — without that,
    "Compatible with ST16000NM002G, Seagate ST18000NM000J" would mask the
    listed MPN too."""

    pieces: list[str] = []
    pos = 0
    while (phrase := phrases.search(title, pos)) is not None:
        inside = _open_paren_depth(title[: phrase.start()]) > 0
        end = _span_end(title, phrase.end(), inside_parens=inside)
        pieces.append(title[pos : phrase.start()])
        pieces.append(" " * (end - phrase.start()))
        pos = end
    pieces.append(title[pos:])
    return "".join(pieces)
