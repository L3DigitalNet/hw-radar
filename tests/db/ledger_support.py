"""Shared fixtures for the E3 ledger tests (test_apify_ledger*.py).

The budget settings are test_apify_budget.py's figures (round prices, NOT
Apify's live ones): one SHAPE run reserves $0.0767 plus a $0.0022 monitoring
allowance ($0.0789 debited), and the standing account-read debit is $0.5433.
With the verified-shape snapshot ($19 prepaid, 10% margin, $5 external bound)
P is $17.10 and the runtime allocation A is $11.00. Tests size filler rows
from these figures through the helpers, never from literals, so a price
change moves every boundary together.

Rows the reconcile unit (E4) will write are seeded here directly: E3 must
count them correctly before E4 exists to produce them.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

import httpx

from hw_radar.acquisition.apify.budget import (
    AdmissionRequest,
    BudgetClass,
    BudgetSettings,
    RunShape,
    UnitPrices,
    estimate_run_cost,
    project_allocation,
    standing_account_read_debit,
)
from hw_radar.acquisition.apify.client import ApifyClient
from hw_radar.acquisition.apify.ledger import LedgerConfig
from hw_radar.catalog.models import (
    AdmissionClass,
    ApifyBudgetCycle,
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
# The verified 2026-09-24 account state (MS2-D-40): $19 prepaid, ~$0.09 used.
VERIFIED_PREPAID: Final = D("19")
VERIFIED_USAGE: Final = D("0.09")

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
    max_account_reads_per_cycle=3000,
    account_snapshot_max_age_s=900,
    cycle_boundary_guard_s=int(GUARD.total_seconds()),
)
CONFIG: Final = LedgerConfig(
    budget=BUDGET,
    ledger_id=LEDGER_A,
    usage_inclusion_lag_s=None,
    max_discovery_reads=24,
    discovery_read_interval_s=300,
)
SHAPE: Final = RunShape(
    memory_mb=1024, timeout_s=600, max_items=100, max_requests=20, max_bytes=5_000_000
)
WATCH: Final = AdmissionRequest(budget_class=BudgetClass.WATCH_REFRESH, run=SHAPE)
RUN_ESTIMATE: Final = estimate_run_cost(SHAPE, BUDGET)
RUN_DEBIT: Final = RUN_ESTIMATE.admission_usd  # estimate + monitoring allowance
STANDING: Final = standing_account_read_debit(BUDGET)
ALLOCATION: Final = project_allocation(BUDGET)


def config(*, budget: dict[str, Any] | None = None, **changes: Any) -> LedgerConfig:
    base = dataclasses.replace(CONFIG, **changes)
    return dataclasses.replace(base, budget=dataclasses.replace(BUDGET, **(budget or {})))


def site(key: str = "e3ledger") -> SourceSite:
    return SourceSite.objects.get_or_create(
        normalized_name=key, defaults={"name": key, "source_type": SourceType.OTHER}
    )[0]


def cycle(
    start: datetime = C1_START,
    end: datetime = C1_END,
    *,
    observed_at: datetime | None = NOW,
    usage: Decimal = VERIFIED_USAGE,
    prepaid: Decimal = VERIFIED_PREPAID,
) -> ApifyBudgetCycle:
    return ApifyBudgetCycle.objects.create(
        cycle_start=start,
        cycle_end=end,
        allocation_usd=ALLOCATION,
        account_prepaid_credit_usd=prepaid,
        account_base_price_usd=D("19"),
        account_limit_usd=D("19"),
        account_usage_usd=usage,
        account_observed_at=observed_at,
        account_data_retention_days=31,
        opened_at=start,
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


# ── Fake Apify account endpoints (httpx.MockTransport only) ──


def account_client(
    *,
    cycle_start: datetime = C1_START,
    cycle_end: datetime = C1_END,
    usage: str = "0.09",
    fail: Callable[[httpx.Request], None] | None = None,
) -> ApifyClient:
    """An ApifyClient whose account reads answer the given cycle.

    `fail(request)` runs first and may raise (a transport error, or a crash
    injected after the counter commit and before the call).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if fail is not None:
            fail(request)
        if request.url.path == "/v2/users/me/limits":
            body = {
                "data": {
                    "monthlyUsageCycle": {"startAt": _z(cycle_start), "endAt": _z(cycle_end)},
                    "limits": {"maxMonthlyUsageUsd": 19, "dataRetentionDays": 31},
                    "current": {"monthlyUsageUsd": "@USAGE@"},
                }
            }
            # The usage is spliced in as a bare JSON number (the client parses
            # numbers, not strings, as money).
            text = json.dumps(body).replace('"@USAGE@"', usage)
            return httpx.Response(200, text=text)
        if request.url.path == "/v2/users/me":
            return httpx.Response(
                200,
                text='{"data": {"plan": {"id": "STARTER", "monthlyBasePriceUsd": 19,'
                ' "monthlyUsageCreditsUsd": 19}}}',
            )
        return httpx.Response(404, text='{"error": {"type": "record-not-found"}}')

    return ApifyClient("test-token", transport=httpx.MockTransport(handler))


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
