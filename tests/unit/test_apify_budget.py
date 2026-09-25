"""Plan E2: the pure Apify budget policy (MS2-D-17, -26, -32, -40, -41, -46).

Every test builds BudgetSettings and LedgerState by hand; nothing here touches
the database. The fixture prices are NOT Apify's live prices (the plan keeps no
live default): they are round figures chosen so KV writes are the dearest
operation and transfer is $0.20/GB, which reproduces the plan's worked
api_call_bound of about $0.000181 at the default caps.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest

from hw_radar.acquisition.apify import budget
from hw_radar.acquisition.apify.budget import (
    HTTP_READ_CHUNK_BYTES,
    AccountSnapshot,
    AdmissionDecision,
    AdmissionRequest,
    BudgetClass,
    BudgetDenied,
    BudgetSettings,
    CostEstimate,
    CycleDebits,
    DenialReason,
    LedgerState,
    OperatorKind,
    RunShape,
    UnitPrices,
    actor_fetch_bytes,
    api_call_bound,
    dataset_page_bound,
    decide_admission,
    estimate_operator_cost,
    estimate_run_cost,
    load_budget_settings,
    pages_per_read,
    per_call_bounds,
    project_allocation,
    request_wire_overhead,
    settle_envelope,
    standing_account_read_debit,
    storage_hours,
    trailing_window_warning,
)
from hw_radar.acquisition.apify.client import HTTP_RECEIVE_BUFFER_BYTES, MAX_API_REQUEST_BODY_BYTES
from hw_radar.acquisition.apify.contract import MAX_LISTING_ROW_BYTES, page_limit

R = DenialReason
D = Decimal
GB: Final = D(10**9)

PRICES: Final = UnitPrices(
    usd_per_cu=D("0.20"),
    dataset_reads_per_1000=D("0.0004"),
    dataset_writes_per_1000=D("0.005"),
    dataset_storage_per_gb_hour=D("0.001"),
    kv_reads_per_1000=D("0.005"),
    kv_writes_per_1000=D("0.05"),
    kv_storage_per_gb_hour=D("0.001"),
    transfer_per_gb=D("0.20"),
)

CFG: Final = BudgetSettings(
    enabled=True,
    prices=PRICES,
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
    cycle_boundary_guard_s=3600,
    # The verified account state of 2026-09-25 (MS2-D-48): the observed cycle
    # start, the $19 limit (= the prepaid credit), Starter base, retention 31.
    billing_cycle_anchor=datetime(2026, 9, 5, tzinfo=UTC),
    account_limit_usd=D("19.00"),
    account_base_price_usd=D("19.00"),
    account_data_retention_days=31,
    account_verified_on=date(2026, 9, 5),
)

NOW: Final = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
# The verified account state of 2026-09-24 (MS2-D-40): an anniversary cycle,
# Starter $19 base and $19 prepaid credit, retention 31 days.
SNAPSHOT: Final = AccountSnapshot(
    cycle_start=datetime(2026, 9, 5, tzinfo=UTC),
    cycle_end=datetime(2026, 10, 4, 23, 59, 59, 999000, tzinfo=UTC),
    observed_at=NOW - timedelta(seconds=60),
    prepaid_credit_usd=D("19"),
    base_price_usd=D("19"),
    account_usage_usd=D("0.09"),
    data_retention_days=31,
    account_limit_usd=D("19"),
)
LEDGER: Final = LedgerState(now=NOW, latch_tripped=False, authority_held=True, snapshot=SNAPSHOT)
SHAPE: Final = RunShape(
    memory_mb=1024, timeout_s=600, max_items=100, max_requests=20, max_bytes=5_000_000
)
WATCH: Final = AdmissionRequest(budget_class=BudgetClass.WATCH_REFRESH, run=SHAPE)
DISCOVERY: Final = AdmissionRequest(budget_class=BudgetClass.DISCOVERY, run=SHAPE)
BUILD: Final = AdmissionRequest(budget_class=BudgetClass.OPERATOR, operator_kind=OperatorKind.BUILD)
INSPECT: Final = AdmissionRequest(
    budget_class=BudgetClass.OPERATOR, operator_kind=OperatorKind.INSPECT
)
PROBE: Final = AdmissionRequest(budget_class=BudgetClass.OPERATOR, operator_kind=OperatorKind.PROBE)
EVERY_CLASS: Final = (WATCH, DISCOVERY, BUILD, INSPECT, PROBE)
CENT: Final = D("0.01")
TICK: Final = D("0.0001")
ZERO: Final = D(0)


def cfg(**changes: Any) -> BudgetSettings:
    return dataclasses.replace(CFG, **changes)


def prices(**changes: Any) -> UnitPrices:
    return dataclasses.replace(PRICES, **changes)


def ledger(*, snapshot: AccountSnapshot | None = SNAPSHOT, **changes: Any) -> LedgerState:
    return dataclasses.replace(LEDGER, snapshot=snapshot, **changes)


def snap(**changes: Any) -> AccountSnapshot:
    return dataclasses.replace(SNAPSHOT, **changes)


def decide(
    request: AdmissionRequest = WATCH,
    settings: BudgetSettings = CFG,
    state: LedgerState = LEDGER,
) -> AdmissionDecision:
    return decide_admission(request, settings, state)


def estimate_of(request: AdmissionRequest, settings: BudgetSettings = CFG) -> CostEstimate:
    if request.run is not None:
        return estimate_run_cost(request.run, settings)
    assert request.operator_kind is not None
    return estimate_operator_cost(request.operator_kind, settings)


def usable(settings: BudgetSettings = CFG, snapshot: AccountSnapshot = SNAPSHOT) -> Decimal:
    """P = prepaid credit - account margin."""
    assert snapshot.prepaid_credit_usd is not None
    return snapshot.prepaid_credit_usd - budget.account_margin_usd(
        settings, snapshot.prepaid_credit_usd
    )


def at_snapshot_edge(
    request_: AdmissionRequest = WATCH,
    *,
    over: Decimal = ZERO,
    settings: BudgetSettings = CFG,
    **snapshot_changes: Any,
) -> LedgerState:
    """A ledger where check 1 (the account snapshot) has exactly `new - over` left.

    Hardware Radar holds $6.00 of runtime debits, so the account usage that
    fills P leaves other workloads' observed consumption (usage - HR_cycle,
    about $3.94) inside the declared $5.00 external bound: check 1 binds, not
    external_liability_exceeded, and not the class cap.
    """
    runtime = D("6.00")
    new = estimate_of(request_, settings).admission_usd
    hr = runtime + standing_account_read_debit(settings)
    usage = usable(settings) - hr - new + over
    return ledger(
        snapshot=snap(account_usage_usd=usage, **snapshot_changes),
        current=CycleDebits(runtime_committed_usd=runtime),
    )


def assert_denied(decision: AdmissionDecision, reason: DenialReason) -> None:
    assert not decision.admitted
    assert decision.reason is reason, decision.detail


# ── The component formula (MS2-D-26 table, MS2-D-32 split) ──────────────────


def test_component_formula() -> None:
    est = estimate_run_cost(SHAPE, CFG)
    ds_read, ds_write = D("0.0004") / 1000, D("0.005") / 1000
    kv_read, kv_write = D("0.005") / 1000, D("0.05") / 1000
    per_byte = D("0.20") / GB
    wire_overhead = 262144 + 2 * 65536
    call = kv_write + (wire_overhead + 262144) * per_byte
    page = (wire_overhead + 1_048_576) * per_byte
    pages = math.ceil(100 / 32) + 1  # page_limit 32 at the defaults (MS2-D-32)
    hours = D(744) + D(2 * 3600) / 3600  # the 31-day lifetime is 744 h, below 744 h + 2 guard
    fetch = 5_000_000 + 20 * wire_overhead + 65536
    expected = {
        "compute": D(1024) / 1024 * 600 / 3600 * D("0.20"),
        "run_writes": 100 * ds_write + 3 * kv_write,
        "transfer": (fetch + 100 * MAX_LISTING_ROW_BYTES + 3 * 65536) * per_byte,
        "proxy": D(0),
        "post_run_reads": 3 * (100 + pages) * ds_read + 3 * kv_read,
        "post_run_deletes": 10 * (101 * ds_write + 3 * kv_write),
        "storage": (
            100 * MAX_LISTING_ROW_BYTES * D("0.001")
            + (65536 + MAX_API_REQUEST_BODY_BYTES) * D("0.001")
        )
        / GB
        * hours,
        "api_calls": (1 + 60 + 4 * 10 + 3) * call + 3 * pages * page,
    }
    assert dict(est.components) == expected
    execution = sum((expected[k] for k in ("compute", "run_writes", "transfer", "proxy")), D(0))
    post_run = sum(
        (expected[k] for k in ("post_run_reads", "post_run_deletes", "storage", "api_calls")), D(0)
    )
    assert est.execution_bound_usd >= execution > est.execution_bound_usd - TICK
    assert est.post_run_liability_usd >= post_run > est.post_run_liability_usd - TICK
    assert est.estimate_usd >= (execution + post_run) * D("1.10")
    assert est.monitoring_bound_usd >= 12 * call
    assert est.estimator_version == "1"


def test_bound_mode_settlement_of_recorded_parts_never_exceeds_the_estimate() -> None:
    # With a zero margin the reserved estimate must still cover the two
    # recorded parts, or a bound-mode settlement would trip the latch falsely.
    est = estimate_run_cost(SHAPE, cfg(margin=D(0)))
    assert est.execution_bound_usd + est.post_run_liability_usd <= est.estimate_usd


# ── Boundaries and remaining amounts (MS2-D-41 *Reserve*) ───────────────────


def test_exact_boundary_admitted_and_epsilon_over_denied() -> None:
    new = estimate_of(WATCH).admission_usd
    cap = project_allocation(CFG) - standing_account_read_debit(CFG)
    at_cap = ledger(current=CycleDebits(runtime_committed_usd=cap - new))
    assert decide(state=at_cap).admitted
    over = ledger(current=CycleDebits(runtime_committed_usd=cap - new + TICK))
    assert_denied(decide(state=over), R.CLASS_CAP)


def test_reservation_exactly_equal_to_remaining_is_admitted() -> None:
    # The account snapshot binds here (usage leaves less than A): the
    # reservation equals exactly what check 1 has left.
    assert decide(state=at_snapshot_edge()).admitted


def test_one_cent_over_remaining_is_denied() -> None:
    assert_denied(decide(state=at_snapshot_edge(over=CENT)), R.ACCOUNT_HEADROOM)


def test_cycle_bounds_come_from_account_limits_not_calendar() -> None:
    # 2 October is a new calendar month but still inside the anniversary cycle
    # that ends on 4 October, so admission proceeds on that cycle's figures.
    october = datetime(2026, 10, 2, tzinfo=UTC)
    fresh = snap(observed_at=october)
    assert decide(state=ledger(now=october, snapshot=fresh)).admitted
    # Past the observed cycle_end and before a new cycle is observed: unknown.
    after = datetime(2026, 10, 5, 0, 0, 1, tzinfo=UTC)
    stale_cycle = snap(observed_at=after)
    assert_denied(decide(state=ledger(now=after, snapshot=stale_cycle)), R.CYCLE_UNKNOWN)
    assert_denied(decide(state=ledger(snapshot=None)), R.CYCLE_UNKNOWN)
    assert_denied(decide(state=ledger(snapshot=snap(cycle_start=None))), R.CYCLE_UNKNOWN)


def test_account_headroom_below_project_target_binds() -> None:
    # Account usage of $11 (HR's $6.54 plus $4.46 of other workloads, inside
    # their $5 bound) leaves P - usage - HR_cycle about $0.10, while A = 11.00
    # still has $4.46 of room: the account's remaining allowance binds.
    state = ledger(
        snapshot=snap(account_usage_usd=D("11")),
        current=CycleDebits(runtime_committed_usd=D("6.00")),
    )
    assert_denied(decide(state=state), R.ACCOUNT_HEADROOM)
    assert project_allocation(CFG) == D("11.00")


def test_project_target_below_account_headroom_binds() -> None:
    # The account has ample prepaid room, but Hardware Radar's own runtime
    # debits already fill A: the class cap, not the account, refuses.
    full = ledger(current=CycleDebits(runtime_committed_usd=D("10.99")))
    assert_denied(decide(state=full), R.CLASS_CAP)
    assert decide(state=ledger(current=CycleDebits(runtime_committed_usd=D("5")))).admitted


def test_admission_never_relies_on_overage() -> None:
    # A limit raised above the prepaid credit adds no admissible headroom.
    for limit in (D("19"), D("100"), None):
        state = at_snapshot_edge(over=CENT, account_limit_usd=limit)
        assert_denied(decide(state=state), R.ACCOUNT_HEADROOM)
        assert decide(state=at_snapshot_edge(account_limit_usd=limit)).admitted


@pytest.mark.parametrize(
    "request_", EVERY_CLASS, ids=lambda r: str(r.operator_kind or r.budget_class)
)
def test_plan_base_price_above_cash_ceiling_denies_all(request_: AdmissionRequest) -> None:
    pricey = ledger(snapshot=snap(base_price_usd=D("20.01")))
    assert_denied(decide(request_, state=pricey), R.CASH_CEILING_EXCEEDED_BY_PLAN)
    assert decide(request_, state=ledger(snapshot=snap(base_price_usd=D("20.00")))).admitted


@pytest.mark.parametrize(
    "state",
    [
        ledger(snapshot=snap(observed_at=NOW - timedelta(seconds=901))),
        ledger(snapshot=snap(observed_at=None)),
        ledger(snapshot=snap(prepaid_credit_usd=None)),
        ledger(snapshot=snap(base_price_usd=None)),
        ledger(snapshot=snap(account_usage_usd=None)),
    ],
    ids=["stale", "never-observed", "no-credit", "no-base-price", "no-usage"],
)
def test_stale_or_unreadable_account_snapshot_denies(state: LedgerState) -> None:
    assert_denied(decide(state=state), R.ACCOUNT_STATE_UNOBSERVABLE)


def test_snapshot_exactly_at_max_age_is_fresh() -> None:
    assert decide(state=ledger(snapshot=snap(observed_at=NOW - timedelta(seconds=900)))).admitted


def test_target_setting_above_twelve_rejected() -> None:
    too_high = cfg(cycle_target_usd=D("12.01"))
    with pytest.raises(BudgetDenied) as caught:
        project_allocation(too_high)
    assert caught.value.reason is R.BUDGET_SETTING_INVALID
    for request_ in EVERY_CLASS:
        assert_denied(decide(request_, too_high), R.BUDGET_SETTING_INVALID)
    assert project_allocation(cfg(cycle_target_usd=D("12.00"))) == D("11.00")


def test_trailing_window_is_report_only() -> None:
    huge = CycleDebits(trailing_31d_usd=D("500"))
    assert decide(state=ledger(current=huge)).admitted
    assert trailing_window_warning(D("500"), CFG)
    assert not trailing_window_warning(D("11.00"), CFG)


# ── External liability (MS2-D-40, OQ26) ─────────────────────────────────────

_SETTINGS_PATH: Final = Path(__file__).resolve().parents[2] / "src" / "hw_radar" / "settings.py"


def _settings_with(monkeypatch: pytest.MonkeyPatch, **env: str) -> ModuleType:
    """Execute settings.py in a throwaway module (see test_settings._load_settings)."""
    for key in ("HW_RADAR_ENV", *env):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("hw_radar._budget_settings_probe", _SETTINGS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("raw", ["", "  ", "five", "-1", "inf", "NaN", "-0.01"])
def test_empty_or_invalid_external_liability_denies_all_paid_admission(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    parsed = _settings_with(
        monkeypatch, HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD=raw
    ).HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD
    assert parsed is None  # the 5.00 default is never substituted
    settings = cfg(external_liability_usd=parsed)
    for request_ in EVERY_CLASS:
        assert_denied(decide(request_, settings), R.EXTERNAL_LIABILITY_UNBOUNDED)


def test_default_external_liability_admits_only_when_invariant_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD", raising=False)
    parsed = _settings_with(monkeypatch).HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD
    assert parsed == D("5.00")
    settings = cfg(external_liability_usd=parsed)
    # Isolate check 2 (R34): with $19 credit and $1.90 margin the $12 target
    # binds first, so this fixture has $15 credit: P = 13.50, P - 5 = 8.50 < A.
    small = snap(prepaid_credit_usd=D("15"), account_usage_usd=D("0"))
    p = usable(settings, small)
    new = estimate_of(WATCH, settings).admission_usd
    standing = standing_account_read_debit(settings)
    committed = p - D("5.00") - new - standing  # HR_cycle + estimate + 5.00 = P
    assert committed + standing + new < project_allocation(settings)  # check 1 and A slack
    exact = ledger(snapshot=small, current=CycleDebits(runtime_committed_usd=committed))
    assert decide(settings=settings, state=exact).admitted
    over = ledger(snapshot=small, current=CycleDebits(runtime_committed_usd=committed + CENT))
    assert_denied(decide(settings=settings, state=over), R.ACCOUNT_HEADROOM)
    stale = ledger(
        snapshot=dataclasses.replace(small, observed_at=NOW - timedelta(hours=1)),
        current=CycleDebits(runtime_committed_usd=committed),
    )
    assert_denied(decide(settings=settings, state=stale), R.ACCOUNT_STATE_UNOBSERVABLE)


def test_external_liability_consumes_share_before_target() -> None:
    # $14 credit: P = 12.60, share P - 5 = 7.60, well below A = 11.00. Runtime
    # debits of 7.00 leave the target room but not the share.
    small = snap(prepaid_credit_usd=D("14"), account_usage_usd=D("0"))
    state = ledger(snapshot=small, current=CycleDebits(runtime_committed_usd=D("7.00")))
    assert_denied(decide(state=state), R.ACCOUNT_HEADROOM)
    assert decide(state=ledger(current=CycleDebits(runtime_committed_usd=D("7.00")))).admitted


def test_concurrent_external_consumption_within_declared_bound_cannot_push_account_past_prepaid() -> (
    None
):
    # Admit at the edge of check 2, then let other workloads consume their
    # whole declared bound before the run completes: the account's total
    # consumption still fits the prepaid credit.
    # $15 credit (P = 13.50) so check 2, not the $11 allocation, is the edge.
    small = snap(prepaid_credit_usd=D("15"), account_usage_usd=D("0"))
    p = usable(CFG, small)
    new = estimate_of(WATCH).admission_usd
    standing = standing_account_read_debit(CFG)
    external = D("5.00")
    committed = p - external - new - standing
    state = ledger(snapshot=small, current=CycleDebits(runtime_committed_usd=committed))
    assert decide(state=state).admitted
    over = ledger(snapshot=small, current=CycleDebits(runtime_committed_usd=committed + TICK))
    assert_denied(decide(state=over), R.ACCOUNT_HEADROOM)
    hr_worst_case = committed + standing + new
    assert hr_worst_case + external <= p <= D("15")


def test_observed_external_consumption_above_bound_denies_and_trips_latch() -> None:
    # usage - HR_cycle is a lower bound on other workloads' consumption.
    hr = standing_account_read_debit(CFG) + D("1.00")
    usage = hr + D("5.00") + TICK
    state = ledger(
        snapshot=snap(account_usage_usd=usage), current=CycleDebits(runtime_committed_usd=D("1.00"))
    )
    decision = decide(state=state)
    assert_denied(decision, R.EXTERNAL_LIABILITY_EXCEEDED)
    assert decision.trip_latch
    at_bound = ledger(
        snapshot=snap(account_usage_usd=hr + D("5.00")),
        current=CycleDebits(runtime_committed_usd=D("1.00")),
    )
    assert decide(state=at_bound).admitted


def test_straddling_reservation_checked_against_both_cycles_external_bound() -> None:
    new = estimate_of(WATCH).admission_usd
    standing = standing_account_read_debit(CFG)
    room = usable() - D("5.00") - new - standing
    fits_next = CycleDebits(runtime_committed_usd=room)
    over_next = CycleDebits(runtime_committed_usd=room, carried_handoff_usd=CENT)
    base = snap(account_usage_usd=D("0"))
    assert decide(state=ledger(snapshot=base, next_cycle=fits_next)).admitted
    assert_denied(decide(state=ledger(snapshot=base, next_cycle=over_next)), R.ACCOUNT_HEADROOM)


# ── Operator class (MS2-D-46) ───────────────────────────────────────────────


def test_operator_reservation_counts_against_operator_class_and_account_checks() -> None:
    # Runtime debits never consume the operator allowance ...
    busy_runtime = ledger(current=CycleDebits(runtime_committed_usd=D("11.00")))
    assert decide(BUILD, state=busy_runtime).admitted
    # ... but an operator row still passes both account checks.
    assert_denied(decide(BUILD, state=at_snapshot_edge(BUILD, over=CENT)), R.ACCOUNT_HEADROOM)
    assert decide(BUILD, state=at_snapshot_edge(BUILD)).admitted
    # And operator debits count in HR_cycle for a runtime request's check 2.
    small = snap(prepaid_credit_usd=D("14"), account_usage_usd=D("0"))
    room = (
        usable(CFG, small)
        - D("5.00")
        - standing_account_read_debit(CFG)
        - estimate_of(WATCH).admission_usd
    )
    ok = ledger(snapshot=small, current=CycleDebits(runtime_committed_usd=room - D("0.50")))
    assert decide(state=ok).admitted
    with_operator = ledger(
        snapshot=small,
        current=CycleDebits(
            runtime_committed_usd=room - D("0.50"), operator_committed_usd=D("0.51")
        ),
    )
    assert_denied(decide(state=with_operator), R.ACCOUNT_HEADROOM)


def test_operator_allowance_exhausted_refuses_reservation() -> None:
    new = estimate_of(INSPECT).admission_usd
    exact = ledger(current=CycleDebits(operator_committed_usd=D("1.00") - new))
    assert decide(INSPECT, state=exact).admitted
    over = ledger(current=CycleDebits(operator_committed_usd=D("1.00") - new + TICK))
    assert_denied(decide(INSPECT, state=over), R.OPERATOR_ALLOWANCE_EXHAUSTED)


def test_default_operator_allowance_fits_two_build_bounds_not_three() -> None:
    assert project_allocation(CFG) == D("11.00")
    build = estimate_of(BUILD).admission_usd
    call = api_call_bound(CFG)
    assert build >= D("0.41") + 72 * call  # (MAX_RUN_POLLS + MAX_CORRECTION_READS) x call
    assert D("0.42") < build < D("0.43")
    committed = D(0)
    for _ in range(2):
        assert decide(
            BUILD, state=ledger(current=CycleDebits(operator_committed_usd=committed))
        ).admitted
        committed += build
    third = decide(BUILD, state=ledger(current=CycleDebits(operator_committed_usd=committed)))
    assert_denied(third, R.OPERATOR_ALLOWANCE_EXHAUSTED)


def test_build_holds_its_correction_reads_as_monitoring_allowance() -> None:
    build = estimate_of(BUILD)
    call = api_call_bound(CFG)
    assert build.estimate_usd >= D("0.41") + 60 * call
    assert build.monitoring_bound_usd >= 12 * call
    assert build.post_run_liability_usd == 0


def test_inspection_envelope_priced_from_unit_price_settings() -> None:
    call = api_call_bound(CFG)
    envelope = 1000 * D("0.0004") / 1000 + 20 * call + 10_000_000 * D("0.20") / GB
    est = estimate_of(INSPECT)
    assert est.estimate_usd >= envelope * D("1.10") > est.estimate_usd - 3 * TICK
    assert est.monitoring_bound_usd == 0
    dearer = estimate_of(INSPECT, cfg(prices=prices(transfer_per_gb=D("0.40"))))
    assert dearer.estimate_usd > est.estimate_usd


@pytest.mark.parametrize(
    "price", ["dataset_reads_per_1000", "kv_writes_per_1000", "transfer_per_gb"]
)
def test_inspection_envelope_denied_when_a_price_is_missing(price: str) -> None:
    settings = cfg(prices=prices(**{price: None}))
    assert_denied(decide(INSPECT, settings), R.PRICING_UNVERIFIED)


def test_inspection_settles_at_full_envelope_never_below() -> None:
    est = estimate_of(INSPECT)
    assert settle_envelope(est) == est.estimate_usd
    assert settle_envelope(est, [D("0.0001"), D(0)]) == est.estimate_usd
    assert settle_envelope(est, [est.estimate_usd + 1]) == est.estimate_usd + 1


def test_probe_envelope_priced_and_denied_when_allowance_short() -> None:
    call = api_call_bound(CFG)
    ds_write = D("0.005") / 1000
    envelope = 10 * (call + ds_write) + MAX_LISTING_ROW_BYTES * D("0.001") / GB * storage_hours(CFG)
    est = estimate_of(PROBE)
    assert est.estimate_usd >= envelope * D("1.10") > est.estimate_usd - 3 * TICK
    short = ledger(current=CycleDebits(operator_committed_usd=D("1.00") - est.admission_usd + TICK))
    assert_denied(decide(PROBE, state=short), R.OPERATOR_ALLOWANCE_EXHAUSTED)
    assert_denied(decide(PROBE, cfg(storage_max_lifetime_s=None)), R.UNBOUNDED_COMPONENT)


# ── Unit prices and component bounds ────────────────────────────────────────


@pytest.mark.parametrize("price", [f.name for f in dataclasses.fields(UnitPrices)])
def test_missing_unit_price_denies_live_admission(price: str) -> None:
    for request_ in (WATCH, DISCOVERY):
        decision = decide(request_, cfg(prices=prices(**{price: None})))
        assert_denied(decision, R.PRICING_UNVERIFIED)


def test_negative_unit_price_is_unverified() -> None:
    assert_denied(decide(settings=cfg(prices=prices(usd_per_cu=D("-0.2")))), R.PRICING_UNVERIFIED)


def test_estimate_includes_capped_reads_deletes_and_storage_lifetime() -> None:
    def reads(n: int) -> Decimal:
        return estimate_run_cost(SHAPE, cfg(max_dataset_reads=n)).components["post_run_reads"]

    kv_part = 3 * D("0.005") / 1000
    per_read = reads(1) - kv_part
    assert reads(0) == kv_part
    assert reads(3) - kv_part == 3 * per_read  # linear in MAX_DATASET_READS
    base = estimate_run_cost(SHAPE, CFG).components
    assert base["post_run_deletes"] > 0
    # Storage is priced over storage_hours, not over the 24 h cleanup deadline.
    long_life = estimate_run_cost(SHAPE, cfg(storage_max_lifetime_s=62 * 86400)).components
    assert long_life["storage"] == base["storage"] * (D(62 * 24) / storage_hours(CFG))


def test_unset_storage_lifetime_denies_live_admission() -> None:
    for request_ in (WATCH, DISCOVERY, PROBE):
        assert_denied(decide(request_, cfg(storage_max_lifetime_s=None)), R.UNBOUNDED_COMPONENT)


def test_transfer_priced_on_every_byte_at_higher_direction_price() -> None:
    # One setting holds max(external, internal); every byte pays it, in
    # decimal GB (10^9), so doubling it doubles the transfer component.
    base = estimate_run_cost(SHAPE, CFG).components["transfer"]
    doubled = estimate_run_cost(SHAPE, cfg(prices=prices(transfer_per_gb=D("0.40")))).components
    assert doubled["transfer"] == 2 * base
    fetch = actor_fetch_bytes(CFG, max_bytes=SHAPE.max_bytes, max_requests=SHAPE.max_requests)
    all_bytes = fetch + SHAPE.max_items * MAX_LISTING_ROW_BYTES + 3 * 65536
    assert base == all_bytes * D("0.20") / D(10**9)


def test_api_calls_priced_at_bound_without_billing_verification() -> None:
    # No poll-billing or transfer-direction fact exists anywhere in the inputs:
    # with prices set and every cap valid, admission is granted.
    decision = decide()
    assert decision.admitted
    assert decision.estimate is not None
    assert decision.estimate.components["api_calls"] > 0


@pytest.mark.parametrize(
    "cap",
    [
        "max_run_polls",
        "max_correction_reads",
        "max_account_reads_per_cycle",
        "api_call_overhead_bytes",
        "max_api_response_bytes",
        "max_dataset_page_bytes",
        "max_delete_attempts",
        "max_dataset_reads",
        "max_kv_reads",
        "max_kv_writes",
        "max_kv_bytes",
        "max_timeout_s",
    ],
)
def test_unset_or_invalid_cap_denies_with_unbounded_component(cap: str) -> None:
    assert_denied(decide(settings=cfg(**{cap: None})), R.UNBOUNDED_COMPONENT)


def test_unset_margin_denies_with_unbounded_component() -> None:
    assert_denied(decide(settings=cfg(margin=None)), R.UNBOUNDED_COMPONENT)
    assert_denied(decide(INSPECT, cfg(margin=D("NaN"))), R.UNBOUNDED_COMPONENT)


def test_account_read_bound_is_standing_cycle_debit() -> None:
    standing = standing_account_read_debit(CFG)
    assert standing >= 3000 * api_call_bound(CFG)
    assert D("0.54") <= standing <= D("0.55")  # the plan's ~$0.54 at the defaults
    new = estimate_of(WATCH).admission_usd
    # An empty ledger still carries the standing debit against A.
    fits = ledger(current=CycleDebits(runtime_committed_usd=D("11.00") - standing - new))
    assert decide(state=fits).admitted
    over = ledger(current=CycleDebits(runtime_committed_usd=D("11.00") - standing - new + TICK))
    assert_denied(decide(state=over), R.CLASS_CAP)


def test_storage_lifetime_below_data_retention_days_denies() -> None:
    # R38 accepted (the fixture's default), so only the lifetime decides.
    assert CFG.call_billing_residual_accepted is not None
    short = cfg(storage_max_lifetime_s=31 * 86400 - 1)
    for request_ in (WATCH, BUILD):
        assert_denied(decide(request_, short), R.UNBOUNDED_COMPONENT)
    assert decide(settings=cfg(storage_max_lifetime_s=31 * 86400)).admitted


def test_missing_data_retention_days_denies() -> None:
    state = ledger(snapshot=snap(data_retention_days=None))
    assert_denied(decide(state=state), R.UNBOUNDED_COMPONENT)


# ── Revision 11 (R10-01, -02, -06, -07, -08) ────────────────────────────────


def test_per_call_bounds_derived_from_settings_by_endpoint() -> None:
    # Non-default settings everywhere, so a hard-coded figure cannot pass.
    settings = cfg(
        prices=prices(dataset_reads_per_1000=D("0.9"), transfer_per_gb=D("0.37")),
        api_call_overhead_bytes=100_000,
        max_api_response_bytes=50_000,
        max_dataset_page_bytes=700_000,
    )
    per_byte = D("0.37") / GB
    wire_overhead = 100_000 + 2 * HTTP_RECEIVE_BUFFER_BYTES
    assert request_wire_overhead(settings) == wire_overhead
    call = D("0.9") / 1000 + (wire_overhead + 50_000) * per_byte  # dataset read now dearest
    page = (wire_overhead + 700_000) * per_byte
    bounds = per_call_bounds(settings)
    assert bounds.dataset_page == page == dataset_page_bound(settings)
    for endpoint in (
        "start_run",
        "run_poll",
        "correction_read",
        "abort",
        "abort_confirm",
        "delete_dataset",
        "delete_kv_store",
        "kv_record_read",
        "build_read",
        "account_read",
    ):
        assert getattr(bounds, endpoint) == call, endpoint
    assert api_call_bound(settings) == call


@pytest.mark.parametrize("raw", ["", "yesterday", "2026-13-01"])
def test_admission_denied_until_call_billing_residual_accepted(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    parsed = _settings_with(
        monkeypatch, HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED=raw
    ).HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED
    assert parsed is None
    for request_ in EVERY_CLASS:
        decision = decide(request_, cfg(call_billing_residual_accepted=parsed))
        assert_denied(decision, R.CALL_BILLING_RESIDUAL_UNACCEPTED)
    for request_ in EVERY_CLASS:
        assert decide(request_, cfg(call_billing_residual_accepted=date(2026, 9, 25))).admitted


def test_transfer_bound_covers_actor_overshoot_of_one_read_and_in_flight_window() -> None:
    # The D1 follow-up worst case for maxBytes = B, maxRequests = N: B - 1
    # counted bytes, then one final full read that crosses the cap, then a full
    # doubled receive window in flight at the stop, plus every other request
    # being a non-200 response abandoned with its own full window in flight.
    window = 2 * HTTP_RECEIVE_BUFFER_BYTES
    for max_bytes, max_requests in ((10, 1), (5_000_000, 20), (1, 200)):
        worst = (max_bytes - 1) + HTTP_READ_CHUNK_BYTES + window + max_requests * window
        covered = actor_fetch_bytes(CFG, max_bytes=max_bytes, max_requests=max_requests)
        assert worst <= covered
        shape = dataclasses.replace(SHAPE, max_bytes=max_bytes, max_requests=max_requests)
        transfer = estimate_run_cost(shape, CFG).components["transfer"]
        assert transfer >= worst * D("0.20") / GB


def test_estimate_prices_four_calls_per_cleanup_attempt_and_the_start_call() -> None:
    call = api_call_bound(CFG)

    def api_calls(**changes: Any) -> Decimal:
        return estimate_run_cost(SHAPE, cfg(**changes)).components["api_calls"]

    assert api_calls(max_delete_attempts=11) - api_calls(max_delete_attempts=10) == 4 * call
    pages = pages_per_read(CFG, SHAPE.max_items, MAX_LISTING_ROW_BYTES)
    no_caps = api_calls(max_delete_attempts=0, max_run_polls=0, max_kv_reads=0, max_dataset_reads=0)
    assert no_caps == call  # the start call alone
    assert pages == math.ceil(SHAPE.max_items / page_limit(1_048_576, MAX_LISTING_ROW_BYTES)) + 1


def test_storage_hours_cover_a_full_cycle_when_lifetime_is_shorter() -> None:
    assert storage_hours(cfg(storage_max_lifetime_s=86400)) == D(744) + 2
    assert storage_hours(cfg(storage_max_lifetime_s=40 * 86400)) == D(40 * 24)
    assert storage_hours(cfg(cycle_boundary_guard_s=0, storage_max_lifetime_s=3600)) == D(744)


def test_observed_cycle_longer_than_744_hours_denies() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    long_cycle = snap(cycle_start=start, cycle_end=start + timedelta(hours=744, seconds=1))
    assert_denied(decide(state=ledger(snapshot=long_cycle)), R.UNBOUNDED_COMPONENT)
    exact = snap(cycle_start=start, cycle_end=start + timedelta(hours=744))
    assert decide(state=ledger(snapshot=exact)).admitted


def test_page_limit_below_one_or_kv_cap_above_response_cap_denies() -> None:
    tiny_page = cfg(max_dataset_page_bytes=MAX_LISTING_ROW_BYTES)  # envelope leaves < 1 row
    assert_denied(decide(settings=tiny_page), R.UNBOUNDED_COMPONENT)
    big_kv = cfg(max_kv_bytes=262145)
    assert_denied(decide(settings=big_kv), R.UNBOUNDED_COMPONENT)
    assert decide(settings=cfg(max_kv_bytes=262144)).admitted


# ── Classes, outstanding work, kill switch, latch, invalid inputs ───────────


def test_discovery_denied_above_reserve_while_watch_refresh_admitted() -> None:
    # Runtime debits leave room under A but not under A - 3.00.
    state = ledger(current=CycleDebits(runtime_committed_usd=D("8.00")))
    assert decide(WATCH, state=state).admitted
    assert_denied(decide(DISCOVERY, state=state), R.CLASS_CAP)
    assert decide(
        DISCOVERY, state=ledger(current=CycleDebits(runtime_committed_usd=D("7")))
    ).admitted


def test_outstanding_reservations_are_counted() -> None:
    # Carried handoff consumption and discovery allowances count like rows.
    for debits in (
        CycleDebits(runtime_committed_usd=D("10.99")),
        CycleDebits(carried_handoff_usd=D("10.99")),
        CycleDebits(discovery_allowance_usd=D("10.99")),
    ):
        assert_denied(decide(state=ledger(current=debits)), R.CLASS_CAP)


def test_inclusion_watermark_reduces_only_the_snapshot_check() -> None:
    state = at_snapshot_edge(over=CENT)
    assert_denied(decide(state=state), R.ACCOUNT_HEADROOM)
    # Spend the snapshot is known to contain is not debited twice by check 1.
    included = dataclasses.replace(state.current, hr_included_usd=D("1.00"))
    assert decide(state=dataclasses.replace(state, current=included)).admitted


@pytest.mark.parametrize(
    "request_", EVERY_CLASS, ids=lambda r: str(r.operator_kind or r.budget_class)
)
def test_kill_switch_denies_every_class(request_: AdmissionRequest) -> None:
    assert_denied(decide(request_, cfg(enabled=False)), R.APIFY_DISABLED)


@pytest.mark.parametrize(
    "request_", EVERY_CLASS, ids=lambda r: str(r.operator_kind or r.budget_class)
)
def test_tripped_latch_denies_everything(request_: AdmissionRequest) -> None:
    assert_denied(decide(request_, state=ledger(latch_tripped=True)), R.OVERRUN_LATCH)


def test_missing_ledger_authority_denies() -> None:
    for request_ in EVERY_CLASS:
        decision = decide(request_, state=ledger(authority_held=False))
        assert_denied(decision, R.LEDGER_AUTHORITY_MISSING)
        assert decision.estimate is not None  # recorded on the denial row


@pytest.mark.parametrize(
    "changes",
    [
        {"cycle_target_usd": None},
        {"operator_allowance_usd": D("12.01")},
        {"watch_refresh_reserve_usd": D("11.01")},
        {"watch_refresh_reserve_usd": None},
        {"cash_ceiling_usd": None},
        {"account_margin_usd": D("NaN")},
        {"account_snapshot_max_age_s": None},
    ],
    ids=lambda c: next(iter(c)),
)
def test_invalid_allocation_setting_denies(changes: dict[str, Any]) -> None:
    assert_denied(decide(settings=cfg(**changes)), R.BUDGET_SETTING_INVALID)


def test_configured_account_margin_replaces_the_ten_percent_default() -> None:
    assert budget.account_margin_usd(CFG, D("19")) == D("1.9")
    assert budget.account_margin_usd(cfg(account_margin_usd=D("0")), D("19")) == 0


@pytest.mark.parametrize(
    "shape",
    [
        {"memory_mb": 0},
        {"timeout_s": -1},
        {"max_items": -1},
        {"max_requests": -1},
        {"max_bytes": -1},
        {"max_item_bytes": 0},
    ],
    ids=lambda c: next(iter(c)),
)
def test_invalid_run_shape_is_a_caller_error(shape: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must"):
        dataclasses.replace(SHAPE, **shape)


def test_mismatched_request_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="operator"):
        AdmissionRequest(budget_class=BudgetClass.OPERATOR, run=SHAPE)
    with pytest.raises(ValueError, match="operator"):
        AdmissionRequest(budget_class=BudgetClass.WATCH_REFRESH, operator_kind=OperatorKind.BUILD)
    with pytest.raises(ValueError, match="operator"):
        AdmissionRequest(budget_class=BudgetClass.DISCOVERY)


def test_timeout_above_max_timeout_denies() -> None:
    long_run = AdmissionRequest(
        budget_class=BudgetClass.WATCH_REFRESH, run=dataclasses.replace(SHAPE, timeout_s=3601)
    )
    assert_denied(decide(long_run), R.UNBOUNDED_COMPONENT)


def test_build_bound_unset_denies() -> None:
    assert_denied(decide(BUILD, cfg(operator_build_bound_usd=None)), R.UNBOUNDED_COMPONENT)


def test_class_values_match_the_catalog_admission_class() -> None:
    from hw_radar.catalog.models.provider import AdmissionClass

    assert {c.value for c in BudgetClass} == {c.value for c in AdmissionClass}


def test_load_budget_settings_reflects_django_settings() -> None:
    loaded = load_budget_settings()
    # The test environment sets no prices: live admission stays denied.
    assert loaded.prices.usd_per_cu is None
    assert loaded.external_liability_usd == D("5.00")
    assert loaded.cycle_target_usd == D("12.00")
    assert loaded.operator_allowance_usd == D("1.00")
    assert loaded.call_billing_residual_accepted == date(2026, 9, 25)
    assert loaded.enabled is False
    assert_denied(decide(settings=dataclasses.replace(loaded, enabled=True)), R.PRICING_UNVERIFIED)


# ── Configured billing cycle (MS2-D-48 *Cycle*, E9.1) ───────────────────────


def _utc(year: int, month: int, day: int, *rest: int) -> datetime:
    return datetime(year, month, day, *rest, tzinfo=UTC)


def _ms_before(value: datetime) -> datetime:
    return value - timedelta(milliseconds=1)


@pytest.mark.parametrize(
    ("anchor", "now", "expected"),
    [
        # The observed cycle of 2026-09-25 (MS2-D-48 *Evidence*).
        pytest.param(
            _utc(2026, 9, 5),
            _utc(2026, 9, 25),
            (_utc(2026, 9, 5), _utc(2026, 10, 4, 23, 59, 59, 999000)),
            id="observed-cycle",
        ),
        pytest.param(
            _utc(2026, 9, 5),
            _utc(2026, 10, 5),
            (_utc(2026, 10, 5), _ms_before(_utc(2026, 11, 5))),
            id="now-at-next-start-is-next-cycle",
        ),
        pytest.param(
            _utc(2026, 9, 5),
            _utc(2026, 10, 4, 23, 59, 59, 999000),
            (_utc(2026, 9, 5), _utc(2026, 10, 4, 23, 59, 59, 999000)),
            id="now-at-cycle-end-is-current-cycle",
        ),
        pytest.param(
            _utc(2027, 1, 28),
            _utc(2027, 2, 10),
            (_utc(2027, 1, 28), _ms_before(_utc(2027, 2, 28))),
            id="day-28-jan-to-feb",
        ),
        pytest.param(
            _utc(2027, 1, 28),
            _utc(2027, 3, 1),
            (_utc(2027, 2, 28), _ms_before(_utc(2027, 3, 28))),
            id="day-28-feb-to-mar-2027",
        ),
        pytest.param(
            _utc(2028, 1, 28),
            _utc(2028, 2, 29),
            (_utc(2028, 2, 28), _ms_before(_utc(2028, 3, 28))),
            id="day-28-feb-to-mar-leap-2028",
        ),
        pytest.param(
            _utc(2026, 9, 5),
            _utc(2027, 1, 2),
            (_utc(2026, 12, 5), _ms_before(_utc(2027, 1, 5))),
            id="dec-to-jan-across-a-year",
        ),
    ],
)
def test_billing_cycle_bounds_from_anchor(
    anchor: datetime, now: datetime, expected: tuple[datetime, datetime]
) -> None:
    assert budget.billing_cycle_bounds(anchor, now) == expected


def test_billing_cycle_bounds_are_computed_from_the_anchor_not_iterated() -> None:
    # 61 months on, the start is still exactly day 28: a derivation that stepped
    # by a fixed duration, or carried any per-step error forward, would have
    # drifted off the anchor day long before.
    anchor = _utc(2026, 1, 28)
    bounds = budget.billing_cycle_bounds(anchor, _utc(2031, 2, 28, 12))
    assert bounds == (_utc(2031, 2, 28), _ms_before(_utc(2031, 3, 28)))


def test_billing_cycle_before_anchor_is_unknown() -> None:
    anchor = _utc(2026, 9, 5)
    assert budget.billing_cycle_bounds(anchor, _ms_before(anchor)) is None


@pytest.mark.parametrize("day", [1, 28])
def test_every_derived_cycle_is_at_most_744_hours(day: int) -> None:
    anchor = _utc(2026, 1, day)
    now = anchor
    for _ in range(48):
        bounds = budget.billing_cycle_bounds(anchor, now)
        assert bounds is not None
        start, end = bounds
        assert start.day == day
        assert end - start <= timedelta(hours=budget.FULL_CYCLE_HOURS)
        now = end + timedelta(milliseconds=1)
