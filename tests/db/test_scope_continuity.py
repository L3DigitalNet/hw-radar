"""MS2-D-31 / MS2-D-36: sweep continuity is tracked per scope, in event-time order.

Continuity is the polling-time evidence stale absence needs. Before D10 it was one
FULL-lane value per source, so complete sweeps of one scope could vouch for an
incomplete sweep of another. The stages now keep it per scope (the FULL lane row
for the legacy NULL scope, ScopeSweepContinuity otherwise) with ordered records
and timestamped breaks. The FULL lane here has a one-hour interval, so the
continuity tolerance is two hours.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ordering_support import HOUR, T0, listing, make_site, observe, record, sweep

from hw_radar.catalog.models import (
    Listing,
    SchedulingLane,
    ScopeSweepContinuity,
    SourceLaneState,
    SourceSite,
)

pytestmark = pytest.mark.django_db

A = "d10order:gpu:a"
B = "d10order:ram:b"
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def _lane(site: SourceSite) -> SourceLaneState:
    return SourceLaneState.objects.get(source_config__source_site=site, lane=SchedulingLane.FULL)


def _scope(site: SourceSite, key: str) -> ScopeSweepContinuity:
    return ScopeSweepContinuity.objects.get(source_site=site, collection_scope=key)


def _continuity(site: SourceSite, key: str | None) -> SourceLaneState | ScopeSweepContinuity:
    return _lane(site) if key is None else _scope(site, key)


@pytest.mark.parametrize("scope_b", [B, None])
def test_scope_a_continuity_does_not_authorize_first_incomplete_scope_b_sweep(
    scope_b: str | None,
) -> None:
    site = make_site()
    observe(site, T0, [record("b1", scope=scope_b)])
    Listing.objects.filter(source_site=site).update(last_seen=LONG_AGO)
    for step in range(5):  # four hours of continuous complete A sweeps
        sweep(site, T0 + step * HOUR, scope_key=A, seen=set())

    first_b = T0 + 4 * HOUR + HOUR / 6
    delisted = sweep(site, first_b, scope_key=scope_b, seen=set(), complete=False)

    assert delisted == 0
    assert listing(site, "b1").delisted_at is None
    assert _continuity(site, scope_b).continuous_since == first_b


def test_run_not_sweeping_null_scope_breaks_null_scope_continuity() -> None:
    site = make_site()
    sweep(site, T0, scope_key=None, seen=set(), delist=False)
    assert _lane(site).continuous_since == T0

    sweep(site, T0 + HOUR, scope_key=A, seen=set(), delist=False)

    lane = _lane(site)
    assert lane.continuous_since is None
    assert lane.continuity_broken_at == T0 + HOUR


@pytest.mark.parametrize("scope_key", [A, None])
def test_older_eligible_sweep_does_not_move_scope_watermark_back(scope_key: str | None) -> None:
    site = make_site()
    sweep(site, T0 + HOUR, scope_key=scope_key, seen=set(), delist=False)

    sweep(site, T0, scope_key=scope_key, seen=set(), delist=False)

    row = _continuity(site, scope_key)
    assert row.last_eligible_sweep_at == T0 + HOUR
    assert row.continuous_since == T0 + HOUR


@pytest.mark.parametrize("scope_key", [A, None])
def test_eligible_sweep_older_than_break_is_noop(scope_key: str | None) -> None:
    site = make_site()
    sweep(site, T0 + HOUR, scope_key=scope_key, seen=set(), eligible=False, delist=False)

    sweep(site, T0, scope_key=scope_key, seen=set(), delist=False)
    # A tie goes to the break: fail closed.
    sweep(site, T0 + HOUR, scope_key=scope_key, seen=set(), delist=False)

    row = _continuity(site, scope_key)
    assert row.continuous_since is None
    assert row.last_eligible_sweep_at is None


def test_late_older_break_still_breaks() -> None:
    site = make_site()
    sweep(site, T0 + HOUR, scope_key=A, seen=set(), delist=False)

    sweep(site, T0, scope_key=A, seen=set(), eligible=False, delist=False)

    row = _scope(site, A)
    assert row.continuous_since is None
    assert row.continuity_broken_at == T0


def test_delayed_null_scope_run_after_newer_scoped_run_cannot_restore_continuity() -> None:
    site = make_site()
    sweep(site, T0, scope_key=None, seen=set(), delist=False)
    t_g = T0 + 2 * HOUR
    sweep(site, t_g, scope_key=A, seen=set(), delist=False)  # GPU-only FULL run
    assert _lane(site).continuity_broken_at == t_g

    t_n = T0 + HOUR  # an older NULL-scope sweep reaches its continuity record late
    sweep(site, t_n, scope_key=None, seen=set(), delist=False)
    assert _lane(site).continuous_since is None

    observe(site, T0, [record("old")])
    Listing.objects.filter(source_site=site).update(last_seen=LONG_AGO)
    local = t_g + HOUR
    delisted = sweep(site, local, scope_key=None, seen=set(), complete=False, grace=HOUR / 2)

    assert delisted == 0
    assert _lane(site).continuous_since == local


def test_legacy_null_scope_only_runs_keep_todays_continuity() -> None:
    site = make_site()
    sweep(site, T0, scope_key=None, seen=set(), delist=False)
    # With no SUCCESS ScraperRun on record, the NULL scope's predecessor lookup
    # finds none and each sweep restarts the run, exactly as before D10.
    sweep(site, T0 + HOUR, scope_key=None, seen=set(), delist=False)

    lane = _lane(site)
    assert lane.continuous_since == T0 + HOUR
    assert lane.last_eligible_sweep_at == T0 + HOUR
    assert lane.continuity_broken_at is None


@pytest.mark.parametrize(
    ("kind", "raises"),
    [
        ("complete", True),
        ("complete_empty", True),
        ("truncated", False),
        ("stale_absence", False),
        ("probe", False),
        ("rejected", False),
    ],
)
def test_only_gated_complete_full_scopes_raise_complete_sweep_watermark(
    kind: str, raises: bool
) -> None:
    site = make_site()
    observe(site, T0, [record("x", scope=A)])
    at = T0 + HOUR
    if kind == "complete":
        sweep(site, at, scope_key=A, seen={"x"})
    elif kind == "complete_empty":
        sweep(site, at, scope_key=A, seen=set())
    elif kind == "truncated":
        # A remote truncation: ineligible, and gate_delist_scope yields nothing.
        sweep(site, at, scope_key=A, seen={"x"}, eligible=False, delist=False)
    elif kind == "stale_absence":
        sweep(site, at, scope_key=A, seen={"x"}, complete=False)
    elif kind == "rejected":
        # A rejected FULL import only breaks its admitted scope.
        sweep(site, at, scope_key=A, seen=set(), eligible=False, delist=False)
    # "probe": PROBE runs never reach the absence stage at all.
    row = ScopeSweepContinuity.objects.filter(source_site=site, collection_scope=A).first()
    watermark = None if row is None else row.last_complete_sweep_at
    assert watermark == (at if raises else None)


def test_remote_scope_a_runs_then_local_incomplete_scope_b_sweep_cannot_stale_delist() -> None:
    site = make_site()
    observe(site, T0, [record("legacy")])
    Listing.objects.filter(source_site=site).update(last_seen=LONG_AGO)
    # Complete remote runs of A: eligible for A's continuity, never stale-absence
    # eligible, and each breaks the NULL scope it did not sweep.
    for step in range(5):
        sweep(site, T0 + step * HOUR, scope_key=A, seen=set())

    # Switch to the local provider: its first incomplete NULL-scope sweep.
    local = T0 + 4 * HOUR + HOUR / 6
    delisted = sweep(site, local, scope_key=None, seen=set(), complete=False)

    assert delisted == 0
    assert listing(site, "legacy").delisted_at is None
    assert _lane(site).continuous_since == local
