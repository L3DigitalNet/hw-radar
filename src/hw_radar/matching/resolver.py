"""DB-facing resolver service (spec C.3.3): runs the pure ladder against catalog
state and writes append-only listing_resolution edges. Implements
acquisition.contracts.ListingResolver; the poller injects it from MS-1b on.

Invariants:
- NEVER gates ingestion: any internal failure — ladder OR apply/DB — becomes a
  grain=none error edge (fallback write in a fresh transaction); pipeline
  .run_source keeps its belt-and-braces catch as the last resort.
- Error edges never demote: a matcher crash appends an audit edge but PRESERVES
  the listing's denormalized accepted state; evidence-based review/none
  outcomes clear it (the veto's job is keeping the listing out of the trusted
  read path).
- Exactly one current edge per listing, DB-enforced (is_current unique) and
  serialized via select_for_update on the listing row; the apply order is
  demote-old → insert-new → link-old.
- No per-poll edge spam: unchanged rung-0 accepts, unchanged misses, and
  REPEATED IDENTICAL errors write no new edge; distinct new errors do.
- Rung 0 prior = the listing's denorm fields (last accepted state): MS-1a
  persist upserts on (source_site, source_listing_key), so a re-observation IS
  the same row. Exception: an automated (rung 1-2) accept decided under an
  older MATCHER_VERSION is re-decided by the full ladder, and the new edge
  records `reconsidered_from_matcher_version`; manual accepts always inherit.
- Single normalizer: all alias joins ride matching.normalize (ADR-0019 rule 1).
- Lazy alias learning (rule 7): dual-labeled listings emit listing_derived OEM
  aliases at MODEL grain max; house SKUs become source-local aliases.
- Category dispatch (MS2-D-02/-03): the latest snapshot's category hint picks
  the matching.categories rules and the _SPEC_READERS entry; no hint is the
  legacy drive default. A category without registered rules gets an
  `unsupported_category` none-edge and never runs drive rules. Every edge the
  ladder path writes records `category` and `category_source`, and a change of
  `category` alone is a decision-input change that writes a new edge.
- Category gates (MS2-D-05/-21), applied to the ladder's verdict in this order:
  cross-category guard (an accept whose target family belongs to another
  category), the category's AcceptancePolicy, then its auto_accept flag. Each
  failure turns the accept into `review` — never `none` — so the collision stays
  visible in the review queue. Drive has no policy and auto_accept on, so for
  drive only the guard can fire, and only on a non-drive target."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from decimal import Decimal
from typing import Final, Literal, cast

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from hw_radar.acquisition.contracts import CATEGORY_HINT_ATTR
from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    CpuSpec,
    DriveSpec,
    GpuSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    Packaging,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ProductVariant,
    RamSpec,
    RecertChannel,
    ResolutionGrain,
    ResolutionMethod,
    RetentionClass,
    WarrantyChannel,
)
from hw_radar.matching import MATCHER_VERSION, categories, ladder
from hw_radar.matching.normalize import canonicalize_listing_text, canonicalize_title
from hw_radar.matching.rules import basic, cpu, gpu, ram
from hw_radar.matching.types import (
    DecodeResult,
    ExtractedAttributes,
    Grain,
    MpnCandidate,
    TokenKind,
)

logger = logging.getLogger(__name__)

_TB = Decimal(1_000_000_000_000)
_ALIAS_KINDS = (
    TokenKind.MANUFACTURER_MPN,
    TokenKind.OEM_PN,
    TokenKind.HOUSE_SKU,
    TokenKind.UNKNOWN_CODE,
)


def _spec_of(model: ProductModel | None) -> DriveSpec | None:
    if model is None:
        return None
    try:
        return model.drive_spec  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no OneToOne reverse-accessor stub
    except DriveSpec.DoesNotExist:
        return None


def _hard_attrs_from_spec(spec: DriveSpec | None) -> ladder.HardAttrs:
    if spec is None:
        return ladder.HardAttrs()
    interface_text = spec.interface.casefold()
    interface = next(
        (k for k in ("nvme", "sas", "sata", "scsi", "usb") if k in interface_text), None
    )
    form_text = spec.form_factor.casefold()
    form_factor = next((k for k in ("3.5", "2.5", "m.2") if k in form_text), None)
    return ladder.HardAttrs(
        capacity_bytes=int(spec.capacity_tb * _TB) if spec.capacity_tb is not None else None,
        interface=interface,
        form_factor=form_factor,
        sector_format=spec.sector_format.casefold() or None,
        security="sed" if spec.sed else None,
    )


def _family_key(family: ProductFamily | None) -> tuple[str, str] | None:
    if family is None:
        return None
    return (family.manufacturer.normalized_name, family.normalized_name)


def _drive_model_attrs(model: ProductModel | None) -> ladder.HardAttrs:
    # The family rides along even when the model has no DriveSpec: the title
    # family veto needs only the family, never the spec row.
    attrs = _hard_attrs_from_spec(_spec_of(model))
    return replace(attrs, family=_family_key(model.product_family if model else None))


def _family_agreement_attrs(family_id: int | None) -> ladder.HardAttrs:
    """C.3.2 agreement set: a family-grain target vetoes only on fields where
    ALL known specs under the family agree; disagreeing fields stay unknown.
    The family's own identity is always known, specs or not (a rung-2
    provisional family has none)."""

    if family_id is None:
        return ladder.HardAttrs()
    family = _family_key(
        ProductFamily.objects.select_related("manufacturer").filter(pk=family_id).first()
    )
    specs = [
        _hard_attrs_from_spec(spec)
        for spec in DriveSpec.objects.filter(product_model__product_family_id=family_id)
    ]
    if not specs:
        return ladder.HardAttrs(family=family)

    def agreed[T](values: set[T | None]) -> T | None:
        return next(iter(values)) if len(values) == 1 else None

    return ladder.HardAttrs(
        capacity_bytes=agreed({a.capacity_bytes for a in specs}),
        interface=agreed({a.interface for a in specs}),
        form_factor=agreed({a.form_factor for a in specs}),
        sector_format=agreed({a.sector_format for a in specs}),
        security=agreed({a.security for a in specs}),
        family=family,
    )


@dataclass(frozen=True)
class _SpecReader:
    """The ORM-bound half of a category's rules: catalog spec rows → the
    ladder's HardAttrs, for a model-grain target and a family agreement set."""

    model_attrs: Callable[[ProductModel | None], ladder.HardAttrs]
    family_attrs: Callable[[int | None], ladder.HardAttrs]


