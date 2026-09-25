"""Apify spend ledger service (plan E3; MS2-D-17, -32, -34, -40, -45, -46, -48).

The ledger turns rows into admission decisions. E2's `decide_admission` is the
policy; this module owns everything that needs the database:

- `reserve` / `reserve_operator`: under the budget advisory lock, materialize
  the configured billing cycle (`ensure_cycle`), sum its (and the next
  cycle's) debits from ledger rows by the MS2-D-34 cycle predicate, ask the
  policy, and persist the admitted reservation or the denial row in the same
  transaction. Concurrent reservations therefore serialize: two callers can
  never both see the same remaining amount.
- `ensure_cycle`: MS2-D-48 *Materialization*. The billing cycle is derived
  from the operator-verified anchor setting, never read from Apify: the
  runtime reads no account state at all (the runtime token cannot, R25).
- `claim_origin`, `export_handoff`, `import_handoff`: the one-authority-per-
  cycle handoff of MS2-D-45, drained and destination-bound.

Cycle predicate (MS2-D-34, revision 5 and 11). With `g` the cycle-boundary
guard, a row counts in billing cycle Y when its interval intersects Y:

  unreconciled (reserved, usage_*)  estimate + monitoring  [reserved_at - g, +inf)
  reconciled, settled amount        actual_usd             [reserved_at - g, last_charge_at + g]
  reconciled, monitoring allowance  monitoring_bound_usd   [reconciled_at - g, end + g]

where the monitoring `end` is +inf while a further selector-4 call is still
possible. Released and denied rows count nothing. Nothing here ever ages a row
out by elapsed time: an unreconciled row counts in every cycle from its
admission onward.

- The overrun latch (MS2-D-26): `trip_latch` records a trip in its own
  budget-locked transaction, `clear_latch` is the owner's
  `apify_budget_reset --reason`, and `reserve` clears trips recorded under an
  older `HW_RADAR_APIFY_ESTIMATOR_VERSION` (an estimator correction).

SCOPE: settlement, usage reads, and selector 4 live in
`acquisition.apify.reconcile` (E4), which builds on the lock, the cycle
predicate, and the latch here; the start job's binding of this ledger is
`jobs.LedgerAdmission` (E5), which calls only `reserve`; the spend report is
E6. This module never calls Apify. The runtime account snapshot and the
runtime's discovery of the billing cycle (MS2-D-40, -32) are retired by
MS2-D-48; they left `ApifyCycleDiscovery` and the cycle row's `account_*`
columns in the schema, unwritten (MS2-D-48 *Schema*).

Requirements: PostgreSQL (`pg_advisory_xact_lock`), a Django context with the
catalog app, and the HW_RADAR_APIFY_* settings read by `load_ledger_config`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, cast

from django.conf import settings
from django.db import connection, models, transaction
from django.db.models import F, Q
from django.utils import timezone

from hw_radar.acquisition.apify.budget import (
    AccountSnapshot,
    AdmissionDecision,
    AdmissionRequest,
    BudgetClass,
    BudgetDenied,
    BudgetSettings,
    CostEstimate,
    CycleDebits,
    LedgerState,
    OperatorKind,
    billing_cycle_bounds,
    decide_admission,
    load_budget_settings,
    project_allocation,
)
from hw_radar.catalog.models import (
    AdmissionClass,
    ApifyBudgetCycle,
    ApifyBudgetLatch,
    ApifyLedgerAuthority,
    ApifySpendReservation,
    ApifyUsageRead,
    ProviderRun,
    ReservationStatus,
)
from hw_radar.catalog.models.provider import LedgerAuthorityKind

logger = logging.getLogger(__name__)

__all__ = [
    "BUDGET_LOCK_KEY",
    "HANDOFF_RECORD_VERSION",
    "HandoffExport",
    "LatchReason",
    "LedgerConfig",
    "LedgerRefused",
    "ReservationOutcome",
    "claim_origin",
    "clear_latch",
    "current_cycle",
    "cycle_debits",
    "ensure_cycle",
    "export_handoff",
    "import_handoff",
    "latch_tripped",
    "load_ledger_config",
    "record_digest",
    "reserve",
    "reserve_operator",
    "take_budget_lock",
    "trip_latch",
    "trip_latch_locked",
    "trip_start_refusal",
    "trip_stranded_start_refusals_locked",
    "unrecorded_start_refusals",
]

# The one budget advisory lock (MS2-D-41 *Reserve*, MS2-D-45 *Serialization*).
# Every ledger write that changes what admission may see takes it first:
# reserve, the handoff export and import, cycle-row writes (ensure_cycle), and
# (E4) reconciliation, corrections, and latch trips. Cross-slice contract: E4
# must take this same key, via take_budget_lock, before any provider_run row
# lock (MS2-D-35 lock order). The value is arbitrary but fixed; changing it
# while two versions run would let them admit concurrently.
BUDGET_LOCK_KEY: Final = 0x4857_5241_4441_5231  # "HWRADAR1"

HANDOFF_RECORD_VERSION: Final = 1

_OPEN_STATUSES: Final = (
    ReservationStatus.RESERVED,
    ReservationStatus.USAGE_PROVISIONAL,
    ReservationStatus.USAGE_FINALIZED,
)
# Envelope rows (inspect, probe) have no provider figure to re-read, so no
# correction obligation and no monitoring allowance (MS2-D-46).
_ENVELOPE_KINDS: Final = (OperatorKind.INSPECT.value, OperatorKind.PROBE.value)
_USD_QUANTUM: Final = Decimal("0.0001")
# "Open-ended" for intervals: far enough to exceed any cycle, near enough that
# adding the (possibly unbounded) guard cannot overflow datetime.
_NEVER: Final = datetime(9000, 1, 1, tzinfo=UTC)
# Stand-in for an unset or invalid cycle-boundary guard in the debit sums: so
# wide that every row touches every cycle, which over-counts. Admission is
# denied anyway in that case (storage_hours needs the guard), but the export
# record and the report must still never under-count.
_UNBOUNDED_GUARD: Final = timedelta(days=3650)
# A clamped cycle ends this long before its successor starts, matching Apify's
# own `...T23:59:59.999Z` end stamps (and budget.billing_cycle_bounds' ends).
_CYCLE_END_EPSILON: Final = timedelta(milliseconds=1)


def _usd(amount: Decimal) -> Decimal:
    # Up, never to nearest, like budget._usd: a debit may only over-count.
    return amount.quantize(_USD_QUANTUM, rounding=ROUND_CEILING)


def take_budget_lock() -> None:
    """Take the budget advisory lock for the current transaction.

    Transaction-scoped (`pg_advisory_xact_lock`): it is released by the commit
    or rollback, so no code path can leak it. Must be called inside
    `transaction.atomic()`; outside one, autocommit would release it at once.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("take_budget_lock needs an open transaction")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [BUDGET_LOCK_KEY])


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerConfig:
    """Everything the ledger reads from settings; None is unset or invalid.

    The settlement fields (E4, MS2-D-41) default to the settings defaults, so
    a config built for admission alone still settles by the plan's rules.
    """

    budget: BudgetSettings
    ledger_id: str
    usage_settle_delay_s: int | None = 10
    usage_stable_reads: int | None = 2
    usage_stable_interval_s: int | None = 60
    usage_finalize_deadline_s: int | None = 86400
    correction_window_s: int | None = 604800
    post_run_cost_mode: str = "bound"
    run_usage_settlement: str = "bound"


