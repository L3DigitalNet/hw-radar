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
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final

import httpx
import pytest
from asgiref.sync import sync_to_async
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
    open_latches,
    open_row,
    provider_run,
    settled_run,
    site,
)
from test_apify_poll_job import ACTOR_ID, LIVE, FakeApify
from test_apify_poll_job import _specs as synthetic_specs  # pyright: ignore[reportPrivateUsage]

from hw_radar.acquisition.apify import reconcile, report
from hw_radar.acquisition.apify.jobs import (
    BudgetDecision,
    BudgetRequest,
    StartStatus,
    TickReport,
    apify_poll_tick,
    start_provider_run,
)
from hw_radar.acquisition.apify.ledger import (
    AccountReader,
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

    async def admit(self, request: BudgetRequest, reader: AccountReader) -> BudgetDecision:
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