def _known(value: str) -> str | None:
    """A satellite choice column as a veto value: "unknown" and blank mean the
    source did not state it, which must read as None (cannot veto)."""
    return value if value and value != "unknown" else None


def _gpu_hard(spec: GpuSpec) -> gpu.GpuHard:
    return gpu.GpuHard(
        chip_vendor=_known(spec.chip_vendor),
        vram_gb=spec.vram_gb,
        interface=_known(spec.interface),
        cooling=_known(spec.cooling),
    )


def _ram_hard(spec: RamSpec) -> ram.RamHard:
    return ram.RamHard(
        generation=_known(spec.generation),
        module_type=_known(spec.module_type),
        ecc=spec.ecc,
        module_capacity_gb=spec.module_capacity_gb,
        speed_mts=spec.speed_mts,
        ranks=spec.ranks,
    )


def _cpu_hard(spec: CpuSpec) -> cpu.CpuHard:
    # Compared in socket_key form on both sides; see matching.rules.cpu.
    return cpu.CpuHard(socket=cpu.socket_key(spec.socket) or None, cores=spec.cores)


def _agreed[P: ladder.CategoryHardAttrs](payloads: list[P]) -> P | None:
    """The C.3.2 agreement set for a category payload: each field keeps its value
    only where every spec in the family agrees; disagreeing fields become None."""
    if not payloads:
        return None
    first = payloads[0]
    return replace(
        first,
        **{
            f.name: getattr(first, f.name)
            if all(getattr(p, f.name) == getattr(first, f.name) for p in payloads)
            else None
            for f in fields(first)
        },
    )


def _satellite_reader[S: (GpuSpec, RamSpec, CpuSpec)](
    satellite: type[S], accessor: str, to_hard: Callable[[S], ladder.CategoryHardAttrs]
) -> _SpecReader:
    """Spec reader for a first-class satellite: the typed payload rides on
    `HardAttrs.category`, and the drive fields stay None so drive's
    `contradictions` could never read them even if misrouted."""

    def model_attrs(model: ProductModel | None) -> ladder.HardAttrs:
        if model is None:
            return ladder.HardAttrs()
        try:
            spec = cast("S", getattr(model, accessor))
        except satellite.DoesNotExist:  # pyright: ignore[reportAttributeAccessIssue] - django-types has no per-model DoesNotExist on a TypeVar-bound class
            return ladder.HardAttrs()
        return ladder.HardAttrs(category=to_hard(spec))

    def family_attrs(family_id: int | None) -> ladder.HardAttrs:
        if family_id is None:
            return ladder.HardAttrs()
        specs = satellite.objects.filter(product_model__product_family_id=family_id)
        return ladder.HardAttrs(category=_agreed([to_hard(spec) for spec in specs]))

    return _SpecReader(model_attrs=model_attrs, family_attrs=family_attrs)


# Basic-watch categories have no satellite: nothing on the catalog side can veto.
_NO_SPEC = _SpecReader(
    model_attrs=lambda _model: ladder.HardAttrs(),
    family_attrs=lambda _family_id: ladder.HardAttrs(),
)

# Keys must equal categories.registered_categories() (pinned by
# test_resolver_dispatch): a registered category without a reader would crash
# every resolution in it, and a reader without rules is dead code.
_SPEC_READERS: Final[dict[str, _SpecReader]] = {
    categories.DRIVE: _SpecReader(
        model_attrs=_drive_model_attrs,
        family_attrs=_family_agreement_attrs,
    ),
    gpu.SLUG: _satellite_reader(GpuSpec, "gpu_spec", _gpu_hard),
    ram.SLUG: _satellite_reader(RamSpec, "ram_spec", _ram_hard),
    cpu.SLUG: _satellite_reader(CpuSpec, "cpu_spec", _cpu_hard),
    **dict.fromkeys(basic.BASIC_CATEGORIES, _NO_SPEC),
}


