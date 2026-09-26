"""MS-2 Slice D task D12: recovery probes dispatch by the selected provider (MS2-D-24).

recovery_probe_job is driven for real; for an `apify` source it reaches the
real start_provider_run, and the probe's outcome is applied by the real D10
importer through a real apify_poll_tick. Every Apify call is served by an
httpx.MockTransport (`FakeApify`), so no test touches the network.

Budget admission is bound to the test-only AllowAllAdmission below by
monkeypatching the start job the poller calls, except in the E8 tests, which
bind the real jobs.LedgerAdmission with the ledger_support settings and settle
the probe through the real reconcile unit (MS2-D-24 with the real ledger).
test_denied_actor_probe_starts_nothing_and_stays_paused runs the production
binding unpatched, whose unset prices deny before any call.
"""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import httpx
import ledger_support
import pytest
from django.test import override_settings
from django.utils import timezone
from ordering_support import listing, observe, record, sweep

from hw_radar.acquisition import sources
from hw_radar.acquisition.apify import importer
from hw_radar.acquisition.apify.client import ApifyClient
from hw_radar.acquisition.apify.contract import SyntheticCollectorInput
from hw_radar.acquisition.apify.jobs import (
    ActorRunSpec,
    BudgetAdmission,
    BudgetDecision,
    BudgetRequest,
    LedgerAdmission,
    TickReport,
    apify_poll_tick,
    start_provider_run,
)
from hw_radar.acquisition.contracts import NullResolver, ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.scheduling.apply import RunOutcome, apply_run_outcome
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.acquisition.scheduling.lifecycle import LifecycleEvent
from hw_radar.catalog.models import (
    ApifyBudgetCycle,
    ApifySpendReservation,
    LifecycleState,
    ProviderKind,
    ProviderRun,
    RunKind,
    SchedulingLane,
    ScopeSweepContinuity,
    ScraperRun,
    SourceConfig,
    SourceSite,
    SourceTier,
)
from hw_radar.catalog.models.provider import (
    AdmissionClass,
    ImportState,
    ReservationStatus,
    StorageState,
)
from hw_radar.poller import service

# transaction=True: the jobs write from sync_to_async threads.
# serialized_rollback=True keeps the migration-seeded SourceSite rows.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


@pytest.fixture(autouse=True)
def admitted_cells(admit: Callable[..., None]) -> None:
    # The production admission matrix admits nothing, and recovery_probe_job
    # skips a source with no admitted category; these tests exercise probe
    # mechanics, so the fixture sources they probe are admitted here.
    admit(("synthetic", "drive"), ("demo", "drive"))


FIXTURE_DIR: Final = Path(__file__).resolve().parents[1] / "fixtures" / "apify_contract" / "v1"
SITE: Final = "synthetic"
SCOPE: Final = "synthetic:hdd:catalog"
ACTOR_ID: Final = "acct~hw-radar-synthetic-collector"
TOKEN: Final = "apify_api_d12_test_token"
MEMORY_MB: Final = 256
TIMEOUT_S: Final = 120
ABSENT_KEY: Final = "syn-absent"

LIVE: Final = override_settings(HW_RADAR_APIFY_ENABLED=True, HW_RADAR_APIFY_ACTOR_ID=ACTOR_ID)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class AllowAllAdmission:
    """Test-only budget admission; production binds jobs.LedgerAdmission."""

    def __init__(self) -> None:
        self.requests: list[BudgetRequest] = []

    async def admit(self, request: BudgetRequest) -> BudgetDecision:
        self.requests.append(request)
        return BudgetDecision(True)


