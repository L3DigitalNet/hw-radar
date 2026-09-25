"""Apify spend settlement, correction monitoring, and operator settle (plan E4).

This is the ledger's write side after admission (MS2-D-23, -26, -32, -34, -41,
-46, -47). Every function that changes a reservation runs in ONE transaction
that takes the budget advisory lock first (`ledger.take_budget_lock`), then the
`provider_run` row, then the `ApifySpendReservation` row (MS2-D-35 total lock
order, ED-05). A latch condition found in such a transaction is tripped inside
it (the budget lock is already held); a condition found while holding only a
`provider_run` lock (the D-side hooks) is tripped by `ledger.trip_latch` after
that transaction commits, never from inside it.

Usage reads (MS2-D-41). Every run or build read that returns a record is
appended to `ApifyUsageRead` in the transaction that applies it; reads are
never updated or deleted. The first non-null total makes a row
`usage_provisional`. A row settles only by the settlement predicate:

- the work-completion anchor `max(finishedAt, provider_run.final_charge_op_at)`
  (a build: its `finishedAt` alone) must be set; a read is *eligible* only at
  or after `anchor + USAGE_SETTLE_DELAY_S + CYCLE_BOUNDARY_GUARD_S` (ED-08);
- `stable_reads`: the last USAGE_STABLE_READS eligible reads, each at least
  USAGE_STABLE_INTERVAL_S apart, agree on the total and the breakdown;
- `bound` (the default): one eligible non-null read, then the run settles at
  `max(execution bound, every observed read)`, returning no capacity;
- either mode, once `anchor + USAGE_FINALIZE_DEADLINE_S` passes or the
  run-poll cap is spent: `bound_unfinalized` at that same maximum.

Elapsed time alone never finalizes: the deadline settles at the bound, never
below it. A runtime row additionally needs a terminal import and verified
deletion (the anchor implies both). Settled amount = run usage + post-run cost
(the full post-run liability in `bound` mode; counters x prices x (1 + margin)
in `counted` mode, falling back to the bound when a price is unset). An amount
above the reservation trips `overrun` in the same transaction.

Reconciliation sets `last_charge_at` (MS2-D-34) and the fixed correction
deadline `correction_monitor_until = last_charge_at + CORRECTION_WINDOW_S`.
Selector 4 then re-reads the figure: each read is counted, stamped pending
(`monitoring_call_pending_since`) and committed before it is sent; its
completion appends the read, applies any upward correction (MS2-D-47: raise
settled and actual, re-check every cycle's invariants, trip on a breach), and,
for the first successful read at or after the deadline, closes monitoring in
the same commit. A failed read closes nothing and backs off.

SCOPE: no Apify call is made here; jobs.py sends the calls and hands the
results in. Admission (reserve) and the cycle predicate are ledger.py's; the
spend report is report.py's, which imports `is_unreconciled_stale` and
`correction_close_overdue` from here for its `unreconciled_stale` and
`correction_close_overdue` flags, so those definitions live only here.

Requirements: PostgreSQL (the advisory lock and row locks), a Django context
with the catalog app, and the LedgerConfig settlement settings.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from itertools import pairwise
from typing import Final, Literal

from django.db import models, transaction
from django.db.models import Q

from hw_radar.acquisition.apify.budget import (
    BudgetDenied,
    DenialReason,
    account_margin_usd,
    api_call_bound,
    dataset_page_bound,
    pages_per_read,
    project_allocation,
    standing_account_read_debit,
)
from hw_radar.acquisition.apify.client import (
    MAX_API_REQUEST_BODY_BYTES,
    ApifyBuild,
    ApifyRun,
)
from hw_radar.acquisition.apify.contract import MAX_LISTING_ROW_BYTES, TERMINAL_RUN_STATUSES
from hw_radar.acquisition.apify.ledger import (
    LatchReason,
    LedgerConfig,
    LedgerRefused,
    cycle_debits,
    take_budget_lock,
    trip_latch_locked,
)
from hw_radar.catalog.models import (
    AdmissionClass,
    ApifyBudgetCycle,
    ApifySpendReservation,
    ApifyUsageRead,
    ProviderRun,
    ReservationStatus,
)
from hw_radar.catalog.models.provider import (
    ImportState,
    OperatorKind,
    PostRunCostMode,
    SettlementBasis,
    StorageState,
)

logger = logging.getLogger(__name__)

__all__ = [
    "USAGE_ALLOWLIST",
    "MonitorCall",
    "begin_monitoring_read",
    "bind_build",
    "complete_monitoring_read",
    "correction_close_overdue",
    "counted_post_run_cost",
    "is_unreconciled_stale",
    "plan_build_read",
    "plan_settlement_read",
    "record_build_usage",
    "record_run_usage",
    "resolve_stale_monitoring_markers",
    "select_build_settlement",
    "select_monitoring",
    "settle_operator",
    "stamp_work_completion",
    "unexpected_components",
]

# MS2-D-26 *Proxy and every other component* (ED-10): the only usage
# components a run may carry. Anything else non-zero (every PROXY_*, the
# default request queue's REQUEST_QUEUE_*, KEY_VALUE_STORE_LISTS, a renamed or
# new key) or any unparseable component trips the latch, because the
# reservation bounds none of them.
USAGE_ALLOWLIST: Final = frozenset(
    {
        "ACTOR_COMPUTE_UNITS",
        "DATASET_READS",
        "DATASET_WRITES",
        "KEY_VALUE_STORE_READS",
        "KEY_VALUE_STORE_WRITES",
        "DATA_TRANSFER_INTERNAL_GBYTES",
        "DATA_TRANSFER_EXTERNAL_GBYTES",
    }
)

_OPEN: Final = (
    ReservationStatus.RESERVED.value,
    ReservationStatus.USAGE_PROVISIONAL.value,
    ReservationStatus.USAGE_FINALIZED.value,
)
_TERMINAL_IMPORT: Final = (ImportState.FINALIZED.value, ImportState.REJECTED.value)
_QUANTUM: Final = Decimal("0.0001")
# MS2-D-32 *Correction reads*: at most MAX_CORRECTION_READS - 3 reads are
# scheduled before the deadline, keeping three for the closing read and its
# retries.
_CLOSING_RESERVE: Final = 3
_OVERDUE_BACKOFF_MAX: Final = timedelta(hours=1)


def _usd(amount: Decimal) -> Decimal:
    # Up, like ledger._usd: a settled or corrected figure may only over-count.
    return amount.quantize(_QUANTUM, rounding=ROUND_CEILING)


def _seconds(value: int | None, name: str) -> timedelta:
    if value is None or value < 0:
        raise BudgetDenied(DenialReason.UNBOUNDED_COMPONENT, f"{name} is unset or invalid")
    return timedelta(seconds=value)


def _guard(config: LedgerConfig) -> timedelta:
    return _seconds(config.budget.cycle_boundary_guard_s, "CYCLE_BOUNDARY_GUARD_S")


# ── Work-completion anchor (MS2-D-32 *Settlement*, R10-04) ──────────────────


def stamp_work_completion(row: ProviderRun, now: datetime) -> bool:
    """Set `final_charge_op_at` once both barriers hold; the caller holds the row lock.

    Called from the two barrier transactions (the one that makes the import
    terminal, and the one that records verified deletion) just before they
    commit. Whichever completes second finds the other's state committed
    (the row lock serializes them) and stamps the anchor; the first finds
    one barrier still open and stamps nothing. Write-once: a set anchor is
    never moved, so metering reads can never shift settlement eligibility.
    Returns whether it stamped (the caller adds the field to its save).
    """
    if row.final_charge_op_at is not None:
        return False
    if row.import_state not in _TERMINAL_IMPORT or row.storage_state != StorageState.DELETED.value:
        return False
    row.final_charge_op_at = now
    return True


# ── Latch helpers ───────────────────────────────────────────────────────────


def unexpected_components(
    usage_usd: Mapping[str, Decimal] | None,
    usage: Mapping[str, Decimal] | None,
    unparseable: Sequence[str],
) -> list[str]:
    """Components outside the MS2-D-26 allowlist, or unparseable ones (empty: none)."""
    found = [f"unparseable {name}" for name in unparseable]
    for label, mapping in (("usageUsd", usage_usd), ("usage", usage)):
        for key, value in (mapping or {}).items():
            if key not in USAGE_ALLOWLIST and value != 0:
                found.append(f"{label}.{key}={value}")
    return found


def _trip(reason: LatchReason, run_id: int | None, config: LedgerConfig, now: datetime) -> None:
    # Caller holds the budget lock (every E4 transaction takes it first).
    trip_latch_locked(reason, run_id, config.budget.estimator_version, now)


# ── Settlement (MS2-D-41) ───────────────────────────────────────────────────


def _lock_reservation(
    *, reservation_id: int | None = None, provider_run_id: int | None = None
) -> tuple[ApifySpendReservation | None, ProviderRun | None]:
    """Lock the run row, then its reservation (lock order 2); the budget lock is held."""
    run: ProviderRun | None = None
    if provider_run_id is not None:
        run = ProviderRun.objects.select_for_update().get(pk=provider_run_id)
        resv = ApifySpendReservation.objects.select_for_update().filter(provider_run=run).first()
        return resv, run
    assert reservation_id is not None
    run_id = (
        ApifySpendReservation.objects.values_list("provider_run_id", flat=True)
        .filter(pk=reservation_id)
        .first()
    )
    if run_id is not None:
        run = ProviderRun.objects.select_for_update().get(pk=run_id)
    return ApifySpendReservation.objects.select_for_update().get(pk=reservation_id), run


def _anchor(resv: ApifySpendReservation, run: ProviderRun | None) -> datetime | None:
    """The eligibility anchor, or None while a barrier (or a build's finish) is open."""
    if run is not None:
        if run.final_charge_op_at is None:
            return None
        return max(run.final_charge_op_at, run.finished_at or run.final_charge_op_at)
    finished = [
        at for at in _reads(resv).values_list("finished_at_reported", flat=True) if at is not None
    ]
    return max(finished) if finished else None


def _reads(resv: ApifySpendReservation) -> models.QuerySet[ApifyUsageRead]:
    return ApifyUsageRead.objects.filter(reservation=resv)


def _eligible_from(anchor: datetime, config: LedgerConfig) -> datetime:
    return anchor + _seconds(config.usage_settle_delay_s, "USAGE_SETTLE_DELAY_S") + _guard(config)


def _stable(reads: list[ApifyUsageRead], config: LedgerConfig) -> ApifyUsageRead | None:
    """The last read of a stable tail, or None (MS2-D-41 *Settlement predicate*)."""
    wanted = config.usage_stable_reads
    interval = config.usage_stable_interval_s
    if wanted is None or wanted < 1 or interval is None or interval < 0:
        return None  # an unset predicate can never be met: settle at the bound
    tail = reads[-wanted:]
    if len(tail) < wanted or any(r.usage_total_usd is None for r in tail):
        return None
    first = tail[0]
    for prev, cur in pairwise(tail):
        if cur.read_at - prev.read_at < timedelta(seconds=interval):
            return None
        if cur.usage_total_usd != first.usage_total_usd or cur.usage_usd != first.usage_usd:
            return None
    return tail[-1]


def counted_post_run_cost(run: ProviderRun, config: LedgerConfig) -> Decimal:
    """`counted` post-run cost: hw-radar's counters x unit prices x (1 + margin).

    Mirrors the MS2-D-26 post-run rows with the counters in place of the caps
    and the storage's actual lifetime (admission to verified deletion) in
    place of `storage_hours`. Raises BudgetDenied when a price or setting is
    unset; the caller then settles at the bound instead.
    """
    cfg = config.budget
    prices = cfg.prices

    def per_op(value: Decimal | None, name: str) -> Decimal:
        if value is None or not value.is_finite() or value < 0:
            raise BudgetDenied(DenialReason.PRICING_UNVERIFIED, f"{name} is unset")
        return value / 1000

    def price(value: Decimal | None, name: str) -> Decimal:
        if value is None or not value.is_finite() or value < 0:
            raise BudgetDenied(DenialReason.PRICING_UNVERIFIED, f"{name} is unset")
        return value

    margin = cfg.margin
    if margin is None or not margin.is_finite() or margin < 0:
        raise BudgetDenied(DenialReason.UNBOUNDED_COMPONENT, "MARGIN is unset")
    kv_bytes = cfg.max_kv_bytes
    if kv_bytes is None or kv_bytes < 0 or run.storage_deleted_at is None:
        raise BudgetDenied(DenialReason.UNBOUNDED_COMPONENT, "storage size or lifetime unknown")
    ds_read = per_op(prices.dataset_reads_per_1000, "DATASET_READS_USD_PER_1000")
    ds_write = per_op(prices.dataset_writes_per_1000, "DATASET_WRITES_USD_PER_1000")
    kv_read = per_op(prices.kv_reads_per_1000, "KV_READS_USD_PER_1000")
    kv_write = per_op(prices.kv_writes_per_1000, "KV_WRITES_USD_PER_1000")
    ds_storage = price(prices.dataset_storage_per_gb_hour, "DATASET_STORAGE_USD_PER_GB_HOUR")
    kv_storage = price(prices.kv_storage_per_gb_hour, "KV_STORAGE_USD_PER_GB_HOUR")
    pages = pages_per_read(cfg, run.max_items, MAX_LISTING_ROW_BYTES)
    call = api_call_bound(cfg)
    page = dataset_page_bound(cfg)
    attempts = run.storage_cleanup_attempts
    lifetime = run.storage_deleted_at - run.admitted_at
    hours = Decimal(max(lifetime.total_seconds(), 0)) / 3600
    cost = (
        run.dataset_read_count * (run.max_items + pages) * ds_read
        + run.kv_read_count * kv_read
        + attempts * ((run.max_items + 1) * ds_write + 3 * kv_write)
        + (
            run.max_items * MAX_LISTING_ROW_BYTES * ds_storage
            + (kv_bytes + MAX_API_REQUEST_BODY_BYTES) * kv_storage
        )
        / Decimal(10**9)
        * hours
        + (1 + run.run_poll_count + 4 * attempts + run.kv_read_count) * call
        + run.dataset_read_count * pages * page
    )
    return _usd(cost * (1 + margin))


def _post_run_cost(
    resv: ApifySpendReservation, run: ProviderRun | None, config: LedgerConfig
) -> tuple[Decimal, str]:
    if run is None:
        return Decimal(0), PostRunCostMode.BOUND.value  # operator builds have none
    bound = resv.post_run_liability_usd or Decimal(0)
    if config.post_run_cost_mode == PostRunCostMode.COUNTED.value:
        try:
            return counted_post_run_cost(run, config), PostRunCostMode.COUNTED.value
        except BudgetDenied as denied:
            logger.warning("counted post-run cost for %s unavailable: %s", resv.pk, denied)
    return bound, PostRunCostMode.BOUND.value


def _next_read_at(resv: ApifySpendReservation, now: datetime, config: LedgerConfig) -> datetime:
    """Selector 4's next read time: spaced to keep three reads for the closing read.

    Never past a future deadline, so a closing read is due at the deadline;
    past it (an overdue, failed closing read), a bounded backoff.
    """
    until = resv.correction_monitor_until
    assert until is not None and resv.reconciled_at is not None
    interval = timedelta(seconds=max(config.usage_stable_interval_s or 0, 0))
    if now < until:
        cap = config.budget.max_correction_reads or 0
        slots = max(cap - _CLOSING_RESERVE, 1)
        spacing = max(interval, (until - resv.reconciled_at) / slots)
        return min(now + spacing, until)
    return now + min(max(interval, timedelta(minutes=1)), _OVERDUE_BACKOFF_MAX)


def _try_settle(
    resv: ApifySpendReservation, run: ProviderRun | None, now: datetime, config: LedgerConfig
) -> bool:
    """Reconcile `resv` if the MS2-D-41 predicate (or its deadline) allows; all locks held."""
    if resv.status not in _OPEN:
        return False
    if run is not None and (
        run.import_state not in _TERMINAL_IMPORT or run.storage_state != StorageState.DELETED.value
    ):
        return False  # the anchor below needs both barriers anyway; explicit for clarity
    if run is None and not resv.provider_build_id:
        return False  # an unbound build stays at its bound (MS2-D-46)
    anchor = _anchor(resv, run)
    if anchor is None:
        return False
    try:
        eligible_from = _eligible_from(anchor, config)
        deadline = anchor + _seconds(config.usage_finalize_deadline_s, "USAGE_FINALIZE_DEADLINE_S")
        window = _seconds(config.correction_window_s, "CORRECTION_WINDOW_S")
    except BudgetDenied as denied:
        logger.warning("reservation %s cannot settle: %s", resv.pk, denied)
        return False
    reads = list(_reads(resv).order_by("read_at", "pk"))
    observed = [r.usage_total_usd for r in reads if r.usage_total_usd is not None]
    eligible = [r for r in reads if r.read_at >= eligible_from]
    execution = resv.execution_bound_usd or Decimal(0)
    at_bound = max([execution, *observed])
    polls = run.run_poll_count if run is not None else resv.run_poll_count
    cap = config.budget.max_run_polls
    cap_spent = cap is not None and polls >= cap

    settled: Decimal | None = None
    basis = ""
    finalized: ApifyUsageRead | None = None
    if config.run_usage_settlement == SettlementBasis.STABLE_READS.value:
        finalized = _stable(eligible, config)
        if finalized is not None:
            assert finalized.usage_total_usd is not None
            # The stable figure, but never below an eligible read: a figure
            # that dipped and recovered is settled at its highest eligible value.
            settled = max(
                (r.usage_total_usd for r in eligible if r.usage_total_usd is not None),
                default=Decimal(0),
            )
            basis = SettlementBasis.STABLE_READS.value
    elif any(r.usage_total_usd is not None for r in eligible):
        settled, basis = at_bound, SettlementBasis.BOUND.value
    if settled is None and (now >= deadline or cap_spent):
        settled, basis = at_bound, SettlementBasis.BOUND_UNFINALIZED.value
    if settled is None:
        return False

    post, mode = _post_run_cost(resv, run, config)
    if finalized is not None and resv.usage_finalized_usd is None:
        # Written once: the first finalized figure is evidence, never recomputed.
        resv.usage_finalized_usd = _usd(finalized.usage_total_usd or Decimal(0))
        resv.usage_finalized_at = now
    run_usage = _usd(settled)
    actual = _usd(run_usage + post)
    resv.settled_run_usage_usd = run_usage
    resv.post_run_cost_usd = post
    resv.post_run_cost_mode = mode
    resv.settlement_basis = basis
    resv.actual_usd = actual
    resv.reconciled_at = now
    if run is not None:
        # MS2-D-34 *Horizon*: every settlement poll was sent before this
        # commit, so reconciled_at covers them.
        stamps = [run.final_charge_op_at, run.finished_at, now]
        last_charge = max(s for s in stamps if s is not None)
    else:
        last_charge = max(anchor, now)
    resv.last_charge_at = last_charge
    resv.status = ReservationStatus.RECONCILED
    resv.correction_monitor_until = last_charge + window
    resv.next_usage_read_at = _next_read_at(resv, now, config)
    resv.save()
    run_id = run.pk if run is not None else None
    if actual > (resv.estimate_usd or Decimal(0)):
        _trip(LatchReason.OVERRUN, run_id, config, now)
    logger.info(
        "reservation %s reconciled at %s (%s, post-run %s %s)",
        resv.pk,
        actual,
        basis,
        mode,
        post,
    )
    return True


def _append_read(
    resv: ApifySpendReservation,
    run: ProviderRun | None,
    *,
    read_at: datetime,
    total: Decimal | None,
    usage_usd: Mapping[str, Decimal] | None,
    finished_at: datetime | None,
    config: LedgerConfig,
) -> ApifyUsageRead:
    return ApifyUsageRead.objects.create(
        reservation=resv,
        provider_run=run,
        read_at=read_at,
        usage_total_usd=total,
        usage_usd={k: str(v) for k, v in (usage_usd or {}).items()},
        finished_at_reported=finished_at,
        price_settings_version=config.budget.estimator_version,
    )


def _mark_provisional(resv: ApifySpendReservation, total: Decimal | None) -> None:
    if total is None:
        return
    resv.usage_provisional_usd = max(resv.usage_provisional_usd or Decimal(0), _usd(total))
    if resv.status == ReservationStatus.RESERVED.value:
        resv.status = ReservationStatus.USAGE_PROVISIONAL
    resv.save(update_fields=["usage_provisional_usd", "status"])


def record_run_usage(
    provider_run_id: int, run: ApifyRun, *, read_at: datetime, config: LedgerConfig
) -> bool:
    """Apply one selector-1/2 `GET` run answer to the ledger; True when it reconciled.

    One budget-locked transaction: trip the latch on an unexpected or
    unparseable usage component or an observed restart (MS2-D-26, ED-10,
    ED-20), append the read to an unreconciled reservation, mark it
    provisional, and try to settle. A run with no reservation (every run
    before E5 binds admission) only feeds the latch checks.
    """
    with transaction.atomic():
        take_budget_lock()
        resv, locked = _lock_reservation(provider_run_id=provider_run_id)
        assert locked is not None
        bad = unexpected_components(run.usage_usd, run.usage, run.unparseable_usage)
        if bad:
            logger.error("provider_run %s usage outside the allowlist: %s", provider_run_id, bad)
            _trip(LatchReason.UNEXPECTED_USAGE_COMPONENT, provider_run_id, config, read_at)
        if run.restart_count:
            _trip(LatchReason.START_OPTION_MISMATCH, provider_run_id, config, read_at)
        if resv is None or resv.status not in _OPEN:
            return False
        _append_read(
            resv,
            locked,
            read_at=read_at,
            total=run.usage_total_usd,
            usage_usd=run.usage_usd,
            finished_at=run.finished_at,
            config=config,
        )
        _mark_provisional(resv, run.usage_total_usd)
        return _try_settle(resv, locked, read_at, config)


def plan_settlement_read(provider_run_id: int, now: datetime, config: LedgerConfig) -> str | None:
    """Selector 2's reconcile unit, before its read: settle without a call, or count one.

    Returns the run id to `GET` (its poll already counted and committed, MS2-D-32
    *Run polls*), or None when the row settled, is not yet eligible (its
    next_attempt_at is then moved to the first eligible read time), or has
    spent its poll cap (it then settles `bound_unfinalized` here).
    """
    with transaction.atomic():
        take_budget_lock()
        resv, run = _lock_reservation(provider_run_id=provider_run_id)
        assert run is not None
        if resv is None or resv.status not in _OPEN:
            return None
        if _try_settle(resv, run, now, config):
            return None
        anchor = _anchor(resv, run)
        if anchor is None or run.external_run_id is None:
            return None
        try:
            eligible_from = _eligible_from(anchor, config)
        except BudgetDenied:
            return None
        if now < eligible_from:
            run.next_attempt_at = eligible_from
            run.save(update_fields=["next_attempt_at"])
            return None
        cap = config.budget.max_run_polls
        if cap is None or run.run_poll_count >= cap:
            return None
        run.run_poll_count += 1
        interval = timedelta(seconds=max(config.usage_stable_interval_s or 0, 0))
        run.next_attempt_at = now + interval
        run.save(update_fields=["run_poll_count", "next_attempt_at"])
        return run.external_run_id


# ── Operator builds (MS2-D-46 *Build identity and settlement*) ──────────────


def bind_build(reservation_id: int, build_id: str, *, now: datetime) -> bool:
    """Bind a provider build id to an operator build reservation, once.

    Its own budget-locked, committed transaction; the poller owns the row from
    then on. Returns False for an idempotent re-bind of the same id; refuses
    (LedgerRefused) a different id, a non-build row, or a row not open.
    """
    build_id = build_id.strip()
    if not build_id:
        raise LedgerRefused("build_id_missing", "--build-id is empty")
    with transaction.atomic():
        take_budget_lock()
        resv = ApifySpendReservation.objects.select_for_update().filter(pk=reservation_id).first()
        if resv is None or resv.operator_kind != OperatorKind.BUILD.value:
            raise LedgerRefused("not_a_build", f"reservation {reservation_id} is not a build row")
        if resv.provider_build_id is not None:
            if resv.provider_build_id == build_id:
                return False
            raise LedgerRefused(
                "build_already_bound",
                f"reservation {reservation_id} is bound to build {resv.provider_build_id}",
            )
        if resv.status not in _OPEN:
            raise LedgerRefused("not_open", f"reservation {reservation_id} is {resv.status}")
        if ApifySpendReservation.objects.filter(provider_build_id=build_id).exists():
            raise LedgerRefused("build_bound_elsewhere", f"build {build_id} is already bound")
        resv.provider_build_id = build_id
        resv.next_usage_read_at = now
        resv.save(update_fields=["provider_build_id", "next_usage_read_at"])
        return True


def select_build_settlement(now: datetime) -> list[int]:
    """Selector 2 for operator builds: bound, unreconciled, due."""
    return list(
        ApifySpendReservation.objects.filter(
            operator_kind=OperatorKind.BUILD.value,
            provider_build_id__isnull=False,
            status__in=_OPEN,
        )
        .filter(Q(next_usage_read_at__isnull=True) | Q(next_usage_read_at__lte=now))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def plan_build_read(reservation_id: int, now: datetime, config: LedgerConfig) -> str | None:
    """Like plan_settlement_read for a build row: settle, or count one `GET` build."""
    with transaction.atomic():
        take_budget_lock()
        resv, _ = _lock_reservation(reservation_id=reservation_id)
        assert resv is not None
        if resv.status not in _OPEN or resv.provider_build_id is None:
            return None
        if _try_settle(resv, None, now, config):
            return None
        cap = config.budget.max_run_polls
        if cap is None or resv.run_poll_count >= cap:
            return None  # stays counted at its bound (MS2-D-34 *Operator rows*)
        resv.run_poll_count += 1
        interval = timedelta(seconds=max(config.usage_stable_interval_s or 0, 0))
        resv.next_usage_read_at = now + interval
        resv.save(update_fields=["run_poll_count", "next_usage_read_at"])
        return resv.provider_build_id


def record_build_usage(
    reservation_id: int, build: ApifyBuild, *, read_at: datetime, config: LedgerConfig
) -> bool:
    """Apply one selector-2 `GET` build answer; True when the build reconciled."""
    with transaction.atomic():
        take_budget_lock()
        resv, _ = _lock_reservation(reservation_id=reservation_id)
        assert resv is not None
        _latch_on_components(build.usage_usd, build.usage, build.unparseable_usage, config, read_at)
        if resv.status not in _OPEN:
            return False
        terminal = build.status in TERMINAL_RUN_STATUSES
        _append_read(
            resv,
            None,
            read_at=read_at,
            total=build.usage_total_usd,
            usage_usd=build.usage_usd,
            # A build's finishedAt anchors settlement only once it is terminal.
            finished_at=build.finished_at if terminal else None,
            config=config,
        )
        _mark_provisional(resv, build.usage_total_usd)
        return _try_settle(resv, None, read_at, config)


def _latch_on_components(
    usage_usd: Mapping[str, Decimal] | None,
    usage: Mapping[str, Decimal] | None,
    unparseable: Sequence[str],
    config: LedgerConfig,
    now: datetime,
    run_id: int | None = None,
) -> None:
    bad = unexpected_components(usage_usd, usage, unparseable)
    if bad:
        logger.error("usage outside the allowlist: %s", bad)
        _trip(LatchReason.UNEXPECTED_USAGE_COMPONENT, run_id, config, now)


# ── Inspection and probe envelopes (MS2-D-46) ───────────────────────────────


def settle_operator(
    reservation_id: int,
    *,
    now: datetime,
    probe_dataset_id: str | None = None,
    probe_deletion_verified: bool = False,
) -> None:
    """`apify_operator_reserve --settle` for an inspection or probe envelope.

    Reconciles at the envelope's full bound (never below: per-read
    attribution in a shared account is not separable), with
    `last_charge_at` = the settle time and no correction obligation. A probe
    is refused until its throwaway dataset id and verified deletion are
    recorded. Build rows are refused: their settle is `bind_build`.
    """
    with transaction.atomic():
        take_budget_lock()
        resv = ApifySpendReservation.objects.select_for_update().filter(pk=reservation_id).first()
        if resv is None or resv.admission_class != AdmissionClass.OPERATOR.value:
            raise LedgerRefused("not_operator", f"reservation {reservation_id} is not operator")
        if resv.operator_kind == OperatorKind.BUILD.value:
            raise LedgerRefused("build_needs_build_id", "a build settles by --build-id")
        if resv.status not in _OPEN:
            raise LedgerRefused("not_open", f"reservation {reservation_id} is {resv.status}")
        if resv.operator_kind == OperatorKind.PROBE.value:
            if not (probe_dataset_id or "").strip() or not probe_deletion_verified:
                raise LedgerRefused(
                    "probe_evidence_missing",
                    "a probe settles only with its dataset id and verified deletion",
                )
            resv.probe_dataset_id = (probe_dataset_id or "").strip()
        bound = resv.estimate_usd or Decimal(0)
        resv.settled_run_usage_usd = bound
        resv.post_run_cost_usd = Decimal(0)
        resv.post_run_cost_mode = PostRunCostMode.BOUND
        resv.settlement_basis = SettlementBasis.BOUND
        resv.actual_usd = bound
        resv.reconciled_at = now
        resv.last_charge_at = now
        resv.status = ReservationStatus.RECONCILED
        resv.save()


# ── Selector 4: correction monitoring (MS2-D-23, -34, -41, -47) ─────────────


def _correction_count(resv: ApifySpendReservation, run: ProviderRun | None) -> int:
    return run.correction_read_count if run is not None else resv.correction_read_count


def select_monitoring(now: datetime, config: LedgerConfig) -> list[int]:
    """Selector 4: reconciled rows with a figure to re-read, unclosed, and due.

    No `correction_monitor_until > now` condition: an expired, unclosed row
    stays selected until a closing read commits. A row whose read cap is spent
    is not selected (no further call) and stays `correction_close_overdue`; a
    row with a read in flight is not selected twice.
    """
    rows = (
        ApifySpendReservation.objects.filter(
            status=ReservationStatus.RECONCILED,
            correction_monitor_until__isnull=False,
            correction_monitor_closed_at__isnull=True,
            monitoring_call_pending_since__isnull=True,
            next_usage_read_at__lte=now,
        )
        .filter(Q(provider_run__external_run_id__isnull=False) | Q(provider_build_id__isnull=False))
        .select_related("provider_run")
        .order_by("pk")
    )
    cap = config.budget.max_correction_reads
    return [r.pk for r in rows if cap is not None and _correction_count(r, r.provider_run) < cap]


@dataclass(frozen=True, slots=True)
class MonitorCall:
    """A counted, committed selector-4 read that jobs.py must now send."""

    reservation_id: int
    kind: Literal["run", "build"]
    remote_id: str


def begin_monitoring_read(
    reservation_id: int, now: datetime, config: LedgerConfig
) -> MonitorCall | None:
    """Count a selector-4 read and mark it pending, committed before it is sent.

    The same commit stamps `monitoring_charge_last_at` and sets
    `monitoring_call_pending_since` (MS2-D-34 *Monitoring charges*): while the
    marker is set the monitoring interval stays open, so a worker suspended
    across a cycle boundary still debits the cycle its read lands in.
    """
    with transaction.atomic():
        take_budget_lock()
        resv, run = _lock_reservation(reservation_id=reservation_id)
        assert resv is not None
        cap = config.budget.max_correction_reads
        if (
            resv.status != ReservationStatus.RECONCILED.value
            or resv.correction_monitor_until is None
            or resv.correction_monitor_closed_at is not None
            or resv.monitoring_call_pending_since is not None
            or cap is None
            or _correction_count(resv, run) >= cap
        ):
            return None
        if run is not None:
            if run.external_run_id is None:
                return None
            run.correction_read_count += 1
            run.save(update_fields=["correction_read_count"])
            call = MonitorCall(reservation_id, "run", run.external_run_id)
        else:
            if resv.provider_build_id is None:
                return None
            resv.correction_read_count += 1
            call = MonitorCall(reservation_id, "build", resv.provider_build_id)
        resv.monitoring_call_pending_since = now
        resv.monitoring_charge_last_at = now
        resv.save(
            update_fields=[
                "correction_read_count",
                "monitoring_call_pending_since",
                "monitoring_charge_last_at",
            ]
        )
        return call


@dataclass(frozen=True, slots=True)
class MonitorResult:
    """What a selector-4 call returned: a figure, or nothing (failed or null)."""

    total: Decimal | None
    usage_usd: Mapping[str, Decimal] | None = None
    usage: Mapping[str, Decimal] | None = None
    unparseable: tuple[str, ...] = ()
    finished_at: datetime | None = None
    over_cap: bool = False


def complete_monitoring_read(
    reservation_id: int, result: MonitorResult | None, now: datetime, config: LedgerConfig
) -> bool:
    """Finish a selector-4 read in one locked transaction; True when it closed monitoring.

    Clears the pending marker and stamps the completion time whatever the
    outcome. A successful read (a record with a non-null total) is appended,
    its upward correction applied (MS2-D-47), and, when `now` is at or after
    the deadline, it closes monitoring in this same commit. A failed read (None,
    a null total, or an over-cap response, which also trips the latch) closes
    nothing and backs off.
    """
    with transaction.atomic():
        take_budget_lock()
        resv, run = _lock_reservation(reservation_id=reservation_id)
        assert resv is not None
        run_id = run.pk if run is not None else None
        resv.monitoring_call_pending_since = None
        resv.monitoring_charge_last_at = now
        closed = False
        if result is not None and result.over_cap:
            _trip(LatchReason.API_RESPONSE_OVER_CAP, run_id, config, now)
        elif result is not None:
            _latch_on_components(
                result.usage_usd, result.usage, result.unparseable, config, now, run_id
            )
        if result is not None and not result.over_cap and result.total is not None:
            read = _append_read(
                resv,
                run,
                read_at=now,
                total=result.total,
                usage_usd=result.usage_usd,
                finished_at=result.finished_at,
                config=config,
            )
            _apply_correction(resv, result.total, now, config, run_id)
            assert resv.correction_monitor_until is not None
            if now >= resv.correction_monitor_until:
                resv.correction_monitor_closed_at = now
                # Set with the closure in this one commit (the closure CHECK).
                resv.correction_closing_read = read  # pyright: ignore[reportAttributeAccessIssue] - django-types types a string-referenced FK as None
                _settle_monitoring_allowance(resv, run, config)
                closed = True
        if not closed:
            resv.next_usage_read_at = _next_read_at(resv, now, config)
        resv.save()
        return closed


def _settle_monitoring_allowance(
    resv: ApifySpendReservation, run: ProviderRun | None, config: LedgerConfig
) -> None:
    """In `counted` mode, settle the closed allowance at reads x api_call_bound (MS2-D-34).

    `bound` mode keeps the full allowance. Never raised, and never lowered
    before closure: reconciliation does not touch it.
    """
    if config.post_run_cost_mode != PostRunCostMode.COUNTED.value:
        return
    try:
        counted = _usd(_correction_count(resv, run) * api_call_bound(config.budget))
    except BudgetDenied:
        return
    resv.monitoring_bound_usd = min(resv.monitoring_bound_usd or Decimal(0), counted)


def _apply_correction(
    resv: ApifySpendReservation,
    total: Decimal,
    now: datetime,
    config: LedgerConfig,
    run_id: int | None,
) -> None:
    """MS2-D-47 steps 2-4 on a locked row: raise, never lower, then re-check invariants."""
    settled = resv.settled_run_usage_usd or Decimal(0)
    figure = _usd(total)
    if figure <= settled:
        return  # a later lower read never returns capacity
    delta = figure - settled
    actual = _usd((resv.actual_usd or Decimal(0)) + delta)
    resv.settled_run_usage_usd = figure
    resv.actual_usd = actual
    resv.save(update_fields=["settled_run_usage_usd", "actual_usd"])
    logger.warning("reservation %s corrected upward by %s", resv.pk, delta)
    if actual > (resv.estimate_usd or Decimal(0)):
        _trip(LatchReason.OVERRUN, run_id, config, now)
    breaches = invariant_breaches(resv, config)
    if breaches:
        logger.error("reservation %s correction breaches %s", resv.pk, breaches)
        _trip(LatchReason.POST_ADMISSION_INVARIANT_BREACH, run_id, config, now)


def invariant_breaches(resv: ApifySpendReservation, config: LedgerConfig) -> list[str]:
    """Every MS2-D-40 invariant the committed totals now exceed, per touched cycle.

    For each observed cycle the row's charge interval touches (the billing
    clock, MS2-D-39), with no new estimate: the row's class cap, the runtime
    allocation A, the operator allowance, the target, and both account checks
    against that cycle's latest snapshot. A figure that cannot be computed (an
    unset setting) is reported as a breach: the check fails closed.
    """
    assert resv.last_charge_at is not None
    cfg = config.budget
    try:
        g = _guard(config)
        allocation = project_allocation(cfg)
        standing = standing_account_read_debit(cfg)
        allowance = cfg.operator_allowance_usd
        target = cfg.cycle_target_usd
        external = cfg.external_liability_usd
        reserve = cfg.watch_refresh_reserve_usd
        if allowance is None or target is None or external is None or reserve is None:
            raise BudgetDenied(DenialReason.BUDGET_SETTING_INVALID, "a budget setting is unset")
    except BudgetDenied as denied:
        return [f"unverifiable: {denied.detail}"]
    found: list[str] = []
    cycles = ApifyBudgetCycle.objects.filter(
        cycle_start__lte=resv.last_charge_at + g, cycle_end__gte=resv.reserved_at - g
    ).order_by("cycle_start")
    for cycle in cycles:
        try:
            debits, _ = cycle_debits(cycle, config)
        except BudgetDenied as denied:
            found.append(f"{cycle.cycle_start:%Y-%m-%d} unverifiable: {denied.detail}")
            continue
        label = f"{cycle.cycle_start:%Y-%m-%d}"
        runtime = (
            debits.runtime_committed_usd
            + debits.carried_handoff_usd
            + debits.discovery_allowance_usd
            + standing
        )
        hr_cycle = runtime + debits.operator_committed_usd
        if resv.admission_class == AdmissionClass.OPERATOR.value:
            if debits.operator_committed_usd > allowance:
                found.append(f"{label} operator allowance")
        else:
            cap = (
                allocation - reserve
                if resv.admission_class == AdmissionClass.DISCOVERY.value
                else allocation
            )
            if runtime > cap:
                found.append(f"{label} {resv.admission_class} class cap")
        if runtime > allocation:
            found.append(f"{label} runtime allocation")
        if debits.operator_committed_usd > allowance:
            found.append(f"{label} operator allowance")
        if hr_cycle > target:
            found.append(f"{label} target")
        prepaid = cycle.account_prepaid_credit_usd
        if prepaid is None or cycle.account_usage_usd is None:
            found.append(f"{label} account snapshot unobserved")
            continue
        try:
            usable = prepaid - account_margin_usd(cfg, prepaid)
        except BudgetDenied as denied:
            found.append(f"{label} unverifiable: {denied.detail}")
            continue
        if cycle.account_usage_usd + hr_cycle - debits.hr_included_usd > usable:
            found.append(f"{label} snapshot check")
        if hr_cycle + external > usable:
            found.append(f"{label} external-liability check")
    return sorted(set(found))


def resolve_stale_monitoring_markers(process_started_at: datetime) -> int:
    """Resolve pending markers a previous poller process left behind (MS2-D-34).

    The poller is the only sender of selector-4 reads, so a marker older than
    this process's start belongs to a process that is gone and can never send
    its read. It resolves to this process's start, the verified cancellation
    point: the interval then ends no earlier than that. Returns the count.
    """
    with transaction.atomic():
        take_budget_lock()
        stale = list(
            ApifySpendReservation.objects.select_for_update().filter(
                monitoring_call_pending_since__lt=process_started_at
            )
        )
        for resv in stale:
            resv.monitoring_call_pending_since = None
            last = resv.monitoring_charge_last_at
            resv.monitoring_charge_last_at = (
                process_started_at if last is None else max(last, process_started_at)
            )
            resv.save(update_fields=["monitoring_call_pending_since", "monitoring_charge_last_at"])
        return len(stale)


# ── Report states for E6 ────────────────────────────────────────────────────


def is_unreconciled_stale(resv: ApifySpendReservation, now: datetime) -> bool:
    """`unreconciled_stale`: still unsettled after its admission cycle ended (MS2-D-26)."""
    if resv.status not in _OPEN:
        return False
    admission = (
        ApifyBudgetCycle.objects.filter(
            cycle_start__lte=resv.reserved_at, cycle_end__gte=resv.reserved_at
        )
        .order_by("-cycle_start")
        .first()
    )
    return admission is not None and admission.cycle_end < now


def correction_close_overdue(
    resv: ApifySpendReservation, now: datetime, config: LedgerConfig
) -> bool:
    """`correction_close_overdue`: an open obligation past its deadline or its read cap."""
    if (
        resv.status != ReservationStatus.RECONCILED.value
        or resv.correction_monitor_until is None
        or resv.correction_monitor_closed_at is not None
    ):
        return False
    cap = config.budget.max_correction_reads
    spent = cap is None or _correction_count(resv, resv.provider_run) >= cap
    return now > resv.correction_monitor_until or spent
