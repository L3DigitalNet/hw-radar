# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# APScheduler 3.x is untyped (see tests/unit/test_poller.py); the scheduled-lane tests read Job objects.
"""MS-2 F1: eBay category sweeps — pagination, per-sweep completeness, per-scope absence.

The Browse fake routes on the request parameters the adapter sends: no
`category_ids` is the legacy drive sweep, otherwise (category_ids, q) picks a
sweep and `offset // 200` picks its page. A page is a body, an HTTP status, or
an exception class to raise, so every stop path of a sweep can be staged.
Bodies are synthetic (no live network); the shapes follow the live Browse
behavior recorded in acquisition.sources.ebay's module docstring.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from django.utils import timezone

from hw_radar.acquisition.contracts import NullResolver, RawBatch, RawItem
from hw_radar.acquisition.pipeline import FETCH_TIMEOUT_S, run_source
from hw_radar.acquisition.scheduling.buckets import BucketRegistry
from hw_radar.acquisition.sources import HARVEST_ADAPTERS, ebay
from hw_radar.acquisition.sources.ebay import (
    _TOKEN_CACHE,  # pyright: ignore[reportPrivateUsage]
    CATEGORY_DEADLINE_S,
    CATEGORY_SWEEPS,
    FIXED_PRICE_FILTER,
    CategorySweep,
    EbayAdapter,
    validate_sweeps,
)
from hw_radar.catalog.models import (
    CheapSignal,
    DelistReason,
    LifecycleState,
    Listing,
    OfferSnapshot,
    RawPayload,
    RunKind,
    RunStatus,
    SchedulingLane,
    ScopeSweepContinuity,
    ScraperRun,
    SourceConfig,
    SourceLaneState,
)
from hw_radar.poller.service import build_scheduler, load_schedules, poll_source

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

GPU = CategorySweep(slug="gpu", query_id="rtx-3090", category_id="27386", q="RTX 3090", max_pages=3)
# Same category, different query: a complete rtx-3090 sweep must not delist it.
GPU_4090 = CategorySweep(
    slug="gpu", query_id="rtx-4090", category_id="27386", q="RTX 4090", max_pages=2
)
RAM = CategorySweep(
    slug="ram", query_id="ddr4", category_id="11210", q="32GB DDR4 ECC RDIMM", max_pages=2
)
CPU = CategorySweep(
    slug="cpu", query_id="epyc-7302", category_id="56088", q="EPYC 7302", max_pages=2
)
ALL = (GPU, GPU_4090, RAM, CPU)

REFDATA_SEEDS = Path(ebay.__file__).parents[2] / "refdata" / "seeds"

TOKEN_BODY = {"access_token": "SYNTH-TOKEN", "expires_in": 7200}

type Page = dict[str, object] | int | type[Exception]


def _summary(key: str, *, title: str | None = None) -> dict[str, object]:
    return {
        "itemId": key,
        "title": title or f"Listing {key}",
        "itemWebUrl": f"https://www.ebay.com/itm/{key}",
        "price": {"value": "100.00", "currency": "USD"},
        "itemLocation": {"country": "US"},
    }


def _page(keys: Sequence[str], *, total: int, more: bool = False) -> dict[str, object]:
    body: dict[str, object] = {"itemSummaries": [_summary(k) for k in keys], "total": total}
    if more:
        body["next"] = "https://api.ebay.com/buy/browse/v1/item_summary/search?offset=next"
    return body


# Legacy drive page that can never prove completeness (total 50 > 1 summary), so
# the NULL scope only ever reaches the grace path in these tests.
def _legacy(*keys: str) -> dict[str, object]:
    return _page(keys or ("drive-a",), total=50)


class Browse:
    """Browse fake; `calls` logs "legacy" or "<category_id>:<offset>" per search."""

    def __init__(self, legacy: dict[str, object], sweeps: dict[CategorySweep, list[Page]]) -> None:
        self.legacy = legacy
        self.pages = {(s.category_id, s.q): pages for s, pages in sweeps.items()}
        self.calls: list[str] = []
        # Seconds to stall before answering a call, keyed like `calls`.
        self.delays: dict[str, float] = {}
        # Statuses for successive token requests; exhausted -> 200.
        self.token_statuses: list[int] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            status = self.token_statuses.pop(0) if self.token_statuses else 200
            return httpx.Response(status, json=TOKEN_BODY if status == 200 else {})
        params = request.url.params
        category = params.get("category_ids")
        if category is None:
            self.calls.append("legacy")
            return httpx.Response(200, json=self.legacy)
        offset = int(params["offset"])
        label = f"{category}:{offset}"
        self.calls.append(label)
        if label in self.delays:
            await asyncio.sleep(self.delays[label])
        assert params["limit"] == "200"
        assert params["filter"] == FIXED_PRICE_FILTER
        page = self.pages[(category, params["q"])][offset // 200]
        if isinstance(page, int):
            return httpx.Response(page, json={"errors": [{"errorId": 99999}]})
        if isinstance(page, type):
            raise page("synthetic transport failure")
        return httpx.Response(200, json=page)

    def adapter(self, sweeps: Sequence[CategorySweep] = ALL) -> EbayAdapter:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return EbayAdapter(client=client, category_sweeps=sweeps)


@pytest.fixture(autouse=True)
def ebay_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("EBAY_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("EBAY_API_BASE", "https://api.ebay.com")
    _TOKEN_CACHE.clear()
    yield
    _TOKEN_CACHE.clear()


@pytest.fixture(scope="module")
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    lo = asyncio.new_event_loop()
    asyncio.set_event_loop(lo)
    yield lo
    asyncio.set_event_loop(None)
    lo.close()


def _run(
    loop: asyncio.AbstractEventLoop, adapter: EbayAdapter, *, run_kind: RunKind | None = None
) -> ScraperRun:
    run, _ = loop.run_until_complete(
        run_source(
            adapter,
            NullResolver(),
            retention_class=EbayAdapter.retention_class,
            expires_policy=EbayAdapter.expires_policy,
            run_kind=run_kind,
        )
    )
    return run


def _scopes(run: ScraperRun) -> dict[str | None, dict[str, object]]:
    entries = cast("list[dict[str, object]]", run.detail_json["scopes"])
    return {cast("str | None", e["scope_key"]): e for e in entries}


def _lane() -> SourceLaneState:
    return SourceConfig.objects.get(source_site__normalized_name="ebay").lane_state(
        SchedulingLane.FULL
    )


def _row(sweep: CategorySweep) -> ScopeSweepContinuity:
    return ScopeSweepContinuity.objects.get(
        source_site__normalized_name="ebay", collection_scope=sweep.scope_key
    )


def _all_complete_sweeps() -> dict[CategorySweep, list[Page]]:
    return {
        GPU: [_page(["gpu-1", "gpu-2"], total=2)],
        GPU_4090: [_page(["gpu4-1"], total=1)],
        RAM: [_page(["ram-1"], total=1)],
        CPU: [_page(["cpu-1"], total=1)],
    }


def test_every_category_listing_carries_its_sweep_hint_and_scope(
    loop: asyncio.AbstractEventLoop,
) -> None:
    # R14: an unhinted non-drive item resolves as a drive, so the hint (and the
    # MS2-D-12 scope) must come from the sweep for every item; the legacy drive
    # items keep neither.
    browse = Browse(_legacy("drive-a"), _all_complete_sweeps())
    adapter = browse.adapter()
    batch = loop.run_until_complete(adapter.fetch())
    by_key = {p.source_listing_key: p for p in adapter.parse(batch)}
    expected = {
        "drive-a": (None, None),
        "gpu-1": ("gpu", "ebay:gpu:rtx-3090"),
        "gpu-2": ("gpu", "ebay:gpu:rtx-3090"),
        "gpu4-1": ("gpu", "ebay:gpu:rtx-4090"),
        "ram-1": ("ram", "ebay:ram:ddr4"),
        "cpu-1": ("cpu", "ebay:cpu:epyc-7302"),
    }
    assert {k: (p.category_hint, p.collection_scope) for k, p in by_key.items()} == expected

    run = _run(loop, browse.adapter())
    assert run.status == RunStatus.SUCCESS
    stored = dict(
        Listing.objects.filter(source_site__normalized_name="ebay").values_list(
            "source_listing_key", "collection_scope"
        )
    )
    assert stored == {k: scope for k, (_, scope) in expected.items()}
    hints = dict(
        OfferSnapshot.objects.filter(listing__source_listing_key__in=list(expected)).values_list(
            "listing__source_listing_key", "attrs_json__category_hint"
        )
    )
    assert hints == {k: hint for k, (hint, _) in expected.items()}


def test_title_never_substitutes_for_the_hint() -> None:
    # A GPU-looking title on the legacy drive page stays unhinted, and a
    # drive-looking title on a GPU page is still hinted gpu: the query scope is
    # the only source of the hint (MS2-D-03).
    gpu_url = str(
        httpx.URL("https://api.ebay.com/buy/browse/v1/item_summary/search", params=GPU.params(0))
    )
    legacy_body: dict[str, object] = {
        "itemSummaries": [_summary("x-1", title="NVIDIA RTX 3090 24GB")],
        "total": 50,
    }
    gpu_body: dict[str, object] = {
        "itemSummaries": [_summary("x-2", title="Seagate Exos 18TB HDD")],
        "total": 1,
    }
    batch = RawBatch(
        source="ebay",
        fetched_at=timezone.now(),
        items=[
            RawItem(
                url="https://api.ebay.com/buy/browse/v1/item_summary/search",
                payload_json=legacy_body,
            ),
            RawItem(url=gpu_url, payload_json=gpu_body),
        ],
    )
    by_key = {p.source_listing_key: p for p in EbayAdapter(category_sweeps=ALL).parse(batch)}
    assert by_key["x-1"].category_hint is None
    assert by_key["x-2"].category_hint == "gpu"


def test_unconfigured_sweep_page_is_dropped_not_unhinted() -> None:
    # A replayed page from a sweep this adapter does not know would otherwise
    # land unhinted and resolve as a drive; it is dropped and counted instead.
    url = str(
        httpx.URL("https://api.ebay.com/buy/browse/v1/item_summary/search", params=GPU.params(0))
    )
    batch = RawBatch(
        source="ebay",
        fetched_at=timezone.now(),
        items=[RawItem(url=url, payload_json=_page(["gpu-1", "gpu-2"], total=2))],
    )
    adapter = EbayAdapter()  # legacy-only: no GPU sweep configured
    assert adapter.parse(batch) == []
    assert adapter.last_parse_skipped == 2


def test_multi_page_sweep_within_cap_is_unprovable(loop: asyncio.AbstractEventLoop) -> None:
    # Every page-level check passes (no `next` at the end, stable total, all
    # ids seen), yet offset paging over a moving ranking cannot prove it saw
    # everything: the sweep stays incomplete but still polls its scope.
    pages: list[Page] = [
        _page(["g-1", "g-2"], total=5, more=True),
        _page(["g-3", "g-4"], total=5, more=True),
        _page(["g-5"], total=5),
    ]
    browse = Browse(_legacy(), {GPU: pages})
    run = _run(loop, browse.adapter([GPU]))

    assert run.status == RunStatus.SUCCESS
    assert browse.calls == ["legacy", "27386:0", "27386:200", "27386:400"]
    assert _scopes(run)[GPU.scope_key] == {
        "scope_key": GPU.scope_key,
        "pages": 3,
        "complete": False,
        "reason": "multi_page_unprovable",
        "continuity": "recorded",
        "delisted": 0,
    }
    assert _row(GPU).last_complete_sweep_at is None
    assert _row(GPU).continuous_since is not None
    assert Listing.objects.filter(collection_scope=GPU.scope_key).count() == 5


def test_single_page_sweep_is_complete(loop: asyncio.AbstractEventLoop) -> None:
    browse = Browse(_legacy(), {GPU: [_page(["g-1", "g-2"], total=2)]})
    run = _run(loop, browse.adapter([GPU]))
    outcome = _scopes(run)[GPU.scope_key]
    assert (outcome["complete"], outcome["reason"], outcome["pages"]) == (True, "complete", 1)
    # The watermark a complete sweep raises proves the gate let it through.
    assert _row(GPU).last_complete_sweep_at is not None


def test_offset_churn_never_delists_a_live_listing(loop: asyncio.AbstractEventLoop) -> None:
    # The verifier's g201 case, scaled down: between page 1 and page 2 one
    # ranked-earlier item is removed and one new item is added, so g-mid
    # slides across the page boundary unseen while `total` and the distinct-id
    # count both still match. Read as complete, g-mid (live) is delisted and
    # redacted on the spot.
    browse = Browse(_legacy(), {GPU: [_page(["g-1", "g-mid"], total=2)]})
    _run(loop, browse.adapter([GPU]))
    browse.pages = {  # pyright: ignore[reportAttributeAccessIssue] - Page is a union
        (GPU.category_id, GPU.q): [
            _page(["g-1"], total=2, more=True),
            _page(["g-new"], total=2),
        ]
    }
    run = _run(loop, browse.adapter([GPU]))

    assert _scopes(run)[GPU.scope_key]["reason"] == "multi_page_unprovable"
    assert run.detail_json["listings_delisted"] == 0
    assert Listing.objects.get(source_listing_key="g-mid").delisted_at is None


def test_page_cap_hit_is_incomplete(loop: asyncio.AbstractEventLoop) -> None:
    capped = CategorySweep(
        slug="gpu", query_id="rtx-3090", category_id="27386", q="RTX 3090", max_pages=2
    )
    pages: list[Page] = [
        _page(["g-1"], total=3, more=True),
        _page(["g-2"], total=3, more=True),
        _page(["g-3"], total=3),  # exists, but past the cap: never requested
    ]
    browse = Browse(_legacy(), {capped: pages})
    run = _run(loop, browse.adapter([capped]))

    assert browse.calls == ["legacy", "27386:0", "27386:200"]
    outcome = _scopes(run)[capped.scope_key]
    assert (outcome["complete"], outcome["reason"], outcome["pages"]) == (False, "page_cap", 2)
    assert _row(capped).last_complete_sweep_at is None


def test_silent_window_end_is_incomplete(loop: asyncio.AbstractEventLoop) -> None:
    # Live Browse past its window: 200 OK, 0 items, total 0, and no `next`.
    # Read naively that is "last page, no next" — i.e. complete.
    # Browse omits itemSummaries entirely on an empty page.
    pages: list[Page] = [_page(["g-1"], total=1, more=True), {"total": 0}]
    browse = Browse(_legacy(), {GPU: pages})
    run = _run(loop, browse.adapter([GPU]))

    assert run.status == RunStatus.SUCCESS
    outcome = _scopes(run)[GPU.scope_key]
    assert (outcome["complete"], outcome["reason"]) == (False, "silent_window_end")


def test_total_moving_mid_sweep_is_incomplete(loop: asyncio.AbstractEventLoop) -> None:
    # An item removed while paging shifts later offsets and can skip another
    # item with no other symptom; the moving total is the only witness.
    pages: list[Page] = [_page(["g-1", "g-2"], total=4, more=True), _page(["g-4"], total=3)]
    browse = Browse(_legacy(), {GPU: pages})
    run = _run(loop, browse.adapter([GPU]))
    assert _scopes(run)[GPU.scope_key]["reason"] == "total_unstable"


def test_last_page_short_of_total_is_incomplete(loop: asyncio.AbstractEventLoop) -> None:
    # No `next`, but fewer distinct items than `total`: the extra guard.
    browse = Browse(_legacy(), {GPU: [_page(["g-1", "g-2"], total=5)]})
    run = _run(loop, browse.adapter([GPU]))
    outcome = _scopes(run)[GPU.scope_key]
    assert (outcome["complete"], outcome["reason"]) == (False, "total_exceeds_seen")


def test_failed_intermediate_page_is_incomplete_and_keeps_the_run(
    loop: asyncio.AbstractEventLoop,
) -> None:
    pages: list[Page] = [_page(["g-1", "g-2"], total=5, more=True), 503]
    browse = Browse(_legacy("drive-a"), {GPU: pages, CPU: [_page(["cpu-1"], total=1)]})
    run = _run(loop, browse.adapter([GPU, CPU]))

    # The failed page fails its sweep, not the run: the drive sweep and the
    # other category still land, and nothing classifies the run on a 503 that
    # is not in its batch.
    assert run.status == RunStatus.SUCCESS
    assert browse.calls == ["legacy", "27386:0", "27386:200", "56088:0"]
    outcomes = _scopes(run)
    gpu = outcomes[GPU.scope_key]
    assert (gpu["complete"], gpu["reason"], gpu["pages"]) == (False, "page_failed:http_503", 1)
    assert gpu["continuity"] == "recorded"  # a truncated sweep still polled its scope
    assert outcomes[CPU.scope_key]["complete"] is True
    assert set(
        Listing.objects.filter(source_site__normalized_name="ebay").values_list(
            "source_listing_key", flat=True
        )
    ) == {"drive-a", "g-1", "g-2", "cpu-1"}
    assert not RawPayload.objects.exclude(http_status=200).exists()
    assert run.records_fetched == 3  # legacy + GPU page 1 + CPU page 1


def test_sweep_failing_on_its_first_page_breaks_that_scope_only(
    loop: asyncio.AbstractEventLoop,
) -> None:
    browse = Browse(
        _legacy("drive-a"),
        {GPU: [_page(["g-1"], total=1)], CPU: [httpx.ConnectError]},
    )
    first = _run(loop, browse.adapter([GPU, CPU]))
    cpu = _scopes(first)[CPU.scope_key]
    assert (cpu["pages"], cpu["complete"], cpu["continuity"]) == (
        0,
        False,
        "broken",
    )
    assert cpu["reason"] == "page_failed:ConnectError"
    row = _row(CPU)
    assert row.continuous_since is None
    assert row.continuity_broken_at is not None
    assert _row(GPU).continuous_since is not None
    lane = _lane()
    assert lane.continuous_since is not None
    assert lane.continuity_broken_at is None


def test_complete_gpu_sweep_delists_only_its_own_scope(loop: asyncio.AbstractEventLoop) -> None:
    browse = Browse(
        _legacy("drive-a", "drive-b"),
        {
            GPU: [_page(["gpu-1", "gpu-2"], total=2)],
            GPU_4090: [_page(["gpu4-1", "gpu4-2"], total=2)],
            RAM: [_page(["ram-1", "ram-2"], total=2)],
            CPU: [_page(["cpu-1", "cpu-2"], total=2)],
        },
    )
    _run(loop, browse.adapter())

    # Second sweep: the complete GPU sweep lost gpu-2. Every other scope also
    # lost its second key, but none of them proves it: the drive page and the
    # RAM/CPU/4090 sweeps are truncated (total > seen), and fresh listings are
    # nowhere near the stale-absence grace.
    browse.legacy = _legacy("drive-a")
    browse.pages = {  # pyright: ignore[reportAttributeAccessIssue] - Page is a union
        (GPU.category_id, GPU.q): [_page(["gpu-1"], total=1)],
        (GPU_4090.category_id, GPU_4090.q): [_page(["gpu4-1"], total=2)],
        (RAM.category_id, RAM.q): [_page(["ram-1"], total=2)],
        (CPU.category_id, CPU.q): [_page(["cpu-1"], total=2)],
    }
    run = _run(loop, browse.adapter())

    assert run.detail_json["listings_delisted"] == 1
    delisted = Listing.objects.filter(
        source_site__normalized_name="ebay", delisted_at__isnull=False
    )
    assert [(d.source_listing_key, d.delist_reason) for d in delisted] == [
        ("gpu-2", DelistReason.ABSENT_FROM_SWEEP)
    ]
    assert {k: v["delisted"] for k, v in _scopes(run).items()} == {
        None: 0,
        GPU.scope_key: 1,
        GPU_4090.scope_key: 0,
        RAM.scope_key: 0,
        CPU.scope_key: 0,
    }


def test_gpu_sweep_advances_only_its_own_continuity(loop: asyncio.AbstractEventLoop) -> None:
    site = SourceConfig.objects.get(source_site__normalized_name="ebay").source_site
    ram_before = ScopeSweepContinuity.objects.create(
        source_site=site,
        collection_scope=RAM.scope_key,
        continuous_since=timezone.now() - timedelta(hours=3),
        last_eligible_sweep_at=timezone.now() - timedelta(minutes=10),
    )
    browse = Browse(_legacy(), {GPU: [_page(["g-1"], total=1)]})
    first = _run(loop, browse.adapter([GPU]))
    gpu_started = _row(GPU).continuous_since
    null_started = _lane().continuous_since
    assert gpu_started is not None
    assert null_started is not None

    second = _run(loop, browse.adapter([GPU]))

    gpu = _row(GPU)
    assert gpu.continuous_since == gpu_started  # one unbroken run of sweeps
    assert gpu.last_eligible_sweep_at is not None
    assert gpu.last_eligible_sweep_at > gpu_started
    ram = _row(RAM)
    assert (ram.continuous_since, ram.last_eligible_sweep_at, ram.continuity_broken_at) == (
        ram_before.continuous_since,
        ram_before.last_eligible_sweep_at,
        None,
    )
    # The legacy NULL scope keeps its own SourceLaneState continuity: the GPU
    # sweep in the same run must not break it (MS2-D-31 applies only to runs
    # that did not sweep the NULL scope themselves).
    lane = _lane()
    assert lane.continuous_since == null_started
    assert lane.continuity_broken_at is None
    assert first.status == second.status == RunStatus.SUCCESS


def test_run_without_a_legacy_sweep_result_breaks_the_null_scope(
    loop: asyncio.AbstractEventLoop,
) -> None:
    # An empty drive page beside a productive category sweep: the run succeeds,
    # but it did not sweep the NULL scope, so it must not pose as its
    # predecessor (MS2-D-31).
    browse = Browse({"itemSummaries": [], "total": 0}, {GPU: [_page(["g-1"], total=1)]})
    run = _run(loop, browse.adapter([GPU]))
    assert run.status == RunStatus.SUCCESS
    assert None not in _scopes(run)
    lane = _lane()
    assert lane.continuous_since is None
    assert lane.continuity_broken_at is not None


def test_short_final_page_is_not_a_soft_block(loop: asyncio.AbstractEventLoop) -> None:
    # EC-007 compares each item's body with the median per-item body size of
    # earlier runs. A padded legacy page makes that median large; the CPU
    # sweep's one-item page is far below 20% of it, which is a legitimate
    # short page, not a block page.
    padded = [_summary(f"drive-{i}", title="Recertified enterprise drive " * 40) for i in range(40)]
    legacy: dict[str, object] = {"itemSummaries": padded, "total": 500}
    browse = Browse(legacy, {CPU: [_page(["cpu-1"], total=1)]})
    first = _run(loop, browse.adapter([CPU]))
    second = _run(loop, browse.adapter([CPU]))
    assert first.status == RunStatus.SUCCESS
    assert second.status == RunStatus.SUCCESS, second.error


def test_probe_is_one_request_and_a_fired_run_skips_category_sweeps(
    loop: asyncio.AbstractEventLoop,
) -> None:
    browse = Browse(_legacy("drive-a"), _all_complete_sweeps())
    adapter = browse.adapter()

    readings = loop.run_until_complete(adapter.probe())
    assert browse.calls == ["legacy"]
    assert [r.source_sku for r in readings] == ["drive-a"]

    # run_heartbeat fires the FULL run on the instance it probed; that run
    # stays at one Browse call and leaves every category scope untouched.
    run = _run(loop, adapter, run_kind=RunKind.FULL)
    assert browse.calls == ["legacy", "legacy"]
    assert list(_scopes(run)) == [None]
    assert not ScopeSweepContinuity.objects.exists()


def test_listing_seen_only_by_the_drive_search_survives_a_complete_scope_sweep(
    loop: asyncio.AbstractEventLoop,
) -> None:
    # x-1 was swept into the GPU scope; this run only the drive search sees
    # it. A scope-less observation keeps the row's GPU scope, so the complete
    # GPU sweep that omits x-1 would delist a listing this very run observed.
    browse = Browse(_legacy("drive-a"), {GPU: [_page(["gpu-1", "x-1"], total=2)]})
    _run(loop, browse.adapter([GPU]))
    assert Listing.objects.get(source_listing_key="x-1").collection_scope == GPU.scope_key

    browse.legacy = _legacy("drive-a", "x-1")
    browse.pages = {(GPU.category_id, GPU.q): [_page(["gpu-1"], total=1)]}  # pyright: ignore[reportAttributeAccessIssue]
    run = _run(loop, browse.adapter([GPU]))

    assert _scopes(run)[GPU.scope_key]["complete"] is True
    assert run.detail_json["listings_delisted"] == 0
    assert Listing.objects.get(source_listing_key="x-1").delisted_at is None


def test_one_key_from_two_sweeps_persists_once_under_the_first_sweep(
    loop: asyncio.AbstractEventLoop,
) -> None:
    # dup-1 comes back from the drive search, the GPU sweep and the RAM sweep.
    # One record per key survives parse: the first category sweep in
    # configuration order (GPU), never the unhinted drive record.
    browse = Browse(
        _legacy("drive-a", "dup-1"),
        {GPU: [_page(["dup-1", "gpu-1"], total=2)], RAM: [_page(["dup-1"], total=1)]},
    )
    adapter = browse.adapter([GPU, RAM])
    batch = loop.run_until_complete(adapter.fetch())
    parsed = adapter.parse(batch)
    assert [p.source_listing_key for p in parsed] == ["drive-a", "dup-1", "gpu-1"]
    dup = next(p for p in parsed if p.source_listing_key == "dup-1")
    assert (dup.category_hint, dup.collection_scope) == ("gpu", GPU.scope_key)
    # Each sweep still vouches for what it saw, so RAM's complete sweep
    # does not lose dup-1 from its seen keys.
    reports = {r.scope_key: r for r in adapter.delist_scopes(batch, parsed)}
    ram_scope = reports[RAM.scope_key].scope
    assert ram_scope is not None
    assert ram_scope.seen_keys == frozenset({"dup-1"})

    run = _run(loop, browse.adapter([GPU, RAM]))
    assert run.status == RunStatus.SUCCESS
    listing = Listing.objects.get(source_listing_key="dup-1")
    assert listing.collection_scope == GPU.scope_key
    assert OfferSnapshot.objects.filter(listing=listing).count() == 1


def test_category_deadline_sits_inside_the_fetch_timeout() -> None:
    assert CATEGORY_DEADLINE_S <= FETCH_TIMEOUT_S / 2


def test_time_budget_ends_the_sweep_not_the_run(
    loop: asyncio.AbstractEventLoop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ebay, "CATEGORY_DEADLINE_S", 0.3)
    pages: list[Page] = [_page(["g-1"], total=2, more=True), _page(["g-2"], total=2)]
    browse = Browse(_legacy("drive-a"), {GPU: pages, CPU: [_page(["cpu-1"], total=1)]})
    browse.delays["27386:200"] = 5.0  # stalls past the deadline, far inside the client timeout
    run = _run(loop, browse.adapter([GPU, CPU]))

    assert run.status == RunStatus.SUCCESS, run.error
    outcomes = _scopes(run)
    gpu = outcomes[GPU.scope_key]
    assert (gpu["complete"], gpu["reason"], gpu["pages"]) == (False, "time_budget", 1)
    # The deadline covers every sweep: CPU is never started and breaks.
    cpu = outcomes[CPU.scope_key]
    assert (cpu["reason"], cpu["pages"], cpu["continuity"]) == ("time_budget", 0, "broken")
    assert browse.calls == ["legacy", "27386:0", "27386:200"]
    assert Listing.objects.filter(source_listing_key__in=["drive-a", "g-1"]).count() == 2


def test_any_category_page_exception_fails_only_its_sweep(
    loop: asyncio.AbstractEventLoop,
) -> None:
    # The GPU page answers 401; the re-mint's token POST then answers 500 and
    # raise_for_status raises HTTPStatusError, not a TransportError.
    browse = Browse(_legacy("drive-a"), {GPU: [401], CPU: [_page(["cpu-1"], total=1)]})
    browse.token_statuses = [200, 500]
    run = _run(loop, browse.adapter([GPU, CPU]))

    assert run.status == RunStatus.SUCCESS, run.error
    outcomes = _scopes(run)
    assert outcomes[GPU.scope_key]["reason"] == "page_failed:HTTPStatusError"
    assert outcomes[GPU.scope_key]["continuity"] == "broken"
    assert outcomes[CPU.scope_key]["complete"] is True
    assert Listing.objects.filter(source_listing_key__in=["drive-a", "cpu-1"]).count() == 2


def test_malformed_category_summary_is_a_parse_drop_not_a_run_failure(
    loop: asyncio.AbstractEventLoop,
) -> None:
    # A zero price fails ParsedListing validation (price > 0). On a category page
    # that is the sweep's parse drop; it must not fail the run as PARSER_ROT and
    # take the drive search with it (verifier finding, 2026-09-25).
    bad = _summary("gpu-bad")
    bad["price"] = {"value": "0", "currency": "USD"}
    page = _page(["gpu-1"], total=2)
    summaries = cast("list[dict[str, object]]", page["itemSummaries"])
    summaries.append(bad)
    browse = Browse(_legacy("drive-a"), {GPU: [page], CPU: [_page(["cpu-1"], total=1)]})
    run = _run(loop, browse.adapter([GPU, CPU]))

    assert run.status == RunStatus.SUCCESS, run.error
    outcomes = _scopes(run)
    assert outcomes[GPU.scope_key]["complete"] is False
    assert outcomes[GPU.scope_key]["reason"] == "parse_drop"
    assert outcomes[CPU.scope_key]["complete"] is True
    keys = set(Listing.objects.values_list("source_listing_key", flat=True))
    assert {"drive-a", "gpu-1", "cpu-1"} <= keys
    assert "gpu-bad" not in keys


def test_scheduled_adapter_sweeps_every_category() -> None:
    # harvest_corpus's entry carries every sweep; the poller's is matrix-filtered
    # (tests/db/test_source_admission.py).
    adapter = HARVEST_ADAPTERS["ebay"]()
    assert isinstance(adapter, EbayAdapter)
    batch = RawBatch(source="ebay", fetched_at=timezone.now(), items=[])
    assert adapter.delist_scopes(batch, []) == []  # nothing fetched, nothing reported
    assert [(s.scope_key, s.category_id, s.q, s.max_pages) for s in CATEGORY_SWEEPS] == [
        ("ebay:gpu:rtx-3090", "27386", "RTX 3090", 5),
        ("ebay:ram:ddr4-ecc-rdimm-32gb", "11210", "32GB DDR4 ECC RDIMM", 4),
        ("ebay:cpu:epyc-9354-56088", "56088", "EPYC 9354", 1),
        ("ebay:cpu:epyc-9654-56088", "56088", "EPYC 9654", 1),
        ("ebay:cpu:epyc-7763-56088", "56088", "EPYC 7763", 1),
        ("ebay:cpu:epyc-7742-56088", "56088", "EPYC 7742", 1),
        ("ebay:cpu:epyc-9354-164", "164", "EPYC 9354", 1),
        ("ebay:cpu:epyc-7763-164", "164", "EPYC 7763", 1),
    ]
    assert all(s.filter == FIXED_PRICE_FILTER for s in CATEGORY_SWEEPS)


def test_cpu_sweeps_query_only_seeded_epyc_models() -> None:
    # F6: a CPU scope exists to be matched against the first-party catalog, so
    # every query must be a seeded model_number (an unseeded query, like the
    # earlier EPYC 7302 pilot, can never produce an accept).
    seed = json.loads((REFDATA_SEEDS / "amd-epyc.json").read_text(encoding="utf-8"))
    seeded = {model["model_number"] for model in seed["models"]}
    cpu_queries = {s.q for s in CATEGORY_SWEEPS if s.slug == "cpu"}
    assert cpu_queries <= seeded


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"slug": "drive"}, "non-drive category"),
        ({"slug": "zz-unregistered"}, "non-drive category"),
        ({"max_pages": 51}, "Browse window"),
        ({"max_pages": 0}, "Browse window"),
        ({"category_id": "27386,11210"}, "not numeric"),
        ({"query_id": "Bad Id"}, "invalid"),
    ],
)
def test_invalid_sweep_is_rejected(kwargs: dict[str, object], message: str) -> None:
    base: dict[str, object] = {
        "slug": "gpu",
        "query_id": "rtx-3090",
        "category_id": "27386",
        "q": "RTX 3090",
    }
    with pytest.raises(ValueError, match=message):
        CategorySweep(**{**base, **kwargs})  # pyright: ignore[reportArgumentType]


def test_sweep_set_must_fit_one_run() -> None:
    with pytest.raises(ValueError, match="unique scope keys"):
        validate_sweeps([GPU, GPU])
    same_query = CategorySweep(slug="gpu", query_id="other", category_id="27386", q="RTX 3090")
    with pytest.raises(ValueError, match="unique"):
        validate_sweeps([GPU, same_query])
    wide = [
        CategorySweep(slug="gpu", query_id=f"q{i}", category_id="27386", q=f"q{i}", max_pages=8)
        for i in range(2)
    ]
    with pytest.raises(ValueError, match="pages per run"):
        validate_sweeps(wide)


# ── Scheduled category sweeps (the poller's full lane) ──────────────────────

CPU_SWEEPS = tuple(s for s in CATEGORY_SWEEPS if s.slug == "cpu")


def _install_browse(monkeypatch: pytest.MonkeyPatch, browse: Browse) -> None:
    # The scheduled path builds its own adapter (sources.admitted_ebay_adapter)
    # with no injected client, so the fake goes in at the AsyncClient seam.
    real = httpx.AsyncClient

    def client(**kwargs: object) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(browse.handler), **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(ebay.httpx, "AsyncClient", client)


def _enabled_ebay_scheduler() -> AsyncIOScheduler:
    """Enable the SEEDED eBay row (migration 0011's heartbeat flags kept) and build
    the scheduler the poller would, from the enabled rows."""
    SourceConfig.objects.filter(source_site__normalized_name="ebay").update(
        enabled=True, lifecycle_state=LifecycleState.ACTIVE
    )
    config = SourceConfig.objects.get(source_site__normalized_name="ebay")
    # The regression lives exactly here: a heartbeat-enabled EBAY_BROWSE row
    # used to get the heartbeat job alone, whose path never sweeps categories.
    assert config.heartbeat_enabled
    assert config.cheap_signal == CheapSignal.EBAY_BROWSE
    configs = list(SourceConfig.objects.select_related("source_site").filter(enabled=True))
    registry = BucketRegistry()
    registry.configure_source("ebay", rate_per_min=60.0, burst=10, now_s=0.0)
    return build_scheduler(registry, load_schedules(configs))


def test_admitted_cpu_cell_schedules_cpu_sweeps_that_delist_per_complete_scope(
    loop: asyncio.AbstractEventLoop,
    monkeypatch: pytest.MonkeyPatch,
    admit: Callable[..., None],
) -> None:
    admit(("ebay", "cpu"))
    scheduler = _enabled_ebay_scheduler()
    job = scheduler.get_job("poll-ebay")
    assert job is not None
    assert job.func is poll_source

    browse = Browse(
        _legacy(),
        {s: [_page([f"{s.query_id}-a", f"{s.query_id}-b"], total=2)] for s in CPU_SWEEPS},
    )
    _install_browse(monkeypatch, browse)
    loop.run_until_complete(job.func(*job.args))
    # Exactly one single-page request per CPU scope: no legacy drive GET and no
    # GPU/RAM page, because only (ebay, cpu) is admitted.
    assert sorted(browse.calls) == sorted(f"{s.category_id}:0" for s in CPU_SWEEPS)

    # Second pass: every scope lost its -b listing. Five sweeps prove it
    # (total 1, one page, no next); the last still reports total 2, so its
    # absence is unproven and must wait out the grace instead.
    incomplete = CPU_SWEEPS[-1]
    browse.calls.clear()
    browse.pages = {  # pyright: ignore[reportAttributeAccessIssue] - Page is a union
        (s.category_id, s.q): [_page([f"{s.query_id}-a"], total=2 if s is incomplete else 1)]
        for s in CPU_SWEEPS
    }
    loop.run_until_complete(job.func(*job.args))
    assert len(browse.calls) == len(CPU_SWEEPS)

    delisted = set(
        Listing.objects.filter(
            source_site__normalized_name="ebay", delisted_at__isnull=False
        ).values_list("source_listing_key", flat=True)
    )
    assert delisted == {f"{s.query_id}-b" for s in CPU_SWEEPS if s is not incomplete}
    run = ScraperRun.objects.filter(source_site__normalized_name="ebay").latest("started_at")
    assert {k: (v["complete"], v["delisted"]) for k, v in _scopes(run).items()} == {
        s.scope_key: (s is not incomplete, 0 if s is incomplete else 1) for s in CPU_SWEEPS
    }


def test_drive_and_cpu_admitted_keep_the_heartbeat_and_still_sweep_cpu(
    loop: asyncio.AbstractEventLoop,
    monkeypatch: pytest.MonkeyPatch,
    admit: Callable[..., None],
) -> None:
    admit(("ebay", "drive"), ("ebay", "cpu"))
    scheduler = _enabled_ebay_scheduler()
    heartbeat_job = scheduler.get_job("poll-heartbeat-ebay")
    full_job = scheduler.get_job("poll-ebay")
    assert heartbeat_job is not None
    assert full_job is not None

    browse = Browse(
        _legacy("drive-a"),
        {s: [_page([f"{s.query_id}-a"], total=1)] for s in CPU_SWEEPS},
    )
    _install_browse(monkeypatch, browse)

    # The heartbeat stays drive-only: its probe is the drive GET, and the FULL
    # run it fires on first sight of drive-a (a transition) skips every sweep.
    loop.run_until_complete(heartbeat_job.func(*heartbeat_job.args))
    assert browse.calls == ["legacy", "legacy"]

    # The scheduled full lane sweeps every CPU scope, and carries the drive GET
    # too: without it this scoped run would break the NULL scope's continuity
    # (stages.apply_absence) every cycle and drive stale-absence could never open.
    browse.calls.clear()
    loop.run_until_complete(full_job.func(*full_job.args))
    assert browse.calls[0] == "legacy"
    assert sorted(browse.calls[1:]) == sorted(f"{s.category_id}:0" for s in CPU_SWEEPS)
    assert _lane().continuous_since is not None


def test_no_admitted_ebay_cell_schedules_nothing() -> None:
    # Current production truth: every eBay cell is NOT_ADMITTED.
    scheduler = _enabled_ebay_scheduler()
    assert scheduler.get_job("poll-ebay") is None
    assert scheduler.get_job("poll-heartbeat-ebay") is None
