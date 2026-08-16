"""ADR-0020: the fast and slow lanes of one source must not corrupt each other.

The go-live gate these pin: before the split, a repair-crawl failure reset the
heartbeat lane's earned interval and imposed its back-off window on the fast
lane (and the reverse), so no fast-lane source could safely be enabled.
"""

import random
from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from hw_radar.acquisition.scheduling.apply import RunOutcome, apply_run_outcome
from hw_radar.acquisition.scheduling.lifecycle import LifecycleEvent
from hw_radar.catalog.models import SchedulingLane, SourceConfig, SourceLaneState

pytestmark = pytest.mark.django_db

# rand() == 1.0 makes backoff_delay_s deterministic (the full envelope), so a
# back-off window is always strictly in the future and comparable across lanes.
_MAX_JITTER = 1.0


def _config(key: str) -> SourceConfig:
    return SourceConfig.objects.select_related("source_site").get(source_site__normalized_name=key)


def test_migration_backfills_both_lanes_for_every_seeded_source() -> None:
    for config in SourceConfig.objects.all():
        lanes = {s.lane: s for s in SourceLaneState.objects.filter(source_config=config)}
        assert set(lanes) == {SchedulingLane.FULL, SchedulingLane.HEARTBEAT}, config.pk
        # Seed cadence is baseline everywhere, so both lanes land on baseline
        # regardless of which branch of the backfill they took.
        assert lanes[SchedulingLane.FULL].current_interval_s == config.cadence_baseline_s
        assert lanes[SchedulingLane.HEARTBEAT].current_interval_s == config.cadence_baseline_s


def test_lane_is_unique_per_source() -> None:
    config = _config("demo")
    with pytest.raises(IntegrityError):
        SourceLaneState.objects.create(
            source_config=config, lane=SchedulingLane.FULL, current_interval_s=60
        )


def test_slow_lane_failure_leaves_the_heartbeat_lane_untouched() -> None:
    config = _config("serverpartdeals")  # heartbeat_enabled (migration 0011)
    hb = config.lane_state(SchedulingLane.HEARTBEAT)
    hb.current_interval_s = 900  # an interval the fast lane earned by ramping
    hb.clean_polls = 3
    hb.save()
    full = config.lane_state(SchedulingLane.FULL)

    apply_run_outcome(
        config,
        RunOutcome(LifecycleEvent.TRANSIENT_FAILURE),
        lane_state=full,
        now=timezone.now(),
        rand=lambda: _MAX_JITTER,
    )

    hb.refresh_from_db()
    assert hb.current_interval_s == 900  # not reset to cadence_baseline_s
    assert hb.clean_polls == 3  # ramp progress survives the other lane's failure
    assert hb.backoff_until is None  # the fast lane keeps polling
    full.refresh_from_db()
    assert full.backoff_until is not None


def test_heartbeat_lane_failure_leaves_the_repair_lane_untouched() -> None:
    config = _config("serverpartdeals")
    full = config.lane_state(SchedulingLane.FULL)
    full.clean_polls = 2
    full.save()
    hb = config.lane_state(SchedulingLane.HEARTBEAT)

    apply_run_outcome(
        config,
        RunOutcome(LifecycleEvent.ANTI_BOT),
        lane_state=hb,
        now=timezone.now(),
        rand=lambda: _MAX_JITTER,
    )

    full.refresh_from_db()
    assert full.current_interval_s == config.cadence_baseline_s
    assert full.clean_polls == 2
    assert full.backoff_until is None
    hb.refresh_from_db()
    assert hb.backoff_until is not None


def test_lane_backoff_windows_are_independent() -> None:
    config = _config("serverpartdeals")
    now = timezone.now()
    hb = config.lane_state(SchedulingLane.HEARTBEAT)
    hb.backoff_until = now + timedelta(hours=1)
    hb.save()
    full = config.lane_state(SchedulingLane.FULL)

    # A clean full-lane run clears only its own window.
    apply_run_outcome(
        config, RunOutcome(LifecycleEvent.SUCCESS), lane_state=full, now=now, rand=random.random
    )

    hb.refresh_from_db()
    still_backed_off = hb.backoff_until
    assert still_backed_off is not None
    assert still_backed_off > now


def test_single_lane_source_ramps_exactly_as_before_the_split() -> None:
    # Equivalence check for non-heartbeat sources: four clean polls halve the
    # interval down toward cadence_ceiling_s, as AW-004 always did.
    config = _config("demo")  # heartbeat_enabled=False
    lane = config.lane_state(SchedulingLane.FULL)
    assert lane.current_interval_s == 3600
    for _ in range(4):
        apply_run_outcome(
            config,
            RunOutcome(LifecycleEvent.SUCCESS),
            lane_state=lane,
            now=timezone.now(),
            rand=random.random,
        )
    lane.refresh_from_db()
    assert lane.current_interval_s == 1800
    assert lane.clean_polls == 0


def test_repair_lane_of_a_heartbeat_source_never_ramps_below_baseline() -> None:
    # CR-006 keeps the repair crawl at the slow end; per-lane ramp state must not
    # turn it into a second fast lane just because it now has its own interval.
    config = _config("serverpartdeals")
    lane = config.lane_state(SchedulingLane.FULL)
    for _ in range(8):
        apply_run_outcome(
            config,
            RunOutcome(LifecycleEvent.SUCCESS),
            lane_state=lane,
            now=timezone.now(),
            rand=random.random,
        )
    lane.refresh_from_db()
    assert lane.current_interval_s == config.cadence_baseline_s


def test_heartbeat_lane_ramps_down_to_the_ceiling() -> None:
    config = _config("serverpartdeals")  # baseline 3600, ceiling 900
    lane = config.lane_state(SchedulingLane.HEARTBEAT)
    for _ in range(16):
        apply_run_outcome(
            config,
            RunOutcome(LifecycleEvent.SUCCESS),
            lane_state=lane,
            now=timezone.now(),
            rand=random.random,
        )
    lane.refresh_from_db()
    assert lane.current_interval_s == config.cadence_ceiling_s


def test_lane_row_is_created_on_demand_when_heartbeat_is_enabled_later() -> None:
    config = _config("goharddrive")  # heartbeat_enabled=False
    SourceLaneState.objects.filter(source_config=config, lane=SchedulingLane.HEARTBEAT).delete()
    lane = config.lane_state(SchedulingLane.HEARTBEAT)
    assert lane.pk is not None
    assert lane.current_interval_s == config.cadence_baseline_s
