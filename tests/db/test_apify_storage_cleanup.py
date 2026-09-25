# pyright: reportPrivateUsage=false
"""MS-2 Slice D task D11: remote storage cleanup (MS2-D-25, MS2-D-32, MS2-D-33).

Storage is deleted in every run outcome: after a successful, rejected, or
failed import through selector 2, and at the absolute deadline through
selector 3 whatever the remote status. The overdue sequence deletes only after
terminal evidence (ED-15, R10-09), a 404 counts only under the 404 rule
(ED-19), and each attempt is counted before its first call (MS2-D-32).

Every Apify call is served by an httpx.MockTransport (`Apify` below), with a
request log, so the tests can assert which calls were never sent. The ticks
run the production default storage units; nothing is substituted.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, Final

import httpx
import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from hw_radar.acquisition import retention_policy
from hw_radar.acquisition.apify import jobs
from hw_radar.acquisition.apify.client import ApifyClient
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
    start_provider_run,
    storage_cleanup_deadline,
)
from hw_radar.acquisition.apify.ledger import AccountReader
from hw_radar.acquisition.contracts import AdapterRetention, NullResolver
from hw_radar.acquisition.retention_policy import SOURCE_RETENTION
from hw_radar.catalog.models import (
    Listing,
    OfferSnapshot,
    ProviderKind,
    ProviderRun,
    RawPayload,
    RetentionClass,
    RunKind,
    SourceConfig,
    SourceSite,
    SourceTier,
)
from hw_radar.catalog.models.provider import AdmissionClass, ImportState, StorageState

# transaction=True: the jobs write from sync_to_async threads.
# serialized_rollback=True keeps the migration-seeded SourceSite rows (eBay).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

FIXTURE_DIR: Final = Path(__file__).resolve().parents[1] / "fixtures" / "apify_contract" / "v1"
SITE: Final = "synthetic"
ACTOR_ID: Final = "acct~hw-radar-synthetic-collector"
MEMORY_MB: Final = 256
TIMEOUT_S: Final = 120
LIVE: Final = override_settings(HW_RADAR_APIFY_ENABLED=True, HW_RADAR_APIFY_ACTOR_ID=ACTOR_ID)
Handler = Callable[[httpx.Request], httpx.Response]


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _run_body(run_id: str, status: str) -> dict[str, Any]:
    return {
        "data": {
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
    }


def _error(status: int) -> httpx.Response:
    return httpx.Response(status, json={"error": {"type": "x", "message": "x"}})


class Apify:
    """Routes for every call the poll tick can make, with a request log.

    `run_status` answers GET run and `abort_status` the abort (None answers
    500); `delete_status` answers every DELETE. Datasets and OUTPUT records
    are served from a fixture registered per run with `serve`.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.run_status: dict[str, str | None] = {}
        self.abort_status: dict[str, str | None] = {}
        self.delete_status = 204
        self.storage: dict[str, dict[str, Any]] = {}
        self.start: Handler = lambda _r: httpx.Response(201, json=_run_body("run-new", "READY"))

    def serve(self, run_id: str, fixture: dict[str, Any]) -> None:
        self.storage[f"ds-{run_id}"] = fixture
        self.storage[f"kv-{run_id}"] = fixture

    def sent(self, method: str, prefix: str = "/v2/") -> list[str]:
        return [
            r.url.path
            for r in self.requests
            if r.method == method and r.url.path.startswith(prefix)
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        parts = path.split("/")
        if request.method == "POST" and path == f"/v2/actors/{ACTOR_ID}/runs":
            return self.start(request)
        if request.method == "POST" and path.endswith("/abort"):
            status = self.abort_status.get(parts[3])
            return (
                _error(500)
                if status is None
                else httpx.Response(200, json=_run_body(parts[3], status))
            )
        if request.method == "GET" and parts[:3] == ["", "v2", "actor-runs"]:
            status = self.run_status.get(parts[3])
            return (
                _error(500)
                if status is None
                else httpx.Response(200, json=_run_body(parts[3], status))
            )
        if request.method == "DELETE":
            return httpx.Response(204) if self.delete_status == 204 else _error(self.delete_status)
        if path.endswith("/items"):
            rows = self.storage[parts[3]]["datasetItems"]
            offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
            return httpx.Response(
                200,
                json=rows[offset : offset + limit],
                headers={"X-Apify-Pagination-Total": str(len(rows))},
            )
        if path.endswith("/records/OUTPUT"):
            return httpx.Response(200, json=self.storage[parts[3]]["output"])
        return _error(500)

    def client(self) -> ApifyClient:
        return ApifyClient("apify_api_d11_test_token", transport=httpx.MockTransport(self))


def _tick(fake: Apify) -> TickReport:
    return _run(apify_poll_tick(resolver=NullResolver(), client_factory=fake.client))


class AllowAll:
    """Test-only budget admission; production binds jobs.LedgerAdmission."""

    async def admit(self, request: BudgetRequest, reader: AccountReader) -> BudgetDecision:
        return BudgetDecision(True)


@pytest.fixture
def site() -> SourceSite:
    return SourceSite.objects.create(name="Synthetic", normalized_name=SITE)


@pytest.fixture
def config(site: SourceSite) -> SourceConfig:
    created = SourceConfig.objects.create(
        source_site=site,
        tier=SourceTier.T2_SPECIALIST,
        domain="synthetic.invalid",
        cadence_baseline_s=3600,
        cadence_ceiling_s=900,
        enabled=True,
        collection_provider=ProviderKind.APIFY,
    )
    return SourceConfig.objects.select_related("source_site").get(pk=created.pk)


_seq = iter(range(1, 10_000))


def _row(site: SourceSite, **overrides: Any) -> ProviderRun:
    """A started provider_run (run and storage ids recorded), with overrides."""
    admitted = _fixture("complete")["admitted"]["queryScope"]
    run_id = f"run-c{next(_seq)}"
    now = timezone.now()
    fields: dict[str, Any] = {
        "source_site": site,
        "external_run_id": run_id,
        "import_idempotency_key": f"apify:{run_id}",
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
        "remote_status": "RUNNING",
        "started_at": datetime.fromisoformat("2026-09-24T12:00:00Z"),
        "dataset_id": f"ds-{run_id}",
        "kv_store_id": f"kv-{run_id}",
        "storage_cleanup_due_at": now + timedelta(hours=24),
    }
    fields.update(overrides)
    return ProviderRun.objects.create(**fields)


def _overdue(site: SourceSite, **overrides: Any) -> ProviderRun:
    return _row(site, storage_cleanup_due_at=timezone.now() - timedelta(minutes=1), **overrides)


def _unpolled_overdue(site: SourceSite) -> ProviderRun:
    """An overdue RUNNING row selector 1 no longer reads (at the poll cap).

    Isolates selector 3: otherwise selector 1's own GET in the same tick could
    observe termination first, and the overdue unit would skip its abort.
    """
    return _overdue(
        site, remote_status="RUNNING", run_poll_count=settings.HW_RADAR_APIFY_MAX_RUN_POLLS
    )


def _ids(row: ProviderRun) -> str:
    return str(row.external_run_id)


def _deletes(row: ProviderRun) -> list[str]:
    return [f"/v2/datasets/{row.dataset_id}", f"/v2/key-value-stores/{row.kv_store_id}"]


def _assert_deleted(row: ProviderRun, *, attempts: int = 1) -> None:
    row.refresh_from_db()
    assert row.storage_state == StorageState.DELETED
    assert row.storage_deleted_at is not None
    assert row.storage_cleanup_attempts == attempts


def _retry_now(row: ProviderRun) -> None:
    ProviderRun.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now())


