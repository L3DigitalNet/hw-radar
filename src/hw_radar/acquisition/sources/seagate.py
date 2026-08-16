"""Seagate Recertified connector: category-page bootstrap JSON (T1, drop_prone, fast-lane).

httpx connector (not Scrapy) — routes through acquisition.http.get so the C-007
robots guardrail applies uniformly. The category page is a normal HTML
document (content-type text/html) that embeds a per-SKU JSON blob for
client-side rendering; `expects_json=False` here (unlike ServerPartDeals/WD's
JSON APIs) so the pipeline's anti_bot "JSON endpoint answered text/html" check
does not misclassify a healthy HTML fetch as a block.

store.seagate.com is robots `Disallow: /` and must NEVER be fetched — this
adapter only ever talks to www.seagate.com; the B1 guard (acquisition.http)
enforces the block for any code path that tries the storefront host anyway.

Config seeded by migration 0011: heartbeat_enabled=True, fast_lane=True.
Seagate's 0005 seed is volatility_profile="drop_prone",
cheap_signal="bootstrap_json", which satisfies the
source_config_fast_lane_eligible CHECK (same shape as WD).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

import httpx

from hw_radar.acquisition import http
from hw_radar.acquisition.contracts import ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.heartbeat import HeartbeatReading
from hw_radar.catalog.models import RunKind

CATEGORY_URL = "https://www.seagate.com/products/seagate-recertified/exos-recertified/"

# robots.txt on www.seagate.com carries `Crawl-delay: 20` (recon 2026-07-06).
# SourceConfig.cadence_ceiling_s=300 (migration 0005 seed) already sits well
# above this floor; this constant exists purely to document + pin the
# obligation so a future cadence change can't silently violate it.
MIN_INTERVAL_S = 20

# The category page ships one `<script id="sku-bootstrap-data">` tag holding a
# JSON object keyed by SKU (ST...NM... codes) → {final_price, stock_status}.
# Bounded, non-greedy match: exactly the payload between this one script tag's
# boundaries, no HTML parser dependency needed for a fixture we control.
_BOOTSTRAP_PATTERN = re.compile(
    r'<script[^>]*id="sku-bootstrap-data"[^>]*>(?P<payload>.*?)</script>',
    re.DOTALL,
)


def _extract_bootstrap(html: str) -> dict[str, object]:
    # Missing/malformed blob degrades to an empty dict (⇒ 0 parsed listings ⇒
    # run_source's "authentic fetch yielded 0 records" PARSER_ROT guard),
    # rather than raising deep inside json.loads.
    match = _BOOTSTRAP_PATTERN.search(html)
    if match is None:
        return {}
    try:
        data = json.loads(match.group("payload"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return cast("dict[str, object]", data)


def _stock_status(raw_status: object) -> str:
    if raw_status == "IN_STOCK":
        return "in_stock"
    if isinstance(raw_status, str) and raw_status:
        return "out_of_stock"
    return "unknown"  # missing/blank stock_status ⇒ heartbeat.AMBIGUOUS, not a guess


class SeagateAdapter:
    name = "seagate-recertified"
    site_key = "seagate-recertified"  # == migration-0005 normalized_name
    run_kind = RunKind.FULL
    expects_json = False  # HTML page carrying embedded JSON, not a JSON endpoint
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
            resp = await http.get(CATEGORY_URL, client=client)
            item = RawItem(
                url=str(resp.url),
                http_status=resp.status_code,
                content_type=resp.headers.get("content-type", "text/html"),
                payload_json=None,
                payload_text=resp.text,
            )
            return RawBatch(source=self.name, fetched_at=datetime.now(UTC), items=[item])
        finally:
            if owns:
                await client.aclose()

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        # One ParsedListing per SKU key in the bootstrap blob. isinstance/cast
        # narrows the untyped embedded JSON so a malformed entry (non-dict, or
        # missing final_price) degrades to "skip that SKU" rather than raising.
        #
        # Both `continue`s below are malformed-record drops and increment
        # last_parse_skipped (SourceAdapter contract), one count per bootstrap SKU
        # entry. A missing or unparseable bootstrap blob yields no entries at all
        # and so is NOT counted here — run_source's empty-parse PARSER_ROT guard
        # owns that failure, which is a whole-page break rather than a bad record.
        self.last_parse_skipped = 0
        out: list[ParsedListing] = []
        for item in batch.items:
            bootstrap = _extract_bootstrap(item.payload_text or "")
            for sku, raw_entry in bootstrap.items():
                if not isinstance(raw_entry, dict):
                    self.last_parse_skipped += 1
                    continue
                entry = cast("dict[str, object]", raw_entry)
                price = entry.get("final_price")
                if price is None:
                    self.last_parse_skipped += 1
                    continue
                try:
                    entry_price = Decimal(str(price))
                except InvalidOperation:
                    # A junk final_price is a bad record, not a page break: skip
                    # it so sibling SKUs in the bootstrap blob still parse.
                    self.last_parse_skipped += 1
                    continue
                out.append(
                    ParsedListing(
                        source_listing_key=sku,
                        url=CATEGORY_URL,
                        title=f"Seagate {sku} Recertified",
                        price=entry_price,
                        stock_status=_stock_status(entry.get("stock_status")),
                        raw_url=item.url,  # per-item raw-payload association (Task B4)
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
                endpoint=CATEGORY_URL,
            )
            for p in self.parse(batch)
        ]
