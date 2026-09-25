"""MS2-D-30 / MS2-D-35 ordering guards: a delayed observation never rewinds current state.

A durable import can land after a newer run or a newer delist. Each test replays
one such interleaving at explicit event times through the extracted stages
(ordering_support), which both the local pipeline and the importer compose.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from django.db import connection
from django.utils import timezone
from ordering_support import HOUR, T0, listing, make_site, observe, record, sweep

from hw_radar.acquisition import stages
from hw_radar.acquisition.apify.client import ApifyClient
from hw_radar.acquisition.apify.importer import import_provider_run
from hw_radar.acquisition.contracts import NullResolver
from hw_radar.acquisition.scheduling.apply import RunOutcome, apply_run_outcome
from hw_radar.acquisition.scheduling.lifecycle import LifecycleEvent
from hw_radar.catalog.models import (
    DelistReason,
    OfferSnapshot,
    ProviderRun,
    RetentionClass,
    SchedulingLane,
    SourceConfig,
    SourceLaneState,
    SourceSite,
)
from hw_radar.catalog.models.provider import AdmissionClass, ImportState

pytestmark = pytest.mark.django_db

S = "d10order:gpu:q1"
ACTOR = "hw-radar-synthetic-collector"
T1, T2 = T0 + HOUR, T0 + 2 * HOUR


def test_reverse_completion_older_import_does_not_overwrite_newer_listing_state() -> None:
    site = make_site()
    newer = record("x", title="R2 title", scope=S, condition="used", international=True)
    older = record("x", title="R1 title", scope=S, condition="new", international=False)

    observe(site, T2, [newer])
    observe(site, T1, [older])

    x = listing(site, "x")
    assert (x.title_raw, x.condition_label_raw, x.is_international) == ("R2 title", "used", True)
    assert x.last_observed_at == T2
    history = list(OfferSnapshot.objects.filter(listing=x).order_by("observed_at"))
    assert [s.observed_at for s in history] == [T1, T2]


def test_older_import_does_not_revive_listing_delisted_by_newer_evidence() -> None:
    site = make_site()
    observe(site, T0, [record("x", scope=S)])
    sweep(site, T2, scope_key=S, seen=set())
    assert listing(site, "x").delisted_at == T2

    observe(site, T1, [record("x", title="late", scope=S)])

    x = listing(site, "x")
    assert x.delisted_at == T2
    assert x.title_raw == "Drive"


def test_older_complete_import_does_not_delist_listing_observed_by_newer_run() -> None:
    site = make_site()
    observe(site, T0, [record("x", scope=S)])
    # "y" is first seen by the newer run and is unknown to the older sweep.
    observe(site, T2, [record("x", scope=S), record("y", scope=S)])

    delisted = sweep(site, T1, scope_key=S, seen=set())

    assert delisted == 0
    assert listing(site, "x").delisted_at is None
    assert listing(site, "y").delisted_at is None


def test_legacy_listing_without_watermark_accepts_first_observation() -> None:
    site = make_site()
    observe(site, T2, [record("x", scope=S)])
    # A row written before migration 0021 carries no watermark.
    type(listing(site, "x")).objects.filter(source_listing_key="x").update(last_observed_at=None)

    observe(site, T1, [record("x", title="first guarded", scope=S)])

    x = listing(site, "x")
    assert x.title_raw == "first guarded"
    assert x.last_observed_at == T1


@pytest.mark.parametrize(
    "retention_class",
    [RetentionClass.EBAY_LISTING_OBSERVATION, RetentionClass.MERCHANT_FACT],
)
@pytest.mark.parametrize("via", ["complete_sweep", "stale_absence"])
def test_delayed_observation_after_newer_delist_restores_no_content_and_keeps_expiry(
    retention_class: RetentionClass, via: str
) -> None:
    site = make_site()
    observe(site, T0, [record("x", scope=S)], retention_class)
    if via == "complete_sweep":
        sweep(site, T2, scope_key=S, seen=set())
    else:
        x = listing(site, "x")
        assert x.mark_delisted(DelistReason.ABSENT_STALE, when=T2)
    assert listing(site, "x").last_absence_at == T2
    before = listing(site, "x")

    observe(site, T1, [record("x", title="late", scope=S)], retention_class)

    x = listing(site, "x")
    assert x.delisted_at == T2
    assert x.title_raw == before.title_raw
    assert x.expires_at == before.expires_at
    assert x.last_observed_at == T0
    if retention_class is RetentionClass.EBAY_LISTING_OBSERVATION:
        assert x.is_content_redacted()


@pytest.mark.parametrize("scope_key", [S, None])
@pytest.mark.parametrize(
    "retention_class",
    [RetentionClass.MERCHANT_FACT, RetentionClass.AMAZON_EPHEMERAL],
)
def test_delayed_import_of_unknown_key_after_newer_complete_sweep_creates_no_active_listing(
    scope_key: str | None, retention_class: RetentionClass
) -> None:
    site = make_site()
    sweep(site, T2, scope_key=scope_key, seen=set())

    observe(site, T1, [record("k", scope=scope_key)], retention_class)

    k = listing(site, "k")
    assert k.delisted_at == T2
    assert k.last_absence_at == T2
    assert k.delist_reason == DelistReason.ABSENT_FROM_SWEEP
    snapshots = OfferSnapshot.objects.filter(listing=k)
    # Merchant facts keep the t1 snapshot as history; a bounded class's t1
    # snapshot would be born already expired by the newer absence.
    assert snapshots.count() == (1 if retention_class is RetentionClass.MERCHANT_FACT else 0)

    later = T2 + HOUR
    observe(site, later, [record("k", scope=scope_key)], retention_class)
    relisted = listing(site, "k")
    assert relisted.pk == k.pk
    assert relisted.delisted_at is None


def test_delayed_observation_cannot_relist_row_delisted_before_newer_complete_sweep() -> None:
    site = make_site()
    observe(site, T0, [record("x", scope=S)])
    x = listing(site, "x")
    assert x.mark_delisted(DelistReason.ABSENT_STALE, when=T0 + HOUR / 2)
    sweep(site, T2, scope_key=S, seen=set())

    observe(site, T1, [record("x", scope=S)])

    assert listing(site, "x").delisted_at == T0 + HOUR / 2


def test_delayed_import_bumps_last_seen_only_forward_and_only_when_current_eligible() -> None:
    site = make_site()
    observe(site, T2, [record("x", scope=S)])
    seen_after_newer = listing(site, "x").last_seen

    observe(site, T1, [record("x", scope=S)])
    assert listing(site, "x").last_seen == seen_after_newer

    observe(site, T2 + HOUR, [record("x", scope=S)])
    assert listing(site, "x").last_seen > seen_after_newer


@pytest.mark.parametrize(
    "retention_class",
    [RetentionClass.EBAY_LISTING_OBSERVATION, RetentionClass.MERCHANT_FACT],
)
def test_delayed_remote_import_after_switch_back_to_local_preserves_newer_content_and_delist_state(
    retention_class: RetentionClass,
) -> None:
    site = make_site()
    admitted, t0, t1 = T0 - HOUR, T0, T0 + HOUR
    observe(site, admitted, [record("x"), record("y")], retention_class)
    # The source switched back to local: a complete NULL-scope sweep at t1
    # rewrites X and delists Y.
    observe(site, t1, [record("x", title="local t1")], retention_class)
    sweep(site, t1, scope_key=None, seen={"x"})
    x_before, y_before = listing(site, "x"), listing(site, "y")
    assert y_before.delisted_at == t1

    # The remote run admitted before the switch now completes, observed at t0.
    observe(site, t0, [record("x", scope=S), record("y", title="remote", scope=S)], retention_class)
    sweep(site, t0, scope_key=S, seen={"x", "y"})

    x, y = listing(site, "x"), listing(site, "y")
    assert (x.title_raw, x.expires_at, x.collection_scope) == (
        "local t1",
        x_before.expires_at,
        None,
    )
    assert x.delisted_at is None
    assert y.delisted_at == t1
    assert (y.title_raw, y.canonical_url, y.expires_at) == (
        y_before.title_raw,
        y_before.canonical_url,
        y_before.expires_at,
    )
    if retention_class is RetentionClass.EBAY_LISTING_OBSERVATION:
        assert y.is_content_redacted()


def test_absence_watermark_survives_a_relist_on_every_delist_path() -> None:
    # Listing.mark_delisted raises last_absence_at itself, so a delist made
    # outside the pipeline (an operator, a future path) still bounds a delayed
    # observation after mark_relisted has cleared delisted_at.
    site = make_site()
    observe(site, T0, [record("x", scope=S)])
    x = listing(site, "x")
    assert x.mark_delisted(DelistReason.ABSENT_STALE, when=T2)
    assert x.mark_relisted()

    observe(site, T1, [record("x", title="late", scope=S)])

    x = listing(site, "x")
    assert x.last_absence_at == T2
    assert x.title_raw == "Drive"


# ── Concurrency under the MS2-D-35 lock order (revision 4 and 10) ─────────────
#
# Each test pauses one transaction at a chosen point while it holds its locks,
# starts the competing transaction in a second thread (which must then block on
# the shared row), and releases the first. transaction=True: the two threads
# need real, separately committed transactions on their own connections.

_PAUSE_S = 0.5


class _Gate:
    """Pause the wrapped callable, after it runs, until release() (first call only)."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.entered = threading.Event()
        self.released = threading.Event()
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = self.real(*args, **kwargs)
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            assert self.released.wait(10)
        return result


