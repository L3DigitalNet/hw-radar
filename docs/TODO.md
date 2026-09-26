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
- [x] Scoped runtime Apify token created by the owner (OpenBao `secret/apps/hw-radar/apify`, env
  `HW_RADAR_APIFY_TOKEN`), restricted to the Hardware Radar Actor (`Read`, `Run`, `List runs`,
  `Manage runs`, restricted access, default run storages). No account permission is needed: the
  runtime reads no account state (OQ30, plan rev 12, MS2-D-48). Capability probe recorded
  2026-09-25 (403 for inaccessible storage, 404 for nonexistent).
- [ ] Define the v1 watch/requirement contract and implement the smallest complete buyer flow:
  saved requirement → eligible observations → evidence-backed shortlist → exactly-one alert.
  Advanced ADR-0011 drive scoring is optional enrichment, not an eligibility dependency.
- [x] Add an acquisition-provider boundary to hw-radar. **Slice D (core) complete 2026-09-25**
  (D1–D12; D9 verifier 10/11 bullets hold, the storage-deadline bullet with the plan's accepted
  exceptions). A FULL Actor start is refused while a same-scope run's import is undecided
  (`scope_run_outstanding`, `3c117c9`). Migration `0021` deployed 2026-09-25 (`508b1f0`).
- [x] Add Hardware Radar Apify budget admission/accounting ($20/month cash ceiling, $12 operating
  target, reserve-before-run + reconcile-after-run, `budget_paused`, no automatic escalation).
  **Slice E complete 2026-09-25** (E1–E8; E7 verifier 16/16 acceptance claims hold; crash windows
  d1/d2 closed `40b7295`). Migration `0022` deployed 2026-09-25 (`508b1f0`). Keep `report._place` in step with
  `ledger._tally` (pinned by `test_cycle_totals_match_ledger_cycle_debits`).
- [ ] Select a deliberately small initial source set (roughly 3–5) that exercises the first-class
  categories and both local + self-owned-Apify provider paths. F1–F3 landed 2026-09-25 (eBay
  category sweeps, `pilot_report`; evidence `docs/evidence/2026-09-25-f1-f3-pilot.md`). Remaining:
  measure a sustained pilot (more than one run per source), which needs the owner's per-cell
  enablement. Only eBay, goHardDrive, and WD are usable local sources — ServerPartDeals and
  Seagate are retired (OQ31) — and no Actor-backed merchant is admitted yet (OQ24, F5b deferred).
- [ ] Capture listing condition: the pilot measured 0% condition coverage on every source (eBay
  Browse `condition`/`conditionId` is not mapped). Condition feeds the drive matcher's variant
  grain, so change it only with a `matcher_version` bump after, or together with, the MS-1e
  ratification.
- [ ] Seed CPU reference rows in production (`import_refdata --category cpu`; production has 0
  category spec rows) before any CPU watch, and extend seeds to cover the chosen F6 EPYC models.
  GPU/RAM production seeding is deferred until their own pilot/owner gate is scheduled.
- [x] Coordinate one self-owned private Hardware Radar Actor as the integration proof. **F5a
  executed 2026-09-25** in a non-production proof environment (evidence:
  `docs/evidence/2026-09-25-f5a-synthetic-proof.md`): build `1.0.1`, eleven admitted runs over every
  fault mode, zero delistings, live AC-4 switch, $0.00527 total account usage.
- [ ] F5a step 5 (MS2-D-45): before any production environment admits paid Apify work in the
  2026-09-05 cycle, drain the proof environment. Run its tick wrapper
  (`~/.local/state/hw-radar-f5a/tick.sh N GAP`, workstation-local; reads the runtime token from
  OpenBao at run time) at least once after 2026-10-02 21:10Z, so every reservation's closing read
  commits (monitoring windows close 2026-10-02 19:40–21:09Z; interim reads that fall due
  2026-09-26 are optional, and a missed one becomes overdue and is taken by the closing read). Then
  confirm `apify_spend_report` shows 0 monitoring and 0 outstanding, and run `apify_ledger_handoff`
  only if production paid admission is actually wanted. Until then the proof environment holds the
  cycle's ledger authority.
- [ ] Production Apify runtime rendering: production secrets come from the Hetzner-side OpenBao
  peer through the CT's bao-agent template, not the workstation path; add the scoped runtime token
  there and render `HW_RADAR_APIFY_TOKEN` only when production paid admission is intentionally
  configured (with the account settings, prices, ledger authority, and `MAX_KV_WRITES=3`, the
  owner-approved F-01 value).
- [ ] Promote the synthetic Actor build to the `prod` tag (MS2-D-43 *Deploy*) only if a production
  smoke is ever wanted; the synthetic Actor is not a production collection source.
- [x] Keep provider identity separate from marketplace/source identity: proven by D7
  (`test_provider_switch_preserves_identity_history_and_watch_state`, AC-4).
- [ ] MS-1e drive-matcher ratification: harvest and draft labels are done (2026-09-25,
  `docs/evidence/2026-09-25-ms1e-audit-packet.md`; provisional FAIL, 17 of 100 required
  auto-accepts). The `ebay-0021` ("comparable to") and `ghd-0006` (Constellation read as Exos)
  traps are fixed (`matcher_version` 2026.09.2), the WD SKU→MPN gap is closed, and OQ32 settled the
  gate on ≥3 declared ratification sources evaluated against production refdata. Waiting on the
  owner: audit the remaining listed ids and decide the labeling rule (R2). Then run the full
  verification gate and flip ADR-0019 only on a composite PASS.
- [ ] Add category-specific validation corpora/gates before auto-accepting GPU/RAM/CPU matches;
  drive-corpus precision does not validate other categories. F4 harvest done 2026-09-25: eBay,
  1,888 unlabeled entries (GPU 851, RAM 991, CPU 16, drive 30) in the git-ignored
  `.harvest/f4-ebay/` on the workstation; regenerate with `manage.py harvest_corpus --source ebay
  --out .harvest/f4-ebay`. Labeling and ratification are owner work (R4); CPU coverage is thin
  because the pilot sweep queries only "EPYC 7302".
- [ ] Deliberately admit each `(source, category)` cell in the admission matrix
  (`src/hw_radar/acquisition/admission.py`) only after its operational, retention/ToS,
  completeness, match-quality, and cost gates pass, then flip the source's `enabled` bit. Before
  the first flip, an operator must run the per-cell live checklist in `docs/handoff/deployed.md`
  against the live system (still unchecked; every cell is `NOT_ADMITTED`).
- [ ] Re-verify eBay category IDs (27386, 170083, 11210, 164, 56088) through the Taxonomy API
  quarterly and on eBay category-change notices (last 2026-09-25, tree 0, version 134).
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
- [ ] **Seagate segment-map research gap:** the `ne`/`vn`/`vx`/`dm` SKU segments are unresearched
  for rebrand ambiguity (unlike the `nm` segment fixed in `matcher_version` 2026.09.2); Seagate
  itself is retired (OQ31), but ghd/other sources may still emit these SKUs.
- [ ] Drive recall now depends on first-party refdata: the ratification gate reads production
  refdata via `import_refdata` (drive seed digest pin), not the unit-test `seeded_catalog`, so a
  gap in the production drive seed is a gap in ratifiable coverage, not just in tests.
- [ ] Before any Intel Xeon pilot, review the bare-number aliases in the Intel refdata seeds (e.g.
  `6338`, `8358`) for collision risk against other manufacturers' bare model numbers.
