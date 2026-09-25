# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownParameterType=false, reportIncompatibleVariableOverride=false
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportUnknownArgumentType=false
# Scrapy ships no py.typed; exceptions scoped to this module: Spider's base
# __init__/Response types are untyped, so overrides and **kwargs forwarding
# cannot be verified against them.
"""goHardDrive connector: Scrapy spider over a Volusion category page (T2, FULL).

No cheap heartbeat signal (migration-0005 cheap_signal="none"): the config row
stays enabled=False, heartbeat_enabled=False, fast_lane=False, so this adapter
exposes NO probe() — the pipeline only ever runs the full parse at T2 cadence.

Volusion renders the visible price in `.product_productprice` / `.pricecolor`
CSS classes, NOT as a JSON-LD offer or a bare `$\\d+.\\d+` literal (recon
2026-07-06), so parse() reads that class text and strips non-numeric characters
before Decimal. The production spider obeys robots (Crawl-delay: 2 via
BASE_SETTINGS); only the file:// test path disables robots, and it does so
through run_spider(settings_override=...) rather than a spider-level override.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import scrapy

from hw_radar.acquisition.contracts import ParsedListing, RawBatch, RawItem
from hw_radar.acquisition.scrapy_support import run_spider
from hw_radar.catalog.models import RunKind

# Production entry point: the Volusion "Desktop Hard Drive (3.5")" category.
# The poller (or a test) overrides this via GoHardDriveAdapter(start_url=...).
# Re-verified 2026-09-25: the earlier /hard-drives-s/1.htm now redirects to
# category 1, "External Enclosure", which silently yielded no drive listings.
CATEGORY_URL = "https://www.goharddrive.com/3-5-inch-Desktop-SATA-IDE-SCSI-SAS-Hard-Drive-s/3.htm"

# Volusion product links carry the SKU as `-p/<sku>.htm`; used both to follow
# products and to derive the stable source_listing_key.
_SKU_RE = re.compile(r"-p/([^/]+)\.htm")


def _first_text(nodes: list[str]) -> str:
    """Return the first non-blank text node, stripped, or "" when there is none."""
    return next((node.strip() for node in nodes if node.strip()), "")


class GoHardDriveSpider(scrapy.Spider):
    name = "goharddrive"

    def __init__(self, start_url: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.start_urls = [start_url]

    def parse(self, response: scrapy.http.Response) -> Iterator[dict[str, str]]:
        for block in response.css("div.v-product"):
            href = block.css('a[href*="-p/"]::attr(href)').get()
            if not href:
                continue
            # Volusion moved the name into <span itemprop="name"> inside the
            # title anchor (seen 2026-09-25); the anchor's own text is then
            # whitespace. Fall back to the older direct-text layout (the frozen
            # MS-1d fixture) and finally to the anchor's title attribute.
            title = (
                _first_text(block.css('[itemprop="name"]::text').getall())
                or _first_text(block.css('a[href*="-p/"]::text').getall())
                or block.css("a.v-product__title::attr(title)").get()
                or ""
            )
            # Join every text node under the price block: Volusion nests the
            # amount inside .pricecolor spans, so a single ::text would miss it.
            price_text = "".join(block.css(".product_productprice ::text").getall())
            yield {
                "url": response.urljoin(href),
                "title": title.strip(),
                "price_text": price_text.strip(),
            }


class GoHardDriveAdapter:
    name = "goharddrive"
    site_key = "goharddrive"  # == migration-0005 normalized_name
    run_kind = RunKind.FULL
    expects_json = False
    last_parse_skipped = 0  # SourceAdapter parse diagnostic; see parse()

    def __init__(self, start_url: str = CATEGORY_URL, *, obey_robots: bool = True) -> None:
        # obey_robots defaults True (C-007). Tests point start_url at a file://
        # fixture — which cannot serve /robots.txt — and pass obey_robots=False.
        self._start_url = start_url
        self._obey_robots = obey_robots

    async def fetch(self) -> RawBatch:
        override = None if self._obey_robots else {"ROBOTSTXT_OBEY": False}
        result = await run_spider(
            GoHardDriveSpider,
            settings_override=override,
            start_url=self._start_url,
        )
        items = [
            RawItem(
                url=str(entry["url"]),
                content_type="text/html",
                payload_json={"title": entry["title"], "price_text": entry["price_text"]},
            )
            for entry in result.items
        ]
        return RawBatch(
            source=self.name,
            fetched_at=datetime.now(tz=UTC),
            items=items,
            scrapy_stats=result.stats,
        )

    def parse(self, batch: RawBatch) -> list[ParsedListing]:
        self.last_parse_skipped = 0
        parsed: list[ParsedListing] = []
        for item in batch.items:
            data = item.payload_json or {}
            cleaned = re.sub(r"[^0-9.]", "", str(data.get("price_text", "")))
            if not cleaned:
                # A product block with no readable price degrades to "skipped"
                # (Volusion markup drift) rather than raising on Decimal("") —
                # counted for the SourceAdapter diagnostic.
                self.last_parse_skipped += 1
                continue
            try:
                price = Decimal(cleaned)
            except InvalidOperation:
                # Digit-stripped text can still be un-Decimal-able ("...", "1.2.3");
                # one bad block must not abort the sibling listings in the batch.
                self.last_parse_skipped += 1
                continue
            match = _SKU_RE.search(item.url)
            sku = match.group(1) if match else item.url
            parsed.append(
                ParsedListing(
                    source_listing_key=sku,
                    url=item.url,
                    title=str(data.get("title", "")),
                    price=price,
                    currency="USD",
                    stock_status="unknown",
                    raw_url=item.url,
                )
            )
        return parsed
