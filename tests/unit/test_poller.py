# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportPrivateUsage=false, reportUnknownArgumentType=false
# APScheduler 3.x is untyped, and the ADR-0012 job-defaults contract is only exposed on _job_defaults.
# job.trigger.timezone (cron trigger's tz) is likewise untyped, hence reportUnknownArgumentType.
import asyncio
import logging
import os
import signal
import subprocess
import sys

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.catalog.models import CheapSignal, SourceConfig, SourceSite
from hw_radar.poller.service import (
    HEARTBEAT_SECONDS,
    SourceSchedule,
    build_scheduler,
    heartbeat,
    run,
)


def _mem_schedule(
    key: str,
    *,
    heartbeat_enabled: bool,
    cheap_signal: CheapSignal,
    heartbeat_interval_s: int,
    full_interval_s: int,
) -> SourceSchedule:
    # In-memory (never saved) config + explicit lane intervals: build_scheduler is
    # sync, reads only attributes and config.source_site.normalized_name, and
    # (ADR-0020) must never resolve a lane row itself, so no DB is touched.
    config = SourceConfig()
    site = SourceSite()
    site.normalized_name = key
    site.name = key
    config.source_site = site
    config.heartbeat_enabled = heartbeat_enabled
    config.cheap_signal = cheap_signal
    config.cadence_baseline_s = full_interval_s
    config.misfire_grace_s = 60
    config.bucket_rate_per_min = 6.0
    config.bucket_burst = 3
    return SourceSchedule(
        config=config,
        full_interval_s=full_interval_s,
        heartbeat_interval_s=heartbeat_interval_s,
    )


def test_poller_package_init_is_import_light() -> None:
    # Regression guard for the service-extraction refactor. `python -m hw_radar.poller`
    # makes runpy import the poller PACKAGE (__init__) to locate __main__, BEFORE
    # __main__ runs django.setup(). That package import must stay ORM-free — the models
    # import now lives in poller.service, reached only after __main__ configures Django.
    # Proven in a clean subprocess with DJANGO_SETTINGS_MODULE unset: importing the
    # package must succeed and must NOT pull hw_radar.catalog.models into sys.modules.
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, hw_radar.poller\n"
            "assert 'hw_radar.catalog.models' not in sys.modules, "
            "sorted(m for m in sys.modules if m.startswith('hw_radar'))",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def empty_scheduler() -> AsyncIOScheduler:
    return build_scheduler(BucketRegistry(), schedules=[])


def test_heartbeat_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="hw_radar.poller"):
        heartbeat()
    assert "heartbeat" in caplog.text


def test_scheduler_registers_heartbeat_job() -> None:
    scheduler = empty_scheduler()
    job = scheduler.get_job("poller-heartbeat")
    assert job is not None
    assert job.trigger.interval.total_seconds() == HEARTBEAT_SECONDS


def test_scheduler_job_defaults_follow_adr_0012() -> None:
    scheduler = empty_scheduler()
    assert scheduler._job_defaults["max_instances"] == 1
    assert scheduler._job_defaults["coalesce"] is True


def test_service_jobs_always_registered() -> None:
    scheduler = empty_scheduler()
    for job_id in (
        "poller-heartbeat",
        "fx-refresh",
        "deadman-push",
        "bucket-checkpoint",
        "recovery-probes",
        "refdata-refresh",
    ):
        assert scheduler.get_job(job_id) is not None, job_id


def test_run_shuts_down_cleanly_on_sigterm(caplog: pytest.LogCaptureFixture) -> None:
    # Faithful to the ADR-0012 systemd contract: systemd stops the unit with SIGTERM,
    # so run() must install the handler, break its wait loop, and shut the scheduler
    # down. Driving the real signal exercises the supervision path the unit depends on.
    async def drive() -> None:
        task = asyncio.ensure_future(run(configs=[], checkpoint=False))
        await asyncio.sleep(0.05)  # let run() install signal handlers + start the scheduler
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(task, timeout=2)

    with caplog.at_level(logging.INFO, logger="hw_radar.poller"):
        asyncio.run(drive())
    assert "poller started" in caplog.text
    assert "poller stopped" in caplog.text


