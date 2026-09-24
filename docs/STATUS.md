# Project Status

## Current snapshot

- MS-0 and MS-1a through MS-1d are implemented and merged: Django/TimescaleDB foundation, ingestion substrate, matching, catalog seed, five connectors, and availability heartbeat.
- All marketplace sources ship disabled; scoring and alerting are not implemented.
- **Strategy re-baselined 2026-09-24:** [ADR 0021](adr/adr-0021-hybrid-acquisition-apify.md)
  adopts hybrid acquisition (retain cheap direct/local collectors; use self-owned private Apify
  Actors selectively; third-party Actors by measured exception) with a hard **$20/month**
  Hardware Radar Apify ceiling and a $12/month initial operating target. [ADR 0022](adr/adr-0022-multi-category-watch-first-v1.md)
  broadens v1 to HDD/SSD + GPU/accelerator + RAM + CPU first-class categories and makes
  requirement matching / watches / shortlist / alerting the launch-critical workflow.
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
- MS-2 scoring design revision 14 (`a3ec96b`) is owner-accepted (in-house closure recorded in
  §6/§7, 2026-09-06); revision 13 (`5e0e7c1`) settled seven MS-2a-facing rules first, and
  revision 14 settled three more raised by plan review.
- The MS-2a scoring-substrate implementation plan is cut and reviewed:
  `docs/superpowers/plans/2026-09-06-ms2a-scoring-substrate.md`, revision 4 (`05f130f`) on `dev`,
  migrations 0018-0026 planned. Cross-agent `review-plan` refused (spec closed at cap), so a
  Codex `delegate` second opinion ran three passes instead: pass 1 (`a626c2f0`) 14 findings/5
  blocking, addressed in plan revision 2 and design revision 13; pass 2 (`8755be2a`) 12
  resolved/2 partial/1 open/4 new (2 design-owned, folded into design revision 14), addressed in
  plan revision 3; pass 3 (`b92dd220`) 4 resolved/16 of 17 partial/19 new (all plan-owned),
  addressed in plan revision 4 and confirmed by an in-house verifier (no fourth Codex pass).
  **Execution is now deferred by ADR 0022.** The design remains the accepted advanced
  drive-scoring design, but MS-2a is no longer the next implementation step or a first-release gate.
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
- **MS-2 multi-category watch core is in progress.** The implementation plan
  (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`, revision 2) is cut and
  under cross-agent review: Codex delegate round 1 (`42deeff0`) returned REVISION NEEDED with 12
  findings, all accepted and dispositioned in revision 2; round 2 is in progress.
- **Slice A (behavior-preserving seams, no migration) is landed on `dev`** (unpushed pending this
  closeout): category registry + injectable ladder veto, optional `ParsedListing.category_hint`,
  resolver category dispatch (the hard-coded drive slug is removed), a provider run-evidence
  contract with a completeness gate, a `CollectionProvider` seam, and a rule that truncated/
  partial/failed provider runs cannot prove delisting. Integrated battery: 645 passed / 1 expected
  skip / 95% coverage, fmt/lint/type/pip-audit clean; a fresh verifier confirmed all 8 Slice A claims.
- The dependency-audit anyio CVEs (CVE-2026-63374/64847/63349) are cleared (`81876da`, anyio
  4.14.1->4.14.2; CI run 36049719317 success).
- **Next slice is B:** GPU/RAM/CPU typed spec satellites, category rows, the authoritative-alias
  acceptance policy (MS2-D-21), refdata importer generalization, and corpus category-hint round
  trip (B6). Catalog migrations 0018/0019 are assigned to Slice B (head remains 0017; nothing
  migrated this session). Production is unchanged at `f3303b1`.
- **Owner gates still open:** OQ23 (does an Apify paid-plan base fee count against the $20/month
  ceiling — live admission stays disabled until resolved), OQ24 (Actor-proof source selection —
  Newegg is excluded on ToU evidence; other candidates need ToS/robots review), the apify-actors
  repo's own admission gate for a new internal Actor, an owner-configured Apify account-level
  usage-limit backstop, MS-1e drive ratification, and a category corpus gate before GPU/RAM/CPU
  auto-accept.
- Work belongs on `dev`; protected `main` advances through pull requests.
- Project Standards Catalog 5 is pinned to release 5.29.0 with Agent Handoff 1.17, contract 1.0, and the dual automatic Claude/Codex profile.
- Upstream project-standards issue #80 (automatic handoff injection under the `uv-strict-python`
  shim) is resolved and re-verified 2026-09-06 under Catalog 5.29.0/Agent Handoff 1.17.
