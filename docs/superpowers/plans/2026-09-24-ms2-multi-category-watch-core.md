# MS-2 — Multi-category watch core: Implementation Plan

> For the executing agent: slices land in order, **one slice = one PR into `dev`**.
> Inside a slice, work top to bottom. Every behavior task is TDD: write the failing
> test first, watch it fail for the stated reason, implement, get it green, and make
> a signed conventional commit.
>
> Design sources, in precedence order:
> 1. Master spec §19 MS-2 (`docs/specs/hw-radar-master-spec.md:921-944`, milestone
>    boundary) and §§7–9 (FR/NFR/IR/DR IDs below).
> 2. [ADR 0021](../../adr/adr-0021-hybrid-acquisition-apify.md) (hybrid acquisition,
>    Apify cost contract).
> 3. [ADR 0022](../../adr/adr-0022-multi-category-watch-first-v1.md) (multi-category,
>    watch-first).
> 4. ADRs 0010, 0012, 0014, 0016 (pattern only), 0017, 0019, 0020.
> 5. Code evidence: [MS-2 code dependency map](../../research/2026-09-24-ms2-code-dependency-map.md)
>    (cited below as *Map Qn*).
>
> Deviations go to the plan's Open risks section and the OQ process, never silently.
> This plan does not edit ADRs or the master spec.

**Goal:** prove the smallest complete multi-category decision path without an
ADR-0011 score:

`saved requirement → collect (local or self-owned Actor) → canonicalize/match →
eligibility verdict (match | no_match | unknown) → shortlist read model`

HDD/SSD, GPU/accelerator, RAM, and CPU are first-class categories. The Apify path is
bounded, idempotent, completeness-honest, and budget-admitted.

**Not in MS-2** (settled D1):

- the "exactly-one alert" and the M365 transport (MS-4);
- shortlist, watch, and detail UI pages (MS-3; MS-2 ships the query/read model only);
- category ranking or scores (MS-5 / deferred ADR 0011);
- rich server-configuration modeling (D11).

## Global constraints

- **Verification gate at every commit:**
  `HW_RADAR_DB_PORT=5433 uv run python -m scripts.check` runs ruff format --check,
  ruff check, basedpyright, coverage run -m pytest, coverage report, and pip-audit.
  Also run `HW_RADAR_DB_PORT=5433 uv run python manage.py makemigrations --check --dry-run`.
  The gate has no docs step. Workstation CPU policy (remote execution wrapper)
  governs *where* the command runs. It does not change the command.
- Run **one pytest process against the shared dev DB at a time**. Concurrent
  `transaction=True` runs on port 5433 flake. Rerun clean before treating a DB-test
  failure as real.
- Baseline at `81876da`: gate green, 552 passed / 1 skipped, coverage 95%. A slice
  may not lower coverage below the configured threshold.
- **Migrations** (settled D2). The catalog head is `0017_identity_retention_checks`,
  and MS-2 owns `0018+` in this order: B `0018`, `0019`; C `0020`; D `0021`; E `0022`.
  Every migration must:
  - apply cleanly to an empty DB and upgrade a DB holding deployed drive data without
    loss;
  - be additive/expand-contract (new nullable columns or new tables only; no drop
    or rename of deployed columns);
  - add the DR-001 CHECK pair and sweep index to every new retention-bearing table,
    via `retention_constraints(prefix)` / `retention_indexes(name)`
    (`src/hw_radar/catalog/models/base.py:53-96`);
  - be tested by `tests/db/test_migrations.py` plus the slice's own
    constraint tests.

  If merge order changes, renumber the *unmerged* slice's migrations before merge.
  Never renumber a merged migration. The deferred MS-2a plan
  (`docs/superpowers/plans/2026-09-06-ms2a-scoring-substrate.md:4-10`) names
  `0018–0026` for its own work. It is **not** reserved here and must be rebased on
  whatever history exists when the owner reactivates it.
- **Public repo:** no secrets, tokens, private hostnames or IPs, Actor account
  identifiers, or absolute local paths in committed text. The Apify token is
  referenced only as the env var `HW_RADAR_APIFY_TOKEN`, rendered by the OpenBao
  Agent (NFR-003). Actor references come from settings or the environment, never
  from committed literals.
- Dependencies change only through `uv add` / `uv remove`. Never hand-edit
  `uv.lock`.
- Conventional, GPG-signed commits. Update `docs/TODO.md` / `docs/STATUS.md` per
  TODO discipline in each slice's close-out task.
- **Regression files are frozen per slice.** Unless the slice lists a file as
  changed, existing test files are not edited. New tests go in new files. Slice
  exit evidence includes `git diff --stat <slice-base> -- <frozen files>` showing
  no changes.

## Things not to do

- Do not change the ADR-0010 identity spine
  (`category → product_family → product_model → product_variant → listing →
  offer_snapshot`), add an EAV table, or put watch-critical values in a JSON bag
  (ADR 0022, NFR-006, D10).
- Do not flatten drive semantics into generic strings. Do not change drive matcher
  decisions or the drive precision gate. Do not treat the drive corpus as
  validation for GPU/RAM/CPU (master spec :917-919, D9).
- Do not map `ListingResolution` outcomes (`accept | review | none`) onto
  eligibility verdicts. They are separate decisions (D10).
- Do not let `unknown` count as `match`, and do not return `no_match` without a
  positive contradiction (FR-014, D10).
- Do not read or create `listing_score`, `cohort_baseline`, `current_*` score
  columns, or any ADR-0011 artifact. Do not present cross-category scores
  (ADR 0022).
- Do not let a non-`complete` provider result produce `DelistScope.complete=True`,
  advance sweep continuity, or reach the stale-absence path for a remote provider.
  Do not change the eBay delist path or the lane-continuity gate (migration 0015)
  (D5).
- Do not create listings, fork history, duplicate observations, or reset watch
  state when a source switches provider. Do not use Apify result IDs as keys
  (DR-003, D6).
- Do not configure an Apify schedule for any production job. Do not use webhooks
  in v1. Do not run a second scheduler (ADR 0012, D4).
- Do not give an Actor DB credentials, hw-radar model imports, or canonical
  state. Do not enable residential proxies, browser escalation, or paid
  third-party Actors automatically (ADR 0021, ADR 0014).
- Do not treat the account-level Apify `max_monthly_usage_usd` as project
  admission. It is an owner backstop only (D3).
- Do not start any live Actor run before the owner gates in *Open risks* clear.
  Do not name Newegg as the Actor source (ToU exclusion, see risk R1).
- Do not model complete servers beyond basic-watch exact identity. Do not merge
  differently configured servers into one variant (D11, ADR 0022).
- Do not edit ADRs 0021/0022, the master spec, or the deferred MS-2a plan.

## Interfaces reused verbatim (do not reimplement)

- Adapter contract `SourceAdapter`, `DelistScope`, `DelistDetector`,
  `adapter_retention` (`src/hw_radar/acquisition/contracts.py:67-153`).
- Pipeline stages `_persist_all`, `_normalize`, `_apply_delist`,
  `_record_sweep_continuity`, `_classify_batch` (`src/hw_radar/acquisition/pipeline.py`).
  Persistence goes through `persist.upsert_listing` / `append_snapshot` / `store_raw`
  (`src/hw_radar/acquisition/persist.py:28-102`).
- Resolver `CatalogResolver.resolve_listing` and `_apply`
  (`src/hw_radar/matching/resolver.py:439-586`): the only edge-writing path.
- Ladder `decide()` and its value types (`src/hw_radar/matching/ladder.py:49-277`).
- Poller admission `check_admission` (`src/hw_radar/acquisition/scheduling/admission.py:32`)
  and the APScheduler build (`src/hw_radar/poller/service.py:304-396`).
- Retention helpers and classes (`src/hw_radar/catalog/models/base.py`).
- Eval harness `load_corpus` / `evaluate_corpus` / `build_report`
  (`src/hw_radar/matching/eval/`).

## Design decisions

Each decision is binding for this plan. It gives its evidence and the rejected
alternative. "Reopen if" names the evidence that would change it.

**MS2-D-01 — Milestone boundary.** MS-2 exits on a persisted eligibility verdict
plus a shortlist read model (query functions + a management command). It has no
UI and no alert.
- *Evidence:* master spec :935-944 (MS-2) and :952-956 (MS-4); settled D1.
- *Rejected:* adopting ADR 0022 confirmation #3's "exactly-one alert" as an MS-2
  gate. It spans MS-2..MS-4 (risk R10).

**MS2-D-02 — Category dispatch registry.**
- **Pure registry.** A pure module `hw_radar.matching.categories` maps category
  slug → `CategoryRules(slug, extract, extract_candidates, decode, veto)`. MS-2
  Slice A registers only `drive`, bound to the existing `vocab.extract`,
  `mpn.extract_candidates`, `grammars.decode`, and `ladder.contradictions`
  objects *by identity*.
- **Ladder veto.** `ladder.decide()` gains a keyword-only `veto` parameter that
  defaults to `contradictions`. It is used at all three veto sites
  (`ladder.py:161,196,225`).
- **Resolver spec readers.** The resolver keeps the ORM-bound spec readers in a
  parallel `_SPEC_READERS` table whose keys must equal the registry's. A test
  pins this.
- **Unregistered categories.** A registered-but-unknown category produces a
  `none` edge with `unsupported_category` evidence. Drive rules never run on it.
- **Edge provenance.** Every edge the dispatcher writes carries
  `evidence["category"]` and `evidence["category_source"]`
  (`hint | legacy_default`). This is the only additive change to persisted drive
  output.
- *Evidence:* Map Q2 (`decide()` touches drive semantics only via
  `contradictions()` and the grammar-gated rung 2).
- *Rejected (a):* a generic `Mapping[str, object]` veto payload. It loses typing
  and flattens drive semantics, which ADR 0022 forbids.
- *Rejected (b):* one resolver class per category. It duplicates `_apply`'s
  transactional edge rules.
- *Reopen if* a category needs a rung the ladder cannot express.

**MS2-D-03 — Category hint at the ingestion boundary.**
- **Contract field.** `ParsedListing.category_hint: str | None`, validated as a
  slug of at most 50 characters (the `Category.slug` shape). It is set by the
  collector from the *query scope it ran* (eBay `category_ids` sweep, Actor
  input scope), never from title text.
