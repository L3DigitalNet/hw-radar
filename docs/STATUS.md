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
- MS-1e: the bounded live harvest and draft labels are done (2026-09-25; eBay 29, WD 75,
  goHardDrive 21; ServerPartDeals and Seagate not harvested because their terms prohibit automated
  access). Provisional gate: FAIL (17 of 100 required auto-accepts). The owner audit, OQ32 (the
  five-source floor), and the ADR-0019 decision remain. Packet:
  `docs/evidence/2026-09-25-ms1e-audit-packet.md`.
- **MS-2 plan** (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`) is at
  revision 12 plus landing notes; review lineage in `docs/handoff/specs-plans.md`.
- **Slices A–C:** seams, first-class GPU/RAM/CPU categories (0018-0019, first-party seeds CPU 9 /
  GPU 8 / RAM 2), eligibility + shortlist read model (0020, `EVALUATOR_VERSION` `ms2c.2`).
  **Deployed 2026-09-25** (bug 001 fixed); evidence in `docs/handoff/deployed.md`.
- **Slices D and E deployed 2026-09-25** (`508b1f0`, PR #35, run 36194557063; migrations
  `0021`/`0022`): provider runs, durable staged import, storage cleanup, and the synthetic Actor
  project (D, verifier 10/11 with R21 exceptions), plus the spend ledger, budget policy, latches,
  correction monitoring, ledger authority/handoff, and `apify_spend_report` (E, verifier 16/16).
  Plan rev 12 (OQ30): the runtime reads no Apify account state. Production stays fail-closed for
  Apify: no Apify variable is rendered, and there is no ledger authority, reservation, or run.
- **F5a executed 2026-09-25 (non-production proof env; production untouched):** build `1.0.1` (REST,
  15 files) + eleven admitted runs over every fault mode, all classified as designed; 0 delistings;
  live duplicate import 0 HTTP calls; live Actor↔local switch kept identity/history (AC-4/5 live);
  Apify 400 on invalid input; 403-vs-404 probe passed. Usage not final at finish (keep `bound`);
  +$0.00527 account usage for the whole proof. Evidence: `docs/evidence/2026-09-25-f5a-synthetic-proof.md`.
- **F-01 (owner-approved):** `HW_RADAR_APIFY_MAX_KV_WRITES` is 3; admission denies values below the
  two-write floor (`f8b8067`).
- **F1–F4 deployed 2026-09-25 (`c2adae0`, PR #36; sources off, so nothing runs):** eBay GPU/RAM/CPU category sweeps with per-scope
  absence; only single-page sweeps prove complete; category IDs and the 5,000/day quota re-verified
  live. F2: no change (ServerPartDeals' terms prohibit it). `pilot_report` added; the scratch-DB pilot
  found 0% condition coverage, unseeded category catalogs, and two connector defects (fixed). F4:
  1,888 unlabeled eBay entries in git-ignored staging. Evidence: `docs/evidence/2026-09-25-f1-f3-pilot.md`.
- **Production still cannot start or pay for an Actor run:** no Actor id, prices, account settings,
  ledger authority, or token rendered; kill switch off. The proof environment keeps the cycle's
  ledger authority until its correction monitoring closes (2026-10-02 ~21:09Z) and a handoff runs.
- Gate @34e1071 (dev, 2026-09-25): 2072 passed/1 skip, 95% cov; Actor 75 passed, 99%; pip-audit clean.
- **Owner decisions:** session 2 (2026-09-24) — HR Actors live in this repo under `actors/`;
  OQ23 (cash ceiling ~$20 incl. $19 Starter fee, HR <=$12, no PAYG). 2026-09-25 — OQ25–OQ29
  (unscoped operator key at `secret/apps/hw-radar/agent/apify`; scoped runtime token created;
  $5.00/cycle external liability; no non-first-party retention class; MS-2 exits on F5a;
  $1.00/cycle operator allowance); R38 accepted; OQ30 (no runtime account reads); F-01 approved.
  Open: OQ24 (no candidate `eligible`), OQ31 (connectors whose terms prohibit automated access),
  OQ32 (MS-1e floor), MS-1e ratification, category corpus gate. See `docs/resolved-questions.md`.
- Verified account state (2026-09-25, operator key, outside the app): Apify STARTER, prepaid credit
  $19, usage limit $19, base price $19, cycle anchor 2026-09-05T00:00Z, retention 31 d; no settings changed.
- Project Standards Catalog 5 pinned to 5.29.0 (Agent Handoff 1.17).
