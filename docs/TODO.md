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

- [ ] Refresh Project Standards after upstream issue #80 ships and re-verify automatic Agent Handoff startup under the `uv-strict-python` shim.

- [ ] Run the MS-1e owner-in-the-loop ratification step (design §6): live-harvest a corpus with
  `manage.py harvest_corpus --all --limit …`, draft labels, have the owner audit a random ~20%
  sample plus every matcher-disagreement entry, run the full verification gate once as the single
  authorizing evidence, and on `ms1_ratification_gate == PASS` flip ADR-0019 to accepted.
  All code-side go-live gates (sweeper, eBay soft-delete, lane-state split) are now clear; this is
  the next owner step.

- [ ] Add the remaining SanDisk/WD real-corpus alias verification.
  Blocked on the owner-gated harvest / first SSD seed.

- [ ] Deliberately enable sources only after their operational and source-specific gates pass.

- [ ] Give the Seller table a retention policy (currently always NULL); needed once IR-002
  redaction reaches merchant usernames.

- [ ] Add a partial index on `expires_at` for retention-sweep efficiency (needs a migration).

- [ ] Make refdata/refresh skip delisted rows via `not_delisted()`.

- [ ] Add `report.redactions` to the poller retention-sweep log line.

- [ ] Add a registry-level test pairing deletion-exemption with redaction for future anchor models.

- [ ] Move `DelistScope`/`DelistDetector` and `adapter_retention()` to `acquisition/contracts.py`.

- [ ] Add a cadence-aware guard for the 6h absence grace after long pauses.

- [ ] Decide the WD Purple recert opt-in question.
