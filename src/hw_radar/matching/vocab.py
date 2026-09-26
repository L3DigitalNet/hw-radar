"""N2 controlled vocabularies → typed attributes (spec C.3.1).

Input is ALWAYS canonicalize_title() output (lowercase, single-spaced, ASCII
dashes). Unknown stays None — never guessed. Attribute values reuse the catalog
TextChoices literals so the resolver can feed variant-on-demand creation
directly. Confidence numbers are per-pattern judgment calls, OQ-provisional.

Brand normalization encodes the WD↔SanDisk "Optimus" rebrand exactly as spec
C.3.1 mandates ("same MPNs, new brand", Jan 2026). Verified scope (Codex
CR-004): the rebrand moved WD-BRANDED SSD LINES under the SanDisk name — it is
NOT an HDD claim. Mapping sandisk → western_digital is still safe as a brand
key because brand is only a GATE here — equivalence alone never merges
anything; every rung-1/2 accept requires an MPN/alias hit, so a native SanDisk
product cannot join a WD model unless its exact token hits a WD alias.
Re-verify against seeded catalog aliases at MS-1c; split the key there if the
catalog contradicts the equivalence."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Final

from hw_radar.matching.normalize import (
    DRIVE_REFERENCE_PHRASE,
    FOR_PARTS_PREAMBLE_WORDS,
    mask_reference_spans,
)
from hw_radar.matching.types import Attribute, ExtractedAttributes

_LAYER = "vocab"
_GB = 1_000_000_000
_TB = 1_000_000_000_000

_CAPACITY = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(tb|gb)\b(?!/s)"
)  # Final-review I-1: exclude link-speed tokens like '12gb/s'
_RPM_PLAIN = re.compile(r"\b(\d{4,5})\s*rpm\b")
_RPM_K = re.compile(r"\b(\d{1,2}(?:\.\d)?)k\s*rpm\b")
_CACHE = re.compile(r"\b(\d{1,4})\s*mb\s+(?:cache|buffer)\b")
_SECTOR = re.compile(r"\b(512n|512e|4kn)\b")
_LINK_SPEED = re.compile(r"\b(\d+(?:\.\d+)?)\s*gb/?s\b")
_WARRANTY_YEARS = re.compile(r"\b(\d{1,2})\s*[- ]?(?:yr|year)s?\s+warranty\b")
# (?<![\d.]) blocks '13.5' matching as 3.5; unit suffix raises confidence.
_FORM_35 = re.compile(r"(?<![\d.])3\.5\s*(\"|in(?:ch)?\b)?")
_FORM_25 = re.compile(r"(?<![\d.])2\.5\s*(\"|in(?:ch)?\b)?")
_FORM_M2 = re.compile(r"\bm\.?2\b")

_INTERFACES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bnvme\b"), "nvme"),
    (re.compile(r"\bsas\b"), "sas"),
    (re.compile(r"\bsata\b"), "sata"),
    (re.compile(r"\bscsi\b"), "scsi"),
    (re.compile(r"\busb\b"), "usb"),
)

# Ordered, first match wins: for_parts outranks used ("used - for parts");
# factory recert outranks plain recert. 'renewed' is Amazon-speak for seller
# refurb, NOT manufacturer recert. '(?<!like )new' keeps "like new" unasserted.
_CONDITIONS: tuple[tuple[re.Pattern[str], str, str | None, float], ...] = (
    (
        # "spares or repair" is the UK/eBay spelling of for-parts. The leading
        # "for <word>" preamble is exempt from reference masking for exactly
        # the words in normalize.FOR_PARTS_PREAMBLE_WORDS, so this pattern
        # reads the same definition: a word exempted there but not asserted
        # here puts a broken drive on a working-condition variant.
        re.compile(
            r"\bparts only\b|\bas[- ]is\b|\bnot working\b"
            r"|\bspares?(?: or | and | ?/ ?| )repairs?\b"
            rf"|\bfor {FOR_PARTS_PREAMBLE_WORDS}\b"
        ),
        "for_parts",
        None,
        0.95,
    ),
    (re.compile(r"\b(?:factory|manufacturer) recert(?:ified)?\b"), "recertified", "factory", 0.95),
    (re.compile(r"\brecert(?:ified)?\b"), "recertified", None, 0.85),
    (re.compile(r"\bseller refurb(?:ished)?\b"), "refurbished", "seller", 0.95),
    (re.compile(r"\brefurb(?:ished)?\b|\brenewed\b"), "refurbished", None, 0.85),
    (re.compile(r"\bopen box\b"), "open_box", None, 0.9),
    (re.compile(r"\bserver pull\b|\bpull(?:ed)?\b|\bused\b"), "used", None, 0.8),
    # "new" asserts nothing in three positions, each excluded here rather than
    # left to the table order:
    # - "like new" / "like-new" (a cosmetic grade; canonicalize_title keeps the
    #   hyphen, so both separators are excluded);
    # - next to a percentage on EITHER side ("90%new", "95 % new", "new 90%"):
    #   seller marketing for a used drive (owner ruling Q7, MS-1e ebay-0261),
    #   not a condition, and it must not fall through to any other condition;
    # - before "pull"/"pulled": "new pull" is a used server pull (Q7). The used
    #   rule above also claims it, but only because it happens to come first;
    #   the lookahead keeps a reordering from turning pulls into new drives.
    (
        re.compile(
            r"\bfactory sealed\b"
            r"|(?<!like )(?<!like-)(?<!%)(?<!% )\bnew\b(?![- ]pull)(?! ?\d{1,3}(?:\.\d+)? ?%)"
        ),
        "new",
        None,
        0.8,
    ),
)

_PACKAGING: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bretail(?:\s+box(?:ed)?)?\b"), "retail"),
    (re.compile(r"\boem\b|\bbulk\b|\bbare drive\b|\bbrown box\b"), "bulk"),
)

_WARRANTY_CHANNELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bno warranty\b"), "none"),
    (re.compile(r"\bmanufacturer warranty\b"), "manufacturer"),
    (re.compile(r"\bseller warranty\b"), "seller"),
)

# Longest-married-name first so 'western digital' wins over 'wd'.
_BRANDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bwestern digital\b"), "western_digital"),
    (re.compile(r"\bsandisk\b"), "western_digital"),  # Optimus rebrand, Jan 2026
    (re.compile(r"\bwd\b"), "western_digital"),
    (re.compile(r"\bhgst\b|\bhitachi\b"), "hgst"),
    (re.compile(r"\bseagate\b"), "seagate"),
    (re.compile(r"\btoshiba\b"), "toshiba"),
    (re.compile(r"\bsamsung\b"), "samsung"),
    (re.compile(r"\bsolidigm\b"), "solidigm"),
    (re.compile(r"\bintel\b"), "intel"),
    (re.compile(r"\bmicron\b|\bcrucial\b"), "micron"),
    (re.compile(r"\bkingston\b"), "kingston"),
    (re.compile(r"\bkioxia\b"), "kioxia"),
)


# Drive product-line names a title can assert, per canonical brand key. Values
# are canonicalize_title() forms, the same strings seeds and grammars produce
# for ProductFamily.normalized_name (ladder.family_conflicts compares them).
# Family-level lines only: series names ("Exos X16", "DC HC550") fold into
# their line via the longest-match scan, and rebrand predecessors
# ("Constellation", "Enterprise Capacity") are deliberately absent, because the
# same MPN was sold under both names (grammars/seagate.py) and naming the
# older brand does not contradict the newer one. Anything unlisted stays
# unknown and can never contradict.
_DISTINCT_FAMILIES: dict[str, tuple[str, ...]] = {
    "seagate": (
        "ironwolf pro",
        "ironwolf",
        "exos",
        "skyhawk ai",
        "skyhawk",
        "barracuda pro",
        "barracuda",
        "firecuda",
        "nytro",
    ),
    "western_digital": ("ultrastar", "deskstar", "red plus", "red pro", "purple pro"),
}
# WD's colour-named lines are ordinary words ("gold", "black", "blue") that a
# title uses for other things, so they count only right after the WD brand
# word ("WD Red", "Western Digital Gold"). Multi-word lines ("red plus") are
# distinctive enough to count anywhere and live in _DISTINCT_FAMILIES.
_WD_BRAND_ADJACENT = re.compile(
    r"\b(?:wd|western digital)\s+"
    r"(red plus|red pro|red|gold|purple pro|purple|blue|black|green)\b"
)


# Quantity: digit-FIRST forms only. The 'xN' form (e.g. 'x16') is deliberately
# unsupported — it collides with Seagate family names (Exos X16/X18/X24).
# Ordered, first match wins, so an explicit lot form outranks a stray "Nx".
#
# Two readers with different bars. Eligibility divides the price by any
# quantity at or above its policy confidence (lot pricing; offer_quantity).
# ladder.decide reviews every accept whose quantity is above 1 at
# LOT_MIN_CONFIDENCE or more (owner ruling Q4: a lot is not a single-unit
# offer). Every form at that bar was checked against all 718 MS-1e drive and
# 284 CPU corpus titles and matched only real multi-unit offers, board bundles
# the CPU bundle veto already reviews, or an explicit quantity of 1.
#
# "Nx" is at the bar only when the count is tied to the item: followed by a
# brand, a drive line, a drive/CPU noun or a TB capacity ("2x seagate",
# "10x amd epyc", "2 x 16tb"; s8 Codex r1 finding 3). A bare "Nx" stays
# below it: "32x 3.25ghz" is a CPU thread count (corpus cpu-0010). Capacity
# is TB only: "2x16gb" / "2x 16gb" is a RAM kit shape, and eligibility reads
# this table for every category, so a GB form would start dividing RAM kit
# prices by their module count.
LOT_MIN_CONFIDENCE: Final = 0.9
_LOT_ITEM_WORDS: Final = (
    "western digital",
    "wd",
    "sandisk",
    "hgst",
    "hitachi",
    "seagate",
    "toshiba",
    "solidigm",
    "intel",
    "kioxia",
    "amd",
    "epyc",
    "xeon",
    "ultrastar",
    *(name for names in _DISTINCT_FAMILIES.values() for name in names),
    r"hard (?:disk )?drives?",
    r"hard disks?",
    "hdds?",
    "ssds?",
    "drives?",
    "disks?",
    "cpus?",
    "processors?",
)
# Brands that also sell memory: "2x kingston 16gb ddr4" is ONE kit of two
# modules, and eligibility divides the price by this count for every category.
# These brands tie the count only when the title carries no memory marker, so
# "2x samsung 870 evo 1tb ssd" still reviews as a drive multipack.
_LOT_MEMORY_BRANDS: Final = ("samsung", "micron", "crucial", "kingston")
_MEMORY_MARKER: Final = r"(?!.*\b(?:ddr\d?|dimm|rdimm|udimm|lrdimm|sodimm|so-dimm|memory|ram)\b)"
# Offer words that may sit between the count and the item ("2x new seagate",
# "3x brand new wd"): the count still describes the item, and requiring
# adjacency would let "2x " prepended to a "New Seagate ..." title escape.
_LOT_OFFER_WORDS: Final = (
    r"(?:brand|new|used|sealed|genuine|original|oem|bulk|retail|pulled"
    r"|refurb(?:ished)?|recert(?:ified)?|factory|enterprise|internal)"
)
_QUANTITIES: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\blot of (\d{1,3})x?\b"), 0.95),
    # "Lot 10 Supermicro Seagate ..." (MS-1e ebay-0282/0288).
    (re.compile(r"\blot (\d{1,3})x?\b"), 0.9),
    (re.compile(r"\b(\d{1,3})[- ]pack\b"), 0.9),
    (re.compile(r"\bqty:? ?(\d{1,3})\b"), 0.9),
    # "2pcs AMD EPYC ...", "1pcs new Seagate ..." (cpu-0260, ebay-0296).
    (re.compile(r"\b(\d{1,3}) ?pcs?\b"), 0.9),
    (
        re.compile(
            r"\b(\d{1,3}) ?x (?:" + _LOT_OFFER_WORDS + r" )*"
            r"(?:(?:" + "|".join(_LOT_ITEM_WORDS) + r")\b|\d+(?:\.\d+)? ?tb\b)"
        ),
        0.9,
    ),
    (
        re.compile(
            r"\b(\d{1,3}) ?x (?:" + _LOT_OFFER_WORDS + r" )*"
            r"(?:" + "|".join(_LOT_MEMORY_BRANDS) + r")\b" + _MEMORY_MARKER
        ),
        0.9,
    ),
    (re.compile(r"\b(\d{1,3})\s?x\b"), 0.7),
)
# Auction catalogue numbers ("Auction Lot 42:", "Lot #42", "Lot No. 42") name
# the sale, not a unit count; they are blanked before the quantity scan. The
# "lot N" form would otherwise read "auction lot 42" as 42 units, send a
# single drive to lot review and divide its price by 42 in eligibility.
_AUCTION_LOT = re.compile(r"\bauction lot (?:#|no\.? ?)?\d+|\blot (?:#|no\.? ?)\d+")


def _alternation(names: tuple[str, ...]) -> re.Pattern[str]:
    # Longest first, so "ironwolf pro" wins over its prefix "ironwolf" at one
    # position and a title naming the Pro line never also mentions the base.
    ordered = sorted(names, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in ordered) + r")\b")


_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (brand, _alternation(names)) for brand, names in _DISTINCT_FAMILIES.items()
)


def _family_mentions(masked: str) -> Attribute[tuple[tuple[str, str], ...]] | None:
    found: set[tuple[str, str]] = set()
    for brand, pattern in _FAMILY_PATTERNS:
        found.update((brand, m.group(1)) for m in pattern.finditer(masked))
    found.update(("western_digital", m.group(1)) for m in _WD_BRAND_ADJACENT.finditer(masked))
    if not found:
        return None
    return Attribute(
        value=tuple(sorted(found)),
        confidence=0.85,
        layer=_LAYER,
        source_text=", ".join(name for _, name in sorted(found)),
    )


def _capacity(title: str) -> Attribute[int] | None:
    values: list[tuple[int, str]] = []
    for m in _CAPACITY.finditer(title):
        unit = _TB if m.group(2) == "tb" else _GB
        values.append((int(float(m.group(1)) * unit), m.group(0)))
    if not values:
        return None
    distinct = {v for v, _ in values}
    confidence = 0.9 if len(distinct) == 1 else 0.5
    value, source = values[0]
    return Attribute(value=value, confidence=confidence, layer=_LAYER, source_text=source)


def _first_pattern(
    title: str, table: tuple[tuple[re.Pattern[str], str], ...], confidence: float
) -> Attribute[str] | None:
    for pattern, value in table:
        m = pattern.search(title)
        if m:
            return Attribute(
                value=value, confidence=confidence, layer=_LAYER, source_text=m.group(0)
            )
    return None


def _form_factor(title: str) -> Attribute[str] | None:
    for pattern, value in ((_FORM_35, "3.5"), (_FORM_25, "2.5")):
        m = pattern.search(title)
        if m:
            confidence = 0.95 if m.group(1) else 0.7  # bare '3.5' is weaker
            return Attribute(
                value=value, confidence=confidence, layer=_LAYER, source_text=m.group(0)
            )
    m = _FORM_M2.search(title)
    if m:
        return Attribute(value="m.2", confidence=0.85, layer=_LAYER, source_text=m.group(0))
    return None


def _rpm(title: str) -> Attribute[int] | None:
    m = _RPM_PLAIN.search(title)
    if m:
        return Attribute(
            value=int(m.group(1)), confidence=0.95, layer=_LAYER, source_text=m.group(0)
        )
    m = _RPM_K.search(title)
    if m:
        return Attribute(
            value=int(float(m.group(1)) * 1000),
            confidence=0.9,
            layer=_LAYER,
            source_text=m.group(0),
        )
    return None


def _int_pattern(title: str, pattern: re.Pattern[str], scale: int = 1) -> Attribute[int] | None:
    m = pattern.search(title)
    if m is None:
        return None
    return Attribute(
        value=int(m.group(1)) * scale, confidence=0.9, layer=_LAYER, source_text=m.group(0)
    )


def _condition(title: str) -> tuple[Attribute[str] | None, Attribute[str] | None]:
    for pattern, value, channel, confidence in _CONDITIONS:
        m = pattern.search(title)
        if m:
            cond = Attribute(
                value=value, confidence=confidence, layer=_LAYER, source_text=m.group(0)
            )
            chan = (
                Attribute(
                    value=channel, confidence=confidence, layer=_LAYER, source_text=m.group(0)
                )
                if channel
                else None
            )
            return cond, chan
    return None, None


def _condition_conflict(title: str) -> Attribute[tuple[str, ...]] | None:
    """Every distinct condition the text asserts, when it asserts more than one.

    Informational for ordinary listings: the first-match precedence of
    _CONDITIONS still picks their condition. with_source_offer_terms and
    source_offer_conflict read it, because a source's proven condition must
    not be folded over a listing that also asserts another."""
    found = [
        (value, m.group(0))
        for pattern, value, _channel, _confidence in _CONDITIONS
        if (m := pattern.search(title)) is not None
    ]
    values = tuple(dict.fromkeys(value for value, _ in found))
    if len(values) < 2:
        return None
    return Attribute(
        value=values,
        confidence=0.9,
        layer=_LAYER,
        source_text=", ".join(text for _, text in found),
    )


def _quantity_statements(title: str) -> tuple[Attribute[int] | None, tuple[int, ...]]:
    """The first-match quantity of _QUANTITIES, plus every distinct count the
    title states at LOT_MIN_CONFIDENCE or more; auction catalogue numbers are
    blanked first."""
    text = _AUCTION_LOT.sub(" ", title)
    first: Attribute[int] | None = None
    counts: set[int] = set()
    for pattern, confidence in _QUANTITIES:
        for m in pattern.finditer(text):
            if first is None:
                first = Attribute(
                    value=int(m.group(1)),
                    confidence=confidence,
                    layer=_LAYER,
                    source_text=m.group(0),
                )
            if confidence >= LOT_MIN_CONFIDENCE:
                counts.add(int(m.group(1)))
    return first, tuple(sorted(counts))


def _conflict(counts: tuple[int, ...], confidence: float) -> Attribute[int]:
    return Attribute(
        value=max(counts),
        confidence=confidence,
        layer=_LAYER,
        source_text="conflicting quantities: " + ", ".join(str(c) for c in counts),
    )


def extract_quantity(title: str) -> Attribute[int] | None:
    """The listing's stated unit count for IDENTITY (ladder.decide's lot
    review), or None when it states none. Public for the category rules
    modules, so every category reads the one table above.

    Conflicting counts at the lot bar ("Lot of 2 ... (1pc)") return the
    largest at the bar: the title cannot prove a single unit, so the lot
    review must fire. Pricing must not use this value as a divisor; it reads
    offer_quantity instead."""
    first, counts = _quantity_statements(title)
    if len(counts) > 1:
        return _conflict(counts, LOT_MIN_CONFIDENCE)
    return first


# Below every eligibility divisor bar (CategoryPolicy.listing_min_confidence),
# so a conflicting count is never used to divide a price.
_CONFLICT_PRICING_CONFIDENCE: Final = 0.5


def offer_quantity(title: str) -> Attribute[int] | None:
    """The listing's stated unit count for PRICING (eligibility's unit-price
    divisor). Equal to extract_quantity except under conflicting counts, where
    the count is unproven and is returned at a confidence no divisor bar
    accepts: the undivided price is then only an upper bound on the unit
    price, never a price divided by a count the title contradicts."""
    first, counts = _quantity_statements(title)
    if len(counts) > 1:
        return _conflict(counts, _CONFLICT_PRICING_CONFIDENCE)
    return first


def _link_speed(title: str) -> Attribute[float] | None:
    m = _LINK_SPEED.search(title)
    if m is None:
        return None
    return Attribute(value=float(m.group(1)), confidence=0.9, layer=_LAYER, source_text=m.group(0))


def _recording(title: str) -> Attribute[str] | None:
    # PMR is deliberately unmapped: marketing usage is ambiguous (spec C.3.1
    # posture: unknown over guessed).
    if re.search(r"\bcmr\b", title):
        return Attribute(value="cmr", confidence=0.95, layer=_LAYER, source_text="cmr")
    if re.search(r"\bsmr\b", title):
        return Attribute(value="smr", confidence=0.95, layer=_LAYER, source_text="smr")
    return None


def _security(title: str) -> Attribute[str] | None:
    for token in ("sed", "fips", "ise"):
        if re.search(rf"\b{token}\b", title):
            return Attribute(value=token, confidence=0.85, layer=_LAYER, source_text=token)
    return None


def _sector(title: str) -> Attribute[str] | None:
    m = _SECTOR.search(title)
    if m is None:
        return None
    return Attribute(value=m.group(1), confidence=0.95, layer=_LAYER, source_text=m.group(0))


def offer_terms(title: str) -> ExtractedAttributes:
    """Return only the offer-term fields (condition, recert channel, packaging,
    warranty months and channel) that vocab.extract would produce for `title`.

    The category rules modules build on this so every category reads condition
    and warranty from the one set of tables above, and the resolver's
    variant-on-demand path gets the same TextChoices literals for all of them.
    Every other field stays None. Both functions read these fields through
    _offer_fields; they differ only in the mask: this reads the shared phrase
    set, vocab.extract the wider drive set (normalize.DRIVE_REFERENCE_PHRASE),
    so on a drive title with a drive-local phrase ("FOR Seagate ... NEW")
    extract may blank a term this still reports.

    Offer terms are read with reference spans masked: "comparable to factory
    recertified drives" says nothing about this item's condition."""
    return _offer_fields(mask_reference_spans(title))


