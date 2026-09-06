import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest

from hw_radar.acquisition.contracts import NullResolver, RawBatch, RawItem
from hw_radar.acquisition.pipeline import run_source
from hw_radar.acquisition.sources.wd import WdAdapter
from hw_radar.catalog.models import Listing, OfferSnapshot, RunStatus, StockStatus

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

# Synthetic WD OCC (SAP Commerce) bodies (OQ8: not captured live). The three
# search sweeps (consumer query + two enterprise category selectors) each
# return base product codes; each per-product response carries the
# variantOptions with the saleable/stockLevelStatus fingerprint. Product B's
# sole variant is saleable=false WHILE stockLevelStatus=inStock — the recon
# nuance that must map to OUT_OF_STOCK, not IN_STOCK.
#
# `wd-gold-sata-hdd-recertified` is returned by BOTH the consumer query sweep
# (contrived, but exercises cross-sweep dedup) and the data-center category
# sweep, pinning that a code seen twice still yields exactly one product
# fetch and one listing. The category sweep also returns a non-recert
# sibling (`wd-gold-sata-hdd`) that must be filtered out before the
# per-product loop, per the module docstring's suffix rule.
SEARCH_CONSUMER = {
    "products": [{"code": "WDBBGB0040HBK-recertified"}, {"code": "wd-gold-sata-hdd-recertified"}]
}
SEARCH_DATA_CENTER = {
    "products": [
        {"code": "wd-gold-sata-hdd-recertified"},
        {"code": "wd-gold-sata-hdd"},  # non-recert sibling; must be filtered out
    ]
}
SEARCH_NAS = {"products": [{"code": "wd-red-sata-hdd-recertified"}]}

PRODUCTS = {
    "WDBBGB0040HBK-recertified": {
        "code": "WDBBGB0040HBK-recertified",
        "name": "WD My Book 4TB Recertified",
        "variantOptions": [
            {
                "code": "RWDBBGB0040HBK-NESN",
                "priceData": {"value": 79.99, "currency": "USD"},
                "stock": {"stockLevelStatus": "inStock"},
                "saleable": True,
            }
        ],
    },
    "wd-gold-sata-hdd-recertified": {
        "code": "wd-gold-sata-hdd-recertified",
        "name": "WD Gold 8TB Recertified",
        "variantOptions": [
            {
                "code": "WD-GOLD-8TB-RECERT",
                "priceData": {"value": 129.99, "currency": "USD"},
                "stock": {"stockLevelStatus": "inStock"},
                "saleable": False,  # saleable=false while inStock ⇒ OUT_OF_STOCK
            }
        ],
    },
    "wd-red-sata-hdd-recertified": {
        "code": "wd-red-sata-hdd-recertified",
        "name": "WD Red 4TB Recertified",
        "variantOptions": [
            {
                "code": "WD-RED-4TB-RECERT",
                "priceData": {"value": 99.99, "currency": "USD"},
                "stock": {"stockLevelStatus": "inStock"},
                "saleable": True,
            }
        ],
    },
}


@pytest.fixture(scope="module")
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    lo = asyncio.new_event_loop()
    asyncio.set_event_loop(lo)
    yield lo
    asyncio.set_event_loop(None)
    lo.close()


def _mock() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)  # no robots.txt ⇒ unrestricted (B1 guard allows)
        if path.endswith("/products/search"):
            query = request.url.params.get("query", "")
            if "cat_data_center_drives" in query:
                return httpx.Response(200, json=SEARCH_DATA_CENTER)
            if "cat_nas_hdd" in query:
                return httpx.Response(200, json=SEARCH_NAS)
            return httpx.Response(200, json=SEARCH_CONSUMER)
        code = path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=PRODUCTS[code])

    return httpx.MockTransport(handler)


