---
schema_version: '1.1'
id: 'adr-0020-hw-radar-per-lane-scheduling-state'
title: 'ADR 0020: Per-lane scheduling state — one state row per (source, lane)'
description: 'Move the mutable cadence state (current_interval_s, clean_polls, backoff_until) off SourceConfig into a SourceLaneState row keyed (source_config, lane), so the ADR-0015 fast heartbeat lane and the CR-006 slow repair lane of one source each own their auto-ramp progress and back-off window, while identity, cadence policy, and source-wide health stay shared on SourceConfig.'
doc_type: 'adr'
status: 'active'
created: '2026-08-16'
updated: '2026-08-16'
reviewed: null
owner: ''
consumer: 'mix'
tags:
  - 'adr'
  - 'scheduling'
  - 'polling'
  - 'reliability'
  - 'data-model'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/adr/adr-0012-orchestration-apscheduler.md'
  - 'docs/adr/adr-0015-availability-heartbeat-grain-volatility-scheduling.md'
  - 'docs/adr/adr-0017-resilient-acquisition.md'
  - 'docs/specs/hw-radar-master-spec.md'
supersedes: []
superseded_by: null
source: []
confidence: 'high'
visibility: 'public'
project:
  decision_makers:
    - 'chris'
  consulted: []
  informed: []
---

# ADR 0020: Per-lane scheduling state — one state row per (source, lane)

MADR status: **accepted**.

## Context and Problem Statement

[ADR 0015](adr-0015-availability-heartbeat-grain-volatility-scheduling.md) gives a heartbeat-enabled source **two** scheduled jobs, and the CR-006 residual made that explicit: a fast `poll-heartbeat-{key}` probe and a slow `poll-{key}` full-pipeline repair crawl, at deliberately different cadences. Both jobs, however, drove the **same three columns** on `source_config` — `current_interval_s`, `clean_polls`, `backoff_until` — because `apply_run_outcome` was written when a source meant one job.

The result is cross-lane corruption in both directions. A repair-crawl transient failure reset `current_interval_s` to `cadence_baseline_s` and opened a back-off window, which the **heartbeat** job then read at its next tick: the fast lane lost the cadence it had earned through four clean polls and was gated by a failure it never saw. Symmetrically, a heartbeat auto-ramp rewrote the interval the repair crawl was rescheduled against, and heartbeat probe failures suspended the repair crawl that would have detected the change the probe missed.

This is a **go-live gate**, not a tidiness issue: enabling any fast-lane source (FR-002: drop-prone ∩ verified cheap signal) with shared state means the freshness SLO the fast lane exists to meet is silently voided by unrelated slow-lane noise.

## Considered Options

- **Option 1 — Keep one row, add lane-suffixed columns** (`heartbeat_current_interval_s`, `heartbeat_clean_polls`, …) on `source_config`.
- **Option 2 — A per-lane state row keyed (source_config, lane)**, with policy and source-wide health left on `source_config`. (chosen)
- **Option 3 — One SourceConfig row per lane** (the heartbeat lane becomes its own registry row).

## Decision Outcome

Chosen option: **Option 2.** A `SourceLaneState` row per `(source_config, lane)` — unique-constrained — holds `current_interval_s`, `clean_polls`, and `backoff_until`. `SchedulingLane` has two members, `full` and `heartbeat`, whose values deliberately mirror the `RunKind` vocabulary: `RunKind.FULL` and `RunKind.PROBE` runs belong to the full lane (a recovery probe replays the full pipeline), `RunKind.HEARTBEAT` runs to the heartbeat lane. Rows exist for both lanes of every source; the heartbeat row of a non-heartbeat source is inert until the flag is flipped, and `SourceConfig.lane_state()` creates a missing row on read at `cadence_baseline_s` so flipping `heartbeat_enabled` never needs a migration.

`apply_run_outcome` takes the lane row the run belongs to and writes cadence state **only** there; `check_admission` is given that lane's `backoff_until`. Both rows move inside one transaction, so a source is never backed off on paper while ramping in the scheduler. Probe bypass semantics are unchanged — a probe still ignores the (now full-lane) back-off window.

