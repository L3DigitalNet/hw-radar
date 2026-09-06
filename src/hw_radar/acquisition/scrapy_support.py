# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# Scrapy/Twisted ship no py.typed; exceptions scoped to this integration module.
"""Scrapy on the poller's asyncio event loop (ADR-0012 single-process design).

install_asyncio_reactor() MUST run before anything imports
twisted.internet.reactor — the poller calls it first thing in run(); tests
get it via run_spider() itself. BASE_SETTINGS encodes the C-007 guardrails
(spec §8.5): robots on, autothrottle on, honest UA, hard timeouts.
"""

from __future__ import annotations

from dataclasses import dataclass

from scrapy import signals
from scrapy.crawler import AsyncCrawlerRunner
from scrapy.settings import Settings
from scrapy.utils.reactor import install_reactor, is_asyncio_reactor_installed

ASYNCIO_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
USER_AGENT = "hw-radar/0.1 (personal price monitor; +https://github.com/L3DigitalNet/hw-radar)"

BASE_SETTINGS: dict[str, object] = {
    "TWISTED_REACTOR": ASYNCIO_REACTOR,
    "ROBOTSTXT_OBEY": True,
    "AUTOTHROTTLE_ENABLED": True,
    "AUTOTHROTTLE_START_DELAY": 1.0,
    "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
    "DOWNLOAD_TIMEOUT": 30,
    "RETRY_ENABLED": True,
    "RETRY_TIMES": 1,  # AW-001: one in-run retry
    "USER_AGENT": USER_AGENT,
    "TELNETCONSOLE_ENABLED": False,
    "LOG_ENABLED": False,
}


def install_asyncio_reactor() -> None:
    # Scrapy >=2.13: is_asyncio_reactor_installed() RAISES RuntimeError when no
    # reactor is installed yet (it no longer silently installs the default) —
    # that no-reactor case is the normal first call in a fresh process.
    try:
        already_asyncio = is_asyncio_reactor_installed()
    except RuntimeError:
        install_reactor(ASYNCIO_REACTOR)
        return
    if not already_asyncio:
        # A wrong (non-asyncio) reactor cannot be switched; fail loudly rather
        # than let crawls hang against a foreign reactor.
        raise RuntimeError("a non-asyncio Twisted reactor is already installed")


@dataclass(frozen=True)
class SpiderResult:
    """Items a spider scraped plus the Scrapy StatsCollector snapshot for the run."""

    items: list[dict[str, object]]
    stats: dict[str, object]


async def run_spider(
    spider_cls: type,
    *,
    settings_override: dict[str, object] | None = None,
    **spider_kwargs: object,
) -> SpiderResult:
    """Run one spider on the current loop; return its scraped items and run stats.

    AsyncCrawlerRunner is the asyncio-native primitive the Scrapy docs prescribe
    (design §MS-1a / Codex SA-006). Documented fallback ONLY if the pinned Scrapy
    lacks it: CrawlerRunner + `runner.crawl(...).asFuture(asyncio.get_running_loop())`.

    settings_override layers over BASE_SETTINGS at run scope, so production
    callers pass nothing (C-007 guardrails intact, robots obeyed) while a test
    driving a file:// fixture — which cannot serve /robots.txt — passes
    {"ROBOTSTXT_OBEY": False}. This keeps that exception at the call site rather
    than baking a permanent ROBOTSTXT_OBEY=False into any production spider.
    """
    install_asyncio_reactor()
    settings = Settings()
    settings.setdict(BASE_SETTINGS, priority="project")
    if settings_override:
        settings.setdict(settings_override, priority="cmdline")
    items: list[dict[str, object]] = []

    def collect(item: dict[str, object], response: object, spider: object) -> None:
        items.append(dict(item))

    runner = AsyncCrawlerRunner(settings)
    crawler = runner.create_crawler(spider_cls)
    crawler.signals.connect(collect, signal=signals.item_scraped)
    await runner.crawl(crawler, **spider_kwargs)
    # Scrapy >= 2.18 types Crawler.stats as always present, since it is
    # assigned in the constructor before crawl() can run. The former
    # None-guard here was defensive against an older Optional annotation.
    stats = crawler.stats.get_stats()
    return SpiderResult(items=items, stats=stats)
