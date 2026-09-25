"""Two ledger crash windows found by the Slice E verifier (MS2-D-26, -32, -33, -34, -45).

d2: an `orphaned_start` or `delete_attempts_exhausted` latch trip used to be
committed after, and separately from, the row mark that justifies it. Once the
mark committed the selectors dropped the row, so a process loss between the
two commits left the latch untripped forever. The trip now commits with the
mark, and every apify-poll tick re-detects a condition that never tripped.

d1: an admitted runtime reservation that never got its provider_run (a crash
between `ledger.reserve` and `jobs._create_run`, or a `_create_run` that rolled
back) counted at its estimate forever and blocked the handoff export. The tick
now releases it after HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S, and a
start whose reservation was released under it is refused before the start
request.

Every sweep function is reached through its module (not imported by name) so
that, against code without it, each test fails on its own rather than the
whole module failing to import.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final

import httpx
import ledger_support
import pytest
from asgiref.sync import sync_to_async
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from ledger_support import (
    BUDGET,
    CONFIG,
    DAY,
    HOUR,
    LEDGER_B,
    NOW,
    Clock,
    FakeRuns,
    claim,
    config,
    cycle,
    live_config,
    live_cycle,
    open_latches,
    open_row,
    provider_run,
    settled_run,
    site,
)
from test_apify_poll_job import ACTOR_ID, LIVE, FakeApify
from test_apify_poll_job import _specs as synthetic_specs  # pyright: ignore[reportPrivateUsage]

from hw_radar.acquisition.apify import jobs, ledger, reconcile, report, storage_cleanup
from hw_radar.acquisition.apify.budget import OperatorKind
from hw_radar.acquisition.apify.jobs import (
    BudgetDecision,
    BudgetRequest,
    LedgerAdmission,
    StartStatus,
    TickReport,
    apify_poll_tick,
    start_provider_run,
)
from hw_radar.acquisition.apify.ledger import (
    LatchReason,
    LedgerRefused,
    clear_latch,
    export_handoff,
)
from hw_radar.acquisition.contracts import NullResolver
from hw_radar.catalog.models import (
    ApifyBudgetLatch,
    ApifySpendReservation,
    ProviderKind,
    ProviderRun,
    ReservationStatus,
    SourceConfig,
    SourceSite,
    SourceTier,
)
from hw_radar.catalog.models.provider import AdmissionClass, StorageState

# transaction=True: the tick writes from sync_to_async threads.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

D = Decimal
GRACE: Final = timedelta(seconds=900)  # the settings default
CAP: Final = BUDGET.max_delete_attempts or 0
EXPORTER: Final = config(budget={"enabled": False})


class Crash(BaseException):
    """Simulated process loss: not an Exception, so no unit handler absorbs it."""


def _tick(clock: Clock, fake: FakeRuns | None = None) -> TickReport:
    return asyncio.run(
        apify_poll_tick(
            resolver=NullResolver(),
            client_factory=(fake or FakeRuns()).client,
            ledger_config=CONFIG,
            clock=clock,
        )
    )


def _crash_at_first_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lose the process at the first latch-row insert, then behave normally.

    The insert is the one step every trip path shares, whichever transaction
    it runs in, so the same injection lands between the mark and the trip
    when they commit separately, and inside the mark's own commit when not.
    """
    manager = ApifyBudgetLatch.objects
    real: Callable[..., Any] = manager.create
    fired: list[bool] = []

    def create(**kwargs: Any) -> Any:
        if not fired:
            fired.append(True)
            raise Crash("process lost at the latch trip")
        return real(**kwargs)

    monkeypatch.setattr(manager, "create", create)


def _trips(run: ProviderRun, reason: LatchReason) -> int:
    return ApifyBudgetLatch.objects.filter(provider_run=run, reason=reason).count()


# ── d2: latch trips lost to a process loss ──────────────────────────────────


@override_settings(HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS=CAP)
def test_orphaned_start_trip_lost_to_process_loss_is_tripped_by_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan = provider_run(site(), NOW - 2 * DAY)  # no start response; deadline passed
    _crash_at_first_trip(monkeypatch)

    with pytest.raises(Crash):
        _tick(Clock(NOW))

    _tick(Clock(NOW + HOUR))
    orphan.refresh_from_db()
    assert orphan.orphaned_start_at is not None
    assert open_latches() == [LatchReason.ORPHANED_START]
    _tick(Clock(NOW + 2 * HOUR))
    assert _trips(orphan, LatchReason.ORPHANED_START) == 1


