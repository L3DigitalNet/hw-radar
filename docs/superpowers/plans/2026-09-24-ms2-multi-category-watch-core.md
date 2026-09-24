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
> This plan does not edit ADRs or the master spec. (Revision 5's owner decisions were
> recorded in those documents by a separate, owner-directed decision-record change:
> the dated ADR 0021/0022 amendments, the master spec revision 0.19, and the OQ23/OQ24
> moves. The plan cites them; it does not carry their authority.)
>
> **Revision 2 (2026-09-24).** Resolves the 12 findings of cross-agent review
> round 1 (see *Review lineage* at the end), aligns the Slice A text with what
> landed for A0–A3, and folds in the Slice B/D/F prep research. Decision IDs
> MS2-D-01..-19 and task IDs are unchanged; new decisions are MS2-D-20..-28 and
> new tasks carry new IDs (B4a–B4c, B6, D10–D12, E8).
>
> **Revision 3 (2026-09-24).** Resolves cross-agent review round 2: the five
> partially resolved findings (F-03, F-04, F-07, F-08, F-10) and three new ones
> (N-01, N-02, N-03). See the round-2 table in *Review lineage*. Existing
> decision and task IDs are unchanged. New decisions are MS2-D-29..-33. No new
> task IDs are added, and no migration is renumbered: the new schema lands in
> the unmerged migrations `0020`, `0021`, and `0022`. The F-04 residual is a
> Slice A code correction (A4/A6 text below).
>
> **Revision 4 (2026-09-24).** Resolves cross-agent review round 3: the three
> partially resolved findings (F-08, N-01, N-02) and one new one (M-01). All
> four concern out-of-order asynchronous imports and accounting horizons in
> Slices D and E. See the round-3 table in *Review lineage*. Existing decision
> and task IDs are unchanged. New decisions are MS2-D-34..-37. No new task IDs
> and no migration renumbering: the new columns land in the unmerged `0021`
> (Slice D) and `0022` (Slice E). Slice D (core) now has an explicit entry
> gate (see *Slice D entry gate*). Slices B and C are unaffected.
>
> **Revision 5 (owner decisions, s2, 2026-09-24).** Encodes the owner's session-2
> decisions on Apify ownership, cost, billing period, clocks, and the Actor proof.
> Each changed passage is marked **owner-overridden** (the owner reversed a
> rev-4 decision) or **owner-clarified (s2, 2026-09-24)** (the owner settled or
> sharpened something rev 4 left open). Rounds 1–4 and every decision not listed
> here are unchanged. Codex review of revision 5 is pending (see *Review lineage*).
> - **Owner-overridden:** MS2-D-14 *Fixtures* and the contract location (the Actor
>   is built in this repository, not a separate one); MS2-D-17 *Period* (the Apify
>   billing cycle replaces the rolling 31-day window as the authority); MS2-D-26
>   *Ceiling* (the OQ23 deduction is withdrawn; the cash ceiling is enforced
>   structurally); MS2-D-34 (the charge horizon becomes billing-cycle intersection);
>   R2 (withdrawn); R12 (the budget period is no longer plan discretion).
> - **Owner-clarified (s2, 2026-09-24):** MS2-D-15 (finalization and post-run
>   billing facts, nine REST calls); MS2-D-19 (the Actor proof splits into a
>   synthetic proof and a separate merchant admission); MS2-D-22 (overrun blocks
>   repair reads); MS2-D-23 and MS2-D-32 (reservation states `usage_provisional` /
>   `usage_finalized`; post-run allowance); MS2-D-25 (the Actor-review item moves in
>   repo); MS2-D-11 (a DB-free truncation-reason vocabulary beside the unchanged
>   `RunCompleteness`); R1, R3, R7, R19, R20, R23.
> - **New decisions:** MS2-D-38 (in-repo Actor ownership, layout, and gate),
>   MS2-D-39 (three clocks), MS2-D-40 (billing-cycle budget and cash-ceiling
>   model, OQ23), MS2-D-41 (reservation → reconciliation lifecycle), MS2-D-42
>   (synthetic Actor proof source), MS2-D-43 (operator/agent Actor workflow),
>   MS2-D-44 (Actor-backed merchant source admission, OQ24).
> - **Tasks:** D-prep now builds the Actor skeleton under `actors/` (D1) and a
>   nine-call client (D3). E1–E7 gain the billing-cycle table, provisional/finalized
>   usage, and the named §29 budget tests. F5 splits into **F5a** (synthetic proof
>   through a real private Actor) and **F5b** (a production Actor-backed merchant
>   source, owner-gated by OQ24). New risks R24–R32. Migrations stay planning
>   allocations; see *Global constraints*.
> - **Implementation-driven clarifications from Slice B** (not owner decisions):
>   MS2-D-06 (the non-first-party retention gap, owner gate R32); B1/B2 (live
>   migration numbers `0018`/`0019` verified, satellite choices); B *Files* (more
>   allowed test edits: type narrowing in `tests/unit/test_refdata_contracts.py`,
>   and drive-scoping with no expected-value change in the seed-corpus tests of
>   `test_refdata_loader.py`, `test_refdata_import.py`, and
>   `test_refdata_categories.py`);
>   B4a (`non_first_party`); B4c (row counts first-party sources support); B6
>   (landed `a789e98`).
> - **Category tests added for owner §29:** B3 and C3 name the exact-authoritative
>   acceptance, fuzzy/merchant-only, hard-contradiction, and missing-attribute
>   tests explicitly. Slice B's task order is unchanged.

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
  and MS-2 plans `0018+` in this order: B `0018`, `0019`; C `0020`; D `0021`; E `0022`.
  D-prep (see *Slice order*) has no migration.
  **These numbers are planning allocations, not reservations** (revision 5,
  owner-clarified (s2, 2026-09-24)). Before writing its migration, each slice
  re-verifies the live migration graph on its base (`showmigrations catalog`) and
  records any drift (a different head, an interleaved migration) in its close-out
  evidence; the slice then numbers from the live head. The deferred MS-2a plan owns
  none of these numbers.
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
  Agent (NFR-003) from a Hardware Radar-scoped path (proposed
  `secret/apps/hw-radar/apify`; the owner provisions it, MS2-D-43). It is never the
  apify-actors venture's token. Actor references (Actor id, build tag) come from
  settings or the environment, never from committed literals. Actor *names*
  (`hw-radar-<purpose>`) and the Actor source under `actors/` are committed
  (MS2-D-38).
- **Actor projects** (revision 5, MS2-D-38): every Hardware Radar Actor lives under
  `actors/hw-radar-<purpose>/` in this repository, with its own `pyproject.toml` and
  `uv.lock`. The gate runs the Actor's own tests, type check, and audit in that
  project's environment; the Apify SDK never enters hw-radar's `uv.lock`.
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
  advance or preserve sweep continuity (it breaks it, MS2-D-11), or reach the
  stale-absence path for a remote provider. A remote run counts toward
  continuity only when both its evidence and its scope say complete (MS2-D-11).
- Do not let one collection scope's sweeps prove continuity for another scope
  (MS2-D-31).
- Do not let an older observation overwrite newer current listing state,
  write current content to (or revive) a listing that newer absence evidence
  retired, create an active listing for a key a newer complete sweep of its
  scope omitted, or delist a listing that a newer run observed (MS2-D-30,
  MS2-D-35).
- Do not copy a snapshot's retention from the current `Listing` on an
  ordering-guarded path; retention follows the observation's own time
  (MS2-D-37).
- Do not let an older run restore continuity that a newer break ended, on any
  scope, the legacy NULL scope included (MS2-D-36).
- Do not let a stored eligibility verdict outlive the inputs it evaluated,
  catalog inputs included (MS2-D-20, MS2-D-29), and do not default any remote
  import to `merchant_fact` retention (MS2-D-25).
- Do not release a spend reservation while charge-producing work remains
  (MS2-D-32), and do not age settled spend out by its `reserved_at`; it counts
  in every Apify billing cycle its charge interval touches (MS2-D-34, revision 5).
  Do not treat a rolling window or a calendar month as the budget period; the
  authority is the account's Apify billing cycle read from the API (MS2-D-40).
  Do not treat the first usage figure at run completion as final, and do not mark
  a reservation reconciled because the Actor process stopped (MS2-D-41).
  Do not derive one clock from another (MS2-D-39).
  Do not anchor a retention deadline at a locally observed event (MS2-D-33).
  Do not change the eBay delist path or the lane-continuity gate (migration 0015)
  (D5).
- Do not create listings, fork history, duplicate observations, or reset watch
  state when a source switches provider. Do not use Apify result IDs as keys
  (DR-003, D6).
- Do not configure an Apify schedule for any production job. Do not use webhooks
  in v1. Do not run a second scheduler (ADR 0012, D4).
- Do not give an Actor DB credentials, hw-radar model imports, Django imports, or
  canonical state. Do not enable residential proxies, browser escalation, or paid
  third-party Actors automatically (ADR 0021, ADR 0014). MS-2 Actor runs use
  no proxy at all (MS2-D-26). Revision 5 (owner-clarified (s2, 2026-09-24)): no
  residential proxy, no automatic proxy rotation, no CAPTCHA solving, no paid
  unblocker, and no paid third-party Actor, ever, including as a fallback. A source
  that needs any of them fails admission (MS2-D-44).
- Do not create, build, or version a Hardware Radar Actor anywhere but
  `actors/` in this repository, and do not depend on the apify-actors venture's
  code, lifecycle state, product records, admission process, or token
  (revision 5, owner-overridden; MS2-D-38).
- Do not start a paid Actor run outside hw-radar's budget admission. That rules
  out the Apify MCP `call-actor` tool, Console "Start", and schedules for any
  Hardware Radar Actor (MS2-D-43).
- Do not deploy an Actor automatically from pull-request code, CI, or a GitHub
  push webhook. Deployment is an explicit, reviewed operator action (MS2-D-43).
- Do not treat the account-level Apify `max_monthly_usage_usd` as project
  admission. It is an owner backstop only (D3), and agents never change it or any
  other billing or account setting without explicit owner authority (MS2-D-40).
- Do not architect correctness around `maxTotalChargeUsd` or the `maxItems` run
  option. Both apply only to pay-per-event or pay-per-result Actors, not to
  Hardware Radar's own Actors (MS2-D-15, revision 5).
- Do not start any live Actor run before the owner gates in *Open risks* clear.
  Do not name Newegg as the Actor source (ToU exclusion, see risk R1).
- Do not model complete servers beyond basic-watch exact identity. Do not merge
  differently configured servers into one variant (D11, ADR 0022).
- Do not edit ADRs 0021/0022, the master spec, or the deferred MS-2a plan from a
  slice. Decision-record changes go through the owner (revision 5's amendments
  were owner-directed).

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
  Slice A registers only `drive`. `rules_for("drive")` builds its
  `CategoryRules` **on each call** from the current module attributes
  `vocab.extract`, `mpn.extract_candidates`, `grammars.decode`, and
  `ladder.contradictions`, so `rules_for("drive").extract is vocab.extract`
  holds, and a monkeypatched module function is honored. Binding at import time
  was rejected because frozen `tests/db/test_resolver.py` (≈:242–314)
  monkeypatches `vocab.extract` and expects an error edge.
  `CATEGORY_SLUG_MAX_LENGTH` (50) lives in `matching/categories.py`.
- **Ladder veto.** `ladder.decide()` gains a keyword-only `veto` parameter that
  defaults to `contradictions`. It is used at all three veto sites
  (`ladder.py:161,196,225`). The two local result variables that were named
  `veto` (`:161`, `:196`) are renamed `vetoed`, so the callable parameter is
  never rebound to a list; the persisted evidence key stays `"veto"` and its
  values are byte-identical.
- **Resolver spec readers.** The resolver keeps the ORM-bound spec readers in a
  parallel `_SPEC_READERS` table whose keys must equal the registry's. A test
  pins this.
- **Unregistered categories.** A registered-but-unknown category produces a
  `none` edge with `unsupported_category` evidence. Drive rules never run on it.
- **Edge provenance.** Every ladder-path edge carries `evidence["category"]`
  and `evidence["category_source"]` (`hint | legacy_default`). This is the only
  additive change to persisted drive output.
- **Invalid hints.** A non-string or non-slug hint read back from `attrs_json`
  produces the resolver's error edge, never a drive fallback.
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
- **Acceptance provenance.** Even with `auto_accept=True`, new categories
  accept only authoritative aliases at approved grains (MS2-D-21).
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
- **Current-state gaps** (code evidence, 2026-09-24): `persist._import_document`
  hard-codes `Category.objects.get_or_create(slug="drive")` (`persist.py:152`)
  and writes `DriveSpec.objects.update_or_create` (`persist.py:208`);
  `SeedModel.spec` is one concrete drive-mirroring `SeedSpec`
  (`contracts.py:56`); provenance is one per-document `SeedProvenance` block,
  not per row; and `SeedProvenance.source_kind` is a Literal of
  `first_party_datasheet | first_party_manual | first_party_page` with no
  non-first-party value. B4a/B4b close these gaps.
- **First-party sources and identifiers** (prep survey, retrieved 2026-09-24;
  per-source ToS spot check stays risk R6):
  - CPU: Intel ARK (ark.intel.com), keyed by processor number (for example
    `Xeon Gold 6448Y`), with the FPO/spec code (`SRxxx`) as the printed
    identifier; AMD specifications pages (amd.com) keyed by marketing name, with
    the OPN (`100-000000xxx`) as the ordering identifier. OPN and FPO/spec code
    are the MPN-like aliases; marketing names are the retail-name aliases.
  - GPU: chip-vendor pages and PDF datasheets (NVIDIA data-center and GeForce
    pages, AMD Instinct and Radeon pages). Every MS2-D-04 GPU field is
    chip-level, so board-partner (AIB) pages are not v1 sources. An AIB page is
    authoritative only for that partner's own board SKU, never for chip facts.
    Intel Arc and Data Center GPU pages were not surveyed.
  - RAM: Micron's official part decoder is the one confirmed first-party
    decoder. Samsung and SK hynix per-part pages exist, but their decode grammars
    are only community-sourced, so the grammars are not authoritative. For OEM
    server part numbers (Dell, HPE, Lenovo), the OEM's own spec for its own SKU
    qualifies. A reseller's claim that an OEM part equals a module-maker part does
    not qualify: that equivalence is `manual` at most.
  - No bulk export is confirmed for any vendor. Seeds stay hand-authored,
    per-model JSON, like the drive seeds.
- **Non-first-party retention gap** (revision 5, implementation-driven
  clarification from Slice B, not an owner decision). No retention class exists
  for non-first-party reference rows: `manufacturer_reference` is first-party
  only, and adding a class rewrites every `*_retention_ttl_coherent` CHECK, as
  migration `0017` did. Until the owner decides (risk R32), the importer refuses a
  non-first-party document before any write (`UnratifiedProvenanceError`, a
  subclass of `ImportConflictError`), and B4c seeds first-party rows only. The
  `SeedProvenance.source_kind` value for such rows is `non_first_party`, which
  maps to alias `source_kind=manual` once the gate clears.
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
  `reasons` JSON (output evidence per DR-004, not watch input), and the
  evaluated-input binding: `requirement_version`, `evaluator_version`,
  `snapshot_observed_at`, `resolution` (FK to the `ListingResolution` edge that
  was current at evaluation, null, `SET_NULL`), `catalog_fingerprint`
  (MS2-D-29), plus `evaluated_at`. Validity is governed by MS2-D-20.
- **Retention.** It is `RetentionGoverned`, mirroring the listing's retention
  class/expiry at evaluation. `Listing.mark_delisted` pulls it forward like
  snapshots (`market.py:369-374`), and the purge registry covers it. eBay-derived
  reasons carry prices, so they must not outlive DR-008.
- **Trigger.** Evaluation runs as a pipeline stage after resolution, isolated
  like the resolver: a failure never blocks ingestion. It is wired on every
  ingestion path (MS2-D-20). It also runs from an `evaluate_watches` command
  after watch edits.
- **Read model.** `shortlist(watch_id)` returns only *current* (MS2-D-20)
  `match` rows for active listings, ordered by landed USD ascending, with
  freshness (`fresh | stale | budget_paused`) and `meets_target`.
  `review_queue(watch_id)` returns current `unknown` rows (`state="unknown"`)
  and every non-current row of any verdict (`state="pending"`).
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
- **Callers outside the seam.** `run_source` is the only *pipeline* caller of
  `fetch`/`parse`, but not the only caller (Map Q3, corrected in revision 2):
  - `harvest_corpus` calls both directly, with no persistence
    (`harvest_corpus.py:134-141`). MS2-D-27 carries the category hint through it.
  - Each heartbeat adapter's `probe()` reuses its own `fetch`/`parse`: eBay,
    ServerPartDeals, Seagate, and WD, called from `run_heartbeat`
    (`heartbeat.py:218`). Heartbeat is local-only (MS2-D-18 CHECK).
  - `run_heartbeat` then fires `run_source` as a FULL run (`heartbeat.py:228`).
    It is therefore inside the seam, and MS2-D-20 wires evaluation there.
- *Evidence:* Map Q3.
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
  | `complete` | the scope, unchanged, if `scope.complete` is True **or** the evidence is stale-eligible (local only); else `None` |
  | `truncated` | `replace(scope, complete=False)` if stale-eligible, else `None` |
  | `partial_failure` | `None` |
  | `failed` | `None` |

  The `complete` row was tightened during Slice A verification (revision 2).
  Revision 1 passed a `complete` evidence's scope through even when the scope
  said `complete=False`. A remote provider reporting `complete` evidence with an
  incomplete scope could then reach `ABSENT_STALE`.

- **Continuity.** `counts_toward_sweep_continuity(evidence, scope)` takes the
  provider's own `DelistScope` (before gating) as well as the evidence
  (revision 3, review F-04 residual):
  - **Local** evidence: unchanged. It counts iff `complete` or stale-eligible
    `truncated`. The scope is ignored, so every local run still counts.
  - **Non-local** evidence counts only if all three hold: completeness is
    `complete`, `scope is not None`, and `scope.complete`. Every other remote
    run breaks continuity. That includes the contradictory case of `complete`
    evidence with an incomplete scope, which `gate_delist_scope` already
    refuses for direct absence.
  - *Why:* revision 2 counted every `complete` evidence. A string of remote
    runs reporting `complete` evidence but an incomplete scope kept the old
    `continuous_since` alive and became successful predecessors. A later local
    incomplete sweep could then stale-delist on continuity that no remote
    scope proved.
  - From Slice D, continuity is tracked per collection scope (MS2-D-31). In
    Slice A every run is the legacy NULL scope, so the lane column below is
    the whole mechanism.
  - An eligible successful FULL run calls `_record_sweep_continuity`, unchanged.
  - An **ineligible** successful FULL run **breaks** continuity:
    `_break_sweep_continuity(site)`, a small helper beside
    `_record_sweep_continuity`, sets the FULL lane's `continuous_since := None`.
    It is a no-op when the site has no `SourceConfig`.
  - A remote FULL result rejected before persistence also breaks continuity
    (MS2-D-22). A rejected PROBE never touches continuity (MS2-D-24).
  - *Why:* merely skipping the update would leave an old local
    `continuous_since` in place. `_record_sweep_continuity` finds the previous
    run by `FULL` + `SUCCESS` alone (`pipeline.py:112-117`), so a truncated remote
    import would make the gap look short and let a later local incomplete sweep
    stale-delist on continuity that no run ever proved.
  - Because every ineligible run nulls continuity, the next eligible run always
    restarts it. So the previous-run lookup needs no completeness predicate.
    That argument assumes runs are recorded in order, which holds in Slice A.
    From Slice D, asynchronous imports can record out of order, so every break
    carries its event time and an older run cannot restore continuity a newer
    break ended (MS2-D-36).
  - *Rejected:* tracking the last eligible sweep in a new lane column. It needs a
    migration in Slice A and duplicates the ScraperRun record.
  - Local runs are always eligible, so local-only behavior is unchanged.
- **Local mapping.** An adapter scope with `complete=True` maps to `complete`. A
  scope with `complete=False`, or no scope at all, maps to stale-eligible
  `truncated`. The eBay path and non-delist sources therefore behave exactly as
  today.
- **Storage.** A persists evidence in `ScraperRun.detail_json["provider"]` (no
  migration). D adds the queryable `ProviderRun` table.
- **Truncation cause** (revision 5, owner-clarified (s2, 2026-09-24)). The four
  `RunCompleteness` values stay exactly as Slice A landed them, and
  `test_completeness_values_are_the_adr0021_taxonomy` stays unmodified. D1 adds a
  separate DB-free `TruncationReason` `TextChoices` in `catalog/models/ops.py`:
  `item_limit | page_limit | request_limit | time_limit | resource_limit`
  (`resource_limit` = a byte or transfer budget set at admission). It qualifies
  `truncated` only:
  - `ProviderRunEvidence` gains an optional `truncation_reason`. A validator
    requires it for non-local `truncated` evidence and forbids it for every other
    completeness. Local truncated evidence keeps `None`.
  - A `None` reason is omitted from the evidence JSON, so local
    `detail_json["provider"]` stays byte-identical and
    `test_run_source_records_local_provider_evidence` stays green unmodified. The
    serializer mechanism is the implementer's choice.
  - D2 adds a nullable `provider_run.truncation_reason`.
  - *Rejected:* new `RunCompleteness` members per cause
    (`truncated_items`, …). That would break the Slice A taxonomy test and every
    `truncated` branch in the gate, although the delist semantics of all causes
    are identical.
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
- **Not sufficient alone.** The candidate filter stops direct cross-scope
  deletion, but it does not prove that a scope was polled across its grace.
  That proof is per-scope continuity (MS2-D-31). The filter also gains the
  newer-observation guard (MS2-D-30).
- *Rejected:* one `SourceSite` per category sweep. It forks listing identity,
  violating D6.

**MS2-D-13 — Provider-run record and idempotency key (Slice D, migration 0021).**
- **Table.** `provider_run` holds:
  - identity: `provider_kind`, `source_site` FK, `external_run_id` (unique with
    kind; null until the start response is recorded, MS2-D-33),
    `import_idempotency_key` (unique, `apify:<run id>`, set with the run id),
    `actor_ref`, `build_id`, `build_number`, `contract_schema_version`;
  - requested scope: `query_scope` JSON (category/query/limits), plus typed
    `scope_key` (the MS2-D-12 key the run was admitted for, null for the legacy
    scope), `memory_mb`, `timeout_s`, `max_items`, `max_pages`, and
    `admission_class`;
  - remote execution (MS2-D-23): `admitted_at`, `remote_status` (null until the
    start response), `remote_terminal_at`, `started_at`, `finished_at`,
    `completeness`, `completeness_reason`, `usage_total_usd`,
    `usage_read_at`, `run_kind` (FULL or PROBE);
  - local work (MS2-D-22, -23, -25, -32, -33): `dataset_id`, `kv_store_id`,
    `dataset_item_count`, `import_state`, `import_listing_ids` (bigint array),
    `stage_detail` JSON (per-stage error counts), `import_attempts`,
    `next_attempt_at`, `scraper_run` OneToOne null, `dataset_read_count`,
    `kv_read_count`, `final_charge_op_at`, `storage_state`,
    `storage_cleanup_due_at` (the absolute retention deadline, set at
    admission), `storage_cleanup_attempts`, `storage_deleted_at`.
- **Idempotent import** (revision 2; the durable stage machine is MS2-D-22):
  1. A replay that finds `finalized` or `rejected` is a no-op before any fetch.
  2. `observed_at` is the run's `startedAt`, which is deterministic and never
     overstates freshness.
  3. Provider imports use insert-if-absent on `(listing_id, observed_at)`.
  4. The `ScraperRun` is created once and reused on every retry.
  5. Idempotency is not ordering. A delayed import may still be older than
     state a newer run already wrote; MS2-D-30, -35, -36, and -37 guard that.
- **Retention.** The table holds no merchant content, only ids, counts, and
  status, so it is not retention-bearing (like `ScraperRun`). Raw dataset items
  land in `RawPayload` under the source's registered retention (MS2-D-25).
- *Rejected:* deduplicating by `RawPayload.content_hash`. It is not unique
  (Map Q5) and cannot express "this remote run was already imported".

**MS2-D-14 — Versioned Actor contract (Slice D, settled D7).**
- **hw-radar owns the contract** (revision 5, owner-overridden: the Actor is also
  built here, MS2-D-38).
  - The single contract artifact is the set of committed, plain JSON Schema
    (draft 2020-12) files in the Actor's own directory,
    `actors/hw-radar-<purpose>/contract/hw-radar-{input,listing,run}-v1.schema.json`.
    They sit inside the Actor's build context, so the Actor can validate its own
    output without reaching outside its directory.
  - hw-radar's Pydantic models in `acquisition/apify/contract.py` are tested for
    conformance: `Model.model_json_schema()` must equal the committed file, and
    the gate fails on drift in either direction. The Actor's own tests validate
    every emitted fixture row and `OUTPUT` record against the same files. So a
    contract change must edit both sides in one hw-radar PR.
  - The Apify-dialect files `.actor/input_schema.json` and
    `.actor/dataset_schema.json` are not the contract. A conformance test pins
    that the Apify input schema declares the same property set, required set,
    types, enums, and numeric bounds as `hw-radar-input-v1`. If D1 uses a dataset
    schema, a test pins its `fields` equal to `hw-radar-listing-v1`.
  - *Rejected (a):* revision 1–4's "Pydantic generates the schemas into hw-radar,
    and the separate Actor repo copies them". There is no separate repo now, and a
    copied file can drift silently.
  - *Rejected (b):* a shared Python package imported by both sides. It couples the
    Actor's build context and dependencies to hw-radar's package and invites a
    Django import into the Actor.
  - *Rejected (c):* the Apify-dialect schemas as the contract. They are not plain
    JSON Schema, so neither side can validate against them with standard tools.
  - *Reopen if* a second Hardware Radar Actor needs the same contract. Promote
    the files to a shared directory then, with `dockerContextDir` covering it.
  - `hw-radar-listing/v1` defines the dataset row: `schemaVersion`, `siteKey`,
    `sourceListingKey`, `url`, `title`, `price` (decimal string), `currency`,
    `shippingPrice`, `stockStatus`, `quantityAvailable`, `sellerName`,
    `conditionLabel`, `shipsFromCountry`, `categoryHint`, `collectionScope`,
    `mpn`, `observedAt`.
  - `hw-radar-run/v1` defines the default-KV `OUTPUT` record: `schemaVersion`,
    `status`, `completeness{complete, truncated, limitsHit{pages, requests, items,
    time, bytes}, pagesDeclared, pagesFetched, itemsDeclared, itemsEmitted}`,
    `queryScope` echo, `provider{actorName, actorVersion, sourceKind}` scope
    metadata, and `errors[]`. It follows the `pdf-evidence-reader`
    status/truncated/coverage pattern. (`limitsHit.bytes` and `provider` are
    revision 5 additions; D1 is unbuilt, so v1 is amended in place.)
  - `hw-radar-input/v1` defines the run input: `schemaVersion`, `siteKey`,
    `collectionScope`, `categoryHint`, `maxItems`, `maxPages`, `maxRequests`,
    `maxBytes`, `timeBudgetSecs`, plus source-specific fields. It has no proxy,
    browser, CAPTCHA, or unblocker field (MS2-D-44). hw-radar validates every
    input against it **before** the start request, so an invalid request never
    reaches paid execution. Apify's own input-schema validation is a second layer
    whose pre-billing behavior D3 verifies (MS2-D-15).
- **Classifier.** `classify_run(remote_status, output, dataset_count,
  usable_count) -> (RunCompleteness, reason)`. `usable_count` is the number of
  dataset rows that pass contract validation.
- **Completeness mapping:**
  - `complete` requires all of: Apify `SUCCEEDED`, `OUTPUT.completeness.complete`,
    no limit hit, and `itemsEmitted` = dataset count = `usable_count`.
  - **Proven complete-empty** is `complete` with reason `complete_empty`. It
    requires all of: `SUCCEEDED`; a valid `OUTPUT` with `complete=true` and no
    limit hit; `pagesFetched ≥ 1`; `itemsDeclared == 0` and `itemsEmitted == 0`,
    both present and non-null; and a dataset count of 0. ADR 0021 requires this
    to be distinguishable from a failed collection.
  - `truncated` covers any limit hit or `TIMED-OUT`. Revision 5: the classifier
    also returns the `TruncationReason` (MS2-D-11) from `limitsHit` in the fixed
    precedence `time, bytes, requests, pages, items` (`TIMED-OUT` ⇒ `time_limit`,
    `bytes` ⇒ `resource_limit`), and records every hit limit in the reason text.
  - `partial_failure` covers `FAILED`/`ABORTED` with items, or `errors` non-empty.
  - `failed` covers:
    - a missing or invalid `OUTPUT`, or an unknown `schemaVersion`;
    - a **non-empty dataset with zero usable rows** (`no_usable_items`);
    - an **empty dataset without complete-empty evidence** (`ambiguous_empty`);
    - revision 5 (owner-clarified (s2, 2026-09-24); rev 4 left these rows
      unassigned): a **self-contradictory report** (`contradictory_report`):
      `complete=true` together with any limit hit; `truncated=true` with no limit
      hit and a non-`TIMED-OUT` status; `SUCCEEDED` with `OUTPUT.status` other
      than `succeeded`; `itemsEmitted` ≠ the dataset count; or `pagesFetched` >
      `pagesDeclared`;
    - a **scope mismatch** (`scope_mismatch`): the `queryScope` echo or
      `provider.actorName` differs from what the run was admitted for.

    Contradiction fails closed: nothing is persisted, and a FULL run breaks
    continuity (MS2-D-22 *Reject*).

  Missing or ambiguous evidence is never `complete` (DR-011). `failed` persists
  nothing (the import is `rejected`, MS2-D-22).
