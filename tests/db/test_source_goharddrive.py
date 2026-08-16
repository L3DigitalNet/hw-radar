import asyncio
import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from hw_radar.acquisition.contracts import NullResolver
from hw_radar.acquisition.pipeline import run_source
from hw_radar.acquisition.sources import ADAPTERS
from hw_radar.acquisition.sources.goharddrive import GoHardDriveAdapter
from hw_radar.catalog.models import Listing, OfferSnapshot, RunStatus

# See test_pipeline.py: serialized_rollback preserves the migration-0005 seed
# (the `goharddrive` SourceSite run_source looks up by normalized_name).
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ms1d" / "goharddrive_category.html"


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


def test_goharddrive_adapter_is_registered() -> None:
    assert ADAPTERS["goharddrive"] is GoHardDriveAdapter


def test_goharddrive_scrapes_two_listings(scrapy_loop: asyncio.AbstractEventLoop) -> None:
    # file:// fixture has no /robots.txt, so this adapter runs with obey_robots
    # False FOR THE TEST ONLY; the production default keeps ROBOTSTXT_OBEY=True.
    adapter = GoHardDriveAdapter(start_url=FIXTURE.as_uri(), obey_robots=False)
    run, _outcome = scrapy_loop.run_until_complete(run_source(adapter, NullResolver()))

    assert run.status == RunStatus.SUCCESS
    assert run.records_valid == 2
    listings = Listing.objects.filter(source_site__normalized_name="goharddrive")
    assert listings.count() == 2

    prices: dict[str, Decimal] = {}
    for listing in listings:
        snap = OfferSnapshot.objects.filter(listing=listing).first()
        assert snap is not None
        prices[listing.source_listing_key] = snap.item_price
    assert prices == {"g01": Decimal("149.99"), "g02": Decimal("89.50")}

    stats: dict[str, object] = run.detail_json["scrapy_stats"]  # pyright: ignore[reportAssignmentType]
    assert stats["downloader/response_status_count/200"] == 1
    assert stats["item_scraped_count"] == 2
    # json.dumps is the actual downstream consumer (detail_json is a JSONField);
    # this is the round-trip guarantee that matters, not isinstance checks.
    json.dumps(stats)


def test_goharddrive_scrapy_stats_on_raw_batch(scrapy_loop: asyncio.AbstractEventLoop) -> None:
    adapter = GoHardDriveAdapter(start_url=FIXTURE.as_uri(), obey_robots=False)
    batch = scrapy_loop.run_until_complete(adapter.fetch())
    assert batch.scrapy_stats is not None
    assert batch.scrapy_stats["item_scraped_count"] == 2
