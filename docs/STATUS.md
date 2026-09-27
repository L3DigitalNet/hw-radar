# Project Status

## Current snapshot

- MS-0 and MS-1a through MS-1d are implemented and merged: Django/TimescaleDB foundation, ingestion
  substrate, matching, catalog seed, five connectors, and availability heartbeat.
- **Production deployed `646809d`** (PR #41, 2026-09-27; `5f2d300`/PR #40 before it): matcher
  `2026.09.4`, evaluator `ms2c.3`, migration `0024`. Gate @`a78ebd2`: 3249 passed / 2 opt-in
  skips, 96% cov; synthetic Actor 75, 99% cov. PR #41 CI green.
- **First live cell, 2026-09-27: eBay x CPU admitted and eBay enabled** (six EPYC scopes, 600 s).
  **F6 proven**: live eBay listings -> EPYC identity -> vendor lock -> eligibility -> shortlist
  (17 match / 51 no_match / 219 unknown). Evidence: `docs/evidence/2026-09-27-ebay-cpu-sa004-f6.md`.
- **OQ31 (owner, 2026-09-26):** ServerPartDeals and Seagate-recertified are retired — permission-
  required, any venue. SA-004's global enable order is replaced by a per-`(source, category)`
  admission matrix (`admission.py`; only eBay x CPU `ADMITTED`; `docs/handoff/deployed.md`).
- Only eBay is enabled (eBay x CPU pilot); every other source is disabled. Scoring and alerting
  are not implemented.
- **Strategy re-baselined 2026-09-24:** [ADR 0021](adr/adr-0021-hybrid-acquisition-apify.md)
  adopts hybrid acquisition (retain cheap direct/local collectors; use self-owned private Apify
  Actors selectively; third-party Actors by measured exception) with a hard **$20/month**
  Hardware Radar Apify ceiling and a $12/month initial operating target. [ADR 0022](adr/adr-0022-multi-category-watch-first-v1.md)
  broadens v1 to HDD/SSD + GPU/accelerator + RAM + CPU first-class categories.
- **Drive matcher ratified (s9, 2026-09-26): ADR 0019 accepted.** OQ33 relabels 8 legacy-family
  rows (historical family wins); composite PASS in one full run: 366/366 = 100% (FP 0, FN 204),
  floors ebay 132 / wd 22 / ghd 9, audit PASS, rung-0 PASS. The corpus is now a CI gate.
- **CPU (s9): AMD EPYC is the only ratified CPU family** (OQ34; matcher `2026.09.4`, live). EPYC
  auto-accepts, 105/105 under production rules; Xeon and other families review `family_not_ratified`.
  Vendor lock is listing evidence; watches can require unlocked (migration `0024`; unknown never passes).
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
- **Admission matrix:** eBay x CPU `ADMITTED` 2026-09-27 after its live checklist; every other
  cell `NOT_ADMITTED`, each waiting on its own checklist and an owner decision.
- **Post-Session-9 owner direction (2026-09-27):** admit no drive cell. Keep eBay x CPU unchanged
  for 3–5 days from 01:09Z, then review pilot health before any widening; evaluate eBay x drive first.
- Drive-matcher ratification does not admit a source/category cell. WD and goHardDrive follow eBay x
  drive only after fresh policy verification and their independent remaining gates.
- Owner requested release/deployment of the decision documentation on 2026-09-27; pilot settings stay unchanged.
- **Owner decisions:** session 2 (2026-09-24) — HR Actors live in this repo under `actors/`;
  OQ23 (cash ceiling ~$20 incl. $19 Starter fee, HR <=$12, no PAYG). 2026-09-25 — OQ25-OQ29
  (scoped runtime token; $5.00/cycle external liability; MS-2 exits on F5a; $1.00/cycle operator
  allowance); R38 accepted; OQ30 (no runtime account reads); F-01 approved (`MAX_KV_WRITES` 3).
  2026-09-26 — OQ31/OQ32 (retirement + admission matrix + ratification floor); ADR 0020 amendment
  (delist completeness); matcher `2026.09.2` implemented (5 Codex rounds); release `478baf0`
  deployed with prod CPU refdata seed; s8 MS-1e Q1-Q8 + CPU audit rulings applied
  (matcher `2026.09.3`); s9 OQ33 (historical family identity) + OQ34 (EPYC-only CPU ratification).
  No question is open.
  See `docs/resolved-questions.md`.
- Verified account state (2026-09-25, operator key, outside the app): Apify STARTER, prepaid credit
  $19, usage limit $19, base price $19, cycle anchor 2026-09-05T00:00Z, retention 31 d; no settings changed.
- Project Standards Catalog 5 pinned to 5.29.0 (Agent Handoff 1.17).
