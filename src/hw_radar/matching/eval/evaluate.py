"""Approach-A corpus evaluator (design E-2, §5): replay each labeled entry through
the PRODUCTION ingest + resolution path and read the answer back off the database.

Why the full path rather than calling the ladder directly: the gate has to certify
the code that actually writes price history, on the exact input surface the
resolver consumes. A shortcut evaluator would be a second code path, and a second
code path that drifts from production is the ADR-0019 rule-1 hazard this milestone
exists to disprove. So per entry: rebuild a real `ParsedListing` → `fx.stamp` →
`upsert_listing` → `append_snapshot` (attrs verbatim into `attrs_json`, SA-004) →
`CatalogResolver().resolve_listing`.

This module orchestrates; it decides nothing about matching. The only judgement it
owns is label comparison (SA-003), which runs the production normalizers over the
label's display values rather than reimplementing them.

**Isolation is the caller's contract, deliberately.** This module opens, commits,
and rolls back nothing: every write lands in whatever transaction the caller has
open, so an evaluation is undone by rolling back that transaction (or a savepoint
around it). Owning transactions here would fight the resolver, which runs its own
`transaction.atomic` blocks and `select_for_update` locks, and would make a
harness run indistinguishable from real ingestion in the database.

The corollary matters for repeat runs: because `CorpusMeta.observed_at` is fixed,
a second evaluation over leaked state from a first one would re-observe the same
listings (rung 0) and collide on the snapshot's observation key. A repeat
evaluation must therefore start from a rolled-back state — which is exactly what
makes two runs bit-identical.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from hw_radar.acquisition import fx, persist
from hw_radar.acquisition.contracts import ParsedListing
from hw_radar.catalog.models import (
    Listing,
    ListingResolution,
    Manufacturer,
    RetentionClass,
    SourceSite,
)
from hw_radar.matching.eval.corpus import CorpusEntry, CorpusMeta, GroundTruthLabel
from hw_radar.matching.ladder import Outcome
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.matching.types import Grain


class UnknownManufacturerError(ValueError):
    """A label names a `manufacturer_key` absent from the seeded catalog.

    Hard failure, never a skipped entry: an unseeded key can only ever compare
    unequal, so tolerating it would quietly convert a labeling or seeding mistake
    into a precision miss (or, worse, into a shrunken denominator).
    """


@dataclass(frozen=True)
class Prediction:
    """What the production resolver decided about one corpus entry.

    Target fields are the resolver's STORED normalized values, so they compare
    directly against loader-normalized label values (SA-003). `rung` and `outcome`
    come from the current resolution edge's evidence; `grain` and the target come
    from the listing's denormalized current resolution.
    """

    entry_id: str
    source: str
    grain: Grain
    manufacturer_key: str | None
    family_norm: str | None
    model_norm: str | None
    variant_tuple: tuple[str, str, str, str] | None
    rung: int | None
    outcome: Outcome


def seeded_manufacturer_keys() -> set[str]:
    return set(
        Manufacturer.objects.values_list(  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType] - django-types leaves values_list's element type Unknown
            "normalized_name", flat=True
        )
    )


def validate_manufacturer_keys(entries: Iterable[CorpusEntry]) -> None:
    """Raise UnknownManufacturerError if any label names an unseeded manufacturer."""
    seeded = seeded_manufacturer_keys()
    unknown = sorted(
        {
            key
            for entry in entries
            if (key := entry.label.expected_target.manufacturer_key) is not None
            and key not in seeded
        }
    )
    if unknown:
        raise UnknownManufacturerError(
            f"labels name manufacturer keys absent from the seeded catalog: {', '.join(unknown)}"
        )


def _ingest(entry: CorpusEntry, observed_at: datetime) -> Listing:
    site, _ = SourceSite.objects.get_or_create(
        normalized_name=entry.source, defaults={"name": entry.source}
    )
    parsed = ParsedListing(
        source_listing_key=entry.listing.source_listing_key,
        url=entry.listing.url,
        title=entry.title,
        price=entry.listing.price,
        currency=entry.listing.currency,
        condition_label=entry.listing.condition_label,
        attrs=dict(entry.listing.attrs),
    )
    # Corpus v1 is USD-only (schema-enforced), so this stamp is the identity stamp
    # and never reads the FX cache — evaluation stays independent of rate data.
    normalized = fx.stamp(parsed, observed_at.date())
    listing, _created = persist.upsert_listing(site, normalized, RetentionClass.MERCHANT_FACT)
    persist.append_snapshot(listing, normalized, observed_at=observed_at)
    return listing


def _current_edge(listing: Listing) -> ListingResolution | None:
    # Typed boundary: django-types has no reverse-FK manager stub for
    # ListingResolution.listing's related_name, and an inline ignore at the call
    # site would leave every downstream `.evidence` read Unknown too.
    return listing.resolutions.filter(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue]
        is_current=True
    ).first()


def _outcome_of(edge: ListingResolution | None) -> tuple[int | None, Outcome]:
    if edge is None:
        return None, Outcome.NONE
    rung = edge.evidence.get("rung")
    outcome = edge.evidence.get("outcome")
    return (
        rung if isinstance(rung, int) else None,
        Outcome(outcome) if isinstance(outcome, str) else Outcome.NONE,
    )


def _read_back(entry: CorpusEntry, listing: Listing) -> Prediction:
    listing.refresh_from_db()
    rung, outcome = _outcome_of(_current_edge(listing))
    manufacturer_key: str | None = None
    family_norm: str | None = None
    model_norm: str | None = None
    variant_tuple: tuple[str, str, str, str] | None = None
    variant = listing.product_variant
    model = variant.product_model if variant is not None else listing.product_model
    if variant is not None:
        variant_tuple = (
            variant.condition,
            variant.packaging,
            variant.recert_channel,
            variant.warranty_channel,
        )
    if model is not None:
        manufacturer_key = model.manufacturer.normalized_name
        model_norm = model.normalized_model_number
        family = model.product_family
        family_norm = family.normalized_name if family is not None else None
    elif listing.product_family is not None:
        manufacturer_key = listing.product_family.manufacturer.normalized_name
        family_norm = listing.product_family.normalized_name
    return Prediction(
        entry_id=entry.id,
        source=entry.source,
        grain=Grain(listing.resolution_grain),
        manufacturer_key=manufacturer_key,
        family_norm=family_norm,
        model_norm=model_norm,
        variant_tuple=variant_tuple,
        rung=rung,
        outcome=outcome,
    )


def evaluate_corpus(entries: Sequence[CorpusEntry], meta: CorpusMeta) -> list[Prediction]:
    """Run every entry through the production path and return one Prediction each.

    Observation time comes from `meta.observed_at` — the corpus manifest's fixed
    UTC instant, never "now" — and is threaded to both the FX stamp's observation
    date and every snapshot, so a re-run on any later date reproduces the same
    predictions bit for bit.

    Transaction management belongs to the caller (see the module docstring): this
    function neither begins nor rolls back anything.

    Raises UnknownManufacturerError before writing anything if a label names an
    unseeded manufacturer — a seeding mistake must abort the run, not degrade it
    into a precision miss.
    """
    validate_manufacturer_keys(entries)
    resolver = CatalogResolver()
    predictions: list[Prediction] = []
    for entry in entries:
        listing = _ingest(entry, meta.observed_at)
        resolver.resolve_listing(listing.pk)
        predictions.append(_read_back(entry, listing))
    return predictions


def prediction_matches(prediction: Prediction, label: GroundTruthLabel) -> bool:
    """Is the prediction correct at the labeled grain (SA-003)?

    Grain must match exactly — a family-grain accept on a model-grain label is
    wrong, not partially right, because the price history it feeds is coarser than
    the label asserts. Target comparison is then: `manufacturer_key` exact against
    the stored controlled key; `family` / `model_number` through the production
    normalizers; variant identity as the exact 4-tuple of enum values, so two
    listings of one model that differ only by condition can never both be "right".

    `family` is compared only when the label asserts one: it is optional at model
    and variant grain (a seeded ProductModel may carry no family), but an asserted
    family is compared strictly — a missing predicted family is a miss, not a pass.
    """
    if prediction.grain is not label.expected_grain:
        return False
    target = label.expected_target
    if label.expected_grain is Grain.NONE:
        return True
    if prediction.manufacturer_key != target.manufacturer_key:
        return False
    if target.family is not None and prediction.family_norm != target.family_norm:
        return False
    if target.model_number is not None and prediction.model_norm != target.model_norm:
        return False
    return not (
        target.variant is not None and prediction.variant_tuple != target.variant.as_tuple()
    )