def load_ledger_config() -> LedgerConfig:
    s = settings
    return LedgerConfig(
        budget=load_budget_settings(),
        ledger_id=s.HW_RADAR_APIFY_LEDGER_ID,
        usage_settle_delay_s=s.HW_RADAR_APIFY_USAGE_SETTLE_DELAY_S,
        usage_stable_reads=s.HW_RADAR_APIFY_USAGE_STABLE_READS,
        usage_stable_interval_s=s.HW_RADAR_APIFY_USAGE_STABLE_INTERVAL_S,
        usage_finalize_deadline_s=s.HW_RADAR_APIFY_USAGE_FINALIZE_DEADLINE_S,
        correction_window_s=s.HW_RADAR_APIFY_CORRECTION_WINDOW_S,
        post_run_cost_mode=s.HW_RADAR_APIFY_POST_RUN_COST_MODE,
        run_usage_settlement=s.HW_RADAR_APIFY_RUN_USAGE_SETTLEMENT,
    )


def _guard(config: LedgerConfig) -> timedelta:
    guard_s = config.budget.cycle_boundary_guard_s
    return _UNBOUNDED_GUARD if guard_s is None or guard_s < 0 else timedelta(seconds=guard_s)


# ── Cycle lookup and debits (MS2-D-34, -40) ─────────────────────────────────


def current_cycle(now: datetime) -> ApifyBudgetCycle | None:
    """The recorded billing cycle covering `now`, or None; read-only, creates nothing.

    For read-only callers (the report). Admission and the authority commands
    use ensure_cycle, which also materializes the configured cycle.
    """
    return (
        ApifyBudgetCycle.objects.filter(cycle_start__lte=now, cycle_end__gte=now)
        .order_by("-cycle_start")
        .first()
    )


def _touches(start: datetime, end: datetime, y_start: datetime, y_end: datetime) -> bool:
    return start <= y_end and end >= y_start


@dataclass(slots=True)
class _Tally:
    """One cycle's debits, split finely enough for the handoff record's lines."""

    runtime_settled: Decimal = Decimal(0)
    runtime_open: Decimal = Decimal(0)
    runtime_monitoring: Decimal = Decimal(0)
    operator_settled: Decimal = Decimal(0)
    operator_open: Decimal = Decimal(0)
    operator_monitoring: Decimal = Decimal(0)
    carried: Decimal = Decimal(0)
    # Reconciled rows touching the cycle that carry a correction obligation.
    obligations: list[ApifySpendReservation] = field(default_factory=list)

    def debits(self) -> CycleDebits:
        return CycleDebits(
            runtime_committed_usd=self.runtime_settled
            + self.runtime_open
            + self.runtime_monitoring,
            operator_committed_usd=(
                self.operator_settled + self.operator_open + self.operator_monitoring
            ),
            carried_handoff_usd=self.carried,
        )


def _has_obligation(row: ApifySpendReservation) -> bool:
    return row.operator_kind not in _ENVELOPE_KINDS


def _correction_reads(row: ApifySpendReservation) -> int:
    # A run's selector-4 reads are counted on its provider_run; a build row
    # has none and counts them on itself (MS2-D-32, -46).
    run = row.provider_run
    return run.correction_read_count if run is not None else row.correction_read_count


def _monitoring_open(row: ApifySpendReservation, config: LedgerConfig) -> bool:
    """Whether a further selector-4 call is still possible (MS2-D-34 *Monitoring charges*)."""
    if row.monitoring_call_pending_since is not None:
        return True
    if not _has_obligation(row) or row.correction_monitor_closed_at is not None:
        return False
    cap = config.budget.max_correction_reads
    # An unset cap bounds nothing, so the interval stays open (fail closed).
    return cap is None or _correction_reads(row) < cap


