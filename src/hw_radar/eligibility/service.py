"""Batched evaluation currency and the re-evaluation service (MS-2 Slice C4;
MS2-D-20, MS2-D-29).

- `live_bindings(evaluations)` computes, for many `WatchEvaluation` rows at
  once, the same five-value `InputBinding` that `evaluate.live_binding`
  computes for one. `shortlist()`, `review_queue()` and
  `evaluate_watches --pending` all decide currency through it, so the three
  cannot disagree about which rows are `pending` (MS2-D-29: "share one
  predicate, which batches the spec reads for the candidate rows").
- `evaluate_pending()` / `evaluate_all()` drive `evaluate_listing` for the
  `evaluate_watches` command. Each listing is isolated: an evaluation that
  raises is counted and the rest still run, and the failed listing's rows stay
  non-current, so they fail closed to `pending`.

Cross-module contract with eligibility/evaluate.py: `_live_catalog_inputs`
below is a batched re-statement of `evaluate._gather` and `live_binding`. It
reuses that module's own value extractors (`_values_of`, `_SPEC_MODELS`,
`_dispatch_category`, `_family_of`) rather than copying them, so the
per-attribute canonical values cannot drift; the grain/member assembly is
restated here. `tests/db/test_shortlist.py::test_batched_binding_equals_live_binding`
pins the two as equal across every grain shape — change either side only with
that test green. Rejected: calling `live_binding` per row. That issues several
queries per row (snapshot, edge, spec, family members); R18 records the
batched read as the design, reopened only if F3 measures it as too slow.

No ADR-0011 scoring artifact is read or written here.
"""

# pyright: reportPrivateUsage=false
# The private evaluate helpers named in the module docstring are used on
# purpose: sharing them is what keeps the batched fingerprint identical to the
# evaluator's.

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import cast

from hw_radar.catalog.models import (
    Listing,
    ListingResolution,
    OfferSnapshot,
    ProductModel,
    ResolutionGrain,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility import evaluate
from hw_radar.eligibility.evaluate import (
    CatalogInputs,
    InputBinding,
    MemberSpec,
    catalog_fingerprint,
    evaluate_listing,
    stored_binding,
)

logger = logging.getLogger(__name__)

# Field names of InputBinding, in declaration order; review rows report which
# of them no longer match (the row's "stale binding", MS2-D-20).
BINDING_FIELDS: tuple[str, ...] = (
    "snapshot_observed_at",
    "resolution_id",
    "requirement_version",
    "evaluator_version",
    "catalog_fingerprint",
)


@dataclass(frozen=True)
class LiveInputs:
    """One listing's live inputs, read once for every row that references it.

    `snapshot` is the listing's latest OfferSnapshot (None if it has none).
    `catalog` is None when the catalog inputs cannot be computed — today only a
    malformed category hint, which `evaluate_listing` also refuses — so every
    row of that listing is non-current.
    """

    snapshot: OfferSnapshot | None
    edge: ListingResolution | None
    catalog: CatalogInputs | None
    fingerprint: str | None


def _latest_snapshots(listing_ids: Sequence[int]) -> dict[int, OfferSnapshot]:
    # DISTINCT ON (listing_id) with observed_at DESC picks each listing's
    # latest row, which is what evaluate._latest_snapshot reads.
    rows = (
        OfferSnapshot.objects.filter(listing_id__in=listing_ids)
        .order_by("listing_id", "-observed_at")
        .distinct("listing_id")
    )
    return {cast("int", s.listing_id): s for s in rows}  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs


def _current_edges(listing_ids: Sequence[int]) -> dict[int, ListingResolution]:
    # Same select_related shape as evaluate._current_edge, so _family_of reads
    # the same loaded relations.
    rows = ListingResolution.objects.filter(
        listing_id__in=listing_ids, is_current=True
    ).select_related(
        "product_family",
        "product_model__product_family",
        "product_variant__product_model__product_family",
    )
    return {cast("int", e.listing_id): e for e in rows}  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue] - django-types has no <field>_id stubs


@dataclass
class _Plan:
    """Per-listing shape of CatalogInputs before the batched spec read."""

    category: str
    grain: str
    family_id: int | None = None
    model_id: int | None = None
    variant_id: int | None = None
    member_ids: list[int] = field(default_factory=list[int])


