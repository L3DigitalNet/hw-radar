---
schema_version: '1.1'
id: 2026-09-24-ms2-code-dependency-map
title: MS-2 Multi-Category Watch Core — Code Dependency Map
description: Code-verified map of the seams the MS-2 multi-category watch core must cut through — unchanged abstractions, drive assumptions, fetch/parse coupling, source-vs-provider conflation, duplicate-observation prevention, delist completeness flow, pre-resolution category hints, and the watch attach point — with file:line evidence at dev 81876da.
doc_type: research
status: active
created: '2026-09-24'
updated: '2026-09-24'
reviewed: null
owner: chris
tags:
- ms-2
- multi-category
- dependency-map
- acquisition-provider
- apify
- category-dispatch
- eligibility
- delist
aliases:
- MS-2 dependency map
related:
- ../adr/adr-0021-hybrid-acquisition-apify.md
- ../adr/adr-0022-multi-category-watch-first-v1.md
- ../adr/adr-0010-canonical-data-model.md
- ../adr/adr-0019-listing-catalog-matching-layer.md
- ../superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md
source: []
confidence: high
visibility: public
license: null
---

# MS-2 Multi-Category Watch Core — Code Dependency Map

## Bottom line

The ADR-0010 identity spine, the listing/offer/retention/delist substrate, the
resolution edge table, and the scheduling/admission substrate are already
category- and provider-agnostic. Three places carry the multi-category and
hybrid-acquisition risk:

1. **Drive semantics are hard-wired into the resolver's inputs**, not into its
   decision shape. `ladder.decide()` is generic except for the `HardAttrs`
   payload and `contradictions()`. The resolver hard-codes the drive vocabulary,
   MPN shapes, grammars, `DriveSpec` reads, and
   `Category.objects.get(slug="drive")`.
2. **Source identity and execution mechanism are the same object.** The
   `SourceAdapter` is both the marketplace identity (`site_key`) and the
   transport. No provider axis, no run-level idempotency, and no completeness
   evidence exist beyond the single boolean `DelistScope.complete`.
3. **No watch, requirement, or eligibility model exists.** It can attach to
   `Listing` identity FKs and `OfferSnapshot` facts without touching any ADR-0011
   scoring table.

Evidence: every line reference below was re-read at `dev` 81876da (2026-09-24).
Paths are repository-relative. This document is a frozen snapshot. The design
decisions it informs live in
[the MS-2 plan](../superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md).

## Q1 — Abstractions that remain unchanged

| Abstraction | Evidence | Why it survives MS-2 unchanged |
| --- | --- | --- |
| Identity spine `Category` / `ProductFamily` / `ProductModel` / `ProductVariant` | `src/hw_radar/catalog/models/identity.py:96-107`, `:110-127`, `:131-166`, `:169-203` | No drive fields. `Category` is a seeded slug table with no CHECK pinning values (`:96-107`); ADR 0010's 2026-09-24 amendment validates, not reopens, it. |
| `DriveSpec` 1:1 satellite pattern | `identity.py:206-236` | The satellite pattern is additive. `gpu_spec`/`ram_spec`/`cpu_spec` copy it, including `retention_constraints()`/`retention_indexes()` (`:233-236`). |
| `ProductAlias` grain-addressed aliases | `identity.py:239-322` | Alias type and source kind are generic; exactly-one-grain CHECK is category-neutral. |
| `Listing` / `OfferSnapshot` / `SourceSite` / `Seller` | `src/hw_radar/catalog/models/market.py:166-507`, `:510-569` | Category-agnostic; identity FKs are nullable and generic (`:244-268`); delist/redaction has no drive logic. |
| DR-001 retention helpers | `src/hw_radar/catalog/models/base.py:4-50`, `:53-74`, `:77-96`, `:99-106` | `retention_constraints(prefix)` + `retention_indexes(name)` give any new retention-bearing table the CHECK pair and sweep index. |
| `ListingResolution` append-only edge + `ResolutionMethod` | `src/hw_radar/catalog/models/resolution.py:25-32`, `:35-134` | Stores identity outcomes (grain/target/method/confidence/evidence), not drive attributes. |
| Matching shape types | `src/hw_radar/matching/types.py:14-35`, `:71-101`; `src/hw_radar/matching/ladder.py:61-111` | `Grain`, `Provenance`, `TokenKind`, `MpnCandidate`, `DecodeResult`, `TargetRef`, `AliasHit`, `PriorResolution`, `Outcome`, `Verdict` carry no drive-typed fields except the `HardAttrs` they hold. |
| Shared normalizer | `src/hw_radar/matching/normalize.py` (`canonicalize_title`, `normalize_alias_text`) | Pure text normalization; ADR-0019 rule 1 stays the one normalizer for every category. |
| Resolver write path | `src/hw_radar/matching/resolver.py:439-565` (`_apply`), `:140-146` (`_current_edge`), `:204-267` (`_alias_hits` lookup/compatibility) | Transactional edge writing, no-spam rules, and alias-kind compatibility are category-neutral. |
| Scheduling/ops substrate | `src/hw_radar/catalog/models/ops.py:93-147` (`SourceConfig`), `:165-203` (`SourceLaneState`), `:206-227` (`ScraperRun`); `src/hw_radar/acquisition/scheduling/admission.py:32` (`check_admission`) | No category coupling; admission order (disabled → SKIP → paused → backoff → bucket) is provider-neutral. |
| Pipeline-owned FX/persist/resolve stages | `src/hw_radar/acquisition/pipeline.py:204-256`, `:326-336`; `src/hw_radar/acquisition/persist.py:28-102` | Shared by every source (spec C.1). The Actor path must reuse them. |
| Eval harness mechanics | `src/hw_radar/matching/eval/{corpus,evaluate,report}.py` | Mechanics are reusable. The corpus (`tests/fixtures/matching_corpus/synthetic.jsonl`) is drive-only and does not validate GPU/RAM/CPU. |

