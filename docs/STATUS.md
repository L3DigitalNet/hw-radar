# Project Status

## Current snapshot

- MS-0 and MS-1a through MS-1d are implemented and merged: Django/TimescaleDB foundation, ingestion
  substrate, matching, catalog seed, five connectors, and availability heartbeat.
- All marketplace sources ship disabled; scoring and alerting are not implemented.
- **Strategy re-baselined 2026-09-24:** [ADR 0021](adr/adr-0021-hybrid-acquisition-apify.md)
  adopts hybrid acquisition (retain cheap direct/local collectors; use self-owned private Apify
  Actors selectively; third-party Actors by measured exception) with a hard **$20/month**
  Hardware Radar Apify ceiling and a $12/month initial operating target. [ADR 0022](adr/adr-0022-multi-category-watch-first-v1.md)
  broadens v1 to HDD/SSD + GPU/accelerator + RAM + CPU first-class categories and makes
  requirement matching / watches / shortlist / alerting the launch-critical workflow.
- Bounded-retention expiry, eBay delete-on-delist (CR-004), and the per-lane scheduling-state
  split are deployed (migrations 0014-0017, 2026-09-06). Source go-live is gated by SA-004 + MS-1e.
- MS-2 scoring design rev 14 and the MS-2a scoring-substrate plan rev 4 are the accepted advanced
  drive-scoring artifacts; **execution is deferred by ADR 0022**.
- MS-1e validation-corpus tooling is deployed (PR #20); the live harvest, owner audit,
  ratification, and ADR-0019 flip remain the owner-in-the-loop step.
- **MS-2 plan** (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`) is at
  revision 11 plus landing notes; review lineage in `docs/handoff/specs-plans.md`.
- **Slices A–C:** seams, first-class GPU/RAM/CPU categories (0018-0019, first-party seeds CPU 9 /
  GPU 8 / RAM 2), eligibility + shortlist read model (0020, `EVALUATOR_VERSION` `ms2c.2`).
  **Deployed 2026-09-25** (now `531916e`, bug 001 fixed); evidence in `docs/handoff/deployed.md`.
- **Slice D (core) complete on `dev` 2026-09-25 (`4ba8dce`, not deployed):** synthetic Actor
  project (`actors/hw-radar-synthetic-collector`, wire-byte cap), schema `0021` (undeployed),
  byte-capped client, import provider, durable staged import (transactional local path), start
  job + `apify-poll` selectors, AC-4/5/6 fixture proofs, storage cleanup, provider-dispatched
  probes. Verifier: 10/11 acceptance bullets hold, the storage-deadline bullet with the plan's
  accepted exceptions (R21).
- **Slice E complete on `dev` 2026-09-25 (`40b7295`, not deployed):** spend ledger (`0022`,
  undeployed), DB-free budget policy, advisory-lock ledger service (cycle discovery, MS2-D-45
  claim/handoff, operator reservations), reconcile + overrun latch + correction monitoring,
  `apify_spend_report` (AC-7), `LedgerAdmission` bound in production, `budget_paused` in the
  shortlist, ledger-settled probe (E8). Verifier: 16/16 acceptance claims hold; its two crash
  windows are fixed (`40b7295`). FULL starts are refused while a same-scope run is outstanding.
- **MS-2 plan revision 12 (R25, OQ30, MS2-D-48) on `dev`:** the runtime reads no Apify account
  state; the cycle comes from `HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR` and four operator-verified
  account settings; a 402 start trips `account_limit_refused`. Synthetic setup/smoke/local-switch
  commands landed (`10062f8`, `7bc1f76`).
- **F5a executed 2026-09-25 (non-production proof env; production untouched):** build `1.0.1` (REST,
  15 files) + eleven admitted runs over every fault mode, all classified as designed; 0 delistings;
  live duplicate import 0 HTTP calls; live Actor↔local switch kept identity/history (AC-4/5 live);
  Apify 400 on invalid input; 403-vs-404 probe passed. Usage not final at finish (keep `bound`);
  +$0.00527 account usage for the whole proof. Evidence: `docs/evidence/2026-09-25-f5a-synthetic-proof.md`.
- **Production still cannot start or pay for an Actor run:** migrations `0021`/`0022` undeployed; no
  Actor id, prices, account settings, ledger authority, or token rendered there; kill switch off.
- Gate @7bc1f76 leg: 2016 passed/1 skip, 95% cov; Actor 75 passed, 99%; pip-audit clean.
- **Owner decisions:** session 2 (2026-09-24) — HR Actors live in this repo under `actors/`;
  OQ23 (cash ceiling ~$20 incl. $19 Starter fee, HR <=$12, no PAYG). 2026-09-25 — OQ25–OQ29
  (unscoped operator key at `secret/apps/hw-radar/agent/apify`; scoped runtime token created;
  $5.00/cycle external liability; no non-first-party retention class; MS-2 exits on F5a;
  $1.00/cycle operator allowance); R38 accepted; OQ30 (no runtime account reads). Open: OQ24, MS-1e ratification, category
  corpus gate. See `docs/resolved-questions.md`.
- Verified account state (2026-09-25, operator key, outside the app): Apify STARTER, prepaid credit
  $19, usage limit $19, base price $19, cycle anchor 2026-09-05T00:00Z, retention 31 d; no settings changed.
- Project Standards Catalog 5 pinned to 5.29.0 (Agent Handoff 1.17).
