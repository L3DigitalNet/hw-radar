"""Apify spend attribution report (plan E6, AC-7; MS2-D-17, -23, -34, -40, -45, -46).

`build_report` reads the ledger tables and returns a `SpendReport`;
`render_report` turns it into the text `apify_spend_report` prints. The report
is organised by billing cycle, the authoritative budget period (MS2-D-17):
each observed cycle gets its bounds, the project allocation, Hardware Radar's
consumption split into settled, outstanding, and monitoring debits, the
remaining project budget, the stored account snapshot, the declared external
liability, the inclusion-watermark status, the ledger authority, operator
consumption by kind, and per-source and per-provider totals. Ledger-wide
sections follow: unsettled rows, overruns and latch trips, and a
trailing-31-day trend labelled secondary, which never admits or denies.

Read-only by construction: nothing here writes a row, takes the budget lock,
or calls Apify. The account figures are the stored snapshot as last observed
by `ledger.refresh_account_snapshot`, so a stale snapshot is reported as
stale rather than refreshed. A setting that makes a figure unpriceable (an
unset unit price or cap) is reported as unavailable with the reason; the
report never raises for it, because an operator needs the rest of the report
most exactly when settings are broken.

Cycle placement mirrors `ledger._tally` (MS2-D-34): the same intervals, the
same boundary guard, the same "unreconciled counts from admission onward".
It is re-implemented here because `_tally` returns only sums and the report
needs each row's share for attribution. Cross-file contract: a change to the
cycle predicate in ledger.py must be made here too;
tests/db/test_apify_spend_report.py pins the totals to `ledger.cycle_debits`.

SCOPE: the output carries ledger ids, source names, provider kinds, reason
codes, timestamps, and dollar figures only. It deliberately omits owner free
text (`attested_by`, a latch's `cleared_reason`), Apify run, dataset, build,
and Actor identifiers, and anything account-identifying: the report is meant
to be pasteable into issues and handoff notes.

Requirements: a Django context with the catalog app and the HW_RADAR_APIFY_*
settings read by `ledger.load_ledger_config`. No PostgreSQL-specific SQL.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Final

from django.db.models import Q

from hw_radar.acquisition.apify.budget import (
    BudgetDenied,
    OperatorKind,
    account_margin_usd,
    api_call_bound,
    project_allocation,
    standing_account_read_debit,
    trailing_window_warning,
)
from hw_radar.acquisition.apify.ledger import (
    LedgerConfig,
    current_cycle,
    discovery_status,
    load_ledger_config,
)
from hw_radar.catalog.models import (
    AdmissionClass,
    ApifyBudgetCycle,
    ApifyBudgetLatch,
    ApifyCycleDiscovery,
    ApifyLedgerAuthority,
    ApifySpendReservation,
    ProviderRun,
    ReservationStatus,
)
from hw_radar.catalog.models.provider import ImportState, SettlementBasis, StorageState

__all__ = [
    "CORRECTION_CLOSE_OVERDUE",
    "TREND_WINDOW",
    "CycleReport",
    "Figure",
    "FlaggedRow",
    "SpendReport",
    "build_report",
    "render_report",
]

# The MS2-D-23 name for an obligation whose closing read has not committed
# although its deadline has passed or its read cap is spent.
CORRECTION_CLOSE_OVERDUE: Final = "correction_close_overdue"
# MS2-D-32 *Run polls*: a row at the poll cap settles at its bound and is
# reported by this name.
API_CALL_CAP_EXHAUSTED: Final = "api_call_cap_exhausted"
TREND_WINDOW: Final = timedelta(days=31)

_OPEN_STATUSES: Final = (
    ReservationStatus.RESERVED.value,
    ReservationStatus.USAGE_PROVISIONAL.value,
    ReservationStatus.USAGE_FINALIZED.value,
)
# Same envelope set and stand-ins as ledger.py (_ENVELOPE_KINDS, _NEVER,
# _UNBOUNDED_GUARD): an unset guard over-counts rather than under-counts.
_ENVELOPE_KINDS: Final = (OperatorKind.INSPECT.value, OperatorKind.PROBE.value)
_NEVER: Final = datetime(9000, 1, 1, tzinfo=UTC)
_UNBOUNDED_GUARD: Final = timedelta(days=3650)
_USD_QUANTUM: Final = Decimal("0.0001")
_TERMINAL_IMPORT: Final = (ImportState.FINALIZED.value, ImportState.REJECTED.value)


def _usd(amount: Decimal) -> Decimal:
    # Up, like ledger._usd: a displayed debit may only over-state.
    return amount.quantize(_USD_QUANTUM, rounding=ROUND_CEILING)


def _dollars(amount: Decimal) -> str:
    # Remaining figures go negative when a cycle is over-committed; print the
    # sign before the currency symbol.
    value = _usd(amount)
    return f"-${-value}" if value < 0 else f"${value}"


@dataclass(frozen=True, slots=True)
class Figure:
    """A dollar figure, or why it cannot be computed from the current settings."""

    value: Decimal | None
    unavailable: str = ""

    @staticmethod
    def of(compute: Callable[[], Decimal]) -> Figure:
        try:
            return Figure(compute())
        except BudgetDenied as denied:
            return Figure(None, str(denied.reason))

    def text(self) -> str:
        if self.value is None:
            return f"unavailable ({self.unavailable or 'not observed'})"
        return _dollars(self.value)


@dataclass(slots=True)
class _Share:
    """One row's contribution to one cycle (or window), by debit line."""

    settled: Decimal = Decimal(0)
    outstanding: Decimal = Decimal(0)
    monitoring: Decimal = Decimal(0)
    hr_included: Decimal = Decimal(0)

    def add(self, other: _Share) -> None:
        self.settled += other.settled
        self.outstanding += other.outstanding
        self.monitoring += other.monitoring
        self.hr_included += other.hr_included

    @property
    def total(self) -> Decimal:
        return self.settled + self.outstanding + self.monitoring


