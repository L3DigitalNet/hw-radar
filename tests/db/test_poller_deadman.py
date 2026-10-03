# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# APScheduler 3.x is untyped (see tests/unit/test_poller.py).
"""The dead-man push reports a poller that can reach its database, not a live process.

Spec §18.5 makes the push a dead-man's switch that alerts on the *absence of
success* ("No successful run heartbeat in window -> investigate poller"), and
ADR-0017 relies on it so that "a stalled poller ... reaches a human off the
box". On 2026-10-03 every collection job failed on a dead DB connection while
the push kept firing, so the off-box monitor stayed green. These tests drive
the real deadman-push job through the scheduler's executor.
"""

from __future__ import annotations

import asyncio

import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, JobExecutionEvent
from django.db import connection

from hw_radar.acquisition import deadman
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.poller.service import build_scheduler

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