- **Persistence.** It is persisted under the reserved `OfferSnapshot.attrs_json`
  key `"category_hint"` only when non-null, so default rows are byte-identical.
  `attrs` may not carry the reserved key.
- **Resolver read.** The resolver reads it from the latest snapshot, like
  `_structured_mpn` (`resolver.py:127-137`).
- **Legacy default.** `None` ⇒ `drive`. All five MS-1 sources are drive-only by
  construction (Map Q7). Every multi-category source must hint every item
  (Slice F test).
- *Rejected (a):* a `SourceConfig` category column. eBay and Actor sources are
  multi-category per sweep, and it would need a migration in A.
- *Rejected (b):* a title classifier. It is unreliable.
- *Reopen if* evaluation at scale needs a queryable pre-resolution category.
  Promote the hint to a `Listing` column then.

**MS2-D-04 — Typed first-class spec satellites (Slice B).** Each satellite is 1:1
on `ProductModel`, `RetentionGoverned`, and carries the CHECK pair, like
`DriveSpec` (`identity.py:206-236`). Fields are the minimum that concrete v1 watch
filters need. `DriveSpec` is unchanged.

| Satellite | Field | Type | Justifying watch filter / trap |
| --- | --- | --- | --- |
| `gpu_spec` | `chip_vendor` | choices nvidia/amd/intel/other/unknown | "NVIDIA only" (CUDA). The model manufacturer is often the board partner, so chip vendor is distinct. |
| | `vram_gb` | PositiveSmallInteger null | Top GPU filter (e.g., ≥24 GB for local inference). |
| | `interface` | choices pcie/sxm/oam/mxm/other/unknown | Hard compatibility. SXM/OAM modules do not fit PCIe hosts, a common used-accelerator trap. |
| | `cooling` | choices active/passive/liquid/unknown | Passive datacenter cards need chassis airflow. Hard fit filter. |
| | `tdp_w` | PositiveSmallInteger null | Power-budget ceiling for homelab hosts. |
| `ram_spec` | `generation` | choices ddr3/ddr4/ddr5/other/unknown | Hard compatibility. |
| | `module_type` | choices udimm/rdimm/lrdimm/sodimm/other/unknown | Hard compatibility (RDIMM/UDIMM/LRDIMM do not mix). |
| | `ecc` | Boolean null | ECC hard filter (ECC UDIMMs exist, so it cannot be derived from type). |
| | `module_capacity_gb` | PositiveSmallInteger null | Per-module size. |
| | `modules_per_kit` | PositiveSmallInteger default 1 | Kit part numbers identify N modules. Total = N × size. |
| | `speed_mts` | PositiveInteger null | Minimum-speed filter. |
| | `ranks` | PositiveSmallInteger null | Server population rules (1R/2R/4R/8R). |
| `cpu_spec` | `socket` | CharField(32), normalized lowercase token | Hard compatibility. The vocabulary is open-ended, so it is a typed column validated by the category rules module, not a DB enum. |
| | `cores` | PositiveSmallInteger null | Minimum-cores filter. |
| | `tdp_w` | PositiveSmallInteger null | Power-budget ceiling. |

- Deliberately excluded: VRAM type, slot width, CAS latency, clocks, threads,
  microarchitecture, and platform-lock. None is a v1 hard filter; each can be
  added later as a new nullable column (NFR-006).
- Listing-level traps such as vendor-locked EPYC parts are offer evidence, not
  model spec.
- *Rejected:* JSON `spec_json`-driven filters. Watch-critical values must be typed
  columns (D10).

**MS2-D-05 — Conservative new-category matching (Slice B).**
- **Rungs.** GPU/RAM/CPU use rung 0 (prior) and rung 1 (exact alias) only. No
  MPN grammars (no rung 2); `decode` returns `None`. Attribute or fuzzy evidence
  never auto-accepts; it produces `review` (D9).
- **Auto-accept flag.** `CategoryRules.auto_accept: bool` is `True` for `drive`
  and `False` for `gpu`/`ram`/`cpu` at MS-2 merge. While `False`, a rung-1 hit
  yields `review` with evidence `auto_accept_disabled`. Flipping it requires an
  owner-ratified category corpus (risk R4).
- **Cross-category guard.** An alias hit or prior whose target family category ≠
  the dispatch category yields `review` with `cross_category` evidence (FR-003,
  no false cross-category merges).
- **Basic-watch categories** (`nic`, `hba`, `motherboard`, `server`) get rules
  with exact curated aliases only and no spec satellite. `server` sets
  `variant_on_demand=False` so configured systems never collapse into one
  variant (D11).
- *Rejected:* enabling GPU/RAM/CPU auto-accept on catalog-authoritative aliases
  at merge. No category corpus exists to prove precision.

**MS2-D-06 — Reference provenance (IR-007, Slice B).** GPU/RAM/CPU reference rows
arrive as curated seed documents in the existing refdata envelope
(`SeedDocument`/`SeedModel`/`SeedAlias`, `refdata/contracts.py:42-99`). The
envelope gets a category-discriminated spec payload and per-row
`source_url` + `retrieved_on`.
- `source_kind=catalog_authoritative` / `retention_class=manufacturer_reference`
  apply only to first-party manufacturer pages. Everything else is `manual`.
- `refdata.persist` becomes category-keyed. Its drive path is unchanged.
- *Rejected:* third-party spec aggregators as authoritative. Provenance and
  licence are unclear.

**MS2-D-07 — Watch/requirement schema and versioning (Slice C, migration 0020).**
- **`Watch`** (catalog app):
  - identity and scope: `name`, `category` FK, `enabled`, optional exact
    `target_family` or `target_model` (CHECK: at most one);
  - version: `requirement_version` PositiveInteger, bumped by the requirement
    service on every requirement edit;
  - hard offer clauses: `max_unit_price_usd` Decimal null,
    `allowed_conditions` ArrayField of `Condition` (empty = no constraint),
    `require_in_stock` bool, `allow_international` bool;
  - one soft threshold: `target_unit_price_usd` Decimal null.
- **Requirement satellites.** Each category has a typed 1:1 satellite:
  - `drive_requirement`: `min_capacity_tb`, `media_types[]`, `interfaces[]`,
    `form_factors[]`, `recording_techs[]`;
  - `gpu_requirement`: `min_vram_gb`, `chip_vendors[]`, `interfaces[]`,
    `coolings[]`, `max_tdp_w`;
  - `ram_requirement`: `generations[]`, `module_types[]`, `require_ecc` null,
    `min_total_capacity_gb`, `min_speed_mts`;
  - `cpu_requirement`: `sockets[]`, `min_cores`, `max_tdp_w`.

  Basic-watch watches have no satellite and must set `target_model` or
  `target_family`.
- **Input validation.** Per-category Pydantic `RequirementSpec` models validate
  input. The service is the only writer, and it enforces satellite category =
  watch category (a cross-table rule the DB cannot CHECK).
- **Hard vs soft.** Hard clauses decide the verdict. `target_unit_price_usd` only
  annotates shortlist rows (`meets_target`) and never changes a verdict. This is
  an inference from ADR 0022's eligibility-before-attractiveness split; see risk
  R11.
- **Naming.** The table is named `watch`. The per-(watch, listing) verdict table
  is `watch_evaluation`. The name `watch_match_state` stays reserved for the MS-4
  alert/dismiss state machine (mvp-web-ui research), avoiding a semantic collision.
- *Rejected (a):* one JSON requirement document per watch. Watch-critical values
  would sit in a JSON bag (D10).
- *Rejected (b):* a single wide requirement table. Nullable cross-category columns
  invite category-mismatched clauses.

**MS2-D-08 — Eligibility semantics (Slice C).**
- **Clause outcomes.** Each hard clause yields `match | no_match | unknown` plus a
  structured reason (`clause`, `outcome`, `evidence_tier`, `required`,
  `observed`, `detail`).
- **Product-attribute clauses** evaluate against **catalog-tier** evidence: the
  typed `*_spec` of the listing's accepted resolution at model/variant grain, or
  the family agreement set at family grain.
  - They yield `match` only from catalog-tier evidence.
  - They yield `no_match` from a catalog-tier contradiction, or from a listing-tier
    extracted attribute that positively contradicts, with extraction confidence
    ≥ the rules module's threshold.
  - Otherwise they are `unknown`. Unresolved or `review` listings are therefore
    `unknown` on product clauses.
- **Offer clauses** (price, condition, stock, international) evaluate against the
  latest `OfferSnapshot` / `Listing` facts. An unknown condition against a
  non-empty allow-list is `unknown`.
- **Aggregate.** Any `no_match` ⇒ `no_match`; else any `unknown` ⇒ `unknown`;
  else `match`. Delisted or expired listings are not evaluated.
- *Rejected:* allowing listing-title evidence to satisfy (`match`) a product
  clause. This is the silent-pass risk FR-014 forbids.

**MS2-D-09 — Evaluation persistence and the shortlist read model (Slice C).**
- **`watch_evaluation`**: unique `(watch, listing)` current state holding verdict,
  `reasons` JSON (output evidence per DR-004, not watch input),
  `requirement_version`, `evaluator_version`, `snapshot_observed_at`, and
  `evaluated_at`.
- **Retention.** It is `RetentionGoverned`, mirroring the listing's retention
  class/expiry at evaluation. `Listing.mark_delisted` pulls it forward like
  snapshots (`market.py:369-374`), and the purge registry covers it. eBay-derived
  reasons carry prices, so they must not outlive DR-008.
- **Trigger.** Evaluation runs as a pipeline stage after resolution, isolated
  like the resolver: a failure never blocks ingestion. It also runs from an
  `evaluate_watches` command after watch edits.
- **Read model.** `shortlist(watch_id)` returns `match` rows at the current
  requirement version for active listings, ordered by landed USD ascending, with
  freshness (`fresh | stale | budget_paused`) and `meets_target`.
  `review_queue(watch_id)` returns `unknown` rows.
- *Rejected:* an append-only evaluation history. DR-004 requires evidence per
  evaluated listing, not per evaluation. History can be added in MS-4 with the
  alert state machine.

