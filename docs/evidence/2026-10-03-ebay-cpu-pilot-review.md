# eBay × CPU pilot review (day 6)

Date: 2026-10-03 UTC (session 10). Scope: the `(ebay, cpu)` cell, live since 2026-09-27 01:09Z, unchanged
since then (release `8f529a8`, matcher `2026.09.4`, evaluator `ms2c.3`, six EPYC scopes, 600 s cadence).
Everything below was read live from production with read-only SQL (`default_transaction_read_only`,
`work_mem` 16 MB) or the production runtime. No private hosts, IPs, or credential values are recorded.
The review window is 2026-09-27 01:09Z to 2026-10-03 10:11Z, about 6.4 days, past the planned 3–5.

## 1. Collection health

| Measure | Result |
| --- | --- |
| Full runs | 874, all `success`; 0 failed, 0 resolver errors, 0 evaluator errors |
| Cadence | Gap between successful runs: min 600 s, median 630 s, max 1040 s (the PR #42 deploy restart, 2026-09-27 11:58Z) |
| Scope proof | All six scopes single-page and `complete` in all 874 runs (`continuity: recorded`) |
| Records per run | 285–300 valid, rising slowly (daily mean 288 → 300); `body_bytes` ≤ 483 KB |
| Browse quota | `buy.browse` 4910/5000 remaining at 10:2xZ (resets 07:00Z); about 6 calls/run, ~820/day |
| Grains | First run 182 none / 101 model / 4 variant; latest 186 / 105 / 8. No `family` grain |
| Shipping known | 99.0% of active listings (67.6% free) |

Delist provenance: every delisting was recorded by a sweep whose scope was `complete` (ADR 0020 holds).

## 2. Churn

Runs recorded 950 delistings, but only 20 listings are delisted now. Listings return after an
absence and the next sweep revives them. Per scope, delistings over the pilot: 7763/164 738,
9354/164 200, 9654/56088 5, 9354/56088 3, 7742/56088 2, 7763/56088 2. Category 164 churns daily
(7763/164: 90–147 per day).

In the retained 6 h snapshot window (36 runs), 291 of 301 listings were present in every run. The
intermittent ones mostly show one long absence, then continuous presence (for example `.......####`),
not alternation. One listing (7763/164) appears in 2 runs out of 36. A single absence from a complete
scope delists immediately, so each return is a relist.

Implication: completeness proves the sweep got everything eBay returned at that moment, not that eBay's
index is stable. For the buyer flow (exactly-one alert), a relist must not count as a new listing, and
a delist should not be read as a sale.

## 3. Watch 1 verdicts (current)

Active listings: **20 `match`**, 50 `no_match`, 229 `unknown` (F6: 17 / 51 / 219). Every match states its
unlock. Matches by model: 7763 ×9 ($1799.99–$2999.95), 9354 ×7 ($1685.00–$2150.00), 9654 ×4
($2398.00–$2780.00). `no_match`: price above $3000 41, stated lock 9.

`watch_evaluation` keeps one current row per listing under the 6 h observation retention class, so
verdict history and shortlist stability over the pilot cannot be reconstructed. The shortlist moved from
17 to 20 matches; when each entry joined or left cannot be measured.

### `unknown` by unresolved clause set

| Clauses `unknown` | Rows |
| --- | --- |
| target + sockets + min_cores + vendor_lock | 94 |
| target + sockets + min_cores | 75 |
| vendor_lock only | 55 |
| target + sockets + min_cores + vendor_lock + price | 2 |
| vendor_lock + price | 2 |
| price only | 1 |

Vendor-lock evidence over active listings: 104 state unlocked, 9 state locked, 186 state nothing.

### Classified samples

Target unresolved (171 rows; 20 sampled):

- Unsupported OEM parts, correctly `none` by policy: 7J13, 7K83, 7T83, 7B13 (the majority).
- **Unseeded legitimate models**: EPYC 9354P and 9654P (single-socket retail SKUs, incl. Dell-branded
  `100-000000805`/`-803`). They need their own seed and audit, never an alias of 9354/9654.
- Not a CPU, or not a usable CPU: a CTO configurator line, a motherboard bundle, a non-working part.

Vendor lock only (55 rows; 15 sampled): titles that state no lock at all (missing marketplace evidence).
Borderline: listing 295 ends in "Used Unlock" at eBay's 80-character limit, the same case as F6 listing
282; it stays `unknown` (fail-closed; whether a bare "Unlock" counts is an owner question). The search
summary carries no item aspects, so a stated lock that exists only in item specifics is invisible.

No extraction defect was demonstrated in the samples; nothing in the matcher or evaluator was changed.

## 4. Incident during this review

Running `pilot_report` in production for this window OOM-killed PostgreSQL in the 4 GiB container
(10:23:04Z). Root causes: the report joined each snapshot to its shared raw page payload (~565 MB through
the join), and PostgreSQL was tuned for the host's RAM. The poller then failed every job on dead
connections until restarted (10:23:54Z). Collection lost about one run; no data was lost. See
[bug 005](../handoff/bugs/005-pilot-report-oom-killed-production-postgres.md).

## 5. Recommendation

The cell is operationally healthy: no failed runs, every scope provably complete, quota at ~16% of the
daily limit. Before widening: seed and audit 9354P/9654P if the watch should include them, decide relist
semantics for alerting, and add verdict history if shortlist stability is to be measured. The owner
decides whether to widen.
