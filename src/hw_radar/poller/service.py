# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# APScheduler 3.x ships no py.typed/stubs; keep these exact-rule exceptions scoped here.
"""Poller service implementation (ADR-0012): one systemd-supervised process.

Split out of ``poller/__init__.py`` so the package root stays import-light: the
module-level ``from hw_radar.catalog.models import ...`` below needs Django
configured, and this module is only imported *after* that — by ``__main__.py``
(which calls ``django.setup()`` first) and by tests (pytest-django configures
settings during collection). That is why the package ``__init__`` no longer
bootstraps Django at import time; see ``poller/__init__.py``.

Per-source interval jobs are registered from SourceConfig rows; the admission
gate (buckets → back-off → lifecycle) runs inside each job, so a denied tick
is cheap. Auto-ramp/back-off changes to a lane's current_interval_s reschedule
that lane's job in place. Django ORM calls go through sync_to_async.

Scheduling state is per lane (ADR-0020): each poll path reads and writes only
its own SourceLaneState row, so a repair-crawl failure cannot reset the
heartbeat's earned cadence or impose its back-off window, or the reverse.
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from asgiref.sync import sync_to_async
from django.utils import timezone

from hw_radar.acquisition import deadman, fx
from hw_radar.acquisition.contracts import adapter_retention
from hw_radar.acquisition.heartbeat import HeartbeatProbe, run_heartbeat
from hw_radar.acquisition.pipeline import run_source
from hw_radar.acquisition.scheduling.admission import check_admission
from hw_radar.acquisition.scheduling.apply import apply_run_outcome
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.acquisition.scheduling.checkpoint import load_buckets, save_buckets
from hw_radar.acquisition.scrapy_support import install_asyncio_reactor
from hw_radar.acquisition.sources import ADAPTERS
from hw_radar.catalog.management.commands.purge_expired import sweep_expired
from hw_radar.catalog.models import (
    CheapSignal,
    LifecycleState,
    RunKind,
    SchedulingLane,
    SourceConfig,
)
from hw_radar.matching.resolver import CatalogResolver
from hw_radar.refdata import refresh as refdata_refresh

if TYPE_CHECKING:
    from apscheduler.job import Job

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 60
DEADMAN_SECONDS = 60
CHECKPOINT_SECONDS = 60
FX_REFRESH_HOUR_UTC = 6
RECOVERY_PROBE_SECONDS = 86_400  # ADR-0017: daily recovery probe for paused sources
REFDATA_REFRESH_DAY = 1  # monthly-order cadence, its own axis (ADR-0018 rule 3)
REFDATA_REFRESH_HOUR_UTC = 7  # after the 06:00 FX refresh
# DR-001 sweep cadence. Hourly is chosen against the tightest bound we carry:
# DR-008 gives eBay observations a 6h TTL, so an expired row outlives its window
# by at most one interval — a daily sweep would stretch that to 30h and break the
# carve-out. The sweep is a handful of indexed DELETEs, so it is cheap to repeat.
RETENTION_SWEEP_SECONDS = 3_600


def heartbeat() -> None:
    logger.info("poller heartbeat: alive")


async def poll_source(site_key: str, registry: BucketRegistry, scheduler: AsyncIOScheduler) -> None:
    config = await sync_to_async(SourceConfig.objects.select_related("source_site").get)(
        source_site__normalized_name=site_key
    )
    lane_state = await sync_to_async(config.lane_state)(SchedulingLane.FULL)
    decision = check_admission(
        enabled=config.enabled,
        lifecycle_state=LifecycleState(config.lifecycle_state),
        run_kind=RunKind.FULL,
        backoff_until=lane_state.backoff_until,
        now=timezone.now(),
        registry=registry,
        source_key=site_key,
        domain=config.domain,
        now_s=time.monotonic(),
    )
    if not decision.admitted:
        logger.info("source %s not admitted: %s", site_key, decision.reason)
        return
    factory = ADAPTERS.get(site_key)
    if factory is None:
        logger.warning("source %s enabled but has no adapter registered", site_key)
        return
    # DR-001/DR-008: the adapter's own retention must ride along, or run_source
    # defaults every persisted row to indefinite merchant_fact — eBay evidence
    # from a scheduled poll would then outlive its 6h window forever, unreachable
    # by the retention sweeper. tests/db/test_poller_retention_wiring.py pins it.
    adapter = factory()
    retention = adapter_retention(adapter)
    _run, outcome = await run_source(
        adapter,
        CatalogResolver(),
        retention_class=retention.retention_class,
        expires_policy=retention.expires_policy,
    )
    interval_before = lane_state.current_interval_s
    await sync_to_async(apply_run_outcome)(
        config, outcome, lane_state=lane_state, now=timezone.now(), rand=random.random
    )
    if lane_state.current_interval_s != interval_before:
        job: Job | None = scheduler.get_job(f"poll-{site_key}")
        if job is not None:
            scheduler.reschedule_job(
                f"poll-{site_key}",
                trigger="interval",
                seconds=lane_state.current_interval_s,
                jitter=max(1, lane_state.current_interval_s // 10),
            )
            logger.info(
                "source %s rescheduled: %ss → %ss",
                site_key,
                interval_before,
                lane_state.current_interval_s,
            )


async def poll_heartbeat(
    site_key: str, registry: BucketRegistry, scheduler: AsyncIOScheduler
) -> None:
    """ADR-0015 fast lane. Mirrors poll_source fully (CR-006 residual): the same
    admission gate (run_kind=HEARTBEAT), apply_run_outcome, and in-place interval
    reschedule — only the poll-HEARTBEAT job is retargeted, and run_heartbeat
    (not run_source) fires the full pipeline solely on a detected transition.

    Every scheduling read and write here is against the HEARTBEAT lane row
    (ADR-0020); the repair crawl's full-lane row is untouched."""
    config = await sync_to_async(SourceConfig.objects.select_related("source_site").get)(
        source_site__normalized_name=site_key
    )
    lane_state = await sync_to_async(config.lane_state)(SchedulingLane.HEARTBEAT)
    decision = check_admission(
        enabled=config.enabled,
        lifecycle_state=LifecycleState(config.lifecycle_state),
        run_kind=RunKind.HEARTBEAT,
        backoff_until=lane_state.backoff_until,
        now=timezone.now(),
        registry=registry,
        source_key=site_key,
        domain=config.domain,
        now_s=time.monotonic(),
    )
    if not decision.admitted:
        logger.info("heartbeat %s not admitted: %s", site_key, decision.reason)
        return
    factory = ADAPTERS.get(site_key)
    if factory is None:
        logger.warning("source %s heartbeat-enabled but has no adapter registered", site_key)
        return
    # Only heartbeat_enabled sources ever schedule this job, so the adapter is a
    # HeartbeatProbe; the ADAPTERS registry only knows the base SourceAdapter type.
    adapter = cast("HeartbeatProbe", factory())
    outcome = await run_heartbeat(adapter, config, CatalogResolver())
    interval_before = lane_state.current_interval_s
    await sync_to_async(apply_run_outcome)(
        config, outcome, lane_state=lane_state, now=timezone.now(), rand=random.random
    )
    if lane_state.current_interval_s != interval_before:
        job_id = f"poll-heartbeat-{site_key}"
        job: Job | None = scheduler.get_job(job_id)
        if job is not None:
            scheduler.reschedule_job(
                job_id,
                trigger="interval",
                seconds=lane_state.current_interval_s,
                jitter=max(1, lane_state.current_interval_s // 10),
            )
            logger.info(
                "heartbeat %s rescheduled: %ss → %ss",
                site_key,
                interval_before,
                lane_state.current_interval_s,
            )


async def refresh_fx_job() -> None:
    stored = await fx.refresh_daily()
    logger.info("fx refresh: %s pairs stored", stored)


async def deadman_job() -> None:
    await deadman.push()


async def checkpoint_job(registry: BucketRegistry) -> None:
    await sync_to_async(save_buckets)(registry)


async def recovery_probe_job(registry: BucketRegistry) -> None:
    """ADR-0017: paused_pending_fix sources get a daily probe; success reactivates."""
    paused = await sync_to_async(
        lambda: list(
            SourceConfig.objects.select_related("source_site").filter(
                enabled=True, lifecycle_state=LifecycleState.PAUSED_PENDING_FIX
            )
        )
    )()
    for config in paused:
        key = config.source_site.normalized_name
        factory = ADAPTERS.get(key)
        if factory is None:
            continue
        # A probe replays the full pipeline, so it is a full-lane run (ADR-0020).
        lane_state = await sync_to_async(config.lane_state)(SchedulingLane.FULL)
        decision = check_admission(
            enabled=config.enabled,
            lifecycle_state=LifecycleState(config.lifecycle_state),
            run_kind=RunKind.PROBE,
            backoff_until=lane_state.backoff_until,
            now=timezone.now(),
            registry=registry,
            source_key=key,
            domain=config.domain,
            now_s=time.monotonic(),
        )
        if not decision.admitted:
            logger.info("probe for %s not admitted: %s", key, decision.reason)
            continue
        # A probe persists real rows, so it forwards retention exactly as
        # poll_source does; see the comment there for the failure it prevents.
        adapter = factory()
        retention = adapter_retention(adapter)
        _run, outcome = await run_source(
            adapter,
            CatalogResolver(),
            retention_class=retention.retention_class,
            expires_policy=retention.expires_policy,
            run_kind=RunKind.PROBE,
        )
        await sync_to_async(apply_run_outcome)(
            config, outcome, lane_state=lane_state, now=timezone.now(), rand=random.random
        )
        logger.info("recovery probe for %s → %s", key, config.lifecycle_state)


async def refdata_refresh_job() -> None:
    """ADR-0018 monthly reference refresh — slow path, never heartbeat/fast-lane."""
    report = await sync_to_async(refdata_refresh.run_refresh)()
    logger.info("refdata refresh: %s", report.as_json())


async def retention_sweep_job() -> None:
    """DR-001 enforcement pass; shares its implementation with `purge_expired`.

    The function is imported rather than driven through `call_command` so the
    per-table counts come back as data to log instead of command stdout.
    """
    report = await sync_to_async(sweep_expired)()
    logger.info(
        "retention sweep: %s row(s) deleted %s; redacted %s",
        report.total,
        dict(report.counts),
        dict(report.redactions),
    )


@dataclass(frozen=True)
class SourceSchedule:
    """One source's registration input: its policy row plus both lane intervals.

    build_scheduler must stay ORM-free — it is called from inside run()'s event
    loop, where any lazy query would raise SynchronousOnlyOperation — so the
    ADR-0020 lane rows are resolved by load_schedules() on a worker thread and
    handed over as plain integers. heartbeat_interval_s is inert for a source
    with heartbeat_enabled=False; no job reads it until the flag is flipped.
    """

    config: SourceConfig
    full_interval_s: int
    heartbeat_interval_s: int


def load_schedules(configs: Sequence[SourceConfig]) -> list[SourceSchedule]:
    """Read (creating if absent) both lane rows for each config. Sync ORM."""
    return [
        SourceSchedule(
            config=config,
            full_interval_s=config.lane_state(SchedulingLane.FULL).current_interval_s,
            heartbeat_interval_s=config.lane_state(SchedulingLane.HEARTBEAT).current_interval_s,
        )
        for config in configs
    ]


def build_scheduler(
    registry: BucketRegistry, schedules: Sequence[SourceSchedule]
) -> AsyncIOScheduler:
    # Codex CR-003: APScheduler defaults to LOCAL time, so the *_UTC constants
    # above were only aspirational until the scheduler itself is pinned — cron
    # triggers inherit the scheduler's timezone, not UTC, unless told to.
    scheduler = AsyncIOScheduler(
        job_defaults={"max_instances": 1, "coalesce": True}, timezone="UTC"
    )
    scheduler.add_job(heartbeat, "interval", seconds=HEARTBEAT_SECONDS, id="poller-heartbeat")
    scheduler.add_job(refresh_fx_job, "cron", hour=FX_REFRESH_HOUR_UTC, id="fx-refresh")
    scheduler.add_job(deadman_job, "interval", seconds=DEADMAN_SECONDS, id="deadman-push")
    scheduler.add_job(
        checkpoint_job,
        "interval",
        seconds=CHECKPOINT_SECONDS,
        id="bucket-checkpoint",
        args=[registry],
    )
    scheduler.add_job(
        recovery_probe_job,
        "interval",
        seconds=RECOVERY_PROBE_SECONDS,
        id="recovery-probes",
        args=[registry],
    )
    scheduler.add_job(
        refdata_refresh_job,
        "cron",
        day=REFDATA_REFRESH_DAY,
        hour=REFDATA_REFRESH_HOUR_UTC,
        id="refdata-refresh",
    )
    scheduler.add_job(
        retention_sweep_job,
        "interval",
        seconds=RETENTION_SWEEP_SECONDS,
        id="retention-sweep",
    )
    for schedule in schedules:
        config = schedule.config
        key = config.source_site.normalized_name
        registry.configure_source(
            key,
            rate_per_min=config.bucket_rate_per_min,
            burst=config.bucket_burst,
            now_s=time.monotonic(),
        )
        if config.heartbeat_enabled:
            # Fast lane: cheap probe at the heartbeat lane's own interval, gating
            # the full pipeline.
            fast_s = schedule.heartbeat_interval_s
            scheduler.add_job(
                poll_heartbeat,
                "interval",
                seconds=fast_s,
                jitter=max(1, fast_s // 10),
                misfire_grace_time=config.misfire_grace_s,
                id=f"poll-heartbeat-{key}",
                args=[key, registry, scheduler],
            )
            # CR-006: non-eBay heartbeat sources also need a slow full-pipeline
            # repair crawl at cadence_baseline_s — CDN edge cache floors probe
            # freshness, so the heartbeat alone can miss changes. eBay's Browse
            # poll IS both heartbeat and full fetch (natively-both source), so a
            # second poll-{key} job would just double-poll: it stays single-job.
            if config.cheap_signal != CheapSignal.EBAY_BROWSE.value:  # .value: django-types quirk
                # The repair lane's interval is its own row's, which ramp_floor_s
                # pins at cadence_baseline_s for heartbeat sources — the slow end
                # CR-006 asks for, now stated by the lane row rather than by
                # reading cadence_baseline_s here.
                slow_s = schedule.full_interval_s
                scheduler.add_job(
                    poll_source,
                    "interval",
                    seconds=slow_s,
                    jitter=max(1, slow_s // 10),
                    misfire_grace_time=config.misfire_grace_s,
                    id=f"poll-{key}",
                    args=[key, registry, scheduler],
                )
        else:
            full_s = schedule.full_interval_s
            scheduler.add_job(
                poll_source,
                "interval",
                seconds=full_s,
                jitter=max(1, full_s // 10),
                misfire_grace_time=config.misfire_grace_s,
                id=f"poll-{key}",
                args=[key, registry, scheduler],
            )
    return scheduler


async def run(configs: Sequence[SourceConfig] | None = None, *, checkpoint: bool = True) -> None:
    """checkpoint=False + configs=[] is the unit-test mode: no ORM call on the
    startup/shutdown path itself (no bucket load/save, no config query, and
    load_schedules over an empty sequence queries nothing either). The
    registered service jobs (FX refresh, checkpoints, probes) do touch the DB —
    but only when they fire, which a short-lived unit run never reaches
    (tests/unit/test_poller.py drives run(configs=[], checkpoint=False))."""
    install_asyncio_reactor()  # before APScheduler starts; Scrapy shares this loop
    registry = (
        await sync_to_async(load_buckets)(now_s=time.monotonic())
        if checkpoint
        else BucketRegistry()
    )
    if configs is None:
        configs = await sync_to_async(
            lambda: list(SourceConfig.objects.select_related("source_site").filter(enabled=True))
        )()
    schedules = await sync_to_async(load_schedules)(configs)
    scheduler = build_scheduler(registry, schedules)
    scheduler.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("poller started (%s source job(s))", len(configs))
    await stop.wait()
    scheduler.shutdown(wait=False)
    if checkpoint:
        try:
            await sync_to_async(save_buckets)(registry)
        except Exception:  # shutdown must complete even if the DB is gone
            logger.warning("bucket checkpoint on shutdown failed", exc_info=True)
    logger.info("poller stopped")