def test_refdata_refresh_job_registered_on_utc_cron() -> None:
    scheduler = build_scheduler(BucketRegistry(), [])
    job = scheduler.get_job("refdata-refresh")
    assert job is not None
    # Codex CR-003: APScheduler cron triggers default to the SCHEDULER timezone,
    # which defaults to LOCAL time — the *_UTC constants are only honest if the
    # scheduler is pinned to UTC.
    assert str(job.trigger.timezone) == "UTC"


def test_heartbeat_sources_get_fast_and_slow_repair_jobs() -> None:
    # CR-006: non-eBay heartbeat sources run TWO jobs — a fast heartbeat probe at
    # the heartbeat lane's interval AND a slow full-pipeline repair crawl at the
    # full lane's, which ADR-0020 pins at cadence_baseline_s.
    schedules = [
        _mem_schedule(
            "wd-recertified",
            heartbeat_enabled=True,
            cheap_signal=CheapSignal.OCC_JSON,
            heartbeat_interval_s=300,
            full_interval_s=1800,
        ),
        _mem_schedule(
            "seagate-recertified",
            heartbeat_enabled=True,
            cheap_signal=CheapSignal.BOOTSTRAP_JSON,
            heartbeat_interval_s=300,
            full_interval_s=1800,
        ),
        _mem_schedule(
            "serverpartdeals",
            heartbeat_enabled=True,
            cheap_signal=CheapSignal.SHOPIFY_PRODUCTS_JSON,
            heartbeat_interval_s=900,
            full_interval_s=3600,
        ),
    ]
    scheduler = build_scheduler(BucketRegistry(), schedules)
    for key, fast, slow in (
        ("wd-recertified", 300, 1800),
        ("seagate-recertified", 300, 1800),
        ("serverpartdeals", 900, 3600),
    ):
        hb = scheduler.get_job(f"poll-heartbeat-{key}")
        repair = scheduler.get_job(f"poll-{key}")
        assert hb is not None, key
        assert repair is not None, key
        assert hb.trigger.interval.total_seconds() == fast
        assert repair.trigger.interval.total_seconds() == slow
        assert fast != slow  # distinct cadences, distinct job IDs


def test_ebay_gets_single_heartbeat_job_only() -> None:
    # eBay's Browse poll IS both heartbeat and full fetch (natively-both source),
    # so a separate poll-ebay repair job would double-poll.
    schedules = [
        _mem_schedule(
            "ebay",
            heartbeat_enabled=True,
            cheap_signal=CheapSignal.EBAY_BROWSE,
            heartbeat_interval_s=120,
            full_interval_s=600,
        )
    ]
    scheduler = build_scheduler(BucketRegistry(), schedules)
    assert scheduler.get_job("poll-heartbeat-ebay") is not None
    assert scheduler.get_job("poll-ebay") is None


def test_heartbeat_disabled_source_gets_single_full_job() -> None:
    schedules = [
        _mem_schedule(
            "goharddrive",
            heartbeat_enabled=False,
            cheap_signal=CheapSignal.NONE,
            heartbeat_interval_s=3600,
            full_interval_s=900,
        )
    ]
    scheduler = build_scheduler(BucketRegistry(), schedules)
    assert scheduler.get_job("poll-goharddrive") is not None
    assert scheduler.get_job("poll-heartbeat-goharddrive") is None


def test_scheduler_is_pinned_to_utc() -> None:
    scheduler = build_scheduler(BucketRegistry(), [])
    assert str(scheduler.timezone) == "UTC"
    fx = scheduler.get_job("fx-refresh")
    assert fx is not None
    assert str(fx.trigger.timezone) == "UTC"  # FX_REFRESH_HOUR_UTC now truthful