**MS2-D-10 — Collection-provider seam (Slice A).**
- **Protocol.** `CollectionProvider` (in `contracts.py`) declares:
  - attributes `provider_kind`, `provider_key`, `site_key`, `run_kind`,
    `expects_json`;
  - methods `fetch`, `parse`, `delist_scope`, `run_evidence`.
- **Local adapter.** `LocalCollectionProvider(adapter)` delegates to today's
  `SourceAdapter` unchanged.
- **Runners.** `run_collection(provider, …)` holds today's `run_source` body.
  `run_source(adapter, …)` keeps its exact signature and delegates. Poller,
  heartbeat, and probe call sites are untouched.
- *Evidence:* Map Q3 (the only `fetch`/`parse` caller is `run_source`).
- *Rejected:* editing each adapter to a new base class. That touches five
  collectors for no behavior gain.

**MS2-D-11 — Run-evidence contract and completeness gate (Slice A).**
- **Enums.** `ProviderKind(local, apify)` and
  `RunCompleteness(complete, truncated, partial_failure, failed)` are
  `TextChoices` in `catalog/models/ops.py`. They need no migration until a field
  uses them.
- **Evidence model.** `ProviderRunEvidence` (frozen Pydantic,
  `extra="forbid"`) holds `evidence_version=1`, kind, key, completeness,
  non-empty reason, and `stale_absence_eligible`. The validator allows `True`
  only for `local`.
- **Gate.** `gate_delist_scope(scope, evidence)`:

  | Completeness | Result |
  | --- | --- |
  | `complete` | the scope, unchanged |
  | `truncated` | `replace(scope, complete=False)` if stale-eligible, else `None` |
  | `partial_failure` | `None` |
  | `failed` | `None` |

- **Continuity.** `counts_toward_sweep_continuity(evidence)` is true only for
  `complete` or stale-eligible `truncated`.
- **Local mapping.** An adapter scope with `complete=True` maps to `complete`. A
  scope with `complete=False`, or no scope at all, maps to stale-eligible
  `truncated`. The eBay path and non-delist sources therefore behave exactly as
  today.
- **Storage.** A persists evidence in `ScraperRun.detail_json["provider"]` (no
  migration). D adds the queryable `ProviderRun` table.
- *Rejected:* reducing the four-state taxonomy to the existing
  `DelistScope.complete` boolean. It keeps completeness an adapter honesty
  assumption (Map Q6a).

**MS2-D-12 — Scoped absence (Slice D, migration 0021).**
- **Fields.** `Listing.collection_scope` is a nullable CharField(100) written
  from `ParsedListing.collection_scope` at upsert; a null never overwrites a set
  value. `DelistScope.scope_key` is optional.
- **Candidate filter.** `_apply_delist` restricts candidates to
  `collection_scope = scope_key`, where a `None` key means `collection_scope IS
  NULL`. Today every listing is NULL and every scope is `None`, so behavior is
  unchanged.
- **Key format.** Scope keys are provider-independent:
  `"<site_key>:<category>:<query_id>"`. Local and Actor collection of the same
  sweep share a key.
- *Why:* a complete per-category sweep on a multi-category site would otherwise
  delist every other category's listings (Map Q6b).
- *Rejected:* one `SourceSite` per category sweep. It forks listing identity,
  violating D6.

**MS2-D-13 — Provider-run record and idempotency key (Slice D, migration 0021).**
- **Table.** `provider_run` holds:
  - identity: `provider_kind`, `source_site` FK, `external_run_id` (unique with
    kind), `import_idempotency_key` (unique, `apify:<run id>`), `actor_ref`,
    `build_id`, `build_number`, `contract_schema_version`;
  - requested scope: `query_scope` JSON (category/query/limits), plus typed
    `memory_mb`, `timeout_s`, `max_items`, `max_pages`, and `admission_class`;
  - lifecycle: `apify_status`, `started_at`, `finished_at`, `completeness`,
    `completeness_reason`, `usage_total_usd`;
  - import: `dataset_id`, `dataset_item_count`, `import_state`
    (`pending | importing | imported | rejected`), `scraper_run` OneToOne null,
    `dataset_deleted_at`.
- **Idempotent import:**
  1. The pipeline's durable persistence (`_persist_all`) and the transition to
     `imported` commit in **one transaction**.
  2. A replay that finds `imported` is a no-op before any fetch.
  3. `observed_at` is the run's `startedAt`, which is deterministic and never
     overstates freshness.
  4. Provider imports use insert-if-absent on `(listing_id, observed_at)`.
- **Retention.** The table holds no merchant content, so it is not
  retention-bearing (like `ScraperRun`). Raw dataset items land in `RawPayload`
  under the source's declared retention.
- *Rejected:* deduplicating by `RawPayload.content_hash`. It is not unique
  (Map Q5) and cannot express "this remote run was already imported".

**MS2-D-14 — Versioned Actor contract (Slice D, settled D7).**
- **hw-radar owns the contract.**
  - Pydantic models are the source of truth, with committed JSON Schemas
    generated from them. A test fails on drift.
  - `hw-radar-listing/v1` defines the dataset row: `schemaVersion`, `siteKey`,
    `sourceListingKey`, `url`, `title`, `price` (decimal string), `currency`,
    `shippingPrice`, `stockStatus`, `quantityAvailable`, `sellerName`,
    `conditionLabel`, `shipsFromCountry`, `categoryHint`, `collectionScope`,
    `mpn`, `observedAt`.
  - `hw-radar-run/v1` defines the default-KV `OUTPUT` record: `schemaVersion`,
    `status`, `completeness{complete, truncated, limitsHit{pages, requests, items,
    time}, pagesDeclared, pagesFetched, itemsDeclared, itemsEmitted}`,
    `queryScope` echo, and `errors[]`. It follows the `pdf-evidence-reader`
    status/truncated/coverage pattern.
- **Completeness mapping:**
  - `complete` requires all of: Apify `SUCCEEDED`, `OUTPUT.completeness.complete`,
    no limit hit, and `itemsEmitted` = dataset count.
  - `truncated` covers any limit hit or `TIMED-OUT`.
  - `partial_failure` covers `FAILED`/`ABORTED` with items, or `errors` non-empty.
  - `failed` covers a missing or invalid `OUTPUT`, an unknown `schemaVersion`, or
    no usable items.

  Missing or ambiguous evidence is never `complete` (DR-011). `failed` persists
  nothing.
- **Fixtures.** Frozen fixtures for each state live in hw-radar. The Actor PR in
  the separate repository copies the committed schemas.
- **Dataset deletion.** The default dataset is deleted after a durable import
  (retention).

**MS2-D-15 — Apify transport (Slice D).** Use a thin async `httpx` client over the
five REST calls needed: start run, get run, list dataset items, get KV record, and
delete dataset.
- `httpx` is already a dependency, and tests use `httpx.MockTransport` / vcrpy
  cassettes.
- *Rejected:* `apify-client` 3.2.0. It defaults to the `impit` transport, adds a
  dependency for five calls, and its return types changed across 2.x→3.x.
- *Reopen if* the needed surface grows beyond these calls.
- The token comes from `HW_RADAR_APIFY_TOKEN`. The owner scopes it to Run on the
  specific Actor(s).

**MS2-D-16 — Completion observation (settled D4).**
- **Poll job.** An APScheduler `apify-poll` interval job polls non-terminal
  `provider_run` rows via `GET /v2/actor-runs/{id}` and imports terminal ones.
- **Start job.** The existing full-lane job for a source with
  `collection_provider=apify` *starts* a run instead of calling `run_source`.
  There is one scheduling owner and no Apify schedules or webhooks.

**MS2-D-17 — Apify spend ledger (Slice E, migration 0022; settled D3).**
- **Period: a rolling 31-day window, not a calendar month or the billing cycle.**
  Every calendar month and every Apify usage cycle (≤31 days) lies inside some
  31-day window. Capping every window at $20 therefore caps both. It also needs
  no dependency on reading the account's cycle boundary.
  - *Rejected (a):* calendar month UTC. It can put up to 2× the cap inside one
    billing cycle that straddles a month boundary.
  - *Rejected (b):* the billing cycle read from the account limits endpoint.
    Admission would then depend on an extra API read, and the boundary drifts if
    the plan changes.
  - Cost of this choice: no "reset" on the 1st. Reports also show calendar-month
    and cycle views for attribution.
- **Reservation estimate.** Estimate before start:
  `memory_mb/1024 × timeout_s/3600 × usd_per_cu × (1 + margin) + per_run_overhead_usd`.
  - `usd_per_cu` defaults to 0.20 (official pricing, 2026-09-24) and is
    settings-tunable.
  - `margin` and `per_run_overhead_usd` are labeled assumptions covering storage
    and transfer.
  - This is a hard bound because self-owned Actors have no per-run $ cap.
- **Admission.** Under a Postgres transaction-scoped advisory lock, admit iff
  `reconciled_actual + outstanding_reservations + estimate ≤ class_limit`.
  - Class limits: `watch_refresh` → $20 hard; `discovery` → $12 operating
    target. Discovery degrades first.
  - Outstanding reservations, including stuck or unreconciled runs, count at
    their estimate. This fails closed.
  - Denials are recorded as ledger rows.
  - A kill switch `HW_RADAR_APIFY_ENABLED` (default false) denies everything.
- **Reconciliation.** Reconcile with `usage_total_usd` at terminal state, and
  re-read until non-null. If actual exceeds the estimate, keep the actual and log
  an overrun.
- **Freshness.** `budget_paused` holds for a source when its newest ledger event
  is a budget denial after its last imported run.
- *Rejected:* fusing with ADR-0016 `SearchBudgetGate` semantics. That gate is
  unbuilt and search-specific. Only the reserve-then-reconcile pattern is reused.

**MS2-D-18 — Per-source provider selection (Slice D, migration 0021).**
`SourceConfig.collection_provider` is choices `local | apify`, default `local`.
- A CHECK enforces `collection_provider='local' OR (heartbeat_enabled = false AND
  fast_lane = false)`. Heartbeat and fast-lane are local-only in MS-2.
