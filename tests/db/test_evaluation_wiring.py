"""MS2-D-20 wiring: eligibility evaluation runs on every ingestion path through
run_collection — scheduled poll, heartbeat-fired FULL run, recovery probe —
with no evaluator argument at any call site, and an evaluator failure never
fails the run.

The watches here are drive watches with no product constraint and an
in-stock / max-price offer clause, so a verdict changes with the offer alone
and no resolution (NullResolver) is needed; the dispatch category defaults to
drive when the collector sends no hint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hw_radar.acquisition import pipeline
from hw_radar.acquisition.contracts import NullResolver, ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.heartbeat import HeartbeatReading, run_heartbeat
from hw_radar.acquisition.pipeline import run_collection, run_source
from hw_radar.acquisition.providers import LocalCollectionProvider
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.catalog.models import (
    Category,
    EligibilityVerdict,
    LifecycleState,
    Listing,
    OfferSnapshot,
    RunKind,
    RunStatus,
    SourceConfig,
    Watch,
    WatchEvaluation,
)
from hw_radar.eligibility import ListingEvaluationResult, evaluate
from hw_radar.eligibility.evaluate import is_current
from hw_radar.eligibility.requirements import DriveRequirementSpec, save_requirement
from hw_radar.eligibility.shortlist import shortlist

# transaction=True: run_collection writes from sync_to_async threads.
# serialized_rollback=True: TransactionTestCase truncation would otherwise delete
# the migration-seeded source and category rows for every later test.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

M, N = EligibilityVerdict.MATCH, EligibilityVerdict.NO_MATCH


class FakeAdapter:
    name = "fake"
    site_key = "demo"
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0

    def __init__(self, records: list[tuple[str, str]]) -> None:
        # (source_listing_key, price) per parsed record.
        self.records = records

    async def fetch(self) -> RawBatch:
        # A fresh fetch time per call: it becomes the snapshot's observed_at,
        # which is half of OfferSnapshot's composite key.
        return RawBatch(
            source=self.name,
            fetched_at=datetime.now(UTC),
            items=[RawItem(url="https://demo.invalid/a", payload_json={"sku": "a"})],
        )

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return [
            ParsedListing(
                source_listing_key=key,
                url=f"https://demo.invalid/{key}",
                title=f"Drive {key}",
                price=Decimal(price),
                shipping_price=Decimal(0),
                stock_status="in_stock",
                raw_url="https://demo.invalid/a",
            )
            for key, price in self.records
        ]


class RecordingEvaluator:
    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.calls: list[int] = []
        self.fail_on = fail_on or set()

    def evaluate_listing(self, listing_id: int) -> ListingEvaluationResult:
        self.calls.append(listing_id)
        key = Listing.objects.get(pk=listing_id).source_listing_key
        if key in self.fail_on:
            raise RuntimeError(f"evaluator crashed on {key}")
        return evaluate.evaluate_listing(listing_id)


def _drive_watch(spec: DriveRequirementSpec) -> Watch:
    watch = Watch.objects.create(name="drive", category=Category.objects.get(slug="drive"))
    save_requirement(watch, spec)
    return watch


def _row(watch: Watch, key: str) -> WatchEvaluation:
    return WatchEvaluation.objects.select_related("watch", "listing").get(
        watch=watch, listing__source_listing_key=key
    )


def _latest_observed(key: str) -> datetime:
    snap = (
        OfferSnapshot.objects.filter(listing__source_listing_key=key)
        .order_by("-observed_at")
        .first()
    )
    assert snap is not None
    return snap.observed_at


def test_default_evaluator_is_the_production_one() -> None:
    # No evaluator argument anywhere: run_source -> run_collection must still
    # evaluate (the F-03 omission this default exists to prevent).
    watch = _drive_watch(DriveRequirementSpec(max_unit_price_usd=Decimal("150.00")))

    run, _ = asyncio.run(run_source(FakeAdapter([("cheap", "100.00")]), NullResolver()))

    assert run.status == RunStatus.SUCCESS
    assert run.detail_json["evaluator_errors"] == 0
    row = _row(watch, "cheap")
    assert row.verdict == M
    assert row.snapshot_observed_at == _latest_observed("cheap")
    assert is_current(row)


def test_pipeline_evaluator_failure_isolated() -> None:
    watch = _drive_watch(DriveRequirementSpec(max_unit_price_usd=Decimal("150.00")))
    adapter = FakeAdapter([("ok", "100.00"), ("bad", "100.00")])
    provider = LocalCollectionProvider(adapter)
    asyncio.run(run_collection(provider, NullResolver()))  # both rows `match`, current
    assert shortlist(watch.pk) and len(shortlist(watch.pk)) == 2

    evaluator = RecordingEvaluator(fail_on={"bad"})
    run, outcome = asyncio.run(run_collection(provider, NullResolver(), evaluator=evaluator))

    # The run and its persistence are unaffected by the evaluator failure.
    assert run.status == RunStatus.SUCCESS
    assert run.snapshots_appended == 2
    assert run.detail_json["evaluator_errors"] == 1
    assert outcome.event.name == "SUCCESS"
    # The failing listing fails closed (its row now binds an old snapshot and
    # leaves the shortlist); the other listing was still evaluated.
    assert not is_current(_row(watch, "bad"))
    assert is_current(_row(watch, "ok"))
    assert [r.listing_id for r in shortlist(watch.pk)] == [_row(watch, "ok").listing.pk]


def test_evaluate_stage_dedupes_and_skips_resolver_failures() -> None:
    # _evaluate_all's own contract, driven directly: a batch that repeats a
    # listing pk cannot reach it through a real run today (the second
    # snapshot insert collides on the hypertable key), so the dedupe is pinned
    # here rather than end to end.
    asyncio.run(run_source(FakeAdapter([("a", "100.00"), ("b", "100.00")]), NullResolver()))
    a, b = (Listing.objects.get(source_listing_key=k).pk for k in ("a", "b"))
    evaluator = RecordingEvaluator()

    errors = asyncio.run(pipeline._evaluate_all(evaluator, [a, a, b, a], {b}))  # pyright: ignore[reportPrivateUsage] - the stage helper is the unit under test

    assert errors == 0
    assert evaluator.calls == [a]


def test_listing_with_resolver_error_is_not_evaluated() -> None:
    watch = _drive_watch(DriveRequirementSpec(max_unit_price_usd=Decimal("150.00")))
    adapter = FakeAdapter([("flaky", "100.00"), ("fine", "100.00")])
    provider = LocalCollectionProvider(adapter)
    asyncio.run(run_collection(provider, NullResolver()))
    assert is_current(_row(watch, "flaky"))

    class FailsOnFlaky:
        def resolve_listing(self, listing_id: int) -> None:
            if Listing.objects.get(pk=listing_id).source_listing_key == "flaky":
                raise RuntimeError("resolver crashed")

    evaluator = RecordingEvaluator()
    run, _ = asyncio.run(run_collection(provider, FailsOnFlaky(), evaluator=evaluator))

    assert run.status == RunStatus.SUCCESS
    assert run.detail_json["resolver_errors"] == 1
    assert run.detail_json["evaluator_errors"] == 0
    flaky = _row(watch, "flaky")
    assert flaky.listing.pk not in evaluator.calls
    # Its old row still binds the previous snapshot: pending, not a stale pass.
    assert flaky.snapshot_observed_at != _latest_observed("flaky")
    assert not is_current(flaky)
    assert is_current(_row(watch, "fine"))


def test_failed_run_does_not_evaluate() -> None:
    evaluator = RecordingEvaluator()

    class Empty(FakeAdapter):
        def parse(self, batch: RawBatch) -> list[ParsedListing]:
            return []  # authentic fetch, zero records -> PARSER_ROT

    run, _ = asyncio.run(
        run_collection(LocalCollectionProvider(Empty([])), NullResolver(), evaluator=evaluator)
    )

    assert run.status == RunStatus.FAILED
    assert evaluator.calls == []


# ── Heartbeat-fired FULL run (heartbeat.py is unedited) ──────────────────────


class FakeHeartbeatAdapter:
    """probe() and fetch()/parse() derived from the same readings, like
    tests/db/test_poller_heartbeat.py's fake."""

    name = "fake-hb"
    site_key = "serverpartdeals"
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0

    def __init__(self, stock: str) -> None:
        self.stock = stock

    def _reading(self) -> HeartbeatReading:
        return HeartbeatReading(
            source_sku="HB-EVAL",
            price=Decimal("100.00"),
            currency="USD",
            stock_status=self.stock,
            shipping_price=Decimal(0),
            http_status=200,
            latency_ms=None,
            endpoint="https://fake.invalid/probe",
        )

    async def probe(self) -> list[HeartbeatReading]:
        return [self._reading()]

    async def fetch(self) -> RawBatch:
        return RawBatch(
            source=self.name,
            fetched_at=datetime.now(UTC),
            items=[RawItem(url="https://fake.invalid/HB-EVAL", payload_json={"sku": "HB-EVAL"})],
        )

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        r = self._reading()
        return [
            ParsedListing(
                source_listing_key=r.source_sku,
                url="https://fake.invalid/HB-EVAL",
                title="Drive HB-EVAL",
                price=Decimal("100.00"),
                shipping_price=r.shipping_price,
                stock_status=r.stock_status,
                raw_url="https://fake.invalid/HB-EVAL",
            )
        ]


