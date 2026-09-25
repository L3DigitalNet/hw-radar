"""Remote storage cleanup for Actor runs (plan D11; MS2-D-25, MS2-D-32, MS2-D-33).

Two units of work, bound by default into jobs.apify_poll_tick:

- cleanup_storage_unit runs from selector 2 once a remote-terminal run's
  import is terminal (finalized or rejected, successful or not). It deletes the
  run's default dataset and default KV store.
- overdue_storage_unit runs from selector 3 once `storage_cleanup_due_at` has
  passed, whatever the remote status (null, non-terminal, or terminal) and
  whether or not selector 1 ever observed termination. It rejects an import
  whose stage 1 has not committed (`storage_deadline_passed`: retention wins
  over completeness), then runs the overdue sequence: abort the run unless it
  was observed terminal, confirm with `GET` run, and delete.

One attempt (MS2-D-32 *Per-call bound*) is at most one abort, one confirming
`GET`, and one `DELETE` per storage not yet verified deleted. The attempt is
counted in `storage_cleanup_attempts` and committed BEFORE its first call, so a
crash mid-attempt still spends it; the start-option-mismatch abort (jobs.py) is
attempt 1 of the same sequence. At `HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS` retries
stop and the row is left `delete_failed`, logged at error level; the selectors
stop handing it out, and the overrun latch trips (`delete_attempts_exhausted`,
MS2-D-32): a row that is never verified deleted is never reconciled, so it
counts at its full estimate in every cycle until an operator resolves it.

Terminal evidence decides (MS2-D-33, R10-09, Binding). A delete is sent only
after the run is observed terminal, so cleanup never closes storage under a run
that may still be charging compute. The abort response is advisory: if it is
a valid response with a terminal status, that observation is persisted and the
confirming `GET` is skipped; otherwise the `GET` is sent, and a terminal status
there is persisted and suffices, whatever the abort answered. Only when neither
response proves termination does the attempt end, deleting nothing.

The 404 rule (MS2-D-25, ED-19). The client reports a 404 on delete as
"absent". That counts as deleted only when HW_RADAR_APIFY_DELETE_404_IS_ABSENT
is true AND the id is the one recorded from this run's own start or run record.
The second condition holds by construction: deletes are sent only for the ids
on the row, which jobs._record_observation fills once and never swaps. While
the setting is false a 404 is a failed attempt, because a scoped token that
masks inaccessible storage as 404 would otherwise verify a deletion while the
storage kept accruing. A second `GET` answering 404 is not proof either: the
same masking token would answer it with 404 too.

Progress survives partial failure: each storage verified deleted is recorded in
stage_detail["storage_verified_deleted"] and never deleted again, because a
repeat delete answers 404, which the 404 rule would refuse to trust.

A row whose start response was never recorded has no run id and no storage
ids, so at its deadline it is marked `orphaned_start_at` and logged at error
level, the latch trips (`orphaned_start`: admitted spend is now untracked),
and nothing is sent (MS2-D-33; R21 leaves it to an operator). Its
pending import is rejected like any other overdue one; for a PROBE that is
load-bearing, because D12's one-outstanding-probe rule counts a PROBE row as
outstanding until its import is finalized or rejected.

A failed attempt never raises to the tick: its outcome is a recorded,
counted state, not a unit crash. It backs off by setting `next_attempt_at` from
the attempt count, which grows with every attempt even when a successful
observation in between clears the tick's own `unit_failures` counter.

Slice E hooks (E4). The commit that records verified deletion is one of the
two barrier transactions of the work-completion anchor: it calls
reconcile.stamp_work_completion, so `final_charge_op_at` is set once, by
whichever of it and the import-terminal commit comes second.

Latch trips commit WITH the state that justifies them. `_begin` and `_finish`
take the budget lock before the provider_run row lock (ED-05, MS2-D-35 order)
and trip `orphaned_start` / `delete_attempts_exhausted` through
ledger.trip_latch_locked in the same transaction that marks the row. Rejected
alternative: commit the mark, then call ledger.trip_latch. Once the mark is
committed, jobs._storage_work_left drops the row from selectors 2 and 3, so a
process loss between the two commits would leave the latch untripped for
good, with nothing ever selecting the row again.

`trip_stranded_latches` re-detects both conditions from persisted state on
every tick and trips any that never tripped: a row a database already holds
in that state, a row left `retained` at the cap, or any path that marks
without tripping. It trips only for a (reason, run) that has NEVER had a
trip, open or cleared, so the owner's `apify_budget_reset` (R21: clean up
manually, then reset) is final for a row that stays orphaned or
delete_failed; re-tripping on "no OPEN trip" would undo every reset on the
next tick.

SCOPE: this module never reads the dataset or the OUTPUT record and never
starts a run. Settlement is acquisition.apify.reconcile's.

Requirements: a Django context with the catalog app, PostgreSQL (row locks via
select_for_update), and the settings HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS and
HW_RADAR_APIFY_DELETE_404_IS_ABSENT.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, cast

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from hw_radar.acquisition.apify import importer, jobs
from hw_radar.acquisition.apify.client import ApifyApiError, ApifyClient, ApifyError, ApifyRun
from hw_radar.acquisition.apify.contract import TERMINAL_RUN_STATUSES
from hw_radar.acquisition.apify.importer import RejectReason
from hw_radar.acquisition.apify.ledger import (
    LatchReason,
    load_ledger_config,
    take_budget_lock,
    trip_latch_locked,
    trip_stranded_start_refusals_locked,
    unrecorded_start_refusals,
)
from hw_radar.acquisition.apify.reconcile import stamp_work_completion
from hw_radar.catalog.models import ProviderRun
from hw_radar.catalog.models.provider import ImportState, StorageState

logger = logging.getLogger(__name__)

VERIFIED_KEY: Final = "storage_verified_deleted"
ERROR_KEY: Final = "storage_cleanup_error"


class Storage(StrEnum):
    DATASET = "dataset"
    KV_STORE = "kv_store"


@dataclass(frozen=True, slots=True)
class _Attempt:
    """What one counted attempt must do, read under the row lock that counted it."""

    run_id: str
    observed_terminal: bool
    pending: tuple[Storage, ...]


def _verified(row: ProviderRun) -> dict[str, str]:
    raw = row.stage_detail.get(VERIFIED_KEY)
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in cast(dict[object, object], raw).items()}


def _begin(provider_run_id: int, *, overdue: bool) -> _Attempt | None:
    """Count one attempt and commit it before any call; None when there is nothing to send.

    Nothing is sent for a row already deleted or orphaned, for a selector-2 row
    that is not remote-terminal (it has no terminal evidence and no abort
    path), or for a row at the attempt cap, which is marked `delete_failed`.
    Marking a row orphaned or delete_failed trips the latch in the same commit.
    """
    now = timezone.now()
    cap: int = settings.HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS
    version = load_ledger_config().budget.estimator_version
    with transaction.atomic():
        # Budget lock first, on every attempt, although only the marking
        # branches trip: whether this attempt marks is known only under the
        # row lock, and the budget lock may not be requested after it (ED-05).
        take_budget_lock()
        row = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        if row.storage_state == StorageState.DELETED.value or row.orphaned_start_at is not None:
            return None
        if row.external_run_id is None:
            if overdue:
                row.orphaned_start_at = now
                row.save(update_fields=["orphaned_start_at"])
                logger.error(
                    "provider_run %s: orphaned_start, deadline %s passed with no recorded "
                    "start response; any remote run and storage are untracked (R21)",
                    provider_run_id,
                    row.storage_cleanup_due_at.isoformat(),
                )
                trip_latch_locked(LatchReason.ORPHANED_START, provider_run_id, version, now)
            return None
        observed_terminal = row.remote_status in TERMINAL_RUN_STATUSES
        if not overdue and not observed_terminal:
            return None
        if row.storage_cleanup_attempts >= cap:
            _mark_delete_failed(row, cap, version, now)
            return None
        row.storage_cleanup_attempts += 1
        row.save(update_fields=["storage_cleanup_attempts"])
        verified = _verified(row)
        return _Attempt(
            run_id=row.external_run_id,
            observed_terminal=observed_terminal,
            pending=tuple(s for s in Storage if s.value not in verified),
        )


def _mark_delete_failed(row: ProviderRun, cap: int, estimator_version: str, now: datetime) -> None:
    """Leave a row at the attempt cap `delete_failed` and trip the latch in the same commit.

    The caller holds the budget lock and then the row lock, in that order.
    """
    if row.storage_state != StorageState.DELETE_FAILED.value:
        row.storage_state = StorageState.DELETE_FAILED
        row.save(update_fields=["storage_state"])
    trip_latch_locked(LatchReason.DELETE_ATTEMPTS_EXHAUSTED, row.pk, estimator_version, now)
    logger.error(
        "provider_run %s: storage cleanup failed %s/%s attempts (deadline %s); "
        "dataset %s and KV store %s may still accrue storage",
        row.pk,
        row.storage_cleanup_attempts,
        cap,
        row.storage_cleanup_due_at.isoformat(),
        row.dataset_id,
        row.kv_store_id,
    )


def _error(exc: Exception) -> str:
    if isinstance(exc, ApifyApiError):
        return f"{type(exc).__name__} {exc.status_code}"
    return type(exc).__name__


async def _observe(provider_run_id: int, run: ApifyRun) -> bool:
    """Persist a valid abort or GET answer; return whether it proves termination."""
    await sync_to_async(jobs._record_observation)(  # pyright: ignore[reportPrivateUsage] - the one MS2-D-23 remote-axis writer
        provider_run_id, run, timezone.now(), poll=False
    )
    return run.status in TERMINAL_RUN_STATUSES


async def _confirm_terminal(provider_run_id: int, run_id: str, client: ApifyClient) -> list[str]:
    """Abort, then confirm with `GET` unless the abort already proved termination.

    Returns the errors of an attempt that ends here (empty once terminal).
    """
    errors: list[str] = []
    try:
        if await _observe(provider_run_id, await client.abort_run(run_id)):
            return []
        errors.append("abort answered non-terminal")
    except ApifyError as exc:
        errors.append(f"abort {_error(exc)}")
    try:
        if await _observe(provider_run_id, await client.get_run(run_id)):
            return []
        errors.append("run read non-terminal")
    except ApifyError as exc:
        errors.append(f"run read {_error(exc)}")
    return errors


async def _delete(storage: Storage, storage_id: str | None, client: ApifyClient) -> str | None:
    """Delete one storage; return None when verified deleted, else why not."""
    if storage_id is None:
        return f"{storage} id unknown"
    try:
        if storage is Storage.DATASET:
            outcome = await client.delete_dataset(storage_id)
        else:
            outcome = await client.delete_key_value_store(storage_id)
    except ApifyError as exc:
        return f"{storage} delete {_error(exc)}"
    if outcome == "absent" and not settings.HW_RADAR_APIFY_DELETE_404_IS_ABSENT:
        return f"{storage} delete 404 not trusted (DELETE_404_IS_ABSENT false)"
    return None


def _finish(provider_run_id: int, deleted: list[Storage], errors: list[str]) -> None:
    """Record the attempt's outcome in one commit: progress, final state, or backoff."""
    now = timezone.now()
    cap: int = settings.HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS
    version = load_ledger_config().budget.estimator_version
    with transaction.atomic():
        # Before the row lock, for the same reason as in _begin: a failed
        # final attempt marks delete_failed and trips in this commit.
        take_budget_lock()
        row = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        verified = _verified(row)
        verified.update({s.value: now.isoformat() for s in deleted})
        detail: dict[str, object] = {**row.stage_detail, VERIFIED_KEY: verified}
        fields = ["stage_detail"]
        if all(s.value in verified for s in Storage):
            detail.pop(ERROR_KEY, None)
            row.storage_state = StorageState.DELETED
            row.storage_deleted_at = now
            fields += ["storage_state", "storage_deleted_at"]
            if stamp_work_completion(row, now):
                fields.append("final_charge_op_at")
        else:
            detail[ERROR_KEY] = errors
            row.next_attempt_at = now + _retry_delay(row.storage_cleanup_attempts)
            fields.append("next_attempt_at")
        row.stage_detail = detail
        row.save(update_fields=fields)
        if row.storage_state != StorageState.DELETED.value:
            if row.storage_cleanup_attempts >= cap:
                _mark_delete_failed(row, cap, version, now)
            else:
                logger.warning(
                    "provider_run %s: storage cleanup attempt %s/%s failed: %s",
                    provider_run_id,
                    row.storage_cleanup_attempts,
                    cap,
                    "; ".join(errors),
                )


