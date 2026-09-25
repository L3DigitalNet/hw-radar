# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# APScheduler 3.x is untyped (see tests/unit/test_poller.py).
"""MS-2 Slice D task D5: the Actor start job and the `apify-poll` selectors.

Every Apify call is served by an httpx.MockTransport (`FakeApify`), so no test
touches the network. Budget admission is bound to the test-only AllowAll below
wherever a start must reach the wire; production stays DenyAllAdmission, which
test_production_defaults_never_send_a_start_request pins.

The restart tests at the end (named in plan D10) inject process loss with a
BaseException raised from a patched importer stage, exactly as
test_apify_import.py does, then run a fresh tick as the restarted poller.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import httpx
import ledger_support
import pytest
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from hw_radar.acquisition.apify import importer, jobs
from hw_radar.acquisition.apify.budget import OperatorKind
from hw_radar.acquisition.apify.client import ApifyClient, ApifyRun, RunOptions
from hw_radar.acquisition.apify.contract import SyntheticCollectorInput
from hw_radar.acquisition.apify.jobs import (
    ActorRunSpec,
    BudgetDecision,
    BudgetRequest,
    StartStatus,
    TickReport,
    apify_poll_tick,
    select_active,
    select_outstanding,
    select_overdue,
    start_mismatches,
    start_provider_run,
)
from hw_radar.acquisition.apify.ledger import reserve_operator
from hw_radar.acquisition.contracts import NullResolver
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.acquisition.sources import ADAPTERS
from hw_radar.catalog.models import (
    Category,
    Listing,
    OfferSnapshot,
    ProviderKind,
    ProviderRun,
    RawPayload,
    RunKind,
    RunStatus,
    ScraperRun,
    SourceConfig,
    SourceSite,
    SourceTier,
)
from hw_radar.catalog.models.provider import (
    AdmissionClass,
    ApifyBudgetLatch,
    ApifySpendReservation,
    ImportState,
    ReservationStatus,
    StorageState,
)
from hw_radar.poller import service

# transaction=True: the jobs write from sync_to_async threads, and the
# committed-before-send checks read from a second connection.
# serialized_rollback=True keeps the migration-seeded SourceSite rows.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

FIXTURE_DIR: Final = Path(__file__).resolve().parents[1] / "fixtures" / "apify_contract" / "v1"
SITE: Final = "synthetic"
ACTOR_ID: Final = "acct~hw-radar-synthetic-collector"
TOKEN: Final = "apify_api_d5_test_token"
MEMORY_MB: Final = 256
TIMEOUT_S: Final = 120

LIVE: Final = override_settings(HW_RADAR_APIFY_ENABLED=True, HW_RADAR_APIFY_ACTOR_ID=ACTOR_ID)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _committed[T](query: Callable[[], T]) -> T:
    """Run `query` on a fresh connection, so it sees committed rows only.

    Used from inside a MockTransport handler to prove a counter was committed
    before the request it guards was sent.
    """

    def work() -> T:
        try:
            return query()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(work).result()


class AllowAll:
    """Test-only budget admission; production binds DenyAllAdmission."""

    def __init__(self) -> None:
        self.requests: list[BudgetRequest] = []

    def admit(self, request: BudgetRequest) -> BudgetDecision:
        self.requests.append(request)
        return BudgetDecision(True)


def _run_body(run_id: str, status: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": run_id,
        "actId": "act-1",
        "status": status,
        "startedAt": "2026-09-24T12:00:00Z",
        "finishedAt": None,
        "buildId": "build-1",
        "buildNumber": "1.0.7",
        "defaultDatasetId": f"ds-{run_id}",
        "defaultKeyValueStoreId": f"kv-{run_id}",
        "options": {"memoryMbytes": MEMORY_MB, "timeoutSecs": TIMEOUT_S, "build": "prod"},
        "stats": {"restartCount": 0},
        "usageTotalUsd": None,
    }
    body.update(overrides)
    return {"data": body}


class FakeApify:
    """Route table for the Apify calls the jobs make, with a request log.

    Runs are keyed by id; `statuses[id]` is what a GET answers. Datasets and
    OUTPUT records are served per storage id from a contract fixture, so a
    terminal row imports through the real D10 importer.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.start_response: Callable[[httpx.Request], httpx.Response] = lambda _r: httpx.Response(
            201, json=_run_body("run-new", "READY")
        )
        self.abort_response: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, json=_run_body(r.url.path.split("/")[3], "ABORTED")
        )
        self.statuses: dict[str, str] = {}
        self.broken_runs: set[str] = set()
        self.storage: dict[str, dict[str, Any]] = {}
        self.broken_datasets: set[str] = set()

    def paths(self, method: str, prefix: str) -> list[str]:
        return [
            r.url.path
            for r in self.requests
            if r.method == method and r.url.path.startswith(prefix)
        ]

    def serve(self, run_id: str, fixture: dict[str, Any]) -> None:
        self.storage[f"ds-{run_id}"] = fixture
        self.storage[f"kv-{run_id}"] = fixture

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        parts = path.split("/")
        if request.method == "POST" and path == f"/v2/actors/{ACTOR_ID}/runs":
            return self.start_response(request)
        if request.method == "POST" and path.endswith("/abort"):
            return self.abort_response(request)
        if request.method == "GET" and parts[:3] == ["", "v2", "actor-runs"]:
            run_id = parts[3]
            if run_id in self.broken_runs:
                return httpx.Response(500, json={"error": {"type": "internal", "message": "x"}})
            return httpx.Response(200, json=_run_body(run_id, self.statuses[run_id]))
        if path.startswith("/v2/datasets/") and path.endswith("/items"):
            dataset = parts[3]
            if dataset in self.broken_datasets:
                return httpx.Response(500, json={"error": {"type": "internal", "message": "x"}})
            rows = self.storage[dataset]["datasetItems"]
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            return httpx.Response(
                200,
                json=rows[offset : offset + limit],
                headers={"X-Apify-Pagination-Total": str(len(rows))},
            )
        if path.startswith("/v2/key-value-stores/") and path.endswith("/records/OUTPUT"):
            return httpx.Response(200, json=self.storage[parts[3]]["output"])
        return httpx.Response(500, json={"error": {"type": "unexpected", "message": path}})

    def client(self) -> ApifyClient:
        return ApifyClient(TOKEN, transport=httpx.MockTransport(self))