- Switching the column never touches listings or watch state.
- The Actor reference per source is resolved from settings (env), not committed.

**MS2-D-19 — Pilot set and the Actor-proof source.** These are recommendations
only; the owner decides (settled D8 as revised).
- **Local paths:**
  - eBay Browse, extended to GPU (27386), RAM (170083), and CPU (164) `category_ids`
    sweeps, one ID per request, with pagination;
  - WD Recertified;
  - Seagate Recertified;
  - ServerPartDeals, whose non-drive collections are still unverified.
- **Actor proof:** owner decision (risk R1). Newegg is excluded because its Terms
  of Use restrict automated access. B&H, ServerPartDeals, and refurbished
  server-parts sellers each need a ToS/robots review before selection.
- **Slice D stays source-agnostic.** It is built and tested against the frozen
  contract with a synthetic fixture source.

## Requirement traceability

MS-2 acceptance bullets are labeled AC-1..AC-8 in master-spec order (:937-944).
Task IDs refer to the slices below.

| Requirement | Source | Slice/task | Proof (test or evidence) |
| --- | --- | --- | --- |
| MS-2 Task 1 — category dispatch, drive matcher intact | spec :927 | A1, A3 | `tests/db/test_ms2_decision_baseline.py` (syrupy baseline unchanged); `tests/unit/test_categories.py`; frozen `test_ladder.py`/`test_resolver.py`/`test_rung0_regression.py` green |
| MS-2 Task 2 — typed GPU/RAM/CPU specs + provenance | spec :928 | B1–B5 | `tests/db/test_category_specs.py` (CHECK pair, 1:1); `tests/unit/test_{gpu,ram,cpu}_rules.py`; `tests/db/test_refdata_categories.py` |
| MS-2 Task 3 — persisted requirements + evaluator | spec :929 | C1–C4 | `tests/unit/test_eligibility_*.py`; `tests/db/test_watch_evaluation.py` |
| MS-2 Task 4 — provider abstraction + one Apify adapter | spec :930 | A4–A6, D1–D8 | `tests/db/test_collection_provider.py`; `tests/unit/test_apify_contract.py`; `tests/db/test_apify_import.py` |
| MS-2 Task 5 — Apify budget admission/accounting | spec :931 | E1–E6 | `tests/unit/test_apify_budget.py`; `tests/db/test_apify_ledger.py` |
| MS-2 Task 6 — 3–5 pilot sources, measured | spec :932 | F1–F4 | `pilot_report` output recorded in STATUS; eBay category sweep tests |
| MS-2 Task 7 — self-owned Actor, observation-only | spec :933 | D1, F5 (owner-gated) | contract schema drift test; Actor PR review in the separate repo |
| AC-1 — HDD/SSD, GPU, RAM, CPU rows coexist, spine unchanged | spec :937 | B1, B2 | `test_category_specs.py::test_four_categories_coexist_on_one_spine`; `makemigrations --check` shows no spine change |
| AC-2 — match/no_match/unknown per first-class category | spec :938 | C3 | `tests/db/test_watch_evaluation.py` parametrized 4 categories × 3 verdicts |
| AC-3 — real-observation shortlist without score | spec :939 | C4, F6 (owner-gated) | `test_shortlist.py` (fixtures); F6 live evidence; `grep` proves no scoring import |
| AC-4 — local ↔ Actor switch keeps identity/history | spec :940 | D7 | `test_apify_import.py::test_provider_switch_preserves_identity_history_and_watch_state` |
| AC-5 — truncated Actor run cannot delist | spec :941 | A6, D8 | `test_collection_provider.py::test_truncated_remote_run_*`; `test_apify_import.py::test_truncated_actor_fixture_cannot_delist` |
| AC-6 — duplicate completion/import idempotent | spec :942 | D6 | `test_apify_import.py::test_duplicate_completion_is_noop`, `::test_crash_between_persist_and_mark_replays_once` |
| AC-7 — attributable cost; admission fails closed | spec :943 | E3–E6 | `test_apify_ledger.py` (attribution by source/provider; denial at limit; kill switch; outstanding counted) |
| AC-8 — full gate green | spec :944 | every slice | gate + `makemigrations --check` per commit |
| FR-001 — per-source provider choice, freshness SLO kept | spec :246 | D2 (MS2-D-18), F1 | `test_provider_selection.py` (CHECK, default local) |
| FR-003 — identity ladder, no false cross-category merges | spec :248 | A3, B3 | `test_resolver_dispatch.py::test_cross_category_alias_goes_to_review` (B) |
| FR-006 — eligibility reasons mandatory | spec :251 | C3 | every `watch_evaluation` row has non-empty `reasons` (DB test) |
| FR-014 — verdict persisted; unknown never passes | spec :259 | C2, C3 | `test_eligibility_aggregate.py` (unknown ≠ match); listing-tier evidence cannot `match` a product clause |
| NFR-003 — secrets via OpenBao | spec :267 | D3 | client reads `HW_RADAR_APIFY_TOKEN` only; test asserts no token in logs/detail_json |
| NFR-006 — new category = satellite + rows | spec :270 | B1, B2 | no change to spine models (migration test + diff) |
| IR-007 — provenance-bearing category references | spec :283 | B4 | seed validation rejects a row without `source_url`/`retrieved_on` |
| IR-008 — Actor provider evidence, idempotent, delist-safe, budgeted | spec :284 | D1–D8, E5 | as AC-4..AC-7 |
| DR-001 — retention CHECK pair on evidence | spec :290 | B1, C1 | constraint tests for `*_spec`, `watch_evaluation` |
| DR-003 — provider IDs never keys | spec :292 | D2 | `Listing` key unchanged; `external_run_id` only on `provider_run` |
| DR-004 — eligibility evidence persisted | spec :293 | C1, C3 | `watch_evaluation.reasons` schema test |
| DR-011 — remote run evidence fields; ambiguous ⇒ no delist; duplicate ⇒ no-op | spec :300 | A4, D1, D2, D6 | `provider_run` field test; mapping-table tests; idempotency tests |
| D-021 / §8.5 — provider ⟂ source; truncated ≠ absence; fail-closed spend | spec :398, :423-425 | A5, D7, E3 | as above |
| D-022 / §8.5 — hard vs soft; unknown never passes | spec :399, :426 | C1, C2 | `test_eligibility_soft_threshold.py` (target never changes verdict) |
| R-MS2-01 — first-class vs basic-watch | ADR 0022 :79 | B2, B3 | basic-watch rules exact-alias-only test |
| R-MS2-02 — servers not merged across configurations | ADR 0022 :115 | B3 | `server` rules: `variant_on_demand=False` test |
| R-MS2-03 — typed satellites, no EAV | ADR 0022 :111 | B1 | migration introspection test (typed columns) |
| R-MS2-04 — satellite fields unspecified (gap) | spec :469 | MS2-D-04 | decision table above |
| R-MS2-05 — spine not reopened | ADR 0010 :117-127 | B1 | no `ProductModel`/`ProductFamily` field change |
| R-MS2-06 — drive matcher is the pattern | spec :396 | A1, B3 | registry shape; new categories reuse `decide()` |
| R-MS2-07 — C.3 drive-specific; generalize around | spec :1216 | A1–A3 | drive rules registered by identity (`is`) |
| R-MS2-08 — resolver failure never blocks ingestion | spec :1216 | A3, C3 | unsupported-category path writes `none` edge; evaluator exception isolated (`test_pipeline_evaluator_failure_isolated`) |
| R-MS2-09 — watch schema gap | spec :110, :475 | MS2-D-07 | decision + C1 migration |
| R-MS2-10 — no universal cross-category score | ADR 0022 :93-95 | C4 | shortlist has no score field; ordering is price, never cross-category |
| R-MS2-11 — hard vs soft (gap) | spec :426, :598 | MS2-D-07 | soft-threshold test |
| R-MS2-12 — shortlist read model (gap) | spec :939, :948 | C4 | `shortlist()` / `review_queue()` tests |
| R-MS2-13 — per-run cost recorded; admission stops at budget | ADR 0021 :148 | E3–E6 | ledger tests |
| R-MS2-14 — ADR-0016 pattern reuse only | ADR 0016 :54-69 | MS2-D-17 | no `SearchBudgetGate` import (grep) |
| R-MS2-15 — Actor-side retention (gap) | — | D5 | dataset deleted after durable import; delete failure retried and logged |
| ADR 0021 — imports idempotent; completeness evidence; one scheduler | ADR 0021 :92-108 | D1, D5, D6 | as AC-5/AC-6; no Apify schedule (config review) |
| ADR 0012 amendment — polled completion, idempotent | ADR 0012 :94 | D5 | `test_apify_poll_job.py` |

## Slice order and migration assignment

| Slice | PR scope | Migrations | Depends on | Why this position |
| --- | --- | --- | --- | --- |
| **A** | Seams: category registry + hint; provider seam + run evidence + completeness gate | none | — | Every later slice plugs into these seams. Behavior-preserving, so it is safe to land first. |
| **B** | GPU/RAM/CPU satellites, category rows, rules, reference seeds, cross-category guard | `0018_category_spec_satellites`, `0019_seed_categories` | A | C's product clauses read the satellites. |
| **C** | Watch + requirement satellites + evaluator + `watch_evaluation` + shortlist read model | `0020_watch_requirements` | B | D's provider-switch test must prove watch state survives (AC-4). |
| **D** | Apify provider (source-agnostic), contract, `provider_run`, idempotent import, scoped absence, provider selection. Production admission = deny-all | `0021_provider_runs` | A, C | Live runs are impossible until E replaces deny-all. D is fail-closed by construction. |
| **E** | Spend ledger, admission, reconcile, `budget_paused`, spend report | `0022_apify_spend_ledger` | D | Reservations attach to `provider_run`. |
| **F** | Pilot sources (eBay category sweeps, SPD check), measurement, owner-gated Actor proof, end-to-end evidence | none planned | B–E | Integration and measurement last (ADR 0022 "measure before breadth"). |

B and D touch disjoint code and may be developed in parallel worktrees. They merge
in the order above; if D merges first, renumber per Global constraints. Every
slice leaves `dev` deployable with all sources still disabled.

## Slice A — Seams (behavior-preserving, no migration)