@override_settings(HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS=CAP)
def test_delete_failed_trip_lost_to_process_loss_is_tripped_by_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Terminal, import finalized, one attempt left; FakeRuns answers every
    # DELETE 500, so this tick's attempt is the one that reaches the cap.
    run, _ = settled_run(anchored=False, import_state="finalized", storage_cleanup_attempts=CAP - 1)
    _crash_at_first_trip(monkeypatch)

    with pytest.raises(Crash):
        _tick(Clock(NOW))

    repaired = _tick(Clock(NOW + HOUR))
    run.refresh_from_db()
    assert run.storage_state == StorageState.DELETE_FAILED
    assert run.storage_cleanup_attempts == CAP  # the lost attempt stays spent
    assert open_latches() == [LatchReason.DELETE_ATTEMPTS_EXHAUSTED]
    assert repaired.cleanup == []  # at the cap: no further attempt is sent
    _tick(Clock(NOW + 2 * HOUR))
    assert _trips(run, LatchReason.DELETE_ATTEMPTS_EXHAUSTED) == 1


@pytest.mark.parametrize("condition", ["orphaned", "delete_failed"])
def test_stranded_row_without_trip_is_tripped_once_and_owner_reset_is_final(
    condition: str,
) -> None:
    # The persisted state the earlier two-commit code left behind.
    if condition == "orphaned":
        row = provider_run(site(), NOW - 2 * DAY, orphaned_start_at=NOW - DAY)
        reason = LatchReason.ORPHANED_START
    else:
        row, _ = settled_run(
            anchored=False,
            import_state="finalized",
            storage_state=StorageState.DELETE_FAILED,
            storage_cleanup_attempts=CAP,
        )
        reason = LatchReason.DELETE_ATTEMPTS_EXHAUSTED

    assert _tick(Clock(NOW)).latch_repaired == [row.pk]
    assert open_latches() == [reason]
    assert _tick(Clock(NOW + HOUR)).latch_repaired == []
    assert _trips(row, reason) == 1

    # R21: the owner cleans up and resets. The row keeps its terminal state,
    # and a later tick must not re-trip it, or no reset could ever hold.
    assert clear_latch("owner cleaned up remotely", now=NOW + 2 * HOUR) == 1
    assert _tick(Clock(NOW + 3 * HOUR)).latch_repaired == []
    assert open_latches() == []
    assert _trips(row, reason) == 1


# ── d1: an admitted reservation that never got a provider_run ───────────────


def test_unattached_reservation_released_after_grace_not_before() -> None:
    fresh = open_row(D("0.40"), NOW - GRACE + timedelta(seconds=1))
    stale = open_row(D("0.30"), NOW - GRACE - timedelta(seconds=1))
    attached = open_row(D("0.20"), NOW - DAY, run=provider_run(site(), NOW - HOUR))

    assert _tick(Clock(NOW)).released == [stale.pk]
    assert ApifySpendReservation.objects.get(pk=fresh.pk).status == ReservationStatus.RESERVED
    assert _tick(Clock(NOW + timedelta(seconds=2))).released == [fresh.pk]

    for pk in (fresh.pk, stale.pk):
        row = ApifySpendReservation.objects.get(pk=pk)
        assert row.status == ReservationStatus.RELEASED
        assert row.actual_usd == D(0)
        assert row.reason == reconcile.UNATTACHED_RESERVATION
    assert ApifySpendReservation.objects.get(pk=attached.pk).status == ReservationStatus.RESERVED


def test_operator_rows_are_never_released_as_unattached() -> None:
    # Operator rows have no provider_run by design (MS2-D-46), at any age.
    build = open_row(
        D("0.41"),
        NOW - 30 * DAY,
        admission_class=AdmissionClass.OPERATOR,
        operator_kind="build",
    )

    assert _tick(Clock(NOW)).released == []
    assert reconcile.release_unattached_reservations(NOW + 365 * DAY, 0) == []
    assert ApifySpendReservation.objects.get(pk=build.pk).status == ReservationStatus.RESERVED


@override_settings(HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S=None)
def test_unset_grace_releases_nothing() -> None:
    row = open_row(D("0.40"), NOW - 30 * DAY)

    assert _tick(Clock(NOW)).released == []
    assert ApifySpendReservation.objects.get(pk=row.pk).status == ReservationStatus.RESERVED


def test_released_unattached_row_no_longer_blocks_handoff_export() -> None:
    cycle()
    claim()
    open_row(D("0.40"), NOW - DAY)
    with pytest.raises(LedgerRefused) as refused:
        export_handoff(LEDGER_B, config=EXPORTER, now=NOW)
    assert refused.value.code == "open_reservation"

    _tick(Clock(NOW))

    exported = export_handoff(LEDGER_B, config=EXPORTER, now=NOW)
    assert exported.record["drain"] == {
        "open_reservations": 0,
        "open_monitoring": 0,
        "obligations": [],
    }


