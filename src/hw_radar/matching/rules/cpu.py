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

Veto fields: socket, cores and the explicitly named model (`model`, see
veto), plus three listing-only identity markers that veto whatever the
target — `sample`, `bundle` and `multi_model` (see below).
tdp_w is extracted but never vetoes, because configurable TDP makes a
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
  - A first-party AMD codename, socket or Zen generation between 'EPYC' and
    the number ('EPYC Genoa 9354', 'EPYC Milan-X 7773X', 'EPYC GENOA SP5 ZEN4
    9354') is skipped when forming the name candidate, which is emitted as
    'epyc <number>'. The number keeps its suffix, so the P-variant and
    multi-model guards see exactly what they would without it.

Candidate filtering alone is not enough for multi-model titles: rung 0 never
looks at candidates, so a listing accepted under one title and re-observed
naming two models would inherit its prior. The ambiguity is therefore also
extracted as `multi_model`, which vetoes, and the veto re-runs at rung 0.
The marker covers every product line, not just EPYC: two distinct processor
names ('Core i7-12700K / Core i9-12900K'), and the Xeon shorthand that names a
second model without repeating 'xeon' ('Xeon Gold 6338 / Platinum 8358',
'Gold 6338/6348', 'E5-2680 v4 / E5-2690 v4'). Without it the second model
emits no candidate at all, so the first model's exact alias is the only hit
and nothing looks ambiguous (round-4 R4-D).

A listing that is a board, system or bundle carrying the CPU ('Supermicro
H12DSi-N6 Motherboard With 2x AMD EPYC 7763') names the CPU exactly, so it
reaches the alias. Its price is not a CPU price, so the product-type marker is
extracted as `bundle` and vetoes (see _BUNDLE for what does and does not count).

Vendor (PSB) lock is extracted as `vendor_lock` but is deliberately NOT an
identity field: it is a property of the particular unit and its sales channel
(the same EPYC 7742 ships locked to Dell or unlocked), not of the CPU model, so
it is listing evidence rather than a `cpu_spec` column, `veto` never reads it,
and a 'Dell Locked EPYC 7742' resolves to EPYC 7742 like any other. Whether a
watch accepts a locked unit is eligibility's decision (`cpu.vendor_lock` in
eligibility.evaluate).

Price is not identity either (owner ruling on cpu-0283: a $399 'EPYC 7763 ...
100-000000312' is EPYC 7763). Nothing in this module, nor the ladder that
calls it, receives the listing price, and that must stay so: a price anomaly
is a question for eligibility, seller trust, deal evaluation and review, and
letting it veto or demote identity would hide exactly the listings those
layers exist to judge.

Engineering and qualification samples ('ES', 'QS', 'engineering sample',
'pre-production', a suffixed OPN) are different parts from the retail SKU:
their own OPNs, stepping, clocks and often locked or unfinished firmware. A
sample title still names the retail model ('EPYC 7763 QS'), so candidate
filtering alone would leave that name as an exact alias hit. The marker is
instead extracted as the `sample` attribute and always vetoes, which routes
any alias hit to review (a contradiction) rather than an auto-accept. A sample
marking in the merchant's structured MPN field counts too (`with_structured_mpn`,
which the resolver applies through CategoryRules.fold_structured): a retail
title over a '100-000000314-04' structured MPN is still a sample.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Final

from hw_radar.matching import vocab
from hw_radar.matching.ladder import CategoryHardAttrs, HardAttrs
from hw_radar.matching.normalize import (
    canonicalize_title,
    mask_reference_spans,
    reference_phrase_pattern,
)
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
    # The product-type marker when the listing is a board, system or bundle
    # rather than a bare CPU; None = nothing says so.
    bundle: Attribute[str] | None = None
    # The distinct model numbers (EPYC numbers and _line_models keys),
    # space-joined, when the title names more than one; None = at most one.
    multi_model: Attribute[str] | None = None
    # VENDOR_LOCKED / VENDOR_UNLOCKED when the title states the unit's vendor
    # (PSB) lock explicitly; None = unknown. Listing evidence for the watch
    # clause `cpu.vendor_lock` only: never compared to the catalog and never
    # read by `veto`, so it cannot change identity (see _vendor_lock).
    vendor_lock: Attribute[str] | None = None
    # The one model the title names explicitly, as a model_key; None = no
    # model named, or several (then multi_model carries the ambiguity).
    model: Attribute[str] | None = None


@dataclass(frozen=True)
class CpuHard(CategoryHardAttrs):
    """`cpu_spec` values for the veto; None = the catalog does not know. The
    socket is already in socket_key form."""

    socket: str | None = None
    cores: int | None = None
    # The target's model_key; set only for a model-grain target (see
    # resolver._cpu_model_hard). A family has no single model to contradict.
    model: str | None = None


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

# (vendor, pattern). Group 0 is the full product-line phrase; the `num` group
# is the bare model number emitted alongside it. Every pattern must end on the
# COMPLETE model token: without a terminating boundary a near-model string's
# prefix is a seeded model ('Xeon Gold 63380' emitted 'xeon gold 6338' and
# '6338', both authoritative aliases; round-3 R3-D). The Scalable pattern
# needs (?![a-z0-9]) rather than \b because its '+' suffix ('8480+') is a
# non-word character, after which \b would fail before a space. The bare
# number must also start its own token, hence the Ryzen tier digit needs
# whitespace after it: an optional '[3579]?' let 'Ryzen 79500' read as tier 7
# plus model '9500'.
#
# Between 'EPYC' and the number a title may carry up to three AMD qualifier
# words: the first-party codename, the socket ('sp5') and the core
# microarchitecture ('zen4', 'zen 4'), in any order ('EPYC GENOA SP5 ZEN4 9354',
# cpu-0082). Only that closed vocabulary is skipped, so a free word between
# them ('EPYC server 9354') still breaks the phrase, and the number must still
# follow the last qualifier directly with its suffix intact (9354P stays 9354P).
_EPYC_NAME_PHRASE = re.compile(
    r"\bepyc\s+(?P<qualifiers>(?:(?:naples|rome|milan(?:-x)?|genoa(?:-x)?|bergamo|siena"
    r"|turin|sp[3-6]|zen\s?[1-6]c?)\s+){1,3})?(?P<num>\d{4}[a-z]{0,2})\b"
)
_NAMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "intel",
        re.compile(
            r"\bxeon\s+(?:platinum|gold|silver|bronze)\s+(?P<num>\d{4}[a-z]{0,2}\+?)(?![a-z0-9])"
        ),
    ),
    ("intel", re.compile(r"\bxeon\s+(?P<num>(?:e[357]|w|d)-?\s?\d{4,5}[a-z]{0,2}(?:\s+v\d)?)\b")),
    ("intel", re.compile(r"\bcore\s+(?P<num>i[3579]-?\s?\d{4,5}[a-z]{0,3})\b")),
    ("intel", re.compile(r"\bcore\s+ultra\s+[3579]\s+(?P<num>\d{3}[a-z]{0,2})\b")),
    ("amd", _EPYC_NAME_PHRASE),
    (
        "amd",
        re.compile(
            r"\bryzen\s+(?:threadripper\s+)?(?:pro\s+)?(?:[3579]\s+)?(?P<num>\d{4}[a-z0-9]{0,3})\b"
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
    # The second branch admits an OPN fused with a following condition word
    # ('100-000000798Open', cpu-0082: the seller's text ran into the part
    # number). Only that closed word set, never an arbitrary alphanumeric
    # continuation: AMD's own boxed part numbers extend the digits with letters
    # ('100-100000312WOF'), and a longer digit run is another code entirely, so
    # a general suffix strip would collapse distinct parts onto one alias.
    ("amd", re.compile(r"\b100-\d{9}(?:\b(?!-\w)|(?=(?:open(?:box)?|new|used)\b(?!-\w)))")),
)
_SSPEC = re.compile(r"\bsr[a-z0-9]{3}\b")
_VOCAB_TAILS = re.compile(r"(?:\d(?:ghz|mhz|mb|gb|w)|lga\d+|\dc/\d+t|\d-?cores?)$")

# CPU-local reference phrase on top of the shared list. For a CPU, "7J13 OEM
# version of 7763" names a different SKU (its own OPN, clocks and firmware).
# Kept out of the shared list: drive masking is pinned by the A0 decision
# oracle and tests/unit/test_reference_context.py, and no drive evidence says
# the phrase names a different drive there (an OEM-relabelled drive can be
# physically the cited model). Kept local, drive MASKING is unchanged. Drive
# canonical text is not fully untouched: the phrase must be registered in
# normalize._CATEGORY_REFERENCE_PHRASES (reference_phrase_pattern rejects an
# unregistered extra), and canonicalize_title keeps clause punctuation as " - "
# for every registered phrase, drive titles included.
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


# Product-type markers that make the LISTING a board, system or bundle, matched
# as whole tokens on the reference-masked title (so "compatible with H12SSL
# motherboard" does not fire). Board words take joined, hyphenated or spaced
# forms: "Mother Board + AMD EPYC 7763" is as much a board listing as
# "Motherboard" (round-3 R3-E). Deliberately absent: 'server', bare 'board' and
# 'workstation'. Bare-CPU titles say "Server CPU", "Server Processor", "for
# 7002/7003 Series Boards" and "Workstation CPU" all the time, so those words
# would turn ordinary retail listings into reviews. The count forms need a
# bundling word or a CPU noun beside them, because a bare 'Nx' is often a core
# count ('32x 3.25GHz').
_BUNDLE = re.compile(
    r"(?<![a-z0-9])(?:mother[-\s]?boards?|main[-\s]?boards?|mobo|barebones?|combos?|bundles?|kits?"
    r"|(?:with|w/|incl|including|plus|\+)\s*[2-9]\s?x"
    r"|[2-9]\s?x\s+(?:cpus?|processors?|amd|intel|epyc|xeon))(?![a-z0-9])"
)


VENDOR_LOCKED: Final = "locked"
VENDOR_UNLOCKED: Final = "unlocked"

# Vendor (AMD PSB) lock wording is read on the canonical title with its OWN
# scope mask (_LOCK_REFERENCE), not the identity mask: lock words describe the
# unit on sale unless they sit inside a span citing another product
# ('replacement for an unlocked processor', 'compatible with Dell locked
# servers'), where they describe that product and never make a reading
# (they can still veto one: see _vendor_lock).
# Deliberately NOT masked: the CPU-local 'oem version of' span. Titles put the
# offered part's unlock inside it ('7T83 ... OEM Version of EPYC 7763
# unlocked'), and a retail EPYC has no PSB lock, so lock wording there can
# only describe the offered OEM unit. The residual risk is a seller writing
# 'OEM version of an unlocked 7763' about a locked unit, which no corpus
# listing shows; masking the span instead would leave every '... OEM Version
# of EPYC 7763 unlocked' listing (three corpus rows) with an unknown lock.
# Explicit wording only; OEM branding alone ('Dell', 'Pulled from Cisco UCS')
# never implies a lock, because OEMs ship both locked and unlocked parts.
#
# Negation is ONE window rule (the s8 round-3 shared negation design, which
# the condition-phrase negation in vocab is specified to follow too; change
# the two together): a lock or unlock assertion is NEGATED when a
# negator token (_NEGATORS) occurs within the _NEGATION_WINDOW tokens before
# its start. Lock qualifiers and articles (_WINDOW_QUALIFIERS) do not use up
# the window, and punctuation and hyphens are separators, never tokens, so
# 'NOT VENDOR-UNLOCKED', 'not a Dell PSB-unlocked CPU' and 'no longer
# unlocked' are all negated. Rounds 1-2 enumerated negation shapes as regex
# patches ('not'+one qualifier+'unlocked'); each round found a shape the
# patch missed ('no longer', two qualifiers) that left the bare 'unlocked'
# standing as a confident unlock satisfying require_vendor_unlocked (Codex s8
# r3 #2). A negated match is never a positive reading: it is recorded as the
# lock state the listing DENIES, which _vendor_lock uses only as a veto. A
# denied unlock does not prove a lock (the title is unknown unless explicit
# lock wording also stands), and a denied lock does not prove an unlock.
#
# Scan order is load-bearing:
#  1. _UNLOCK_PHRASES first: positive unlocked phrases whose own wording holds
#     a negator ('no vendor lock', 'not PSB locked', 'non-locked'). Matched
#     whole, their negator is their meaning, not a negation of a neighbour;
#     the window still applies before THEIR start ('not a no-vendor-lock CPU').
#  2. _UNLOCK_WORD, _UNLOCK_STEM, then _LOCK_WORD, each on text with every
#     earlier match blanked, so the 'lock'/'locked' inside a claimed phrase
#     or inside 'un-locked' is never read again: a sub-match inherits its
#     enclosing match's verdict and can never re-assert a denied or claimed
#     phrase.
# A window stops at an earlier assertion, so the negator claimed by 'no
# vendor lock' does not also negate a following 'Unlocked'. A bare 'unlock'
# (_UNLOCK_STEM) is never a reading ('unlock code'), but negated ('without
# unlock', 'no unlock') it is a denial, so it still vetoes an 'Unlocked'
# elsewhere in the title. Typos ('unclocked') are deliberately absent, and
# hyphens and spaces are interchangeable separators ('no-vendor-lock',
# 'psb-locked'). The watch clause treats unknown as not satisfying an unlocked requirement, so a
# missed unlocked costs a review while a misread one makes a locked CPU
# eligible: every doubtful shape resolves to unknown.
_LOCK_OEMS = r"(?:dell|lenovo|hpe?|cisco)"
_LOCK_QUALIFIER = rf"(?:(?:vendor|psb|{_LOCK_OEMS})[-\s]+)"
# The shared reference phrases plus lock-local ones. The lock-local phrases
# are registered in normalize._CATEGORY_REFERENCE_PHRASES (reference_phrase_
# pattern enforces it) so canonicalize_title keeps their clause punctuation
# and a span ends at the clause it opened; unregistered, the span ran on
# through a later 'NOT UNLOCKED' (Codex s8 r2 A). Boundaries alone cannot
# make masking safe (a title with no punctuation still runs the span to the
# end), which is why _vendor_lock also reads contradictions unmasked.
_LOCK_REFERENCE = reference_phrase_pattern("works with", "work with", "for use with", "for use in")
# 'isn't' canonicalizes to 'isn t' (the apostrophe becomes a space), so the
# contracted forms are listed by their stem as well as their squeezed form.
_NEGATORS: Final = frozenset(
    {"not", "no", "never", "non", "without", "isn", "isnt", "aren", "arent", "ain", "aint"}
)
_WINDOW_QUALIFIERS: Final = frozenset(
    {"vendor", "psb", "dell", "lenovo", "hp", "hpe", "cisco", "factory", "manufacturer"}
    | {"a", "an", "the", "cpu"}
)
_NEGATION_WINDOW: Final = 3
_WINDOW_TOKEN = re.compile(r"[a-z0-9]+")
_UNLOCK_PHRASES = re.compile(
    r"\bno[-\s]+(?:(?:vendor|psb)[-\s]+)?lock(?:ed)?\b"
    rf"|\b(?:not|isn\s?t)[-\s]+{_LOCK_QUALIFIER}?locked\b"
    r"|\bnon[-\s]?(?:(?:vendor|psb)[-\s]+)?locked\b"
)
_UNLOCK_WORD = re.compile(r"\bun-?locked\b")
_UNLOCK_STEM = re.compile(r"\bun-?lock\b")
# The bare word covers 'Dell Locked', 'vendor-locked', 'PSB locked', 'locked
# to <vendor>' and the emphasized '(*locked*)'. '<OEM> only' ('LENOVO ONLY') is
# an exclusivity claim, which for a CPU means it only boots in that OEM's
# boards: a vendor lock stated in other words.
_LOCK_WORD = re.compile(rf"\blocked\b|\b(?:vendor|psb)[-\s]lock\b|\b{_LOCK_OEMS}\s+only\b")


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


def _marker(pattern: re.Pattern[str], text: str) -> Attribute[str] | None:
    m = pattern.search(text)
    if m is None:
        return None
    return Attribute(value=m.group(0), confidence=0.9, layer=LAYER, source_text=m.group(0))


# Pin counts are socket context, not models, and some fit the model shapes:
# 'SP3 4094-pin' read 4094 as the sole EPYC model and vetoed the OPN's 7763
# (Codex s8 r2 C), and 'Gold 6338 4189-pin' reads as a coordinated second
# Xeon. Blanked before any model scan: a number followed by 'pin(s)', or an
# AMD socket name followed by that socket's own pin count ('SP5 6096',
# 'Socket SP3 (4094)'). Only the socket's own counts, never any number after a
# socket word: 'EPYC SP3 7763' names the model right after the socket. LGA
# counts need nothing here; _SOCKETS already takes 'LGA 4677' whole.
_PIN_COUNT = re.compile(
    r"\b\d{3,4}[-\s]?pins?\b"
    r"|\b(?:socket\s+)?(?:sp[3-6]|s?trx4|tr4|swrx8|str5|am[45])[-\s]*"
    r"\(?(?:1331|1718|4094|4844|6096)\b\)?"
)


def _epyc_models(identity: str) -> set[str]:
    """The distinct EPYC model numbers an EPYC title names; empty for a title
    without 'epyc'.

    Read from the reference-masked title, so a model cited only as a
    reference object does not count. Suffixes count as distinct models
    ('9354' and '9354p' are two parts).
    """
    if _EPYC_NAME.search(identity) is None:
        return set()
    unsocketed = _blank(_PIN_COUNT, identity)
    for pattern in _SOCKETS:
        unsocketed = pattern.sub(lambda m: " " * len(m.group(0)), unsocketed)
    return {m.group(0) for m in _EPYC_MODEL.finditer(unsocketed)}


# Xeon shorthand for a second model in a Xeon title: the tier word or the
# E-series prefix without another 'xeon' ('Gold 6338 / Platinum 8358',
# 'E5-2680 v4 / E5-2690 v4'), and bare numbers coordinated with a tier model
# ('Gold 6338/6348', 'Gold 6338 or 8358', 'Gold 6338, 6348 & 8358'). Read for
# the multi-model marker only, never as candidates: without the line word
# beside them these are not complete processor names, and emitting them would
# widen what reaches the alias table.
_XEON_NAME = re.compile(r"\bxeon\b")
_XEON_TIER_MODEL = re.compile(
    r"\b(?:platinum|gold|silver|bronze)\s+(?P<num>\d{4}[a-z]{0,2}\+?)(?![a-z0-9])"
)
# A bare model number continuing a tier model. The separator set is what the
# canonical title leaves of a coordination: "/" survives canonicalization,
# "or"/"and" are words, a raw "," becomes " - " only when a reference phrase
# is present and is otherwise erased along with "&", leaving bare whitespace
# ("gold 6338 8358" is how "Gold 6338, 8358" and "Gold 6338 & 8358" arrive).
# Accepting bare whitespace is why the unit lookahead is load-bearing: without
# it "Gold 6248 2666 MHz" or "Gold 6338 1000W" would read as two models. A
# misread here only vetoes (review), never accepts, so the set leans wide
# (round-5 R4-D residual: "Gold 6338 or 8358" accepted the seeded 6338).
_COORDINATED_MODEL = re.compile(
    r"(?:\s*/\s*|\s+-\s+|\s*[,&]\s*|\s+(?:or|and)\s+|\s+)"
    r"(?P<num>\d{4}(?!\s?(?:ghz|mhz|mt/s|gb|mb|tb|w)(?![a-z]))[a-z]{0,2}\+?)(?![a-z0-9])"
)
_XEON_E_MODEL = re.compile(r"\b(?P<num>e[357]-?\s?\d{4}[a-z]{0,2}(?:\s*v\d)?)\b")


def _model_key(num: str) -> str:
    # Spacing and hyphens are styling ('e5-2680 v4' = 'e52680v4'); '+' is not
    # ('8480' and '8480+' are two SKUs), so normalize_alias_text is not used.
    return re.sub(r"[\s-]", "", num)


def _line_models(identity: str) -> set[str]:
    """The distinct non-EPYC processor models a title names, as _model_key
    strings. EPYC is _epyc_models' job: its number shape is broader than the
    name phrase, so reading the EPYC phrase here too could count one model
    twice under two spellings."""
    identity = _blank(_PIN_COUNT, identity)
    models = {
        _model_key(m.group("num"))
        for _vendor, pattern in _NAMES
        if pattern is not _EPYC_NAME_PHRASE
        for m in pattern.finditer(identity)
    }
    if _XEON_NAME.search(identity) is not None:
        for m in _XEON_TIER_MODEL.finditer(identity):
            models.add(_model_key(m.group("num")))
            pos = m.end()
            while (more := _COORDINATED_MODEL.match(identity, pos)) is not None:
                models.add(_model_key(more.group("num")))
                pos = more.end()
        models.update(_model_key(m.group("num")) for m in _XEON_E_MODEL.finditer(identity))
    return models


def _model_keys(identity: str) -> set[str]:
    return _epyc_models(identity) | _line_models(identity)


def _multi_model(identity: str) -> Attribute[str] | None:
    models = _model_keys(identity)
    if len(models) < 2:
        return None
    joined = " ".join(sorted(models))
    return Attribute(value=joined, confidence=0.9, layer=LAYER, source_text=joined)


def _listing_model(identity: str) -> Attribute[str] | None:
    models = _model_keys(identity)
    if len(models) != 1:
        return None
    (key,) = models
    return Attribute(value=key, confidence=0.9, layer=LAYER, source_text=key)


def model_key(model_number: str) -> str | None:
    """The catalog side of the `model` veto: a seeded `model_number` ('EPYC
    9354', 'Xeon Platinum 8480+') read by the same scanners as a listing
    title, so both sides compare in one key space ('9354', '8480+'). None
    when the number does not read as exactly one model; then the target
    cannot veto on model."""
    models = _model_keys(canonicalize_title(model_number))
    if len(models) != 1:
        return None
    (key,) = models
    return key


def _blank(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


@dataclass(frozen=True, slots=True)
class _LockWording:
    """The first match of each kind in one text: `unlocked` / `locked` are
    un-negated assertions; `unlock_denied` / `lock_denied` are negated ones,
    the lock state the text explicitly denies (see the window-rule note)."""

    unlocked: re.Match[str] | None = None
    locked: re.Match[str] | None = None
    unlock_denied: re.Match[str] | None = None
    lock_denied: re.Match[str] | None = None


def _negated(text: str, start: int, stops: list[tuple[int, int]]) -> bool:
    counted = 0
    for token in reversed(list(_WINDOW_TOKEN.finditer(text, 0, start))):
        if any(lo <= token.start() < hi for lo, hi in stops):
            return False
        word = token.group(0)
        if word in _NEGATORS:
            return True
        if word not in _WINDOW_QUALIFIERS:
            counted += 1
            if counted == _NEGATION_WINDOW:
                return False
    return False


def _lock_wording(text: str) -> _LockWording:
    """Classify every lock/unlock assertion in `text` in the load-bearing scan
    order described above, then apply the negation window to each."""
    scans = ((_UNLOCK_PHRASES, True, True), (_UNLOCK_WORD, True, True))
    scans += ((_UNLOCK_STEM, True, False), (_LOCK_WORD, False, True))
    # (match, is an unlock assertion, may make a reading when not negated)
    found: list[tuple[re.Match[str], bool, bool]] = []
    rest = text
    for pattern, unlock, reads in scans:
        found += [(m, unlock, reads) for m in pattern.finditer(rest)]
        rest = _blank(pattern, rest)
    spans = [f[0].span() for f in found]
    first: dict[tuple[bool, bool], re.Match[str]] = {}
    for m, unlock, reads in sorted(found, key=lambda f: f[0].start()):
        denied = _negated(text, m.start(), [s for s in spans if s[1] <= m.start()])
        if denied or reads:
            first.setdefault((unlock, denied), m)
    return _LockWording(
        unlocked=first.get((True, False)),
        locked=first.get((False, False)),
        unlock_denied=first.get((True, True)),
        lock_denied=first.get((False, True)),
    )


def _vendor_lock(title: str) -> Attribute[str] | None:
    """The unit's stated vendor lock; None when the title states neither, or
    both (a self-contradicting title is unknown, not whichever came first).
    A negated unlock never yields unlocked: it is unknown, or locked when
    explicit lock wording also stands; a negated lock likewise never yields
    locked. See _LOCK_REFERENCE for the scope."""
    masked = _lock_wording(mask_reference_spans(title, _LOCK_REFERENCE))
    # The reading comes from the masked text; the veto from the whole title.
    # A reference mask may only ever turn a reading into unknown: wording it
    # hides can remove evidence FOR a reading, never evidence AGAINST one. An
    # over-wide span ('Unlocked works with Dell R7525 NOT UNLOCKED', no
    # punctuation to end it) would otherwise hide the negation and leave a
    # confident unlocked that satisfies require_vendor_unlocked. The cost is
    # that lock wording about a cited product ('Unlocked, compatible with Dell
    # locked servers') makes the unit's own reading unknown: a review, where
    # the other error makes a locked CPU eligible.
    whole = _lock_wording(title)
    if masked.unlocked is not None and masked.locked is None and masked.unlock_denied is None:
        if whole.unlock_denied is not None or whole.locked is not None:
            return None
        m, value = masked.unlocked, VENDOR_UNLOCKED
    elif masked.locked is not None and masked.unlocked is None and masked.lock_denied is None:
        if whole.unlocked is not None or whole.lock_denied is not None:
            return None
        m, value = masked.locked, VENDOR_LOCKED
    else:
        return None
    return Attribute(value=value, confidence=0.9, layer=LAYER, source_text=m.group(0))


def _is_amd(title: str) -> bool:
    """The vendor-lock scope. The lock is AMD Platform Secure Boot; on an Intel
    part "Unlocked"/"locked" states the multiplier (K-series), so reading it
    there would satisfy an unlocked requirement with the wrong property. Read
    on the unmasked title, not the identity-masked one: identity masking hides
    'OEM Version of AMD EPYC 7763 unlocked', whose lock wording _vendor_lock
    deliberately still reads, and a hidden brand would drop it."""
    brand = _brand(title)
    return brand is not None and brand.value == "amd"


def extract(title: str) -> ExtractedAttributes:
    sockets = [(socket_key(m.group(0)), m.group(0)) for p in _SOCKETS for m in p.finditer(title)]
    cores = [(int(m.group(1) or m.group(2)), m.group(0)) for m in _CORES.finditer(title)]
    # The sample, bundle and multi-model markers are identity evidence, so they
    # read the reference-masked title: "replacement for an engineering sample"
    # does not make the listed part one.
    identity = _identity_text(title)
    brand = _brand(identity)
    payload = CpuAttributes(
        socket=sole_value(sockets, 0.9),
        cores=sole_value(cores, 0.85),
        tdp_w=sole_value(((int(m.group(1)), m.group(0)) for m in _TDP.finditer(title)), 0.7),
        sample=_marker(_SAMPLE, identity),
        bundle=_marker(_BUNDLE, identity),
        multi_model=_multi_model(identity),
        vendor_lock=_vendor_lock(title) if _is_amd(title) else None,
        model=_listing_model(identity),
    )
    # quantity feeds ladder.decide's lot review (a "Lot of 4" CPU listing is
    # not a single-unit offer); read from the one vocab table, like drive.
    return replace(
        vocab.offer_terms(title),
        brand=brand,
        category_attrs=payload,
        quantity=vocab.extract_quantity(title),
    )


def with_structured_mpn(extracted: ExtractedAttributes, structured_mpn: str) -> ExtractedAttributes:
    """`extracted` with a sample marking found in the merchant's structured MPN
    field folded into `sample`; unchanged when the title already carries one or
    the field has none.

    The title-only `extract` cannot see the field, and the candidate guard that
    drops a suffixed OPN only stops that token from being a hit: a clean title
    ('AMD EPYC 7763 64-Core SP3') still hits the retail alias, so without this the
    listing would accept although the merchant asserts a QS part.
    """
    payload = extracted.category_attrs
    if not isinstance(payload, CpuAttributes) or payload.sample is not None:
        return extracted
    sample = _marker(_SAMPLE, canonicalize_title(structured_mpn))
    if sample is None:
        return extracted
    return replace(extracted, category_attrs=replace(payload, sample=sample))


def extract_candidates(
    title: str, *, structured_mpn: str | None = None, source_key: str = ""
) -> list[MpnCandidate]:
    out = CandidateSet()
    title = _identity_text(title)
    brand = _brand(title)
    add_structured(out, structured_mpn, TokenKind.MANUFACTURER_MPN, brand.value if brand else "")
    if len(_epyc_models(title)) > 1:
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
            whole = m.group(0)
            if m.groupdict().get("qualifiers"):
                # 'epyc genoa sp5 9354' → 'epyc 9354': seeds alias the name
                # without codename or platform words, so the whole phrase
                # would never join.
                start, end = m.span("qualifiers")
                whole = title[m.start() : start] + title[end : m.end()]
            out.add(whole, TokenKind.MANUFACTURER_MPN, vendor=vendor, confidence=0.85)
            out.add(m.group("num"), TokenKind.MANUFACTURER_MPN, vendor=vendor, confidence=0.75)
    add_house_skus(out, title, source_key)
    add_code_tokens(out, title, _VOCAB_TAILS)
    return out.result()


def veto(extracted: ExtractedAttributes, catalog: HardAttrs) -> list[str]:
    """Fields where the listing and the target are both known and disagree
    (socket, cores, and the named `model`), plus 'sample', 'bundle' and
    'multi_model' whenever the listing carries that marker, whatever the
    target."""
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
    # Same reasoning: no catalog CPU is a board or a bundle, and a title naming
    # two models asserts neither. These are what keep a rung-0 prior from being
    # inherited when a re-observed title turns into one (rung 0 reads no
    # candidates, only this veto).
    if listing.bundle is not None:
        vetoed.append("bundle")
    if listing.multi_model is not None:
        vetoed.append("multi_model")
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
    # The candidate that reached the target may be an OPN or ordering code
    # while the title names a different model: '9354P ... 100-000000798'
    # hits the seeded 9354 through its OPN, and 9354P has no alias to collide
    # with, so no candidate-level guard sees two targets (and CPU runs without
    # distinct_mpn_guard). Comparing the named model with the target's own is
    # the only place the unseeded suffix variant is visible, and as a veto it
    # also re-runs at rung 0, so an accepted listing re-titled to another
    # model cannot inherit its prior (Codex s8 r1 #8).
    if listing.model is not None and spec.model is not None and listing.model.value != spec.model:
        vetoed.append("model")
    return vetoed
