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
- [ ] Define the v1 watch/requirement contract and implement the smallest complete buyer flow:
  saved requirement → eligible observations → evidence-backed shortlist → exactly-one alert.
  Advanced ADR-0011 drive scoring is optional enrichment, not an eligibility dependency.
- [ ] Add an acquisition-provider boundary to hw-radar. **Seam done:** `CollectionProvider`
  contract, run-evidence completeness gate, truncated/partial/failed runs cannot delist.
  **D-prep code-complete:** D3 client and D1 Actor project (`actors/hw-radar-synthetic-collector`,
  contract schemas, `classify_run`, CI Actor gates). **D2 schema done** (migration `0021`:
  `provider_run`, `scope_sweep_continuity`, `SourceConfig.collection_provider`, listing scope and
  ordering watermarks, NULL-scope lane watermarks, scope-filtered `_apply_delist`). **D4 done**
  (`ApifyImportProvider`, `provider_run.run_output` in `0021`, `source_retention` registry,
  derived dataset `page_limit`, remote runs skip the soft-block classifier). **D3 follow-up done**
  (`/v2/actors` start with `restartOnError=false`, surfaced unparseable usage,
  `dataRetentionDays`, response/page/request byte caps, pinned `SO_RCVBUF`). **D10 core done**
  (`acquisition.stages`: transactional persist/delist with the MS2-D-35 lock order and in-memory
  retry, `observe_listing` ordering guards, per-observation snapshot retention, per-scope ordered
  continuity; `acquisition.apify.importer`: MS2-D-22 state machine, read caps, Reject). **D5
  done** (`acquisition.apify.jobs`: start job behind `DenyAllAdmission` + kill switch, MS2-D-33
  refusals and row-before-start, options/version-line check with the counted mismatch abort,
  `apify-poll` job with the three MS2-D-23 selectors and per-row backoff; D10 restart tests).
  **D6–D8 done** (AC-4/5/6 fixture proofs in `tests/db/test_apify_import.py`;
  `ProviderRunEvidence.truncation_reason`, optional on the model, DB CHECK enforces it).
  **D12 done** (`recovery_probe_job` starts a PROBE Actor run for an `apify` source, never its
  local adapter; one outstanding probe per source; a finalized probe recovers only when
  `complete`/`truncated`, `partial_failure` is `PROBE_FAILURE`).
  **D11 done** (`acquisition.apify.storage_cleanup`: selector-2 cleanup and selector-3
  overdue units bound as the `apify_poll_tick` defaults; counted attempts with delete-attempt
  cap → `delete_failed`; terminal-evidence-first abort/confirm; the ED-19 404 rule behind
  `HW_RADAR_APIFY_DELETE_404_IS_ABSENT`; `provider_run.orphaned_start_at` in `0021`).
  **Slice D (core) closed 2026-09-25 (D9):** verifier pass at `4ba8dce` — 10/11 acceptance
  bullets hold; the storage-deadline bullet holds with the plan's accepted exceptions (a lost
  start response has no storage ids to delete, residual R21 + E latch; `delete_failed` at the
  attempt cap; overdue cleanup *starts* at the deadline). No production path can start a live
  run. Live Actor runs remain owner-gated (scoped runtime token, Slice E admission, operator
  reservation before any build). Remaining hand-offs to F5a (E4 landed the
  `final_charge_op_at` stamp and the D-side latch trips; E5 bound the ledger as
  `BUDGET_ADMISSION` and records probe denials as ledger rows): F5a registers the synthetic site's `ActorRunSpec` in
  `jobs.RUN_SPECS` (empty in production, so every `apify` source is refused `no_run_spec`). Apify
  push/build/run: none has occurred yet.