def _plan(
    listing: Listing, snapshot: OfferSnapshot | None, edge: ListingResolution | None
) -> _Plan:
    """The grain/target half of evaluate._gather, without the spec read."""
    category = evaluate._dispatch_category(listing, snapshot)
    grain = ResolutionGrain.NONE.value if edge is None else str(edge.grain)
    if edge is None or grain == ResolutionGrain.NONE.value:
        return _Plan(category, ResolutionGrain.NONE.value)
    if grain == ResolutionGrain.FAMILY.value:
        family = edge.product_family
        if family is None:  # unreachable under the grain/target CHECK, as in _gather
            raise ValueError(f"family edge {edge.pk} has no family")
        # member_ids are filled in by the batched family read.
        return _Plan(category, grain, family_id=cast("int", family.pk))
    variant = edge.product_variant
    model = edge.product_model if variant is None else variant.product_model
    if model is None:  # unreachable under the grain/target CHECK, as in _gather
        raise ValueError(f"edge {edge.pk} has no model")
    model_id = cast("int", model.pk)
    return _Plan(
        category,
        grain,
        family_id=evaluate._family_of(model),
        model_id=model_id,
        variant_id=None if variant is None else cast("int", variant.pk),
        member_ids=[model_id],
    )


def live_inputs(listings: Iterable[Listing]) -> dict[int, LiveInputs]:
    """Read the live inputs of many listings in a fixed number of queries:
    latest snapshots, current edges, family members, and one spec read per
    category involved. Keyed by listing id."""
    by_id = {cast("int", listing.pk): listing for listing in listings}
    ids = list(by_id)
    if not ids:
        return {}
    snapshots = _latest_snapshots(ids)
    edges = _current_edges(ids)

    plans: dict[int, _Plan | None] = {}
    for listing_id, listing in by_id.items():
        try:
            plans[listing_id] = _plan(listing, snapshots.get(listing_id), edges.get(listing_id))
        except ValueError:
            # Same inputs evaluate_listing refuses: no current row can exist.
            logger.warning("listing %s: catalog inputs not computable", listing_id)
            plans[listing_id] = None

    family_ids = {
        p.family_id
        for p in plans.values()
        if p is not None and p.grain == ResolutionGrain.FAMILY.value and p.family_id is not None
    }
    members_of: dict[int, list[int]] = defaultdict(list)
    for family_id, model_id in ProductModel.objects.filter(
        product_family_id__in=family_ids
    ).values_list("product_family_id", "pk"):
        members_of[cast("int", family_id)].append(cast("int", model_id))
    for p in plans.values():
        if p is not None and p.grain == ResolutionGrain.FAMILY.value and p.family_id is not None:
            p.member_ids = members_of.get(p.family_id, [])

    wanted: dict[str, set[int]] = defaultdict(set)
    for p in plans.values():
        if p is not None:
            wanted[p.category].update(p.member_ids)
    specs: dict[str, dict[int, evaluate._SpecRow]] = {}
    for category, model_ids in wanted.items():
        spec_model = evaluate._SPEC_MODELS.get(category)
        if spec_model is None or not model_ids:
            continue
        # The spec's pk IS the model id (1:1 satellite), as evaluate._members relies on.
        specs[category] = {
            cast("int", s.pk): s for s in spec_model.objects.filter(product_model_id__in=model_ids)
        }

    result: dict[int, LiveInputs] = {}
    for listing_id, p in plans.items():
        snapshot, edge = snapshots.get(listing_id), edges.get(listing_id)
        if p is None:
            result[listing_id] = LiveInputs(snapshot, edge, None, None)
            continue
        inputs = CatalogInputs(
            p.category,
            p.grain,
            p.family_id,
            p.model_id,
            p.variant_id,
            _member_specs(p.category, p.member_ids, specs.get(p.category, {})),
        )
        result[listing_id] = LiveInputs(snapshot, edge, inputs, catalog_fingerprint(inputs))
    return result


def _member_specs(
    category: str, model_ids: Sequence[int], rows: dict[int, evaluate._SpecRow]
) -> tuple[MemberSpec, ...]:
    # Mirrors evaluate._members: a category with no spec satellite carries an
    # empty value map; a missing spec row is the explicit `absent` marker (None).
    if category not in evaluate._SPEC_MODELS:
        return tuple(MemberSpec(model_id=m, spec={}) for m in sorted(model_ids))
    return tuple(
        MemberSpec(
            model_id=m,
            spec=None if (row := rows.get(m)) is None else evaluate._values_of(row),
        )
        for m in sorted(model_ids)
    )


