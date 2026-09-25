"""F5a code prerequisites: apify_synthetic_setup, the synthetic RUN_SPECS entry, apify_smoke.

The smoke tests run the real command end to end: the real start job admitted by
the production LedgerAdmission (Django settings carry test_apify_ledger_authority's
round prices and the live configured account state, and the cycle's authority is
claimed), then real apify_poll_tick ticks and the real importer. Apify is served
by SmokeApify over httpx.MockTransport, so no test touches the network.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from ledger_support import LEDGER_A, claim, live_cycle_start
from test_apify_ledger_authority import priced

from hw_radar.acquisition.apify import jobs, synthetic
from hw_radar.acquisition.apify.client import ApifyClient
from hw_radar.acquisition.apify.contract import QueryScope, SyntheticCollectorInput
from hw_radar.acquisition.apify.jobs import StartStatus, start_provider_run
from hw_radar.catalog.management.commands.apify_smoke import Command as SmokeCommand
from hw_radar.catalog.models import (
    ApifySpendReservation,
    Category,
    ProviderKind,
    ProviderRun,
    SourceConfig,
    SourceSite,
    SourceTier,
)
from hw_radar.catalog.models.provider import ImportState, ReservationStatus

# transaction=True: the jobs write from sync_to_async threads.
# serialized_rollback=True keeps the migration-seeded rows (categories, sites).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

FIXTURE: Final = (
    Path(__file__).resolve().parents[1] / "fixtures" / "apify_contract" / "v1" / "complete.json"
)
ACTOR_ID: Final = "acct~hw-radar-synthetic-collector"
TOKEN: Final = "apify_api_f5a_test_token"
COMMIT: Final = "0123456789abcdef0123456789abcdef01234567"

# Everything a live start needs beyond priced(): the kill switch, the Actor,
# this environment's ledger id, and the three caps with no default. 3600 s is
# the timeout cap the ledger tests price with; the synthetic run asks for 300.
LIVE_SMOKE: Final[dict[str, object]] = {
    "HW_RADAR_APIFY_ENABLED": True,
    "HW_RADAR_APIFY_ACTOR_ID": ACTOR_ID,
    "HW_RADAR_APIFY_LEDGER_ID": LEDGER_A,
    "HW_RADAR_APIFY_MAX_TIMEOUT_S": 3600,
    "HW_RADAR_APIFY_MAX_KV_WRITES": 3,
    "HW_RADAR_APIFY_MAX_KV_BYTES": 65536,
}


class SmokeApify:
    """Serves one synthetic run the way the real Actor would answer this input.

    The start request's input is echoed into OUTPUT.queryScope and each
    dataset row, and its query options into run.options, so the real
    importer's scope check and the start job's MS2-D-26 options check both
    see a faithful run. `terminal_status` is what every poll answers;
    `output=False` serves no OUTPUT record (the importer then rejects the
    import as missing_output). Storage deletes succeed. No account endpoint
    is served: the runtime reads none (MS2-D-48).
    """

    def __init__(self, *, terminal_status: str = "SUCCEEDED", output: bool = True) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self._rows: list[dict[str, Any]] = fixture["datasetItems"]
        self._output: dict[str, Any] = fixture["output"]
        self.terminal_status = terminal_status
        self.serve_output = output
        self.requests: list[httpx.Request] = []
        self.started_input: dict[str, Any] = {}
        self._options: dict[str, Any] = {}

    def client(self) -> ApifyClient:
        return ApifyClient(TOKEN, transport=httpx.MockTransport(self))

    def starts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST" and r.url.path.endswith("/runs")]

    def _run_body(self, status: str) -> dict[str, Any]:
        return {
            "data": {
                "id": "smoke-1",
                "actId": "act-1",
                "status": status,
                "startedAt": "2026-09-25T12:00:00Z",
                "finishedAt": None,
                "buildId": "build-1",
                "buildNumber": "1.0.7",
                "defaultDatasetId": "ds-smoke-1",
                "defaultKeyValueStoreId": "kv-smoke-1",
                "options": self._options,
                "stats": {"restartCount": 0},
                "usageTotalUsd": 0.001,
            }
        }

    def _scope(self) -> dict[str, Any]:
        """The admitted QueryScope of the started input, in wire (camelCase) form."""
        sent = SyntheticCollectorInput.model_validate(self.started_input)
        return QueryScope.model_validate(
            {name: getattr(sent, name) for name in QueryScope.model_fields}
        ).model_dump(mode="json")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == f"/v2/actors/{ACTOR_ID}/runs":
            self.started_input = json.loads(request.content)
            params = request.url.params
            self._options = {
                "memoryMbytes": int(params["memory"]),
                "timeoutSecs": int(params["timeout"]),
                "build": params.get("build"),
            }
            return httpx.Response(201, json=self._run_body("READY"))
        if request.method == "GET" and path == "/v2/actor-runs/smoke-1":
            return httpx.Response(200, json=self._run_body(self.terminal_status))
        if request.method == "DELETE":
            return httpx.Response(204)
        scope = self._scope()
        if path.endswith("/items"):
            rows = [
                row
                | {
                    "siteKey": scope["siteKey"],
                    "collectionScope": scope["collectionScope"],
                    "categoryHint": scope["categoryHint"],
                }
                for row in self._rows
            ]
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            return httpx.Response(
                200,
                json=rows[offset : offset + limit],
                headers={"X-Apify-Pagination-Total": str(len(rows))},
            )
        if path.endswith("/records/OUTPUT"):
            if not self.serve_output:
                return httpx.Response(404, json={"error": {"type": "record-not-found"}})
            return httpx.Response(200, json=self._output | {"queryScope": scope})
        return httpx.Response(500, json={"error": {"type": "unexpected", "message": path}})


class FakeClock:
    """A monotonic clock that advances by `step` seconds on every read."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def _smoke(fake: SmokeApify, *args: str, clock: FakeClock | None = None) -> str:
    out = StringIO()
    command = SmokeCommand(
        client_factory=fake.client,
        sleep=lambda _s: None,
        monotonic=clock or FakeClock(0.0),
    )
    call_command(command, "--fixture-commit", COMMIT, *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def _smoke_fails(fake: SmokeApify, *args: str, match: str) -> str:
    out = StringIO()
    command = SmokeCommand(
        client_factory=fake.client, sleep=lambda _s: None, monotonic=FakeClock(0.0)
    )
    with pytest.raises(CommandError, match=match):
        call_command(command, *args, stdout=out, stderr=StringIO())
    return out.getvalue()


@pytest.fixture
def synthetic_config() -> SourceConfig:
    call_command("apify_synthetic_setup", stdout=StringIO())
    return SourceConfig.objects.select_related("source_site").get(
        source_site__normalized_name=synthetic.SITE_KEY
    )


@pytest.fixture
def live() -> Iterator[None]:
    """Every setting a live start needs, and the current cycle's authority claimed."""
    with override_settings(**priced(), **LIVE_SMOKE):
        claim(live_cycle_start())
        yield


# ── apify_synthetic_setup ────────────────────────────────────────────────────


def test_setup_creates_a_disabled_actor_backed_site_and_is_idempotent() -> None:
    first, second = StringIO(), StringIO()
    call_command("apify_synthetic_setup", stdout=first)
    config = SourceConfig.objects.select_related("source_site").get(
        source_site__normalized_name="synthetic"
    )
    before = (config.updated_at, config.source_site.updated_at)

    call_command("apify_synthetic_setup", stdout=second)

    assert "created" in first.getvalue()
    assert "already present (no change)" in second.getvalue()
    assert SourceSite.objects.filter(normalized_name="synthetic").count() == 1
    assert SourceConfig.objects.filter(source_site__normalized_name="synthetic").count() == 1
    config.refresh_from_db()
    config.source_site.refresh_from_db()
    assert (config.updated_at, config.source_site.updated_at) == before
    assert (
        config.collection_provider,
        config.enabled,
        config.heartbeat_enabled,
        config.fast_lane,
    ) == (ProviderKind.APIFY, False, False, False)
    # The poller schedules and recovery-probes only enabled configs
    # (poller.service.run, recovery_probe_job), so this site is never started
    # by a scheduled job.
    assert not SourceConfig.objects.filter(enabled=True, pk=config.pk).exists()


def test_setup_reports_drift_and_rewrites_nothing(synthetic_config: SourceConfig) -> None:
    SourceConfig.objects.filter(pk=synthetic_config.pk).update(enabled=True)

    with pytest.raises(CommandError, match=r"drift.*enabled=True"):
        call_command("apify_synthetic_setup", stdout=StringIO())

    synthetic_config.refresh_from_db()
    assert synthetic_config.enabled is True


def test_synthetic_category_hint_is_a_seeded_category() -> None:
    assert Category.objects.filter(slug=synthetic.CATEGORY_HINT).exists()


# ── production refusal ───────────────────────────────────────────────────────


@override_settings(IS_PRODUCTION=True)
def test_both_commands_refuse_in_production() -> None:
    fake = SmokeApify()
    with pytest.raises(CommandError, match="production"):
        call_command("apify_synthetic_setup", stdout=StringIO())
    _smoke_fails(fake, "--fixture-commit", COMMIT, match="production")

    assert not SourceSite.objects.filter(normalized_name="synthetic").exists()
    assert fake.requests == []
    assert not ProviderRun.objects.exists()


def test_production_like_config_is_refused_before_any_spend() -> None:
    """The shipped RUN_SPECS entry is inert: no fixture commit means no spec.

    Production has no synthetic SourceConfig and no fixture-commit setting.
    Even with the kill switch on, an Actor id set, and the production ledger
    binding, an Actor-backed merchant site and the synthetic site are both
    refused `no_run_spec`, and a malformed commit is refused `invalid_input`,
    each before a client is built or a ledger row written.
    """
    built: list[ApifyClient] = []

    def factory() -> ApifyClient:
        client = SmokeApify().client()
        built.append(client)
        return client

    merchant = SourceSite.objects.create(name="Merchant", normalized_name="merchant-x")
    rows = [
        SourceConfig.objects.create(
            source_site=site,
            tier=SourceTier.T2_SPECIALIST,
            domain=f"{site.normalized_name}.invalid",
            cadence_baseline_s=3600,
            cadence_ceiling_s=900,
            collection_provider=ProviderKind.APIFY,
        )
        for site in (merchant, SourceSite.objects.create(name="Syn", normalized_name="synthetic"))
    ]
    configs = list(
        SourceConfig.objects.select_related("source_site").filter(pk__in=[r.pk for r in rows])
    )

    def start(config: SourceConfig) -> jobs.StartResult:
        return asyncio.run(start_provider_run(config, client_factory=factory))

    with override_settings(HW_RADAR_APIFY_ENABLED=True, HW_RADAR_APIFY_ACTOR_ID=ACTOR_ID):
        unset = [start(config) for config in configs]
        with override_settings(HW_RADAR_APIFY_SYNTHETIC_FIXTURE_COMMIT="not-a-sha"):
            malformed = start(
                next(c for c in configs if c.source_site.normalized_name == "synthetic")
            )

    assert [(r.status, r.reason) for r in unset] == [(StartStatus.REFUSED, "no_run_spec")] * 2
    assert malformed.status is StartStatus.REFUSED
    assert malformed.reason.startswith("invalid_input")
    assert built == []
    assert not ProviderRun.objects.exists()
    assert not ApifySpendReservation.objects.exists()


# ── apify_smoke ──────────────────────────────────────────────────────────────


def test_smoke_runs_admission_start_poll_and_import(
    synthetic_config: SourceConfig, live: None
) -> None:
    fake = SmokeApify()

    out = _smoke(fake)

    [start] = fake.starts()
    assert (start.url.params["build"], start.url.params["memory"], start.url.params["timeout"]) == (
        "candidate",
        "256",
        "300",
    )
    sent = SyntheticCollectorInput.model_validate(fake.started_input)
    assert (sent.fixture_commit, sent.fault_mode, sent.collection_scope) == (
        COMMIT,
        "none",
        "synthetic:drive:catalog",
    )
    row = ProviderRun.objects.get()
    resv = ApifySpendReservation.objects.get(provider_run=row)
    assert (row.import_state, row.completeness) == (ImportState.FINALIZED, "complete")
    # The run poll fed the ledger its usage (reconcile.record_run_usage);
    # storage cleanup and settlement are later ticks' work.
    assert resv.status == ReservationStatus.USAGE_PROVISIONAL
    lines = out.splitlines()
    assert lines[0] == "start: started"
    for expected in (
        f"provider_run: {row.pk} (external run smoke-1)",
        "remote_status: SUCCEEDED",
        "build_number: 1.0.7",
        "import_state: finalized",
        "completeness: complete (complete)",
        "truncation_reason: -",
        "rows_emitted: 3",
        "dataset_item_count: 3",
        "rows_imported: 3",
    ):
        assert expected in lines
    assert (
        f"reservation: {resv.pk} status=usage_provisional estimate_usd={resv.estimate_usd}" in out
    )
    # The smoke never touches the config: it stays disabled and unscheduled.
    synthetic_config.refresh_from_db()
    assert synthetic_config.enabled is False


@pytest.mark.parametrize(
    ("settings_changes", "status", "reason", "ledger_rows"),
    [
        ({"HW_RADAR_APIFY_ENABLED": False}, "refused", "apify_disabled", 0),
        # Larger than the 300 s the synthetic run asks for: the ledger denies
        # and records the denial, and admits nothing.
        ({"HW_RADAR_APIFY_MAX_TIMEOUT_S": 200}, "denied", "unbounded_component", 1),
    ],
    ids=["kill-switch-off", "timeout-over-cap"],
)
def test_smoke_denial_prints_the_reason_and_starts_nothing(
    synthetic_config: SourceConfig,
    live: None,
    settings_changes: dict[str, object],
    status: str,
    reason: str,
    ledger_rows: int,
) -> None:
    fake = SmokeApify()

    with override_settings(**settings_changes):
        out = _smoke_fails(fake, "--fixture-commit", COMMIT, match=f"{status}: {reason}")

    assert out.splitlines() == [f"start: {status} {reason}"]
    assert fake.requests == []
    assert not ProviderRun.objects.exists()
    assert not ApifySpendReservation.objects.exclude(status=ReservationStatus.DENIED).exists()
    assert ApifySpendReservation.objects.count() == ledger_rows


def test_smoke_without_the_site_names_the_setup_command(live: None) -> None:
    fake = SmokeApify()
    _smoke_fails(fake, "--fixture-commit", COMMIT, match="apify_synthetic_setup")
    assert fake.requests == []


def test_smoke_fails_when_fault_mode_none_is_rejected(
    synthetic_config: SourceConfig, live: None
) -> None:
    fake = SmokeApify(output=False)

    out = _smoke_fails(fake, "--fixture-commit", COMMIT, match="smoke failed.*rejected")

    assert "import_state: rejected" in out.splitlines()
    assert "reject_reason: " in out


def test_smoke_with_a_fault_mode_exits_zero_once_the_import_is_terminal(
    synthetic_config: SourceConfig, live: None
) -> None:
    # The fake ignores the fault mode; what is pinned is the exit rule: a
    # fault run is an experiment whose verdict is the printed checks.
    fake = SmokeApify(output=False)

    out = _smoke(fake, "--fault-mode", "fail")

    assert SyntheticCollectorInput.model_validate(fake.started_input).fault_mode == "fail"
    assert "import_state: rejected" in out.splitlines()


def test_smoke_gives_up_after_the_bounded_wait(synthetic_config: SourceConfig, live: None) -> None:
    fake = SmokeApify(terminal_status="RUNNING")
    command = SmokeCommand(
        client_factory=fake.client, sleep=lambda _s: None, monotonic=FakeClock(1.0)
    )
    out = StringIO()

    with pytest.raises(CommandError, match="import still pending after 3s"):
        call_command(
            command, "--fixture-commit", COMMIT, "--wait-secs", "3", stdout=out, stderr=StringIO()
        )

    assert "remote_status: RUNNING" in out.getvalue().splitlines()
    assert len(fake.starts()) == 1


@pytest.mark.parametrize(
    ("args", "match"),
    [
        (["--fixture-commit", "abc123"], "40-hex"),
        (["--fixture-commit", COMMIT.upper()], "40-hex"),
        (["--fixture-commit", COMMIT, "--fault-mode", "explode"], "invalid choice"),
        (["--fixture-commit", COMMIT, "--max-items", "501"], "(?s)invalid run input.*maxItems"),
        (["--fixture-commit", COMMIT, "--max-pages", "0"], "(?s)invalid run input.*maxPages"),
        (["--fixture-commit", COMMIT, "--max-requests", "101"], "invalid run input"),
        (["--fixture-commit", COMMIT, "--max-bytes", "10000001"], "invalid run input"),
        (["--fixture-commit", COMMIT, "--time-budget-secs", "9"], "invalid run input"),
        (["--fixture-commit", COMMIT, "--build", ""], "not a build tag"),
        (["--fixture-commit", COMMIT, "--build", "prod tag"], "not a build tag"),
        (["--fixture-commit", COMMIT, "--wait-secs", "0"], "--wait-secs"),
        (["--fixture-commit", COMMIT, "--wait-secs", "3601"], "--wait-secs"),
        (["--fixture-commit", COMMIT, "--poll-secs", "61"], "--poll-secs"),
    ],
)
def test_smoke_validates_its_options_before_anything_runs(
    synthetic_config: SourceConfig, live: None, args: list[str], match: str
) -> None:
    fake = SmokeApify()
    _smoke_fails(fake, *args, match=match)
    assert fake.requests == []
    assert not ProviderRun.objects.exists()
    assert not ApifySpendReservation.objects.exists()


def test_smoke_accepts_bounded_cap_overrides(synthetic_config: SourceConfig, live: None) -> None:
    fake = SmokeApify()

    _smoke(fake, "--max-items", "500", "--max-pages", "50", "--build", "prod")

    sent = SyntheticCollectorInput.model_validate(fake.started_input)
    assert (sent.max_items, sent.max_pages) == (500, 50)
    assert fake.starts()[0].url.params["build"] == "prod"
    row = ProviderRun.objects.get()
    assert (row.max_items, row.max_pages) == (500, 50)
