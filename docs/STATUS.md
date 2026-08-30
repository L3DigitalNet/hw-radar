# Project Status

## Current snapshot

- MS-0 and MS-1a through MS-1d are implemented and merged: Django/TimescaleDB foundation, ingestion substrate, matching, catalog seed, five connectors, and availability heartbeat.
- All marketplace sources ship disabled; scoring and alerting are not implemented.
- Bounded-retention expiry enforcement, eBay listing-grain delete-on-delist (CR-004), and the
  per-lane scheduling-state split (ADR-0020) landed on `dev` (`5a7f5b7`, 2026-08-16).
- Retention/delist follow-up fixes landed on `dev` (`db62b6f`, 2026-08-30): partial `expires_at`
  indexes (migration 0014) and a continuity-aware CR-004 absence grace (migration 0015). Not yet
  pushed, PR'd, or deployed.
- Source go-live now remains gated by the SA-004 operations checklist and the MS-1e owner
  ratification step, not by missing code.
- MS-1e's validation-corpus harness and harvest tooling are merged to `main` and DEPLOYED
  (PR #20, release `1099f766`, healthz-verified 2026-08-16): the Approach-A evaluator, `EvalReport`
  with `precision_verdict`/`audit_gate`, the pure `ms1_ratification_gate`, the `harvest_corpus`
  management command with parse-diagnostics, and the rung-0 regression suite.
- The live harvest, Claude label drafting, owner audit, ratification run, and ADR-0019 flip remain
  the deferred owner-in-the-loop step (design §6); `tests/db/test_ratification_corpus.py` skips
  with "corpus not yet harvested" until that step lands.
- The full Python verification gate passes on `dev`; DB-backed tests require TimescaleDB.
- Work belongs on `dev`; protected `main` advances through pull requests.
- Project Standards Catalog 5 is pinned to release 5.11.0 with Agent Handoff 1.6, contract 1.0, and the dual automatic Claude/Codex profile.
- Automatic handoff injection is blocked under the workstation's `uv-strict-python` shim by upstream project-standards issue #80; the managed integration remains unchanged.