# ── Cleanup in every run outcome ──────────────────────────────────────────────


def test_cleanup_after_successful_import(site: SourceSite) -> None:
    row = _row(site, remote_status="SUCCEEDED")
    fake = Apify()
    fake.serve(_ids(row), _fixture("complete"))

    first = _tick(fake)
    assert first.imported == [row.pk]
    assert fake.sent("DELETE") == []  # the import ends first; storage is its input

    second = _tick(fake)

    assert second.cleanup == [row.pk]
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row)
    assert row.import_state == ImportState.FINALIZED
    assert fake.sent("POST", "/v2/actor-runs/") == []  # observed terminal: no abort
    assert select_outstanding(timezone.now()) == []
    assert _tick(fake) == TickReport()


def test_cleanup_after_rejected_import(site: SourceSite) -> None:
    row = _row(site, remote_status="SUCCEEDED")
    fake = Apify()
    fake.serve(_ids(row), _fixture("ambiguous-empty"))  # classifies as failed

    _tick(fake)
    row.refresh_from_db()
    assert row.import_state == ImportState.REJECTED
    # D10 hand-off: the importer never deletes; a rejection leaves it retained.
    assert row.storage_state == StorageState.RETAINED

    assert _tick(fake).cleanup == [row.pk]
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row)


def test_cleanup_after_failed_remote_run(site: SourceSite) -> None:
    fixture = _fixture("failed-with-items")
    row = _row(site, remote_status=fixture["remoteStatus"])
    assert row.remote_status == "FAILED"
    fake = Apify()
    fake.serve(_ids(row), fixture)

    _tick(fake)
    _tick(fake)

    row.refresh_from_db()
    assert row.import_state in {ImportState.FINALIZED, ImportState.REJECTED}
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row)


