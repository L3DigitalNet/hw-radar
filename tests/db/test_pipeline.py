import asyncio
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from django.utils import timezone

from hw_radar.acquisition import fx
from hw_radar.acquisition.contracts import NullResolver, ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.persist import upsert_listing
from hw_radar.acquisition.pipeline import (
    _grain_counts,  # pyright: ignore[reportPrivateUsage]
    _median_body_bytes,  # pyright: ignore[reportPrivateUsage]
    run_source,
)
from hw_radar.acquisition.scheduling.apply import RunOutcome, apply_run_outcome
from hw_radar.acquisition.scheduling.lifecycle import LifecycleEvent
from hw_radar.catalog.models import (
    LifecycleState,
    Listing,
    OfferSnapshot,
    RawPayload,
    ResolutionGrain,
    RetentionClass,
    RunFailureClass,
    RunKind,
    RunStatus,
    SchedulingLane,
    ScraperRun,
    SourceConfig,
    SourceSite,
)

# transaction=True: run_source writes from sync_to_async threads.
# serialized_rollback=True: TransactionTestCase truncation would otherwise delete
# the migration-0005 seed rows for every later test in the session.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


class FakeAdapter:
    name = "fake"
    site_key = "demo"
    run_kind = RunKind.FULL
    expects_json = True
    # SourceAdapter conformance only: run_source never reads the parse diagnostic.
    last_parse_skipped = 0

    def __init__(self, items: list[RawItem], parsed: list[ParsedListing]) -> None:
        self._items = items
        self._parsed = parsed

    async def fetch(self) -> RawBatch:
        # A fresh timestamp per call: observed_at is now stamped from
        # batch.fetched_at, which is half of OfferSnapshot's (listing_id,
        # observed_at) composite PK — a fixed constant here would collide on
        # the rerun test below instead of exercising DR-005's append-only path.
        return RawBatch(source=self.name, fetched_at=datetime.now(UTC), items=self._items)

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        return self._parsed


def ok_item(url: str = "https://demo.invalid/a") -> RawItem:
    return RawItem(url=url, payload_json={"sku": "a"})


def ok_parsed(key: str = "sku-a", raw_url: str = "https://demo.invalid/a") -> ParsedListing:
    return ParsedListing(
        source_listing_key=key,
        url="https://demo.invalid/a",
        title="Demo 8TB",
        price=Decimal("99.99"),
        raw_url=raw_url,
    )


def test_success_path_persists_and_reports() -> None:
    adapter = FakeAdapter([ok_item()], [ok_parsed()])
    run, outcome = asyncio.run(run_source(adapter, NullResolver()))
    assert run.status == RunStatus.SUCCESS
    assert outcome.event is LifecycleEvent.SUCCESS
    assert run.records_fetched == 1
    assert run.records_valid == 1
    assert run.listings_upserted == 1
    assert run.snapshots_appended == 1
    assert Listing.objects.filter(source_site__normalized_name="demo").count() == 1
    assert OfferSnapshot.objects.count() == 1


def test_per_item_raw_payloads_are_stored_and_associated() -> None:
    # CR-002: two RawItems, each producing one listing tagged with its own
    # raw_url, must land as two RawPayload rows with correct snapshot->raw
    # association (not the items[0]-only bug, where every snapshot pointed at
    # a single stored row regardless of provenance).
    items = [ok_item(url="https://x.test/a"), ok_item(url="https://x.test/b")]
    parsed = [
        ok_parsed(key="A", raw_url="https://x.test/a"),
        ok_parsed(key="B", raw_url="https://x.test/b"),
    ]
    adapter = FakeAdapter(items, parsed)
    asyncio.run(run_source(adapter, NullResolver()))
    assert RawPayload.objects.count() == 2  # not 1 (items[0]-only bug is dead)
    snap_a = OfferSnapshot.objects.get(listing__source_listing_key="A")
    assert snap_a.raw_payload is not None
    assert snap_a.raw_payload.endpoint == "https://x.test/a"
    snap_b = OfferSnapshot.objects.get(listing__source_listing_key="B")
    assert snap_b.raw_payload is not None
    assert snap_b.raw_payload.endpoint == "https://x.test/b"