**Scope:** MS2-D-02, -03, -10, -11. No schema change, no drive decision change, no
adapter edit.

**Files created:**
- `src/hw_radar/matching/categories.py`
- `src/hw_radar/acquisition/providers.py`
- `tests/unit/test_categories.py`
- `tests/unit/test_ladder_veto.py`
- `tests/unit/test_contracts_ms2.py`
- `tests/unit/test_provider_evidence.py`
- `tests/db/test_ms2_decision_baseline.py` (+ its `__snapshots__/`)
- `tests/db/test_resolver_dispatch.py`
- `tests/db/test_persist_category_hint.py`
- `tests/db/test_collection_provider.py`

**Files changed:**
- `src/hw_radar/matching/ladder.py`
- `src/hw_radar/matching/resolver.py`
- `src/hw_radar/acquisition/contracts.py`
- `src/hw_radar/acquisition/persist.py`
- `src/hw_radar/acquisition/pipeline.py`
- `src/hw_radar/catalog/models/ops.py`
- `src/hw_radar/catalog/models/__init__.py` (exports only)

**Frozen for this slice:** every existing file under `tests/`, and
`src/hw_radar/acquisition/sources/*`, `heartbeat.py`, `poller/service.py`,
`matching/{vocab,mpn,types}.py`, and `matching/grammars/*`.

Focused test command pattern (DB tests need the dev DB; one pytest process at a
time):
`HW_RADAR_DB_PORT=5433 uv run pytest <test path> -q`.

### A0 — Characterization baseline (on unmodified code)

1. Create `tests/db/test_ms2_decision_baseline.py` with
   `test_synthetic_corpus_decisions_baseline(snapshot)` (syrupy, already a dev
   dependency):
   - Load `tests/fixtures/matching_corpus/synthetic.jsonl` + `.meta.json` via
     `load_corpus` / `load_meta`.
   - Seed the catalog with the `seeded_catalog` fixture from
     `tests/db/test_ratification_corpus.py:152-189`. Import the fixture, or copy
     it and its `_manufacturer`/`_seed_model` helpers verbatim into the new file.
     Do not edit the original.
   - Inside a rolled-back atomic block, call `evaluate_corpus(entries, meta)`.
   - Assert `snapshot == [(p.entry_id, p.outcome, p.grain, p.rung,
     p.manufacturer_key, p.family_norm, p.model_norm, p.variant_tuple) for p in
     predictions]` (`Prediction`, `src/hw_radar/matching/eval/evaluate.py:61-78`).
2. Add `test_ladder_golden_baseline(snapshot)`. It runs every
   `ladder.decide(...)` input tuple used in `tests/unit/test_ladder.py`
   (re-declared locally) and snapshots the `Verdict` fields `outcome`, `grain`,
   `rung`, `method`, `target`, `confidence`, and `sorted(evidence)`.
3. Run `HW_RADAR_DB_PORT=5433 uv run pytest tests/db/test_ms2_decision_baseline.py --snapshot-update -q`
   **on the unmodified base**, then run it again without `--snapshot-update`. It
   should be green.
4. Commit: `test(ms2): pin drive decision baseline before category dispatch`.

This is the before/after oracle for the whole slice. No later Slice A commit may
run `--snapshot-update`.

### A1 — Category registry + ladder veto injection

1. **RED.** `tests/unit/test_ladder_veto.py`:
   - `test_custom_veto_is_consulted_at_rung1`: a rung-1 viable single-target hit
     that would ACCEPT under the default. With `veto=lambda e, h: ["probe"]` it
     returns `Outcome.REVIEW`, `rung=1`, and `evidence["veto"] == ["probe"]`.
   - `test_custom_veto_is_consulted_at_rung0_prior` and
     `test_custom_veto_filters_oem_fanout`: the same probe at the other two
     sites.
   - `test_default_veto_is_contradictions`:
     `inspect.signature(ladder.decide).parameters["veto"].default is ladder.contradictions`.

   Fails: `decide()` has no `veto` parameter (TypeError).
2. **GREEN.** In `ladder.py`:
   - Add `type Veto = Callable[[ExtractedAttributes, HardAttrs], list[str]]`.
   - Add `*, veto: Veto = contradictions` to `decide`.
   - Replace the three `contradictions(` calls inside `decide` with `veto(`. Leave
     the rung-2 capacity check as is.
3. **RED.** `tests/unit/test_categories.py`:
   - `test_drive_rules_bind_existing_functions_by_identity`: `rules_for("drive")`
     has `extract is vocab.extract`,
     `extract_candidates is mpn.extract_candidates`, `decode is grammars.decode`,
     and `veto is ladder.contradictions`.
   - `test_registered_categories_is_drive_only_in_slice_a`.
   - `test_dispatch_category_none_is_legacy_drive`.
   - `test_dispatch_category_passes_hint_through`.
   - `test_rules_for_unregistered_is_none`.
   - `test_invalid_slug_rejected`: `dispatch_category("GPU!")` raises
     `ValueError`.

   Fails: module missing.
4. **GREEN.** Create `src/hw_radar/matching/categories.py` (pure, no ORM). The
   **binding** surface:

   ```python
   DRIVE: Final = "drive"
   LEGACY_DEFAULT_CATEGORY: Final = DRIVE
   CATEGORY_SLUG_RE: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

   class CandidateExtractor(Protocol):
       def __call__(self, title: str, *, structured_mpn: str | None = None,
                    source_key: str = "") -> list[MpnCandidate]: ...

   @dataclass(frozen=True)
   class CategoryRules:
       slug: str
       extract: Callable[[str], ExtractedAttributes]
       extract_candidates: CandidateExtractor
       decode: Callable[[str], DecodeResult | None]
       veto: ladder.Veto

   def rules_for(slug: str) -> CategoryRules | None: ...
   def registered_categories() -> frozenset[str]: ...
   def dispatch_category(hint: str | None) -> str: ...  # None -> DRIVE; validates slug
   ```

   Module docstring: record the legacy-default trap (MS2-D-03) and that the
   ORM-bound spec readers live in `resolver._SPEC_READERS`.
5. Run `uv run pytest tests/unit/test_categories.py tests/unit/test_ladder_veto.py tests/unit/test_ladder.py -q`,
   then the full gate. Commit:
   `feat(matching): add category rules registry and injectable ladder veto`.

### A2 — Category hint on the ingestion contract

1. **RED.** `tests/unit/test_contracts_ms2.py`:
   - `test_category_hint_defaults_to_none`.
   - `test_category_hint_accepts_slug` (`"gpu"`, `"basic-watch"`).
   - `test_category_hint_rejects_non_slug` (`"GPU"`, `"gpu card"`, and a
     51-character string).
   - `test_attrs_may_not_carry_reserved_category_hint_key`: `ParsedListing(...,
     attrs={"category_hint": "gpu"})` raises `ValidationError`.
   - `test_normalized_listing_inherits_hint`.

   Fails: unknown field / no validation.
2. **GREEN.** In `contracts.py`:
   - Add `CATEGORY_HINT_ATTR: Final = "category_hint"`.
   - Add `ParsedListing.category_hint: str | None = Field(default=None, max_length=50, pattern=CATEGORY_SLUG_RE.pattern)`.
     Import the pattern constant from `hw_radar.matching.categories`. That
     module is pure Python and imports no Django or acquisition module, so no
     import cycle arises.
   - Add a `model_validator(mode="after")` that rejects `CATEGORY_HINT_ATTR` in
     `attrs`.
3. **RED.** `tests/db/test_persist_category_hint.py`:
   - `test_snapshot_attrs_unchanged_without_hint`: `append_snapshot` with no hint
     writes `attrs_json == dict(record.attrs)` exactly.
   - `test_snapshot_attrs_carry_hint_when_set`: with `category_hint="gpu"`, it
     writes `{**attrs, "category_hint": "gpu"}`.
4. **GREEN.** In `persist.append_snapshot`, merge the hint under
   `CATEGORY_HINT_ATTR` only when non-null.
5. Run the focused tests plus the frozen `tests/db/test_persist.py`,
   `tests/db/test_ratification_corpus.py`, and `tests/unit/test_contracts.py`,
   then the gate. Commit:
   `feat(acquisition): add optional category hint to the listing contract`.

### A3 — Resolver category dispatch

1. **RED.** `tests/db/test_resolver_dispatch.py`. Define local fixtures mirroring
   `tests/db/test_resolver.py:33-121`; do not import from or edit that file.
   Create snapshots with `persist.append_snapshot` using a `NormalizedListing`
   (`fx_rate=1`, `fx_pair="USD/USD"`, `fx_source="identity"`).
   - `test_no_hint_resolves_as_drive_and_records_provenance`: the ST16000NM001G
     title with no snapshot yields the same grain/method/rung as the frozen
     `test_rung1_parity_*` case, plus `evidence["category"] == "drive"` and
     `evidence["category_source"] == "legacy_default"`.
   - `test_explicit_drive_hint_is_decision_identical`: the same listing with
     `category_hint="drive"` produces identical
     `(outcome, grain, rung, method, targets)`, with `category_source == "hint"`.
   - `test_unregistered_hint_writes_none_edge_and_never_runs_drive_rules`:
     `category_hint="gpu"` on a drive-shaped title that would rung-1 accept
     yields a current edge `grain=none` with
     `evidence["unsupported_category"] is True` and `evidence["category"] == "gpu"`.
     `Listing.product_model` stays None. The edge evidence has no
     `mpn_hypothesis` key, which `ladder.decide()` always writes
     (`ladder.py:154`). That proves the drive ladder never ran. A monkeypatch of
     `vocab.extract` would prove nothing, because the registry binds the function
     object at import.
   - `test_rung2_provisional_family_uses_dispatch_category`: the
     `test_rung2_decode_*` title makes a family whose `category.slug == "drive"`.
   - `test_spec_readers_match_registry`:
     `set(resolver._SPEC_READERS) == categories.registered_categories()`.
   - `test_unsupported_repoll_does_not_spam_edges`: resolve twice yields one edge.

   Fails: no dispatch/evidence keys, and "gpu" currently resolves as drive.