@dataclass(frozen=True, slots=True)
class Totals:
    """Rows and debits attributed to one key (a source, provider, or operator kind)."""

    rows: int
    settled: Decimal
    outstanding: Decimal
    monitoring: Decimal
    denials: int = 0
    provider_runs: int = 0
    provider_observed_usd: Decimal = Decimal(0)

    @property
    def total(self) -> Decimal:
        return self.settled + self.outstanding + self.monitoring


@dataclass(frozen=True, slots=True)
class CycleReport:
    cycle_start: datetime
    cycle_end: datetime
    is_current: bool
    allocation: Figure
    recorded_allocation: Decimal | None
    consumed_settled: Decimal
    outstanding: Decimal
    monitoring: Decimal
    discovery_allowance: Figure
    standing_account_reads: Figure
    carried_handoff: Decimal
    remaining_project_budget: Figure
    account_usage: Decimal | None
    prepaid_credit: Decimal | None
    remaining_prepaid: Figure
    admission_headroom: Figure
    account_observed_at: datetime | None
    snapshot_stale: bool | None
    external_liability: Figure
    external_observed: Figure
    watermark: str
    hr_included: Decimal
    authority: str
    operator_by_kind: dict[str, Totals]
    operator_allowance: Figure
    by_source: dict[str, Totals]
    by_provider: dict[str, Totals]
    denials: dict[str, int]


@dataclass(frozen=True, slots=True)
class FlaggedRow:
    reservation_id: int
    label: str
    reserved_at: datetime
    amount: Decimal | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpendReport:
    generated_at: datetime
    cycles: list[CycleReport]
    discovery: str | None
    unsettled: list[FlaggedRow]
    overruns: list[FlaggedRow]
    latches: list[str]
    trend_total: Decimal
    trend_discovery: Figure
    trend_warning: bool | None
    trend_window_start: datetime


# ── Cycle placement (mirror of ledger._tally, MS2-D-34) ─────────────────────


def _guard(config: LedgerConfig) -> timedelta:
    guard_s = config.budget.cycle_boundary_guard_s
    return _UNBOUNDED_GUARD if guard_s is None or guard_s < 0 else timedelta(seconds=guard_s)


def _touches(start: datetime, end: datetime, y_start: datetime, y_end: datetime) -> bool:
    return start <= y_end and end >= y_start


def _has_obligation(row: ApifySpendReservation) -> bool:
    return row.operator_kind not in _ENVELOPE_KINDS


def _correction_reads(row: ApifySpendReservation) -> int:
    run = row.provider_run
    return run.correction_read_count if run is not None else row.correction_read_count


