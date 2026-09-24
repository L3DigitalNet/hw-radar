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
- Bounded-retention expiry, eBay listing-grain delete-on-delist (CR-004), and per-lane
  scheduling-state split (ADR-0020) landed `5a7f5b7`; follow-up index/grace migrations 0014-0016
  landed `db62b6f`/`6e68585`. OQ22 resolved via migration 0017 (`8101504`, owner option a):
  `RetentionClass.LISTING_DERIVED_ALIAS` plus the DR-001 CHECK pair. Migrations 0014-0017 are
  DEPLOYED (PR #22, `f3303b1`, 2026-09-06). Source go-live is gated by SA-004 + MS-1e ratification.
- MS-2 scoring design revision 14 (`a3ec96b`) is owner-accepted (2026-09-06). The MS-2a
  scoring-substrate plan reached revision 4 (`05f130f`) across three Codex `delegate` passes
  (`a626c2f0`/`8755be2a`/`b92dd220`); design and plan are the accepted advanced drive-scoring
  artifacts, but **execution is deferred by ADR 0022** and MS-2a is not the next implementation step.
- Master-spec hygiene pass landed (`c3af4d7`, Revision History 0.15). The scrapy CVE-2026-84366
  dependency-audit red is cleared (`8cc3f10`, scrapy 2.16.0->2.18.0); the anyio CVEs
  (CVE-2026-63374/64847/63349) are cleared (`81876da`, anyio 4.14.1->4.14.2; CI 36049719317 success).
- MS-1e's validation-corpus harness/harvest tooling is merged and DEPLOYED (PR #20, `1099f766`,
  2026-08-16): Approach-A evaluator, `EvalReport`, `ms1_ratification_gate`, `harvest_corpus`. The
  live harvest/label-draft/audit/ratification/ADR-0019 flip remain the deferred owner-in-the-loop
  step; `tests/db/test_ratification_corpus.py` skips until it lands.
- The full Python verification gate passes on `dev`; DB-backed tests require TimescaleDB.
- **MS-2 multi-category watch-core plan converged at revision 4**
  (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`). Four-round Codex delegate
  review lineage: r1 `42deeff0` REVISION NEEDED (12 findings) -> r2 `537cd698` REVISION NEEDED
  (7 resolved/5 partial/3 new) -> r3 `f1b156e6` REVISION NEEDED (5 resolved/3 partial/1 new) ->
  r4 `a4b2e45b` READY WITH ADVISORIES, no new findings. All findings dispositioned in the plan's
  review lineage section.
- **Slice A (behavior-preserving seams, no migration) is landed on `dev`**: category registry +
  injectable ladder veto, optional `ParsedListing.category_hint`, resolver category dispatch
  (hard-coded drive slug removed), a provider run-evidence contract with a completeness gate, a
  `CollectionProvider` seam, and a rule that truncated/partial/failed provider runs cannot prove
  delisting. A post-closeout fix (`27b0c1d`) makes `counts_toward_sweep_continuity` require both
  complete evidence and a complete scope for remote runs to extend continuity. Final battery at
  `27b0c1d`: 652 passed / 1 expected skip / 95% coverage, fmt/lint/type/pip-audit clean,
  makemigrations --check clean, A0 drive-decision snapshot oracle green.
- **Plan rev 4 adds a mandatory Slice D entry gate:** a focused design review of the D/E
  asynchronous-ordering design (watermarks, per-scope continuity, per-observation retention,
  charge horizon, risk R23) against then-current code before task D2; D1/D3 not gated.
- **Next slice is B (unblocked):** GPU/RAM/CPU typed spec satellites, category rows, alias policy
  MS2-D-21, refdata importer generalization, corpus category-hint round trip (B6). Catalog
  migrations 0018/0019 assigned (head remains 0017; nothing migrated). Production unchanged `f3303b1`.
- **Owner gates still open:** OQ23 (Apify base fee vs $20/month ceiling), OQ24 (Actor-proof source
  selection — Newegg excluded on ToU evidence; bounded-retention sources like eBay 6h TTL also
  need a verified per-run Apify storage expiry before an Actor path, per plan finding R20), the
  apify-actors repo's own admission gate, an owner-configured Apify usage-limit backstop, MS-1e
  drive ratification, and a category corpus gate before GPU/RAM/CPU auto-accept.
- Contributed 5 reference pages to `llm-wiki` (`443b921`): Apify cost model, apify-client 3.2.0,
  eBay Browse completeness, Newegg ToU, CPU/GPU/RAM reference sources. Work belongs on `dev`;
  protected `main` advances through pull requests.
- Project Standards Catalog 5 is pinned to 5.29.0 (Agent Handoff 1.17, contract 1.0, dual automatic
  Claude/Codex profile). Upstream issue #80 (handoff injection under `uv-strict-python`) is resolved
  and re-verified 2026-09-06 under this pin.
