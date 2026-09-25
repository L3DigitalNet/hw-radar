"""Plan E3: the ledger service's cycle predicate, reserve(), and the configured cycle.

MS2-D-34 (a row counts in every billing cycle its charge interval touches; an
unreconciled row in every cycle from its admission onward), MS2-D-40 (the
billing cycle is the period; reconciled spend stays debited), MS2-D-48 (the
cycle is materialized from the configured anchor, never read from Apify), and
MS2-D-39 (charges are placed by the billing clock, never by observation
time). Figures come from ledger_support.
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from io import StringIO
from typing import Any

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from ledger_support import (
    ALLOCATION,
    ANCHOR,
    BUDGET,
    C1_END,
    C1_START,
    C2_END,
    C2_START,
    C3_END,
    C3_START,
    CONFIG,
    DAY,
    ELIGIBLE,
    FINISHED,
    GUARD,
    HOUR,
    NOW,
    RUN_DEBIT,
    RUN_ESTIMATE,
    SHAPE,
    STABLE,
    WATCH,
    Clock,
    FakeRuns,
    api_run,
    claim,
    config,
    custom_estimate,
    cycle,
    live_cycle_start,
    open_latches,
    open_row,
    provider_run,
    reconcile,
    reserved,
    settled_run,
    site,
)
from test_apify_import import ACTOR as IMPORT_ACTOR
from test_apify_import import SITE as IMPORT_SITE
from test_apify_import import TOKEN as IMPORT_TOKEN
from test_apify_import import Crash as ImportCrash
from test_apify_import import _fake as import_fake  # pyright: ignore[reportPrivateUsage]
from test_apify_import import _fixture as import_fixture  # pyright: ignore[reportPrivateUsage]
from test_apify_import import (
    _provider_run as import_provider_run_row,  # pyright: ignore[reportPrivateUsage]
)
from test_apify_ledger_authority import priced

from hw_radar.acquisition.apify import importer, jobs, ledger, storage_cleanup
from hw_radar.acquisition.apify import reconcile as reconcile_mod
from hw_radar.acquisition.apify.budget import (
    DenialReason,
    OperatorKind,
    api_call_bound,
    dataset_page_bound,
    estimate_run_cost,
    pages_per_read,
    storage_hours,
)
from hw_radar.acquisition.apify.client import MAX_API_REQUEST_BODY_BYTES, ApifyClient
from hw_radar.acquisition.apify.contract import MAX_LISTING_ROW_BYTES
from hw_radar.acquisition.apify.importer import import_provider_run
from hw_radar.acquisition.apify.jobs import TickReport, apify_poll_tick
from hw_radar.acquisition.apify.ledger import (
    LatchReason,
    LedgerRefused,
    ReservationOutcome,
    current_cycle,
    cycle_debits,
    reserve,
    reserve_operator,
    trip_latch,
)
from hw_radar.acquisition.apify.reconcile import (
    USAGE_ALLOWLIST,
    MonitorResult,
    begin_monitoring_read,
    bind_build,
    complete_monitoring_read,
    correction_close_overdue,
    invariant_breaches,
    is_unreconciled_stale,
    plan_build_read,
    plan_settlement_read,
    record_run_usage,
    resolve_stale_monitoring_markers,
    select_build_settlement,
    select_monitoring,
    stamp_work_completion,
)
from hw_radar.acquisition.contracts import NullResolver
from hw_radar.catalog.models import (
    ApifyBudgetCycle,
    ApifyBudgetLatch,
    ApifyLedgerAuthority,
    ApifySpendReservation,
    ApifyUsageRead,
    ProviderRun,
    ReservationStatus,
    SourceSite,
)
from hw_radar.catalog.models.provider import ImportState, UsageReadImmutableError

D = Decimal
TICK = D("0.0001")


def _reserve(at: object = NOW, cfg: object = CONFIG) -> ReservationOutcome:
    return reserve(WATCH, source_site_id=site().pk, config=cfg, now=at)  # type: ignore[arg-type]


def _runtime(at_cycle: ApifyBudgetCycle) -> Decimal:
    return cycle_debits(at_cycle, CONFIG)[0].runtime_committed_usd


def _fill_to(room: Decimal, at: object) -> ApifySpendReservation:
    """Seed an open row leaving exactly `room` of the watch_refresh cap."""
    return open_row(ALLOCATION - room, at)  # type: ignore[arg-type]


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
    unset = config(budget={"billing_cycle_anchor": None})
    assert _reserve(cfg=unset).reason == DenialReason.CYCLE_UNKNOWN
    assert not ApifyBudgetCycle.objects.exists()
    # A `now` before the anchor derives no cycle either.
    assert _reserve(C1_START - HOUR).reason == DenialReason.CYCLE_UNKNOWN
    assert not ApifyBudgetCycle.objects.exists()


@pytest.mark.django_db
def test_first_reserve_materializes_the_configured_cycle_row() -> None:
    claim()
    assert _reserve().admitted
    row = ApifyBudgetCycle.objects.get()
    assert (row.cycle_start, row.cycle_end) == (C1_START, C1_END)
    assert row.allocation_usd == ALLOCATION
    assert row.account_read_count == 0
    assert (
        row.account_prepaid_credit_usd,
        row.account_base_price_usd,
        row.account_limit_usd,
        row.account_usage_usd,
        row.account_observed_at,
        row.account_data_retention_days,
    ) == (None,) * 6
    # A second reserve finds the same row rather than creating another.
    assert _reserve(NOW + HOUR).admitted
    assert ApifyBudgetCycle.objects.count() == 1


@pytest.mark.django_db
def test_rollover_materializes_next_cycle_and_continues_authority() -> None:
    claim()
    assert _reserve().admitted
    at = C2_START + HOUR
    assert _reserve(at).admitted
    assert list(
        ApifyBudgetCycle.objects.order_by("cycle_start").values_list("cycle_start", "cycle_end")
    ) == [
        (C1_START, C1_END),
        (C2_START, C2_END),
    ]
    continued = ApifyLedgerAuthority.objects.get(cycle_start=C2_START)
    assert (continued.kind, continued.ledger_id) == ("continued", "env-a")


@pytest.mark.django_db
def test_anchor_moved_backward_into_recorded_cycle_denies_cycle_unknown() -> None:
    c1 = cycle()
    claim()
    before = list(ApifyBudgetCycle.objects.values_list("pk", "cycle_start", "cycle_end"))
    # An anchor of 1 Sep derives [1 Sep, 30 Sep], and the recorded 5 Sep row
    # starts inside it: history would be rewritten, so nothing is created.
    back = config(budget={"billing_cycle_anchor": C1_START.replace(day=1)})
    outcome = _reserve(cfg=back)
    assert outcome.reason == DenialReason.CYCLE_UNKNOWN
    # The denial names the conflict, not a generic "no cycle" (verifier, s5).
    assert "conflicts with a recorded cycle" in outcome.detail
    assert list(ApifyBudgetCycle.objects.values_list("pk", "cycle_start", "cycle_end")) == before
    c1.refresh_from_db()
    assert c1.cycle_end == C1_END


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
    """A day-5 anchor's 5th-to-4th cycles place spend by their bounds, never by calendar month.

    The plan's second case, a cycle shortened by a plan change, lives in
    test_anchor_moved_forward_clamps_the_recorded_cycle.
    """
    claim()
    assert _reserve(C1_START + DAY).admitted
    assert _reserve(C2_START + DAY).admitted
    c1 = ApifyBudgetCycle.objects.get(cycle_start=C1_START)
    c2 = ApifyBudgetCycle.objects.get(cycle_start=C2_START)
    assert (c1.cycle_end, c2.cycle_end) == (C1_END, C2_END)
    ApifySpendReservation.objects.all().delete()
    # 3 October is calendar October but billing cycle 1 (5 Sep -> 4 Oct).
    october_3 = C1_END - timedelta(days=1, hours=12)
    reconcile(open_row(D("0.30"), october_3), actual=D("0.20"), last_charge_at=october_3 + HOUR)
    assert (_runtime(c1), _runtime(c2)) == (D("0.20"), D(0))


@pytest.mark.django_db
def test_anchor_moved_forward_clamps_the_recorded_cycle() -> None:
    c1 = cycle()
    claim()
    # A plan change moves the real cycle: the operator re-verifies and sets a
    # later anchor, 20 Sep, which derives [20 Sep, 19 Oct] (MS2-D-48 *Drift*).
    new_start = C1_START + 15 * DAY
    moved = config(
        budget={"billing_cycle_anchor": new_start, "account_verified_on": new_start.date()}
    )
    at = new_start - DAY
    reconcile(open_row(D("0.20"), at), actual=D("0.20"), last_charge_at=at + HOUR)
    late = reconcile(
        open_row(D("0.30"), at), actual=D("0.30"), last_charge_at=new_start - GUARD / 2
    )
    after = new_start + HOUR
    assert _reserve(after, moved).admitted
    c1.refresh_from_db()
    assert c1.cycle_end == new_start - timedelta(milliseconds=1)
    new_cycle = current_cycle(after)
    assert new_cycle is not None and new_cycle.cycle_start == new_start
    assert _runtime(c1) == D("0.50")
    # Only the row within a guard of the boundary, plus the admitted run.
    assert _runtime(new_cycle) == (late.actual_usd or D(0)) + RUN_DEBIT
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
    c1, c2 = cycle(), cycle(C2_START, C2_END)
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
    c3 = cycle(C3_START, C3_END)
    claim(C3_START)
    stuck_run = provider_run(site(), C1_START + DAY, remote_status="RUNNING")
    open_row(D("0.40"), C1_START + DAY, run=stuck_run)
    assert _runtime(c3) == D("0.40")
    at = C3_START + DAY
    _fill_to(RUN_DEBIT + D("0.40") - TICK, at)
    assert _reserve(at).reason == DenialReason.CLASS_CAP


# ── Reconciled spend stays debited (MS2-D-40, R5-01; MS2-D-48) ──


@pytest.mark.django_db
def test_reconciled_spend_stays_debited_against_configured_limit() -> None:
    # A $15 limit (P = 13.50, P - E = 8.50 < A) so the external-liability
    # check binds. Room for exactly three runs after an 8.50 - 3 runs filler.
    small = config(budget={"account_limit_usd": D("15")})
    cycle()
    claim()
    filler = D("8.50") - 3 * RUN_DEBIT
    reconcile(open_row(filler, NOW - DAY), actual=filler, last_charge_at=NOW - DAY + HOUR)
    for i in range(3):
        outcome = _reserve(NOW + i * timedelta(minutes=1), small)
        assert outcome.admitted
        row = reserved(outcome.reservation_id)
        # Settled at its estimate with monitoring still open: it keeps
        # debiting its full RUN_DEBIT, and nothing observed ever lowers it.
        reconcile(row, actual=row.estimate_usd or D(0), last_charge_at=row.reserved_at + HOUR)
    outcome = _reserve(NOW + timedelta(minutes=5), small)
    assert (outcome.admitted, outcome.reason) == (False, DenialReason.ACCOUNT_HEADROOM)


# ══ E4: reconcile and the overrun latch (MS2-D-23, -26, -32, -33, -34, -41, -46, -47) ══
#
# Direct calls into acquisition.apify.reconcile with explicit `now`s; the tests
# the plan says run "through the scheduler" drive apify_poll_tick with a
# settable Clock and FakeRuns (httpx.MockTransport) further below.


def _settle_read(
    run: ProviderRun, total: str | None, at: object, cfg: object = CONFIG, **kw: Any
) -> bool:
    return record_run_usage(
        run.pk,
        api_run(run.external_run_id or "", total, **kw),
        read_at=at,  # type: ignore[arg-type]
        config=cfg,  # type: ignore[arg-type]
    )


def _fresh(row: ApifySpendReservation) -> ApifySpendReservation:
    row.refresh_from_db()
    return row


# ── The latch: trips, reset, estimator bump (MS2-D-26) ──


@pytest.mark.django_db
def test_overrun_latch_denies_admission_until_reset() -> None:
    cycle()
    claim()
    with pytest.raises(CommandError, match="latch_not_tripped"):
        call_command("apify_budget_reset", "--reason", "nothing to clear")
    trip_latch(LatchReason.OVERRUN, config=CONFIG, now=NOW)
    assert _reserve().reason == DenialReason.OVERRUN_LATCH
    out = StringIO()
    call_command("apify_budget_reset", "--reason", "overrun investigated", stdout=out)
    assert "overrun latch cleared (1 trips" in out.getvalue()
    trip = ApifyBudgetLatch.objects.get()
    assert trip.cleared_reason == "owner reset: overrun investigated"
    assert _reserve().admitted


@pytest.mark.django_db
def test_estimator_version_bump_clears_latch() -> None:
    cycle()
    claim()
    trip_latch(LatchReason.OVERRUN, config=CONFIG, now=NOW)
    assert _reserve().reason == DenialReason.OVERRUN_LATCH
    bumped = config(budget={"estimator_version": "2"})
    assert _reserve(cfg=bumped).admitted
    trip = ApifyBudgetLatch.objects.get()
    assert trip.cleared_at == NOW
    assert trip.cleared_reason == "estimator_version bumped to 2"


@pytest.mark.django_db
def test_proxy_usage_trips_latch() -> None:
    run, _ = settled_run()
    _settle_read(run, "0.03", NOW, usage_usd={"PROXY_RESIDENTIAL_TRANSFER_GBYTES": "0.01"})
    assert open_latches() == [LatchReason.UNEXPECTED_USAGE_COMPONENT]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("usage_usd", "unparseable"),
    [
        ({"REQUEST_QUEUE_WRITES": "0.001"}, ()),
        ({"PROXY_SERPS": "0.002"}, ()),
        ({"KEY_VALUE_STORE_LISTS": "0.0001"}, ()),
        ({"SOMETHING_NEW": "0.0001"}, ()),
        ({}, ("usageUsd.ACTOR_COMPUTE_UNITS",)),
    ],
    ids=["request-queue", "proxy-serps", "kv-lists", "unknown-key", "unparseable"],
)
def test_usage_component_outside_allowlist_trips_latch(
    usage_usd: dict[str, str], unparseable: tuple[str, ...]
) -> None:
    run, _ = settled_run()
    _settle_read(run, "0.03", NOW, usage_usd=usage_usd, unparseable=unparseable)
    assert open_latches() == [LatchReason.UNEXPECTED_USAGE_COMPONENT]


@pytest.mark.django_db
def test_allowlisted_and_zero_components_trip_nothing() -> None:
    run, _ = settled_run()
    usage = dict.fromkeys(USAGE_ALLOWLIST, "0.001") | {"PROXY_SERPS": "0"}
    _settle_read(run, "0.03", NOW, usage_usd=usage)
    assert open_latches() == []


@pytest.mark.django_db
def test_observed_restart_trips_latch_as_start_option_mismatch() -> None:
    # ED-20: a restart in the run record counts as a start-option mismatch,
    # tripped after the observation's own transaction (jobs._record_observation).
    run, _ = settled_run()
    jobs._record_observation(run.pk, api_run(run.external_run_id or "", None, restart_count=1), NOW)  # pyright: ignore[reportPrivateUsage]
    assert open_latches() == [LatchReason.START_OPTION_MISMATCH]
    assert ApifyBudgetLatch.objects.values_list("provider_run_id", flat=True).get() == run.pk  # pyright: ignore[reportAttributeAccessIssue]


# ── Usage states and the settlement predicate (MS2-D-41) ──


@pytest.mark.django_db
def test_first_usage_read_is_provisional_until_settle_delay() -> None:
    run, row = settled_run()
    # 5 s after finishedAt: evidence, provisional, never settling.
    assert not _settle_read(run, "0.03", FINISHED + timedelta(seconds=5), STABLE)
    row = _fresh(row)
    assert (row.status, row.usage_provisional_usd) == (
        ReservationStatus.USAGE_PROVISIONAL,
        D("0.0300"),
    )
    assert row.usage_finalized_usd is None
    assert ApifyUsageRead.objects.filter(reservation=row).count() == 1


@pytest.mark.django_db
def test_settlement_ignores_reads_before_final_charge_op_plus_guard() -> None:
    run, row = settled_run()
    # Two identical, spaced reads that END just before anchor + delay + guard:
    # neither is eligible, so stable_reads cannot finalize on them (ED-08).
    assert not _settle_read(run, "0.03", ELIGIBLE - timedelta(seconds=61), STABLE)
    assert not _settle_read(run, "0.03", ELIGIBLE - timedelta(seconds=1), STABLE)
    assert _fresh(row).status == ReservationStatus.USAGE_PROVISIONAL
    assert not _settle_read(run, "0.03", ELIGIBLE, STABLE)
    assert _settle_read(run, "0.03", ELIGIBLE + timedelta(seconds=60), STABLE)
    row = _fresh(row)
    assert (row.status, row.settlement_basis, row.usage_finalized_usd) == (
        ReservationStatus.RECONCILED,
        "stable_reads",
        D("0.0300"),
    )


@pytest.mark.django_db
def test_nonnull_usage_rising_after_ten_seconds_is_not_finalized_early() -> None:
    run, row = settled_run()
    assert not _settle_read(run, "0.02", FINISHED + timedelta(seconds=10), STABLE)
    assert not _settle_read(run, "0.03", FINISHED + timedelta(seconds=30), STABLE)
    row = _fresh(row)
    assert row.status == ReservationStatus.USAGE_PROVISIONAL
    assert _runtime(cycle()) == RUN_DEBIT  # still at its full bound
    assert not _settle_read(run, "0.03", ELIGIBLE, STABLE)
    assert _settle_read(run, "0.03", ELIGIBLE + timedelta(seconds=60), STABLE)
    assert _fresh(row).settled_run_usage_usd == D("0.0300")  # the later, higher figure


@pytest.mark.django_db
def test_stable_reads_predicate_requires_identical_consecutive_reads() -> None:
    run, row = settled_run()
    # Same total, different breakdown: not stable.
    _settle_read(run, "0.03", ELIGIBLE, STABLE, usage_usd={"ACTOR_COMPUTE_UNITS": "0.03"})
    assert not _settle_read(
        run,
        "0.03",
        ELIGIBLE + timedelta(seconds=60),
        STABLE,
        usage_usd={"ACTOR_COMPUTE_UNITS": "0.02", "DATASET_READS": "0.01"},
    )
    # Identical but closer than the 60 s interval: not stable.
    assert not _settle_read(
        run,
        "0.03",
        ELIGIBLE + timedelta(seconds=90),
        STABLE,
        usage_usd={"ACTOR_COMPUTE_UNITS": "0.02", "DATASET_READS": "0.01"},
    )
    assert _fresh(row).status == ReservationStatus.USAGE_PROVISIONAL
    assert _settle_read(
        run,
        "0.03",
        ELIGIBLE + timedelta(seconds=150),
        STABLE,
        usage_usd={"ACTOR_COMPUTE_UNITS": "0.02", "DATASET_READS": "0.01"},
    )


@pytest.mark.django_db
def test_bound_mode_settles_at_max_of_execution_bound_and_reads() -> None:
    run, low = settled_run()
    assert _settle_read(run, "0.01", ELIGIBLE)  # below the execution bound
    low = _fresh(low)
    assert (low.settlement_basis, low.settled_run_usage_usd) == (
        "bound",
        RUN_ESTIMATE.execution_bound_usd,
    )
    run2, high = settled_run()
    _settle_read(run2, "0.04", NOW)  # early, above the bound: still counted
    assert _settle_read(run2, "0.02", ELIGIBLE)
    assert _fresh(high).settled_run_usage_usd == D("0.0400")


@pytest.mark.django_db
def test_process_stop_alone_never_reconciles() -> None:
    # SUCCEEDED with finishedAt and a usage figure, but the import and the
    # storage are still open: no anchor, so nothing ever settles it.
    run, row = settled_run(anchored=False)
    far = NOW + 30 * DAY
    assert not _settle_read(run, "0.03", far)
    assert plan_settlement_read(run.pk, far, CONFIG) is None
    assert _fresh(row).status == ReservationStatus.USAGE_PROVISIONAL


@pytest.mark.django_db
def test_usage_read_before_final_charge_op_does_not_reconcile() -> None:
    run, row = settled_run(anchored=False, import_state="finalized")
    assert not _settle_read(run, "0.03", ELIGIBLE + DAY)
    assert _fresh(row).status == ReservationStatus.USAGE_PROVISIONAL


@pytest.mark.django_db
def test_no_read_eligible_before_import_and_deletion_barriers() -> None:
    # Cleanup deferred past the deadline: reads are appended as evidence, but
    # none is eligible and no finalize deadline runs until the anchor is set.
    run, row = settled_run(anchored=False, import_state="finalized", storage_state="retained")
    for hours in (2, 30, 72):
        assert not _settle_read(run, "0.03", NOW + timedelta(hours=hours), STABLE)
    assert ApifyUsageRead.objects.filter(reservation=row).count() == 3
    assert _fresh(row).status == ReservationStatus.USAGE_PROVISIONAL
    assert plan_settlement_read(run.pk, NOW + 10 * DAY, STABLE) is None


@pytest.mark.django_db
def test_non_null_usage_before_cleanup_completes_does_not_release_liability() -> None:
    c1 = cycle()
    run, row = settled_run(
        anchored=False, import_state="observations_committed", storage_state="retained"
    )
    _settle_read(run, "0.01", ELIGIBLE)
    assert _fresh(row).status == ReservationStatus.USAGE_PROVISIONAL
    assert _runtime(c1) == RUN_DEBIT


@pytest.mark.django_db
def test_unsettled_usage_keeps_full_reservation_and_retries() -> None:
    c1 = cycle()
    run, row = settled_run()
    assert plan_settlement_read(run.pk, ELIGIBLE, CONFIG) == run.external_run_id
    assert not _settle_read(run, None, ELIGIBLE)  # a null figure settles nothing
    run.refresh_from_db()
    assert run.next_attempt_at == ELIGIBLE + timedelta(seconds=60)  # retried
    assert _fresh(row).status == ReservationStatus.RESERVED
    assert _runtime(c1) == RUN_DEBIT


@pytest.mark.django_db
def test_late_usage_reconciled_on_later_tick() -> None:
    run, row = settled_run()
    # A read before eligibility moves the retry to the first eligible instant.
    assert plan_settlement_read(run.pk, NOW, CONFIG) is None
    run.refresh_from_db()
    assert run.next_attempt_at == ELIGIBLE
    assert plan_settlement_read(run.pk, ELIGIBLE, CONFIG) is not None
    assert not _settle_read(run, None, ELIGIBLE)
    later = ELIGIBLE + timedelta(minutes=5)
    assert plan_settlement_read(run.pk, later, CONFIG) is not None
    assert _settle_read(run, "0.03", later)
    assert _fresh(row).status == ReservationStatus.RECONCILED


@pytest.mark.django_db
def test_permanently_unfinalized_run_stays_at_bound_and_is_stale_at_cycle_end() -> None:
    c1 = cycle()
    run, row = settled_run(reserved_at=C1_END - DAY)
    for i in range(4):  # a figure that never stabilizes
        _settle_read(run, f"0.0{i + 1}", ELIGIBLE + timedelta(minutes=i), STABLE)
    assert _fresh(row).status == ReservationStatus.USAGE_PROVISIONAL
    assert _runtime(c1) == RUN_DEBIT
    assert not is_unreconciled_stale(row, C1_END - HOUR)
    assert is_unreconciled_stale(row, C1_END + HOUR)
    # At the finalize deadline it settles at max(execution bound, every read).
    deadline = ANCHOR + timedelta(seconds=86400)
    assert plan_settlement_read(run.pk, deadline, STABLE) is None
    row = _fresh(row)
    assert (row.status, row.settlement_basis, row.settled_run_usage_usd) == (
        ReservationStatus.RECONCILED,
        "bound_unfinalized",
        D("0.0400"),
    )
    assert row.usage_finalized_usd is None


@pytest.mark.django_db
def test_run_poll_cap_settles_at_bound_unfinalized() -> None:
    # MS2-D-32 *Run polls* (the E side of D11's poll-cap test, ED-01): at the
    # cap no read is made and the row settles at the bound, returning nothing.
    run, row = settled_run(run_poll_count=BUDGET.max_run_polls)
    assert plan_settlement_read(run.pk, ELIGIBLE, STABLE) is None
    row = _fresh(row)
    assert (row.status, row.settlement_basis, row.settled_run_usage_usd) == (
        ReservationStatus.RECONCILED,
        "bound_unfinalized",
        RUN_ESTIMATE.execution_bound_usd,
    )


@pytest.mark.django_db
def test_finalized_usage_written_once_not_recomputed() -> None:
    run, row = settled_run()
    _settle_read(run, "0.02", ELIGIBLE, STABLE)
    assert _settle_read(run, "0.02", ELIGIBLE + timedelta(seconds=60), STABLE)
    row = _fresh(row)
    first = (row.usage_finalized_usd, row.usage_finalized_at)
    assert first == (D("0.0200"), ELIGIBLE + timedelta(seconds=60))
    # A later lower read changes nothing; a later higher one is a correction
    # that raises the settled usage while the finalized figure stays evidence.
    _monitor(row, "0.01", NOW + DAY)
    row = _fresh(row)
    assert (row.usage_finalized_usd, row.settled_run_usage_usd) == (D("0.0200"), D("0.0200"))
    _monitor(row, "0.025", NOW + 2 * DAY)
    row = _fresh(row)
    assert (row.usage_finalized_usd, row.usage_finalized_at) == first
    assert row.settled_run_usage_usd == D("0.0250")


# ── Post-run cost and reconcile (MS2-D-41) ──


@pytest.mark.django_db
def test_post_run_cost_uses_bound_until_measured() -> None:
    run, row = settled_run()
    assert _settle_read(run, "0.03", ELIGIBLE)
    row = _fresh(row)
    assert (row.post_run_cost_mode, row.post_run_cost_usd) == (
        "bound",
        RUN_ESTIMATE.post_run_liability_usd,
    )
    assert row.actual_usd == RUN_ESTIMATE.execution_bound_usd + RUN_ESTIMATE.post_run_liability_usd


@pytest.mark.django_db
def test_post_run_cost_counted_mode_uses_operation_counters() -> None:
    counted = dataclasses.replace(STABLE, post_run_cost_mode="counted")
    run, row = settled_run(
        reserved_at=ANCHOR - 2 * HOUR,
        dataset_read_count=2,
        kv_read_count=1,
        storage_cleanup_attempts=1,
        run_poll_count=4,
    )
    _settle_read(run, "0.02", ELIGIBLE, counted)
    assert _settle_read(run, "0.02", ELIGIBLE + timedelta(seconds=60), counted)
    run.refresh_from_db()
    row = _fresh(row)
    # Recomputed here from the counters, independently of the module.
    p = BUDGET.prices
    assert p.dataset_reads_per_1000 and p.kv_reads_per_1000 and p.dataset_writes_per_1000
    assert p.kv_writes_per_1000 and p.dataset_storage_per_gb_hour and p.kv_storage_per_gb_hour
    pages = pages_per_read(BUDGET, run.max_items, MAX_LISTING_ROW_BYTES)
    call = api_call_bound(BUDGET)
    page = dataset_page_bound(BUDGET)
    hours = D(2)  # admitted two hours before verified deletion
    cost = (
        2 * (run.max_items + pages) * p.dataset_reads_per_1000 / 1000
        + 1 * p.kv_reads_per_1000 / 1000
        + 1
        * ((run.max_items + 1) * p.dataset_writes_per_1000 / 1000 + 3 * p.kv_writes_per_1000 / 1000)
        + (
            run.max_items * MAX_LISTING_ROW_BYTES * p.dataset_storage_per_gb_hour
            + (65536 + MAX_API_REQUEST_BODY_BYTES) * p.kv_storage_per_gb_hour
        )
        / D(10**9)
        * hours
        + (1 + 4 + 4 * 1 + 1) * call  # start + 4 polls + 1 cleanup attempt + 1 KV read
        + 2 * pages * page
    ) * D("1.10")
    expected = cost.quantize(D("0.0001"), rounding=ROUND_CEILING)
    assert (row.post_run_cost_mode, row.post_run_cost_usd) == ("counted", expected)
    assert expected < RUN_ESTIMATE.post_run_liability_usd


@pytest.mark.django_db
def test_reconcile_below_reservation_returns_capacity() -> None:
    c1 = cycle()
    run, row = settled_run()
    assert _runtime(c1) == RUN_DEBIT
    _settle_read(run, "0.01", ELIGIBLE, STABLE)
    assert _settle_read(run, "0.01", ELIGIBLE + timedelta(seconds=60), STABLE)
    row = _fresh(row)
    actual = D("0.0100") + RUN_ESTIMATE.post_run_liability_usd
    settled_at = ELIGIBLE + timedelta(seconds=60)
    assert row.actual_usd == actual
    assert actual < RUN_ESTIMATE.estimate_usd
    assert _runtime(c1) == actual + RUN_ESTIMATE.monitoring_bound_usd
    assert row.last_charge_at == settled_at  # reconciled_at, the latest stamp
    assert row.correction_monitor_until == settled_at + 7 * DAY


@pytest.mark.django_db
def test_reconcile_above_reservation_trips_overrun_and_pauses_paid_work() -> None:
    cycle()
    claim()
    run, row = settled_run()
    assert _settle_read(run, "0.05", ELIGIBLE)  # 0.05 + post-run > the 0.0767 estimate
    row = _fresh(row)
    assert row.actual_usd is not None and row.actual_usd > RUN_ESTIMATE.estimate_usd
    assert open_latches() == [LatchReason.OVERRUN]
    assert _reserve().reason == DenialReason.OVERRUN_LATCH


@pytest.mark.django_db
def test_upward_correction_after_reconciliation_raises_settled_and_trips_latch() -> None:
    cycle()
    run, row = settled_run()
    assert _settle_read(run, "0.01", ELIGIBLE)
    assert open_latches() == []
    _monitor(_fresh(row), "0.06", NOW + DAY)
    row = _fresh(row)
    assert row.settled_run_usage_usd == D("0.0600")
    assert row.actual_usd == D("0.0600") + RUN_ESTIMATE.post_run_liability_usd
    assert LatchReason.OVERRUN in open_latches()


@pytest.mark.django_db
def test_later_lower_read_never_returns_capacity() -> None:
    c1 = cycle()
    run, row = settled_run()
    assert _settle_read(run, "0.05", ELIGIBLE, STABLE) is False
    assert _settle_read(run, "0.05", ELIGIBLE + timedelta(seconds=60), STABLE)
    before = _runtime(c1)
    _monitor(_fresh(row), "0.001", NOW + DAY)
    assert _fresh(row).settled_run_usage_usd == D("0.0500")
    assert _runtime(c1) == before


def _monitor(
    row: ApifySpendReservation, total: str | None, at: datetime, cfg: object = CONFIG
) -> bool:
    """One selector-4 read by direct calls: counted and pending, then completed."""
    call = begin_monitoring_read(row.pk, at, cfg)  # type: ignore[arg-type]
    assert call is not None
    result = None if total is None else MonitorResult(total=D(total))
    return complete_monitoring_read(row.pk, result, at, cfg)  # type: ignore[arg-type]


@pytest.mark.django_db
def test_below_estimate_upward_correction_after_capacity_reuse_trips_latch() -> None:
    cycle()
    claim()
    run_a, a = settled_run(estimate=custom_estimate("2.00"))
    _settle_read(run_a, "0.50", ELIGIBLE, STABLE)
    assert _settle_read(run_a, "0.50", ELIGIBLE + timedelta(seconds=60), STABLE)
    # B reserves the released $1.50 and more: the runtime cap is now full.
    a = _fresh(a)
    room = ALLOCATION - (a.actual_usd or D(0)) - (a.monitoring_bound_usd or D(0))
    open_row(room, NOW)
    assert open_latches() == []
    _monitor(a, "1.00", NOW + DAY)  # still below A's own $2 reservation
    assert open_latches() == [LatchReason.POST_ADMISSION_INVARIANT_BREACH]
    assert _fresh(a).actual_usd == D("1.0000")


def _stable_with(**budget: Any) -> Any:
    return dataclasses.replace(STABLE, budget=dataclasses.replace(STABLE.budget, **budget))


@pytest.mark.django_db
def test_upward_correction_breaching_external_liability_check_trips_latch() -> None:
    # A $6.00 configured limit -> P = 5.40; with E = 5.00 Hardware Radar may
    # hold 0.40. The row's correction takes HR_cycle past that, while the
    # class cap and the target both still hold.
    small = _stable_with(account_limit_usd=D("6.00"))
    cycle()
    run, row = settled_run(estimate=custom_estimate("0.60"))
    _settle_read(run, "0.10", ELIGIBLE, small)
    assert _settle_read(run, "0.10", ELIGIBLE + timedelta(seconds=60), small)
    assert open_latches() == []
    _monitor(_fresh(row), "0.45", NOW + DAY, small)
    assert invariant_breaches(_fresh(row), small, NOW + DAY) == [
        "2026-09-05 external-liability check"
    ]
    assert open_latches() == [LatchReason.POST_ADMISSION_INVARIANT_BREACH]


# Contract cases 1-6 of budget.account_setting_problem (R12-04). The correction
# below happens at NOW + DAY (21 Sep); a verified-on of 22 Sep is invalid then.
_CORRECTION_AT = NOW + DAY


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("changes", "setting"),
    [
        ({"billing_cycle_anchor": None}, "BILLING_CYCLE_ANCHOR"),
        ({"account_limit_usd": None}, "ACCOUNT_LIMIT_USD"),
        ({"account_base_price_usd": None}, "ACCOUNT_BASE_PRICE_USD"),
        ({"account_data_retention_days": 0}, "ACCOUNT_DATA_RETENTION_DAYS"),
        ({"account_verified_on": (_CORRECTION_AT + DAY).date()}, "ACCOUNT_VERIFIED_ON"),
        ({"account_margin_usd": D("NaN")}, "ACCOUNT_MARGIN_USD"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_correction_with_invalid_account_setting_is_an_invariant_breach(
    changes: dict[str, Any], setting: str
) -> None:
    cycle()
    claim()
    run, row = settled_run(estimate=custom_estimate("2.00"))
    _settle_read(run, "0.50", ELIGIBLE, STABLE)
    assert _settle_read(run, "0.50", ELIGIBLE + timedelta(seconds=60), STABLE)
    assert open_latches() == []
    bad = _stable_with(**changes)
    _monitor(_fresh(row), "1.00", _CORRECTION_AT, bad)  # upward, still below $2
    assert invariant_breaches(_fresh(row), bad, _CORRECTION_AT) == [f"unverifiable: {setting}"]
    assert open_latches() == [LatchReason.POST_ADMISSION_INVARIANT_BREACH]
    if setting == "ACCOUNT_VERIFIED_ON":
        # Evaluated at the caller's own now: a day later the date is valid.
        assert invariant_breaches(_fresh(row), bad, _CORRECTION_AT + 2 * DAY) == []


@pytest.mark.django_db
def test_correction_attributed_to_charge_interval_cycles_not_read_time() -> None:
    c1, c2 = cycle(), cycle(C2_START, C2_END)
    run, row = settled_run()
    assert _settle_read(run, "0.01", ELIGIBLE)
    _monitor(_fresh(row), "0.03", C2_START + DAY)  # read in cycle 2
    row = _fresh(row)
    assert row.last_charge_at is not None and row.last_charge_at < C1_END - GUARD
    runtime_c1 = cycle_debits(c1, CONFIG)[0].runtime_committed_usd
    runtime_c2 = cycle_debits(c2, CONFIG)[0].runtime_committed_usd
    # The raised settled amount counts where the charges happened (cycle 1);
    # cycle 2 carries only the monitoring allowance the read spent there.
    assert runtime_c1 == (row.actual_usd or D(0)) + (row.monitoring_bound_usd or D(0))
    assert runtime_c2 == row.monitoring_bound_usd


@pytest.mark.django_db
def test_usage_reads_are_append_only_evidence() -> None:
    run, row = settled_run()
    _settle_read(run, "0.03", NOW)
    read = ApifyUsageRead.objects.get(reservation=row)
    read.usage_total_usd = D("0.01")
    with pytest.raises(UsageReadImmutableError):
        read.save()
    with pytest.raises(UsageReadImmutableError):
        read.delete()
    with pytest.raises(UsageReadImmutableError):
        ApifyUsageRead.objects.filter(pk=read.pk).update(usage_total_usd=D("0.01"))
    with pytest.raises(UsageReadImmutableError):
        ApifyUsageRead.objects.filter(pk=read.pk).delete()
    # A read must cite its reservation's own run (null for both on a build).
    other, _ = settled_run()
    with pytest.raises(ValueError, match="filed under a reservation"):
        ApifyUsageRead.objects.create(reservation=row, provider_run=other, read_at=NOW)
    assert ApifyUsageRead.objects.get(pk=read.pk).usage_total_usd == D("0.03")


# ── Correction monitoring by direct calls (MS2-D-23 selector 4, MS2-D-34) ──


@pytest.mark.django_db
def test_correction_read_cap_keeps_obligation_open_and_overdue() -> None:
    run, row = settled_run()
    assert _settle_read(run, "0.03", ELIGIBLE)
    row = _fresh(row)
    cap = BUDGET.max_correction_reads or 0
    ProviderRun.objects.filter(pk=run.pk).update(correction_read_count=cap)
    row = _fresh(row)
    assert begin_monitoring_read(row.pk, NOW + 30 * DAY, CONFIG) is None
    assert row.pk not in select_monitoring(NOW + 30 * DAY, CONFIG)
    assert row.correction_monitor_closed_at is None
    assert correction_close_overdue(row, NOW + DAY, CONFIG)  # the cap alone makes it overdue


@pytest.mark.django_db
def test_monitoring_interval_stays_open_while_final_read_is_pending_across_cycle_boundary() -> None:
    c2 = cycle(C2_START, C2_END)
    cycle()
    run, row = settled_run()
    assert _settle_read(run, "0.03", C1_END - 5 * DAY)  # reconciled well inside cycle 1
    cap = BUDGET.max_correction_reads or 0
    ProviderRun.objects.filter(pk=run.pk).update(correction_read_count=cap - 1)
    # The final read's increment commits more than one guard before cycle 1
    # ends, then the worker pauses; the read is only sent in cycle 2.
    pre_send = C1_END - 3 * GUARD
    assert begin_monitoring_read(row.pk, pre_send, CONFIG) is not None
    allowance = RUN_ESTIMATE.monitoring_bound_usd
    # Counted in cycle 2 while pending, although the cap is spent and the
    # pre-send stamp lies more than a guard before cycle 1 ends.
    assert cycle_debits(c2, CONFIG)[0].runtime_committed_usd == allowance
    sent = C2_START + 2 * HOUR
    complete_monitoring_read(row.pk, MonitorResult(total=D("0.03")), sent, CONFIG)
    row = _fresh(row)
    assert (row.monitoring_call_pending_since, row.monitoring_charge_last_at) == (None, sent)
    # Completed in cycle 2, so the allowance stays in cycle 2's debit.
    assert cycle_debits(c2, CONFIG)[0].runtime_committed_usd == allowance


@pytest.mark.django_db
def test_stale_monitoring_marker_resolves_at_poller_start() -> None:
    c2 = cycle(C2_START, C2_END)
    cycle()
    run, row = settled_run()
    assert _settle_read(run, "0.03", C1_END - 2 * DAY)
    ProviderRun.objects.filter(pk=run.pk).update(
        correction_read_count=(BUDGET.max_correction_reads or 0) - 1
    )
    ApifySpendReservation.objects.filter(pk=row.pk).update(next_usage_read_at=C1_END - DAY)
    assert begin_monitoring_read(row.pk, C1_END - DAY, CONFIG) is not None
    # The process dies with the read counted and unsent. A marker newer than
    # the new process's start is left alone (it may be in flight).
    assert resolve_stale_monitoring_markers(C1_END - DAY) == 0
    restart = C2_START + 3 * HOUR
    assert resolve_stale_monitoring_markers(restart) == 1
    row = _fresh(row)
    assert (row.monitoring_call_pending_since, row.monitoring_charge_last_at) == (None, restart)
    # The interval ends no earlier than the new process's start: cycle 2 counts it.
    assert cycle_debits(c2, CONFIG)[0].runtime_committed_usd == RUN_ESTIMATE.monitoring_bound_usd


@pytest.mark.django_db
def test_counted_mode_never_releases_unused_monitoring_allowance_at_reconciliation() -> None:
    counted = dataclasses.replace(CONFIG, post_run_cost_mode="counted")
    run, row = settled_run()
    assert _settle_read(run, "0.03", ELIGIBLE, counted)
    row = _fresh(row)
    assert row.monitoring_bound_usd == RUN_ESTIMATE.monitoring_bound_usd  # untouched
    assert row.correction_monitor_until is not None
    # Once closed with no further call possible, it settles at reads x bound.
    _monitor(row, "0.03", row.correction_monitor_until, counted)
    row = _fresh(row)
    assert row.correction_monitor_closed_at is not None
    expected = (1 * api_call_bound(BUDGET)).quantize(D("0.0001"), rounding=ROUND_CEILING)
    assert row.monitoring_bound_usd == expected


@pytest.mark.django_db
def test_failed_deletion_with_no_later_runs_counts_full_cycle_storage_in_every_cycle() -> None:
    # R10-07: retention (7 days) is shorter than a cycle, the deletion failed
    # at its cap, the latch trips, and no run follows. The row is never
    # reconciled, so its full estimate, whose storage component is priced over
    # at least a full 744 h cycle, counts in each of three later cycles.
    cycles = [cycle(), cycle(C2_START, C2_END), cycle(C3_START, C3_END)]
    ApifyBudgetCycle.objects.update(account_data_retention_days=7)
    run, row = settled_run(anchored=False, import_state="finalized", storage_state="delete_failed")
    trip_latch(
        LatchReason.DELETE_ATTEMPTS_EXHAUSTED, provider_run_id=run.pk, config=CONFIG, now=NOW
    )
    assert storage_hours(BUDGET) >= 744
    assert D(str(row.component_bounds["storage"])) > 0
    for at_cycle in cycles:
        assert _runtime(at_cycle) == RUN_DEBIT
    assert plan_settlement_read(run.pk, C3_END, CONFIG) is None
    assert _fresh(row).status == ReservationStatus.RESERVED


@pytest.mark.django_db
def test_delayed_deletion_keeps_storage_liability_outstanding() -> None:
    c1, c2 = cycle(), cycle(C2_START, C2_END)
    run, row = settled_run(
        reserved_at=C1_END - 2 * DAY,
        anchored=False,
        import_state="finalized",
        storage_state="retained",
    )
    for at_cycle in (c1, c2):
        assert _runtime(at_cycle) == RUN_DEBIT  # beyond its admission cycle
    # Deletion is verified days later; that commit is the second barrier.
    deleted = C2_START + 3 * DAY
    run.storage_state = "deleted"
    assert stamp_work_completion(run, deleted)
    run.save()
    assert _settle_read(run, "0.03", deleted + timedelta(seconds=10) + GUARD)
    row = _fresh(row)
    assert row.status == ReservationStatus.RECONCILED
    assert row.last_charge_at is not None and row.last_charge_at >= deleted


# ── Work-completion anchor (MS2-D-32 *Settlement*, residual (a)) ──


@pytest.mark.django_db
def test_final_charge_op_at_is_set_once_at_the_later_barrier() -> None:
    run, _ = settled_run(anchored=False, import_state="finalized", storage_state="retained")
    assert not stamp_work_completion(run, NOW)  # one barrier still open
    run.storage_state = "deleted"
    assert stamp_work_completion(run, NOW + HOUR)
    assert not stamp_work_completion(run, NOW + 2 * HOUR)  # write-once
    assert run.final_charge_op_at == NOW + HOUR


# ── Serialization (MS2-D-26, ED-05) ──


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_reconcile_concurrent_with_admission_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    cycle()
    claim()
    run, row = settled_run()
    # Room for the new run only once the settled row returns its capacity.
    returned = RUN_ESTIMATE.estimate_usd - (
        RUN_ESTIMATE.execution_bound_usd + RUN_ESTIMATE.post_run_liability_usd
    )
    _fill_to(RUN_DEBIT + RUN_DEBIT - returned / 2, NOW - HOUR)
    site_id = site().pk
    settling = threading.Event()
    real = reconcile_mod._try_settle  # pyright: ignore[reportPrivateUsage]

    def slow_settle(*args: Any, **kwargs: Any) -> bool:
        settled = real(*args, **kwargs)
        settling.set()
        time.sleep(0.5)  # hold the budget lock with the settlement uncommitted
        return settled

    monkeypatch.setattr(reconcile_mod, "_try_settle", slow_settle)

    def reconciler() -> bool:
        try:
            return _settle_read(run, "0.03", ELIGIBLE)
        finally:
            connection.close()

    def admitter() -> tuple[ReservationOutcome, float]:
        try:
            settling.wait(timeout=10)
            start = time.monotonic()
            outcome = reserve(WATCH, source_site_id=site_id, config=CONFIG, now=NOW)
            return outcome, time.monotonic() - start
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        settled = pool.submit(reconciler)
        admitted = pool.submit(admitter)
        assert settled.result()
        outcome, waited = admitted.result()
    # The admission waited on the lock and then saw the post-reconcile total.
    assert waited >= 0.3
    assert outcome.admitted
    assert _fresh(row).status == ReservationStatus.RECONCILED


def _provider_run_locks_held() -> bool:
    """Whether this backend holds a row-level lock mode on provider_run."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid()"
            " AND relation = 'provider_run'::regclass"
            " AND mode IN ('RowShareLock', 'RowExclusiveLock')"
        )
        row = cursor.fetchone()
    return bool(row and row[0])


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_latch_trip_never_requested_while_holding_provider_run_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held: list[bool] = []
    real = ledger.take_budget_lock

    def recording_lock() -> None:
        held.append(_provider_run_locks_held())
        real()

    monkeypatch.setattr(ledger, "take_budget_lock", recording_lock)
    monkeypatch.setattr(reconcile_mod, "take_budget_lock", recording_lock)
    # The storage units trip inside their own budget-locked attempt commits.
    monkeypatch.setattr(storage_cleanup, "take_budget_lock", recording_lock)
    cycle()
    fake = FakeRuns()
    # Every trip site: a delete cap, an orphaned start, a restart, an
    # unexpected component, a settlement, and an overrun.
    # Admitted two days back, so both storage deadlines (admission + 1 day) passed.
    capped, _ = settled_run(
        reserved_at=NOW - 2 * DAY,
        anchored=False,
        import_state="finalized",
        storage_cleanup_attempts=BUDGET.max_delete_attempts,
    )
    orphan = provider_run(site(), NOW - 2 * DAY)
    with override_settings(HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS=BUDGET.max_delete_attempts):
        asyncio.run(storage_cleanup.overdue_storage_unit(capped.pk, fake.client()))
        asyncio.run(storage_cleanup.overdue_storage_unit(orphan.pk, fake.client()))
    run, _ = settled_run()
    jobs._record_observation(run.pk, api_run(run.external_run_id or "", None, restart_count=2), NOW)  # pyright: ignore[reportPrivateUsage]
    _settle_read(run, "0.09", ELIGIBLE, usage_usd={"PROXY_SERPS": "0.01"})
    assert set(open_latches()) >= {
        LatchReason.DELETE_ATTEMPTS_EXHAUSTED,
        LatchReason.ORPHANED_START,
        LatchReason.START_OPTION_MISMATCH,
        LatchReason.UNEXPECTED_USAGE_COMPONENT,
        LatchReason.OVERRUN,
    }
    # Four budget-locked transactions (the last trips two reasons in one), and
    # none of them started while its backend held a provider_run row lock.
    assert held == [False, False, False, False]


