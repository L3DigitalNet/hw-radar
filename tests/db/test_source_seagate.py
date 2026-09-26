import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest

from hw_radar.acquisition import http
from hw_radar.acquisition.admission import RETIRED_REASON, RetiredSourceError
from hw_radar.acquisition.contracts import NullResolver, RawBatch, RawItem
from hw_radar.acquisition.pipeline import run_source
from hw_radar.acquisition.sources.seagate import CATEGORY_URL, MIN_INTERVAL_S, SeagateAdapter
from hw_radar.catalog.models import Listing, ScraperRun, StockStatus

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

# Synthetic Seagate category page (OQ8: not captured live). Recon 2026-07-06:
# the page embeds a single <script id="sku-bootstrap-data"> JSON blob keyed by
# SKU (ST...NM... codes) → {final_price, stock_status}. SKU B is deliberately
# non-IN_STOCK to pin the stock-status mapping.
SYNTHETIC_HTML = """
<html>
<body>
<div id="product-grid">Exos Recertified</div>
<script id="sku-bootstrap-data" type="application/json">
{"ST16000NM002C": {"final_price": 349.99, "stock_status": "IN_STOCK"},
 "ST18000NM004C": {"final_price": 419.99, "stock_status": "BACKORDER"}}
</script>
</body>
</html>
"""


@pytest.fixture(scope="module")
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    lo = asyncio.new_event_loop()
    asyncio.set_event_loop(lo)
    yield lo
    asyncio.set_event_loop(None)
    lo.close()


def _recording_client() -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    # Answers every request as the real site would, so a missing guard shows up
    # as a recorded request (and a successful run) rather than a transport error
    # that could be mistaken for the refusal.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 20\n")
        return httpx.Response(200, text=SYNTHETIC_HTML, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


# OQ31: the adapter is retained only for its offline parser. Every network entry
# point must refuse before a request, whoever calls it.


def test_fetch_refuses_retired_source_without_a_request(
    loop: asyncio.AbstractEventLoop,
) -> None:
    client, seen = _recording_client()
    with pytest.raises(RetiredSourceError, match="seagate-recertified is retired / permission"):
        loop.run_until_complete(SeagateAdapter(client=client).fetch())
    assert seen == []


def test_probe_refuses_retired_source_without_a_request(
    loop: asyncio.AbstractEventLoop,
) -> None:
    client, seen = _recording_client()
    with pytest.raises(RetiredSourceError, match=re.escape(RETIRED_REASON)):
        loop.run_until_complete(SeagateAdapter(client=client).probe())
    assert seen == []


def test_run_source_refuses_before_a_run_row_or_request(
    loop: asyncio.AbstractEventLoop,
) -> None:
    client, seen = _recording_client()
    runs_before = ScraperRun.objects.count()
    with pytest.raises(RetiredSourceError):
        loop.run_until_complete(run_source(SeagateAdapter(client=client), NullResolver()))
    assert ScraperRun.objects.count() == runs_before
    assert not Listing.objects.filter(source_site__normalized_name="seagate-recertified").exists()
    assert seen == []


def test_parse_reads_the_synthetic_category_page() -> None:
    # The happy-path parse the retired live-run test used to cover end to end,
    # including the non-IN_STOCK mapping SKU B pins.
    batch = RawBatch(
        source="seagate-recertified",
        fetched_at=datetime.now(UTC),
        items=[RawItem(url=CATEGORY_URL, content_type="text/html", payload_text=SYNTHETIC_HTML)],
    )
    parsed = {p.source_listing_key: p for p in SeagateAdapter().parse(batch)}
    assert set(parsed) == {"ST16000NM002C", "ST18000NM004C"}
    assert parsed["ST16000NM002C"].stock_status == StockStatus.IN_STOCK
    assert parsed["ST18000NM004C"].stock_status == StockStatus.OUT_OF_STOCK
    assert all(p.url == CATEGORY_URL for p in parsed.values())


def test_min_interval_s_documents_crawl_delay_floor() -> None:
    # robots.txt Crawl-delay: 20 on www.seagate.com — this constant pins that
    # obligation so a future cadence change can't silently violate it.
    assert MIN_INTERVAL_S == 20


def test_parse_skips_malformed_bootstrap_entries() -> None:
    # Defensive isinstance-narrowing: a missing bootstrap script tag, invalid
    # JSON syntax, a non-dict bootstrap payload, a non-dict SKU entry, and an
    # entry missing final_price must all degrade to "skip", not raise.
    batch = RawBatch(
        source="seagate-recertified",
        fetched_at=datetime.now(UTC),
        items=[
            RawItem(
                url="https://www.seagate.com/products/seagate-recertified/no-script/",
                content_type="text/html",
                payload_text="<html><body>no bootstrap here</body></html>",
            ),
            RawItem(
                url="https://www.seagate.com/products/seagate-recertified/invalid-json/",
                content_type="text/html",
                payload_text=(
                    '<script id="sku-bootstrap-data" type="application/json">'
                    "{not valid json,,,}"
                    "</script>"
                ),
            ),
            RawItem(
                url="https://www.seagate.com/products/seagate-recertified/bad-shape/",
                content_type="text/html",
                payload_text=(
                    '<script id="sku-bootstrap-data" type="application/json">[1, 2, 3]</script>'
                ),
            ),
            RawItem(
                url="https://www.seagate.com/products/seagate-recertified/mixed/",
                content_type="text/html",
                payload_text=(
                    '<script id="sku-bootstrap-data" type="application/json">'
                    '{"NOT-A-DICT": "oops", '
                    '"ST99999NM000A": {"stock_status": "IN_STOCK"}, '
                    '"ST16000NM002C": {"final_price": 349.99, "stock_status": "IN_STOCK"}}'
                    "</script>"
                ),
            ),
        ],
    )
    parsed = SeagateAdapter().parse(batch)
    assert [p.source_listing_key for p in parsed] == ["ST16000NM002C"]


def test_stock_status_missing_key_maps_to_unknown() -> None:
    # An entry that has final_price but no stock_status key at all (not even
    # a non-IN_STOCK string) must map to "unknown", not "out_of_stock" — the
    # heartbeat decision engine treats "unknown" as AMBIGUOUS, a materially
    # different signal from a confirmed out-of-stock reading.
    batch = RawBatch(
        source="seagate-recertified",
        fetched_at=datetime.now(UTC),
        items=[
            RawItem(
                url="https://www.seagate.com/products/seagate-recertified/no-status/",
                content_type="text/html",
                payload_text=(
                    '<script id="sku-bootstrap-data" type="application/json">'
                    '{"ST16000NM002C": {"final_price": 349.99}}'
                    "</script>"
                ),
            ),
        ],
    )
    parsed = SeagateAdapter().parse(batch)
    assert [p.stock_status for p in parsed] == ["unknown"]


def test_store_seagate_com_is_never_fetched(loop: asyncio.AbstractEventLoop) -> None:
    # B1 guard: store.seagate.com is robots Disallow: / — the Seagate adapter
    # only ever fetches www.seagate.com, but this pins that acquisition.http.get
    # itself blocks the storefront host if any code path ever tried it.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(http.RobotsDisallowed):
        loop.run_until_complete(
            http.get("https://store.seagate.com/products/recertified", client=client)
        )
