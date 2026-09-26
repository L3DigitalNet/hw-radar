"""Eligibility evaluator (MS-2 Slice C3; MS2-D-08, MS2-D-09, MS2-D-20, MS2-D-29).

Frozen public interface (C-wire's pipeline stage and `evaluate_watches` call
exactly these; change them only together with those callers):

- `evaluate_listing(listing_id) -> ListingEvaluationResult` evaluates one
  listing against every ENABLED watch of the listing's dispatch category and
  writes one `WatchEvaluation` per (watch, listing), overwriting in place. A
  delisted or expired listing is not evaluated (`skipped` says why) and its
  existing rows are left untouched: the read model excludes such listings by
  itself. It RAISES on failure (for example a malformed category hint); the
  caller counts the error and never blocks ingestion on it. Nothing is written
  for a listing whose evaluation raised, so its old rows stay non-current and
  fail closed to `pending` (MS2-D-20).
- `ListingEvaluator` is the protocol `run_collection(..., evaluator=None)`
  accepts; `WatchEvaluator` is the production implementation `None` binds.
- `catalog_inputs(listing) -> CatalogInputs` gathers everything the product
  clauses read, and `catalog_fingerprint(inputs)` hashes it (MS2-D-29). The
  evaluator and the read-time validity check both call these, so they cannot
  drift.
- `live_binding(listing, watch)` / `stored_binding(evaluation)` are the two
  sides of the MS2-D-20 currency predicate: a row is current iff they are
  equal. The evaluator stamps rows from `live_binding`, so a freshly written
  row is current by construction.

Semantics (MS2-D-08):
- Every clause yields `match | no_match | unknown` plus a structured reason.
  The aggregate is: any `no_match` => `no_match`; else any `unknown` =>
  `unknown`; else `match`. `unknown` never counts as `match` (FR-014).
- Product-attribute clauses (the category's requirement satellite) and the
  target clause read CATALOG-tier evidence only for `match`: the typed spec of
  the listing's current ACCEPTED resolution (model/variant grain), or the
  family agreement set (family grain). A review, none, or error edge — or no
  edge at all — supplies no catalog evidence, so those clauses are `unknown`.
  Under today's conservative new-category acceptance (auto_accept off for
  gpu/ram/cpu) that means gpu/ram/cpu listings are usually `unknown`; that is
  the intended outcome, not a defect.
- A LISTING-tier attribute (extracted from the title by the category's rules
  module) can only ever produce `no_match`, and only when it positively
  contradicts the requirement with confidence >= the category policy's
  threshold. Agreement at listing tier never produces `match`.
- Offer clauses (price, condition, stock, international) read the latest
  `OfferSnapshot` and the `Listing`.
- The soft `Watch.target_unit_price_usd` is never read here (MS2-D-07, R11).

Clocks (MS2-D-39): `evaluated_at` is processing time. Retention mirrors the
listing's class and `expires_at`, which the listing already derives from
observation time, so an evaluation (whose reasons carry eBay prices) expires
with the observation it describes and never outlives DR-008.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol, cast

from django.db import transaction
from django.utils import timezone

from hw_radar.acquisition.contracts import CATEGORY_HINT_ATTR
from hw_radar.catalog.models import (
    CpuRequirement,
    CpuSpec,
    DriveRequirement,
    DriveSpec,
    EligibilityVerdict,
    GpuRequirement,
    GpuSpec,
    Listing,
    ListingResolution,
    OfferSnapshot,
    ProductModel,
    RamRequirement,
    RamSpec,
    ResolutionGrain,
    StockStatus,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility.requirements import DRIVE_FORM_FACTOR_TOKENS, DRIVE_INTERFACE_TOKENS
from hw_radar.matching import categories, vocab
from hw_radar.matching.normalize import canonicalize_listing_text
from hw_radar.matching.rules.cpu import CpuAttributes, socket_key
from hw_radar.matching.rules.gpu import GpuAttributes
from hw_radar.matching.rules.ram import RamAttributes
from hw_radar.matching.types import Attribute, ExtractedAttributes

# Bump on ANY change to clause semantics, reason shape, or policy values: a
# stored row whose evaluator_version differs is non-current (MS2-D-20), so a
# bump makes every old verdict pending until re-evaluated. Forgetting it leaves
# verdicts computed under the old rules looking current.
EVALUATOR_VERSION: Final = "ms2c.2"
# Bump when the CatalogInputs shape or its canonical JSON changes, so every
# stored fingerprint stops matching instead of silently comparing across shapes.
CATALOG_INPUTS_VERSION: Final = "1"

_BYTES_PER_TB: Final = Decimal(10**12)

type Scalar = str | int | bool | None


class EvidenceTier(StrEnum):
    CATALOG = "catalog"
    LISTING = "listing"
    OFFER = "offer"
    # No evidence was used: the clause is unconstrained or had nothing to read.
    NONE = "none"


@dataclass(frozen=True)
class CategoryPolicy:
    """Explicit per-category contradiction rule (MS2-D-08).

    `listing_min_confidence`: the extraction confidence a title attribute needs
    before it may contradict a requirement. The matching rules modules do not
    publish such a threshold, so it lives here; 0.85 admits the rules modules'
    anchored patterns (0.85-0.95) and excludes their weak forms (a bare '3.5',
    an ambiguous multi-capacity title at 0.5, a CPU wattage at 0.7).

    `on_tier_conflict`: the outcome when the catalog satisfies a requirement
    but a confident title attribute contradicts it. `no_match` for every
    category today, following MS2-D-08's literal rule that a positive listing
    contradiction yields `no_match`; `unknown` would be the alternative (it
    would route the conflict to the review queue instead).
    """

    listing_min_confidence: float = 0.85
    on_tier_conflict: EligibilityVerdict = EligibilityVerdict.NO_MATCH


_DEFAULT_POLICY: Final = CategoryPolicy()
CATEGORY_POLICIES: Final[Mapping[str, CategoryPolicy]] = {
    "drive": _DEFAULT_POLICY,
    "gpu": _DEFAULT_POLICY,
    "ram": _DEFAULT_POLICY,
    "cpu": _DEFAULT_POLICY,
}


def policy_for(category: str) -> CategoryPolicy:
    return CATEGORY_POLICIES.get(category, _DEFAULT_POLICY)


@dataclass(frozen=True)
class ClauseResult:
    """One clause's outcome and its structured reason (MS2-D-08 shape plus
    `source`, which names the observation or catalog inputs the clause read)."""

    clause: str
    outcome: EligibilityVerdict
    evidence_tier: EvidenceTier
    required: object
    observed: object
    detail: str
    source: Mapping[str, object] = field(default_factory=dict[str, object])

    def as_reason(self) -> dict[str, object]:
        return {
            "clause": self.clause,
            "outcome": self.outcome.value,
            "evidence_tier": self.evidence_tier.value,
            "required": _jsonable(self.required),
            "observed": _jsonable(self.observed),
            "detail": self.detail,
            "source": _jsonable(dict(self.source)),
        }


def _jsonable(value: object) -> object:
    """Plain-JSON form for reasons and fingerprints: Decimal as its normalized
    fixed-point string (so 18.000 and 18 hash identically), datetimes as ISO."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in cast("Mapping[object, object]", value).items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in cast("Sequence[object]", value)]
    return value