def test_wd_persists_variants_across_two_product_responses(
    loop: asyncio.AbstractEventLoop,
) -> None:
    adapter = WdAdapter(client=httpx.AsyncClient(transport=_mock()))
    run, _ = loop.run_until_complete(run_source(adapter, NullResolver()))
    assert run.status == RunStatus.SUCCESS
    # 3 recert codes survive merge/dedup/suffix-filter across the three
    # sweeps: wd-gold-sata-hdd-recertified is deduped (seen by both the
    # consumer and data-center sweeps) and wd-gold-sata-hdd (no suffix) is
    # dropped, so only one product fetch happens per surviving code.
    assert run.records_valid == 3
    # 3 search responses + 3 per-product responses each land as a RawItem.
    assert run.records_fetched == 6
    listings = Listing.objects.filter(source_site__normalized_name="wd-recertified")
    assert listings.count() == 3

    in_stock = OfferSnapshot.objects.get(listing__source_listing_key="RWDBBGB0040HBK-NESN")
    assert in_stock.stock_status == StockStatus.IN_STOCK
    oos = OfferSnapshot.objects.get(listing__source_listing_key="WD-GOLD-8TB-RECERT")
    assert oos.stock_status == StockStatus.OUT_OF_STOCK  # saleable=false wins over inStock
    red = OfferSnapshot.objects.get(listing__source_listing_key="WD-RED-4TB-RECERT")
    assert red.stock_status == StockStatus.IN_STOCK

    # B4 per-item raw association: each variant's snapshot points at the raw
    # payload of ITS OWN product response, not a shared/first one.
    assert in_stock.raw_payload is not None
    assert oos.raw_payload is not None
    assert in_stock.raw_payload.pk != oos.raw_payload.pk
    assert "WDBBGB0040HBK-recertified" in in_stock.raw_payload.endpoint
    assert "wd-gold-sata-hdd-recertified" in oos.raw_payload.endpoint


def test_fetch_merges_dedupes_and_filters_sweeps(loop: asyncio.AbstractEventLoop) -> None:
    # Isolates the sweep-merge/dedupe/suffix-filter behavior from persistence:
    # 3 search RawItems always land regardless of overlap, and the product
    # fetch count pins that wd-gold-sata-hdd-recertified (seen by two sweeps)
    # is fetched once, while wd-gold-sata-hdd (no suffix) is never fetched.
    fetched_product_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path.endswith("/products/search"):
            query = request.url.params.get("query", "")
            if "cat_data_center_drives" in query:
                return httpx.Response(200, json=SEARCH_DATA_CENTER)
            if "cat_nas_hdd" in query:
                return httpx.Response(200, json=SEARCH_NAS)
            return httpx.Response(200, json=SEARCH_CONSUMER)
        code = path.rsplit("/", 1)[-1]
        fetched_product_paths.append(code)
        return httpx.Response(200, json=PRODUCTS[code])

    adapter = WdAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    batch = loop.run_until_complete(adapter.fetch())

    search_items = [i for i in batch.items if "/products/search" in i.url]
    assert len(search_items) == 3  # all three sweeps run even though codes overlap

    assert sorted(fetched_product_paths) == [
        "WDBBGB0040HBK-recertified",
        "wd-gold-sata-hdd-recertified",  # fetched once despite appearing in 2 sweeps
        "wd-red-sata-hdd-recertified",
    ]
    assert "wd-gold-sata-hdd" not in fetched_product_paths  # non-recert sibling filtered out


def test_probe_returns_saleable_stock_fingerprint(loop: asyncio.AbstractEventLoop) -> None:
    # HeartbeatProbe contract (migration 0011 flips heartbeat_enabled=True): one
    # cheap reading per variant, no DB writes, saleable∧stockLevelStatus mapped.
    adapter = WdAdapter(client=httpx.AsyncClient(transport=_mock()))
    readings = loop.run_until_complete(adapter.probe())
    assert {r.source_sku for r in readings} == {
        "RWDBBGB0040HBK-NESN",
        "WD-GOLD-8TB-RECERT",
        "WD-RED-4TB-RECERT",
    }
    oos = next(r for r in readings if r.source_sku == "WD-GOLD-8TB-RECERT")
    assert oos.stock_status == StockStatus.OUT_OF_STOCK


def test_parse_skips_malformed_variants() -> None:
    # Defensive isinstance-narrowing: a non-list variantOptions, a non-dict
    # variant, and a variant missing priceData must degrade to "skip", not
    # raise. Also covers a variant missing the required `code` (PR #12 review:
    # KeyError-on-malformed-body must degrade to "skip this variant", not
    # crash the whole run).
    batch = RawBatch(
        source="wd-recertified",
        fetched_at=datetime.now(UTC),
        items=[
            RawItem(
                url="https://api.westerndigital.com/wdwebservices/v2/us/products/A",
                payload_json={"name": "bad variants", "variantOptions": "oops"},
            ),
            RawItem(
                url="https://api.westerndigital.com/wdwebservices/v2/us/products/B",
                payload_json={
                    "name": "WD My Book 4TB Recertified",
                    "variantOptions": [
                        "not-a-dict",
                        {"code": "NO-PRICE"},  # missing priceData
                        {"priceData": {"value": 9.99, "currency": "USD"}},  # missing `code`
                        PRODUCTS["WDBBGB0040HBK-recertified"]["variantOptions"][0],
                    ],
                },
            ),
        ],
    )
    parsed = WdAdapter().parse(batch)
    assert [p.source_listing_key for p in parsed] == ["RWDBBGB0040HBK-NESN"]
