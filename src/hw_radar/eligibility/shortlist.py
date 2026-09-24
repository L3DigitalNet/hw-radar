"""Shortlist and review-queue read model (MS-2 Slice C4; MS2-D-01, MS2-D-09,
MS2-D-20).

- `shortlist(watch_id)` returns the watch's CURRENT `match` rows on live
  listings, ordered by landed USD ascending (listings with no USD price last,
  then by listing id), each with `freshness` and the soft `meets_target`
  annotation.
- `review_queue(watch_id)` returns, on live listings, every current `unknown`
  row (`state="unknown"`) and every non-current row of any verdict
  (`state="pending"`), each with the binding fields that went stale.

Invariants: a non-current row never reaches the shortlist, whatever its
stored verdict (MS2-D-20); an `unknown` never counts as a match (FR-014); a
delisted or expired listing appears in neither list. Currency is decided by
`service.row_currency`, the one batched predicate `evaluate_watches --pending`
also uses.

Deliberately NO score: there is no ADR-0011 scoring artifact and no
cross-category ranking in MS-2 (ADR 0022; R-MS2-10) — the order is price
within one watch. `tests/db/test_shortlist.py` guards both the row shape and
the package's imports.

Freshness (a labeled, tunable assumption, plan C4): `stale` when the listing's
latest snapshot is older than STALE_CADENCE_MULTIPLE x its source's
`cadence_baseline_s`, or when there is no snapshot or no source config to
judge by; else `fresh`. Slice E adds `budget_paused`. The age is observation
time measured against processing `now`; neither clock is derived from the
other (MS2-D-39).
"""

# pyright: reportPrivateUsage=false
# evaluate._offer_facts is shared on purpose: meets_target and landed_usd must
# read the offer exactly as the hard price clause did.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, cast

from django.utils import timezone

from hw_radar.catalog.models import EligibilityVerdict, SourceConfig, Watch
from hw_radar.eligibility import evaluate
from hw_radar.eligibility.service import RowCurrency, candidate_rows, row_currency
from hw_radar.matching.normalize import canonicalize_title

# Assumption (plan C4): two baseline cadences without a new observation is
# the point where a row's price can no longer be read as live.
STALE_CADENCE_MULTIPLE: Final = 2


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"


class ReviewState(StrEnum):
    UNKNOWN = "unknown"
    PENDING = "pending"


@dataclass(frozen=True)
class ShortlistRow:
    evaluation_id: int
    watch_id: int
    listing_id: int
    source: str
    title: str
    url: str
    # Item + stated shipping + stated tax, in USD at the snapshot's FX stamp —
    # the price the hard clause judged (evaluate.OfferFacts.price_usd). None
    # when the snapshot has no FX stamp.
    landed_usd: Decimal | None
    shipping_known: bool
    # The soft target (Watch.target_unit_price_usd) judged with the hard price
    # clause's unit-price rule: True/False, or None when the watch has no
    # target or the unit price is not provable (unstated shipping, uncertain
    # lot quantity). Annotation only; it never changes membership (MS2-D-07).
    meets_target: bool | None
    freshness: Freshness
    observed_at: datetime | None
    evaluated_at: datetime
    reasons: Sequence[Mapping[str, object]]


@dataclass(frozen=True)
class ReviewRow:
    evaluation_id: int
    watch_id: int
    listing_id: int
    source: str
    title: str
    url: str
    state: ReviewState
    # The STORED verdict. For a pending row it describes inputs that have since
    # changed, so it must not be read as the listing's current eligibility.
    verdict: EligibilityVerdict
    # Binding fields (service.BINDING_FIELDS) whose stored value no longer
    # matches the live one; empty for a current `unknown` row.
    stale_fields: tuple[str, ...]
    snapshot_observed_at: datetime | None
    evaluated_at: datetime
    reasons: Sequence[Mapping[str, object]]