class FakeApify:
    """Serves the start, the run polls, and one contract fixture's storage.

    Every started run gets a fresh id; a poll answers `terminal_status` (the
    fixture's remoteStatus by default), and every run's dataset and OUTPUT are
    the served fixture's, so the probe imports through the real importer.
    """

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.terminal_status: str = fixture["remoteStatus"]
        self.requests: list[httpx.Request] = []
        self._started = 0

    def starts(self) -> int:
        return sum(1 for r in self.requests if r.method == "POST" and r.url.path.endswith("/runs"))

    def _run_body(self, run_id: str, status: str) -> dict[str, Any]:
        return {
            "data": {
                "id": run_id,
                "actId": "act-1",
                "status": status,
                "startedAt": self.fixture["startedAt"],
                "finishedAt": None,
                "buildId": "build-1",
                "buildNumber": "1.0.7",
                "defaultDatasetId": f"ds-{run_id}",
                "defaultKeyValueStoreId": f"kv-{run_id}",
                "options": {"memoryMbytes": MEMORY_MB, "timeoutSecs": TIMEOUT_S},
                "stats": {"restartCount": 0},
                "usageTotalUsd": None,
            }
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        parts = path.split("/")
        if request.method == "POST" and path == f"/v2/actors/{ACTOR_ID}/runs":
            self._started += 1
            return httpx.Response(201, json=self._run_body(f"probe-{self._started}", "READY"))
        if request.method == "GET" and parts[:3] == ["", "v2", "actor-runs"]:
            return httpx.Response(200, json=self._run_body(parts[3], self.terminal_status))
        if path.startswith("/v2/datasets/") and path.endswith("/items"):
            rows = self.fixture["datasetItems"]
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            return httpx.Response(
                200,
                json=rows[offset : offset + limit],
                headers={"X-Apify-Pagination-Total": str(len(rows))},
            )
        if path.startswith("/v2/key-value-stores/") and path.endswith("/records/OUTPUT"):
            output = self.fixture["output"]
            if output is None:
                return httpx.Response(404, json={"error": {"type": "record-not-found"}})
            return httpx.Response(200, json=output)
        return httpx.Response(500, json={"error": {"type": "unexpected", "message": path}})

    def client(self) -> ApifyClient:
        return ApifyClient(TOKEN, transport=httpx.MockTransport(self))


def _specs() -> dict[str, Any]:
    # Every fixture shares the `complete` fixture's admitted scope, so one
    # start input serves them all; only the served storage differs.
    spec = ActorRunSpec(
        input_model=SyntheticCollectorInput,
        run_input=_fixture("complete")["actorInput"],
        memory_mb=MEMORY_MB,
        timeout_s=TIMEOUT_S,
    )

    def factory(_config: SourceConfig, _kind: RunKind) -> ActorRunSpec:
        return spec

    return {SITE: factory}


def _bind_start(
    monkeypatch: pytest.MonkeyPatch, fake: FakeApify, admission: BudgetAdmission | None
) -> None:
    """Point the poller's start job at the fake, with `admission` (None: the production binding)."""
    monkeypatch.setattr(
        service,
        "start_provider_run",
        functools.partial(
            start_provider_run, admission=admission, run_specs=_specs(), client_factory=fake.client
        ),
    )


@pytest.fixture
def paused_actor_source() -> SourceConfig:
    # F5a creates this site with a management command, not a migration (MS2-D-42).
    site = SourceSite.objects.create(name="Synthetic", normalized_name=SITE)
    config = SourceConfig.objects.create(
        source_site=site,
        tier=SourceTier.T2_SPECIALIST,
        domain="synthetic.invalid",
        cadence_baseline_s=3600,
        cadence_ceiling_s=900,
        enabled=True,
        collection_provider=ProviderKind.APIFY,
        lifecycle_state=LifecycleState.PAUSED_PENDING_FIX,
    )
    config.lane_state(SchedulingLane.FULL)
    return config


@pytest.fixture
def outcomes(monkeypatch: pytest.MonkeyPatch) -> list[LifecycleEvent]:
    """Record every lifecycle event the importer applies (the probe outcome)."""
    seen: list[LifecycleEvent] = []

    def spy(config: SourceConfig, outcome: RunOutcome, **kwargs: Any) -> None:
        seen.append(outcome.event)
        apply_run_outcome(config, outcome, **kwargs)

    monkeypatch.setattr(importer, "apply_run_outcome", spy)
    return seen


def _registry() -> BucketRegistry:
    registry = BucketRegistry()
    registry.configure_source(SITE, rate_per_min=60, burst=5, now_s=0.0)
    return registry


def _probe(registry: BucketRegistry | None = None) -> None:
    _run(service.recovery_probe_job(registry or _registry()))


def _tick(fake: FakeApify, clock: ledger_support.Clock | None = None) -> TickReport:
    return _run(
        apify_poll_tick(
            resolver=NullResolver(),
            client_factory=fake.client,
            ledger_config=ledger_support.CONFIG,
            clock=clock,
        )
    )


def _state(site_key: str = SITE) -> LifecycleState:
    config = SourceConfig.objects.get(source_site__normalized_name=site_key)
    return LifecycleState(config.lifecycle_state)


def _seed_continuity(site: SourceSite, at: datetime) -> tuple[datetime, datetime]:
    """Give the lane and the probe's scope live continuity, plus an absent listing.

    `syn-absent` is in the probe's scope and not in any fixture, so a probe
    that were read as absence evidence would delist it. Returns the lane's and
    the scope row's continuous_since.
    """
    observe(site, at, [record(ABSENT_KEY, scope=SCOPE)])
    sweep(site, at, scope_key=SCOPE, seen={ABSENT_KEY}, delist=False)
    # Later than the scoped sweep, which breaks the NULL scope at its own time
    # (MS2-D-31); a NULL sweep at that same instant would stay broken.
    sweep(site, at + timedelta(hours=1), scope_key=None, seen={ABSENT_KEY}, delist=False)
    lane, scope = _continuity(site)
    assert lane is not None
    assert scope is not None
    return lane, scope


def _continuity(site: SourceSite) -> tuple[datetime | None, datetime | None]:
    config = SourceConfig.objects.get(source_site=site)
    scope = ScopeSweepContinuity.objects.get(source_site=site, collection_scope=SCOPE)
    return config.lane_state(SchedulingLane.FULL).continuous_since, scope.continuous_since


def _probe_row() -> ProviderRun:
    return ProviderRun.objects.get(run_kind=RunKind.PROBE)


# ── Dispatch ──────────────────────────────────────────────────────────────────


@LIVE
def test_local_success_cannot_clear_actor_provider_failure(
    paused_actor_source: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched: list[str] = []

    class SucceedingLocalAdapter:
        name = SITE
        site_key = SITE
        run_kind = RunKind.FULL
        expects_json = True

        async def fetch(self) -> RawBatch:
            fetched.append(SITE)
            return RawBatch(
                source=SITE,
                fetched_at=timezone.now(),
                items=[RawItem(url="https://synthetic.invalid/p", payload_json={"sku": "p"})],
            )

        def parse(self, batch: RawBatch) -> list[ParsedListing]:
            raise AssertionError("never reached")

    fake = FakeApify(_fixture("complete"))
    admission = AllowAllAdmission()
    _bind_start(monkeypatch, fake, admission)
    monkeypatch.setitem(sources.ADAPTERS, SITE, SucceedingLocalAdapter)

    _probe()

    assert fetched == []
    assert not ScraperRun.objects.filter(source_site=paused_actor_source.source_site).exists()
    # The probe went to the selected provider instead: one Actor start, as discovery.
    assert fake.starts() == 1
    [request] = admission.requests
    assert (request.run_kind, request.admission_class) == (RunKind.PROBE, AdmissionClass.DISCOVERY)
    # Started is not recovered: the outcome waits for the import.
    assert _state() is LifecycleState.PAUSED_PENDING_FIX


@LIVE
def test_budget_admitted_actor_probe_recovers_source(
    paused_actor_source: SourceConfig,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[LifecycleEvent],
) -> None:
    fake = FakeApify(_fixture("complete"))
    _bind_start(monkeypatch, fake, AllowAllAdmission())

    _probe()
    assert _state() is LifecycleState.PAUSED_PENDING_FIX
    report = _tick(fake)

    row = _probe_row()
    assert report.imported == [row.pk]
    assert (row.import_state, row.completeness) == (ImportState.FINALIZED, "complete")
    assert row.admission_class == AdmissionClass.DISCOVERY
    assert outcomes == [LifecycleEvent.PROBE_SUCCESS]
    assert _state() is LifecycleState.ACTIVE


@LIVE
def test_actor_probe_never_delists_or_touches_continuity(
    paused_actor_source: SourceConfig,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[LifecycleEvent],
) -> None:
    # `complete` is a complete sweep that omits syn-absent: as a FULL run it
    # would delist it. As a PROBE it must neither delist nor move continuity.
    fixture = _fixture("complete")
    site = paused_actor_source.source_site
    before = _seed_continuity(
        site, datetime.fromisoformat(fixture["startedAt"]) - timedelta(days=1)
    )
    scope_before = ScopeSweepContinuity.objects.get(source_site=site, collection_scope=SCOPE)
    fake = FakeApify(fixture)
    _bind_start(monkeypatch, fake, AllowAllAdmission())

    _probe()
    _tick(fake)

    row = _probe_row()
    assert row.import_state == ImportState.FINALIZED
    assert row.stage_detail["listings_delisted"] == 0
    bait = listing(site, ABSENT_KEY)
    assert (bait.delisted_at, bait.delist_reason) == (None, "")
    assert _continuity(site) == before
    scope_after = ScopeSweepContinuity.objects.get(source_site=site, collection_scope=SCOPE)
    assert (scope_after.continuity_broken_at, scope_after.last_complete_sweep_at) == (
        scope_before.continuity_broken_at,
        scope_before.last_complete_sweep_at,
    )
    assert outcomes == [LifecycleEvent.PROBE_SUCCESS]


@LIVE
@pytest.mark.parametrize(
    ("case", "fixture_name", "reject_reason"),
    [
        # OUTPUT on a contract major hw-radar does not know.
        ("invalid_output", "unknown-schema", "failed_run"),
        ("ambiguous_empty", "ambiguous-empty", "failed_run"),
        # A valid, complete run whose storage deadline passed before import.
        ("storage_deadline", "complete", "storage_deadline_passed"),
    ],
)
def test_rejected_probe_records_probe_failure_and_leaves_continuity_unchanged(
    paused_actor_source: SourceConfig,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[LifecycleEvent],
    case: str,
    fixture_name: str,
    reject_reason: str,
) -> None:
    fixture = _fixture(fixture_name)
    site = paused_actor_source.source_site
    before = _seed_continuity(
        site, datetime.fromisoformat(fixture["startedAt"]) - timedelta(days=1)
    )
    fake = FakeApify(fixture)
    _bind_start(monkeypatch, fake, AllowAllAdmission())

    _probe()
    if case == "storage_deadline":
        ProviderRun.objects.filter(run_kind=RunKind.PROBE).update(
            storage_cleanup_due_at=timezone.now() - timedelta(seconds=1)
        )
    _tick(fake)

    row = _probe_row()
    assert row.import_state == ImportState.REJECTED
    assert row.stage_detail["reject_reason"] == reject_reason
    assert outcomes == [LifecycleEvent.PROBE_FAILURE]
    assert _continuity(site) == before
    assert listing(site, ABSENT_KEY).delisted_at is None
    assert _state() is LifecycleState.PAUSED_PENDING_FIX


@LIVE
def test_partial_failure_probe_keeps_source_paused(
    paused_actor_source: SourceConfig,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[LifecycleEvent],
) -> None:
    # partial_failure imports (it is not a `failed` classification) but is
    # not a probe success: only complete or truncated recovers (MS2-D-24).
    fake = FakeApify(_fixture("partial-with-errors"))
    _bind_start(monkeypatch, fake, AllowAllAdmission())

    _probe()
    _tick(fake)

    row = _probe_row()
    assert (row.import_state, row.completeness) == (ImportState.FINALIZED, "partial_failure")
    assert outcomes == [LifecycleEvent.PROBE_FAILURE]
    assert _state() is LifecycleState.PAUSED_PENDING_FIX


@LIVE
def test_truncated_probe_recovers_source(
    paused_actor_source: SourceConfig,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[LifecycleEvent],
) -> None:
    fake = FakeApify(_fixture("truncated-by-pages"))
    _bind_start(monkeypatch, fake, AllowAllAdmission())

    _probe()
    _tick(fake)

    assert _probe_row().completeness == "truncated"
    assert outcomes == [LifecycleEvent.PROBE_SUCCESS]
    assert _state() is LifecycleState.ACTIVE


@LIVE
def test_denied_actor_probe_starts_nothing_and_stays_paused(
    paused_actor_source: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeApify(_fixture("complete"))
    # No admission bound: the production LedgerAdmission, whose unset prices deny.
    _bind_start(monkeypatch, fake, None)

    _probe()

    assert fake.requests == []
    assert not ProviderRun.objects.exists()
    assert not ScraperRun.objects.exists()
    assert _state() is LifecycleState.PAUSED_PENDING_FIX
    # E5: the probe denial is a ledger row, not only a log line (MS2-D-17).
    [denial] = ApifySpendReservation.objects.all()
    assert (denial.status, denial.admission_class, denial.denial_reason) == (
        ReservationStatus.DENIED,
        AdmissionClass.DISCOVERY,
        "pricing_unverified",
    )


@LIVE
def test_one_outstanding_probe_per_source(
    paused_actor_source: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeApify(_fixture("partial-with-errors"))
    admission = AllowAllAdmission()
    _bind_start(monkeypatch, fake, admission)
    registry = _registry()

    _probe(registry)
    _probe(registry)  # the first probe is still running remotely

    assert fake.starts() == 1
    assert len(admission.requests) == 1
    assert ProviderRun.objects.filter(run_kind=RunKind.PROBE).count() == 1

    # Once the first probe's import is decided (a PROBE_FAILURE here, so the
    # source is still paused), the next daily probe may start another.
    _tick(fake)
    assert _probe_row().import_state == ImportState.FINALIZED
    _probe(registry)
    assert fake.starts() == 2
    assert ProviderRun.objects.filter(run_kind=RunKind.PROBE).count() == 2


def test_local_provider_probe_path_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # A paused `local` source still replays its own adapter and recovers at once;
    # the Actor start job is never consulted for it.
    class ProbeAdapter:
        name = "demo"
        site_key = "demo"
        run_kind = RunKind.FULL
        expects_json = True

        async def fetch(self) -> RawBatch:
            return RawBatch(
                source="demo",
                fetched_at=timezone.now(),
                items=[RawItem(url="https://demo.invalid/p", payload_json={"sku": "p"})],
            )

        def parse(self, batch: RawBatch) -> list[ParsedListing]:
            return [
                ParsedListing(
                    source_listing_key="probe-sku",
                    url="https://demo.invalid/p",
                    title="Probe 8TB",
                    price=Decimal("99.99"),
                )
            ]

    async def no_actor_start(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a local source must never start an Actor run")

    monkeypatch.setattr(service, "start_provider_run", no_actor_start)
    monkeypatch.setitem(sources.ADAPTERS, "demo", ProbeAdapter)
    SourceConfig.objects.filter(source_site__normalized_name="demo").update(
        enabled=True, lifecycle_state=LifecycleState.PAUSED_PENDING_FIX
    )
    registry = BucketRegistry()
    registry.configure_source("demo", rate_per_min=60.0, burst=3, now_s=0.0)

    _probe(registry)

    assert _state("demo") is LifecycleState.ACTIVE
    run = ScraperRun.objects.get(source_site__normalized_name="demo")
    assert run.run_kind == RunKind.PROBE
    assert not ProviderRun.objects.exists()


# ── E8: the probe under the real ledger (MS2-D-24, -17, -41) ─────────────────


class LedgerFakeApify(FakeApify):
    """FakeApify plus what the real ledger path calls: storage deletes.

    Runs report a small allowlisted usage so bound-mode settlement has an
    eligible non-null read, and storage deletes succeed. It serves no account
    endpoint: the runtime reads none (MS2-D-48), and a request for one falls
    through to FakeApify, which fails it.
    """

    USAGE: Final = "0.001"

    def _run_body(self, run_id: str, status: str) -> dict[str, Any]:
        body = super()._run_body(run_id, status)
        body["data"]["usageTotalUsd"] = float(self.USAGE)
        return body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            self.requests.append(request)
            return httpx.Response(204)
        return super().__call__(request)


def _live_cycle() -> ApifyBudgetCycle:
    """The configured live cycle covering the real now, with this ledger's authority."""
    cycle = ledger_support.live_cycle()
    ledger_support.claim(cycle.cycle_start)
    return cycle


@LIVE
def test_budget_admitted_actor_probe_recovers_source_with_ledger(
    paused_actor_source: SourceConfig,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[LifecycleEvent],
) -> None:
    cycle = _live_cycle()
    fake = LedgerFakeApify(_fixture("complete"))
    _bind_start(monkeypatch, fake, LedgerAdmission(config=ledger_support.live_config()))

    _probe()

    # The start is the first request: no account read precedes it (MS2-D-48).
    assert [r.method for r in fake.requests][:1] == ["POST"]
    cycle.refresh_from_db()
    assert cycle.account_read_count == 0
    row = _probe_row()
    resv = ApifySpendReservation.objects.get(provider_run=row)
    assert (resv.status, resv.admission_class, resv.source_site) == (
        ReservationStatus.RESERVED,
        AdmissionClass.DISCOVERY,
        paused_actor_source.source_site,
    )

    _tick(fake)  # polls the terminal run, then imports it
    assert _state() is LifecycleState.ACTIVE
    assert outcomes == [LifecycleEvent.PROBE_SUCCESS]
    _tick(fake)  # deletes storage: the second settlement barrier
    row.refresh_from_db()
    assert (row.import_state, row.storage_state) == (ImportState.FINALIZED, StorageState.DELETED)
    assert row.final_charge_op_at is not None
    # Past the settle delay and the cycle-boundary guard: one eligible read settles.
    settle_at = ledger_support.Clock(row.final_charge_op_at + 2 * ledger_support.HOUR)
    report = _tick(fake, settle_at)

    assert report.reconciled == [row.pk]
    resv.refresh_from_db()
    assert resv.status == ReservationStatus.RECONCILED
    assert resv.settlement_basis == "bound"
    assert resv.actual_usd is not None and resv.estimate_usd is not None
    assert resv.actual_usd <= resv.estimate_usd
    assert ledger_support.open_latches() == []


@LIVE
def test_probe_denied_when_discovery_exhausted(
    paused_actor_source: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    cycle = _live_cycle()
    budget = ledger_support.BUDGET
    discovery_cap = ledger_support.ALLOCATION - (budget.watch_refresh_reserve_usd or Decimal(0))
    # Leaves $0.0001 of the discovery class: no probe fits, while the
    # watch_refresh reserve above it is untouched (discovery degrades first).
    ledger_support.open_row(discovery_cap - Decimal("0.0001"), cycle.cycle_start)
    fake = LedgerFakeApify(_fixture("complete"))
    _bind_start(monkeypatch, fake, LedgerAdmission(config=ledger_support.live_config()))

    _probe()

    assert fake.requests == []  # no account read, and no start
    assert not ProviderRun.objects.exists()
    denial = ApifySpendReservation.objects.get(status=ReservationStatus.DENIED)
    assert (denial.admission_class, denial.denial_reason, denial.source_site) == (
        AdmissionClass.DISCOVERY,
        "class_cap",
        paused_actor_source.source_site,
    )
    assert _state() is LifecycleState.PAUSED_PENDING_FIX