@pytest.fixture
def site() -> SourceSite:
    # F5a creates this site with a management command, not a migration (MS2-D-42).
    return SourceSite.objects.create(name="Synthetic", normalized_name=SITE)


@pytest.fixture
def config(site: SourceSite) -> SourceConfig:
    return SourceConfig.objects.select_related("source_site").get(
        pk=SourceConfig.objects.create(
            source_site=site,
            tier=SourceTier.T2_SPECIALIST,
            domain="synthetic.invalid",
            cadence_baseline_s=3600,
            cadence_ceiling_s=900,
            enabled=True,
            collection_provider=ProviderKind.APIFY,
        ).pk
    )


def _spec(*, timeout_s: int = TIMEOUT_S, **input_overrides: Any) -> ActorRunSpec:
    run_input = {**_fixture("complete")["actorInput"], **input_overrides}
    return ActorRunSpec(
        input_model=SyntheticCollectorInput,
        run_input=run_input,
        memory_mb=MEMORY_MB,
        timeout_s=timeout_s,
    )


def _specs(spec: ActorRunSpec | None = None, site_key: str = SITE) -> dict[str, Any]:
    chosen = spec or _spec()

    def factory(_config: SourceConfig, _kind: RunKind) -> ActorRunSpec:
        return chosen

    return {site_key: factory}


def _start(
    config: SourceConfig,
    fake: FakeApify,
    *,
    admission: Any = None,
    specs: dict[str, Any] | None = None,
) -> jobs.StartResult:
    return _run(
        start_provider_run(
            config,
            admission=admission if admission is not None else AllowAll(),
            run_specs=specs if specs is not None else _specs(),
            client_factory=fake.client,
        )
    )


def _tick(fake: FakeApify, **kwargs: Any) -> TickReport:
    return _run(apify_poll_tick(resolver=NullResolver(), client_factory=fake.client, **kwargs))


_seq = iter(range(1, 10_000))


def _row(site: SourceSite, *, run_id: str | None = "auto", **overrides: Any) -> ProviderRun:
    """A provider_run as the start job leaves it, with the given overrides."""
    admitted = _fixture("complete")["admitted"]["queryScope"]
    if run_id == "auto":
        run_id = f"run-{next(_seq)}"
    now = timezone.now()
    fields: dict[str, Any] = {
        "source_site": site,
        "external_run_id": run_id,
        "import_idempotency_key": None if run_id is None else f"apify:{run_id}",
        "actor_ref": ACTOR_ID,
        "contract_schema_version": "hw-radar-run/v1",
        "query_scope": admitted,
        "scope_key": admitted["collectionScope"],
        "memory_mb": MEMORY_MB,
        "timeout_s": TIMEOUT_S,
        "max_items": admitted["maxItems"],
        "max_pages": admitted["maxPages"],
        "admission_class": AdmissionClass.WATCH_REFRESH,
        "admitted_at": now - timedelta(minutes=10),
        "remote_status": None if run_id is None else "RUNNING",
        "started_at": None if run_id is None else datetime.fromisoformat("2026-09-24T12:00:00Z"),
        "dataset_id": None if run_id is None else f"ds-{run_id}",
        "kv_store_id": None if run_id is None else f"kv-{run_id}",
        "storage_cleanup_due_at": now + timedelta(hours=24),
    }
    fields.update(overrides)
    return ProviderRun.objects.create(**fields)