def test_cleanup_of_abandoned_run_at_deadline(site: SourceSite) -> None:
    # Terminal, but its import never got anywhere (every read failed and backed
    # off) until the deadline passed. Selector 3 takes it: the import is
    # rejected, and, the run being observed terminal, no abort is sent.
    row = _overdue(site, remote_status="SUCCEEDED", remote_terminal_at=timezone.now())
    fake = Apify()

    report = _tick(fake)

    assert report.overdue == [row.pk]
    row.refresh_from_db()
    assert row.import_state == ImportState.REJECTED
    assert row.stage_detail["reject_reason"] == "storage_deadline_passed"
    assert fake.sent("POST") == []
    assert fake.sent("GET", "/v2/datasets/") == []
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row)


def test_deadline_before_stage1_rejects_import_and_deletes_storage(site: SourceSite) -> None:
    row = _overdue(site, remote_status="SUCCEEDED")
    fake = Apify()
    fake.serve(_ids(row), _fixture("complete"))

    _tick(fake)

    row.refresh_from_db()
    assert row.import_state == ImportState.REJECTED
    assert row.stage_detail["reject_reason"] == "storage_deadline_passed"
    # Retention wins over completeness: nothing was read or persisted.
    assert row.dataset_read_count == 0
    assert fake.sent("GET", "/v2/datasets/") == []
    assert not Listing.objects.filter(source_site=site).exists()
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row)


def test_cleanup_failure_retries_with_backoff(site: SourceSite) -> None:
    row = _row(site, remote_status="SUCCEEDED", import_state=ImportState.FINALIZED)
    fake = Apify()
    fake.delete_status = 500

    before = timezone.now()
    first = _tick(fake)

    # A failed attempt is a recorded outcome, not a unit crash.
    assert (first.cleanup, first.failed) == ([row.pk], [])
    row.refresh_from_db()
    assert (row.storage_state, row.storage_cleanup_attempts) == (StorageState.RETAINED, 1)
    assert row.stage_detail["storage_cleanup_error"] == [
        "dataset delete ApifyApiError 500",
        "kv_store delete ApifyApiError 500",
    ]
    assert row.next_attempt_at is not None
    first_delay = row.next_attempt_at - before
    assert timedelta(seconds=55) <= first_delay <= timedelta(seconds=65)

    # Backing off: the next tick sends nothing for the row.
    sent = len(fake.requests)
    assert _tick(fake) == TickReport()
    assert len(fake.requests) == sent

    _retry_now(row)
    before = timezone.now()
    _tick(fake)
    row.refresh_from_db()
    assert row.storage_cleanup_attempts == 2
    assert row.next_attempt_at is not None
    second_delay = row.next_attempt_at - before
    assert second_delay > first_delay + timedelta(seconds=30)

    # A partial success is kept: the dataset is never deleted twice.
    fake.delete_status = 204
    _retry_now(row)
    fake.requests.clear()
    _tick(fake)
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row, attempts=3)
    assert "storage_cleanup_error" not in row.stage_detail


def test_bounded_source_deadline_is_half_ttl() -> None:
    admitted = timezone.now()
    bounded = AdapterRetention(
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
        expires_policy=lambda at: at + timedelta(hours=6),
    )
    indefinite = AdapterRetention(retention_class=RetentionClass.MERCHANT_FACT, expires_policy=None)

    assert storage_cleanup_deadline(admitted, bounded) == admitted + timedelta(hours=3)
    assert storage_cleanup_deadline(admitted, indefinite) == admitted + timedelta(hours=24)
    with override_settings(HW_RADAR_APIFY_STORAGE_CLEANUP_MAX=2 * 3600):
        # min(STORAGE_CLEANUP_MAX, bounded_ttl / 2): the smaller term binds.
        assert storage_cleanup_deadline(admitted, bounded) == admitted + timedelta(hours=2)


