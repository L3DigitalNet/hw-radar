# Bug Records

| ID | Title | Status |
| --- | --- | --- |
| [001](001-nginx-static-403.md) | nginx cannot serve `/static/` (403) in production | Fixed 2026-09-25 (`531916e`) |
| [002](002-scrapy-dns-threadpool.md) | Scrapy connectors time out on every real host (reactor thread pool never started) | Fixed 2026-09-25 (`b9e01a1`); deployed in `c2adae0` |
| [003](003-full-suite-checkout-moves-mid-run.md) | Full-suite battery in a moving checkout breaks `inspect.getsource` structural tests | Known; freeze the tree per session 7, 2026-09-26 |
| [004](004-ebay-credentials-not-rendered-in-production.md) | eBay credentials were never rendered in production | Fixed 2026-09-26 (s9, before admission) |
| [005](005-pilot-report-oom-killed-production-postgres.md) | `pilot_report` OOM-killed production PostgreSQL; the poller never reconnected | Fixed 2026-10-03 (s10); released in `1208526` |
