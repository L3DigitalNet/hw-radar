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
  grace — expected and safe; deployed 2026-09-06 (run 34056023371); confirm on the first
  post-deploy sweep log (`delist stage ... skipping stale-absence marks`).

- [ ] Give the Seller table a retention policy (currently always NULL); needed once IR-002
  redaction reaches merchant usernames.
- [ ] Decide the WD Purple recert opt-in question.

- [ ] owner-gated: run MS-2a execution per the plan
  (`docs/superpowers/plans/2026-09-06-ms2a-scoring-substrate.md`, revision 4 `05f130f`),
  engineering legs per plan phases, once the owner says go.
- [ ] MS-2e must resolve eBay `feedbackScore` semantics before its plan can be cut.
