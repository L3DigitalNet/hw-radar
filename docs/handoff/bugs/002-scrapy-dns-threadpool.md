# Bug 002: Scrapy connectors time out on every real host

Status: fixed on `dev` 2026-09-25 (`b9e01a1`); not yet deployed. No production impact: every
source is disabled, so no Scrapy connector has ever run in production.
Found: 2026-09-25, during the MS-1e live harvest (goHardDrive harvested 0 listings).
Severity: high for the goHardDrive source, the only Scrapy connector with a real host. It could
never have collected in production, and its tests could not show it.

## Symptom

`GoHardDriveAdapter().fetch()` returned 0 items after ~120 s. Scrapy stats showed
`DownloadTimeoutError` on robots.txt and the category page. `curl`, `httpx`, and a standalone
`CrawlerProcess` spider fetched the same URLs in under a second with the same User-Agent.

## Cause

`run_spider` (`acquisition/scrapy_support.py`) drives Scrapy's `AsyncCrawlerRunner` from an asyncio
loop, both `asyncio.run` in management commands and the poller's loop. The Twisted asyncio reactor
is installed but never `reactor.run()`. Twisted creates its thread pool but starts it only on
reactor startup, which therefore never happens. `AsyncCrawlerRunner` does not install Scrapy's
threaded resolver, so Twisted's default resolver runs `getaddrinfo` on that never-started pool.
Every hostname lookup waits until `DOWNLOAD_TIMEOUT`. The test suite drives spiders with `file://`
fixtures, which need no DNS.

## Fix

`install_asyncio_reactor()` now starts the reactor thread pool once, with daemon worker threads, so
a management command can still exit. Calling `reactor.startRunning()` inside the running loop was
rejected: the process then cannot exit and the reactor cannot restart. A regression test in
`tests/unit/test_scrapy_support.py` runs `run_spider` twice in a fresh process against a local HTTP
server reached as `localhost`, so resolution is exercised. Mutation-checked: without the pool start
the crawls return nothing, and without daemon threads the process hangs.

## Lesson

A connector test that never resolves a hostname cannot prove the connector works. Each network
collector needs at least one test that goes through name resolution, even if only to `localhost`.
The same harvest also found goHardDrive's category URL and title selector had drifted (`2c8bce6`):
a disabled source decays silently, so re-verify a connector live before enabling it.
