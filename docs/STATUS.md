# Project Status

## Current snapshot

- MS-0 and MS-1a through MS-1d are implemented and merged: Django/TimescaleDB foundation, ingestion substrate, matching, catalog seed, five connectors, and availability heartbeat.
- All marketplace sources ship disabled; scoring and alerting are not implemented.
- Bounded-retention expiry enforcement, eBay listing-grain delete-on-delist (CR-004), and the
  per-lane scheduling-state split (ADR-0020) landed on `dev` (`5a7f5b7`, 2026-08-16).
- Retention/delist follow-up fixes landed on `dev` (`db62b6f`, 2026-08-30): partial `expires_at`
  indexes (migration 0014) and a continuity-aware CR-004 absence grace (migration 0015). A third
  partial-index migration (0016, `6e68585`) covers `ProductModel`/`DriveSpec`/`ProductAlias`.
- OQ22 (resolver-learned-alias retention class) is resolved (owner option a): migration 0017
  (`8101504`) adds `RetentionClass.LISTING_DERIVED_ALIAS`, stamps it via the resolver, and lands
  the DR-001 CHECK pair on `ProductModel`/`DriveSpec`/`ProductAlias` with idempotent backfill.
  Migrations 0014-0017 are DEPLOYED (PR #22, `f3303b1`, 2026-09-06).
- Source go-live now remains gated by the SA-004 operations checklist and the MS-1e owner
  ratification step, not by missing code.
- MS-2 scoring design revision 12 on `dev` (`f3bc307`), owner-ratified (§1.2 incl. S2-23
  re-ratification, 2026-09-06). Fresh cross-agent audit b2eedc33 ran all five rounds against
  revision 7-12: SA-001..SA-008 resolved by the peer (revisions 8-11); SA-009 partial at the
  round-5 cap (mechanism accepted, two deliverable/coverage sentences inconsistent) and closed
  in revision 12 with in-house verification only (no peer review of revision 12 yet).
  Next step is owner-gated: accept the in-house SA-009 closure, or open a third audit against
  revision 12; then the planner cuts the MS-2a plan.
- Master-spec hygiene pass landed (`c3af4d7`, Revision History 0.15): score home = listing_score,
  $/TB computed in the scoring lib rather than an offer_snapshot generated column, lot-quantity
  wording, C.4 cap units 0.35/0.60, and ADR-0011 wording (q = price percentile, 1-q cheapness).
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