def binding_for(watch: Watch, live: LiveInputs) -> InputBinding | None:
    """The binding a row of `watch` evaluated now would carry, or None when the
    catalog inputs are not computable (so no stored row can equal it)."""
    if live.fingerprint is None:
        return None
    return InputBinding(
        snapshot_observed_at=None if live.snapshot is None else live.snapshot.observed_at,
        resolution_id=None if live.edge is None else cast("int", live.edge.pk),
        requirement_version=watch.requirement_version,
        # Read through the module at call time, never imported by value: the
        # running evaluator's version is what a stored row must equal.
        evaluator_version=evaluate.EVALUATOR_VERSION,
        catalog_fingerprint=live.fingerprint,
    )


@dataclass(frozen=True)
class RowCurrency:
    """One row's MS2-D-20 currency decision and the live inputs it was made on."""

    evaluation: WatchEvaluation
    live: LiveInputs
    current: bool
    # Binding fields whose stored value differs from the live one; every field
    # when the live binding is not computable. Empty iff `current`.
    stale_fields: tuple[str, ...]


def row_currency(evaluations: Sequence[WatchEvaluation]) -> list[RowCurrency]:
    """Decide currency for many rows with one batched read of their inputs.

    Rows must have `watch` and `listing` loaded (select_related); `watch` must
    be fresh from the database, since its `requirement_version` is one of the
    five values compared.
    """
    lives = live_inputs(e.listing for e in evaluations)
    out: list[RowCurrency] = []
    for e in evaluations:
        live = lives[cast("int", e.listing.pk)]
        want = binding_for(e.watch, live)
        have = stored_binding(e)
        if want is None:
            stale = BINDING_FIELDS
        else:
            stale = tuple(f for f in BINDING_FIELDS if getattr(have, f) != getattr(want, f))
        out.append(RowCurrency(e, live, not stale, stale))
    return out


def candidate_rows(watch_id: int) -> list[WatchEvaluation]:
    """Every evaluation of the watch whose listing is a live offer.

    Delisted and expired listings are excluded here, through Listing.active()
    — the one live-offer predicate — because the evaluator no longer
    re-evaluates them (MS2-D-08), so their rows could only ever be stale noise.
    """
    return list(
        WatchEvaluation.objects.filter(watch_id=watch_id, listing__in=Listing.objects.active())
        .select_related("watch__category", "listing__source_site")
        .order_by("pk")
    )


@dataclass
class EvaluationReport:
    listings: int = 0
    evaluated: int = 0
    skipped: int = 0
    errors: list[int] = field(default_factory=list[int])


def _evaluate_ids(listing_ids: Iterable[int]) -> EvaluationReport:
    report = EvaluationReport()
    for listing_id in listing_ids:
        report.listings += 1
        try:
            result = evaluate_listing(listing_id)
        except Exception:  # one failed listing must not abort the batch
            logger.exception("eligibility evaluation failed for listing %s", listing_id)
            report.errors.append(listing_id)
            continue
        if result.skipped is None:
            report.evaluated += 1
        else:
            report.skipped += 1
    return report


def pending_listing_ids(watch_ids: Sequence[int] | None = None) -> list[int]:
    """Listings with at least one non-current row on an ENABLED watch.

    Disabled watches are left out because evaluate_listing never refreshes
    them: re-evaluating their listings could not make their rows current.
    """
    watches = Watch.objects.filter(enabled=True)
    if watch_ids is not None:
        watches = watches.filter(pk__in=watch_ids)
    pending: dict[int, None] = {}
    for watch_id in watches.order_by("pk").values_list("pk", flat=True):
        for state in row_currency(candidate_rows(cast("int", watch_id))):
            if not state.current:
                pending[cast("int", state.evaluation.listing.pk)] = None
    return list(pending)


def evaluate_pending(watch_ids: Sequence[int] | None = None) -> EvaluationReport:
    """Re-evaluate every listing holding a non-current row (MS2-D-20 repair)."""
    return _evaluate_ids(pending_listing_ids(watch_ids))


def evaluate_all() -> EvaluationReport:
    """Evaluate every live listing against the enabled watches of its category.

    This is the path after a watch is created or edited: a new watch has no
    rows, so `evaluate_pending` cannot reach its listings. The listing's
    category is only known from its latest snapshot's hint, so every live
    listing is visited and `evaluate_listing` does the category filtering.
    """
    if not Watch.objects.filter(enabled=True).exists():
        return EvaluationReport()
    ids = Listing.objects.active().order_by("pk").values_list("pk", flat=True)
    return _evaluate_ids(cast("int", i) for i in ids.iterator())