def _tally(
    y_start: datetime,
    y_end: datetime,
    config: LedgerConfig,
    *,
    include_carried: bool = True,
) -> _Tally:
    """Sum every debit whose interval touches cycle [y_start, y_end]."""
    # Cross-file contract: report._place re-implements this per-row cycle
    # membership for attribution; change both together (pinned by
    # test_apify_spend_report.py::test_cycle_totals_match_ledger_cycle_debits).
    g = _guard(config)
    tally = _Tally()
    # SQL prefilter only; the exact MS2-D-34 predicate is applied per row
    # below. A reconciled row qualifies by its charge interval or by a
    # monitoring interval that may still reach this cycle.
    touching_reconciled = Q(status=ReservationStatus.RECONCILED) & (
        Q(last_charge_at__gte=y_start - g)
        | Q(reconciled_at__gte=y_start - g)
        | Q(monitoring_charge_last_at__gte=y_start - g)
        | Q(correction_monitor_closed_at__isnull=True)
        | Q(monitoring_call_pending_since__isnull=False)
    )
    rows = ApifySpendReservation.objects.filter(
        (Q(status__in=_OPEN_STATUSES) | touching_reconciled) & Q(reserved_at__lte=y_end + g)
    ).select_related("provider_run")
    for row in rows:
        operator = row.admission_class == AdmissionClass.OPERATOR.value
        start = row.reserved_at - g
        if row.status != ReservationStatus.RECONCILED.value:
            # The E1 CHECK guarantees both bounds on every admitted row.
            amount = (row.estimate_usd or Decimal(0)) + (row.monitoring_bound_usd or Decimal(0))
            if operator:
                tally.operator_open += amount
            else:
                tally.runtime_open += amount
            continue
        assert row.last_charge_at is not None and row.reconciled_at is not None
        if _touches(start, row.last_charge_at + g, y_start, y_end):
            settled = row.actual_usd or Decimal(0)
            if operator:
                tally.operator_settled += settled
            else:
                tally.runtime_settled += settled
        monitoring_end = (
            _NEVER
            if _monitoring_open(row, config)
            else (row.monitoring_charge_last_at or row.reconciled_at) + g
        )
        if _touches(row.reconciled_at - g, monitoring_end, y_start, y_end):
            allowance = row.monitoring_bound_usd or Decimal(0)
            if operator:
                tally.operator_monitoring += allowance
            else:
                tally.runtime_monitoring += allowance
            if _has_obligation(row):
                tally.obligations.append(row)
        elif _has_obligation(row) and _touches(start, row.last_charge_at + g, y_start, y_end):
            tally.obligations.append(row)

    if include_carried:
        authority = ApifyLedgerAuthority.objects.filter(cycle_start=y_start).first()
        if authority is not None:
            tally.carried = authority.carried_consumption_usd
    return tally


def cycle_debits(
    cycle: ApifyBudgetCycle, config: LedgerConfig | None = None
) -> tuple[CycleDebits, CycleDebits]:
    """Return (this cycle's debits, the next cycle's) as reserve would see them.

    The next cycle is not recorded yet, so it is modeled as starting just
    after `cycle.cycle_end` and running forever: exactly the rows that can
    still charge after this cycle ends. Report and test helper; reserve calls
    the same code under the lock.
    """
    config = config or load_ledger_config()
    current = _tally(cycle.cycle_start, cycle.cycle_end, config)
    after = _tally(cycle.cycle_end + _CYCLE_END_EPSILON, _NEVER, config, include_carried=False)
    return current.debits(), after.debits()


def _snapshot(cycle: ApifyBudgetCycle, cfg: BudgetSettings) -> AccountSnapshot:
    """The recorded cycle's bounds plus the configured account settings (MS2-D-48)."""
    return AccountSnapshot(
        cycle_start=cycle.cycle_start,
        cycle_end=cycle.cycle_end,
        account_limit_usd=cfg.account_limit_usd,
        base_price_usd=cfg.account_base_price_usd,
        data_retention_days=cfg.account_data_retention_days,
        verified_on=cfg.account_verified_on,
    )


def ensure_cycle(config: LedgerConfig, now: datetime) -> ApifyBudgetCycle | None:
    """Return the recorded row of the configured cycle covering `now`, creating it if needed.

    MS2-D-48 *Materialization*; the caller holds the budget lock. Derives the
    cycle from HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR and returns None, creating
    nothing, when the anchor is unset or after `now`, or when the derived
    cycle conflicts with a recorded one. The caller then denies (or refuses)
    `cycle_unknown`. Otherwise it clamps any older row overlapping the derived
    cycle, creates the row (account_* columns null, account_read_count 0), and
    continues authority across the rollover (MS2-D-45).
    """
    # Same guard as take_budget_lock: outside a transaction there is no lock,
    # and two callers could each create or clamp a cycle row concurrently.
    if not connection.in_atomic_block:
        raise RuntimeError("ensure_cycle needs an open transaction holding the budget lock")
    anchor = config.budget.billing_cycle_anchor
    bounds = billing_cycle_bounds(anchor, now) if anchor is not None else None
    if bounds is None:
        return None
    start, end = bounds
    existing = ApifyBudgetCycle.objects.filter(cycle_start=start).first()
    if existing is not None:
        if existing.cycle_end == end:
            return existing
        # Case 2: an earlier (forward) anchor change clamped this row.
        return _cycle_conflict(start, f"recorded cycle {existing.pk} ends {existing.cycle_end}")
    # Case 3: the anchor moved backward into recorded history. Hardware Radar
    # never rewrites recorded cycles itself; only the owner resolves this, by
    # correcting the anchor.
    inside = ApifyBudgetCycle.objects.filter(cycle_start__gt=start, cycle_start__lte=end).first()
    if inside is not None:
        return _cycle_conflict(start, f"recorded cycle {inside.pk} starts inside it")
    try:
        allocation: Decimal | None = project_allocation(config.budget)
    except BudgetDenied:
        allocation = None
    # Case 4: a forward anchor change (a plan change) starts the new cycle
    # before an older row's end. Clamp so two rows never cover one instant.
    ApifyBudgetCycle.objects.filter(cycle_start__lt=start, cycle_end__gte=start).update(
        cycle_end=start - _CYCLE_END_EPSILON
    )
    cycle = ApifyBudgetCycle.objects.create(
        cycle_start=start,
        cycle_end=end,
        allocation_usd=allocation,
        opened_at=now,
        account_read_count=0,
    )
    _ensure_continued_authority(cycle, config, now)
    return cycle