def aggregate(results: Sequence[ClauseResult]) -> EligibilityVerdict:
    """Any `no_match` => `no_match`; else any `unknown` => `unknown`; else
    `match`. Raises ValueError for no clauses: a verdict must carry reasons
    (FR-006), and an empty clause list must never read as a vacuous `match`."""
    if not results:
        raise ValueError("a verdict needs at least one clause")
    outcomes = {r.outcome for r in results}
    if EligibilityVerdict.NO_MATCH in outcomes:
        return EligibilityVerdict.NO_MATCH
    if EligibilityVerdict.UNKNOWN in outcomes:
        return EligibilityVerdict.UNKNOWN
    return EligibilityVerdict.MATCH


# ── Product-attribute clauses (pure) ─────────────────────────────────────────


def product_outcome(
    catalog_ok: bool | None, listing_contradicts: bool, policy: CategoryPolicy
) -> tuple[EligibilityVerdict, EvidenceTier]:
    """The tri-state table for one product-attribute clause.

    `catalog_ok` is None when the catalog tier has no value (no accepted edge,
    no spec row, an unstated field, or a family whose members disagree).
    `listing_contradicts` is True only for a confident title attribute that
    fails the requirement. Note the asymmetry: nothing here can return `match`
    without `catalog_ok is True`.
    """
    if catalog_ok is False:
        return EligibilityVerdict.NO_MATCH, EvidenceTier.CATALOG
    if listing_contradicts:
        if catalog_ok is True:
            return policy.on_tier_conflict, EvidenceTier.LISTING
        return EligibilityVerdict.NO_MATCH, EvidenceTier.LISTING
    if catalog_ok is True:
        return EligibilityVerdict.MATCH, EvidenceTier.CATALOG
    return EligibilityVerdict.UNKNOWN, EvidenceTier.NONE


@dataclass(frozen=True)
class CatalogField[V]:
    """One catalog-tier value for a clause. `value` None means unknown;
    `why` says where it came from or why it is missing."""

    value: V | None
    why: str


def attribute_clause[V](
    clause: str,
    required: object,
    satisfies: Callable[[V], bool] | None,
    catalog: CatalogField[V],
    listing: Attribute[V] | None,
    policy: CategoryPolicy,
    *,
    listing_satisfies: Callable[[V], bool] | None = None,
    source: Mapping[str, object] | None = None,
) -> ClauseResult:
    """Judge one product attribute. `satisfies` None means the watch does not
    constrain it: the clause is a `match` that read no evidence.
    `listing_satisfies` overrides the predicate for title values in a coarser
    vocabulary than the catalog (a title says 'smr', the catalog 'smr_dm')."""
    src = dict(source or {})
    if satisfies is None:
        return ClauseResult(
            clause, EligibilityVerdict.MATCH, EvidenceTier.NONE, None, None, "no constraint", src
        )
    catalog_ok = None if catalog.value is None else satisfies(catalog.value)
    title_check = listing_satisfies or satisfies
    listing_contradicts = (
        listing is not None
        and listing.confidence >= policy.listing_min_confidence
        and not title_check(listing.value)
    )
    outcome, tier = product_outcome(catalog_ok, listing_contradicts, policy)
    observed: dict[str, object] = {"catalog": catalog.value, "catalog_source": catalog.why}
    if listing is not None:
        observed["listing"] = {
            "value": listing.value,
            "confidence": listing.confidence,
            "source_text": listing.source_text,
        }
    if outcome is EligibilityVerdict.UNKNOWN:
        detail = f"no catalog evidence ({catalog.why}); listing evidence cannot satisfy a clause"
    elif tier is EvidenceTier.LISTING:
        detail = "title attribute contradicts the requirement"
        if catalog_ok is True:
            detail += " while the catalog satisfies it (tier conflict)"
    elif outcome is EligibilityVerdict.NO_MATCH:
        detail = "catalog value fails the requirement"
    else:
        detail = "catalog value satisfies the requirement"
    return ClauseResult(clause, outcome, tier, required, observed, detail, src)


def _at_least[N: (int, Decimal)](bound: N | None) -> Callable[[N], bool] | None:
    if bound is None:
        return None
    return lambda value: value >= bound


def _at_most(bound: int | None) -> Callable[[int], bool] | None:
    if bound is None:
        return None
    return lambda value: value <= bound


def _one_of(allowed: Sequence[str]) -> Callable[[str], bool] | None:
    if not allowed:
        return None
    members = frozenset(allowed)
    return lambda value: value in members