## Q2 — Drive assumptions and how separable each is

| Drive assumption | Evidence | Separability |
| --- | --- | --- |
| N2 extraction output type | `matching/types.py:45-68` (`ExtractedAttributes`: capacity, interface, rpm, sector, recording tech, security + shared condition/packaging/warranty/brand/quantity) | Entangled type, but one pure entry point: `vocab.extract()` (`matching/vocab.py:233`). |
| Drive vocabulary | `matching/vocab.py:24-253` | Entangled regex module. Condition/packaging/warranty tables (`:53-78`) are cross-category in substance. |
| MPN shapes | `matching/mpn.py:43-70` (`_classify_shape`, `extract_candidates`) | Drive vendor shapes. One entry point, `extract_candidates(title, *, structured_mpn, source_key)` (`:57-59`). |
| MPN grammars | `matching/grammars/__init__.py:15-28` (`decode` over Seagate/WD/Toshiba) | Drive-only. Single call site `_first_decode` (`matching/resolver.py:270-277`). |
| Veto payload | `matching/ladder.py:49-58` (`HardAttrs`), `:114-131` (`contradictions`) | `decide()` (`:147-277`) reaches drive semantics only through `contradictions()` (called at `:161`, `:196`, `:225`) and the rung-2 capacity check (`:244-262`, reachable only when a drive grammar decoded). This is the cleanest dispatch boundary. |
| Spec readers | `matching/resolver.py:75-81` (`_spec_of` → `model.drive_spec`), `:84-99` (`_hard_attrs_from_spec`, drive token vocab), `:102-124` (`_family_agreement_attrs` over `DriveSpec`) | ORM-bound, drive-only. Called from `_prior_from_listing` (`:149-189`) and `_alias_hits` (`:204-267`). |
| Provisional family category | `matching/resolver.py:332` `Category.objects.get(slug="drive")` inside `_materialize` (`:310-364`) | One literal. Rung 2 (the only path that creates families) is reachable only via drive grammars. |
| Resolver entry | `matching/resolver.py:280-307` (`_run_ladder`), `:567-586` (`CatalogResolver.resolve_listing`) | Natural dispatch site. It must know the listing's category before choosing extract/candidates/decode/veto/spec readers (see Q7). |
| Refdata seed contract | `src/hw_radar/refdata/contracts.py:56` (`SeedSpec` mirrors `DriveSpec`), `src/hw_radar/refdata/persist.py:152` (`get_or_create(slug="drive")`), `:208` (`DriveSpec.objects.update_or_create`) | Envelope (`SeedDocument`/`SeedModel`/`SeedAlias`, `contracts.py:42-99`) and conflict detection are reusable. The spec payload and persist target are drive-only. |
| Drive category seed | `src/hw_radar/catalog/migrations/0001_initial.py:7-13`, `:577` | Drive row seeded by migration; other categories need rows (data only). |

## Q3 — `fetch()`/`parse()` coupling

