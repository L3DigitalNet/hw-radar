---
schema_version: '1.1'
id: 'adr-0010-hw-radar-canonical-data-model'
title: 'ADR 0010: Canonical data model — product/variant identity ladder'
description: 'Model identity as a multi-grain ladder — category → product_family → product_model (physical, condition-free) → product_variant (sellable: condition/packaging/warranty-channel) → listing → offer_snapshot, plus an orthogonal drive_unit (serial/SMART) grain — with external identifiers as aliases, drive attributes in a typed per-category satellite, and a retention_class on every evidence record.'
doc_type: 'adr'
status: 'active'
created: '2026-07-03'
updated: '2026-09-24'
reviewed: null
owner: ''
consumer: 'mix'
tags:
  - 'adr'
  - 'data-model'
  - 'schema'
  - 'entity-resolution'
  - 'extensibility'
  - 'retention'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/adr/adr-0007-datastore-postgresql-timescaledb.md'
  - 'docs/adr/adr-0008-currency-landed-cost-normalization.md'
  - 'docs/adr/adr-0009-secrets-runtime-openbao-agent.md'
  - 'docs/specs/hw-radar-master-spec.md'
  - 'docs/open-questions.md'
  - 'docs/resolved-questions.md'
  - 'docs/research/database-architecture.md'
  - 'docs/research/entity-resolution-for-cross-marketplace-hard-drive-and-ssd-price-tracking.md'
  - 'docs/research/machine-usable-drive-suitability-taxonomy-for-24-7-nas-and-server-scoring.md'
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

# ADR 0010: Canonical data model — product/variant identity ladder

MADR status: **accepted**.

## Context and Problem Statement

The hardest modeling problem in Hardware Radar is **identity**: recognizing that listings from ~20 merchants — with different titles, SKUs, and identifiers — refer to the same physical drive, while keeping genuinely different things distinct (16 TB vs 18 TB; **new vs recertified**; SATA vs SAS). The accumulating price history is the tool's compounding value, so whatever the canonical entity is, it is the single most **costly-to-reverse** table in the system — every offer, score, and observation references it, and history accrues under it. Getting the grain wrong means a later rewrite, not a migration.

