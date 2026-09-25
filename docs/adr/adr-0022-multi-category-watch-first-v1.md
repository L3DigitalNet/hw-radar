---
schema_version: '1.1'
id: 'adr-0022-hw-radar-multi-category-watch-first-v1'
title: 'ADR 0022: Broaden v1 to multi-category, watch-first hardware monitoring'
description: 'Broaden the first release from HDD/SSD-only scoring to multiple PC/server hardware categories, make requirement matching + trustworthy price/availability watches the launch-critical workflow, keep scoring category-specific rather than universal, and defer the full drive scoring plan from the immediate critical path.'
doc_type: 'adr'
status: 'active'
created: '2026-09-24'
updated: '2026-09-24'
reviewed: '2026-09-24'
owner: 'Chris Purcell'
consumer: 'mix'
tags:
  - 'adr'
  - 'product-scope'
  - 'hardware'
  - 'watching'
  - 'scoring'
  - 'data-model'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/adr/adr-0010-canonical-data-model.md'
  - 'docs/adr/adr-0011-composite-deal-score.md'
  - 'docs/adr/adr-0021-hybrid-acquisition-apify.md'
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

# ADR 0022: Broaden v1 to multi-category, watch-first hardware monitoring

MADR status: **accepted**.

## Context and Problem Statement

The original MVP constrained v1 to HDDs/SSDs and made a sophisticated 0–100 drive score the next major implementation milestone. Since that plan was written, the owner has decided the first useful release should cover a broader set of PC and server hardware, including GPUs, rather than perfecting storage-specific scoring before the product has a complete buyer workflow.

The existing identity spine already anticipated this expansion: `category → product_family → product_model → product_variant → listing → offer_snapshot` is category-generic and drive-specific fields are isolated in `drive_spec` ([ADR 0010](adr-0010-canonical-data-model.md)). The implementation around matching and scoring, however, is still drive-specific.

The strategy therefore needs to broaden product scope **without pretending every hardware category can share one compatibility model or one meaningful score**.

## Considered Options

- **Option 1 — Keep v1 drives-only and finish the existing MS-2 scoring plan before expanding.**
- **Option 2 — Broaden v1 and build a universal cross-category 0–100 score immediately.**
- **Option 3 — Broaden v1, make requirement matching/watches/alerts the launch-critical workflow, and deepen category-specific scoring later. (chosen)**

## Decision Outcome

Chosen option: **Option 3 — multi-category, watch-first v1.**

### First-release category scope

The first release supports these as **first-class categories**:

- HDDs and SSDs;
- GPUs / compute accelerators;
- RAM;
- CPUs.

It may also support selected **basic-watch categories** where exact or curated identity is sufficient:

- NICs;
- HBAs / RAID controllers;
- motherboards;
- complete servers / barebones systems;
- other PC/server components that fit the canonical identity spine.

"First-class" means the category has typed, queryable attributes sufficient for useful requirement matching and comparison. "Basic-watch" means exact part number / curated family plus price, condition, availability, and evidence can be useful even before rich compatibility semantics exist.

### Eligibility before attractiveness

The launch-critical decision is:

> **Does this offer meet the saved requirement?**

Only after eligibility is established should the system answer:

> **How attractive is this offer relative to comparable alternatives/history?**

Missing or contradictory evidence produces an explicit `unknown` / review-needed state. It must not silently become a compatibility pass.

### No universal cross-category score

Do not make a drive score of 90, a GPU score of 90, and a RAM score of 90 imply the same thing.

[ADR 0011](adr-0011-composite-deal-score.md) remains a valid **drive-scoring design**, but implementation of its full MS-2 substrate is **not a first-release blocker**. Category-specific ranking can be added when enough real history and category semantics exist.

The first release may use simple, explainable category-local signals such as:

- requirement match state;
- target-price threshold;
- price change/history where comparable;
- condition;
- seller/source trust evidence;
- shipping/landed-cost uncertainty;
- observation freshness.

### Category modeling

