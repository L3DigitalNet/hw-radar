import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest

from hw_radar.acquisition.admission import RETIRED_REASON, RetiredSourceError
from hw_radar.acquisition.contracts import NullResolver, RawBatch, RawItem
from hw_radar.acquisition.pipeline import run_source
from hw_radar.acquisition.sources.serverpartdeals import ServerPartDealsAdapter
from hw_radar.catalog.models import Listing, ScraperRun, StockStatus

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

SYNTHETIC = {  # synthetic Shopify products.json (OQ8: not captured live)
    "products": [
        {
            "title": "Seagate Exos X20 20TB Recertified",
            "handle": "exos-x20-20tb-recert",
            "variants": [
                {
                    "id": 111,
                    "sku": "ST20000NM002D-RECERT",
                    "price": "279.99",
                    "available": True,
                    "title": "Default",
                }
            ],
        },
        {
            "title": "WD Ultrastar DC HC560 20TB Recertified",
            "handle": "hc560-20tb-recert",
            "variants": [
                {
                    "id": 222,
                    "sku": "WUH722020BLE-RECERT",
                    "price": "289.99",
                    "available": False,
                    "title": "Default",
                }
            ],
        },
    ]
}


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
            return httpx.Response(200, text="User-agent: *\nDisallow: /admin\n")
        return httpx.Response(200, json=SYNTHETIC)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


# OQ31: the adapter is retained only for its offline parser. Every network entry
# point must refuse before a request, whoever calls it.


def test_fetch_refuses_retired_source_without_a_request(
    loop: asyncio.AbstractEventLoop,
) -> None:
    client, seen = _recording_client()
    with pytest.raises(RetiredSourceError, match="serverpartdeals is retired / permission"):
        loop.run_until_complete(ServerPartDealsAdapter(client=client).fetch())
    assert seen == []


def test_probe_refuses_retired_source_without_a_request(
    loop: asyncio.AbstractEventLoop,
) -> None:
    client, seen = _recording_client()
    with pytest.raises(RetiredSourceError, match=re.escape(RETIRED_REASON)):
        loop.run_until_complete(ServerPartDealsAdapter(client=client).probe())
    assert seen == []


def test_run_source_refuses_before_a_run_row_or_request(
    loop: asyncio.AbstractEventLoop,
) -> None:
    client, seen = _recording_client()
    runs_before = ScraperRun.objects.count()
    with pytest.raises(RetiredSourceError):
        loop.run_until_complete(run_source(ServerPartDealsAdapter(client=client), NullResolver()))
    assert ScraperRun.objects.count() == runs_before
    assert not Listing.objects.filter(source_site__normalized_name="serverpartdeals").exists()
    assert seen == []


def test_parse_reads_the_synthetic_products_payload() -> None:
    # The happy-path parse the retired live-run test used to cover end to end.
    batch = RawBatch(
        source="serverpartdeals",
        fetched_at=datetime.now(UTC),
        items=[
            RawItem(
                url="https://serverpartdeals.com/x/products.json",
                payload_json=cast("dict[str, object]", SYNTHETIC),
            )
        ],
    )
    parsed = {p.source_listing_key: p for p in ServerPartDealsAdapter().parse(batch)}
    assert set(parsed) == {"exos-x20-20tb-recert:111", "hc560-20tb-recert:222"}
    assert parsed["exos-x20-20tb-recert:111"].stock_status == StockStatus.IN_STOCK
    assert parsed["hc560-20tb-recert:222"].stock_status == StockStatus.OUT_OF_STOCK


def test_parse_skips_malformed_products_and_variants() -> None:
    # Defensive isinstance-narrowing branches (a top-level `products` that
    # isn't a list, a non-dict product, a product missing/mistyped
    # `variants`, or a non-dict variant) must degrade to "skip that entry",
    # not raise. Also covers required fields missing on an otherwise
    # well-formed product/variant (PR #12 review: KeyError-on-malformed-body
    # must degrade to "skip this entry", not crash the whole run).
    batch = RawBatch(
        source="serverpartdeals",
        fetched_at=datetime.now(UTC),
        items=[
            RawItem(
                url="https://serverpartdeals.com/collections/malformed/products.json",
                payload_json={"products": "oops-not-a-list"},
            ),
            RawItem(
                url="https://serverpartdeals.com/collections/x/products.json",
                payload_json={
                    "products": [
                        "not-a-product",
                        {"title": "No variants list", "handle": "no-variants", "variants": "oops"},
                        {"handle": "no-title", "variants": []},  # missing required `title`
                        {"title": "No handle", "variants": []},  # missing required `handle`
                        {
                            "title": "Good",
                            "handle": "exos-x20-20tb-recert",
                            "variants": [
                                "not-a-variant",
                                {"sku": "NO-ID", "price": "1.00"},  # missing required `id`
                                {"id": 333, "sku": "NO-PRICE"},  # missing required `price`
                                SYNTHETIC["products"][0]["variants"][0],
                            ],
                        },
                    ]
                },
            ),
        ],
    )
    adapter = ServerPartDealsAdapter()
    parsed = adapter.parse(batch)
    assert [p.source_listing_key for p in parsed] == ["exos-x20-20tb-recert:111"]
