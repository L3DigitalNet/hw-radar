"""WD Recertified connector: two-step SAP-Commerce (OCC) JSON (T1, drop_prone, fast-lane).

httpx JSON connector (not Scrapy) — routes through acquisition.http.get so the
C-007 robots guardrail applies uniformly. api.westerndigital.com serves NO
robots.txt (404 ⇒ unrestricted per RFC 9309), so the B1 guard returns allowed.

Config seeded by migration 0011: heartbeat_enabled=True, fast_lane=True. WD's
0005 seed is volatility_profile="drop_prone", cheap_signal="occ_json", which
satisfies the source_config_fast_lane_eligible CHECK (unlike ServerPartDeals,
which is `churning` and cannot be fast-laned).

Fetch is TWO steps against the OCC API:
  1. search sweep → base product `code`s;
  2. one per-product GET per code → its `variantOptions`.
Each HTTP response becomes its OWN RawItem (Task B4 per-item raw persistence),
so every variant's raw_url points at the product response it was parsed from,
and _persist_all's by_url association stores distinct provenance per product.

Three search sweeps, not one: the OCC catalog has no recert facet for
enterprise SKUs, so `query=recertified` (consumer My Book / Elements / My
Passport) is joined by two `category:<code>` sweeps — `cat_data_center_drives`
(Ultrastar DC HCxxx + WD Gold) and `cat_nas_hdd` (WD Red) — live-verified
against api.westerndigital.com on 2026-08-16. Combining a free-text query with
a category selector (`query=recertified:relevance:category:...`) returns
empty, so the sweeps must stay separate round-trips. Both category sweeps
return their non-recert siblings too, since recert-ness is only encoded in the
product code suffix `-recertified` (and the display name) — never in a
category/facet value — so every sweep's codes are merged, deduped, and then
filtered to the `-recertified` suffix before the per-product detail loop.
WD's taxonomy is undocumented and unversioned: these category codes can drift
without notice and have no stability guarantee from WD. WD Purple recert
exists in the catalog but is deliberately excluded (owner decision covers only
Gold/Red/Ultrastar).
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

API_BASE = "https://api.westerndigital.com"
SEARCH_URL = f"{API_BASE}/wdwebservices/v2/us/products/search"
# OCC `fields` projections keep the payload to the code/price/stock we consume.
_SEARCH_COMMON = {
    "fields": "products(code)",
    "lang": "en",
    "curr": "USD",
}
# Three independent sweeps (see module docstring): consumer free-text query
# plus one category selector per enterprise line. Order matters only for
# fetch-count determinism in tests, not for correctness (codes are deduped).
SEARCH_PARAMS_LIST = [
    {**_SEARCH_COMMON, "query": "recertified"},
    {**_SEARCH_COMMON, "query": ":relevance:category:cat_data_center_drives"},
    {**_SEARCH_COMMON, "query": ":relevance:category:cat_nas_hdd"},
]
PRODUCT_PARAMS = {
    "fields": "code,name,variantOptions(code,priceData(FULL),stock(FULL))",
    "lang": "en",
    "curr": "USD",
}
# OCC stockLevelStatus values that signal availability; a variant still needs
# saleable=true on top of this (recon: variants can be inStock yet not saleable).
_IN_STOCK_STATUSES = {"instock", "lowstock"}


def _product_url(code: str) -> str:
    return f"{API_BASE}/wdwebservices/v2/us/products/{code}"


def _codes_from_search(payload: dict[str, object] | None) -> list[str]:
    # isinstance narrows the untyped OCC search JSON; a malformed body yields
    # zero codes (→ run_source's empty-parse PARSER_ROT guard) rather than
    # raising deep in an iteration.
    data = payload or {}
    raw_products = data.get("products", [])
    if not isinstance(raw_products, list):
        return []
    codes: list[str] = []
    for entry in cast("list[object]", raw_products):
        if not isinstance(entry, dict):
            continue
        code = cast("dict[str, object]", entry).get("code")
        if isinstance(code, str):
            codes.append(code)
    return codes


class WdAdapter:
    name = "wd-recertified"
    site_key = "wd-recertified"  # == migration-0005 normalized_name
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
            items: list[RawItem] = []
            # dict-as-ordered-set: preserves first-seen order across the three
            # sweeps while deduping codes that multiple sweeps surface (e.g. a
            # Gold drive could in principle appear in more than one category).
            seen_codes: dict[str, None] = {}
            for search_params in SEARCH_PARAMS_LIST:
                search = await http.get(SEARCH_URL, client=client, params=search_params)
                items.append(
                    RawItem(
                        url=str(search.url),
                        http_status=search.status_code,
                        content_type=search.headers.get("content-type", "application/json"),
                        payload_json=search.json() if search.status_code == 200 else None,
                        payload_text=search.text,
                    )
                )
                for code in _codes_from_search(items[-1].payload_json):
                    seen_codes[code] = None
            # Category sweeps return non-recert siblings alongside recert SKUs
            # (see module docstring) — only the `-recertified` suffix marks a
            # product as actually recertified, so it gates every sweep's
            # output uniformly before the expensive per-product fetch.
            for code in (c for c in seen_codes if c.endswith("-recertified")):
                resp = await http.get(_product_url(code), client=client, params=PRODUCT_PARAMS)
                items.append(
                    RawItem(
                        url=str(resp.url),
                        http_status=resp.status_code,
                        content_type=resp.headers.get("content-type", "application/json"),
                        payload_json=resp.json() if resp.status_code == 200 else None,
                        payload_text=resp.text,
                    )
                )
            return RawBatch(source=self.name, fetched_at=datetime.now(UTC), items=items)
        finally:
            if owns:
                await client.aclose()

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        # One ParsedListing per variantOptions[] entry. isinstance/cast narrows
        # each nesting level of the untyped OCC JSON (see _codes_from_search):
        # the search RawItem has no variantOptions and yields nothing, so
        # iterating every item naturally emits only per-product variants, each
        # carrying its own product response url as raw_url.
        #
        # Every `continue` below is a malformed-record drop and increments
        # last_parse_skipped (SourceAdapter contract). The search RawItem carries
        # no "variantOptions" key at all, so it takes the `[]` default and is never
        # counted — only a product response whose variantOptions is present but not
        # a list is a real drop.
        self.last_parse_skipped = 0
        out: list[ParsedListing] = []
        for item in batch.items:
            data = item.payload_json or {}
            raw_variants = data.get("variantOptions", [])
            if not isinstance(raw_variants, list):
                self.last_parse_skipped += 1
                continue
            title = str(data.get("name", ""))
            for raw_variant in cast("list[object]", raw_variants):
                if not isinstance(raw_variant, dict):
                    self.last_parse_skipped += 1
                    continue
                variant = cast("dict[str, object]", raw_variant)
                code = variant.get("code")
                if code is None:
                    self.last_parse_skipped += 1
                    continue  # no code ⇒ can't key the listing; skip this variant
                raw_price = variant.get("priceData")
                if not isinstance(raw_price, dict):
                    self.last_parse_skipped += 1
                    continue  # no price ⇒ not a sellable variant; skip
                value = cast("dict[str, object]", raw_price).get("value")
                if value is None:
                    self.last_parse_skipped += 1
                    continue
                try:
                    price = Decimal(str(value))
                except InvalidOperation:
                    self.last_parse_skipped += 1
                    continue
                out.append(
                    ParsedListing(
                        source_listing_key=str(code),
                        url=_listing_url(variant, item.url),
                        title=title,
                        price=price,
                        stock_status=_stock_status(variant),
                        raw_url=item.url,  # per-item raw-payload association (Task B4)
                        attrs={"saleable": bool(variant.get("saleable", False))},
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
                # the reading's data came from this variant's product response,
                # not the search sweep — D1 writes this as the observation endpoint.
                endpoint=p.raw_url,
            )
            for p in self.parse(batch)
        ]


def _stock_status(variant: dict[str, object]) -> str:
    # A variant that is saleable=false is OUT_OF_STOCK even when
    # stockLevelStatus says inStock (recon 2026-07-06); only saleable=true AND
    # an in-stock status level is IN_STOCK.
    if not bool(variant.get("saleable", False)):
        return "out_of_stock"
    raw_stock = variant.get("stock")
    status = ""
    if isinstance(raw_stock, dict):
        status = str(cast("dict[str, object]", raw_stock).get("stockLevelStatus", ""))
    return "in_stock" if status.lower() in _IN_STOCK_STATUSES else "out_of_stock"


def _listing_url(variant: dict[str, object], product_url: str) -> str:
    # OCC variants carry no public PDP url in this projection; fall back to the
    # product API response url so ParsedListing.url is always populated.
    url = variant.get("url")
    return str(url) if isinstance(url, str) and url else product_url
