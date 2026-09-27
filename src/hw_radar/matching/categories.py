"""Category rules registry (MS2-D-02/-05/-21): maps a category slug to the pure
matching functions and acceptance settings the resolver runs for that category.
Pure — no ORM, no Django, no acquisition imports — so `acquisition.contracts`
can import the slug pattern without an import cycle.

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
- `drive` is bound to the existing ADR-0019 objects BY IDENTITY (`vocab.extract`,
  `mpn.extract_candidates`, `grammars.decode`, `ladder.contradictions`) with no
  acceptance policy: dispatch is a seam, not a second drive matcher, and the A0
  baseline pins that drive decisions never move.
- `gpu`, `ram`, `cpu` and the basic-watch categories (`nic`, `hba`,
  `motherboard`, `server`) run their `matching.rules` modules under
  NEW_CATEGORY_ACCEPTANCE: only a `catalog_authoritative` alias at model or
  variant grain may accept (MS2-D-21). gpu/ram ship with `auto_accept=False`
  until an owner-ratified category corpus exists (MS2-D-05, risk R4), so even
  an authoritative hit is `review`. The basic-watch categories keep
  `auto_accept=True`: the plan scopes the disabled flag to gpu/ram/cpu, and
  their exact curated aliases are gated by the policy alone.
- A corpus ratifies only the families it measured (OQ34, owner 2026-09-26).
  `ratified_families` narrows auto-accept to named `FamilyKey`s; cpu ratifies
  AMD EPYC alone, so an authoritative Intel Xeon hit is `review` with
  `family_not_ratified`. A condition variant belongs to its model's family, so
  it inherits that family's ratification. A future Xeon corpus adds a key here;
  it never needs a category-wide flip.

Reserved prefix: slugs starting `zz-` are test sentinels (e.g. `zz-unregistered`)
and must never be registered. The unsupported-category tests rely on a slug that
is guaranteed to stay unregistered; if a real category took their slug they
would silently pass for the wrong reason.

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
from hw_radar.matching.normalize import canonicalize_title
from hw_radar.matching.rules import basic, cpu, gpu, no_decode, ram
from hw_radar.matching.types import DecodeResult, ExtractedAttributes, Grain, MpnCandidate
from hw_radar.refdata.contracts import MANUFACTURER_KEY_RE

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
class AcceptancePolicy:
    """Which rung-1 hits may stand as ACCEPT for a category (MS2-D-21): the
    winning alias's `source_kind` must be authoritative and the target grain
    approved. The resolver applies it after `ladder.decide` and before the
    `auto_accept` flag; failures become `review`, never `none`, so a collision
    with a manual or learned alias stays visible."""

    authoritative_source_kinds: frozenset[str]
    grains: frozenset[Grain]


NEW_CATEGORY_ACCEPTANCE: Final = AcceptancePolicy(
    authoritative_source_kinds=frozenset({"catalog_authoritative"}),
    grains=frozenset({Grain.MODEL, Grain.VARIANT}),
)


@dataclass(frozen=True, order=True)
class FamilyKey:
    """A product family's stable logical identity: `Manufacturer.normalized_name`
    plus `ProductFamily.normalized_name`, the pair `product_family_unique_per_mfr`
    makes unique. Never a primary key: a ratification must name the same family
    in every environment and survive a catalog rebuild.

    Both halves must already be in normalized form (build one with `of`). The
    resolver compares keys by exact equality, so an un-normalized key could only
    ever fail to match; rejecting it at import makes that typo loud instead of
    silently leaving a ratified family in review. Equality is also the whole
    guard against look-alikes: `amd/epyc` is not `intel/epyc`, and `epyc` is not
    `epyc embedded`."""

    manufacturer: str
    family: str

    def __post_init__(self) -> None:
        if not MANUFACTURER_KEY_RE.fullmatch(self.manufacturer):
            raise ValueError(f"manufacturer {self.manufacturer!r} is not a normalized key")
        if not self.family or self.family != canonicalize_title(self.family):
            raise ValueError(f"family {self.family!r} is not a normalized family name")

    @classmethod
    def of(cls, manufacturer_key: str, family_name: str) -> FamilyKey:
        """Key a seed's `manufacturer_key` and display `family_name`, normalized
        exactly as refdata.persist normalizes the family row it creates."""
        return cls(manufacturer_key, canonicalize_title(family_name))


@dataclass(frozen=True)
class RatifiedFamilies:
    """The families an owner-ratified corpus measured (OQ34). Non-empty by
    construction: "no family ratified" is `auto_accept=False`, never an empty
    set, so an empty allowlist can't be misread as "no restriction"."""

    keys: frozenset[FamilyKey]

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError("RatifiedFamilies needs at least one family; use auto_accept=False")

    def permits(self, key: FamilyKey | None) -> bool:
        return key is not None and key in self.keys