def _in_thread(work: Callable[[], object]) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def target() -> None:
        try:
            work()
        except BaseException as exc:  # surfaced to the test thread below
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=target)
    thread.start()
    return thread, errors


def _interleave(first: Callable[[], object], gate: _Gate, second: Callable[[], object]) -> None:
    t1, e1 = _in_thread(first)
    assert gate.entered.wait(10)
    t2, e2 = _in_thread(second)
    time.sleep(_PAUSE_S)  # let the second transaction reach its blocking lock
    gate.released.set()
    t1.join(10)
    t2.join(10)
    assert not e1 and not e2, (e1, e2)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
@pytest.mark.parametrize("first", ["import", "sweep"])
def test_stage1_creation_and_newer_complete_sweep_serialize_on_scope_row(
    first: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = make_site()
    t1, t2 = T0 + HOUR, T0 + 2 * HOUR
    target = "observe_listing" if first == "import" else "apply_delist"
    gate = _Gate(getattr(stages, target))
    monkeypatch.setattr(stages, target, gate)

    def import_k() -> object:
        return observe(site, t1, [record("k", scope=S)])

    def sweep_s() -> object:
        return sweep(site, t2, scope_key=S, seen=set())

    if first == "import":
        _interleave(import_k, gate, sweep_s)
    else:
        _interleave(sweep_s, gate, import_k)

    k = listing(site, "k")
    assert k.delisted_at == t2


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_scope_move_is_not_delisted_by_other_scope_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = make_site()
    other = "d10order:gpu:q2"
    observe(site, T0, [record("x", scope=other)])
    t1, t2 = T0 + HOUR, T0 + 2 * HOUR
    gate = _Gate(stages.observe_listing)
    monkeypatch.setattr(stages, "observe_listing", gate)

    # The import on S holds X's row lock, having moved X into S at t1; the
    # older-scope sweep of S' at t2 selects X as a candidate and blocks on it.
    _interleave(
        lambda: observe(site, t1, [record("x", title="moved", scope=S)]),
        gate,
        lambda: sweep(site, t2, scope_key=other, seen=set()),
    )

    x = listing(site, "x")
    assert x.delisted_at is None
    assert (x.collection_scope, x.title_raw) == (S, "moved")


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_run_outcome_and_stage1_interleave_without_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = make_site()
    config = SourceConfig.objects.get(source_site=site)
    lane = config.lane_state(SchedulingLane.FULL)
    gate = _Gate(stages.observe_listing)
    monkeypatch.setattr(stages, "observe_listing", gate)

    # Stage 1 holds the FULL lane row; apply_run_outcome(FULL) takes
    # SourceConfig and then waits for that lane row. Neither holds what the
    # other needs first, so both commit.
    _interleave(
        lambda: observe(site, T0, [record("x", scope=S)]),
        gate,
        lambda: apply_run_outcome(
            config,
            RunOutcome(LifecycleEvent.SUCCESS),
            lane_state=lane,
            now=timezone.now(),
            rand=lambda: 0.5,
        ),
    )

    lane.refresh_from_db()
    assert lane.clean_polls == 1
    assert listing(site, "x").delisted_at is None


_FIXTURE_DIR: Final = Path(__file__).resolve().parents[1] / "fixtures" / "apify_contract" / "v1"


def _import_fixture(scope: str, keys: list[str], run_id: str) -> dict[str, Any]:
    """The Actor's complete fixture, re-keyed to `keys` and re-scoped to `scope`."""
    fixture = json.loads((_FIXTURE_DIR / "complete.json").read_text(encoding="utf-8"))
    template = fixture["datasetItems"][0]
    fixture["datasetItems"] = [
        {**template, "sourceListingKey": key, "collectionScope": scope} for key in keys
    ]
    for query in (fixture["admitted"]["queryScope"], fixture["output"]["queryScope"]):
        query["collectionScope"] = scope
    fixture["output"]["completeness"].update(itemsDeclared=len(keys), itemsEmitted=len(keys))
    fixture["runId"] = run_id
    return fixture


def _admitted_run(site: SourceSite, fixture: dict[str, Any], started: datetime) -> ProviderRun:
    query = fixture["admitted"]["queryScope"]
    now = timezone.now()
    return ProviderRun.objects.create(
        source_site=site,
        external_run_id=fixture["runId"],
        import_idempotency_key=f"apify:{fixture['runId']}",
        actor_ref=fixture["admitted"]["actorName"],
        contract_schema_version="hw-radar-run/v1",
        query_scope=query,
        scope_key=query["collectionScope"],
        memory_mb=256,
        timeout_s=120,
        max_items=query["maxItems"],
        max_pages=query["maxPages"],
        admission_class=AdmissionClass.WATCH_REFRESH,
        admitted_at=started - HOUR,
        remote_status=fixture["remoteStatus"],
        started_at=started,
        dataset_id="ds-overlap",
        kv_store_id="kv-overlap",
        storage_cleanup_due_at=now + 24 * HOUR,
    )


def _serve(fixture: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    rows = fixture["datasetItems"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/datasets/ds-overlap/items":
            offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
            return httpx.Response(
                200,
                json=rows[offset : offset + limit],
                headers={"X-Apify-Pagination-Total": str(len(rows))},
            )
        if request.url.path == "/v2/key-value-stores/kv-overlap/records/OUTPUT":
            return httpx.Response(200, json=fixture["output"])
        return httpx.Response(500, json={"error": {"type": "unexpected", "message": "path"}})

    return handler


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_overlapping_scopes_sharing_listings_do_not_deadlock_or_consume_read_cap() -> None:
    # The synthetic site is the one site the importer's retention registry
    # admits; it gets a SourceConfig here so the NULL scope has its lane row.
    site = make_site("synthetic")
    keys = [f"syn-overlap-{i:02d}" for i in range(20)]
    observe(site, T0, [record(k) for k in keys])
    scope_a, scope_b = "synthetic:hdd:catalog", "synthetic:hdd:other"
    imports = [
        (_import_fixture(scope_a, keys, "run-overlap-a"), T0 + HOUR),
        (_import_fixture(scope_b, list(reversed(keys)), "run-overlap-b"), T0 + 2 * HOUR),
    ]
    rows = [_admitted_run(site, fixture, started) for fixture, started in imports]
    barrier = threading.Barrier(len(rows) + 1)

    def run_import(row: ProviderRun, fixture: dict[str, Any]) -> Callable[[], object]:
        def work() -> object:
            client = ApifyClient(
                "apify_api_overlap", transport=httpx.MockTransport(_serve(fixture))
            )
            barrier.wait(10)
            return asyncio.run(
                import_provider_run(
                    row.pk, client=client, actor_name=ACTOR, resolver=NullResolver()
                )
            )

        return work

    def local_persist() -> object:
        barrier.wait(10)
        # A local NULL-scope persist overlapping both scoped stage-1 transactions,
        # in yet another key order.
        return observe(site, T0 + 3 * HOUR, [record(k) for k in keys[::2] + keys[1::2]])

    threads = [
        _in_thread(run_import(row, fixture))
        for row, (fixture, _) in zip(rows, imports, strict=True)
    ]
    threads.append(_in_thread(local_persist))
    for thread, errors in threads:
        thread.join(60)
        # Every conflict was either ordered by the lock order or absorbed by the
        # in-memory retry; nothing escaped (a stage-1 escape would cost a read).
        assert not thread.is_alive()
        assert not errors, errors
    for row in rows:
        row.refresh_from_db()
        assert row.import_state == ImportState.FINALIZED, row.stage_detail
        assert row.dataset_read_count == 1
        assert "retry_exhausted" not in row.stage_detail
    assert OfferSnapshot.objects.filter(listing__source_site=site).count() == 4 * len(keys)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_overlapping_local_full_runs_serialize_on_lane_row(monkeypatch: pytest.MonkeyPatch) -> None:
    site = make_site()
    observe(site, T0, [record("old")])
    type(listing(site, "old")).objects.filter(source_site=site).update(last_seen=T0 - 10 * HOUR)
    sweep(site, T0, scope_key=None, seen={"old"})
    gate = _Gate(stages.apply_delist)
    monkeypatch.setattr(stages, "apply_delist", gate)
    delisted: list[int] = []

    # The FULL-lane run (newer) holds the lane row; the heartbeat-fired FULL run
    # (older event time) waits for it, then finds a newer eligible sweep and
    # skips its own stale absence instead of rewriting continuous_since.
    _interleave(
        lambda: sweep(site, T0 + HOUR, scope_key=None, seen={"old"}),
        gate,
        lambda: delisted.append(
            sweep(site, T0 + HOUR / 2, scope_key=None, seen=set(), complete=False, grace=HOUR / 6)
        ),
    )

    assert delisted == [0]
    lane = SourceLaneState.objects.get(source_config__source_site=site, lane=SchedulingLane.FULL)
    assert lane.continuous_since is not None
    assert lane.last_eligible_sweep_at == T0 + HOUR
    assert listing(site, "old").delisted_at is None