**What stays source-level, and why.** `cadence_baseline_s` and `cadence_ceiling_s` are **policy**, one negotiated contract with the site, so both lanes share them. `lifecycle_state`, `consecutive_failures`, and `consecutive_parser_rot` are the **health** axis: both lanes poll the same site, so a parser rot or soft block seen on either is evidence about the source, and the ADR-0017 escalation to `paused_pending_fix` must count failures across the whole source — split counters would let each lane sit just under the threshold indefinitely. `backing_off` therefore remains a source-level *observation* rather than a gate: nothing in the admission order keys on it (only `SKIP`, `paused_pending_fix`, and `backoff_until` deny), so keeping it shared costs no lane isolation while keeping one honest health signal for operators and alerting. `paused_pending_fix` and `SKIP` are unambiguously source conditions — they mean "our code is broken" or "this site is off limits", never "this lane is."

**A heartbeat-lane failure therefore does affect the slow lane — but only on the health axis, never the cadence axis.** It advances the shared failure counters (so escalation and alerting still work) while leaving the repair crawl's interval, clean-poll count, and back-off window untouched. That is the precise boundary this ADR draws.

**The repair lane does not auto-ramp.** For a heartbeat-enabled source, the full lane's ramp floor is `cadence_baseline_s` rather than `cadence_ceiling_s`, which makes AW-004's halving a no-op. CR-006 put the repair crawl at the slow end on purpose — the cheap probe already covers freshness, and a ramping repair crawl would converge on the fast cadence and double the load we spent ADR-0015 avoiding. Single-lane sources are unaffected: their full lane floors at `cadence_ceiling_s` and ramps exactly as before.

Option 1 was rejected: lane-suffixed columns re-encode the lane in field names, so every reader has to branch on `heartbeat_enabled` to pick a column, and a third lane means another migration and another branch. Option 3 was rejected as worse: it duplicates identity, policy, buckets, and the FR-002 eligibility CHECK per lane, and it breaks the "one registry row per source" contract that `source_site → config` (a `OneToOneField`), admission bucket keys, and the ADR-0017 lifecycle all rely on.

### Consequences

- **Good** — lane isolation is structural, not disciplinary: a writer physically cannot touch the other lane's cadence, because it holds a different row.
- **Good** — behavior for single-lane (non-heartbeat) sources is unchanged, so the split carries no risk for the sources running today.
- **Good** — a third lane later (say a nightly deep crawl) is a new enum member and rows, not a schema change.
- **Bad (accepted)** — one more row per source per lane, and every scheduling read is now a two-step (config, then lane row). Mitigated by resolving lane rows once per job tick and by `load_schedules()` doing it on a worker thread, keeping `build_scheduler` ORM-free (it runs inside the poller's event loop, where a lazy query would raise `SynchronousOnlyOperation`).
- **Bad (accepted)** — back-off *depth* is still shared through `consecutive_failures`: a lane that fails right after the other one did starts deeper in the ladder. Deliberate — that is the health axis working as intended — but it means the two lanes are not fully independent circuits, only independent cadences.

### Confirmation

Migration `0013_source_lane_state` backfills both lanes from the pre-split columns before dropping them, seeding each lane at the cadence its job actually ran at (`cadence_baseline_s` for a heartbeat source's repair crawl, the shared `current_interval_s` for its probe) and copying any pending back-off window to **both** lanes, since the owning lane is not recoverable from the old shape and holding a source back is safer than hammering one that just returned 429. The reverse is best-effort and lossy: it collapses the lane that drove the source's cadence back into the columns, takes the later of the two back-off windows, and discards the other lane's ramp progress.

`tests/db/test_lane_state.py` pins the gate: a slow-lane failure leaves the heartbeat lane's interval, clean-poll count, and (absent) back-off window untouched; a heartbeat-lane failure does the same to the repair lane; a non-heartbeat source still halves its interval on the fourth clean poll; and the repair lane of a heartbeat source never ramps below baseline.

## More Information

- **Refines** [ADR 0015](adr-0015-availability-heartbeat-grain-volatility-scheduling.md) (the fast/slow lane split it introduced) and [ADR 0017](adr-0017-resilient-acquisition.md) (whose lifecycle and failure counters stay source-level here); scheduled by [ADR 0012](adr-0012-orchestration-apscheduler.md).
- **Registry-as-settings-rows** framing follows [ADR 0016](adr-0016-search-api-self-governance.md): the numbers in both tables stay OQ9-provisional tunables changed by UPDATE, not deploy.