def test_heartbeat_fired_run_updates_watch_evaluation() -> None:
    watch = _drive_watch(DriveRequirementSpec(require_in_stock=True))
    config = SourceConfig.objects.select_related("source_site").get(
        source_site__normalized_name="serverpartdeals"
    )
    adapter = FakeHeartbeatAdapter("out_of_stock")
    asyncio.run(run_heartbeat(adapter, config, NullResolver()))  # baseline sighting fires
    first = _row(watch, "HB-EVAL")
    assert first.verdict == N

    adapter.stock = "in_stock"
    asyncio.run(run_heartbeat(adapter, config, NullResolver()))  # transition fires FULL run

    row = _row(watch, "HB-EVAL")
    assert row.verdict == M
    assert row.snapshot_observed_at == _latest_observed("HB-EVAL")
    assert row.snapshot_observed_at != first.snapshot_observed_at
    assert is_current(row)


# ── Recovery probe (poller/service.py is unedited) ───────────────────────────


def test_recovery_probe_run_evaluates(
    monkeypatch: pytest.MonkeyPatch, admit: Callable[..., None]
) -> None:
    from hw_radar.acquisition import sources
    from hw_radar.poller.service import recovery_probe_job

    watch = _drive_watch(DriveRequirementSpec(max_unit_price_usd=Decimal("150.00")))
    monkeypatch.setitem(sources.ADAPTERS, "demo", lambda: FakeAdapter([("probe-sku", "99.99")]))
    admit(("demo", "drive"))
    SourceConfig.objects.filter(source_site__normalized_name="demo").update(
        enabled=True, lifecycle_state=LifecycleState.PAUSED_PENDING_FIX
    )
    registry = BucketRegistry()
    registry.configure_source("demo", rate_per_min=60.0, burst=3, now_s=0.0)

    asyncio.run(recovery_probe_job(registry))

    row = _row(watch, "probe-sku")
    assert row.verdict == M
    assert row.snapshot_observed_at == _latest_observed("probe-sku")
    assert is_current(row)
    assert datetime.now(UTC) - row.evaluated_at < timedelta(minutes=5)