def test_spend_report_lists_released_rows_and_not_as_unsettled() -> None:
    cycle()
    row = open_row(D("0.40"), NOW - DAY)
    _tick(Clock(NOW))

    built = report.build_report(config=CONFIG, now=NOW)

    assert [(r.reservation_id, r.reasons, r.amount) for r in built.released] == [
        (row.pk, (reconcile.UNATTACHED_RESERVATION,), D(0))
    ]
    assert built.unsettled == []
    text = report.render_report(built)
    assert f"#{row.pk} watch_refresh e3ledger" in text.split("Released rows:")[1]


# ── d1: the start side of the release race ──────────────────────────────────


@pytest.fixture
def synthetic() -> SourceConfig:
    source = SourceSite.objects.create(name="Synthetic", normalized_name="synthetic")
    created = SourceConfig.objects.create(
        source_site=source,
        tier=SourceTier.T2_SPECIALIST,
        domain="synthetic.invalid",
        cadence_baseline_s=3600,
        cadence_ceiling_s=900,
        enabled=True,
        collection_provider=ProviderKind.APIFY,
    )
    return SourceConfig.objects.select_related("source_site").get(pk=created.pk)


class ReleasedUnderStart:
    """Admits, then lets the release unit win: the start stalled past the grace."""

    def __init__(self) -> None:
        self.reservation_id: int | None = None

    async def admit(self, request: BudgetRequest) -> BudgetDecision:
        def reserve_then_release() -> int:
            now = timezone.now()
            row = open_row(D("0.40"), now - 2 * GRACE)
            assert reconcile.release_unattached_reservations(now, int(GRACE.total_seconds())) == [
                row.pk
            ]
            return row.pk

        self.reservation_id = await sync_to_async(reserve_then_release)()
        return BudgetDecision(True, reservation_id=self.reservation_id)


@LIVE
def test_start_whose_reservation_was_released_is_refused_and_sends_nothing(
    synthetic: SourceConfig,
) -> None:
    fake = FakeApify()
    admission = ReleasedUnderStart()

    result = asyncio.run(
        start_provider_run(
            synthetic, admission=admission, run_specs=synthetic_specs(), client_factory=fake.client
        )
    )

    assert (result.status, result.reason, result.provider_run_id) == (
        StartStatus.DENIED,
        "reservation_not_open",
        None,
    )
    assert fake.paths("POST", f"/v2/actors/{ACTOR_ID}/") == []
    assert not ProviderRun.objects.exists()  # _create_run rolled its row back
    released = ApifySpendReservation.objects.get(pk=admission.reservation_id)
    assert released.status == ReservationStatus.RELEASED
    assert released.provider_run is None


def test_fake_apify_start_route_is_the_one_asserted_above() -> None:
    # Guards the assertion above against vacuity: the path it checks is the
    # one a real start request is routed to.
    fake = FakeApify()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        client.post(f"https://api.apify.com/v2/actors/{ACTOR_ID}/runs")
    assert fake.paths("POST", f"/v2/actors/{ACTOR_ID}/") == [f"/v2/actors/{ACTOR_ID}/runs"]


# ── E9.3: a 402 whose latch trip was lost between commits (MS2-D-48, R12-01) ──
#
# The start job records the 402 in stage_detail (one commit), then trips
# `account_limit_refused` (a second, budget-locked commit: ED-05 forbids the
# budget lock under the provider-row lock). A process lost between the two
# leaves the 402 recorded and the latch open. `jobs.trip_start_refusal` is
# looked up through the module, so replacing it injects the loss exactly there;
# raising=False keeps the injection valid against code without it.

PAYMENT_REQUIRED: Final = {
    "error": {"type": "x402-payment-required", "message": "usage limit exceeded"}
}


def _live_start(synthetic: SourceConfig, fake: FakeApify) -> Any:
    return asyncio.run(
        start_provider_run(
            synthetic,
            admission=LedgerAdmission(config=live_config()),
            run_specs=synthetic_specs(),
            client_factory=fake.client,
        )
    )


def _refusing_fake() -> FakeApify:
    fake = FakeApify()
    fake.start_response = lambda _r: httpx.Response(402, json=PAYMENT_REQUIRED)
    return fake


def _start_losing_the_trip(synthetic: SourceConfig, monkeypatch: pytest.MonkeyPatch) -> ProviderRun:
    """A 402 start whose process is lost after _record_start_error committed."""
    claim(live_cycle().cycle_start)

    def lost(_provider_run_id: int) -> bool:
        raise Crash

    monkeypatch.setattr(jobs, "trip_start_refusal", lost, raising=False)
    with contextlib.suppress(Crash):
        _live_start(synthetic, _refusing_fake())
    monkeypatch.undo()
    row = ProviderRun.objects.get()
    assert row.stage_detail["start_error"] == {
        "type": "ApifyApiError",
        "status_code": 402,
        "error_type": "x402-payment-required",
    }
    return row