@dataclass(frozen=True)
class CategoryRules:
    slug: str
    extract: Callable[[str], ExtractedAttributes]
    extract_candidates: CandidateExtractor
    decode: Callable[[str], DecodeResult | None]
    veto: ladder.Veto
    # False turns every accept that survives the policy into `review`
    # (`auto_accept_disabled`). Flipping it needs a ratified category corpus.
    auto_accept: bool = True
    # None = every family auto_accept allows. Otherwise only these families may
    # auto-accept; any other policy-passing hit is `review` with
    # `family_not_ratified` (OQ34). Needs an AcceptancePolicy and auto_accept on:
    # drive has no policy, and a scope under a disabled flag would be dead.
    ratified_families: RatifiedFamilies | None = None
    # False keeps an accepted model-grain listing at model grain instead of
    # creating a condition variant; `server` configurations are not variants.
    variant_on_demand: bool = True
    # None = no acceptance gate. Drive only: its decisions are pinned by A0.
    acceptance: AcceptancePolicy | None = None
    # Folds the merchant-asserted structured MPN (snapshot attrs `mpn`) into the
    # title-extracted attributes, for categories whose veto must see it; the
    # resolver calls it only when the field is present. None = title only.
    # `extract` keeps its title-only signature because drive binds
    # `vocab.extract` by identity and the eligibility evaluator calls it too.
    fold_structured: Callable[[ExtractedAttributes, str], ExtractedAttributes] | None = None
    # ladder.decide's distinct-MPN guard: review an accept whose title names
    # MPNs of more than one model. Drive only; the other extractors emit
    # several MPN-kind candidates for one product (see ladder.decide).
    distinct_mpn_guard: bool = False

    def __post_init__(self) -> None:
        if self.ratified_families is not None and (self.acceptance is None or not self.auto_accept):
            raise ValueError(
                f"{self.slug}: ratified_families needs an AcceptancePolicy and auto_accept=True"
            )


# OQ34 (owner, 2026-09-26): the EPYC corpus ratifies AMD EPYC and nothing else.
# Intel Xeon stays review until its own corpus is audited; it is not listed here.
CPU_RATIFIED_FAMILIES: Final = RatifiedFamilies(frozenset({FamilyKey.of("amd", "EPYC")}))


def _drive_rules() -> CategoryRules:
    return CategoryRules(
        slug=DRIVE,
        extract=vocab.extract,
        extract_candidates=mpn.extract_candidates,
        decode=grammars.decode,
        veto=ladder.contradictions,
        distinct_mpn_guard=True,
    )


def _gpu_rules() -> CategoryRules:
    return CategoryRules(
        slug=gpu.SLUG,
        extract=gpu.extract,
        extract_candidates=gpu.extract_candidates,
        decode=no_decode,
        veto=gpu.veto,
        auto_accept=False,
        acceptance=NEW_CATEGORY_ACCEPTANCE,
    )


def _ram_rules() -> CategoryRules:
    return CategoryRules(
        slug=ram.SLUG,
        extract=ram.extract,
        extract_candidates=ram.extract_candidates,
        decode=no_decode,
        veto=ram.veto,
        auto_accept=False,
        acceptance=NEW_CATEGORY_ACCEPTANCE,
    )


def _cpu_rules() -> CategoryRules:
    return CategoryRules(
        slug=cpu.SLUG,
        extract=cpu.extract,
        extract_candidates=cpu.extract_candidates,
        decode=no_decode,
        veto=cpu.veto,
        ratified_families=CPU_RATIFIED_FAMILIES,
        fold_structured=cpu.with_structured_mpn,
        acceptance=NEW_CATEGORY_ACCEPTANCE,
    )


def _basic_rules(slug: str) -> Callable[[], CategoryRules]:
    def factory() -> CategoryRules:
        return CategoryRules(
            slug=slug,
            extract=basic.extractor(slug),
            extract_candidates=basic.extract_candidates,
            decode=no_decode,
            veto=basic.veto,
            variant_on_demand=slug != "server",
            acceptance=NEW_CATEGORY_ACCEPTANCE,
        )

    return factory


# Factories, not prebuilt CategoryRules: the module attributes are read on every
# rules_for call, so a patched `vocab.extract` (the resolver's crash-path tests
# patch it) reaches the resolver exactly as the direct call it replaced did. A
# prebuilt instance would freeze the import-time function objects and silently
# route around any such patch.
_REGISTRY: Final[dict[str, Callable[[], CategoryRules]]] = {
    DRIVE: _drive_rules,
    gpu.SLUG: _gpu_rules,
    ram.SLUG: _ram_rules,
    cpu.SLUG: _cpu_rules,
    **{slug: _basic_rules(slug) for slug in basic.BASIC_CATEGORIES},
}


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