# ── Start job ─────────────────────────────────────────────────────────────────


@LIVE
@override_settings(HW_RADAR_APIFY_ACTOR_BUILD="prod")
def test_start_uses_configured_actor_and_build_tag(config: SourceConfig) -> None:
    fake = FakeApify()
    seen_rows: list[list[tuple[Any, ...]]] = []

    def start(request: httpx.Request) -> httpx.Response:
        # MS2-D-33: the row, with admitted_at and its absolute deadline, is
        # committed before the start request leaves.
        seen_rows.append(
            _committed(
                lambda: list(
                    ProviderRun.objects.values_list(
                        "external_run_id", "admitted_at", "storage_cleanup_due_at"
                    )
                )
            )
        )
        return httpx.Response(201, json=_run_body("run-new", "READY"))

    fake.start_response = start
    admission = AllowAll()
    before = timezone.now()

    result = _start(config, fake, admission=admission)

    assert result.status is StartStatus.STARTED
    [request] = fake.requests
    assert request.url.path == f"/v2/actors/{ACTOR_ID}/runs"
    assert request.url.params["build"] == "prod"
    assert request.url.params["memory"] == str(MEMORY_MB)
    assert request.url.params["timeout"] == str(TIMEOUT_S)
    assert request.url.params["restartOnError"] == "false"
    assert json.loads(request.content) == _fixture("complete")["actorInput"]
    [[(run_id, admitted_at, due_at)]] = seen_rows
    assert run_id is None
    assert due_at == admitted_at + timedelta(hours=24)

    row = ProviderRun.objects.get(pk=result.provider_run_id)
    assert row.actor_ref == ACTOR_ID
    assert row.build_number == "1.0.7"
    assert (row.external_run_id, row.import_idempotency_key) == ("run-new", "apify:run-new")
    assert (row.dataset_id, row.kv_store_id) == ("ds-run-new", "kv-run-new")
    assert row.remote_status == "READY"
    assert row.admitted_at >= before
    assert row.storage_cleanup_attempts == 0
    # D4 hand-off: query_scope is the camelCase QueryScope wire JSON started with.
    assert row.query_scope == _fixture("complete")["admitted"]["queryScope"]
    assert row.scope_key == "synthetic:hdd:catalog"
    [budget] = admission.requests
    assert (budget.admission_class, budget.memory_mb, budget.timeout_s) == (
        AdmissionClass.WATCH_REFRESH,
        MEMORY_MB,
        TIMEOUT_S,
    )


@LIVE
def test_probe_start_is_admitted_as_discovery(config: SourceConfig) -> None:
    admission = AllowAll()
    result = _run(
        start_provider_run(
            config,
            run_kind=RunKind.PROBE,
            admission=admission,
            run_specs=_specs(),
            client_factory=FakeApify().client,
        )
    )
    assert result.status is StartStatus.STARTED
    assert admission.requests[0].admission_class is AdmissionClass.DISCOVERY
    row = ProviderRun.objects.get(pk=result.provider_run_id)
    assert (row.run_kind, row.admission_class) == (RunKind.PROBE, AdmissionClass.DISCOVERY)


@LIVE
def test_start_build_outside_contract_version_line_aborts(config: SourceConfig) -> None:
    fake = FakeApify()
    fake.start_response = lambda _r: httpx.Response(
        201, json=_run_body("run-v2", "READY", buildNumber="2.0.1")
    )

    result = _start(config, fake)

    assert result.status is StartStatus.MISMATCH_ABORTED
    assert "outside contract line 1.x" in result.reason
    assert fake.paths("POST", "/v2/actor-runs/run-v2/abort") == ["/v2/actor-runs/run-v2/abort"]
    row = ProviderRun.objects.get(pk=result.provider_run_id)
    assert row.import_state == ImportState.REJECTED
    assert row.stage_detail["reject_reason"] == "start_option_mismatch"
    assert row.storage_cleanup_attempts == 1
    assert row.remote_status == "ABORTED"
    assert row.remote_terminal_at is not None
    # Rejected, never imported: no dataset or OUTPUT read, nothing deleted yet.
    assert fake.paths("GET", "/v2/datasets") == []
    assert fake.paths("DELETE", "/v2/") == []
    assert row.storage_state == StorageState.RETAINED