2. **GREEN.** In `resolver.py`:
   - Add a frozen `_SpecReader(model_attrs, family_attrs)` and `_SPEC_READERS`
     with `{DRIVE: _SpecReader(lambda m: _hard_attrs_from_spec(_spec_of(m)), _family_agreement_attrs)}`.
   - Add `_category_hint(listing) -> str | None` from the latest snapshot's
     `attrs_json`. It may share one query with `_structured_mpn`; the returned
     values must be identical.
   - Thread the reader through `_prior_from_listing(listing, spec)` and
     `_alias_hits(candidates, source_site_id, spec)`, and the decoder through
     `_first_decode(candidates, decode)`.
   - `_run_ladder` resolves `slug = categories.dispatch_category(hint)`. For an
     unregistered slug it returns the canonical title,
     `ExtractedAttributes()`, `[]`, and a `Verdict(Outcome.NONE, Grain.NONE,
     evidence={"category": slug, "category_source": ..., "unsupported_category": True})`.
     Otherwise it calls `rules.extract` / `rules.extract_candidates` /
     `rules.decode` and passes `veto=rules.veto` to `ladder.decide`, then adds
     the two provenance keys to `verdict.evidence`.
   - Add `category_slug: str | None = None` to `ladder.TargetRef`. `_run_ladder`
     stamps it via `dataclasses.replace` when `target.family_key` is set.
   - `_materialize` uses `Category.objects.get(slug=target.category_slug)` and
     raises `ValueError` if `family_key` is set without a slug. That routes to the
     existing error-edge fallback, never a silent drive default.
   - `_materialize`'s signature is unchanged; the frozen monkeypatch test at
     `test_resolver.py:360` depends on it.
   - Do not bump `MATCHER_VERSION`: no rule changed.
3. Run the focused tests, then the frozen `tests/unit/test_ladder.py`,
   `tests/db/test_resolver.py`, `tests/db/test_rung0_regression.py`,
   `tests/db/test_ratification_corpus.py`, and `tests/db/test_ms2_decision_baseline.py`
   (baseline must be unchanged). Then run the gate. Commit:
   `feat(matching): dispatch resolution by category with drive as legacy default`.

### A4 — Provider run-evidence contract + completeness gate (pure)

1. **RED.** `tests/unit/test_provider_evidence.py`:
   - `test_completeness_values_are_the_adr0021_taxonomy`:
     `set(RunCompleteness.values) == {"complete","truncated","partial_failure","failed"}`.
   - `test_evidence_is_frozen_and_forbids_extra`.
   - `test_empty_reason_rejected`.
   - `test_remote_provider_cannot_be_stale_absence_eligible`: `APIFY` +
     `stale_absence_eligible=True` raises `ValidationError`.
   - A parametrized `test_gate_delist_scope` over 4 completeness values × 2
     eligibility values × `scope.complete ∈ {True, False}`. Expected:
     - `complete` → the input scope, unchanged;
     - `truncated` + eligible → a scope with `complete is False` and the other
       fields equal;
     - `truncated` + ineligible → `None`;
     - `partial_failure` and `failed` → `None`.
   - `test_gate_passes_none_scope_through`.
   - `test_continuity_counts_only_complete_or_eligible_truncated`.

   Fails: names missing.