# ── Revision 3 (MS2-D-33, F-10 residual) ──────────────────────────────────────


def _spec(*, timeout_s: int = TIMEOUT_S, **input_overrides: Any) -> ActorRunSpec:
    return ActorRunSpec(
        input_model=SyntheticCollectorInput,
        run_input={**_fixture("complete")["actorInput"], **input_overrides},
        memory_mb=MEMORY_MB,
        timeout_s=timeout_s,
    )


def _start(config: SourceConfig, fake: Apify, spec: ActorRunSpec | None = None) -> Any:
    chosen = spec or _spec()
    key = config.source_site.normalized_name
    return _run(
        start_provider_run(
            config,
            admission=AllowAll(),
            run_specs={key: lambda _c, _k: chosen},
            client_factory=fake.client,
        )
    )


@LIVE
def test_deadline_is_anchored_at_admission_not_terminal_observation(
    config: SourceConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Apify()
    started = _start(config, fake)
    assert started.status is StartStatus.STARTED
    row = ProviderRun.objects.get(pk=started.provider_run_id)
    due = row.storage_cleanup_due_at
    assert due == row.admitted_at + timedelta(hours=24)

    # Termination first observed twenty hours after admission (a long outage).
    later = row.admitted_at + timedelta(hours=20)
    monkeypatch.setattr(jobs, "timezone", SimpleNamespace(now=lambda: later))
    fake.run_status["run-new"] = "SUCCEEDED"
    assert _run(jobs._poll_unit(row.pk, fake.client())) is None

    row.refresh_from_db()
    assert (row.remote_status, row.remote_terminal_at) == ("SUCCEEDED", later)
    assert row.storage_cleanup_due_at == due


def test_restart_after_source_ttl_rejects_expired_content_before_persistence(
    site: SourceSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    ttl = timedelta(hours=6)
    bounded = AdapterRetention(
        retention_class=RetentionClass.AMAZON_EPHEMERAL, expires_policy=lambda at: at + ttl
    )
    monkeypatch.setattr(
        retention_policy, "SOURCE_RETENTION", MappingProxyType({**SOURCE_RETENTION, SITE: bounded})
    )
    # Admitted and started seven hours ago; the poller then went down with the
    # run last seen RUNNING, and comes back after the source TTL has passed.
    admitted = timezone.now() - timedelta(hours=7)
    row = _row(
        site,
        admitted_at=admitted,
        started_at=admitted,
        storage_cleanup_due_at=storage_cleanup_deadline(admitted, bounded),
    )
    assert row.storage_cleanup_due_at == admitted + ttl / 2
    fake = Apify()
    fake.run_status[_ids(row)] = "SUCCEEDED"
    fake.serve(_ids(row), _fixture("complete"))

    _tick(fake)

    row.refresh_from_db()
    assert row.import_state == ImportState.REJECTED
    assert row.stage_detail["reject_reason"] in {"storage_deadline_passed", "content_past_ttl"}
    assert fake.sent("GET", "/v2/datasets/") == []
    assert fake.sent("GET", "/v2/key-value-stores/") == []
    assert not Listing.objects.filter(source_site=site).exists()
    assert not OfferSnapshot.objects.filter(listing__source_site=site).exists()
    assert not RawPayload.objects.filter(
        endpoint__startswith=f"apify-dataset:{row.dataset_id}/"
    ).exists()
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row)


@override_settings(HW_RADAR_APIFY_MAX_RUN_POLLS=60)
def test_unobserved_run_past_deadline_is_aborted_and_cleaned(site: SourceSite) -> None:
    # Still RUNNING on the row: termination was never observed, and at the poll
    # cap selector 1 no longer reads it.
    row = _overdue(site, remote_status="RUNNING", run_poll_count=60)
    assert select_active(timezone.now()) == []
    fake = Apify()
    fake.abort_status[_ids(row)] = "ABORTED"

    report = _tick(fake)

    assert (report.polled, report.overdue) == ([], [row.pk])
    assert fake.sent("POST") == [f"/v2/actor-runs/{_ids(row)}/abort"]
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row)
    assert (row.remote_status, row.run_poll_count) == ("ABORTED", 60)
    assert row.remote_terminal_at is not None
    assert row.import_state == ImportState.REJECTED
    assert row.stage_detail["reject_reason"] == "storage_deadline_passed"


