"""Shared fixtures for the E3 ledger tests (test_apify_ledger*.py).

The budget settings are test_apify_budget.py's figures (round prices, NOT
Apify's live ones): one SHAPE run reserves $0.0767 plus a $0.0022 monitoring
allowance ($0.0789 debited). With the configured account state of MS2-D-48
(anchor C1_START, $19 limit, 10% margin, $5 external bound) P is $17.10 and
the runtime allocation A is $11.00. Tests size filler rows
from these figures through the helpers, never from literals, so a price
change moves every boundary together.

The E3 tests seed reconciled rows directly (`reconcile`, `close_monitoring`)
to test the cycle predicate in isolation; the E4 tests below settle rows
through acquisition.apify.reconcile instead, from the `settled_run` fixture.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import httpx

from hw_radar.acquisition.apify.budget import (
    AdmissionRequest,
    BudgetClass,
    BudgetSettings,
    CostEstimate,
    RunShape,
    UnitPrices,
    billing_cycle_bounds,
    estimate_run_cost,
    project_allocation,
)
from hw_radar.acquisition.apify.client import ApifyClient, ApifyRun, RunOptions
from hw_radar.acquisition.apify.ledger import LedgerConfig
from hw_radar.catalog.models import (
    AdmissionClass,
    ApifyBudgetCycle,
    ApifyBudgetLatch,
    ApifyLedgerAuthority,
    ApifySpendReservation,
    ApifyUsageRead,
    ProviderKind,
    ProviderRun,
    ReservationStatus,
    RunKind,
    SourceSite,
    SourceType,
)
from hw_radar.catalog.models.provider import LedgerAuthorityKind

D = Decimal
HOUR: Final = timedelta(hours=1)
DAY: Final = timedelta(days=1)
GUARD: Final = HOUR  # CYCLE_BOUNDARY_GUARD_S below
LEDGER_A: Final = "env-a"
LEDGER_B: Final = "env-b"

# Anniversary cycles (MS2-D-40): the 5th to the 4th, never calendar months.
C1_START: Final = datetime(2026, 9, 5, tzinfo=UTC)
C1_END: Final = datetime(2026, 10, 4, 23, 59, 59, 999000, tzinfo=UTC)
C2_START: Final = datetime(2026, 10, 5, tzinfo=UTC)
C2_END: Final = datetime(2026, 11, 4, 23, 59, 59, 999000, tzinfo=UTC)
C3_START: Final = datetime(2026, 11, 5, tzinfo=UTC)
C3_END: Final = datetime(2026, 12, 4, 23, 59, 59, 999000, tzinfo=UTC)
NOW: Final = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

BUDGET: Final = BudgetSettings(
    enabled=True,
    prices=UnitPrices(
        usd_per_cu=D("0.20"),
        dataset_reads_per_1000=D("0.0004"),
        dataset_writes_per_1000=D("0.005"),
        dataset_storage_per_gb_hour=D("0.001"),
        kv_reads_per_1000=D("0.005"),
        kv_writes_per_1000=D("0.05"),
        kv_storage_per_gb_hour=D("0.001"),
        transfer_per_gb=D("0.20"),
    ),
    margin=D("0.10"),
    estimator_version="1",
    max_timeout_s=3600,
    cycle_target_usd=D("12.00"),
    operator_allowance_usd=D("1.00"),
    watch_refresh_reserve_usd=D("3.00"),
    cash_ceiling_usd=D("20.00"),
    account_margin_usd=None,
    external_liability_usd=D("5.00"),
    call_billing_residual_accepted=date(2026, 9, 25),
    operator_build_bound_usd=D("0.41"),
    operator_inspect_max_items=1000,
    operator_inspect_max_record_reads=20,
    operator_inspect_max_bytes=10_000_000,
    operator_probe_max_calls=10,
    max_dataset_reads=3,
    max_kv_reads=3,
    max_delete_attempts=10,
    max_run_polls=60,
    max_correction_reads=12,
    max_kv_writes=3,
    max_kv_bytes=65536,
    storage_max_lifetime_s=31 * 86400,
    api_call_overhead_bytes=262144,
    max_api_response_bytes=262144,
    max_dataset_page_bytes=1_048_576,
    cycle_boundary_guard_s=int(GUARD.total_seconds()),
    billing_cycle_anchor=C1_START,
    account_limit_usd=D("19.00"),
    account_base_price_usd=D("19.00"),
    account_data_retention_days=31,
    account_verified_on=C1_START.date(),
)
CONFIG: Final = LedgerConfig(budget=BUDGET, ledger_id=LEDGER_A)
SHAPE: Final = RunShape(
    memory_mb=1024, timeout_s=600, max_items=100, max_requests=20, max_bytes=5_000_000
)
WATCH: Final = AdmissionRequest(budget_class=BudgetClass.WATCH_REFRESH, run=SHAPE)
RUN_ESTIMATE: Final = estimate_run_cost(SHAPE, BUDGET)
RUN_DEBIT: Final = RUN_ESTIMATE.admission_usd  # estimate + monitoring allowance
ALLOCATION: Final = project_allocation(BUDGET)


# Tests driven by the real clock (the start job and the commands stamp real
# time) configure the anchor as the first of the current UTC month, so the
# configured cycle always covers their now, whatever the date the suite runs.
_TODAY: Final = datetime.now(UTC)
LIVE_ANCHOR: Final = datetime(_TODAY.year, _TODAY.month, 1, tzinfo=UTC)
LIVE_BUDGET: Final = dataclasses.replace(
    BUDGET, billing_cycle_anchor=LIVE_ANCHOR, account_verified_on=LIVE_ANCHOR.date()
)
LIVE_CONFIG: Final = dataclasses.replace(CONFIG, budget=LIVE_BUDGET)


def live_cycle_start() -> datetime:
    """The start of the cycle LIVE_ANCHOR derives for the real clock."""
    bounds = billing_cycle_bounds(LIVE_ANCHOR, datetime.now(UTC))
    assert bounds is not None
    return bounds[0]


def live_cycle() -> ApifyBudgetCycle:
    """The recorded LIVE_ANCHOR cycle covering the real now, as ensure_cycle writes it."""
    bounds = billing_cycle_bounds(LIVE_ANCHOR, datetime.now(UTC))
    assert bounds is not None
    return cycle(*bounds)


def config(*, budget: dict[str, Any] | None = None, **changes: Any) -> LedgerConfig:
    base = dataclasses.replace(CONFIG, **changes)
    return dataclasses.replace(base, budget=dataclasses.replace(BUDGET, **(budget or {})))


def site(key: str = "e3ledger") -> SourceSite:
    return SourceSite.objects.get_or_create(
        normalized_name=key, defaults={"name": key, "source_type": SourceType.OTHER}
    )[0]


def cycle(start: datetime = C1_START, end: datetime = C1_END) -> ApifyBudgetCycle:
    """A recorded cycle row as ledger.ensure_cycle writes it: account_* columns null."""
    return ApifyBudgetCycle.objects.create(
        cycle_start=start, cycle_end=end, allocation_usd=ALLOCATION, opened_at=start
    )


def claim(start: datetime = C1_START, ledger_id: str = LEDGER_A) -> ApifyLedgerAuthority:
    return ApifyLedgerAuthority.objects.create(
        cycle_start=start,
        kind=LedgerAuthorityKind.ORIGIN,
        ledger_id=ledger_id,
        attested_by="test owner",
        created_at=start,
    )


def provider_run(src: SourceSite, admitted_at: datetime, **fields: Any) -> ProviderRun:
    return ProviderRun.objects.create(
        source_site=src,
        provider_kind=ProviderKind.APIFY,
        scope_key="e3ledger:gpu:q1",
        memory_mb=SHAPE.memory_mb,
        timeout_s=SHAPE.timeout_s,
        max_items=SHAPE.max_items,
        max_pages=5,
        admission_class=AdmissionClass.WATCH_REFRESH,
        run_kind=RunKind.FULL,
        admitted_at=admitted_at,
        storage_cleanup_due_at=admitted_at + DAY,
        **fields,
    )


def open_row(
    amount: Decimal,
    reserved_at: datetime,
    *,
    admission_class: AdmissionClass = AdmissionClass.WATCH_REFRESH,
    status: ReservationStatus = ReservationStatus.RESERVED,
    run: ProviderRun | None = None,
    operator_kind: str = "",
) -> ApifySpendReservation:
    """An unreconciled row whose full `amount` is its estimate (no monitoring)."""
    runtime = admission_class != AdmissionClass.OPERATOR
    return ApifySpendReservation.objects.create(
        admission_class=admission_class,
        operator_kind=operator_kind,
        source_site=site() if runtime else None,
        provider_run=run,
        status=status,
        estimate_usd=amount,
        execution_bound_usd=amount,
        post_run_liability_usd=D(0),
        monitoring_bound_usd=D(0),
        estimator_version="1",
        reserved_at=reserved_at,
    )


def reconcile(
    row: ApifySpendReservation,
    *,
    actual: Decimal,
    last_charge_at: datetime,
    reconciled_at: datetime | None = None,
    closing_read_at: datetime | None = None,
    closing_usage: Decimal | None = None,
    run: ProviderRun | None = None,
) -> ApifySpendReservation:
    """Stand in for E4: settle `row` and, if `closing_read_at`, close its monitoring.

    correction_monitor_until is `last_charge_at + 7 days` (the default window);
    without a closing read the obligation stays open, as after a real
    reconciliation.
    """
    if row.admission_class != AdmissionClass.OPERATOR.value and row.provider_run is None:
        row.provider_run = run or provider_run(site(), row.reserved_at)
    row.status = ReservationStatus.RECONCILED
    row.actual_usd = actual
    row.settled_run_usage_usd = actual
    row.reconciled_at = reconciled_at or last_charge_at
    row.last_charge_at = last_charge_at
    row.correction_monitor_until = last_charge_at + 7 * DAY
    row.save()
    if closing_read_at is not None:
        close_monitoring(row, read_at=closing_read_at, usage=closing_usage or actual)
    return row


def usage_read(row: ApifySpendReservation, *, read_at: datetime, usage: Decimal) -> ApifyUsageRead:
    return ApifyUsageRead.objects.create(
        reservation=row,
        provider_run=row.provider_run,
        read_at=read_at,
        usage_total_usd=usage,
        price_settings_version="1",
    )


def close_monitoring(
    row: ApifySpendReservation, *, read_at: datetime, usage: Decimal
) -> ApifyUsageRead:
    """Stand in for E4's MS2-D-47 closing-read transaction (read + raise + close)."""
    read = usage_read(row, read_at=read_at, usage=usage)
    settled = row.settled_run_usage_usd or D(0)
    actual = row.actual_usd or D(0)
    if usage > settled:
        actual, settled = actual + usage - settled, usage
    # One UPDATE: the closure CHECK needs the closing read in the same write,
    # and django-types cannot type the string-referenced FK's attribute setter.
    ApifySpendReservation.objects.filter(pk=row.pk).update(
        settled_run_usage_usd=settled,
        actual_usd=actual,
        correction_monitor_closed_at=read_at,
        correction_closing_read=read,
        monitoring_charge_last_at=read_at,
    )
    row.refresh_from_db()
    return read