def test_duplicate_raw_item_urls_are_not_collapsed() -> None:
    # CR-002 residual (Codex round 2): the url->RawPayload map is only for
    # snapshot association; storage itself must never collapse duplicate
    # source URLs into one row, or an evidence row silently vanishes.
    items = [ok_item(url="https://x.test/shared"), ok_item(url="https://x.test/shared")]
    parsed = [
        ok_parsed(key="C", raw_url="https://x.test/shared"),
        ok_parsed(key="D", raw_url="https://x.test/shared"),
    ]
    adapter = FakeAdapter(items, parsed)
    asyncio.run(run_source(adapter, NullResolver()))
    assert RawPayload.objects.filter(endpoint="https://x.test/shared").count() == 2


def test_bounded_retention_stamps_expires_at() -> None:
    # DR-001: bounded classes REQUIRE a non-null expires_at or the DB's
    # TTL-coherent CHECK constraint rejects the row. expires_policy is a
    # callable (not a fixed datetime) so the TTL stays relative to each
    # observation's own fetch time (DR-008 <=6h for eBay).
    adapter = FakeAdapter([ok_item()], [ok_parsed()])
    run, _ = asyncio.run(
        run_source(
            adapter,
            NullResolver(),
            retention_class=RetentionClass.EBAY_LISTING_OBSERVATION,
            expires_policy=lambda observed: observed + timedelta(hours=6),
        )
    )
    assert run.status == RunStatus.SUCCESS
    listing = Listing.objects.get(source_site__normalized_name="demo")
    assert listing.retention_class == RetentionClass.EBAY_LISTING_OBSERVATION
    assert listing.expires_at is not None  # bounded class => CHECK would have rejected a null
    assert RawPayload.objects.filter(
        retention_class=RetentionClass.EBAY_LISTING_OBSERVATION
    ).exists()


def test_rerun_is_append_only() -> None:
    adapter = FakeAdapter([ok_item()], [ok_parsed()])
    asyncio.run(run_source(adapter, NullResolver()))
    asyncio.run(run_source(adapter, NullResolver()))
    assert Listing.objects.filter(source_site__normalized_name="demo").count() == 1
    assert OfferSnapshot.objects.count() == 2
    assert ScraperRun.objects.count() == 2


def test_anti_bot_response_fails_the_run() -> None:
    blocked = RawItem(url="https://demo.invalid/a", http_status=403, content_type="text/html")
    adapter = FakeAdapter([blocked], [])
    run, outcome = asyncio.run(run_source(adapter, NullResolver()))
    assert run.status == RunStatus.FAILED
    assert run.failure_class == RunFailureClass.ANTI_BOT
    assert outcome.event is LifecycleEvent.ANTI_BOT
    assert Listing.objects.count() == 0


def test_zero_records_on_authentic_200_is_parser_rot() -> None:
    # ERR-003/EC-009: fetch looked healthy, extractor produced nothing.
    adapter = FakeAdapter([ok_item()], [])
    run, outcome = asyncio.run(run_source(adapter, NullResolver()))
    assert run.failure_class == RunFailureClass.PARSER_ROT
    assert outcome.event is LifecycleEvent.PARSER_ROT


def test_hanging_fetch_times_out_as_transient() -> None:
    # ADR-0012: hard fetch-stage timeout — a wedged adapter must not hold the job.
    class Hanging(FakeAdapter):
        async def fetch(self) -> RawBatch:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    run, outcome = asyncio.run(run_source(Hanging([], []), NullResolver(), fetch_timeout_s=0.05))
    assert run.status == RunStatus.FAILED
    assert run.failure_class == RunFailureClass.TRANSIENT
    assert outcome.event is LifecycleEvent.TRANSIENT_FAILURE


def test_resolver_crash_never_blocks_ingestion() -> None:
    # C.3: the listing persists; the resolver error is recorded, the run succeeds.
    class Exploding:
        def resolve_listing(self, listing_id: int) -> None:
            raise RuntimeError("resolver bug")

    adapter = FakeAdapter([ok_item()], [ok_parsed()])
    run, outcome = asyncio.run(run_source(adapter, Exploding()))
    assert run.status == RunStatus.SUCCESS
    assert outcome.event is LifecycleEvent.SUCCESS
    assert run.detail_json["resolver_errors"] == 1
    assert Listing.objects.count() == 1


