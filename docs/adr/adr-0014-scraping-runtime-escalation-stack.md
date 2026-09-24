---
schema_version: '1.1'
id: 'adr-0014-hw-radar-scraping-runtime-escalation-stack'
title: 'ADR 0014: Scraping runtime — HTTP-first, structured-data-first, browser-last escalation stack'
description: 'Adopt a four-tier acquisition stack — Scrapy orchestrator with a structured-data detector in front of every parser, curl_cffi for the TLS-fingerprint gap, Playwright via scrapy-playwright for genuine JS rendering, managed unblocker or skip for the hostile tail — and defer the curl_cffi and Playwright tiers to MS-5, shipping the five MS-1 recert sources on plain HTTP + structured-data parsing.'
doc_type: 'adr'
status: 'active'
created: '2026-07-04'
updated: '2026-09-24'
reviewed: null
owner: ''
consumer: 'mix'
tags:
  - 'adr'
  - 'web-scraping'
  - 'scrapy'
  - 'playwright'
  - 'curl-cffi'
  - 'anti-bot'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/specs/hw-radar-master-spec.md'
  - 'docs/resolved-questions.md'
  - 'docs/research/pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md'
  - 'docs/research/per-source-polling-cadence-and-skip-policy.md'
supersedes: []
superseded_by: null
source: []
confidence: 'high'
visibility: 'public'
license: null
project:
  decision_makers:
    - 'chris'
  consulted: []
  informed: []
---

# ADR 0014: Scraping runtime — HTTP-first, structured-data-first, browser-last escalation stack

MADR status: **accepted**.

## Context and Problem Statement

The spec named only "**Scrapy. Additional options to be considered**" — under-specified for a set of ~20 merchants that range from plain HTML storefronts to JS-assembled and anti-bot-protected pages. What concrete tools handle each difficulty level, and how much of that stack must ship in v1? A specific owner question also needed answering: **would we actually use Playwright, and can it scrape headlessly without an AI agent/LLM?**

Research [`pragmatic-architecture-for-low-volume-python-e-commerce-scraping`](../research/pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md) supplied the difficulty→technique decision tree and the default stack (resolved-questions.md OQ14).

## Considered Options

- **Option 1 — Adopt the full four-tier stack now.**
- **Option 2 — Adopt the tiered stack as the architecture, but defer the browser/TLS tiers to MS-5**, shipping the MS-1 recert sources on plain HTTP + structured-data parsing. (chosen)
- **Option 3 — Reach for a browser (or a managed scraping API) as the default** for JS-looking sites.

## Decision Outcome

Chosen option: **Option 2.**

**The tier ladder (per source), HTTP-first and browser-last:**

1. **Scrapy** as the orchestrator (crawl policy, retry, throttle, robots, storage), with a **structured-data detector in front of every parser** — JSON-LD → first-party platform JSON → hidden bootstrap JSON (e.g. `__NEXT_DATA__`, Shopify `/products/{handle}.js`) → stable HTML selectors. Most target sources (recert stores, structured-data storefronts) expose the needed fields in the initial HTML, so this tier alone covers them.
2. **`curl_cffi`** — a narrow fallback when plain HTTP is blocked purely at the **TLS/HTTP fingerprint** layer and the underlying endpoint is still non-JS. Not a replacement for Scrapy; a targeted sidecar probe.
3. **Playwright via `scrapy-playwright`** — a real headless browser **only** for the minority of pages whose data is genuinely assembled client-side, and mainly for **one-time endpoint/JS reconnaissance**, after which steady-state collection moves back to the discovered JSON source with no browser.
4. **Managed unblocker API (tiny high-value tail) → SKIP** — for the small set of targets that need residential rotation / CAPTCHA-solving to work *routinely*, which is the declared hard-stop (use the API for a few URLs, or skip the source).

**Answering the owner's Playwright question directly:** Playwright is a **code-driven, headless browser-automation library — nothing to do with an AI agent or LLM** (that would be a separate "browser agent" category, which this project does not use). It runs deterministically on the server, driven entirely by our code, and via `scrapy-playwright` only requests explicitly marked as needing a browser go through Chromium; everything else stays on plain HTTP.

**Defer tiers 2–3 to MS-5.** MS-1's five recert sources (WD/Seagate Recertified, ServerPartDeals, goHardDrive, eBay Browse/Feed) are the "easy" tier — plain HTTP + structured-data parsing. `curl_cffi` and Playwright are added only when a specific source demands them, keeping the MS-1 surface small.

Option 1 was rejected (premature complexity — most sources never need a browser). Option 3 was rejected: assuming "JS framework ⇒ use a browser" is the key mistake the research warns against, and it multiplies compute and anti-bot surface for no benefit.

### Consequences

- **Good** — smallest, cheapest, most stable path per source; the browser is a scalpel, not the default engine.
- **Good** — dovetails with the spec's **Special Considerations guardrails** (`ROBOTSTXT_OBEY=True`, AUTOTHROTTLE, honor `429`/`Retry-After`, no anti-bot bypass) and shares the **same skip cutoff** as OQ9's skip policy — decided consistently.
- **Good** — MS-1 stays lean; the browser/TLS tiers arrive with MS-5 breadth, when a hostile source actually justifies them.
- **Bad (accepted)** — a source that later hardens its anti-bot posture may need escalation up the ladder; the OQ9 back-off/skip logic detects this (a soft-block that maxes the 24 h cooldown), so escalation is a measured event, not a surprise.

### Confirmation

Implementation confirmation: MS-1's five sources yield normalized listings on plain HTTP + structured-data parsing; MS-5 adds `curl_cffi`/Playwright only where the tier ladder measurably requires it.


## 2026-09-24 Amendment — technique ladder is independent of execution venue

[ADR 0021](adr-0021-hybrid-acquisition-apify.md) adds a hybrid local/Apify execution model. This ADR's **HTTP-first, structured-data-first, browser-last** rule remains accepted as the technique ladder for each source.

The ladder may now execute:

- locally in hw-radar for cheap official API / structured-data / HTTP paths; or
- inside a self-owned private Apify Actor when managed execution materially improves reliability or maintenance.

Moving a collector to Apify is **not** permission to skip directly to browser automation, residential proxies, CAPTCHA solving, or anti-bot bypass. Browser and proxy use remain explicit, source-specific escalation decisions.

Hardware Radar has a hard **$20/month Apify ceiling** ([ADR 0021](adr-0021-hybrid-acquisition-apify.md)); the initial operating target is $12/month. No collector may automatically escalate to paid residential proxies or a paid third-party Actor after failure. If a source cannot meet its required value/freshness inside the legal/operational guardrails and budget, reduce cadence/scope or skip it.


## More Information

- **Fills** the spec's under-specified "Web Scraping Libraries" line and names the tools per acquisition tier.
- **Shares** the skip cutoff with resolved-questions.md **OQ9** (same tier-ladder hard-stop) and the failure classification with **OQ8**.
- **Findings:** resolved-questions.md **OQ14**; research [`pragmatic-architecture`](../research/pragmatic-architecture-for-low-volume-python-e-commerce-scraping.md).
