"""Basic-watch rules for `nic`, `hba`, `motherboard`, and `server` (MS2-D-05,
ADR 0022 R-MS2-01): exact curated aliases only.

These categories have no spec satellite, so there is nothing to extract beyond
offer terms and brand and nothing to veto; a listing reaches a product only
through an exact `product_alias` hit that passes the acceptance policy. Brand
tables are per category because the same word means different makers in
different categories (Intel makes NICs and boards; Dell makes servers and
rebadged HBAs).

`server` differs in one respect, set in `categories`: `variant_on_demand=False`,
so an accepted server stays at model grain and differently configured systems
never collapse into one condition variant (ADR 0022, D11).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from typing import Final

from hw_radar.matching import vocab
from hw_radar.matching.ladder import HardAttrs
from hw_radar.matching.rules import (
    CandidateSet,
    add_code_tokens,
    add_house_skus,
    add_structured,
    sole_pattern,
)
from hw_radar.matching.types import ExtractedAttributes, MpnCandidate, TokenKind

type _BrandTable = tuple[tuple[re.Pattern[str], str], ...]

_BRANDS: Final[dict[str, _BrandTable]] = {
    "nic": (
        (re.compile(r"\bintel\b"), "intel"),
        (re.compile(r"\bmellanox\b|\bconnectx\b"), "mellanox"),
        (re.compile(r"\bbroadcom\b"), "broadcom"),
        (re.compile(r"\bchelsio\b"), "chelsio"),
        (re.compile(r"\bsolarflare\b"), "solarflare"),
    ),
    "hba": (
        (re.compile(r"\bbroadcom\b|\blsi\b|\bavago\b"), "broadcom"),
        (re.compile(r"\badaptec\b|\bmicrochip\b"), "microchip"),
        (re.compile(r"\batto\b"), "atto"),
    ),
    "motherboard": (
        (re.compile(r"\bsupermicro\b"), "supermicro"),
        (re.compile(r"\basrock\s+rack\b"), "asrock_rack"),
        (re.compile(r"\basus\b"), "asus"),
        (re.compile(r"\bgigabyte\b"), "gigabyte"),
        (re.compile(r"\btyan\b"), "tyan"),
    ),
    "server": (
        (re.compile(r"\bdell\b|\bpoweredge\b"), "dell"),
        (re.compile(r"\bhpe\b|\bproliant\b"), "hpe"),
        (re.compile(r"\blenovo\b|\bthinksystem\b"), "lenovo"),
        (re.compile(r"\bsupermicro\b"), "supermicro"),
        (re.compile(r"\bcisco\b"), "cisco"),
    ),
}
BASIC_CATEGORIES: Final = tuple(_BRANDS)

_VOCAB_TAILS = re.compile(r"(?:\d(?:gb|tb|mb|w)|gb/s|gbe)$")


def extractor(slug: str) -> Callable[[str], ExtractedAttributes]:
    """The `extract` for one basic-watch category: offer terms plus its brand."""
    brands = _BRANDS[slug]

    def extract(title: str) -> ExtractedAttributes:
        return replace(vocab.offer_terms(title), brand=sole_pattern(title, brands, 0.9))

    return extract


def extract_candidates(
    title: str, *, structured_mpn: str | None = None, source_key: str = ""
) -> list[MpnCandidate]:
    """Structured MPN, house SKUs, and code-shaped tokens only. They carry no
    vendor hint, so the ladder accepts them only with an extracted brand or a
    structured field as brand evidence."""
    out = CandidateSet()
    add_structured(out, structured_mpn, TokenKind.UNKNOWN_CODE, "")
    add_house_skus(out, title, source_key)
    add_code_tokens(out, title, _VOCAB_TAILS)
    return out.result()


def veto(_extracted: ExtractedAttributes, _catalog: HardAttrs) -> list[str]:
    """No satellite, so no catalog field can contradict the listing."""
    return []
