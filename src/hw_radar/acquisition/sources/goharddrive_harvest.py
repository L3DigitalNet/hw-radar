# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownParameterType=false, reportIncompatibleVariableOverride=false
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportUnknownArgumentType=false
# Scrapy ships no py.typed; the same scoped exceptions as goharddrive.py, kept in
# this module so harvest_corpus itself stays fully strict.
"""Harvest-only goHardDrive collection: several categories, paginated, page-capped.

SCOPE: used only by `manage.py harvest_corpus --ghd-url/--ghd-max-pages` to widen
the MS-1e validation corpus. Nothing here is registered in
acquisition.sources.ADAPTERS or HARVEST_ADAPTERS, so the poller and the default
harvest keep fetching exactly the first page of goharddrive.CATEGORY_URL.

Parsing is the production connector's own (GoHardDriveSpider.parse for product
blocks, GoHardDriveAdapter.parse for listings), so staged rows have the shape the
scheduled source would ingest. Robots stay obeyed through run_spider's
BASE_SETTINGS.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

import scrapy

from hw_radar.acquisition.contracts import RawBatch, RawItem
from hw_radar.acquisition.scrapy_support import run_spider
from hw_radar.acquisition.sources.goharddrive import GoHardDriveAdapter, GoHardDriveSpider

# Hard ceiling on goHardDrive page fetches per harvest (robots.txt excluded).
MAX_GHD_PAGES = 8
_GHD_HOST = "www.goharddrive.com"
# Volusion embeds the listing's refine state in `var SearchParams = '...page=1'`
# and prints the page count beside the "Go to page" box; its "Next Page" button
# is JavaScript (productlist.js Refine(): location.pathname + "?" + SearchParams),
# so there is no <a href> to follow and the next URL is rebuilt the same way.
_GHD_SEARCH_PARAMS_RE = re.compile(r"var SearchParams = '([^']*)'")
_GHD_PAGE_COUNT_RE = re.compile(r'title="Go to page"\s*/>\s*of\s+(\d+)')


def validate_ghd_url(url: str) -> str:
    """Accept only an https goHardDrive Volusion category URL (`...-s/<n>.htm`)."""
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or (parts.hostname or "").lower() != _GHD_HOST
        or re.search(r"-s/\d+\.htm$", parts.path) is None
        or parts.query
    ):
        raise ValueError(f"not a goHardDrive category URL: {url!r}")
    return url


class PagingGoHardDriveSpider(GoHardDriveSpider):
    """GoHardDriveSpider over several categories, following each one's pagination.

    Page 1 of each category schedules its remaining pages while the shared page
    budget lasts; later pages schedule nothing, so a category is paged at most
    once. The budget counts every request this spider issues (start pages
    included), so `max_pages` is a hard ceiling on page fetches.
    """

    name = "goharddrive-harvest"
    # Crawl-delay: 2 from goHardDrive's robots.txt: Scrapy's robots middleware
    # only filters disallowed paths and never applies Crawl-delay, so it is set
    # here, one request at a time. Retries are off because a retried page is
    # another fetch, and `max_pages` promises a ceiling on fetches.
    custom_settings = {  # noqa: RUF012 — Scrapy reads this class attribute
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "RETRY_ENABLED": False,
    }

    def __init__(self, start_urls: list[str], max_pages: int, **kwargs: object) -> None:
        super().__init__(start_url=start_urls[0], **kwargs)
        self.start_urls = list(start_urls)
        self._budget = max_pages - len(start_urls)
        self._pages: list[str] = []

    def parse(self, response: scrapy.http.Response) -> Iterator[Any]:
        self._pages.append(response.url)
        self.crawler.stats.set_value("harvest/pages", list(self._pages))
        yield from super().parse(response)
        text = response.text
        state = _GHD_SEARCH_PARAMS_RE.search(text)
        count = _GHD_PAGE_COUNT_RE.search(text)
        if state is None or count is None or "page=1" not in state.group(1).split("&"):
            return
        path = urlsplit(response.url).path
        for page in range(2, int(count.group(1)) + 1):
            if self._budget <= 0:
                return
            self._budget -= 1
            query = re.sub(r"(^|&)page=\d+", rf"\g<1>page={page}", state.group(1))
            yield scrapy.Request(response.urljoin(f"{path}?{query}"), callback=self.parse)


class GoHardDrivePagingAdapter(GoHardDriveAdapter):
    """Harvest-only goHardDrive adapter: several categories, paginated, page-capped.

    `pages_fetched` lists the page URLs the last fetch() actually received.
    """

    def __init__(self, urls: Sequence[str], max_pages: int) -> None:
        super().__init__()
        self._urls = list(urls)
        self._max_pages = max_pages
        self.pages_fetched: list[str] = []

    async def fetch(self) -> RawBatch:
        result = await run_spider(
            PagingGoHardDriveSpider, start_urls=self._urls, max_pages=self._max_pages
        )
        self.pages_fetched = cast("list[str]", result.stats.get("harvest/pages", []))
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
            fetched_at=datetime.now(UTC),
            items=items,
            scrapy_stats=result.stats,
        )

    def harvest_report(self) -> dict[str, Any]:
        # harvest_corpus.HarvestReporting: merged into the manifest's source entry.
        return {"pages_fetched": self.pages_fetched}