def _latest_snapshot_attrs(listing: Listing) -> Mapping[str, object]:
    attrs = (  # pyright: ignore[reportUnknownVariableType] - django-types has no reverse-FK manager stub, propagated from the chained call below
        listing.snapshots.order_by("-observed_at")  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no reverse-FK manager stub
        .values_list("attrs_json", flat=True)
        .first()
    )
    if isinstance(attrs, dict):
        return cast("dict[str, object]", attrs)  # attrs_json is always a JSON object
    return {}


def _structured_mpn(attrs: Mapping[str, object]) -> str | None:
    value = attrs.get("mpn")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _category_hint(attrs: Mapping[str, object]) -> str | None:
    """The collector-asserted category persisted by persist.append_snapshot.

    Raises ValueError for a present but non-string value: a corrupted hint must
    surface as an error edge, not quietly dispatch to the legacy drive default."""

    value = attrs.get(CATEGORY_HINT_ATTR)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"non-string category hint: {value!r}")


def _current_edge(listing: Listing, *, for_update: bool = False) -> ListingResolution | None:
    queryset = listing.resolutions.filter(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no reverse-FK manager stub for ListingResolution.listing's related_name
        is_current=True
    )
    if for_update:
        queryset = queryset.select_for_update()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType] - queryset type is Unknown from the line above
    return queryset.first()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType] - queryset type is Unknown from the line above


def _prior_from_listing(listing: Listing, spec: _SpecReader) -> ladder.PriorResolution | None:
    """Rung-0 prior = the listing's DENORM fields — the last *accepted* state.

    Deliberately not the current edge: after a review/none/error edge the denorm
    is the accepted-state memory (error edges preserve it, evidence-based misses
    clear it — see _apply), so denorm-as-prior gives rung 0 exactly the C.3.2
    'already resolved' semantics."""

    if listing.resolution_grain == ResolutionGrain.NONE:  # pyright: ignore[reportUnnecessaryComparison] - basedpyright misreads a TextChoices member's runtime (value, label) tuple as its static type in `if` (not `assert`) context; ResolutionGrain.NONE IS the str "none" at runtime
        return None
    if listing.product_variant_id is not None:  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        model = listing.product_variant.product_model  # pyright: ignore[reportOptionalMemberAccess] - narrowed non-None by the product_variant_id check above; pyright can't cross-narrow FK from its _id sibling
        target = ladder.TargetRef(
            grain=Grain.VARIANT,
            family_id=model.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
            model_id=model.pk,
            variant_id=listing.product_variant_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        )
        hard = spec.model_attrs(model)
    elif listing.product_model_id is not None:  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        target = ladder.TargetRef(
            grain=Grain.MODEL,
            family_id=listing.product_model.product_family_id,  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - narrowed non-None by product_model_id above; product_family_id has no stub
            model_id=listing.product_model_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        )
        hard = spec.model_attrs(listing.product_model)
    elif listing.product_family_id is not None:  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        target = ladder.TargetRef(
            grain=Grain.FAMILY,
            family_id=listing.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        )
        hard = spec.family_attrs(
            listing.product_family_id  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        )
    else:
        return None
    return ladder.PriorResolution(
        target=target,
        confidence=listing.resolution_confidence or 0.5,
        hard_attrs=hard,
    )


# CR-003: an alias hit only counts when the ALIAS TYPE is compatible with the
# KIND of token that collided — a house SKU must not satisfy an MPN alias, an
# ASIN/other alias must never satisfy anything but its own kind. UNKNOWN_CODE
# may look up identifier-ish types but the ladder still demands brand evidence.
_COMPATIBLE_ALIAS_TYPES: dict[TokenKind, frozenset[str]] = {
    TokenKind.MANUFACTURER_MPN: frozenset({"mpn", "retail_pn", "region_pn"}),
    TokenKind.OEM_PN: frozenset({"oem_pn"}),
    TokenKind.HOUSE_SKU: frozenset({"other", "retail_pn"}),
    TokenKind.UNKNOWN_CODE: frozenset({"mpn", "oem_pn", "retail_pn", "region_pn"}),
}