@LIVE
def test_start_option_mismatch_abort_is_counted_cleanup_attempt(config: SourceConfig) -> None:
    fake = FakeApify()
    fake.start_response = lambda _r: httpx.Response(
        201,
        json=_run_body(
            "run-mm", "RUNNING", options={"memoryMbytes": 4096, "timeoutSecs": TIMEOUT_S}
        ),
    )
    attempts_at_abort: list[int] = []

    def abort(_request: httpx.Request) -> httpx.Response:
        attempts_at_abort.append(
            _committed(
                lambda: ProviderRun.objects.get(external_run_id="run-mm").storage_cleanup_attempts
            )
        )
        # Not yet terminal: the attempt must not delete anything.
        return httpx.Response(200, json=_run_body("run-mm", "ABORTING"))

    fake.abort_response = abort

    result = _start(config, fake)

    assert result.status is StartStatus.MISMATCH_ABORTED
    assert "memory 4096 != 256" in result.reason
    assert attempts_at_abort == [1]  # committed before the abort was sent
    row = ProviderRun.objects.get(pk=result.provider_run_id)
    assert (row.storage_cleanup_attempts, row.remote_status) == (1, "ABORTING")
    assert row.import_state == ImportState.REJECTED
    assert fake.paths("DELETE", "/v2/") == []

    # Deletion waits for terminal evidence: the next tick observes ABORTED and
    # only then hands the row to the storage-cleanup unit (D11).
    cleaned: list[int] = []

    async def cleanup(pk: int, _client: ApifyClient) -> None:
        cleaned.append(pk)

    fake.statuses["run-mm"] = "ABORTED"
    report = _tick(fake, cleanup=cleanup)
    assert report.polled == [row.pk]
    assert cleaned == [row.pk]
    assert fake.paths("GET", "/v2/datasets") == []


@LIVE
def test_start_request_is_never_retried(config: SourceConfig) -> None:
    fake = FakeApify()

    def lost(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    fake.start_response = lost

    result = _start(config, fake)

    assert result.status is StartStatus.START_FAILED
    assert len(fake.paths("POST", "/v2/actors/")) == 1
    row = ProviderRun.objects.get(pk=result.provider_run_id)
    assert (row.external_run_id, row.remote_status, row.dataset_id) == (None, None, None)
    assert row.stage_detail["start_error"] == {"type": "ConnectError"}

    # No selector re-sends it: the row has no run id to poll or import...
    overdue: list[int] = []

    async def overdue_unit(pk: int, _client: ApifyClient) -> None:
        overdue.append(pk)

    _tick(fake, overdue=overdue_unit)
    assert len(fake.paths("POST", "/v2/actors/")) == 1
    assert overdue == []
    # ...and at its deadline selector 3 takes it as an orphaned start (MS2-D-33).
    ProviderRun.objects.filter(pk=row.pk).update(storage_cleanup_due_at=timezone.now())
    _tick(fake, overdue=overdue_unit)
    assert overdue == [row.pk]
    assert len(fake.paths("POST", "/v2/actors/")) == 1


def test_production_defaults_never_send_a_start_request(config: SourceConfig) -> None:
    fake = FakeApify()
    # Kill switch default (false) and the production DenyAll binding.
    disabled = _run(start_provider_run(config, run_specs=_specs(), client_factory=fake.client))
    with LIVE:
        denied = _run(start_provider_run(config, run_specs=_specs(), client_factory=fake.client))
        # No site is registered in RUN_SPECS in production.
        unspecced = _run(
            start_provider_run(config, admission=AllowAll(), client_factory=fake.client)
        )

    assert (disabled.status, disabled.reason) == (StartStatus.REFUSED, "apify_disabled")
    assert (denied.status, denied.reason) == (StartStatus.DENIED, "budget_admission_unavailable")
    assert (unspecced.status, unspecced.reason) == (StartStatus.REFUSED, "no_run_spec")
    assert fake.requests == []
    assert not ProviderRun.objects.exists()


@LIVE
@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("timeout", "timeout_exceeds_retention"),
        ("bounded", "bounded_retention_unenforceable"),
        ("invalid", "invalid_input"),
        ("no_actor", "actor_unconfigured"),
    ],
)
def test_start_refusals_spend_nothing(config: SourceConfig, case: str, reason: str) -> None:
    fake = FakeApify()
    admission = AllowAll()
    target = config
    specs = _specs()
    if case == "timeout":
        # 24 h deadline - 1 h import margin leaves 23 h for the run.
        specs = _specs(_spec(timeout_s=23 * 3600 + 1))
    elif case == "bounded":
        target = SourceConfig.objects.select_related("source_site").get(
            source_site__normalized_name="ebay"
        )
        specs = _specs(_spec(siteKey="ebay", collectionScope="ebay:hdd:search"), "ebay")
    elif case == "invalid":
        specs = _specs(_spec(maxItems=0))

    with override_settings(HW_RADAR_APIFY_ACTOR_ID="" if case == "no_actor" else ACTOR_ID):
        result = _run(
            start_provider_run(
                target, admission=admission, run_specs=specs, client_factory=fake.client
            )
        )

    assert result.status is StartStatus.REFUSED
    assert str(result.reason).startswith(reason)
    # MS2-D-33: these refusals come before budget admission, with no spend.
    assert admission.requests == []
    assert fake.requests == []
    assert not ProviderRun.objects.exists()


