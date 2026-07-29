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

- [ ] Write and execute the MS-1e validation-corpus and harvest-tooling implementation plan.

  Keep the real-corpus harvest, owner label audit, ratification gate, and ADR-0019 flip as the later owner-in-the-loop step.

- [ ] Implement bounded-retention expiry enforcement before enabling any bounded source.

- [ ] Implement the eBay listing-grain delete-on-delist soft-delete path before eBay go-live.

- [ ] Resolve the shared fast/slow-lane scheduling state before enabling a fast-lane source.

  Admission backoff, `current_interval_s`, and clean-poll ramp state currently couple both lanes through one `SourceConfig` row.

- [ ] Complete connector follow-ups for WD enterprise recert discovery and Scrapy diagnostics.

- [ ] Add the remaining DB-level family-agreement veto regression and SanDisk/WD real-corpus alias verification.

- [ ] Deliberately enable sources only after their operational and source-specific gates pass.
