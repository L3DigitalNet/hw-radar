# Project Status

## Current snapshot

- MS-0 and MS-1a through MS-1d are implemented and merged: Django/TimescaleDB foundation, ingestion
  substrate, matching, catalog seed, five connectors, and availability heartbeat.
- **Production deployed `478baf0`** (PR #38, Deploy run 36248125289, 2026-09-26 ~14:33Z):
  **matcher `2026.09.2`** is live (5 Codex review rounds + in-house verifier); the retirement
  migration and CPU refdata seed are in production. Gate @`d5bc7c3` (dev): 2591 passed / 3 opt-in
  skips, 95% cov; synthetic Actor 75 passed, 99% cov.
- **OQ31 (owner, 2026-09-26):** ServerPartDeals and Seagate-recertified are retired — permission-
  required, any venue. SA-004's global enable order is replaced by a per-`(source, category)`
  admission matrix (`admission.py`; every live cell `NOT_ADMITTED`; `docs/handoff/deployed.md`).
- All marketplace sources ship disabled; scoring and alerting are not implemented.
- **Strategy re-baselined 2026-09-24:** [ADR 0021](adr/adr-0021-hybrid-acquisition-apify.md)
  adopts hybrid acquisition (retain cheap direct/local collectors; use self-owned private Apify
  Actors selectively; third-party Actors by measured exception) with a hard **$20/month**
  Hardware Radar Apify ceiling and a $12/month initial operating target. [ADR 0022](adr/adr-0022-multi-category-watch-first-v1.md)
  broadens v1 to HDD/SSD + GPU/accelerator + RAM + CPU first-class categories.
- **MS-1e expanded corpus: provisional FAIL** on draft labels (packet
  `docs/evidence/2026-09-26-ms1e-expanded-audit-packet.md`). **OQ32 (owner, 2026-09-26):** the
  five-source floor is replaced by a metadata-declared >=3-source floor (eBay/WD/goHardDrive),
  evaluated against production refdata. Owner audit (Q1-Q8) + ADR-0019 decision remain.
- **CPU corpus packet** (`docs/evidence/2026-09-26-cpu-epyc-audit-packet.md`): would-accept
  104/99 = 95.19%, `auto_accept` False pending a 60-id owner audit and the CPU gate decision.
- **MS-2 plan** (`docs/superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md`) Slices A-F
  are code-complete and deployed; review lineage in `docs/handoff/specs-plans.md`. Production
  stays fail-closed for Apify: no `APIFY` variable is rendered, no ledger authority or run exists.
- **F5a executed 2026-09-25 (non-production proof env):** build `1.0.1`, eleven admitted runs over
  every fault mode, 0 delistings, live Actor<->local identity switch held, +$0.00527 account
  usage. Evidence: `docs/evidence/2026-09-25-f5a-synthetic-proof.md`. Proof env still holds the
  2026-09-05 cycle's ledger authority; drains after its correction monitoring closes 2026-10-02
  ~21:09Z (see `docs/handoff/state.md`).
- **Delist rule amendment (ADR 0020, 2026-09-26):** no eBay scope (legacy drive or category) may
  stale-delist unless the sweep is provably complete.
- **Admission matrix updated 2026-09-26:** eBay x drive FAILs on the expanded-corpus draft labels
  (369/271); eBay x CPU has seeded refdata and a 95.19% would-accept rate, owner audit pending.
- **Owner decisions:** session 2 (2026-09-24) — HR Actors live in this repo under `actors/`;
  OQ23 (cash ceiling ~$20 incl. $19 Starter fee, HR <=$12, no PAYG). 2026-09-25 — OQ25-OQ29
  (scoped runtime token; $5.00/cycle external liability; MS-2 exits on F5a; $1.00/cycle operator
  allowance); R38 accepted; OQ30 (no runtime account reads); F-01 approved (`MAX_KV_WRITES` 3).
  2026-09-26 — OQ31/OQ32 (retirement + admission matrix + ratification floor); ADR 0020 amendment
  (delist completeness); matcher `2026.09.2` implemented (5 Codex rounds); release `478baf0`
  deployed with prod CPU refdata seed. Open: MS-1e owner audit, CPU owner audit + gate decision.
  See `docs/resolved-questions.md`.
- Verified account state (2026-09-25, operator key, outside the app): Apify STARTER, prepaid credit
  $19, usage limit $19, base price $19, cycle anchor 2026-09-05T00:00Z, retention 31 d; no settings changed.
- Project Standards Catalog 5 pinned to 5.29.0 (Agent Handoff 1.17).
