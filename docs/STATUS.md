# Project Status

## Current snapshot

- MS-0 and MS-1a through MS-1d are implemented and merged: Django/TimescaleDB foundation, ingestion substrate, matching, catalog seed, five connectors, and availability heartbeat.
- All marketplace sources ship disabled; scoring and alerting are not implemented.
- Bounded-retention expiry enforcement, eBay listing-grain delete-on-delist (CR-004), and the
  per-lane scheduling-state split (ADR-0020) landed on `dev` (`5a7f5b7`, 2026-08-16).
- Retention/delist follow-up fixes landed on `dev` (`db62b6f`, 2026-08-30): partial `expires_at`
  indexes (migration 0014) and a continuity-aware CR-004 absence grace (migration 0015). A third
  partial-index migration (0016, `6e68585`) covers `ProductModel`/`DriveSpec`/`ProductAlias`. All
  three (0014/0015/0016) are on `dev` and pushed (2026-09-06) but undeployed pending
  the dev→main PR.
- Source go-live now remains gated by the SA-004 operations checklist and the MS-1e owner
  ratification step, not by missing code.
- MS-2 scoring design authored and under cross-agent (Codex) review, revision 5 on `dev`
  (`ebcb71b`): rounds 1-4 complete, round 5 held pending owner ratification of S2-1..S2-26
  (design §1.2). The MS-2a plan cannot be cut until that ratification lands.
- The scrapy CVE-2026-84366 dependency-audit red is cleared (`8cc3f10`, scrapy 2.16.0->2.18.0).
- MS-1e's validation-corpus harness and harvest tooling are merged to `main` and DEPLOYED
  (PR #20, release `1099f766`, healthz-verified 2026-08-16): the Approach-A evaluator, `EvalReport`
  with `precision_verdict`/`audit_gate`, the pure `ms1_ratification_gate`, the `harvest_corpus`
  management command with parse-diagnostics, and the rung-0 regression suite.
- The live harvest, Claude label drafting, owner audit, ratification run, and ADR-0019 flip remain
  the deferred owner-in-the-loop step (design §6); `tests/db/test_ratification_corpus.py` skips
  with "corpus not yet harvested" until that step lands.
- The full Python verification gate passes on `dev`; DB-backed tests require TimescaleDB.
- Work belongs on `dev`; protected `main` advances through pull requests.
- Project Standards Catalog 5 is pinned to release 5.29.0 with Agent Handoff 1.17, contract 1.0, and the dual automatic Claude/Codex profile.
- Upstream project-standards issue #80 (automatic handoff injection under the `uv-strict-python`
  shim) is resolved and re-verified 2026-09-06 under Catalog 5.29.0/Agent Handoff 1.17.
