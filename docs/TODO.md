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
  first-party rows seeded; RAM expansion is a follow-up below.
- [ ] Add RAM seed expansion beyond the current 2 first-party rows (Slice B residual; more
  Micron-authored PDFs are hosted on third-party domains and are blocked by OQ27).
- [ ] Add `ProviderRunEvidence.truncation_reason` (MS2-D-11), deferred out of D1 into D2 or a
  follow-up.
- [ ] Run the Slice D entry-gate design review before D2 (plan: Slice D entry gate): a focused
  review of the D/E async-ordering design (watermarks, continuity, retention, charge horizon, R23).
- [ ] **Blocked on owner (OQ25):** provision a Hardware Radar Apify credential
  (`secret/apps/hw-radar/apify`, env `HW_RADAR_APIFY_TOKEN`) and decide `.mcp.json` tool scope.
- [ ] **Blocked on owner (OQ26):** set the external-liability bound for the shared Apify account;
  live paid admission, including the synthetic proof run, is denied until set.
- [ ] **Blocked on owner (OQ27):** decide the retention class for non-first-party reference data
  (e.g. the third-party-hosted Micron RAM PDFs); non-first-party seeds are refused until decided.
- [ ] **Blocked on owner (OQ28):** confirm MS-2 may exit on the synthetic proof alone while the
  Actor-backed merchant pilot (F5b) waits on OQ24.
- [ ] **Blocked on owner, optional (OQ29):** set the operator (build/inspection) allowance size.
- [ ] Define the v1 watch/requirement contract and implement the smallest complete buyer flow:
  saved requirement → eligible observations → evidence-backed shortlist → exactly-one alert.
  Advanced ADR-0011 drive scoring is optional enrichment, not an eligibility dependency.
- [ ] Add an acquisition-provider boundary to hw-radar. **Seam done:** `CollectionProvider`
  contract, run-evidence completeness gate, truncated/partial/failed runs cannot delist.
  **D-prep code-complete:** D3 client and D1 Actor project (`actors/hw-radar-synthetic-collector`,
  contract schemas, `classify_run`, CI Actor gates). **Next (Slice D):** Apify push/build/run
  (none has occurred yet), truncation_reason (above), Slice D entry-gate review, then D2.
- [ ] Add Hardware Radar Apify budget admission/accounting: hard $20/month project ceiling,
  initial $12/month operating target, reserve-before-run + reconcile-after-run, active-watch work
  ahead of broad discovery, explicit stale/`budget_paused` state, and no automatic residential
  proxy / paid third-party Actor escalation. Design is in the MS-2 plan; implementation is Slice E.
- [ ] Select a deliberately small initial source set (roughly 3–5) that exercises the first-class
  categories and both local + self-owned-Apify provider paths. Measure cost, completeness,
  identifier quality, condition/shipping coverage, freshness, and failure recovery before adding
  breadth.
- [ ] Coordinate one self-owned private Hardware Radar Actor as the integration proof. **Owned
  and managed in this repo** under `actors/` (not `apify-actors`, session-2 owner decision). Keep
  Actor output observation-only: no Django model imports, no production DB credentials, no
  canonical matching/persistence.
- [ ] **User-owned:** merge `L3DigitalNet/apify-actors` PR #62 (doc-only awareness note that
  Hardware Radar owns its Actors independently; CI green, awaiting operator merge per that
  repo's governance rule).
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