@LIVE
def test_timeout_exceeding_retention_window_denied(config: SourceConfig) -> None:
    fake = Apify()
    # 24 h deadline - 1 h import margin leaves 23 h for the run's timeout.
    result = _start(config, fake, _spec(timeout_s=23 * 3600 + 1))

    assert (result.status, result.reason) == (StartStatus.REFUSED, "timeout_exceeds_retention")
    assert fake.requests == []
    assert not ProviderRun.objects.exists()

    # Exactly at the bound the start is admitted and sent.
    assert _start(config, fake, _spec(timeout_s=23 * 3600)).status is not StartStatus.REFUSED
    assert fake.sent("POST", "/v2/actors/") == [f"/v2/actors/{ACTOR_ID}/runs"]


@LIVE
def test_bounded_source_actor_start_denied() -> None:
    ebay = SourceConfig.objects.select_related("source_site").get(
        source_site__normalized_name="ebay"
    )
    fake = Apify()

    result = _start(ebay, fake, _spec(siteKey="ebay", collectionScope="ebay:hdd:search"))

    assert (result.status, result.reason) == (
        StartStatus.REFUSED,
        "bounded_retention_unenforceable",
    )
    assert fake.requests == []
    assert not ProviderRun.objects.exists()


@pytest.mark.parametrize("run_kind", [RunKind.FULL, RunKind.PROBE])
def test_orphaned_start_is_marked_at_deadline_and_not_reselected(
    config: SourceConfig, run_kind: RunKind
) -> None:
    # A lost start response leaves a row with no run or storage ids. For a PROBE
    # the rejection is load-bearing beyond retention: D12's one-outstanding-
    # probe rule counts a PROBE row as outstanding until its import is
    # finalized or rejected, so an orphan left pending would block every later
    # probe of the source.
    row = _overdue(
        config.source_site,
        run_kind=run_kind,
        admission_class=AdmissionClass.DISCOVERY
        if run_kind is RunKind.PROBE
        else AdmissionClass.WATCH_REFRESH,
        external_run_id=None,
        import_idempotency_key=None,
        remote_status=None,
        started_at=None,
        dataset_id=None,
        kv_store_id=None,
    )
    fake = Apify()

    assert _tick(fake).overdue == [row.pk]

    assert fake.requests == []  # nothing to abort or delete
    row.refresh_from_db()
    assert row.orphaned_start_at is not None
    assert row.storage_state == StorageState.RETAINED
    assert row.storage_cleanup_attempts == 0
    assert row.import_state == ImportState.REJECTED
    assert row.stage_detail["reject_reason"] == "storage_deadline_passed"
    assert select_overdue(timezone.now()) == []
    assert select_outstanding(timezone.now()) == []


# ── Revision 10/11: terminal evidence decides (ED-15, R10-09) ─────────────────


@pytest.mark.parametrize(
    ("abort", "read"),
    [
        (None, "RUNNING"),  # non-2xx abort, non-terminal read
        (None, None),  # non-2xx abort, failed read
        ("ABORTING", "RUNNING"),  # 2xx non-terminal abort, non-terminal read
    ],
    ids=["abort-error+running", "abort-error+read-error", "aborting+running"],
)
def test_overdue_abort_error_or_nonterminal_read_retries_and_deletes_only_after_terminal(
    site: SourceSite, abort: str | None, read: str | None
) -> None:
    row = _unpolled_overdue(site)
    fake = Apify()
    fake.abort_status[_ids(row)] = abort
    fake.run_status[_ids(row)] = read

    for attempt in (1, 2):
        before = timezone.now()
        _tick(fake)
        row.refresh_from_db()
        assert row.storage_cleanup_attempts == attempt
        assert row.storage_state == StorageState.RETAINED
        assert row.next_attempt_at is not None
        assert row.next_attempt_at > before
        assert fake.sent("DELETE") == []
        _retry_now(row)
    assert len(fake.sent("POST")) == 2
    assert len(fake.sent("GET", "/v2/actor-runs/")) == 2

    fake.run_status[_ids(row)] = "ABORTED"
    _tick(fake)
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row, attempts=3)