def _cycle_conflict(start: datetime, detail: str) -> None:
    logger.error(
        "apify billing cycle anchor conflicts with a recorded cycle (derived start %s): %s;"
        " paid admission denies cycle_unknown until the owner corrects the anchor",
        start.isoformat(),
        detail,
    )


# ── Authority (MS2-D-45) ─────────────────────────────────────────────────────


def _ensure_continued_authority(
    cycle: ApifyBudgetCycle, config: LedgerConfig, now: datetime
) -> None:
    """Create the `continued` row at rollover for an environment that never handed off.

    Only across ADJACENT observed cycles: an environment that saw no cycle row
    in between may have been offline while another environment claimed the
    skipped cycle, so authority does not jump a gap. Caller holds the lock.
    """
    if ApifyLedgerAuthority.objects.filter(cycle_start=cycle.cycle_start).exists():
        return
    previous = (
        ApifyBudgetCycle.objects.filter(cycle_start__lt=cycle.cycle_start)
        .order_by("-cycle_start")
        .first()
    )
    if previous is None or cycle.cycle_start - previous.cycle_end > _guard(config):
        return
    held = ApifyLedgerAuthority.objects.filter(
        cycle_start=previous.cycle_start, handed_off_at__isnull=True
    ).first()
    if held is None:
        return
    ApifyLedgerAuthority.objects.create(
        cycle_start=cycle.cycle_start,
        kind=LedgerAuthorityKind.CONTINUED,
        ledger_id=held.ledger_id,
        created_at=now,
    )


def _authority_held(cycle: ApifyBudgetCycle, config: LedgerConfig, now: datetime) -> bool:
    _ensure_continued_authority(cycle, config, now)
    return ApifyLedgerAuthority.objects.filter(
        cycle_start=cycle.cycle_start, handed_off_at__isnull=True
    ).exists()


# ── Overrun latch (MS2-D-26) ────────────────────────────────────────────────


class LatchReason(StrEnum):
    """Why the overrun latch tripped (`ApifyBudgetLatch.reason`, MS2-D-26, -33, -47)."""

    OVERRUN = "overrun"  # a settled amount above its own reservation
    POST_ADMISSION_INVARIANT_BREACH = "post_admission_invariant_breach"
    UNEXPECTED_USAGE_COMPONENT = "unexpected_usage_component"
    DATASET_OVER_CAP = "dataset_over_cap"
    KV_STORE_OVER_CAP = "kv_store_over_cap"  # an OUTPUT record above MAX_KV_BYTES
    START_OPTION_MISMATCH = "start_option_mismatch"
    DELETE_ATTEMPTS_EXHAUSTED = "delete_attempts_exhausted"
    ORPHANED_START = "orphaned_start"
    API_RESPONSE_OVER_CAP = "api_response_over_cap"
    # MS2-D-48 *Hard-limit refusal*: Apify answered a run start with HTTP 402.
    ACCOUNT_LIMIT_REFUSED = "account_limit_refused"


def latch_tripped() -> bool:
    return ApifyBudgetLatch.objects.filter(cleared_at__isnull=True).exists()


def trip_latch_locked(
    reason: str, provider_run_id: int | None, estimator_version: str, now: datetime
) -> bool:
    """Record a trip; the caller holds the budget lock. False if already open.

    Idempotent per (reason, provider_run) while the trip is open, because
    conditions are re-detected: storage_cleanup.trip_stranded_latches checks
    every tick for an orphaned or delete_failed row that never tripped, and
    the jobs.py hooks re-run a start mismatch's trip on resume. One open trip
    already pauses admission.
    """
    if ApifyBudgetLatch.objects.filter(
        cleared_at__isnull=True, reason=reason, provider_run_id=provider_run_id
    ).exists():
        return False
    ApifyBudgetLatch.objects.create(
        tripped_at=now,
        provider_run_id=provider_run_id,
        reason=reason,
        estimator_version=estimator_version,
    )
    logger.error("apify overrun latch tripped: %s (provider_run %s)", reason, provider_run_id)
    return True


def trip_latch(
    reason: str,
    *,
    provider_run_id: int | None = None,
    config: LedgerConfig | None = None,
    now: datetime | None = None,
) -> bool:
    """Trip the overrun latch in its own budget-locked transaction.

    Lock order (MS2-D-26 *Serialization*, ED-05): the budget lock is taken
    first, so a caller must NOT hold a provider_run (or any later) row lock:
    commit the row's own transaction, then call this. Returns False when the
    same (reason, run) trip is already open.
    """
    version = (config or load_ledger_config()).budget.estimator_version
    with transaction.atomic():
        take_budget_lock()
        return trip_latch_locked(reason, provider_run_id, version, now or timezone.now())


