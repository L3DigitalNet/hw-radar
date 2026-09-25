# F5a synthetic Actor proof — live evidence (2026-09-25)

Evidence record for MS-2 plan task F5a (MS2-D-42, -43, -48) and the owner's 18 *Synthetic Actor proof
acceptance* items. Dated snapshot; findings are reconciled forward into the plan, STATUS, and TODO.
Provider identifiers (Actor id, run ids, account name) are configuration and are deliberately not recorded
in this public repository; `provider_run` numbers below refer to the proof environment's database.

## Environment and configuration

- **Proof environment:** non-production workstation environment, its own database (migrated through `0022`),
  settings in an untracked env file outside the repository; production untouched (`531916e`, migration `0020`).
- **Credentials:** runtime token = owner-created scoped token (OpenBao `secret/apps/hw-radar/apify`), injected per
  command; operator key (`secret/apps/hw-radar/agent/apify`) used only outside the application for create,
  build, probe, and operator reads. The operator key was never placed in the application environment.
- **Kill switch:** the env file keeps `HW_RADAR_APIFY_ENABLED=false`; `true` was passed only to individual
  reservation / `apify_smoke` commands. No scheduler ran; the synthetic `SourceConfig` stayed `enabled=False`.
- **Account (operator read, 2026-09-25):** plan STARTER; prepaid usage credit $19; account usage limit $19
  (backstop at the prepaid credit, no pay-as-you-go exposure); base price $19; cycle 2026-09-05T00:00Z →
  2026-10-04T23:59:59.999Z; `dataRetentionDays` 31. Account usage before the proof: $0.0901.
- **Unit prices** (apify.com/pricing, Starter column, retrieved 2026-09-25; Starter = Free, Scale/Business
  cheaper): $0.20/CU; dataset reads $0.0004/1,000; dataset writes $0.005/1,000; KV reads $0.005/1,000;
  KV writes $0.05/1,000; dataset and KV timed storage $0.001/GB-hour ($1.00/1,000 GB-hours); transfer
  $0.20/GB (external; internal $0.05, the higher applied direction-agnostically).
- **Estimator settings (owner-authorized):** margin 0.25; max timeout 300 s; max KV writes 2; max KV bytes
  65,536; storage max lifetime 2,678,400 s (31 d). Owner decisions unchanged: external liability $5.00,
  operator allowance $1.00, cycle target $12.00, cash ceiling $20.00.
- **Account settings (MS2-D-48):** anchor 2026-09-05T00:00:00Z; account limit $19.00; base price $19.00;
  retention 31 d; verified on 2026-09-25.
- **Ledger:** `apify_ledger_claim --origin` in the proof environment (ledger id = the environment's name);
  project allocation $11.00, `P` $17.10, external-liability headroom $12.10.

## Scoped runtime token

| Capability | Expected | Observed | Result |
| --- | --- | --- | --- |
| Authenticate | valid | 403 (not 401) on out-of-scope calls | ✅ |
| Account limits / usage / user | not needed (MS2-D-48) | 403 `insufficient-permissions` | ✅ fail-closed by design |
| Own Actor, its runs, its build | 200 | 200 (after the owner fixed a trailing `)` in the resource id) | ✅ |
| Another Actor on the account | denied | 404 `record-or-token-not-found` | ✅ |
| List Actors / runs / datasets / KV stores | denied | 403 | ✅ |
| Start the Actor, poll, read default dataset and `OUTPUT` | allowed | 11 runs started, polled, imported | ✅ |
| Delete the run's default storages | allowed | every run's storages deleted by the cleanup selector | ✅ |
| Delete inaccessible storage (throwaway unnamed dataset, operator `probe` reservation) | 403 | 403 `insufficient-permissions`; dataset intact; operator delete 204, then 404 | ✅ → `DELETE_404_IS_ABSENT=true` in the proof env (MS2-D-25 rule met) |
| Delete nonexistent storage | 404 | 404 `record-not-found` (dataset and KV) | ✅ |

Console labels observed in the owner's token editor (2026-09-25): per-Actor permissions `Read`, `Run`,
`List runs`, `List webhooks`, `Manage runs`; *Running Actors*: `Restricted access`, "Allow this token to access
default run storages", and "Allow this token to run full-permission Actors" (turned off; not in the docs'
screenshots).

## Actor deployment