def _stranded() -> tuple[QuerySet[ProviderRun], QuerySet[ProviderRun]]:
    """(orphaned, exhausted) rows whose latch condition has never been tripped.

    `exhausted` also takes a row still `retained` at the attempt cap: a crash
    between _begin's final counted attempt and its _finish, or a lowered
    HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS, leaves it there, and the selectors
    (attempts < cap) would never hand it to _begin to be marked.
    """
    cap: int = settings.HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS
    orphaned = ProviderRun.objects.filter(orphaned_start_at__isnull=False).exclude(
        budget_latch_trips__reason=LatchReason.ORPHANED_START
    )
    exhausted = ProviderRun.objects.filter(
        Q(storage_state=StorageState.DELETE_FAILED)
        | (
            ~Q(storage_state=StorageState.DELETED)
            & Q(storage_cleanup_attempts__gte=cap, orphaned_start_at__isnull=True)
        )
    ).exclude(budget_latch_trips__reason=LatchReason.DELETE_ATTEMPTS_EXHAUSTED)
    return orphaned, exhausted


def trip_stranded_latches(now: datetime | None = None) -> list[int]:
    """Trip the latch for every orphaned, exhausted, or 402-refused row that never tripped it.

    Run once per apify-poll tick (module docstring). Returns the provider_run
    ids it tripped for. The candidate check is lock-free, so an ordinary tick
    takes no budget lock. The candidates are re-read under the lock, so a
    trip that _begin or _finish committed after the lock-free check is seen
    and never duplicated.
    """
    orphaned, exhausted = _stranded()
    # The 402 refusals (MS2-D-48 *Durable recovery*) join the same lock-free
    # precheck, so a tick with nothing stranded still takes no budget lock.
    refused = unrecorded_start_refusals()
    if not orphaned.exists() and not exhausted.exists() and not refused.exists():
        return []
    cap: int = settings.HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS
    version = load_ledger_config().budget.estimator_version
    at = now or timezone.now()
    tripped: list[int] = []
    with transaction.atomic():
        take_budget_lock()
        for pk in orphaned.values_list("pk", flat=True):
            logger.error("provider_run %s: orphaned_start without a latch trip; tripping now", pk)
            trip_latch_locked(LatchReason.ORPHANED_START, pk, version, at)
            tripped.append(pk)
        pks = list(exhausted.values_list("pk", flat=True))
        for row in ProviderRun.objects.select_for_update().filter(pk__in=pks).order_by("pk"):
            _mark_delete_failed(row, cap, version, at)
            tripped.append(row.pk)
        tripped += trip_stranded_start_refusals_locked(version, at)
    return tripped


