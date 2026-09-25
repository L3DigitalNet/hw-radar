"""Stage-level drivers for the D10 ordering, retention, and continuity tests.

These call the extracted transactional stages (acquisition.stages) directly, in
the same two-step shape the local pipeline and the importer use, so one test can
replay an arbitrary interleaving of observations and sweeps at chosen event times
without standing up an Actor run or an adapter per step. The stages are the unit
under test: run_collection and the importer only compose them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

from hw_radar.acquisition.contracts import DelistScope, NormalizedListing, RawBatch, RawItem
from hw_radar.acquisition.persist import ObservationRetention
from hw_radar.acquisition.stages import (
    PersistResult,
    apply_absence,
    atomic_with_retry,
    ensure_watermark_rows,
    persist_observations,
    target_scopes,
)
from hw_radar.catalog.models import (
    BOUNDED_RETENTION_CLASSES,
    Listing,
    RetentionClass,
    SourceConfig,
    SourceSite,
    SourceTier,
    SourceType,
)

T0: Final = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
HOUR: Final = timedelta(hours=1)
SIX_HOURS: Final = timedelta(hours=6)


def make_site(key: str = "d10order", *, with_config: bool = True) -> SourceSite:
    """A D10-local site; with_config gives it the FULL lane row the NULL scope needs."""
    site = SourceSite.objects.create(name=key, normalized_name=key, source_type=SourceType.OTHER)
    if with_config:
        SourceConfig.objects.create(
            source_site=site,
            tier=SourceTier.T2_SPECIALIST,
            domain=f"{key}.invalid",
            cadence_baseline_s=3600,
            cadence_ceiling_s=900,
        )
    return site


def record(
    key: str,
    *,
    title: str = "Drive",
    scope: str | None = None,
    condition: str = "",
    international: bool = False,
    price: str = "100.00",
) -> NormalizedListing:
    return NormalizedListing(
        source_listing_key=key,
        url=f"https://order.invalid/{key}",
        title=title,
        price=Decimal(price),
        condition_label=condition,
        collection_scope=scope,
        fx_rate=Decimal(1),
        fx_pair="USD/USD",
        fx_rate_date=T0.date(),
        fx_source="identity",
        is_international=international,
    )


def retention_for(
    retention_class: RetentionClass, observed_at: datetime, ttl: timedelta = SIX_HOURS
) -> ObservationRetention:
    bounded = retention_class in BOUNDED_RETENTION_CLASSES
    return ObservationRetention(retention_class, observed_at + ttl if bounded else None)


def observe(
    site: SourceSite,
    at: datetime,
    records: list[NormalizedListing],
    retention_class: RetentionClass = RetentionClass.MERCHANT_FACT,
) -> PersistResult:
    """Run one persist transaction for `records` observed at `at`."""
    batch = RawBatch(
        source="order",
        fetched_at=at,
        items=[RawItem(url="https://order.invalid/page", payload_json={"at": at.isoformat()})],
    )
    ensure_watermark_rows(site, target_scopes(site, records) | {None})
    return atomic_with_retry(
        lambda: persist_observations(site, batch, records, retention_for(retention_class, at)),
        label="test persist",
    )


def sweep(
    site: SourceSite,
    at: datetime,
    *,
    scope_key: str | None,
    seen: set[str],
    complete: bool = True,
    eligible: bool = True,
    delist: bool = True,
    grace: timedelta = HOUR,
) -> int:
    """Run one delist transaction: continuity for `scope_key`, then its gated delist."""
    gated = (
        DelistScope(
            seen_keys=frozenset(seen),
            observed_at=at,
            complete=complete,
            absence_grace=grace,
            scope_key=scope_key,
        )
        if delist
        else None
    )
    ensure_watermark_rows(site, {scope_key, None})
    return atomic_with_retry(
        lambda: apply_absence(
            site, swept_scope_key=scope_key, eligible=eligible, gated=gated, event_time=at
        ),
        label="test sweep",
    )


def listing(site: SourceSite, key: str) -> Listing:
    return Listing.objects.get(source_site=site, source_listing_key=key)