def reserved(outcome_id: int | None) -> ApifySpendReservation:
    assert outcome_id is not None
    return ApifySpendReservation.objects.get(pk=outcome_id)


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ── E4: settlement fixtures (reconcile.py) ──
#
# A run whose import is finalized and whose storage is verified deleted, with
# the work-completion anchor at ANCHOR. Eligible settlement reads start at
# ELIGIBLE (anchor + the 10 s settle delay + the 1 h cycle-boundary guard).

ANCHOR: Final = NOW
FINISHED: Final = NOW - timedelta(minutes=10)
ELIGIBLE: Final = ANCHOR + timedelta(seconds=10) + GUARD
STABLE: Final = dataclasses.replace(CONFIG, run_usage_settlement="stable_reads")
_run_ids = iter(range(1, 1_000_000))


def settled_run(
    *,
    reserved_at: datetime = NOW - HOUR,
    anchored: bool = True,
    estimate: CostEstimate = RUN_ESTIMATE,
    admission_class: AdmissionClass = AdmissionClass.WATCH_REFRESH,
    **run_fields: Any,
) -> tuple[ProviderRun, ApifySpendReservation]:
    """A terminal run attached to a `reserved` reservation of `estimate`.

    anchored=True: import finalized, storage deleted, anchor stamped (the
    state both barrier transactions leave). Otherwise the run is terminal
    with its import and storage still open.
    """
    n = next(_run_ids)
    fields: dict[str, Any] = {
        "external_run_id": f"e4-run-{n}",
        "import_idempotency_key": f"apify:e4-run-{n}",
        "remote_status": "SUCCEEDED",
        "started_at": reserved_at + timedelta(minutes=5),
        "finished_at": FINISHED,
        "dataset_id": f"ds-e4-{n}",
        "kv_store_id": f"kv-e4-{n}",
    }
    if anchored:
        fields |= {
            "import_state": "finalized",
            "storage_state": "deleted",
            "storage_deleted_at": ANCHOR,
            "final_charge_op_at": ANCHOR,
        }
    fields.update(run_fields)
    run = provider_run(site(), reserved_at, **fields)
    resv = ApifySpendReservation.objects.create(
        admission_class=admission_class,
        source_site=site(),
        provider_run=run,
        estimate_usd=estimate.estimate_usd,
        execution_bound_usd=estimate.execution_bound_usd,
        post_run_liability_usd=estimate.post_run_liability_usd,
        monitoring_bound_usd=estimate.monitoring_bound_usd,
        component_bounds={k: str(v) for k, v in estimate.components.items()},
        estimator_version=estimate.estimator_version,
        reserved_at=reserved_at,
    )
    return run, resv


