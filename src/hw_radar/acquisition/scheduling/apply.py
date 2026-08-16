"""The single mutator of source scheduling state.

Composes the pure pieces (lifecycle transition, back-off ladder, auto-ramp)
into one atomic write. Nothing else writes these fields — keeping every
scheduling decision auditable at one code point.

ADR-0020 splits that write across two rows: cadence state (interval, clean-poll
counter, back-off window) lands on the caller-supplied SourceLaneState, so an
outcome from one lane can never disturb the other; source-wide health
(lifecycle, failure counters, last-run stamps) lands on SourceConfig, shared by
both lanes. Both rows move inside one transaction, so a crash can never leave a
source backed off on paper but ramping in the scheduler.

Because the two lanes are separate APScheduler jobs, they can be inside this
function at the same time — max_instances=1 is per job id, not across job ids —
and the SourceConfig row they share is read-modify-write state. Every decision
is therefore computed from rows re-read FOR UPDATE inside the transaction; see
apply_run_outcome for the lock order and what the caller's instances mean
afterwards.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction

from hw_radar.acquisition.scheduling.backoff import (
    backoff_delay_s,
    clamp_retry_after,
    interval_after_success,
)
from hw_radar.acquisition.scheduling.lifecycle import LifecycleEvent, transition
from hw_radar.catalog.models import (
    LifecycleState,
    SchedulingLane,
    SourceConfig,
    SourceLaneState,
)

_SUCCESS_EVENTS = frozenset({LifecycleEvent.SUCCESS, LifecycleEvent.PROBE_SUCCESS})
_FAILURE_EVENTS = frozenset(
    {
        LifecycleEvent.TRANSIENT_FAILURE,
        LifecycleEvent.ANTI_BOT,
        LifecycleEvent.PARSER_ROT,
        LifecycleEvent.UNKNOWN_FAILURE,
    }
)
# PROBE_FAILURE is deliberately NOT a failure event here: a failed daily probe on an
# already-paused source keeps it paused (the transition is a no-op) and must not
# stack back-off windows or failure counters — the probe cadence is the daily job.


@dataclass(frozen=True)
class RunOutcome:
    event: LifecycleEvent
    retry_after_s: float | None = None


def ramp_floor_s(config: SourceConfig, lane: SchedulingLane) -> int:
    """Return the fastest interval auto-ramp may earn for this lane.

    Everywhere except one case this is cadence_ceiling_s. The exception is the
    full lane of a heartbeat-enabled source: that job is the CR-006 repair
    crawl, deliberately pinned at the slow end because the cheap probe already
    covers freshness. Flooring it at cadence_baseline_s (its own starting
    interval) makes the halving a no-op, so the repair crawl keeps the fixed
    cadence it had before ADR-0020 instead of ramping down into a second fast
    lane and doubling the load on the source.
    """
    if lane is SchedulingLane.FULL and config.heartbeat_enabled:
        return config.cadence_baseline_s
    return config.cadence_ceiling_s


_CONFIG_MANAGED_FIELDS = (
    "lifecycle_state",
    "consecutive_failures",
    "consecutive_parser_rot",
    "last_run_at",
    "last_success_at",
    "updated_at",
)
_LANE_MANAGED_FIELDS = ("clean_polls", "current_interval_s", "backoff_until", "updated_at")


def _apply_locked(
    config: SourceConfig,
    lane_state: SourceLaneState,
    outcome: RunOutcome,
    *,
    now: datetime,
    rand: Callable[[], float],
) -> None:
    """Mutate the two locked rows in memory. Caller owns the transaction and saves."""
    event = outcome.event
    lane = SchedulingLane(lane_state.lane)
    new_state = transition(
        LifecycleState(config.lifecycle_state),
        event,
        consecutive_parser_rot=config.consecutive_parser_rot,
    )
    config.last_run_at = now

    if event in _SUCCESS_EVENTS:
        config.last_success_at = now
        config.consecutive_failures = 0
        config.consecutive_parser_rot = 0
        lane_state.backoff_until = None
        lane_state.current_interval_s, lane_state.clean_polls = interval_after_success(
            lane_state.current_interval_s, ramp_floor_s(config, lane), lane_state.clean_polls
        )
    elif event in _FAILURE_EVENTS:
        config.consecutive_failures += 1
        config.consecutive_parser_rot = (
            config.consecutive_parser_rot + 1 if event is LifecycleEvent.PARSER_ROT else 0
        )
        lane_state.clean_polls = 0
        # AW-003: cadence resets to baseline; the back-off window rides on top.
        lane_state.current_interval_s = config.cadence_baseline_s
        delay_s = (
            clamp_retry_after(outcome.retry_after_s, config.cadence_baseline_s)
            if outcome.retry_after_s is not None
            else backoff_delay_s(config.consecutive_failures, rand)
        )
        lane_state.backoff_until = now + timedelta(seconds=delay_s)

    config.lifecycle_state = new_state


def apply_run_outcome(
    config: SourceConfig,
    outcome: RunOutcome,
    *,
    lane_state: SourceLaneState,
    now: datetime,
    rand: Callable[[], float],
) -> None:
    """Apply one run's outcome to its lane's cadence state and the source's health.

    `lane_state` must identify the lane the run belongs to (full lane for
    RunKind.FULL and RunKind.PROBE, heartbeat lane for RunKind.HEARTBEAT).

    The two arguments are used as row identity, not as input state: the shared
    SourceConfig row is read-modify-write state that the other lane's job may
    have advanced since the caller loaded it, so both rows are re-read FOR
    UPDATE inside the transaction and every counter, ladder step and lifecycle
    transition is computed from those locked values. Without that, two lanes
    failing concurrently each read consecutive_failures=0 and both write 1 — the
    escalation to paused_pending_fix never fires — and a stale heartbeat success
    can resurrect a source the full lane just paused for parser rot.

    Both callers' instances are refreshed in place from the committed rows, so
    the poller can still compare intervals for an in-place reschedule without
    another query. Unsaved local edits to the managed fields are therefore
    discarded rather than persisted; write them to the database first if they
    are meant to be inputs.
    """
    with transaction.atomic():
        # Lock order is config row then lane row, in this order on every path.
        # Both lanes take the same shared config row first and only ever their
        # OWN lane row second, so no two transactions can hold locks the other
        # needs — a reversed order here would introduce a real deadlock cycle.
        locked_config = SourceConfig.objects.select_for_update().get(pk=config.pk)
        locked_lane = SourceLaneState.objects.select_for_update().get(pk=lane_state.pk)
        _apply_locked(locked_config, locked_lane, outcome, now=now, rand=rand)
        locked_config.save(update_fields=list(_CONFIG_MANAGED_FIELDS))
        locked_lane.save(update_fields=list(_LANE_MANAGED_FIELDS))

    for field in _CONFIG_MANAGED_FIELDS:
        setattr(config, field, getattr(locked_config, field))
    for field in _LANE_MANAGED_FIELDS:
        setattr(lane_state, field, getattr(locked_lane, field))