- [ ] Add Hardware Radar Apify budget admission/accounting: hard $20/month project ceiling,
  initial $12/month operating target, reserve-before-run + reconcile-after-run, active-watch work
  ahead of broad discovery, explicit stale/`budget_paused` state, and no automatic residential
  proxy / paid third-party Actor escalation. Design is in the MS-2 plan; implementation is Slice E.
  **E1 done** (migration `0022_apify_spend_ledger`: reservation, usage-read, latch, cycle,
  cycle-discovery, and ledger-authority tables with their single-row CHECKs). **E2 done**
  (`acquisition.apify.budget`: DB-free `estimate_run_cost`, operator envelopes, per-call bounds,
  `decide_admission`; Slice E settings keys that deny rather than raise). **E3 done**
  (`acquisition.apify.ledger`: `reserve` under the budget advisory lock with the MS2-D-34 cycle
  predicate, account snapshot + counted cycle discovery, MS2-D-45 claim/export/import, operator
  reservations; commands `apify_ledger_claim`, `apify_ledger_handoff`, `apify_operator_reserve`
  (reserve only), `apify_budget_reset --discovery`; 0022 gained the authority's
  `imported_record_digest` and `handoff_record`). **E6 done** (`acquisition.apify.report` and the
  read-only `apify_spend_report` command: per-cycle figures, attribution, unsettled/overrun rows,
  trailing-31-day trend). **E4 done** (`acquisition.apify.reconcile`: stable-read/bound
  settlement from the write-once work-completion anchor, post-run cost, selector-2 reconcile
  unit and bound-build reads, selector 4 with pending markers and closing reads, MS2-D-47
  correction re-checks; latch trips/reset/estimator-version clear in `ledger`;
  `apify_operator_reserve --settle` and `apify_budget_reset --reason`; `ApifyUsageRead`
  append-only; E3 residuals: denied envelope rows persisted, `--reason` stored). **E5 done**
  (`jobs.LedgerAdmission` is the production binding: account-snapshot refresh only when a
  setting would not already deny, then `reserve`; the run is attached to its reservation in the
  row's creating commit; `budget_paused` + reason in the shortlist; E4 residuals: KV byte-cap
  trip, stale selector-4 markers resolved at poller start, `report.py` on `reconcile`'s
  predicates; over-cap account reads trip the latch). **E8 done** (probe admitted, imported,
  and settled through the real ledger and reconcile unit). **Crash windows closed** (verifier
  d1/d2): the `orphaned_start`/`delete_attempts_exhausted` trips commit with the row mark, and
  every tick re-detects untripped rows. Every tick also releases runtime reservations left
  unattached past `HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S` (900). **Next:** E7 close-out. Mirror any
  `ledger._tally` predicate change in `report._place` (pinned by
  `test_cycle_totals_match_ledger_cycle_debits`). Owner/operator before F5a's first paid call:
  set the eight unit prices from `apify.com/pricing`, plus `…_MARGIN`, `…_MAX_TIMEOUT_S`,
  `…_MAX_KV_WRITES`, `…_MAX_KV_BYTES`, and `…_STORAGE_MAX_LIFETIME` (no plan defaults; unset
  denies); set `…_LEDGER_ID`, `…_ACTOR_ID`, and `HW_RADAR_APIFY_ENABLED=true`; render the scoped
  `HW_RADAR_APIFY_TOKEN`; the first enabled start then discovers the billing cycle (denied
  `ledger_authority_missing`), after which the owner runs `apify_ledger_claim`.
- [ ] Select a deliberately small initial source set (roughly 3–5) that exercises the first-class
  categories and both local + self-owned-Apify provider paths. Measure cost, completeness,
  identifier quality, condition/shipping coverage, freshness, and failure recovery before adding
  breadth.
- [ ] Coordinate one self-owned private Hardware Radar Actor as the integration proof. **Owned
  and managed in this repo** under `actors/` (not `apify-actors`, session-2 owner decision). Keep
  Actor output observation-only: no Django model imports, no production DB credentials, no
  canonical matching/persistence.
- [ ] Keep provider identity separate from marketplace/source identity so moving a source between
  local and Apify execution preserves listing identity and price history.
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