def test_poll_source_starts_apify_run_instead_of_local_adapter(
    config: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[str, RunKind]] = []

    async def fake_start(cfg: SourceConfig, *, run_kind: RunKind) -> jobs.StartResult:
        started.append((cfg.source_site.normalized_name, run_kind))
        return jobs.StartResult(StartStatus.DENIED, "budget_admission_unavailable")

    def local_adapter() -> Any:
        raise AssertionError("the local adapter must never run for an apify source")

    monkeypatch.setattr(service, "start_provider_run", fake_start)
    monkeypatch.setitem(ADAPTERS, SITE, local_adapter)
    registry = BucketRegistry()
    registry.configure_source(SITE, rate_per_min=60, burst=5, now_s=0.0)

    _run(service.poll_source(SITE, registry, service.AsyncIOScheduler()))

    assert started == [(SITE, RunKind.FULL)]
    assert not ScraperRun.objects.filter(source_site=config.source_site).exists()


def test_apify_poll_job_is_registered_with_no_source_enabled() -> None:
    # Registered unconditionally (ED-07: it drains admitted runs with the kill
    # switch off); max_instances=1 / coalesce=True come from the scheduler's
    # job defaults, which tests/unit/test_poller.py pins.
    scheduler = service.build_scheduler(BucketRegistry(), [])
    job = scheduler.get_job("apify-poll")
    assert job is not None
    assert job.trigger.interval.total_seconds() == jobs.APIFY_POLL_SECONDS


def test_idle_tick_builds_no_client_and_missing_token_backs_off_nothing(
    site: SourceSite,
) -> None:
    def no_token() -> ApifyClient:
        return ApifyClient(token="")  # raises ApifyTokenMissingError

    assert _run(apify_poll_tick(resolver=NullResolver(), client_factory=no_token)) == TickReport()

    row = _row(site)
    report = _run(apify_poll_tick(resolver=NullResolver(), client_factory=no_token))
    assert "token missing" in report.error
    row.refresh_from_db()
    assert (row.next_attempt_at, row.run_poll_count) == (None, 0)


# ── Selectors ─────────────────────────────────────────────────────────────────


@override_settings(HW_RADAR_APIFY_MAX_RUN_POLLS=60)
def test_active_selector_polls_only_non_terminal_runs(site: SourceSite) -> None:
    running = _row(site, remote_status="RUNNING")
    ready = _row(site, remote_status="READY")
    aborting = _row(site, remote_status="ABORTING")
    _row(site, remote_status="SUCCEEDED")
    _row(site, remote_status="TIMED-OUT")
    _row(site, run_id=None)  # start response never recorded: no run id to poll
    _row(site, remote_status="RUNNING", run_poll_count=60)  # at the poll cap
    # Polled just now: spacing is timeout_s / (cap / 2) = 4 s.
    _row(site, remote_status="RUNNING", usage_read_at=timezone.now())
    _row(site, remote_status="RUNNING", next_attempt_at=timezone.now() + timedelta(minutes=5))

    expected = [running.pk, ready.pk, aborting.pk]
    assert select_active(timezone.now()) == expected

    fake = FakeApify()
    for row in (running, ready, aborting):
        fake.statuses[str(row.external_run_id)] = "RUNNING"
    report = _tick(fake)

    assert report.polled == expected
    assert sorted(fake.paths("GET", "/v2/actor-runs/")) == sorted(
        f"/v2/actor-runs/{row.external_run_id}" for row in (running, ready, aborting)
    )
    polled = ProviderRun.objects.filter(pk__in=expected)
    assert {r.run_poll_count for r in polled} == {1}
    assert all(r.usage_read_at is not None for r in polled)
    assert ProviderRun.objects.exclude(pk__in=expected).filter(run_poll_count=1).count() == 0


def test_poll_records_termination_once_and_keeps_recorded_storage_ids(site: SourceSite) -> None:
    row = _row(site, remote_status="RUNNING")
    fake = FakeApify()
    fake.statuses[str(row.external_run_id)] = "SUCCEEDED"
    fake.serve(str(row.external_run_id), _fixture("complete"))

    report = _tick(fake)

    assert report.polled == [row.pk]
    # Terminal in this tick's poll, so selector 2 imported it in the same tick.
    assert report.imported == [row.pk]
    row.refresh_from_db()
    assert row.remote_status == "SUCCEEDED"
    assert row.remote_terminal_at is not None
    assert row.import_state == ImportState.FINALIZED
    assert row.dataset_id == f"ds-{row.external_run_id}"


