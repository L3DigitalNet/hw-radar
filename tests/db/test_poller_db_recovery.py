# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false
# APScheduler 3.x is untyped (see tests/unit/test_poller.py).
"""The poller recovers its database connection after the server goes away.

Production incident 2026-10-03: PostgreSQL was OOM-killed and restarted, and
every poller job then raised `OperationalError: the connection is closed`
until the service was restarted. A non-request process gets none of Django's
request-boundary connection recycling, so the scheduler's executor must do it
around every job — in the thread that job's ORM work runs in (the single
sync_to_async thread for coroutine jobs, the loop's default executor thread
for plain functions).

Each scenario severs the live driver connection underneath Django's wrapper —
exactly what a server restart leaves behind — and then requires the jobs that
follow to succeed through the real scheduler built by build_scheduler.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, JobExecutionEvent
from asgiref.sync import sync_to_async
from django.db import connection

from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.catalog.models import SourceSite
from hw_radar.poller.service import build_scheduler

# transaction=True: the jobs' ORM work runs on other threads and their own
# connections, which a test transaction on this thread could not cover.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _query() -> int:
    return SourceSite.objects.count()


def _sever() -> None:
    """Close the driver connection under Django's wrapper, as a server restart does."""
    connection.ensure_connection()
    raw = connection.connection
    assert raw is not None
    raw.close()


def _close() -> None:
    # Resolves `connection` in the calling thread; passing the bound method
    # `connection.close` would capture the submitting thread's wrapper instead.
    connection.close()


async def _async_query_job() -> int:
    return await sync_to_async(_query)()


def _sync_query_job() -> int:
    return _query()


async def _run_jobs(func, count: int, sever) -> list[bool]:
    """Run `func` once, sever, then run it `count` more times; return each run's success."""
    scheduler = build_scheduler(BucketRegistry(), schedules=[])
    # Only the probe job may fire; the registered service jobs stay out of it.
    scheduler.remove_all_jobs()
    events: asyncio.Queue[JobExecutionEvent] = asyncio.Queue()
    scheduler.add_listener(events.put_nowait, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    scheduler.start()
    try:

        async def fire() -> bool:
            scheduler.add_job(func, "date")
            event = await asyncio.wait_for(events.get(), timeout=30)
            return event.exception is None

        outcomes = [await fire()]
        await sever()
        outcomes += [await fire() for _ in range(count)]
        return outcomes
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.parametrize("max_age", [0, None], ids=["conn-max-age-0", "persistent"])
def test_coroutine_job_recovers_after_connection_loss(
    monkeypatch: pytest.MonkeyPatch, max_age: int | None
) -> None:
    # max_age 0 is the project's setting; None (persistent connections) proves
    # the error path too: there the first job after the loss may fail, and the
    # executor must then discard the broken connection so the next one succeeds.
    monkeypatch.setitem(connection.settings_dict, "CONN_MAX_AGE", max_age)

    async def sever() -> None:
        await sync_to_async(_sever)()

    outcomes = asyncio.run(_run_jobs(_async_query_job, 3, sever))
    assert outcomes[0] is True
    if max_age == 0:
        assert outcomes[1:] == [True, True, True]
    else:
        assert outcomes[2:] == [True, True]


@pytest.mark.parametrize("max_age", [0, None], ids=["conn-max-age-0", "persistent"])
def test_sync_job_recovers_after_connection_loss(
    monkeypatch: pytest.MonkeyPatch, max_age: int | None
) -> None:
    # A plain-function job runs in the loop's default executor, on a thread
    # (and connection) of its own. One worker makes "the same thread" exact.
    monkeypatch.setitem(connection.settings_dict, "CONN_MAX_AGE", max_age)

    async def scenario() -> list[bool]:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))

        async def sever() -> None:
            await loop.run_in_executor(None, _sever)

        try:
            return await _run_jobs(_sync_query_job, 3, sever)
        finally:
            # The pool thread holds a connection of its own; close it before
            # asyncio.run retires the thread, or it outlives the test.
            await loop.run_in_executor(None, _close)

    outcomes = asyncio.run(scenario())
    assert outcomes[0] is True
    if max_age == 0:
        assert outcomes[1:] == [True, True, True]
    else:
        assert outcomes[2:] == [True, True]