def test_run_report_records_per_grain_resolution_counts() -> None:
    # SA-003: MS-1e needs a real per-run denominator of post-resolution grain
    # distribution, not just a pass/fail resolver_errors tally.
    class FamilyResolver:
        def resolve_listing(self, listing_id: int) -> None:
            listing = Listing.objects.get(pk=listing_id)
            listing.resolution_grain = ResolutionGrain.FAMILY
            listing.save()

    adapter = FakeAdapter([ok_item()], [ok_parsed()])
    run, outcome = asyncio.run(run_source(adapter, FamilyResolver()))
    assert run.status == RunStatus.SUCCESS
    assert outcome.event is LifecycleEvent.SUCCESS
    raw_grain_counts = run.detail_json["grain_counts"]
    assert isinstance(raw_grain_counts, dict)
    grain_counts = cast("dict[str, int]", raw_grain_counts)
    assert grain_counts["family"] == 1
    assert sum(grain_counts.values()) == run.records_valid


def test_grain_counts_sums_to_len_with_duplicate_listing_pk() -> None:
    # E1 review: _persist_all's listing_ids can contain the SAME Listing.pk
    # more than once — when two records in one batch share a source_listing_key,
    # upsert_listing (keyed on (source_site, source_listing_key)) returns the
    # SAME Listing both times, and each occurrence is appended to listing_ids.
    # (A true end-to-end run_source repro isn't reachable: those two records
    # would also share observed_at — fixed once per batch — so the second
    # append_snapshot would collide on OfferSnapshot's (listing_id, observed_at)
    # PK before persistence even finishes; that's a separate, pre-existing
    # constraint unrelated to this fix.) So pin the contract directly against
    # _grain_counts: it must tally against listing_ids WITH duplicates
    # preserved, so sum(counts.values()) == len(listing_ids) unconditionally —
    # matching records_valid, which counts every normalized record including
    # ones that upsert to a listing seen earlier in the same batch. The old
    # implementation summed DISTINCT query rows and collapsed the duplicate pk
    # to a single count, undercounting.
    site = SourceSite.objects.get(normalized_name="demo")
    normalized = fx.stamp(ok_parsed(key="dup-key"), date(2026, 1, 1))
    listing, _created = upsert_listing(site, normalized, RetentionClass.MERCHANT_FACT)
    listing.resolution_grain = ResolutionGrain.FAMILY
    listing.save()
    listing_ids = [listing.pk, listing.pk]  # same pk twice, as _persist_all would produce
    counts = _grain_counts(listing_ids)
    assert counts["family"] == 2
    assert sum(counts.values()) == len(listing_ids)  # not 1 — old distinct-row bug


def test_adapter_crash_is_classified() -> None:
    class Exploding(FakeAdapter):
        async def fetch(self) -> RawBatch:
            raise TimeoutError("socket timed out")

    run, outcome = asyncio.run(run_source(Exploding([], []), NullResolver()))
    assert run.status == RunStatus.FAILED
    assert run.failure_class == RunFailureClass.TRANSIENT
    assert outcome.event is LifecycleEvent.TRANSIENT_FAILURE


def test_apply_success_ramps_and_clears_backoff() -> None:
    config = SourceConfig.objects.get(source_site__normalized_name="demo")
    lane = config.lane_state(SchedulingLane.FULL)
    lane.clean_polls = 3
    lane.backoff_until = timezone.now() + timedelta(hours=1)
    now = timezone.now()
    apply_run_outcome(
        config, RunOutcome(LifecycleEvent.SUCCESS), lane_state=lane, now=now, rand=random.random
    )
    config.refresh_from_db()
    lane.refresh_from_db()
    assert config.lifecycle_state == LifecycleState.ACTIVE
    assert lane.current_interval_s == 1800  # 3600 halved, floored at 900
    assert lane.clean_polls == 0
    assert lane.backoff_until is None
    assert config.consecutive_failures == 0
    assert config.last_success_at is not None