def test_outstanding_selector_picks_terminal_rows_with_unfinished_import(
    site: SourceSite,
) -> None:
    pending = _row(site, remote_status="SUCCEEDED")
    resolved = _row(site, remote_status="FAILED", import_state=ImportState.RESOLVED)
    finalized_retained = _row(site, remote_status="SUCCEEDED", import_state=ImportState.FINALIZED)
    rejected_failed_delete = _row(
        site,
        remote_status="ABORTED",
        import_state=ImportState.REJECTED,
        storage_state=StorageState.DELETE_FAILED,
    )
    _row(
        site,
        remote_status="SUCCEEDED",
        import_state=ImportState.FINALIZED,
        storage_state=StorageState.DELETED,
    )
    _row(
        site,
        remote_status="TIMED-OUT",
        import_state=ImportState.REJECTED,
        storage_state=StorageState.DELETED,
    )
    _row(site, remote_status="RUNNING")  # selector 1's, not selector 2's
    _row(site, run_id=None)
    # Backing off after a retry exhaustion: the importer relies on this filter.
    _row(site, remote_status="SUCCEEDED", next_attempt_at=timezone.now() + timedelta(minutes=5))

    assert select_outstanding(timezone.now()) == [
        pending.pk,
        resolved.pk,
        finalized_retained.pk,
        rejected_failed_delete.pk,
    ]


@pytest.mark.parametrize("remote_status", [None, "RUNNING", "SUCCEEDED"])
def test_overdue_selector_picks_rows_past_deadline_regardless_of_remote_status(
    site: SourceSite, remote_status: str | None
) -> None:
    past = timezone.now() - timedelta(minutes=1)
    run_id = None if remote_status is None else "auto"
    overdue_row = _row(
        site,
        run_id=run_id,
        remote_status=remote_status,
        storage_cleanup_due_at=past,
        # Never observed terminal by selector 1, even when it is terminal.
        remote_terminal_at=None,
    )
    _row(site, run_id=run_id, remote_status=remote_status)  # deadline ahead
    _row(
        site,
        run_id=run_id,
        remote_status=remote_status,
        storage_cleanup_due_at=past,
        storage_state=StorageState.DELETED,
    )

    assert select_overdue(timezone.now()) == [overdue_row.pk]

    seen: list[int] = []

    async def overdue(pk: int, _client: ApifyClient) -> None:
        seen.append(pk)

    fake = FakeApify()
    fake.statuses = {
        str(r.external_run_id): "RUNNING" for r in ProviderRun.objects.all() if r.external_run_id
    }
    fake.broken_datasets = {r.dataset_id for r in ProviderRun.objects.all() if r.dataset_id}
    assert _tick(fake, overdue=overdue).overdue == [overdue_row.pk]
    assert seen == [overdue_row.pk]


def test_one_failing_row_does_not_block_others(site: SourceSite) -> None:
    poll_fails = _row(site, remote_status="RUNNING")
    poll_ok = _row(site, remote_status="RUNNING")
    import_fails = _row(site, remote_status="SUCCEEDED")
    import_ok = _row(site, remote_status="SUCCEEDED")
    fake = FakeApify()
    fake.broken_runs = {str(poll_fails.external_run_id)}
    fake.statuses[str(poll_ok.external_run_id)] = "RUNNING"
    fixture = _fixture("complete")
    fake.serve(str(import_ok.external_run_id), fixture)
    fake.serve(str(import_fails.external_run_id), fixture)
    fake.broken_datasets = {str(import_fails.dataset_id)}

    report = _tick(fake)

    assert report.failed == [poll_fails.pk, import_fails.pk]
    assert report.polled == [poll_ok.pk]
    assert report.imported == [import_ok.pk]
    import_ok.refresh_from_db()
    assert import_ok.import_state == ImportState.FINALIZED
    for failed in (poll_fails, import_fails):
        failed.refresh_from_db()
        assert failed.stage_detail["unit_failures"] == 1
        assert failed.next_attempt_at is not None
        assert failed.next_attempt_at > timezone.now()
    import_fails.refresh_from_db()
    assert import_fails.import_state == ImportState.PENDING

    # Backed-off rows wait; the next tick does not retry them yet.
    requests_before = len(fake.requests)
    second = _tick(fake)
    assert second.failed == []
    assert poll_fails.pk not in second.polled
    assert all(
        str(poll_fails.external_run_id) not in r.url.path for r in fake.requests[requests_before:]
    )


# ── Restart recovery (plan D10, landing with D5) ──────────────────────────────


class Crash(BaseException):
    """Simulated process loss: escapes every `except Exception` in the jobs."""


class _FailOnce:
    def __init__(self, real: Any) -> None:
        self.real = real
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            raise Crash
        return self.real(*args, **kwargs)


def _drive_fixture() -> dict[str, Any]:
    # The complete fixture retargeted at the seeded `drive` category, so the
    # production evaluator has a real category to bind (as in test_apify_import).
    fixture = copy.deepcopy(_fixture("complete"))
    for row in fixture["datasetItems"]:
        row["categoryHint"] = "drive"
    return fixture


def _counts(site: SourceSite) -> tuple[int, int, int]:
    return (
        Listing.objects.filter(source_site=site).count(),
        OfferSnapshot.objects.filter(listing__source_site=site).count(),
        RawPayload.objects.filter(endpoint__startswith="apify-dataset:").count(),
    )