def _refusal_trips(row: ProviderRun) -> list[bool]:
    """One entry per account_limit_refused trip of `row`: True while it is open."""
    return [
        cleared is None
        for cleared in ApifyBudgetLatch.objects.filter(
            reason=LatchReason.ACCOUNT_LIMIT_REFUSED, provider_run=row
        ).values_list("cleared_at", flat=True)
    ]


def _live_tick() -> TickReport:
    return asyncio.run(
        apify_poll_tick(
            resolver=NullResolver(), client_factory=FakeRuns().client, ledger_config=live_config()
        )
    )


@LIVE
def test_402_trip_lost_between_commits_is_repaired_before_next_admission(
    synthetic: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _start_losing_the_trip(synthetic, monkeypatch)
    resv = ApifySpendReservation.objects.get(provider_run=row)
    before = (resv.status, resv.estimate_usd, row.pk)

    # No tick in between: the next admissions of both classes repair it first.
    other = site("other-shop")
    runtime = ledger.reserve(ledger_support.WATCH, source_site_id=other.pk, config=live_config())
    operator = ledger.reserve_operator(OperatorKind.INSPECT, config=live_config())

    assert _refusal_trips(row) == [True]
    assert ApifyBudgetLatch.objects.count() == 1
    assert (runtime.admitted, runtime.reason) == (False, "overrun_latch")
    assert (operator.admitted, operator.reason) == (False, "overrun_latch")
    assert ApifySpendReservation.objects.filter(status=ReservationStatus.RESERVED).count() == 1
    resv.refresh_from_db()
    attached = ApifySpendReservation.objects.filter(pk=resv.pk, provider_run=row).exists()
    assert attached and (resv.status, resv.estimate_usd, row.pk) == before


@LIVE
def test_402_trip_lost_between_commits_is_repaired_by_the_next_tick(
    synthetic: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _start_losing_the_trip(synthetic, monkeypatch)

    _live_tick()

    assert _refusal_trips(row) == [True]


@LIVE
def test_owner_cleared_402_trip_is_never_retripped(
    synthetic: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _start_losing_the_trip(synthetic, monkeypatch)
    _live_tick()
    call_command("apify_budget_reset", "--reason", "account limit re-verified")

    # The cleared trip is the record that the 402 was handled (R21 rule).
    outcome = ledger.reserve(
        ledger_support.WATCH, source_site_id=site("other-shop").pk, config=live_config()
    )
    _live_tick()

    assert _refusal_trips(row) == [False]
    assert outcome.reason != "overrun_latch"
    assert not ledger.latch_tripped()


@LIVE
def test_estimator_bump_does_not_clear_account_limit_refused(
    synthetic: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _start_losing_the_trip(synthetic, monkeypatch)
    _live_tick()
    ledger.trip_latch(LatchReason.OVERRUN, config=live_config())
    bumped = dataclasses.replace(
        live_config(), budget=dataclasses.replace(live_config().budget, estimator_version="2")
    )

    outcome = ledger.reserve(
        ledger_support.WATCH, source_site_id=site("other-shop").pk, config=bumped
    )

    # The bump clears the overrun trip (a replaced price bound) but not the
    # hard-limit refusal, which says nothing about the estimator.
    assert _refusal_trips(row) == [True]
    assert ApifyBudgetLatch.objects.get(reason=LatchReason.OVERRUN).cleared_at is not None
    assert outcome.reason == "overrun_latch"


@LIVE
def test_delayed_402_callback_after_repair_and_owner_clear_does_not_retrip(
    synthetic: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim(live_cycle().cycle_start)

    def delayed(provider_run_id: int) -> bool:
        # The callback stalls after _record_start_error committed; meanwhile
        # a tick's repair trips the latch and the owner clears it.
        storage_cleanup.trip_stranded_latches()
        call_command("apify_budget_reset", "--reason", "account limit re-verified")
        return ledger.trip_start_refusal(provider_run_id)  # pyright: ignore[reportAttributeAccessIssue]

    monkeypatch.setattr(jobs, "trip_start_refusal", delayed, raising=False)
    result = _live_start(synthetic, _refusing_fake())

    assert (result.status, result.reason) == (StartStatus.START_FAILED, "account_limit_refused")
    row = ProviderRun.objects.get()
    assert _refusal_trips(row) == [False]
    assert not ledger.latch_tripped()