def test_apply_transient_backs_off_and_resets_interval() -> None:
    config = SourceConfig.objects.get(source_site__normalized_name="demo")
    lane = config.lane_state(SchedulingLane.FULL)
    lane.current_interval_s = 900
    now = timezone.now()
    apply_run_outcome(
        config,
        RunOutcome(LifecycleEvent.TRANSIENT_FAILURE),
        lane_state=lane,
        now=now,
        rand=lambda: 1.0,
    )
    config.refresh_from_db()
    lane.refresh_from_db()
    assert config.lifecycle_state == LifecycleState.BACKING_OFF
    assert config.consecutive_failures == 1
    assert lane.clean_polls == 0
    assert lane.current_interval_s == 3600  # AW-003: cadence resets to baseline
    assert lane.backoff_until is not None
    assert lane.backoff_until > now


def test_apply_probe_failure_is_state_neutral() -> None:
    # CR-NEW-002: a failed probe on a paused source keeps it paused, untouched —
    # no back-off window, no failure counters (the daily probe job is the cadence).
    config = SourceConfig.objects.get(source_site__normalized_name="demo")
    config.lifecycle_state = LifecycleState.PAUSED_PENDING_FIX
    config.save()
    lane = config.lane_state(SchedulingLane.FULL)
    now = timezone.now()
    apply_run_outcome(
        config,
        RunOutcome(LifecycleEvent.PROBE_FAILURE),
        lane_state=lane,
        now=now,
        rand=lambda: 1.0,
    )
    config.refresh_from_db()
    lane.refresh_from_db()
    assert config.lifecycle_state == LifecycleState.PAUSED_PENDING_FIX
    assert config.consecutive_failures == 0
    assert lane.backoff_until is None
    assert config.last_run_at is not None


def _seed_full_run_history(
    site: SourceSite, *, runs: int, body_bytes: int, records_fetched: int
) -> None:
    now = timezone.now()
    for i in range(runs):
        ScraperRun.objects.create(
            source_site=site,
            run_kind=RunKind.FULL,
            status=RunStatus.SUCCESS,
            started_at=now - timedelta(minutes=i + 1),
            finished_at=now - timedelta(minutes=i + 1),
            records_fetched=records_fetched,
            detail_json={"body_bytes": body_bytes, "resolver_errors": 0},
        )


def _multi_item(n: int, size: int) -> list[RawItem]:
    return [
        RawItem(url=f"https://demo.invalid/{i}", payload_text="x" * size, payload_json={"sku": i})
        for i in range(n)
    ]


def _multi_parsed(n: int) -> list[ParsedListing]:
    return [
        ParsedListing(
            source_listing_key=f"sku-{i}",
            url=f"https://demo.invalid/{i}",
            title=f"Demo {i}",
            price=Decimal("99.99"),
        )
        for i in range(n)
    ]


def test_median_body_bytes_uses_per_item_average_not_run_total() -> None:
    # EC-007 regression: detail_json["body_bytes"] is a run-level SUM (6 items
    # x 1000 bytes = 6000), so the median basis must recover the per-item
    # average (~1000), not the raw run total (~6000).
    site = SourceSite.objects.get(normalized_name="demo")
    _seed_full_run_history(site, runs=3, body_bytes=6000, records_fetched=6)
    assert _median_body_bytes(site) == 1000


def test_healthy_multi_item_run_is_not_misclassified_as_anti_bot() -> None:
    # EC-007 regression: prior to the fix, classify_response compared each
    # item's ~1000-byte body against a run-total median of ~6000, so every
    # healthy item looked like a <20% soft-block outlier and the run failed.
    site = SourceSite.objects.get(normalized_name="demo")
    _seed_full_run_history(site, runs=3, body_bytes=6000, records_fetched=6)
    adapter = FakeAdapter(_multi_item(6, 1000), _multi_parsed(6))
    run, outcome = asyncio.run(run_source(adapter, NullResolver()))
    assert run.status == RunStatus.SUCCESS
    assert run.failure_class == ""
    assert outcome.event is LifecycleEvent.SUCCESS


def test_apply_honors_retry_after_verbatim() -> None:
    config = SourceConfig.objects.get(source_site__normalized_name="demo")
    lane = config.lane_state(SchedulingLane.FULL)
    now = timezone.now()
    apply_run_outcome(
        config,
        RunOutcome(LifecycleEvent.TRANSIENT_FAILURE, retry_after_s=120.0),
        lane_state=lane,
        now=now,
        rand=lambda: 1.0,
    )
    lane.refresh_from_db()
    assert lane.backoff_until is not None
    assert abs((lane.backoff_until - now).total_seconds() - 120.0) < 1.0
