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

- [ ] Run the MS-1e owner-in-the-loop ratification step (design §6): live-harvest a corpus with
  `manage.py harvest_corpus --all --limit …`, draft labels, have the owner audit a random ~20%
  sample plus every matcher-disagreement entry, run the full verification gate once as the single
  authorizing evidence, and on `ms1_ratification_gate == PASS` flip ADR-0019 to accepted.
  All code-side go-live gates (sweeper, eBay soft-delete, lane-state split) are now clear; this is
  the next owner step.

- [ ] Add the remaining SanDisk/WD real-corpus alias verification.
  Blocked on the owner-gated harvest / first SSD seed.

- [ ] Deliberately enable sources only after their operational and source-specific gates pass.

- [ ] After deploying migration 0015, `SourceLaneState.continuous_since` starts NULL, so no
  `ABSENT_STALE` delist is possible until each FULL lane has polled continuously for the 6h
  grace — expected and safe; confirm in the first post-deploy sweep logs (`delist stage ...
  skipping stale-absence marks`).

- [ ] Give the Seller table a retention policy (currently always NULL); needed once IR-002
  redaction reaches merchant usernames.
- [ ] `ProductModel`, `DriveSpec`, and `ProductAlias` carry the partial `expires_at` index
  (migration 0016) but still lack the DR-001 CHECK pair from `retention_constraints()`.
  Add the CHECK pair and update the ~45 test/db fixture sites that omit `retention_class`
  once the new resolver-learned-alias retention OQ (see `docs/open-questions.md`) is decided.

- [ ] Decide the WD Purple recert opt-in question.

- [ ] owner-gated choice: open a fresh cross-agent audit against MS-2 design revision 7
  (`docs/superpowers/specs/2026-09-06-ms2-scoring-design.md`, `2a28656`) for peer confirmation
  of SA-025/026/027/019, OR proceed directly to the planner cutting the MS-2a plan (then
  cross-agent review-plan).
- [ ] dev→main PR and deployment of migrations 0014/0015/0016 remains owner-gated.
