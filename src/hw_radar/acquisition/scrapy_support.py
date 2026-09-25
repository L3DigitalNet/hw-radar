# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# Scrapy/Twisted ship no py.typed; exceptions scoped to this integration module.
"""Scrapy on the poller's asyncio event loop (ADR-0012 single-process design).

install_asyncio_reactor() MUST run before anything imports
twisted.internet.reactor — the poller calls it first thing in run(); tests
get it via run_spider() itself. BASE_SETTINGS encodes the C-007 guardrails
(spec §8.5): robots on, autothrottle on, honest UA, hard timeouts.

The reactor is never *run* here (no reactor.run()/react()): the asyncio loop
owned by asyncio.run() or the poller drives AsyncioSelectorReactor directly.
Twisted's startup triggers therefore never fire, so install_asyncio_reactor()
also starts the reactor thread pool that startup would have started — see
_start_reactor_threadpool for why that matters and how exit stays clean.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import partial

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
        already_asyncio = True
    if not already_asyncio:
        # A wrong (non-asyncio) reactor cannot be switched; fail loudly rather
        # than let crawls hang against a foreign reactor.
        raise RuntimeError("a non-asyncio Twisted reactor is already installed")
    _start_reactor_threadpool()


def _start_reactor_threadpool() -> None:
    """Start the reactor's thread pool with daemon workers; idempotent.

    Safe to call on every run_spider(): a started pool is left untouched.
    """
    # Without this, every real network crawl times out. Twisted's
    # ReactorBase._initThreadPool creates the pool but defers
    # `threadpool.start` to `callWhenRunning`, i.e. to the reactor's "startup"
    # system event — which only reactor.run()/startRunning() fire. On a loop we
    # drive ourselves that never happens, so work queued with callInThread sits
    # in the backlog forever. Name resolution is such work (the reactor's
    # default _GAIResolver runs getaddrinfo on this pool), so every request to a
    # hostname hangs until DOWNLOAD_TIMEOUT (DownloadTimeoutError, robots.txt
    # included). file:// fixtures need no DNS, which is why only live crawls
    # broke. Sources: Twisted 26.4.0 src/twisted/internet/base.py
    # (_initThreadPool, _initThreads); Scrapy 2.18 docs "Run Scrapy from a
    # script" (https://docs.scrapy.org/en/2.18/topics/practices.html), whose
    # reactor examples all run the reactor via react()/CrawlerProcess.
    #
    # Daemon workers are load-bearing: Twisted's default threadFactory makes
    # non-daemon threads, which it joins in its own "shutdown" event — an event
    # that never fires here either, so one idle worker would block interpreter
    # exit forever (management commands such as harvest_corpus would never
    # return). An idle daemon worker blocked on its queue is safe to abandon.
    #
    # Rejected alternatives: calling reactor.startRunning() inside the running
    # loop also starts the pool, but still leaves non-daemon workers that hang
    # exit, and marks the reactor started so it can never be restarted.
    # TWISTED_REACTOR_ENABLED=False (Scrapy's documented asyncio.run() path)
    # is flagged experimental and not production-ready in Scrapy 2.18
    # (https://docs.scrapy.org/en/2.18/topics/asyncio.html), swaps the HTTP
    # handler for httpx, and forbids the reactor the poller installs.
    from twisted.internet import reactor

    pool = reactor.getThreadPool()
    if pool.started:
        return
    # Must precede start(): workers are created by start() and on demand, and
    # a worker built from the default factory would be non-daemon.
    pool.threadFactory = partial(threading.Thread, daemon=True)
    pool.start()


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