def test_non2xx_abort_with_terminal_read_proceeds_to_delete_in_same_attempt(
    site: SourceSite,
) -> None:
    row = _unpolled_overdue(site)
    fake = Apify()
    fake.abort_status[_ids(row)] = None
    fake.run_status[_ids(row)] = "SUCCEEDED"

    _tick(fake)

    assert fake.sent("POST") == [f"/v2/actor-runs/{_ids(row)}/abort"]
    assert fake.sent("GET", "/v2/actor-runs/") == [f"/v2/actor-runs/{_ids(row)}"]
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row, attempts=1)
    assert row.remote_status == "SUCCEEDED"
    assert row.remote_terminal_at is not None


def test_terminal_abort_response_proceeds_to_delete_without_confirming_read(
    site: SourceSite,
) -> None:
    row = _unpolled_overdue(site)
    fake = Apify()
    fake.abort_status[_ids(row)] = "ABORTED"
    fake.run_status[_ids(row)] = "RUNNING"  # would contradict the abort if it were sent

    _tick(fake)

    assert fake.sent("GET", "/v2/actor-runs/") == []
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row, attempts=1)
    assert row.remote_status == "ABORTED"
    assert row.remote_terminal_at is not None


@pytest.mark.parametrize("rule_enabled", [False, True], ids=["rule-off", "rule-on"])
def test_delete_404_counts_as_deleted_only_when_rule_enabled(
    site: SourceSite, rule_enabled: bool
) -> None:
    row = _row(site, remote_status="SUCCEEDED", import_state=ImportState.FINALIZED)
    fake = Apify()
    fake.delete_status = 404

    with override_settings(
        HW_RADAR_APIFY_DELETE_404_IS_ABSENT=rule_enabled, HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS=2
    ):
        _tick(fake)
        row.refresh_from_db()
        if rule_enabled:
            _assert_deleted(row, attempts=1)
            return
        assert (row.storage_state, row.storage_cleanup_attempts) == (StorageState.RETAINED, 1)
        assert row.stage_detail["storage_cleanup_error"] == [
            "dataset delete 404 not trusted (DELETE_404_IS_ABSENT false)",
            "kv_store delete 404 not trusted (DELETE_404_IS_ABSENT false)",
        ]

        _retry_now(row)
        _tick(fake)
        row.refresh_from_db()
        assert (row.storage_state, row.storage_cleanup_attempts) == (
            StorageState.DELETE_FAILED,
            2,
        )
        # At the cap retries stop: neither selector hands the row out again.
        _retry_now(row)
        assert select_outstanding(timezone.now()) == []
        ProviderRun.objects.filter(pk=row.pk).update(storage_cleanup_due_at=timezone.now())
        assert select_overdue(timezone.now()) == []
        assert len(fake.sent("DELETE")) == 4


@override_settings(HW_RADAR_APIFY_MAX_RUN_POLLS=60)
def test_run_poll_cap_stops_polling_and_settles_at_bound(site: SourceSite) -> None:
    row = _row(site, remote_status="RUNNING", run_poll_count=59)
    fake = Apify()
    fake.run_status[_ids(row)] = "RUNNING"

    assert _tick(fake).polled == [row.pk]
    row.refresh_from_db()
    assert row.run_poll_count == 60

    # At the cap selectors 1 and 2 stop reading the row, however long it runs.
    ProviderRun.objects.filter(pk=row.pk).update(usage_read_at=None)
    assert select_active(timezone.now()) == []
    assert _tick(fake) == TickReport()
    assert len(fake.sent("GET", "/v2/actor-runs/")) == 1

    # Its termination, never observed, is selector 3's at the deadline; the
    # abort and confirming read there are cleanup attempts, not run polls.
    ProviderRun.objects.filter(pk=row.pk).update(storage_cleanup_due_at=timezone.now())
    fake.abort_status[_ids(row)] = "ABORTING"
    fake.run_status[_ids(row)] = "ABORTED"
    _tick(fake)
    assert fake.sent("DELETE") == _deletes(row)
    _assert_deleted(row, attempts=1)
    assert row.run_poll_count == 60
    # The usage settlement at the bound (basis `bound_unfinalized`) is Slice E's
    # reconciliation (E3/E4); here the row only reaches the state it needs.
    assert row.import_state == ImportState.REJECTED