def _monitoring_open(row: ApifySpendReservation, config: LedgerConfig) -> bool:
    if row.monitoring_call_pending_since is not None:
        return True
    if not _has_obligation(row) or row.correction_monitor_closed_at is not None:
        return False
    cap = config.budget.max_correction_reads
    return cap is None or _correction_reads(row) < cap


def _place(
    row: ApifySpendReservation,
    y_start: datetime,
    y_end: datetime,
    config: LedgerConfig,
    watermark: datetime | None,
) -> _Share | None:
    """This row's share of [y_start, y_end], or None when it does not touch it."""
    g = _guard(config)
    start = row.reserved_at - g
    if start > y_end:
        return None
    if row.status in _OPEN_STATUSES:
        # Open-ended from admission: an unreconciled row counts in every cycle
        # from its admission onward, at estimate plus monitoring allowance.
        amount = (row.estimate_usd or Decimal(0)) + (row.monitoring_bound_usd or Decimal(0))
        return _Share(outstanding=amount)
    if row.status != ReservationStatus.RECONCILED.value:
        return None
    assert row.last_charge_at is not None and row.reconciled_at is not None
    share = _Share()
    touched = False
    if _touches(start, row.last_charge_at + g, y_start, y_end):
        touched = True
        share.settled = row.actual_usd or Decimal(0)
        if watermark is not None and row.last_charge_at + g < watermark:
            share.hr_included = share.settled
    monitoring_end = (
        _NEVER
        if _monitoring_open(row, config)
        else (row.monitoring_charge_last_at or row.reconciled_at) + g
    )
    if _touches(row.reconciled_at - g, monitoring_end, y_start, y_end):
        touched = True
        share.monitoring = row.monitoring_bound_usd or Decimal(0)
    return share if touched else None


def _discovery_rows_touching(y_start: datetime, y_end: datetime, g: timedelta) -> int:
    return (
        ApifyCycleDiscovery.objects.filter(opened_at__lte=y_end + g)
        .filter(Q(closed_at__isnull=True) | Q(closed_at__gte=y_start - g))
        .count()
    )


def _discovery_allowance(config: LedgerConfig, count: int) -> Figure:
    if count == 0:
        return Figure(Decimal(0))
    reads = config.max_discovery_reads
    if reads is None or reads < 0:
        return Figure(None, "unbounded_component")
    return Figure.of(lambda: _usd(reads * api_call_bound(config.budget)) * count)


# ── Row labels and flags ─────────────────────────────────────────────────────


def _label(row: ApifySpendReservation) -> str:
    if row.admission_class == AdmissionClass.OPERATOR.value:
        return f"operator/{row.operator_kind}"
    source = row.source_site.name if row.source_site is not None else "?"
    return f"{row.admission_class} {source}"


def _provider_key(row: ApifySpendReservation) -> str:
    if row.provider_run is not None:
        return row.provider_run.provider_kind
    if row.admission_class == AdmissionClass.OPERATOR.value:
        return "none (operator)"
    return "unattached"


def _close_overdue(row: ApifySpendReservation, config: LedgerConfig, now: datetime) -> bool:
    """MS2-D-23: an open obligation past its deadline or at its read cap."""
    if (
        row.status != ReservationStatus.RECONCILED.value
        or not _has_obligation(row)
        or row.correction_monitor_until is None
        or row.correction_monitor_closed_at is not None
    ):
        return False
    cap = config.budget.max_correction_reads
    at_cap = cap is not None and _correction_reads(row) >= cap
    return now >= row.correction_monitor_until or at_cap


def _unsettled_reasons(row: ApifySpendReservation, config: LedgerConfig) -> tuple[str, ...]:
    reasons = [row.status]
    run = row.provider_run
    cap = config.budget.max_run_polls
    polls = run.run_poll_count if run is not None else row.run_poll_count
    if cap is not None and polls >= cap:
        reasons.append(API_CALL_CAP_EXHAUSTED)
    if run is not None:
        if run.import_state not in _TERMINAL_IMPORT:
            reasons.append(f"import_state={run.import_state}")
        if run.storage_state != StorageState.DELETED.value:
            reasons.append(f"storage_state={run.storage_state}")
    elif row.admission_class != AdmissionClass.OPERATOR.value:
        reasons.append("no_provider_run")
    elif row.operator_kind == OperatorKind.BUILD.value and row.provider_build_id is None:
        # Counted at its full bound until `--settle --build-id` binds it (MS2-D-46).
        reasons.append("build_id_unbound")
    return tuple(reasons)