def unrecorded_start_refusals() -> models.QuerySet[ProviderRun]:
    """Rows whose start Apify refused with HTTP 402 and that have never had the trip.

    The one predicate behind both 402 trip paths (MS2-D-48 *Durable
    recovery*). Any `account_limit_refused` trip of the row, open OR cleared,
    means the refusal was recorded: a cleared one is the owner's deliberate
    reset (the R21 rule of storage_cleanup._stranded). Re-tripping on "no
    OPEN trip" instead would undo every owner reset on the next admission.
    """
    return ProviderRun.objects.filter(stage_detail__start_error__status_code=402).exclude(
        budget_latch_trips__reason=LatchReason.ACCOUNT_LIMIT_REFUSED
    )


def trip_stranded_start_refusals_locked(estimator_version: str, now: datetime) -> list[int]:
    """Trip `account_limit_refused` for every 402 whose trip was lost; the caller holds the lock.

    The start job commits the 402 (stage_detail.start_error) and its trip in
    two transactions, because the ED-05 lock order forbids the budget lock
    under the provider-row lock; a process lost between them leaves the 402
    as the only durable record. Called by `reserve` before the latch is
    read, so no later admission of any class slips past it, and by every
    poll tick's storage_cleanup.trip_stranded_latches. Returns the row ids.
    """
    tripped: list[int] = []
    for pk in unrecorded_start_refusals().values_list("pk", flat=True).order_by("pk"):
        logger.error("provider_run %s: HTTP 402 start refusal without a latch trip; tripping", pk)
        trip_latch_locked(LatchReason.ACCOUNT_LIMIT_REFUSED, pk, estimator_version, now)
        tripped.append(pk)
    return tripped


def trip_start_refusal(
    provider_run_id: int, *, config: LedgerConfig | None = None, now: datetime | None = None
) -> bool:
    """The start job's own trip for a 402 it just recorded; False when already recorded.

    Deliberately not `trip_latch`, whose idempotence covers only OPEN trips:
    a callback delayed past a tick's repair and the owner's reset would then
    trip an already-handled 402 again (R12-201). Same budget-locked
    transaction and lock order as trip_latch; the caller holds no row lock.
    """
    version = (config or load_ledger_config()).budget.estimator_version
    with transaction.atomic():
        take_budget_lock()
        if not unrecorded_start_refusals().filter(pk=provider_run_id).exists():
            return False
        return trip_latch_locked(
            LatchReason.ACCOUNT_LIMIT_REFUSED, provider_run_id, version, now or timezone.now()
        )


def _clear_on_estimator_bump(estimator_version: str, now: datetime) -> int:
    """Clear open trips recorded under another estimator version; caller holds the lock.

    An estimator correction is the plan's second way to clear the latch
    (MS2-D-26): a bump of HW_RADAR_APIFY_ESTIMATOR_VERSION says the bound that
    was overrun has been replaced, and the clear is recorded like an owner's.
    `account_limit_refused` is exempt (MS2-D-48): a replaced price bound says
    nothing about the account's hard limit, so only the owner's reset clears it.
    """
    return (
        ApifyBudgetLatch.objects.filter(cleared_at__isnull=True)
        .exclude(estimator_version=estimator_version)
        .exclude(reason=LatchReason.ACCOUNT_LIMIT_REFUSED)
        .update(
            cleared_at=now,
            cleared_reason=f"estimator_version bumped to {estimator_version}",
        )
    )


def clear_latch(reason: str, *, now: datetime | None = None) -> int:
    """The owner's `apify_budget_reset --reason`: clear every open trip. Returns the count.

    Refused (LedgerRefused `latch_not_tripped`) when nothing is open, so a
    mistyped reset leaves an audit trail of nothing rather than a no-op row.
    """
    text = reason.strip()
    if not text:
        raise LedgerRefused("reason_missing", "a latch reset needs a reason")
    with transaction.atomic():
        take_budget_lock()
        at = now or timezone.now()
        open_trips = ApifyBudgetLatch.objects.filter(cleared_at__isnull=True)
        if not open_trips.exists():
            raise LedgerRefused("latch_not_tripped", "the overrun latch is not tripped")
        # cleared_at >= tripped_at is a CHECK; a trip stamped by a skewed
        # clock after `at` is cleared at its own trip time instead.
        cleared = 0
        for trip in open_trips:
            trip.cleared_at = max(at, trip.tripped_at)
            trip.cleared_reason = f"owner reset: {text}"
            trip.save(update_fields=["cleared_at", "cleared_reason"])
            cleared += 1
        return cleared


# ── Reserve (MS2-D-17, -40, -41, -46) ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReservationOutcome:
    """A reserve result. reservation_id is the admitted row or the denial row.

    Every outcome is persisted, denials included: a denied envelope row may
    carry unset (null) limits, which the envelope CHECK allows on denials.
    """

    admitted: bool
    reason: str
    detail: str
    reservation_id: int
    estimate: CostEstimate | None


def _envelope_limits(
    kind: OperatorKind | None, cfg: BudgetSettings
) -> dict[str, int | None] | None:
    """The E1 envelope columns for an operator row, or None if a limit is unset."""
    if kind is OperatorKind.INSPECT:
        limits = {
            "envelope_max_items": cfg.operator_inspect_max_items,
            "envelope_max_record_reads": cfg.operator_inspect_max_record_reads,
            "envelope_max_bytes": cfg.operator_inspect_max_bytes,
        }
    elif kind is OperatorKind.PROBE:
        limits = {"envelope_max_calls": cfg.operator_probe_max_calls}
    else:
        return {}
    if any(v is None or v < 0 for v in limits.values()):
        return None
    return dict(limits)