def _equals[T](expected: T | None) -> Callable[[T], bool] | None:
    if expected is None:
        return None
    return lambda value: value == expected


# ── Offer clauses (pure) ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class OfferFacts:
    """Offer-tier inputs, read from the latest snapshot and the listing.

    `price_usd` is item price + shipping IF STATED + tax IF STATED, converted
    with the snapshot's FX stamp; None when there is no snapshot or no FX rate
    (a foreign amount is never read as USD, ADR-0008). When shipping is
    unstated it is therefore a LOWER bound on the landed price, and
    `shipping_known` says so; price_clause relies on that flag to keep an
    unknown shipping cost from passing a hard maximum. Unstated tax is
    excluded by definition: the hard maximum is a pre-tax bound whenever the
    source states no tax. (The stored `OfferSnapshot.total_landed_price`
    coalesces both to zero; that convention is unchanged and is not what the
    hard clause reads.)
    """

    snapshot_observed_at: datetime | None
    price_usd: Decimal | None
    shipping_known: bool
    quantity: Attribute[int] | None
    stock_status: str | None
    is_international: bool
    condition: Attribute[str] | None


def price_clause(
    max_unit_price_usd: Decimal | None, facts: OfferFacts, policy: CategoryPolicy
) -> ClauseResult:
    """Unit price = `OfferFacts.price_usd` / quantity, compared with the
    watch maximum (pre-tax when the source states no tax; see OfferFacts).

    Unknown shipping never passes (hard missing evidence is `unknown`): the
    price without shipping is a lower bound, so exceeding the maximum proves
    `no_match`, while being within it is only `unknown` (`shipping_unknown`).
    `match` requires stated shipping, an explicit 0 included.

    No quantity statement in the title is read as a single unit (the spec's
    $/TB convention). A quantity stated below the policy confidence is not
    trusted as a divisor: the undivided total is then only an UPPER bound on
    the unit price, so exceeding the maximum proves nothing (`unknown`).
    """
    src = {"snapshot_observed_at": facts.snapshot_observed_at}
    if max_unit_price_usd is None:
        return ClauseResult(
            "offer.max_unit_price_usd",
            EligibilityVerdict.MATCH,
            EvidenceTier.NONE,
            None,
            None,
            "no constraint",
            src,
        )
    if facts.price_usd is None:
        return ClauseResult(
            "offer.max_unit_price_usd",
            EligibilityVerdict.UNKNOWN,
            EvidenceTier.NONE,
            max_unit_price_usd,
            None,
            "no snapshot or no FX stamp: USD price unknown",
            src,
        )
    quantity = facts.quantity
    trusted = quantity is not None and quantity.confidence >= policy.listing_min_confidence
    divisor = quantity.value if trusted and quantity is not None and quantity.value > 0 else 1
    uncertain_quantity = quantity is not None and not trusted
    unit = facts.price_usd / divisor
    observed: dict[str, object] = {
        "price_usd": facts.price_usd,
        "quantity": divisor,
        "quantity_uncertain": uncertain_quantity,
        "unit_usd": unit,
        "shipping_known": facts.shipping_known,
    }
    if unit <= max_unit_price_usd:
        if facts.shipping_known:
            outcome, detail = EligibilityVerdict.MATCH, "unit price within the maximum"
        else:
            outcome, code = EligibilityVerdict.UNKNOWN, "shipping_unknown"
            observed["reason_code"] = code
            detail = f"{code}: price before shipping is within the maximum"
    elif uncertain_quantity:
        outcome, code = EligibilityVerdict.UNKNOWN, "quantity_uncertain"
        observed["reason_code"] = code
        detail = f"{code}: total exceeds the maximum but the lot quantity is uncertain"
    else:
        outcome, detail = EligibilityVerdict.NO_MATCH, "unit price exceeds the maximum"
    return ClauseResult(
        "offer.max_unit_price_usd",
        outcome,
        EvidenceTier.OFFER,
        max_unit_price_usd,
        observed,
        detail,
        src,
    )


def condition_clause(allowed: Sequence[str], facts: OfferFacts) -> ClauseResult:
    """An unstated or 'unknown' condition against a non-empty allow-list is
    `unknown` (MS2-D-08). The condition is the listing's own statement, read
    with the shared offer-term vocabulary the resolver uses."""
    src = {"snapshot_observed_at": facts.snapshot_observed_at}
    if not allowed:
        return ClauseResult(
            "offer.condition",
            EligibilityVerdict.MATCH,
            EvidenceTier.NONE,
            [],
            None,
            "no constraint",
            src,
        )
    stated = facts.condition
    observed = (
        None
        if stated is None
        else {
            "value": stated.value,
            "confidence": stated.confidence,
            "source_text": stated.source_text,
        }
    )
    if stated is None or stated.value == "unknown":
        return ClauseResult(
            "offer.condition",
            EligibilityVerdict.UNKNOWN,
            EvidenceTier.NONE,
            list(allowed),
            observed,
            "listing states no condition",
            src,
        )
    ok = stated.value in allowed
    return ClauseResult(
        "offer.condition",
        EligibilityVerdict.MATCH if ok else EligibilityVerdict.NO_MATCH,
        EvidenceTier.OFFER,
        list(allowed),
        observed,
        "condition allowed" if ok else "condition not allowed",
        src,
    )


def stock_clause(require_in_stock: bool, facts: OfferFacts) -> ClauseResult:
    src = {"snapshot_observed_at": facts.snapshot_observed_at}
    if not require_in_stock:
        return ClauseResult(
            "offer.in_stock",
            EligibilityVerdict.MATCH,
            EvidenceTier.NONE,
            False,
            facts.stock_status,
            "no constraint",
            src,
        )
    status = facts.stock_status
    if status == StockStatus.IN_STOCK.value:
        outcome, tier, detail = EligibilityVerdict.MATCH, EvidenceTier.OFFER, "in stock"
    elif status in (StockStatus.OUT_OF_STOCK.value, StockStatus.PREORDER.value):
        outcome, tier, detail = EligibilityVerdict.NO_MATCH, EvidenceTier.OFFER, "not in stock"
    else:
        outcome, tier, detail = (
            EligibilityVerdict.UNKNOWN,
            EvidenceTier.NONE,
            "stock status unknown",
        )
    return ClauseResult("offer.in_stock", outcome, tier, True, status, detail, src)