def custom_estimate(
    estimate: str, *, execution: str | None = None, post: str = "0", monitoring: str = "0.0022"
) -> CostEstimate:
    """A round-figure estimate for the MS2-D-47 scenarios ($2 reserved, and so on)."""
    return CostEstimate(
        components={"compute": D(execution or estimate)},
        execution_bound_usd=D(execution or estimate),
        post_run_liability_usd=D(post),
        monitoring_bound_usd=D(monitoring),
        estimate_usd=D(estimate),
        estimator_version="1",
    )


def api_run(
    run_id: str,
    total: str | None,
    *,
    status: str = "SUCCEEDED",
    usage_usd: dict[str, str] | None = None,
    usage: dict[str, str] | None = None,
    unparseable: tuple[str, ...] = (),
    restart_count: int | None = 0,
    finished_at: datetime | None = FINISHED,
) -> ApifyRun:
    """A parsed `GET` run answer, as the client would return it."""
    return ApifyRun(
        id=run_id,
        act_id="act-e4",
        status=status,
        status_message=None,
        started_at=FINISHED - HOUR,
        finished_at=finished_at,
        build_id="build-e4",
        build_number="1.0.1",
        default_dataset_id=None,
        default_key_value_store_id=None,
        options=RunOptions(
            memory_mbytes=SHAPE.memory_mb,
            timeout_secs=SHAPE.timeout_s,
            build="latest",
            max_items=None,
            restart_on_error=None,
        ),
        usage_total_usd=None if total is None else D(total),
        usage_usd={k: D(v) for k, v in (usage_usd or {}).items()}
        if usage_usd is not None
        else None,
        usage={k: D(v) for k, v in (usage or {}).items()} if usage is not None else None,
        unparseable_usage=unparseable,
        restart_count=restart_count,
    )