Two forces constrain the choice, and they pull in opposite directions ([General Design Principles](../specs/hw-radar-master-spec.md#1-purpose--background)):

- **Extensibility & Expandability** — the catalog must accommodate more marketplaces, scoring criteria, users, and eventually **other hardware types** (RAM, GPUs) without a schema rewrite.
- **Engineered to Needs (original 2026-07-03 scope)** — do not over-engineer; the then-current v1 was drives-only. **ADR 0022 supersedes that scope assumption as of 2026-09-24; this ADR's topology decision is unchanged.**

This ADR fixes the **identity spine and the rules for what lives at each grain**. The exhaustive column lists are delegated to the research (they can evolve without a new ADR); the ADR governs the topology.

**Why now, and why it changed:** the datastore engine is settled (PostgreSQL + TimescaleDB, [ADR 0007](adr-0007-datastore-postgresql-timescaledb.md)), which explicitly deferred the _schema_. The original schema sketch in [`database-architecture.md`](../research/database-architecture.md) modeled a two-level `drive_model → offer_snapshot` identity. A 2026-07-03 reconciliation against the **full** research corpus (entity-resolution, suitability-taxonomy, recert, identity/warranty-verification, deal-score, and the scraping/retention reports — several of which post-date the DB-architecture report) found that two-level model **under-built identity**. This ADR adopts the deeper model those reports require.

## Considered Options

- **Option 1 — Two-level `drive_model → offer_snapshot`**, condition as a per-snapshot enum, external identifiers (GTIN/MPN/ASIN/ePID) as columns on `drive_model` (the original `database-architecture.md` sketch).
- **Option 2 — Fully generic Entity-Attribute-Value** (`product` + `attribute(key,value)` rows) for maximum flexibility across hardware types.
- **Option 3 — Generic multi-grain identity ladder + typed per-category satellite** (chosen).

## Decision Outcome

Chosen option: **Option 3.** Identity is a **ladder of grains**, all category-generic except one typed satellite, with external identifiers demoted to aliases and drive attributes isolated in a per-category satellite.

### The identity ladder

| Grain | Table | What it is | Key rule |
| --- | --- | --- | --- |
| Category | `category` | `drive` (v1); later `ram`, `gpu` | The extensibility axis |
| Family | `product_family` | e.g. "Exos X18", "IronWolf Pro" | Watches and tier-lookup target this |
| **Model** | `product_model` | the **physical** variant, **condition-free** | Canonical identity anchor; a surrogate `id` |
| **Variant** | `product_variant` | the **sellable** identity: condition · packaging · recert-channel · warranty-channel | Price analytics roll up here |
| Listing | `listing` | one merchant's offer page | Carries a derived `listing_fingerprint` |
| Observation | `offer_snapshot` | time-series price/stock/fx/score | The TimescaleDB hypertable |
| **Unit** (orthogonal) | `drive_unit` | a **physical drive**: serial + SMART/FARM | Grain below the model; recert-trust evidence |

Supporting: `product_alias` (external identifiers), `drive_spec` (typed satellite), `manufacturer`, `seller`, `source_site`, `raw_payload`, `search_observation`, plus a `verification_event` cache for warranty-lookup results.

### The load-bearing rules (this is the decision)

1. **The canonical entity is the physical `product_model`, condition-free.** Its identity anchor is `manufacturer + normalized_model_number` (a surrogate `id`, not a natural key). Condition is **not** part of model identity ("recert 14 TB Exos" and "new 14 TB Exos" are one model).
2. **Condition/packaging/warranty-channel form a `product_variant`, not a snapshot enum.** A sellable variant is the unit of price comparison. This is the entity-resolution report's "dual identity" (physical vs sellable) made concrete, and it is the grain the original two-level sketch lacked.
3. **External identifiers are aliases, never canonical columns.** GTIN/UPC, ASIN, ePID, OEM-vs-retail and region/revision part numbers go in `product_alias(alias_type, source_site_id, normalized_alias_text, is_primary, first_seen, last_seen)` — because a product has _multiple_ valid GTINs, and ASIN/ePID are marketplace-local. (Confirmed downstream: no target merchant reliably exposes GTIN; **eBay `epid` is Partner-gated** — so matching leans on normalized MPN + parsed attributes, which only works if identifiers are many-to-one aliases.)
4. **Drive attributes live in a typed 1:1 satellite `drive_spec`, not the generic spine.** Scoring-critical fields are **typed columns** (e.g. `recording_tech` CMR/SMR — device-managed SMR is **surfaced but veto-capped**, not gated out of the catalog, per [ADR 0011](adr-0011-composite-deal-score.md); `plp`; `market_tier`; `model_family`; `dwpd`; `workload_tb_year`), with the long tail in `spec_json`. The full attribute list is the suitability-taxonomy report's field tables. Adding a hardware type = a new `*_spec` satellite, **no change to the spine**.
5. **The physical unit is its own grain.** Serial numbers and SMART/FARM data describe one drive (`drive_unit`), and warranty-lookup results are cached in `verification_event` — both absent from the original sketch, both required by recert-trust scoring.
6. **Every evidence/observation record carries a `retention_class` + `expires_at`.** Persistence is governed per source (see [the US data-retention review](../research/us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md)): `merchant_fact` (indefinite), `ebay_listing_observation` (6 h freshness / delete-on-delist), `amazon_ephemeral` (24 h, no image bytes) + `amazon_identifier` (ASIN indefinite), `transient_discovery` (TTL 0 — Google/Serper/Brave), `tavily_extract` (indefinite — no anti-storage clause on extracted facts). **No image-byte columns anywhere** (URLs/hashes only); provider result IDs are transient, never keys. `raw_payload` therefore cannot be one uniform table. **A source-restricted retention class takes precedence over any grain-level TTL a later ADR attaches** — `ebay_listing_observation`'s 6 h freshness / delete-on-delist caps the blanket heartbeat TTLs [ADR 0015](adr-0015-availability-heartbeat-grain-volatility-scheduling.md) sets, so eBay-sourced heartbeats never inherit those longer TTLs.

**Rejected — Option 1 (two-level):** it cannot represent recert-vs-new as distinct sellable identities for price analytics, has no home for serials/SMART, and (by putting GTIN/MPN in columns) cannot hold the multiple/marketplace-scoped identifiers real listings carry. It is the shape this ADR deliberately supersedes; do not "simplify" back to it.

**Rejected — Option 2 (EAV):** forfeits typing, constraints, and indexing — exactly the tools the design leans on (generated `dollars_per_tb`, `pg_trgm` fuzzy matching, partial indexes, gate columns like `recording_tech`). It trades the whole PostgreSQL toolkit for a flexibility drives don't need. Violates _Maintainability_ and _Engineered to Needs_.

### Extensibility, satisfied at the right cost

Four of the five extensibility axes are already met by the data-driven spine (marketplaces = `source_site`/`seller` rows; scoring criteria = category-local scorer/decision logic; users = `users`; alerting = app-layer). Only **hardware types** touch the canonical entity, and they are satisfied structurally: `category`/`product_family`/`product_model`/`product_variant` are category-generic, while typed satellites carry category fields. This was deliberately designed so adding RAM/GPU/CPU does not require a spine rewrite. **ADR 0022 now exercises that extension in v1** rather than deferring it.

### Consequences

- **Good** — recert-vs-new, capacity/interface variants, and cross-merchant sameness are all first-class; the moat (price history) rolls up to a stable `product_variant`.
- **Good** — extensible to new hardware without a spine rewrite; extensible to new identifiers/marketplaces via alias rows.
- **Good** — retention/PII/licensing are enforced _in the schema_ (`retention_class`/`expires_at`, no image bytes), so the acquisition-legal posture is structural, not conventional.
- **Bad (accepted complexity)** — more grains than a naive catalog: most queries join `product_model → drive_spec` and `listing → product_variant`. Justified only because the identity table is the one thing too costly to reshape later; every _cheap-to-add_ concern (RAM specs, extra scoring) is deferred.
- **Bad (population reality)** — with GTIN/ePID largely unavailable, entity resolution leans on parsed attributes + MPN aliases + `pg_trgm`; the alias table will be sparse for the high-value recert merchants. This is a resolver-accuracy cost, not a schema flaw.
- **Downstream ADRs / deferred detail** — the **scoring** tables (`cohort_baseline`, `seller_rating_observation`, enriched `listing_score`), **alerting** tables (`watch`/`watch_selector`/`watch_match_state`/`notification_event`), **scraper-ops** tables (`source`/`scraper_runs`), and **reference/seed** tables (`model_family_ref`, `hdd/ssd_price_baseline`) attach to this spine but are specified in their own research and will get their own ADRs or milestone implementations. This ADR fixes the identity spine they hang from, not their internals.

### Confirmation

The spec's Database Schema section is updated to reference this ladder (superseding the `drive_model`/`listing`/`observation` shorthand). Implementation confirmation (MS-0/MS-1): initial migrations create `category → product_family → product_model → product_variant → listing → offer_snapshot` with `product_alias` and the `drive_spec` satellite; a recert and a new listing of the same drive resolve to **one `product_model`, two `product_variant`s**; `offer_snapshot` is a TimescaleDB hypertable; every evidence table has a non-null `retention_class`; no table stores image bytes.


## 2026-09-24 Scope Amendment — multi-category v1

[ADR 0022](adr-0022-multi-category-watch-first-v1.md) **supersedes only this ADR's original drives-only v1 scope assumption.** The identity topology remains accepted and unchanged.

- v1 now treats HDD/SSD, GPU/accelerator, RAM, and CPU as first-class categories.
- The category-generic spine remains `category → product_family → product_model → product_variant → listing → offer_snapshot`.
- Category-specific attributes still belong in typed 1:1 satellites (`drive_spec`, later `gpu_spec`, `ram_spec`, `cpu_spec`, etc.); do not replace them with EAV or generic strings.
- The existing drive-specific `drive_unit` / SMART/FARM grain remains drive-only.
- Complete servers/barebones may start as exact/curated basic watches; do not overload `product_variant` to pretend materially different installed configurations are the same sellable identity. A configuration-aware representation must precede rich system-level comparison.

The load-bearing decision of this ADR — the generic identity spine plus typed category satellites — is therefore **validated by the scope expansion rather than reopened**.


## More Information

- **Primary research:** [`database-architecture.md`](../research/database-architecture.md) (engine + base schema + generated columns/indexing), [`entity-resolution-…`](../research/entity-resolution-for-cross-marketplace-hard-drive-and-ssd-price-tracking.md) (dual physical/sellable identity, aliases, blocking), [`machine-usable-drive-suitability-taxonomy-…`](../research/machine-usable-drive-suitability-taxonomy-for-24-7-nas-and-server-scoring.md) (the `drive_spec` field tables + tier ladder), [`recertified-enterprise-hard-drives-…`](../research/recertified-enterprise-hard-drives-for-homelab-and-small-business-buyers.md) and [`programmatic-identity-and-warranty-verification-…`](../research/programmatic-identity-and-warranty-verification-for-used-enterprise-hdd-listings.md) (condition/warranty/SMART → variant + `drive_unit`/`verification_event`), [`principled-deal-score-…`](../research/principled-deal-score-for-hard-drive-listings.md) (cohort key + score inputs), [`us-scraping-and-data-retention-landscape-…`](../research/us-scraping-and-data-retention-landscape-for-a-retail-hdd-price-monitor.md) (the `retention_class` verdicts).
- **Related ADRs:** [ADR 0007](adr-0007-datastore-postgresql-timescaledb.md) (engine, which deferred this schema), [ADR 0008](adr-0008-currency-landed-cost-normalization.md) (FX fields on `offer_snapshot`), [ADR 0009](adr-0009-secrets-runtime-openbao-agent.md) (runtime secrets).
- **Open/deferred sub-decisions** (not blockers for the identity spine): eBay long-run price-history stance (store observations, avoid a derived eBay price _model_ — §8 gray area, counsel-flagged); `sector_format`/`sed` as scoring factors (present as nullable `drive_spec` columns, scoring optional); Keepa as a cold-start baseline feed vs. seed tables.