# ── Assembly ─────────────────────────────────────────────────────────────────


def _authority_text(cycle_start: datetime) -> tuple[str, Decimal]:
    authority = ApifyLedgerAuthority.objects.filter(cycle_start=cycle_start).first()
    if authority is None:
        return "none (paid admission denied: ledger_authority_missing)", Decimal(0)
    # attested_by is owner free text and deliberately not printed (SCOPE).
    text = f"{authority.kind} ledger={authority.ledger_id} since {_ts(authority.created_at)}"
    if authority.handed_off_at is not None:
        text += f"; handed off to {authority.handed_off_to} at {_ts(authority.handed_off_at)}"
    return text, authority.carried_consumption_usd


def _watermark(config: LedgerConfig, observed_at: datetime | None) -> tuple[str, datetime | None]:
    lag = config.usage_inclusion_lag_s
    if lag is None or lag < 0:
        return (
            "unset (HR_included = 0: reconciled spend is debited on top of the snapshot)",
            None,
        )
    if observed_at is None:
        return f"lag {lag}s; no snapshot observed, so no watermark", None
    mark = observed_at - timedelta(seconds=lag)
    return f"lag {lag}s; watermark {_ts(mark)}", mark


def _cycle_report(
    cycle: ApifyBudgetCycle,
    rows: list[ApifySpendReservation],
    config: LedgerConfig,
    now: datetime,
    is_current: bool,
) -> CycleReport:
    y_start, y_end = cycle.cycle_start, cycle.cycle_end
    cfg = config.budget
    watermark_text, watermark = _watermark(config, cycle.account_observed_at)

    runtime = _Share()
    operator = _Share()
    by_kind: dict[str, _Share] = defaultdict(_Share)
    kind_rows: dict[str, int] = defaultdict(int)
    by_source: dict[str, _Share] = defaultdict(_Share)
    source_rows: dict[str, int] = defaultdict(int)
    source_denials: dict[str, int] = defaultdict(int)
    by_provider: dict[str, _Share] = defaultdict(_Share)
    provider_rows: dict[str, int] = defaultdict(int)
    denials: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.status == ReservationStatus.DENIED.value:
            # A denial debits nothing; it is attributed to the cycle it was
            # decided in, which is the cycle whose admission it blocked.
            if y_start <= row.reserved_at <= y_end:
                denials[row.denial_reason] += 1
                if row.source_site is not None:
                    source_denials[row.source_site.name] += 1
            continue
        share = _place(row, y_start, y_end, config, watermark)
        if share is None:
            continue
        provider = _provider_key(row)
        by_provider[provider].add(share)
        provider_rows[provider] += 1
        if row.admission_class == AdmissionClass.OPERATOR.value:
            operator.add(share)
            by_kind[row.operator_kind].add(share)
            kind_rows[row.operator_kind] += 1
        else:
            runtime.add(share)
            name = row.source_site.name if row.source_site is not None else "?"
            by_source[name].add(share)
            source_rows[name] += 1

    g = _guard(config)
    discovery = _discovery_allowance(config, _discovery_rows_touching(y_start, y_end, g))
    standing = Figure.of(lambda: standing_account_read_debit(cfg))
    authority_text, carried = _authority_text(y_start)
    allocation = Figure.of(lambda: project_allocation(cfg))

    fixed = (discovery.value or Decimal(0)) + (standing.value or Decimal(0))
    # The watch_refresh class check's left side (budget.decide_admission).
    remaining_project = _unavailable(allocation, discovery, standing) or Figure(
        (allocation.value or Decimal(0)) - (runtime.total + carried + fixed)
    )
    # HR_cycle of MS2-D-40: every class, carried consumption, and both allowances.
    hr_cycle = _unavailable(discovery, standing) or Figure(
        runtime.total + operator.total + carried + fixed
    )

    usage = cycle.account_usage_usd
    prepaid = cycle.account_prepaid_credit_usd
    remaining_prepaid = (
        Figure(prepaid - usage) if prepaid is not None and usage is not None else Figure(None)
    )
    headroom = Figure(None)
    external_observed = Figure(None)
    if prepaid is not None and usage is not None and hr_cycle.value is not None:
        hr_value = hr_cycle.value
        headroom = Figure.of(
            lambda: (
                prepaid
                - account_margin_usd(cfg, prepaid)
                - (usage + hr_value - runtime.hr_included - operator.hr_included)
            )
        )
        # The MS2-D-40 latch check's left side: what the account used beyond
        # Hardware Radar's own debits (may be negative, which is within bound).
        external_observed = Figure(usage - hr_value)
    elif hr_cycle.value is None:
        headroom = external_observed = Figure(None, hr_cycle.unavailable)

    external = cfg.external_liability_usd
    external_fig = (
        Figure(external)
        if external is not None and external.is_finite() and external >= 0
        else Figure(None, "external_liability_unbounded")
    )
    max_age = cfg.account_snapshot_max_age_s
    stale: bool | None = None
    if is_current and max_age is not None:
        observed = cycle.account_observed_at
        stale = observed is None or now - observed > timedelta(seconds=max_age)

    allowance = cfg.operator_allowance_usd
    operator_allowance = (
        Figure(allowance - operator.total)
        if allowance is not None and allowance.is_finite() and allowance >= 0
        else Figure(None, "budget_setting_invalid")
    )

    runs = ProviderRun.objects.filter(admitted_at__gte=y_start, admitted_at__lte=y_end)
    run_counts: dict[str, int] = defaultdict(int)
    run_usage: dict[str, Decimal] = defaultdict(Decimal)
    for run in runs.only("provider_kind", "usage_total_usd"):
        run_counts[run.provider_kind] += 1
        run_usage[run.provider_kind] += run.usage_total_usd or Decimal(0)

    providers = sorted(set(by_provider) | set(run_counts))
    return CycleReport(
        cycle_start=y_start,
        cycle_end=y_end,
        is_current=is_current,
        allocation=allocation,
        recorded_allocation=cycle.allocation_usd,
        consumed_settled=runtime.settled + operator.settled,
        outstanding=runtime.outstanding + operator.outstanding,
        monitoring=runtime.monitoring + operator.monitoring,
        discovery_allowance=discovery,
        standing_account_reads=standing,
        carried_handoff=carried,
        remaining_project_budget=remaining_project,
        account_usage=usage,
        prepaid_credit=prepaid,
        remaining_prepaid=remaining_prepaid,
        admission_headroom=headroom,
        account_observed_at=cycle.account_observed_at,
        snapshot_stale=stale,
        external_liability=external_fig,
        external_observed=external_observed,
        watermark=watermark_text,
        hr_included=runtime.hr_included + operator.hr_included,
        authority=authority_text,
        operator_by_kind={
            k: _totals(by_kind[k], kind_rows[k]) for k in sorted(set(by_kind) | set(kind_rows))
        },
        operator_allowance=operator_allowance,
        by_source={
            k: _totals(by_source[k], source_rows[k], denials=source_denials[k])
            for k in sorted(set(by_source) | set(source_denials))
        },
        by_provider={
            k: _totals(
                by_provider[k],
                provider_rows[k],
                provider_runs=run_counts[k],
                provider_observed=run_usage[k],
            )
            for k in providers
        },
        denials=dict(sorted(denials.items())),
    )