def _retry_delay(attempts: int) -> timedelta:
    # Keyed on the attempt count, not jobs' unit_failures: every valid run
    # observation clears unit_failures, so an attempt whose abort answered
    # would restart its backoff at the base delay on every retry.
    return min(jobs.UNIT_BACKOFF_BASE * 2 ** max(attempts - 1, 0), jobs.UNIT_BACKOFF_MAX)


async def _attempt(provider_run_id: int, client: ApifyClient, *, overdue: bool) -> None:
    attempt = await sync_to_async(_begin)(provider_run_id, overdue=overdue)
    if attempt is None:
        return
    if not attempt.observed_terminal:
        errors = await _confirm_terminal(provider_run_id, attempt.run_id, client)
        if errors:
            # No terminal evidence: the run may still be charging compute, so
            # this attempt deletes nothing.
            await sync_to_async(_finish)(provider_run_id, [], errors)
            return
    # Re-read after the observations: an abort or GET answer may have filled a
    # storage id the start response lacked (never replaced one it recorded).
    row = await sync_to_async(ProviderRun.objects.get)(pk=provider_run_id)
    ids = {Storage.DATASET: row.dataset_id, Storage.KV_STORE: row.kv_store_id}
    deleted: list[Storage] = []
    errors: list[str] = []
    for storage in attempt.pending:
        failure = await _delete(storage, ids[storage], client)
        if failure is None:
            deleted.append(storage)
        else:
            errors.append(failure)
    await sync_to_async(_finish)(provider_run_id, deleted, errors)