def _baselines(states: Sequence[RowCurrency]) -> dict[int, int]:
    site_ids = {cast("int", s.evaluation.listing.source_site.pk) for s in states}
    return {
        cast("int", site_id): cast("int", baseline)
        for site_id, baseline in SourceConfig.objects.filter(
            source_site_id__in=site_ids
        ).values_list("source_site_id", "cadence_baseline_s")
    }


def _freshness(observed_at: datetime | None, baseline_s: int | None, now: datetime) -> Freshness:
    # Unknown cadence or no observation cannot vouch for a live price, so it
    # reads as stale rather than fresh.
    if observed_at is None or baseline_s is None:
        return Freshness.STALE
    limit = timedelta(seconds=STALE_CADENCE_MULTIPLE * baseline_s)
    return Freshness.STALE if now - observed_at > limit else Freshness.FRESH


def _meets_target(watch: Watch, facts: evaluate.OfferFacts) -> bool | None:
    target = watch.target_unit_price_usd
    if target is None:
        return None
    clause = evaluate.price_clause(target, facts, evaluate.policy_for(watch.category.slug))
    if clause.outcome == EligibilityVerdict.MATCH:
        return True
    if clause.outcome == EligibilityVerdict.NO_MATCH:
        return False
    return None


def _sort_key(row: ShortlistRow) -> tuple[bool, Decimal, int]:
    return (row.landed_usd is None, row.landed_usd or Decimal(0), row.listing_id)


def shortlist(watch_id: int) -> list[ShortlistRow]:
    """The watch's current `match` rows on live listings, cheapest first."""
    states = row_currency(candidate_rows(watch_id))
    matches = [
        s for s in states if s.current and s.evaluation.verdict == EligibilityVerdict.MATCH.value
    ]
    baselines = _baselines(matches)
    now = timezone.now()
    rows: list[ShortlistRow] = []
    for s in matches:
        e, listing, snapshot = s.evaluation, s.evaluation.listing, s.live.snapshot
        # Must equal evaluate_listing's canonical offer text, or quantity and
        # condition (and so meets_target) would be read differently here.
        canonical = canonicalize_title(f"{listing.title_raw} {listing.condition_label_raw}".strip())
        facts = evaluate._offer_facts(listing, snapshot, canonical)
        observed_at = None if snapshot is None else snapshot.observed_at
        rows.append(
            ShortlistRow(
                evaluation_id=cast("int", e.pk),
                watch_id=cast("int", e.watch.pk),
                listing_id=cast("int", listing.pk),
                source=listing.source_site.normalized_name,
                title=listing.title_raw,
                url=listing.canonical_url,
                landed_usd=facts.price_usd,
                shipping_known=facts.shipping_known,
                meets_target=_meets_target(e.watch, facts),
                freshness=_freshness(
                    observed_at, baselines.get(cast("int", listing.source_site.pk)), now
                ),
                observed_at=observed_at,
                evaluated_at=e.evaluated_at,
                reasons=e.reasons,
            )
        )
    rows.sort(key=_sort_key)
    return rows


def review_queue(watch_id: int) -> list[ReviewRow]:
    """Current `unknown` rows and every non-current row, on live listings,
    ordered by listing id."""
    rows: list[ReviewRow] = []
    for s in row_currency(candidate_rows(watch_id)):
        e = s.evaluation
        if s.current and e.verdict != EligibilityVerdict.UNKNOWN.value:
            continue
        listing = e.listing
        rows.append(
            ReviewRow(
                evaluation_id=cast("int", e.pk),
                watch_id=cast("int", e.watch.pk),
                listing_id=cast("int", listing.pk),
                source=listing.source_site.normalized_name,
                title=listing.title_raw,
                url=listing.canonical_url,
                state=ReviewState.UNKNOWN if s.current else ReviewState.PENDING,
                verdict=EligibilityVerdict(e.verdict),
                stale_fields=s.stale_fields,
                snapshot_observed_at=e.snapshot_observed_at,
                evaluated_at=e.evaluated_at,
                reasons=e.reasons,
            )
        )
    rows.sort(key=lambda r: r.listing_id)
    return rows