def _totals(
    share: _Share,
    rows: int,
    *,
    denials: int = 0,
    provider_runs: int = 0,
    provider_observed: Decimal = Decimal(0),
) -> Totals:
    return Totals(
        rows=rows,
        settled=share.settled,
        outstanding=share.outstanding,
        monitoring=share.monitoring,
        denials=denials,
        provider_runs=provider_runs,
        provider_observed_usd=provider_observed,
    )


def _unavailable(*parts: Figure) -> Figure | None:
    """An unavailable Figure carrying the first missing part's reason, or None."""
    return next((Figure(None, p.unavailable) for p in parts if p.value is None), None)


def build_report(
    *,
    config: LedgerConfig | None = None,
    now: datetime,
    cycles: int | None = None,
) -> SpendReport:
    """Assemble the report from stored ledger state as of `now`.

    `cycles` limits the per-cycle sections to the most recent N observed
    cycles; the ledger-wide sections always cover every row. Never raises for
    a settings problem: each affected figure is reported as unavailable.
    """
    config = config or load_ledger_config()
    rows = list(
        ApifySpendReservation.objects.select_related("provider_run", "source_site").order_by("pk")
    )
    current = current_cycle(now)
    observed = list(ApifyBudgetCycle.objects.order_by("-cycle_start"))
    if cycles is not None:
        observed = observed[:cycles]
    observed.reverse()
    cycle_reports = [
        _cycle_report(c, rows, config, now, is_current=current is not None and c.pk == current.pk)
        for c in observed
    ]

    unsettled: list[FlaggedRow] = []
    overruns: list[FlaggedRow] = []
    for row in rows:
        if row.status in _OPEN_STATUSES:
            unsettled.append(
                FlaggedRow(
                    row.pk,
                    _label(row),
                    row.reserved_at,
                    row.estimate_usd,
                    _unsettled_reasons(row, config),
                )
            )
        elif _close_overdue(row, config, now):
            until = row.correction_monitor_until
            assert until is not None
            unsettled.append(
                FlaggedRow(
                    row.pk,
                    _label(row),
                    row.reserved_at,
                    row.actual_usd,
                    (CORRECTION_CLOSE_OVERDUE, f"deadline={_ts(until)}"),
                )
            )
        if row.status == ReservationStatus.RECONCILED.value:
            reasons: list[str] = []
            if (
                row.actual_usd is not None
                and row.estimate_usd is not None
                and row.actual_usd > row.estimate_usd
            ):
                reasons.append(f"actual_above_estimate (estimate {_dollars(row.estimate_usd)})")
            if row.settlement_basis == SettlementBasis.BOUND_UNFINALIZED.value:
                reasons.append(API_CALL_CAP_EXHAUSTED)
            if reasons:
                overruns.append(
                    FlaggedRow(row.pk, _label(row), row.reserved_at, row.actual_usd, tuple(reasons))
                )

    latches = [
        _latch_text(latch, rows)
        for latch in ApifyBudgetLatch.objects.select_related("provider_run").order_by("pk")
    ]

    window_start = now - TREND_WINDOW
    trend = _Share()
    for row in rows:
        share = _place(row, window_start, now, config, None)
        if share is not None:
            trend.add(share)
    trend_discovery = _discovery_allowance(
        config, _discovery_rows_touching(window_start, now, _guard(config))
    )
    trend_total = trend.total + (trend_discovery.value or Decimal(0))
    try:
        warning: bool | None = trailing_window_warning(trend_total, config.budget)
    except BudgetDenied:
        warning = None

    return SpendReport(
        generated_at=now,
        cycles=cycle_reports,
        discovery=discovery_status(config),
        unsettled=unsettled,
        overruns=overruns,
        latches=latches,
        trend_total=trend_total,
        trend_discovery=trend_discovery,
        trend_warning=warning,
        trend_window_start=window_start,
    )