- The private Actor resource was created empty through the REST API (limited permissions, no source) so the
  token could be scoped to it (scoped tokens bind only to existing resources).
- **Push method:** no Apify CLI; the REST API (`PUT …/versions/1.0`, then `POST …/builds`). Uploaded exactly 15
  files from the reviewed `dev` commit `b21471f` (`.actor/`, `contract/`, `src/`, `pyproject.toml`, `uv.lock`,
  `README.md`, `.python-version`); tests, fixtures, `DEPLOYMENTS.md`, and `.actorignore` excluded; secret scan
  clean. This makes the open "does `apify push` scope uploads?" question moot for this deployment.
- **Build** `1.0.1`, tag `candidate`, `useCache=false`: SUCCEEDED in 14.9 s; the intended Dockerfile,
  `uv 0.11.6` `sync --locked` (45 packages); build memory 4,096 MB; 0.01652 CU = **$0.0033** against the
  $0.4231 operator build reservation (settled at its bound, `bound` mode).

## Runs (all through `apify_smoke` → admission → reservation → start → poll → import → cleanup)

Every run: build `1.0.1`, 256 MB, timeout 300 s, `watch_refresh`, reservation estimate $0.0348 + monitoring
$0.0022; fixture commit `b21471f`; storage deleted by the cleanup selector.

| Run | Fault mode | Remote | Classification | Rows (emitted / dataset / imported) | Usage (USD) |
| --- | --- | --- | --- | --- | --- |
| 1 | none | SUCCEEDED | complete | 3 / 3 / 3 | 0.000199 |
| 2 | truncate_items | SUCCEEDED | truncated, `item_limit` | 1 / 1 / 1 | 0.000152 |
| 3 | truncate_pages | SUCCEEDED | truncated, `page_limit` | 2 / 2 / 2 | 0.000169 |
| 4 | truncate_bytes | SUCCEEDED | truncated, `resource_limit` | 2 / 2 / 2 | 0.000149 |
| 5 | truncate_time | SUCCEEDED | truncated, `time_limit` | 2 / 2 / 2 | 0.000170 |
| 6 | partial_failure | SUCCEEDED | partial_failure (`injected_fetch_error`) | 2 / 2 / 2 | 0.000180 |
| 7 | contradictory_report | SUCCEEDED | **failed** (complete with a limit hit) → rejected | 3 / 3 / 0 | 0.000177 |
| 8 | count_mismatch | SUCCEEDED | **failed** (count mismatch) → rejected | 4 / 3 / 0 | 0.000154 |
| 9 | unknown_schema | SUCCEEDED | **failed** (`hw-radar-run/v99`) → rejected | – / 3 / 0 | 0.000159 |
| 10 | fail | FAILED | partial_failure (`remote_failed_with_items`) | 2 / 2 / 2 | 0.000151 |
| 11 | none (provider-switch Actor leg) | SUCCEEDED | complete | 3 / 3 / 3 | 0.000174 |

## Behavioral claims (live)

1. Complete remote evidence participates normally: runs 1 and 11 imported all three listings.
2. Truncated evidence cannot delist, and 3. failed/partial evidence cannot delist: listing 3 appeared only in
   complete runs; after nine truncated/partial/failed runs it was never delisted (0 delistings in total).
4. Contradictory remote evidence fails closed: runs 7–9 were rejected with 0 rows imported.
5. Duplicate import is idempotent: re-importing a finalized run (1) and a rejected run (7) made **zero HTTP
   calls** and changed nothing (snapshots, listings, read counters, import states).
6. An old import cannot overwrite newer state: the live scenario is prevented by construction — a FULL start is
   refused `scope_run_outstanding` while a same-scope run is undecided (`3c117c9`), so two same-scope imports
   cannot race; the D10 ordering tests remain the proof (acceptance item 10 has no live half).
7. Provider switch preserves identity (AC-4 live): Actor → local → Actor → local on the synthetic site kept the
   same three listing ids, source keys, URL hashes, and `first_seen`; 8. price/history continuity: snapshots
   appended on every leg (listing 1: 7 → 8 → 9 → 10), zero delistings. (No watch exists in the proof
   environment; watch-state survival is proven by the D7 fixture test.)
9. Storage cleanup occurs: every run's default dataset and KV store deleted within ~10 s of its import, including
   with the kill switch off (the drain is independent of admission).
10. Run/output identifiers are provider evidence only: eleven provider runs map onto three canonical listings by
    source key.
