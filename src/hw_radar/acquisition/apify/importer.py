"""Durable staged import of one terminal Actor run (plan D10; MS2-D-22, MS2-D-23).

import_provider_run drives one ProviderRun through the MS2-D-22 state machine

  pending -> observations_committed -> absence_applied -> resolved -> evaluated
  -> finalized, with `rejected` the only other terminal state,

and returns the state it left the row in. Every transition is a compare-and-set
under select_for_update on the provider_run row, and every stage's effects are
idempotent, so a duplicate executor (two poll ticks, a restarted poller) stops
at the compare-and-set and a crash at any point resumes from the recorded
state. Only stage 1 needs the dataset.

- Claim (own transaction): creates the ScraperRun (RUNNING, started_at =
  the run's startedAt) once, links it, and counts the attempt; every retry
  reuses that ScraperRun (MS2-D-13).
- Stage 1: retention preconditions (MS2-D-33) and the read cap (MS2-D-32) are
  checked before any read; each read is counted and committed BEFORE it is
  sent, so a process loss before the stage-1 commit makes the next
  invocation's read another counted read, while the in-memory retry of the
  stage-1 transaction reuses the batch already in memory and never re-reads.
  A `failed` classification is rejected before anything is persisted.
- Stage 2: continuity for the admitted scope, the NULL-scope break, and the
  gated scoped delist (acquisition.stages.apply_absence).
- Stages 3 and 4: resolve and evaluate outside any transaction; errors are
  counted in stage_detail and never block (C.3, MS2-D-20).
- Stage 5 and Reject: outcome transactions that set the ScraperRun terminal
  and apply apply_run_outcome exactly once, guarded by the compare-and-set.

SCOPE: selection, backoff scheduling and storage cleanup belong to the jobs
(D5) and cleanup (D11). This module neither polls the run nor deletes storage;
it honours next_attempt_at only by writing it on retry exhaustion. The overrun
latch (MS2-D-22 *Overrun blocks repair reads*) is Slice E's and is not
consulted here yet.

Requirements: PostgreSQL (acquisition.stages) and a Django context. The
read caps are read from settings when defined (HW_RADAR_APIFY_MAX_DATASET_READS,
HW_RADAR_APIFY_MAX_KV_READS) and default to MS2-D-32's assumed 3 otherwise.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from datetime import timedelta
from enum import StrEnum
from typing import Final, cast

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from hw_radar.acquisition.apify.client import ApifyClient
from hw_radar.acquisition.apify.contract import ContractViolation
from hw_radar.acquisition.apify.provider import IMPORT_PROVIDER_KEY, ApifyImportProvider
from hw_radar.acquisition.contracts import (
    DelistScope,
    ListingResolver,
    ProviderRunEvidence,
)
from hw_radar.acquisition.persist import ObservationRetention
from hw_radar.acquisition.pipeline import (
    _EVENT_BY_CLASS,  # pyright: ignore[reportPrivateUsage] - one ADR-0017 mapping for both paths
    _evaluate_all,  # pyright: ignore[reportPrivateUsage] - the shared stage-4 body
    _grain_counts,  # pyright: ignore[reportPrivateUsage] - the shared run tally
    _normalize,  # pyright: ignore[reportPrivateUsage] - the shared FX stamping step
)
from hw_radar.acquisition.providers import counts_toward_sweep_continuity, gate_delist_scope
from hw_radar.acquisition.retention_policy import UnknownSourceRetention
from hw_radar.acquisition.scheduling.apply import RunOutcome, apply_run_outcome
from hw_radar.acquisition.scheduling.lifecycle import LifecycleEvent
from hw_radar.acquisition.stages import (
    RetryExhausted,
    apply_absence,
    atomic_with_retry,
    break_continuity,
    ensure_watermark_rows,
    full_lane_state,
    persist_observations,
    target_scopes,
)
from hw_radar.catalog.models import (
    Listing,
    ProviderKind,
    ProviderRun,
    RunCompleteness,
    RunFailureClass,
    RunKind,
    RunStatus,
    SchedulingLane,
    ScopeSweepContinuity,
    ScraperRun,
    SourceConfig,
)
from hw_radar.catalog.models.provider import ImportState
from hw_radar.eligibility import ListingEvaluator, WatchEvaluator

logger = logging.getLogger(__name__)

DEFAULT_MAX_DATASET_READS: Final = 3  # MS2-D-32 assumption
DEFAULT_MAX_KV_READS: Final = 3  # MS2-D-32 assumption
# Backoff written on in-memory retry exhaustion; D5's selector honours it.
RETRY_EXHAUSTED_BACKOFF: Final = timedelta(minutes=5)

_TERMINAL: Final = frozenset({ImportState.FINALIZED, ImportState.REJECTED})


class RejectReason(StrEnum):
    """Why an import was rejected (stage_detail["reject_reason"]; MS2-D-22 *Reject*)."""

    FAILED_RUN = "failed_run"
    INVALID_CONTRACT = "invalid_contract"
    UNKNOWN_RETENTION = "unknown_retention"
    STORAGE_DEADLINE_PASSED = "storage_deadline_passed"
    CONTENT_PAST_TTL = "content_past_ttl"
    READ_CAP_EXHAUSTED = "read_cap_exhausted"


# The ADR-0017 failure class a FULL rejection feeds apply_run_outcome. A run the
# Actor could not complete or described inconsistently is the collector's
# fault, as parser rot is a local adapter's; the budget and retention refusals
# are hw-radar's own local conditions and back off as transient.
REJECT_FAILURE_CLASS: Final[dict[RejectReason, RunFailureClass]] = {
    RejectReason.FAILED_RUN: RunFailureClass.PARSER_ROT,
    RejectReason.INVALID_CONTRACT: RunFailureClass.PARSER_ROT,
    RejectReason.UNKNOWN_RETENTION: RunFailureClass.UNKNOWN,
    RejectReason.STORAGE_DEADLINE_PASSED: RunFailureClass.TRANSIENT,
    RejectReason.CONTENT_PAST_TTL: RunFailureClass.TRANSIENT,
    RejectReason.READ_CAP_EXHAUSTED: RunFailureClass.TRANSIENT,
}


class _Rejected(Exception):
    """Internal: a stage decided the import must be rejected."""

    def __init__(self, reason: RejectReason, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else str(reason))
        self.reason = reason
        self.detail = detail


class _StateMoved(Exception):
    """Internal: the compare-and-set found another executor already advanced the row."""


def _max_reads(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return value if isinstance(value, int) else default


async def import_provider_run(
    provider_run_id: int,
    *,
    client: ApifyClient,
    actor_name: str,
    resolver: ListingResolver,
    evaluator: ListingEvaluator | None = None,
    rand: Callable[[], float] = random.random,
) -> ImportState:
    """Advance one terminal ProviderRun through every remaining import stage.

    Returns the row's import_state afterwards: FINALIZED or REJECTED when the
    run reached a terminal state, or the unchanged prior state when stage 1 or
    stage 2 exhausted its in-memory retries (stage_detail.retry_exhausted and
    next_attempt_at are then recorded). Client and other unexpected errors
    propagate with the row left at its last committed state, to resume on the
    next invocation. `evaluator=None` binds the production WatchEvaluator,
    exactly as run_collection does.
    """
    listing_evaluator: ListingEvaluator = evaluator if evaluator is not None else WatchEvaluator()
    row = await sync_to_async(ProviderRun.objects.select_related("source_site").get)(
        pk=provider_run_id
    )
    if ImportState(row.import_state) in _TERMINAL:
        return ImportState(row.import_state)
    await sync_to_async(_claim)(row.pk)
    row = await sync_to_async(ProviderRun.objects.select_related("source_site").get)(pk=row.pk)
    try:
        while True:
            state = ImportState(row.import_state)
            if state in _TERMINAL:
                return state
            if state is ImportState.PENDING:
                await _stage1(row, client=client, actor_name=actor_name)
            elif state is ImportState.OBSERVATIONS_COMMITTED:
                await sync_to_async(_stage2)(row.pk)
            elif state is ImportState.ABSENCE_APPLIED:
                await _stage3(row, resolver)
            elif state is ImportState.RESOLVED:
                await _stage4(row, listing_evaluator)
            elif state is ImportState.EVALUATED:
                await sync_to_async(_stage5)(row.pk, rand)
            row = await sync_to_async(ProviderRun.objects.select_related("source_site").get)(
                pk=row.pk
            )
    except _Rejected as rejected:
        await sync_to_async(_reject)(row.pk, rejected.reason, rejected.detail, rand)
    except RetryExhausted:
        await sync_to_async(_record_exhaustion)(row.pk)
    except _StateMoved:
        pass
    return ImportState(
        await sync_to_async(
            lambda: ProviderRun.objects.values_list("import_state", flat=True).get(pk=row.pk)
        )()
    )


def _lock(provider_run_id: int, expected: ImportState) -> ProviderRun:
    """Lock the provider_run row (lock order item 2) and compare-and-set its state."""
    row = (
        ProviderRun.objects.select_for_update()
        .select_related("source_site")
        .get(pk=provider_run_id)
    )
    if ImportState(row.import_state) is not expected:
        raise _StateMoved(f"provider_run {provider_run_id} is {row.import_state}, not {expected}")
    return row


def _claim(provider_run_id: int) -> None:
    with transaction.atomic():
        row = (
            ProviderRun.objects.select_for_update()
            .select_related("source_site")
            .get(pk=provider_run_id)
        )
        if row.import_state in _TERMINAL:
            return
        if row.scraper_run is None:
            row.scraper_run = ScraperRun.objects.create(
                source_site=row.source_site,
                run_kind=row.run_kind,
                started_at=row.started_at or row.admitted_at,
            )
        row.import_attempts += 1
        row.save(update_fields=["scraper_run", "import_attempts"])


# ── Stage 1 ─────────────────────────────────────────────────────────────────


def _count_reads(provider_run_id: int) -> None:
    """Spend one dataset read and one KV read, committed before either is sent.

    Counting after the call would let a crash loop re-read a paid dataset
    without bound (MS2-D-32). At the cap the import is rejected instead.
    """
    with transaction.atomic():
        row = _lock(provider_run_id, ImportState.PENDING)
        max_dataset = _max_reads("HW_RADAR_APIFY_MAX_DATASET_READS", DEFAULT_MAX_DATASET_READS)
        max_kv = _max_reads("HW_RADAR_APIFY_MAX_KV_READS", DEFAULT_MAX_KV_READS)
        if row.dataset_read_count >= max_dataset or row.kv_read_count >= max_kv:
            raise _Rejected(
                RejectReason.READ_CAP_EXHAUSTED,
                f"dataset reads {row.dataset_read_count}/{max_dataset}, "
                f"kv reads {row.kv_read_count}/{max_kv}",
            )
        row.dataset_read_count += 1
        row.kv_read_count += 1
        row.save(update_fields=["dataset_read_count", "kv_read_count"])


async def _stage1(row: ProviderRun, *, client: ApifyClient, actor_name: str) -> None:
    now = timezone.now()
    if row.storage_cleanup_due_at <= now:
        raise _Rejected(RejectReason.STORAGE_DEADLINE_PASSED)
    try:
        provider = await sync_to_async(ApifyImportProvider)(
            row, client=client, actor_name=actor_name
        )
    except UnknownSourceRetention as exc:
        raise _Rejected(RejectReason.UNKNOWN_RETENTION, str(exc)) from exc
    except ContractViolation as exc:
        raise _Rejected(RejectReason.INVALID_CONTRACT, str(exc)) from exc
    retention = provider.retention
    started = row.started_at
    if started is not None and retention.expires_policy is not None:
        deadline = retention.expires_policy(started)
        if deadline is not None and deadline <= now:
            raise _Rejected(RejectReason.CONTENT_PAST_TTL, f"expired at {deadline.isoformat()}")
    await sync_to_async(_count_reads)(row.pk)
    batch = await provider.fetch()
    # fetch() wrote the classification onto `row` (the same instance).
    if row.completeness == RunCompleteness.FAILED.value:
        raise _Rejected(RejectReason.FAILED_RUN, row.completeness_reason)
    parsed = provider.parse(batch)
    normalized = await _normalize(parsed, batch.fetched_at.date())
    expires_at = retention.expires_policy(batch.fetched_at) if retention.expires_policy else None
    observation_retention = ObservationRetention(retention.retention_class, expires_at)
    site = row.source_site
    records_fetched = len(batch.items)

    def commit() -> None:
        # Each attempt starts from the immutable batch: every model instance
        # is re-fetched under this attempt's locks and every count is rebuilt,
        # so nothing an aborted attempt mutated in Python leaks into the retry.
        locked = _lock(row.pk, ImportState.PENDING)
        if locked.storage_cleanup_due_at <= timezone.now():
            raise _Rejected(RejectReason.STORAGE_DEADLINE_PASSED, "passed during stage 1")
        result = persist_observations(site, batch, normalized, observation_retention)
        locked.import_listing_ids = result.listing_ids
        locked.stage_detail = {
            **locked.stage_detail,
            "records_fetched": records_fetched,
            "records_valid": len(normalized),
            "listings_upserted": result.upserted,
            "snapshots_appended": result.appended,
        }
        locked.import_state = ImportState.OBSERVATIONS_COMMITTED
        locked.save(update_fields=["import_listing_ids", "stage_detail", "import_state"])

    def run() -> None:
        ensure_watermark_rows(site, target_scopes(site, normalized) | {None})
        atomic_with_retry(commit, label=f"import stage 1 provider_run {row.pk}")

    await sync_to_async(run)()


# ── Stage 2 ─────────────────────────────────────────────────────────────────


def _evidence(row: ProviderRun) -> ProviderRunEvidence:
    return ProviderRunEvidence(
        provider_kind=ProviderKind.APIFY,
        provider_key=IMPORT_PROVIDER_KEY,
        completeness=RunCompleteness(row.completeness),
        completeness_reason=row.completeness_reason,
        stale_absence_eligible=False,
    )


def _stage2(provider_run_id: int) -> None:
    row = ProviderRun.objects.select_related("source_site").get(pk=provider_run_id)
    site = row.source_site
    started = row.started_at or row.admitted_at
    full = RunKind(row.run_kind) is RunKind.FULL
    scope: DelistScope | None = None
    if full and row.completeness == RunCompleteness.COMPLETE.value:
        # The run's seen keys are exactly the listings stage 1 persisted: parse
        # returned one listing per usable row, and a complete run has no
        # unusable rows (classify_run counts them against completeness).
        seen = frozenset(
            Listing.objects.filter(pk__in=row.import_listing_ids).values_list(
                "source_listing_key", flat=True
            )
        )
        scope = DelistScope(
            seen_keys=seen,
            observed_at=started,
            complete=True,
            absence_grace=timedelta(0),
            scope_key=row.scope_key,
        )
    evidence = _evidence(row)
    if full:
        ensure_watermark_rows(site, {row.scope_key, None})

    def commit() -> None:
        locked = _lock(provider_run_id, ImportState.OBSERVATIONS_COMMITTED)
        delisted = 0
        # PROBE runs skip absence, continuity and the watermark (existing rule).
        if full:
            delisted = apply_absence(
                site,
                swept_scope_key=row.scope_key,
                eligible=counts_toward_sweep_continuity(evidence, scope),
                gated=gate_delist_scope(scope, evidence),
                event_time=started,
            )
        locked.stage_detail = {**locked.stage_detail, "listings_delisted": delisted}
        locked.import_state = ImportState.ABSENCE_APPLIED
        locked.save(update_fields=["stage_detail", "import_state"])

    atomic_with_retry(commit, label=f"import stage 2 provider_run {provider_run_id}")


# ── Stages 3 and 4 ──────────────────────────────────────────────────────────


def _advance(
    provider_run_id: int, expected: ImportState, to: ImportState, **detail: object
) -> None:
    with transaction.atomic():
        locked = _lock(provider_run_id, expected)
        locked.stage_detail = {**locked.stage_detail, **detail}
        locked.import_state = to
        locked.save(update_fields=["stage_detail", "import_state"])


async def _stage3(row: ProviderRun, resolver: ListingResolver) -> None:
    failed: list[int] = []
    for listing_id in dict.fromkeys(row.import_listing_ids):
        try:
            await sync_to_async(resolver.resolve_listing)(listing_id)
        except Exception:  # resolver failure never blocks ingestion (C.3)
            logger.exception("resolver failed for listing %s", listing_id)
            failed.append(listing_id)
    await sync_to_async(_advance)(
        row.pk,
        ImportState.ABSENCE_APPLIED,
        ImportState.RESOLVED,
        resolver_errors=len(failed),
        resolver_failed_ids=failed,
    )


async def _stage4(row: ProviderRun, evaluator: ListingEvaluator) -> None:
    failed_ids = row.stage_detail.get("resolver_failed_ids", [])
    resolver_failed: set[int] = set()
    if isinstance(failed_ids, list):
        resolver_failed = {i for i in cast(list[object], failed_ids) if isinstance(i, int)}
    errors = await _evaluate_all(evaluator, list(row.import_listing_ids), resolver_failed)
    await sync_to_async(_advance)(
        row.pk, ImportState.RESOLVED, ImportState.EVALUATED, evaluator_errors=errors
    )


# ── Outcome transactions: stage 5 and Reject ────────────────────────────────


def _stage5(provider_run_id: int, rand: Callable[[], float]) -> None:
    row = ProviderRun.objects.select_related("source_site", "scraper_run").get(pk=provider_run_id)
    grain_counts = _grain_counts(list(row.import_listing_ids))
    full_lane_state(row.source_site)  # ensured before the outcome transaction locks it
    with transaction.atomic():
        locked = _lock(provider_run_id, ImportState.EVALUATED)
        detail = locked.stage_detail
        run = ScraperRun.objects.select_for_update().get(pk=locked.scraper_run_id)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType] - django-types has no <fk>_id stubs
        run.records_fetched = _int(detail.get("records_fetched"))
        run.records_valid = _int(detail.get("records_valid"))
        run.listings_upserted = _int(detail.get("listings_upserted"))
        run.snapshots_appended = _int(detail.get("snapshots_appended"))
        run.detail_json = {
            "resolver_errors": _int(detail.get("resolver_errors")),
            "evaluator_errors": _int(detail.get("evaluator_errors")),
            "grain_counts": grain_counts,
            "listings_delisted": _int(detail.get("listings_delisted")),
            "provider": _evidence(locked).model_dump(mode="json"),
        }
        run.status = RunStatus.SUCCESS
        run.finished_at = timezone.now()
        run.save()
        event = (
            LifecycleEvent.PROBE_SUCCESS
            if RunKind(locked.run_kind) is RunKind.PROBE
            else LifecycleEvent.SUCCESS
        )
        # Lock order: provider_run (held) -> SourceConfig -> FULL lane, both taken
        # inside apply_run_outcome's savepoint and held to this commit. Stage 5
        # holds no scope or listing lock here (MS2-D-35 *Conditions*).
        _apply_outcome(locked, RunOutcome(event), rand)
        locked.import_state = ImportState.FINALIZED
        locked.save(update_fields=["import_state"])


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _apply_outcome(row: ProviderRun, outcome: RunOutcome, rand: Callable[[], float]) -> None:
    config = SourceConfig.objects.filter(source_site=row.source_site).first()
    if config is None:
        # Sites without a SourceConfig (the Actor-only synthetic site before
        # F5a seeds one) have no lifecycle to advance.
        return
    lane = config.lane_state(SchedulingLane.FULL)
    apply_run_outcome(config, outcome, lane_state=lane, now=timezone.now(), rand=rand)


def _reject(
    provider_run_id: int, reason: RejectReason, detail: str, rand: Callable[[], float]
) -> None:
    """Reject the import in one outcome transaction (MS2-D-22 *Reject*).

    Lock order provider_run -> SourceConfig -> FULL lane -> the admitted
    scope's row, with the scope row ensured beforehand. A FULL rejection
    applies the failure outcome and breaks the admitted scope's continuity at
    the run's startedAt (admitted_at when no start response was recorded); a
    PROBE rejection is PROBE_FAILURE and never touches continuity, so it cannot
    shorten a lane it did not sweep.
    """
    row = ProviderRun.objects.select_related("source_site").get(pk=provider_run_id)
    full = RunKind(row.run_kind) is RunKind.FULL
    if full:
        ensure_watermark_rows(row.source_site, {row.scope_key})
    failure_class = REJECT_FAILURE_CLASS[reason]
    message = f"{reason}: {detail}" if detail else str(reason)
    with transaction.atomic():
        locked = (
            ProviderRun.objects.select_for_update()
            .select_related("source_site")
            .get(pk=provider_run_id)
        )
        if locked.import_state in _TERMINAL:
            return
        if locked.scraper_run_id is not None:  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType] - django-types has no <fk>_id stubs
            ScraperRun.objects.filter(pk=locked.scraper_run_id).update(  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
                status=RunStatus.FAILED,
                failure_class=failure_class,
                error=message[:2000],
                finished_at=timezone.now(),
            )
        if full:
            _apply_outcome(locked, RunOutcome(_EVENT_BY_CLASS[failure_class]), rand)
            scope_row = (
                ScopeSweepContinuity.objects.select_for_update()
                .filter(source_site=locked.source_site, collection_scope=locked.scope_key)
                .first()
            )
            break_continuity(
                locked.started_at or locked.admitted_at,
                scope_key=locked.scope_key,
                lane=None,
                scope_row=scope_row,
            )
        else:
            _apply_outcome(locked, RunOutcome(LifecycleEvent.PROBE_FAILURE), rand)
        locked.stage_detail = {**locked.stage_detail, "reject_reason": str(reason)}
        locked.import_state = ImportState.REJECTED
        locked.save(update_fields=["stage_detail", "import_state"])
    logger.warning("provider_run %s rejected: %s", provider_run_id, message)


def _record_exhaustion(provider_run_id: int) -> None:
    """Record an in-memory retry exhaustion in its own short transaction.

    The exhausted transaction already rolled back, so the row stays at its
    prior import_state and the in-memory batch is discarded; the next
    invocation after next_attempt_at resumes, and for stage 1 that is a new
    counted read (MS2-D-35 *Exhaustion*).
    """
    with transaction.atomic():
        row = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        exhausted = row.stage_detail.get("retry_exhausted", 0)
        row.stage_detail = {
            **row.stage_detail,
            "retry_exhausted": (exhausted if isinstance(exhausted, int) else 0) + 1,
        }
        row.next_attempt_at = timezone.now() + RETRY_EXHAUSTED_BACKOFF
        row.save(update_fields=["stage_detail", "next_attempt_at"])