def _latch_text(latch: ApifyBudgetLatch, rows: Iterable[ApifySpendReservation]) -> str:
    # cleared_reason is owner free text and deliberately not printed (SCOPE).
    state = "OPEN" if latch.cleared_at is None else f"cleared {_ts(latch.cleared_at)}"
    text = f"latch {latch.pk}: {latch.reason} tripped {_ts(latch.tripped_at)} [{state}]"
    run = latch.provider_run
    if run is not None:
        match = next((r.pk for r in rows if r.provider_run == run), None)
        text += f" reservation={match}" if match is not None else " (run has no reservation)"
    return text


# ── Rendering ────────────────────────────────────────────────────────────────


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _money(value: Decimal | None) -> str:
    return "not observed" if value is None else _dollars(value)


def _totals_line(key: str, t: Totals) -> str:
    line = (
        f"    {key}: rows={t.rows} settled={_dollars(t.settled)}"
        f" outstanding={_dollars(t.outstanding)} monitoring={_dollars(t.monitoring)}"
        f" total={_dollars(t.total)}"
    )
    if t.denials:
        line += f" denials={t.denials}"
    if t.provider_runs or t.provider_observed_usd:
        line += (
            f" provider_runs={t.provider_runs}"
            f" provider_observed_usage={_dollars(t.provider_observed_usd)}"
        )
    return line