def _alias_hits(
    candidates: list[MpnCandidate], source_site_id: int, spec: _SpecReader
) -> list[ladder.AliasHit]:
    by_key = {c.normalized: c for c in candidates if c.kind in _ALIAS_KINDS}
    if not by_key:
        return []
    rows = (
        ProductAlias.objects.filter(normalized_alias_text__in=list(by_key))
        .filter(Q(source_site__isnull=True) | Q(source_site_id=source_site_id))
        .select_related(
            "product_variant__product_model__manufacturer",
            "product_variant__product_model__product_family__manufacturer",
            "product_model__manufacturer",
            "product_model__product_family__manufacturer",
            "product_family__manufacturer",
        )
    )
    hits: list[ladder.AliasHit] = []
    for row in rows:
        candidate = by_key[row.normalized_alias_text]
        if row.alias_type not in _COMPATIBLE_ALIAS_TYPES.get(candidate.kind, frozenset()):
            continue
        if row.product_variant is not None:
            model = row.product_variant.product_model
            target = ladder.TargetRef(
                grain=Grain.VARIANT,
                family_id=model.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
                model_id=model.pk,
                variant_id=row.product_variant.pk,
            )
            brand: str | None = model.manufacturer.normalized_name
            hard = spec.model_attrs(model)
        elif row.product_model is not None:
            target = ladder.TargetRef(
                grain=Grain.MODEL,
                family_id=row.product_model.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
                model_id=row.product_model.pk,
            )
            brand = row.product_model.manufacturer.normalized_name
            hard = spec.model_attrs(row.product_model)
        else:
            target = ladder.TargetRef(
                grain=Grain.FAMILY,
                family_id=row.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
            )
            brand = (
                row.product_family.manufacturer.normalized_name
                if row.product_family is not None
                else None
            )
            hard = spec.family_attrs(
                row.product_family_id  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
            )
        hits.append(
            ladder.AliasHit(
                target=target,
                source_kind=row.source_kind,
                alias_type=row.alias_type,
                brand=brand,
                hard_attrs=hard,
                candidate_kind=candidate.kind,
                candidate_vendor=candidate.vendor_hint,
                candidate_structured=candidate.from_structured_field,
                candidate_normalized=candidate.normalized,
                candidate_review_only=candidate.review_only,
            )
        )
    return hits


def _first_decode(
    candidates: list[MpnCandidate], decode: Callable[[str], DecodeResult | None]
) -> DecodeResult | None:
    for candidate in candidates:
        if candidate.kind is TokenKind.OEM_PN:
            continue
        result = decode(candidate.normalized)
        if result is not None:
            return result
    return None


def _review(verdict: ladder.Verdict, **reason: object) -> ladder.Verdict:
    """Demote a gated ACCEPT to REVIEW, keeping the ladder's evidence and rung so
    the review queue shows what matched and which gate stopped it."""
    return ladder.Verdict(
        ladder.Outcome.REVIEW,
        Grain.NONE,
        rung=verdict.rung,
        evidence={**verdict.evidence, **reason},
    )


def _target_category(target: ladder.TargetRef) -> str:
    """The category a catalog target belongs to: its family's category. A
    family-less model predates categories — every MS-1 catalog row is a drive —
    so it reads as the legacy default, which keeps drive hits on those models
    untouched while a gpu/ram/cpu listing hitting one is still cross-category."""
    if target.family_id is None:
        return categories.LEGACY_DEFAULT_CATEGORY
    return ProductFamily.objects.values_list("category__slug", flat=True).get(pk=target.family_id)


@dataclass(frozen=True)
class _AcceptanceBasis:
    method: str
    source_kind: str | None


def _prior_basis(listing: Listing) -> _AcceptanceBasis | None:
    """What the listing's accepted state (the rung-0 prior) rests on: the latest
    non-error accept edge. A rung-0 edge carries its own basis forward in
    evidence, because its method is `source_alias` whatever it inherited."""
    edge = cast(
        "ListingResolution | None",
        listing.resolutions.filter(evidence__outcome=ladder.Outcome.ACCEPT.value)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no reverse-FK manager stub
        .exclude(evidence__has_key="error")
        .order_by("-resolved_at", "-pk")
        .first(),
    )
    if edge is None:
        return None
    source_kind = edge.evidence.get("alias_source_kind")
    method = edge.method
    if method == ResolutionMethod.SOURCE_ALIAS.value:
        method = edge.evidence.get("acceptance_basis", method)
    return _AcceptanceBasis(
        method=str(method), source_kind=source_kind if isinstance(source_kind, str) else None
    )


# Methods whose accept is a rule-derived, automated decision (rungs 1-2). Their
# correctness is only as good as the matcher_version that produced them; a
# MANUAL accept is an owner decision and no rule change may overturn it.
_AUTOMATED_METHODS: Final = frozenset(
    {ResolutionMethod.EXACT_ALIAS.value, ResolutionMethod.MPN_DECODE.value}
)


def _stale_prior_version(listing: Listing) -> str | None:
    """The older matcher_version the listing's accepted state was decided under,
    when that decision was automated; None when the prior may be inherited.

    The origin is the latest non-error accept edge that is NOT a rung-0
    `source_alias` edge: a rung-0 edge only re-stamps an inherited target, so its
    own matcher_version says nothing about which rules chose that target (and
    denorm is only ever set by an accept, so no fresh decision sits between the
    origin and later rung-0 edges). No origin edge at all — denorm written
    outside the resolver — is not provably automated and stays inheritable."""
    origin = cast(
        "ListingResolution | None",
        listing.resolutions.filter(evidence__outcome=ladder.Outcome.ACCEPT.value)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no reverse-FK manager stub
        .exclude(evidence__has_key="error")
        .exclude(method=ResolutionMethod.SOURCE_ALIAS.value)
        .order_by("-resolved_at", "-pk")
        .first(),
    )
    if origin is None or origin.method not in _AUTOMATED_METHODS:
        return None
    if origin.matcher_version == MATCHER_VERSION:
        return None
    return origin.matcher_version