- **Raw batch shape.** `RawBatch.items` holds dataset rows only. The `OUTPUT`
  record is stored on `provider_run` and is never a `RawItem`. So the zero-record
  parser-rot guard (`batch.items and not parsed`, `pipeline.py:362`) passes a
  complete-empty batch and still rejects non-empty unusable output. A
  complete-empty result with a `scope_key` delists that scope's active listings
  through the normal `ABSENT_FROM_SWEEP` path.
- **Fixtures.** Frozen fixtures for each state live in hw-radar
  (`tests/fixtures/apify_contract/v1/`). Revision 5 (owner-overridden): the Actor
  lives in `actors/` in this repository and validates against the same committed
  schemas (MS2-D-38); nothing is copied to another repository.
- **Storage cleanup.** The run's default dataset and KV store are deleted by
  deadline, independent of import success (MS2-D-25).

**MS2-D-15 — Apify transport (Slice D).** Use a thin async `httpx` client over the
seven run and storage REST calls needed: start run, get run, abort run (on a start-option
mismatch, MS2-D-26, or a passed retention deadline, MS2-D-33), list dataset
items, get KV record, delete dataset, and delete KV store. Revision 1 listed
five calls; F-08 and F-10 added abort and KV-store delete. Revision 5
(owner-clarified (s2, 2026-09-24)) adds two free-of-platform-usage account reads
for MS2-D-40, making nine: get account limits (`GET /v2/users/me/limits`, which
carries `monthlyUsageCycle` and the account limit) and get monthly usage
(`GET /v2/users/me/usage/monthly`, optional `date`, per-cycle and per-day service
usage). If the limits response lacks the plan's base price and prepaid credit,
D3 adds a tenth read, `GET /v2/users/me` (its plan block). Whether these account
reads are free of platform usage is verified in E2 together with `GET` run
polls.
- `httpx` is already a dependency, and tests use `httpx.MockTransport` / vcrpy
  cassettes.
- *Rejected:* `apify-client` 3.2.0 (released 2026-09-03). Since 3.0.0 it uses
  `impit` as its required default transport; the optional `httpx2` extra is a
  separate package, not the `httpx` we already have. It would add a dependency
  for seven calls, and its return types changed across 2.x→3.x.
- **Confirmed facts the client relies on** (docs.apify.com, retrieved
  2026-09-24; prep report for D):
  - Run status lifecycle: `READY` → `RUNNING` → terminal `SUCCEEDED | FAILED |
    TIMED-OUT | ABORTED`, reached via the transitional `TIMING-OUT` and
    `ABORTING`.
  - Run object fields (Python-client attribute names; the camelCase wire names
    follow the same aliasing): `id`, `status`, `started_at`, `finished_at`,
    `build_id`, `build_number`, `default_dataset_id`,
    `default_key_value_store_id`, `options.memory_mbytes`,
    `options.timeout_secs`, `options.max_items`, `stats.compute_units`,
    `usage` (a per-component breakdown, nullable), and
    `usage_total_usd: float | None`.
  - `usage_total_usd` is nullable. Its value right at `SUCCEEDED` is
    unconfirmed, and Apify says historical dollar amounts are recomputed at
    current pricing and are "for informational purposes only". It is therefore
    re-read until non-null (MS2-D-23).
  - **Naming trap.** The start request sets the run timeout with the REST query
    parameter `timeout` (seconds) and memory with `memory` (MB). The Python
    client names them `run_timeout` (a `timedelta`) and `memory_mbytes`. The run
    response reports them as `options.timeoutSecs` / `options.memoryMbytes`.
    None of these is the client's per-request HTTP `timeout` tier. The Actor's
    `timeout` must never be confused with the httpx request timeout.
  - `max_total_charge_usd` applies only to pay-per-event Actors. It is not a
    cost bound for a self-owned Actor.
  - Revision 5 billing facts (official docs retrieved 2026-09-24; research
    input `apify-billing.md`, not committed):
    - The first Get Run response after completion "can still show preliminary
      `stats`, costs, and event counts"; Apify advises waiting about 10 s and
      reading again. The first figure is therefore **provisional** (MS2-D-41).
    - Dollar figures read back later for a historical run are recomputed at
      *current* pricing. The finalized figure is captured once and never
      re-derived from a later read.
    - Dataset reads and writes after the run finishes are platform usage billed
      to the **account**; no source says they are added to the run's
      `usageTotalUsd`. Post-run cost is therefore accounted from hw-radar's own
      operation counters (MS2-D-32, MS2-D-41), and measured by diffing the
      account's monthly usage (F5a).
    - `usageTotalUsd` and `usageUsd` are returned only to authenticated reads.
    - `maxTotalChargeUsd` and the `maxItems` run option apply only to
      pay-per-event and pay-per-result Actors. They are **not applicable** to
      Hardware Radar's own Actors (settled). The client does not send
      `maxItems`; the Actor input's own caps bound items (MS2-D-26). If a later
      verification shows either applies to a private pay-per-usage Actor, it is
      added as an extra defense only, never as the correctness mechanism.
- **Unconfirmed until D3:** the exact wire names of `usageTotalUsd`,
  `buildNumber`, and the `usage` component keys; the response to aborting a run
  that already finished; whether Apify rejects an input that fails the Actor's
  input schema before any billable run exists (revision 5); and whether a
  scoped (limited-permission) token can read `/users/me/limits` and
  `/users/me/usage/monthly` (revision 5). D3 verifies these against the official
  API reference, and F5a confirms them live. MS2-D-26 does not rely on
  platform `maxItems` enforcement.
- *Reopen if* the needed surface grows beyond these calls.
- The token comes from `HW_RADAR_APIFY_TOKEN`, rendered from the Hardware
  Radar-scoped OpenBao path (MS2-D-43). The owner scopes it as narrowly as the
  runtime allows: run the Hardware Radar Actor(s), read and delete their run
  storages, and read account limits and usage. If a scoped token cannot read the
  account endpoints, account-headroom admission denies with
  `account_state_unobservable` until the owner decides the token scope (R25).

**MS2-D-16 — Completion observation (settled D4).**
- **Poll job.** An APScheduler `apify-poll` interval job (`max_instances=1`,
  `coalesce=True`) runs three independent selectors each tick: active remote
  executions, terminal rows with outstanding local work, and overdue storage
  regardless of remote status (MS2-D-23, MS2-D-33).
- **Start job.** The existing full-lane job for a source with
  `collection_provider=apify` *starts* a run instead of calling `run_source`.
  The recovery-probe job dispatches the same way (MS2-D-24). There is one
  scheduling owner and no Apify schedules or webhooks.