# ── D-side hand-offs: import and storage trips (MS2-D-22, -32, -33) ──


def _import_row(fixture: dict[str, Any], **overrides: Any) -> ProviderRun:
    synthetic = SourceSite.objects.get_or_create(
        normalized_name=IMPORT_SITE, defaults={"name": "Synthetic"}
    )[0]
    row = import_provider_run_row(synthetic, fixture)
    if overrides:
        ProviderRun.objects.filter(pk=row.pk).update(**overrides)
        row.refresh_from_db()
    return row


def _import(row: ProviderRun, fake: Any) -> ImportState:
    client = ApifyClient(IMPORT_TOKEN, transport=httpx.MockTransport(fake))
    return asyncio.run(
        import_provider_run(row.pk, client=client, actor_name=IMPORT_ACTOR, resolver=NullResolver())
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_dataset_over_cap_trips_latch() -> None:
    fixture = import_fixture("complete")
    rows = len(fixture["datasetItems"])
    row = _import_row(fixture, max_items=rows - 1)
    _import(row, import_fake(fixture))
    assert open_latches() == [LatchReason.DATASET_OVER_CAP]
    assert ApifyBudgetLatch.objects.values_list("provider_run_id", flat=True).get() == row.pk  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_overrun_never_admits_repair_run_or_dataset_reread() -> None:
    cycle()
    claim()
    trip_latch(LatchReason.OVERRUN, config=CONFIG, now=NOW)
    # No repair run or probe: admission is denied.
    assert _reserve().reason == DenialReason.OVERRUN_LATCH
    # No dataset re-read: a stage-1 retry after a counted read is not attempted.
    fixture = import_fixture("complete")
    row = _import_row(fixture, dataset_read_count=1, kv_read_count=1)
    fake = import_fake(fixture)
    assert _import(row, fake) is ImportState.PENDING
    row.refresh_from_db()
    assert fake.dataset_requests == []
    assert row.dataset_read_count == 1
    assert row.stage_detail["blocked_by_overrun_latch"] is True
    assert row.next_attempt_at is not None and row.next_attempt_at > timezone.now()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_repeated_pre_commit_reads_are_capped_and_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reservation already covered MAX_DATASET_READS full reads: its
    # post-run read bound grows by exactly one read per allowed read.
    one_read = (
        estimate_run_cost(SHAPE, dataclasses.replace(BUDGET, max_dataset_reads=3)).components[
            "post_run_reads"
        ]
        - estimate_run_cost(SHAPE, dataclasses.replace(BUDGET, max_dataset_reads=2)).components[
            "post_run_reads"
        ]
    )
    assert one_read > 0
    assert RUN_ESTIMATE.components["post_run_reads"] >= 3 * one_read
    fixture = import_fixture("complete")
    row = _import_row(fixture)

    def lose_process(*_args: Any, **_kwargs: Any) -> Any:
        raise ImportCrash  # stage 1 loses its process before committing

    monkeypatch.setattr(importer, "persist_observations", lose_process)
    cap = BUDGET.max_dataset_reads or 0
    for attempt in range(1, cap + 1):
        with pytest.raises(ImportCrash):
            _import(row, import_fake(fixture))
        row.refresh_from_db()
        assert row.dataset_read_count == attempt  # counted before each read
    fake = import_fake(fixture)
    assert _import(row, fake) is ImportState.REJECTED
    row.refresh_from_db()
    assert row.stage_detail["reject_reason"] == "read_cap_exhausted"
    assert fake.dataset_requests == []  # the read after the cap is refused


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_delete_cap_exhausted_trips_latch() -> None:
    run, row = settled_run(
        anchored=False,
        import_state="finalized",
        storage_cleanup_attempts=(BUDGET.max_delete_attempts or 0) - 1,
    )
    fake = FakeRuns()  # every DELETE answers 500
    asyncio.run(storage_cleanup.cleanup_storage_unit(run.pk, fake.client()))
    run.refresh_from_db()
    assert run.storage_state == "delete_failed"
    assert open_latches() == [LatchReason.DELETE_ATTEMPTS_EXHAUSTED]
    # Never reconciled: it keeps its full estimate (MS2-D-32).
    assert _fresh(row).status == ReservationStatus.RESERVED


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_orphaned_start_trips_latch() -> None:
    orphan = provider_run(site(), NOW - 2 * DAY)  # no start response recorded
    asyncio.run(storage_cleanup.overdue_storage_unit(orphan.pk, FakeRuns().client()))
    orphan.refresh_from_db()
    assert orphan.orphaned_start_at is not None
    assert open_latches() == [LatchReason.ORPHANED_START]
    # Re-running the unit does not trip again (one open trip per condition).
    asyncio.run(storage_cleanup.overdue_storage_unit(orphan.pk, FakeRuns().client()))
    assert ApifyBudgetLatch.objects.count() == 1


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_barrier_transactions_stamp_final_charge_op_at_once() -> None:
    # Residual (a), through the real barrier commits: the import finalizes
    # first (no stamp), then verified deletion stamps the anchor.
    fixture = import_fixture("complete")
    row = _import_row(fixture)
    assert _import(row, import_fake(fixture)) is ImportState.FINALIZED
    row.refresh_from_db()
    assert row.final_charge_op_at is None

    def deletes(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client = ApifyClient(IMPORT_TOKEN, transport=httpx.MockTransport(deletes))
    asyncio.run(storage_cleanup.cleanup_storage_unit(row.pk, client))
    row.refresh_from_db()
    assert row.storage_state == "deleted"
    assert row.final_charge_op_at == row.storage_deleted_at


# ── Through the scheduler (apify_poll_tick, MS2-D-23 selectors 2 and 4) ──


def _tick(fake: FakeRuns, clock: Clock, cfg: object = CONFIG) -> TickReport:
    return asyncio.run(
        apify_poll_tick(
            resolver=NullResolver(),
            client_factory=fake.client,
            ledger_config=cfg,  # type: ignore[arg-type]
            clock=clock,
        )
    )


def _reconciled_run(
    fake: FakeRuns, *, total: str = "0.03", at: datetime = ELIGIBLE, **kw: Any
) -> tuple[ProviderRun, ApifySpendReservation]:
    run, row = settled_run(**kw)
    assert _settle_read(run, total, at)
    fake.totals[run.external_run_id or ""] = total
    return run, _fresh(row)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_poll_tick_selects_reconciled_cleaned_up_run_for_correction_monitoring() -> None:
    cycle()
    fake = FakeRuns()
    run, row = _reconciled_run(fake)
    assert run.storage_state == "deleted" and run.import_state == "finalized"
    assert row.next_usage_read_at is not None
    fake.totals[run.external_run_id or ""] = "0.04"  # a rising figure
    report = _tick(fake, Clock(row.next_usage_read_at))
    assert report.monitored == [row.pk]
    row = _fresh(row)
    assert row.settled_run_usage_usd == D("0.0400")
    assert row.correction_monitor_closed_at is None  # before the deadline


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_settlement_polls_do_not_move_completion_anchor() -> None:
    fake = FakeRuns()
    run, row = settled_run()
    fake.totals[run.external_run_id or ""] = "0.03"
    first = ANCHOR + timedelta(seconds=3610)
    assert _tick(fake, Clock(ANCHOR + HOUR), STABLE).reconciled == [run.pk]  # not yet eligible
    run.refresh_from_db()
    assert (run.run_poll_count, run.next_attempt_at) == (0, first)
    _tick(fake, Clock(first), STABLE)
    _tick(fake, Clock(ANCHOR + timedelta(seconds=3670)), STABLE)
    run.refresh_from_db()
    row = _fresh(row)
    assert (row.status, row.settlement_basis) == (ReservationStatus.RECONCILED, "stable_reads")
    assert run.run_poll_count == 2
    assert run.final_charge_op_at == ANCHOR  # no poll moved the anchor


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_over_cap_control_response_trips_latch() -> None:
    fake = FakeRuns()
    run, _ = settled_run(anchored=False, remote_status="RUNNING", finished_at=None)
    fake.huge.add(run.external_run_id or "")
    report = _tick(fake, Clock(NOW))
    assert report.failed == [run.pk]
    assert open_latches() == [LatchReason.API_RESPONSE_OVER_CAP]
    assert ApifyBudgetLatch.objects.values_list("provider_run_id", flat=True).get() == run.pk  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
# B below is an unattached runtime row standing in for a live run's capacity;
# with no grace it is never released as unattached across the eight days.
@override_settings(HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S=None)
def test_outage_spanning_correction_deadline_closing_read_applies_correction() -> None:
    c1 = cycle()
    claim()
    fake = FakeRuns()
    # Day 0: run A reserves $2 and settles at $0.50 (stable reads).
    run_a, a = settled_run(estimate=custom_estimate("2.00"))
    _settle_read(run_a, "0.50", ELIGIBLE, STABLE)
    assert _settle_read(run_a, "0.50", ELIGIBLE + timedelta(seconds=60), STABLE)
    a = _fresh(a)
    assert a.correction_monitor_until is not None
    day0 = a.reconciled_at or NOW
    # Run B reserves into the released capacity (the runtime cap is now full).
    open_row(ALLOCATION - (a.actual_usd or D(0)) - (a.monitoring_bound_usd or D(0)), day0)
    # The provider raises A to $1 on day 6; the poller is down from day 5 to
    # day 8, across the day-7 deadline.
    fake.totals[run_a.external_run_id or ""] = "0.50"
    clock = Clock(day0)
    while (a.next_usage_read_at or day0) < day0 + 5 * DAY:
        clock.at = a.next_usage_read_at or day0
        _tick(fake, clock, STABLE)
        a = _fresh(a)
    assert a.correction_monitor_closed_at is None
    fake.totals[run_a.external_run_id or ""] = "1.00"  # day 6, unseen
    report = _tick(fake, Clock(day0 + 8 * DAY), STABLE)  # the restarted poller
    assert report.monitored == [a.pk]
    a = _fresh(a)
    assert a.settled_run_usage_usd == D("1.0000")
    assert a.correction_monitor_closed_at == day0 + 8 * DAY
    closing_id = ApifySpendReservation.objects.values_list(
        "correction_closing_read_id", flat=True
    ).get(pk=a.pk)
    closing = ApifyUsageRead.objects.get(pk=closing_id)
    assert (closing.read_at, closing.usage_total_usd) == (day0 + 8 * DAY, D("1.00"))
    assert open_latches() == [LatchReason.POST_ADMISSION_INVARIANT_BREACH]
    assert report.released == []
    # Counted at $1 in the cycle A's charge interval touches.
    assert _runtime(c1) >= ALLOCATION + D("0.50")


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.parametrize("failure", ["transport", "missing", "null"])
def test_failed_closing_read_keeps_obligation_open_and_overdue(failure: str) -> None:
    cycle()
    fake = FakeRuns()
    run, row = _reconciled_run(fake)
    until = row.correction_monitor_until
    assert until is not None
    remote = run.external_run_id or ""
    if failure == "transport":
        fake.failing.add(remote)
    elif failure == "missing":
        fake.missing.add(remote)
    else:
        fake.totals[remote] = None
    after = until + DAY
    report = _tick(fake, Clock(after))
    assert report.monitored == [row.pk]
    row = _fresh(row)
    assert row.correction_monitor_closed_at is None
    assert row.next_usage_read_at is not None and row.next_usage_read_at > after  # backed off
    assert correction_close_overdue(row, after, CONFIG)
    # A later successful read closes it.
    fake.failing.clear()
    fake.missing.clear()
    fake.totals[remote] = "0.03"
    _tick(fake, Clock(row.next_usage_read_at))
    row = _fresh(row)
    assert row.correction_monitor_closed_at is not None
    assert not correction_close_overdue(row, after + DAY, CONFIG)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_closing_read_rollback_leaves_monitoring_open(monkeypatch: pytest.MonkeyPatch) -> None:
    cycle()
    fake = FakeRuns()
    run, row = _reconciled_run(fake)
    assert row.correction_monitor_until is not None
    fake.totals[run.external_run_id or ""] = "0.05"
    reads_before = ApifyUsageRead.objects.filter(reservation=row).count()

    def fail_after_append(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected after the read was appended")

    monkeypatch.setattr(reconcile_mod, "_settle_monitoring_allowance", fail_after_append)
    report = _tick(fake, Clock(row.correction_monitor_until + HOUR))
    assert report.failed == [row.pk]
    row = _fresh(row)
    # The read, the correction, and the closure rolled back together.
    assert ApifyUsageRead.objects.filter(reservation=row).count() == reads_before
    assert row.settled_run_usage_usd == RUN_ESTIMATE.execution_bound_usd
    closing_id = ApifySpendReservation.objects.values_list(
        "correction_closing_read_id", flat=True
    ).get(pk=row.pk)
    assert (row.correction_monitor_closed_at, closing_id) == (None, None)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_correction_monitoring_closes_only_after_successful_closing_read() -> None:
    cycle()
    fake = FakeRuns()
    _, row = _reconciled_run(fake)
    until = row.correction_monitor_until
    assert until is not None
    reads = 0
    while row.next_usage_read_at is not None and row.next_usage_read_at < until:
        _tick(fake, Clock(row.next_usage_read_at))
        row = _fresh(row)
        reads += 1
        assert row.correction_monitor_closed_at is None  # before the deadline, never
    # Spaced so at most MAX_CORRECTION_READS - 3 reads fall before the deadline.
    assert 0 < reads <= (BUDGET.max_correction_reads or 0) - 3
    assert row.next_usage_read_at == until  # the closing read is due at the deadline
    _tick(fake, Clock(until))
    row = _fresh(row)
    assert row.correction_monitor_closed_at == until


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_correction_deadline_not_moved_by_monitoring_reads() -> None:
    cycle()
    fake = FakeRuns()
    _, row = _reconciled_run(fake)
    until = row.correction_monitor_until
    for _ in range(3):
        at = row.next_usage_read_at
        assert at is not None
        _tick(fake, Clock(at))
        row = _fresh(row)
        # The read is a charge after last_charge_at: it moves only the
        # monitoring interval's stamp, never the deadline or the anchor.
        assert (row.correction_monitor_until, row.monitoring_charge_last_at) == (until, at)
    assert row.last_charge_at is not None and row.last_charge_at < (
        row.monitoring_charge_last_at or NOW
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_closing_read_after_cycle_boundary_debits_the_new_cycle() -> None:
    cycle()
    fake = FakeRuns()
    # Reconciled in cycle 1, three days before it ends: the deadline is in cycle 2.
    _, row = _reconciled_run(fake, at=C1_END - 3 * DAY)
    until = row.correction_monitor_until
    assert until is not None and C2_START < until < C2_END
    ApifySpendReservation.objects.filter(pk=row.pk).update(next_usage_read_at=until)
    _tick(fake, Clock(until))
    row = _fresh(row)
    assert row.correction_monitor_closed_at == until
    later = until + HOUR
    c2 = cycle(C2_START, C2_END)
    claim(C2_START)
    monitoring = row.monitoring_bound_usd or D(0)
    assert cycle_debits(c2, CONFIG)[0].runtime_committed_usd == monitoring
    # An admission that fits only without the allowance is denied in cycle 2.
    _fill_to(RUN_DEBIT + monitoring - TICK, later)
    assert _reserve(later).reason == DenialReason.CLASS_CAP


C4_START = datetime(2026, 12, 5, tzinfo=C1_START.tzinfo)
C4_END = datetime(2027, 1, 4, 23, 59, 59, 999000, tzinfo=C1_START.tzinfo)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_monitoring_allowance_carried_through_prolonged_outage() -> None:
    c1, c2, c3 = cycle(), cycle(C2_START, C2_END), cycle(C3_START, C3_END)
    c4 = cycle(C4_START, C4_END)
    fake = FakeRuns()
    run, row = _reconciled_run(fake)
    allowance = row.monitoring_bound_usd or D(0)
    # Down across two boundaries: no read yet, and a read is still possible.
    for at_cycle in (c2, c3, c4):
        assert _runtime(at_cycle) == allowance
    # The restarted poller in cycle 3 makes the last read the cap allows.
    ProviderRun.objects.filter(pk=run.pk).update(
        correction_read_count=(BUDGET.max_correction_reads or 0) - 1
    )
    fake.failing.add(run.external_run_id or "")  # it fails: the obligation stays open
    _tick(fake, Clock(C3_START + DAY))
    row = _fresh(row)
    assert row.correction_monitor_closed_at is None
    # After the cap no further call is possible: later cycles drop it, and the
    # cycles its reads touched keep it.
    assert _runtime(c4) == 0
    for at_cycle in (c2, c3):
        assert _runtime(at_cycle) == allowance
    assert _runtime(c1) == (row.actual_usd or D(0)) + allowance


# ── Operator rows (MS2-D-46) ──


def _build_row(reason: str = "rebuild") -> ApifySpendReservation:
    cycle()
    claim()
    outcome = reserve_operator(OperatorKind.BUILD, config=CONFIG, now=NOW, reason=reason)
    assert outcome.admitted
    return reserved(outcome.reservation_id)


@pytest.mark.django_db
def test_operator_reservation_has_no_provider_run() -> None:
    row = _build_row()
    assert (row.admission_class, row.operator_kind, row.provider_run) == ("operator", "build", None)
    assert row.reason == "rebuild"  # residual (d): the --reason is stored


@pytest.mark.django_db
def test_rebinding_a_different_build_id_is_refused() -> None:
    row = _build_row()
    assert bind_build(row.pk, "build-1", now=NOW)
    assert not bind_build(row.pk, "build-1", now=NOW)  # idempotent
    with pytest.raises(LedgerRefused, match="build_already_bound"):
        bind_build(row.pk, "build-2", now=NOW)
    assert _fresh(row).provider_build_id == "build-1"


@pytest.mark.django_db
def test_unsettled_operator_reservation_counts_at_bound() -> None:
    row = _build_row()
    debits = cycle_debits(ApifyBudgetCycle.objects.get(), CONFIG)[0]
    assert debits.operator_committed_usd == (row.estimate_usd or D(0)) + (
        row.monitoring_bound_usd or D(0)
    )


@pytest.mark.django_db
def test_build_row_without_build_id_stays_at_bound_and_never_reconciles() -> None:
    row = _build_row()
    assert row.pk not in select_build_settlement(NOW + 30 * DAY)
    assert plan_build_read(row.pk, NOW + 30 * DAY, CONFIG) is None
    assert _fresh(row).status == ReservationStatus.RESERVED


@pytest.mark.django_db
def test_inspection_settle_sets_last_charge_at_and_no_correction_obligation() -> None:
    claim(live_cycle_start())
    with override_settings(**priced(), HW_RADAR_APIFY_ENABLED=True):
        call_command("apify_operator_reserve", "--kind", "inspect", "--reason", "look at run")
    row = ApifySpendReservation.objects.get(operator_kind="inspect")
    out = StringIO()
    call_command("apify_operator_reserve", "--settle", str(row.pk), stdout=out)
    row = _fresh(row)
    assert row.status == ReservationStatus.RECONCILED
    assert row.actual_usd == row.estimate_usd  # never below the envelope
    assert row.last_charge_at == row.reconciled_at
    assert row.correction_monitor_until is None
    assert row.pk not in select_monitoring(timezone.now() + 30 * DAY, CONFIG)
    assert row.reason == "look at run"


@pytest.mark.django_db
def test_probe_settle_needs_dataset_id_and_verified_deletion() -> None:
    cycle()
    claim()
    outcome = reserve_operator(OperatorKind.PROBE, config=CONFIG, now=NOW, reason="R25 probe")
    assert outcome.admitted
    with pytest.raises(CommandError, match="probe_evidence_missing"):
        call_command("apify_operator_reserve", "--settle", str(outcome.reservation_id))
    with pytest.raises(CommandError, match="probe_evidence_missing"):
        call_command(
            "apify_operator_reserve",
            "--settle",
            str(outcome.reservation_id),
            "--probe-dataset-id",
            "ds-probe",
        )
    call_command(
        "apify_operator_reserve",
        "--settle",
        str(outcome.reservation_id),
        "--probe-dataset-id",
        "ds-probe",
        "--deletion-verified",
    )
    row = reserved(outcome.reservation_id)
    assert (row.status, row.probe_dataset_id) == (ReservationStatus.RECONCILED, "ds-probe")
    assert row.correction_monitor_until is None


@pytest.mark.django_db
def test_envelope_denial_from_unset_limit_is_recorded() -> None:
    # Residual (c): the denial is persisted, with the limits that were set.
    cycle()
    claim()
    unset = config(budget={"operator_inspect_max_bytes": None})
    outcome = reserve_operator(OperatorKind.INSPECT, config=unset, now=NOW, reason="look")
    assert not outcome.admitted
    row = reserved(outcome.reservation_id)
    assert (row.status, row.denial_reason, row.reason) == (
        ReservationStatus.DENIED,
        DenialReason.UNBOUNDED_COMPONENT,
        "look",
    )
    assert (row.envelope_max_items, row.envelope_max_bytes) == (1000, None)


@pytest.mark.django_db
def test_budget_reset_rejects_the_retired_discovery_option() -> None:
    # MS2-D-48 retired runtime cycle discovery with its owner reset.
    with pytest.raises(CommandError, match="--discovery"):
        call_command("apify_budget_reset", "--discovery", "--reason", "apify outage over")


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_operator_build_settles_from_build_cost() -> None:
    row = _build_row()
    fake = FakeRuns()
    fake.builds["build-9"] = "0.12"
    call_command("apify_operator_reserve", "--settle", str(row.pk), "--build-id", "build-9")
    # The command only binds (at wall-clock time); the poller reads, settles,
    # and stamps. The build finished long before, so both reads are eligible.
    row = _fresh(row)
    assert row.status == ReservationStatus.RESERVED
    bound_at = row.next_usage_read_at
    assert bound_at is not None
    # Finished two hours before the bind: past the eligibility delay, well
    # inside the finalize deadline, so only the stable-read predicate settles it.
    fake.build_finished["build-9"] = (bound_at - 2 * HOUR).isoformat()
    assert _tick(fake, Clock(bound_at), STABLE).builds == [row.pk]
    assert _fresh(row).status == ReservationStatus.USAGE_PROVISIONAL
    _tick(fake, Clock(bound_at + timedelta(seconds=60)), STABLE)
    row = _fresh(row)
    assert (row.status, row.settlement_basis) == (ReservationStatus.RECONCILED, "stable_reads")
    assert (row.settled_run_usage_usd, row.actual_usd) == (D("0.1200"), D("0.1200"))
    assert row.last_charge_at == row.reconciled_at  # later than the build's finishedAt
    assert row.run_poll_count == 2
    assert ApifyUsageRead.objects.filter(reservation=row, provider_run__isnull=True).count() == 2


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_restart_rereads_reconciled_build_and_applies_upward_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _build_row()
    bind_build(row.pk, "build-7", now=NOW)
    fake = FakeRuns()
    fake.builds["build-7"] = "0.30"
    eligible = FINISHED + timedelta(seconds=10) + GUARD
    _tick(fake, Clock(eligible))  # bound mode: one eligible read settles it
    row = _fresh(row)
    assert row.status == ReservationStatus.RECONCILED
    # Fill the operator allowance with a second build reservation.
    second = _build_row_again()
    assert second.admitted
    # A new poller process, from persisted state only, re-reads the build.
    monkeypatch.setattr(jobs, "PROCESS_STARTED_AT", (row.next_usage_read_at or NOW) + HOUR)
    fake.builds["build-7"] = "0.60"
    report = _tick(fake, Clock((row.next_usage_read_at or NOW) + 2 * HOUR))
    assert report.monitored == [row.pk]
    row = _fresh(row)
    assert row.settled_run_usage_usd == D("0.6000")
    # 0.60 + the second build's 0.4231 debit exceeds the 1.00 operator allowance.
    assert LatchReason.POST_ADMISSION_INVARIANT_BREACH in open_latches()


def _build_row_again() -> ReservationOutcome:
    # Inside the snapshot's 900 s freshness window.
    return reserve_operator(
        OperatorKind.BUILD, config=CONFIG, now=NOW + timedelta(minutes=10), reason="second"
    )


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_build_monitoring_read_after_cycle_boundary_is_debited() -> None:
    row = _build_row()
    c2 = cycle(C2_START, C2_END)
    bind_build(row.pk, "build-5", now=NOW)
    fake = FakeRuns()
    fake.builds["build-5"] = "0.20"
    _tick(fake, Clock(FINISHED + timedelta(seconds=10) + GUARD))
    row = _fresh(row)
    assert row.status == ReservationStatus.RECONCILED
    allowance = row.monitoring_bound_usd or D(0)
    ApifySpendReservation.objects.filter(pk=row.pk).update(next_usage_read_at=C2_START + DAY)
    report = _tick(fake, Clock(C2_START + DAY))
    assert report.monitored == [row.pk]
    row = _fresh(row)
    assert row.monitoring_charge_last_at == C2_START + DAY
    assert cycle_debits(c2, CONFIG)[0].operator_committed_usd == allowance
