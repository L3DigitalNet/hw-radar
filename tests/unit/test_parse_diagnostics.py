"""Per-adapter reconciliation of the `SourceAdapter.last_parse_skipped` contract (MS-1e B3).

Each case feeds one adapter a real-shape batch mixing valid and malformed raw
records and asserts the full accounting: every raw record either becomes a
ParsedListing or is counted as skipped, never neither and never both. That
reconciliation is what makes `harvest_corpus`'s `skipped_malformed` trustworthy —
without it an adapter could silently drop half a page and still look healthy.

These are pure parse() tests: no network, no database, no credentials. They live
here rather than beside the connector suites in `tests/db/`, which are gated on a
live TimescaleDB for their persistence assertions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from hw_radar.acquisition.contracts import RawBatch, RawItem
from hw_radar.acquisition.sources.demo import DemoAdapter
from hw_radar.acquisition.sources.ebay import EbayAdapter
from hw_radar.acquisition.sources.goharddrive import GoHardDriveAdapter
from hw_radar.acquisition.sources.seagate import SeagateAdapter
from hw_radar.acquisition.sources.serverpartdeals import ServerPartDealsAdapter
from hw_radar.acquisition.sources.wd import WdAdapter


def _batch(source: str, items: list[RawItem]) -> RawBatch:
    return RawBatch(source=source, fetched_at=datetime.now(UTC), items=items)


def _json_item(url: str, payload: dict[str, Any]) -> RawItem:
    return RawItem(url=url, payload_json=payload)


def test_serverpartdeals_reconciles_valid_and_malformed_records() -> None:
    # Shopify /products.json shape, with four drops at four distinct sites: two bad
    # variants (missing price, non-numeric price) inside an otherwise valid product,
    # a non-dict product, and a product with no handle. The handle-less product
    # counts ONCE even though it nests a well-formed variant — a product-level drop
    # is one discarded record, because parse() never descends into it.
    payload = {
        "products": [
            {
                "handle": "seagate-exos-x18",
                "title": "Seagate Exos X18 18TB",
                "variants": [
                    {"id": 1, "price": "199.00", "sku": "ST18000NM000J", "available": True},
                    {"id": 2, "sku": "no-price"},
                    {"id": 3, "price": "call for quote"},
                ],
            },
            "not-a-product",
            {"title": "handle missing", "variants": [{"id": 9, "price": "10.00"}]},
        ]
    }
    adapter = ServerPartDealsAdapter()

    parsed = adapter.parse(
        _batch("serverpartdeals", [_json_item("https://spd.invalid/p", payload)])
    )

    assert [p.source_listing_key for p in parsed] == ["seagate-exos-x18:1"]
    assert adapter.last_parse_skipped == 4


def test_wd_reconciles_valid_and_malformed_variants() -> None:
    # OCC two-step shape: the search RawItem carries no variantOptions at all and
    # must count as neither harvested nor skipped — it is not a product record.
    search = _json_item("https://wd.invalid/search", {"products": [{"code": "WDBBGB0040HBK"}]})
    product = _json_item(
        "https://wd.invalid/products/WDBBGB0040HBK",
        {
            "code": "WDBBGB0040HBK",
            "name": "WD My Book 4TB Recertified",
            "variantOptions": [
                {
                    "code": "RWDBBGB0040HBK-NESN",
                    "priceData": {"value": 79.99},
                    "stock": {"stockLevelStatus": "inStock"},
                    "saleable": True,
                },
                {"priceData": {"value": 99.99}},
                {"code": "RWDBBGB0080HBK-NESN"},
                {"code": "RWDBBGB0120HBK-NESN", "priceData": {"value": "on request"}},
            ],
        },
    )
    adapter = WdAdapter()

    parsed = adapter.parse(_batch("wd-recertified", [search, product]))

    assert [p.source_listing_key for p in parsed] == ["RWDBBGB0040HBK-NESN"]
    assert adapter.last_parse_skipped == 3


def test_ebay_reconciles_valid_and_malformed_summaries() -> None:
    payload = {
        "itemSummaries": [
            {
                "itemId": "v1|123|0",
                "title": "Seagate Exos X18 18TB Recertified",
                "itemWebUrl": "https://www.ebay.com/itm/123",
                "price": {"value": "189.99", "currency": "USD"},
            },
            {"title": "no itemId"},
            {"itemId": "v1|124|0", "title": "no price block"},
            {"itemId": "v1|125|0", "price": {"currency": "USD"}},
        ]
    }
    adapter = EbayAdapter()

    parsed = adapter.parse(_batch("ebay", [_json_item("https://api.ebay.invalid/search", payload)]))

    assert [p.source_listing_key for p in parsed] == ["v1|123|0"]
    assert adapter.last_parse_skipped == 3


def test_seagate_reconciles_valid_and_malformed_bootstrap_entries() -> None:
    bootstrap = {
        "ST18000NM000J": {"final_price": "219.00", "stock_status": "IN_STOCK"},
        "ST16000NM000J": {"stock_status": "IN_STOCK"},
        "ST14000NM001G": "not-a-dict",
        # Un-Decimal-able final_price is a bad record, not a page break: it must
        # skip without aborting the sibling SKUs (InvalidOperation regression).
        "ST12000NM0007": {"final_price": "TBD", "stock_status": "IN_STOCK"},
    }
    html = f'<html><script id="sku-bootstrap-data">{json.dumps(bootstrap)}</script></html>'
    adapter = SeagateAdapter()

    parsed = adapter.parse(
        _batch(
            "seagate-recertified",
            [RawItem(url="https://www.seagate.invalid/recert", payload_text=html)],
        )
    )

    assert [p.source_listing_key for p in parsed] == ["ST18000NM000J"]
    assert adapter.last_parse_skipped == 3


def test_goharddrive_reconciles_priceless_product_blocks() -> None:
    # The spider emits one RawItem per Volusion product block; a block whose price
    # markup drifted arrives with empty price_text and is the adapter's only drop.
    items = [
        _json_item(
            "https://www.goharddrive.com/exos-x18-p/st18000nm000j.htm",
            {"title": "Seagate Exos X18 18TB", "price_text": "$199.00"},
        ),
        _json_item(
            "https://www.goharddrive.com/exos-x16-p/st16000nm001g.htm",
            {"title": "Seagate Exos X16 16TB", "price_text": ""},
        ),
        _json_item(
            "https://www.goharddrive.com/exos-x14-p/st14000nm001g.htm",
            {"title": "Seagate Exos X14 14TB", "price_text": "Call for price"},
        ),
        _json_item(
            "https://www.goharddrive.com/exos-x12-p/st12000nm0007.htm",
            # Digit-stripping leaves "1.2.3" — non-empty but un-Decimal-able; must
            # skip this block, not abort the batch (InvalidOperation regression).
            {"title": "Seagate Exos X12 12TB", "price_text": "v1.2.3"},
        ),
    ]
    adapter = GoHardDriveAdapter()

    parsed = adapter.parse(_batch("goharddrive", items))

    assert [p.source_listing_key for p in parsed] == ["st18000nm000j"]
    assert adapter.last_parse_skipped == 3


def test_skip_count_is_reset_per_parse_call() -> None:
    # last_parse_skipped describes the MOST RECENT parse only; harvest_corpus reads
    # it straight after parse(), so a count that accumulated across calls would
    # inflate every source that is polled more than once in a process.
    bad = {"products": [{"title": "no handle"}]}
    good = {"products": [{"handle": "h", "title": "T", "variants": [{"id": 1, "price": "5.00"}]}]}
    adapter = ServerPartDealsAdapter()

    adapter.parse(_batch("serverpartdeals", [_json_item("https://spd.invalid/p", bad)]))
    assert adapter.last_parse_skipped == 1

    adapter.parse(_batch("serverpartdeals", [_json_item("https://spd.invalid/p", good)]))
    assert adapter.last_parse_skipped == 0


def test_demo_adapter_satisfies_the_diagnostic_contract() -> None:
    # The fixture source has no drop site (it raises on a bad blob), but it is in
    # the ADAPTERS registry and so must still expose a resettable counter.
    adapter = DemoAdapter()
    adapter.last_parse_skipped = 7

    parsed = adapter.parse(
        _batch(
            "demo",
            [
                RawItem(
                    url="file:///demo",
                    payload_text=json.dumps(
                        {
                            "sku": "DEMO-1",
                            "name": "Demo Drive",
                            "offers": {"price": "10.00", "availability": "InStock"},
                        }
                    ),
                )
            ],
        )
    )

    assert len(parsed) == 1
    assert adapter.last_parse_skipped == 0