def international_clause(allow_international: bool, facts: OfferFacts) -> ClauseResult:
    src = {"snapshot_observed_at": facts.snapshot_observed_at}
    if allow_international:
        return ClauseResult(
            "offer.international",
            EligibilityVerdict.MATCH,
            EvidenceTier.NONE,
            True,
            facts.is_international,
            "no constraint",
            src,
        )
    ok = not facts.is_international
    return ClauseResult(
        "offer.international",
        EligibilityVerdict.MATCH if ok else EligibilityVerdict.NO_MATCH,
        EvidenceTier.OFFER,
        False,
        facts.is_international,
        "domestic offer" if ok else "international offer not allowed",
        src,
    )


def offer_clauses(watch: Watch, facts: OfferFacts, policy: CategoryPolicy) -> list[ClauseResult]:
    return [
        price_clause(watch.max_unit_price_usd, facts, policy),
        condition_clause(list(watch.allowed_conditions), facts),
        stock_clause(watch.require_in_stock, facts),
        international_clause(watch.allow_international, facts),
    ]


# ── Catalog inputs (MS2-D-29) ────────────────────────────────────────────────


@dataclass(frozen=True)
class MemberSpec:
    """One model's typed spec values as the product clauses read them.
    `spec` None is the explicit `absent` marker: the model has no spec row."""

    model_id: int
    spec: Mapping[str, Scalar] | None


@dataclass(frozen=True)
class CatalogInputs:
    """Everything the product and target clauses read (MS2-D-29).

    - `grain` is the current edge's grain when that edge is an ACCEPT, else
      "none" — the constant no-evidence marker; review, none and error edges
      all collapse to it because none of them supplies catalog evidence.
    - `family_id` is the family-grain target, or the resolved model's family
      at model/variant grain (the target clause reads it, so a family
      reassignment must change the fingerprint).
    - `members` holds the resolved model at model/variant grain, or every
      family member sorted by id at family grain (the agreement-set inputs).
    - `category` is the dispatch category (latest snapshot's hint, legacy
      drive default), which also selects the spec satellite read.
    """

    category: str
    grain: str
    family_id: int | None
    model_id: int | None
    variant_id: int | None
    members: tuple[MemberSpec, ...]

    def canonical(self) -> dict[str, object]:
        return {
            "version": CATALOG_INPUTS_VERSION,
            "category": self.category,
            "grain": self.grain,
            "family_id": self.family_id,
            "model_id": self.model_id,
            "variant_id": self.variant_id,
            "members": [
                {"model_id": m.model_id, "spec": "absent" if m.spec is None else dict(m.spec)}
                for m in self.members
            ],
        }