def _recordable_limits(kind: OperatorKind | None, cfg: BudgetSettings) -> dict[str, int | None]:
    """The envelope columns a DENIED row records: each valid limit, null otherwise."""
    if kind is OperatorKind.INSPECT:
        limits = {
            "envelope_max_items": cfg.operator_inspect_max_items,
            "envelope_max_record_reads": cfg.operator_inspect_max_record_reads,
            "envelope_max_bytes": cfg.operator_inspect_max_bytes,
        }
    elif kind is OperatorKind.PROBE:
        limits = {"envelope_max_calls": cfg.operator_probe_max_calls}
    else:
        return {}
    return {k: v if v is not None and v >= 0 else None for k, v in limits.items()}


def reserve(
    request: AdmissionRequest,
    *,
    source_site_id: int | None = None,
    config: LedgerConfig | None = None,
    now: datetime | None = None,
    reason: str = "",
) -> ReservationOutcome:
    """Admit or deny one paid operation and persist the outcome atomically.

    Runtime classes need `source_site_id` (attribution and budget_paused);
    operator rows have none. The admitted row is `reserved` with no
    provider_run: the start job attaches its run (E5). Never raises for a
    budget or settings problem; every denial is a persisted `denied` row with
    its DenialReason and, when the estimate exists, its components.
    """
    config = config or load_ledger_config()
    cfg = config.budget
    is_runtime = request.budget_class is not BudgetClass.OPERATOR
    if is_runtime and source_site_id is None:
        raise ValueError("a runtime reservation needs source_site_id")
    with transaction.atomic():
        take_budget_lock()
        # Sampled after the lock: a caller that waited on it must not decide
        # with the time it started waiting.
        now = now or timezone.now()
        _clear_on_estimator_bump(cfg.estimator_version, now)
        # Before the latch is read: a 402 whose trip a lost process never
        # committed must pause this admission, not the one after it.
        trip_stranded_start_refusals_locked(cfg.estimator_version, now)
        latch = latch_tripped()
        cycle = ensure_cycle(config, now)
        current = CycleDebits()
        after: CycleDebits | None = None
        # With no cycle, authority is undeterminable (it is per cycle), and
        # the snapshot-less state below denies with cycle_unknown, which names
        # the actual blocker; passing True here can never admit.
        held = cycle is None
        if cycle is not None:
            held = _authority_held(cycle, config, now)
            current, after = cycle_debits(cycle, config)
        # next_cycle is always set: a new row is unreconciled until E4 settles
        # it, and an unreconciled row counts in every cycle from its admission
        # onward, so every request's charge interval can reach the next cycle.
        state = LedgerState(
            now=now,
            latch_tripped=latch,
            authority_held=held,
            snapshot=_snapshot(cycle, cfg) if cycle is not None else None,
            current=current,
            next_cycle=after,
        )
        decision = decide_admission(request, cfg, state)
        return _persist(request, decision, source_site_id, cfg, now, reason)


def _persist(
    request: AdmissionRequest,
    decision: AdmissionDecision,
    source_site_id: int | None,
    cfg: BudgetSettings,
    now: datetime,
    operator_reason: str,
) -> ReservationOutcome:
    kind = request.operator_kind
    envelope = _envelope_limits(kind, cfg)
    reason = "" if decision.admitted else str(decision.reason)
    if envelope is None:
        # Only reachable on a denial (an unset limit makes the envelope
        # unpriceable, so the estimate itself was refused): the row records
        # the limits that were set, which the envelope CHECK allows on denials.
        assert not decision.admitted
        envelope = _recordable_limits(kind, cfg)
    estimate = decision.estimate
    bounds: dict[str, object] = {}
    if estimate is not None:
        bounds = {
            "estimate_usd": estimate.estimate_usd,
            "execution_bound_usd": estimate.execution_bound_usd,
            "post_run_liability_usd": estimate.post_run_liability_usd,
            "monitoring_bound_usd": estimate.monitoring_bound_usd,
            "component_bounds": {k: str(v) for k, v in estimate.components.items()},
            "estimator_version": estimate.estimator_version,
        }
    row = ApifySpendReservation.objects.create(
        source_site_id=source_site_id,
        admission_class=AdmissionClass(request.budget_class.value),
        operator_kind=kind.value if kind is not None else "",
        status=ReservationStatus.RESERVED if decision.admitted else ReservationStatus.DENIED,
        denial_reason=reason,
        reason=operator_reason.strip(),
        reserved_at=now,
        **envelope,
        **bounds,
    )
    return ReservationOutcome(decision.admitted, reason, decision.detail, row.pk, estimate)


def reserve_operator(
    kind: OperatorKind,
    *,
    config: LedgerConfig | None = None,
    now: datetime | None = None,
    reason: str = "",
) -> ReservationOutcome:
    """Reserve one operator operation's bound before it is performed (MS2-D-46).

    `reason` is the operator's `--reason`, stored on the row (admitted or denied).
    """
    return reserve(
        AdmissionRequest(budget_class=BudgetClass.OPERATOR, operator_kind=kind),
        config=config,
        now=now,
        reason=reason,
    )


# ── Handoff (MS2-D-45) ───────────────────────────────────────────────────────


