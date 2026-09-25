"""Transactional ingestion stages shared by the local pipeline and the Apify importer.

MS2-D-22 *Code shape*: the persist and delist steps are extracted from
run_collection so the local path and the durable importer compose the same
functions. Each function here runs INSIDE one database transaction that its
caller opens through atomic_with_retry, and each takes its locks in the one
total order of MS2-D-35 (revision 10, ED-05):

  provider_run (importer only) -> FULL SourceLaneState row -> ScopeSweepContinuity
  rows by collection_scope -> existing Listing rows by pk -> inserts of new keys
  by source_listing_key -> child rows of a locked listing.

The watermark rows (the FULL lane row and each touched scope row) are created by
ensure_watermark_rows in their own committed transaction BEFORE the locking
transaction starts. An all-NULL watermark row decides nothing, so committing it
early is safe; creating it inside the locking transaction would hold its
unique-index entry to the outer commit and make a concurrent creator of the same
key wait out of order (MS2-D-35 *Row creation before locking*).

No function here awaits: callers run them through sync_to_async so no
transaction ever spans an await.

Requirements: PostgreSQL. The in-memory retry keys on SQLSTATEs (40P01, 40001,
23505), and the candidate re-check relies on READ COMMITTED re-evaluating the
WHERE clause against the locked row version.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from django.db import DatabaseError, transaction
from django.db.models import Q

from hw_radar.acquisition.contracts import DelistScope, NormalizedListing, RawBatch
from hw_radar.acquisition.persist import (
    ObservationRetention,
    mark_absent,
    observe_listing,
    store_raw,
)
from hw_radar.catalog.models import (
    DelistReason,
    Listing,
    RawPayload,
    RunKind,
    RunStatus,
    SchedulingLane,
    ScopeSweepContinuity,
    ScraperRun,
    SourceConfig,
    SourceLaneState,
    SourceSite,
)

logger = logging.getLogger(__name__)

# Retries after the first attempt (MS2-D-35 *In-memory retry*): a fourth
# retryable abort exhausts the invocation.
MAX_IN_MEMORY_RETRIES: Final = 3
# Deadlock, serialization failure, and a concurrent insert of the same key.
RETRYABLE_SQLSTATES: Final = frozenset({"40P01", "40001", "23505"})

# Floor on the CR-004 continuity tolerance; see acquisition.pipeline, which
# re-exports it under the same name for existing importers.
MIN_CONTINUITY_TOLERANCE: Final = timedelta(minutes=15)


class RetryExhausted(RuntimeError):
    """Every in-memory attempt of one transaction hit a retryable abort.

    The last attempt rolled back, so no partial effect exists. The importer
    records the exhaustion and backs off; the local path lets it end the run
    like a crash in that transaction (MS2-D-35, D10).
    """


def sqlstate_of(exc: BaseException) -> str | None:
    """Return the PostgreSQL SQLSTATE behind a Django DatabaseError, if any.

    Django wraps the driver error and chains it as __cause__; psycopg 3 names
    the code `sqlstate`, psycopg 2 `pgcode`, so both are read.
    """
    current: BaseException | None = exc
    while current is not None:
        for attr in ("sqlstate", "pgcode"):
            code = getattr(current, attr, None)
            if isinstance(code, str):
                return code
        current = current.__cause__ or current.__context__
    return None


def atomic_with_retry[T](work: Callable[[], T], *, label: str) -> T:
    """Run `work` in one transaction, retrying retryable aborts in memory.

    `work` must rebuild every database-derived value itself on each call and
    must not mutate its captured inputs: a rollback restores the database, not
    Python state, so reusing an aborted attempt's objects or accumulators
    would double-count (MS2-D-35 *Clean state per attempt*). Must be called
    outside any open transaction; nested in one it would only roll back to a
    savepoint while the outer transaction still holds the conflicting locks.
    Raises RetryExhausted after MAX_IN_MEMORY_RETRIES retries; any other error
    propagates unchanged.
    """
    last: DatabaseError | None = None
    for attempt in range(MAX_IN_MEMORY_RETRIES + 1):
        try:
            with transaction.atomic():
                return work()
        except DatabaseError as exc:
            code = sqlstate_of(exc)
            if code not in RETRYABLE_SQLSTATES:
                raise
            last = exc
            logger.info("%s: retryable abort %s on attempt %d", label, code, attempt + 1)
    logger.warning("%s: in-memory retries exhausted", label)
    raise RetryExhausted(label) from last


# ── Watermark rows ──────────────────────────────────────────────────────────


def full_lane_state(site: SourceSite) -> SourceLaneState | None:
    """Return the site's FULL lane row, creating it if absent; None without a SourceConfig.

    Sites without a SourceConfig (isolation tests, the Actor-only synthetic
    site) have no NULL-scope watermarks; every NULL-scope guard then reads "no
    bound" and continuity for the NULL scope is never proven.
    """
    config = SourceConfig.objects.filter(source_site=site).first()
    if config is None:
        return None
    return config.lane_state(SchedulingLane.FULL)


def ensure_watermark_rows(site: SourceSite, scope_keys: Iterable[str | None]) -> None:
    """Create the FULL lane row and each non-NULL scope row, committed, before locking.

    Runs in autocommit (each get_or_create is its own short transaction); see
    the module docstring for why this must not happen inside the locking one.
    """
    full_lane_state(site)
    for key in sorted({k for k in scope_keys if k is not None}):
        ScopeSweepContinuity.objects.get_or_create(source_site=site, collection_scope=key)


def target_scopes(site: SourceSite, normalized: list[NormalizedListing]) -> set[str | None]:
    """Return every scope a batch's observations target (plain read, no locks).

    A scope-less observation targets the scope its existing row already has,
    because it never rewrites collection_scope (MS2-D-12); a new scope-less
    key targets the NULL scope.
    """
    scopes: set[str | None] = {n.collection_scope for n in normalized if n.collection_scope}
    scopeless = [n.source_listing_key for n in normalized if n.collection_scope is None]
    if scopeless:
        existing = dict(
            Listing.objects.filter(source_site=site, source_listing_key__in=scopeless).values_list(
                "source_listing_key", "collection_scope"
            )
        )
        scopes.update(existing.get(key) for key in scopeless)
    return scopes


@dataclass
class _LockedScopes:
    lane: SourceLaneState | None
    rows: dict[str, ScopeSweepContinuity]

    def complete_sweep_at(self, site: SourceSite, scope_key: str | None) -> datetime | None:
        if scope_key is None:
            return None if self.lane is None else self.lane.last_complete_sweep_at
        row = self.rows.get(scope_key)
        if row is None:
            # A scope a concurrent writer moved a row into after this
            # transaction chose its scope set: read its watermark unlocked
            # rather than lock it out of order. The watermark only rises, so
            # a stale read can at worst keep an observation eligible that a
            # concurrent complete sweep is about to delist anyway.
            row = ScopeSweepContinuity.objects.filter(
                source_site=site, collection_scope=scope_key
            ).first()
        return None if row is None else row.last_complete_sweep_at


def _lock_scopes(site: SourceSite, scope_keys: Iterable[str | None]) -> _LockedScopes:
    # Lock order items 4 then 5 (MS2-D-35): the FULL lane row first, always,
    # then the non-NULL scope rows sorted by key.
    lane: SourceLaneState | None = None
    config = SourceConfig.objects.filter(source_site=site).first()
    if config is not None:
        lane = (
            SourceLaneState.objects.select_for_update()
            .filter(source_config=config, lane=SchedulingLane.FULL)
            .first()
        )
    keys = sorted({k for k in scope_keys if k is not None})
    rows = {
        row.collection_scope: row
        for row in ScopeSweepContinuity.objects.select_for_update()
        .filter(source_site=site, collection_scope__in=keys)
        .order_by("collection_scope")
    }
    return _LockedScopes(lane=lane, rows=rows)


# ── Persist (import stage 1 / the local persist step) ───────────────────────


@dataclass(frozen=True)
class PersistResult:
    """Attempt-local counts of one persist transaction.

    listing_ids repeats a pk once per record, as the MS-1 _persist_all did, so
    downstream tallies (grain counts) still sum to records_valid.
    """

    listing_ids: list[int]
    upserted: int
    appended: int


def persist_observations(
    site: SourceSite,
    batch: RawBatch,
    normalized: list[NormalizedListing],
    retention: ObservationRetention,
) -> PersistResult:
    """Store raw payloads and apply every observation of one batch, under locks.

    Must run inside a transaction (atomic_with_retry) after
    ensure_watermark_rows. observed_at is the batch's fetched_at, the instant
    the offer was observed, so it matches RawPayload.fetched_at and the FX
    stamping basis and stored payloads replay faithfully.
    """
    observed_at = batch.fetched_at
    scopes = _lock_scopes(site, target_scopes(site, normalized))
    keys = sorted({n.source_listing_key for n in normalized})
    locked: dict[str, Listing] = {
        listing.source_listing_key: listing
        for listing in Listing.objects.select_for_update()
        .filter(source_site=site, source_listing_key__in=keys)
        .order_by("pk")
    }
    # CR-002: every RawItem is stored, not just items[0], and raws is a list so
    # duplicate source URLs each keep their own stored row.
    raws = [
        store_raw(
            item,
            fetched_at=batch.fetched_at,
            retention_class=retention.retention_class,
            expires_at=retention.expires_at,
        )
        for item in batch.items
    ]
    by_url: dict[str, RawPayload] = {}
    for item, raw in zip(batch.items, raws, strict=True):
        by_url.setdefault(item.url, raw)
    # Single-item batches may leave raw_url unset; the one payload must be it.
    sole = raws[0] if len(raws) == 1 else None

    def observe(record: NormalizedListing) -> int:
        existing = locked.get(record.source_listing_key)
        target = record.collection_scope
        if target is None and existing is not None:
            target = existing.collection_scope
        result = observe_listing(
            site,
            record,
            retention,
            observed_at=observed_at,
            existing=existing,
            scope_complete_sweep_at=scopes.complete_sweep_at(site, target),
            raw=by_url.get(record.raw_url) or sole,
        )
        locked[record.source_listing_key] = result.listing
        return 1 if result.snapshot is not None else 0

    # Lock order item 7: new keys are inserted in source_listing_key order, after
    # every existing-row lock is held, each from its first record in the batch.
    first_new: dict[str, int] = {}
    for index, record in enumerate(normalized):
        if record.source_listing_key not in locked:
            first_new.setdefault(record.source_listing_key, index)
    appended = 0
    for key in sorted(first_new):
        appended += observe(normalized[first_new[key]])
    created_from = set(first_new.values())
    for index, record in enumerate(normalized):
        if index not in created_from:
            appended += observe(record)
    listing_ids = [locked[record.source_listing_key].pk for record in normalized]
    return PersistResult(listing_ids=listing_ids, upserted=len(normalized), appended=appended)


# ── Continuity and absence (import stage 2 / the local delist step) ─────────


def _tolerance(lane: SourceLaneState | None) -> timedelta:
    interval = 0 if lane is None else lane.current_interval_s
    return max(timedelta(seconds=2 * interval), MIN_CONTINUITY_TOLERANCE)


def record_continuity(
    site: SourceSite,
    observed_at: datetime,
    *,
    scope_key: str | None,
    lane: SourceLaneState | None,
    scope_row: ScopeSweepContinuity | None,
) -> datetime | None:
    """Fold one eligible sweep of `scope_key` into that scope's continuity (MS2-D-36).

    `lane` and `scope_row` are already locked by the caller. Returns the start
    of the scope's current uninterrupted run of eligible sweeps (the instant
    the CR-004 grace is measured from), or None when continuity is unproven:
    the NULL scope of a site without a SourceConfig, a sweep at or before the newest break (ties go to the
    break, failing closed), or a sweep older than the newest recorded one.

    The NULL scope keeps the ScraperRun previous-run lookup (MS2-D-36 rejected
    alternative (a)): the frozen eBay tests simulate pauses by backdating
    ScraperRun.started_at. The current run is still RUNNING here, so
    status=SUCCESS excludes it. A non-NULL scope reads only its own row's
    last_eligible_sweep_at, never another scope's runs (MS2-D-31).
    """
    if scope_key is None and lane is None:
        return None
    row: SourceLaneState | ScopeSweepContinuity = (
        _need_lane(lane) if scope_key is None else _need(scope_row)
    )
    if row.continuity_broken_at is not None and observed_at <= row.continuity_broken_at:
        return None
    if row.last_eligible_sweep_at is not None and observed_at < row.last_eligible_sweep_at:
        return None
    if scope_key is None:
        previous: datetime | None = (
            ScraperRun.objects.filter(
                source_site=site, run_kind=RunKind.FULL, status=RunStatus.SUCCESS
            )
            .order_by("-started_at")
            .values_list("started_at", flat=True)
            .first()
        )
    else:
        previous = row.last_eligible_sweep_at
    if (
        row.continuous_since is None
        or previous is None
        or observed_at - previous > _tolerance(lane)
    ):
        row.continuous_since = observed_at
    row.last_eligible_sweep_at = observed_at
    row.save(update_fields=["continuous_since", "last_eligible_sweep_at", "updated_at"])
    return row.continuous_since


def break_continuity(
    event_time: datetime,
    *,
    scope_key: str | None,
    lane: SourceLaneState | None,
    scope_row: ScopeSweepContinuity | None,
) -> None:
    """End a scope's continuity at `event_time` (MS2-D-36 *Break*); rows already locked.

    Always applies, even when event_time is older than the current start: a
    late break costs one grace of stale absence, never a false delist. Merely
    skipping the record is not enough (plan review F-04): the NULL scope's
    previous-run lookup counts every successful FULL run, so a string of
    ineligible runs would otherwise read as unbroken polling.
    """
    if scope_key is None and lane is None:
        return
    row: SourceLaneState | ScopeSweepContinuity = (
        _need_lane(lane) if scope_key is None else _need(scope_row)
    )
    row.continuous_since = None
    if row.continuity_broken_at is None or row.continuity_broken_at < event_time:
        row.continuity_broken_at = event_time
    row.save(update_fields=["continuous_since", "continuity_broken_at", "updated_at"])


def _need_lane(lane: SourceLaneState | None) -> SourceLaneState:
    assert lane is not None  # callers return early for a NULL scope without a lane
    return lane


def _need(row: ScopeSweepContinuity | None) -> ScopeSweepContinuity:
    if row is None:
        # ensure_watermark_rows runs before every locking transaction, so a
        # missing row is a caller that skipped it, not a data condition.
        raise RuntimeError("scope_sweep_continuity row was not ensured before locking")
    return row


def apply_delist(site: SourceSite, scope: DelistScope, continuous_since: datetime | None) -> int:
    """Soft-delete this site's active listings in `scope` that the sweep contradicts.

    Must run inside a transaction. Per-row rather than a bulk update():
    mark_delisted pulls the DR-008 evidence TTLs forward and redacts
    delete-on-delist content, which a queryset update would skip.

    CR-004 continuity invariant: ABSENT_STALE needs both that the listing went
    unseen for the grace and that the scope was polled continuously across it
    (`continuous_since`); a complete sweep is direct evidence and needs neither.

    Ordering guard (MS2-D-30, revision 10 ED-06): a listing observed by a
    newer run (last_observed_at > scope.observed_at) is never delisted by this
    older sweep. Candidates are locked with select_for_update().order_by("pk")
    and the FULL predicate stays in the WHERE clause, so under READ COMMITTED
    Postgres re-evaluates it on the locked row version: a concurrent import that
    moved a candidate into another scope, or observed it later, drops it here.
    A None scope_key means collection_scope IS NULL, never "all scopes".
    """
    if scope.complete:
        reason = DelistReason.ABSENT_FROM_SWEEP
        stale_cutoff: datetime | None = None
    else:
        if continuous_since is None or scope.observed_at - continuous_since < scope.absence_grace:
            logger.info(
                "delist stage for %s: skipping stale-absence marks — scope %s has only "
                "been polled continuously since %s, short of the %s absence grace",
                site.normalized_name,
                scope.scope_key,
                continuous_since,
                scope.absence_grace,
            )
            return 0
        reason = DelistReason.ABSENT_STALE
        stale_cutoff = scope.observed_at - scope.absence_grace
    candidates = (
        Listing.objects.select_for_update()
        .filter(source_site=site, delisted_at__isnull=True)
        .exclude(source_listing_key__in=scope.seen_keys)
        .filter(Q(last_observed_at__isnull=True) | Q(last_observed_at__lte=scope.observed_at))
    )
    if scope.scope_key is None:
        candidates = candidates.filter(collection_scope__isnull=True)
    else:
        candidates = candidates.filter(collection_scope=scope.scope_key)
    if stale_cutoff is not None:
        candidates = candidates.filter(last_seen__lt=stale_cutoff)
    delisted = 0
    for listing in candidates.order_by("pk"):
        if mark_absent(listing, reason, when=scope.observed_at):
            delisted += 1
    return delisted


def apply_absence(
    site: SourceSite,
    *,
    swept_scope_key: str | None,
    eligible: bool,
    gated: DelistScope | None,
    event_time: datetime,
) -> int:
    """Record or break continuity for the swept scope, then apply the gated delist.

    Must run inside a transaction after ensure_watermark_rows(site,
    {swept_scope_key, None}). Called only for successful FULL runs; PROBE runs
    skip absence, continuity, and the watermark (existing rule).

    - eligible: record continuity for the swept scope at event_time; else break.
    - A run that swept a non-NULL scope also breaks the NULL scope at its own
      time (MS2-D-31), so a scoped run can never serve as the NULL scope's
      predecessor in the site-wide ScraperRun lookup.
    - A gated complete scope raises that scope's complete-sweep watermark in
      the same transaction as its ABSENT_FROM_SWEEP marks (MS2-D-35),
      complete-empty and zero-delist sweeps included.
    Returns the number of listings delisted.
    """
    scopes = _lock_scopes(site, [swept_scope_key])
    scope_row = None if swept_scope_key is None else scopes.rows.get(swept_scope_key)
    continuous_since: datetime | None = None
    if eligible:
        continuous_since = record_continuity(
            site, event_time, scope_key=swept_scope_key, lane=scopes.lane, scope_row=scope_row
        )
    else:
        break_continuity(
            event_time, scope_key=swept_scope_key, lane=scopes.lane, scope_row=scope_row
        )
    if swept_scope_key is not None:
        break_continuity(event_time, scope_key=None, lane=scopes.lane, scope_row=None)
    if gated is None:
        return 0
    delisted = apply_delist(site, gated, continuous_since)
    if gated.complete:
        _raise_complete_sweep(gated, lane=scopes.lane, scope_row=scope_row)
    return delisted


def _raise_complete_sweep(
    gated: DelistScope, *, lane: SourceLaneState | None, scope_row: ScopeSweepContinuity | None
) -> None:
    row: SourceLaneState | ScopeSweepContinuity | None = (
        lane if gated.scope_key is None else scope_row
    )
    if row is None:
        return
    if row.last_complete_sweep_at is None or row.last_complete_sweep_at < gated.observed_at:
        row.last_complete_sweep_at = gated.observed_at
        row.save(update_fields=["last_complete_sweep_at", "updated_at"])