11. No proxy: no proxy component in any run's `usage`.
12. No unexpected usage component: every run shows only compute, dataset writes, KV reads, KV writes, and
    internal/external transfer.

Also: Apify rejects a schema-invalid input itself — `POST …/runs` with missing required fields returned HTTP 400
`invalid-input` ("Field input.collectionScope is required") and created no run (acceptance item 2, MS2-D-15).

## Measurements

- **Usage finalization (run 11, operator reads relative to `finishedAt`):** +0 s $0.000104 (missing dataset
  writes and the KV read, ~40% low); +10 s $0.00017368; +30 s $0.00017381; +120 s and +300 s $0.00017381
  (stable); after storage deletion $0.00017381 (unchanged). Run 10 (`fail`) was read by the runtime 8 s after
  finish at $0.0000525 and later read $0.000151 (~3×). Runs 1–9 re-read after dataset reads and deletes moved by
  at most ±$0.0000005 (both directions). ⇒ run usage is not final at finish; `…_RUN_USAGE_SETTLEMENT` stays
  `bound` (the `stable_reads` predicate is not relaxed by this evidence).
- **Post-run costs are account-level, not run-level:** no dataset read appears in any run's `usage`. The account's
  daily breakdown for the proof day: dataset reads 23 (= the total items read across the ten runs' datasets,
  so **dataset reads are billed per item returned**, which the estimator already assumes:
  `dataset_reads × (max_items + pages)`); KV reads 19 (10 Actor `INPUT` + 9 hw-radar `OUTPUT`); KV writes 20
  (**2 per run: the platform's `INPUT` write is billed to the run**, plus `OUTPUT`); external transfer 0.00214 GB
  ($0.00043) — about 2 MB, far above the runs' own external transfer, i.e. API calls are billed through
  transfer (R38 residual, now observed rather than assumed); compute 0.01812 CU; storage deletions carry no
  separate charge.
- **Account usage diff:** $0.090078 (before) → $0.095350 (after runs 1–10, the build, and all reads) =
  **+$0.00527** for everything Hardware Radar did; ledger-observed build + run usage ≈ $0.00492, so post-run
  retrieval, API transfer, and storage ≈ $0.00035 (~$0.000035 per run).
- **Estimator vs actual:** per-run reservation $0.0348 (+ $0.0022 monitoring) against ≤ $0.0002 actual — a
  ~100–170× conservative margin, dominated by the compute bound (256 MB × 300 s) and post-run liability.

## Findings for the plan (§31)

- **F-01 (KV writes at the bound):** `…_MAX_KV_WRITES=2` is exactly consumed by the platform `INPUT` write plus
  `OUTPUT`; the intended spare unit for an unexpected write does not exist. Owner decision: raise to 3 before
  production (cost of a third write $0.00005). No proof run exceeded the bound.
- **F-02 (usage not final at finish):** confirms keeping `bound` settlement; any future `stable_reads` enablement
  needs reads ≥ 30 s after finish.
- **F-03 (API calls billed via transfer):** R38 stays accepted (owner, 2026-09-25); the measured magnitude is
  ~2 MB/day of external transfer for this proof's call volume.
- **F-04 (scoped-token UX):** the resource id field accepted a trailing `)` silently; the token then behaved as
  if it had no Actor permission (404/403). Future walkthroughs must present ids outside punctuation.

## Reservations and settlement

Operator: build $0.4231 (reconciled at bound $0.4209 after the one-hour guard), inspections $0.0077 × 4 and probe
$0.0024 (each reconciled at its envelope). Runtime: eleven run reservations at $0.0348 (+ $0.0022 monitoring),
each reconciled at the unmargined execution bound $0.0278 once an eligible read existed (runs 1–10 at 20:31Z,
run 11 at 21:08Z; `bound` mode returns no capacity). Final spend report: settled $0.7599, outstanding $0.0000,
monitoring $0.0264, remaining project budget $10.67 of $11.00, operator allowance remaining $0.5437, no overrun
rows, no latch trips, one denial (`apify_disabled`, the intended kill-switch check). Ledger debits exceed the
provider-measured spend (≈ $0.0052) by about 150×. Correction monitoring stays open for
`…_CORRECTION_WINDOW_S` (7 days); per F5a step 5 the proof environment must drain and hand off before any
production environment admits paid work in this cycle.