def _apply_category_gates(
    listing: Listing, slug: str, rules: categories.CategoryRules, verdict: ladder.Verdict
) -> ladder.Verdict:
    """Cross-category guard, then AcceptancePolicy, then auto_accept (the module
    docstring's order). Only an ACCEPT is ever changed, and only toward REVIEW.

    Order matters: the guard runs first so a foreign target is reported as
    `cross_category` rather than as a policy miss, and the policy runs before
    auto_accept so a non-authoritative hit reads `acceptance_policy` even while
    auto-accept is off (MS2-D-21)."""
    target = verdict.target
    if verdict.outcome is not ladder.Outcome.ACCEPT or target is None:
        return verdict
    # A rung-2 provisional family is created under the dispatch category, so it
    # cannot be foreign; everything else points at an existing catalog row.
    if target.family_key is None:
        target_category = _target_category(target)
        if target_category != slug:
            return _review(verdict, cross_category={"target_category": target_category})
    policy = rules.acceptance
    if policy is None:
        return verdict
    if verdict.rung == 0:
        # Inherit only what the policy could have accepted itself, or an owner's
        # manual decision; any other prior (a pre-policy accept, a learned-alias
        # accept) is re-reviewed rather than trusted forever.
        basis = _prior_basis(listing)
        trusted = basis is not None and (
            basis.method == ResolutionMethod.MANUAL.value
            or (
                basis.method == ResolutionMethod.EXACT_ALIAS.value
                and basis.source_kind in policy.authoritative_source_kinds
            )
        )
        if basis is None or not trusted:
            return _review(
                verdict,
                acceptance_policy={
                    "source_kind": basis.source_kind if basis else None,
                    "grain": target.grain.value,
                    "prior_method": basis.method if basis else None,
                },
            )
        return replace(
            verdict,
            evidence={
                **verdict.evidence,
                "alias_source_kind": basis.source_kind,
                "acceptance_basis": basis.method,
            },
        )
    if verdict.rung != 1:
        # No other rung may accept under a policy (the new categories have no
        # grammar decode); fail closed rather than let a future rung slip past.
        return _review(verdict, acceptance_policy={"rung": verdict.rung})
    source_kind = verdict.winning_hit.source_kind if verdict.winning_hit else None
    if source_kind not in policy.authoritative_source_kinds or target.grain not in policy.grains:
        return _review(
            verdict,
            acceptance_policy={"source_kind": source_kind, "grain": target.grain.value},
        )
    if not rules.auto_accept:
        return _review(verdict, auto_accept_disabled=True, alias_source_kind=source_kind)
    return replace(verdict, evidence={**verdict.evidence, "alias_source_kind": source_kind})


def _run_ladder(
    listing: Listing, *, reconsider: bool = False
) -> tuple[str, ExtractedAttributes, list[MpnCandidate], ladder.Verdict]:
    canonical = canonicalize_listing_text(listing.title_raw, listing.condition_label_raw)
    attrs = _latest_snapshot_attrs(listing)
    hint = _category_hint(attrs)
    slug = categories.dispatch_category(hint)
    provenance: dict[str, object] = {
        "category": slug,
        "category_source": _category_source(hint),
    }
    rules = categories.rules_for(slug)
    if rules is None:
        # No rules for this category: record it and stop. Falling through to the
        # drive rules would let a GPU or RAM title alias-hit a drive model and
        # write drive price history. The prior is deliberately not consulted,
        # so an earlier drive accept is cleared rather than inherited.
        return (
            canonical,
            ExtractedAttributes(),
            [],
            ladder.Verdict(
                ladder.Outcome.NONE,
                Grain.NONE,
                evidence={**provenance, "unsupported_category": True},
            ),
        )
    spec = _SPEC_READERS[slug]
    structured_mpn = _structured_mpn(attrs)
    extracted = rules.extract(canonical)
    if rules.fold_structured is not None and structured_mpn is not None:
        extracted = rules.fold_structured(extracted, structured_mpn)
    candidates = rules.extract_candidates(
        canonical,
        structured_mpn=structured_mpn,
        source_key=listing.source_site.normalized_name,
    )
    # reconsider (C.3.4 catalog-refresh re-run): prior=None bypasses rung 0 so
    # rungs 1-2 get a shot at freshly seeded aliases — otherwise a family-grain
    # listing re-accepts its prior forever and the catalog seed can never
    # upgrade it. The veto still runs; unchanged outcomes write no edge.
    prior = None if reconsider else _prior_from_listing(listing, spec)
    # C.3.5: a MATCHER_VERSION bump is a re-resolution experiment, so an
    # automated accept decided under older rules is re-decided by the full
    # ladder instead of inherited. Without this, a rule fix (e.g. 2026.09.2's
    # ST…NM… no longer implying Exos) could never undo a false merge the old
    # rules made — rung 0 would re-accept it on every re-observation.
    stale_version = _stale_prior_version(listing) if prior is not None else None
    if stale_version is not None:
        prior = None
        provenance["reconsidered_from_matcher_version"] = stale_version
    verdict = ladder.decide(
        extracted,
        candidates,
        prior,
        _alias_hits(
            candidates,
            listing.source_site_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
            spec,
        ),
        _first_decode(candidates, rules.decode),
        veto=rules.veto,
        distinct_mpn_guard=rules.distinct_mpn_guard,
    )
    verdict = _apply_category_gates(listing, slug, rules, verdict)
    target = verdict.target
    if target is not None:
        # The ladder never names a category; stamp the dispatch category so
        # _materialize creates a provisional family where it was matched and
        # applies this category's variant_on_demand setting.
        verdict = replace(verdict, target=replace(target, category_slug=slug))
    verdict = replace(verdict, evidence={**verdict.evidence, **provenance})
    if reconsider:
        verdict = replace(verdict, evidence={**verdict.evidence, "reconsider": True})
    return canonical, extracted, candidates, verdict


