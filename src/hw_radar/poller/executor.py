# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportPrivateUsage=false, reportAttributeAccessIssue=false, reportImplicitOverride=false
# APScheduler 3.x ships no py.typed/stubs, and the one hook this module needs
# (_do_submit_job, job._jobstore_alias) is executor-private; keep these
# exact-rule exceptions scoped here.
"""APScheduler executor that gives every poller job a usable database connection.

Django recycles connections only at HTTP request boundaries (the
request_started / request_finished signals call close_old_connections). The
poller is not a request handler, so without this executor a connection that
breaks under it — the server restarting, a network drop, an idle timeout —
stays cached in its thread forever, and every later job in that thread fails
with `OperationalError: the connection is closed`. That is what happened on
2026-10-03: PostgreSQL was OOM-killed and restarted, and the poller kept
failing every job until the service was restarted by hand.

ConnectionHygieneExecutor runs close_old_connections before and after every
job, i.e. it applies Django's request lifecycle to a scheduled job. With the
project's CONN_MAX_AGE of 0 that closes each job's connection when the job
ends, so the next job always connects afresh; with persistent connections it
discards one that errored and fails its usability check, so at most the job
that discovered the loss fails.

Which thread is cleaned is the whole point, because Django connections are
per thread:

- A coroutine job's ORM work runs through asgiref's sync_to_async, whose
  default thread_sensitive=True funnels every call made outside an
  async_to_sync context — the poller's case — into ONE process-wide executor
  thread. The cleanup is therefore itself dispatched with sync_to_async, so it
  lands on that same thread. A job that used sync_to_async(...,
  thread_sensitive=False) would run on other threads this executor never
  cleans; none does today, and none should.
- A plain-function job runs in the event loop's default executor; the cleanup
  runs inside the same worker call, so it cleans that worker thread.

Rejected alternatives: wrapping each job function at add_job (job.func must
stay the real function — callers and tests look jobs up and compare or call
job.func directly); an EVENT_JOB_* listener (listeners run synchronously on
the loop thread, which owns no ORM connection and cannot await the
sync_to_async thread); and Django's CONN_HEALTH_CHECKS alone (it only runs
once close_old_connections has reset the per-"request" health-check flag,
which nothing in the poller ever does).

Cross-library contract: _do_submit_job mirrors
apscheduler.executors.asyncio.AsyncIOExecutor._do_submit_job (APScheduler
3.11), changing only the callable it schedules; the job-event semantics
(EVENT_JOB_EXECUTED / ERROR / MISSED via run_job and run_coroutine_job) are
APScheduler's own. Re-check this method when APScheduler is upgraded.
tests/db/test_poller_db_recovery.py proves recovery for both job kinds.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.base import run_coroutine_job, run_job
from apscheduler.util import iscoroutinefunction_partial
from asgiref.sync import sync_to_async
from django.db import close_old_connections

if TYPE_CHECKING:
    from apscheduler.job import Job

logger = logging.getLogger(__name__)


def recycle_connections() -> None:
    """Close this thread's connections that are broken or past CONN_MAX_AGE.

    Never raises: closing a connection whose server is gone can itself raise,
    but Django drops the wrapper's handle in a `finally` either way, so the
    next ORM call reconnects. Letting the error escape would instead fail a
    job over a connection it was about to replace anyway.
    """
    try:
        close_old_connections()
    except Exception:
        logger.warning("closing a stale database connection failed", exc_info=True)


async def _run_coroutine_job(
    job: Job, jobstore_alias: str | None, run_times: Sequence[datetime], logger_name: str
) -> list[Any]:
    # Both cleanups go through sync_to_async so they run on the thread that
    # owns the job's ORM connection (module docstring).
    await sync_to_async(recycle_connections)()
    try:
        return await run_coroutine_job(job, jobstore_alias, run_times, logger_name)
    finally:
        await sync_to_async(recycle_connections)()


def _run_job(
    job: Job, jobstore_alias: str | None, run_times: Sequence[datetime], logger_name: str
) -> list[Any]:
    recycle_connections()
    try:
        return run_job(job, jobstore_alias, run_times, logger_name)
    finally:
        recycle_connections()


class ConnectionHygieneExecutor(AsyncIOExecutor):
    """AsyncIOExecutor that recycles Django DB connections around every job."""

    def _do_submit_job(self, job: Job, run_times: Sequence[datetime]) -> None:
        def callback(f) -> None:
            self._pending_futures.discard(f)
            try:
                events = f.result()
            except BaseException:
                self._run_job_error(job.id, *sys.exc_info()[1:])
            else:
                self._run_job_success(job.id, events)

        if iscoroutinefunction_partial(job.func):
            coro = _run_coroutine_job(job, job._jobstore_alias, run_times, self._logger.name)
            f = self._eventloop.create_task(coro)
        else:
            f = self._eventloop.run_in_executor(
                None, _run_job, job, job._jobstore_alias, run_times, self._logger.name
            )

        f.add_done_callback(callback)
        self._pending_futures.add(f)