# The stage whose entry crashes leaves the row in the state before it.
_CRASH_AT: Final = {
    ImportState.OBSERVATIONS_COMMITTED: "_stage2",
    ImportState.ABSENCE_APPLIED: "_stage3",
    ImportState.RESOLVED: "_stage4",
    ImportState.EVALUATED: "_stage5",
}


@pytest.mark.parametrize("state", list(_CRASH_AT), ids=lambda s: str(s))
def test_restart_recovers_each_unfinished_import_state(
    site: SourceSite, monkeypatch: pytest.MonkeyPatch, state: ImportState
) -> None:
    assert Category.objects.filter(slug="drive").exists()
    fixture = _drive_fixture()
    row = _row(site, remote_status="SUCCEEDED")
    fake = FakeApify()
    fake.serve(str(row.external_run_id), fixture)
    stage = _CRASH_AT[state]
    monkeypatch.setattr(importer, stage, _FailOnce(getattr(importer, stage)))

    with pytest.raises(Crash):
        _tick(fake)
    row.refresh_from_db()
    assert row.import_state == state
    assert row.scraper_run is not None
    first_run = row.scraper_run.pk

    # The restarted poller finds the row from persisted state alone.
    assert select_outstanding(timezone.now()) == [row.pk]
    report = _tick(fake)

    assert report.imported == [row.pk]
    row.refresh_from_db()
    assert row.import_state == ImportState.FINALIZED
    rows = len(fixture["datasetItems"])
    assert _counts(site) == (rows, rows, rows)
    assert ScraperRun.objects.filter(source_site=site).count() == 1
    run = ScraperRun.objects.get(source_site=site)
    assert run.pk == first_run
    assert run.status == RunStatus.SUCCESS
    # Every one of these states is past stage 1, so the dataset is read once.
    assert row.dataset_read_count == 1
    assert len(fake.paths("GET", "/v2/datasets/")) == 1


def test_restart_retries_storage_cleanup_after_finalized_import(site: SourceSite) -> None:
    row = _row(site, remote_status="SUCCEEDED")
    fake = FakeApify()
    fake.serve(str(row.external_run_id), _fixture("complete"))
    calls: list[int] = []

    async def cleanup(pk: int, _client: ApifyClient) -> None:
        calls.append(pk)
        if len(calls) == 1:
            raise Crash
        await asyncio.sleep(0)
        await _mark_deleted(pk)

    first = _tick(fake, cleanup=cleanup)
    assert first.imported == [row.pk]
    row.refresh_from_db()
    assert (row.import_state, row.storage_state) == (ImportState.FINALIZED, StorageState.RETAINED)

    with pytest.raises(Crash):
        _tick(fake, cleanup=cleanup)
    row.refresh_from_db()
    assert row.storage_state == StorageState.RETAINED

    # After the restart the finalized row is still selected, for cleanup only.
    assert select_outstanding(timezone.now()) == [row.pk]
    third = _tick(fake, cleanup=cleanup)
    assert third.cleanup == [row.pk]
    assert third.imported == []
    assert calls == [row.pk, row.pk]
    row.refresh_from_db()
    assert row.storage_state == StorageState.DELETED
    assert row.dataset_read_count == 1  # cleanup never re-imports
    assert select_outstanding(timezone.now()) == []


async def _mark_deleted(pk: int) -> None:
    from asgiref.sync import sync_to_async

    await sync_to_async(
        lambda: ProviderRun.objects.filter(pk=pk).update(
            storage_state=StorageState.DELETED, storage_deleted_at=timezone.now()
        )
    )()


def _started(
    *,
    memory: int | None = MEMORY_MB,
    timeout: int | None = TIMEOUT_S,
    build: str | None = None,
    restart_on_error: bool | None = None,
    restart_count: int | None = 0,
    build_number: str | None = "1.0.7",
) -> ApifyRun:
    return ApifyRun(
        id="run-x",
        act_id=None,
        status="READY",
        status_message=None,
        started_at=None,
        finished_at=None,
        build_id=None,
        build_number=build_number,
        default_dataset_id=None,
        default_key_value_store_id=None,
        options=RunOptions(
            memory_mbytes=memory,
            timeout_secs=timeout,
            build=build,
            max_items=None,
            restart_on_error=restart_on_error,
        ),
        usage_total_usd=None,
        usage_usd=None,
        usage=None,
        unparseable_usage=(),
        restart_count=restart_count,
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # Apify's documented options omit restartOnError and may omit build:
        # "not reported" is not a mismatch; an unreported memory/timeout is.
        ({}, []),
        ({"build": "prod", "restart_on_error": False, "restart_count": None}, []),
        ({"timeout": 60}, ["timeout 60 != 120"]),
        ({"memory": None}, ["memory None != 256"]),
        ({"build": "beta"}, ["build tag 'beta' != 'prod'"]),
        ({"restart_on_error": True}, ["restartOnError true"]),
        ({"restart_count": 1}, ["restarted 1x"]),
        ({"build_number": None}, ["build number unreported"]),
        ({"build_number": "10.0.1"}, ["build 10.0.1 outside contract line 1.x"]),
    ],
)
def test_start_mismatches_fail_closed(overrides: dict[str, Any], expected: list[str]) -> None:
    run = _started(**overrides)
    assert start_mismatches(run, memory_mb=MEMORY_MB, timeout_s=TIMEOUT_S, build_tag="prod") == (
        expected
    )


