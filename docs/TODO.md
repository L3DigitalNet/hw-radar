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
  and [ADR 0022](adr/adr-0022-multi-category-watch-first-v1.md). **Plan converged at rev 4**
  (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`); Codex r1-r4 review
  READY WITH ADVISORIES.
- [ ] Establish the category-domain boundary around the existing ADR-0010 identity spine.
  **Slice A done and post-closeout fixed** (`27b0c1d`): category registry, injectable ladder veto,
  optional `category_hint`, resolver dispatch (drive slug hard-code removed). **Next (Slice B):**
  GPU/RAM/CPU typed spec satellites, category rows, authoritative-alias policy MS2-D-21, refdata
  importer generalization, corpus category-hint round trip (B6).
- [ ] Run the Slice D entry-gate design review before D2 (plan: Slice D entry gate): a focused
  review of the D/E async-ordering design (watermarks, continuity, retention, charge horizon, R23).
- [ ] Define the v1 watch/requirement contract and implement the smallest complete buyer flow:
  saved requirement → eligible observations → evidence-backed shortlist → exactly-one alert.
  Advanced ADR-0011 drive scoring is optional enrichment, not an eligibility dependency.
- [ ] Add an acquisition-provider boundary to hw-radar. **Seam done:** `CollectionProvider`
  contract, run-evidence completeness gate, truncated/partial/failed runs cannot delist. **Next
  (Slice D):** Apify-backed provider adapter starting bounded private Actor runs with idempotent
  import.
- [ ] Add Hardware Radar Apify budget admission/accounting: hard $20/month project ceiling,
  initial $12/month operating target, reserve-before-run + reconcile-after-run, active-watch work
  ahead of broad discovery, explicit stale/`budget_paused` state, and no automatic residential
  proxy / paid third-party Actor escalation. Design is in the MS-2 plan; implementation is Slice E.
- [ ] Select a deliberately small initial source set (roughly 3–5) that exercises the first-class
  categories and both local + self-owned-Apify provider paths. Measure cost, completeness,
  identifier quality, condition/shipping coverage, freshness, and failure recovery before adding
  breadth.
- [ ] Coordinate one self-owned private Hardware Radar Actor in the separate
  `L3DigitalNet/apify-actors` repo as the integration proof. Keep Actor output observation-only:
  no Django model imports, no production DB credentials, no canonical matching/persistence.
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
- [ ] **Blocked on owner (OQ23):** decide whether an Apify paid-plan base fee counts against the
  $20/month Hardware Radar ceiling. Live Apify admission stays disabled until resolved.
- [ ] **Blocked on owner (OQ24):** rule on the Actor-proof source. Newegg is excluded on ToU
  evidence (automated access/scraping prohibited "for any purpose", retrieved 2026-09-24); B&H
  and refurbished server-parts sellers still need ToS/robots review. Plan finding R20: bounded-
  retention sources (e.g. eBay, 6h TTL) need a verified per-run Apify storage expiry for an Actor path.
- [ ] **Blocked on owner:** admission decision for a new internal Actor in the separate
  `L3DigitalNet/apify-actors` repo's opportunity/admission gate.
- [ ] **Blocked on owner:** configure the Apify account-level monthly usage limit as a spend backstop.