class LedgerRefused(Exception):
    """An authority or handoff command was refused; `code` names why."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class HandoffExport:
    record: dict[str, object]
    digest: str


def record_digest(record: Mapping[str, object]) -> str:
    """sha256 of the canonical JSON: sorted keys, no whitespace, money as strings."""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def claim_origin(
    attested_by: str, *, config: LedgerConfig | None = None, now: datetime | None = None
) -> ApifyLedgerAuthority:
    """Create the owner-attested `origin` authority for the current cycle (R35).

    Materializes the configured cycle (ensure_cycle), so bootstrap needs no
    earlier denied start. Refused without a ledger id, `cycle_unknown` when no
    cycle can be derived (anchor unset, invalid, in the future, or
    conflicting), or when this environment already has any authority row for
    the cycle (including a handed-off one: an exporter can never admit again).
    """
    config = config or load_ledger_config()
    if not config.ledger_id:
        raise LedgerRefused("ledger_id_unset", "HW_RADAR_APIFY_LEDGER_ID is empty")
    if not attested_by.strip():
        raise LedgerRefused("attestation_missing", "an origin claim needs an attestation")
    with transaction.atomic():
        take_budget_lock()
        at = now or timezone.now()
        cycle = ensure_cycle(config, at)
        if cycle is None:
            raise LedgerRefused("cycle_unknown", "no billing cycle derivable from the anchor")
        if ApifyLedgerAuthority.objects.filter(cycle_start=cycle.cycle_start).exists():
            raise LedgerRefused("authority_exists", "this cycle already has an authority row")
        return ApifyLedgerAuthority.objects.create(
            cycle_start=cycle.cycle_start,
            kind=LedgerAuthorityKind.ORIGIN,
            ledger_id=config.ledger_id,
            attested_by=attested_by.strip()[:200],
            created_at=at,
        )


def _drain_refusal() -> LedgerRefused | None:
    """The first reason the whole ledger is not drained, or None (MS2-D-45)."""
    open_row = ApifySpendReservation.objects.filter(status__in=_OPEN_STATUSES).first()
    if open_row is not None:
        return LedgerRefused("open_reservation", f"reservation {open_row.pk} is {open_row.status}")
    obligations = ApifySpendReservation.objects.filter(status=ReservationStatus.RECONCILED).exclude(
        operator_kind__in=_ENVELOPE_KINDS
    )
    for row in obligations.select_related("correction_closing_read"):
        closing = row.correction_closing_read
        # Closed means evidenced, not elapsed: a committed closing read at or
        # after the deadline (revision 8). A pending marker is a call in flight.
        if (
            row.correction_monitor_until is None
            or row.correction_monitor_closed_at is None
            or closing is None
            or closing.read_at < row.correction_monitor_until
            or row.monitoring_call_pending_since is not None
        ):
            return LedgerRefused(
                "correction_monitoring_open",
                f"reservation {row.pk} has no committed closing read at or after its deadline",
            )
    # A post-reconciliation read above the settled usage is a correction not
    # yet applied. Pre-settlement reads belong to the settlement predicate.
    unapplied = (
        ApifyUsageRead.objects.filter(
            reservation__status=ReservationStatus.RECONCILED,
            read_at__gt=F("reservation__reconciled_at"),
            usage_total_usd__isnull=False,
        )
        .filter(
            Q(reservation__settled_run_usage_usd__isnull=True)
            | Q(usage_total_usd__gt=F("reservation__settled_run_usage_usd"))
        )
        .first()
    )
    if unapplied is not None:
        return LedgerRefused(
            "unapplied_correction",
            f"usage read {unapplied.pk} exceeds its reservation's settled usage",
        )
    return None


def _build_record(
    cycle: ApifyBudgetCycle,
    authority: ApifyLedgerAuthority,
    destination: str,
    config: LedgerConfig,
    now: datetime,
) -> dict[str, object]:
    tally = _tally(cycle.cycle_start, cycle.cycle_end, config)
    # Version 1 is kept although the discovery-allowance and standing
    # read-debit lines retired with runtime account state are gone
    # (MS2-D-48): an older record that still carries them is accepted, and
    # its total over-counts, which fails closed.
    lines = {
        "settled_runtime_usd": tally.runtime_settled,
        "settled_operator_usd": tally.operator_settled,
        "monitoring_allowance_usd": tally.runtime_monitoring + tally.operator_monitoring,
        # A handoff destination exporting onward passes its own carried
        # consumption along, or the third environment would lose it.
        "carried_in_usd": tally.carried,
    }
    total = sum(lines.values(), Decimal(0))
    obligations = [
        {
            "reservation_id": row.pk,
            "correction_monitor_until": _iso(row.correction_monitor_until),
            "closing_read_at": _iso(
                row.correction_closing_read.read_at if row.correction_closing_read else None
            ),
            "closing_read_usage_usd": (
                str(row.correction_closing_read.usage_total_usd)
                if row.correction_closing_read
                and row.correction_closing_read.usage_total_usd is not None
                else None
            ),
            "settled_usd": str(row.actual_usd),
        }
        for row in sorted(tally.obligations, key=lambda r: r.pk)
    ]
    return {
        "version": HANDOFF_RECORD_VERSION,
        "source_ledger_id": authority.ledger_id,
        "destination_ledger_id": destination,
        "cycle_start": _iso(cycle.cycle_start),
        "cycle_end": _iso(cycle.cycle_end),
        "exported_at": _iso(now),
        "lines": {k: str(_usd(v)) for k, v in lines.items()},
        "carried_consumption_usd": str(_usd(total)),
        "drain": {"open_reservations": 0, "open_monitoring": 0, "obligations": obligations},
    }


def export_handoff(
    destination: str, *, config: LedgerConfig | None = None, now: datetime | None = None
) -> HandoffExport:
    """Hand this cycle's authority to exactly one destination ledger, drained.

    Runs under the budget lock, so it serializes with every admission: either
    a reservation committed first (and this export refuses on it) or the
    export commits first (and admission is denied ledger_authority_missing).
    A repeated export to the same destination returns the stored record; any
    other destination is refused, irrevocably.
    """
    config = config or load_ledger_config()
    destination = destination.strip()
    if not destination:
        raise LedgerRefused("destination_missing", "--to needs a ledger id")
    with transaction.atomic():
        take_budget_lock()
        at = now or timezone.now()
        cycle = ensure_cycle(config, at)
        if cycle is None:
            raise LedgerRefused("cycle_unknown", "no billing cycle derivable from the anchor")
        authority = ApifyLedgerAuthority.objects.filter(cycle_start=cycle.cycle_start).first()
        if authority is None:
            raise LedgerRefused("no_authority", "this environment holds no authority this cycle")
        if authority.handed_off_at is not None:
            if authority.handed_off_to == destination and authority.handoff_record is not None:
                return HandoffExport(
                    dict(authority.handoff_record), authority.handoff_record_digest or ""
                )
            raise LedgerRefused(
                "already_handed_off", f"authority was handed off to {authority.handed_off_to}"
            )
        if config.budget.enabled:
            raise LedgerRefused("apify_enabled", "HW_RADAR_APIFY_ENABLED must be false to export")
        if destination == authority.ledger_id:
            raise LedgerRefused("destination_is_self", "cannot hand off to this ledger")
        refusal = _drain_refusal()
        if refusal is not None:
            raise refusal
        record = _build_record(cycle, authority, destination, config, at)
        digest = record_digest(record)
        authority.handed_off_at = at
        authority.handed_off_to = destination
        authority.handoff_record = record
        authority.handoff_record_digest = digest
        authority.save(
            update_fields=[
                "handed_off_at",
                "handed_off_to",
                "handoff_record",
                "handoff_record_digest",
            ]
        )
        return HandoffExport(record, digest)


def _money(value: object, name: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise LedgerRefused("record_invalid", f"{name} is not a number") from exc
    if not amount.is_finite() or amount < 0:
        raise LedgerRefused("record_invalid", f"{name} is negative or not finite")
    return amount


def _parse_dt(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise LedgerRefused("record_invalid", f"{name} is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LedgerRefused("record_invalid", f"{name} is not a timestamp") from exc
    if parsed.tzinfo is None:
        raise LedgerRefused("record_invalid", f"{name} has no timezone")
    return parsed


def _as_dict(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _validated_total(record: Mapping[str, object]) -> Decimal:
    """Check the record's drain evidence and return its carried consumption."""
    if record.get("version") != HANDOFF_RECORD_VERSION:
        raise LedgerRefused("record_invalid", "unknown handoff record version")
    drain = _as_dict(record.get("drain"))
    if drain is None:
        raise LedgerRefused("record_invalid", "drain attestation missing")
    if drain.get("open_reservations") != 0 or drain.get("open_monitoring") != 0:
        raise LedgerRefused("open_liability", "the record shows open liability or monitoring")
    obligations = drain.get("obligations")
    if not isinstance(obligations, list):
        raise LedgerRefused("record_invalid", "drain obligations missing")
    for item in cast(list[object], obligations):
        entry = _as_dict(item)
        if entry is None:
            raise LedgerRefused("record_invalid", "malformed obligation entry")
        until = entry.get("correction_monitor_until")
        read_at = entry.get("closing_read_at")
        if until is None or read_at is None:
            raise LedgerRefused(
                "closing_read_missing", "an obligation-bearing row has no closing-read evidence"
            )
        if _parse_dt(read_at, "closing_read_at") < _parse_dt(until, "correction_monitor_until"):
            raise LedgerRefused(
                "closing_read_missing", "a closing read predates its correction deadline"
            )
    lines = _as_dict(record.get("lines"))
    if not lines:
        raise LedgerRefused("record_invalid", "consumption lines missing")
    line_sum = sum((_money(v, f"lines.{k}") for k, v in lines.items()), Decimal(0))
    total = _money(record.get("carried_consumption_usd"), "carried_consumption_usd")
    if total < line_sum:
        raise LedgerRefused("record_invalid", "carried consumption is below its lines")
    return total


