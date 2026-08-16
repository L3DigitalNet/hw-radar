"""ServerPartDeals connector: Shopify /products.json (T2, churning, heartbeat-not-fast-lane).

httpx JSON connector (not Scrapy) — routes through acquisition.http.get so the
C-007 robots guardrail applies uniformly across scrape and API-style sources.
Config seeded by migration 0011: heartbeat_enabled=True, fast_lane=False (SPD
is `churning`, not `drop_prone`, so the source_config_fast_lane_eligible CHECK
would reject fast_lane=True here).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

import httpx

from hw_radar.acquisition import http
from hw_radar.acquisition.contracts import ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.heartbeat import HeartbeatReading
from hw_radar.catalog.models import RunKind

COLLECTION_URL = (
    "https://serverpartdeals.com/collections/manufacturer-recertified-drives/products.json"
)


class ServerPartDealsAdapter:
    name = "serverpartdeals"
    site_key = "serverpartdeals"
    run_kind = RunKind.FULL
    expects_json = True
    last_parse_skipped = 0  # SourceAdapter parse diagnostic; see parse()

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # Inject-or-own-and-close: tests inject a MockTransport client (not
        # closed by us); production leaves this None and gets a fresh client
        # per fetch, closed on the way out.
        self._client = client

    async def fetch(self) -> RawBatch:
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            resp = await http.get(COLLECTION_URL, client=client, params={"limit": "250"})
            item = RawItem(
                url=str(resp.url),
                http_status=resp.status_code,
                content_type=resp.headers.get("content-type", "application/json"),
                payload_json=resp.json() if resp.status_code == 200 else None,
                payload_text=resp.text,
            )
            return RawBatch(source=self.name, fetched_at=datetime.now(UTC), items=[item])
        finally:
            if owns:
                await client.aclose()

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        # payload_json is dict[str, object] (untyped external JSON); isinstance
        # narrows each nesting level explicitly rather than casting to Any, so
        # a malformed Shopify response degrades to "0 records" (PARSER_ROT via
        # run_source's empty-parse guard) instead of raising deep in a .get().
        # The `cast`s below only retarget list[Unknown]/dict[Unknown, Unknown]
        # (bare-isinstance narrowing) to their declared element types; the
        # isinstance checks are the actual runtime safety net.
        #
        # Every `continue` below is a malformed-record drop and increments
        # last_parse_skipped (SourceAdapter contract): a product-level drop counts
        # once for the product, a variant-level drop once per variant, so the
        # count is in units of "records that could have become a listing".
        self.last_parse_skipped = 0
        out: list[ParsedListing] = []
        for item in batch.items:
            data = item.payload_json or {}
            raw_products = data.get("products", [])
            if not isinstance(raw_products, list):
                self.last_parse_skipped += 1
                continue
            products = cast("list[object]", raw_products)
            for raw_product in products:
                if not isinstance(raw_product, dict):
                    self.last_parse_skipped += 1
                    continue
                product = cast("dict[str, object]", raw_product)
                # handle/title are required to build a listing; a Shopify entry
                # missing either degrades to "skip this product", not a crash.
                handle = product.get("handle")
                title = product.get("title")
                if not isinstance(handle, str) or not isinstance(title, str):
                    self.last_parse_skipped += 1
                    continue
                raw_variants = product.get("variants", [])
                if not isinstance(raw_variants, list):
                    self.last_parse_skipped += 1
                    continue
                variants = cast("list[object]", raw_variants)
                for raw_variant in variants:
                    if not isinstance(raw_variant, dict):
                        self.last_parse_skipped += 1
                        continue
                    variant = cast("dict[str, object]", raw_variant)
                    variant_id = variant.get("id")
                    raw_price = variant.get("price")
                    if variant_id is None or raw_price is None:
                        self.last_parse_skipped += 1
                        continue
                    try:
                        price = Decimal(str(raw_price))
                    except InvalidOperation:
                        self.last_parse_skipped += 1
                        continue
                    out.append(
                        ParsedListing(
                            source_listing_key=f"{handle}:{variant_id}",
                            url=f"https://serverpartdeals.com/products/{handle}",
                            title=title,
                            price=price,
                            stock_status=(
                                "in_stock" if variant.get("available") else "out_of_stock"
                            ),
                            raw_url=item.url,  # per-item raw-payload association (Task B4)
                            attrs={
                                "sku": variant.get("sku", ""),
                                "variant_title": variant.get("title", ""),
                            },
                        )
                    )
        return out

    async def probe(self) -> list[HeartbeatReading]:
        batch = await self.fetch()
        return [
            HeartbeatReading(
                source_sku=p.source_listing_key,
                price=p.price,
                currency=p.currency,
                stock_status=p.stock_status,
                shipping_price=p.shipping_price,
                http_status=200,
                latency_ms=None,
                endpoint=COLLECTION_URL,
            )
            for p in self.parse(batch)
        ]
