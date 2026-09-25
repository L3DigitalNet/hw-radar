# Project Tasks

<!--
Purpose:
- This document is the user-visible task list and agent-visible project queue.

Instructions for AI agents:
- Do not add tasks to the `## User tasks` section.
- Do add tasks to the `## Agent tasks` section. Include all open work from agent-managed handoff documents.
- Use `- [ ]` to indicate open work and `- [x]` for work completed during the current session.
- Remove completed standalone agent tasks after recording their outcomes in `docs/STATUS.md`.
-->

## User tasks

## Agent tasks

- [x] Re-baseline the implementation around [ADR 0021](adr/adr-0021-hybrid-acquisition-apify.md)
  and [ADR 0022](adr/adr-0022-multi-category-watch-first-v1.md). **Plan converged at rev 8**
  (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`); Codex r5-r8 review
  READY, no new findings.
- [x] Establish the category-domain boundary around the existing ADR-0010 identity spine.
  **Slice B complete on dev** (satellites, category rows, refdata importer, first-party seeds,
  rules/registry/acceptance policy, corpus category-hint round trip). CPU 9 / GPU 8 / RAM 2
  first-party rows seeded; no further RAM expansion is planned in MS-2 (OQ27 resolved
  2026-09-25 — no new retention class for non-first-party reference data).
- [ ] Split the committed input contract into a common schema + synthetic extension so a future
  merchant Actor can't inherit `faultMode` (verifier finding, low priority).
- [ ] Harden D3 client's proxy guard: currently top-level only; nested proxy config is blocked via
  `prepare_run_input`'s `extra=forbid`, but a dedicated nested-key guard is a cleaner fix (low).
- [ ] Add listing-row fields (`title_raw`, `condition_label_raw`, `is_international`) to the
  eligibility evaluation binding; not currently read by the evaluator (verifier finding, low).
- [ ] **Blocked on owner:** create the scoped runtime Apify token (OpenBao
  `secret/apps/hw-radar/apify`, env `HW_RADAR_APIFY_TOKEN`); the unscoped operator/deploy key
  exists at `secret/apps/hw-radar/agent/apify` (OQ25 resolved 2026-09-25). Permissions needed:
  run Hardware Radar-owned Actors, read their runs/default storages, **delete** their run storages
  (MS2-D-33 cleanup), and ideally read `/users/me/limits` + `/users/me/usage/monthly` (MS2-D-40).
  The empty private Actor resource now exists (2026-09-25), so the token's resource-specific
  permissions can name it; Apify scopes only to existing resources.
- [ ] Define the v1 watch/requirement contract and implement the smallest complete buyer flow:
  saved requirement → eligible observations → evidence-backed shortlist → exactly-one alert.
  Advanced ADR-0011 drive scoring is optional enrichment, not an eligibility dependency.
- [x] Add an acquisition-provider boundary to hw-radar. **Slice D (core) complete 2026-09-25**
  (D1–D12; D9 verifier 10/11 bullets hold, the storage-deadline bullet with the plan's accepted
  exceptions). A FULL Actor start is refused while a same-scope run's import is undecided
  (`scope_run_outstanding`, `3c117c9`). Migration `0021` undeployed. See `docs/STATUS.md`.
- [x] Add Hardware Radar Apify budget admission/accounting ($20/month cash ceiling, $12 operating
  target, reserve-before-run + reconcile-after-run, `budget_paused`, no automatic escalation).
  **Slice E complete 2026-09-25** (E1–E8; E7 verifier 16/16 acceptance claims hold; crash windows
  d1/d2 closed `40b7295`). Migration `0022` undeployed. Keep `report._place` in step with
  `ledger._tally` (pinned by `test_cycle_totals_match_ledger_cycle_debits`).
- [ ] Select a deliberately small initial source set (roughly 3–5) that exercises the first-class
  categories and both local + self-owned-Apify provider paths. Measure cost, completeness,
  identifier quality, condition/shipping coverage, freshness, and failure recovery before adding
  breadth.
- [ ] Coordinate one self-owned private Hardware Radar Actor as the integration proof. **Owned
  and managed in this repo** under `actors/` (not `apify-actors`, session-2 owner decision). Keep
  Actor output observation-only: no Django model imports, no production DB credentials, no
  canonical matching/persistence.
  **F5a prerequisites (all unmet; production stays deny-all until every one holds):**
  1. Owner: scoped runtime token (item above); verify it can read `/v2/users/me` and
     `/v2/users/me/limits`, else admission denies `account_state_unobservable` (the operator key
     is never a fallback). Keep the account usage limit at or below the prepaid credit.
  2. Operator: set the eight unit prices from `apify.com/pricing` (`…_USD_PER_CU`,
     `…_DATASET_{READS,WRITES}_USD_PER_1000`, `…_DATASET_STORAGE_USD_PER_GB_HOUR`,
     `…_KV_{READS,WRITES}_USD_PER_1000`, `…_KV_STORAGE_USD_PER_GB_HOUR`,
     `…_TRANSFER_USD_PER_GB`) plus `…_MARGIN`, `…_MAX_TIMEOUT_S`, `…_MAX_KV_WRITES`,
     `…_MAX_KV_BYTES` (≤ `…_MAX_API_RESPONSE_BYTES`), `…_STORAGE_MAX_LIFETIME` (≥ 31-day
     retention), `…_LEDGER_ID`, `…_ACTOR_ID`; confirm `…_ACTOR_BUILD`/`…_ACTOR_NAME`.
  3. Code: register the synthetic site's `ActorRunSpec` in `jobs.RUN_SPECS` and its
     SourceSite/SourceConfig (`collection_provider=apify`); deploy `0021`/`0022`.
  4. Operator: `apify_operator_reserve --kind build` before `apify push`/build; settle it.
  5. Set `HW_RADAR_APIFY_ENABLED=true`; the first start discovers the cycle and is denied
     `ledger_authority_missing`; owner runs `apify_ledger_claim`; then the capability probe (R25).
- [x] Keep provider identity separate from marketplace/source identity: proven by D7
  (`test_provider_switch_preserves_identity_history_and_watch_state`, AC-4).
- [ ] Run the existing MS-1e owner-in-the-loop ratification step for the drive matcher before
  enabling affected drive source/category combinations: live harvest, label draft, owner audit,
  full verification gate, and ADR-0019 flip only on PASS.
- [ ] Add category-specific validation corpora/gates before auto-accepting GPU/RAM/CPU matches;
  drive-corpus precision does not validate other categories.
- [ ] Deliberately enable each source × category combination only after its operational,
  retention/ToS, completeness, match-quality, and cost gates pass.
- [ ] Add the remaining SanDisk/WD real-corpus alias verification; blocked on the owner-gated
  drive harvest / first SSD seed.
- [ ] Confirm the first post-migration-0015 continuous-sweep log before relying on
  `ABSENT_STALE` delisting (deployed 2026-09-06; expected 6h grace).
- [ ] **Gated framework upgrade:** Django 6.1.2+ (PR #31, 6.1.1, was closed/deferred 2026-09-25).
  Before adopting any 6.1.x release: read its release notes, review backwards-incompatible
  changes against the raw-SQL Timescale migrations/constraints, run PostgreSQL checks and a
  deprecation-warning pass, run the full gate, smoke-test admin/auth/sessions, and run
  `manage.py check --deploy` before an intentional production deploy. 6.0 security support runs
  to April 2027.
- [ ] **Deferred:** Scrapy 2.19+ (PR #32 closed 2026-09-25; no need for its remote-control/MCP
  capability, and its aiohttp dependency set widens the surface — 2.18 works). On adoption, pin
  `REMOTE_CONTROL_ENABLED = False` and `TELNETCONSOLE_ENABLED = False`.
- [ ] Give the Seller table a retention policy before merchant usernames enter IR-002 redaction.
- [ ] Decide the WD Purple recert opt-in question.
- [ ] **Deferred:** MS-2a scoring-substrate execution
  (`docs/superpowers/plans/2026-09-06-ms2a-scoring-substrate.md`). Revisit after the
  multi-category watch-first path is working and real history identifies which category-specific
  scoring work is valuable.
- [ ] **Deferred with MS-2:** resolve eBay `feedbackScore` semantics before any future MS-2e plan.
- [ ] **Blocked on owner (OQ24):** rule on the production Actor-backed merchant source. Newegg is
  excluded on ToU evidence (automated access/scraping prohibited "for any purpose", retrieved
  2026-09-24); B&H and refurbished server-parts sellers still need ToS/robots review. Plan finding
  R20: bounded-retention sources (e.g. eBay, 6h TTL) need a verified per-run Apify storage expiry.
  The first Actor proof no longer needs this answer (uses a controlled synthetic source).
