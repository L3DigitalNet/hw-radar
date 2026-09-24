---
schema_version: '1.1'
id: 'adr-0021-hw-radar-hybrid-acquisition-apify'
title: 'ADR 0021: Hybrid acquisition — local collectors + self-owned Apify Actors'
description: 'Use a hybrid acquisition model: retain inexpensive direct/API/structured-data collectors locally, add self-owned private Apify Actors selectively for sources where managed execution materially helps, keep Hardware Radar as the authority for normalization/matching/persistence, and enforce a hard Apify spend ceiling of $20/month.'
doc_type: 'adr'
status: 'active'
created: '2026-09-24'
updated: '2026-09-24'
reviewed: '2026-09-24'
owner: 'Chris Purcell'
consumer: 'mix'
tags:
  - 'adr'
  - 'acquisition'
  - 'apify'
  - 'scraping'
  - 'cost-control'
  - 'orchestration'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/adr/adr-0012-orchestration-apscheduler.md'
  - 'docs/adr/adr-0014-scraping-runtime-escalation-stack.md'
  - 'docs/adr/adr-0017-resilient-acquisition.md'
  - 'docs/specs/hw-radar-master-spec.md'
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

# ADR 0021: Hybrid acquisition — local collectors + self-owned Apify Actors

MADR status: **accepted**.

## Context and Problem Statement

Hardware Radar originally committed to a predominantly local acquisition stack: direct APIs or structured HTTP where available, then Scrapy/curl_cffi/Playwright escalation inside the hw-radar deployment. That architecture is implemented far enough to prove the common ingestion boundary, but the owner subsequently adopted Apify and already operates a separate `L3DigitalNet/apify-actors` monorepo.

The new question is not whether Apify can replace scraping in the abstract. It is whether Hardware Radar should move collection work to Apify, keep it local, or use both while preserving the product's existing normalization, identity, retention, scheduling, and evidence contracts.

The owner also set a controlling cost constraint: **Hardware Radar must not spend more than $20/month on Apify**. This rules out architectures that assume open-ended browser/proxy usage or default to third-party per-result Actors.

## Considered Options

- **Option 1 — Keep acquisition predominantly local.** Generalize the existing collectors and use official APIs/structured endpoints wherever possible.
- **Option 2 — Hybrid acquisition.** Keep inexpensive direct/local collectors, add Apify selectively, and prefer self-owned Actors for project-specific collectors. **(chosen)**
- **Option 3 — Apify-first acquisition.** Run most collection through Apify, including sources that are already cheap and reliable locally.

## Decision Outcome

Chosen option: **Option 2 — hybrid acquisition, with self-owned Actors as the default Apify path.**

### Provider selection is per source

A marketplace/source and its execution provider are separate concepts. A listing from eBay, Newegg, ServerPartDeals, or another merchant remains the same source listing regardless of whether the bytes arrived from:

1. an official API;
2. a local hw-radar HTTP/Scrapy collector;
3. a self-owned private Apify Actor; or
4. an explicitly approved third-party Actor.

Do **not** fork listing identity or price history when the collection provider changes.

Existing cheap paths stay local unless measurement shows a concrete benefit from moving them. In particular, official APIs and simple structured JSON endpoints are not migrated merely for uniformity.

### Self-owned Actors

Hardware Radar-specific Actors live in the existing private `L3DigitalNet/apify-actors` monorepo and are treated as **internal infrastructure first**, not as Store products. Publication/monetization is a separate decision.

Actors own source-specific acquisition concerns:

- fetching and pagination;
- source-specific extraction;
- bounded retries/timeouts/concurrency;
- run diagnostics;
- query/category scope;
- evidence about pagination/completeness.

Actors do **not** own canonical product identity, cross-source matching, category semantics, price history, watch eligibility, alert state, or writes to the hw-radar system-of-record.

The Actor output is a versioned observation contract consumed by an hw-radar provider adapter.

### Scheduling and ingestion ownership

The hw-radar poller remains the scheduling/admission owner ([ADR 0012](adr-0012-orchestration-apscheduler.md)). For an Apify-backed source it starts a bounded Actor run, records the provider run/build identifiers, and imports completed output asynchronously.

Do not configure an independent Apify schedule for the same production job. There is one scheduling owner per job.

Imports must be idempotent. Duplicate completion notifications, retries, or repeated dataset reads must not create duplicate listings/observations.

### Completeness is evidence, not an assumption

Every provider result must preserve enough metadata to distinguish:

- a complete enumeration that found no matching offers;
- a bounded/truncated run that stopped at a page/request/time/cost limit;
- a failed or partially failed collection.

A bounded or failed Actor run is **never** evidence that unseen listings disappeared. Existing delist/absence logic may consume provider absence only when the provider contract proves the relevant sweep was complete.

### Cost contract

Hardware Radar's Apify consumption has a **hard maximum of $20/month**.

Implementation shall:

- track attributable run cost/usage by source and provider;
- use a lower operating target (initially **$12/month**) so tests, retries, and estimation error have headroom;
- reserve budget before admitting paid work and reconcile actual usage afterward;
- stop admitting nonessential Apify work before the hard limit can be crossed;
- degrade broad discovery before active-watch refreshes;
- surface `budget_paused` / stale state rather than implying freshness;
- make browser execution and residential-proxy use explicit per-source decisions;
- never automatically escalate to paid residential proxies or paid third-party Actors after a failure.

If the Apify account is shared with other workloads, Hardware Radar may use only its explicitly allocated remaining budget; it must not assume the account's full allowance is available.

### Third-party Actors

Third-party Store Actors are an exception path, not the default. A third-party Actor may be adopted only when a representative benchmark shows that its total cost, reliability, data contract, and maintenance benefit beat both a local collector and a self-owned Actor for that source.

## Consequences

- **Good** — preserves working official-API/structured-data paths and the current ingestion/matching investment.
- **Good** — adds managed execution where it is useful without making Apify the system of record.
- **Good** — owning Actors controls output schema, resource limits, tests, and source-specific behavior.
- **Good** — provider changes are reversible because the canonical identity/history remains in hw-radar.
- **Bad (accepted)** — two execution environments must be operated and observed.
- **Bad (accepted)** — self-owned Actors outsource execution, not selector/parser maintenance; source drift is still our responsibility.
- **Constraint** — browser/proxy-heavy sources may not fit the $20/month ceiling at the desired cadence and must be reduced, skipped, or handled another way rather than silently exceeding budget.

## Confirmation

This decision is confirmed when:

1. at least one source can be collected through a self-owned private Actor and imported through the common hw-radar pipeline;
2. switching that source between direct/local and Actor-backed collection preserves the same source-listing identity;
3. a deliberately truncated Actor run cannot trigger false delists;
4. per-run/provider cost is recorded and admission stops safely at the configured project budget;
5. no production job is independently scheduled in both APScheduler and Apify.

## More Information

- **Amends, does not supersede:** [ADR 0012](adr-0012-orchestration-apscheduler.md) — APScheduler remains the scheduling/admission owner; some jobs may execute remotely.
- **Amends, does not supersede:** [ADR 0014](adr-0014-scraping-runtime-escalation-stack.md) — HTTP-first / structured-data-first / browser-last remains the per-source technique rule; execution may now be local or in a self-owned Actor.
- [ADR 0017](adr-0017-resilient-acquisition.md) continues to govern per-source isolation, breaker state, and graceful degradation.
