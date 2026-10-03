# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# APScheduler 3.x is untyped (see tests/unit/test_poller.py).
"""The dead-man push reports a poller that is collecting, not a live process.

Spec §18.5 makes the push a dead-man's switch that alerts on the *absence of
success* ("No successful run heartbeat in window -> investigate poller"), and
ADR-0017 relies on it so that "a stalled poller ... reaches a human off the
box". On 2026-10-03 every collection job failed on a dead DB connection while
the push kept firing, so the off-box monitor stayed green. The push is also
withheld once an enabled collection source goes without a successful run for
its window. The reachability test drives the real deadman-push job through
the scheduler's executor; the freshness tests call the job directly with an
injected clock and process start.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, JobExecutionEvent
from django.db import connection
from django.utils import timezone

from hw_radar.acquisition import deadman
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.catalog.models import RunKind, RunStatus, ScraperRun, SourceConfig
from hw_radar.poller.service import build_scheduler, deadman_job

# transaction=True: the job's probe runs on the sync_to_async thread and its
# own connection, which a test transaction on this thread could not cover.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

# Port 1 (tcpmux) has no listener on a test host: connecting is refused at
# once, which is what a restarting or OOM-killed PostgreSQL looks like.
UNREACHABLE_PORT = "1"


def test_deadman_push_is_withheld_while_the_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pushes: list[None] = []

    async def fake_push() -> bool:
        pushes.append(None)
        return True

    monkeypatch.setattr(deadman, "push", fake_push)
    real_port = connection.settings_dict["PORT"]

    async def scenario() -> list[int]:
        scheduler = build_scheduler(BucketRegistry(), schedules=[])
        job = scheduler.get_job("deadman-push")
        assert job is not None
        func = job.func
        scheduler.remove_all_jobs()
        events: asyncio.Queue[JobExecutionEvent] = asyncio.Queue()
        scheduler.add_listener(events.put_nowait, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        scheduler.start()
        seen: list[int] = []
        try:
            for port in (real_port, UNREACHABLE_PORT, real_port):
                # The executor closes each job's connection when it ends
                # (CONN_MAX_AGE 0), so the next probe dials this port afresh.
                monkeypatch.setitem(connection.settings_dict, "PORT", port)
                scheduler.add_job(func, "date")
                event = await asyncio.wait_for(events.get(), timeout=30)
                # The job itself must never fail: absence of the push is the
                # signal, and a raising job would only add log noise.
                assert event.exception is None
                seen.append(len(pushes))
        finally:
            scheduler.shutdown(wait=False)
            monkeypatch.setitem(connection.settings_dict, "PORT", real_port)
        return seen

    # One push while reachable, none while unreachable, pushes again on recovery.
    assert asyncio.run(scenario()) == [1, 1, 2]


# ── Collection freshness ──────────────────────────────────────────────────────
#
# eBay is the fixture source because it is the one seeded row the production
# matrix admits (ebay x cpu), so build_scheduler gives it a poll-ebay job. Its
# seeded cadence_baseline_s is 600, so its window is 1800 s (3 x 600, which
# equals the floor); the ages below sit far from that edge on purpose.
EBAY = "ebay"
LONG_AGO = timedelta(days=1)
STALE_AGE = timedelta(hours=2)
FRESH_AGE = timedelta(minutes=10)


def _config(key: str) -> SourceConfig:
    return SourceConfig.objects.select_related("source_site").get(source_site__normalized_name=key)


def _enable(key: str) -> SourceConfig:
    config = _config(key)
    config.enabled = True
    config.save()
    return config


def _record_run(
    key: str,
    *,
    finished_at: datetime,
    status: RunStatus = RunStatus.SUCCESS,
    run_kind: RunKind = RunKind.FULL,
) -> None:
    ScraperRun.objects.create(
        source_site=_config(key).source_site,
        run_kind=run_kind,
        status=status,
        started_at=finished_at - timedelta(seconds=30),
        finished_at=finished_at,
    )


def _pushes_after_deadman(
    monkeypatch: pytest.MonkeyPatch, *, now: datetime, process_started_at: datetime
) -> int:
    pushes: list[None] = []

    async def fake_push() -> bool:
        pushes.append(None)
        return True

    monkeypatch.setattr(deadman, "push", fake_push)
    asyncio.run(deadman_job(process_started_at=process_started_at, clock=lambda: now))
    return len(pushes)


def test_deadman_pushes_while_every_collection_source_is_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    _enable(EBAY)
    _record_run(EBAY, finished_at=now - FRESH_AGE)

    assert _pushes_after_deadman(monkeypatch, now=now, process_started_at=now - LONG_AGO) == 1


def test_deadman_withholds_and_names_a_source_stale_beyond_its_window(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    now = timezone.now()
    config = _enable(EBAY)
    _record_run(EBAY, finished_at=now - STALE_AGE)
    # Decoys that must not count as collection success: a recent failed run, a
    # recent heartbeat-kind success, and last_success_at, which a clean
    # heartbeat probe advances without collecting anything.
    _record_run(EBAY, finished_at=now - FRESH_AGE, status=RunStatus.FAILED)
    _record_run(EBAY, finished_at=now - FRESH_AGE, run_kind=RunKind.HEARTBEAT)
    config.last_success_at = now - FRESH_AGE
    config.save(update_fields=["last_success_at"])

    with caplog.at_level(logging.WARNING, logger="hw_radar.poller.service"):
        pushes = _pushes_after_deadman(monkeypatch, now=now, process_started_at=now - LONG_AGO)

    assert pushes == 0
    withheld = [r.getMessage() for r in caplog.records if "collection stalled" in r.getMessage()]
    assert len(withheld) == 1
    assert "ebay (last success 7200s ago; window 1800s)" in withheld[0]


def test_deadman_withholds_for_a_source_that_never_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    _enable(EBAY)

    assert _pushes_after_deadman(monkeypatch, now=now, process_started_at=now - LONG_AGO) == 0


def test_deadman_pushes_for_a_stale_source_within_the_restart_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A poller restarted five minutes ago has not had one window to run the
    # source yet, so the two-hour-old success must not page anyone.
    now = timezone.now()
    _enable(EBAY)
    _record_run(EBAY, finished_at=now - STALE_AGE)

    started = now - timedelta(minutes=5)
    assert _pushes_after_deadman(monkeypatch, now=now, process_started_at=started) == 1


def test_deadman_ignores_a_disabled_source(monkeypatch: pytest.MonkeyPatch) -> None:
    now = timezone.now()
    _record_run(EBAY, finished_at=now - STALE_AGE)
    assert _config(EBAY).enabled is False

    assert _pushes_after_deadman(monkeypatch, now=now, process_started_at=now - LONG_AGO) == 1


def test_deadman_pushes_with_no_enabled_collection_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    assert not SourceConfig.objects.filter(enabled=True).exists()

    assert _pushes_after_deadman(monkeypatch, now=now, process_started_at=now - LONG_AGO) == 1


def test_deadman_ignores_an_enabled_source_the_matrix_never_schedules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # demo is a fixture source with no admitted category: build_scheduler
    # builds it no job, so its silence is not a stall.
    now = timezone.now()
    _enable("demo")

    assert _pushes_after_deadman(monkeypatch, now=now, process_started_at=now - LONG_AGO) == 1


def test_deadman_ignores_a_heartbeat_only_source(
    monkeypatch: pytest.MonkeyPatch,
    nothing_admitted: None,
    admit: Callable[..., None],
) -> None:
    # eBay with only drive admitted is collected by its heartbeat lane alone
    # (no poll-ebay job), and a clean probe records no run, so it has no
    # collection-run evidence to judge.
    admit((EBAY, "drive"))
    now = timezone.now()
    _enable(EBAY)

    assert _pushes_after_deadman(monkeypatch, now=now, process_started_at=now - LONG_AGO) == 1
