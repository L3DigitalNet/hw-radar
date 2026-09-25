"""MS2-D-48 (revision 12, R25): no runtime path requests an Apify account endpoint.

The runtime token cannot read `/v2/users/...` (403), and the owner chose to
drop runtime account reads: the billing cycle and the account state are
operator-verified settings. This drives every paid runtime path through one
recording httpx.MockTransport that refuses any `/v2/users` request, and checks
the ledger's own read counters stayed at zero.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import ledger_support
import pytest
from test_apify_recovery_probe import (
    LIVE,
    LedgerFakeApify,
    _bind_start,  # pyright: ignore[reportPrivateUsage]
    _fixture,  # pyright: ignore[reportPrivateUsage]
    _probe,  # pyright: ignore[reportPrivateUsage]
    _probe_row,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
    _specs,  # pyright: ignore[reportPrivateUsage]
    paused_actor_source,  # noqa: F401  # pyright: ignore[reportUnusedImport]  # fixture
)

from hw_radar.acquisition.apify.budget import OperatorKind
from hw_radar.acquisition.apify.jobs import (
    LedgerAdmission,
    StartStatus,
    TickReport,
    apify_poll_tick,
    start_provider_run,
)
from hw_radar.acquisition.apify.ledger import reserve_operator
from hw_radar.acquisition.apify.reconcile import bind_build
from hw_radar.acquisition.contracts import NullResolver
from hw_radar.catalog.models import (
    ApifyBudgetCycle,
    ApifyCycleDiscovery,
    ApifySpendReservation,
    SourceConfig,
)

# The start job and the poller run their own transactions (sync_to_async), as
# in test_apify_recovery_probe, whose fixtures this module reuses.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

ACCOUNT_PREFIX = "/v2/users"
BUILD_ID = "build-no-account-reads"


class _RecordingFake(LedgerFakeApify):
    """LedgerFakeApify that records every path, refuses account endpoints, and serves one build."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        super().__init__(fixture)
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path.startswith(ACCOUNT_PREFIX):
            # 403 is what the scoped runtime token actually gets (MS2-D-48
            # *Evidence*); the recorded path fails the test either way.
            return httpx.Response(403, json={"error": {"type": "insufficient-permissions"}})
        if request.method == "GET" and path == f"/v2/actor-builds/{BUILD_ID}":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": BUILD_ID,
                        "status": "SUCCEEDED",
                        "startedAt": "2026-01-01T00:00:00.000Z",
                        "finishedAt": "2026-01-01T00:10:00.000Z",
                        "usageTotalUsd": 0.01,
                        "usageUsd": {"ACTOR_COMPUTE_UNITS": 0.01},
                    }
                },
            )
        return super().__call__(request)


def _tick(fake: _RecordingFake, clock: ledger_support.Clock | None = None) -> TickReport:
    return _run(
        apify_poll_tick(
            resolver=NullResolver(),
            client_factory=fake.client,
            ledger_config=ledger_support.live_config(),
            clock=clock,
        )
    )


@LIVE
def test_runtime_paths_never_request_account_endpoints(
    paused_actor_source: SourceConfig,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingFake(_fixture("complete"))
    ledger_support.claim(ledger_support.live_cycle_start())

    # A start denied by admission (anchor unset): no row, no account read.
    unset = ledger_support.config(budget={"billing_cycle_anchor": None})
    denied = _run(
        start_provider_run(
            paused_actor_source,
            admission=LedgerAdmission(config=unset),
            run_specs=_specs(),
            client_factory=fake.client,
        )
    )
    assert (denied.status, denied.reason) == (StartStatus.DENIED, "cycle_unknown")

    # recovery_probe_job starts an admitted run through LedgerAdmission.
    _bind_start(monkeypatch, fake, LedgerAdmission(config=ledger_support.live_config()))
    _probe()
    row = _probe_row()
    resv = ApifySpendReservation.objects.get(provider_run=row)

    # Poll -> import -> storage delete -> settlement -> selector-4 monitoring
    # -> the closing read at the correction deadline.
    _tick(fake)
    _tick(fake)
    row.refresh_from_db()
    assert row.final_charge_op_at is not None
    report = _tick(fake, ledger_support.Clock(row.final_charge_op_at + 2 * ledger_support.HOUR))
    assert report.reconciled == [row.pk]
    resv.refresh_from_db()
    assert resv.next_usage_read_at is not None and resv.correction_monitor_until is not None
    assert _tick(fake, ledger_support.Clock(resv.next_usage_read_at)).monitored == [resv.pk]
    closing = resv.correction_monitor_until + ledger_support.HOUR
    assert _tick(fake, ledger_support.Clock(closing)).monitored == [resv.pk]
    resv.refresh_from_db()
    assert resv.correction_monitor_closed_at is not None

    # An operator build row, bound, then read through selector 2.
    build = reserve_operator(OperatorKind.BUILD, config=ledger_support.live_config(), reason="b")
    assert build.admitted
    assert bind_build(build.reservation_id, BUILD_ID, now=closing)
    _tick(fake, ledger_support.Clock(closing + ledger_support.HOUR))
    assert f"/v2/actor-builds/{BUILD_ID}" in fake.paths

    assert fake.paths
    assert [p for p in fake.paths if p.startswith(ACCOUNT_PREFIX)] == []
    assert list(ApifyBudgetCycle.objects.values_list("account_read_count", flat=True)) == [0]
    assert not ApifyCycleDiscovery.objects.exists()


SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "hw_radar"


@pytest.mark.django_db(transaction=False)
def test_no_runtime_module_references_account_endpoints() -> None:
    # Static half of MS2-D-48: no runtime module may even name an account
    # endpoint, so no future caller can reach one through a helper.
    hits = [
        f"{path.relative_to(SRC_ROOT)}:{n}"
        for path in sorted(SRC_ROOT.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "/v2/users" in line or "users/me" in line
    ]
    assert hits == []