- The coupling is one structural protocol: `SourceAdapter`
  (`src/hw_radar/acquisition/contracts.py:67-84`: `name`, `site_key`, `run_kind`,
  `expects_json`, `last_parse_skipped`, `fetch() -> RawBatch`,
  `parse(batch) -> list[ParsedListing]`). Optional capabilities are discovered
  structurally: `DelistDetector.delist_scope` (`contracts.py:120-125`), the
  reflective retention read `adapter_retention()` (`contracts.py:136-153`), and
  `HeartbeatProbe.probe()` (`src/hw_radar/acquisition/heartbeat.py:92-97`).
- **Direct `fetch()`/`parse()` callers.** This list was corrected on
  2026-09-24 after cross-agent review round 1 (finding F-02), and re-verified by
  `grep -rnE '\.(fetch|parse)\(' src/` at `dev` df114de. The first version wrongly
  said `run_source()` was the only production caller. There are three kinds of
  caller:
  - **The pipeline.** `run_source()` (`pipeline.py:339-416`) fetches under
    `asyncio.timeout` (`:354-355`), classifies (`:356-357`), parses (`:359`),
    and applies the zero-record parser-rot guard (`:362-363`).
  - **Corpus harvesting, outside the pipeline.** The `harvest_corpus`
    management command's `_fetch_parse`
    (`src/hw_radar/catalog/management/commands/harvest_corpus.py:134-141`,
    fetch at `:140`, parse at `:141`, called at `:204`) drives adapters
    directly and persists nothing. It serializes listings through
    `_staging_entry` (`:70`), which copies only `source_listing_key`, `url`,
    `price`, `currency`, `condition_label`, and `attrs`. The eval harness
    reloads them through `ListingFields` (`matching/eval/corpus.py:181`,
    `extra="forbid"`) and rebuilds a `ParsedListing` in `_ingest`
    (`matching/eval/evaluate.py:106`), again without any category field.
  - **Heartbeat probes, outside the pipeline.** Each `HeartbeatProbe.probe()`
    reuses its own adapter's `fetch()`/`parse()` to produce readings, with no DB
    writes:
    - eBay `sources/ebay.py:318-335` (fetch `:321`, parse `:334`);
    - ServerPartDeals `sources/serverpartdeals.py:130-143` (`:131`, `:143`);
    - Seagate `sources/seagate.py:149-162` (`:150`, `:162`);
    - WD `sources/wd.py:208-223` (`:209`, `:223`).

    `run_heartbeat()` calls `adapter.probe()` (`heartbeat.py:218`). On a
    transition it calls `run_source(..., run_kind=RunKind.FULL)`
    (`heartbeat.py:228-234`).

  `acquisition/http.py:55` (`parser.parse(...)`) is a robots-parser call, not
  an adapter call.
- Callers of `run_source`: `poll_source` (`src/hw_radar/poller/service.py:81`,
  call at `:110-115`), `recovery_probe_job` (`service.py:208`, call at
  `:242-248`), and `run_heartbeat` (`heartbeat.py:228`). All three forward
  `adapter_retention(adapter)`. None of them passes a category, an evaluator,
  or a provider. `recovery_probe_job` always resolves the local factory from
  `ADAPTERS` (`service.py:219-221`).
- Adapter construction is a zero-argument factory in `ADAPTERS`
  (`src/hw_radar/acquisition/sources/__init__.py:13-20`). No run-time query
  scope, category, or provider argument exists. Each adapter hard-codes its
  scope (eBay: `SEARCH_PARAMS`, `ebay.py:54`).
- **Implication:** a provider seam can wrap `SourceAdapter` without editing any
  adapter. `run_source(adapter, …)` keeps its signature and delegates to a
  provider-generic runner. All five collectors, the heartbeat-fired FULL run,
  and the recovery probe then run unchanged. The seam does **not** cover:
  - corpus harvesting and replay, which need their own category-hint plumbing;
  - heartbeat `probe()` readings, which stay local-only;
  - provider selection for recovery probes, which needs its own dispatch.

  The MS-2 plan (revision 2) handles these in MS2-D-27, MS2-D-18, and MS2-D-24.

## Q4 — Source identity conflated with execution mechanism

- `site_key` is both the registry key (`sources/__init__.py:13`) and the
  `SourceSite` lookup inside the pipeline (`pipeline.py:349`). Transport is
  chosen inside each adapter class (eBay `httpx.AsyncClient`, `ebay.py:134`;
  Scrapy adapters via `acquisition/scrapy_support.py`).