def import_handoff(
    record: Mapping[str, object],
    *,
    config: LedgerConfig | None = None,
    now: datetime | None = None,
) -> bool:
    """Import a handoff record as this cycle's `handoff` authority.

    Returns True when the row was created and False for a re-import of the
    same record (a no-op). Refuses a record bound to another destination, for
    another cycle, from this ledger itself, or without drain evidence.
    """
    config = config or load_ledger_config()
    if not config.ledger_id:
        raise LedgerRefused("ledger_id_unset", "HW_RADAR_APIFY_LEDGER_ID is empty")
    if record.get("destination_ledger_id") != config.ledger_id:
        raise LedgerRefused("wrong_destination", "the record is bound to another ledger")
    source = record.get("source_ledger_id")
    if not isinstance(source, str) or not source or source == config.ledger_id:
        raise LedgerRefused("record_invalid", "the record's source ledger is invalid")
    total = _validated_total(record)
    record_cycle = _parse_dt(record.get("cycle_start"), "cycle_start")
    digest = record_digest(record)
    with transaction.atomic():
        take_budget_lock()
        at = now or timezone.now()
        # Two environments must configure the same anchor: a destination
        # whose anchor derives another cycle refuses the record here.
        cycle = ensure_cycle(config, at)
        if cycle is None or cycle.cycle_start != record_cycle:
            raise LedgerRefused("wrong_cycle", "the record is not for the current cycle")
        existing = ApifyLedgerAuthority.objects.filter(cycle_start=cycle.cycle_start).first()
        if existing is not None:
            if existing.imported_record_digest == digest:
                return False
            raise LedgerRefused("authority_exists", "this cycle already has an authority row")
        ApifyLedgerAuthority.objects.create(
            cycle_start=cycle.cycle_start,
            kind=LedgerAuthorityKind.HANDOFF,
            ledger_id=config.ledger_id,
            carried_consumption_usd=_usd(total),
            attested_by=f"handoff from {source}"[:200],
            created_at=at,
            imported_record_digest=digest,
        )
        return True
