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
  the same row.
- Single normalizer: all alias joins ride matching.normalize (ADR-0019 rule 1).
- Lazy alias learning (rule 7): dual-labeled listings emit listing_derived OEM
  aliases at MODEL grain max; house SKUs become source-local aliases."""

from __future__ import annotations

import logging
from dataclasses import replace
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from hw_radar.catalog.models import (
    AliasSourceKind,
    AliasType,
    Category,
    DriveSpec,
    Listing,
    ListingResolution,
    Manufacturer,
    Packaging,
    ProductAlias,
    ProductFamily,
    ProductModel,
    ProductVariant,
    RecertChannel,
    ResolutionGrain,
    RetentionClass,
    WarrantyChannel,
)
from hw_radar.matching import MATCHER_VERSION, ladder, mpn, vocab
from hw_radar.matching.grammars import decode
from hw_radar.matching.normalize import canonicalize_title
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


def _family_agreement_attrs(family_id: int | None) -> ladder.HardAttrs:
    """C.3.2 agreement set: a family-grain target vetoes only on fields where
    ALL known specs under the family agree; disagreeing fields stay unknown."""

    if family_id is None:
        return ladder.HardAttrs()
    specs = [
        _hard_attrs_from_spec(spec)
        for spec in DriveSpec.objects.filter(product_model__product_family_id=family_id)
    ]
    if not specs:
        return ladder.HardAttrs()

    def agreed[T](values: set[T | None]) -> T | None:
        return next(iter(values)) if len(values) == 1 else None

    return ladder.HardAttrs(
        capacity_bytes=agreed({a.capacity_bytes for a in specs}),
        interface=agreed({a.interface for a in specs}),
        form_factor=agreed({a.form_factor for a in specs}),
        sector_format=agreed({a.sector_format for a in specs}),
        security=agreed({a.security for a in specs}),
    )


def _structured_mpn(listing: Listing) -> str | None:
    attrs = (  # pyright: ignore[reportUnknownVariableType] - django-types has no reverse-FK manager stub, propagated from the chained call below
        listing.snapshots.order_by("-observed_at")  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no reverse-FK manager stub
        .values_list("attrs_json", flat=True)
        .first()
    )
    if isinstance(attrs, dict):
        value = attrs.get("mpn")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType] - attrs_json is dict[str, object], but attrs' own type is Unknown from the line above
        if isinstance(value, str) and value.strip():
            return value
    return None


def _current_edge(listing: Listing, *, for_update: bool = False) -> ListingResolution | None:
    queryset = listing.resolutions.filter(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no reverse-FK manager stub for ListingResolution.listing's related_name
        is_current=True
    )
    if for_update:
        queryset = queryset.select_for_update()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType] - queryset type is Unknown from the line above
    return queryset.first()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType] - queryset type is Unknown from the line above


def _prior_from_listing(listing: Listing) -> ladder.PriorResolution | None:
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
        hard = _hard_attrs_from_spec(_spec_of(model))
    elif listing.product_model_id is not None:  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        target = ladder.TargetRef(
            grain=Grain.MODEL,
            family_id=listing.product_model.product_family_id,  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - narrowed non-None by product_model_id above; product_family_id has no stub
            model_id=listing.product_model_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        )
        hard = _hard_attrs_from_spec(_spec_of(listing.product_model))
    elif listing.product_family_id is not None:  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        target = ladder.TargetRef(
            grain=Grain.FAMILY,
            family_id=listing.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        )
        hard = _family_agreement_attrs(
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


def _alias_hits(candidates: list[MpnCandidate], source_site_id: int) -> list[ladder.AliasHit]:
    by_key = {c.normalized: c for c in candidates if c.kind in _ALIAS_KINDS}
    if not by_key:
        return []
    rows = (
        ProductAlias.objects.filter(normalized_alias_text__in=list(by_key))
        .filter(Q(source_site__isnull=True) | Q(source_site_id=source_site_id))
        .select_related(
            "product_variant__product_model__manufacturer",
            "product_variant__product_model__product_family",
            "product_model__manufacturer",
            "product_model__product_family",
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
            hard = _hard_attrs_from_spec(_spec_of(model))
        elif row.product_model is not None:
            target = ladder.TargetRef(
                grain=Grain.MODEL,
                family_id=row.product_model.product_family_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
                model_id=row.product_model.pk,
            )
            brand = row.product_model.manufacturer.normalized_name
            hard = _hard_attrs_from_spec(_spec_of(row.product_model))
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
            hard = _family_agreement_attrs(
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
            )
        )
    return hits


def _first_decode(candidates: list[MpnCandidate]) -> DecodeResult | None:
    for candidate in candidates:
        if candidate.kind is TokenKind.OEM_PN:
            continue
        result = decode(candidate.normalized)
        if result is not None:
            return result
    return None


def _run_ladder(
    listing: Listing, *, reconsider: bool = False
) -> tuple[str, ExtractedAttributes, list[MpnCandidate], ladder.Verdict]:
    canonical = canonicalize_title(f"{listing.title_raw} {listing.condition_label_raw}".strip())
    extracted = vocab.extract(canonical)
    candidates = mpn.extract_candidates(
        canonical,
        structured_mpn=_structured_mpn(listing),
        source_key=listing.source_site.normalized_name,
    )
    # reconsider (C.3.4 catalog-refresh re-run): prior=None bypasses rung 0 so
    # rungs 1-2 get a shot at freshly seeded aliases — otherwise a family-grain
    # listing re-accepts its prior forever and the catalog seed can never
    # upgrade it. The veto still runs; unchanged outcomes write no edge.
    prior = None if reconsider else _prior_from_listing(listing)
    verdict = ladder.decide(
        extracted,
        candidates,
        prior,
        _alias_hits(
            candidates,
            listing.source_site_id,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue] - django-types has no <field>_id shadow-attribute stubs
        ),
        _first_decode(candidates),
    )
    if reconsider:
        verdict = replace(verdict, evidence={**verdict.evidence, "reconsider": True})
    return canonical, extracted, candidates, verdict


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
        vendor, family_name = target.family_key
        manufacturer, _ = Manufacturer.objects.get_or_create(
            normalized_name=vendor,
            defaults={"name": vendor.replace("_", " ").title()},
        )
        family, _ = ProductFamily.objects.get_or_create(
            manufacturer=manufacturer,
            normalized_name=canonicalize_title(family_name),
            defaults={
                "category": Category.objects.get(slug="drive"),
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
    if grain == ResolutionGrain.MODEL and model is not None and extracted.condition is not None:
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
    if unchanged_accept or unchanged_miss or unchanged_error:
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
    if accepted and current is not None and "error" not in current.evidence:
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