- None of `SourceConfig` (`ops.py:93-147`), `ScraperRun` (`ops.py:206-227`),
  `SourceTier`, `CheapSignal`, `SchedulingLane` (`ops.py:57-67`), or `RunKind`
  (`ops.py:70-75`) is a provider axis. Tier and cheap-signal describe cost/exposure, lane
  is cadence, and run kind is pipeline purpose. Repurposing any of them would
  conflate unrelated meanings.
- `ScraperRun.detail_json` (`ops.py:220`) is the only extension point that
  needs no migration (`pipeline.py:396-403`).
- The listing identity key is provider-neutral already: `Listing` is unique on
  `(source_site, source_listing_key)` (`market.py:303-307`). Provider
  switching preserves identity **iff** every provider for a site emits the same
  `source_listing_key` for the same offer. That property must be pinned by a
  contract test; nothing enforces it today.

## Q5 — Duplicate-observation prevention

- Listing level: `upsert_listing()` uses `update_or_create` on
  `(source_site, source_listing_key)` (`persist.py:52-71`). Replays never
  duplicate listings.
- Observation level: `OfferSnapshot` has only its composite PK
  `(listing_id, observed_at)` (`market.py:513`). `append_snapshot()` always
  `.create()`s (`persist.py:74-102`) with `observed_at = batch.fetched_at`
  (`pipeline.py:239`). A replay with a new `fetched_at` appends a new row
  (intended MS-1 behavior). A replay with the same `fetched_at` raises on the PK.
  That protection is accidental, not designed.
- Raw level: `RawPayload.content_hash` (`evidence.py:34`) is a plain index
  (`evidence.py:40-41`), not unique. Replays duplicate raw rows
  (`persist.py:28-49`).
- Run level: **no idempotency key exists.** `ScraperRun` has no provider
  run/build identifier. Two imports of the same remote run would create two
  `ScraperRun` rows and append snapshots twice if `fetched_at` differed.
  ADR 0021 / DR-011 require a designed run-level key.

## Q6 — Delist scope and completeness flow

- Only `RunKind.FULL` runs reach the delist stage (`pipeline.py:377-383`).
  PROBE and HEARTBEAT-kind runs are excluded. Heartbeat-fired runs are FULL
  (`heartbeat.py:228-234`) and do run it.
- Continuity is recorded for every successful FULL run before the scope is
  consulted: `_record_sweep_continuity` (`pipeline.py:87-126`, call at `:379`),
  stored in `SourceLaneState.continuous_since` (`ops.py:195`, migration 0015).
- The scope is adapter-owned via `isinstance(adapter, DelistDetector)`
  (`pipeline.py:380-383`). Only eBay implements it (`ebay.py:293-316`).
  `_sweep_is_complete` (`ebay.py:268-291`) requires no parse skips, no `next`
  href, and `total <= seen`, which is normally False for the broad drive sweep.
- `_apply_delist` (`pipeline.py:129-168`): `complete=True` delists every
  site listing absent from `seen_keys` immediately (`ABSENT_FROM_SWEEP`). With
  `complete=False` it delists only after continuity ≥ `absence_grace` and
  `last_seen < observed_at - absence_grace` (`ABSENT_STALE`). Failed runs never
  reach the delist stage (`pipeline.py:413-433`).
- **Two gaps for MS-2.** (a) Completeness is one boolean (`contracts.py:114-117`)
  asserted by the adapter's own honesty. A naive remote provider could claim
  `complete=True` for a truncated run, or feed `complete=False` into the
  stale-absence path. The ADR-0021 four-state completeness taxonomy has no
  representation. (b) The candidate set is **site-wide**
  (`pipeline.py:145-149`). A complete sweep of one category or query on a
  multi-category site (eBay GPU `category_ids`) would delist that site's
  listings from every other sweep. Multi-sweep sources need scope-keyed absence
  before they can produce complete scopes.
- eBay delete-on-delist redaction stays in `Listing.mark_delisted`
  (`market.py:330-377`) and `DELETE_ON_DELIST_CLASSES` (`market.py:107`),
  independent of provider.

## Q7 — Pre-resolution category-hint data