**MS2-D-17 — Apify spend ledger (Slice E, migration 0022; settled D3).**
- **Period** (revision 5, **owner-overridden**). The authoritative budget period
  is the account's actual Apify billing cycle, discovered from the API
  (`GET /v2/users/me/limits` → `monthlyUsageCycle.startAt/endAt`) and stored per
  cycle (MS2-D-40). It is never a hard-coded calendar month and never a rolling
  window. Verified account state 2026-09-24: an anniversary cycle,
  2026-09-05T00:00:00Z → 2026-10-04T23:59:59.999Z.
  - *Withdrawn:* revision 1–4's "rolling 31-day window" and its rejection of the
    billing cycle ("an extra API read; the boundary drifts if the plan
    changes"). The owner ruled the cycle authoritative. The extra read is
    cheap and cached (MS2-D-40), and a drifting boundary is exactly why it is
    read, not assumed.
  - A trailing 31-day total remains only as a secondary **trend and safety
    metric** in the spend report and a warning log. It never admits or denies.
  - *Rejected (a):* calendar month UTC. It can put up to 2× the cap inside one
    billing cycle that straddles a month boundary.
- **Reservation estimate.** It is the sum of the bounded charge components in
  MS2-D-26, times `(1 + margin)`. Revision 1's single compute formula plus an
  assumed overhead is withdrawn: it bounded compute only.
- **Admission.** Under the budget advisory lock (MS2-D-26), a paid run is
  admitted only if both MS2-D-40 checks pass: the project-allocation check for
  its class and the account prepaid-headroom check.
  - Class limits (revision 5): `watch_refresh` → the cycle's project allocation
    `A`; `discovery` → `A − HW_RADAR_APIFY_WATCH_REFRESH_RESERVE_USD`, so
    discovery degrades first. Remote recovery probes use `discovery`
    (MS2-D-24).
  - Outstanding reservations, including stuck or unreconciled runs of any age,
    count at their estimate. This fails closed.
  - Denials are recorded as ledger rows.
  - A kill switch `HW_RADAR_APIFY_ENABLED` (default false) denies everything.
    So do missing unit prices, a tripped overrun latch (MS2-D-26), an unknown or
    stale billing cycle, an unobservable account state, and a plan whose base
    price exceeds the cash ceiling (MS2-D-40).
- **Reconciliation.** Settlement follows MS2-D-32 and MS2-D-41. A non-null
  `usage_total_usd` alone never releases a reservation. The reservation is
  reconciled only after the import is terminal, storage deletion is verified,
  run usage is finalized, and the post-run cost is accounted. The
  outstanding-work selector (MS2-D-23) drives the re-reads. Reconciliation
  takes the same lock as admission. An actual above the reservation trips the
  latch (MS2-D-26, MS2-D-41); it is never merely logged.
- **Freshness.** `budget_paused` holds for a source when its newest ledger event
  is a budget denial after its last imported run, or while the overrun latch is
  tripped. Revision 5: the denial reason (`class_cap`, `account_headroom`,
  `overrun_latch`, `cycle_unknown`, `account_state_unobservable`, …) is part of
  the visible state (MS2-D-41).
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
    sweeps, one ID per request, with pagination (facts in F1);
  - WD Recertified;
  - Seagate Recertified;
  - ServerPartDeals, whose non-drive collections are still unverified.
- **Actor proof** (revision 5, owner-clarified (s2, 2026-09-24); OQ24 split). Two
  separate decisions replace revision 4's single "owner picks a merchant":
  - **(a) First Actor proof = a controlled synthetic source through a real
    private Apify Actor** (settled; MS2-D-42, task F5a). It exercises real
    compute, run lifecycle, dataset retrieval, delayed completion, cost
    accounting, and API behavior with no merchant legal question.
  - **(b) A production Actor-backed merchant source** is a separate
    source-admission decision per candidate (MS2-D-44, task F5b). It stays open
    as OQ24. Newegg stays excluded because its Terms of Use prohibit automated
    access and scraping (R1 has the evidence). B&H, ServerPartDeals, and
    refurbished server-parts sellers each need a source-admission record before
    selection. Existing local connectors are not grandfathered into an Actor
    path.
- **Slice D stays source-agnostic.** It is built and tested against the frozen
  contract with a synthetic fixture source.

**MS2-D-20 — Evaluation is bound to its inputs and wired on every ingestion path
(Slice C; review F-03).**
- **Validity.** A `watch_evaluation` row is *current* iff all five hold:
  - `snapshot_observed_at` equals the listing's latest `OfferSnapshot.observed_at`;
  - `resolution_id` equals the listing's current edge id (`is_current=True`),
    or both are null;
  - `requirement_version` equals the watch's `requirement_version`;
  - `evaluator_version` equals the running `EVALUATOR_VERSION`;
  - `catalog_fingerprint` equals the fingerprint of the listing's current
    catalog inputs, recomputed at read time (MS2-D-29, revision 3).
- **Read model.** Only current `match` rows qualify for `shortlist()`. A
  non-current row of any verdict is `pending`. It is shown by `review_queue()`
  with its stale binding, and it never qualifies.
- **Failure path needs no write.** Any new evidence that the evaluator did not
  assess leaves the old row non-current. That covers an evaluator exception,
  a crash, a resolver failure, and an unfinished import stage. The old row
  therefore drops out of the shortlist.
- **Wiring.** `run_collection` gains `evaluator: ListingEvaluator | None = None`.
  `None` binds the production `WatchEvaluator`; it is not a null object. So every
  path through `run_collection` evaluates with no per-caller wiring:
  `poll_source`; the FULL run that `run_heartbeat` fires (`heartbeat.py:228`);
  `recovery_probe_job` PROBE runs; and provider-import stage 4 (MS2-D-22).
  Tests inject a fake. A listing whose resolution raised in this run is not
  evaluated and stays pending.
- **Repair.** The next observation re-evaluates. `evaluate_watches --pending`
  re-evaluates every non-current row on demand.
- *Rejected (a):* an evaluator parameter each caller must pass, with a null
  default. That is exactly the omission F-03 found on the heartbeat path; any
  future caller would silently skip evaluation.
- *Rejected (b):* write-time invalidation, which marks rows pending inside the
  persistence transaction. It adds a write to the ingestion transaction and still
  leaves crash windows. Read-time binding cannot be skipped.
- *Rejected (c):* a scheduled evaluation-backlog job in MS-2. `pending` already
  fails closed. *Reopen if* F3 measures a material pending backlog.

**MS2-D-21 — New-category acceptance policy (Slice B; review F-09).**
- **Policy object.** `CategoryRules.acceptance: AcceptancePolicy | None`.
  - Drive: `None`. No gate applies, drive decisions are untouched, and A0 guards
    this.
  - gpu, ram, cpu, and every basic-watch category:
    `AcceptancePolicy(authoritative_source_kinds={"catalog_authoritative"},
    grains={MODEL, VARIANT})`.
- **Where it applies.** The resolver applies the policy after `ladder.decide`
  returns and before the `auto_accept` flag.
- **Rung 1.** An `ACCEPT` stands only if two things hold: the winning alias hit
  (the ladder's highest-confidence single-target hit) has an authoritative
  `source_kind`, and the target grain is approved. Otherwise the result is
  `REVIEW` with evidence
  `acceptance_policy={"source_kind": ..., "grain": ...}`. The OEM family fan-out
  accepts at family grain, so for these categories it becomes `REVIEW`.
- **Evidence for rung 0.** Accepted non-drive edges record
  `evidence["alias_source_kind"]`.
- **Rung 0.** A prior is inherited only if its edge is an authoritative
  `exact_alias` accept, or a `manual` owner decision. Any other prior becomes
  `REVIEW`.
- **Manual and learned aliases stay visible.** `manual` and `listing_derived`
  aliases stay in `alias_hits`. That includes aliases the resolver learned from
  earlier observations (`resolver.py:374-405`). So a collision stays
  reviewable; it never becomes a silent `none`.
- *Evidence:* `source_kind` sets confidence only (`ladder.py:28-32`, `:195`). It
  does not gate acceptance.
- *Rejected:* filtering non-authoritative hits out before `decide`. That turns
  reviewable collisions into `none`.
- *Reopen if* the owner ratifies `manual` aliases for a category (R4).

**MS2-D-22 — Durable staged provider import (Slice D; review F-05).**
Replaces revision 1's "persist and mark `imported` in one transaction".
- **Stages.** `import_state` moves `pending → observations_committed →
  absence_applied → resolved → evaluated → finalized`. The only other terminal
  state is `rejected`.
- **Transitions.** Each transition is a compare-and-set under
  `select_for_update` on the row. Every stage's effects are idempotent, so a
  duplicate executor stops at the compare-and-set.
- **Claim.** In its own transaction the claim creates the `ScraperRun`
  (RUNNING, `started_at` = Apify `startedAt`) if absent, links it, and increments
  `import_attempts`. Every retry reuses that `ScraperRun`.
- **Stage 1 (one transaction).** Before the transaction:
  - Check the retention preconditions (MS2-D-33). If the deadline has passed
    or the content is already past its source TTL, go to *Reject* before any
    read or canonical write.
  - Count the read against the read cap, then read and validate the dataset
    and `OUTPUT` (MS2-D-32). If the cap is exhausted, go to *Reject* with
    `read_cap_exhausted`.
  - Run `classify_run`. A `failed` result goes to *Reject*.

  Otherwise the transaction does: `store_raw`; the scope-row lock and the
  ordering-guarded listing observation, including absence-gated content
  writes, relist, and creation (MS2-D-30, MS2-D-35); insert-if-absent
  snapshots with per-observation retention (MS2-D-37); `import_listing_ids`;
  and state `observations_committed`. The transaction re-checks the deadline.
  A crash rolls the whole stage back; a retry is another counted read.
- **Stage 2 (one transaction).** Continuity is recorded or broken for the
  run's admitted `scope_key`, in event-time order (MS2-D-11, -31, -36). The
  gated scoped delist then runs with the newer-observation guard (MS2-D-12,
  -14, -30). A gated complete FULL scope also raises that scope's
  complete-sweep watermark (MS2-D-35). The state becomes `absence_applied`.
  PROBE runs skip absence, continuity, and the watermark (existing rule).
- **Stage 3.** Resolve each id in `import_listing_ids`, then set `resolved`.
  Resolver errors are counted in `stage_detail` and never block.
- **Stage 4.** Evaluate each listing whose resolution did not raise (MS2-D-20),
  then set `evaluated`. Evaluator errors are counted; the affected rows stay
  pending (fail-closed).
- **Stage 5 (one transaction).** Set the `ScraperRun` to SUCCESS with its
  counts and `detail_json` (provider evidence, resolver and evaluator errors,
  `listings_delisted`). Apply `apply_run_outcome`. Set `finalized`. The
  compare-and-set makes the lifecycle outcome apply exactly once.
- **Reject (one transaction).** Covers `failed` completeness, an invalid
  contract, unknown retention (MS2-D-25), a storage deadline passed before
  stage 1, content already past its source TTL (MS2-D-33), and an exhausted
  read cap (MS2-D-32). The `ScraperRun` becomes FAILED with a classification,
  and the state becomes `rejected`. What else happens depends on `run_kind`
  (revision 3, review F-07 residual):
  - **FULL:** the failure lifecycle outcome is applied, and continuity is
    broken for the run's admitted `scope_key`, with the run's `startedAt` as
    the break's event time, or `admitted_at` when no start response was
    recorded (MS2-D-31, MS2-D-36).
  - **PROBE:** the outcome is `PROBE_FAILURE` (state-neutral, MS2-D-24).
    Continuity is never touched, so a rejected probe cannot shorten or reset a
    lane it did not sweep.
- **Resumption.** The outstanding-work selector (MS2-D-23) resumes a row from its
  recorded stage. Only stage 1 needs the dataset.
- **Overrun blocks repair reads** (revision 5, owner-clarified (s2, 2026-09-24)).
  While the overrun latch is tripped, a stage-1 retry that would read the dataset
  or the `OUTPUT` record again is not attempted: an overrun never triggers another
  paid call to repair itself. The row waits, visibly, until the latch clears.
  Retention-required storage deletion (MS2-D-25, MS2-D-33) still runs, because its
  cost is already reserved and deletion stops further storage accrual.
- **Code shape.** D extracts the stages from `run_collection` into functions.
  The local path composes the same functions in memory with no durable markers.
  A crashed local run is repaired by the next poll, and its evaluations stay
  pending (MS2-D-20).
- *Rejected (a):* committing persistence and a terminal marker together
  (revision 1). A crash after that commit strands delist, resolution,
  evaluation, and finalization forever.
- *Rejected (b):* one transaction over all stages. A resolver or evaluator
  failure must not roll back ingestion (C.3), and the transaction would hold
  locks across hundreds of resolutions.
- *Rejected (c):* an external task queue. It is a second scheduler (ADR 0012).

**MS2-D-23 — Remote execution status is separate from local outstanding work
(Slice D, E; review F-06).**
- **Two axes.**
  - Remote: `remote_status` is the Apify enum. `remote_terminal_at` is set when
    the job first sees a terminal status. It is an observation fact only and
    never anchors a deadline (MS2-D-33).
  - Local: `import_state` (MS2-D-22), `storage_state`
    (`retained | deleted | delete_failed`, MS2-D-25), and, from E, the
    reservation `status` (`reserved | usage_provisional | usage_finalized |
    reconciled`, MS2-D-41; revision 5 renames rev 4's `usage_observed`).
- **Selector 1, active.** Rows where `remote_status` is set and non-terminal.
  A row with a null status has no run id to poll, so only selector 3 covers
  it. Each row is polled with `GET` run, which updates status, ids, and usage.
- **Selector 2, outstanding.** Rows where `remote_status` is terminal and any of
  these holds:
  - `import_state ∉ {finalized, rejected}`;
  - `storage_state ≠ deleted` and the import is terminal;
  - (E) the reservation is not yet `reconciled` (MS2-D-32).
- **Selector 3, overdue storage (revision 3, MS2-D-33).** Rows where
  `storage_state ≠ deleted` and `storage_cleanup_due_at ≤ now`, whatever the
  remote status: null (start response never recorded), non-terminal, or
  terminal. It does not depend on selector 1 ever having observed termination.
- **Isolation and restart.** Each unit of work is claimed and committed
  separately, with per-row backoff via `next_attempt_at`, so one failing row
  never starts the others. Both selectors read only persisted state, so a poller
  restart resumes every row.
- *Rejected:* revision 1's "poll non-terminal rows and import terminal ones". A
  row whose remote status is terminal would never be selected again. That
  strands unfinished imports, failed deletions, and late usage.
- *Rejected:* revision 2's terminal-only storage clause. A run whose terminal
  status was never observed (poller outage, lost start response) had no
  deadline selector at all.

**MS2-D-24 — Recovery probes dispatch by the selected provider (Slice D;
review F-07).**
- **Dispatch.** `recovery_probe_job` branches on
  `SourceConfig.collection_provider`.
  - `local` runs today's path byte for byte.
  - `apify` calls `start_provider_run(source, run_kind=PROBE)`. That path uses
    the same `check_admission(PROBE)` and then budget admission as class
    `discovery`, so a paused source cannot spend watch-refresh headroom. It
    applies the bounded run options (MS2-D-26), allows at most one outstanding
    probe run per source, completes asynchronously (MS2-D-22/-23), and uses
    registered retention (MS2-D-25).
- **Probe outcome.** When a PROBE import finalizes, the outcome is
  `PROBE_SUCCESS` iff completeness is `complete` or `truncated`. Otherwise it is
  `PROBE_FAILURE`, which is state-neutral.
  - A budget denial starts no run. The source stays paused and the denial is
    recorded.
  - A **rejected** PROBE import (invalid `OUTPUT`, ambiguous-empty output, a
    passed storage deadline, or any other MS2-D-22 reject reason) records
    `PROBE_FAILURE` (revision 3, review F-07 residual).
  - A PROBE import never reaches absence or continuity, on any path,
    including *Reject*.
- **The local adapter never runs for an Actor-backed source.** It is never
  invoked for an `apify` source in any job, even when one is registered.
- *Rejected:* probing an Actor-backed source with its local adapter. That
  "recovers" the wrong execution provider, and an Actor-only source could
  never recover.

**MS2-D-25 — Provider-independent source retention and remote storage cleanup
(Slice D; review F-10).**
- **Registry.** A new pure module `acquisition/retention_policy.py` holds
  `SOURCE_RETENTION: Mapping[str, AdapterRetention]`, keyed by `site_key`.
  `source_retention(site_key)` raises `UnknownSourceRetention` for an
  unregistered key.
- **Remote imports.** Every remote import passes the policy explicitly to the
  stage functions as a required keyword with no default. Unknown retention
  rejects the import. It never falls back to `run_collection`'s `merchant_fact`
  default (`contracts.py:136-153`).
- **Local path.** The local path keeps `adapter_retention(adapter)` in MS-2.
  A test pins `adapter_retention(ADAPTERS[k]()) == source_retention(k)` for every
  registered adapter, so the two sources of truth cannot drift. An Actor-only
  source must be registered in the registry.
- **Cleanup deadline.** The deadline is absolute and set at admission
  (MS2-D-33, revision 3):
  `storage_cleanup_due_at = admitted_at +
  min(HW_RADAR_APIFY_STORAGE_CLEANUP_MAX, bounded_ttl / 2)`.
  Revision 2 anchored it at `remote_terminal_at`, the first *locally observed*
  termination, so a poller outage longer than a source TTL granted content a
  fresh allowance. That anchor is withdrawn.
  - The default for `HW_RADAR_APIFY_STORAGE_CLEANUP_MAX` is 24 h (an
    assumption; tunable).
  - The `bounded_ttl / 2` term applies only to bounded sources, which MS-2
    denies an Actor path anyway (MS2-D-33). It stays in the formula so the
    bound is already correct if that denial is ever lifted.
  - The deadline is always far inside Apify's default-storage expiry: 7 days
    on Free, 31 days on paid plans (Apify help center, retrieved 2026-09-24).
- **Cleanup.** Cleanup deletes the run's default dataset and default KV store.
  An HTTP 404 counts as deleted. It runs for successful, rejected, failed, and
  abandoned runs. It fires when the import reaches a terminal state (selector 2)
  or when the deadline passes (selector 3), whichever comes first, independent
  of import success and of any remote-status observation.
  - If the deadline passes before stage 1 committed, the import is `rejected`
    with `storage_deadline`: retention wins over completeness.
  - Failures retry with backoff up to the delete-attempt cap (MS2-D-32). A row
    still `delete_failed` past its deadline logs at error level and appears in
    the pilot and spend reports.
- **Actor contract.** The Actor may write merchant content only to the default
  dataset. The `OUTPUT` record in the default KV store holds only counts, scope,
  and errors. Revision 5: this is a review item on the hw-radar PR that changes
  `actors/`, backed by an Actor test that asserts the KV store holds only
  `OUTPUT` (MS2-D-38).
- *Rejected (a):* a retention column on `SourceConfig`. Legal retention would
  become admin-editable, and `expires_policy` is a callable. The code registry is
  reviewed and tested.
- *Rejected (b):* deleting only after a durable import (revision 1). Rejected,
  failed, and abandoned datasets would then live until Apify's own expiry, far
  beyond bounded TTLs.

**MS2-D-26 — Every admitted charge is bounded; an overrun latch pauses paid
admission (Slice E; review F-08).** This decision amends MS2-D-17.
- **Charge components.** Unit prices are from the official pricing page
  (apify.com/pricing, retrieved 2026-09-24). Each price is a setting with a
  cited URL and date. For live admission, no price setting has a default.

  | Component | Bound in the reservation | Enforced by |
  | --- | --- | --- |
  | Compute | `memory_mb/1024 × timeout_s/3600 × usd_per_cu` ($0.20/CU on Free/Starter) | The start request's `memory` and `timeout` query parameters. The start response's `options` must equal the request; on a mismatch the run is aborted and the latch trips. |
  | Dataset and KV writes during the run | `max_items × dataset write price` + `max_kv_writes × KV write price` | Actor input caps `maxItems` and `maxPages`, and a per-row byte cap (contract schema `maxLength`). The Actor contract allows only `OUTPUT` in the default KV store, written at most `max_kv_writes` times, with a byte cap. Import rejects over-cap rows. A dataset count above `max_items` trips the latch. |
  | Post-run reads, deletes, and timed storage | MS2-D-32: `max_dataset_reads × max_items × read price` + `max_kv_reads × KV read price` + `2 × max_delete_attempts × op price` + timed storage for `max_items × max_item_bytes + max_kv_bytes` over `…_STORAGE_MAX_LIFETIME` | hw-radar's own counters, incremented before each operation (MS2-D-32). Storage time is bounded by the platform's default-storage expiry, not by cleanup success. |
  | Data transfer | `max_requests × max_response_bytes × transfer unit price` | Actor input caps. If the transfer rate's direction semantics cannot be verified in E2, live admission stays disabled. |
  | Proxy | Disallowed: $0 | Contract and Actor input carry no proxy configuration, and an Actor test asserts the Actor source never constructs `ProxyConfiguration` (MS2-D-38, MS2-D-44). Any non-zero proxy component in the run's `usage` breakdown trips the latch. Residential proxies are never used, although the account has the residential-proxy feature available (verified account state 2026-09-24), so policy and tests, not the account, prevent it. |

- **Reservation.** `(Σ component bounds) × (1 + margin)`. If any unit price is
  missing, admission is denied with `pricing_unverified`. If any component
  lacks an enforceable bound (for example an unset `…_STORAGE_MAX_LIFETIME`),
  admission is denied with `unbounded_component` (MS2-D-32).
- **Ceiling** (revision 5, **owner-overridden**; OQ23 resolved). Revision 4's
  `effective_hard_cap = $20 − HW_RADAR_APIFY_CAP_DEDUCTION_USD −
  HW_RADAR_APIFY_SAFETY_MARGIN_USD` is withdrawn, with both settings. The owner's
  rule is a **cash ceiling plus attributable consumption**, enforced by MS2-D-40:
  - The hard owner ceiling is the account's total Apify **cash outlay**, about
    $20 per billing cycle. The plan's subscription fee counts, because it is
    actual money. The prepaid platform usage that fee buys is not charged a
    second time.
  - Hardware Radar's **attributable platform consumption** targets at most $12
    per cycle, and it may use only the lesser of that target and the prepaid
    allowance actually remaining after the account's other workloads.
  - No pay-as-you-go overage is relied on for normal operation, so admission
    never lets Hardware Radar's worst case exceed the remaining prepaid
    allowance. There is no "$12 plus $19" and no "$20 of run charges on top of
    the subscription".
- **Invariant** (revision 5). Per class, within the current billing cycle:
  `consumed_in_cycle + Σ unreconciled reservations touching the cycle + new
  reservation ≤ class cap`, and the account-headroom check of MS2-D-40.
  "Unreconciled" means `reserved`, `usage_provisional`, or `usage_finalized`, each
  counted at its full estimate (MS2-D-32, MS2-D-41).
- **Cycle attribution** (revision 5, owner-overridden; MS2-D-34 as revised
  replaces the revision-4 31-day text).
  - `reserved_at` is fixed for good and is provenance: it records when admission
    happened.
  - A reservation's *charge interval* runs from `reserved_at` to its
    `last_charge_at` (MS2-D-34), each widened by the cycle-boundary guard
    (MS2-D-40). While a reservation is not `reconciled`, its interval is open
    ended.
  - A reservation counts, at its settled amount once reconciled and at its full
    estimate before that, in **every** billing cycle its charge interval
    intersects. Counting the whole amount in each such cycle over-counts, which
    fails closed.
- **Serialization.** Reserve, reconcile, latch trip, and latch reset each take
  the same `pg_advisory_xact_lock(APIFY_BUDGET_LOCK)`.
- **Overrun latch.** A new `apify_budget_latch` table in migration 0022 records
  each trip and each clear.
  - Trip conditions at reconcile: actual > the reserved estimate (revision 5,
    owner-clarified (s2, 2026-09-24): "higher actual → overrun state". The
    estimate already carries its `(1 + margin)`, so revision 1–4's extra
    `HW_RADAR_APIFY_OVERRUN_TOLERANCE` is withdrawn); any proxy usage; a dataset
    over its cap; or a start-option mismatch, which revision 5 extends to a
    started build outside the contract's version line (MS2-D-38). Revision 3 adds
    an exhausted delete-attempt cap and an orphaned start (MS2-D-32, -33).
  - While tripped, no paid call is made to repair the overrun: no new run, no
    probe, and no dataset re-read (MS2-D-22). The state is visible in the
    freshness and spend report as `budget_overrun`.
  - While tripped, all paid admission is denied with `overrun_latch`, and every
    Apify source shows `budget_paused`.
  - It clears only through the owner command `apify_budget_reset --reason`, or
    through an estimator correction: a bump of
    `HW_RADAR_APIFY_ESTIMATOR_VERSION`, recorded as a clear event.
- **Late usage.** The reservation stays counted at its full estimate until it
  is settled under MS2-D-32 and MS2-D-41. A first non-null `usage_total_usd`
  does not settle it; it is provisional. A reservation still unsettled when its
  admission cycle ends is carried, at its full estimate, into the next cycle
  and reported as `unreconciled_stale` for owner action (revision 5 replaces
  "after 31 days").
- *Rejected:* revision 1's "the estimate is a hard bound" with an assumed
  overhead. Storage, transfer, and proxy were unbounded, and an overrun was only
  logged.

**MS2-D-27 — Category hint through the corpus tooling (Slice B task B6; review
F-02).**
- **Corpus schema.** `ListingFields` (`matching/eval/corpus.py:181`,
  `extra="forbid"`) gains `category_hint: str | None = None`, with the A2 slug
  validation.
- **Harvest.** `harvest_corpus._staging_entry` (`:70`) writes a
  `"category_hint"` key only when the hint is non-null.
- **Replay.** `eval/evaluate._ingest` (`:106`) passes the hint into the
  `ParsedListing` it rebuilds.
- **Compatibility.** The existing drive corpus has no such key and loads
  byte-identically. Staging output for unhinted listings is byte-identical. The
  A0 baseline is unchanged.
- **Why Slice B, not F.** The hint changes a decision only once a non-drive
  category is registered (B3). F4 should be pure measurement, with no schema
  change inside it.
- *Rejected:* carrying the hint in `attrs`. A2 forbids the reserved key.

**MS2-D-28 — Remote rows skip the HTTP soft-block classifier (Slice D;
discovered during revision 2).**
- **Hazard.** `_classify_batch` flags any item whose body is under 20% of the
  site's median per-item body size (`classify.py:55-59`). That median comes from
  the site's prior successful FULL runs (`pipeline.py:171-187`). Dataset rows are
  small JSON, while local runs store full pages, so after a provider switch
  every imported row would be classified `ANTI_BOT`.
- **Decision.**
  - The import path does not run `_classify_batch`. Remote transport health comes
    from `classify_run`.
  - `_median_body_bytes` counts only runs whose `detail_json["provider"]
    ["provider_kind"]` is `local` or absent.
- *Rejected:* a separate per-provider median. Remote rows are not HTTP responses,
  so a soft-block ratio does not apply to them.

**MS2-D-29 — Evaluations are bound to their catalog inputs (Slice C,
migration 0020; review F-03 residual).**
- **Hazard.** Product clauses read catalog specs (MS2-D-08), and those specs
  change without any listing-side event. The refdata importer updates specs in
  place (`refdata/persist.py:208`), and family membership is reassigned in
  place (`persist.py:192-193`). The refresh loop reconsiders only NONE and
  FAMILY listings (`refdata/refresh.py:65-68`). A same-target re-resolution
  keeps the existing edge (`matching/resolver.py:581-592`). Correcting a GPU's
  cooling or TDP therefore changes none of the four revision-2 binding fields,
  and an old `match` would stay current.
- **Catalog inputs.** One function, `eligibility.catalog_inputs(listing) ->
  CatalogInputs`, gathers everything product clauses read. The evaluator and
  the validity check both call it, so they cannot drift. It covers:
  - model or variant grain: the dispatch category, the target ids, and the
    typed `*_spec` field values of the target model, or an explicit `absent`
    marker when the spec row is missing;
  - family grain: the family id and category, the sorted member model ids,
    and each member's typed spec values, which are the agreement-set inputs;
  - no accepted edge: a constant marker. Product clauses are `unknown` there
    anyway.
- **Fingerprint.** `catalog_fingerprint = sha256(canonical JSON of
  CatalogInputs + CATALOG_INPUTS_VERSION)` is stored on `watch_evaluation`
  (CharField(64)). Validity (MS2-D-20) recomputes it at read time.
  `shortlist()`, `review_queue()`, and `evaluate_watches --pending` share one
  predicate, which batches the spec reads for the candidate rows.
- **Effect.** A same-target catalog correction makes the row non-current
  immediately, so it drops out of `shortlist()` and shows as `pending`, with
  no new observation and no resolver run. An edit to an unrelated model
  changes no fingerprint, so evaluations do not churn.
- *Rejected (a):* atomic invalidation when specs or family membership change.
  Every writer would need a hook: the refdata importer, Django admin (B5
  registers the satellites), family reassignment, and any future queryset
  `.update()`, which bypasses model signals. One missed writer means a silent
  stale pass. MS2-D-20 rejected write-time invalidation for the same reason.
- *Rejected (b):* one catalog-wide revision counter. Any unrelated catalog edit
  would make every evaluation pending, and every writer would still have to
  bump it.
- *Reopen if* F3 measures material shortlist latency from recomputing
  fingerprints. The replacement would then be a database-trigger-maintained
  per-model revision column, which cannot be skipped by any writer.

**MS2-D-30 — Monotonic observation and absence watermarks (Slice D,
migration 0021; review N-01).**
- **Hazard.** A durable asynchronous import can finish after a newer run or a
  local poll. Today `upsert_listing` overwrites title, condition, international
  status, and retention unconditionally (`acquisition/persist.py:59-70`).
  `_persist_all` relists every seen listing (`pipeline.py:284`), and
  `_apply_delist` delists every unseen candidate (`pipeline.py:182-205`).
  Deterministic `observed_at` values make snapshots idempotent, but they do not
  make these current-state effects chronological. eBay's delete-on-delist
  redaction makes a false delist destructive.
- **Watermarks.**
  - Observation: a new nullable `Listing.last_observed_at` holds the
    `observed_at` of the newest observation applied to current state. It is
    only ever raised, never lowered. A NULL means "no observation since the
    column existed", so the guards treat it as older than any observation. No
    backfill is needed.
  - Absence, per listing: a new nullable `Listing.last_absence_at`, raised by
    `mark_delisted` to its evidence time and never lowered (MS2-D-35).
  - Absence, per scope: a complete-sweep watermark `last_complete_sweep_at`
    (MS2-D-35), plus the continuity watermarks `last_eligible_sweep_at` and
    `continuity_broken_at` (MS2-D-31, MS2-D-36).
- **Guards.** Each runs in the persisting transaction under
  `select_for_update` on the listing row, on the local and the import path
  alike (revision 4 wording; MS2-D-35 gives the absence half):
  - *Current state.* A new `persist.observe_listing(site, normalized,
    retention_class, *, expires_at, observed_at)` replaces the content
    columns (`canonical_url`, `url_hash`, `title_raw`, `condition_label_raw`,
    `is_international`, `retention_class`, `expires_at`, `collection_scope`),
    relists, and raises `last_observed_at` only when the observation is
    *current-eligible*: `observed_at ≥ last_observed_at` (or the watermark is
    NULL) **and** `observed_at` is not older than any absence watermark that
    applies to the row (MS2-D-35). Content writes and revival share this one
    predicate, so no path can restore content that a newer delist retired
    or extend its `expires_at`. An observation that is not current-eligible
    leaves the row untouched, so `last_seen` is not bumped either.
    `upsert_listing` keeps its signature for existing callers.
  - *Creation.* A key with no row is created active only if the observation
    is not older than its scope's complete-sweep watermark. Otherwise it is
    created already delisted (MS2-D-35).
  - *Absence.* `_apply_delist` excludes candidates whose `last_observed_at >
    scope.observed_at`, and re-checks that under the row lock before
    `mark_delisted`. A listing observed or first seen by a newer run is never
    delisted by an older sweep, whether complete or stale.
  - *History.* Snapshots append with insert-if-absent on `(listing_id,
    observed_at)`, carrying the observation's own retention, not the current
    row's (MS2-D-37). "Latest snapshot" is ordered by `observed_at`
    (`resolver.py:156`), so an older snapshot never replaces the current
    offer, and the MS2-D-20 binding is unaffected.
- **Local path.** A local run's `observed_at` is its own fetch time, so it is
  normally the newest. The guards are then no-ops, and the frozen pipeline and
  delist tests stay green.
- *Rejected (a):* refusing an import whose run is older than the listing's
  newest observation. That drops legitimate history, and it cannot protect
  listings that only the older run saw.
- *Rejected (b):* ordering by ScraperRun start alone. Listings are touched by
  more than one run, so ordering has to be decided per row.
- *Reopen if* a source supplies per-row `observedAt` values that differ
  materially from run start. The watermark would then use the row value.

**MS2-D-31 — Sweep continuity per collection scope (Slice D, migration 0021;
review N-02).**
- **Hazard.** MS2-D-12 makes scopes independent collection units, but
  continuity is one FULL-lane value per source, and its previous-run lookup
  counts every successful FULL `ScraperRun` on the site
  (`pipeline.py:123-141`). Repeated complete GPU runs can keep that value alive
  while RAM, or the legacy scope, is never swept. A later incomplete sweep of
  that scope can then stale-delist on borrowed history.
- **Decision: per-scope tracking.** Scopes are provider-independent
  (MS2-D-12), so one record serves local and Actor runs of the same scope:
  - **Legacy NULL scope:** keeps the lane's `continuous_since` and the
    `ScraperRun` previous-run lookup for gap detection, so in-order legacy
    polling behaves exactly as today. Revision 4 (MS2-D-36) makes it ordered:
    the FULL lane row gains the NULL scope's own `last_eligible_sweep_at` and
    `continuity_broken_at`, and the record and break rules below apply to it
    under `select_for_update` on the lane row. One extra rule remains: a
    successful FULL run that did not sweep the NULL scope **breaks** the
    NULL-scope continuity, with the run's `observed_at` as the break's event
    time. That keeps a scoped run from ever serving as the NULL scope's
    predecessor in the site-level lookup. Before D every run sweeps the NULL
    scope, so nothing changes until scoped runs exist.
  - **Non-null scopes:** a new table `scope_sweep_continuity` (`source_site`
    FK, `collection_scope` CharField(100) not null, `continuous_since` null,
    `last_eligible_sweep_at` null, `continuity_broken_at` null,
    `last_complete_sweep_at` null, `updated_at`; unique `(source_site,
    collection_scope)`). Gap detection reads the row's own
    `last_eligible_sweep_at`, never another scope's runs. The tolerance is
    `max(2 × FULL current_interval_s, MIN_CONTINUITY_TOLERANCE)`, as for the
    lane.
  - **Record and break, every scope** (MS2-D-36 gives the full rule):
    - *Record:* under the row lock, an eligible sweep observed at `t` changes
      nothing if `t ≤ continuity_broken_at` or `t < last_eligible_sweep_at`.
      Otherwise it restarts `continuous_since` at `t` if the value is null or
      the gap exceeds tolerance, then sets `last_eligible_sweep_at := t`.
    - *Break:* sets `continuous_since := None` and raises
      `continuity_broken_at` to the break's event time.
- **Which scopes a run swept.**
  - A local run: the `scope_key` of each `DelistScope` it returns (F1's
    multi-scope capability can return several). A scope-less local run swept
    the NULL scope.
  - A remote run: the `scope_key` it was *admitted* for
    (`provider_run.scope_key`), never the Actor's own claim.
  - Eligibility per swept scope follows MS2-D-11. A run leaves scopes it did
    not sweep untouched, except the NULL-scope break rule above.
- **Delist.** `_apply_delist` reads the continuity of the scope it is
  applying. Stale absence for a scope needs that scope's own grace of
  continuous sweeps.
- **Slice and migration.** Slice D, D2 (migration `0021`), with the
  pipeline wiring in D10. F1 applies it per sweep. Slice A needs no change,
  because scopes do not exist before D.
- *Rejected (a):* disabling stale absence for every non-null scope. eBay
  category sweeps above 10,000 items (F1, R8) could then never retire an
  absent listing except by retention expiry. It also leaves the legacy
  scope's reverse hazard (remote scoped runs bridging a later local NULL
  sweep) unfixed.
- *Rejected (b):* moving the NULL scope into the new table too. It changes the
  mechanism that frozen tests pin, for no behavior gain.
- *Consequence (assumption):* when Actor runs rotate scopes more slowly than
  every FULL tick, the per-scope gap can exceed the tolerance, so continuity
  keeps restarting. That fails closed, because stale absence simply does not
  fire. Remote runs are never stale-eligible in any case.
- *Reopen if* per-scope cadence becomes configurable; the tolerance should
  then use the scope's own interval.

**MS2-D-32 — The reservation bounds cumulative authorized work; liability is
retained until settlement (Slice D counters, Slice E ledger, migrations 0021
and 0022; review F-08 residual).** This decision amends MS2-D-17 and MS2-D-26.
- **Hazard.** Revision 2 reserved one dataset read and storage only until the
  cleanup deadline. A stage-1 rollback permits another full read, and a failed
  cleanup keeps billable storage past the deadline. Reconciling on the first
  non-null usage released the reservation while import reads and cleanup
  could still charge.
- **Operation caps.** These are enforced by hw-radar's own counters. Each
  counter is incremented and committed *before* its operation, so a crash
  mid-operation still counts it.
  - Dataset reads: `provider_run.dataset_read_count ≤
    HW_RADAR_APIFY_MAX_DATASET_READS` (default 3, an assumption). A full
    paginated read counts once. At the cap, the import is rejected with
    `read_cap_exhausted` (MS2-D-22) and cleanup proceeds.
  - KV reads: `kv_read_count ≤ HW_RADAR_APIFY_MAX_KV_READS` (default 3, an
    assumption), with the same rejection.
  - Deletes: `storage_cleanup_attempts ≤ HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS`
    (default 10 per storage, an assumption). At the cap, automatic retries
    stop, the row stays `delete_failed` and is reported, and the latch trips.
    The remote storage then expires at platform expiry, which the storage
    component already reserved.
  - KV writes and bytes: the Actor contract bounds them (MS2-D-26 table), and
    a KV store over its byte cap trips the latch.
  - `GET` run polls are assumed non-billable. E2 verifies that. If they are
    billable, live admission stays denied until a poll cap is added. The same
    applies to the two account reads (MS2-D-15, revision 5).
- **Reservation split.** The estimate has two recorded parts in
  `component_bounds`:
  - an *execution* part: compute, run-time writes, transfer, and a $0 proxy;
  - a *post-run liability*: capped reads, capped deletes, and timed storage
    over `HW_RADAR_APIFY_STORAGE_MAX_LIFETIME`. That setting is the plan's
    default-storage expiry. It has no default; unset denies live admission
    with `unbounded_component`.
- **Settlement** (revision 5 names the states per MS2-D-41). Status runs
  `reserved → usage_provisional → usage_finalized → reconciled` (`released` for
  a start that never ran, `denied` for denials).
  - A non-null `usage_total_usd` read is `usage_provisional` until it is
    finalized (MS2-D-41). The full estimate stays counted in every state before
    `reconciled`.
  - `reconciled` requires all of: `import_state ∈ {finalized, rejected}`;
    `storage_state = deleted` (verified, including 404); finalized run usage;
    and the post-run cost accounted after `final_charge_op_at`. The
    `final_charge_op_at` field is stamped after the last read or delete.
  - The settled amount is the finalized run usage **plus** the post-run cost
    (revision 5, owner-clarified (s2, 2026-09-24)). The official docs say
    post-run dataset reads are account usage, and no source says they are added
    to the run's own figure, so revision 4's "final usage covers them if E2
    verifies it" branch is withdrawn. Until F5a's measurement is recorded, the
    post-run cost is the full post-run liability bound, which counts as spent
    and fails closed (MS2-D-41 *Post-run cost*).
  - The overrun check compares the settled amount with the estimate.
- **Window.** Revision 5: a settled row counts in every billing cycle its charge
  interval touches (MS2-D-34 as revised). A row that is not `reconciled` counts
  in every cycle regardless of age.
- **Live admission stays denied** wherever a component lacks an enforceable
  bound: a missing unit price, unverified transfer direction, an unset storage
  lifetime, unverified poll billing, or a missing operation-cap setting.
- *Rejected (a):* reconciling on the first non-null usage (revision 2). It
  releases liability while charge-producing work remains.
- *Rejected (b):* pricing storage only until the cleanup deadline. Cleanup can
  fail, and only platform expiry is an enforced bound.
- *Reopen if* Apify documents per-storage retention settable per run. The
  storage lifetime could then be that value.

**MS2-D-33 — Absolute retention deadline from admission (Slice D, migration
0021; Slice E latch wiring; review F-10 residual).** This decision amends
MS2-D-25.
- **Anchor.** `provider_run` is created at admission, before the start
  request, with `admitted_at` and the absolute `storage_cleanup_due_at`
  (MS2-D-25 formula). `external_run_id`, `remote_status`, and the storage ids
  are filled from the start response. The deadline never moves later. An
  Actor cannot collect before it starts, so `admitted_at` is no later than the
  earliest collection time, and the anchor is conservative.
- **Timeout fits the deadline.** The start job (D5) refuses the start, before
  budget admission and with no spend, with `timeout_exceeds_retention`
  unless `timeout_s + HW_RADAR_APIFY_IMPORT_MARGIN ≤ storage_cleanup_due_at −
  admitted_at`. The margin defaults to 1 h (an assumption).
- **Remote expiry: deny bounded-source Actor paths.** The start job refuses
  the start, before budget admission, with
  `bounded_retention_unenforceable` for every site whose registered retention
  (MS2-D-25) is bounded.
  - *Why:* hw-radar's deletion is the only remote-expiry mechanism, and it can
    fail. The platform fallback (days) exceeds bounded TTLs (hours). No
    per-run storage-expiry setting is confirmed (MS2-D-15 facts).
  - This does not affect the pilot. eBay, the only MS-1 adapter with a
    bounded class (`ebay.py:131`), collects through its local API.
  - Fixture imports of bounded sources remain tested (D11), because the import
    path must still honor retention if the denial is lifted.
- **Overdue selector.** Selector 3 (MS2-D-23) acts on any row past its
  deadline with storage not `deleted`, whatever its remote status:
  1. Abort the run unless it was observed terminal. An abort that finds the
     run already finished counts as success. The exact response is an
     assumption that D3 verifies alongside the other wire facts.
  2. Delete the dataset and KV store.
  3. Reject the import with `storage_deadline` if stage 1 has not committed.

  A row whose start response was never recorded has no storage ids. It is
  marked `orphaned_start`, reported, and trips the latch (from E), because
  admitted spend is now untracked.
- **Expired content.** Before stage 1 reads or writes anything, and again
  inside its transaction, the import is rejected with `retention_expired` if
  `expires_policy(observed_at) ≤ now` for a bounded class. The stage-1
  transaction also re-checks `storage_cleanup_due_at > now`. Expired content
  never reaches canonical tables.
- *Rejected (a):* anchoring at `remote_terminal_at` (revision 2). A delayed
  observation extends retention.
- *Rejected (b):* anchoring at the Actor's reported `startedAt`. It is known
  only after a successful poll, so a run that is never observed would have no
  deadline.
- *Rejected (c):* relying on the platform's default-storage expiry for bounded
  sources. It is days, not hours.
- *Reopen if* D3 verifies a per-run or per-storage expiry that can be set at or
  below the source TTL. Bounded-source Actor paths could then be admitted.

**MS2-D-34 — Settled spend counts in every billing cycle its charges can touch
(Slice E, migration 0022; review F-08 residual, round 3; revision 5
owner-overridden: the period is the billing cycle, MS2-D-40).** This decision
amends the *Window* rules of MS2-D-26 and MS2-D-32. The hazard and the
`last_charge_at` horizon are unchanged from revision 4; the window predicate is
replaced.
- **Hazard.** Revision 3 aged a reconciled reservation out at `reserved_at` +
  31 days + `…_STORAGE_CLEANUP_MAX`, but MS2-D-32 lets cleanup succeed after
  its deadline (retries up to the delete-attempt cap). Counterexample: a run
  is reserved on day 0, misses its day-1 cleanup deadline, deletes on day 3,
  and reconciles. Its settled amount left the window shortly after day 32,
  while its day-3 delete and storage charges still fall inside billing cycles
  that run to day 34. That under-counts ADR 0021's hard monthly ceiling.
- **Horizon.** At reconciliation, under the budget lock, set
  `ApifySpendReservation.last_charge_at := max(provider_run.final_charge_op_at,
  provider_run.finished_at)`, ignoring a null `finished_at`.
  `final_charge_op_at` is stamped after the last read or delete, and
  reconciliation requires verified deletion (MS2-D-32), so it also ends the
  run's timed storage. Compute ends at `finished_at`.
- **Cycle predicate** (revision 5; replaces revision 4's
  `last_charge_at ≥ now − 31 days`). For billing cycle `Y = [start, end]`,
  `consumed(Y)` sums `actual_usd` over `reconciled` rows whose charge interval
  `[reserved_at − guard, last_charge_at + guard]` intersects `Y`. A row that is
  not `reconciled` counts at its full estimate in every cycle from its
  admission cycle onward. `guard` is `HW_RADAR_APIFY_CYCLE_BOUNDARY_GUARD_S`
  (MS2-D-40), which absorbs clock skew between hw-radar and Apify at a cycle
  edge.
- **Why this is conservative.** Each charge a run produces falls between its
  admission and its final charge-producing operation (compute ends at
  `finished_at`; reads and deletes end at `final_charge_op_at`; verified
  deletion ends timed storage). Every cycle that can hold any of its charges
  therefore intersects the interval, and the whole amount is counted there,
  which over-counts and fails closed. Revision 4's "31 days suffices" argument
  is withdrawn with the rolling window.
- A trailing-31-day sum of the same rows is still computed, for the spend
  report's trend view only (MS2-D-17).
- *Rejected (a):* accounting each component at its actual charge time. The
  confirmed run fields (MS2-D-15) give a usage total and a component
  breakdown with no charge timestamps, so per-component times would be
  hw-radar guesses. One horizon needs no such data.
- *Rejected (b):* a longer fixed window, such as `reserved_at` plus the
  delete-attempt cap times the backoff. Retry timing has no hard bound during
  an outage, so any fixed extension can be exceeded.
- *Rejected (c)* (revision 5): attributing each reservation only to its
  admission cycle. A run admitted just before a cycle ends charges compute,
  reads, deletes, and storage in the next cycle.
- *Reopen if* Apify exposes timestamped per-run charge events.

**MS2-D-35 — Absence watermarks gate current content and key creation
(Slice D, migration 0021; review N-01 residual, round 3).** This decision
amends MS2-D-30.
- **Hazard.** Revision 3 gated revival and staleness against
  `last_observed_at`, but not current-content writes against newer absence,
  and it created unseen keys normally. Two timelines broke it:
  1. A listing observed at t0 is delisted at t2 (and redacted, for a
     delete-on-delist class). A delayed observation from t1 then arrives.
     Since t1 > t0, the revision-3 current-state guard restored its content
     and extended `expires_at`; only the relist was refused. The existing
     `mark_delisted` (`market.py:330-377`) stamps `delisted_at`, pulls bounded
     expiry forward, and redacts, but advances no watermark that
     current-state writes consult.
  2. A complete sweep of scope S at t2 omits key K, which has no row. A
     delayed t1 import then creates K as active, despite newer complete
     evidence that K is absent.
- **Listing absence watermark.** A new nullable `Listing.last_absence_at`.
  `mark_delisted` raises it to `when` (never lowers it) in its own
  transaction. `mark_relisted` does not clear it. No backfill: guards read
  the *effective absence* `greatest(last_absence_at, delisted_at)`, ignoring
  NULLs, so rows delisted before `0021` are covered by `delisted_at`.
  - *Why a new column:* `delisted_at` alone is cleared by `mark_relisted`, so
    the guard would depend on every present and future relist path also
    raising `last_observed_at`.
- **Scope complete-sweep watermark.** `last_complete_sweep_at` per scope:
  for the NULL scope on the FULL `SourceLaneState` row, for non-null scopes on
  `scope_sweep_continuity` (MS2-D-31). It is raised to `scope.observed_at`,
  never lowered, by the delist stage of a FULL run whose gated scope is
  complete, in the same transaction as its `ABSENT_FROM_SWEEP` marks. That
  includes a complete-empty scope and a complete sweep that delisted nothing,
  on the local and the import path. Probes, incomplete scopes, stale-absence
  sweeps, and rejected runs never raise it.
- **Current-eligible predicate.** An observation at `t` targeting scope S (the
  `collection_scope` it writes; `None` is the NULL scope) is
  *current-eligible* iff all three hold, each with NULL meaning "no bound":
  - `t ≥ last_observed_at`;
  - `t ≥ effective absence`;
  - `t ≥ last_complete_sweep_at(S)`.

  Ties go to presence, as today. A run persists its observations before its
  own delist stage raises either watermark, and fixture adapters that reuse
  one `fetched_at` across runs (for example `tests/db/test_poller_jobs.py:44`)
  must keep relisting on reappearance. Distinct remote runs never tie in
  practice, because `startedAt` differs.

  Only a current-eligible observation writes content, relists, raises
  `last_observed_at`, or creates an active row (MS2-D-30 *Current state*).
  The third condition matters only for keys without a row and for rows that
  were already delisted before S's newest complete sweep. Any active row that
  sweep saw already has `last_observed_at ≥` that sweep.
- **Not current-eligible:**
  - *Existing row:* untouched, including content, `expires_at`, the relist,
    `last_observed_at`, and `last_seen`. The snapshot follows MS2-D-37.
  - *No row:* the row is created as a delisted history anchor. In one
    transaction, the listing is inserted from the observation, with
    `last_observed_at := t`, and then
    `mark_delisted(ABSENT_FROM_SWEEP, when=last_complete_sweep_at(S))` runs.
    That applies the existing retention semantics: bounded expiry is pulled
    forward, delete-on-delist content is redacted before commit, and
    `last_absence_at` is raised. The snapshot follows MS2-D-37, so it is
    kept for merchant-fact classes and not written for bounded ones. The key
    now has an identity, so a later current-eligible observation relists the
    same pk (DR-003, D6).
- **Serialization.** The transaction that writes listings (import stage 1 or
  the local persist step) first takes `select_for_update` on each touched
  scope's watermark row: the FULL lane row for the NULL scope, and the
  `scope_sweep_continuity` row for a non-null scope, inserted if absent. The
  delist stage takes the same lock before it raises the watermark and marks
  listings. Either the creation sees the newer watermark, or the newer sweep
  sees the created row as a candidate (`last_observed_at = t1 < t2`) and
  delists it. Locks are taken in a fixed order: scope rows sorted by key,
  NULL first, then listing rows.
- **Why "no row" proves absence.** Listings are never row-deleted (the
  retention anchor, `market.py:341`), and a sweep's own stage 1 creates
  every key it saw before its stage 2 raises the watermark. So a key with no
  row was not seen by any sweep that raised S's watermark.
- *Rejected (a):* skipping creation entirely. That drops merchant-fact
  history, which MS2-D-30's rejected alternative (a) already protects.
- *Rejected (b):* creating the row active and letting the next complete
  sweep delist it. That publishes stale availability until then, and for a
  delete-on-delist class it holds retired content.
- *Rejected (c):* a per-scope set of absent keys. Its storage is unbounded,
  and the watermark plus the no-row argument is sufficient.
- *Residual:* stale-absence sweeps do not raise the scope watermark, and
  `last_seen` is stamped at persistence time. See risk R23.

**MS2-D-36 — Ordered continuity with timestamped breaks for every scope,
the NULL scope included (Slice D, migration 0021; review N-02 residual,
round 3).** This decision amends MS2-D-11 and MS2-D-31.
- **Hazard.** Revision 3's NULL-scope break was reversible out of order. A
  newer GPU-only FULL run breaks NULL continuity. An older NULL-scope run,
  whose stage 2 was delayed, then calls the unchanged recorder
  (`pipeline.py:98-141`), finds `continuous_since` null, and sets it to its
  old observation time. A later local incomplete NULL sweep finds the recent
  GPU run through the site-wide SUCCESS lookup, keeps that old start, and can
  stale-delist on cross-scope history.
- **Watermarks per scope.** Each scope has `last_eligible_sweep_at` and
  `continuity_broken_at`. For the NULL scope these are two new nullable
  columns on `SourceLaneState`, written only on the FULL lane row. For
  non-null scopes they are columns of `scope_sweep_continuity`.
- **Record** (an eligible sweep of S observed at `t`, under
  `select_for_update` on S's row):
  1. If `continuity_broken_at` is set and `t ≤ continuity_broken_at`, change
     nothing and return `None`, so the run's own stale absence does not fire.
     A tie goes to the break, which fails closed. Legacy local runs never
     break, so the tie rule cannot touch them.
  2. If `last_eligible_sweep_at` is set and `t < last_eligible_sweep_at`,
     change nothing and return `None`.
  3. Otherwise find the predecessor. For the NULL scope it is today's
     `ScraperRun` lookup, unchanged. For a non-null scope it is
     `last_eligible_sweep_at`. Restart `continuous_since := t` if it is null,
     there is no predecessor, or the gap exceeds tolerance. Set
     `last_eligible_sweep_at := t` and return `continuous_since`.
- **Break** (event time `e`): `continuous_since := None` and
  `continuity_broken_at := greatest(continuity_broken_at, e)`. A break
  always applies, even when `e` is older than the current continuity start.
  A late break costs one grace of stale absence, never a false delist.
  Event times:
  - an ineligible successful FULL run: its `observed_at`;
  - the NULL-scope break for a successful FULL run that did not sweep the
    NULL scope: its `observed_at`;
  - a rejected FULL remote import: its `startedAt`, or `admitted_at` when no
    start response was recorded.
- **Why the NULL scope keeps the `ScraperRun` lookup.** A remote run's stage
  2 (break) precedes its stage 5 (SUCCESS). So by the time a scoped run is
  visible to the lookup, it has already broken NULL continuity at its own
  time, and any later restart is newer than it. A run newer than `t` is
  either an eligible NULL sweep (step 2 fires) or a break (step 1 fires). So
  a predecessor the lookup returns is never a non-NULL run inside the current
  chain, and never newer than `t`. For in-order legacy local polling, `t` is
  monotonic, steps 1 and 2 never fire, and behavior is exactly today's.
- **Code.** D changes `_record_sweep_continuity` and
  `_break_sweep_continuity` to take the scope and the event time. Slice A code
  is not reopened.
- *Rejected (a):* replacing the NULL scope's `ScraperRun` lookup with the lane
  watermark as predecessor. The frozen eBay tests simulate pauses by
  backdating `ScraperRun.started_at` (`test_source_ebay.py:378-381`,
  `:421`), so `test_ebay_polling_pause_suspends_stale_absence` would stop
  testing a pause. Deployed data would also lose its first predecessor.
- *Rejected (b):* moving the NULL scope into `scope_sweep_continuity`, for
  MS2-D-31's reason (b).
- *Rejected (c):* ignoring any remote run that finishes after a newer run.
  Ordering is per scope, and a run older than another scope's run is still
  valid evidence for its own scope.

**MS2-D-37 — Snapshot retention follows the observation (Slice D task D10;
review M-01, round 3).** This decision amends MS2-D-30 *History* and changes
`acquisition/persist.py::append_snapshot`.
- **Hazard.** `append_snapshot` copies `retention_class` and `expires_at`
  from the `Listing` (`persist.py:105-106`), and `_persist_all` relies on that
  (`pipeline.py:285-289`). Once MS2-D-30 leaves a newer listing untouched
  while appending an older observation, the older snapshot inherits the newer
  deadline. With a 6 h policy, t1 applied, then t0 < t1 appended, the t0
  snapshot expires at t1 + 6 h. Its raw payload expires correctly, because
  `store_raw` takes the fetch-time expiry. `observe_listing` is shared, so the
  local path is exposed too, and the bounded-source Actor denial (MS2-D-33)
  does not close the defect.
- **Contract (Binding).**
  `append_snapshot(listing, normalized, *, observed_at, raw=None,
  retention: ObservationRetention | None = None)`.
  `ObservationRetention(retention_class, expires_at)` is a frozen value type in
  `persist.py`.
  - `None` copies from the listing, exactly as today. Existing callers and
    frozen tests are unchanged.
  - The ordering-guarded observation path always passes it explicitly: the
    source policy's class for this observation (the MS2-D-25 registry for
    imports, `adapter_retention` locally) and that policy's expiry computed
    from the observation's own `observed_at` (`None` for indefinite classes).
    For a local run this equals the batch `expires_at`, so local output is
    unchanged.
- **Newer-absence restriction.** For a bounded class, when newer absence
  exists (the effective absence of MS2-D-35, or the scope watermark used to
  create the row delisted, strictly after `observed_at`), the snapshot's
  `expires_at := min(own deadline, that absence time)`. This is the same
  pull-forward `mark_delisted` applies to existing snapshots. If the result
  is at or before the transaction's `now`, the snapshot is not written,
  because newer evidence already retired that content. Indefinite
  (`merchant_fact`) classes keep a NULL expiry and always append as history;
  `mark_delisted` never expires them either.
- **Effect.** Each snapshot expires on its own deadline. The purge sweep
  removes the t0 snapshot at t0 + 6 h while the listing and the t1 snapshot
  remain.
- **Ownership.** D10 makes this change, together with `observe_listing`.
  `store_raw` is unchanged.
- *Rejected (a):* skipping older snapshots for bounded classes. That drops
  history MS2-D-30 keeps.
- *Rejected (b):* recomputing snapshot expiry in the purge sweeper. The DR-001
  CHECK pair and the sweep index read the stored column.

**MS2-D-38 — Hardware Radar Actors are built, versioned, tested, deployed, and
managed in this repository (D-prep D1, F5a; revision 5).**
- **Ownership (owner-overridden).** Owner direction, 2026-09-24: "All Apify
  Actors used specifically by Hardware Radar must be built, versioned, tested,
  deployed, and managed from the `hw-radar` project/repository." Actor source,
  schemas, tests, build and deploy definitions, versioning, cost and usage
  integration, and operator procedures all live here. A change to the
  Actor↔hw-radar contract lands atomically in one hw-radar PR. Hardware Radar
  does not depend on the apify-actors venture's code, lifecycle state, product
  records, admission process, implementation work, or token. Revision 1–4 text
  that placed the Actor in that repository is withdrawn (MS2-D-14, F5, R2).
- **Layout (architect decision; reviewable).** One top-level directory per
  Actor, `actors/hw-radar-<purpose>/`, outside the Django package:
  - `.actor/actor.json`: `actorSpecification: 1`, `name`, `version` in
    `MAJOR.MINOR` form whose major equals the contract major (v1 ⇒ `1.x`), an
    explicit `defaultMemoryMbytes` and `maxMemoryMbytes`, and the input schema
    reference. `actor.json` has no run-timeout field (research input
    `apify-ops.md` A4), so the default run timeout is the Actor's
    `timeBudgetSecs` input default, and hw-radar always sends an explicit
    `timeout` run option (MS2-D-15, MS2-D-26).
  - `.actor/input_schema.json` (Apify dialect) and, if used,
    `.actor/dataset_schema.json`, both conformance-tested against the contract
    (MS2-D-14);
  - `contract/hw-radar-{input,listing,run}-v1.schema.json`, the contract
    artifact (MS2-D-14);
  - its own `pyproject.toml` and `uv.lock` (the Apify Python SDK lives only
    there), a Dockerfile, the Actor source, `tests/`, and `fixtures/`;
  - an `.actorignore` limiting what `apify push` uploads (MS2-D-43).
- **Boundaries (binding).** No Django import, no `hw_radar` import, no DB
  credentials, no DB writes, and no proxy construction. An Actor test enforces
  the import and proxy rules by scanning the Actor source. Tests are
  deterministic and fixture-driven, with no network access.
- **Structure.** The Actor has a pure core (input → bounded fetch plan → rows
  plus `OUTPUT`) and a thin SDK entry point. The first Actor carries no shared
  internal framework, base classes, or plugin registry. *Reopen if* a second
  Actor duplicates a real seam.
- **Gate integration (architect decision).** D1 extends `scripts/check.py` so
  CI and the local gate also run, in the Actor's own project environment
  (`uv run --project actors/<name> --locked …`): basedpyright (strict), pytest
  with coverage (threshold ≥ the root's 85), and pip-audit. Root
  `ruff format --check .` and `ruff check .` already cover `actors/` (root
  config `src`/`extend-exclude`). The Actor's `pyproject.toml` carries its own
  `[tool.ruff]` `target-version` equal to the Actor image's Python, because the
  root's `py314` target may emit syntax an older image cannot parse. hw-radar's
  pytest runs the contract-conformance tests (MS2-D-14) and never imports the
  Actor.
  - *Rejected (a):* a uv workspace member. One shared lockfile would put the
    Apify SDK and its transitive dependencies into hw-radar's resolution and
    pip-audit surface, and an SDK pin could block hw-radar upgrades.
  - *Rejected (b):* Actor code under `src/hw_radar/`. It couples the Actor's
    build context to the Django package and invites the forbidden imports.
- **Naming.** Actors in the shared account are named `hw-radar-<purpose>`. The
  first is `hw-radar-synthetic-collector` (MS2-D-42). Actor ids and the account
  owner are configuration, never committed.
- **Runtime permission.** Each Actor uses Apify's *Limited permissions* level:
  it reads its input and writes only its default dataset and KV store (research
  input `apify-ops.md` A6). Full permissions are never requested.

**MS2-D-39 — Three clocks, never derived from one another (Slices D, E;
revision 5, owner-clarified (s2, 2026-09-24)).**
- **Clocks.**
  - *Observation (event) time:* when merchant state was observed. For an Actor
    run it is the run's `startedAt` (MS2-D-13), or a per-row `observedAt` if one
    is ever adopted (MS2-D-30 *Reopen if*).
  - *Processing (import) time:* hw-radar's own clock when it admits, polls,
    imports, or cleans up (`admitted_at`, `import_attempts`, `next_attempt_at`,
    the local `now`).
  - *Billing / finalization time:* Apify's clock of when charges accrue
    (`startedAt`..`finishedAt` for compute, operation times for reads and
    deletes, storage lifetime) and when a usage figure becomes final
    (MS2-D-41). It also defines the billing cycle (MS2-D-40).
- **Which clock governs each transition:**

  | Transition | Clock | Decisions |
  | --- | --- | --- |
  | Listing lifecycle: content writes, relist, creation, `last_observed_at` | observation | MS2-D-30, MS2-D-35 |
  | Continuity record and break | observation (break event time) | MS2-D-31, MS2-D-36 |
  | Delist and relist evidence (`mark_delisted(when=…)`, absence watermarks) | observation | MS2-D-35 |
  | Retention expiry of listings, snapshots, raw payloads, evaluations | observation (`expires_policy(observed_at)`) | MS2-D-33, MS2-D-37 |
  | Remote storage cleanup deadline | processing (anchored at `admitted_at`, which precedes any observation) | MS2-D-25, MS2-D-33 |
  | Import stage progress, retries, backoff | processing | MS2-D-22, MS2-D-23 |
  | Budget reservation and its admission cycle | processing (`reserved_at`), compared with Apify's cycle bounds under the boundary guard | MS2-D-40 |
  | Usage finalization and reconciliation, and cycle attribution of charges | billing / finalization | MS2-D-34, MS2-D-41 |

- **Rules.** An import's processing time is never used as observation time. A
  locally observed termination is never used as `finishedAt` (MS2-D-33). An
  observation time never attributes a charge to a cycle. `last_seen` is the one
  known processing-time stamp on the listing path: it is `auto_now`, and R23
  records its effect.
- **Preserved property (R23, verbatim):** "a delayed import may delay a correct
  delist, never cause a false delist". Any change to `last_seen` stamping must
  come with out-of-order tests proving it.

**MS2-D-40 — The billing cycle is the budget period, and the cash ceiling is
enforced as project allocation plus account prepaid headroom (Slice E, migration
0022; revision 5; OQ23 resolved).**
- **Cycle discovery (owner-overridden).** Admission reads
  `GET /v2/users/me/limits` for `monthlyUsageCycle.startAt/endAt`, the account
  limit, and the current usage, and `GET /v2/users/me` or the same response for
  the plan's base price and prepaid usage credit (D3 confirms which endpoint
  carries each field). It stores one `ApifyBudgetCycle` row per cycle:
  `cycle_start`, `cycle_end`, the project allocation opened for the cycle, and
  the latest observed account figures (`account_prepaid_credit_usd`,
  `account_base_price_usd`, `account_limit_usd`, `account_usage_usd`,
  `account_observed_at`). Consumed finalized usage, outstanding reservations,
  and remaining project budget are computed from reservation rows under the
  budget lock, never stored as counters that could drift from those rows.
  - A snapshot older than `HW_RADAR_APIFY_ACCOUNT_SNAPSHOT_MAX_AGE_S` (default
    900 s, an assumption) is refreshed before admission. A failed refresh denies
    with `account_state_unobservable`. `now` past the stored `cycle_end`,
    before a new cycle is observed, denies with `cycle_unknown`.
- **Project allocation.** `A = HW_RADAR_APIFY_CYCLE_TARGET_USD −
  HW_RADAR_APIFY_OPERATOR_ALLOWANCE_USD`.
  - The target defaults to 12.00, and settings validation rejects a value above
    12.00, the owner's operating target. Raising it is an owner decision.
  - The operator allowance (default 0.50, an assumption) covers
    Hardware Radar-attributable spend that bypasses the ledger: builds, and
    dataset or log reads by an operator through the Console, CLI, or MCP
    (MS2-D-43).
- **Account prepaid headroom.** A paid run is admitted only if also
  `account_usage_usd + Σ Hardware Radar unreconciled reservations + estimate ≤
  account_prepaid_credit_usd − HW_RADAR_APIFY_ACCOUNT_MARGIN_USD`.
  - Hardware Radar's outstanding reservations are counted in full even though
    part of them may already appear in the account usage. That double counting
    fails closed.
  - The margin defaults to 10% of the observed prepaid credit. Apify states that
    the platform limit may deviate by up to about 10% at enforcement, and other
    workloads keep consuming between snapshots, so the ~$20 cash ceiling rests on
    this admission margin, not on the account limit alone.
  - So Hardware Radar uses the lesser of its target and the prepaid allowance
    actually remaining. It never assumes the whole allowance is available, and
    it never relies on pay-as-you-go overage.
- **Cash-ceiling guard.** If the observed base price exceeds
  `HW_RADAR_APIFY_CASH_CEILING_USD` (20.00), every paid admission is denied with
  `cash_ceiling_exceeded_by_plan`. With the verified Starter plan ($19 base,
  $19 prepaid credit), cash outlay is $19 plus any overage, and the headroom
  check keeps Hardware Radar from causing overage.
- **Account backstop (owner-clarified).** The account-level usage limit is a
  secondary defense only. Verified account state 2026-09-24: it equals the
  prepaid credit ($19). The plan recommends keeping it at or below the prepaid
  credit and never raising it for Hardware Radar. No agent changes any billing
  or account setting without explicit owner authority. The project-level
  controls apply regardless of the account limit.
- **Cycle boundary.** A reservation whose charge interval (MS2-D-34) comes
  within `HW_RADAR_APIFY_CYCLE_BOUNDARY_GUARD_S` (default 3600 s, an
  assumption) of a cycle edge counts in both cycles. Unsettled reservations are
  carried into each new cycle at full estimate. A new run whose worst-case
  interval could straddle the cycle end must fit the current cycle's remaining
  allocation in full; it then also counts against the next cycle once that
  opens.
- **Secondary metric.** The trailing-31-day total is reported and logged at
  warning level when it exceeds `A`. It never admits or denies.
- **Verified account state, 2026-09-24** (read-only API; recorded separately from
  the rule above, which names no plan): plan STARTER; base price $19; prepaid
  usage credit $19; account limit $19; cycle 2026-09-05T00:00:00Z →
  2026-10-04T23:59:59.999Z; cycle usage about $0.09, all from other workloads
  sharing the account; data retention 31 days; the residential-proxy feature is
  available on the account.
- *Rejected (a):* one fixed "$20 minus deduction" cap (revision 4). It
  conflated cash with consumption and ignored other workloads.
- *Rejected (b):* the account limit as the enforcement point. It is shared,
  approximate (about 10%), and an owner billing setting.
- *Reopen if* the owner changes plans, target, or cash ceiling, or Apify
  documents per-workload limits.

**MS2-D-41 — Reservation → reconciliation lifecycle with provisional and
finalized usage (Slices D, E; revision 5, owner-clarified (s2, 2026-09-24)).**
This decision refines MS2-D-17, MS2-D-26, and MS2-D-32.
- **Flow.** `eligibility (check_admission) → estimate maximum cost → reserve →
  start run → poll completion → import bounded output → obtain finalized usage →
  account post-run retrieval, transfer, and storage cost → reconcile → release
  the unused reservation`.
- **Reserve.** Under the budget lock, both MS2-D-40 checks run against the
  worst-case estimate. A reservation exactly equal to the remaining amount is
  admitted; one cent more is denied. Concurrent reservations serialize on the
  lock, so the account is never oversubscribed.
- **Usage states.** `reserved → usage_provisional → usage_finalized →
  reconciled`.
  - The first non-null `usage_total_usd` is `usage_provisional`.
  - It becomes `usage_finalized` on the first read taken at least
    `HW_RADAR_APIFY_USAGE_SETTLE_DELAY_S` (default 10 s, from Apify's guidance;
    F5a measures it) after the run's Apify `finishedAt`. The finalized figure
    and its read time are written once, and later reads never overwrite them,
    because historical re-reads are recomputed at current pricing.
  - A provider value that is still null or unsettled leaves the row in its
    state, with the full estimate counted, and it is retried. No state is
    corrupted.
- **Post-run cost.** Dataset and KV reads, deletes, transfer, and timed storage
  after the run are account usage. Until F5a records the measurement
  (`HW_RADAR_APIFY_POST_RUN_COST_MODE=bound`, the default), the post-run cost
  equals the full post-run liability bound (MS2-D-32). After the owner reviews
  F5a's evidence, the mode may be set to `counted`: hw-radar's operation
  counters × verified unit prices × `(1 + margin)`. "Do not mark the budget
  reconciled merely because the Actor process stopped."
- **Reconcile.** Settled amount = finalized run usage + post-run cost.
  - Lower than the reservation: the difference returns to capacity at
    reconciliation.
  - Higher than the reservation: the overrun latch trips (MS2-D-26), which
    pauses all further paid work. An overrun never triggers another paid call to
    repair itself.
- **Visibility.** Every budget error is operational state, not only a log line:
  the `budget_paused` freshness state carries its reason, and the spend report
  lists overruns, unsettled rows, stale snapshots, and the cycle figures.
- *Rejected:* trusting the value in the response that first reports
  `SUCCEEDED`. Apify documents it as preliminary.

**MS2-D-42 — The first Actor proof uses a controlled synthetic source (Slice D
fixtures, F5a; revision 5, owner-clarified (s2, 2026-09-24); OQ24 part (a)).**
- **Source.** The Actor `hw-radar-synthetic-collector` fetches static fixture
  pages committed in this public repository, over HTTPS, from
  `raw.githubusercontent.com` at a **pinned commit SHA** passed in its input.
  The base URL is fixed in the Actor code, and the input carries only the SHA and
  fixture paths.
  - *Why:* content addressed by a commit is deterministic and immutable, owned by
    the project, free of merchant terms-of-use questions, not short-retention,
    and it exercises real HTTP fetches, request and byte caps, and transfer usage.
  - *Rejected (a):* fixtures baked into the Actor image. That exercises no
    network fetch, transfer, or request limit.
  - *Rejected (b):* a named Apify storage as the source. Named storages persist
    indefinitely and cost money, and they are not a collection source.
  - *Rejected (c):* a hw-radar-hosted endpoint. The deployment has no public
    ingress.
  - *Rejected (d):* public scraping sandboxes. They are not project-owned, not
    pinned, and carry their own terms.
  - *Reopen if* GitHub raw-content rate limits or terms make the pinned fetch
    unreliable at the proof's small request count.
- **Fault injection.** Only this Actor accepts a `faultMode` input:
  `none | truncate_items | truncate_pages | truncate_time | truncate_bytes |
  partial_failure | contradictory_report | count_mismatch | unknown_schema |
  fail`. Each mode produces deterministic output for one MS2-D-14 row. The
  field exists only in this Actor's input schema, never in `hw-radar-input-v1`'s
  common part, so a merchant Actor cannot receive it.
- **Site and retention.** The synthetic site has a `SourceSite` and
  `SourceConfig` created by an idempotent management command (F5a), not a
  migration. It is registered in `SOURCE_RETENTION` with an indefinite class
  (`merchant_fact`), because MS2-D-33 denies bounded-class sites an Actor path.
- **Environment.** The proof runs from a non-production hw-radar environment,
  so synthetic rows never enter the production catalog. At most one hw-radar
  environment has `HW_RADAR_APIFY_ENABLED=true` in a billing cycle. If a second
  environment is enabled in the same cycle, its `HW_RADAR_APIFY_CYCLE_TARGET_USD`
  is first lowered by the first environment's reported consumption (R27).

**MS2-D-43 — Operator and agent workflow for Hardware Radar Actors (F5a, E7;
revision 5, owner-clarified (s2, 2026-09-24)).** The procedure is binding; the
command spellings are illustrative and are verified in D-prep and F5a.
- **Tool boundaries (research input `apify-ops.md`, 2026-09-24).** The Apify MCP
  server cannot create, push, build, or version an Actor; deployment is the
  Apify CLI (`apify push`), the REST API, or the GitHub integration. The MCP can
  run Actors and read runs, logs, datasets, and KV records only with a token or
  OAuth, beyond the four anonymous tools the project's `.mcp.json` loads today.
  It has no account-usage tool. Rollback is a build-tag reassignment. There is
  no self-service "disable Actor".
- **Create or update.** Edit `actors/<name>/` and its contract in one hw-radar PR
  with the gate green. Then, from a clean checkout of the reviewed commit on
  `dev` or `main` (never a PR branch), run `apify push` from `actors/<name>/`
  with the owner-provisioned deploy credential. The build lands under the
  `candidate` build tag. Record the git commit, Actor version, and build number
  in `actors/<name>/DEPLOYMENTS.md`.
  - D-prep verifies, through the Actor version's `sourceFiles` returned by the
    API, that `apify push` uploads only `actors/<name>/`. If it uploads more,
    the fallback is a Git-source Actor
    (`<repo>#<branch>:actors/<name>`) with **no** push webhook, built only by
    an explicit build API call.
  - *Rejected:* GitHub-integration auto-build on push. It deploys automatically
    from pushed code, which the owner ruled out, and it ignores `actor.json`'s
    version and tag.
- **Build.** `apify push` builds; a rebuild is an explicit build API call. The
  build cost is covered by the operator allowance (MS2-D-40).
- **Smoke run.** A management command (`apify_smoke`, F5a) starts the
  `candidate` build on the synthetic source **through hw-radar's own admission
  and ledger**, imports it, and prints the contract checks. Never the MCP
  `call-actor` tool or Console "Start", which bypass the ledger.
- **Inspect output.** The authoritative inspection is hw-radar's import and
  `provider_run` record. Operator reads through the MCP (if widened), CLI, or
  Console are allowed read-only, and their dataset-read cost falls in the
  operator allowance.
- **Measure resource usage.** `provider_run` records `stats` and the finalized
  `usageUsd` breakdown. F5a runs the measurement protocol: the finalization
  delta at 0, 10, 30, and 120 s; the account usage diff around a dataset read;
  the storage accrual after deletion; and the `date`-parameter cycle read. The
  account-limit enforcement experiment is **not** run on the shared account
  without explicit owner sign-off.
- **Deploy (promote).** After a green smoke, reassign the `prod` build tag to
  the candidate build. hw-radar settings name the Actor
  (`HW_RADAR_APIFY_ACTOR_ID`) and the build tag (`HW_RADAR_APIFY_ACTOR_BUILD`,
  default `prod`). Each run records the resolved build number, and a start whose
  build is outside the contract's version line is aborted and trips the latch
  (MS2-D-26).
- **Roll back.** Reassign `prod` to the previous build (a retag, no rebuild).
  The previous build keeps a `rollback` tag, because untagged builds unused for
  90 days are garbage-collected.
- **Disable execution.** Hardware Radar-side kill switches, in order of reach:
  `HW_RADAR_APIFY_ENABLED=false` (all paid admission); setting the source's
  `collection_provider=local` or disabling the source; removing the `prod` tag;
  and, in an emergency, aborting active runs through hw-radar's abort call.
  Revoking the runtime token is the last resort.
- **Credentials.** The runtime token is `HW_RADAR_APIFY_TOKEN`, from the proposed
  OpenBao path `secret/apps/hw-radar/apify` (owner provisions; R25). The deploy
  credential is separate and operator-held. Neither is ever the apify-actors
  venture's token, and CI holds no Apify credential. Agents deploy only on an
  explicit owner or orchestrator instruction for that deployment.
- **MCP tool filter (owner decision, R24; `.mcp.json` is not edited by this
  plan).** If the owner wants MCP inspection of runs and storage, the
  recommended value adds only read tools:
  `https://mcp.apify.com?tools=search-actors,fetch-actor-details,search-apify-docs,fetch-apify-docs,get-actor-run,get-actor-run-list,get-actor-log,get-dataset,get-dataset-items,get-dataset-schema,get-key-value-store,get-key-value-store-keys,get-key-value-store-record`.
  - It excludes `call-actor` and the RAG web-browser Actor tool (both
    spend-capable), `abort-actor-run`, and the task tools.
  - Spend: the listed tools start no run, but dataset and KV reads after a run
    are billed account usage. They fall in the operator allowance.
  - Authentication: any non-anonymous tool requires OAuth (account-wide) or a
    bearer token. A token cannot be committed to `.mcp.json` in this public
    repository, so it must come from a local, uncommitted configuration.
  - Hardware Radar's runtime never depends on the MCP.

**MS2-D-44 — Production Actor-backed merchant sources pass a source-admission
decision (F5b; revision 5, owner-clarified (s2, 2026-09-24); OQ24 part (b)).**
- **Artifact.** Each candidate gets a record under
  `docs/research/source-admission/` from the template in that directory's
  README: the business value; URLs reviewed; Terms date and retrieval date;
  automated-access language; robots directives for the proposed endpoints;
  official API or structured alternatives; expected cadence and volume;
  retention constraints across Actor memory, dataset, KV store, staging, raw,
  and canonical storage; authentication or login; anti-bot behavior; browser or
  proxy need; maintenance and cost; and a recommendation of
  `eligible | permission-required | exclude`.
- **Rules.** Newegg stays excluded (R1). Robots permission is not contractual
  permission, and the absence of an obvious prohibition is not permission. A
  source that needs residential proxies, automatic proxy rotation, CAPTCHA
  solving, a paid unblocker, or a paid third-party Actor fails admission unless
  the owner revisits it. A bounded-retention source also fails an Actor path
  under MS2-D-33.
- **No grandfathering.** Existing local connectors are not admitted to an Actor
  path by virtue of existing. A finding that an existing, disabled local
  connector conflicts with this acquisition posture is recorded separately (its
  own admission record or an open question), not folded into an Actor decision.
- **Gate.** F5b starts only after the owner answers OQ24 for a named candidate
  with an `eligible` record.

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
| MS-2 Task 7 — self-owned Actor, observation-only, built in this repository (rev 5, MS2-D-38) | spec :934 | D1, F5a, F5b (owner-gated) | contract conformance tests (`test_apify_contract.py::test_pydantic_models_equal_committed_contract_schemas`); Actor tests in `actors/hw-radar-synthetic-collector/tests/` (no Django import, no proxy, KV holds only `OUTPUT`); F5a live evidence |
| AC-1 — HDD/SSD, GPU, RAM, CPU rows coexist, spine unchanged | spec :937 | B1, B2 | `test_category_specs.py::test_four_categories_coexist_on_one_spine`; `makemigrations --check` shows no spine change |
| AC-2 — match/no_match/unknown per first-class category | spec :938 | C3 | `tests/db/test_watch_evaluation.py` parametrized 4 categories × 3 verdicts |
| AC-3 — real-observation shortlist without score | spec :939 | C4, F6 (owner-gated) | `test_shortlist.py` (fixtures); F6 live evidence; `grep` proves no scoring import |
| AC-4 — local ↔ Actor switch keeps identity/history | spec :940 | D7 | `test_apify_import.py::test_provider_switch_preserves_identity_history_and_watch_state` |
| AC-5 — truncated Actor run cannot delist | spec :941 | A6, D8 | `test_collection_provider.py::test_truncated_remote_run_*`, `::test_local_after_prolonged_truncated_remote_cannot_mass_delist`; `test_apify_import.py::test_truncated_actor_fixture_cannot_delist` |
| AC-6 — duplicate completion/import idempotent | spec :942 | D6, D10 | `test_apify_import.py::test_duplicate_completion_is_noop`, `::test_crash_between_persist_and_mark_replays_once`, `::test_crash_after_observation_commit_resumes_without_duplicates`, `::test_crash_before_finalization_finalizes_once` |
| AC-7 — attributable cost; admission fails closed | spec :943 | E2–E6, E8, F5a | `test_apify_ledger.py` (attribution by source/provider; denial at limit; kill switch; outstanding counted; `::test_overrun_latch_denies_admission_until_reset`; `::test_reservation_straddling_cycle_boundary_counts_in_both_cycles`); F5a live usage and post-run measurement |
| AC-8 — full gate green | spec :944 | every slice | gate + `makemigrations --check` per commit |
| FR-001 — per-source provider choice, freshness SLO kept | spec :246 | D2 (MS2-D-18), F1 | `test_provider_selection.py` (CHECK, default local) |
| FR-003 — identity ladder, no false cross-category merges | spec :248 | A3, B3 | `test_resolver_categories.py::test_cross_category_alias_goes_to_review`; `::test_non_authoritative_alias_never_auto_accepts_new_category` (MS2-D-21) |
| FR-006 — eligibility reasons mandatory | spec :251 | C3 | every `watch_evaluation` row has non-empty `reasons` (DB test) |
| FR-014 — verdict persisted; unknown never passes | spec :259 | C2, C3, C4 | `test_eligibility_aggregate.py` (unknown ≠ match); listing-tier evidence cannot `match` a product clause; `test_shortlist.py::test_prior_match_then_contradictory_evidence_leaves_shortlist` (MS2-D-20) |
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
| R-MS2-15 — Actor-side retention (gap) | — | D11 | `test_apify_storage_cleanup.py` (deadline cleanup for successful/rejected/failed/abandoned runs; retries; 404 idempotent) |
| ADR 0021 — imports idempotent; completeness evidence; one scheduler | ADR 0021 :92-108 | D1, D5, D6 | as AC-5/AC-6; no Apify schedule (config review) |
| ADR 0012 amendment — polled completion, idempotent | ADR 0012 :94 | D5, D10 | `test_apify_poll_job.py` (both selectors; restart recovery per state) |
| ADR 0017 — recovery probes per selected provider | ADR 0017 | D12 | `test_apify_recovery_probe.py::test_local_success_cannot_clear_actor_provider_failure`, `::test_budget_admitted_actor_probe_recovers_source` |
| DR-001/DR-008 — bounded retention on remote imports | spec :290 | D11 | `test_apify_retention.py::test_bounded_source_import_retains_listings_snapshots_raw_and_evaluations` |
| ADR 0021 — complete-empty distinguishable from failure | ADR 0021 :100 | D1, D8 | `test_apify_import.py::test_complete_empty_run_delists_scope_end_to_end`, `::test_ambiguous_empty_run_cannot_delist` |
| MS-2 Task 6 prerequisite — non-drive corpus replay | spec :932 | B6 | `test_corpus_category_hint.py::test_harvested_non_drive_entry_reaches_category_rules` |
| FR-014 — catalog correction cannot leave a stale pass (MS2-D-29) | spec :259 | C1, C3, C4 | `test_evaluation_binding.py::test_same_target_catalog_correction_excludes_match_from_shortlist_immediately`, `::test_family_membership_change_makes_family_grain_evaluation_pending` |
| IR-008 / CR-004 — delayed imports cannot overwrite, revive, or delist (MS2-D-30) | spec :284 | D2, D10 | `test_apify_import_ordering.py::test_reverse_completion_older_import_does_not_overwrite_newer_listing_state`, `::test_delayed_remote_import_after_switch_back_to_local_preserves_newer_content_and_delist_state` |
| IR-008 / DR-008 — delayed imports cannot restore retired content or create keys a newer complete sweep omitted (MS2-D-35) | spec :284, :290 | D2, D10 | `test_apify_import_ordering.py::test_delayed_observation_after_newer_delist_restores_no_content_and_keeps_expiry`, `::test_delayed_import_of_unknown_key_after_newer_complete_sweep_creates_no_active_listing`, `::test_delayed_observation_cannot_relist_row_delisted_before_newer_complete_sweep`, `::test_stage1_creation_and_newer_complete_sweep_serialize_on_scope_row` |
| ADR 0021 — stale absence needs the relevant scope's continuity (MS2-D-11, -31) | ADR 0021 :92-108 | A4–A6, D2, D10 | `test_collection_provider.py::test_remote_complete_evidence_incomplete_scope_breaks_continuity_then_local_cannot_stale_delist`; `test_scope_continuity.py::test_scope_a_continuity_does_not_authorize_first_incomplete_scope_b_sweep` |
| ADR 0021 — an older run cannot restore continuity a newer break ended (MS2-D-36) | ADR 0021 :92-108 | D2, D10 | `test_scope_continuity.py::test_delayed_null_scope_run_after_newer_scoped_run_cannot_restore_continuity`, `::test_eligible_sweep_older_than_break_is_noop` [NULL, non-null]; frozen `test_source_ebay.py` continuity tests green |
| DR-001 / DR-008 — snapshot retention follows the observation's own time (MS2-D-37) | spec :290 | D10 | `test_persist_observation_retention.py::test_reverse_order_bounded_observation_snapshot_keeps_its_own_deadline`, `::test_older_bounded_observation_after_newer_delist_is_not_snapshotted`, `::test_append_snapshot_default_copies_listing_retention` |
| AC-7 — reservation bounds cumulative work (MS2-D-32) | spec :943 | D10, E1–E4 | `test_apify_ledger.py::test_repeated_pre_commit_reads_are_capped_and_reserved`, `::test_non_null_usage_before_cleanup_completes_does_not_release_liability`, `::test_delayed_deletion_keeps_storage_liability_outstanding` |
| AC-7 — settled spend counts in every billing cycle its charge interval touches (MS2-D-34 as revised in rev 5) | spec :943 | E1, E3, E4 | `test_apify_ledger.py::test_late_cleanup_settled_spend_counts_in_the_cycle_of_its_final_charge`, `::test_reconciled_spend_leaves_a_cycle_its_charge_interval_does_not_touch` |
| C-011 / AC-7 — billing cycle is the period; cash ceiling = project allocation + account prepaid headroom (MS2-D-40, OQ23) | spec :181, :943 | E1–E3 | `test_apify_budget.py::test_cycle_bounds_come_from_account_limits_not_calendar`, `::test_account_headroom_below_project_target_binds`, `::test_project_target_below_account_headroom_binds`, `::test_admission_never_relies_on_overage`, `::test_plan_base_price_above_cash_ceiling_denies_all`; `test_apify_ledger.py::test_arbitrary_non_calendar_cycle_boundary` |
| AC-7 — provisional → finalized usage; reconcile and release (MS2-D-41) | spec :943 | E4 | `test_apify_ledger.py::test_first_usage_read_is_provisional_until_settle_delay`, `::test_finalized_usage_written_once_not_recomputed`, `::test_reconcile_below_reservation_returns_capacity`, `::test_reconcile_above_reservation_trips_overrun_and_pauses_paid_work` |
| IR-008 / DR-011 — three clocks (MS2-D-39) | spec :285, :301 | D10, E3 | ordering tests in `test_apify_import_ordering.py` (observation clock); `test_apify_storage_cleanup.py::test_deadline_is_anchored_at_admission_not_terminal_observation` (processing clock); `test_apify_ledger.py::test_cycle_attribution_uses_charge_interval_not_observation_time` (billing clock) |
| DR-001/DR-008 — absolute remote retention deadline (MS2-D-33) | spec :290 | D5, D11 | `test_apify_storage_cleanup.py::test_restart_after_source_ttl_rejects_expired_content_before_persistence`, `::test_unobserved_run_past_deadline_is_aborted_and_cleaned` |

### Synthetic Actor proof acceptance (owner §21, revision 5)

The owner's 18 acceptance items for the first Actor proof (MS2-D-42). "Fixture"
proofs run in the gate; "live" proofs are F5a evidence recorded in STATUS.

| # | Owner acceptance item | Task | Proof |
| --- | --- | --- | --- |
| 1 | Provider abstraction starts the correct Actor | D5, F5a | `test_apify_poll_job.py::test_start_uses_configured_actor_and_build_tag`; live: `provider_run.actor_ref`/`build_number` match settings |
| 2 | Input-schema validation rejects bad requests before paid execution where possible | D1, D3, F5a | `test_apify_contract.py::test_invalid_input_rejected_before_start_request` (no HTTP call recorded); live: Apify's own rejection of an invalid input observed and recorded (MS2-D-15) |
| 3 | Run bounded by timeout, memory, item, and request limits | D1, D3, E2 | `test_start_sends_memory_and_timeout_run_options`; Actor `tests/test_limits.py::test_item_page_request_byte_caps_enforced`; `test_apify_budget.py` component formula |
| 4 | No residential proxy | D1, E4 | Actor `tests/test_boundaries.py::test_no_proxy_configuration_or_django_import`; `test_apify_ledger.py::test_proxy_usage_trips_latch`; live: zero proxy component in `usageUsd` |
| 5 | Versioned output contract | D1 | `test_apify_contract.py::test_pydantic_models_equal_committed_contract_schemas`, `::test_unknown_schema_version_is_failed` |
| 6 | Source/provider scope metadata in output | D1, D4 | `test_apify_contract.py::test_scope_mismatch_is_failed`; `test_apify_import.py::test_imported_rows_carry_scope_and_category_hint` |
| 7 | Complete vs intentionally truncated distinguishable | D1, D8, F5a | `test_apify_contract.py` mapping table incl. `::test_truncation_reason_distinguishes_items_pages_time_bytes`; live: `faultMode=truncate_items` run classified `truncated`/`item_limit` |
| 8 | Contradictory completion metadata fails closed | D1, D8 | `test_apify_contract.py::test_contradictory_report_is_failed` [complete+limit, count mismatch, status mismatch]; `test_apify_import.py::test_contradictory_report_rejected_and_breaks_continuity` |
| 9 | Importing the same output twice has no duplicate canonical effects | D6 | `test_duplicate_completion_is_noop`, `test_replayed_dataset_read_does_not_duplicate_snapshots`, `test_duplicated_dataset_import_is_noop` |
| 10 | Late import cannot overwrite newer listing state | D10 | `test_reverse_completion_older_import_does_not_overwrite_newer_listing_state` |
| 11 | Truncated import cannot delist | A6, D8 | `test_truncated_actor_fixture_cannot_delist` [item, page, time, bytes] |
| 12 | Provider switch preserves listing identity | D7 | `test_provider_switch_preserves_identity_history_and_watch_state` |
| 13 | Run usage/cost evidence captured | D3, E4, F5a | `test_apify_ledger.py::test_finalized_usage_written_once_not_recomputed`; live: `usage_total_usd`, `usageUsd`, `stats` on `provider_run` |
| 14 | Post-run retrieval cost/usage measured | E4, F5a | `test_apify_ledger.py::test_post_run_cost_uses_bound_until_measured`; live: account usage diff around the dataset read (MS2-D-43) |
| 15 | Reservation and reconciliation correct | E3, E4 | `test_reconcile_below_reservation_returns_capacity`, `test_reconcile_above_reservation_trips_overrun_and_pauses_paid_work`, `test_reservation_exactly_equal_to_remaining_is_admitted` |
| 16 | Budget exhaustion prevents a new paid run | E2, E5 | `test_one_cent_over_remaining_is_denied`; `test_denied_start_records_denial_and_starts_nothing` |
| 17 | `budget_paused` visible as freshness/operational state | E5 | `test_denied_start_shows_budget_paused_with_reason_in_shortlist` |
| 18 | Full gate green | every slice | gate (incl. the Actor project's commands, MS2-D-38) + `makemigrations --check` |

## Slice order and migration assignment

| Slice | PR scope | Migrations | Depends on | Why this position |
| --- | --- | --- | --- | --- |
| **A** | Seams: category registry + hint; provider seam + run evidence + completeness gate | none | — | Every later slice plugs into these seams. Behavior-preserving, so it is safe to land first. |
| **B** | GPU/RAM/CPU satellites, category rows, rules, reference seeds, cross-category guard | `0018_category_spec_satellites`, `0019_seed_categories` | A | C's product clauses read the satellites. |
| **C** | Watch + requirement satellites + evaluator + `watch_evaluation` (with `catalog_fingerprint`) + shortlist read model | `0020_watch_requirements` | B | D's provider-switch test must prove watch state survives (AC-4). |
| **D-prep** | D1 (the `actors/hw-radar-synthetic-collector/` Actor project with its committed contract schemas, fault-injection fixtures, and own tests; hw-radar's conformance-tested contract models, `classify_run` with truncation reasons, fixtures; the Actor gate commands in `scripts/check.py`) and D3 (httpx client, nine or ten calls). Pure code with no DB schema, no pipeline wiring, and no Apify deployment (revision 5, MS2-D-38). | none | A | Has no dependency on B or C, so it may be developed and merged any time after A. |
| **D** (core) | D2, D4–D12: `provider_run`, staged import, scoped absence, per-scope ordered continuity (`scope_sweep_continuity`, NULL-scope lane watermarks), ordering and absence watermarks (`Listing.last_observed_at`, `Listing.last_absence_at`, per-scope `last_complete_sweep_at`), per-observation snapshot retention, provider selection, poll selectors, probes, retention, cleanup. Production admission = deny-all. Starts only after the *Slice D entry gate* | `0021_provider_runs` | C, D-prep, entry gate | D7 needs `WatchEvaluation` (C), and stage 4 needs the evaluator. Live runs are impossible until E replaces deny-all. D is fail-closed by construction. |
| **E** | Spend ledger, billing-cycle table, account-headroom admission, provisional/finalized usage, reconcile, `budget_paused`, spend report (revision 5, MS2-D-40, -41) | `0022_apify_spend_ledger` | D | Reservations attach to `provider_run`. |
| **F** | Pilot sources (eBay category sweeps, SPD check), measurement, synthetic Actor proof (F5a), owner-gated merchant Actor source (F5b), end-to-end evidence | none planned | B–E | Integration and measurement last (ADR 0022 "measure before breadth"). |

**Merge order = dependency graph:** A → B → C → D (core) → E → F. D-prep merges
at any point after A and before D (core). Revision 1's "if D merges first,
renumber" alternative is withdrawn: D (core) needs C's code and schema, and
renumbering migrations cannot satisfy that dependency. For parallel work, only
D-prep runs alongside B/C. Every slice leaves `dev` deployable with all sources
still disabled.

The migration names in this table are planning allocations (revision 5): each
slice re-verifies the live graph and records drift before numbering (*Global
constraints*). Slice B verified `0018`/`0019` with no drift.

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
   `rung`, `method`, `confidence`, the target fields `(grain, family_id,
   model_id, variant_id, family_key)`, and the full sorted evidence *items*
   (keys and values). As landed, this is stronger than sorted keys, and it
   deliberately excludes `TargetRef.category_slug`, which A3 adds.
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
   - Rename the two local result variables that were named `veto` (rung 0 at
     `:161`, rung 1 at `:196`) to `vetoed`. Otherwise the recipe yields
     `veto = veto(...)`, which rebinds the annotated callable parameter to a list
     and fails strict basedpyright. The evidence key stays `"veto"` and its
     values are byte-identical. The OEM fan-out filter (`:225`) calls
     `veto(...)` inline.
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
   CATEGORY_SLUG_MAX_LENGTH: Final = 50
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

   `rules_for` builds the `CategoryRules` on each call from the current module
   attributes (MS2-D-02); it does not bind them at import.
   Module docstring: record the legacy-default trap (MS2-D-03) and that the
   ORM-bound spec readers live in `resolver._SPEC_READERS`.
5. Run `uv run pytest tests/unit/test_categories.py tests/unit/test_ladder_veto.py tests/unit/test_ladder.py -q`
   and `uv run basedpyright src/hw_radar/matching/ladder.py src/hw_radar/matching/categories.py`
   (0 errors), then the full gate. Commit:
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
   - Add `ParsedListing.category_hint: str | None = Field(default=None, max_length=CATEGORY_SLUG_MAX_LENGTH, pattern=CATEGORY_SLUG_RE.pattern)`.
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
     (`ladder.py:154`). That proves the drive ladder never ran, without relying
     on a monkeypatch. `rules_for` resolves module functions per call, so a
     monkeypatch would be honored. The frozen `test_resolver.py` error-edge
     tests (≈:242–314) depend on that.
   - Invalid-hint case (as landed): a non-string or non-slug hint in
     `attrs_json` yields the error edge, never a drive fallback.
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
     - `complete` + `scope.complete is False` + ineligible (remote) → `None`
       (`test_gate_remote_complete_evidence_with_incomplete_scope_is_none`,
       added during Slice A verification). With eligible local evidence, the
       scope passes unchanged.
   - `test_gate_passes_none_scope_through`.
   - `test_continuity_counts_only_complete_or_eligible_truncated`, called with
     the two-argument signature from revision 3.
   - Revision 3 (F-04 residual) adds:
     - `test_remote_complete_evidence_with_incomplete_or_missing_scope_does_not_count`:
       APIFY `complete` evidence with `scope.complete=False`, and with
       `scope=None`, both return False. With `scope.complete=True` it returns
       True.
     - `test_local_continuity_mapping_unchanged`: for every local evidence the
       local mapping can produce, the result equals the revision-2 rule, for
       every scope value (None, complete, incomplete).

   Fails: names missing.
2. **GREEN:**
   - In `catalog/models/ops.py`, add `ProviderKind` and `RunCompleteness`
     `TextChoices` and export them.
   - In `contracts.py`, add `ProviderRunEvidence` (MS2-D-11 fields,
     `ConfigDict(frozen=True, extra="forbid")`, the validator above).
   - In the new `acquisition/providers.py`, add `gate_delist_scope(scope:
     DelistScope | None, evidence: ProviderRunEvidence) -> DelistScope | None`
     and `counts_toward_sweep_continuity(evidence: ProviderRunEvidence, scope:
     DelistScope | None) -> bool` (MS2-D-11 as amended in revision 3). Each
     docstring states the ADR-0021/D5 invariant ("only `complete` may keep
     `complete=True`; remote runs never reach stale absence"). The continuity
     docstring adds that a remote run counts only when both the evidence and
     the scope say complete.
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
       parsed)`, then `evidence = provider.run_evidence(...)`.
     - If `counts_toward_sweep_continuity(evidence, scope)` (the ungated
       provider scope), call `_record_sweep_continuity`. Otherwise call the new
       `_break_sweep_continuity(site)`, which sets the FULL lane's
       `continuous_since := None` (MS2-D-11). The helper sits beside
       `_record_sweep_continuity` and is a no-op without a `SourceConfig`.
     - `_apply_delist` runs on `gate_delist_scope(scope, evidence)` when that
       is not None.
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
   - `test_ineligible_run_breaks_existing_continuity`: with an old non-null
     `continuous_since`, a successful truncated remote FULL run leaves it
     `None`. The test is parametrized over `truncated`, `partial_failure`, and
     `failed` evidence, which all reach the success path in this fake.
   - `test_local_after_prolonged_truncated_remote_cannot_mass_delist`: seed an
     old non-null `continuous_since`, a prior successful local FULL run, and
     several active listings whose `last_seen` is well past the grace. Then run
     several truncated remote runs at intervals within the continuity
     tolerance. Then run a local fake whose `DelistDetector` returns an
     incomplete scope with a short grace. Expected: zero delists, and
     `continuous_since` restarts at the local run's `observed_at`.
   - `test_complete_remote_evidence_with_incomplete_scope_cannot_stale_delist`
     (added during Slice A verification): remote `complete` evidence with a
     `complete=False` scope delists nothing.
   - `test_remote_complete_evidence_incomplete_scope_breaks_continuity_then_local_cannot_stale_delist`
     (revision 3, F-04 residual). Seed an old non-null `continuous_since`, a
     prior successful local FULL run, and active listings whose `last_seen` is
     well past the grace. Run several remote FULL runs with `complete` evidence
     and a `complete=False` scope, at intervals within the continuity
     tolerance. After the first one, `continuous_since` is `None`. Then run a
     local incomplete sweep with a short grace. Expected: zero delists, and
     `continuous_since` restarts at the local run's `observed_at`.
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

**Revision 3 correction (F-04 residual).** A0–A6 had already landed when round
2 reported the residual. The fix lands on `dev` as a Slice A correction:
- scope: `acquisition/providers.py` (the two-argument
  `counts_toward_sweep_continuity`), its one call in `acquisition/pipeline.py`,
  and the Slice A-owned `tests/unit/test_provider_evidence.py` and
  `tests/db/test_collection_provider.py`;
- tests: the three named in A4 and A6 above. The existing unit continuity test
  moves to the two-argument call with its expectations unchanged.

The frozen pre-MS-2 test files stay unmodified, and so does the A0 baseline.

### A7 — Slice close-out

1. Full gate plus `makemigrations --check --dry-run`, both green.
   `git diff --stat <slice-base> -- tests/unit/test_ladder.py tests/db/test_resolver.py tests/db/test_rung0_regression.py tests/db/test_ratification_corpus.py tests/db/test_pipeline.py tests/db/test_pipeline_demo.py tests/db/test_listing_delist.py tests/db/test_source_*.py tests/db/test_poller_*.py tests/db/test_ms1_acceptance.py src/hw_radar/acquisition/sources src/hw_radar/acquisition/heartbeat.py src/hw_radar/poller`
   shows no changes.
2. Update `docs/TODO.md` (Slice A done; Slice B next) and the `docs/STATUS.md`
   focus line. Commit `docs: record MS-2 slice A completion`.

**Slice A acceptance:**
- The syrupy baseline is unchanged.
- All frozen regression files pass unmodified.
- Truncated, partial, and failed provider results cannot delist, and they break
  any existing continuity. Complete ones can delist. Remote `complete` evidence
  with an incomplete or missing scope cannot delist, and it breaks continuity
  (revision 3).
- The local continuity mapping is unchanged.
- An unregistered category never runs drive rules.
- There is no migration.

**Exit evidence:**
- gate output with the test count (≥ 552 + new);
- the empty frozen-file diff;
- the commit list.

## Slice B — First-class category specs and rules

**Scope:** MS2-D-04, -05, -06, -21, -27.

**Task order:** B1 → B2 → B3 → B4a → B4b → B4c → B6 → B5. B5 is the close-out
and runs last.

**Files:**
- `catalog/models/identity.py` (satellites + choices);
- migrations `0018`, `0019`;
- `matching/categories.py` (register gpu/ram/cpu + basic-watch);
- new `matching/rules/{gpu,ram,cpu,basic}.py`;
- `matching/resolver.py` (spec readers, cross-category guard, `auto_accept`,
  `variant_on_demand`);
- `refdata/{contracts,persist}.py` (category-discriminated spec payload);
- `refdata/seeds/`;
- `catalog/admin.py`;
- `matching/eval/{corpus,evaluate}.py` and
  `catalog/management/commands/harvest_corpus.py` (B6 only);
- **existing Slice A tests, narrowly (B3; revision 3, review N-03).** Slice A
  pinned a drive-only registry, and B3 registers more categories, so these two
  files are explicitly *not* frozen for B. Only the edits listed here are
  allowed:
  - `tests/unit/test_categories.py`:
    - `test_registered_categories_is_drive_only_in_slice_a` (≈:19–20) is
      renamed `test_registered_categories_matches_ms2_registry`. It asserts
      the exact set `{drive, gpu, ram, cpu, nic, hba, motherboard, server}`.
    - `test_rules_for_unregistered_is_none` (≈:35–36) uses the sentinel slug
      `zz-unregistered` instead of `gpu`.
    - The `dispatch_category("gpu")` pass-through assertion (≈:31) may stay,
      since it tests dispatch, not registration.
  - `tests/db/test_resolver_dispatch.py`:
    `test_unregistered_hint_writes_none_edge_and_never_runs_drive_rules`
    (≈:158–173) hints `zz-unregistered` instead of `gpu`, and asserts
    `evidence["category"] == "zz-unregistered"`. Every other assertion stays:
    NONE grain, `unsupported_category`, no `mpn_hypothesis`, no model.
  - `zz-unregistered` is a permanent sentinel. The exact-set registry
    assertion excludes it, and the `categories.py` module docstring reserves
    the `zz-` prefix for tests, never to be registered. A future category
    therefore cannot silently turn the unsupported-category tests green for
    the wrong reason.
  - Unchanged: `test_drive_rules_bind_existing_functions_by_identity`, the
    explicit-drive and legacy-default resolver tests, the invalid-slug tests,
    `test_spec_readers_match_registry`, and the A0 baseline
    (`tests/db/test_ms2_decision_baseline.py`, with no `--snapshot-update`).
    B's frozen-file diff lists every other pre-B test file.
  - Revision 5 (implementation-driven clarification from B4a): one more allowed
    edit. `tests/unit/test_refdata_contracts.py` (≈:55–56) gains
    `assert isinstance(spec, SeedDriveSpec)` type narrowing before its unchanged
    `capacity_tb`/`rpm` assertions. `SeedModel.spec` became a tagged union, so
    strict basedpyright needs the narrowing; no assertion changes.
  - Revision 5 (implementation-driven clarification from B4c): tests that pin
    the shipped seed corpus are scoped to drive documents, with **no**
    expected-value or snapshot changes, because any seed addition would
    otherwise touch them:
    - `tests/unit/test_refdata_loader.py::test_repo_seed_corpus_totals`: the
      drive assertions are scoped to `category == "drive"` documents, and new
      exact whole-corpus assertions (manufacturer keys, model and alias totals,
      per-category model counts) cover the first-party GPU, RAM, and CPU seeds;
    - `tests/db/test_refdata_import.py`: the `docs` fixture is drive-filtered;
    - the B-era `tests/db/test_refdata_categories.py`:
      `test_drive_documents_validate_unchanged` and
      `test_drive_import_byte_identical` are drive-filtered, with the snapshot
      unchanged.

- **B1 — Satellites (0018).** Add `GpuSpec`, `RamSpec`, and `CpuSpec` per the
  MS2-D-04 table, each with `retention_constraints("<table>")` and
  `retention_indexes("<table>_expires")`. Revision 5 (implementation-driven
  clarification): the live graph was verified with no drift, so B1 is
  `0018_category_spec_satellites` and B2 is `0019_seed_categories`. The
  satellites are 1:1 on `ProductModel` with `primary_key=True`, like `DriveSpec`,
  and use the choices `GpuChipVendor`, `GpuInterface`, `GpuCooling`,
  `RamGeneration`, and `RamModuleType`.
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
  - **Acceptance policy (MS2-D-21).** Add `AcceptancePolicy` and
    `CategoryRules.acceptance`, and apply the policy in `_run_ladder` after
    `decide`. Accepted non-drive edges record `alias_source_kind`.
  - **Category change writes an edge.** This is a residual from Slice A. The
    resolver's no-spam rule writes no new edge when the outcome is unchanged.
    So a listing whose current edge is a `none` or `review` with
    `category=drive` keeps that stale category when a later snapshot hints
    `gpu`. B3 treats a change of `evidence["category"]` as a decision-input
    change and writes a new edge. For drive listings the category never changes,
    so drive behavior and the A0 baseline are unaffected.
  - **Version.** Bump `MATCHER_VERSION` because rules were added. The drive
    baseline snapshot must still match (decisions, not the version string).
  - Tests:
    - `tests/unit/test_{gpu,ram,cpu}_rules.py`: extraction and veto tables.
    - `tests/db/test_resolver_categories.py`: rung-1 review while auto-accept is
      off; `test_cross_category_alias_goes_to_review`; server no-variant.
    - `test_non_authoritative_alias_never_auto_accepts_new_category`. With
      auto-accept enabled through a test-only registration, the same token is
      aliased by `catalog_authoritative`, by `manual`, and by `listing_derived`.
      Only the authoritative alias accepts; the other two yield `review` with
      `acceptance_policy` evidence.
    - `test_alias_learned_from_earlier_observation_goes_to_review`: a listing
      learns a `listing_derived` alias, and a later listing hits it.
    - `test_family_grain_fanout_is_review_for_new_categories`.
    - `test_prior_from_non_authoritative_edge_is_review`.
    - `test_drive_acceptance_unchanged` (the A0 baseline).
    - `test_category_change_writes_new_edge_even_when_outcome_unchanged`.
    - Revision 5 (owner §29 category list; named so each exists explicitly):
      - `test_exact_authoritative_alias_accepts_when_enabled` [gpu, ram, cpu]:
        with a test-only `auto_accept=True` registration, an exact
        `catalog_authoritative` alias at model grain accepts.
      - `test_fuzzy_or_merchant_only_evidence_never_auto_accepts` [gpu, ram,
        cpu]: attribute-only evidence with no alias hit, and a hit only on a
        `listing_derived` or merchant-title token, yield `review` or `none`,
        never `accept`.
      - `test_hard_attribute_contradiction_blocks_accept` [gpu, ram, cpu]: an
        exact authoritative alias whose extracted hard attribute contradicts the
        target spec (for example VRAM, DDR generation, or socket) yields
        `review` with `veto` evidence.
    - The N-03 edits to `tests/unit/test_categories.py` and
      `tests/db/test_resolver_dispatch.py` listed under *Files*, landing in
      the same commit as the registration so the gate never goes red.
- **B4 — Reference seeds.** B4 is split into three sub-tasks. Drive seed
  documents stay valid, unchanged.
  - **B4a — Contract (`refdata/contracts.py`).**
    - Add a `category` discriminator to `SeedDocument`, where `drive` is the
      default when absent, so existing drive documents validate unchanged.
    - Make `SeedModel.spec` a tagged union
      `SeedDriveSpec | SeedGpuSpec | SeedRamSpec | SeedCpuSpec`. Each variant's
      fields mirror its satellite by name, following the existing convention
      (`contracts.py:57-58`).
    - Add per-row provenance: `source_url` and `retrieved_on` on every non-drive
      `SeedModel`. The per-document block alone no longer satisfies IR-007 for
      new categories.
    - Extend the `SeedProvenance.source_kind` Literal with a non-first-party
      value that maps to alias `source_kind=manual`. This follows MS2-D-06:
      authoritative status applies only to first-party manufacturer pages.
      Revision 5 (implementation-driven clarification): the value is
      `non_first_party`, and the importer refuses such documents until R32
      clears (MS2-D-06).
    - Tests: `test_refdata_categories.py::test_drive_documents_validate_unchanged`,
      `::test_non_drive_row_without_source_url_rejected`,
      `::test_spec_variant_must_match_document_category`, and
      `::test_non_first_party_row_maps_to_manual`.
  - **B4b — Importer (`refdata/persist.py`).**
    - Replace the hard-coded `slug="drive"` lookup (`:152`) with the document's
      category.
    - Replace the `DriveSpec` write (`:208`) with a category → satellite
      dispatch table, keyed like `_SPEC_READERS`.
    - The `catalog_authoritative` / `manufacturer_reference` gating
      (`:225-228`) applies only to first-party rows. Conflict detection stays
      unchanged.
    - Tests: `::test_gpu_document_imports_to_gpu_spec_and_gpu_category`, and
      `::test_drive_import_byte_identical` (the frozen `test_refdata_*` suites
      stay green).
  - **B4c — Seeds.** Author a small curated seed per category, enough for C's
    representative cases, from the first-party sources in MS2-D-06. Record the
    URL and date for every row. The suggested scope is homelab and used-server
    relevance:
    - CPU: about 10–15 rows (recent EPYC SP5 and Xeon LGA4677, plus AM5 and
      LGA1700/1851 if consumer coverage is wanted);
    - GPU: about 8–12 rows (NVIDIA V100/A100/H100 class, high-VRAM GeForce, and
      AMD Instinct);
    - RAM: about 10–15 rows (DDR4 and DDR5 RDIMM at 16/32/64 GB, sourced from
      Micron's decoder first).

    Community decode grammars (Samsung, SK hynix) and OEM↔module-maker
    equivalences are seeded as `manual` or not at all.

    Revision 5 (implementation-driven clarification): the row counts above are
    superseded by what first-party sources support, researched 2026-09-24: CPU
    9, GPU 8, and RAM 2 first-party rows. Three more Micron-authored PDFs are
    hosted on third-party domains, so they count as non-first-party and are
    blocked by R32. RAM expansion is a follow-up.
- **B6 — Category hint through the corpus tooling (MS2-D-27).** Add
  `ListingFields.category_hint`. `_staging_entry` writes the key only when it is
  non-null, and `_ingest` passes the hint through. Revision 5 (implementation-driven
  clarification): B6 landed on `dev` as `a789e98`. The hint is validated with
  `CATEGORY_SLUG_RE`, and its key sits inside the staging entry's `listing`
  block.
  - Tests (new file `tests/db/test_corpus_category_hint.py`):
    - `test_harvested_non_drive_entry_reaches_category_rules`: a `ParsedListing`
      with `category_hint="gpu"` and a drive-shaped title that the drive rules
      would accept at rung 1 goes through `_staging_entry`, a JSONL round trip,
      `load_corpus`, and `evaluate_corpus`. The resulting edge has
      `evidence["category"] == "gpu"` and never a drive model target.
    - `test_unhinted_staging_entry_bytes_unchanged`.
    - `test_existing_drive_corpus_loads_unchanged`.
    - The A0 baseline is unchanged.
- **B5 — Admin + close-out.** Register the satellites in Django admin, then run
  the gate and update TODO/STATUS.

**Acceptance:**
- AC-1 holds.
- GPU/RAM/CPU exact-alias hits reach `review` while auto-accept is off.
- Cross-category hits never auto-accept.
- With auto-accept enabled, only authoritative aliases at model or variant
  grain accept for the new categories.
- A harvested non-drive corpus entry replays through its own category rules.
- The drive baseline is unchanged.
- The only edits to existing tests are the listed N-03 edits.

**Exit evidence:** migration apply/rollback transcript on a DB holding drive data;
gate; `git diff <slice-base> -- tests/unit/test_categories.py
tests/db/test_resolver_dispatch.py`, which shows only the N-03 edits; and the
empty frozen-file diff for every other pre-B test file.

## Slice C — Watches, eligibility, shortlist read model

**Scope:** MS2-D-07, -08, -09, -20, -29.

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
  `(watch, verdict)`, and the MS2-D-09 input-binding fields, including the
  nullable `resolution` FK with `SET_NULL` and `catalog_fingerprint`,
  MS2-D-29).
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
  - It writes or updates `WatchEvaluation`, mirroring listing retention and
    stamping the input binding (MS2-D-20). The catalog fingerprint comes from
    the same `catalog_inputs()` call that feeds the product clauses
    (MS2-D-29).
  - It runs in the pipeline after resolve via a `ListingEvaluator` protocol.
    `run_collection(..., evaluator=None)` binds the production `WatchEvaluator`
    (MS2-D-20), so poll, heartbeat-fired, and recovery-probe runs all evaluate
    without caller changes. `heartbeat.py` and `poller/service.py` are not
    edited in C. A listing whose resolution raised is skipped. Failures are
    counted in `detail_json["evaluator_errors"]` and never block ingestion.
  - Tests:
    - `tests/unit/test_eligibility_clauses.py`: per-clause tri-state tables,
      including listing-tier contradiction ⇒ `no_match`, listing-tier agreement
      ⇏ `match`, and review-resolution ⇒ `unknown`.
    - `tests/unit/test_eligibility_aggregate.py`.
    - `tests/db/test_watch_evaluation.py` (AC-2): a parametrized drive/gpu/ram/cpu
      × match/no_match/unknown set built on B's curated seeds and accepted
      resolutions. Revision 5 (owner §29) names two cases explicitly:
      `test_missing_required_catalog_attribute_is_unknown` [drive, gpu, ram,
      cpu] (a required clause whose catalog value is NULL or whose spec row is
      absent yields `unknown`, never `match`), and
      `test_hard_catalog_contradiction_is_no_match` [drive, gpu, ram, cpu].
    - Soft-threshold test.
    - Delist pull-forward and purge-registry test.
    - `test_pipeline_evaluator_failure_isolated`.
    - `tests/db/test_evaluation_binding.py`:
      - `test_prior_match_then_contradictory_evidence_updates_verdict`: a new
        over-budget snapshot turns `match` into `no_match`.
      - `test_evaluator_failure_after_prior_match_leaves_shortlist`: the
        evaluator raises on the new snapshot, so the old `match` row is
        `pending` and absent from `shortlist()`.
      - `test_resolution_change_makes_evaluation_pending`.
      - `test_heartbeat_fired_run_updates_watch_evaluation`: `run_heartbeat`
        with a transitioning fake probe fires the FULL run, and the evaluation
        is re-bound to the new snapshot with no evaluator argument passed.
      - `test_recovery_probe_run_evaluates`.
      - `test_listing_with_resolver_error_is_not_evaluated`.
      - Revision 3 (MS2-D-29, F-03 residual):
        - `test_same_target_catalog_correction_excludes_match_from_shortlist_immediately`.
          A GPU listing has an accepted model-grain edge; a test-only
          auto-accept registration or a manual edge may be used. The watch
          requires `coolings=[active]`, and the evaluation is `match` in
          `shortlist()`. A corrected seed is re-imported through
          `refdata.persist`, setting that model's `GpuSpec.cooling` to
          `passive`, with the same alias and the same target. There is no new
          observation and no resolver run. Immediately afterwards the row is
          absent from `shortlist()` and appears as `pending` in
          `review_queue()`. `evaluate_watches --pending` then persists
          `no_match`.
        - `test_catalog_correction_by_queryset_update_is_detected`: the same
          correction made with `GpuSpec.objects.filter(...).update(...)`,
          which proves that no writer hook is needed.
        - `test_family_membership_change_makes_family_grain_evaluation_pending`.
        - `test_unrelated_catalog_edit_keeps_evaluation_current` (a negative
          control: no churn).
- **C4 — Read model.** Implement `shortlist(watch_id)` and
  `review_queue(watch_id)` with freshness `fresh | stale` (stale = latest
  snapshot older than 2 × the source's `cadence_baseline_s`; a labeled
  assumption, tunable). E adds `budget_paused`. Add the `show_shortlist`
  command.
  - Only current rows (MS2-D-20) qualify. `review_queue()` labels rows
    `unknown` or `pending`. `evaluate_watches --pending` re-evaluates non-current
    rows.
  - Tests: ordering by landed USD; only current evaluations are included (a
    stale requirement version, snapshot, resolution, evaluator version, or
    catalog fingerprint each excludes the row and shows it as `pending`);
    `test_prior_match_then_contradictory_evidence_leaves_shortlist`; delisted
    and expired rows are excluded; the result has no score field; a `grep` guard
    asserts no import of any scoring module.
- **C5 — Close-out:** gate; TODO/STATUS.

**Acceptance:**
- AC-2 holds.
- The shortlist runs on fixture observations with no ADR-0011 artifact (AC-3's
  code half).
- Evaluator failures are isolated, and they fail closed: the affected rows
  become `pending`.
- Evaluation runs on the poll, heartbeat-fired, and probe paths.
- A catalog correction to an evaluated target makes its evaluation `pending`
  immediately (MS2-D-29).

## Slice D — Apify provider (source-agnostic)

**Scope:** MS2-D-12…-16, -18, -22…-25, -28, -30, -31, -33, -35…-37, the
D-side counters of -32, and (revision 5) -38, -39, and the D-side fixtures of
-42.

**PRs:** D-prep is D1 + D3. D (core) runs in the order D2 → D4 → D10 → D5 →
D6 → D7 → D8 → D11 → D12 → D9. The staged importer (D10) comes before the jobs
and the tests that import through it. See *Slice order*.

**Slice D entry gate (revision 4).** Before D (core) implementation starts at
D2, the D/E asynchronous-ordering design gets a focused design review against
the then-current code. D-prep (D1, D3) is not gated.
- **Design under review:** the watermarks (MS2-D-30, -35), per-scope ordered
  continuity (MS2-D-31, -36), per-observation retention (MS2-D-37), the charge
  horizon (MS2-D-32, -34), and the absolute retention deadline (MS2-D-33).
- **Re-verify against code:** the seams these decisions cite still behave as
  cited. That means `persist.upsert_listing` and `append_snapshot`
  (`persist.py:52-107`); `_persist_all`'s relist and snapshot calls
  (`pipeline.py:284-289`); `_record_sweep_continuity`,
  `_break_sweep_continuity`, and `_apply_delist` (`pipeline.py:98-205`);
  `Listing.mark_delisted` and `mark_relisted` (`market.py:330-377`, `:482`);
  `SourceLaneState` (`ops.py:190`); and the frozen continuity tests in
  `test_source_ebay.py` and `test_collection_provider.py`.
- **Re-verify externally** (these are already D3 and E2 gates; the review
  confirms the design does not depend on an unverified answer):
  - Apify wire fields: `usageTotalUsd`, `buildNumber`, the `usage` component
    keys, platform `maxItems` semantics, and the response to aborting a run
    that already finished (MS2-D-15, -33; D3).
  - Storage expiry: default-storage expiry for the chosen plan, and whether a
    per-run or per-storage expiry exists (MS2-D-25, -33 *Reopen if*).
  - Billing completeness: whether run usage covers the importer's reads, the
    deletes, and timed default storage; whether `GET` run polls bill; and the
    direction of transfer pricing (MS2-D-32; E2).
- **Also settle:** the lock order and contention of the MS2-D-35 scope-row
  locks against the poller's lane-state writes, and risk R23.
- **Revision 5 scope extension (owner-clarified (s2, 2026-09-24)).** The review
  also covers, against the D3 wire facts:
  - billing-cycle accounting: cycle discovery, the stored cycle row, straddling
    reservations, and the boundary guard (MS2-D-34, -40);
  - usage finalization: the provisional → finalized rule and the settle delay
    (MS2-D-41);
  - post-run usage: the bound-until-measured allowance and the account-diff
    measurement (MS2-D-32, -41);
  - the three clocks: each transition in the MS2-D-39 table is checked against
    the code that will implement it.
- **Outcome.** Record the review and its disposition in *Review lineage*. A
  finding that changes a contract revises this plan before D2 starts.

**Files:**
- new `acquisition/apify/{client,contract,provider,jobs}.py`;
- revision 5 (MS2-D-38): the new Actor project `actors/hw-radar-synthetic-collector/`
  (`.actor/`, `contract/hw-radar-{input,listing,run}-v1.schema.json`,
  `pyproject.toml`, `uv.lock`, Dockerfile, `.actorignore`, source, `tests/`,
  `fixtures/`, `DEPLOYMENTS.md`), and `scripts/check.py` (the Actor project's
  gate commands). Revision 1–4's `acquisition/apify/schemas/` location is
  withdrawn;
- `tests/fixtures/apify_contract/v1/*`;
- `catalog/models/provider.py` (`ProviderRun`);
- migration `0021`;
- `contracts.py` (`ParsedListing.collection_scope`, `DelistScope.scope_key`);
- `persist.py` (scope write; insert-if-absent snapshot for provider imports;
  the ordering-guarded `observe_listing`, MS2-D-30, -35; and the
  `ObservationRetention` parameter of `append_snapshot`, MS2-D-37);
- `catalog/models/market.py` (`Listing.last_observed_at`, MS2-D-30;
  `Listing.last_absence_at`, raised by `mark_delisted`, MS2-D-35),
  `catalog/models/ops.py` (the NULL-scope watermarks on `SourceLaneState`,
  MS2-D-35, -36), and `catalog/models/provider.py` (`ScopeSweepContinuity`,
  MS2-D-31, -35, -36);
- `pipeline.py` (scope-filtered and newer-observation-guarded `_apply_delist`
  that raises the complete-sweep watermark; ordered per-scope continuity with
  timestamped breaks and the NULL-scope break rule; absence-gated relist and
  creation; explicit snapshot retention in `_persist_all`; stage functions
  extracted from `run_collection`; local-only `_median_body_bytes`);
- new `acquisition/retention_policy.py`;
- new `acquisition/apify/importer.py` (the stage machine);
- `poller/service.py` (apify start branch, provider-dispatched recovery probe,
  and the `apify-poll` job).

- **D1 — Contract and the Actor skeleton** (revision 5 rewrites the location,
  MS2-D-14, -38, -42).
  - Create `actors/hw-radar-synthetic-collector/` per MS2-D-38. Verify the
    Apify Python template layout and the Actor image's Python version against
    current official docs (research input `apify-ops.md` left both unconfirmed),
    and record the URL and date in the Actor README.
  - Commit the contract schemas under the Actor's `contract/`. Add the
    hw-radar Pydantic models in `acquisition/apify/contract.py` and
    `classify_run(remote_status, output, dataset_count, usable_count) ->
    (RunCompleteness, reason, TruncationReason | None)` per the MS2-D-14 mapping,
    including `contradictory_report` and `scope_mismatch`. Add `TruncationReason`
    (MS2-D-11) without touching `RunCompleteness`.
  - The listing row schema carries per-field `maxLength` caps, which supply the
    per-row byte cap in MS2-D-26. The input schema carries `maxItems`,
    `maxPages`, `maxRequests`, `maxBytes`, and `timeBudgetSecs`, and has no
    proxy field. The synthetic Actor's input adds `fixtureCommit`,
    `fixturePaths`, and `faultMode` (MS2-D-42).
  - The Actor's pure core enforces every cap itself, writes merchant content
    only to the default dataset, and writes only `OUTPUT` to the default KV
    store.
  - Extend `scripts/check.py` with the Actor project's basedpyright, pytest with
    coverage, and pip-audit commands (MS2-D-38). No Apify push or build happens
    in D-prep.
  - Add frozen fixtures: complete, **complete-empty**, **ambiguous-empty**
    (`SUCCEEDED`, empty dataset, `itemsDeclared` missing), **nonempty-unusable**,
    truncated-by-pages, truncated-by-items, truncated-by-bytes, timed-out,
    partial-with-errors, failed-with-items, missing-OUTPUT, unknown-schema,
    count-mismatch, complete-with-limit-hit, status-mismatch, and
    scope-mismatch. The Actor's own tests regenerate each fixture from its
    `faultMode`, so the hw-radar fixtures and the Actor's output cannot drift.
  - Tests: `tests/unit/test_apify_contract.py` covers:
    - `test_pydantic_models_equal_committed_contract_schemas` (the drift guard,
      reading the files under `actors/`);
    - `test_apify_input_schema_matches_contract_input` (MS2-D-14);
    - `test_invalid_input_rejected_before_start_request`;
    - the mapping table over every fixture;
    - "unknown or missing ⇒ never complete", and
      `test_unknown_schema_version_is_failed`;
    - `test_complete_empty_requires_all_zero_counts_and_pages_fetched`;
    - `test_empty_without_evidence_is_failed_ambiguous`;
    - `test_nonempty_unusable_is_failed_not_complete_empty`;
    - revision 5: `test_truncation_reason_distinguishes_items_pages_time_bytes`,
      `test_contradictory_report_is_failed` [complete + limit hit, count
      mismatch, status mismatch], and `test_scope_mismatch_is_failed`.
  - Actor tests (`actors/hw-radar-synthetic-collector/tests/`, run by the
    Actor project's pytest, no network): `test_limits.py::test_item_page_request_byte_caps_enforced`;
    `test_boundaries.py::test_no_proxy_configuration_or_django_import` (a
    source scan); `test_output.py::test_every_fault_mode_emits_schema_valid_output`;
    `test_output.py::test_kv_store_holds_only_output_record`.
  - The Slice A test files stay unmodified, including
    `test_completeness_values_are_the_adr0021_taxonomy` and
    `test_run_source_records_local_provider_evidence`.
- **D2 — Schema (0021).**
  - Add `ProviderRun` with the MS2-D-13 fields, including the
    MS2-D-22/-23/-25/-32/-33 state columns; `SourceConfig.collection_provider`
    + CHECK; `Listing.collection_scope` + index; `Listing.last_observed_at`
    (nullable, no backfill, MS2-D-30); `Listing.last_absence_at` (nullable,
    no backfill, MS2-D-35); the NULL-scope watermarks on `SourceLaneState`,
    all nullable and written only on the FULL lane row
    (`last_eligible_sweep_at`, `continuity_broken_at`,
    `last_complete_sweep_at`; MS2-D-35, -36); and the `scope_sweep_continuity`
    table with the same three watermarks (MS2-D-31, -35, -36).
  - `_apply_delist` scope filter (None ⇔ NULL).
  - Tests: CHECK, uniqueness of `(provider_kind, external_run_id)` and of the
    idempotency key (null run ids allowed for starting rows),
    `test_scoped_complete_sweep_cannot_delist_other_scopes`, and the
    `(source_site, collection_scope)` uniqueness of `scope_sweep_continuity`.
    Every revision-4 column is nullable with no backfill, so deployed rows
    upgrade with the column NULL, which each guard reads as "no bound".
    Frozen eBay delist tests stay green.
- **D3 — Client.** Add a thin `httpx.AsyncClient` wrapper for the seven run and
  storage calls plus the account reads (MS2-D-15, revision 5), with dataset
  pagination and the token from `HW_RADAR_APIFY_TOKEN`. The client never sends
  `maxItems` or `maxTotalChargeUsd`. The run response parser keeps
  `usage_total_usd` nullable and returns `finishedAt`, so E can apply the
  settle rule (MS2-D-41).
  - Start passes `memory` (MB) and `timeout` (s) as run options, following the
    MS2-D-15 naming trap. It returns the run's `options` so the caller can verify
    them.
  - Tests: `httpx.MockTransport`; the token never appears in logs, errors, or
    `detail_json`. `test_start_sends_memory_and_timeout_run_options` asserts the
    query parameters and that the httpx request timeout is independent of them.
    `test_delete_404_is_success`.
  - Before merge, verify the wire names still unconfirmed in MS2-D-15 against
    the official API reference: `usageTotalUsd`, `buildNumber`, the `usage`
    component keys (the proxy keys matter to MS2-D-26), the account-limits and
    monthly-usage fields MS2-D-40 reads, and whether invalid input is rejected
    before a billable run exists. Record the URL and date in the client
    docstring. (`maxItems` is settled as not applicable, revision 5.)
  - Revision 5 tests: `test_account_limits_parsed_into_cycle_bounds`,
    `test_monthly_usage_parsed_with_date_parameter`, and
    `test_client_never_sends_max_items_or_max_total_charge`.
- **D4 — Import provider.** Add `ApifyImportProvider(provider_run)` as a
  `CollectionProvider` of kind `apify`:
  - `fetch` reads the dataset into a `RawBatch` of dataset rows only, with
    `fetched_at=startedAt`. `OUTPUT` is stored on `provider_run` (MS2-D-14);
  - `parse` validates rows, rejects `siteKey` ≠ the run's site, rejects rows over
    the byte cap, and carries `category_hint`/`collection_scope`;
  - `delist_scope` returns a scope only for `complete` (including
    complete-empty), with `scope_key`;
  - `run_evidence` comes from `classify_run` and is never stale-eligible;
  - retention comes from `source_retention(site_key)` (MS2-D-25), never from
    defaults;
  - `_classify_batch` is not applied to dataset rows, and `_median_body_bytes`
    counts local runs only (MS2-D-28). Test:
    `test_provider_switch_does_not_soft_block_imports_or_local_runs`.
  - Revision 5 test: `test_imported_rows_carry_scope_and_category_hint`.
- **D5 — Jobs.**
  - The start job runs through `check_admission`, then a `BudgetAdmission`
    protocol. Its production binding is `DenyAllAdmission`, so live starts are
    impossible until E.
  - The start job creates the `provider_run` row with `admitted_at` and the
    absolute deadline before the start request (MS2-D-33).
  - The `apify-poll` interval job runs the three MS2-D-23 selectors. Terminal
    rows enter the MS2-D-22 stage machine (D10). Storage cleanup and overdue
    storage are their own units of work (D11).
  - Actor failure maps to ADR-0017 lifecycle events via the existing
    classification, at stage 5 or at reject.
  - Tests (`tests/db/test_apify_poll_job.py`):
    - `test_active_selector_polls_only_non_terminal_runs`;
    - `test_outstanding_selector_picks_terminal_rows_with_unfinished_import`;
    - `test_overdue_selector_picks_rows_past_deadline_regardless_of_remote_status`
      (parametrized: null, non-terminal, and terminal remote status);
    - `test_one_failing_row_does_not_block_others`;
    - revision 5: `test_start_uses_configured_actor_and_build_tag` (the Actor
      id and build tag come from settings) and
      `test_start_build_outside_contract_version_line_aborts` (MS2-D-38).
- **D6 — Idempotency (AC-6).**
  - `test_duplicate_completion_is_noop`: a second import of the same run adds no
    `ScraperRun`, `OfferSnapshot`, or `RawPayload` rows.
  - `test_crash_between_persist_and_mark_replays_once`: a crash inside the
    stage-1 transaction rolls back, and the replay imports exactly once.
  - `test_replayed_dataset_read_does_not_duplicate_snapshots`.
  - `test_retry_reuses_scraper_run`.
  - Revision 5 (owner §29): `test_duplicated_dataset_import_is_noop` (the same
    dataset imported through two `provider_run` claims of one run adds nothing)
    and `test_duplicated_remote_completion_observed_twice_imports_once` (two
    poll ticks both observe `SUCCEEDED`).
- **D7 — Provider switch (AC-4).** On a synthetic fixture source, run a local
  fake adapter, then import an Apify fixture with the same keys and scope, then
  switch back to local.
  - Listing pks are identical, snapshot history is continuous, and no listing is
    created.
  - `WatchEvaluation` rows persist.
  - Covered by `test_provider_switch_preserves_identity_history_and_watch_state`.
    Revision 5 (owner §29) adds assertions to it: the price history across the
    switch is contiguous (every snapshot, local and imported, on the same
    listing pk, ordered by `observed_at`), and no `external_run_id`, dataset id,
    or `import_idempotency_key` appears in any `Listing` or `OfferSnapshot` key
    column.
- **D8 — Truncation and emptiness (AC-5, ADR 0021 :100).**
  `test_truncated_actor_fixture_cannot_delist` runs end to end through import;
  so does the timed-out case. Revision 5 parametrizes it over the truncation
  reasons [item, page, time, bytes] and asserts the stored
  `provider_run.truncation_reason`. Also revision 5 (owner §29):
  `test_partial_failure_and_failed_actor_fixtures_cannot_delist` and
  `test_contradictory_report_rejected_and_breaks_continuity`. Two more tests
  also run end to end:
  - `test_complete_empty_run_delists_scope_end_to_end`: the complete-empty
    fixture passes classification, passes the zero-record parser-rot guard, and
    delists only its own scope's listings as `ABSENT_FROM_SWEEP`.
  - `test_ambiguous_empty_run_cannot_delist` and
    `test_nonempty_unusable_run_is_rejected_and_cannot_delist`: each FULL
    run is rejected, delists nothing, and breaks continuity for its scope.
    The PROBE counterparts are in D12.
- **D10 — Durable staged import (MS2-D-22, MS2-D-23).** Extract the stage
  functions from `run_collection`. The frozen pipeline tests must stay green.
  Then implement the importer state machine.
  - Tests (`tests/db/test_apify_import.py`, crash injection by raising a
    `BaseException` subclass from a patched stage boundary):
    - `test_crash_after_observation_commit_resumes_without_duplicates`;
    - `test_crash_during_resolution_resumes_and_evaluates`;
    - `test_crash_during_evaluation_resumes_and_evaluates`;
    - `test_crash_before_finalization_finalizes_once` (the lifecycle outcome is
      applied once, and the `ScraperRun` goes SUCCESS once).

    Each asserts that no rows are duplicated, the same `ScraperRun` pk is
    reused, and the `WatchEvaluation` ends bound to the imported snapshot.
  - Restart recovery for each state (`test_apify_poll_job.py`):
    `test_restart_recovers_each_unfinished_import_state` (parametrized over the
    four intermediate states) and
    `test_restart_retries_storage_cleanup_after_finalized_import`.
  - Read caps (MS2-D-32), in `test_apify_import.py`:
    `test_stage1_rollback_retry_counts_each_dataset_read` and
    `test_read_cap_exhausted_rejects_import_and_cleans_up`.
  - Ordering guards (MS2-D-30), in the new file
    `tests/db/test_apify_import_ordering.py`:
    - `test_reverse_completion_older_import_does_not_overwrite_newer_listing_state`.
      Remote runs R1 (started t1) and R2 (started t2 > t1) cover the same
      listings with different title, condition, international flag, and
      retention expiry. R2 finalizes first, then R1 imports. The current
      columns keep R2's values, and `last_observed_at == t2`. R1's snapshots
      are appended as history, and the latest snapshot is still R2's.
    - `test_older_import_does_not_revive_listing_delisted_by_newer_evidence`.
    - `test_older_complete_import_does_not_delist_listing_observed_by_newer_run`.
      This includes a listing first seen by the newer run.
    - `test_delayed_remote_import_after_switch_back_to_local_preserves_newer_content_and_delist_state`.
      A remote run is admitted at t0, then the source switches back to local.
      A local complete sweep at t1 changes listing X's title and delists
      listing Y. The t0 import then completes: X keeps its t1 content, and Y
      stays delisted with its `delisted_at` unchanged. The test is
      parametrized over a delete-on-delist class, where Y also stays redacted,
      and a merchant-fact class. Nothing the local sweep observed is delisted.
      Revision 4 adds one assertion: X's `expires_at` and Y's content columns
      are also unchanged (MS2-D-35).
    - `test_legacy_listing_without_watermark_accepts_first_observation`.
    - Revision 4 (MS2-D-35, N-01 residual), in the same file:
      - `test_delayed_observation_after_newer_delist_restores_no_content_and_keeps_expiry`
        (timeline 1). Listing X is observed at t0 and delisted at t2 by a
        complete sweep. An import observed at t1 (t0 < t1 < t2) then
        completes. X stays delisted with `delisted_at == t2`, its content
        columns and `expires_at` are unchanged, and `last_observed_at` stays
        t0. Parametrized over a delete-on-delist class, where X also stays
        redacted (`is_content_redacted()` holds, as in the existing
        switch-back test), and a merchant-fact class. Also parametrized over
        the t2 delist coming from a complete sweep or from stale absence. The
        stale case raises no scope watermark, so it isolates
        `last_absence_at`.
      - `test_delayed_import_of_unknown_key_after_newer_complete_sweep_creates_no_active_listing`
        (timeline 2). A complete sweep of scope S at t2 omits key K, which has
        no row. The t1 import then creates K already delisted
        (`ABSENT_FROM_SWEEP`, `delisted_at == last_absence_at == t2`),
        never active. Parametrized over S as a non-null scope and as the NULL
        scope, and over a merchant-fact class (the t1 snapshot is kept as
        history) and a bounded delete-on-delist class (the row is redacted and
        no snapshot is written). A later current-eligible observation at t3
        relists the same pk.
      - `test_delayed_observation_cannot_relist_row_delisted_before_newer_complete_sweep`:
        a row delisted at t0.5, a complete sweep of its scope at t2, then an
        import observed at t1. The row stays delisted.
      - `test_stage1_creation_and_newer_complete_sweep_serialize_on_scope_row`:
        two threads interleave the t1 import's stage 1 with the t2 sweep's
        delist stage under the scope-row lock. In both commit orders, K ends
        delisted.
  - Snapshot retention (MS2-D-37, M-01), in the new file
    `tests/db/test_persist_observation_retention.py`:
    - `test_reverse_order_bounded_observation_snapshot_keeps_its_own_deadline`.
      A bounded fixture source has a 6 h class. The t1 observation is applied
      first, then t0 < t1. The listing keeps t1's state and
      `expires_at == t1 + 6 h`. The t0 snapshot has `expires_at == t0 + 6 h`.
      After the clock passes t0 + 6 h, the purge sweep removes the t0
      snapshot while the listing and the t1 snapshot remain.
    - `test_older_bounded_observation_after_newer_delist_is_not_snapshotted`:
      the newer delist time is in the past, so no t1 snapshot row exists.
    - `test_merchant_fact_older_observation_appends_history_with_null_expiry`.
    - `test_append_snapshot_default_copies_listing_retention`: the
      compatibility default is byte-identical to today's behavior.
  - Per-scope continuity (MS2-D-31), in the new file
    `tests/db/test_scope_continuity.py`:
    - `test_scope_a_continuity_does_not_authorize_first_incomplete_scope_b_sweep`,
      parametrized with scope B as a non-null scope and as the legacy NULL
      scope. Scope A is swept completely for longer than the grace. Then the
      first incomplete local sweep of scope B runs with a short grace.
      Expected: zero delists, and B's continuity starts at that sweep.
    - `test_remote_scope_a_runs_then_local_incomplete_scope_b_sweep_cannot_stale_delist`:
      the same scenario with remote runs for A, then a switch to the local
      provider.
    - `test_run_not_sweeping_null_scope_breaks_null_scope_continuity`.
    - `test_older_eligible_sweep_does_not_move_scope_watermark_back`.
    - `test_legacy_null_scope_only_runs_keep_todays_continuity` (a
      behavior-preservation control).
    - Revision 4 (MS2-D-36, N-02 residual):
      - `test_delayed_null_scope_run_after_newer_scoped_run_cannot_restore_continuity`.
        NULL-scope continuity is established. A GPU-only FULL run at t_g
        breaks it (`continuity_broken_at == t_g`). A NULL-scope remote run
        observed at t_n < t_g then reaches stage 2: continuity stays null.
        Then a local incomplete NULL sweep runs, with a short grace and a
        listing aged past it. Expected: zero stale delists, and
        `continuous_since` equals that local sweep's time.
      - `test_eligible_sweep_older_than_break_is_noop`, parametrized over
        the NULL scope and a non-null scope.
      - `test_late_older_break_still_breaks` (fail-safe direction).
    - Scope complete-sweep watermark (MS2-D-35):
      `test_only_gated_complete_full_scopes_raise_complete_sweep_watermark`,
      parametrized over complete, complete-empty, truncated, stale-absence,
      PROBE, and rejected runs.
- **D11 — Source retention and storage cleanup (MS2-D-25).**
  - Tests (`tests/db/test_apify_retention.py`):
    - `test_bounded_source_import_retains_listings_snapshots_raw_and_evaluations`:
      a synthetic bounded fixture source with a 6 h TTL class. After import,
      every listing, snapshot, raw payload, and watch evaluation carries the
      class and `expires_at`. After expiry the purge sweep removes all of them.
    - `test_unregistered_source_import_rejected_not_merchant_fact`.
    - `test_registry_matches_every_local_adapter_retention`.
  - Tests (`tests/db/test_apify_storage_cleanup.py`):
    - `test_cleanup_after_successful_import`;
    - `test_cleanup_after_rejected_import`;
    - `test_cleanup_after_failed_remote_run`;
    - `test_cleanup_of_abandoned_run_at_deadline`;
    - `test_deadline_before_stage1_rejects_import_and_deletes_storage`;
    - `test_cleanup_failure_retries_with_backoff`;
    - `test_bounded_source_deadline_is_half_ttl`;
    - revision 3 (MS2-D-33, F-10 residual):
      - `test_deadline_is_anchored_at_admission_not_terminal_observation`:
        terminal status first observed long after admission leaves
        `storage_cleanup_due_at` unchanged.
      - `test_restart_after_source_ttl_rejects_expired_content_before_persistence`:
        a bounded fixture source is used, and the poller is "down" (clock
        moved) past the source TTL. On restart the import is rejected with
        `retention_expired` or `storage_deadline`. There are no new
        `Listing`, `OfferSnapshot`, or `RawPayload` rows, and selector 3
        deletes the storage.
      - `test_unobserved_run_past_deadline_is_aborted_and_cleaned`: the row
        still says `RUNNING` because termination was never observed. Past the
        deadline, selector 3 aborts, deletes both storages, and rejects the
        import, with no selector-1 observation.
      - `test_timeout_exceeding_retention_window_denied`.
      - `test_bounded_source_actor_start_denied`.
- **D12 — Provider-dispatched recovery probes (MS2-D-24).**
  - Tests (`tests/db/test_apify_recovery_probe.py`; admission is bound to a
    test-only `AllowAllAdmission`, and production stays deny-all):
    - `test_local_success_cannot_clear_actor_provider_failure`: a paused
      apify-provider source also has a registered local fake that would succeed.
      The local fake's `fetch` is never called, and the source stays paused.
    - `test_budget_admitted_actor_probe_recovers_source`: a PROBE fixture import
      finalizes `complete` and the source is reactivated.
    - `test_actor_probe_never_delists_or_touches_continuity`.
    - `test_rejected_probe_records_probe_failure_and_leaves_continuity_unchanged`
      (revision 3, F-07 residual). It is parametrized over invalid `OUTPUT`,
      ambiguous-empty output, and storage-deadline expiry. Each case starts
      with a non-null `continuous_since`, both for the lane and for the
      probe's scope row. Each ends with the outcome `PROBE_FAILURE`, both
      continuity values unchanged, zero delists, and the source still paused.
    - `test_partial_failure_probe_keeps_source_paused`.
    - `test_denied_actor_probe_starts_nothing_and_stays_paused`.
    - `test_one_outstanding_probe_per_source`.
    - `test_local_provider_probe_path_unchanged`.
- **D9 — Close-out.** This runs last in D (core). Gate; TODO/STATUS. Record
  that live Actor runs remain owner-gated.

**Acceptance:**
- AC-4, AC-5, and AC-6 are proven against fixtures.
- Every import crash window resumes to exactly one finalized result, and the
  result is evaluated.
- Remote retention never defaults, and Apify storage is deleted by an
  absolute deadline in every run outcome, whether or not termination was
  observed.
- A delayed or reordered import never overwrites newer listing state, writes
  content to or revives a listing that newer absence retired, creates an
  active listing for a key a newer complete sweep omitted, or delists a newer
  observation.
- Every snapshot carries its own observation's retention deadline.
- One scope's continuity never authorizes another scope's stale absence, and
  an older run never restores continuity that a newer break ended, on any
  scope.
- A rejected probe records `PROBE_FAILURE` and leaves continuity unchanged.
- Recovery probes honor the selected provider.
- A complete-empty result is distinguishable from a failed one.
- No code path can start a live run (deny-all).
- The contract schemas are committed once, in the Actor's directory, and both
  hw-radar's models and the Actor's output are tested against them (revision 5,
  MS2-D-14, -38).

## Slice E — Apify spend ledger and admission

**Scope:** MS2-D-17, -26, -32, -34, the E-side latch wiring of -33, and
(revision 5) -40 and -41.

**Files:**
- new `acquisition/apify/budget.py`;
- `catalog/models/provider.py` (`ApifySpendReservation`, `ApifyBudgetLatch`,
  and, revision 5, `ApifyBudgetCycle`);
- migration `0022`;
- `acquisition/apify/jobs.py` (bind the real admission; the reconcile unit in
  the outstanding selector);
- settings keys (values are not secret):
  - `HW_RADAR_APIFY_ENABLED`, default false;
  - the unit prices `…_USD_PER_CU`, `…_DATASET_*`, `…_KV_*`, and
    `…_TRANSFER_USD_PER_GB`. They carry no live default; each carries its URL
    and date;
  - `…_MARGIN`, `…_ESTIMATOR_VERSION`, `…_MAX_TIMEOUT_S`, and
    `…_STORAGE_CLEANUP_MAX`;
  - revision 5 (MS2-D-40, -41, -43): `…_CYCLE_TARGET_USD` (12.00, validated
    ≤ 12.00), `…_OPERATOR_ALLOWANCE_USD` (0.50), `…_WATCH_REFRESH_RESERVE_USD`
    (3.00), `…_ACCOUNT_MARGIN_USD` (default 10% of the observed prepaid
    credit), `…_CASH_CEILING_USD` (20.00), `…_ACCOUNT_SNAPSHOT_MAX_AGE_S`
    (900), `…_CYCLE_BOUNDARY_GUARD_S` (3600), `…_USAGE_SETTLE_DELAY_S` (10),
    `…_POST_RUN_COST_MODE` (`bound`), `…_ACTOR_ID` (no default), and
    `…_ACTOR_BUILD` (`prod`);
  - withdrawn in revision 5 (owner-overridden by OQ23 and MS2-D-41):
    `…_SAFETY_MARGIN_USD`, `…_CAP_DEDUCTION_USD`, and `…_OVERRUN_TOLERANCE`;
  - revision 3 (MS2-D-32, -33): `…_MAX_DATASET_READS` (3),
    `…_MAX_KV_READS` (3), `…_MAX_DELETE_ATTEMPTS` (10), `…_MAX_KV_WRITES`,
    `…_MAX_KV_BYTES`, `…_IMPORT_MARGIN` (1 h), and `…_STORAGE_MAX_LIFETIME`,
    which has no default and denies live admission while unset. The defaults
    in parentheses are assumptions, except the owner's $12 target and $20 cash
    ceiling. (Verified account state 2026-09-24: data retention 31 days, which
    the operator uses to set `…_STORAGE_MAX_LIFETIME`.)

  The revision-1 `…_PER_RUN_OVERHEAD_USD` key is withdrawn.
- new commands `apify_spend_report` and `apify_budget_reset`.

- **E1 — Schema (0022).** Add `ApifySpendReservation` with:
  - `provider_run` OneToOne null (null for denials) and `source_site`;
  - `admission_class` (`watch_refresh | discovery`) and `status`
    (`reserved | usage_provisional | usage_finalized | reconciled | released |
    denied`, MS2-D-32, MS2-D-41);
  - revision 5: `usage_provisional_usd`, `usage_finalized_usd` and
    `usage_finalized_at` (written once), `post_run_cost_usd` and
    `post_run_cost_mode`, and `denial_reason` values for the MS2-D-40 denials;
  - `estimate_usd` and `actual_usd` (Decimal 10,4); `execution_bound_usd` and
    `post_run_liability_usd` (Decimal 10,4, MS2-D-32); a `component_bounds`
    JSON breakdown; `estimator_version`; `reserved_at` (admission time,
    provenance only, never changed); `reconciled_at`; `last_charge_at`
    (nullable, set at reconciliation, the window anchor, MS2-D-34); and
    `denial_reason`;
  - indexes on `reserved_at`, `status`, and `(status, last_charge_at)`.
  - `ApifyBudgetLatch` (MS2-D-26): `tripped_at`, `provider_run` null, `reason`,
    `cleared_at`, `cleared_reason`, and `estimator_version`.
  - `ApifyBudgetCycle` (revision 5, MS2-D-40): `cycle_start` (unique),
    `cycle_end`, `allocation_usd`, `account_prepaid_credit_usd`,
    `account_base_price_usd`, `account_limit_usd`, `account_usage_usd`,
    `account_observed_at`, and `opened_at`. It is not retention-bearing (no
    merchant content).
- **E2 — Pure policy.** Implement `estimate_run_cost` (the MS2-D-26 component
  sum) and `decide_admission`.
  - Before writing the price defaults, re-verify every unit price and the
    direction semantics of data transfer on the official pricing page. Record
    the URL and date. If transfer semantics cannot be verified, keep live
    admission denied (`pricing_unverified`).
  - Tests (`tests/unit/test_apify_budget.py`):
    - the component formula;
    - `test_exact_boundary_admitted_and_epsilon_over_denied` (equality at the
      class cap is admitted; +0.0001 is denied);
    - revision 5 (owner §29 budget list; MS2-D-40):
      `test_reservation_exactly_equal_to_remaining_is_admitted`;
      `test_one_cent_over_remaining_is_denied`;
      `test_cycle_bounds_come_from_account_limits_not_calendar`;
      `test_account_headroom_below_project_target_binds` (the account's
      remaining prepaid allowance is smaller than the target);
      `test_project_target_below_account_headroom_binds`;
      `test_admission_never_relies_on_overage` (an account limit raised above
      the prepaid credit adds no admissible headroom);
      `test_plan_base_price_above_cash_ceiling_denies_all`;
      `test_stale_or_unreadable_account_snapshot_denies`;
      `test_target_setting_above_twelve_rejected`;
      `test_trailing_window_is_report_only`;
    - withdrawn with the OQ23 deduction (revision 5):
      `test_safety_margin_and_oq23_deduction_lower_hard_cap` and
      `test_unset_cap_deduction_denies_live_admission`;
    - `test_missing_unit_price_denies_live_admission`;
    - `test_estimate_includes_capped_reads_deletes_and_storage_lifetime`
      (MS2-D-32): the reservation grows linearly with
      `…_MAX_DATASET_READS`, and it prices storage over
      `…_STORAGE_MAX_LIFETIME`, not over the cleanup deadline;
    - `test_unset_storage_lifetime_denies_live_admission`
      (`unbounded_component`);
    - discovery is denied above `A − …_WATCH_REFRESH_RESERVE_USD` while
      watch_refresh is still admitted up to `A` (revision 5, MS2-D-17);
    - outstanding reservations are counted;
    - the kill switch;
    - `test_tripped_latch_denies_everything`;
    - invalid inputs.
- **E3 — Ledger service.** Implement the cycle snapshot (MS2-D-40) and
  `reserve()` under the budget advisory lock, with the revision-5 MS2-D-34
  predicate: a row counts in every billing cycle its charge interval touches,
  and unreconciled rows count at full estimate in every cycle from their
  admission cycle onward.
  - Tests (`tests/db/test_apify_ledger.py`):
    - `test_concurrent_reservations_one_admitted_at_boundary` (two threads);
    - `test_reservation_straddling_cycle_boundary_counts_in_both_cycles`
      (replaces `test_run_spanning_window_boundary_is_counted`);
    - `test_arbitrary_non_calendar_cycle_boundary` (a 5th→4th cycle, and a
      cycle shortened by a plan change);
    - `test_cycle_rollover_carries_unsettled_reservations`;
    - `test_reconciled_spend_leaves_a_cycle_its_charge_interval_does_not_touch`
      (replaces revision 4's `test_reconciled_spend_rolls_off_31_days_after_last_charge`);
    - `test_late_cleanup_settled_spend_counts_in_the_cycle_of_its_final_charge`
      (MS2-D-34, F-08 residual, revision-5 form; replaces revision 4's
      `test_late_cleanup_settled_spend_counts_until_31_days_after_final_charge`).
      A run is reserved two days before a cycle ends and misses its cleanup
      deadline. Deletion succeeds three days into the next cycle, and the row
      is reconciled with `last_charge_at` there. The settled amount counts in
      both cycles, and a new reservation in the second cycle that fits only
      without it is denied;
    - `test_cycle_attribution_uses_charge_interval_not_observation_time`
      (MS2-D-39);
    - `test_unreconciled_reservation_never_ages_out`;
    - `test_stuck_reservation_still_counted`.
- **E4 — Reconcile and the overrun latch.** Settle under the same lock per
  MS2-D-32 and MS2-D-41 (revision 5). The first non-null `usage_total_usd` makes
  the row `usage_provisional`; the first read at least the settle delay after
  `finishedAt` makes it `usage_finalized`, written once. `reconciled` requires a
  terminal import, verified deletion, finalized usage, and the post-run cost
  (`bound` until F5a's measurement, MS2-D-41). Reconciliation sets
  `last_charge_at` in the same locked transaction (MS2-D-34). The reconcile unit
  joins the MS2-D-23 outstanding selector. A null or unsettled value keeps the
  estimate and is retried. A row unsettled at its admission cycle's end is
  flagged `unreconciled_stale`.
  - Latch trips: an actual above the reservation, non-zero proxy usage, a dataset
    count over `max_items`, a KV store over its byte cap, a start-option
    mismatch (the start job aborts that run), an exhausted delete-attempt cap,
    or an `orphaned_start` (MS2-D-33).
  - Tests:
    - `test_overrun_latch_denies_admission_until_reset`;
    - `test_estimator_version_bump_clears_latch`;
    - `test_proxy_usage_trips_latch`;
    - `test_dataset_over_cap_trips_latch`;
    - `test_start_option_mismatch_aborts_and_trips_latch`;
    - `test_reconcile_concurrent_with_admission_serializes`: the reconcile
      thread and the reserve thread interleave under the lock, and the admission
      decision sees either the pre-reconcile or the post-reconcile total, never a
      torn one;
    - `test_late_usage_reconciled_on_later_tick`;
    - revision 3 (MS2-D-32, F-08 residual):
      - `test_repeated_pre_commit_reads_are_capped_and_reserved`: stage 1 is
        rolled back repeatedly. Each retry increments `dataset_read_count`
        before reading, and the reservation already covered
        `…_MAX_DATASET_READS` reads. The read after the cap is refused, and
        the import is rejected with `read_cap_exhausted`.
      - `test_delayed_deletion_keeps_storage_liability_outstanding`: cleanup
        fails past the deadline, and the reservation stays counted at its full
        estimate in admission beyond its admission cycle. Once deletion is
        verified and usage re-read, the reservation is `reconciled`.
      - `test_non_null_usage_before_cleanup_completes_does_not_release_liability`:
        usage arrives while the import is at `observations_committed` and the
        storage is `retained`. The status is `usage_provisional` or
        `usage_finalized` (revision 5 names), and admission still counts the
        full estimate.
      - `test_usage_read_before_final_charge_op_does_not_reconcile`;
      - `test_delete_cap_exhausted_trips_latch`;
      - `test_orphaned_start_trips_latch`.
    - revision 5 (MS2-D-41; owner §29):
      - `test_first_usage_read_is_provisional_until_settle_delay`;
      - `test_finalized_usage_written_once_not_recomputed` (a later read at a
        different price leaves the finalized figure unchanged);
      - `test_unsettled_usage_keeps_full_reservation_and_retries`;
      - `test_post_run_cost_uses_bound_until_measured` and
        `test_post_run_cost_counted_mode_uses_operation_counters`;
      - `test_reconcile_below_reservation_returns_capacity`;
      - `test_reconcile_above_reservation_trips_overrun_and_pauses_paid_work`;
      - `test_overrun_never_admits_repair_run_or_dataset_reread`;
      - `test_process_stop_alone_never_reconciles`.
- **E5 — Wire admission.** Replace `DenyAllAdmission` with the ledger in the
  start job, and derive `budget_paused` into C's freshness.
  - Tests: `test_denied_start_records_denial_and_starts_nothing`;
    `test_denied_start_shows_budget_paused_with_reason_in_shortlist`; a later
    successful import clears it.
- **E6 — Attribution report (AC-7).** Revision 5: `apify_spend_report` prints,
  per billing cycle (the authoritative view): the cycle bounds, the project
  allocation, consumed finalized usage, outstanding reservations, remaining
  project budget, and the observed account usage, prepaid credit, and remaining
  prepaid allowance; then per-source and per-provider totals from the ledger and
  `provider_run`; unsettled and overrun rows with their reasons; and a
  trailing-31-day trend line labeled secondary (MS2-D-17). The calendar-month
  view of revision 4 is withdrawn.
  - Test: output for a seeded ledger spanning two cycles.
- **E8 — Budget-admitted probe (MS2-D-24 with the real ledger).**
  `test_budget_admitted_actor_probe_recovers_source_with_ledger`: a probe is
  admitted under the `discovery` class, reserved, imported, and reconciled, and
  the source recovers. `test_probe_denied_when_discovery_exhausted`.
- **E7 — Close-out.** This runs last. Gate; TODO/STATUS. Record the owner
  tasks (revision 5): keep the account-level usage limit at or below the
  prepaid credit; provision the Hardware Radar runtime token and its OpenBao
  path (R25); decide the MCP tool filter (R24). OQ23 is resolved; no deduction
  setting remains.

**Acceptance:** AC-7 holds. Admission fails closed on the kill switch, at the
class cap, at the account prepaid headroom, on a tripped overrun latch, on
missing prices, on an unknown cycle or unobservable account state, on a plan
above the cash ceiling, on any component without an enforceable bound, and on
missing settings. Reconciliation and admission are serialized. No reservation
ages out unreconciled, and none is released while charge-producing work remains
or before its usage is finalized and its post-run cost accounted. Settled spend
counts in every billing cycle its charge interval touches, however late cleanup
succeeded (revision 5).

## Slice F — Pilot sources, measurement, end-to-end proof

**Scope:** MS2-D-19; MS-2 Tasks 6–7; AC-3 live half.

**Files:**
- `acquisition/sources/ebay.py` (category sweeps);
- possibly `serverpartdeals.py`;
- new command `pilot_report`;
- `docs/STATUS.md` evidence.

- **F1 — eBay category sweeps.**
  - **Facts** (developer.ebay.com Browse `item_summary/search` and Taxonomy
    docs; the eBay prep report retrieved these 2026-09-24, some via verbatim
    search-engine snippets because the pages blocked direct fetch):
    - US leaf categories (tree `0` for `EBAY_US`): GPU / Graphics & Video
      Cards **27386**, RAM **170083**, CPU **164**.
    - eBay has **no dedicated datacenter-accelerator category**. Datacenter
      accelerators and consumer cards share 27386, so disambiguation falls to
      GPU extraction (R8).
    - `category_ids` accepts **one** ID per request, so each category is its
      own sweep.
    - `limit` is at most 200. `offset` is 0–9,999 and a multiple of `limit`. A
      result set is capped at **10,000 items**, so `complete=True` is
      achievable only when `total ≤ 10,000`.
    - Today `fetch()` issues exactly one GET (`ebay.py:192-208`) and has no
      pagination loop. `_sweep_is_complete` (`:268-291`) needs no `next` href
      and `total ≤ seen`, so a category whose `total` exceeds 200 can never be
      proven complete without a loop.
    - The Browse daily quota is **unconfirmed**. Several secondary sources say
      5,000/day, but the official limits table only footnotes Buy APIs. Even
      four sweeps at a 5-minute cadence (about 1,152 calls/day) fit within that
      figure. Confirm it with `getRateLimits` before setting cadence.
    - Category IDs change periodically, with quarterly category-change notices.
  - **Sweeps.** Configure per-category sweeps: GPU 27386, RAM 170083, CPU 164,
    one `category_ids` per request. Re-verify the IDs via the Taxonomy API
    (`getCategorySuggestions` / category subtree) at implementation time, record
    the date, and add a periodic re-verification TODO. Each sweep has a
    `category_hint` and `collection_scope="ebay:<slug>:<query_id>"`, which is the
    MS2-D-12 key format. The legacy drive keyword sweep keeps scope None and hint
    None, so no data backfill is needed.
  - **Pagination.** Add a pagination loop, `limit=200` with `offset` cycling,
    until there is no `next` href or a page cap below the 10,000-item ceiling.
    Hitting the page cap makes the sweep incomplete: it is never
    `complete=True`.
  - **Multi-scope delist.** The adapter returns one scope per sweep via a new
    optional multi-scope capability that the pipeline applies per scope. Each
    per-scope absence uses MS2-D-12's `collection_scope` filter, so a complete
    GPU sweep can never delist RAM, CPU, or drive listings. Continuity is
    recorded or broken per swept scope (MS2-D-31). The legacy drive sweep
    stays in every eBay run, so the NULL-scope lane keeps today's continuity.
  - **Retention.** Retention and delete-on-delist are unchanged (class
    `ebay_listing_observation`, ≤6 h, delete-on-delist). The API License
    Agreement's obligations attach to the Browse mechanism, not to a category.
    The 2026-09-24 check found nothing category-specific.
  - **Tests** (cassettes):
    - per-sweep completeness;
    - `test_pagination_reaches_complete_when_total_within_cap`;
    - `test_page_cap_hit_is_incomplete`;
    - every non-drive item carries a hint;
    - a GPU complete sweep cannot delist drive listings (scoped absence);
    - the frozen drive tests stay green.
- **F2 — ServerPartDeals breadth check.** If non-drive collections exist, add
  hinted, scoped collection sweeps. Otherwise record "drive-only" as a finding.
- **F3 — Measurement.** `pilot_report` summarizes, per source and provider:
  runs, completeness distribution, identifier (MPN) coverage, condition and
  shipping presence, freshness lag, failures, and cost (Task 6).
- **F4 — Category corpora (prep for owner gate R4).** Harvest GPU/RAM/CPU samples
  with `harvest_corpus`. Its hint round trip landed in B6 (MS2-D-27), so the
  harvested entries replay through their own category rules. F4 changes no
  tooling. Labeling and ratification are owner-in-the-loop, as in MS-1e.
- **F5 — Actor proof.** Revision 5 (owner-clarified (s2, 2026-09-24); OQ24
  split) replaces revision 4's single owner-gated merchant proof with two tasks.
  Revision 4's step "open the Actor PR in the separate Actor repository" is
  withdrawn (owner-overridden, MS2-D-38).
- **F5a — Synthetic proof through a real private Actor (MS2-D-42, -43).** Gated
  on: E merged; the Hardware Radar runtime token provisioned (R25); verified
  unit prices; `…_STORAGE_MAX_LIFETIME` set; the latch clear; and an explicit
  owner or orchestrator instruction to deploy. No merchant legal decision is
  needed.
  1. Create the synthetic site with its idempotent setup command, in a
     non-production environment (MS2-D-42).
  2. Deploy `hw-radar-synthetic-collector` by the MS2-D-43 procedure, verify the
     push uploaded only the Actor directory, and record the deployment.
  3. Run through hw-radar's own path:
     `APScheduler/provider admission → budget reservation → Actor start →
     remote execution → bounded output → completion polling → completeness
     report → dataset/output import → idempotency → event-time handling →
     listing identity handling → finalized usage reconciliation`.
     Cover one complete run, one run per truncation reason, one
     `contradictory_report` run, one duplicate import, one delayed older
     import after a newer run, and one local ↔ Actor switch on the synthetic
     site (AC-4 and AC-5 live).
  4. Run the measurement protocol (MS2-D-43): finalization deltas, the post-run
     account usage diff, storage accrual after deletion, and the `date`
     parameter cycle read. Record the results; the owner decides whether
     `…_POST_RUN_COST_MODE` may become `counted`.
  5. Record every §21 item's live evidence (see *Synthetic Actor proof
     acceptance*) in STATUS.
- **F5b — Production Actor-backed merchant source (owner-gated: OQ24, R1).**
  After the owner answers OQ24 for a candidate with an `eligible` source-admission
  record (MS2-D-44):
  1. Build `actors/hw-radar-<source>/` in this repository on the same contract.
  2. Set that source to `collection_provider=apify`.
  3. Run one bounded live run and one deliberately truncated run. Live admission
     requires the E preconditions and a registered retention that is not bounded
     (MS2-D-33).
  4. Switch local ↔ Actor where a local path exists.
  5. Record cost and completeness.
- **F6 — End-to-end exit (owner-gated: R5).** With pilot sources enabled by the
  owner, create one real watch. `show_shortlist` produces a qualifying shortlist
  from real observations with no score (AC-3). Record the evidence in STATUS.

**MS-2 exit:** AC-1..AC-8 evidenced. The live halves of AC-4 and AC-5 are
recorded by F5a on the synthetic source (revision 5). The live half of AC-3 is
recorded after the owner gates clear (F6). MS-2 Task 6's self-owned-Apify pilot
source is F5b, which stays owner-gated by OQ24; whether MS-2 may exit before
F5b lands is an owner decision (R31).

## Open risks and owner gates

| ID | Risk / gate | Owner action | Blocks |
| --- | --- | --- | --- |
| R1 | **Actor-proof source is an owner (legal) decision.** Newegg's Terms of Use (kb.newegg.com policy-agreement page, retrieved 2026-09-24) prohibit access "through any automated means, including ... scripts or web crawlers" and to "'Scrape' ... the Site for any purpose". No non-commercial carve-out was observed. Its robots.txt (retrieved 2026-09-24) fully blocks the `ChangeDetection` price-watch user agent, though it does not disallow product or search paths generally. No sanctioned data feed exists: the affiliate program (Rakuten) is link-based, and the Marketplace API is seller-only. Newegg is therefore **excluded** unless the owner decides otherwise. Whether that KB page is the footer-linked canonical ToU is unconfirmed. B&H, ServerPartDeals, and refurbished server-parts sellers are candidates only after a ToS/robots review. Apify execution does not change permissibility. Revision 5 (owner-clarified (s2, 2026-09-24)): this is now OQ24 part (b) only; each candidate needs a source-admission record (MS2-D-44), and the first Actor proof uses the synthetic source instead (MS2-D-42). | Answer OQ24 for a candidate after its admission record | F5b only |
| R2 | **Withdrawn in revision 5 (owner-overridden, MS2-D-38).** Revision 1–4 said the separate Actor repository's product-admission gate and branch rules applied to an internal Actor. Hardware Radar Actors are now built, versioned, tested, and deployed in this repository, under its own review and gate, with no dependency on that repository's process. | — | none |
| R3 | Account-level Apify configuration. **OQ23 is resolved** (revision 5, owner-overridden; `resolved-questions.md#oq23`): the ~$20 ceiling is the account's total cash outlay, the subscription fee counts, prepaid usage is not charged twice, and Hardware Radar uses at most the lesser of its $12 target and the remaining prepaid allowance (MS2-D-40). No deduction setting remains. Open owner actions: keep the account usage limit at or below the prepaid credit (verified equal, $19, 2026-09-24) and never raise it for Hardware Radar; provision the runtime token (R25). | Keep the limit; provision the token | E live admission; F5a |
| R4 | GPU/RAM/CPU auto-accept needs an owner-ratified category corpus. The drive corpus does not validate other categories. | Label/ratify F4 corpora | Flipping `auto_accept` |
| R5 | MS-1e drive-matcher ratification is still pending, and all sources ship disabled. The real-observation exit proof (AC-3 live) needs the owner to enable pilot sources. | Ratify; enable | F6 |
| R6 | GPU/RAM/CPU reference seeds come from first-party pages. The ToS/licence of each manufacturer spec page should be spot-checked. Curated manual rows are the fallback. | Spot-check | B4 authoritative flag |
| R7 | Apify `usage_total_usd` is nullable, preliminary right after completion (Apify: re-read after about 10 s), and recomputed at current pricing when read later. Post-run dataset reads are account usage, not part of the run figure (revision 5 facts, MS2-D-15). MS2-D-41 therefore finalizes usage by a settle rule, writes the finalized figure once, and adds the post-run cost at its full bound until F5a measures it. Residuals: storage and transfer bounds rely on caps in reviewed Actor code (hw-radar detects a violation only after the fact, via the dataset count and the `usage` breakdown); the transfer direction semantics must be verified in E2; and the 10 s settle delay is unmeasured on this account. | Review the Actor PR's caps; review F5a measurements | E accuracy; live admission |
| R8 | eBay category IDs can change (quarterly category-change notices), so they are re-verified via the Taxonomy API. eBay does not separate datacenter accelerators from consumer GPUs (both are in 27386), so disambiguation falls to extraction. The Browse quota (5,000/day) is corroborated by secondary sources only; the official table footnotes Buy APIs. Per-category `total` above 10,000 can never be proven complete. | Confirm the quota via `getRateLimits` | F1 |
| R9 | The deferred MS-2a plan names migrations 0018–0026, which collide with this plan. Its own header requires a rebase at activation (D2). | Rebase at reactivation | MS-2a only |
| R10 | ADR 0022 confirmation #3 and STATUS say "shortlist + exactly-one alert". Master spec §19 puts the alert in MS-4 (applied, D1). §10.1/§11 still show score-first wording. This is a documentation conflict and was not edited here. | Reconcile docs | none |
| R11 | Soft-threshold semantics (target price annotates, never decides) are an inference from ADR 0022, not a stated rule. | Confirm or override MS2-D-07 | C |
| R12 | Watch/requirement persistence (MS2-D-07) is a plan-level decision; the master spec allows "milestone implementations", so no ADR is strictly required. Revision 5 (owner-overridden): the budget period is no longer plan discretion. The owner ruled the Apify billing cycle authoritative, and ADR 0021's 2026-09-24 amendment records it (MS2-D-17, MS2-D-40). | Decide ADR vs plan for MS2-D-07 only | none |
| R13 | Slice A intentionally adds provenance keys to new resolution edges (`category`, `category_source`) and `detail_json["provider"]`. A run whose `delist_scope()` raises no longer advances continuity. An ineligible remote run breaks continuity (MS2-D-11). Remote `complete` evidence with an incomplete or missing scope cannot delist and breaks continuity (revision 3). All of these changes are additive or strictly more conservative. | — | none |
| R14 | Legacy default `category_hint=None ⇒ drive` is a trap for any future multi-category source that forgets to hint. F1's test enforces hints for eBay category sweeps. Every new multi-category collector must copy that test. | — | F1 and later sources |
| R15 | Slice A residual, reported by the coordinator 2026-09-24. A listing whose current edge is `none` or `review` keeps a stale `evidence["category"]` when a later snapshot changes the hint, because no-spam writes no edge when the outcome is unchanged. B3 closes this by treating a category change as a decision-input change. | — | B3 |
| R16 | Evaluations become `pending` whenever new evidence arrives and evaluation fails (MS2-D-20). No scheduled backlog job exists in MS-2, so a persistently failing evaluator leaves rows pending until the next observation or `evaluate_watches --pending`. | Watch F3 pending counts | none |
| R17 | The overrun latch pauses **all** paid Apify admission until the owner resets it or the estimator version is bumped. That is deliberately blunt: one bad estimate can stop Actor-backed freshness (`budget_paused`) until owner action. | Reset after review | E live operation |
| R18 | MS2-D-29 recomputes catalog fingerprints at read time, one batched spec read per shortlist call. It has not been measured at pilot scale. | Watch F3 shortlist latency | none (reopen path in MS2-D-29) |
| R19 | MS2-D-32 pre-reserves storage over the platform's default-storage expiry, and it counts unverified post-run components as spent. Estimates are therefore deliberately high, and admission is tighter than actual spend. The operation-cap defaults (3 reads, 10 delete attempts) are assumptions. Whether `GET` run polls and the account reads are billable is unverified (E2). Revision 5: the post-run cost also counts at its full bound until F5a measures it (MS2-D-41). | Set `…_STORAGE_MAX_LIFETIME` for the chosen plan; confirm in E2 | E live admission |
| R20 | MS2-D-33 denies an Actor path to every bounded-retention source, because no enforceable remote expiry within hours is confirmed. The synthetic proof source is registered indefinite (MS2-D-42). A production Actor-backed merchant source (F5b) must therefore be a merchant-fact source, or D3 must verify a per-run storage expiry. | Consider it in each source-admission record | F5b if a bounded source is chosen |
| R21 | A start whose response is lost leaves a remote run hw-radar cannot identify (`orphaned_start`). MS2-D-33 detects it at the deadline and trips the latch. Automatic discovery would need an extra list-runs call outside MS2-D-15's seven, which is not planned. | Clean up manually on report; reset the latch | E live operation |
| R22 | MS2-D-31's per-scope tolerance uses the FULL lane interval. If Actor runs rotate scopes more slowly than that, per-scope continuity keeps restarting. That fails closed (stale absence does not fire), but it can hide real absence until a complete sweep. | Revisit if per-scope cadence becomes configurable | none |
| R23 | MS2-D-35 residual. Stale-absence sweeps raise no scope watermark, and `last_seen` is `auto_now`, so an import stamps it at persistence time rather than at `observed_at`. A delayed current-eligible import therefore makes a listing look fresher to stale absence by up to the import delay, which the storage deadline bounds (default 24 h). A previously unknown key that a stale sweep would have treated as absent stays active for about one grace longer. This fails toward keeping a listing active, never toward a false delist. Preserved property (revision 5, verbatim): "a delayed import may delay a correct delist, never cause a false delist". Any improvement needs out-of-order tests (MS2-D-39). The fix, stamping `last_seen` from `observed_at` on the import path, touches the `auto_now` contract that `redact_merchant_content` documents. | Decide at the *Slice D entry gate* | none (D entry gate decision) |
| R24 | The project `.mcp.json` loads only the four anonymous Apify MCP tools, so MCP cannot inspect runs, logs, datasets, or KV records. Widening it needs OAuth (account-wide) or a bearer token kept out of this public repository, and the recommended read-only value is in MS2-D-43. `call-actor` is spend-capable and stays excluded; MCP dataset reads are billed account usage (operator allowance). | Decide whether to widen the filter and how to authenticate | Operator inspection only; not the runtime |
| R25 | Hardware Radar has no Apify token of its own yet; only the apify-actors venture's agent token exists, and Hardware Radar must not use it. Whether a limited-permission token can read `/users/me/limits` and `/users/me/usage/monthly` is unverified (MS2-D-15). | Provision a Hardware Radar runtime token at the proposed `secret/apps/hw-radar/apify` path, rendered as `HW_RADAR_APIFY_TOKEN`, and a separate operator deploy credential | D3 live verification; E live admission; F5a |
| R26 | Usage finalization is unverified on this account: Apify documents a preliminary first figure and advises a re-read after about 10 s, but the actual settle time is unmeasured. | Review F5a's finalization measurements | E accuracy |
| R27 | Post-run consumption (dataset reads, storage, transfer) is unmeasured, so the post-run cost counts at its full bound, which makes admission tighter than actual spend. The account is shared: other workloads (the apify-actors venture) reduce the prepaid allowance Hardware Radar may use, and two hw-radar environments enabled in one cycle could each spend the full target unless the second is lowered (MS2-D-42). | Approve `counted` mode after F5a; keep one enabled environment per cycle | E live admission |
| R28 | The residential-proxy feature is available on the account (verified 2026-09-24), so nothing at the account level stops an Actor from using it. Code tests, the input schema, and the proxy usage latch are the controls (MS2-D-26, MS2-D-38, MS2-D-44). | — | none |
| R29 | Monorepo `apify push` scoping is unconfirmed: whether it uploads only `actors/<name>/` when run there. D-prep verifies it through the version's `sourceFiles`; the fallback is a Git-source Actor with no push webhook (MS2-D-43). | — | F5a deployment |
| R30 | The platform limit may deviate by up to about 10% at enforcement (Apify help center). The ~$20 cash ceiling therefore rests on Hardware Radar's account margin (MS2-D-40), not on the account limit. Whether the platform aborts a running run at the limit is unverified, and the enforcement experiment must not run on the shared account without owner sign-off. | Sign off before any enforcement experiment | none |
| R31 | MS-2 Task 6 names a self-owned-Apify pilot source, but F5b waits on OQ24. Whether MS-2 may exit on the synthetic proof (F5a) while F5b is pending is not decided. | Decide the MS-2 exit condition for Task 6 | MS-2 exit only |
| R32 | (Implementation-driven, Slice B.) No retention class exists for non-first-party reference rows, so the importer refuses non-first-party seed documents and B4c seeds first-party rows only; RAM coverage is two rows. Adding a class rewrites every `*_retention_ttl_coherent` CHECK, as migration `0017` did (MS2-D-06). | Decide the retention class for non-first-party reference data | B4c RAM expansion; any non-first-party seed |

No new ADR or OQ file is created by this plan. Revision 5: OQ23 is resolved and
OQ24 split by the owner's 2026-09-24 decisions, recorded in
`resolved-questions.md`, `open-questions.md`, and ADR 0021's amendment (not by
this plan). R12 still offers an optional ADR for MS2-D-07. New owner decisions
this plan needs are R24, R25, R31, and R32.

## Review lineage

**Round 1: cross-agent delegate `42deeff0`** (opposite provider, static
read-only review of revision 1 at `df114de`).
- **Verdict:** REVISION NEEDED, with 12 findings (F-01..F-12).
- **Disposition:** all 12 ACCEPTED and resolved in revision 2. The rows below
  describe revision 2. Where round 2 found a residual (F-03, F-04, F-07, F-08,
  F-10), the round-2 table supersedes the row, and where round 3 found one
  (F-08), the round-3 table supersedes both. Each table records the revision
  that answered it; the decision text above is the current contract.
- The review's "verified correct" items stand unchanged: the migration head, the
  spine and satellite pattern, the listing and snapshot keys, the three veto
  sites, site-wide absence, the gate contents, and the milestone boundary.
- Slice A text also reflects what landed for A0–A3 and the Slice A verifier's
  gate tightening.

| Finding | Sev. | Disposition | Plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| F-01 veto result locals collide with the injected callable | medium | Accepted. The locals are renamed `vetoed` (as landed); the evidence is byte-identical; basedpyright is added to A1 | MS2-D-02 *Ladder veto*; A1 step 2 and step 5 | `test_ladder_veto.py::test_custom_veto_is_consulted_at_rung1`, `::test_custom_veto_is_consulted_at_rung0_prior`; A0 `test_ladder_golden_baseline`; `basedpyright` 0 errors |
| F-02 map misses direct callers; F4 loses the category hint | high | Accepted. Map Q3 is corrected with grep-verified callers. A backward-compatible corpus schema, harvest, and replay task lands in B (MS2-D-27 says why B, not F) | MS2-D-10 *Callers outside the seam*; MS2-D-27; B6; F4; map Q3 | `test_corpus_category_hint.py::test_harvested_non_drive_entry_reaches_category_rules`, `::test_unhinted_staging_entry_bytes_unchanged`, `::test_existing_drive_corpus_loads_unchanged` |
| F-03 stale eligibility stays a shortlist match | high | Accepted. An evaluation is bound to its snapshot, resolution, requirement, and evaluator version. A non-current row is `pending` and never qualifies. The production evaluator is the `run_collection` default, so poll, heartbeat, probe, and import paths all evaluate | MS2-D-09; MS2-D-20; C1, C3, C4; MS2-D-22 stage 4 | `test_evaluation_binding.py::test_prior_match_then_contradictory_evidence_updates_verdict`, `::test_evaluator_failure_after_prior_match_leaves_shortlist`, `::test_heartbeat_fired_run_updates_watch_evaluation`, `::test_recovery_probe_run_evaluates`; `test_shortlist.py::test_prior_match_then_contradictory_evidence_leaves_shortlist` |
| F-04 skipped continuity lets truncated runs bridge a gap | high | Accepted (orchestrator decision). An ineligible successful FULL run **breaks** continuity via `_break_sweep_continuity`; eligible runs are unchanged; rejected remote runs also break it | MS2-D-11 *Continuity*; A5 step 2; A6; Slice A acceptance; R13 | `test_collection_provider.py::test_ineligible_run_breaks_existing_continuity`, `::test_local_after_prolonged_truncated_remote_cannot_mass_delist`; `test_apify_import.py::test_ambiguous_empty_run_cannot_delist` |
| F-05 import commit point precedes required work | high | Accepted. A durable stage machine runs pending → observations_committed → absence_applied → resolved → evaluated → finalized, with compare-and-set transitions, `ScraperRun` reuse, and the terminal marker only after every effect | MS2-D-13; MS2-D-22; D10 | `test_apify_import.py::test_crash_after_observation_commit_resumes_without_duplicates`, `::test_crash_during_resolution_resumes_and_evaluates`, `::test_crash_during_evaluation_resumes_and_evaluates`, `::test_crash_before_finalization_finalizes_once`, `::test_retry_reuses_scraper_run` |
| F-06 poller selection excludes the work it promises to retry | high | Accepted. Remote status is separate from local work, and two selectors run: active, and outstanding (import, cleanup, reconcile, late usage) | MS2-D-16; MS2-D-23; D5; D10; E4 | `test_apify_poll_job.py::test_outstanding_selector_picks_terminal_rows_with_unfinished_import`, `::test_restart_recovers_each_unfinished_import_state`, `::test_restart_retries_storage_cleanup_after_finalized_import`; `test_apify_ledger.py::test_late_usage_reconciled_on_later_tick` |
| F-07 recovery probes ignore the selected provider | high | Accepted. Probes dispatch by provider. A remote probe gets budget admission (discovery class), a bounded start, async completion, retention, and PROBE semantics, and it never authorizes absence | MS2-D-16; MS2-D-24; D12; E8 | `test_apify_recovery_probe.py::test_local_success_cannot_clear_actor_provider_failure`, `::test_budget_admitted_actor_probe_recovers_source`, `::test_actor_probe_never_delists_or_touches_continuity`; `::test_budget_admitted_actor_probe_recovers_source_with_ledger` |
| F-08 estimate called a hard bound without bounding every cost | high | Accepted. Every charge component is bounded by an enforced option or disallowed (no proxies). There is a safety margin below $20, the OQ23 deduction gates live admission, and an overrun latch applies. Reconcile and admission share one lock. Attribution is pinned at `reserved_at`, the window is 31 days + max timeout, and unreconciled rows never age out | MS2-D-17; MS2-D-26; E1–E4; R3; R7; R17 | `test_apify_budget.py::test_exact_boundary_admitted_and_epsilon_over_denied`, `::test_unset_cap_deduction_denies_live_admission`; `test_apify_ledger.py::test_overrun_latch_denies_admission_until_reset`, `::test_reconcile_concurrent_with_admission_serializes`, `::test_run_spanning_window_boundary_is_counted`, `::test_concurrent_reservations_one_admitted_at_boundary`, `::test_unreconciled_reservation_never_ages_out`, `::test_proxy_usage_trips_latch` |
| F-09 no authoritative-alias gate for new categories | high | Accepted. For new categories a category `AcceptancePolicy` allows only `catalog_authoritative` hits at model or variant grain. Manual and listing-derived aliases stay reviewable; drive is untouched | MS2-D-05; MS2-D-21; B3 | `test_resolver_categories.py::test_non_authoritative_alias_never_auto_accepts_new_category`, `::test_alias_learned_from_earlier_observation_goes_to_review`, `::test_family_grain_fanout_is_review_for_new_categories`, `::test_prior_from_non_authoritative_edge_is_review`, `::test_drive_acceptance_unchanged` |
| F-10 remote retention lacks an execution and cleanup path | high | Accepted. A code registry of source retention is forwarded explicitly on every remote import, and unknown retention is rejected. Deadline-based cleanup of the dataset and KV store covers every run outcome, with retries | MS2-D-13; MS2-D-14; MS2-D-25; D4; D11 | `test_apify_retention.py::test_bounded_source_import_retains_listings_snapshots_raw_and_evaluations`, `::test_unregistered_source_import_rejected_not_merchant_fact`; `test_apify_storage_cleanup.py::test_cleanup_after_rejected_import`, `::test_cleanup_of_abandoned_run_at_deadline`, `::test_cleanup_failure_retries_with_backoff` |
| F-11 complete-empty enumeration classified as failure | medium | Accepted. Proven complete-empty (all counts zero, complete evidence) is `complete`; non-empty unusable output and ambiguous empty output are `failed`. OUTPUT is not a `RawItem`, so the parser-rot guard holds | MS2-D-14; D1; D4; D8 | `test_apify_contract.py::test_complete_empty_requires_all_zero_counts_and_pages_fetched`, `::test_empty_without_evidence_is_failed_ambiguous`; `test_apify_import.py::test_complete_empty_run_delists_scope_end_to_end`, `::test_ambiguous_empty_run_cannot_delist`, `::test_nonempty_unusable_run_is_rejected_and_cannot_delist` |
| F-12 invalid D-before-B merge alternative | medium | Accepted. The alternative is removed. D is split into D-prep (D1 and D3, depending on A only) and D (core, depending on C and D-prep). Merge order follows the dependency graph | *Slice order and migration assignment*; Slice D *PRs* | Dependency table; no migration in D-prep (`makemigrations --check` in D-prep's gate) |

**Also folded in revision 2** (not review findings):
- The Slice A verifier's `gate_delist_scope` tightening (MS2-D-11, A4, A6).
- The landed A0–A3 details (MS2-D-02, A0–A3).
- The MS2-D-28 soft-block hazard, found while re-reading `_classify_batch`.
- The R15 Slice A residual, closed in B3.
- The prep evidence: eBay categories (F1, R8), Apify API facts (MS2-D-15),
  Newegg terms (R1, MS2-D-19), and Slice B reference-data sources and importer
  gaps (MS2-D-06, B4a–B4c).

**Round 2: cross-agent delegate `537cd698`** (opposite provider, static
read-only review of revision 2 at `e82a83a` plus the Slice A code).
- **Verdict:** REVISION NEEDED. Seven round-1 findings were RESOLVED (F-01,
  F-02, F-05, F-06, F-09, F-11, F-12), five were PARTIAL (F-03, F-04, F-07,
  F-08, F-10), and three findings were new (N-01, N-02, N-03).
- **Disposition:** all eight open items ACCEPTED and resolved in revision 3.
  F-04 is resolved by an orchestrator decision implemented in Slice A code.
  The rest are plan changes for Slices B–E.
- The review's "verified correct" items stand: callable veto sites, category
  dispatch, the scope gate, the continuity break for non-complete evidence,
  and migration ordering from `0018`.
- Round 3 found residuals in F-08, N-01, and N-02. For those three, the
  round-3 table supersedes the rows below.

| Finding | Sev. | Status | Decision / plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| F-03 residual: binding omits mutable catalog inputs | high | Accepted; resolved | MS2-D-29 (new; read-time catalog fingerprint; atomic invalidation rejected); MS2-D-09; MS2-D-20 *Validity* (five conditions); C1, C3, C4; Slice C acceptance | `test_evaluation_binding.py::test_same_target_catalog_correction_excludes_match_from_shortlist_immediately`, `::test_catalog_correction_by_queryset_update_is_detected`, `::test_family_membership_change_makes_family_grain_evaluation_pending`, `::test_unrelated_catalog_edit_keeps_evaluation_current` |
| F-04 residual: complete evidence + incomplete scope still counted toward continuity | high | Accepted (orchestrator decision); resolved in Slice A code | MS2-D-11 *Continuity* (`counts_toward_sweep_continuity(evidence, scope)`: local unchanged; non-local needs complete evidence and a complete scope); A4, A5, A6; *Revision 3 correction*; Slice A acceptance; R13 | `test_provider_evidence.py::test_remote_complete_evidence_with_incomplete_or_missing_scope_does_not_count`, `::test_local_continuity_mapping_unchanged`; `test_collection_provider.py::test_remote_complete_evidence_incomplete_scope_breaks_continuity_then_local_cannot_stale_delist` |
| F-07 residual: Reject broke continuity for probes | medium | Accepted; resolved | MS2-D-22 *Reject* (FULL only; PROBE → `PROBE_FAILURE`); MS2-D-24 *Probe outcome*; MS2-D-11; D12 | `test_apify_recovery_probe.py::test_rejected_probe_records_probe_failure_and_leaves_continuity_unchanged` [invalid OUTPUT, ambiguous-empty, storage deadline] |
| F-08 residual: reservation not an upper bound on authorized work | high | Accepted; resolved | MS2-D-32 (new; operation caps, execution/post-run split, settlement after the final charge-producing operation, `unbounded_component`); MS2-D-17 *Reconciliation*; MS2-D-26 table, *Reservation*, *Window*, *Late usage*; MS2-D-13 counters; MS2-D-22 stage 1; E settings, E1–E4; R7, R19 | `test_apify_ledger.py::test_repeated_pre_commit_reads_are_capped_and_reserved`, `::test_delayed_deletion_keeps_storage_liability_outstanding`, `::test_non_null_usage_before_cleanup_completes_does_not_release_liability`, `::test_usage_read_before_final_charge_op_does_not_reconcile`, `::test_delete_cap_exhausted_trips_latch`; `test_apify_budget.py::test_estimate_includes_capped_reads_deletes_and_storage_lifetime`, `::test_unset_storage_lifetime_denies_live_admission`; `test_apify_import.py::test_stage1_rollback_retry_counts_each_dataset_read`, `::test_read_cap_exhausted_rejects_import_and_cleans_up` |
| F-10 residual: cleanup clock started at observed termination | high | Accepted; resolved | MS2-D-33 (new; absolute deadline at admission, bounded-source Actor denial, overdue selector, expired-content rejection); MS2-D-25 *Cleanup deadline* and *Cleanup*; MS2-D-23 selector 3; MS2-D-13; MS2-D-15 abort; MS2-D-16; D5, D11; F5; R20, R21 | `test_apify_storage_cleanup.py::test_restart_after_source_ttl_rejects_expired_content_before_persistence`, `::test_unobserved_run_past_deadline_is_aborted_and_cleaned`, `::test_deadline_is_anchored_at_admission_not_terminal_observation`, `::test_timeout_exceeding_retention_window_denied`, `::test_bounded_source_actor_start_denied`; `test_apify_poll_job.py::test_overdue_selector_picks_rows_past_deadline_regardless_of_remote_status`; `test_apify_ledger.py::test_orphaned_start_trips_latch` |
| N-01: delayed imports lack ordering against newer observations | high | Accepted; resolved | MS2-D-30 (new; `Listing.last_observed_at` watermark; guarded upsert, relist, and delist under row locks; history appends); MS2-D-12; MS2-D-13 item 5; MS2-D-22 stages 1–2; D2, D10 | `test_apify_import_ordering.py::test_reverse_completion_older_import_does_not_overwrite_newer_listing_state`, `::test_older_import_does_not_revive_listing_delisted_by_newer_evidence`, `::test_older_complete_import_does_not_delist_listing_observed_by_newer_run`, `::test_delayed_remote_import_after_switch_back_to_local_preserves_newer_content_and_delist_state`, `::test_legacy_listing_without_watermark_accepts_first_observation` |
| N-02: continuity is per source, not per scope | high | Accepted; resolved by per-scope tracking in Slice D (migration `0021`); disabling stale absence for new scopes rejected | MS2-D-31 (new; `scope_sweep_continuity`; the NULL scope keeps the lane mechanism plus a break rule); MS2-D-11; MS2-D-12; MS2-D-22 stage 2 and *Reject*; D2, D10; F1; R22 | `test_scope_continuity.py::test_scope_a_continuity_does_not_authorize_first_incomplete_scope_b_sweep` [non-null B, NULL B], `::test_remote_scope_a_runs_then_local_incomplete_scope_b_sweep_cannot_stale_delist`, `::test_run_not_sweeping_null_scope_breaks_null_scope_continuity`, `::test_older_eligible_sweep_does_not_move_scope_watermark_back`, `::test_legacy_null_scope_only_runs_keep_todays_continuity` |
| N-03: Slice B contradicts frozen Slice A registry tests | medium | Accepted; resolved | Slice B *Files* (narrow authorization; `zz-unregistered` sentinel); B3 tests; Slice B acceptance and exit evidence | renamed `test_categories.py::test_registered_categories_matches_ms2_registry`; `::test_rules_for_unregistered_is_none` and `test_resolver_dispatch.py::test_unregistered_hint_writes_none_edge_and_never_runs_drive_rules` on `zz-unregistered`; A0 `test_ms2_decision_baseline.py` unchanged |

**Migrations:** no number changes. The new schema lands in migrations that
have not yet merged:
- `0020`: `watch_evaluation.catalog_fingerprint`;
- `0021`: `Listing.last_observed_at`, `scope_sweep_continuity`, and the new
  `provider_run` columns;
- `0022`: the reservation status values and liability columns.

**Round 3: cross-agent delegate `f1b156e6`** (opposite provider, static
read-only review of revision 3 at `f4dab71` plus the Slice A code).
- **Verdict:** REVISION NEEDED. Five items were RESOLVED (F-03, F-04, F-07,
  F-10, N-03), three were PARTIAL (F-08, N-01, N-02), and one finding was new
  (M-01). Slice B was found unblocked.
- **Disposition:** all four open items ACCEPTED and resolved in revision 4 as
  plan changes to Slices D and E. No Slice A, B, or C contract changes;
  MS2-D-11 gains only a forward note to MS2-D-36. A focused design review now
  gates Slice D (core) (*Slice D entry gate*).
- The review's "verified correct" items stand: the F-04 gate and reset in
  Slice A code, catalog fingerprinting, state-neutral rejected probes,
  admission-time deadlines with the bounded-source Actor denial, Slice B's
  narrow test-update authorization, and the migration assignment B
  `0018`/`0019`, C `0020`, D `0021`, E `0022` on head `0017`.

| Item | Sev. | Status | Decision / plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| F-08 residual: settled spend aged out by `reserved_at` although cleanup may succeed later | high | Accepted; resolved | MS2-D-34 (new; `last_charge_at` window anchor; `reserved_at` provenance only; component-time accounting rejected); MS2-D-26 *Window and attribution*; MS2-D-32 *Window*; *Things not to do*; E1 (`last_charge_at`, index), E3, E4, E6; Slice E scope and acceptance | `test_apify_ledger.py::test_late_cleanup_settled_spend_counts_until_31_days_after_final_charge`, `::test_reconciled_spend_rolls_off_31_days_after_last_charge` (replaces `::test_reconciled_spend_rolls_off_after_window`), `::test_unreconciled_reservation_never_ages_out` |
| N-01 residual: current-content writes and unknown-key creation ignore newer absence | high | Accepted; resolved | MS2-D-35 (new; `Listing.last_absence_at`; per-scope `last_complete_sweep_at`; one current-eligible predicate for content, relist, and creation; unknown keys older than the scope watermark created as delisted history anchors; scope-row lock serialization); MS2-D-30 *Watermarks* and *Guards*; MS2-D-22 stages 1–2; D2, D10; Slice D acceptance; R23 | `test_apify_import_ordering.py::test_delayed_observation_after_newer_delist_restores_no_content_and_keeps_expiry` [delete-on-delist (stays redacted), merchant-fact] × [complete, stale delist], `::test_delayed_import_of_unknown_key_after_newer_complete_sweep_creates_no_active_listing` [non-null, NULL scope] × [merchant-fact, bounded delete-on-delist], `::test_delayed_observation_cannot_relist_row_delisted_before_newer_complete_sweep`, `::test_stage1_creation_and_newer_complete_sweep_serialize_on_scope_row`, `::test_delayed_remote_import_after_switch_back_to_local_preserves_newer_content_and_delist_state` (redaction assertion kept, content and expiry assertions added); `test_scope_continuity.py::test_only_gated_complete_full_scopes_raise_complete_sweep_watermark` |
| N-02 residual: NULL-scope break reversible by a delayed older run | high | Accepted; resolved | MS2-D-36 (new; per-scope `last_eligible_sweep_at` + `continuity_broken_at`, NULL scope on the FULL lane row; breaks carry event time; the NULL scope keeps the `ScraperRun` predecessor lookup so frozen eBay pause tests keep their meaning); MS2-D-31 *Legacy NULL scope*, *Record and break*; MS2-D-11 *Continuity*; MS2-D-22 stage 2 and *Reject*; D2, D10 | `test_scope_continuity.py::test_delayed_null_scope_run_after_newer_scoped_run_cannot_restore_continuity`, `::test_eligible_sweep_older_than_break_is_noop` [NULL, non-null], `::test_late_older_break_still_breaks`, `::test_legacy_null_scope_only_runs_keep_todays_continuity`; frozen `test_source_ebay.py` continuity tests green |
| M-01 (new): historical snapshots inherit the newer listing's retention deadline | high | Accepted; resolved | MS2-D-37 (new; an optional `ObservationRetention` argument to `append_snapshot`, whose omitted default copies from the listing; explicit per-observation class and expiry on the guarded path; newer-absence pull-forward, and no write when already retired); MS2-D-30 *History*; D *Files* (`persist.py`, owned by D10); D10 | `test_persist_observation_retention.py::test_reverse_order_bounded_observation_snapshot_keeps_its_own_deadline`, `::test_older_bounded_observation_after_newer_delist_is_not_snapshotted`, `::test_merchant_fact_older_observation_appends_history_with_null_expiry`, `::test_append_snapshot_default_copies_listing_retention` |

**Migrations (round 3):** no number changes. The new columns land in
migrations that have not yet merged, all nullable with no backfill:
- `0021` (Slice D, D2): `Listing.last_absence_at`; on `SourceLaneState`,
  `last_eligible_sweep_at`, `continuity_broken_at`, and
  `last_complete_sweep_at`; and on `scope_sweep_continuity`,
  `continuity_broken_at` and `last_complete_sweep_at`.
- `0022` (Slice E, E1): `ApifySpendReservation.last_charge_at` and the
  `(status, last_charge_at)` index.
- `0018`–`0020` are unchanged.

**Round 4: cross-agent delegate `a4b2e45b`** (opposite provider, static
read-only review of revision 4 at `ea80849`). Recorded in revision 5; the
round's record previously lived only in the handoff documents.
- **Verdict:** READY WITH ADVISORIES. All four round-3 items (F-08, N-01, N-02,
  M-01) RESOLVED at plan level; no new findings (R4-01 onward: none). Slice B
  was found clear to proceed.
- **Advisories (accepted, no plan change needed):** complete and record the
  mandatory *Slice D entry gate* before D2, including R23 and the scope/lane
  lock order; keep production deny-all admission until E's verification
  conditions hold. External Apify assumptions were not checked (research
  disabled) and remain D3, E2, and live-admission obligations.

**Revision 5 (owner decisions, s2, 2026-09-24).** Not a review response: it
encodes owner decisions made after round 4 converged (see the revision-5
changelog at the top, and MS2-D-38..-44). Rounds 1–4 above are unchanged, and
every decision not named in the changelog keeps its round-4 status.
- **Review status:** a Codex review of revision 5 is **pending**. The
  orchestrator runs round 5 after this revision lands; its verdict and
  disposition are recorded here as *Round 5*.
- **Design choices beyond the owner's words**, offered for that review:
  the contract artifact location and conformance direction (MS2-D-14); the
  Actor project and gate layout (MS2-D-38); the `TruncationReason` vocabulary
  (MS2-D-11); the two-check admission with its operator allowance, account
  margin, watch-refresh reserve, and boundary guard defaults (MS2-D-40); the
  settle-delay finalization rule and the `bound`/`counted` post-run modes
  (MS2-D-41); the pinned raw-GitHub synthetic source and the non-production
  proof environment (MS2-D-42); and `apify push` from a reviewed checkout with
  a `candidate` → `prod` tag promotion (MS2-D-43).

## Next slice after A

First the Slice A F-04 correction lands (see *Revision 3 correction* under A6).
Then **Slice B — first-class category specs and rules** (migrations `0018`,
`0019`), starting with B1 (satellites + CHECK pair + AC-1 coexistence test). D-prep
(D1 + D3) may proceed in parallel after A (see *Slice order*).