The ADR-0010 identity spine remains authoritative. Each first-class category gets a typed `*_spec` satellite and category-specific extraction/matching rules rather than an EAV rewrite.

The drive matcher is not generalized by replacing drive fields with generic strings. Implement a category dispatch boundary around shared normalization/identity primitives and category-owned attribute extraction/contradiction rules.

Complete servers and barebones systems require configuration-aware representation: a base server model must not silently merge offers with materially different CPU, memory, storage, controller, or included-component configurations. v1 may keep server-system support at the basic-watch level until that representation is designed.

### Source breadth

Broader category scope does **not** require all ~20 contemplated sources to be live for the first useful release.

Start with a deliberately small source set (roughly 3–5 high-value sources) that collectively exercise the first-class categories and the hybrid acquisition model ([ADR 0021](adr-0021-hybrid-acquisition-apify.md)). Expand source breadth only after quality, cost, and freshness are measured.

### Buyer-facing workflow

The first complete product path is:

`save requirement/watch → collect observations → normalize/match → evaluate eligibility → show shortlist/evidence → alert on a qualifying opportunity`.

The UI should prioritize:

- actionable shortlist;
- saved watches/requirements;
- listing detail with evidence, match confidence, condition, shipping uncertainty, and freshness;
- source/provider health and stale/budget-paused states;
- price history where comparison is valid.

Advanced scoring is an enhancement to that workflow, not a prerequisite for it.

## Consequences

- **Good** — reaches the broader hardware-buying use case sooner.
- **Good** — validates the product with a complete useful workflow before investing further in storage-specific scoring machinery.
- **Good** — reuses the costly-to-reverse identity spine exactly as intended.
- **Good** — avoids a misleading universal score across unrelated hardware domains.
- **Bad (accepted)** — category-specific matching/spec work becomes the main domain complexity.
- **Bad (accepted)** — some categories will launch with shallower semantics than storage.
- **Deferred** — the existing detailed MS-2 drive-scoring design and plan remain useful artifacts, but are no longer the next implementation step.

## Confirmation

This decision is confirmed when:

1. the schema and resolver can represent at least HDD/SSD, GPU, RAM, and CPU category data without changing the canonical identity spine;
2. a saved watch can express category-specific required attributes and return `match` / `no_match` / `unknown`;
3. one end-to-end watch produces a real shortlist and exactly-one alert without requiring ADR-0011 scoring;
4. the UI never presents scores from unrelated categories as directly comparable;
5. the old MS-2 scoring implementation plan is explicitly marked deferred pending the new sequencing.

## More Information

- **Amends scope assumption in** [ADR 0010](adr-0010-canonical-data-model.md): v1 is no longer drives-only; the generic-spine decision remains unchanged.
- **Narrows launch-critical scope of** [ADR 0011](adr-0011-composite-deal-score.md): it remains the accepted drive-score design, but no longer gates the multi-category first release.
- [ADR 0021](adr-0021-hybrid-acquisition-apify.md) supplies the acquisition-provider strategy and cost ceiling for the broader source/category plan.

## Amendment — 2026-09-24: Actor proof and Actor-backed pilot sources (owner clarification)

Owner decisions of 2026-09-24 (session 2); the decision above is unchanged.

- The first proof of the hybrid acquisition model's Actor path uses a controlled
  synthetic source through a real private Apify Actor built in this repository
  ([ADR 0021 amendment](adr-0021-hybrid-acquisition-apify.md#amendment--2026-09-24-actor-ownership-billing-cycle-budget-and-actor-proof-owner-clarification)).
  That synthetic source is a test instrument, not one of the "roughly 3–5
  high-value sources" of *Source breadth*.
- An Actor-backed production merchant source joins the pilot set only after a
  per-source admission decision (`docs/research/source-admission/`;
  [OQ24](../open-questions.md#oq24--production-actor-backed-merchant-source-admission)).
  Existing local connectors are not grandfathered into an Actor path.
- Whether MS-2 may exit on the synthetic proof while the Actor-backed pilot source
  waits on OQ24 is an open owner decision, recorded as risk R31 in the MS-2 plan.