def open_latches() -> list[str]:
    return sorted(
        ApifyBudgetLatch.objects.filter(cleared_at__isnull=True).values_list("reason", flat=True)
    )


class Clock:
    """A settable clock for apify_poll_tick(clock=...)."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


class FakeRuns:
    """MockTransport for selector 1/2/4 run and build reads (never the network).

    `totals[id]` is the usageTotalUsd a `GET` answers (None: a null total);
    ids in `failing` answer 500, ids in `missing` 404, ids in `huge` a body
    over the control-response cap. `builds[id]` answers `GET` build the same way.
    """

    def __init__(self) -> None:
        self.totals: dict[str, str | None] = {}
        self.builds: dict[str, str | None] = {}
        self.build_finished: dict[str, str] = {}
        self.failing: set[str] = set()
        self.missing: set[str] = set()
        self.huge: set[str] = set()
        self.status: dict[str, str] = {}
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        self.requests.append(f"{request.method} {request.url.path}")
        remote = parts[3] if len(parts) > 3 else ""
        if remote in self.failing:
            return httpx.Response(500, json={"error": {"type": "internal", "message": "x"}})
        if remote in self.missing:
            return httpx.Response(404, json={"error": {"type": "record-not-found", "message": "x"}})
        if remote in self.huge:
            return httpx.Response(200, content=b"{" + b" " * 300_000 + b"}")
        if request.method == "GET" and parts[:3] == ["", "v2", "actor-runs"]:
            total = self.totals.get(remote)
            body = {
                "id": remote,
                "status": self.status.get(remote, "SUCCEEDED"),
                "startedAt": _z(FINISHED - HOUR),
                "finishedAt": _z(FINISHED),
                "options": {"memoryMbytes": SHAPE.memory_mb, "timeoutSecs": SHAPE.timeout_s},
                "stats": {"restartCount": 0},
                "usageTotalUsd": "@T@",
                "usageUsd": {"ACTOR_COMPUTE_UNITS": "@T@"},
            }
            text = json.dumps({"data": body}).replace(
                '"@T@"', total if total is not None else "null"
            )
            return httpx.Response(200, text=text)
        if request.method == "GET" and parts[:3] == ["", "v2", "actor-builds"]:
            total = self.builds.get(remote)
            body = {
                "id": remote,
                "status": "SUCCEEDED",
                "startedAt": _z(FINISHED - HOUR),
                "finishedAt": self.build_finished.get(remote, _z(FINISHED)),
                "usageTotalUsd": "@T@",
                "usageUsd": {"ACTOR_COMPUTE_UNITS": "@T@"},
            }
            text = json.dumps({"data": body}).replace(
                '"@T@"', total if total is not None else "null"
            )
            return httpx.Response(200, text=text)
        return httpx.Response(500, json={"error": {"type": "unexpected", "message": "path"}})

    def client(self) -> ApifyClient:
        return ApifyClient("test-token", transport=httpx.MockTransport(self))