# ── Slice E4: latch wiring and the kill switch (MS2-D-17, -26, ED-07) ────────


@LIVE
def test_start_option_mismatch_aborts_and_trips_latch(config: SourceConfig) -> None:
    fake = FakeApify()
    fake.start_response = lambda _r: httpx.Response(
        201,
        json=_run_body(
            "run-mx", "RUNNING", options={"memoryMbytes": 4096, "timeoutSecs": TIMEOUT_S}
        ),
    )
    result = _start(config, fake)
    assert result.status is StartStatus.MISMATCH_ABORTED
    assert fake.paths("POST", "/v2/actor-runs/run-mx/abort") == ["/v2/actor-runs/run-mx/abort"]
    trip = ApifyBudgetLatch.objects.get(cleared_at__isnull=True)
    assert (trip.reason, trip.provider_run_id) == (  # pyright: ignore[reportAttributeAccessIssue]
        "start_option_mismatch",
        result.provider_run_id,
    )


class _DrainingApify(FakeApify):
    """FakeApify whose runs report a usage figure and whose deletes succeed."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        if request.method == "DELETE":
            self.requests.append(request)
            return httpx.Response(204)
        if request.method == "GET" and parts[:3] == ["", "v2", "actor-runs"]:
            self.requests.append(request)
            run_id = parts[3]
            return httpx.Response(
                200,
                json=_run_body(
                    run_id,
                    self.statuses[run_id],
                    finishedAt="2026-09-24T12:05:00Z",
                    usageTotalUsd=0.01,
                    usageUsd={"ACTOR_COMPUTE_UNITS": 0.01},
                ),
            )
        return super().__call__(request)


def test_kill_switch_off_still_drains_imports_cleanup_and_closing_reads(
    site: SourceSite, config: SourceConfig
) -> None:
    # HW_RADAR_APIFY_ENABLED is false (the default): starts, probes, and
    # operator reservations are denied, while selectors 1-4 still import,
    # delete, settle, and close liability already reserved.
    fake = _DrainingApify()
    row = _row(site, remote_status="SUCCEEDED")
    fake.statuses[row.external_run_id or ""] = "SUCCEEDED"
    fake.serve(row.external_run_id or "", _fixture("complete"))
    estimate = ledger_support.RUN_ESTIMATE
    reservation = ApifySpendReservation.objects.create(
        admission_class=AdmissionClass.WATCH_REFRESH,
        source_site=site,
        provider_run=row,
        estimate_usd=estimate.estimate_usd,
        execution_bound_usd=estimate.execution_bound_usd,
        post_run_liability_usd=estimate.post_run_liability_usd,
        monitoring_bound_usd=estimate.monitoring_bound_usd,
        estimator_version="1",
        reserved_at=row.admitted_at,
    )
    # No settle delay, guard, or correction window, so one tick per stage.
    budget = dataclasses.replace(ledger_support.BUDGET, enabled=False, cycle_boundary_guard_s=0)
    cfg = dataclasses.replace(
        ledger_support.CONFIG, budget=budget, usage_settle_delay_s=0, correction_window_s=0
    )
    clock = ledger_support.Clock(timezone.now())

    def tick() -> TickReport:
        clock.at += timedelta(minutes=2)
        return _run(
            apify_poll_tick(
                resolver=NullResolver(), client_factory=fake.client, ledger_config=cfg, clock=clock
            )
        )

    assert tick().imported == [row.pk]
    assert tick().cleanup == [row.pk]
    assert tick().reconciled == [row.pk]
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.RECONCILED
    assert tick().monitored == [reservation.pk]
    reservation.refresh_from_db()
    row.refresh_from_db()
    assert (row.import_state, row.storage_state) == (ImportState.FINALIZED, StorageState.DELETED)
    assert reservation.correction_monitor_closed_at is not None
    assert len(fake.paths("DELETE", "/v2/")) == 2

    # Paid admission stays off.
    denied = _start(config, fake)
    assert (denied.status, denied.reason) == (StartStatus.REFUSED, "apify_disabled")
    outcome = reserve_operator(OperatorKind.BUILD, config=cfg, reason="build while disabled")
    assert (outcome.admitted, outcome.reason) == (False, "apify_disabled")
