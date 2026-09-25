"""Plan E3: the ledger service's cycle predicate, reserve(), and cycle discovery.

MS2-D-34 (a row counts in every billing cycle its charge interval touches; an
unreconciled row in every cycle from its admission onward), MS2-D-40 (the
billing cycle is the period; reconciled spend stays in the snapshot check),
MS2-D-32 *Cycle discovery*, and MS2-D-39 (charges are placed by the billing
clock, never by observation time). Figures come from ledger_support.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from ledger_support import (
    ALLOCATION,
    C1_END,
    C1_START,
    C2_END,
    C2_START,
    C3_END,
    C3_START,
    CONFIG,
    DAY,
    GUARD,
    HOUR,
    NOW,
    RUN_DEBIT,
    RUN_ESTIMATE,
    STANDING,
    WATCH,
    account_client,
    claim,
    config,
    cycle,
    open_row,
    provider_run,
    reconcile,
    reserved,
    site,
)

from hw_radar.acquisition.apify.budget import DenialReason
from hw_radar.acquisition.apify.ledger import (
    DISCOVERY_EXHAUSTED,
    RefreshOutcome,
    ReservationOutcome,
    current_cycle,
    cycle_debits,
    discovery_status,
    refresh_account_snapshot,
    reserve,
)
from hw_radar.catalog.models import (
    ApifyBudgetCycle,
    ApifyCycleDiscovery,
    ApifyLedgerAuthority,
    ApifySpendReservation,
    ReservationStatus,
)

D = Decimal
TICK = D("0.0001")


def _reserve(at: object = NOW, cfg: object = CONFIG) -> ReservationOutcome:
    return reserve(WATCH, source_site_id=site().pk, config=cfg, now=at)  # type: ignore[arg-type]


def _runtime(at_cycle: ApifyBudgetCycle) -> Decimal:
    return cycle_debits(at_cycle, CONFIG)[0].runtime_committed_usd


def _fill_to(room: Decimal, at: object) -> ApifySpendReservation:
    """Seed an open row leaving exactly `room` of the watch_refresh cap."""
    return open_row(ALLOCATION - STANDING - room, at)  # type: ignore[arg-type]


# ── reserve() basics ──


@pytest.mark.django_db
def test_admitted_reservation_persists_estimate_and_denial_persists_reason() -> None:
    cycle()
    claim()
    admitted = _reserve()
    row = reserved(admitted.reservation_id)
    assert admitted.admitted
    assert (row.status, row.estimate_usd, row.monitoring_bound_usd) == (
        ReservationStatus.RESERVED,
        RUN_ESTIMATE.estimate_usd,
        RUN_ESTIMATE.monitoring_bound_usd,
    )
    assert row.provider_run is None and row.reserved_at == NOW
    assert set(row.component_bounds) == set(RUN_ESTIMATE.components)

    denied = _reserve(cfg=config(budget={"enabled": False}))
    row = reserved(denied.reservation_id)
    assert (row.status, row.denial_reason) == (ReservationStatus.DENIED, "apify_disabled")


@pytest.mark.django_db
def test_denial_with_estimate_records_components() -> None:
    cycle()
    claim()
    _fill_to(RUN_DEBIT - TICK, NOW - HOUR)
    outcome = _reserve()
    row = reserved(outcome.reservation_id)
    assert outcome.reason == DenialReason.CLASS_CAP
    assert row.estimate_usd == RUN_ESTIMATE.estimate_usd
    assert set(row.component_bounds) == set(RUN_ESTIMATE.components)


@pytest.mark.django_db
def test_denied_rows_count_nothing() -> None:
    c1 = cycle()
    claim()
    _fill_to(RUN_DEBIT - TICK, NOW - HOUR)
    before = _runtime(c1)
    assert not _reserve().admitted
    assert _runtime(c1) == before


@pytest.mark.django_db
def test_unknown_cycle_denies() -> None:
    assert _reserve().reason == DenialReason.CYCLE_UNKNOWN


@pytest.mark.django_db
def test_external_liability_exceeded_trips_latch() -> None:
    cycle(usage=D("5.60"))  # other workloads: 5.60 - standing 0.54 > 5.00
    claim()
    outcome = _reserve()
    assert outcome.reason == DenialReason.EXTERNAL_LIABILITY_EXCEEDED
    assert _reserve().reason == DenialReason.OVERRUN_LATCH


# ── Concurrency (MS2-D-41 *Reserve*) ──


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_reservations_one_admitted_at_boundary() -> None:
    cycle()
    claim()
    # Room for one run and a half: each thread alone fits, both together don't.
    _fill_to(RUN_DEBIT + RUN_DEBIT / 2, NOW - HOUR)
    site_id = site().pk
    barrier = threading.Barrier(2)

    def contender() -> ReservationOutcome:
        try:
            barrier.wait(timeout=10)
            return reserve(WATCH, source_site_id=site_id, config=CONFIG, now=NOW)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [f.result() for f in [pool.submit(contender), pool.submit(contender)]]
    assert sorted(o.admitted for o in outcomes) == [False, True]
    assert {o.reason for o in outcomes if not o.admitted} == {DenialReason.CLASS_CAP}
    assert ApifySpendReservation.objects.filter(status=ReservationStatus.RESERVED).count() == 2


# ── The cycle predicate (MS2-D-34) ──


@pytest.mark.django_db
def test_reservation_straddling_cycle_boundary_counts_in_both_cycles() -> None:
    c1, c2 = cycle(), cycle(C2_START, C2_END)
    # Settled within one guard of the edge: its charges may be in either cycle.
    reconcile(
        open_row(D("0.30"), C1_END - 2 * HOUR), actual=D("0.25"), last_charge_at=C1_END - GUARD / 2
    )
    # Unreconciled across the edge: full estimate in both.
    open_row(D("0.40"), C1_END - 2 * DAY)
    for at_cycle in (c1, c2):
        assert _runtime(at_cycle) == D("0.25") + D("0.40")


@pytest.mark.django_db
def test_arbitrary_non_calendar_cycle_boundary() -> None:
    """A 5th-to-4th cycle places spend by its bounds, never by calendar month.

    The plan's second case, a cycle shortened by a plan change, needs the
    async snapshot refresh (transactional DB) and lives in
    test_cycle_shortened_by_plan_change_moves_the_boundary.
    """
    c1, c2 = cycle(), cycle(C2_START, C2_END)
    # 3 October is calendar October but billing cycle 1 (5 Sep -> 4 Oct).
    october_3 = C1_END - timedelta(days=1, hours=12)
    reconcile(open_row(D("0.30"), october_3), actual=D("0.20"), last_charge_at=october_3 + HOUR)
    assert (_runtime(c1), _runtime(c2)) == (D("0.20"), D(0))


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_cycle_shortened_by_plan_change_moves_the_boundary() -> None:
    cycle(observed_at=None)
    claim()
    shortened_end = C1_START + 15 * DAY  # a plan change ends cycle 1 early
    at = shortened_end - DAY
    outcome = asyncio.run(
        refresh_account_snapshot(account_client(cycle_end=shortened_end), config=CONFIG, now=at)
    )
    assert outcome == RefreshOutcome.REFRESHED
    assert ApifyBudgetCycle.objects.get(cycle_start=C1_START).cycle_end == shortened_end
    reconcile(open_row(D("0.20"), at), actual=D("0.20"), last_charge_at=at + HOUR)
    late = reconcile(
        open_row(D("0.30"), at), actual=D("0.30"), last_charge_at=shortened_end - GUARD / 2
    )
    # After the shortened end no row covers now until the next cycle is found.
    after = shortened_end + HOUR
    assert current_cycle(after) is None
    assert _reserve(after).reason == DenialReason.CYCLE_UNKNOWN
    new_start = shortened_end + timedelta(milliseconds=1)
    asyncio.run(
        refresh_account_snapshot(
            account_client(cycle_start=new_start, cycle_end=new_start + 30 * DAY),
            config=CONFIG,
            now=after,
        )
    )
    new_cycle = current_cycle(after)
    assert new_cycle is not None and new_cycle.cycle_start == new_start
    old = ApifyBudgetCycle.objects.get(cycle_start=C1_START)
    assert _runtime(old) == D("0.50")
    assert _runtime(new_cycle) == late.actual_usd  # only the row within a guard
    # Authority continues across the plan-change boundary (adjacent cycles).
    assert ApifyLedgerAuthority.objects.filter(cycle_start=new_start).exists()


@pytest.mark.django_db
def test_cycle_rollover_carries_unsettled_reservations() -> None:
    c1, c2, c3 = cycle(), cycle(C2_START, C2_END), cycle(C3_START, C3_END)
    open_row(D("0.40"), NOW)
    open_row(
        D("0.10"), NOW, status=ReservationStatus.USAGE_PROVISIONAL, run=provider_run(site(), NOW)
    )
    assert [_runtime(c) for c in (c1, c2, c3)] == [D("0.50")] * 3


@pytest.mark.django_db
def test_reconciled_spend_leaves_a_cycle_its_charge_interval_does_not_touch() -> None:
    c1, c2 = cycle(), cycle(C2_START, C2_END)
    reconcile(
        open_row(D("0.40"), NOW),
        actual=D("0.30"),
        last_charge_at=NOW + DAY,
        closing_read_at=NOW + 8 * DAY,
    )
    assert (_runtime(c1), _runtime(c2)) == (D("0.30"), D(0))


@pytest.mark.django_db
def test_late_cleanup_settled_spend_counts_in_the_cycle_of_its_final_charge() -> None:
    c1, c2 = cycle(), cycle(C2_START, C2_END, observed_at=C2_START + 3 * DAY)
    claim()
    late = open_row(D("0.40"), C1_END - 2 * DAY)  # missed its cleanup deadline
    reconcile(
        late,
        actual=D("0.35"),
        last_charge_at=C2_START + 3 * DAY,
        closing_read_at=C2_START + 11 * DAY,
    )
    assert _runtime(c1) == _runtime(c2) == D("0.35")
    # In cycle 2 (authority continued), a run that fits only without it is denied.
    at = C2_START + 3 * DAY
    _fill_to(RUN_DEBIT + D("0.35") - TICK, at)
    outcome = _reserve(at)
    assert outcome.reason == DenialReason.CLASS_CAP
    assert ApifyLedgerAuthority.objects.get(cycle_start=C2_START).kind == "continued"


@pytest.mark.django_db
def test_cycle_attribution_uses_charge_interval_not_observation_time() -> None:
    c1, c2 = cycle(), cycle(C2_START, C2_END)
    # Observed (the run's startedAt, MS2-D-39) and finished mid-cycle 1, but
    # its import and deletion (final_charge_op_at) happened in cycle 2.
    started = C1_START + 5 * DAY
    run = provider_run(
        site(),
        started,
        started_at=started,
        finished_at=started + HOUR,
        final_charge_op_at=C2_START + 2 * DAY,
    )
    row = open_row(D("0.40"), started)
    reconcile(row, actual=D("0.30"), last_charge_at=C2_START + 2 * DAY, run=run)
    assert _runtime(c2) == D("0.30")
    # And the converse: its observation time never puts it in a later cycle.
    early = open_row(D("0.40"), started)
    run2 = provider_run(site("e3other"), started, started_at=C2_START + DAY)
    reconcile(early, actual=D("0.10"), last_charge_at=started + DAY, run=run2)
    assert _runtime(c2) == D("0.30")
    assert _runtime(c1) == D("0.40")


@pytest.mark.django_db
def test_unreconciled_reservation_never_ages_out() -> None:
    cycle()
    c3 = cycle(C3_START, C3_END)
    open_row(D("0.40"), C1_START + DAY)
    assert _runtime(c3) == D("0.40")


@pytest.mark.django_db
def test_stuck_reservation_still_counted() -> None:
    cycle()
    c3 = cycle(C3_START, C3_END, observed_at=C3_START + DAY)
    claim(C3_START)
    stuck_run = provider_run(site(), C1_START + DAY, remote_status="RUNNING")
    open_row(D("0.40"), C1_START + DAY, run=stuck_run)
    assert _runtime(c3) == D("0.40")
    at = C3_START + DAY
    _fill_to(RUN_DEBIT + D("0.40") - TICK, at)
    assert _reserve(at).reason == DenialReason.CLASS_CAP


# ── Reconciled spend stays in the snapshot check (MS2-D-40, R5-01) ──

# A filler row settled in this cycle plus a snapshot whose usage leaves room
# for exactly three runs under the snapshot check. The filler keeps
# `usage - HR_cycle` below the $5 external bound, so the snapshot check (not
# the external-liability check or the class cap) is the one that binds.
FILLER = D("6.00")
LAGGING_USAGE = D("17.10") - FILLER - STANDING - 3 * RUN_DEBIT - TICK


def _snapshot_bound_ledger() -> None:
    cycle(usage=LAGGING_USAGE)
    claim()
    reconcile(open_row(FILLER, NOW - DAY), actual=FILLER, last_charge_at=NOW - DAY + HOUR)


def _reserve_and_reconcile(at: object) -> ReservationOutcome:
    outcome = _reserve(at)
    if outcome.admitted:
        row = reserved(outcome.reservation_id)
        reconcile(row, actual=row.estimate_usd or D(0), last_charge_at=row.reserved_at + HOUR)  # type: ignore[operator]
    return outcome


@pytest.mark.django_db
def test_repeated_reserve_reconcile_against_one_unchanged_snapshot_keeps_debit() -> None:
    _snapshot_bound_ledger()
    for i in range(3):
        assert _reserve_and_reconcile(NOW + i * timedelta(minutes=1)).admitted
    outcome = _reserve(NOW + timedelta(minutes=5))
    assert (outcome.admitted, outcome.reason) == (False, DenialReason.ACCOUNT_HEADROOM)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_refreshed_snapshot_that_still_lags_keeps_reconciled_debit() -> None:
    _snapshot_bound_ledger()
    for i in range(3):
        assert _reserve_and_reconcile(NOW + i * timedelta(minutes=1)).admitted
    later = NOW + 30 * timedelta(minutes=1)  # past the 900 s snapshot age
    assert _reserve(later).reason == DenialReason.ACCOUNT_STATE_UNOBSERVABLE
    # The refreshed snapshot reports the same (lagging) account usage.
    refreshed = asyncio.run(
        refresh_account_snapshot(account_client(usage=str(LAGGING_USAGE)), config=CONFIG, now=later)
    )
    assert refreshed == RefreshOutcome.REFRESHED
    assert _reserve(later).reason == DenialReason.ACCOUNT_HEADROOM


@pytest.mark.django_db
def test_inclusion_watermark_unset_debits_all_reconciled_cycle_spend() -> None:
    c1 = cycle()
    reconcile(
        open_row(D("0.40"), C1_START + DAY), actual=D("0.30"), last_charge_at=C1_START + DAY + HOUR
    )
    current, _ = cycle_debits(c1, CONFIG)
    assert current.hr_included_usd == 0
    assert current.runtime_committed_usd == D("0.30")


@pytest.mark.django_db
def test_inclusion_lag_set_drops_only_rows_ended_before_watermark() -> None:
    c1 = cycle(observed_at=NOW)
    lag = config(usage_inclusion_lag_s=int((2 * HOUR).total_seconds()))
    # Watermark = NOW - 2 h; a row's interval ends at last_charge_at + guard.
    reconcile(open_row(D("0.40"), NOW - DAY), actual=D("0.30"), last_charge_at=NOW - 4 * HOUR)
    reconcile(
        open_row(D("0.40"), NOW - DAY), actual=D("0.20"), last_charge_at=NOW - HOUR - HOUR / 2
    )
    open_row(D("0.10"), NOW - DAY)
    current, _ = cycle_debits(c1, lag)
    assert current.hr_included_usd == D("0.30")
    assert current.runtime_committed_usd == D("0.60")


# ── Cycle discovery (MS2-D-32, R10-05) ──


class _Crash(BaseException):
    """Process loss between the counter commit and the call."""


def _read_count_elsewhere() -> int:
    def work() -> int:
        try:
            return ApifyCycleDiscovery.objects.get(closed_at__isnull=True).read_count
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(work).result()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_empty_ledger_bootstrap_read_is_counted_before_it_is_sent() -> None:
    seen: list[int] = []

    def crash(_request: httpx.Request) -> None:
        seen.append(_read_count_elsewhere())  # committed, visible to another connection
        raise _Crash

    with pytest.raises(_Crash):
        asyncio.run(refresh_account_snapshot(account_client(fail=crash), config=CONFIG, now=NOW))
    assert seen == [1]
    assert ApifyCycleDiscovery.objects.get().read_count == 1
    assert not ApifyBudgetCycle.objects.exists()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_exhausted_cycle_read_cap_then_rollover_discovers_next_cycle() -> None:
    c1 = cycle()
    claim()
    ApifyBudgetCycle.objects.filter(pk=c1.pk).update(account_read_count=3000)
    stale = NOW + HOUR
    outcome = asyncio.run(refresh_account_snapshot(account_client(), config=CONFIG, now=stale))
    assert outcome == RefreshOutcome.READ_CAP_EXHAUSTED
    assert _reserve(stale).reason == DenialReason.ACCOUNT_STATE_UNOBSERVABLE
    # After cycle_end, discovery uses its own allowance, not the spent cap.
    after = C1_END + timedelta(minutes=10)
    outcome = asyncio.run(
        refresh_account_snapshot(
            account_client(cycle_start=C2_START, cycle_end=C2_END), config=CONFIG, now=after
        )
    )
    assert outcome == RefreshOutcome.DISCOVERED
    c2 = ApifyBudgetCycle.objects.get(cycle_start=C2_START)
    discovery = ApifyCycleDiscovery.objects.get()
    assert (discovery.close_reason, discovery.cycle_start, discovery.read_count) == (
        "discovered",
        C2_START,
        1,
    )
    # The discovering read counts on the new row too, plus the plan read.
    assert c2.account_read_count == 2
    c1.refresh_from_db()
    allowance = cycle_debits(c1, CONFIG)[0].discovery_allowance_usd
    assert allowance > 0
    assert cycle_debits(c2, CONFIG)[0].discovery_allowance_usd == allowance
    # Authority continued, and the fresh snapshot admits.
    assert _reserve(after).admitted


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_failed_discovery_reads_count_and_stop_at_cap() -> None:
    cfg = config(max_discovery_reads=3)

    def down(_request: httpx.Request) -> None:
        raise httpx.ConnectError("apify unreachable")

    at = NOW
    for n in range(1, 4):
        outcome = asyncio.run(
            refresh_account_snapshot(account_client(fail=down), config=cfg, now=at)
        )
        assert outcome == RefreshOutcome.READ_FAILED
        # Spacing: a read inside the interval is not sent at all.
        too_soon = asyncio.run(
            refresh_account_snapshot(account_client(fail=down), config=cfg, now=at + HOUR / 60)
        )
        # At the cap, exhaustion is reported before spacing.
        expected = (
            RefreshOutcome.DISCOVERY_EXHAUSTED if n == 3 else RefreshOutcome.DISCOVERY_TOO_SOON
        )
        assert too_soon == expected
        at += timedelta(seconds=300)
    assert ApifyCycleDiscovery.objects.get().read_count == 3
    capped = asyncio.run(refresh_account_snapshot(account_client(), config=cfg, now=at))
    assert capped == RefreshOutcome.DISCOVERY_EXHAUSTED
    assert _reserve(at, cfg).reason == DenialReason.CYCLE_UNKNOWN
    assert discovery_status(cfg) == DISCOVERY_EXHAUSTED

    # The owner command reads the cap from settings.
    with override_settings(HW_RADAR_APIFY_MAX_DISCOVERY_READS=3):
        call_command("apify_budget_reset", "--discovery", "--reason", "apify outage over")
    assert discovery_status(cfg) is None
    rows = ApifyCycleDiscovery.objects.order_by("pk")
    assert [r.close_reason for r in rows] == ["owner_reset", ""]
    found = asyncio.run(refresh_account_snapshot(account_client(), config=cfg, now=at))
    assert found == RefreshOutcome.DISCOVERED
