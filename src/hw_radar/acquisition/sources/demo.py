# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownParameterType=false, reportIncompatibleVariableOverride=false
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
# Scrapy ships no py.typed; exceptions scoped to this module: Spider's base
# __init__/custom_settings/Response types are untyped, so overrides and
# **kwargs forwarding cannot be verified against them.
"""Walking-skeleton source: a Scrapy spider over a local file:// JSON-LD fixture.

Proves poller → Scrapy(asyncio reactor) → parse → FX → resolve → persist with
zero network. ROBOTSTXT_OBEY=False here is the single sanctioned exception
(file:// cannot serve robots.txt); production spiders MUST NOT copy it (C-007).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import scrapy

from hw_radar.acquisition.contracts import ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.scrapy_support import run_spider
from hw_radar.catalog.models import RunKind

FIXTURE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "demo_listings.html"


class DemoSpider(scrapy.Spider):
    name = "demo"
    custom_settings: ClassVar[dict[str, object]] = {"ROBOTSTXT_OBEY": False}  # file:// only

    def __init__(self, fixture_url: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.start_urls = [fixture_url]

    def parse(self, response: scrapy.http.Response) -> Iterator[dict[str, str]]:
        for blob in response.css('script[type="application/ld+json"]::text').getall():
            yield {"jsonld": blob, "url": response.url}


class DemoAdapter:
    name = "demo"
    site_key = "demo"
    run_kind = RunKind.FULL
    expects_json = False
    # The fixture is checked into the repo, so parse() below raises on a bad blob
    # instead of dropping it — a broken fixture is a test bug, not source drift.
    # The attribute exists (and stays 0) only to satisfy the SourceAdapter protocol.
    last_parse_skipped = 0

    def __init__(self, fixture: Path = FIXTURE) -> None:
        self._fixture = fixture

    async def fetch(self) -> RawBatch:
        scraped = await run_spider(DemoSpider, fixture_url=self._fixture.as_uri())
        items = [
            RawItem(
                url=str(entry["url"]),
                content_type="text/html",
                payload_text=str(entry["jsonld"]),
            )
            for entry in scraped
        ]
        return RawBatch(source=self.name, fetched_at=datetime.now(tz=UTC), items=items)

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        self.last_parse_skipped = 0
        parsed: list[ParsedListing] = []
        for item in batch.items:
            data = json.loads(item.payload_text or "{}")
            offer = data["offers"]
            parsed.append(
                ParsedListing(
                    source_listing_key=str(data["sku"]),
                    url=item.url,
                    title=str(data["name"]),
                    price=Decimal(str(offer["price"])),
                    currency=str(offer.get("priceCurrency", "USD")),
                    stock_status=(
                        "in_stock" if "InStock" in str(offer.get("availability", "")) else "unknown"
                    ),
                    raw_url=item.url,
                )
            )
        return parsed