def catalog_fingerprint(inputs: CatalogInputs) -> str:
    """sha256 over canonical JSON (sorted keys, no whitespace) of the inputs,
    which embed CATALOG_INPUTS_VERSION."""
    payload = json.dumps(
        _jsonable(inputs.canonical()), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _known(value: str | None) -> str | None:
    """A spec choice column as evidence: blank and "unknown" mean unstated."""
    return value if value and value != "unknown" else None


def _token(text: str, tokens: Sequence[str]) -> str | None:
    # Substring scan in the same token order matching.resolver uses to map the
    # free-text DriveSpec columns (see requirements.DRIVE_INTERFACE_TOKENS).
    folded = text.casefold()
    return next((t for t in tokens if t in folded), None)


def _drive_values(spec: DriveSpec) -> dict[str, Scalar]:
    return {
        "capacity_tb": None
        if spec.capacity_tb is None
        else format(spec.capacity_tb.normalize(), "f"),
        "media_type": _known(spec.media_type),
        "interface": _token(spec.interface, DRIVE_INTERFACE_TOKENS),
        "form_factor": _token(spec.form_factor, DRIVE_FORM_FACTOR_TOKENS),
        "recording_tech": _known(spec.recording_tech),
    }


def _gpu_values(spec: GpuSpec) -> dict[str, Scalar]:
    return {
        "chip_vendor": _known(spec.chip_vendor),
        "vram_gb": spec.vram_gb,
        "interface": _known(spec.interface),
        "cooling": _known(spec.cooling),
        "tdp_w": spec.tdp_w,
    }


def _ram_values(spec: RamSpec) -> dict[str, Scalar]:
    total = (
        None if spec.module_capacity_gb is None else spec.module_capacity_gb * spec.modules_per_kit
    )
    return {
        "generation": _known(spec.generation),
        "module_type": _known(spec.module_type),
        "ecc": spec.ecc,
        "total_capacity_gb": total,
        "speed_mts": spec.speed_mts,
    }


def _cpu_values(spec: CpuSpec) -> dict[str, Scalar]:
    return {
        "socket": socket_key(spec.socket) or None,
        "cores": spec.cores,
        "tdp_w": spec.tdp_w,
    }


type _SpecRow = DriveSpec | GpuSpec | RamSpec | CpuSpec


def _values_of(spec: _SpecRow) -> dict[str, Scalar]:
    if isinstance(spec, DriveSpec):
        return _drive_values(spec)
    if isinstance(spec, GpuSpec):
        return _gpu_values(spec)
    if isinstance(spec, RamSpec):
        return _ram_values(spec)
    return _cpu_values(spec)


# Spec satellite per first-class category. Categories absent here (the
# basic-watch ones) have no satellite: their members carry an empty value map,
# since no product-attribute clause reads anything for them.
_SPEC_MODELS: Final[Mapping[str, type[_SpecRow]]] = {
    "drive": DriveSpec,
    "gpu": GpuSpec,
    "ram": RamSpec,
    "cpu": CpuSpec,
}


def _members(category: str, model_ids: Sequence[int]) -> tuple[MemberSpec, ...]:
    spec_model = _SPEC_MODELS.get(category)
    if spec_model is None:
        return tuple(MemberSpec(model_id=m, spec={}) for m in sorted(model_ids))
    # One batched read for all members; the spec's pk IS the model id.
    rows: dict[int, _SpecRow] = {
        cast("int", s.pk): s for s in spec_model.objects.filter(product_model_id__in=model_ids)
    }
    return tuple(
        MemberSpec(model_id=m, spec=None if (row := rows.get(m)) is None else _values_of(row))
        for m in sorted(model_ids)
    )


def _dispatch_category(listing: Listing, snapshot: OfferSnapshot | None) -> str:
    """The resolver's dispatch rule, applied to the same input (the latest
    snapshot's collector hint). Raises ValueError for a malformed hint rather
    than defaulting it to drive (the resolver likewise refuses it, with an
    error edge), so a garbled hint can never route a listing to another
    category's watches."""
    # cast to object: attrs_json is typed as a dict, but a stored JSON value can
    # be any JSON type, and only an object can carry the hint.
    attrs = cast("object", {} if snapshot is None else snapshot.attrs_json)
    hint = (
        cast("Mapping[str, object]", attrs).get(CATEGORY_HINT_ATTR)
        if isinstance(attrs, dict)
        else None
    )
    if hint is not None and not isinstance(hint, str):
        raise ValueError(f"listing {listing.pk}: non-string category hint {hint!r}")
    return categories.dispatch_category(hint)


def _latest_snapshot(listing: Listing) -> OfferSnapshot | None:
    return OfferSnapshot.objects.filter(listing=listing).order_by("-observed_at").first()


def _current_edge(listing: Listing) -> ListingResolution | None:
    return (
        ListingResolution.objects.filter(listing=listing, is_current=True)
        .select_related(
            "product_family",
            "product_model__product_family",
            "product_variant__product_model__product_family",
        )
        .first()
    )


def _family_of(model: ProductModel) -> int | None:
    family = model.product_family
    return None if family is None else cast("int", family.pk)


def _gather(
    listing: Listing, snapshot: OfferSnapshot | None, edge: ListingResolution | None
) -> CatalogInputs:
    category = _dispatch_category(listing, snapshot)
    grain = ResolutionGrain.NONE.value if edge is None else str(edge.grain)
    if edge is None or grain == ResolutionGrain.NONE.value:
        return CatalogInputs(category, ResolutionGrain.NONE.value, None, None, None, ())
    if grain == ResolutionGrain.FAMILY.value:
        family = edge.product_family
        if family is None:  # the grain/target CHECK makes this unreachable
            raise ValueError(f"family edge {edge.pk} has no family")
        member_ids = list(
            ProductModel.objects.filter(product_family=family).values_list("pk", flat=True)
        )
        return CatalogInputs(
            category,
            grain,
            cast("int", family.pk),
            None,
            None,
            _members(category, cast("list[int]", member_ids)),
        )
    variant = edge.product_variant
    model = edge.product_model if variant is None else variant.product_model
    if model is None:  # the grain/target CHECK makes this unreachable
        raise ValueError(f"edge {edge.pk} has no model")
    return CatalogInputs(
        category,
        grain,
        _family_of(model),
        cast("int", model.pk),
        None if variant is None else cast("int", variant.pk),
        _members(category, [cast("int", model.pk)]),
    )


def catalog_inputs(listing: Listing) -> CatalogInputs:
    """Gather the listing's current catalog inputs (MS2-D-29).

    Reads the latest snapshot (for the dispatch category), the current
    resolution edge, and the spec rows of the resolved model or of every family
    member. Raises ValueError for a malformed category hint.
    """
    return _gather(listing, _latest_snapshot(listing), _current_edge(listing))


# ── Input binding (MS2-D-20) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class InputBinding:
    snapshot_observed_at: datetime | None
    resolution_id: int | None
    requirement_version: int
    evaluator_version: str
    catalog_fingerprint: str


def live_binding(listing: Listing, watch: Watch) -> InputBinding:
    """The binding a row evaluated NOW would carry. Single-row form; a batched
    read-model predicate must compute the same five values."""
    snapshot = _latest_snapshot(listing)
    edge = _current_edge(listing)
    return InputBinding(
        snapshot_observed_at=None if snapshot is None else snapshot.observed_at,
        resolution_id=None if edge is None else cast("int", edge.pk),
        requirement_version=watch.requirement_version,
        evaluator_version=EVALUATOR_VERSION,
        catalog_fingerprint=catalog_fingerprint(_gather(listing, snapshot, edge)),
    )


def stored_binding(evaluation: WatchEvaluation) -> InputBinding:
    return InputBinding(
        snapshot_observed_at=evaluation.snapshot_observed_at,
        resolution_id=cast("int | None", evaluation.resolution_id),  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs
        requirement_version=evaluation.requirement_version,
        evaluator_version=evaluation.evaluator_version,
        catalog_fingerprint=evaluation.catalog_fingerprint,
    )


def is_current(evaluation: WatchEvaluation) -> bool:
    """MS2-D-20 validity for one row (all five binding values equal)."""
    return stored_binding(evaluation) == live_binding(evaluation.listing, evaluation.watch)


# ── Per-category product clauses ─────────────────────────────────────────────


@dataclass(frozen=True)
class _CatalogView:
    """Catalog-tier field access over CatalogInputs. At family grain a field
    is known only where every member has a spec row and all agree (the C.3.2
    agreement set); one member without a spec row makes every field unknown,
    because the unknown member could hold any value."""

    inputs: CatalogInputs

    def get(self, name: str) -> CatalogField[Scalar]:
        inputs = self.inputs
        if inputs.grain == ResolutionGrain.NONE.value:
            return CatalogField(None, "no accepted resolution")
        if not inputs.members:
            return CatalogField(None, "family has no member models")
        if any(m.spec is None for m in inputs.members):
            return CatalogField(None, "spec row absent")
        values = {cast("Mapping[str, Scalar]", m.spec).get(name) for m in inputs.members}
        if len(values) != 1:
            return CatalogField(None, "family members disagree")
        value = next(iter(values))
        where = (
            "family agreement set" if inputs.grain == ResolutionGrain.FAMILY.value else "model spec"
        )
        return CatalogField(value, where if value is not None else f"{where}: not stated")


def _typed[V](field_value: CatalogField[Scalar], kind: type[V]) -> CatalogField[V]:
    value = field_value.value
    return CatalogField(value if isinstance(value, kind) else None, field_value.why)


def _decimal_field(field_value: CatalogField[Scalar]) -> CatalogField[Decimal]:
    value = field_value.value
    return CatalogField(Decimal(value) if isinstance(value, str) else None, field_value.why)


def _int_field(field_value: CatalogField[Scalar]) -> CatalogField[int]:
    value = field_value.value
    # bool is an int subclass; no int-valued clause field is ever boolean.
    ok = isinstance(value, int) and not isinstance(value, bool)
    return CatalogField(cast("int", value) if ok else None, field_value.why)


def _recording_title_ok(allowed: Sequence[str]) -> Callable[[str], bool]:
    # Titles only say 'cmr' or 'smr'; 'smr' is compatible with any required
    # SMR subtype, so it contradicts only a requirement that allows none.
    def ok(value: str) -> bool:
        if value == "smr":
            return any(a.startswith("smr") for a in allowed)
        return value in allowed

    return ok


def _drive_clauses(
    req: DriveRequirement,
    view: _CatalogView,
    extracted: ExtractedAttributes,
    policy: CategoryPolicy,
    source: Mapping[str, object],
) -> list[ClauseResult]:
    capacity = extracted.capacity_bytes
    listing_capacity = (
        None
        if capacity is None
        else Attribute(
            Decimal(capacity.value) / _BYTES_PER_TB,
            capacity.confidence,
            capacity.layer,
            capacity.source_text,
        )
    )
    media = list(req.media_types)
    interfaces = list(req.interfaces)
    forms = list(req.form_factors)
    recordings = list(req.recording_techs)
    return [
        attribute_clause(
            "drive.min_capacity_tb",
            req.min_capacity_tb,
            _at_least(req.min_capacity_tb),
            _decimal_field(view.get("capacity_tb")),
            listing_capacity,
            policy,
            source=source,
        ),
        # Titles carry no media-type attribute, so this clause is catalog-only.
        attribute_clause(
            "drive.media_types",
            media,
            _one_of(media),
            _typed(view.get("media_type"), str),
            None,
            policy,
            source=source,
        ),
        attribute_clause(
            "drive.interfaces",
            interfaces,
            _one_of(interfaces),
            _typed(view.get("interface"), str),
            extracted.interface,
            policy,
            source=source,
        ),
        attribute_clause(
            "drive.form_factors",
            forms,
            _one_of(forms),
            _typed(view.get("form_factor"), str),
            extracted.form_factor,
            policy,
            source=source,
        ),
        attribute_clause(
            "drive.recording_techs",
            recordings,
            _one_of(recordings),
            _typed(view.get("recording_tech"), str),
            extracted.recording_tech,
            policy,
            listing_satisfies=_recording_title_ok(recordings),
            source=source,
        ),
    ]


def _gpu_clauses(
    req: GpuRequirement,
    view: _CatalogView,
    extracted: ExtractedAttributes,
    policy: CategoryPolicy,
    source: Mapping[str, object],
) -> list[ClauseResult]:
    attrs = extracted.category_attrs
    title = attrs if isinstance(attrs, GpuAttributes) else GpuAttributes()
    vendors = list(req.chip_vendors)
    interfaces = list(req.interfaces)
    coolings = list(req.coolings)
    return [
        attribute_clause(
            "gpu.min_vram_gb",
            req.min_vram_gb,
            _at_least(req.min_vram_gb),
            _int_field(view.get("vram_gb")),
            title.vram_gb,
            policy,
            source=source,
        ),
        attribute_clause(
            "gpu.chip_vendors",
            vendors,
            _one_of(vendors),
            _typed(view.get("chip_vendor"), str),
            title.chip_vendor,
            policy,
            source=source,
        ),
        attribute_clause(
            "gpu.interfaces",
            interfaces,
            _one_of(interfaces),
            _typed(view.get("interface"), str),
            title.interface,
            policy,
            source=source,
        ),
        attribute_clause(
            "gpu.coolings",
            coolings,
            _one_of(coolings),
            _typed(view.get("cooling"), str),
            title.cooling,
            policy,
            source=source,
        ),
        # Titles quote board power or configurable limits, so a title wattage
        # is not evidence about the product's TDP (matching.rules.gpu does not
        # veto on it either): catalog-only.
        attribute_clause(
            "gpu.max_tdp_w",
            req.max_tdp_w,
            _at_most(req.max_tdp_w),
            _int_field(view.get("tdp_w")),
            None,
            policy,
            source=source,
        ),
    ]


def _ram_clauses(
    req: RamRequirement,
    view: _CatalogView,
    extracted: ExtractedAttributes,
    policy: CategoryPolicy,
    source: Mapping[str, object],
) -> list[ClauseResult]:
    attrs = extracted.category_attrs
    title = attrs if isinstance(attrs, RamAttributes) else RamAttributes()
    # A title's kit total is known only in the explicit 'NxSIZE' kit form; a
    # lone size could be one module or a whole kit (matching.rules.ram).
    total = None
    if title.modules_per_kit is not None and title.module_capacity_gb is not None:
        total = Attribute(
            title.modules_per_kit.value * title.module_capacity_gb.value,
            min(title.modules_per_kit.confidence, title.module_capacity_gb.confidence),
            title.module_capacity_gb.layer,
            title.module_capacity_gb.source_text,
        )
    generations = list(req.generations)
    module_types = list(req.module_types)
    return [
        attribute_clause(
            "ram.generations",
            generations,
            _one_of(generations),
            _typed(view.get("generation"), str),
            title.generation,
            policy,
            source=source,
        ),
        attribute_clause(
            "ram.module_types",
            module_types,
            _one_of(module_types),
            _typed(view.get("module_type"), str),
            title.module_type,
            policy,
            source=source,
        ),
        attribute_clause(
            "ram.require_ecc",
            req.require_ecc,
            _equals(req.require_ecc),
            _typed(view.get("ecc"), bool),
            title.ecc,
            policy,
            source=source,
        ),
        attribute_clause(
            "ram.min_total_capacity_gb",
            req.min_total_capacity_gb,
            _at_least(req.min_total_capacity_gb),
            _int_field(view.get("total_capacity_gb")),
            total,
            policy,
            source=source,
        ),
        attribute_clause(
            "ram.min_speed_mts",
            req.min_speed_mts,
            _at_least(req.min_speed_mts),
            _int_field(view.get("speed_mts")),
            title.speed_mts,
            policy,
            source=source,
        ),
    ]


def _cpu_clauses(
    req: CpuRequirement,
    view: _CatalogView,
    extracted: ExtractedAttributes,
    policy: CategoryPolicy,
    source: Mapping[str, object],
) -> list[ClauseResult]:
    attrs = extracted.category_attrs
    title = attrs if isinstance(attrs, CpuAttributes) else CpuAttributes()
    sockets = list(req.sockets)
    return [
        attribute_clause(
            "cpu.sockets",
            sockets,
            _one_of(sockets),
            _typed(view.get("socket"), str),
            title.socket,
            policy,
            source=source,
        ),
        attribute_clause(
            "cpu.min_cores",
            req.min_cores,
            _at_least(req.min_cores),
            _int_field(view.get("cores")),
            title.cores,
            policy,
            source=source,
        ),
        attribute_clause(
            "cpu.max_tdp_w",
            req.max_tdp_w,
            _at_most(req.max_tdp_w),
            _int_field(view.get("tdp_w")),
            title.tdp_w,
            policy,
            source=source,
        ),
    ]


type _Requirement = DriveRequirement | GpuRequirement | RamRequirement | CpuRequirement

_REQUIREMENT_MODELS: Final[Mapping[str, type[_Requirement]]] = {
    "drive": DriveRequirement,
    "gpu": GpuRequirement,
    "ram": RamRequirement,
    "cpu": CpuRequirement,
}


def product_clauses(
    requirement: _Requirement | None,
    category: str,
    inputs: CatalogInputs,
    extracted: ExtractedAttributes,
    policy: CategoryPolicy,
    source: Mapping[str, object],
) -> list[ClauseResult]:
    """The category's product-attribute clauses. A first-class watch with no
    satellite row (only possible by bypassing the requirement service) yields
    one `unknown` clause instead of silently reading as unconstrained."""
    if category not in _REQUIREMENT_MODELS:
        return []
    if requirement is None:
        return [
            ClauseResult(
                f"{category}.requirement",
                EligibilityVerdict.UNKNOWN,
                EvidenceTier.NONE,
                None,
                None,
                "watch has no requirement satellite; save it through the requirement service",
                source,
            )
        ]
    view = _CatalogView(inputs)
    if isinstance(requirement, DriveRequirement):
        return _drive_clauses(requirement, view, extracted, policy, source)
    if isinstance(requirement, GpuRequirement):
        return _gpu_clauses(requirement, view, extracted, policy, source)
    if isinstance(requirement, RamRequirement):
        return _ram_clauses(requirement, view, extracted, policy, source)
    return _cpu_clauses(requirement, view, extracted, policy, source)


def target_clause(
    target_family_id: int | None,
    target_model_id: int | None,
    inputs: CatalogInputs,
    source: Mapping[str, object],
) -> ClauseResult | None:
    """Exact-identity clause for a watch with a target (required for the
    basic-watch categories, optional otherwise). Catalog tier only.

    A model target against a family-grain resolution is `unknown`, never
    `no_match`: the listing may be any member, and deciding it would need the
    target model's own family, which is watch-side state outside the listing's
    catalog inputs.
    """
    if target_family_id is None and target_model_id is None:
        return None
    required = {"family_id": target_family_id, "model_id": target_model_id}
    observed = {"grain": inputs.grain, "family_id": inputs.family_id, "model_id": inputs.model_id}

    def result(outcome: EligibilityVerdict, detail: str) -> ClauseResult:
        tier = EvidenceTier.NONE if outcome is EligibilityVerdict.UNKNOWN else EvidenceTier.CATALOG
        return ClauseResult("target", outcome, tier, required, observed, detail, source)

    if inputs.grain == ResolutionGrain.NONE.value:
        return result(EligibilityVerdict.UNKNOWN, "no accepted resolution")
    at_family = inputs.grain == ResolutionGrain.FAMILY.value
    if target_model_id is not None:
        if at_family:
            return result(EligibilityVerdict.UNKNOWN, "resolved only to a family")
        same = inputs.model_id == target_model_id
        return result(
            EligibilityVerdict.MATCH if same else EligibilityVerdict.NO_MATCH,
            "resolved to the target model" if same else "resolved to another model",
        )
    if inputs.family_id is None:
        return result(EligibilityVerdict.UNKNOWN, "resolved model has no family")
    same = inputs.family_id == target_family_id
    return result(
        EligibilityVerdict.MATCH if same else EligibilityVerdict.NO_MATCH,
        "resolved within the target family" if same else "resolved to another family",
    )


# ── Evaluation (DB) ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ListingEvaluationResult:
    listing_id: int
    # "delisted" | "expired" when the listing was not evaluated, else None.
    skipped: str | None
    # watch id -> verdict, for every watch evaluated in this call.
    verdicts: Mapping[int, EligibilityVerdict]


class ListingEvaluator(Protocol):
    """What `run_collection` calls after resolution for each listing whose
    resolution did not raise. Implementations raise on failure; the caller
    counts it and never blocks ingestion (MS2-D-20)."""

    def evaluate_listing(self, listing_id: int) -> ListingEvaluationResult: ...


class WatchEvaluator:
    """Production ListingEvaluator. Stateless — safe to construct per run."""

    def evaluate_listing(self, listing_id: int) -> ListingEvaluationResult:
        return evaluate_listing(listing_id)


def _offer_facts(listing: Listing, snapshot: OfferSnapshot | None, canonical: str) -> OfferFacts:
    price: Decimal | None = None
    shipping_known = False
    if snapshot is not None:
        shipping_known = snapshot.shipping_price is not None
        rate = snapshot.fx_rate
        if rate is not None:
            # Stated components only (see OfferFacts): read explicitly rather
            # than from total_landed_price, which silently zeroes unstated ones.
            stated = [snapshot.shipping_price, snapshot.tax_price]
            total = snapshot.item_price + sum((c for c in stated if c is not None), Decimal(0))
            price = (total * rate).quantize(Decimal("0.01"))
    return OfferFacts(
        snapshot_observed_at=None if snapshot is None else snapshot.observed_at,
        price_usd=price,
        shipping_known=shipping_known,
        # Quantity comes from the one quantity vocabulary the codebase has
        # (matching.vocab); the category rules modules extract none.
        quantity=vocab.extract(canonical).quantity,
        stock_status=None if snapshot is None else snapshot.stock_status,
        is_international=listing.is_international,
        condition=vocab.offer_terms(canonical).condition,
    )


def _listing_attributes(category: str, canonical: str) -> ExtractedAttributes:
    rules = categories.rules_for(category)
    return ExtractedAttributes() if rules is None else rules.extract(canonical)


def _is_expired(listing: Listing, now: datetime) -> bool:
    # Mirrors ListingQuerySet.active()'s freshness clause for one loaded row.
    # cast: django-types types a nullable DateTimeField as non-optional.
    expires = cast("datetime | None", listing.expires_at)
    return expires is not None and expires <= now


@transaction.atomic
def evaluate_listing(listing_id: int) -> ListingEvaluationResult:
    """Evaluate one listing against every enabled watch of its dispatch
    category and upsert the `WatchEvaluation` rows. See the module docstring
    for the contract."""
    # The listing row lock is the resolver's lock (matching.resolver._apply), so
    # the edge and snapshot read below cannot interleave with a concurrent
    # resolution of this listing, and two evaluations of it serialize.
    listing = Listing.objects.select_for_update().get(pk=listing_id)
    now = timezone.now()
    if listing.delisted_at is not None:
        return ListingEvaluationResult(listing_id, "delisted", {})
    if _is_expired(listing, now):
        return ListingEvaluationResult(listing_id, "expired", {})

    snapshot = _latest_snapshot(listing)
    edge = _current_edge(listing)
    # ONE catalog read feeds both the clauses and the stored fingerprint
    # (MS2-D-29): reading the catalog twice could stamp a fingerprint of inputs
    # the clauses never saw.
    inputs = _gather(listing, snapshot, edge)
    fingerprint = catalog_fingerprint(inputs)
    category = inputs.category

    # Watches (with their versions) are read BEFORE their satellites. A
    # concurrent save_requirement commits both together, so this order can only
    # pair a newer satellite with an older version (a pending row), never an
    # older satellite with the newer version (a stale verdict stamped current).
    watches = list(Watch.objects.filter(enabled=True, category__slug=category).order_by("pk"))
    if not watches:
        return ListingEvaluationResult(listing_id, None, {})
    requirement_model = _REQUIREMENT_MODELS.get(category)
    requirements: dict[int, _Requirement] = (
        {}
        if requirement_model is None
        else {cast("int", r.pk): r for r in requirement_model.objects.filter(watch__in=watches)}
    )

    canonical = canonicalize_listing_text(listing.title_raw, listing.condition_label_raw)
    extracted = _listing_attributes(category, canonical)
    facts = _offer_facts(listing, snapshot, canonical)
    policy = policy_for(category)
    catalog_source: dict[str, object] = {
        "resolution_id": None if edge is None else edge.pk,
        "resolution_outcome": None if edge is None else edge.evidence.get("outcome"),
        "grain": inputs.grain,
        "family_id": inputs.family_id,
        "model_id": inputs.model_id,
        "variant_id": inputs.variant_id,
        "catalog_fingerprint": fingerprint,
    }

    verdicts: dict[int, EligibilityVerdict] = {}
    for watch in watches:
        watch_id = cast("int", watch.pk)
        results: list[ClauseResult] = []
        target = target_clause(
            cast("int | None", watch.target_family_id),  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs
            cast("int | None", watch.target_model_id),  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs
            inputs,
            catalog_source,
        )
        if target is not None:
            results.append(target)
        results.extend(
            product_clauses(
                requirements.get(watch_id), category, inputs, extracted, policy, catalog_source
            )
        )
        results.extend(offer_clauses(watch, facts, policy))
        verdict = aggregate(results)
        WatchEvaluation.objects.update_or_create(
            watch=watch,
            listing=listing,
            defaults={
                "verdict": verdict,
                "reasons": [r.as_reason() for r in results],
                "requirement_version": watch.requirement_version,
                "evaluator_version": EVALUATOR_VERSION,
                "snapshot_observed_at": facts.snapshot_observed_at,
                "resolution": edge,
                "catalog_fingerprint": fingerprint,
                "evaluated_at": now,
                "retention_class": listing.retention_class,
                "expires_at": listing.expires_at,
            },
        )
        verdicts[watch_id] = verdict
    return ListingEvaluationResult(listing_id, None, verdicts)