async def cleanup_storage_unit(provider_run_id: int, client: ApifyClient) -> None:
    """Selector 2's storage unit: delete a terminal run's storage after its import ended."""
    await _attempt(provider_run_id, client, overdue=False)


def _reject_if_stage1_uncommitted(provider_run_id: int, now: datetime) -> None:
    row = ProviderRun.objects.get(pk=provider_run_id)
    if row.storage_cleanup_due_at > now or row.import_state != ImportState.PENDING.value:
        return
    importer._reject(  # pyright: ignore[reportPrivateUsage] - the one MS2-D-22 Reject transaction
        provider_run_id,
        RejectReason.STORAGE_DEADLINE_PASSED,
        "rejected by the overdue storage unit",
        random.random,
        only_from=ImportState.PENDING,
    )


async def overdue_storage_unit(provider_run_id: int, client: ApifyClient) -> None:
    """Selector 3's unit: reject an uncommitted import, then abort, confirm, and delete.

    The rejection comes first because it needs no remote evidence: an attempt
    that ends without terminal evidence must not leave an import that stage 1
    could still commit. (Stage 1 re-checks the deadline itself; this also
    covers a run that is never observed terminal, which selector 2 never
    reaches.)
    """
    await sync_to_async(_reject_if_stage1_uncommitted)(provider_run_id, timezone.now())
    await _attempt(provider_run_id, client, overdue=True)
