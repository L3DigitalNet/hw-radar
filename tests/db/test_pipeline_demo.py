import asyncio
import json
from collections.abc import Iterator

import pytest

from hw_radar.acquisition.contracts import NullResolver
from hw_radar.acquisition.pipeline import run_source
from hw_radar.acquisition.sources import ADAPTERS
from hw_radar.acquisition.sources.demo import DemoAdapter
from hw_radar.catalog.models import Listing, OfferSnapshot, RunStatus, ScraperRun

# See test_pipeline.py: serialized_rollback preserves the migration seed.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


@pytest.fixture(scope="module")
def scrapy_loop() -> Iterator[asyncio.AbstractEventLoop]:
    # SINGLE loop for every Scrapy-touching test in this module (design §MS-1a
    # reactor-lifecycle rule): the asyncio reactor binds to the loop present at
    # install; fresh per-test asyncio.run() loops would race a stale reactor.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    asyncio.set_event_loop(None)
    loop.close()


def test_demo_adapter_is_registered() -> None:
    assert ADAPTERS["demo"] is DemoAdapter


def test_walking_skeleton_end_to_end(scrapy_loop: asyncio.AbstractEventLoop) -> None:
    # The MS-1a exit criterion: Scrapy (asyncio reactor, shared loop) → parse →
    # FX-stamp → resolve(stub) → persist, all under one event loop, no network.
    run, _outcome = scrapy_loop.run_until_complete(run_source(DemoAdapter(), NullResolver()))
    assert run.status == RunStatus.SUCCESS
    assert run.records_valid == 2
    listings = Listing.objects.filter(source_site__normalized_name="demo")
    assert listings.count() == 2
    assert {listing.source_listing_key for listing in listings} == {"DEMO-16TB-R", "DEMO-8TB-R"}
    snap = OfferSnapshot.objects.filter(listing__in=listings).first()
    assert snap is not None
    assert snap.fx_pair == "USD/USD"  # FR-004 identity stamp present

    stats: dict[str, object] = run.detail_json["scrapy_stats"]  # pyright: ignore[reportAssignmentType]
    assert stats["downloader/response_status_count/200"] == 1
    assert stats["item_scraped_count"] == 2
    json.dumps(stats)  # detail_json is a JSONField; this is the real contract


def test_two_consecutive_crawls_one_process_one_loop(
    scrapy_loop: asyncio.AbstractEventLoop,
) -> None:
    # Codex SA-006 requirement: the second scheduled crawl is where a wrong
    # reactor/runner integration breaks — prove both crawls inside ONE coroutine
    # on the module loop, and that re-runs append (DR-005).
    async def two_runs() -> None:
        await run_source(DemoAdapter(), NullResolver())
        await run_source(DemoAdapter(), NullResolver())

    scrapy_loop.run_until_complete(two_runs())
    assert Listing.objects.filter(source_site__normalized_name="demo").count() == 2
    assert OfferSnapshot.objects.count() == 4
    assert ScraperRun.objects.filter(status=RunStatus.SUCCESS).count() == 2