@dataclass(frozen=True)
class SourceOfferProvenance:
    """Offer terms a source proves for every listing it carries, whatever the
    title says or omits. Values are the catalog TextChoices literals, like the
    title-derived ones."""

    condition: str
    recert_channel: str


# Per-source provenance, keyed by SourceSite.normalized_name (the connector's
# `site_key`; test_vocab_owner_rulings.test_declared_sources_are_registered_adapters
# pins every key to a registered adapter). The WD recertified store sells only WD's own
# `-recertified` product codes (acquisition/sources/wd.py filters on that
# suffix), so its listings are factory recertifications (owner ruling Q6)
# although the store titles say only "Recertified". The generic text rule is
# deliberately untouched: a marketplace "recertified" still asserts no channel.
# Seagate's recertified store is not declared: no ruling covers it yet.
SOURCE_OFFER_PROVENANCE: Final[dict[str, SourceOfferProvenance]] = {
    "wd-recertified": SourceOfferProvenance(condition="recertified", recert_channel="factory"),
}


def source_offer_conflict(
    extracted: ExtractedAttributes, source_key: str
) -> tuple[str, ...] | None:
    """The conditions a listing from a declared source asserts when it asserts
    the declared condition AND another ("... Recertified" with a "Used"
    condition label; "... Recertified New Pull"); None otherwise, and always
    None for an undeclared source.

    The resolver reviews an accept on such a listing at every rung, rung 0
    included: the listing contradicts itself, so neither the store's variant
    nor any first-match pick is a sellable identity it proves, and inheriting
    a prior factory variant would keep the contradiction forever."""
    declared = SOURCE_OFFER_PROVENANCE.get(source_key)
    conflict = extracted.condition_conflict
    if declared is None or conflict is None or declared.condition not in conflict.value:
        return None
    return conflict.value


