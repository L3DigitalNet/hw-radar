---
schema_version: '1.1'
id: 'adr-0012-hw-radar-orchestration-apscheduler'
title: 'ADR 0012: Orchestration engine — APScheduler in one supervised poller'
description: 'Run the recurring fetch→…→alert pipeline under APScheduler 3.11.x inside a single systemd-supervised long-running poller process that owns per-source cadence, jitter, two-level token-bucket admission, and shared circuit-breaker state — not one systemd timer per scrape; reconciles ADR 0006 to say systemd supervises the process rather than schedules each scrape.'
doc_type: 'adr'
status: 'active'
created: '2026-07-04'
updated: '2026-09-24'
reviewed: null
owner: ''
consumer: 'mix'
tags:
  - 'adr'
  - 'orchestration'
  - 'scheduling'
  - 'apscheduler'
  - 'systemd'
  - 'rate-limiting'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/adr/adr-0006-cd-rsync-over-tailscale-ssh.md'
  - 'docs/specs/hw-radar-master-spec.md'
  - 'docs/resolved-questions.md'
  - 'docs/research/orchestration-choice-for-a-single-vm-price-polling-service.md'
  - 'docs/research/orchestration-engine-reconfirmation-2026.md'
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

# ADR 0012: Orchestration engine — APScheduler in one supervised poller

MADR status: **accepted**.

## Context and Problem Statement

The recurring pipeline (`fetch → parse → normalize → entity-resolve → score → persist → alert`) needs a scheduler for ~20 sources on a single LXC container. Two facts make the choice load-bearing rather than incidental:

1. The acquisition design (OQ7/OQ9/OQ10) requires **shared, in-memory, fast-mutating state** across sources — per-source and per-domain **two-level token buckets**, an adaptive 429/503 cooldown, and a **circuit-breaker** whose `paused_pending_fix` state must be visible to whatever decides "is this source eligible right now."
2. **ADR 0006 said "periodic scrapes run under systemd timers, not an in-process scheduler."** That was written for stateless, independent jobs — before the orchestration research established that these scrape jobs are **not** independent (they share admission budgets and breaker state). So there is a live contradiction to resolve.

Research [`orchestration-choice-for-a-single-vm-price-polling-service`](../research/orchestration-choice-for-a-single-vm-price-polling-service.md) recommended APScheduler; [`orchestration-engine-reconfirmation-2026`](../research/orchestration-engine-reconfirmation-2026.md) re-confirmed it a day later against the 2026 ecosystem (resolved-questions.md OQ12).

## Considered Options

- **Option 1 — APScheduler 3.11.x in one long-running, systemd-supervised poller process** (`AsyncIOScheduler`). (chosen)
- **Option 2 — Independent systemd timers**, one oneshot unit per scrape.
- **Option 3 — A broker-based task queue** (Celery / RQ / Dramatiq / Taskiq / Repid).

## Decision Outcome

Chosen option: **Option 1.**

**APScheduler 3.11.x, not 4.x.** 3.11.3 shipped 2026-06-28 (active maintenance); APScheduler 4.0 is still `4.0.0a6` and labeled "do NOT use in production." APScheduler 3.x also **does not safely support multiple scheduler processes sharing one job store**, so "one process" is the *correct* model, not merely the simplest.

**One supervised process, shared state in memory.** The poller runs as a single `systemd` service (`Restart=on-failure`, resource limits, the same hardening ADR 0006 already specifies for the worker unit), holding the token buckets and circuit-breaker registry **in memory** (checkpointed to PostgreSQL for crash recovery, and to the `scraper_runs` table for run state). A `Retry-After` on one source's request immediately affects that source's *next* admission check — no cross-process re-read from disk. Because **Scrapy's default reactor is now the asyncio one**, the crawler and the `AsyncIOScheduler` share a single event loop in the same process, with no Twisted↔asyncio bridge. Blast radius is bounded by `max_instances=1`, `coalesce=True`, per-source `misfire_grace_time`, and hard fetch-stage timeouts.

**ADR 0006 reconciliation (a wording change, not a design reversal).** ADR 0006's actual goals — process isolation from the web unit, independent resource limits, `Restart=` semantics, journal logging — are **preserved**: systemd supervises the **poller process**; it simply no longer schedules each individual scrape. `systemd` timers remain the right primitive for anything genuinely independent and stateless (nightly `VACUUM`, backup verification). See the amendment recorded in ADR 0006.

Option 2 was rejected: independent timer units make *shared* back-off/circuit-breaker state a slow, lock-contended cross-process emulation of what an in-process scheduler does natively. Option 3 was rejected as over-engineered: Celery/RQ/Dramatiq/Taskiq/Repid solve distributed high-throughput *broker* workloads this project does not have, and every one of them adds a **Redis** dependency (and its CVE surface — e.g. RediShell CVE-2025-49844) to run fewer than 20 jobs/day per source.

### Consequences

- **Good** — the token-bucket/circuit-breaker substrate that OQ7/OQ9/OQ10 all assume becomes trivial: it is just shared in-process state.
- **Good** — smallest security and operational surface: no broker, no network listener for the scheduler, no extra patch burden.
- **Good** — consistent with the design principles: **Moderate Aggressive Usage** and **Reliability** both want fast shared admission/eligibility state; **Engineered to Needs** argues against a broker.
- **Bad (accepted, mitigated)** — scheduling, admission, and execution share one failure domain; a scheduler bug can affect job execution. Bounded by `max_instances=1` / `coalesce` / `misfire_grace_time` / fetch-stage timeouts rather than by a per-run OS process.
- **Watch** — when APScheduler 4.0 stabilizes (drops the production warning), re-evaluate; the migration is a contained, single-process change.

### Confirmation

Implementation confirmation (MS-1/MS-5): the poller runs as one `Active` systemd service; per-source cadence, jitter, and the adaptive 429/503 cooldown are observable in `scraper_runs`; a source that trips the breaker moves to `paused_pending_fix` without halting the others.


## 2026-09-24 Amendment — external execution under one scheduling owner

[ADR 0021](adr-0021-hybrid-acquisition-apify.md) adds self-owned Apify Actors as an optional execution provider. APScheduler remains the **production scheduling/admission owner**.

For an Apify-backed source, the supervised poller may start a bounded Actor run, persist provider run/build identifiers and cost/completeness metadata, and import completed output asynchronously. This does not create a second scheduler.

- Do not configure an independent Apify schedule for a production job already owned by APScheduler.
- The poller continues to own source eligibility, cadence, back-off/circuit-breaker state, and project-level budget admission.
- Remote execution must degrade like any other source failure: one Actor/source failing cannot halt the others.
- Completion delivery may be polled or webhook-assisted, but imports are idempotent and duplicate notifications cannot duplicate observations.

The original "one supervised poller" decision therefore still holds; only the physical location of some fetch/parse work may be remote.


## More Information

- **Amends** [ADR 0006](adr-0006-cd-rsync-over-tailscale-ssh.md) (the "timers for scrapes" sentence). Does **not** reopen ADR 0006's deployment mechanics.
- **Unblocks** OQ7 (search self-governance), OQ9 (cadence/back-off), OQ10 (resilience) — all assume this in-process model.
- **Findings:** resolved-questions.md **OQ12**; research [`orchestration-choice`](../research/orchestration-choice-for-a-single-vm-price-polling-service.md) + [`orchestration-engine-reconfirmation-2026`](../research/orchestration-engine-reconfirmation-2026.md).