def _render_cycle(c: CycleReport) -> list[str]:
    marker = " [current]" if c.is_current else ""
    allocation = c.allocation.text()
    if c.recorded_allocation is not None and c.recorded_allocation != c.allocation.value:
        allocation += f" (recorded at cycle open: {_dollars(c.recorded_allocation)})"
    stale = {None: "", True: " [STALE]", False: " [fresh]"}[c.snapshot_stale]
    observed = _ts(c.account_observed_at) if c.account_observed_at is not None else "never"
    lines = [
        f"Billing cycle {_ts(c.cycle_start)} -> {_ts(c.cycle_end)}{marker}",
        f"  project allocation (A):          {allocation}",
        f"  consumed (settled usage):        {_dollars(c.consumed_settled)}",
        f"  outstanding reservations:        {_dollars(c.outstanding)}",
        f"  monitoring allowances:           {_dollars(c.monitoring)}",
        f"  discovery read allowance:        {c.discovery_allowance.text()}",
        f"  standing account-read debit:     {c.standing_account_reads.text()}",
        f"  carried handoff consumption:     {_dollars(c.carried_handoff)}",
        f"  remaining project budget:        {c.remaining_project_budget.text()}",
        f"  account snapshot observed:       {observed}{stale}",
        f"  observed account usage:          {_money(c.account_usage)}",
        f"  observed prepaid credit:         {_money(c.prepaid_credit)}",
        f"  remaining prepaid allowance:     {c.remaining_prepaid.text()}",
        f"  admission headroom (snapshot):   {c.admission_headroom.text()}",
        f"  declared external liability:     {c.external_liability.text()}",
        f"  observed non-HR account usage:   {c.external_observed.text()}",
        f"  inclusion watermark:             {c.watermark}",
        f"  HR_included (settled in snapshot): {_dollars(c.hr_included)}",
        f"  ledger authority:                {c.authority}",
        f"  operator allowance remaining:    {c.operator_allowance.text()}",
        "  operator consumption by kind:",
    ]
    lines += [_totals_line(k, t) for k, t in c.operator_by_kind.items()] or ["    (none)"]
    lines.append("  per source:")
    lines += [_totals_line(k, t) for k, t in c.by_source.items()] or ["    (none)"]
    lines.append("  per provider (ledger debits; provider_run runs admitted this cycle):")
    lines += [_totals_line(k, t) for k, t in c.by_provider.items()] or ["    (none)"]
    lines.append("  denials by reason:")
    lines += [f"    {k}: {n}" for k, n in c.denials.items()] or ["    (none)"]
    return lines


def _render_flagged(rows: list[FlaggedRow]) -> list[str]:
    if not rows:
        return ["  (none)"]
    return [
        f"  #{r.reservation_id} {r.label} reserved {_ts(r.reserved_at)}"
        f" amount {_money(r.amount)}: {', '.join(r.reasons)}"
        for r in rows
    ]


def render_report(report: SpendReport) -> str:
    lines = [f"Apify spend report as of {_ts(report.generated_at)}"]
    if report.discovery is not None:
        lines.append(f"cycle discovery: {report.discovery}")
    if not report.cycles:
        lines.append("No billing cycle observed (admission: cycle_unknown).")
    for c in report.cycles:
        lines.append("")
        lines += _render_cycle(c)
    lines += ["", "Unsettled rows:", *_render_flagged(report.unsettled)]
    lines += ["", "Overrun rows:", *_render_flagged(report.overruns)]
    lines += ["", "Overrun latch trips:"]
    lines += [f"  {text}" for text in report.latches] or ["  (none)"]
    warning = {
        None: "unavailable (allocation settings invalid)",
        True: "WARNING: above the project allocation",
        False: "within the project allocation",
    }[report.trend_warning]
    lines += [
        "",
        "Trailing 31 days (secondary trend metric; never admits or denies):",
        f"  {_ts(report.trend_window_start)} -> {_ts(report.generated_at)}:"
        f" {_dollars(report.trend_total)} ({warning})",
    ]
    if report.trend_discovery.value is None:
        lines.append(f"  discovery allowance {report.trend_discovery.text()}, not included")
    return "\n".join(lines) + "\n"