def with_source_offer_terms(extracted: ExtractedAttributes, source_key: str) -> ExtractedAttributes:
    """`extracted` with the source's declared offer terms folded in; unchanged
    for a source with no declaration.

    The title stays authoritative when it contradicts the declaration: a store
    title asserting another condition ("Used", "For parts") keeps that
    condition and gets NO declared channel, since the store's claim evidently
    does not describe this item and a factory channel on a used or broken
    drive would file it under a sellable variant it is not. The same holds
    for a title channel other than the declared one. A title that states no
    condition gets the declared one.

    A title asserting the declared condition AND another (source_offer_conflict)
    gets neither a condition nor a channel: the first-match table would pick
    "recertified" and the fold would add "factory", proving a factory recert
    the listing's own "Used" contradicts. Condition unknown keeps a
    condition-restricted watch at `unknown`, and the resolver reviews.

    The ONE fold both offer-term readers apply, each over the same category
    extraction: the resolver before the ladder (so _materialize and
    _offer_reconsideration see the factory variant) and eligibility's
    _listing_attributes (so the condition clause reads the same condition). A
    reader that skipped it, or folded over a different extraction, would
    disagree with the variant."""
    declared = SOURCE_OFFER_PROVENANCE.get(source_key)
    if declared is None:
        return extracted
    if source_offer_conflict(extracted, source_key) is not None:
        return replace(extracted, condition=None, recert_channel=None)
    condition, channel = extracted.condition, extracted.recert_channel
    if condition is not None and condition.value != declared.condition:
        return extracted
    if channel is not None and channel.value != declared.recert_channel:
        return extracted
    source_text = f"source:{source_key}"
    return replace(
        extracted,
        condition=condition
        or Attribute(
            value=declared.condition, confidence=0.95, layer="source", source_text=source_text
        ),
        recert_channel=channel
        or Attribute(
            value=declared.recert_channel,
            confidence=0.95,
            layer="source",
            source_text=source_text,
        ),
    )