- `RawItem`, `RawBatch`, `ParsedListing`, and `NormalizedListing`
  (`contracts.py:21-65`) carry **no category field**.
  `ParsedListing.attrs: dict[str, object]` (`contracts.py:53`) is the only open
  extension. It is persisted verbatim as `OfferSnapshot.attrs_json`
  (`persist.py:98`, `market.py:544`) and already read back by the resolver for
  the structured MPN (`resolver.py:127-137`, latest snapshot by `observed_at`).
- `SourceConfig` has no category or scope field (`ops.py:93-147`). No adapter
  requests or reads an eBay `categoryId` (`ebay.py:54`, `SEARCH_PARAMS` is
  keyword-only).
- Reliability: today the category is implicit in which adapter/query produced
  the row. All five MS-1 sources are drive-only by construction, so "no hint ⇒
  drive" reproduces current behavior exactly. The earliest reliable boundary is
  the query scope the collector ran: eBay `category_ids` per sweep, or the
  Actor input scope echoed per item. Title text is not reliable for this.
- The resolver already reads the latest snapshot's `attrs_json`. A typed
  `ParsedListing.category_hint`, persisted under a reserved `attrs_json` key,
  reaches the resolver without a migration.

## Q8 — Watch/requirement attach point without ADR-0011 entanglement

- Nothing exists: `accounts/models.py` defines only the `User` stub, and there
  is no `watch`, `watch_selector`, `watch_match_state`, `notification_event`,
  `listing_score`, or `cohort_baseline` model anywhere in `src/`. ADR 0010
  explicitly deferred those tables to their own designs.
- Eligibility inputs that exist without scoring:
  - resolved identity on `Listing` (`product_family`/`product_model`/`product_variant`, `market.py:244-268`) plus the typed `*_spec` satellite of that model;
  - the current `ListingResolution` edge (outcome/rung/method/confidence, `resolution.py:35-134`);
  - offer facts on the latest `OfferSnapshot` (`item_price`, `shipping_price`, `stock_status`, FX-stamped USD, `market.py:510-569`);
  - `Listing` condition text, delist state, and retention (`market.py:166-507`).
- `ListingResolution` answers "which catalog entity is this?"
  (`accept | review | none`). It has no `no_match`, and eligibility must not
  be derived from it by renaming. The eligibility verdict is a second,
  orthogonal decision keyed by (watch, listing).
- ADR-0011 artifacts (`listing_score`, `cohort_baseline`, denormalized
  `current_*` score fields, `seller_rating_observation`) are scoring-only and
  unbuilt. The deferred MS-2a plan
  (`docs/superpowers/plans/2026-09-06-ms2a-scoring-substrate.md:4-10`) names
  migrations 0018–0026 but explicitly requires a rebase before any execution.
  The watch core must read none of these.

## Budget governance status

ADR 0016's `SearchBudgetGate` (kill switch → persisted reserve-then-call →
failing-provider breaker → bucket) is architecture only. A repository grep for
the gate, spend-cap, and reservation names returns nothing in `src/`. The only
admission gate is `check_admission` (`scheduling/admission.py:32`) plus
per-source token buckets, with no cost dimension. An Apify spend ledger is new
construction. ADR 0016 supplies the reserve-then-reconcile *pattern* only.

## External facts consumed (dated 2026-09-24)

- Apify: `maxTotalChargeUsd` applies only to pay-per-event Actors. A
  self-owned Actor's cost bound is `memory × timeout × $/CU` (1 CU = 1 GB-hour;
  $0.20/CU on Free/Starter per the official pricing page). Runs expose
  `usageTotalUsd` (`usage_total_usd`, `float | None`), recomputed at current
  pricing ("informational"). `waitForFinish` holds at most 60 s, so
  completion needs polling or webhooks. Webhooks need public ingress, which
  this deployment lacks. Default storages expire in 7 days (Free) or 31 days
  (paid). Account-level `max_monthly_usage_usd` exists as a backstop.
  `apify-client` 3.2.0 defaults to the `impit` transport, not `httpx`.
- eBay Browse: `category_ids` accepts **one** ID per request. US leaf IDs
  observed are GPU 27386, RAM 170083, and CPU 164; IDs can change and should be
  re-resolved via the Taxonomy API. Result sets cap at 10,000 and `limit` maxes
  at 200. The current collector issues one GET with no pagination
  (`ebay.py:192-208`).
- Newegg: its Terms of Use prohibit automated access and scraping "for any
  purpose", and its robots.txt names a price-watch bot for a full-site block.
  Execution venue does not change permissibility. Newegg is excluded unless
  the owner decides otherwise.
