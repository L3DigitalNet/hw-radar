# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# APScheduler 3.x is untyped (see tests/unit/test_poller.py).
"""The DR-001 sweep must be a standing poller job, not a manual-only command."""

from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.poller.service import RETENTION_SWEEP_SECONDS, build_scheduler


def test_retention_sweep_is_scheduled_even_with_no_sources() -> None:
    # Service-level job: it must exist before any source is enabled, since the
    # sweeper is the gate that lets a bounded source be enabled at all.
    scheduler = build_scheduler(BucketRegistry(), configs=[])
    job = scheduler.get_job("retention-sweep")
    assert job is not None
    assert job.trigger.interval.total_seconds() == RETENTION_SWEEP_SECONDS


def test_sweep_interval_honors_the_tightest_retention_bound() -> None:
    # DR-008 caps eBay observations at 6h; a sweep slower than that bound would
    # let expired rows outlive their window by more than the window itself.
    assert RETENTION_SWEEP_SECONDS <= 6 * 3600