def _category_source(hint: str | None) -> Literal["hint", "legacy_default"]:
    return "legacy_default" if hint is None else "hint"


def _materialize(
    extracted: ExtractedAttributes, verdict: ladder.Verdict
) -> tuple[str, ProductFamily | None, ProductModel | None, ProductVariant | None, bool]:
    """Turn an ACCEPT verdict's TargetRef into live rows. Returns
    (grain, family, model, variant, variant_created_on_demand)."""

    if verdict.outcome is not ladder.Outcome.ACCEPT or verdict.target is None:
        return ResolutionGrain.NONE, None, None, None, False
    target = verdict.target
    family: ProductFamily | None = None
    model: ProductModel | None = None
    variant: ProductVariant | None = None
    if target.family_key is not None:
        if target.category_slug is None:
            # Raising routes to resolve_listing's error-edge fallback. Defaulting
            # to drive here would re-create the hard-coded drive path dispatch
            # replaced, silently filing another category's family under drive.
            raise ValueError(f"provisional family {target.family_key!r} has no category")
        vendor, family_name = target.family_key
        manufacturer, _ = Manufacturer.objects.get_or_create(
            normalized_name=vendor,
            defaults={"name": vendor.replace("_", " ").title()},
        )
        family, _ = ProductFamily.objects.get_or_create(
            manufacturer=manufacturer,
            normalized_name=canonicalize_title(family_name),
            defaults={
                "category": Category.objects.get(slug=target.category_slug),
                "name": family_name.title(),
            },
        )
    elif target.grain is Grain.FAMILY and target.family_id is not None:
        family = ProductFamily.objects.get(pk=target.family_id)
    if target.model_id is not None:
        model = ProductModel.objects.get(pk=target.model_id)
    if target.variant_id is not None:
        variant = ProductVariant.objects.get(pk=target.variant_id)
    grain: str = ResolutionGrain(target.grain.value)
    on_demand = False
    rules = categories.rules_for(target.category_slug) if target.category_slug else None
    # A target without a stamped category (only direct callers, never
    # _run_ladder) keeps the historical drive behavior of creating variants.
    variant_on_demand = rules.variant_on_demand if rules is not None else True
    if (
        variant_on_demand
        and grain == ResolutionGrain.MODEL
        and model is not None
        and extracted.condition is not None
    ):
        # C.3.3: variant rows are created on demand once model grain + normalized
        # condition are both known — the sellable identity materializes here.
        variant, _created = ProductVariant.objects.get_or_create(
            product_model=model,
            condition=extracted.condition.value,
            packaging=(extracted.packaging.value if extracted.packaging else Packaging.UNKNOWN),
            recert_channel=(
                extracted.recert_channel.value
                if extracted.recert_channel
                else RecertChannel.UNKNOWN
            ),
            warranty_channel=(
                extracted.warranty_channel.value
                if extracted.warranty_channel
                else WarrantyChannel.UNKNOWN
            ),
        )
        grain = ResolutionGrain.VARIANT
        on_demand = True
    return grain, family, model, variant, on_demand