def _offer_fields(masked: str) -> ExtractedAttributes:
    # Caller passes mask_reference_spans output. condition, packaging,
    # recert_channel and warranty_channel are VARIANT identity:
    # resolver._materialize get_or_creates the ProductVariant from exactly
    # these four, so reading them from a reference span would file the listing
    # under a sellable variant it never offered (review finding F4).
    condition, recert_channel = _condition(masked)
    return ExtractedAttributes(
        condition=condition,
        condition_conflict=_condition_conflict(masked),
        recert_channel=recert_channel,
        packaging=_first_pattern(masked, _PACKAGING, 0.85),
        warranty_months=_int_pattern(masked, _WARRANTY_YEARS, scale=12),
        warranty_channel=_first_pattern(masked, _WARRANTY_CHANNELS, 0.9),
    )


def extract(title: str) -> ExtractedAttributes:
    # The drive phrase set, same as mpn.extract_candidates: brand and MPN must
    # read one masked text.
    masked = mask_reference_spans(title, DRIVE_REFERENCE_PHRASE)
    offer = _offer_fields(masked)
    return replace(
        offer,
        # Hard-attribute fields read the UNMASKED title. The ladder consults
        # them only in contradiction vetoes (ladder.contradictions), and every
        # veto outcome is REVIEW: a value read from a reference span can send a
        # match to review but can never create or select an identity. Masking
        # them instead would let an over-reaching span hide the listing's own
        # contradicting capacity or interface, trading a visible review for a
        # silent accept. quantity is in the same position: its only matching
        # use is ladder.decide's lot review, which can only demote, and
        # eligibility's lot pricing keeps its historical unmasked read.
        capacity_bytes=_capacity(title),
        interface=_first_pattern(title, _INTERFACES, 0.9),
        link_speed_gbps=_link_speed(title),
        form_factor=_form_factor(title),
        rpm=_rpm(title),
        cache_mb=_int_pattern(title, _CACHE),
        sector_format=_sector(title),
        recording_tech=_recording(title),
        security=_security(title),
        quantity=extract_quantity(title),
        # Brand satisfies the rung-1 brand gate, so it is identity evidence
        # and reads the masked text like the offer terms.
        brand=_first_pattern(masked, _BRANDS, 0.9),
        # Masked although it only ever vetoes: a line named in a comparison
        # ("comparable to IronWolf Pro") says nothing about this item, and
        # reading it would send every such Exos listing to review.
        family_mentions=_family_mentions(masked),
    )
