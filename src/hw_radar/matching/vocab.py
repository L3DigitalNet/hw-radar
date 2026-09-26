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

from hw_radar.matching.normalize import mask_reference_spans
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
        re.compile(r"\bfor parts\b|\bparts only\b|\bas[- ]is\b|\bnot working\b"),
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
    (re.compile(r"\bfactory sealed\b|(?<!like )\bnew\b"), "new", None, 0.8),
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

# Quantity: digit-FIRST forms only. The 'xN' form (e.g. 'x16') is deliberately
# unsupported — it collides with Seagate family names (Exos X16/X18/X24).
_QUANTITIES: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\blot of (\d{1,3})\b"), 0.95),
    (re.compile(r"\b(\d{1,3})[- ]pack\b"), 0.9),
    (re.compile(r"\bqty:? ?(\d{1,3})\b"), 0.9),
    (re.compile(r"\b(\d{1,3})\s?x\b"), 0.7),
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


def _quantity(title: str) -> Attribute[int] | None:
    for pattern, confidence in _QUANTITIES:
        m = pattern.search(title)
        if m:
            return Attribute(
                value=int(m.group(1)),
                confidence=confidence,
                layer=_LAYER,
                source_text=m.group(0),
            )
    return None


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
    Every other field stays None. Each field comes from the same helper call
    extract() makes, so the two can never disagree on a title."""
    condition, recert_channel = _condition(title)
    return ExtractedAttributes(
        condition=condition,
        recert_channel=recert_channel,
        packaging=_first_pattern(title, _PACKAGING, 0.85),
        warranty_months=_int_pattern(title, _WARRANTY_YEARS, scale=12),
        warranty_channel=_first_pattern(title, _WARRANTY_CHANNELS, 0.9),
    )


def extract(title: str) -> ExtractedAttributes:
    condition, recert_channel = _condition(title)
    return ExtractedAttributes(
        capacity_bytes=_capacity(title),
        interface=_first_pattern(title, _INTERFACES, 0.9),
        link_speed_gbps=_link_speed(title),
        form_factor=_form_factor(title),
        rpm=_rpm(title),
        cache_mb=_int_pattern(title, _CACHE),
        sector_format=_sector(title),
        recording_tech=_recording(title),
        security=_security(title),
        condition=condition,
        recert_channel=recert_channel,
        packaging=_first_pattern(title, _PACKAGING, 0.85),
        warranty_months=_int_pattern(title, _WARRANTY_YEARS, scale=12),
        warranty_channel=_first_pattern(title, _WARRANTY_CHANNELS, 0.9),
        quantity=_quantity(title),
        # Brand is identity evidence (it satisfies the rung-1 brand gate), so a
        # brand word cited in a reference span ("comparable to Seagate Exos")
        # must not supply it. Only brand is masked: the ladder uses the other
        # fields solely in hard-attribute vetoes, where a field read from a span
        # can send a match to review but can never create one.
        brand=_first_pattern(mask_reference_spans(title), _BRANDS, 0.9),
    )
