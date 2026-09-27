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

- [ ] Split the committed input contract into a common schema + synthetic extension so a future
  merchant Actor can't inherit `faultMode` (verifier finding, low priority).
- [ ] Harden D3 client's proxy guard: currently top-level only; nested proxy config is blocked via
  `prepare_run_input`'s `extra=forbid`, but a dedicated nested-key guard is a cleaner fix (low).
- [ ] Add listing-row fields (`title_raw`, `condition_label_raw`, `is_international`) to the
  eligibility evaluation binding; not currently read by the evaluator (verifier finding, low).
- [ ] Define the v1 watch/requirement contract and implement the smallest complete buyer flow:
  saved requirement → eligible observations → evidence-backed shortlist → exactly-one alert.
  Advanced ADR-0011 drive scoring is optional enrichment, not an eligibility dependency.
- [ ] Hold source expansion at eBay x CPU during its 3–5-day pilot. F1–F3 landed 2026-09-25 (eBay
  sweeps, `pilot_report`; evidence `docs/evidence/2026-09-25-f1-f3-pilot.md`). Evaluate eBay x drive
  first; WD/goHardDrive follow with per-cell gates. No Actor-backed merchant is admitted (OQ24, F5b).
- [ ] Capture listing condition: the pilot measured 0% condition coverage on every source (eBay
  Browse `condition`/`conditionId` is not mapped). Condition feeds the drive matcher's variant
  grain, so change it only with a `matcher_version` bump after, or together with, the MS-1e
  ratification. Same change: keep the title/label join visible to the negation window (today
  "... No Screws" + label "Used" denies the label; canonical text erases the " | " join).
- [ ] GPU/RAM production refdata seeding stays deferred until their own owner gate (CPU seeds
  cover the four F6 EPYC models, seeded 2026-09-26).
- [ ] F5a step 5 (MS2-D-45): before any production environment admits paid Apify work in the
  2026-09-05 cycle, drain the proof environment. Run its tick wrapper
  (`~/.local/state/hw-radar-f5a/tick.sh N GAP`, workstation-local; reads the runtime token from
  OpenBao at run time) at least once after 2026-10-02 21:10Z, so every reservation's closing read
  commits (monitoring windows close 2026-10-02 19:40–21:09Z; interim reads that fall due
  2026-09-26 are optional, and a missed one becomes overdue and is taken by the closing read). Then
  confirm `apify_spend_report` shows 0 monitoring and 0 outstanding, and run `apify_ledger_handoff`
  only for an admitted Actor-backed source with intentional production paid admission. Until then
  the proof environment holds the cycle's ledger authority.
- [ ] Production Apify runtime rendering: production secrets come from the Hetzner-side OpenBao
  peer through the CT's bao-agent template, not the workstation path; add the scoped runtime token
  there and render `HW_RADAR_APIFY_TOKEN` only for an admitted Actor-backed source with production
  paid admission intentionally configured (account settings, prices, ledger authority, and `MAX_KV_WRITES=3`, the
  owner-approved F-01 value).
- [ ] Promote the synthetic Actor build to the `prod` tag (MS2-D-43 *Deploy*) only if a production
  smoke is ever wanted; the synthetic Actor is not a production collection source.
- [ ] The drive corpus is now a CI gate (`test_ms1_ratification_gate`, refdata digest pinned): a
  committed drive-seed change fails it until the corpus is re-measured and its digest re-pinned.
- [ ] Historical-family recall (OQ33): the matcher returns `none` on the 8 legacy-family rows
  (Constellation, RE, Pipeline HD, ...). Add support only on the historical family itself, with
  reference evidence; never a successor remap (Constellation is not Exos, RE is not Gold).
- [ ] Add category-specific validation corpora/gates before auto-accepting GPU/RAM/CPU matches;
  drive-corpus precision does not validate other categories. F4 harvest done 2026-09-25: eBay,
  1,888 unlabeled entries (GPU 851, RAM 991, CPU 16, drive 30) in the git-ignored
  `.harvest/f4-ebay/` on the workstation; regenerate with `manage.py harvest_corpus --source ebay
  --out .harvest/f4-ebay`. Labeling and ratification are owner work (R4); CPU coverage is thin
  because the pilot sweep queries only "EPYC 7302".
- [ ] Ratify further CPU families only with their own owner-audited corpus (Intel Xeon first): add
  the family key to `categories.CPU_RATIFIED_FAMILIES`; EPYC-only today (OQ34, matcher 2026.09.4).
- [ ] eBay x CPU pilot (live 2026-09-27 01:09Z): run unchanged for 3–5 days; review about Sep 30–
  Oct 2 before widening. Report single-page scope proof, Browse calls/headroom, successful/failed runs,
  and `COMPLETE` delist provenance for all six scopes.
- [ ] Report eBay x CPU churn, duplicates, variations, unknown reasons over time, lock distribution,
  shipping-known share, and shortlist stability/usefulness for each of its six scopes.
- [ ] Diagnose `unknown`: histogram target unresolved, vendor lock unknown, shipping unknown, price
  indeterminate, and multiple reasons. Sample 10–20 largest-bucket rows; do not lower unknown as a KPI.
- [ ] Classify sampled unknowns as missing marketplace evidence, extraction defects, unseeded legitimate
  models, or unsupported OEM/sample parts. Fix only demonstrated defects.
- [ ] Keep new EPYC scopes single-page provable. An OEM part (7R13, 7J13, ...) needs its own seed and
  audit; never treat it as a 7763 alias.
- [ ] Evaluate eBay x drive first, without admission: assess ranking-limited legacy-query completeness,
  identifier coverage, CPU-plus-drive Browse quota, legacy-heartbeat effect, and watch usefulness.
- [ ] Evaluate WD/goHardDrive only after eBay x drive, with fresh policy verification and their own
  remaining per-cell gates. ADR 0019 ratifies the matcher only; no drive cell is admitted.
- [ ] Residual latent CPU matcher gap: Ryzen/Core "or" alternatives are unflagged because their
  lines are unseeded (cannot alias-hit). (Model priors now upgrade on a new condition: 2026.09.3.)
- [ ] Admit a `(source, category)` cell only after its independent operational, retention/ToS,
  completeness, match-quality, and cost checklist passes. eBay x CPU alone is admitted; drive cells
  remain unchecked pending the evaluations above.
- [ ] Before each future cell flip, an operator must run its live checklist in `docs/handoff/deployed.md`;
  then enable the source only when that cell is admitted.
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