def _emit_learned_aliases(
    listing: Listing, candidates: list[MpnCandidate], model_id: int | None
) -> None:
    if model_id is None:
        return
    has_manufacturer_token = any(c.kind is TokenKind.MANUFACTURER_MPN for c in candidates)
    # DR-001 stamp for every row this function creates (OQ22, owner-ratified
    # 2026-09-06): LISTING_DERIVED_ALIAS with expires_at NULL. The class is
    # indefinite because ADR-0019 rule 7's review queue only shrinks if learned
    # aliases survive, and it is deliberately NOT MANUFACTURER_REFERENCE — these
    # tokens are inferred from a merchant listing, not asserted by a datasheet,
    # and only refdata.persist may promote a row to the authoritative class. The
    # product_alias CHECK pair (migration 0017) rejects an unstamped insert, so
    # both branches below must carry these two keys.
    for candidate in candidates:
        if candidate.kind is TokenKind.OEM_PN and has_manufacturer_token:
            # Dual-labeled listing: OEM token + resolved MPN → learned alias at
            # MODEL grain max (the N:N verdict, ADR-0019 rule 7).
            ProductAlias.objects.get_or_create(
                alias_type=AliasType.OEM_PN,
                normalized_alias_text=candidate.normalized,
                source_site=None,
                product_model_id=model_id,
                product_family=None,
                defaults={
                    "source_kind": AliasSourceKind.LISTING_DERIVED,
                    "retention_class": RetentionClass.LISTING_DERIVED_ALIAS,
                    "expires_at": None,
                },
            )
        elif candidate.kind is TokenKind.HOUSE_SKU:
            ProductAlias.objects.get_or_create(
                alias_type=AliasType.OTHER,
                normalized_alias_text=candidate.normalized,
                source_site=listing.source_site,
                defaults={
                    "product_model_id": model_id,
                    "source_kind": AliasSourceKind.LISTING_DERIVED,
                    "retention_class": RetentionClass.LISTING_DERIVED_ALIAS,
                    "expires_at": None,
                },
            )


def _error_verdict(exc: Exception) -> ladder.Verdict:
    return ladder.Verdict(
        outcome=ladder.Outcome.NONE, grain=Grain.NONE, evidence={"error": repr(exc)}
    )


def _product_model_id(obj: ListingResolution | ProductVariant | None) -> int | None:
    """Typed boundary for the product_model_id shadow attribute: django-types has
    no <field>_id stubs, so the attribute access resolves as Unknown. A declared
    variable annotation at the call site does NOT stop that Unknown from
    propagating into a downstream typed call (verified empirically) — only a
    function return-type boundary does. Centralizing the single unavoidable
    ignore here keeps it out of _apply's control flow."""

    if obj is None:
        return None
    return obj.product_model_id  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs


def _stamp_evaluated(current: ListingResolution) -> None:
    current.last_evaluated_at = timezone.now()
    current.save(update_fields=["last_evaluated_at"])


def _edge_target_ids(edge: ListingResolution) -> tuple[int | None, int | None, int | None]:
    return (edge.product_family_id, edge.product_model_id, edge.product_variant_id)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs


@transaction.atomic
def _apply(
    listing: Listing,
    canonical: str,
    extracted: ExtractedAttributes,
    candidates: list[MpnCandidate],
    verdict: ladder.Verdict,
) -> None:
    # CR-002: serialize concurrent resolutions of one listing — lock the listing
    # row, then the current edge, inside this transaction.
    locked = Listing.objects.select_for_update().select_related("source_site").get(pk=listing.pk)
    current = _current_edge(locked, for_update=True)
    is_error = "error" in verdict.evidence
    # An unchanged rung-0 accept skips the write — UNLESS the current edge is an
    # error edge, in which case the clean re-accept must supersede it so the
    # ledger's current converges back to the accepted state.
    unchanged_accept = (
        verdict.outcome is ladder.Outcome.ACCEPT
        and verdict.rung == 0
        and current is not None
        and "error" not in current.evidence
    )
    unchanged_miss = (
        verdict.outcome is not ladder.Outcome.ACCEPT
        and not is_error
        and current is not None
        and current.grain == ResolutionGrain.NONE  # pyright: ignore[reportUnnecessaryComparison] - basedpyright misreads a TextChoices member's runtime (value, label) tuple as its static type in `if`/boolean-expr (not `assert`) context
        and "error" not in current.evidence
        and current.evidence.get("outcome") == verdict.outcome
    )
    unchanged_error = (
        is_error
        and current is not None
        and current.evidence.get("error") == verdict.evidence.get("error")
    )
    # A changed dispatch category is a changed decision input even when the
    # outcome is not: without a new edge, a listing whose drive-era `none` edge
    # predates its gpu hint would keep reporting category=drive forever. Edges
    # written before category provenance existed were all drive decisions, so a
    # missing key reads as the legacy default and drive re-polls stay silent.
    # Error verdicts carry no category and stay governed by unchanged_error.
    category_changed = (
        current is not None
        and "error" not in current.evidence
        and "category" in verdict.evidence
        and current.evidence.get("category", categories.LEGACY_DEFAULT_CATEGORY)
        != verdict.evidence["category"]
    )
    if ((unchanged_accept or unchanged_miss) and not category_changed) or unchanged_error:
        # Routine re-poll with an unchanged outcome: no edge spam
        # (append-only ≠ append-always). Distinct NEW errors DO append (CR-001).
        # But freshness IS recorded (MS-1b carry-forward): a long-lived miss
        # only looks re-examined when something actually re-ran the ladder.
        if current is not None:
            _stamp_evaluated(current)
        if canonical and locked.title_normalized != canonical:
            locked.title_normalized = canonical
            locked.save(update_fields=["title_normalized"])
        return
    accepted = verdict.outcome is ladder.Outcome.ACCEPT
    if accepted:
        grain, family, model, variant, on_demand = _materialize(extracted, verdict)
    else:
        # Non-accept (incl. error) edges never materialize identity rows — this
        # also keeps the CR-001 fallback error-write free of _materialize.
        grain, family, model, variant, on_demand = ResolutionGrain.NONE, None, None, None, False
    # A version re-decision that lands on the same target still writes an edge:
    # it records the decision under the current rules, which is what lets the
    # NEXT re-observation inherit at rung 0 — skipping it would re-run the full
    # ladder on every poll, and leave no diffable trace of the re-resolution.
    reconsidered = "reconsidered_from_matcher_version" in verdict.evidence
    if (
        accepted
        and current is not None
        and "error" not in current.evidence
        and not category_changed
        and not reconsidered
    ):
        new_targets = (
            family.pk if grain == ResolutionGrain.FAMILY and family is not None else None,
            model.pk if grain == ResolutionGrain.MODEL and model is not None else None,
            variant.pk if grain == ResolutionGrain.VARIANT and variant is not None else None,
        )
        if current.grain == grain and _edge_target_ids(current) == new_targets:
            # Same accept, same target (a reconsider re-hit, or a rung-1 accept
            # identical to the current edge): freshness only, no new edge.
            _stamp_evaluated(current)
            if canonical and locked.title_normalized != canonical:
                locked.title_normalized = canonical
                locked.save(update_fields=["title_normalized"])
            return
    evidence: dict[str, object] = {**verdict.evidence, "outcome": verdict.outcome}
    if verdict.rung is not None:
        evidence["rung"] = verdict.rung
    if on_demand:
        evidence["variant_on_demand"] = True
    if is_error and locked.resolution_grain != ResolutionGrain.NONE:  # pyright: ignore[reportUnnecessaryComparison] - basedpyright misreads a TextChoices member's runtime (value, label) tuple as its static type in `if` (not `assert`) context
        evidence["denorm_preserved"] = True
    # CR-002 ordering: demote-old → insert-new → link-old. The one-current
    # unique constraint must hold at every individual statement.
    if current is not None:
        current.is_current = False
        current.save(update_fields=["is_current"])
    edge = ListingResolution.objects.create(
        listing=locked,
        grain=grain,
        product_family=family if grain == ResolutionGrain.FAMILY else None,
        product_model=model if grain == ResolutionGrain.MODEL else None,
        product_variant=variant if grain == ResolutionGrain.VARIANT else None,
        method=verdict.method if accepted else "",
        confidence=verdict.confidence if accepted else None,
        matcher_version=MATCHER_VERSION,
        evidence=evidence,
        resolved_at=timezone.now(),
    )
    if current is not None:
        current.superseded_by = edge
        current.save(update_fields=["superseded_by"])
    # Denorm refresh (C.3.3 "refreshed on accept"), by outcome:
    #  accept       → point at the target;
    #  review/none  → CLEAR — an evidence-based miss (incl. veto trip) must pull
    #                 the listing out of the trusted-resolution read path;
    #  error        → PRESERVE the last accepted state — a matcher crash says
    #                 nothing about the listing; the edge records the failure
    #                 (CR-001: audit trail without demotion).
    locked.title_normalized = canonical or locked.title_normalized
    if is_error:
        locked.save(update_fields=["title_normalized"])
        return
    locked.resolution_grain = grain
    locked.resolution_confidence = edge.confidence
    locked.product_family = edge.product_family
    locked.product_model = edge.product_model
    locked.product_variant = edge.product_variant
    locked.save(
        update_fields=[
            "title_normalized",
            "resolution_grain",
            "resolution_confidence",
            "product_family",
            "product_model",
            "product_variant",
        ]
    )
    if accepted:
        edge_model_id = _product_model_id(edge)
        resolved_model_id = (
            edge_model_id if edge_model_id is not None else _product_model_id(variant)
        )
        _emit_learned_aliases(locked, candidates, resolved_model_id)


class CatalogResolver:
    """The ADR-0019 resolver service. Stateless — safe to construct per call."""

    def resolve_listing(self, listing_id: int, *, reconsider: bool = False) -> None:
        listing = Listing.objects.select_related("source_site").get(pk=listing_id)
        try:
            canonical, extracted, candidates, verdict = _run_ladder(listing, reconsider=reconsider)
        except Exception as exc:  # ladder failure → error verdict (C.3)
            logger.exception("matcher crashed for listing %s", listing_id)
            canonical, extracted, candidates = "", ExtractedAttributes(), []
            verdict = _error_verdict(exc)
        try:
            _apply(listing, canonical, extracted, candidates, verdict)
        except Exception as exc:
            # CR-001: an apply/materialize/DB failure must still leave a trace in
            # the resolution ledger — fall back to a bare error edge in a fresh
            # transaction. If even THAT fails, propagate: pipeline.run_source
            # counts it as a resolver_error and never blocks ingestion.
            logger.exception("resolution apply failed for listing %s", listing_id)
            _apply(listing, canonical, ExtractedAttributes(), [], _error_verdict(exc))