2. **GREEN:**
   - In `catalog/models/ops.py`, add `ProviderKind` and `RunCompleteness`
     `TextChoices` and export them.
   - In `contracts.py`, add `ProviderRunEvidence` (MS2-D-11 fields,
     `ConfigDict(frozen=True, extra="forbid")`, the validator above).
   - In the new `acquisition/providers.py`, add `gate_delist_scope(scope:
     DelistScope | None, evidence: ProviderRunEvidence) -> DelistScope | None`
     and `counts_toward_sweep_continuity(evidence) -> bool`. Each docstring
     states the ADR-0021/D5 invariant ("only `complete` may keep
     `complete=True`; remote runs never reach stale absence").
3. Run `HW_RADAR_DB_PORT=5433 uv run python manage.py makemigrations --check --dry-run`.
   It must report no changes, because the choices are unused by any field.
   Then run the gate. Commit:
   `feat(acquisition): add provider run-evidence contract and completeness gate`.

### A5 — CollectionProvider seam + LocalCollectionProvider

1. **RED.** Add to `tests/unit/test_provider_evidence.py`, using in-memory fake
   adapters:
   - `test_local_provider_delegates_fetch_and_parse`.
   - `test_local_evidence_complete_when_adapter_proves_complete`.
   - `test_local_evidence_truncated_eligible_when_adapter_sweep_incomplete`.
   - `test_local_evidence_truncated_eligible_when_adapter_has_no_delist_detector`
     (reason `completeness_not_asserted`).
   - `test_local_evidence_for_non_full_run_records_absence_not_evaluated`.
   - `test_local_provider_satisfies_protocol`: `provider: CollectionProvider =
     LocalCollectionProvider(adapter)` type-checks under basedpyright.

   Then `tests/db/test_collection_provider.py::test_run_source_records_local_provider_evidence`:
   a FakeAdapter run on the seeded `demo` site stores
   `run.detail_json["provider"]` with `{"evidence_version": 1, "provider_kind":
   "local", "provider_key": "local", "completeness": "truncated",
   "completeness_reason": "completeness_not_asserted", "stale_absence_eligible": true}`.
   The existing keys `body_bytes`, `resolver_errors`, `grain_counts`, and
   `listings_delisted` keep their prior semantics.
2. **GREEN:**
   - In `contracts.py`, add the `CollectionProvider` Protocol (MS2-D-10, with
     `run_evidence(batch, parsed, scope, *, run_kind)`).
   - In `providers.py`, add `LocalCollectionProvider` and the reason constants.
   - In `pipeline.py`:
     - Move the body of `run_source` to `run_collection(provider, resolver, *,
       retention_class, expires_policy, run_kind, fetch_timeout_s)`.
     - `run_source(adapter, resolver, **kwargs)` returns `await
       run_collection(LocalCollectionProvider(adapter), resolver, ...)`, and its
       signature is unchanged.
     - The FULL-run delist stage becomes: `scope = provider.delist_scope(batch,
       parsed)`, then `evidence = provider.run_evidence(...)`. Continuity is
       recorded only if `counts_toward_sweep_continuity(evidence)`, and
       `_apply_delist` runs on `gate_delist_scope(scope, evidence)` when that is
       not None.
     - Non-FULL runs compute evidence with `scope=None`.
     - Write `detail_json["provider"] = evidence.model_dump(mode="json")`.
   - Keep `_classify_batch` on `provider.expects_json`, and keep the
     zero-record parser-rot guard.
   - One documented difference: continuity is now recorded after
     `delist_scope()`. If `delist_scope` raises, the run fails as before and no
     longer advances `continuous_since`. That is strictly more conservative.
     Write it in the delist-stage comment.
3. Run the frozen `tests/db/test_pipeline.py`, `test_pipeline_demo.py`,
   `test_source_{ebay,goharddrive,seagate,serverpartdeals,wd}.py`,
   `test_poller_jobs.py`, `test_poller_heartbeat.py`,
   `test_poller_retention_wiring.py`, `test_ms1_acceptance.py`, and
   `tests/unit/test_poller*.py`, one process at a time. Then run the gate.
   Commit: `refactor(acquisition): route collection through a provider seam`.

### A6 — Truncated or failed provider results cannot delist (DB proof)

1. **RED.** In `tests/db/test_collection_provider.py`, use a
   `FakeRemoteProvider(provider_kind=APIFY, site_key="demo")` whose
   `delist_scope` **claims** `complete=True` and whose `run_evidence` returns a
   configurable completeness. Seed an active listing `k-old` on `demo` with
   `last_seen` well past any grace (queryset `.update`), and the lane with an old
   `continuous_since`. The batch parses only `k-new`.
   - `test_truncated_remote_run_claiming_complete_scope_cannot_delist`: `k-old`
     is still active and `detail_json["listings_delisted"] == 0`.
   - `test_partial_failure_and_failed_remote_runs_cannot_delist`: parametrized,
     same assertions.
   - `test_truncated_remote_run_does_not_advance_continuity`: with
     `continuous_since=None` before the run, it is still `None` after.
   - `test_complete_remote_run_delists_absent_listing` (positive control): `k-old`
     is delisted with `DelistReason.ABSENT_FROM_SWEEP`.
   - `test_local_incomplete_scope_keeps_stale_absence_path`: a local fake adapter
     implements `DelistDetector` with `complete=False` and a grace shorter than
     continuity. `k-old` is delisted as `ABSENT_STALE`, mirroring eBay
     `test_source_ebay.py:356-365`.

   Expected: green once A5 is wired correctly. The positive and negative controls
   prove the gate is live. If any negative case fails, fix the wiring. Never
   weaken the test.
2. Commit: `test(acquisition): prove non-complete provider runs cannot delist`.

### A7 — Slice close-out

1. Full gate plus `makemigrations --check --dry-run`, both green.
   `git diff --stat <slice-base> -- tests/unit/test_ladder.py tests/db/test_resolver.py tests/db/test_rung0_regression.py tests/db/test_ratification_corpus.py tests/db/test_pipeline.py tests/db/test_pipeline_demo.py tests/db/test_listing_delist.py tests/db/test_source_*.py tests/db/test_poller_*.py tests/db/test_ms1_acceptance.py src/hw_radar/acquisition/sources src/hw_radar/acquisition/heartbeat.py src/hw_radar/poller`
   shows no changes.
2. Update `docs/TODO.md` (Slice A done; Slice B next) and the `docs/STATUS.md`
   focus line. Commit `docs: record MS-2 slice A completion`.

**Slice A acceptance:**
- The syrupy baseline is unchanged.
- All frozen regression files pass unmodified.
- Truncated, partial, and failed provider results cannot delist or advance
  continuity; complete ones can.
- An unregistered category never runs drive rules.
- There is no migration.

**Exit evidence:**
- gate output with the test count (≥ 552 + new);
- the empty frozen-file diff;
- the commit list.

## Slice B — First-class category specs and rules

**Scope:** MS2-D-04, -05, -06.

**Files:**
- `catalog/models/identity.py` (satellites + choices);
- migrations `0018`, `0019`;
- `matching/categories.py` (register gpu/ram/cpu + basic-watch);
- new `matching/rules/{gpu,ram,cpu,basic}.py`;
- `matching/resolver.py` (spec readers, cross-category guard, `auto_accept`,
  `variant_on_demand`);
- `refdata/{contracts,persist}.py` (category-discriminated spec payload);
- `refdata/seeds/`;
- `catalog/admin.py`.

- **B1 — Satellites (0018).** Add `GpuSpec`, `RamSpec`, and `CpuSpec` per the
  MS2-D-04 table, each with `retention_constraints("<table>")` and
  `retention_indexes("<table>_expires")`.
  - Tests: `tests/db/test_category_specs.py` covers the CHECK pair (empty class
    rejected; bounded without expiry rejected), 1:1 uniqueness, and
    `test_four_categories_coexist_on_one_spine` (AC-1).
  - The migration applies to an empty DB and to a DB holding drive rows; the
    drive-row counts are unchanged.
- **B2 — Category rows (0019, data).** `get_or_create` the rows `gpu`, `ram`,
  `cpu`, `nic`, `hba`, `motherboard`, and `server`. `drive` already exists from
  `0001:7-13`. The reverse deletes only rows with no families.
  - Tests: forward and reverse migration.
- **B3 — Rules.**
  - **Extractors.** Pure `extract`/`extract_candidates`/`veto` per category. The
    typed per-category attribute payload rides on `ExtractedAttributes` as a new
    optional `category_attrs` field (default None). The typed catalog payload
    rides on `HardAttrs` as a new optional `category` field. The drive fields
    and the drive `contradictions()` are untouched.
  - **Brands and part numbers.** Brand tables are category-local.
  - **Offer terms.** Condition, packaging, and warranty extraction reuse
    `vocab`'s tables through a new public helper. `vocab.extract` output must
    stay identical; the A0 baseline guards this.
  - **Rungs.** Rung 1 only. The `auto_accept=False` → `review`
    (`auto_accept_disabled`) behavior applies to gpu/ram/cpu. The cross-category
    guard applies to prior and alias targets.
  - **Basic-watch.** Basic-watch rules are exact-alias-only; `server` sets
    `variant_on_demand=False`.
  - **Version.** Bump `MATCHER_VERSION` because rules were added. The drive
    baseline snapshot must still match (decisions, not the version string).
  - Tests: `tests/unit/test_{gpu,ram,cpu}_rules.py` (extraction/veto tables),
    `tests/db/test_resolver_categories.py` (rung-1 review while auto-accept is
    off, cross-category review, server no-variant), and the A0 baseline.
- **B4 — Reference seeds.** Make the refdata envelope category-discriminated;
  drive seed documents stay valid unchanged. Every non-drive row requires
  `source_url` + `retrieved_on`. `catalog_authoritative` applies only to
  first-party hosts listed in the seed document's provenance block.
  - Author a small curated seed per category (a handful of models each, enough
    for C's representative cases) from first-party manufacturer pages, recording
    the URL and date.
  - Tests: `tests/db/test_refdata_categories.py`, plus the frozen
    `test_refdata_*` suites green.
- **B5 — Admin + close-out.** Register the satellites in Django admin, then run
  the gate and update TODO/STATUS.

**Acceptance:**
- AC-1 holds.
- GPU/RAM/CPU exact-alias hits reach `review` while auto-accept is off.
- Cross-category hits never auto-accept.
- The drive baseline is unchanged.

**Exit evidence:** migration apply/rollback transcript on a DB holding drive data;
gate.

## Slice C — Watches, eligibility, shortlist read model

**Scope:** MS2-D-07, -08, -09.

**Files:**
- new `catalog/models/watch.py`;
- migration `0020`;
- new package `hw_radar/eligibility/` (`requirements.py`, `evaluate.py`,
  `shortlist.py`, `service.py`);
- `acquisition/pipeline.py` (post-resolution evaluation stage, isolated);
- `catalog/models/market.py` (`mark_delisted` pull-forward for evaluations);
- the purge registry;
- new management commands `evaluate_watches` and `show_shortlist`.

- **C1 — Schema (0020).** Add `Watch`, the four requirement satellites
  (ArrayFields of the satellite choices), and `WatchEvaluation`
  (`RetentionGoverned`, CHECK pair, unique `(watch, listing)`, index
  `(watch, verdict)`).
  - Tests: constraints, including the at-most-one target CHECK and the retention
    pair; also the migration test.
- **C2 — Requirement service.** Per-category Pydantic `RequirementSpec`s back
  `save_requirement(watch, spec)`. It is the single writer: it enforces category
  = satellite, bumps `requirement_version`, and requires a target for
  basic-watch categories.
  - Tests: validation matrix; version bump on each edit; no bump on a no-op save.
- **C3 — Evaluator.** Implement `evaluate_listing(listing_id)` for all enabled
  watches of the dispatch category, with a clause registry per category and
  clause/aggregate semantics per MS2-D-08.
  - It writes or updates `WatchEvaluation`, mirroring listing retention.
  - It runs in the pipeline after resolve via a `ListingEvaluator` protocol
    (`NullEvaluator` default in `run_collection`; the poller passes the real one).
    Failures are counted in `detail_json["evaluator_errors"]` and never block
    ingestion.
  - Tests:
    - `tests/unit/test_eligibility_clauses.py`: per-clause tri-state tables,
      including listing-tier contradiction ⇒ `no_match`, listing-tier agreement
      ⇏ `match`, and review-resolution ⇒ `unknown`.
    - `tests/unit/test_eligibility_aggregate.py`.
    - `tests/db/test_watch_evaluation.py` (AC-2): a parametrized drive/gpu/ram/cpu
      × match/no_match/unknown set built on B's curated seeds and accepted
      resolutions.
    - Soft-threshold test.
    - Delist pull-forward and purge-registry test.
    - `test_pipeline_evaluator_failure_isolated`.
- **C4 — Read model.** Implement `shortlist(watch_id)` and
  `review_queue(watch_id)` with freshness `fresh | stale` (stale = latest
  snapshot older than 2 × the source's `cadence_baseline_s`; a labeled
  assumption, tunable). E adds `budget_paused`. Add the `show_shortlist`
  command.
  - Tests: ordering by landed USD; only the current requirement version is
    included; delisted and expired rows are excluded; the result has no score
    field; a `grep` guard asserts no import of any scoring module.
- **C5 — Close-out:** gate; TODO/STATUS.

**Acceptance:**
- AC-2 holds.
- The shortlist runs on fixture observations with no ADR-0011 artifact (AC-3's
  code half).
- Evaluator failures are isolated.

## Slice D — Apify provider (source-agnostic)

**Scope:** MS2-D-12…-16, -18.

**Files:**
- new `acquisition/apify/{client,contract,provider,jobs}.py`;
- committed schemas `acquisition/apify/schemas/hw-radar-{listing,run}-v1.schema.json`;
- `tests/fixtures/apify_contract/v1/*`;
- `catalog/models/provider.py` (`ProviderRun`);
- migration `0021`;
- `contracts.py` (`ParsedListing.collection_scope`, `DelistScope.scope_key`);
- `persist.py` (scope write; insert-if-absent snapshot for provider imports);
- `pipeline.py` (scope-filtered `_apply_delist`; transactional import hook);
- `poller/service.py` (apify start branch + `apify-poll` job).

- **D1 — Contract.**
  - Add the Pydantic models, committed JSON Schemas, and `classify_run(apify_status,
    output, dataset_count) -> (RunCompleteness, reason)` per the MS2-D-14 mapping.
  - Add frozen fixtures: complete, truncated-by-pages, timed-out,
    partial-with-errors, failed-with-items, missing-OUTPUT, unknown-schema, and
    count-mismatch.
  - Tests: `tests/unit/test_apify_contract.py` covers the schema-drift guard
    (`Model.model_json_schema() == committed file`), the mapping table over every
    fixture, and "unknown or missing ⇒ never complete".
- **D2 — Schema (0021).**
  - Add `ProviderRun`; `SourceConfig.collection_provider` + CHECK;
    `Listing.collection_scope` + index.
  - `_apply_delist` scope filter (None ⇔ NULL).
  - Tests: CHECK, uniqueness of `(provider_kind, external_run_id)` and of the
    idempotency key, and `test_scoped_complete_sweep_cannot_delist_other_scopes`.
    Frozen eBay delist tests stay green.
- **D3 — Client.** Add a thin `httpx.AsyncClient` wrapper for the five calls,
  with dataset pagination and the token from `HW_RADAR_APIFY_TOKEN`.
  - Tests: `httpx.MockTransport`; the token never appears in logs, errors, or
    `detail_json`.
  - Verify live field names (`usageTotalUsd`, `defaultDatasetId`,
    `defaultKeyValueStoreId`, `startedAt`, `finishedAt`, `buildId`,
    `buildNumber`) against the official API reference before merge. They are
    flagged unconfirmed in the prep evidence.
- **D4 — Import provider.** Add `ApifyImportProvider(provider_run)` as a
  `CollectionProvider` of kind `apify`:
  - `fetch` reads the dataset + `OUTPUT` into a `RawBatch` with
    `fetched_at=startedAt`;
  - `parse` validates rows, rejects `siteKey` ≠ the run's site, and carries
    `category_hint`/`collection_scope`;
  - `delist_scope` returns a scope only for `complete`, with `scope_key`;
  - `run_evidence` comes from `classify_run` and is never stale-eligible.
- **D5 — Jobs.**
  - The start job runs through `check_admission`, then a `BudgetAdmission`
    protocol. Its production binding is `DenyAllAdmission`, so live starts are
    impossible until E.
  - The `apify-poll` interval job moves terminal runs to import:
    `select_for_update` claim → `run_collection` with the persistence
    transaction hook → `imported` → delete the dataset (failure logged and
    retried).
  - Actor failure maps to ADR-0017 lifecycle events via the existing
    classification.
  - Tests: `tests/db/test_apify_poll_job.py`.
- **D6 — Idempotency (AC-6).**
  - `test_duplicate_completion_is_noop`: a second import of the same run adds no
    `ScraperRun`, `OfferSnapshot`, or `RawPayload` rows.
  - `test_crash_between_persist_and_mark_replays_once`.
  - `test_replayed_dataset_read_does_not_duplicate_snapshots`.
- **D7 — Provider switch (AC-4).** On a synthetic fixture source, run a local
  fake adapter, then import an Apify fixture with the same keys and scope, then
  switch back to local.
  - Listing pks are identical, snapshot history is continuous, and no listing is
    created.
  - `WatchEvaluation` rows persist.
  - Covered by `test_provider_switch_preserves_identity_history_and_watch_state`.
- **D8 — Truncation (AC-5).**
  `test_truncated_actor_fixture_cannot_delist` runs end to end through import;
  so does the timed-out case.
- **D9 — Close-out.** Gate; TODO/STATUS. Record that live Actor runs remain
  owner-gated.

**Acceptance:**
- AC-4, AC-5, and AC-6 are proven against fixtures.
- No code path can start a live run (deny-all).
- The schemas are committed for the Actor repository to copy.

## Slice E — Apify spend ledger and admission

**Scope:** MS2-D-17.

**Files:**
- new `acquisition/apify/budget.py`;
- `catalog/models/provider.py` (`ApifySpendReservation`);
- migration `0022`;
- `acquisition/apify/jobs.py` (bind the real admission);
- settings keys `HW_RADAR_APIFY_ENABLED`, `…_USD_PER_CU`, `…_MARGIN`,
  `…_PER_RUN_OVERHEAD_USD` (defaults committed; values are not secret);
- new command `apify_spend_report`.

- **E1 — Schema (0022).** Add `ApifySpendReservation` with:
  - `provider_run` OneToOne null (null for denials) and `source_site`;
  - `admission_class` (`watch_refresh | discovery`) and `status`
    (`reserved | reconciled | released | denied`);
  - `estimate_usd` and `actual_usd` (Decimal 10,4), `reserved_at`,
    `reconciled_at`, `denial_reason`;
  - indexes on `reserved_at` and `status`.
- **E2 — Pure policy.** Implement `estimate_run_cost` and `decide_admission`.
  - Tests: `tests/unit/test_apify_budget.py` covers the estimate formula,
    equality at the limit (admitted), +0.0001 over (denied), discovery denied
    above $12 while watch_refresh is admitted up to $20, outstanding
    reservations counted, the kill switch, and invalid inputs.
- **E3 — Ledger service.** Implement `reserve()` under a
  `pg_advisory_xact_lock` and a rolling-31-day aggregation.
  - Tests: `tests/db/test_apify_ledger.py` covers concurrent reserve
    serialization (two threads, one admitted at the boundary), window roll-off,
    and stuck reservations still counted.
- **E4 — Reconcile.** Reconcile at terminal state; a null `usage_total_usd`
  keeps the estimate and is retried; an overrun is recorded.
  - Tests: reconcile paths.
- **E5 — Wire admission.** Replace `DenyAllAdmission` with the ledger in the
  start job, and derive `budget_paused` into C's freshness.
  - Tests: a denied start records a denial and the source shows `budget_paused`
    in `shortlist()`; a later successful import clears it.
- **E6 — Attribution report (AC-7).** `apify_spend_report` prints the rolling
  window, calendar-month, and per-source/provider totals from the ledger and
  `provider_run.usage_total_usd`.
  - Test: output for a seeded ledger.
- **E7 — Close-out.** Gate; TODO/STATUS. Record the owner tasks: account-level
  `max_monthly_usage_usd`, scoped token, and plan choice.

**Acceptance:** AC-7 holds; admission fails closed on the kill switch, at the
limit, on an unreconciled overrun, and on missing settings.

## Slice F — Pilot sources, measurement, end-to-end proof

**Scope:** MS2-D-19; MS-2 Tasks 6–7; AC-3 live half.

**Files:**
- `acquisition/sources/ebay.py` (category sweeps);
- possibly `serverpartdeals.py`;
- new command `pilot_report`;
- `docs/STATUS.md` evidence.

- **F1 — eBay category sweeps.**
  - **Sweeps.** Configure per-category sweeps: GPU 27386, RAM 170083, CPU 164,
    one `category_ids` per request. Verify the IDs via the Taxonomy API at
    implementation time and record the date. Each sweep has a `category_hint`
    and `collection_scope="ebay:<slug>:<query_id>"`. The legacy drive keyword
    sweep keeps scope None and hint None, so no data backfill is needed.
  - **Pagination.** Paginate with `limit=200`/`offset` up to a page cap below
    the 10,000-item ceiling.
  - **Multi-scope delist.** The adapter returns one scope per sweep via a new
    optional multi-scope capability that the pipeline applies per scope.
  - **Retention.** Retention and delete-on-delist are unchanged (class
    `ebay_listing_observation`).
  - **Tests** (cassettes):
    - per-sweep completeness;
    - every non-drive item carries a hint;
    - a GPU complete sweep cannot delist drive listings (scoped absence);
    - the frozen drive tests stay green.
- **F2 — ServerPartDeals breadth check.** If non-drive collections exist, add
  hinted, scoped collection sweeps. Otherwise record "drive-only" as a finding.
- **F3 — Measurement.** `pilot_report` summarizes, per source and provider:
  runs, completeness distribution, identifier (MPN) coverage, condition and
  shipping presence, freshness lag, failures, and cost (Task 6).
- **F4 — Category corpora (prep for owner gate R4).** Harvest GPU/RAM/CPU samples
  with the existing `harvest_corpus` tooling. Labeling and ratification are
  owner-in-the-loop, as in MS-1e.
- **F5 — Actor proof (owner-gated: R1, R2, R3).** After the owner selects a
  source and clears its ToS/robots review and the repository admission gate:
  1. Open the Actor PR in the separate Actor repository (private beta, copying
     the committed schemas).
  2. Set that source to `collection_provider=apify`.
  3. Run one bounded live run and one deliberately truncated run (AC-5 live).
  4. Switch local ↔ Actor where a local path exists (AC-4 live).
  5. Record cost and completeness.
- **F6 — End-to-end exit (owner-gated: R5).** With pilot sources enabled by the
  owner, create one real watch. `show_shortlist` produces a qualifying shortlist
  from real observations with no score (AC-3). Record the evidence in STATUS.

**MS-2 exit:** AC-1..AC-8 evidenced. The live halves of AC-3, AC-4, and AC-5 are
recorded after the owner gates clear.

## Open risks and owner gates

| ID | Risk / gate | Owner action | Blocks |
| --- | --- | --- | --- |
| R1 | **Actor-proof source is an owner (legal) decision.** Newegg's Terms of Use prohibit automated access and scraping "for any purpose", and robots.txt blocks a named price-watch bot. **Excluded** unless the owner decides otherwise. B&H, ServerPartDeals, and refurbished server-parts sellers are candidates only after a ToS/robots review. Apify execution does not change permissibility. | Choose the source after review | F5 live proof only |
| R2 | The separate Actor repository's product-admission gate (opportunity brief → BUILD) and its no-self-merge/branch rules apply to an internal Actor unless the owner explicitly directs the build. | Direct or brief | F5 |
| R3 | Account-level Apify configuration: `max_monthly_usage_usd` backstop, a scoped Run-only token, and the plan choice. **Open question:** does a paid plan's base subscription (Starter $19/mo on the 2026-09-24 official pricing page) count against the $20/month ceiling? If it does, a paid plan leaves ~$1 of usage headroom. The ledger caps *usage*. Recommend opening OQ23. | Configure; answer OQ | E live admission; F5 |
| R4 | GPU/RAM/CPU auto-accept needs an owner-ratified category corpus. The drive corpus does not validate other categories. | Label/ratify F4 corpora | Flipping `auto_accept` |
| R5 | MS-1e drive-matcher ratification is still pending, and all sources ship disabled. The real-observation exit proof (AC-3 live) needs the owner to enable pilot sources. | Ratify; enable | F6 |
| R6 | GPU/RAM/CPU reference seeds come from first-party pages. The ToS/licence of each manufacturer spec page should be spot-checked. Curated manual rows are the fallback. | Spot-check | B4 authoritative flag |
| R7 | Apify `usageTotalUsd` is recomputed at current pricing ("informational"), and its availability right at `SUCCEEDED` is unconfirmed. Storage and transfer outside run usage are covered only by the labeled overhead assumption. | — (the design reserves worst case) | E accuracy |
| R8 | eBay category IDs can change (Taxonomy API). eBay does not separate datacenter accelerators from consumer GPUs, so disambiguation falls to extraction. The Browse quota (5,000/day) is corroborated, not officially confirmed. | — | F1 |
| R9 | The deferred MS-2a plan names migrations 0018–0026, which collide with this plan. Its own header requires a rebase at activation (D2). | Rebase at reactivation | MS-2a only |
| R10 | ADR 0022 confirmation #3 and STATUS say "shortlist + exactly-one alert". Master spec §19 puts the alert in MS-4 (applied, D1). §10.1/§11 still show score-first wording. This is a documentation conflict and was not edited here. | Reconcile docs | none |
| R11 | Soft-threshold semantics (target price annotates, never decides) are an inference from ADR 0022, not a stated rule. | Confirm or override MS2-D-07 | C |
| R12 | Watch/requirement persistence (MS2-D-07) and the rolling-31-day budget window (MS2-D-17) are plan-level decisions. The master spec allows "milestone implementations", so no ADR is strictly required. An ADR would make them durable if the owner prefers. | Decide ADR vs plan | none |
| R13 | Slice A intentionally adds provenance keys to new resolution edges (`category`, `category_source`) and `detail_json["provider"]`. A run whose `delist_scope()` raises no longer advances continuity. Both changes are additive or strictly more conservative. | — | none |
| R14 | Legacy default `category_hint=None ⇒ drive` is a trap for any future multi-category source that forgets to hint. F1's test enforces hints for eBay category sweeps. Every new multi-category collector must copy that test. | — | F1 and later sources |

No new ADR or OQ file is created by this plan. R3 recommends OQ23, and R12 an
optional ADR, for the owner to open.

## Next slice after A

**Slice B — first-class category specs and rules** (migrations `0018`, `0019`),
starting with B1 (satellites + CHECK pair + AC-1 coexistence test).
