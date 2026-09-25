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
- Bounded-retention expiry, eBay delete-on-delist (CR-004), and per-lane scheduling-state split
  landed `5a7f5b7`/`db62b6f`/`6e68585`. OQ22 resolved via migration 0017 (`8101504`). Migrations
  0014-0017 deployed 2026-09-06 (PR #22). Source go-live gated by SA-004 + MS-1e.
- MS-2 scoring design revision 14 (`a3ec96b`) is owner-accepted (2026-09-06). The MS-2a
  scoring-substrate plan reached revision 4 (`05f130f`) across three Codex `delegate` passes
  (`a626c2f0`/`8755be2a`/`b92dd220`); design and plan are the accepted advanced drive-scoring
  artifacts, but **execution is deferred by ADR 0022** and MS-2a is not the next implementation step.
- MS-1e's validation-corpus harness/harvest tooling is merged and DEPLOYED (PR #20, `1099f766`,
  2026-08-16): Approach-A evaluator, `EvalReport`, `ms1_ratification_gate`, `harvest_corpus`. The
  live harvest/label-draft/audit/ratification/ADR-0019 flip remain the deferred owner-in-the-loop
  step; `tests/db/test_ratification_corpus.py` skips until it lands.
- Full gate @001a5b6: 1231 passed/1 skip, 96% cov; Actor 67 passed, 99%; pip-audit clean (`--skip-editable`).
- **MS-2 multi-category watch-core plan converged at revision 8**
  (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`). Eight-round Codex
  delegate review lineage: r1-r4 converged rev 4 (session 1); r5 `0469e098` REVISION NEEDED (5) ->
  r6 `6af5388b` REVISION NEEDED (3 partial + 2 new) -> r7 `01639c2a` REVISION NEEDED (2 partial +
  1 new) -> r8 `b73b6633` READY, no new findings. All findings dispositioned in the plan.
- **Session 2 owner decisions (2026-09-24):** Hardware Radar owns all its Apify Actors in this
  repo under `actors/<name>/`, managed independently of `apify-actors` (PR #62, doc-only, merged
  by the operator). OQ23 resolved (cash ceiling ~$20 incl. $19 Starter fee, HR <=$12 attributable,
  no PAYG). OQ24 split: synthetic proof first via a real private Actor; merchant-source admission
  separate; Newegg excluded; existing connectors not grandfathered.
- **Slice B complete on `dev`:** GPU/RAM/CPU typed spec satellites (migration 0018), category rows
  (0019), refdata contract + importer, first-party seeds (CPU 9 / GPU 8 / RAM 2), category rules +
  registry + acceptance policy, category-change edge, corpus category-hint round trip (B6).
- **Slice C code complete on `dev`:** eligibility schema (migration 0020), evaluator (C2/C3),
  shortlist/review-queue read model (C4), evaluation wired into every ingestion path (C3 wiring).
  `EVALUATOR_VERSION` is `ms2c.2`; implementation-driven clarifications recorded in the plan.
- **D-prep code complete on `dev`:** D3 collector client; D1 Actor project
  `actors/hw-radar-synthetic-collector` + contract schemas + `classify_run` + CI Actor gates. No
  Apify push/build/run has occurred. Open: `ProviderRunEvidence.truncation_reason` (MS2-D-11) not
  yet added; private-helper coupling in `eligibility/service.py`/`shortlist.py` (drift-tested).
  gate-runner verified. **Deployed 2026-09-25** (`ac8d608` run 36078378772, then `96ce005`): 0018-0020
  applied, sources disabled, no Actor run; evidence in `docs/handoff/deployed.md`.
- Battery @62e670b green: 1226 passed/1 expected skip, 96% coverage, pip-audit clean; Actor
  project 64 passed, 99% coverage. Migrations empty->head and head->0017->head both OK; A0 oracle
  unchanged. Verifiers: Slice B C1-C9, Slice C/D-prep V1-V10 all CONFIRMED. Post-battery hardening
  (complete-report count checks, Actor wall-clock deadline) landed after it; targeted gates green.
- **Owner gates open (session 2):** OQ25 (Apify credential + MCP tool scope), OQ26 (external-
  liability bound; blocks all live paid admission incl. the synthetic proof), OQ27 (retention
  class for non-first-party reference data), OQ28 (can MS-2 exit on the synthetic proof alone),
  OQ29 (operator allowance size, optional). Plus carried-over: MS-1e drive ratification and a
  category corpus gate before GPU/RAM/CPU auto-accept. See `docs/open-questions.md`.
- Verified read-only account state (2026-09-24): Apify plan STARTER, `maxMonthlyUsageUsd` 19
  (prepaid credit), billing cycle 5th 00:00Z -> 4th 23:59:59Z (anniversary). No settings changed.
  Deviation: this used the apify-actors agent namespace's token once, read-only (no HR token yet).
- Project Standards Catalog 5 pinned to 5.29.0 (Agent Handoff 1.17); upstream issue #80 resolved
  and re-verified 2026-09-06 under this pin.
