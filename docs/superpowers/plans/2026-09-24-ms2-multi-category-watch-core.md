# MS-2 — Multi-category watch core: Implementation Plan

> For the executing agent: slices land in order, **one slice = one PR into `dev`**.
> Inside a slice, work top to bottom. Every behavior task is TDD: write the failing
> test first, watch it fail for the stated reason, implement, get it green, and make
> a signed conventional commit.
>
> Design sources, in precedence order:
> 1. Master spec §19 MS-2 (`docs/specs/hw-radar-master-spec.md:921-944`, milestone
>    boundary) and §§7–9 (FR/NFR/IR/DR IDs below).
> 2. [ADR 0021](../../adr/adr-0021-hybrid-acquisition-apify.md) (hybrid acquisition,
>    Apify cost contract).
> 3. [ADR 0022](../../adr/adr-0022-multi-category-watch-first-v1.md) (multi-category,
>    watch-first).
> 4. ADRs 0010, 0012, 0014, 0016 (pattern only), 0017, 0019, 0020.
> 5. Code evidence: [MS-2 code dependency map](../../research/2026-09-24-ms2-code-dependency-map.md)
>    (cited below as *Map Qn*).
>
> Deviations go to the plan's Open risks section and the OQ process, never silently.
> This plan does not edit ADRs or the master spec. (Revision 5's owner decisions were
> recorded in those documents by a separate, owner-directed decision-record change:
> the dated ADR 0021/0022 amendments, the master spec revision 0.19, and the OQ23/OQ24
> moves. The plan cites them; it does not carry their authority.)
>
> **Revision 2 (2026-09-24).** Resolves the 12 findings of cross-agent review
> round 1 (see *Review lineage* at the end), aligns the Slice A text with what
> landed for A0–A3, and folds in the Slice B/D/F prep research. Decision IDs
> MS2-D-01..-19 and task IDs are unchanged; new decisions are MS2-D-20..-28 and
> new tasks carry new IDs (B4a–B4c, B6, D10–D12, E8).
>
> **Revision 3 (2026-09-24).** Resolves cross-agent review round 2: the five
> partially resolved findings (F-03, F-04, F-07, F-08, F-10) and three new ones
> (N-01, N-02, N-03). See the round-2 table in *Review lineage*. Existing
> decision and task IDs are unchanged. New decisions are MS2-D-29..-33. No new
> task IDs are added, and no migration is renumbered: the new schema lands in
> the unmerged migrations `0020`, `0021`, and `0022`. The F-04 residual is a
> Slice A code correction (A4/A6 text below).
>
> **Revision 4 (2026-09-24).** Resolves cross-agent review round 3: the three
> partially resolved findings (F-08, N-01, N-02) and one new one (M-01). All
> four concern out-of-order asynchronous imports and accounting horizons in
> Slices D and E. See the round-3 table in *Review lineage*. Existing decision
> and task IDs are unchanged. New decisions are MS2-D-34..-37. No new task IDs
> and no migration renumbering: the new columns land in the unmerged `0021`
> (Slice D) and `0022` (Slice E). Slice D (core) now has an explicit entry
> gate (see *Slice D entry gate*). Slices B and C are unaffected.
>
> **Revision 5 (owner decisions, s2, 2026-09-24).** Encodes the owner's session-2
> decisions on Apify ownership, cost, billing period, clocks, and the Actor proof.
> Each changed passage is marked **owner-overridden** (the owner reversed a
> rev-4 decision) or **owner-clarified (s2, 2026-09-24)** (the owner settled or
> sharpened something rev 4 left open). Rounds 1–4 and every decision not listed
> here are unchanged. Codex review of revision 5 is pending (see *Review lineage*).
> - **Owner-overridden:** MS2-D-14 *Fixtures* and the contract location (the Actor
>   is built in this repository, not a separate one); MS2-D-17 *Period* (the Apify
>   billing cycle replaces the rolling 31-day window as the authority); MS2-D-26
>   *Ceiling* (the OQ23 deduction is withdrawn; the cash ceiling is enforced
>   structurally); MS2-D-34 (the charge horizon becomes billing-cycle intersection);
>   R2 (withdrawn); R12 (the budget period is no longer plan discretion).
> - **Owner-clarified (s2, 2026-09-24):** MS2-D-15 (finalization and post-run
>   billing facts, nine REST calls); MS2-D-19 (the Actor proof splits into a
>   synthetic proof and a separate merchant admission); MS2-D-22 (overrun blocks
>   repair reads); MS2-D-23 and MS2-D-32 (reservation states `usage_provisional` /
>   `usage_finalized`; post-run allowance); MS2-D-25 (the Actor-review item moves in
>   repo); MS2-D-11 (a DB-free truncation-reason vocabulary beside the unchanged
>   `RunCompleteness`); R1, R3, R7, R19, R20, R23.
> - **New decisions:** MS2-D-38 (in-repo Actor ownership, layout, and gate),
>   MS2-D-39 (three clocks), MS2-D-40 (billing-cycle budget and cash-ceiling
>   model, OQ23), MS2-D-41 (reservation → reconciliation lifecycle), MS2-D-42
>   (synthetic Actor proof source), MS2-D-43 (operator/agent Actor workflow),
>   MS2-D-44 (Actor-backed merchant source admission, OQ24).
> - **Tasks:** D-prep now builds the Actor skeleton under `actors/` (D1) and a
>   nine-call client (D3). E1–E7 gain the billing-cycle table, provisional/finalized
>   usage, and the named §29 budget tests. F5 splits into **F5a** (synthetic proof
>   through a real private Actor) and **F5b** (a production Actor-backed merchant
>   source, owner-gated by OQ24). New risks R24–R32. Migrations stay planning
>   allocations; see *Global constraints*.
> - **Implementation-driven clarifications from Slice B** (not owner decisions):
>   MS2-D-06 (the non-first-party retention gap, owner gate R32); B1/B2 (live
>   migration numbers `0018`/`0019` verified, satellite choices); B *Files* (more
>   allowed test edits: type narrowing in `tests/unit/test_refdata_contracts.py`,
>   and drive-scoping with no expected-value change in the seed-corpus tests of
>   `test_refdata_loader.py`, `test_refdata_import.py`, and
>   `test_refdata_categories.py`);
>   B4a (`non_first_party`); B4c (row counts first-party sources support); B6
>   (landed `a789e98`).
> - **Category tests added for owner §29:** B3 and C3 name the exact-authoritative
>   acceptance, fuzzy/merchant-only, hard-contradiction, and missing-attribute
>   tests explicitly. Slice B's task order is unchanged.
>
> **Revision 6 (review round 5, 2026-09-24).** Resolves Codex round 5 (delegate
> `0469e098`, REVISION NEEDED, findings R5-01..R5-05, all accepted). Every change
> is confined to the Apify budget design; Slices A–C and D's ordering design are
> unchanged. See *Review lineage*, Round 5.
> - **R5-01:** MS2-D-40 debits reconciled Hardware Radar spend on top of the
>   account snapshot until an evidence-backed inclusion watermark exists.
> - **R5-02:** MS2-D-40 adds an owner-supplied external-liability bound for the
>   shared account (owner gate R33; paid admission denied while unset) and
>   withdraws the claim that the 10% margin bounds other workloads.
> - **R5-03:** MS2-D-41 replaces elapsed-time finalization with a stable-read
>   settlement predicate, a default `bound` settlement, append-only usage-read
>   evidence (`ApifyUsageRead`), and upward corrections that can trip the latch.
> - **R5-04:** new MS2-D-45: one ledger authority per cycle, handed off only
>   after the old environment is disabled and fully drained; MS2-D-42's
>   target-reduction handoff is withdrawn.
> - **R5-05:** new MS2-D-46: builds and operator inspection are reserved in the
>   same ledger (`operator` class).
> - Tasks: E settings, E1 (`ApifyUsageRead`, `ApifyLedgerAuthority`, class
>   `operator`, `settlement_basis`), E2–E7 tests, and F5a steps 1, 2, 4, and 5.
>   New risks R33–R36; R26, R27, and R30 updated.
>
> **Revision 7 (review round 6, 2026-09-24).** Resolves Codex round 6 (delegate
> `6af5388b`, REVISION NEEDED: R5-03, R5-04, R5-05 partial; R6-01, R6-02 new;
> all accepted). Mechanisms are the simplest conservative ones. See *Review
> lineage*, Round 6.
> - **R5-03:** MS2-D-23 adds selector 4 (correction monitoring for reconciled,
>   cleaned-up rows until a persisted `correction_monitor_until`); MS2-D-41 sets
>   a correction window; E4's "finalize on the first read after the delay"
>   sentence is removed.
> - **R5-04 + R6-01:** MS2-D-45 refuses handoff while any correction window is
>   open, binds each export irrevocably to one destination ledger id,
>   serializes export with admission and corrections under the budget lock, and
>   makes retry and re-import idempotent.
> - **R5-05:** MS2-D-46 replaces the flat $0.01 inspection bound with a finite
>   operation/byte envelope priced from the unit-price settings; exceeding it
>   needs a new reservation; settlement never falls below it.
> - **R6-02:** new MS2-D-47: an upward correction atomically raises the settled
>   amounts, re-checks every cycle, class, allocation, and account invariant on
>   the charge's billing interval, and trips the latch if any is exceeded.
> - New risk R37; R35 updated. E settings, E1 fields, and E2–E4 tests updated.
>
> **Revision 8 (review round 7, 2026-09-24).** Resolves Codex round 7 (delegate
> `01639c2a`, REVISION NEEDED: R5-03 and R5-04 partial, one root; R7-01 new;
> all accepted). No new decision ID, table, task, or migration number. See
> *Review lineage*, Round 7.
> - **R5-03 + R5-04:** elapsed `correction_monitor_until` no longer closes
>   monitoring. MS2-D-23 selector 4 keeps an expired, unclosed row eligible
>   until a **successful closing read** at or after the deadline commits, with
>   any correction applied under the budget lock in the same transaction
>   (MS2-D-41, -47); a failed or unavailable closing read keeps the obligation
>   open and visibly overdue. MS2-D-45's export requires committed closing-read
>   evidence and no unapplied read; its "no correction after handoff, by
>   construction" claim is withdrawn, and a later correction is only the R37
>   residual.
> - **R7-01:** operator build reservations carry a durable
>   `provider_build_id` and a null `provider_run` (stated explicitly for all
>   operator rows); MS2-D-15 adds the build-record read; MS2-D-23 settles and
>   monitors bound builds; MS2-D-34 derives `last_charge_at` for builds from
>   the build record and for inspections from their settlement; MS2-D-46's
>   `--settle` binds the build id.
> - E1 fields (migration `0022`), E4 and E6 text, the E3/E4 test lists, the D3
>   client test, F5a steps 2 and 5, the *Slice order* E row, R37, and the ADR
>   0021 addendum note are updated.
>
> **Revision 9 (owner decisions, s4, 2026-09-25).** Encodes the owner's
> 2026-09-25 answers to OQ25–OQ29. The authority is the owner's decision as
> recorded, by a separate decision-record change and not by this plan, in
> `resolved-questions.md` ([OQ25](../../resolved-questions.md#oq25--hardware-radar-apify-credential-and-mcp-tool-scope),
> [OQ26](../../resolved-questions.md#oq26--external-liability-bound-for-the-shared-apify-account),
> [OQ27](../../resolved-questions.md#oq27--retention-class-for-non-first-party-reference-data),
> [OQ28](../../resolved-questions.md#oq28--can-ms-2-exit-on-the-synthetic-proof-alone),
> [OQ29](../../resolved-questions.md#oq29--operator-allowance-size)) and in the 2026-09-25 amendment of
> [ADR 0021](../../adr/adr-0021-hybrid-acquisition-apify.md). Each changed
> passage is marked **owner decision (s4, 2026-09-25)**. Review rounds 1–7,
> every decision and task ID, and every migration allocation are unchanged. No
> decision ID, task ID, table, or migration is added. The *Slice D entry gate*
> still precedes D (core).
> - **OQ25 (R24, R25; MS2-D-15, -43):** the owner created a dedicated Hardware
>   Radar Apify key, stored at OpenBao `secret/apps/hw-radar/agent/apify`. A
>   2026-09-25 capability probe showed that it is **unscoped** (full account),
>   because Apify does not let a scoped token create or modify Actors. It
>   therefore serves the **operator/deploy role only** and is never rendered
>   to the production app environment. The **runtime role** still needs a
>   separate, owner-created **scoped** token at `secret/apps/hw-radar/apify`,
>   rendered as `HW_RADAR_APIFY_TOKEN`. Production rendering of that token is
>   deferred until Slice E live admission is ready. Whether a scoped token can
>   read `/users/me/limits` and `/users/me/usage/monthly` stays unverified
>   until the token exists. `.mcp.json` is unchanged, and MS2-D-43's read-only
>   filter is the target, enabled only once a scoped read credential and
>   operator reservations exist.
> - **OQ26 (R33; MS2-D-40 check 2):** `HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD`
>   defaults to the owner-set **5.00** per Apify billing cycle, replacing
>   revision 6's "no default". It is a conservative Hardware Radar accounting
>   bound, not permission for another project to spend $5. Every fail-closed
>   path stays: an explicitly empty or invalid value, a stale or unobservable
>   snapshot, and an unsatisfied invariant each deny paid admission.
> - **OQ27 (R32; MS2-D-06, B4a, B4c):** MS-2 adds no retention class for
>   non-first-party reference data and no retention-CHECK rewrite.
>   Authoritative seeds come only from first-party sources. The importer's
>   refusal of `non_first_party` documents is the MS-2 behavior. B4c's RAM
>   expansion from the three third-party-hosted Micron PDFs is withdrawn, and
>   the existing first-party RAM rows stay.
> - **OQ28 (R31; F5, MS-2 exit):** the synthetic proof (F5a) is sufficient for
>   the Apify portion of MS-2 exit. F5b is not required to close MS-2; it stays
>   gated by OQ24, which may remain open after MS-2 closes.
> - **OQ29 (R36; MS2-D-40, -46):** `HW_RADAR_APIFY_OPERATOR_ALLOWANCE_USD`
>   defaults to the owner-set **1.00** per billing cycle, replacing the 0.50
>   assumption of revisions 5–8. At the default target, the runtime allocation
>   `A` is therefore 11.00.
> - Changed text: the *Global constraints* public-repo bullet; MS2-D-06,
>   -15, -17, -19, -40, -43; B4a, B4c, D3, the E settings, E2, E7, E
>   *Acceptance*, F5, F5a, F5b, and *MS-2 exit*; the traceability rows for
>   Task 6, Task 7, NFR-003, and AC-7 external liability; the *Synthetic Actor
>   proof acceptance* note; the *Slice order* F row; R3, R24, R25, R31–R34,
>   and R36; the paragraph after the risk table; and a note on the round-5
>   R5-02 lineage row.
> - **Owner items still open:** the scoped runtime token (the R25 residual);
>   the R35 attestation at the first origin claim; corpus ratification (R4)
>   and MS-1e drive-matcher ratification (R5), which remains a distinct gate
>   that neither an MS-2 deploy nor the synthetic proof satisfies; and OQ24
>   for any production Actor-backed merchant source (F5b).
>
> **Revision 10 (Slice D entry gate, 2026-09-25).** Records the revision-4
> *Slice D entry gate* review (architect, 2026-09-25, verdict READY WITH PLAN
> AMENDMENTS: 0 critical, 2 high, 8 medium, 10 low, ED-01..ED-20) and applies
> every finding before D2 starts. All 20 are accepted; the full disposition is in
> *Review lineage*, *Slice D entry gate*. The ordering design (MS2-D-30, -31,
> -35, -36, -37) is kept; no decision, task, or migration number is added or
> renumbered. The new columns land in the unmerged `0021` (D) and `0022` (E).
> Revision 9's owner decisions are unchanged, and each passage this revision
> changes is marked **revision 10 (entry gate)**.
> - **ED-01 (high):** the F5a admission gate was circular: live admission
>   waited on E2 verifying poll billing and transfer direction, which the
>   docs cannot settle and only F5a can measure. `GET` polls and account
>   reads are now counted, capped, and priced at a conservative API-call
>   bound, and transfer is priced in both directions at the higher transfer
>   price (MS2-D-15, -26, -32, -41, -46; E2; F5a; R7, R19). Admission is
>   denied only when a required bound is unset or invalid. F5a measures the
>   true values; lowering a bound needs a plan revision.
> - **ED-02 (high):** binding `last_seen` rule and tests for
>   `observe_listing` (MS2-D-30, D10). R23 stays an accepted MS-2 residual.
> - **ED-03..ED-10 (medium):** `provider_run.scope_key` is NOT NULL (MS2-D-13,
>   -31, D2, D10); the local path's two transactions and crash semantics (D10);
>   one binding lock order with in-memory transaction retry (MS2-D-35); the full
>   candidate predicate under the delist lock (MS2-D-30); the kill switch
>   denies new admission only (MS2-D-17); settlement reads wait for post-run
>   operations (MS2-D-41); the account's `dataRetentionDays` is checked against
>   the storage lifetime (MS2-D-25, -26, -33, -40); and the usage latch is an
>   allowlist (MS2-D-26).
> - **ED-11..ED-20 (low):** citations re-pointed to the current code; the
>   three-clock table corrected (MS2-D-39); overlapping local FULL runs
>   (MS2-D-30, -36); the `maxTotalChargeUsd` doc contradiction (MS2-D-15);
>   abort handling in the overdue selector (MS2-D-33); daily-bucket posting lag
>   (MS2-D-40, F5a); the deprecated `/v2/acts/` path and `restartOnError=false`
>   (D3 follow-up); the stage-2 NULL-scope break (MS2-D-22); and a gated
>   404-delete rule (MS2-D-25).
> - **Owner items:** none new. R23 is closed as an accepted residual; the
>   owner may still lower a revision-10 bound only from F5a evidence and a plan
>   revision.
>
> **Revision 11 (revision 10 targeted review, 2026-09-25).** Resolves the 12
> findings of the Codex targeted review of revision 10 (3 high, 6 medium,
> 3 low, R10-01..R10-12, all accepted; see *Review lineage*). Revision 10
> replaced "unverified ⇒ deny" with worst-case bounds; the review found some
> bounds asserted rather than derived and some liabilities outside the ledger.
> Each changed passage is marked **revision 11 (R10-NN)**. No decision, task,
> or migration number is added or renumbered, and no revision-10 bound is
> lowered. Revision 9's owner decisions are not reopened.
> - **R10-01 (high):** the API-call bound is now derived per endpoint from
>   Apify's documented billing units (official docs and pricing page,
>   retrieved 2026-09-25) and enforced wire-byte ceilings (MS2-D-32 *Per-call
>   bound*). The docs name the units but never the per-call multiplicity or
>   the metered-byte basis, so that remainder is an explicit residual, **R38**,
>   which the owner must accept before any paid admission (a new owner item;
>   it is an owner decision, not F5a evidence, so the F5a gate stays
>   non-circular).
> - **R10-02 (high):** the Actor's post-receipt byte check overshoots
>   `maxBytes`; the transfer bound now adds a per-request wire overhead and a
>   one-read overshoot, and a D1 follow-up counts wire bytes and pins the
>   receive buffer (MS2-D-26, D1 follow-up).
> - **R10-03 (high):** remaining correction-read liability is retained and
>   carried into every cycle in which a monitoring call is still possible;
>   the correction deadline stays fixed at reconciliation (MS2-D-23, -32, -34,
>   -41).
> - **R10-04..R10-09 (medium):** a write-once work-completion anchor that
>   metering reads never move (MS2-D-13, -32, -41); a durable cycle-discovery
>   allowance and handoff treatment for account reads (MS2-D-32, -40, -45);
>   every external call enumerated, including the start-option-mismatch abort
>   and an operator `probe` envelope for the R25 capability probe (MS2-D-25,
>   -32, -46); one storage model for failed deletion, a recurring full-cycle
>   liability (MS2-D-25, -26, -32, -40); a dataset page size derived from a
>   serialized-row bound (MS2-D-32, D3); and terminal-evidence precedence in
>   the overdue selector (MS2-D-33).
> - **R10-10..R10-12 (low):** in-memory retry exhaustion semantics (MS2-D-35,
>   D10); a lock order that names Reject and the HEARTBEAT lane (MS2-D-35); R36
>   figures by formula.
> - **Migrations:** `0021` gets **no** column change. Two existing `0021`
>   columns get stated semantics: `final_charge_op_at` is write-once at the
>   work-completion barrier, and `storage_cleanup_attempts` also counts the
>   start-option-mismatch abort. `0022` (E1) adds
>   `ApifySpendReservation.monitoring_bound_usd` and
>   `monitoring_charge_last_at`, the `ApifyCycleDiscovery` table, and the
>   `operator_kind` value `probe`.
>
> **Revision 12 (owner decision (s5, 2026-09-25), R25).** The runtime stops
> reading Apify account state. On 2026-09-25 the owner-created scoped runtime
> token (`HW_RADAR_APIFY_TOKEN`) got `403 insufficient-permissions` from
> `GET /v2/users/me`, `/v2/users/me/limits`, and `/v2/users/me/usage/monthly`.
> Apify's scoped-token form offers account-level permissions only for Actors,
> Tasks, Schedules, and Storages (docs.apify.com/platform/integrations/api,
> retrieved 2026-09-25), so no scoped token can make these reads. Every
> admission was therefore denied `account_state_unobservable`, which blocked
> F5a. The owner chose "Drop runtime account reads" on 2026-09-25. The
> decision is recorded in
> [OQ30](../../resolved-questions.md#oq30--runtime-apify-account-reads-r25) and in the
> 2026-09-25 (s5) amendment of [ADR 0021](../../adr/adr-0021-hybrid-acquisition-apify.md),
> which the same owner-directed change updates. New decision **MS2-D-48**
> holds the design, and the new *Implementation tasks (rev 12)* (E9.1–E9.6)
> hold the engineer's work. Each changed passage is marked **Revision 12
> (owner decision (s5, 2026-09-25), R25)**.
> - **Replaced:** the observed account snapshot and API cycle discovery
>   (MS2-D-17 *Period*, MS2-D-40) give way to a configured billing-cycle
>   anchor plus operator-verified account settings. The operator checks them
>   with the operator key outside the application before enabling. Headroom
>   becomes `HR_cycle + estimate + E ≤ P`, with `P = ACCOUNT_LIMIT_USD −
>   account margin`.
> - **Retired:** snapshot check 1 and `external_liability_exceeded`
>   (MS2-D-40), the standing account-read debit and *Cycle discovery*
>   (MS2-D-32), `USAGE_INCLUSION_LAG_S`, and the runtime client's three
>   account reads (MS2-D-15). The owner accepts the consequence: the runtime
>   can no longer see the other workloads' actual spend, only Apify's hard
>   limit. A start that Apify refuses with HTTP 402 trips a new latch reason,
>   `account_limit_refused`.
> - **Kept:** `cycle_unknown` and `account_state_unobservable`, with new
>   triggers. Also kept unchanged: every ledger reservation, the lock order,
>   the latch, row-before-start, operator reservations, `E` (5.00), the cycle
>   target (12.00), the cash ceiling (20.00), the operator allowance (1.00),
>   and R38.
> - **No schema change.** Migrations `0021` and `0022` are not edited, and no
>   `0023` is added. The `ApifyCycleDiscovery` table and the
>   `ApifyBudgetCycle.account_*` and `account_read_count` columns stay in
>   place, unwritten (MS2-D-48 *Schema*).
> - F5a's operator-side account reads move to the operator key under an
>   operator `inspect` reservation (MS2-D-46, F5a). New risk R39. R25's
>   account-read residual is closed. Its storage-delete residual and the
>   token's Actor **Read** grant stay open.
> - Changed text: MS2-D-15, -17, -32, -34, -40, -45, -46, -47; the new
>   MS2-D-48; *Things not to do*; the traceability rows for NFR-003, C-011,
>   and AC-7 reconciled spend, plus a new rev-12 row; *Synthetic Actor proof
>   acceptance* item 14; the E settings, E1, E3, E5, E6, E7, and *Acceptance*;
>   the new *Implementation tasks (rev 12)*; F5a; R3, R25, R38, and the new
>   R39; the paragraph after the risk table; and *Review lineage*. Codex review
>   of revision 12 is pending.
> - **Rev-12 Codex follow-up (2026-09-25).** A Codex bounded review of
>   revision 12 (`ea9029f8`) returned REVISION_REQUIRED with five findings,
>   R12-01..R12-05 (1 high, 3 medium, 1 low), all accepted. The owner also
>   answered the three R39 points. The changes stay within revision 12 and
>   are marked **Rev-12 Codex follow-up**. No decision ID is added. E9 was
>   revised in place before any E9 task ran: E9.2 now absorbs the forced
>   retirements, E9.6 (verification-age warning) is new, and the close-out
>   is E9.7.
>   - R12-01: durable recovery for a 402 whose latch was never recorded,
>     inside `reserve` and each tick (MS2-D-48 *Hard-limit refusal*).
>   - R12-02: commit boundaries and the full fixture inventory (E9.2, E9.4).
>   - R12-03: reserve before disabling for a planned change, plus a bounded
>     recovery-verification exception (MS2-D-48 *Operator verification*).
>   - R12-04: one account-setting validation contract for admission and
>     corrections (MS2-D-47, E9.2).
>   - R12-05: R33/R34 overrides and the command docstrings (E9.5).
>   - Owner: no verification expiry (a report warning instead), R38 still
>     accepted, and a C-011 clarification in the master spec. R39 is
>     closed.
>   See *Review lineage*.

**Goal:** prove the smallest complete multi-category decision path without an
ADR-0011 score:

`saved requirement → collect (local or self-owned Actor) → canonicalize/match →
eligibility verdict (match | no_match | unknown) → shortlist read model`

HDD/SSD, GPU/accelerator, RAM, and CPU are first-class categories. The Apify path is
bounded, idempotent, completeness-honest, and budget-admitted.

**Not in MS-2** (settled D1):

- the "exactly-one alert" and the M365 transport (MS-4);
- shortlist, watch, and detail UI pages (MS-3; MS-2 ships the query/read model only);
- category ranking or scores (MS-5 / deferred ADR 0011);
- rich server-configuration modeling (D11).

## Global constraints

- **Verification gate at every commit:**
  `HW_RADAR_DB_PORT=5433 uv run python -m scripts.check` runs ruff format --check,
  ruff check, basedpyright, coverage run -m pytest, coverage report, and pip-audit.
  Also run `HW_RADAR_DB_PORT=5433 uv run python manage.py makemigrations --check --dry-run`.
  The gate has no docs step. Workstation CPU policy (remote execution wrapper)
  governs *where* the command runs. It does not change the command.
- Run **one pytest process against the shared dev DB at a time**. Concurrent
  `transaction=True` runs on port 5433 flake. Rerun clean before treating a DB-test
  failure as real.
- Baseline at `81876da`: gate green, 552 passed / 1 skipped, coverage 95%. A slice
  may not lower coverage below the configured threshold.
- **Migrations** (settled D2). The catalog head is `0017_identity_retention_checks`,
  and MS-2 plans `0018+` in this order: B `0018`, `0019`; C `0020`; D `0021`; E `0022`.
  D-prep (see *Slice order*) has no migration.
  **These numbers are planning allocations, not reservations** (revision 5,
  owner-clarified (s2, 2026-09-24)). Before writing its migration, each slice
  re-verifies the live migration graph on its base (`showmigrations catalog`) and
  records any drift (a different head, an interleaved migration) in its close-out
  evidence; the slice then numbers from the live head. The deferred MS-2a plan owns
  none of these numbers.
  Every migration must:
  - apply cleanly to an empty DB and upgrade a DB holding deployed drive data without
    loss;
  - be additive/expand-contract (new nullable columns or new tables only; no drop
    or rename of deployed columns);
  - add the DR-001 CHECK pair and sweep index to every new retention-bearing table,
    via `retention_constraints(prefix)` / `retention_indexes(name)`
    (`src/hw_radar/catalog/models/base.py:53-96`);
  - be tested by `tests/db/test_migrations.py` plus the slice's own
    constraint tests.

  If merge order changes, renumber the *unmerged* slice's migrations before merge.
  Never renumber a merged migration. The deferred MS-2a plan
  (`docs/superpowers/plans/2026-09-06-ms2a-scoring-substrate.md:4-10`) names
  `0018–0026` for its own work. It is **not** reserved here and must be rebased on
  whatever history exists when the owner reactivates it.
- **Public repo:** no secrets, tokens, private hostnames or IPs, Actor account
  identifiers, or absolute local paths in committed text. The Apify token is
  referenced only as the env var `HW_RADAR_APIFY_TOKEN`, rendered by the OpenBao
  Agent (NFR-003) from the Hardware Radar runtime path `secret/apps/hw-radar/apify`
  (owner decision (s4, 2026-09-25), OQ25: a scoped token the owner still has to
  create, MS2-D-43). The operator/deploy key at `secret/apps/hw-radar/agent/apify`
  is unscoped, is never rendered to the production app environment, and never
  serves the runtime. CI holds no Apify credential. Neither credential is ever the
  apify-actors venture's token. Actor references (Actor id, build tag) come from
  settings or the environment, never from committed literals. Actor *names*
  (`hw-radar-<purpose>`) and the Actor source under `actors/` are committed
  (MS2-D-38).
- **Actor projects** (revision 5, MS2-D-38): every Hardware Radar Actor lives under
  `actors/hw-radar-<purpose>/` in this repository, with its own `pyproject.toml` and
  `uv.lock`. The gate runs the Actor's own tests, type check, and audit in that
  project's environment; the Apify SDK never enters hw-radar's `uv.lock`.
- Dependencies change only through `uv add` / `uv remove`. Never hand-edit
  `uv.lock`.
- Conventional, GPG-signed commits. Update `docs/TODO.md` / `docs/STATUS.md` per
  TODO discipline in each slice's close-out task.
- **Regression files are frozen per slice.** Unless the slice lists a file as
  changed, existing test files are not edited. New tests go in new files. Slice
  exit evidence includes `git diff --stat <slice-base> -- <frozen files>` showing
  no changes.

## Things not to do

- Revision 12 (owner decision (s5, 2026-09-25), R25): do not add a runtime
  Apify account read (`/v2/users/...`) to any admission, poll, reconcile,
  report, or command path. Do not render the unscoped operator key to any
  application runtime, including a proof environment, and do not
  read account state with it from inside the application (MS2-D-48).
- Do not change the ADR-0010 identity spine
  (`category → product_family → product_model → product_variant → listing →
  offer_snapshot`), add an EAV table, or put watch-critical values in a JSON bag
  (ADR 0022, NFR-006, D10).
- Do not flatten drive semantics into generic strings. Do not change drive matcher
  decisions or the drive precision gate. Do not treat the drive corpus as
  validation for GPU/RAM/CPU (master spec :917-919, D9).
- Do not map `ListingResolution` outcomes (`accept | review | none`) onto
  eligibility verdicts. They are separate decisions (D10).
- Do not let `unknown` count as `match`, and do not return `no_match` without a
  positive contradiction (FR-014, D10).
- Do not read or create `listing_score`, `cohort_baseline`, `current_*` score
  columns, or any ADR-0011 artifact. Do not present cross-category scores
  (ADR 0022).
- Do not let a non-`complete` provider result produce `DelistScope.complete=True`,
  advance or preserve sweep continuity (it breaks it, MS2-D-11), or reach the
  stale-absence path for a remote provider. A remote run counts toward
  continuity only when both its evidence and its scope say complete (MS2-D-11).
- Do not let one collection scope's sweeps prove continuity for another scope
  (MS2-D-31).
- Do not let an older observation overwrite newer current listing state,
  write current content to (or revive) a listing that newer absence evidence
  retired, create an active listing for a key a newer complete sweep of its
  scope omitted, or delist a listing that a newer run observed (MS2-D-30,
  MS2-D-35).
- Do not copy a snapshot's retention from the current `Listing` on an
  ordering-guarded path; retention follows the observation's own time
  (MS2-D-37).
- Do not let an older run restore continuity that a newer break ended, on any
  scope, the legacy NULL scope included (MS2-D-36).
- Do not let a stored eligibility verdict outlive the inputs it evaluated,
  catalog inputs included (MS2-D-20, MS2-D-29), and do not default any remote
  import to `merchant_fact` retention (MS2-D-25).
- Do not release a spend reservation while charge-producing work remains
  (MS2-D-32), and do not age settled spend out by its `reserved_at`; it counts
  in every Apify billing cycle its charge interval touches (MS2-D-34, revision 5).
  Do not treat a rolling window or a calendar month as the budget period; the
  authority is the account's Apify billing cycle read from the API (MS2-D-40).
  Do not treat the first usage figure at run completion as final, and do not mark
  a reservation reconciled because the Actor process stopped (MS2-D-41).
  Do not derive one clock from another (MS2-D-39).
  Revision 6: do not treat elapsed time as usage finality (MS2-D-41); do not
  drop reconciled spend from the account check while no inclusion watermark
  exists, or admit paid work without the owner's external-liability bound
  (MS2-D-40); do not admit paid work in an environment without the cycle's
  ledger authority (MS2-D-45); and do not build or inspect an Actor without an
  operator reservation (MS2-D-46).
  Do not anchor a retention deadline at a locally observed event (MS2-D-33).
  Do not change the eBay delist path or the lane-continuity gate (migration 0015)
  (D5).
- Do not create listings, fork history, duplicate observations, or reset watch
  state when a source switches provider. Do not use Apify result IDs as keys
  (DR-003, D6).
- Do not configure an Apify schedule for any production job. Do not use webhooks
  in v1. Do not run a second scheduler (ADR 0012, D4).
- Do not give an Actor DB credentials, hw-radar model imports, Django imports, or
  canonical state. Do not enable residential proxies, browser escalation, or paid
  third-party Actors automatically (ADR 0021, ADR 0014). MS-2 Actor runs use
  no proxy at all (MS2-D-26). Revision 5 (owner-clarified (s2, 2026-09-24)): no
  residential proxy, no automatic proxy rotation, no CAPTCHA solving, no paid
  unblocker, and no paid third-party Actor, ever, including as a fallback. A source
  that needs any of them fails admission (MS2-D-44).
- Do not create, build, or version a Hardware Radar Actor anywhere but
  `actors/` in this repository, and do not depend on the apify-actors venture's
  code, lifecycle state, product records, admission process, or token
  (revision 5, owner-overridden; MS2-D-38).
- Do not start a paid Actor run outside hw-radar's budget admission. That rules
  out the Apify MCP `call-actor` tool, Console "Start", and schedules for any
  Hardware Radar Actor (MS2-D-43).
- Do not deploy an Actor automatically from pull-request code, CI, or a GitHub
  push webhook. Deployment is an explicit, reviewed operator action (MS2-D-43).
- Do not treat the account-level Apify `max_monthly_usage_usd` as project
  admission. It is an owner backstop only (D3), and agents never change it or any
  other billing or account setting without explicit owner authority (MS2-D-40).
- Do not architect correctness around `maxTotalChargeUsd` or the `maxItems` run
  option. `maxItems` applies only to pay-per-result Actors, and the docs
  contradict each other on `maxTotalChargeUsd` (MS2-D-15, revision 10), so
  either is at most an extra defense for Hardware Radar's own Actors.
- Do not start any live Actor run before the owner gates in *Open risks* clear.
  Do not name Newegg as the Actor source (ToU exclusion, see risk R1).
- Do not model complete servers beyond basic-watch exact identity. Do not merge
  differently configured servers into one variant (D11, ADR 0022).
- Do not edit ADRs 0021/0022, the master spec, or the deferred MS-2a plan from a
  slice. Decision-record changes go through the owner (revision 5's amendments
  were owner-directed).

## Interfaces reused verbatim (do not reimplement)

- Adapter contract `SourceAdapter`, `DelistScope`, `DelistDetector`,
  `adapter_retention` (`src/hw_radar/acquisition/contracts.py:93-180`).
- Pipeline stages `_persist_all`, `_normalize`, `_apply_delist`,
  `_record_sweep_continuity`, `_classify_batch` (`src/hw_radar/acquisition/pipeline.py`).
  Persistence goes through `persist.upsert_listing` / `append_snapshot` / `store_raw`
  (`src/hw_radar/acquisition/persist.py:28-107`).
- Resolver `CatalogResolver.resolve_listing` and `_apply`
  (`src/hw_radar/matching/resolver.py:439-586`): the only edge-writing path.
- Ladder `decide()` and its value types (`src/hw_radar/matching/ladder.py:49-277`).
- Poller admission `check_admission` (`src/hw_radar/acquisition/scheduling/admission.py:32`)
  and the APScheduler build (`src/hw_radar/poller/service.py:304-396`).
- Retention helpers and classes (`src/hw_radar/catalog/models/base.py`).
- Eval harness `load_corpus` / `evaluate_corpus` / `build_report`
  (`src/hw_radar/matching/eval/`).

## Design decisions

Each decision is binding for this plan. It gives its evidence and the rejected
alternative. "Reopen if" names the evidence that would change it.

**MS2-D-01 — Milestone boundary.** MS-2 exits on a persisted eligibility verdict
plus a shortlist read model (query functions + a management command). It has no
UI and no alert.
- *Evidence:* master spec :935-944 (MS-2) and :952-956 (MS-4); settled D1.
- *Rejected:* adopting ADR 0022 confirmation #3's "exactly-one alert" as an MS-2
  gate. It spans MS-2..MS-4 (risk R10).

**MS2-D-02 — Category dispatch registry.**
- **Pure registry.** A pure module `hw_radar.matching.categories` maps category
  slug → `CategoryRules(slug, extract, extract_candidates, decode, veto)`. MS-2
  Slice A registers only `drive`. `rules_for("drive")` builds its
  `CategoryRules` **on each call** from the current module attributes
  `vocab.extract`, `mpn.extract_candidates`, `grammars.decode`, and
  `ladder.contradictions`, so `rules_for("drive").extract is vocab.extract`
  holds, and a monkeypatched module function is honored. Binding at import time
  was rejected because frozen `tests/db/test_resolver.py` (≈:242–314)
  monkeypatches `vocab.extract` and expects an error edge.
  `CATEGORY_SLUG_MAX_LENGTH` (50) lives in `matching/categories.py`.
- **Ladder veto.** `ladder.decide()` gains a keyword-only `veto` parameter that
  defaults to `contradictions`. It is used at all three veto sites
  (`ladder.py:161,196,225`). The two local result variables that were named
  `veto` (`:161`, `:196`) are renamed `vetoed`, so the callable parameter is
  never rebound to a list; the persisted evidence key stays `"veto"` and its
  values are byte-identical.
- **Resolver spec readers.** The resolver keeps the ORM-bound spec readers in a
  parallel `_SPEC_READERS` table whose keys must equal the registry's. A test
  pins this.
- **Unregistered categories.** A registered-but-unknown category produces a
  `none` edge with `unsupported_category` evidence. Drive rules never run on it.
- **Edge provenance.** Every ladder-path edge carries `evidence["category"]`
  and `evidence["category_source"]` (`hint | legacy_default`). This is the only
  additive change to persisted drive output.
- **Invalid hints.** A non-string or non-slug hint read back from `attrs_json`
  produces the resolver's error edge, never a drive fallback.
- *Evidence:* Map Q2 (`decide()` touches drive semantics only via
  `contradictions()` and the grammar-gated rung 2).
- *Rejected (a):* a generic `Mapping[str, object]` veto payload. It loses typing
  and flattens drive semantics, which ADR 0022 forbids.
- *Rejected (b):* one resolver class per category. It duplicates `_apply`'s
  transactional edge rules.
- *Reopen if* a category needs a rung the ladder cannot express.

**MS2-D-03 — Category hint at the ingestion boundary.**
- **Contract field.** `ParsedListing.category_hint: str | None`, validated as a
  slug of at most 50 characters (the `Category.slug` shape). It is set by the
  collector from the *query scope it ran* (eBay `category_ids` sweep, Actor
  input scope), never from title text.
- **Persistence.** It is persisted under the reserved `OfferSnapshot.attrs_json`
  key `"category_hint"` only when non-null, so default rows are byte-identical.
  `attrs` may not carry the reserved key.
- **Resolver read.** The resolver reads it from the latest snapshot, like
  `_structured_mpn` (`resolver.py:127-137`).
- **Legacy default.** `None` ⇒ `drive`. All five MS-1 sources are drive-only by
  construction (Map Q7). Every multi-category source must hint every item
  (Slice F test).
- *Rejected (a):* a `SourceConfig` category column. eBay and Actor sources are
  multi-category per sweep, and it would need a migration in A.
- *Rejected (b):* a title classifier. It is unreliable.
- *Reopen if* evaluation at scale needs a queryable pre-resolution category.
  Promote the hint to a `Listing` column then.

**MS2-D-04 — Typed first-class spec satellites (Slice B).** Each satellite is 1:1
on `ProductModel`, `RetentionGoverned`, and carries the CHECK pair, like
`DriveSpec` (`identity.py:206-236`). Fields are the minimum that concrete v1 watch
filters need. `DriveSpec` is unchanged.

| Satellite | Field | Type | Justifying watch filter / trap |
| --- | --- | --- | --- |
| `gpu_spec` | `chip_vendor` | choices nvidia/amd/intel/other/unknown | "NVIDIA only" (CUDA). The model manufacturer is often the board partner, so chip vendor is distinct. |
| | `vram_gb` | PositiveSmallInteger null | Top GPU filter (e.g., ≥24 GB for local inference). |
| | `interface` | choices pcie/sxm/oam/mxm/other/unknown | Hard compatibility. SXM/OAM modules do not fit PCIe hosts, a common used-accelerator trap. |
| | `cooling` | choices active/passive/liquid/unknown | Passive datacenter cards need chassis airflow. Hard fit filter. |
| | `tdp_w` | PositiveSmallInteger null | Power-budget ceiling for homelab hosts. |
| `ram_spec` | `generation` | choices ddr3/ddr4/ddr5/other/unknown | Hard compatibility. |
| | `module_type` | choices udimm/rdimm/lrdimm/sodimm/other/unknown | Hard compatibility (RDIMM/UDIMM/LRDIMM do not mix). |
| | `ecc` | Boolean null | ECC hard filter (ECC UDIMMs exist, so it cannot be derived from type). |
| | `module_capacity_gb` | PositiveSmallInteger null | Per-module size. |
| | `modules_per_kit` | PositiveSmallInteger default 1 | Kit part numbers identify N modules. Total = N × size. |
| | `speed_mts` | PositiveInteger null | Minimum-speed filter. |
| | `ranks` | PositiveSmallInteger null | Server population rules (1R/2R/4R/8R). |
| `cpu_spec` | `socket` | CharField(32), normalized lowercase token | Hard compatibility. The vocabulary is open-ended, so it is a typed column validated by the category rules module, not a DB enum. |
| | `cores` | PositiveSmallInteger null | Minimum-cores filter. |
| | `tdp_w` | PositiveSmallInteger null | Power-budget ceiling. |

- Deliberately excluded: VRAM type, slot width, CAS latency, clocks, threads,
  microarchitecture, and platform-lock. None is a v1 hard filter; each can be
  added later as a new nullable column (NFR-006).
- Listing-level traps such as vendor-locked EPYC parts are offer evidence, not
  model spec.
- *Rejected:* JSON `spec_json`-driven filters. Watch-critical values must be typed
  columns (D10).

**MS2-D-05 — Conservative new-category matching (Slice B).**
- **Rungs.** GPU/RAM/CPU use rung 0 (prior) and rung 1 (exact alias) only. No
  MPN grammars (no rung 2); `decode` returns `None`. Attribute or fuzzy evidence
  never auto-accepts; it produces `review` (D9).
- **Auto-accept flag.** `CategoryRules.auto_accept: bool` is `True` for `drive`
  and `False` for `gpu`/`ram`/`cpu` at MS-2 merge. While `False`, a rung-1 hit
  yields `review` with evidence `auto_accept_disabled`. Flipping it requires an
  owner-ratified category corpus (risk R4).
- **Cross-category guard.** An alias hit or prior whose target family category ≠
  the dispatch category yields `review` with `cross_category` evidence (FR-003,
  no false cross-category merges).
- **Basic-watch categories** (`nic`, `hba`, `motherboard`, `server`) get rules
  with exact curated aliases only and no spec satellite. `server` sets
  `variant_on_demand=False` so configured systems never collapse into one
  variant (D11).
- **Acceptance provenance.** Even with `auto_accept=True`, new categories
  accept only authoritative aliases at approved grains (MS2-D-21).
- *Rejected:* enabling GPU/RAM/CPU auto-accept on catalog-authoritative aliases
  at merge. No category corpus exists to prove precision.

**MS2-D-06 — Reference provenance (IR-007, Slice B).** GPU/RAM/CPU reference rows
arrive as curated seed documents in the existing refdata envelope
(`SeedDocument`/`SeedModel`/`SeedAlias`, `refdata/contracts.py:42-99`). The
envelope gets a category-discriminated spec payload and per-row
`source_url` + `retrieved_on`.
- `source_kind=catalog_authoritative` / `retention_class=manufacturer_reference`
  apply only to first-party manufacturer pages. Everything else is `manual`.
- `refdata.persist` becomes category-keyed. Its drive path is unchanged.
- **Current-state gaps** (code evidence, 2026-09-24): `persist._import_document`
  hard-codes `Category.objects.get_or_create(slug="drive")` (`persist.py:152`)
  and writes `DriveSpec.objects.update_or_create` (`persist.py:208`);
  `SeedModel.spec` is one concrete drive-mirroring `SeedSpec`
  (`contracts.py:56`); provenance is one per-document `SeedProvenance` block,
  not per row; and `SeedProvenance.source_kind` is a Literal of
  `first_party_datasheet | first_party_manual | first_party_page` with no
  non-first-party value. B4a/B4b close these gaps.
- **First-party sources and identifiers** (prep survey, retrieved 2026-09-24;
  per-source ToS spot check stays risk R6):
  - CPU: Intel ARK (ark.intel.com), keyed by processor number (for example
    `Xeon Gold 6448Y`), with the FPO/spec code (`SRxxx`) as the printed
    identifier; AMD specifications pages (amd.com) keyed by marketing name, with
    the OPN (`100-000000xxx`) as the ordering identifier. OPN and FPO/spec code
    are the MPN-like aliases; marketing names are the retail-name aliases.
  - GPU: chip-vendor pages and PDF datasheets (NVIDIA data-center and GeForce
    pages, AMD Instinct and Radeon pages). Every MS2-D-04 GPU field is
    chip-level, so board-partner (AIB) pages are not v1 sources. An AIB page is
    authoritative only for that partner's own board SKU, never for chip facts.
    Intel Arc and Data Center GPU pages were not surveyed.
  - RAM: Micron's official part decoder is the one confirmed first-party
    decoder. Samsung and SK hynix per-part pages exist, but their decode grammars
    are only community-sourced, so the grammars are not authoritative. For OEM
    server part numbers (Dell, HPE, Lenovo), the OEM's own spec for its own SKU
    qualifies. A reseller's claim that an OEM part equals a module-maker part does
    not qualify: that equivalence is `manual` at most.
  - No bulk export is confirmed for any vendor. Seeds stay hand-authored,
    per-model JSON, like the drive seeds.
- **Non-first-party retention gap** (revision 5, implementation-driven
  clarification from Slice B, not an owner decision). No retention class exists
  for non-first-party reference rows: `manufacturer_reference` is first-party
  only, and adding a class rewrites every `*_retention_ttl_coherent` CHECK, as
  migration `0017` did. The importer refuses a non-first-party document before
  any write (`UnratifiedProvenanceError`, a subclass of `ImportConflictError`),
  and B4c seeds first-party rows only. The `SeedProvenance.source_kind` value for
  such rows is `non_first_party`, which maps to alias `source_kind=manual` at the
  contract level.
  - **Owner decision (s4, 2026-09-25); [OQ27](../../resolved-questions.md#oq27--retention-class-for-non-first-party-reference-data),
    R32 resolved.** MS-2 adds no production retention class for non-first-party
    reference data: no `third_party_reference` class and no rewrite of the
    retention CHECKs. Authoritative reference seeds come only from first-party,
    manufacturer-authoritative sources. Non-first-party PDFs and pages may
    inform research or manual review, but they never automatically seed
    authoritative aliases or specs. The importer's refusal above is therefore
    the MS-2 behavior, not a gate awaiting clearance. Reopen only with a
    concrete source, intended use, provenance model, and actual need.
- *Rejected:* third-party spec aggregators as authoritative. Provenance and
  licence are unclear.

**MS2-D-07 — Watch/requirement schema and versioning (Slice C, migration 0020).**
- **`Watch`** (catalog app):
  - identity and scope: `name`, `category` FK, `enabled`, optional exact
    `target_family` or `target_model` (CHECK: at most one);
  - version: `requirement_version` PositiveInteger, bumped by the requirement
    service on every requirement edit;
  - hard offer clauses: `max_unit_price_usd` Decimal null,
    `allowed_conditions` ArrayField of `Condition` (empty = no constraint),
    `require_in_stock` bool, `allow_international` bool;
  - one soft threshold: `target_unit_price_usd` Decimal null.
- **Requirement satellites.** Each category has a typed 1:1 satellite:
  - `drive_requirement`: `min_capacity_tb`, `media_types[]`, `interfaces[]`,
    `form_factors[]`, `recording_techs[]`;
  - `gpu_requirement`: `min_vram_gb`, `chip_vendors[]`, `interfaces[]`,
    `coolings[]`, `max_tdp_w`;
  - `ram_requirement`: `generations[]`, `module_types[]`, `require_ecc` null,
    `min_total_capacity_gb`, `min_speed_mts`;
  - `cpu_requirement`: `sockets[]`, `min_cores`, `max_tdp_w`.

  Basic-watch watches have no satellite and must set `target_model` or
  `target_family`.
- **Input validation.** Per-category Pydantic `RequirementSpec` models validate
  input. The service is the only writer, and it enforces satellite category =
  watch category (a cross-table rule the DB cannot CHECK).
- **Hard vs soft.** Hard clauses decide the verdict. `target_unit_price_usd` only
  annotates shortlist rows (`meets_target`) and never changes a verdict. This is
  an inference from ADR 0022's eligibility-before-attractiveness split; see risk
  R11.
- **Naming.** The table is named `watch`. The per-(watch, listing) verdict table
  is `watch_evaluation`. The name `watch_match_state` stays reserved for the MS-4
  alert/dismiss state machine (mvp-web-ui research), avoiding a semantic collision.
- *Rejected (a):* one JSON requirement document per watch. Watch-critical values
  would sit in a JSON bag (D10).
- *Rejected (b):* a single wide requirement table. Nullable cross-category columns
  invite category-mismatched clauses.

**MS2-D-08 — Eligibility semantics (Slice C).**
- **Clause outcomes.** Each hard clause yields `match | no_match | unknown` plus a
  structured reason (`clause`, `outcome`, `evidence_tier`, `required`,
  `observed`, `detail`).
- **Product-attribute clauses** evaluate against **catalog-tier** evidence: the
  typed `*_spec` of the listing's accepted resolution at model/variant grain, or
  the family agreement set at family grain.
  - They yield `match` only from catalog-tier evidence.
  - They yield `no_match` from a catalog-tier contradiction, or from a listing-tier
    extracted attribute that positively contradicts, with extraction confidence
    ≥ the rules module's threshold.
  - Otherwise they are `unknown`. Unresolved or `review` listings are therefore
    `unknown` on product clauses.
- **Offer clauses** (price, condition, stock, international) evaluate against the
  latest `OfferSnapshot` / `Listing` facts. An unknown condition against a
  non-empty allow-list is `unknown`.
- **Aggregate.** Any `no_match` ⇒ `no_match`; else any `unknown` ⇒ `unknown`;
  else `match`. Delisted or expired listings are not evaluated.
- *Rejected:* allowing listing-title evidence to satisfy (`match`) a product
  clause. This is the silent-pass risk FR-014 forbids.

**MS2-D-09 — Evaluation persistence and the shortlist read model (Slice C).**
- **`watch_evaluation`**: unique `(watch, listing)` current state holding verdict,
  `reasons` JSON (output evidence per DR-004, not watch input), and the
  evaluated-input binding: `requirement_version`, `evaluator_version`,
  `snapshot_observed_at`, `resolution` (FK to the `ListingResolution` edge that
  was current at evaluation, null, `SET_NULL`), `catalog_fingerprint`
  (MS2-D-29), plus `evaluated_at`. Validity is governed by MS2-D-20.
- **Retention.** It is `RetentionGoverned`, mirroring the listing's retention
  class/expiry at evaluation. `Listing.mark_delisted` pulls it forward like
  snapshots (`market.py:369-374`), and the purge registry covers it. eBay-derived
  reasons carry prices, so they must not outlive DR-008.
- **Trigger.** Evaluation runs as a pipeline stage after resolution, isolated
  like the resolver: a failure never blocks ingestion. It is wired on every
  ingestion path (MS2-D-20). It also runs from an `evaluate_watches` command
  after watch edits.
- **Read model.** `shortlist(watch_id)` returns only *current* (MS2-D-20)
  `match` rows for active listings, ordered by landed USD ascending, with
  freshness (`fresh | stale | budget_paused`) and `meets_target`.
  `review_queue(watch_id)` returns current `unknown` rows (`state="unknown"`)
  and every non-current row of any verdict (`state="pending"`).
- *Rejected:* an append-only evaluation history. DR-004 requires evidence per
  evaluated listing, not per evaluation. History can be added in MS-4 with the
  alert state machine.

**MS2-D-10 — Collection-provider seam (Slice A).**
- **Protocol.** `CollectionProvider` (in `contracts.py`) declares:
  - attributes `provider_kind`, `provider_key`, `site_key`, `run_kind`,
    `expects_json`;
  - methods `fetch`, `parse`, `delist_scope`, `run_evidence`.
- **Local adapter.** `LocalCollectionProvider(adapter)` delegates to today's
  `SourceAdapter` unchanged.
- **Runners.** `run_collection(provider, …)` holds today's `run_source` body.
  `run_source(adapter, …)` keeps its exact signature and delegates. Poller,
  heartbeat, and probe call sites are untouched.
- **Callers outside the seam.** `run_source` is the only *pipeline* caller of
  `fetch`/`parse`, but not the only caller (Map Q3, corrected in revision 2):
  - `harvest_corpus` calls both directly, with no persistence
    (`harvest_corpus.py:134-141`). MS2-D-27 carries the category hint through it.
  - Each heartbeat adapter's `probe()` reuses its own `fetch`/`parse`: eBay,
    ServerPartDeals, Seagate, and WD, called from `run_heartbeat`
    (`heartbeat.py:218`). Heartbeat is local-only (MS2-D-18 CHECK).
  - `run_heartbeat` then fires `run_source` as a FULL run (`heartbeat.py:228`).
    It is therefore inside the seam, and MS2-D-20 wires evaluation there.
- *Evidence:* Map Q3.
- *Rejected:* editing each adapter to a new base class. That touches five
  collectors for no behavior gain.

**MS2-D-11 — Run-evidence contract and completeness gate (Slice A).**
- **Enums.** `ProviderKind(local, apify)` and
  `RunCompleteness(complete, truncated, partial_failure, failed)` are
  `TextChoices` in `catalog/models/ops.py`. They need no migration until a field
  uses them.
- **Evidence model.** `ProviderRunEvidence` (frozen Pydantic,
  `extra="forbid"`) holds `evidence_version=1`, kind, key, completeness,
  non-empty reason, and `stale_absence_eligible`. The validator allows `True`
  only for `local`.
- **Gate.** `gate_delist_scope(scope, evidence)`:

  | Completeness | Result |
  | --- | --- |
  | `complete` | the scope, unchanged, if `scope.complete` is True **or** the evidence is stale-eligible (local only); else `None` |
  | `truncated` | `replace(scope, complete=False)` if stale-eligible, else `None` |
  | `partial_failure` | `None` |
  | `failed` | `None` |

  The `complete` row was tightened during Slice A verification (revision 2).
  Revision 1 passed a `complete` evidence's scope through even when the scope
  said `complete=False`. A remote provider reporting `complete` evidence with an
  incomplete scope could then reach `ABSENT_STALE`.

- **Continuity.** `counts_toward_sweep_continuity(evidence, scope)` takes the
  provider's own `DelistScope` (before gating) as well as the evidence
  (revision 3, review F-04 residual):
  - **Local** evidence: unchanged. It counts iff `complete` or stale-eligible
    `truncated`. The scope is ignored, so every local run still counts.
  - **Non-local** evidence counts only if all three hold: completeness is
    `complete`, `scope is not None`, and `scope.complete`. Every other remote
    run breaks continuity. That includes the contradictory case of `complete`
    evidence with an incomplete scope, which `gate_delist_scope` already
    refuses for direct absence.
  - *Why:* revision 2 counted every `complete` evidence. A string of remote
    runs reporting `complete` evidence but an incomplete scope kept the old
    `continuous_since` alive and became successful predecessors. A later local
    incomplete sweep could then stale-delist on continuity that no remote
    scope proved.
  - From Slice D, continuity is tracked per collection scope (MS2-D-31). In
    Slice A every run is the legacy NULL scope, so the lane column below is
    the whole mechanism.
  - An eligible successful FULL run calls `_record_sweep_continuity`, unchanged.
  - An **ineligible** successful FULL run **breaks** continuity:
    `_break_sweep_continuity(site)`, a small helper beside
    `_record_sweep_continuity`, sets the FULL lane's `continuous_since := None`.
    It is a no-op when the site has no `SourceConfig`.
  - A remote FULL result rejected before persistence also breaks continuity
    (MS2-D-22). A rejected PROBE never touches continuity (MS2-D-24).
  - *Why:* merely skipping the update would leave an old local
    `continuous_since` in place. `_record_sweep_continuity` finds the previous
    run by `FULL` + `SUCCESS` alone (`pipeline.py:140-145`), so a truncated remote
    import would make the gap look short and let a later local incomplete sweep
    stale-delist on continuity that no run ever proved.
  - Because every ineligible run nulls continuity, the next eligible run always
    restarts it. So the previous-run lookup needs no completeness predicate.
    That argument assumes runs are recorded in order, which holds in Slice A.
    From Slice D, asynchronous imports can record out of order, so every break
    carries its event time and an older run cannot restore continuity a newer
    break ended (MS2-D-36).
  - *Rejected:* tracking the last eligible sweep in a new lane column. It needs a
    migration in Slice A and duplicates the ScraperRun record.
  - Local runs are always eligible, so local-only behavior is unchanged.
- **Local mapping.** An adapter scope with `complete=True` maps to `complete`. A
  scope with `complete=False`, or no scope at all, maps to stale-eligible
  `truncated`. The eBay path and non-delist sources therefore behave exactly as
  today.
- **Storage.** A persists evidence in `ScraperRun.detail_json["provider"]` (no
  migration). D adds the queryable `ProviderRun` table.
- **Truncation cause** (revision 5, owner-clarified (s2, 2026-09-24)). The four
  `RunCompleteness` values stay exactly as Slice A landed them, and
  `test_completeness_values_are_the_adr0021_taxonomy` stays unmodified. D1 adds a
  separate DB-free `TruncationReason` `TextChoices` in `catalog/models/ops.py`:
  `item_limit | page_limit | request_limit | time_limit | resource_limit`
  (`resource_limit` = a byte or transfer budget set at admission). It qualifies
  `truncated` only:
  - `ProviderRunEvidence` gains an optional `truncation_reason`. A validator
    forbids it for every completeness except non-local `truncated`. Local
    truncated evidence keeps `None`. *Amended 2026-09-25 (D8 landing):* the
    model does not *require* it for non-local `truncated` evidence, because the
    frozen Slice A remote fakes in `tests/db/test_collection_provider.py` build
    that evidence without a reason. The requirement is enforced where the
    Apify path persists it: the `provider_run_truncation_reason_coherent`
    CHECK (set iff completeness is `truncated`), and the importer copies the
    reason from the `provider_run` row into the evidence.
  - A `None` reason is omitted from the evidence JSON, so local
    `detail_json["provider"]` stays byte-identical and
    `test_run_source_records_local_provider_evidence` stays green unmodified. The
    serializer mechanism is the implementer's choice.
  - D2 adds a nullable `provider_run.truncation_reason`.
  - *Rejected:* new `RunCompleteness` members per cause
    (`truncated_items`, …). That would break the Slice A taxonomy test and every
    `truncated` branch in the gate, although the delist semantics of all causes
    are identical.
- *Rejected:* reducing the four-state taxonomy to the existing
  `DelistScope.complete` boolean. It keeps completeness an adapter honesty
  assumption (Map Q6a).

**MS2-D-12 — Scoped absence (Slice D, migration 0021).**
- **Fields.** `Listing.collection_scope` is a nullable CharField(100) written
  from `ParsedListing.collection_scope` at upsert; a null never overwrites a set
  value. `DelistScope.scope_key` is optional.
- **Candidate filter.** `_apply_delist` restricts candidates to
  `collection_scope = scope_key`, where a `None` key means `collection_scope IS
  NULL`. Today every listing is NULL and every scope is `None`, so behavior is
  unchanged.
- **Key format.** Scope keys are provider-independent:
  `"<site_key>:<category>:<query_id>"`. Local and Actor collection of the same
  sweep share a key.
- *Why:* a complete per-category sweep on a multi-category site would otherwise
  delist every other category's listings (Map Q6b).
- **Not sufficient alone.** The candidate filter stops direct cross-scope
  deletion, but it does not prove that a scope was polled across its grace.
  That proof is per-scope continuity (MS2-D-31). The filter also gains the
  newer-observation guard (MS2-D-30).
- *Rejected:* one `SourceSite` per category sweep. It forks listing identity,
  violating D6.

**MS2-D-13 — Provider-run record and idempotency key (Slice D, migration 0021).**
- **Table.** `provider_run` holds:
  - identity: `provider_kind`, `source_site` FK, `external_run_id` (unique with
    kind; null until the start response is recorded, MS2-D-33),
    `import_idempotency_key` (unique, `apify:<run id>`, set with the run id),
    `actor_ref`, `build_id`, `build_number`, `contract_schema_version`;
  - requested scope: `query_scope` JSON (category/query/limits), plus typed
    `scope_key` (the MS2-D-12 key the run was admitted for), `memory_mb`,
    `timeout_s`, `max_items`, `max_pages`, and `admission_class`.
    Revision 10 (entry gate, ED-03): `scope_key` is **NOT NULL**. Every
    `provider_run` row is an Actor run, and the landed contract requires the
    scope on the run input and on every row (`QueryScope.collection_scope`,
    `contract.py:123`; `ListingRow.collection_scope`, `:214`; `collectionScope`
    is `required` in both schemas). A remote run therefore never sweeps the
    legacy NULL scope; revision 3's "null for the legacy scope" is withdrawn;
  - remote execution (MS2-D-23): `admitted_at`, `remote_status` (null until the
    start response), `remote_terminal_at`, `started_at`, `finished_at`,
    `completeness`, `completeness_reason`, `usage_total_usd`,
    `usage_read_at`, `run_kind` (FULL or PROBE);
  - local work (MS2-D-22, -23, -25, -32, -33): `dataset_id`, `kv_store_id`,
    `dataset_item_count`, `import_state`, `import_listing_ids` (bigint array),
    `stage_detail` JSON (per-stage error counts), `import_attempts`,
    `next_attempt_at`, `scraper_run` OneToOne null, `dataset_read_count`,
    `kv_read_count`, `final_charge_op_at`, `storage_state`,
    `storage_cleanup_due_at` (the absolute retention deadline, set at
    admission), `storage_cleanup_attempts`, `storage_deleted_at`, and
    (revision 10, ED-01) the API-call counters `run_poll_count` and
    `correction_read_count` (MS2-D-32). Revision 11 (R10-04, R10-06) states
    the semantics of two of these without changing the schema:
    `final_charge_op_at` is **write-once**, set at the work-completion
    barrier (MS2-D-32 *Settlement*), and `storage_cleanup_attempts` also
    counts the start-option-mismatch abort (MS2-D-32 *Per-call bound*).
- **Idempotent import** (revision 2; the durable stage machine is MS2-D-22):
  1. A replay that finds `finalized` or `rejected` is a no-op before any fetch.
  2. `observed_at` is the run's `startedAt`, which is deterministic and never
     overstates freshness.
  3. Provider imports use insert-if-absent on `(listing_id, observed_at)`.
  4. The `ScraperRun` is created once and reused on every retry.
  5. Idempotency is not ordering. A delayed import may still be older than
     state a newer run already wrote; MS2-D-30, -35, -36, and -37 guard that.
- **Retention.** The table holds no merchant content, only ids, counts, and
  status, so it is not retention-bearing (like `ScraperRun`). Raw dataset items
  land in `RawPayload` under the source's registered retention (MS2-D-25).
- *Rejected:* deduplicating by `RawPayload.content_hash`. It is not unique
  (Map Q5) and cannot express "this remote run was already imported".

**MS2-D-14 — Versioned Actor contract (Slice D, settled D7).**
- **hw-radar owns the contract** (revision 5, owner-overridden: the Actor is also
  built here, MS2-D-38).
  - The single contract artifact is the set of committed, plain JSON Schema
    (draft 2020-12) files in the Actor's own directory,
    `actors/hw-radar-<purpose>/contract/hw-radar-{input,listing,run}-v1.schema.json`.
    They sit inside the Actor's build context, so the Actor can validate its own
    output without reaching outside its directory.
  - hw-radar's Pydantic models in `acquisition/apify/contract.py` are tested for
    conformance: `Model.model_json_schema()` must equal the committed file, and
    the gate fails on drift in either direction. The Actor's own tests validate
    every emitted fixture row and `OUTPUT` record against the same files. So a
    contract change must edit both sides in one hw-radar PR.
  - The Apify-dialect files `.actor/input_schema.json` and
    `.actor/dataset_schema.json` are not the contract. A conformance test pins
    that the Apify input schema declares the same property set, required set,
    types, enums, and numeric bounds as `hw-radar-input-v1`. If D1 uses a dataset
    schema, a test pins its `fields` equal to `hw-radar-listing-v1`.
  - *Rejected (a):* revision 1–4's "Pydantic generates the schemas into hw-radar,
    and the separate Actor repo copies them". There is no separate repo now, and a
    copied file can drift silently.
  - *Rejected (b):* a shared Python package imported by both sides. It couples the
    Actor's build context and dependencies to hw-radar's package and invites a
    Django import into the Actor.
  - *Rejected (c):* the Apify-dialect schemas as the contract. They are not plain
    JSON Schema, so neither side can validate against them with standard tools.
  - *Reopen if* a second Hardware Radar Actor needs the same contract. Promote
    the files to a shared directory then, with `dockerContextDir` covering it.
  - `hw-radar-listing/v1` defines the dataset row: `schemaVersion`, `siteKey`,
    `sourceListingKey`, `url`, `title`, `price` (decimal string), `currency`,
    `shippingPrice`, `stockStatus`, `quantityAvailable`, `sellerName`,
    `conditionLabel`, `shipsFromCountry`, `categoryHint`, `collectionScope`,
    `mpn`, `observedAt`.
  - `hw-radar-run/v1` defines the default-KV `OUTPUT` record: `schemaVersion`,
    `status`, `completeness{complete, truncated, limitsHit{pages, requests, items,
    time, bytes}, pagesDeclared, pagesFetched, itemsDeclared, itemsEmitted}`,
    `queryScope` echo, `provider{actorName, actorVersion, sourceKind}` scope
    metadata, and `errors[]`. It follows the `pdf-evidence-reader`
    status/truncated/coverage pattern. (`limitsHit.bytes` and `provider` are
    revision 5 additions; D1 is unbuilt, so v1 is amended in place.)
  - `hw-radar-input/v1` defines the run input: `schemaVersion`, `siteKey`,
    `collectionScope`, `categoryHint`, `maxItems`, `maxPages`, `maxRequests`,
    `maxBytes`, `timeBudgetSecs`, plus source-specific fields. It has no proxy,
    browser, CAPTCHA, or unblocker field (MS2-D-44). hw-radar validates every
    input against it **before** the start request, so an invalid request never
    reaches paid execution. Apify's own input-schema validation is a second layer
    whose pre-billing behavior D3 verifies (MS2-D-15).
- **Classifier.** `classify_run(remote_status, output, dataset_count,
  usable_count) -> (RunCompleteness, reason)`. `usable_count` is the number of
  dataset rows that pass contract validation.
- **Completeness mapping:**
  - `complete` requires all of: Apify `SUCCEEDED`, `OUTPUT.completeness.complete`,
    no limit hit, and `itemsEmitted` = dataset count = `usable_count`.
  - **Proven complete-empty** is `complete` with reason `complete_empty`. It
    requires all of: `SUCCEEDED`; a valid `OUTPUT` with `complete=true` and no
    limit hit; `pagesFetched ≥ 1`; `itemsDeclared == 0` and `itemsEmitted == 0`,
    both present and non-null; and a dataset count of 0. ADR 0021 requires this
    to be distinguishable from a failed collection.
  - `truncated` covers any limit hit or `TIMED-OUT`. Revision 5: the classifier
    also returns the `TruncationReason` (MS2-D-11) from `limitsHit` in the fixed
    precedence `time, bytes, requests, pages, items` (`TIMED-OUT` ⇒ `time_limit`,
    `bytes` ⇒ `resource_limit`), and records every hit limit in the reason text.
  - `partial_failure` covers `FAILED`/`ABORTED` with items, or `errors` non-empty.
  - `failed` covers:
    - a missing or invalid `OUTPUT`, or an unknown `schemaVersion`;
    - a **non-empty dataset with zero usable rows** (`no_usable_items`);
    - an **empty dataset without complete-empty evidence** (`ambiguous_empty`);
    - revision 5 (owner-clarified (s2, 2026-09-24); rev 4 left these rows
      unassigned): a **self-contradictory report** (`contradictory_report`):
      `complete=true` together with any limit hit; `truncated=true` with no limit
      hit and a non-`TIMED-OUT` status; `SUCCEEDED` with `OUTPUT.status` other
      than `succeeded`; `itemsEmitted` ≠ the dataset count; or `pagesFetched` >
      `pagesDeclared`;
    - a **scope mismatch** (`scope_mismatch`): the `queryScope` echo or
      `provider.actorName` differs from what the run was admitted for.

    Contradiction fails closed: nothing is persisted, and a FULL run breaks
    continuity (MS2-D-22 *Reject*).

  Missing or ambiguous evidence is never `complete` (DR-011). `failed` persists
  nothing (the import is `rejected`, MS2-D-22).
- **Raw batch shape.** `RawBatch.items` holds dataset rows only. The `OUTPUT`
  record is stored on `provider_run` and is never a `RawItem`. So the zero-record
  parser-rot guard (`batch.items and not parsed`, `pipeline.py:456`) passes a
  complete-empty batch and still rejects non-empty unusable output. A
  complete-empty result with a `scope_key` delists that scope's active listings
  through the normal `ABSENT_FROM_SWEEP` path.
- **Fixtures.** Frozen fixtures for each state live in hw-radar
  (`tests/fixtures/apify_contract/v1/`). Revision 5 (owner-overridden): the Actor
  lives in `actors/` in this repository and validates against the same committed
  schemas (MS2-D-38); nothing is copied to another repository.
- **Storage cleanup.** The run's default dataset and KV store are deleted by
  deadline, independent of import success (MS2-D-25).

**MS2-D-15 — Apify transport (Slice D).** Use a thin async `httpx` client over the
seven run and storage REST calls needed: start run, get run, abort run (on a start-option
mismatch, MS2-D-26, or a passed retention deadline, MS2-D-33), list dataset
items, get KV record, delete dataset, and delete KV store. Revision 1 listed
five calls; F-08 and F-10 added abort and KV-store delete. Revision 5
(owner-clarified (s2, 2026-09-24)) adds two free-of-platform-usage account reads
for MS2-D-40, making nine: get account limits (`GET /v2/users/me/limits`, which
carries `monthlyUsageCycle` and the account limit) and get monthly usage
(`GET /v2/users/me/usage/monthly`, optional `date`, per-cycle and per-day service
usage). If the limits response lacks the plan's base price and prepaid credit,
D3 adds a tenth read, `GET /v2/users/me` (its plan block). Revision 10 (entry
gate, ED-01): the docs do not say whether these account reads, `GET` run
polls, or build reads bill, so the design no longer waits for E2 to verify
it. Each such call is counted, capped, and priced at the API-call bound
(MS2-D-32) whether or not it bills, and F5a measures the real cost. Revision 8 (review R7-01) adds one more read for MS2-D-46's operator
builds: get build (`GET /v2/actor-builds/{buildId}`), whose record carries the
build's status, `finishedAt`, and usage (Apify prices a historical run *or
build* the same way; research input `apify-billing.md`). The count becomes ten,
or eleven with the plan-block read. The build record's exact wire names are
unconfirmed until D3, like the run's; if it carries no dollar usage, a build
settles at its bound (MS2-D-46). A build read is priced like the other `GET`
reads (revision 10). Revision 11 (R10-01, R10-06): MS2-D-32 *Per-call bound*
enumerates every external call hw-radar makes, runtime and operator, with
its committed counter, its documented billing units, and its reservation
component. A call outside that table is not made. Revision 12 (owner
decision (s5, 2026-09-25), R25): the three account reads (`GET
/v2/users/me/limits`, `/v2/users/me/usage/monthly`, `/v2/users/me`) are
**removed** from the runtime client, and nothing replaces them. The runtime
surface is the seven run and storage calls plus get build. Account state
comes from operator-verified configuration (MS2-D-48). *Rejected:* keeping
the methods in the client but calling them from nowhere. A later change could
wire them back, and the runtime token gets 403 on them anyway. Removing them
makes the rule structural, and a static test can check it (E9.4).
- `httpx` is already a dependency, and tests use `httpx.MockTransport` / vcrpy
  cassettes.
- *Rejected:* `apify-client` 3.2.0 (released 2026-09-03). Since 3.0.0 it uses
  `impit` as its required default transport; the optional `httpx2` extra is a
  separate package, not the `httpx` we already have. It would add a dependency
  for seven calls, and its return types changed across 2.x→3.x.
- **Confirmed facts the client relies on** (docs.apify.com, retrieved
  2026-09-24; prep report for D):
  - Run status lifecycle: `READY` → `RUNNING` → terminal `SUCCEEDED | FAILED |
    TIMED-OUT | ABORTED`, reached via the transitional `TIMING-OUT` and
    `ABORTING`.
  - Run object fields (Python-client attribute names; the camelCase wire names
    follow the same aliasing): `id`, `status`, `started_at`, `finished_at`,
    `build_id`, `build_number`, `default_dataset_id`,
    `default_key_value_store_id`, `options.memory_mbytes`,
    `options.timeout_secs`, `options.max_items`, `stats.compute_units`,
    `usage` (a per-component breakdown, nullable), and
    `usage_total_usd: float | None`.
  - `usage_total_usd` is nullable. Its value right at `SUCCEEDED` is
    unconfirmed, and Apify says historical dollar amounts are recomputed at
    current pricing and are "for informational purposes only". It is therefore
    re-read until non-null (MS2-D-23).
  - **Naming trap.** The start request sets the run timeout with the REST query
    parameter `timeout` (seconds) and memory with `memory` (MB). The Python
    client names them `run_timeout` (a `timedelta`) and `memory_mbytes`. The run
    response reports them as `options.timeoutSecs` / `options.memoryMbytes`.
    None of these is the client's per-request HTTP `timeout` tier. The Actor's
    `timeout` must never be confused with the httpx request timeout.
  - `max_total_charge_usd` applies only to pay-per-event Actors. It is not a
    cost bound for a self-owned Actor. Revision 10 (entry gate, ED-14): the
    docs contradict this. The run-start reference
    (`api/v2/actors-runs-post`, retrieved 2026-09-25) describes
    `maxTotalChargeUsd` as capping "the total amount charged for all pricing
    models", while the Store page describes it for pay-per-event only. The
    design does not depend on it either way.
  - Revision 5 billing facts (official docs retrieved 2026-09-24; research
    input `apify-billing.md`, not committed):
    - The first Get Run response after completion "can still show preliminary
      `stats`, costs, and event counts"; Apify advises waiting about 10 s and
      reading again. The first figure is therefore **provisional** (MS2-D-41).
    - Dollar figures read back later for a historical run are recomputed at
      *current* pricing. The finalized figure is captured once and never
      re-derived from a later read.
    - Dataset reads and writes after the run finishes are platform usage billed
      to the **account**; no source says they are added to the run's
      `usageTotalUsd`. Post-run cost is therefore accounted from hw-radar's own
      operation counters (MS2-D-32, MS2-D-41), and measured by diffing the
      account's monthly usage (F5a).
    - `usageTotalUsd` and `usageUsd` are returned only to authenticated reads.
    - `maxTotalChargeUsd` and the `maxItems` run option apply only to
      pay-per-event and pay-per-result Actors. They are **not applicable** to
      Hardware Radar's own Actors (settled). The client does not send
      `maxItems`; the Actor input's own caps bound items (MS2-D-26). If a later
      verification shows either applies to a private pay-per-usage Actor, it is
      added as an extra defense only, never as the correctness mechanism.
      Revision 10 (ED-14): `maxItems` is confirmed pay-per-result only, so
      "(settled)" now covers `maxItems` alone. Sending `maxTotalChargeUsd` at
      the execution bound, as that extra defense, needs a later plan revision
      (for example after F5a); until then
      `test_client_never_sends_max_items_or_max_total_charge` stays.
- **Unconfirmed until D3:** the exact wire names of `usageTotalUsd`,
  `buildNumber`, and the `usage` component keys; the response to aborting a run
  that already finished; whether Apify rejects an input that fails the Actor's
  input schema before any billable run exists (revision 5); and whether a
  scoped (limited-permission) token can read `/users/me/limits` and
  `/users/me/usage/monthly` (revision 5). D3 verifies these against the official
  API reference, and F5a confirms them live. MS2-D-26 does not rely on
  platform `maxItems` enforcement. Revision 10 (entry gate, retrieved
  2026-09-25): `usageTotalUsd` (nullable), `buildNumber`, and the `usage` /
  `usageUsd` component keys are confirmed in `api/v2/actor-run-get`; aborting
  a run that is already `FINISHED`, `FAILED`, `ABORTING`, or `TIMED-OUT` "does
  nothing" (`actor-run-abort-post`), though the response code is not stated,
  so MS2-D-33 no longer depends on it (ED-15).
- *Reopen if* the needed surface grows beyond these calls.
- The token comes from `HW_RADAR_APIFY_TOKEN`, rendered from the Hardware
  Radar runtime path `secret/apps/hw-radar/apify` (MS2-D-43). The owner scopes
  it as narrowly as the runtime allows: run the Hardware Radar Actor(s), read
  and delete their run storages, and read account limits and usage. If a scoped
  token cannot read the account endpoints, account-headroom admission denies
  with `account_state_unobservable` until the owner decides the token scope (R25).
  Revision 9 (owner decision (s4, 2026-09-25), OQ25): the owner's dedicated key
  is unscoped and serves the operator/deploy role only, so it is never a runtime
  fallback for an account read that the scoped token cannot make. The scoped
  runtime token does not exist yet.
  Revision 12 (owner decision (s5, 2026-09-25), R25): the owner created the
  scoped token on 2026-09-25, and the pre-probe answered the question left
  open above. All three account endpoints return 403
  `insufficient-permissions`, and Apify offers no account or usage
  permission for scoped tokens. The runtime therefore makes no account read
  (MS2-D-48), and the token no longer needs to read account limits or usage.
  The same pre-probe got 404 on `GET` of the Hardware Radar Actor, its runs,
  and its builds, so the token also needs **Read** on that Actor: the poll
  job's `GET /v2/actor-runs/{id}` and the build selector's `GET
  /v2/actor-builds/{id}` (`jobs._build_unit`, selector 4) use the runtime
  client (R25 residual).

**MS2-D-16 — Completion observation (settled D4).**
- **Poll job.** An APScheduler `apify-poll` interval job (`max_instances=1`,
  `coalesce=True`) runs three independent selectors each tick: active remote
  executions, terminal rows with outstanding local work, and overdue storage
  regardless of remote status (MS2-D-23, MS2-D-33).
- **Start job.** The existing full-lane job for a source with
  `collection_provider=apify` *starts* a run instead of calling `run_source`.
  The recovery-probe job dispatches the same way (MS2-D-24). There is one
  scheduling owner and no Apify schedules or webhooks.

**MS2-D-17 — Apify spend ledger (Slice E, migration 0022; settled D3).**
- **Period** (revision 5, **owner-overridden**). The authoritative budget period
  is the account's actual Apify billing cycle, discovered from the API
  (`GET /v2/users/me/limits` → `monthlyUsageCycle.startAt/endAt`) and stored per
  cycle (MS2-D-40). It is never a hard-coded calendar month and never a rolling
  window. Verified account state 2026-09-24: an anniversary cycle,
  2026-09-05T00:00:00Z → 2026-10-04T23:59:59.999Z.
  - Revision 12 (owner decision (s5, 2026-09-25), R25): the cycle is still
    the account's actual Apify billing cycle, but the runtime no longer
    discovers it from the API. The runtime derives it from the configured
    anchor `HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR`, which the operator verifies
    against `monthlyUsageCycle.startAt` with the operator key before
    enabling (MS2-D-48 *Cycle*). The drift risk that revision 5 cited as the
    reason to read the cycle becomes R39. The runtime cannot detect a drift,
    so the operator procedure covers it and Apify's 402 is the fail-closed
    signal.
  - *Withdrawn:* revision 1–4's "rolling 31-day window" and its rejection of the
    billing cycle ("an extra API read; the boundary drifts if the plan
    changes"). The owner ruled the cycle authoritative. The extra read is
    cheap and cached (MS2-D-40), and a drifting boundary is exactly why it is
    read, not assumed.
  - A trailing 31-day total remains only as a secondary **trend and safety
    metric** in the spend report and a warning log. It never admits or denies.
  - *Rejected (a):* calendar month UTC. It can put up to 2× the cap inside one
    billing cycle that straddles a month boundary.
- **Reservation estimate.** It is the sum of the bounded charge components in
  MS2-D-26, times `(1 + margin)`. Revision 1's single compute formula plus an
  assumed overhead is withdrawn: it bounded compute only.
- **Admission.** Under the budget advisory lock (MS2-D-26), a paid run is
  admitted only if both MS2-D-40 checks pass: the project-allocation check for
  its class and the account prepaid-headroom check.
  - Class limits (revision 5): `watch_refresh` → the cycle's project allocation
    `A`; `discovery` → `A − HW_RADAR_APIFY_WATCH_REFRESH_RESERVE_USD`, so
    discovery degrades first. Remote recovery probes use `discovery`
    (MS2-D-24).
  - Outstanding reservations, including stuck or unreconciled runs of any age,
    count at their estimate. This fails closed.
  - Denials are recorded as ledger rows.
  - A kill switch `HW_RADAR_APIFY_ENABLED` (default false) denies all new
    paid admission. Revision 10 (entry gate, ED-07; replaces "denies
    everything"): that means new starts, probes, and operator reservations
    only. The poll job's selectors 1–4 (MS2-D-23) keep running while it is
    false, because they settle liability already reserved: imports, storage
    deletes, usage reads, and closing reads. The overrun-latch rule on repair
    reads (MS2-D-22) is unchanged. A disabled environment can therefore drain
    and export (MS2-D-45), and MS2-D-33 deadlines are still enforced. Paid
    admission is also denied by missing unit prices, a tripped overrun latch
    (MS2-D-26), an unknown or stale billing cycle, an unobservable account
    state, and a plan whose base price exceeds the cash ceiling (MS2-D-40). Revision 6 adds an unset or
    exceeded external-liability bound (MS2-D-40) and a missing ledger authority
    (MS2-D-45). Revision 9 (OQ26): the bound defaults to the owner's 5.00, so
    "unset" now means an explicitly empty or invalid value. Revision 11
    (R10-01) adds an unaccepted residual R38
    (`call_billing_residual_unaccepted`, MS2-D-26 *Reservation*).
- **Reconciliation.** Settlement follows MS2-D-32 and MS2-D-41. A non-null
  `usage_total_usd` alone never releases a reservation. The reservation is
  reconciled only after the import is terminal, storage deletion is verified,
  run usage is finalized, and the post-run cost is accounted. The
  outstanding-work selector (MS2-D-23) drives the re-reads. Reconciliation
  takes the same lock as admission. An actual above the reservation trips the
  latch (MS2-D-26, MS2-D-41); it is never merely logged.
- **Freshness.** `budget_paused` holds for a source when its newest ledger event
  is a budget denial after its last imported run, or while the overrun latch is
  tripped. Revision 5: the denial reason (`class_cap`, `account_headroom`,
  `overrun_latch`, `cycle_unknown`, `account_state_unobservable`, …) is part of
  the visible state (MS2-D-41). Revision 12 (owner decision (s5,
  2026-09-25), R25): `cycle_unknown` now means that the configured anchor is
  unset, invalid, or in the future, or conflicts with a recorded cycle.
  `account_state_unobservable` now means that an operator-verified account
  setting is unset or invalid (MS2-D-48 *Reasons*). A stale snapshot and a
  failed read no longer exist.
- *Rejected:* fusing with ADR-0016 `SearchBudgetGate` semantics. That gate is
  unbuilt and search-specific. Only the reserve-then-reconcile pattern is reused.

**MS2-D-18 — Per-source provider selection (Slice D, migration 0021).**
`SourceConfig.collection_provider` is choices `local | apify`, default `local`.
- A CHECK enforces `collection_provider='local' OR (heartbeat_enabled = false AND
  fast_lane = false)`. Heartbeat and fast-lane are local-only in MS-2.
- Switching the column never touches listings or watch state.
- The Actor reference per source is resolved from settings (env), not committed.

**MS2-D-19 — Pilot set and the Actor-proof source.** These are recommendations
only; the owner decides (settled D8 as revised).
- **Local paths:**
  - eBay Browse, extended to GPU (27386), RAM (170083), and CPU (164) `category_ids`
    sweeps, one ID per request, with pagination (facts in F1);
  - WD Recertified;
  - Seagate Recertified;
  - ServerPartDeals, whose non-drive collections are still unverified.
- **Actor proof** (revision 5, owner-clarified (s2, 2026-09-24); OQ24 split). Two
  separate decisions replace revision 4's single "owner picks a merchant":
  - **(a) First Actor proof = a controlled synthetic source through a real
    private Apify Actor** (settled; MS2-D-42, task F5a). It exercises real
    compute, run lifecycle, dataset retrieval, delayed completion, cost
    accounting, and API behavior with no merchant legal question.
  - **(b) A production Actor-backed merchant source** is a separate
    source-admission decision per candidate (MS2-D-44, task F5b). It stays open
    as OQ24, and it is not an MS-2 exit condition (revision 9, owner decision
    (s4, 2026-09-25), [OQ28](../../resolved-questions.md#oq28--can-ms-2-exit-on-the-synthetic-proof-alone)). Newegg stays excluded because its Terms of Use prohibit automated
    access and scraping (R1 has the evidence). B&H, ServerPartDeals, and
    refurbished server-parts sellers each need a source-admission record before
    selection. Existing local connectors are not grandfathered into an Actor
    path.
- **Slice D stays source-agnostic.** It is built and tested against the frozen
  contract with a synthetic fixture source.

**MS2-D-20 — Evaluation is bound to its inputs and wired on every ingestion path
(Slice C; review F-03).**
- **Validity.** A `watch_evaluation` row is *current* iff all five hold:
  - `snapshot_observed_at` equals the listing's latest `OfferSnapshot.observed_at`;
  - `resolution_id` equals the listing's current edge id (`is_current=True`),
    or both are null;
  - `requirement_version` equals the watch's `requirement_version`;
  - `evaluator_version` equals the running `EVALUATOR_VERSION`;
  - `catalog_fingerprint` equals the fingerprint of the listing's current
    catalog inputs, recomputed at read time (MS2-D-29, revision 3).
- **Read model.** Only current `match` rows qualify for `shortlist()`. A
  non-current row of any verdict is `pending`. It is shown by `review_queue()`
  with its stale binding, and it never qualifies.
- **Failure path needs no write.** Any new evidence that the evaluator did not
  assess leaves the old row non-current. That covers an evaluator exception,
  a crash, a resolver failure, and an unfinished import stage. The old row
  therefore drops out of the shortlist.
- **Wiring.** `run_collection` gains `evaluator: ListingEvaluator | None = None`.
  `None` binds the production `WatchEvaluator`; it is not a null object. So every
  path through `run_collection` evaluates with no per-caller wiring:
  `poll_source`; the FULL run that `run_heartbeat` fires (`heartbeat.py:228`);
  `recovery_probe_job` PROBE runs; and provider-import stage 4 (MS2-D-22).
  Tests inject a fake. A listing whose resolution raised in this run is not
  evaluated and stays pending.
- **Repair.** The next observation re-evaluates. `evaluate_watches --pending`
  re-evaluates every non-current row on demand.
- *Rejected (a):* an evaluator parameter each caller must pass, with a null
  default. That is exactly the omission F-03 found on the heartbeat path; any
  future caller would silently skip evaluation.
- *Rejected (b):* write-time invalidation, which marks rows pending inside the
  persistence transaction. It adds a write to the ingestion transaction and still
  leaves crash windows. Read-time binding cannot be skipped.
- *Rejected (c):* a scheduled evaluation-backlog job in MS-2. `pending` already
  fails closed. *Reopen if* F3 measures a material pending backlog.

**MS2-D-21 — New-category acceptance policy (Slice B; review F-09).**
- **Policy object.** `CategoryRules.acceptance: AcceptancePolicy | None`.
  - Drive: `None`. No gate applies, drive decisions are untouched, and A0 guards
    this.
  - gpu, ram, cpu, and every basic-watch category:
    `AcceptancePolicy(authoritative_source_kinds={"catalog_authoritative"},
    grains={MODEL, VARIANT})`.
- **Where it applies.** The resolver applies the policy after `ladder.decide`
  returns and before the `auto_accept` flag.
- **Rung 1.** An `ACCEPT` stands only if two things hold: the winning alias hit
  (the ladder's highest-confidence single-target hit) has an authoritative
  `source_kind`, and the target grain is approved. Otherwise the result is
  `REVIEW` with evidence
  `acceptance_policy={"source_kind": ..., "grain": ...}`. The OEM family fan-out
  accepts at family grain, so for these categories it becomes `REVIEW`.
- **Evidence for rung 0.** Accepted non-drive edges record
  `evidence["alias_source_kind"]`.
- **Rung 0.** A prior is inherited only if its edge is an authoritative
  `exact_alias` accept, or a `manual` owner decision. Any other prior becomes
  `REVIEW`.
- **Manual and learned aliases stay visible.** `manual` and `listing_derived`
  aliases stay in `alias_hits`. That includes aliases the resolver learned from
  earlier observations (`resolver.py:374-405`). So a collision stays
  reviewable; it never becomes a silent `none`.
- *Evidence:* `source_kind` sets confidence only (`ladder.py:28-32`, `:195`). It
  does not gate acceptance.
- *Rejected:* filtering non-authoritative hits out before `decide`. That turns
  reviewable collisions into `none`.
- *Reopen if* the owner ratifies `manual` aliases for a category (R4).

**MS2-D-22 — Durable staged provider import (Slice D; review F-05).**
Replaces revision 1's "persist and mark `imported` in one transaction".
- **Stages.** `import_state` moves `pending → observations_committed →
  absence_applied → resolved → evaluated → finalized`. The only other terminal
  state is `rejected`.
- **Transitions.** Each transition is a compare-and-set under
  `select_for_update` on the row. Every stage's effects are idempotent, so a
  duplicate executor stops at the compare-and-set.
- **Claim.** In its own transaction the claim creates the `ScraperRun`
  (RUNNING, `started_at` = Apify `startedAt`) if absent, links it, and increments
  `import_attempts`. Every retry reuses that `ScraperRun`.
- **Stage 1 (one transaction).** Before the transaction:
  - Check the retention preconditions (MS2-D-33). If the deadline has passed
    or the content is already past its source TTL, go to *Reject* before any
    read or canonical write.
  - Count the read against the read cap, then read and validate the dataset
    and `OUTPUT` (MS2-D-32). If the cap is exhausted, go to *Reject* with
    `read_cap_exhausted`.
  - Run `classify_run`. A `failed` result goes to *Reject*.

  Otherwise the transaction does: `store_raw`; the scope-row lock and the
  ordering-guarded listing observation, including absence-gated content
  writes, relist, and creation (MS2-D-30, MS2-D-35); insert-if-absent
  snapshots with per-observation retention (MS2-D-37); `import_listing_ids`;
  and state `observations_committed`. The transaction re-checks the deadline.
  A crash rolls the whole stage back; a retry after process loss is another
  counted read. Revision 10 (entry gate, ED-05): a transaction aborted by a
  deadlock, serialization failure, or unique violation is retried in the same
  process with the dataset already in memory (MS2-D-35 *Serialization*), so a
  benign lock conflict never spends the read cap. Revision 11 (R10-10): each
  retry rebuilds its state from the immutable fetched batch, and a fourth
  retryable abort *exhausts* the attempt with no partial effect; only the
  next invocation, after backoff, may make another counted read (MS2-D-35
  *In-memory retry*).
- **Stage 2 (one transaction).** Continuity is recorded or broken for the
  run's admitted `scope_key`, in event-time order (MS2-D-11, -31, -36). For a
  FULL run, and because every remote run is scoped (MS2-D-13), stage 2 also
  breaks the NULL scope at the run's `startedAt` (MS2-D-31, -36), under the
  FULL lane-row lock, which it takes first (revision 10, ED-18). The
  gated scoped delist then runs with the newer-observation guard (MS2-D-12,
  -14, -30). A gated complete FULL scope also raises that scope's
  complete-sweep watermark (MS2-D-35). The state becomes `absence_applied`.
  PROBE runs skip absence, continuity, and the watermark (existing rule).
- **Stage 3.** Resolve each id in `import_listing_ids`, then set `resolved`.
  Resolver errors are counted in `stage_detail` and never block.
- **Stage 4.** Evaluate each listing whose resolution did not raise (MS2-D-20),
  then set `evaluated`. Evaluator errors are counted; the affected rows stay
  pending (fail-closed).
- **Stage 5 (one transaction).** Set the `ScraperRun` to SUCCESS with its
  counts and `detail_json` (provider evidence, resolver and evaluator errors,
  `listings_delisted`). Apply `apply_run_outcome`. Set `finalized`. The
  compare-and-set makes the lifecycle outcome apply exactly once.
- **Reject (one transaction).** Covers `failed` completeness, an invalid
  contract, unknown retention (MS2-D-25), a storage deadline passed before
  stage 1, content already past its source TTL (MS2-D-33), and an exhausted
  read cap (MS2-D-32). The `ScraperRun` becomes FAILED with a classification,
  and the state becomes `rejected`. Revision 11 (R10-11): Reject is an outcome
  transaction, so it locks `provider_run` → `SourceConfig` → FULL lane row →
  the admitted scope's `scope_sweep_continuity` row (FULL only), with the
  scope row ensured beforehand (MS2-D-35 *Serialization*). What else happens
  depends on `run_kind` (revision 3, review F-07 residual):
  - **FULL:** the failure lifecycle outcome is applied, and continuity is
    broken for the run's admitted `scope_key`, with the run's `startedAt` as
    the break's event time, or `admitted_at` when no start response was
    recorded (MS2-D-31, MS2-D-36).
  - **PROBE:** the outcome is `PROBE_FAILURE` (state-neutral, MS2-D-24).
    Continuity is never touched, so a rejected probe cannot shorten or reset a
    lane it did not sweep.
- **Resumption.** The outstanding-work selector (MS2-D-23) resumes a row from its
  recorded stage. Only stage 1 needs the dataset.
- **Overrun blocks repair reads** (revision 5, owner-clarified (s2, 2026-09-24)).
  While the overrun latch is tripped, a stage-1 retry that would read the dataset
  or the `OUTPUT` record again is not attempted: an overrun never triggers another
  paid call to repair itself. The row waits, visibly, until the latch clears.
  Retention-required storage deletion (MS2-D-25, MS2-D-33) still runs, because its
  cost is already reserved and deletion stops further storage accrual.
- **Code shape.** D extracts the stages from `run_collection` into functions.
  The local path composes the same functions in memory with no durable markers.
  A crashed local run is repaired by the next poll, and its evaluations stay
  pending (MS2-D-20). Revision 10 (entry gate, ED-04): today's local path has
  no transaction; `_persist_all` and the delist and continuity calls run
  statement by statement in autocommit (`pipeline.py:254-306`, `:488-501`).
  D10 **introduces** the local path's two transactions (D10, *Local
  transactions*), because `select_for_update` outside `atomic()` raises
  `TransactionManagementError`. A crash mid-persist now rolls back the whole
  local batch, where today it persists partially.
- *Rejected (a):* committing persistence and a terminal marker together
  (revision 1). A crash after that commit strands delist, resolution,
  evaluation, and finalization forever.
- *Rejected (b):* one transaction over all stages. A resolver or evaluator
  failure must not roll back ingestion (C.3), and the transaction would hold
  locks across hundreds of resolutions.
- *Rejected (c):* an external task queue. It is a second scheduler (ADR 0012).

**MS2-D-23 — Remote execution status is separate from local outstanding work
(Slice D, E; review F-06).**
- **Two axes.**
  - Remote: `remote_status` is the Apify enum. `remote_terminal_at` is set when
    the job first sees a terminal status. It is an observation fact only and
    never anchors a deadline (MS2-D-33).
  - Local: `import_state` (MS2-D-22), `storage_state`
    (`retained | deleted | delete_failed`, MS2-D-25), and, from E, the
    reservation `status` (`reserved | usage_provisional | usage_finalized |
    reconciled`, MS2-D-41; revision 5 renames rev 4's `usage_observed`).
- **Selector 1, active.** Rows where `remote_status` is set and non-terminal.
  A row with a null status has no run id to poll, so only selector 3 covers
  it. Each row is polled with `GET` run, which updates status, ids, and usage.
- **Selector 2, outstanding.** Rows where `remote_status` is terminal and any of
  these holds:
  - `import_state ∉ {finalized, rejected}`;
  - `storage_state ≠ deleted` and the import is terminal;
  - (E) the reservation is not yet `reconciled` (MS2-D-32).

  (E, revision 8, review R7-01) It also selects operator build reservations
  (`operator_kind = build`) with a bound `provider_build_id` that are not yet
  `reconciled`. Their unit reads the build record (MS2-D-15) and settles the
  row by MS2-D-41 and MS2-D-46; such a row has no `provider_run`.
- **Selector 3, overdue storage (revision 3, MS2-D-33).** Rows where
  `storage_state ≠ deleted` and `storage_cleanup_due_at ≤ now`, whatever the
  remote status: null (start response never recorded), non-terminal, or
  terminal. It does not depend on selector 1 ever having observed termination.
- **Selector 4, correction monitoring (revision 7, review R5-03; closure
  revised in revision 8, reviews R5-03 and R5-04).** Reservation rows with
  `status = reconciled`, a provider figure to re-read (the `provider_run`'s
  run id, or an operator build's `provider_build_id`), a set
  `correction_monitor_until`, a null `correction_monitor_closed_at`, and
  `next_usage_read_at ≤ now`, whatever the import and storage state. So a run
  that is reconciled and fully cleaned up is still polled. There is no
  `correction_monitor_until > now` condition: a row whose deadline has passed
  stays selected until it is closed. `correction_monitor_until` is persisted
  at reconciliation as `last_charge_at + HW_RADAR_APIFY_CORRECTION_WINDOW_S`
  (MS2-D-41). `next_usage_read_at` is never scheduled past the deadline while
  the deadline is in the future, so a closing read is due at the deadline.
  Revision 11 (R10-03): both values are fixed at reconciliation; no
  monitoring read moves the deadline. The reads are themselves charges after
  `last_charge_at`, so they are debited through the row's separate
  monitoring interval (MS2-D-34 *Monitoring charges*), and every read, a
  failed one included, stamps `monitoring_charge_last_at` in the transaction
  that increments its counter before the read is sent.
  - *Every read.* A successful read (the record is returned with a non-null
    usage total) appends an `ApifyUsageRead` and applies any upward correction
    in **one** transaction under the budget lock (MS2-D-47). A read is never
    committed without its correction.
  - *Closing read.* The first successful read with `read_at ≥
    correction_monitor_until` is the closing read. The same transaction that
    appends it and applies its correction sets `correction_monitor_closed_at`
    and `correction_closing_read` (the read's id). Monitoring is closed only
    when that transaction commits; a rollback leaves the row open.
  - *Failed closing read.* A transport error, a missing record, or a null
    usage total closes nothing. The row keeps its obligation, backs off through
    `next_usage_read_at`, and appears in the spend report as
    `correction_close_overdue` (visible stale state, MS2-D-46 *Reporting*,
    E6). So a poller outage that spans the deadline delays closure; the
    restart's closing read still applies a correction that arose during the
    outage.
  - Rows with nothing to re-read (inspection envelopes, which settle at their
    full bound, MS2-D-46) carry no obligation: `correction_monitor_until` stays
    null, and selector 4 never selects them.
- **Isolation and restart.** Each unit of work is claimed and committed
  separately, with per-row backoff via `next_attempt_at`, so one failing row
  never starts the others. Every selector reads only persisted state, so a poller
  restart resumes every row.
- *Rejected:* revision 1's "poll non-terminal rows and import terminal ones". A
  row whose remote status is terminal would never be selected again. That
  strands unfinished imports, failed deletions, and late usage.
- *Rejected:* revision 2's terminal-only storage clause. A run whose terminal
  status was never observed (poller outage, lost start response) had no
  deadline selector at all.

**MS2-D-24 — Recovery probes dispatch by the selected provider (Slice D;
review F-07).**
- **Dispatch.** `recovery_probe_job` branches on
  `SourceConfig.collection_provider`.
  - `local` runs today's path byte for byte.
  - `apify` calls `start_provider_run(source, run_kind=PROBE)`. That path uses
    the same `check_admission(PROBE)` and then budget admission as class
    `discovery`, so a paused source cannot spend watch-refresh headroom. It
    applies the bounded run options (MS2-D-26), allows at most one outstanding
    probe run per source, completes asynchronously (MS2-D-22/-23), and uses
    registered retention (MS2-D-25).
- **Probe outcome.** When a PROBE import finalizes, the outcome is
  `PROBE_SUCCESS` iff completeness is `complete` or `truncated`. Otherwise it is
  `PROBE_FAILURE`, which is state-neutral.
  - A budget denial starts no run. The source stays paused and the denial is
    recorded.
  - A **rejected** PROBE import (invalid `OUTPUT`, ambiguous-empty output, a
    passed storage deadline, or any other MS2-D-22 reject reason) records
    `PROBE_FAILURE` (revision 3, review F-07 residual).
  - A PROBE import never reaches absence or continuity, on any path,
    including *Reject*.
- **The local adapter never runs for an Actor-backed source.** It is never
  invoked for an `apify` source in any job, even when one is registered.
- *Rejected:* probing an Actor-backed source with its local adapter. That
  "recovers" the wrong execution provider, and an Actor-only source could
  never recover.

**MS2-D-25 — Provider-independent source retention and remote storage cleanup
(Slice D; review F-10).**
- **Registry.** A new pure module `acquisition/retention_policy.py` holds
  `SOURCE_RETENTION: Mapping[str, AdapterRetention]`, keyed by `site_key`.
  `source_retention(site_key)` raises `UnknownSourceRetention` for an
  unregistered key.
- **Remote imports.** Every remote import passes the policy explicitly to the
  stage functions as a required keyword with no default. Unknown retention
  rejects the import. It never falls back to `run_collection`'s `merchant_fact`
  default (`contracts.py:162-180`).
- **Local path.** The local path keeps `adapter_retention(adapter)` in MS-2.
  A test pins `adapter_retention(ADAPTERS[k]()) == source_retention(k)` for every
  registered adapter, so the two sources of truth cannot drift. An Actor-only
  source must be registered in the registry.
- **Cleanup deadline.** The deadline is absolute and set at admission
  (MS2-D-33, revision 3):
  `storage_cleanup_due_at = admitted_at +
  min(HW_RADAR_APIFY_STORAGE_CLEANUP_MAX, bounded_ttl / 2)`.
  Revision 2 anchored it at `remote_terminal_at`, the first *locally observed*
  termination, so a poller outage longer than a source TTL granted content a
  fresh allowance. That anchor is withdrawn.
  - The default for `HW_RADAR_APIFY_STORAGE_CLEANUP_MAX` is 24 h (an
    assumption; tunable).
  - The `bounded_ttl / 2` term applies only to bounded sources, which MS-2
    denies an Actor path anyway (MS2-D-33). It stays in the formula so the
    bound is already correct if that denial is ever lifted.
  - The deadline is far inside the account's data retention, which the
    limits read reports as `limits.dataRetentionDays` (31 days on the
    verified STARTER account). Revision 10 (entry gate, ED-09) withdraws the
    help-center "7 days on Free, 31 days on paid" figures: the official pages
    disagree (unnamed storages "expire after 7 days unless otherwise
    specified"; paid-plan data "follows your plan's retention period, which you
    can configure in your billing settings"; the ten most recent runs are
    stored "indefinitely"). Revision 11 (R10-07) withdraws "the storage
    liability rests on the checked retention". The 2026-09-25 docs still
    conflict (`platform/storage`: on paid plans "All data (including your 10
    most recent runs) follows your plan's retention period";
    `actors/running/runs-and-builds`: "Apify securely stores your ten most
    recent runs indefinitely"), and a failed deletion can stop new runs and
    leave its run among the ten most recent. No platform expiry is therefore
    relied on: storage that is not verified deleted is a **recurring
    liability** with a full-cycle storage bound in every cycle it can exist
    (MS2-D-26 table, MS2-D-32 *Deletes*).
- **Cleanup.** Cleanup deletes the run's default dataset and default KV store.
  It runs for successful, rejected, failed, and abandoned runs. It fires when
  the import reaches a terminal state (selector 2) or when the deadline passes
  (selector 3), whichever comes first, independent of import success and of
  any remote-status observation.
  - *404 rule* (revision 10, ED-19; replaces "An HTTP 404 counts as deleted").
    The docs separate 403 `insufficient-permissions` from 404
    `record-not-found`, but whether a scoped token receives a 404 for storage
    it cannot access is unobserved. If it did, a 404 would falsely verify
    deletion while the storage kept accruing. A 404 on delete therefore counts
    as deleted only when both hold: the storage id is the one recorded from
    this run's own start or run record, and
    `HW_RADAR_APIFY_DELETE_404_IS_ABSENT` is true. That setting defaults to
    false. The operator sets it only after the scoped-token capability probe
    (R25) records a 403 for a delete against storage the token cannot access,
    using a throwaway **unnamed** storage created under an operator `probe`
    reservation (revision 11, R10-06; MS2-D-46 gives its envelope; a named
    storage is retained indefinitely, `platform/storage`). While it
    is false, a 404 is a failed attempt: it retries up to the delete-attempt
    cap, then `delete_failed` and the latch (MS2-D-32). A second `GET` returning
    404 is not used as proof, because a masking token would return 404 to that
    read too.
  - If the deadline passes before stage 1 committed, the import is `rejected`
    with `storage_deadline`: retention wins over completeness.
  - Failures retry with backoff up to the delete-attempt cap (MS2-D-32). A row
    still `delete_failed` past its deadline logs at error level and appears in
    the pilot and spend reports.
- **Actor contract.** The Actor may write merchant content only to the default
  dataset. The `OUTPUT` record in the default KV store holds only counts, scope,
  and errors. Revision 5: this is a review item on the hw-radar PR that changes
  `actors/`, backed by an Actor test that asserts the KV store holds only
  `OUTPUT` (MS2-D-38).
- *Rejected (a):* a retention column on `SourceConfig`. Legal retention would
  become admin-editable, and `expires_policy` is a callable. The code registry is
  reviewed and tested.
- *Rejected (b):* deleting only after a durable import (revision 1). Rejected,
  failed, and abandoned datasets would then live until Apify's own expiry, far
  beyond bounded TTLs.

**MS2-D-26 — Every admitted charge is bounded; an overrun latch pauses paid
admission (Slice E; review F-08).** This decision amends MS2-D-17.
- **Charge components.** Unit prices are from the official pricing page
  (apify.com/pricing, retrieved 2026-09-24). Each price is a setting with a
  cited URL and date. For live admission, no price setting has a default.

  | Component | Bound in the reservation | Enforced by |
  | --- | --- | --- |
  | Compute | `memory_mb/1024 × timeout_s/3600 × usd_per_cu` ($0.20/CU on Free/Starter) | The start request's `memory` and `timeout` query parameters, and (revision 10, ED-20) an explicit `restartOnError=false`, because a platform restart could exceed `memory × timeout` and move `startedAt`/`finishedAt`. The start response's `options` must equal the request; on a mismatch the run is aborted and the latch trips. A restart observed in the run record also trips it. Revision 11 (R10-06): the mismatch abort is an overdue-path attempt, counted by `storage_cleanup_attempts` before it is sent (MS2-D-32 *Per-call bound*). |
  | Dataset and KV writes during the run | `max_items × dataset write price` + `max_kv_writes × KV write price` | Actor input caps `maxItems` and `maxPages`, and a per-row byte cap (contract schema `maxLength`). The Actor contract allows only `OUTPUT` in the default KV store, written at most `max_kv_writes` times, with a byte cap. Import rejects over-cap rows. A dataset count above `max_items` trips the latch. Revision 11 (R10-01): `max_kv_writes` counts every write to the default KV store during the run, the platform's `INPUT` record included, and `max_item_bytes` is the serialized-row bound defined in MS2-D-32 *Per-call bound*. |
  | Post-run storage operations and timed storage | Revision 11 (R10-01, R10-07; MS2-D-32 *Per-call bound*): `max_dataset_reads × (max_items + pages_per_read) × dataset read price` + `max_kv_reads × KV read price` + `max_delete_attempts × ((max_items + 1) × dataset write price + 3 × KV write price)` + timed storage for `max_items × max_item_bytes + max_kv_bytes + MAX_API_REQUEST_BODY_BYTES` (the `INPUT` record) over `storage_hours = max(…_STORAGE_MAX_LIFETIME, 744 h + 2 × guard)` | hw-radar's own counters, incremented and committed before each call (MS2-D-32). Revision 11 (R10-07) replaces revision 10's "bounded by the account's observed data retention … for runs outside the ten most recent": no platform expiry is relied on. `storage_hours` covers at least one full billing cycle (744 h, 31 days; admission denies with `unbounded_component` when an observed cycle is longer), and a row whose storage is not verified deleted is never reconciled, so it counts at its full estimate, and therefore at a full cycle of storage, in **every** cycle from admission until verified deletion, however long that takes. |
  | API calls (revision 10, ED-01; derived per endpoint in revision 11, R10-01) | `(1 + max_run_polls + 4 × max_delete_attempts + max_kv_reads) × api_call_bound + max_dataset_reads × pages_per_read × dataset_page_bound`, plus the monitoring allowance `max_correction_reads × api_call_bound`, held as `monitoring_bound_usd` and debited by MS2-D-34 *Monitoring charges* | hw-radar's own counters, each incremented and committed before its call is sent; the per-call wire-byte ceilings are enforced by the client (MS2-D-32 *Per-call bound*). The storage-operation parts of dataset and KV calls are priced in the row above. What the docs do not state, the per-call multiplicity and the metered-byte basis, is the owner-accepted residual R38. |
  | Data transfer | Revision 10 (ED-01), direction-agnostic; revision 11 (R10-02): `(actor_fetch_bytes + max_items × max_item_bytes + max_kv_writes × max_kv_bytes) × transfer price`, where `actor_fetch_bytes = maxBytes + maxRequests × request_wire_overhead + HTTP_READ_CHUNK_BYTES` and `transfer price = max(external, internal)` per decimal GB (10⁹ bytes, which prices a byte higher than a GiB would). hw-radar's own reads are priced per call in the *API calls* row, whose wire bytes are at least the content bytes revision 10 priced here. | Actor input caps (`maxBytes`, `maxItems`, `maxRequests`), the contract's row and KV byte caps, and hw-radar's read counters. Revision 11 (R10-02): the Actor checks `maxBytes` only after a chunk arrives (`core.py:344-353`; `test_limits.py:158-175` accepts a 100-byte chunk with `maxBytes=10`), so the check is not a transfer ceiling. The bound therefore adds, per request, `request_wire_overhead` (request, response headers, TLS handshake, and the bytes in flight to the pinned receive buffer, MS2-D-32) and, once, one network read of overshoot at the stop. The D1 follow-up counts wire bytes and pins the receive buffer; F5a measures the real transfer. The docs do not say which transfers are external or internal, so no direction has to be verified. Revision 5's `max_requests × max_response_bytes × transfer unit price` and its "unverified direction ⇒ live admission disabled" rule are withdrawn. |
  | Proxy and every other component | Disallowed: $0 | Contract and Actor input carry no proxy configuration, and an Actor test asserts the Actor source never constructs `ProxyConfiguration` (MS2-D-38, MS2-D-44). Revision 10 (ED-10) replaces the proxy-key rule with an **allowlist**: any non-zero `usage` or `usageUsd` component outside {`ACTOR_COMPUTE_UNITS`, `DATASET_READS`, `DATASET_WRITES`, `KEY_VALUE_STORE_READS`, `KEY_VALUE_STORE_WRITES`, `DATA_TRANSFER_INTERNAL_GBYTES`, `DATA_TRANSFER_EXTERNAL_GBYTES`} trips the latch (`unexpected_usage_component`). That covers every `PROXY_*` key; `REQUEST_QUEUE_*` (every run has a default request queue, which the Actor never uses and cleanup never deletes); `KEY_VALUE_STORE_LISTS`, which this table does not bound; and any renamed or new key. A component value the client cannot parse also trips it. The component keys are confirmed in `api/v2/actor-run-get` (2026-09-25). Residential proxies are never used, although the account has the residential-proxy feature available (verified account state 2026-09-24), so policy and tests, not the account, prevent it. |

- **Reservation.** `(Σ component bounds) × (1 + margin)`. If any unit price is
  missing, admission is denied with `pricing_unverified`. If any component
  lacks an enforceable bound (for example an unset `…_STORAGE_MAX_LIFETIME`),
  admission is denied with `unbounded_component` (MS2-D-32). Revision 10:
  that includes a storage lifetime shorter than the observed
  `dataRetentionDays` (MS2-D-40) and an unset or invalid API-call cap
  (MS2-D-32). An unverified *billing fact* is never by itself a reason to deny
  admission; each is priced by a bound instead (ED-01). Revision 11 (R10-01):
  a bound is admitted only if it is derived from documented billing units,
  enforced by an hw-radar ceiling, or covered by a named residual the owner
  has accepted. The one such residual is R38 (per-call multiplicity and the
  metered-byte basis). **The owner accepted R38 on 2026-09-25**, so
  `HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED` defaults to `2026-09-25`
  (applied only when the variable is absent, like the external-liability
  default). A present-but-empty or invalid value denies every paid admission,
  `operator` included, with `call_billing_residual_unaccepted`. The acceptance is an owner decision
  recorded outside this plan, not F5a evidence, so it does not make F5a wait
  on itself.
- **Ceiling** (revision 5, **owner-overridden**; OQ23 resolved). Revision 4's
  `effective_hard_cap = $20 − HW_RADAR_APIFY_CAP_DEDUCTION_USD −
  HW_RADAR_APIFY_SAFETY_MARGIN_USD` is withdrawn, with both settings. The owner's
  rule is a **cash ceiling plus attributable consumption**, enforced by MS2-D-40:
  - The hard owner ceiling is the account's total Apify **cash outlay**, about
    $20 per billing cycle. The plan's subscription fee counts, because it is
    actual money. The prepaid platform usage that fee buys is not charged a
    second time.
  - Hardware Radar's **attributable platform consumption** targets at most $12
    per cycle, and it may use only the lesser of that target and the prepaid
    allowance actually remaining after the account's other workloads.
  - No pay-as-you-go overage is relied on for normal operation, so admission
    never lets Hardware Radar's worst case exceed the remaining prepaid
    allowance. There is no "$12 plus $19" and no "$20 of run charges on top of
    the subscription".
- **Invariant** (revision 5). Per class, within the current billing cycle:
  `consumed_in_cycle + Σ unreconciled reservations touching the cycle + new
  reservation ≤ class cap`, and the account-headroom check of MS2-D-40.
  "Unreconciled" means `reserved`, `usage_provisional`, or `usage_finalized`, each
  counted at its full estimate (MS2-D-32, MS2-D-41).
- **Cycle attribution** (revision 5, owner-overridden; MS2-D-34 as revised
  replaces the revision-4 31-day text).
  - `reserved_at` is fixed for good and is provenance: it records when admission
    happened.
  - A reservation's *charge interval* runs from `reserved_at` to its
    `last_charge_at` (MS2-D-34), each widened by the cycle-boundary guard
    (MS2-D-40). While a reservation is not `reconciled`, its interval is open
    ended.
  - A reservation counts, at its settled amount once reconciled and at its full
    estimate before that, in **every** billing cycle its charge interval
    intersects. Counting the whole amount in each such cycle over-counts, which
    fails closed.
- **Serialization.** Reserve, reconcile, latch trip, and latch reset each take
  the same `pg_advisory_xact_lock(APIFY_BUDGET_LOCK)`. Revision 10 (entry
  gate, ED-05): they take it **first**, before any row lock, per the total
  order in MS2-D-35 *Serialization*. A transaction that already holds a
  `provider_run`, scope, or listing lock never requests the budget lock. A
  latch condition found before a stage transaction (a dataset over its cap,
  a KV store over its byte cap) is tripped in its own budget-locked
  transaction before the stage transaction starts.
- **Overrun latch.** A new `apify_budget_latch` table in migration 0022 records
  each trip and each clear.
  - Trip conditions at reconcile: actual > the reserved estimate (revision 5,
    owner-clarified (s2, 2026-09-24): "higher actual → overrun state". The
    estimate already carries its `(1 + margin)`, so revision 1–4's extra
    `HW_RADAR_APIFY_OVERRUN_TOLERANCE` is withdrawn); any proxy usage, which
    revision 10 widens to any usage component outside the allowlist in the
    table above or any unparseable component value; a dataset
    over its cap; or a start-option mismatch, which revision 5 extends to a
    started build outside the contract's version line (MS2-D-38). Revision 3 adds
    an exhausted delete-attempt cap and an orphaned start (MS2-D-32, -33).
  - While tripped, no paid call is made to repair the overrun: no new run, no
    probe, and no dataset re-read (MS2-D-22). The state is visible in the
    freshness and spend report as `budget_overrun`.
  - While tripped, all paid admission is denied with `overrun_latch`, and every
    Apify source shows `budget_paused`.
  - It clears only through the owner command `apify_budget_reset --reason`, or
    through an estimator correction: a bump of
    `HW_RADAR_APIFY_ESTIMATOR_VERSION`, recorded as a clear event.
- **Late usage.** The reservation stays counted at its full estimate until it
  is settled under MS2-D-32 and MS2-D-41. A first non-null `usage_total_usd`
  does not settle it; it is provisional. A reservation still unsettled when its
  admission cycle ends is carried, at its full estimate, into the next cycle
  and reported as `unreconciled_stale` for owner action (revision 5 replaces
  "after 31 days").
- *Rejected:* revision 1's "the estimate is a hard bound" with an assumed
  overhead. Storage, transfer, and proxy were unbounded, and an overrun was only
  logged.

**MS2-D-27 — Category hint through the corpus tooling (Slice B task B6; review
F-02).**
- **Corpus schema.** `ListingFields` (`matching/eval/corpus.py:181`,
  `extra="forbid"`) gains `category_hint: str | None = None`, with the A2 slug
  validation.
- **Harvest.** `harvest_corpus._staging_entry` (`:70`) writes a
  `"category_hint"` key only when the hint is non-null.
- **Replay.** `eval/evaluate._ingest` (`:106`) passes the hint into the
  `ParsedListing` it rebuilds.
- **Compatibility.** The existing drive corpus has no such key and loads
  byte-identically. Staging output for unhinted listings is byte-identical. The
  A0 baseline is unchanged.
- **Why Slice B, not F.** The hint changes a decision only once a non-drive
  category is registered (B3). F4 should be pure measurement, with no schema
  change inside it.
- *Rejected:* carrying the hint in `attrs`. A2 forbids the reserved key.

**MS2-D-28 — Remote rows skip the HTTP soft-block classifier (Slice D;
discovered during revision 2).**
- **Hazard.** `_classify_batch` flags any item whose body is under 20% of the
  site's median per-item body size (`classify.py:55-57`). That median comes from
  the site's prior successful FULL runs (`pipeline.py:221-238`). Dataset rows are
  small JSON, while local runs store full pages, so after a provider switch
  every imported row would be classified `ANTI_BOT`.
- **Decision.**
  - The import path does not run `_classify_batch`. Remote transport health comes
    from `classify_run`.
  - `_median_body_bytes` counts only runs whose `detail_json["provider"]
    ["provider_kind"]` is `local` or absent.
- *Rejected:* a separate per-provider median. Remote rows are not HTTP responses,
  so a soft-block ratio does not apply to them.

**MS2-D-29 — Evaluations are bound to their catalog inputs (Slice C,
migration 0020; review F-03 residual).**
- **Hazard.** Product clauses read catalog specs (MS2-D-08), and those specs
  change without any listing-side event. The refdata importer updates specs in
  place (`refdata/persist.py:208`), and family membership is reassigned in
  place (`persist.py:192-193`). The refresh loop reconsiders only NONE and
  FAMILY listings (`refdata/refresh.py:65-68`). A same-target re-resolution
  keeps the existing edge (`matching/resolver.py:581-592`). Correcting a GPU's
  cooling or TDP therefore changes none of the four revision-2 binding fields,
  and an old `match` would stay current.
- **Catalog inputs.** One function, `eligibility.catalog_inputs(listing) ->
  CatalogInputs`, gathers everything product clauses read. The evaluator and
  the validity check both call it, so they cannot drift. It covers:
  - model or variant grain: the dispatch category, the target ids, and the
    typed `*_spec` field values of the target model, or an explicit `absent`
    marker when the spec row is missing;
  - family grain: the family id and category, the sorted member model ids,
    and each member's typed spec values, which are the agreement-set inputs;
  - no accepted edge: a constant marker. Product clauses are `unknown` there
    anyway.
- **Fingerprint.** `catalog_fingerprint = sha256(canonical JSON of
  CatalogInputs + CATALOG_INPUTS_VERSION)` is stored on `watch_evaluation`
  (CharField(64)). Validity (MS2-D-20) recomputes it at read time.
  `shortlist()`, `review_queue()`, and `evaluate_watches --pending` share one
  predicate, which batches the spec reads for the candidate rows.
- **Effect.** A same-target catalog correction makes the row non-current
  immediately, so it drops out of `shortlist()` and shows as `pending`, with
  no new observation and no resolver run. An edit to an unrelated model
  changes no fingerprint, so evaluations do not churn.
- *Rejected (a):* atomic invalidation when specs or family membership change.
  Every writer would need a hook: the refdata importer, Django admin (B5
  registers the satellites), family reassignment, and any future queryset
  `.update()`, which bypasses model signals. One missed writer means a silent
  stale pass. MS2-D-20 rejected write-time invalidation for the same reason.
- *Rejected (b):* one catalog-wide revision counter. Any unrelated catalog edit
  would make every evaluation pending, and every writer would still have to
  bump it.
- *Reopen if* F3 measures material shortlist latency from recomputing
  fingerprints. The replacement would then be a database-trigger-maintained
  per-model revision column, which cannot be skipped by any writer.

**MS2-D-30 — Monotonic observation and absence watermarks (Slice D,
migration 0021; review N-01).**
- **Hazard.** A durable asynchronous import can finish after a newer run or a
  local poll. Today `upsert_listing` overwrites title, condition, international
  status, and retention unconditionally (`acquisition/persist.py:59-71`).
  `_persist_all` relists every seen listing (`pipeline.py:297`), and
  `_apply_delist` delists every unseen candidate (`pipeline.py:179-218`).
  Deterministic `observed_at` values make snapshots idempotent, but they do not
  make these current-state effects chronological. eBay's delete-on-delist
  redaction makes a false delist destructive.
- **Watermarks.**
  - Observation: a new nullable `Listing.last_observed_at` holds the
    `observed_at` of the newest observation applied to current state. It is
    only ever raised, never lowered. A NULL means "no observation since the
    column existed", so the guards treat it as older than any observation. No
    backfill is needed.
  - Absence, per listing: a new nullable `Listing.last_absence_at`, raised by
    `mark_delisted` to its evidence time and never lowered (MS2-D-35).
  - Absence, per scope: a complete-sweep watermark `last_complete_sweep_at`
    (MS2-D-35), plus the continuity watermarks `last_eligible_sweep_at` and
    `continuity_broken_at` (MS2-D-31, MS2-D-36).
- **Guards.** Each runs in the persisting transaction under
  `select_for_update` on the listing row, on the local and the import path
  alike (revision 4 wording; MS2-D-35 gives the absence half):
  - *Current state.* A new `persist.observe_listing(site, normalized,
    retention_class, *, expires_at, observed_at)` replaces the content
    columns (`canonical_url`, `url_hash`, `title_raw`, `condition_label_raw`,
    `is_international`, `retention_class`, `expires_at`, `collection_scope`),
    relists, and raises `last_observed_at` only when the observation is
    *current-eligible*: `observed_at ≥ last_observed_at` (or the watermark is
    NULL) **and** `observed_at` is not older than any absence watermark that
    applies to the row (MS2-D-35). Content writes and revival share this one
    predicate, so no path can restore content that a newer delist retired
    or extend its `expires_at`. An observation that is not current-eligible
    leaves the row untouched, so `last_seen` is not bumped either.
    `upsert_listing` keeps its signature for existing callers.
    - *`last_seen` (Binding; revision 10, entry gate, ED-02).* A
      current-eligible observation (a content write, a relist, or an active
      creation) sets `last_seen` exactly as today's `update_or_create` does:
      either `last_seen` is in `update_fields`, or the save is a full save.
      Django adds `auto_now` fields to `update_or_create`'s `update_fields`
      (`.venv/…/django/db/models/query.py:1037-1053`), so today every upsert
      bumps `Listing.last_seen` (`market.py:290`). A guarded
      `save(update_fields=[…content columns…])` that omits it would silently
      stop the bump. A listing seen by run N and missing from a truncated run
      N+1 inside its grace would then stale-delist (`last_seen__lt`,
      `pipeline.py:213`), breaking R23's preserved property and, for eBay,
      redacting content. A non-current-eligible observation leaves
      `last_seen` unchanged. Frozen tests backdate `last_seen` by hand and
      cannot catch this, so D10 pins it with named tests.
    - Revision 10 keeps R23 as an accepted MS-2 residual: `last_seen` stays
      `auto_now` processing time, and D2/D10 do not stamp it from
      `observed_at` (see R23).
  - *Creation.* A key with no row is created active only if the observation
    is not older than its scope's complete-sweep watermark. Otherwise it is
    created already delisted (MS2-D-35).
  - *Absence.* `_apply_delist` excludes candidates whose `last_observed_at >
    scope.observed_at`, and re-checks that under the row lock before
    `mark_delisted`. A listing observed or first seen by a newer run is never
    delisted by an older sweep, whether complete or stale. Revision 10 (entry
    gate, ED-06): the under-lock re-check repeats the **full** candidate
    predicate: `collection_scope = scope_key` (NULL for `None`),
    `delisted_at IS NULL`, `last_observed_at` NULL or `≤ scope.observed_at`,
    and, for stale absence, the `last_seen` cut-off. Otherwise a concurrent
    current-eligible import on scope S could move candidate X into S after
    scope S′'s stage 2 read it, and S′ would delist another scope's live
    listing. Candidates are selected with
    `select_for_update().order_by("pk")` (MS2-D-35 *Serialization*); under
    Postgres READ COMMITTED the `WHERE` clause is re-evaluated on the locked
    row version, which gives the re-check.
  - *History.* Snapshots append with insert-if-absent on `(listing_id,
    observed_at)`, carrying the observation's own retention, not the current
    row's (MS2-D-37). "Latest snapshot" is ordered by `observed_at`
    (`resolver.py:249-252`), so an older snapshot never replaces the current
    offer, and the MS2-D-20 binding is unaffected.
- **Local path.** A local run's `observed_at` is its own fetch time, so it is
  normally the newest. The guards then normally no-op, and the frozen pipeline
  and delist tests stay green. Revision 10 (entry gate, ED-13): for a
  heartbeat-enabled source, the heartbeat-fired FULL `run_source`
  (`heartbeat.py:228-234`) is a separate APScheduler job, and `max_instances=1`
  applies per job id (`service.py:311`), so it can overlap the FULL-lane run of
  the same source. The guards and the ordered continuity rules then apply, and
  they fail closed. This also fixes today's unlocked lost update on
  `continuous_since` (`pipeline.py:152-153`, `:174-176`).
- *Rejected (a):* refusing an import whose run is older than the listing's
  newest observation. That drops legitimate history, and it cannot protect
  listings that only the older run saw.
- *Rejected (b):* ordering by ScraperRun start alone. Listings are touched by
  more than one run, so ordering has to be decided per row.
- *Reopen if* a source supplies per-row `observedAt` values that differ
  materially from run start. The watermark would then use the row value.

**MS2-D-31 — Sweep continuity per collection scope (Slice D, migration 0021;
review N-02).**
- **Hazard.** MS2-D-12 makes scopes independent collection units, but
  continuity is one FULL-lane value per source, and its previous-run lookup
  counts every successful FULL `ScraperRun` on the site
  (`pipeline.py:111-154`). Repeated complete GPU runs can keep that value alive
  while RAM, or the legacy scope, is never swept. A later incomplete sweep of
  that scope can then stale-delist on borrowed history.
- **Decision: per-scope tracking.** Scopes are provider-independent
  (MS2-D-12), so one record serves local and Actor runs of the same scope:
  - **Legacy NULL scope:** keeps the lane's `continuous_since` and the
    `ScraperRun` previous-run lookup for gap detection, so in-order legacy
    polling behaves exactly as today. Revision 4 (MS2-D-36) makes it ordered:
    the FULL lane row gains the NULL scope's own `last_eligible_sweep_at` and
    `continuity_broken_at`, and the record and break rules below apply to it
    under `select_for_update` on the lane row. One extra rule remains: a
    successful FULL run that did not sweep the NULL scope **breaks** the
    NULL-scope continuity, with the run's `observed_at` as the break's event
    time. That keeps a scoped run from ever serving as the NULL scope's
    predecessor in the site-level lookup. Before D every run sweeps the NULL
    scope, so nothing changes until scoped runs exist.
  - **Non-null scopes:** a new table `scope_sweep_continuity` (`source_site`
    FK, `collection_scope` CharField(100) not null, `continuous_since` null,
    `last_eligible_sweep_at` null, `continuity_broken_at` null,
    `last_complete_sweep_at` null, `updated_at`; unique `(source_site,
    collection_scope)`). Gap detection reads the row's own
    `last_eligible_sweep_at`, never another scope's runs. The tolerance is
    `max(2 × FULL current_interval_s, MIN_CONTINUITY_TOLERANCE)`, as for the
    lane.
  - **Record and break, every scope** (MS2-D-36 gives the full rule):
    - *Record:* under the row lock, an eligible sweep observed at `t` changes
      nothing if `t ≤ continuity_broken_at` or `t < last_eligible_sweep_at`.
      Otherwise it restarts `continuous_since` at `t` if the value is null or
      the gap exceeds tolerance, then sets `last_eligible_sweep_at := t`.
    - *Break:* sets `continuous_since := None` and raises
      `continuity_broken_at` to the break's event time.
- **Which scopes a run swept.**
  - A local run: the `scope_key` of each `DelistScope` it returns (F1's
    multi-scope capability can return several). A scope-less local run swept
    the NULL scope.
  - A remote run: the `scope_key` it was *admitted* for
    (`provider_run.scope_key`), never the Actor's own claim. Revision 10
    (entry gate, ED-03): that key is never NULL (MS2-D-13), so a remote run
    never sweeps the NULL scope, and a successful remote FULL run's stage 2
    always applies the NULL-scope break (MS2-D-22, -36). The MS2-D-36
    out-of-order NULL-scope hazard remains only on the local path (overlapping
    local FULL runs, MS2-D-30 *Local path*).
  - Eligibility per swept scope follows MS2-D-11. A run leaves scopes it did
    not sweep untouched, except the NULL-scope break rule above.
- **Delist.** `_apply_delist` reads the continuity of the scope it is
  applying. Stale absence for a scope needs that scope's own grace of
  continuous sweeps.
- **Slice and migration.** Slice D, D2 (migration `0021`), with the
  pipeline wiring in D10. F1 applies it per sweep. Slice A needs no change,
  because scopes do not exist before D.
- *Rejected (a):* disabling stale absence for every non-null scope. eBay
  category sweeps above 10,000 items (F1, R8) could then never retire an
  absent listing except by retention expiry. It also leaves the legacy
  scope's reverse hazard (remote scoped runs bridging a later local NULL
  sweep) unfixed.
- *Rejected (b):* moving the NULL scope into the new table too. It changes the
  mechanism that frozen tests pin, for no behavior gain.
- *Consequence (assumption):* when Actor runs rotate scopes more slowly than
  every FULL tick, the per-scope gap can exceed the tolerance, so continuity
  keeps restarting. That fails closed, because stale absence simply does not
  fire. Remote runs are never stale-eligible in any case.
- *Reopen if* per-scope cadence becomes configurable; the tolerance should
  then use the scope's own interval.

**MS2-D-32 — The reservation bounds cumulative authorized work; liability is
retained until settlement (Slice D counters, Slice E ledger, migrations 0021
and 0022; review F-08 residual).** This decision amends MS2-D-17 and MS2-D-26.
- **Hazard.** Revision 2 reserved one dataset read and storage only until the
  cleanup deadline. A stage-1 rollback permits another full read, and a failed
  cleanup keeps billable storage past the deadline. Reconciling on the first
  non-null usage released the reservation while import reads and cleanup
  could still charge.
- **Operation caps.** These are enforced by hw-radar's own counters. Each
  counter is incremented and committed *before* its operation, so a crash
  mid-operation still counts it.
  - Dataset reads: `provider_run.dataset_read_count ≤
    HW_RADAR_APIFY_MAX_DATASET_READS` (default 3, an assumption). A full
    paginated read counts once. At the cap, the import is rejected with
    `read_cap_exhausted` (MS2-D-22) and cleanup proceeds.
  - KV reads: `kv_read_count ≤ HW_RADAR_APIFY_MAX_KV_READS` (default 3, an
    assumption), with the same rejection.
  - Deletes: `storage_cleanup_attempts ≤ HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS`
    (default 10, an assumption). Revision 11 (R10-06) makes the unit explicit:
    one attempt is the overdue sequence of *Per-call bound* below, which
    deletes each storage not yet verified deleted. At the cap, automatic
    retries stop, the row stays `delete_failed` and is reported, and the
    latch trips. Revision 11 (R10-07) withdraws "the remote storage then
    expires at platform expiry, which the storage component already
    reserved": the docs do not settle that expiry (MS2-D-25). A
    `delete_failed` row is never reconciled, so it counts at its full
    estimate, including a full billing cycle of storage (MS2-D-26
    `storage_hours`), in every cycle for as long as it stays `delete_failed`.
  - KV writes and bytes: the Actor contract bounds them (MS2-D-26 table), and
    a KV store over its byte cap trips the latch.
  - *API calls* (revision 10, entry gate, ED-01; replaces "`GET` run polls
    are assumed non-billable; E2 verifies that; if billable, live admission
    stays denied until a poll cap is added"). The docs cannot say whether
    these calls bill, and only a live run can measure it, so the design caps
    and prices them instead of waiting for verification:
    - **Documented billing units** (revision 11, R10-01; official docs and
      pricing page, retrieved 2026-09-25). These replace revision 10's
      asserted unit bound:
      - Platform usage "comprises four main parts": compute units, data
        transfer ("between the web, Apify platform, and other external
        systems"), proxy, and storage operations ("Read, write, and other
        operations performed on the Key-value store, Dataset, and Request
        queue") (`docs.apify.com/platform/actors/running/usage-and-resources`).
        A compute unit measures "resources consumed by Actor runs and builds".
      - The pricing page's usage-priced services are exactly: compute units,
        proxies, per storage type timed storage (GB-hours) and reads and
        writes per 1,000 (plus key-value lists), and data transfer
        external/internal per GB. There is no API-request, delete, or
        account-read unit (`apify.com/pricing`; the same rates in
        `docs.apify.com/platform/actors/publishing/monetize/pricing-and-costs`).
      - Pay-per-usage costs are "compute units, data transfer, storage
        operations, and residential or SERP proxies", and "Reading from or
        writing to a run's dataset after the run finishes also counts as
        platform usage" (`apify.com/pricing`, FAQ).
      - Monthly usage "includes your use of Actors, compute, data transfer,
        and storage" (`docs.apify.com/api/v2/users-me-usage-monthly-get`).
      - Dataset pagination "is always performed with the granularity of a
        single item", and `limit` is the "Maximum number of items to return"
        (`docs.apify.com/api/v2/dataset-items-get`).

      So a call that addresses no storage (run, build, abort, account)
      performs no storage operation and consumes no compute; its only
      possible charge is transfer. A storage call can add operations only of
      its storage type. **Not documented:** how many operations one call is
      metered as, and which bytes are metered as transfer (residual R38).
    - **Per-call bound** (Binding; revision 11, R10-01, R10-06, R10-08;
      replaces revision 10's *Unit bound*).
      - *Wire ceiling.* `request_wire_overhead = …_API_CALL_OVERHEAD_BYTES +
        2 × HTTP_RECEIVE_BUFFER_BYTES`, and `wire_bytes(body_cap) =
        request_wire_overhead + body_cap`. `…_API_CALL_OVERHEAD_BYTES`
        (default 262144, an assumption) covers the request line and headers
        the client builds; the request body, which the client refuses to
        send above `MAX_API_REQUEST_BODY_BYTES` (16384, a code constant; only
        the start call has a body); the response header block, which
        httpcore rejects above 100 KiB (`MAX_INCOMPLETE_EVENT_SIZE`,
        `httpcore/_async/http11.py:45`); and a TLS handshake (OpenSSL's
        default 100 KiB certificate-list limit, a library default).
        `HTTP_RECEIVE_BUFFER_BYTES` (65536, a code constant) is set as
        `SO_RCVBUF` on every Apify client socket through httpx's
        `AsyncHTTPTransport(socket_options=…)`. Linux doubles it, so at most
        `2 ×` it is in flight when the client stops reading. The bytes
        delivered after an over-cap response is abandoned are therefore
        bounded; the latch no longer only detects them.
      - *Response caps.* A control or KV-record body is read up to
        `…_MAX_API_RESPONSE_BYTES` (262144), a dataset page up to
        `…_MAX_DATASET_PAGE_BYTES` (1048576, an assumption). A larger body is
        an error for that call and trips the latch (`api_response_over_cap`),
        because valid content cannot produce it (next bullet).
      - *Dataset page size (R10-08).* `max_item_bytes` is the serialized-row
        bound, computed by one pure function from
        `hw-radar-listing-v1.schema.json`: each string field `2 + 12 ×
        maxLength` (`maxLength` counts code points, and a JSON-escaped
        astral code point is 12 bytes; a `const` or `enum` uses its longest
        literal), each integer its longest decimal form, `null` 4 bytes, each
        property `len(key) + 4` plus a 32-byte whitespace allowance (the
        response's pretty-printing is undocumented), and 34 bytes per object.
        For v1 that is 32,735 bytes. `page_limit = floor((…_MAX_DATASET_PAGE_BYTES
        − DATASET_PAGE_ENVELOPE_BYTES) / max_item_bytes)` with a 1024-byte
        envelope (a code constant), which is 32 at the defaults, and
        `pages_per_read = ceil(max_items / page_limit) + 1` (the `+ 1` is a
        terminating empty page), 17 at the defaults. The importer sends
        `page_limit` as every page's `limit`, so a contract-valid dataset
        never yields an over-cap page, and the D-prep client's
        1000-item default is never used for imports. Admission denies with
        `unbounded_component` if `page_limit < 1` or `max_kv_bytes >
        …_MAX_API_RESPONSE_BYTES`.
      - *Unit bounds.* `api_call_bound = (the dearest documented
        storage-operation price) + wire_bytes(…_MAX_API_RESPONSE_BYTES) ×
        transfer price`. The docs give a no-storage call zero operations; the
        one operation is revision 10's margin, kept so no bound is lowered.
        `dataset_page_bound = wire_bytes(…_MAX_DATASET_PAGE_BYTES) × transfer
        price`; its item reads are priced in the MS2-D-26 storage row.
        `transfer price` is `max(external, internal)` per decimal GB.
        Illustrative only, at the defaults and Starter prices ($0.05 per
        1,000 KV writes; $0.20 per GB): `api_call_bound ≈ $0.00005 + 655,360
        B × $0.20/10⁹ ≈ $0.000181`, and `dataset_page_bound ≈ 1,441,792 B ×
        $0.20/10⁹ ≈ $0.000288`. Code computes both from the settings and
        never hard-codes them.
      - *Every call hw-radar makes* (each counter is incremented and
        committed before its call is sent, and failed calls count):

        | Call | Sent by | Counter and cap | Documented billing units | Priced in |
        | --- | --- | --- | --- | --- |
        | Start run, `POST /v2/actors/{id}/runs` | D5 start job, once per `provider_run`; never retried (a lost response is `orphaned_start`, MS2-D-33) | the `provider_run` row, created before the call | compute, the default storages' timed storage, the platform's `INPUT` write, transfer | execution part; `1 × api_call_bound` in the *API calls* row |
        | `GET /v2/actor-runs/{id}` | selectors 1 and 2 | `run_poll_count` ≤ `…_MAX_RUN_POLLS` | transfer only | `api_call_bound` each |
        | `GET /v2/actor-runs/{id}` | selector 4 (monitoring) | `correction_read_count` ≤ `…_MAX_CORRECTION_READS`; stamps `monitoring_charge_last_at` | transfer only | monitoring allowance (MS2-D-34) |
        | Abort, `POST /v2/actor-runs/{id}/abort`, and its confirming `GET` run | selector 3 attempt; the start-option-mismatch abort, which D5 sends as attempt 1 | `storage_cleanup_attempts` ≤ `…_MAX_DELETE_ATTEMPTS` | transfer only (an abort ends compute; it adds none) | 2 of the 4 calls per attempt |
        | `DELETE /v2/datasets/{id}` and `DELETE /v2/key-value-stores/{id}` | the same attempt, after terminal evidence (MS2-D-33) | `storage_cleanup_attempts` | an unpriced "other operation", priced as the dearest priced operation of its type: `max_items + 1` dataset writes; 3 KV writes (`INPUT`, `OUTPUT`, the store); transfer | 2 of the 4 calls per attempt; MS2-D-26 storage row |
        | Dataset page, `GET /v2/datasets/{id}/items` | stage 1 | `dataset_read_count` ≤ `…_MAX_DATASET_READS` per full read; at most `pages_per_read` pages per read | dataset reads (priced at one per item returned, and one for an empty page); transfer | `dataset_page_bound` per page; MS2-D-26 storage row |
        | `GET /v2/key-value-stores/{id}/records/OUTPUT` | stage 1 | `kv_read_count` ≤ `…_MAX_KV_READS` | one KV read; transfer | `api_call_bound`; MS2-D-26 storage row |
        | `GET /v2/actor-builds/{id}` | selectors 2 and 4, operator build rows | the build row's `run_poll_count` and `correction_read_count` | transfer only | build allowance (MS2-D-46) |
        | `GET /v2/users/me/limits`, `/v2/users/me/usage/monthly`, `/v2/users/me` (revision 12, R25: **retired**. The runtime never sends them, and the operator makes them with the operator key outside the application under an `inspect` reservation, MS2-D-48) | snapshot refresh; cycle discovery | `ApifyBudgetCycle.account_read_count` ≤ `…_MAX_ACCOUNT_READS_PER_CYCLE` in a known cycle; `ApifyCycleDiscovery.read_count` ≤ `…_MAX_DISCOVERY_READS` otherwise | transfer only | standing cycle debit; discovery allowance |
        | Capability probe: create an unnamed dataset, runtime-token `DELETE`, operator-key cleanup `DELETE` | operator (R25) | the operator's count against `…_OPERATOR_PROBE_MAX_CALLS` (procedural, R36) | dataset create and deletes (unpriced, priced as dataset writes), one empty dataset's timed storage, transfer | probe envelope (MS2-D-46) |
        | Build and push, and inspection reads through the Console, CLI, or MCP | operator | procedural (R36) | compute (build), storage reads, transfer | build bound; inspection envelope (MS2-D-46) |

        The Actor's own platform calls inside a run (reading `INPUT`,
        pushing rows, writing `OUTPUT`) are run usage. The MS2-D-26 write
        and transfer rows price their content, the usage allowlist latch
        bounds their kinds, and their per-call overhead is part of R38.
      - *Residual R38* (owner acceptance required; MS2-D-26 *Reservation*).
        The docs do not state (a) how many operations one call is metered as.
        The plan prices at most one per item or record a call returns or
        deletes, and one for a call that returns none. They do not state
        (b) which bytes are metered as transfer. The plan prices at most the
        call's wire bytes, which the ceilings above bound. They also do not
        state (c) the per-call overhead of the Actor SDK's platform calls
        inside a run. If an assumption fails, the enforced call counts limit
        the damage without a documented monetary ceiling. Snapshot check 1
        (MS2-D-40) and `external_liability_exceeded` detect it after the
        fact, because the account figure contains any under-priced charge.
        F5a step 4 measures (a) and (b).
        Revision 12 (owner decision (s5, 2026-09-25), R25): snapshot
        check 1 and `external_liability_exceeded` are retired (MS2-D-48), so
        the runtime no longer detects an under-priced charge after the fact.
        Only the operator's cycle reconciliation (MS2-D-48 *Operator
        reconciliation*, with operator-key reads outside the application)
        and F5a step 4 can find one, and Apify's hard limit caps the
        account (402 → `account_limit_refused`). The owner accepted R38 when
        after-the-fact runtime detection still existed. R39 records that it
        is gone.
    - **Run polls.** Every `GET` run made by selectors 1 and 2, and every
      `GET` build for an operator build row, increments and commits
      `run_poll_count` before it is sent; failed calls count too. The cap is
      `HW_RADAR_APIFY_MAX_RUN_POLLS` (default 60, an assumption). Selector 1
      spaces a row's polls at least `timeout_s / (…_MAX_RUN_POLLS / 2)` apart,
      so half the cap covers the whole run timeout and the rest covers
      settlement reads. At the cap, selectors 1 and 2 stop reading the row.
      Its run usage then settles at `max(execution bound, every observed read)`
      with basis `bound_unfinalized` once the other reconciliation
      preconditions hold, and a row whose termination was never observed is
      handled by selector 3 at its deadline (MS2-D-33). No capacity is
      returned, so this fails closed. The row is reported as
      `api_call_cap_exhausted`; the latch does not trip, because stopping the
      calls stops their cost.
    - **Correction reads.** Every selector-4 read (MS2-D-23), closing reads
      and their retries included, increments and commits
      `correction_read_count` before it is sent. The cap is
      `HW_RADAR_APIFY_MAX_CORRECTION_READS` (default 12, an assumption).
      Selector 4 schedules `next_usage_read_at` so that at most
      `…_MAX_CORRECTION_READS − 3` reads fall before `correction_monitor_until`,
      which keeps three for the closing read and its retries. At the cap, no
      further read is made and the obligation stays open and visibly
      `correction_close_overdue`. It blocks a handoff export, as the R37
      residual already does for a closing read that can never succeed.
      Revision 11 (R10-03): these reads come after `last_charge_at`, so
      their allowance `…_MAX_CORRECTION_READS × api_call_bound` is held
      outside the reconcile-time settled amount, as `monitoring_bound_usd`,
      and debited in every cycle in which a monitoring call is still possible
      (MS2-D-34 *Monitoring charges*). The increment's transaction also sets
      `monitoring_charge_last_at := now` on the reservation; it takes the
      budget lock first, then the `provider_run` row (MS2-D-35).
    - **Overdue path.** Each selector-3 attempt (MS2-D-33) makes at most one
      abort, one confirming `GET` run, and one `DELETE` per storage not yet
      verified deleted: four calls (revision 11, R10-06; revision 10 counted
      the abort and the `GET` only). All are counted by
      `storage_cleanup_attempts`, so they are bounded by
      `…_MAX_DELETE_ATTEMPTS`, and retention-required deletion and abort are
      never blocked by the run-poll cap. The start-option-mismatch abort
      (MS2-D-26) is attempt 1 of this sequence: D5 increments and commits
      `storage_cleanup_attempts` before sending it, and the attempt deletes
      only after terminal evidence (MS2-D-33).
    - **Account reads** (withdrawn by revision 12, R25; see the note that
      ends this bullet) (`GET /v2/users/me/limits`, `/usage/monthly`, and
      `/users/me`) increment and commit `ApifyBudgetCycle.account_read_count`
      before each call (a read that discovers a new cycle counts on the new
      cycle's row as well, which over-counts). The cap is
      `HW_RADAR_APIFY_MAX_ACCOUNT_READS_PER_CYCLE` (default 3000, an
      assumption). A refresh happens only when admission needs a snapshot
      older than `…_ACCOUNT_SNAPSHOT_MAX_AGE_S`. `cap × api_call_bound` is
      debited in full, from the moment the cycle row opens, in `HR_cycle` and
      in both runtime class checks, which derive from `A` (MS2-D-17, -40).
      Revision 11 (R10-12 method): at the defaults it is `3000 ×
      api_call_bound ≈ $0.54` per cycle (revision 10's figure, $0.31, used
      its asserted unit bound). At the cap no refresh is made, so the
      snapshot goes stale and admission denies with
      `account_state_unobservable`.
      - *Cycle discovery* (Binding; revision 11, R10-05). The per-cycle
        counter needs a cycle row, which does not exist on an empty ledger
        at first start or once `now` is past the latest `cycle_end`. Those
        reads use the `ApifyCycleDiscovery` table instead (E1): at most one
        open row (a partial unique index); each read increments and commits
        its `read_count` and `last_read_at` before it is sent; reads are at
        least `…_DISCOVERY_READ_INTERVAL_S` (300) apart; the cap is
        `…_MAX_DISCOVERY_READS` (24) per row; failed calls count. The read
        that returns a cycle covering `now` creates that `ApifyBudgetCycle`
        row and closes the discovery row (`closed_at`, `cycle_start`) in one
        transaction. The row's full allowance, `…_MAX_DISCOVERY_READS ×
        api_call_bound`, is debited like the standing account-read debit (in
        `HR_cycle` and in both runtime class checks) in every cycle that
        `[opened_at − guard, closed_at + guard]` intersects, and it is
        open-ended while the row is open. Bootstrap reads therefore count in
        the first cycle, and a rollover's reads count in both the old and
        the new cycle. At the discovery cap no further read is made,
        admission stays denied with `cycle_unknown`, and the spend report
        shows `cycle_discovery_exhausted`. The owner command
        `apify_budget_reset --discovery --reason` closes the exhausted row and
        opens a new one. An exhausted per-cycle cap never blocks finding the
        next cycle, because once `now` is past `cycle_end` discovery uses its
        own allowance.
      - Revision 12 (owner decision (s5, 2026-09-25), R25): this
        *Account reads* bullet and its *Cycle discovery* sub-bullet are
        **withdrawn**. The runtime sends no account read, so it has no
        per-cycle read counter, no standing debit
        (`…_MAX_ACCOUNT_READS_PER_CYCLE × api_call_bound`), no
        `ApifyCycleDiscovery` allowance, no `…_MAX_DISCOVERY_READS` or
        `…_DISCOVERY_READ_INTERVAL_S`, and no `apify_budget_reset
        --discovery`. The cycle row is materialized from the configured
        anchor under the budget lock (MS2-D-48 *Cycle*). A missing row
        therefore costs nothing to create, and an empty ledger or a
        rollover has nothing to discover. `HR_cycle` and both runtime class
        checks lose the standing and discovery terms.
    - The reservation prices the per-run calls in the MS2-D-26 *API calls*
      row. An operator build reservation adds `…_MAX_RUN_POLLS ×
      api_call_bound` to its build bound and holds `…_MAX_CORRECTION_READS ×
      api_call_bound` as its monitoring allowance (MS2-D-34, -46; revision 11,
      R10-03). F5a measures whether and how these calls bill; any lower
      bound needs a plan revision citing that evidence.
- **Reservation split.** The estimate has two recorded parts in
  `component_bounds`:
  - an *execution* part: compute, run-time writes, transfer, and a $0 proxy
    (revision 10: the transfer of hw-radar's own post-run reads, and the
    per-run API-call bound, belong to the post-run liability, so `counted`
    mode, MS2-D-41, keeps pricing them);
  - a *post-run liability*: capped reads, capped deletes, and timed storage
    over `storage_hours` (MS2-D-26; revision 11, R10-07). The operator sets
    `HW_RADAR_APIFY_STORAGE_MAX_LIFETIME` from the account's retention. It
    has no default; unset denies live admission with `unbounded_component`,
    and so does (revision 10, ED-09) a value shorter than the account's
    observed `dataRetentionDays` (MS2-D-40). Revision 11: that check is kept,
    but the storage bound no longer rests on it, because `storage_hours`
    covers a full cycle and an undeleted row recurs in every cycle. The
    post-run liability also carries the per-run API-call bound (*API calls*
    above), except the monitoring allowance;
  - (revision 11, R10-03) the *monitoring allowance* `monitoring_bound_usd =
    …_MAX_CORRECTION_READS × api_call_bound`, fixed at admission (0 for an
    inspection). It is not part of the reconcile-time settled amount, and
    MS2-D-34 *Monitoring charges* debits it.
- **Settlement** (revision 5 names the states per MS2-D-41). Status runs
  `reserved → usage_provisional → usage_finalized → reconciled` (`released` for
  a start that never ran, `denied` for denials).
  - A non-null `usage_total_usd` read is `usage_provisional` until it is
    finalized (MS2-D-41). The full estimate stays counted in every state before
    `reconciled`.
  - `reconciled` requires all of: `import_state ∈ {finalized, rejected}`;
    `storage_state = deleted` (verified; a 404 only under MS2-D-25's *404
    rule*, revision 10); finalized run usage;
    and the post-run cost accounted after `final_charge_op_at`.
  - *Work-completion anchor* (Binding; revision 11, R10-04; replaces
    "stamped after the last read or delete"). `final_charge_op_at` is
    **write-once**. It is set to the commit time of whichever of the two
    barrier transactions completes second: the one that makes `import_state`
    terminal (`finalized` or `rejected`), and the one that records
    `storage_state = deleted`. Every import read and every delete completes
    before its result commits, and none is made after both barriers, so the
    anchor is at or after every one of them. Metering calls never move it:
    selector-1 and selector-2 `GET` run polls, settlement reads, and
    selector-4 monitoring reads. It stays null while either barrier is open,
    so a run with deferred cleanup has no eligible settlement read and no
    finalize deadline yet (MS2-D-41).
  - The settled amount is the finalized run usage **plus** the post-run cost
    (revision 5, owner-clarified (s2, 2026-09-24)). The official docs say
    post-run dataset reads are account usage, and no source says they are added
    to the run's own figure, so revision 4's "final usage covers them if E2
    verifies it" branch is withdrawn. Until F5a's measurement is recorded, the
    post-run cost is the full post-run liability bound, which counts as spent
    and fails closed (MS2-D-41 *Post-run cost*).
  - The overrun check compares the settled amount with the estimate.
- **Window.** Revision 5: a settled row counts in every billing cycle its charge
  interval touches (MS2-D-34 as revised). A row that is not `reconciled` counts
  in every cycle regardless of age.
- **Live admission stays denied** wherever a component lacks an enforceable
  bound: a missing unit price, an unset storage lifetime or one shorter than
  the observed `dataRetentionDays` (MS2-D-40), or a missing or invalid
  operation-cap or API-call-cap setting. Revision 10 (entry gate, ED-01)
  removes "unverified transfer direction" and "unverified poll billing" from
  this list. Both are now priced by conservative bounds (MS2-D-26 table,
  *API calls* above), so admission for F5a, the run that measures them, no
  longer depends on their answer. Revision 11 (R10-01, R10-07, R10-08) adds
  an unaccepted R38 (`call_billing_residual_unaccepted`), a `page_limit`
  below 1 or `max_kv_bytes` above the response cap, and an observed cycle
  longer than 744 h (`unbounded_component`). None depends on F5a evidence.
- *Rejected (a):* reconciling on the first non-null usage (revision 2). It
  releases liability while charge-producing work remains.
- *Rejected (b):* pricing storage only until the cleanup deadline. Cleanup can
  fail. Revision 11 (R10-07): platform expiry is not relied on either,
  because the docs disagree about it; an undeleted storage recurs at a full
  cycle per cycle.
- *Rejected (c)* (revision 11, R10-07): modeling a failed deletion as living
  until a verified platform expiry. That needs an expiry the 2026-09-25 docs
  do not settle for a run that may stay among the ten most recent.
- *Rejected (d)* (revision 11, R10-01): proving per-call billing from F5a
  alone. A measurement shows typical billing, not a worst case, so the
  undocumented remainder is an owner-accepted residual (R38), and F5a only
  informs a later plan revision.
- *Reopen if* Apify documents per-storage retention settable per run. The
  storage lifetime could then be that value.

**MS2-D-33 — Absolute retention deadline from admission (Slice D, migration
0021; Slice E latch wiring; review F-10 residual).** This decision amends
MS2-D-25.
- **Anchor.** `provider_run` is created at admission, before the start
  request, with `admitted_at` and the absolute `storage_cleanup_due_at`
  (MS2-D-25 formula). `external_run_id`, `remote_status`, and the storage ids
  are filled from the start response. The deadline never moves later. An
  Actor cannot collect before it starts, so `admitted_at` is no later than the
  earliest collection time, and the anchor is conservative.
- **Timeout fits the deadline.** The start job (D5) refuses the start, before
  budget admission and with no spend, with `timeout_exceeds_retention`
  unless `timeout_s + HW_RADAR_APIFY_IMPORT_MARGIN ≤ storage_cleanup_due_at −
  admitted_at`. The margin defaults to 1 h (an assumption).
- **Remote expiry: deny bounded-source Actor paths.** The start job refuses
  the start, before budget admission, with
  `bounded_retention_unenforceable` for every site whose registered retention
  (MS2-D-25) is bounded.
  - *Why:* hw-radar's deletion is the only remote-expiry mechanism, and it can
    fail. The platform fallback (days) exceeds bounded TTLs (hours). No
    per-run storage-expiry setting is confirmed (MS2-D-15 facts).
  - This does not affect the pilot. eBay, the only MS-1 adapter with a
    bounded class (`ebay.py:131`), collects through its local API.
  - Fixture imports of bounded sources remain tested (D11), because the import
    path must still honor retention if the denial is lifted.
- **Overdue selector.** Selector 3 (MS2-D-23) acts on any row past its
  deadline with storage not `deleted`, whatever its remote status:
  1. Abort the run unless it was observed terminal. Revision 10 (entry gate,
     ED-15) replaces "an abort that finds the run already finished counts as
     success; the exact response is an assumption": the abort response is
     advisory. Apify documents an abort of a finished run as a no-op but not
     its status code. After the abort, the unit confirms with `GET` run.
     Revision 11 (R10-09) replaces revision 10's two rules with one
     precedence (Binding). **Terminal evidence decides first:** if either
     valid response (a 2xx abort whose parsed run status is terminal, or a
     2xx `GET` whose status is terminal) shows a terminal status, the unit
     persists that observation (`remote_status`, and `remote_terminal_at` if
     unset) and proceeds to step 2 in the same attempt. The other call's
     error or non-terminal status is then ignored. If the abort response is
     already terminal, the confirming `GET` is skipped. Only when neither
     response proves termination does the attempt end and retry with
     backoff. All of it counts as one overdue attempt under the shared cap
     (MS2-D-32 *Per-call bound*). Step 2 deletes only after the run is
     observed terminal, so a cleanup never closes storage for a run that may
     still be charging compute.
  2. Delete the dataset and KV store.
  3. Reject the import with `storage_deadline` if stage 1 has not committed.

  A row whose start response was never recorded has no storage ids. It is
  marked `orphaned_start`, reported, and trips the latch (from E), because
  admitted spend is now untracked.
- **Expired content.** Before stage 1 reads or writes anything, and again
  inside its transaction, the import is rejected with `retention_expired` if
  `expires_policy(observed_at) ≤ now` for a bounded class. The stage-1
  transaction also re-checks `storage_cleanup_due_at > now`. Expired content
  never reaches canonical tables.
- *Rejected (a):* anchoring at `remote_terminal_at` (revision 2). A delayed
  observation extends retention.
- *Rejected (b):* anchoring at the Actor's reported `startedAt`. It is known
  only after a successful poll, so a run that is never observed would have no
  deadline.
- *Rejected (c):* relying on the platform's default-storage expiry for bounded
  sources. It is days, not hours.
- *Reopen if* D3 verifies a per-run or per-storage expiry that can be set at or
  below the source TTL. Bounded-source Actor paths could then be admitted.
  Revision 10 (entry gate, ED-09): the 2026-09-25 docs show no per-run expiry.
  An account-level retention (`dataRetentionDays`) exists, but it is a shared,
  owner-controlled billing setting measured in days, so it does not reopen
  this decision, and the bounded-source denial stands.

**MS2-D-34 — Settled spend counts in every billing cycle its charges can touch
(Slice E, migration 0022; review F-08 residual, round 3; revision 5
owner-overridden: the period is the billing cycle, MS2-D-40).** This decision
amends the *Window* rules of MS2-D-26 and MS2-D-32. The hazard and the
`last_charge_at` horizon are unchanged from revision 4; the window predicate is
replaced.
- **Hazard.** Revision 3 aged a reconciled reservation out at `reserved_at` +
  31 days + `…_STORAGE_CLEANUP_MAX`, but MS2-D-32 lets cleanup succeed after
  its deadline (retries up to the delete-attempt cap). Counterexample: a run
  is reserved on day 0, misses its day-1 cleanup deadline, deletes on day 3,
  and reconciles. Its settled amount left the window shortly after day 32,
  while its day-3 delete and storage charges still fall inside billing cycles
  that run to day 34. That under-counts ADR 0021's hard monthly ceiling.
- **Horizon.** At reconciliation, under the budget lock, set
  `ApifySpendReservation.last_charge_at := max(provider_run.final_charge_op_at,
  provider_run.finished_at, reconciled_at)`, ignoring a null `finished_at`.
  `final_charge_op_at` is the write-once work-completion anchor (MS2-D-32
  *Settlement*), and reconciliation requires verified deletion (MS2-D-32),
  so it also ends the run's timed storage. Compute ends at `finished_at`.
  Revision 11 (R10-03) adds `reconciled_at`. The selector-1 and selector-2
  `GET` run polls and settlement reads are charges that can come after the
  anchor; every one is sent before reconciliation commits, so `reconciled_at`
  is at or after all of them. Charges after reconciliation are the
  monitoring reads, below.
- **Monitoring charges** (Binding; revision 11, R10-03). Selector-4 reads
  (MS2-D-23), including closing reads and retries, happen after
  `last_charge_at`, possibly in a later cycle or after a long outage. They
  are debited through a second interval rather than by moving
  `last_charge_at` or the correction deadline:
  - Each row carries `monitoring_bound_usd` (MS2-D-32 *Reservation split*),
    `monitoring_charge_last_at`, and `monitoring_call_pending_since`. Each
    selector-4 read sets `monitoring_call_pending_since` in the same commit
    that increments its counter, before the read is sent. When the read
    completes (any outcome), one commit clears the marker and sets
    `monitoring_charge_last_at` to the completion time (final resolution
    check, R10-03: the pre-send stamp alone could end the interval while the
    counted call was still to run, e.g. a worker suspended past a cycle
    boundary).
  - A **further monitoring call is possible** while
    `correction_monitor_closed_at` is null and `correction_read_count <
    …_MAX_CORRECTION_READS`, **or** while `monitoring_call_pending_since` is
    set. Otherwise the interval ends at `monitoring_charge_last_at`, or at
    `reconciled_at` if no read was ever sent.
  - An uncertain outcome keeps the liability. The poller is the only process
    that sends selector-4 reads, so a marker left by a previous process can
    no longer be sent once that process is gone. At poller start, a stale
    marker is resolved to the new process's start time, which is the
    verified cancellation point, and the interval ends no earlier than
    that. Test:
    `test_monitoring_interval_stays_open_while_final_read_is_pending_across_cycle_boundary`
    (the last increment commits more than one guard before the cycle ends,
    the worker pauses, the read is sent in the next cycle, and the
    allowance counts in the new cycle), plus
    `test_stale_monitoring_marker_resolves_at_poller_start`.
  - `monitoring_bound_usd` counts (in full until it settles, below), in
    `HR_cycle`, in its class cap,
    and in both account checks, for **every** cycle that the interval
    `[reconciled_at − guard, end + guard]` intersects. The interval is
    open-ended while a further call is possible, so the remaining liability
    is carried into every new cycle at rollover, however long an outage
    lasts. A completed read falls inside the interval, so it counts in the
    cycle of its own send stamp.
  - `bound` post-run mode never settles it below the full allowance. In
    `counted` mode (MS2-D-41), once no further call is possible it settles
    at `correction_read_count × api_call_bound`. Reconciliation never
    releases it, so the unused read allowance cannot return to capacity
    before the reads it covers.
  - `correction_monitor_until` stays `last_charge_at +
    …_CORRECTION_WINDOW_S`, fixed at reconciliation. Monitoring charges
    extend only the financial interval, never the monitoring deadline.
- **Operator rows** (revision 8, review R7-01). An operator reservation has
  no `provider_run` (MS2-D-46), so the run formula does not apply:
  - a build row sets `last_charge_at := max(the build record's finishedAt,
    reconciled_at)` (revision 11, R10-03, adds `reconciled_at` for the
    settlement `GET` build reads), with `finishedAt` taken from the read that
    settles it. The build's compute ends at `finishedAt`, and its later reads
    are covered by `reconciled_at` and by the monitoring interval above,
    which applies to build rows unchanged. A build with no terminal status or no
    `finishedAt` cannot settle; it stays counted at its bound and is retried;
  - an inspection row sets `last_charge_at :=` its `--settle` time. The
    operator performs the inspection between reserving and settling, so every
    inspection charge falls inside `[reserved_at, settle time]`.
- **Cycle predicate** (revision 5; replaces revision 4's
  `last_charge_at ≥ now − 31 days`). For billing cycle `Y = [start, end]`,
  `consumed(Y)` sums `actual_usd` over `reconciled` rows whose charge interval
  `[reserved_at − guard, last_charge_at + guard]` intersects `Y`. A row that is
  not `reconciled` counts at its full estimate in every cycle from its
  admission cycle onward. `guard` is `HW_RADAR_APIFY_CYCLE_BOUNDARY_GUARD_S`
  (MS2-D-40), which absorbs clock skew between hw-radar and Apify at a cycle
  edge. Revision 11 (R10-03, R10-05): `consumed(Y)` also adds each row's
  `monitoring_bound_usd` by the *Monitoring charges* interval, and each
  `ApifyCycleDiscovery` allowance by its own interval (MS2-D-32 *Cycle
  discovery*).
  Revision 12 (owner decision (s5, 2026-09-25), R25): the discovery allowance term is withdrawn with cycle
  discovery (MS2-D-32, -48). No `ApifyCycleDiscovery` row is ever written.
- **Why this is conservative.** Each charge a run produces falls between its
  admission and its final charge-producing operation (compute ends at
  `finished_at`; reads and deletes end at `final_charge_op_at`; polls and
  settlement reads end before `reconciled_at`, revision 11; verified
  deletion ends timed storage; monitoring reads have their own interval). Every cycle that can hold any of its charges
  therefore intersects the interval, and the whole amount is counted there,
  which over-counts and fails closed. Revision 4's "31 days suffices" argument
  is withdrawn with the rolling window.
- A trailing-31-day sum of the same rows is still computed, for the spend
  report's trend view only (MS2-D-17).
- *Rejected (a):* accounting each component at its actual charge time. The
  confirmed run fields (MS2-D-15) give a usage total and a component
  breakdown with no charge timestamps, so per-component times would be
  hw-radar guesses. One horizon needs no such data.
- *Rejected (b):* a longer fixed window, such as `reserved_at` plus the
  delete-attempt cap times the backoff. Retry timing has no hard bound during
  an outage, so any fixed extension can be exceeded.
- *Rejected (c)* (revision 5): attributing each reservation only to its
  admission cycle. A run admitted just before a cycle ends charges compute,
  reads, deletes, and storage in the next cycle.
- *Reopen if* Apify exposes timestamped per-run charge events.

**MS2-D-35 — Absence watermarks gate current content and key creation
(Slice D, migration 0021; review N-01 residual, round 3).** This decision
amends MS2-D-30.
- **Hazard.** Revision 3 gated revival and staleness against
  `last_observed_at`, but not current-content writes against newer absence,
  and it created unseen keys normally. Two timelines broke it:
  1. A listing observed at t0 is delisted at t2 (and redacted, for a
     delete-on-delist class). A delayed observation from t1 then arrives.
     Since t1 > t0, the revision-3 current-state guard restored its content
     and extended `expires_at`; only the relist was refused. The existing
     `mark_delisted` (`market.py:330-388`) stamps `delisted_at`, pulls bounded
     expiry forward, and redacts, but advances no watermark that
     current-state writes consult.
  2. A complete sweep of scope S at t2 omits key K, which has no row. A
     delayed t1 import then creates K as active, despite newer complete
     evidence that K is absent.
- **Listing absence watermark.** A new nullable `Listing.last_absence_at`.
  `mark_delisted` raises it to `when` (never lowers it) in its own
  transaction. `mark_relisted` does not clear it. No backfill: guards read
  the *effective absence* `greatest(last_absence_at, delisted_at)`, ignoring
  NULLs, so rows delisted before `0021` are covered by `delisted_at`.
  - *Why a new column:* `delisted_at` alone is cleared by `mark_relisted`, so
    the guard would depend on every present and future relist path also
    raising `last_observed_at`.
- **Scope complete-sweep watermark.** `last_complete_sweep_at` per scope:
  for the NULL scope on the FULL `SourceLaneState` row, for non-null scopes on
  `scope_sweep_continuity` (MS2-D-31). It is raised to `scope.observed_at`,
  never lowered, by the delist stage of a FULL run whose gated scope is
  complete, in the same transaction as its `ABSENT_FROM_SWEEP` marks. That
  includes a complete-empty scope and a complete sweep that delisted nothing,
  on the local and the import path. Probes, incomplete scopes, stale-absence
  sweeps, and rejected runs never raise it.
- **Current-eligible predicate.** An observation at `t` targeting scope S (the
  `collection_scope` it writes; `None` is the NULL scope) is
  *current-eligible* iff all three hold, each with NULL meaning "no bound":
  - `t ≥ last_observed_at`;
  - `t ≥ effective absence`;
  - `t ≥ last_complete_sweep_at(S)`.

  Ties go to presence, as today. A run persists its observations before its
  own delist stage raises either watermark, and fixture adapters that reuse
  one `fetched_at` across runs (for example `tests/db/test_poller_jobs.py:44`)
  must keep relisting on reappearance. Distinct remote runs never tie in
  practice, because `startedAt` differs.

  Only a current-eligible observation writes content, relists, raises
  `last_observed_at`, or creates an active row (MS2-D-30 *Current state*).
  The third condition matters only for keys without a row and for rows that
  were already delisted before S's newest complete sweep. Any active row that
  sweep saw already has `last_observed_at ≥` that sweep.
- **Not current-eligible:**
  - *Existing row:* untouched, including content, `expires_at`, the relist,
    `last_observed_at`, and `last_seen`. The snapshot follows MS2-D-37.
  - *No row:* the row is created as a delisted history anchor. In one
    transaction, the listing is inserted from the observation, with
    `last_observed_at := t`, and then
    `mark_delisted(ABSENT_FROM_SWEEP, when=last_complete_sweep_at(S))` runs.
    That applies the existing retention semantics: bounded expiry is pulled
    forward, delete-on-delist content is redacted before commit, and
    `last_absence_at` is raised. The snapshot follows MS2-D-37, so it is
    kept for merchant-fact classes and not written for bounded ones. The key
    now has an identity, so a later current-eligible observation relists the
    same pk (DR-003, D6).
- **Serialization.** The transaction that writes listings (import stage 1 or
  the local persist step) first takes `select_for_update` on each touched
  scope's watermark row: the FULL lane row for the NULL scope, and the
  `scope_sweep_continuity` row for a non-null scope, inserted if absent. The
  delist stage takes the same lock before it raises the watermark and marks
  listings. Either the creation sees the newer watermark, or the newer sweep
  sees the created row as a candidate (`last_observed_at = t1 < t2`) and
  delists it. Locks are taken in a fixed order: scope rows sorted by key,
  NULL first, then listing rows.
  - **Total lock order (Binding; revision 10, entry gate, ED-05).** Revision
    4 gave no order among listing rows and none for inserting a scope row
    mid-transaction. Two stage-1 or persist transactions on disjoint scope
    sets that share listings could then deadlock (a listing that moved scope,
    or a local NULL-scope run overlapping a scoped import), and so could a
    stage 2 on scope S′ against a stage 1 on S. For an import each abort
    used to cost a counted dataset re-read. Every D and E transaction
    therefore takes only the locks it needs, in this one order:
    1. `APIFY_BUDGET_LOCK` (advisory; E-side units only, MS2-D-26
       *Serialization*);
    2. the `provider_run` row, then (E-side units only, always under the
       budget lock) its `ApifySpendReservation` row (revision 11, R10-03);
    3. `SourceConfig`, taken only by an **outcome transaction**: one that
       applies `apply_run_outcome`. Revision 11 (R10-11) names all of them:
       stage 5, Reject (MS2-D-22), and the poller jobs (`poll_source`,
       `poll_heartbeat`, `recovery_probe_job`);
    4. exactly one `SourceLaneState` row. It is the FULL lane row (the NULL
       scope) for every stage, persist, delist, and FULL or PROBE outcome
       transaction. Revision 11 (R10-11): it is the HEARTBEAT lane row only in
       `poll_heartbeat`'s outcome transaction, which locks no scope or listing
       row. An outcome transaction locks its one lane row immediately after
       `SourceConfig`, and no transaction locks both lane rows;
    5. `scope_sweep_continuity` rows, ordered by `collection_scope`;
    6. existing `Listing` rows, locked in one
       `select_for_update().order_by("pk")` query: the batch's existing keys
       in stage 1 and the persist step, the delist candidates in stage 2 and
       the local delist step;
    7. inserts of new listing keys, in `source_listing_key` order, after
       every existing-row lock is held;
    8. child rows of a locked listing (`OfferSnapshot`, `RawPayload`,
       `ListingResolution`, `WatchEvaluation`).
  - **Row creation before locking.** Scope rows and the FULL lane row are
    ensured with `get_or_create` in their own short, committed transaction
    **before** the locking transaction starts. An all-NULL watermark row
    means "no bound", so committing it early changes no decision. The
    review's alternative, a savepoint inside the locking transaction, is
    rejected: the inserted row and its unique-index entry stay locked until the
    outer commit, so a concurrent inserter of the same key waits out of
    order.
  - **Conditions of the order.** Stages 1 and 2 and the local persist and
    delist steps never lock `SourceConfig` (a plain read is fine) or the
    HEARTBEAT lane row. Stage 5 holds no scope or listing lock when it calls
    `apply_run_outcome`. The resolver and the evaluator lock one listing, then
    its children, outside these transactions (stages 3 and 4). Revision 11
    (R10-11): the reused `apply_run_outcome` (`scheduling/apply.py:162-171`) locks
    `SourceConfig` and then the lane it is given. Inside Reject it runs as a
    savepoint of Reject's transaction, so its locks are held to Reject's
    commit, and Reject then takes the admitted scope's row (item 5). The full
    Reject order is `provider_run` → `SourceConfig` → FULL lane → scope row,
    with the scope row ensured beforehand. A PROBE Reject takes no scope row.
  - **In-memory retry** (revision 11, R10-10 rewrites the exhaustion rule). A
    stage-1, stage-2, persist, or delist transaction that Postgres aborts
    with a deadlock (`40P01`), a serialization failure (`40001`), or a unique
    violation (`23505`, a concurrent insert of the same key) is retried in
    the same process up to three times (a code constant). The retry does not
    re-read the dataset or refetch the batch; the data is already in memory.
    - *Clean state per attempt (Binding).* The fetched batch (dataset rows
      and `OUTPUT`) is immutable. Each attempt rebuilds every
      database-derived value from it: model instances are re-fetched under
      the attempt's own locks, and the attempt-local accumulators (counts,
      `import_listing_ids`, `stage_detail` error counters, delist results)
      start empty. Nothing an aborted attempt mutated in Python is reused,
      because a rollback restores only the database.
    - *Exhaustion.* A fourth retryable abort exhausts the invocation. Its
      last transaction has rolled back, so there is no partial effect. A
      separate short transaction then records the exhaustion
      (`stage_detail.retry_exhausted` incremented, `next_attempt_at` backed
      off, a warning logged), and the row stays at its prior `import_state`.
      The in-memory batch is discarded. Any other error propagates as
      before.
    - *Next read.* A later invocation, after backoff, resumes from the
      recorded stage. For stage 1 that is a new counted dataset read under
      the read cap (MS2-D-32); at the cap the import is rejected with
      `read_cap_exhausted`. Stage 2 needs no read. A local persist or delist
      that exhausts ends the local run like a crash in that transaction
      (D10), and the next poll repairs it. So a counted re-read happens
      after process loss or after exhaustion, never inside one invocation
      (MS2-D-22, -32).
  - **Contention (accepted).** `apply_run_outcome(FULL)` holds the
    `SourceConfig` lock while it waits for the FULL lane row, which a stage 1
    or a local persist may hold for one transaction. That source's HEARTBEAT
    `apply_run_outcome` waits behind it. For Actor imports the wait is at
    most one 500-item transaction (`MAX_ITEMS_CEILING`, `contract.py:73`).
    For Actor sources heartbeat is disabled (MS2-D-18), so this arises only
    after a switch back to local.
- **Why "no row" proves absence.** Listings are never row-deleted (the
  retention anchor, `market.py:342-345`), and a sweep's own stage 1 creates
  every key it saw before its stage 2 raises the watermark. So a key with no
  row was not seen by any sweep that raised S's watermark.
- *Rejected (a):* skipping creation entirely. That drops merchant-fact
  history, which MS2-D-30's rejected alternative (a) already protects.
- *Rejected (b):* creating the row active and letting the next complete
  sweep delist it. That publishes stale availability until then, and for a
  delete-on-delist class it holds retired content.
- *Rejected (c):* a per-scope set of absent keys. Its storage is unbounded,
  and the watermark plus the no-row argument is sufficient.
- *Residual:* stale-absence sweeps do not raise the scope watermark, and
  `last_seen` is stamped at persistence time. See risk R23.

**MS2-D-36 — Ordered continuity with timestamped breaks for every scope,
the NULL scope included (Slice D, migration 0021; review N-02 residual,
round 3).** This decision amends MS2-D-11 and MS2-D-31.
- **Hazard.** Revision 3's NULL-scope break was reversible out of order. A
  newer GPU-only FULL run breaks NULL continuity. An older NULL-scope run,
  whose stage 2 was delayed, then calls the unchanged recorder
  (`pipeline.py:111-154`), finds `continuous_since` null, and sets it to its
  old observation time. A later local incomplete NULL sweep finds the recent
  GPU run through the site-wide SUCCESS lookup, keeps that old start, and can
  stale-delist on cross-scope history.
- **Watermarks per scope.** Each scope has `last_eligible_sweep_at` and
  `continuity_broken_at`. For the NULL scope these are two new nullable
  columns on `SourceLaneState`, written only on the FULL lane row. For
  non-null scopes they are columns of `scope_sweep_continuity`.
- **Record** (an eligible sweep of S observed at `t`, under
  `select_for_update` on S's row):
  1. If `continuity_broken_at` is set and `t ≤ continuity_broken_at`, change
     nothing and return `None`, so the run's own stale absence does not fire.
     A tie goes to the break, which fails closed. Legacy local runs never
     break, so the tie rule cannot touch them.
  2. If `last_eligible_sweep_at` is set and `t < last_eligible_sweep_at`,
     change nothing and return `None`.
  3. Otherwise find the predecessor. For the NULL scope it is today's
     `ScraperRun` lookup, unchanged. For a non-null scope it is
     `last_eligible_sweep_at`. Restart `continuous_since := t` if it is null,
     there is no predecessor, or the gap exceeds tolerance. Set
     `last_eligible_sweep_at := t` and return `continuous_since`.
- **Break** (event time `e`): `continuous_since := None` and
  `continuity_broken_at := greatest(continuity_broken_at, e)`. A break
  always applies, even when `e` is older than the current continuity start.
  A late break costs one grace of stale absence, never a false delist.
  Event times:
  - an ineligible successful FULL run: its `observed_at`;
  - the NULL-scope break for a successful FULL run that did not sweep the
    NULL scope: its `observed_at`;
  - a rejected FULL remote import: its `startedAt`, or `admitted_at` when no
    start response was recorded.
- **Why the NULL scope keeps the `ScraperRun` lookup.** A remote run's stage
  2 (break) precedes its stage 5 (SUCCESS). So by the time a scoped run is
  visible to the lookup, it has already broken NULL continuity at its own
  time, and any later restart is newer than it. A run newer than `t` is
  either an eligible NULL sweep (step 2 fires) or a break (step 1 fires). So
  a predecessor the lookup returns is never a non-NULL run inside the current
  chain, and never newer than `t`. For in-order legacy local polling, `t` is
  monotonic, so steps 1 and 2 normally no-op and behavior is today's.
  Revision 10 (entry gate, ED-13): for a heartbeat-enabled source, a
  heartbeat-fired FULL run can overlap the FULL-lane run (MS2-D-30 *Local
  path*). Steps 1 and 2 can then fire on the local path and skip the older
  sweep's stale absence. That is a small, fail-closed behavior change, and the
  lane-row lock replaces today's unlocked lost update on `continuous_since`.
- **Code.** D changes `_record_sweep_continuity` and
  `_break_sweep_continuity` to take the scope and the event time. Slice A code
  is not reopened.
- *Rejected (a):* replacing the NULL scope's `ScraperRun` lookup with the lane
  watermark as predecessor. The frozen eBay tests simulate pauses by
  backdating `ScraperRun.started_at` (`test_source_ebay.py:378-381`,
  `:421`), so `test_ebay_polling_pause_suspends_stale_absence` would stop
  testing a pause. Deployed data would also lose its first predecessor.
- *Rejected (b):* moving the NULL scope into `scope_sweep_continuity`, for
  MS2-D-31's reason (b).
- *Rejected (c):* ignoring any remote run that finishes after a newer run.
  Ordering is per scope, and a run older than another scope's run is still
  valid evidence for its own scope.

**MS2-D-37 — Snapshot retention follows the observation (Slice D task D10;
review M-01, round 3).** This decision amends MS2-D-30 *History* and changes
`acquisition/persist.py::append_snapshot`.
- **Hazard.** `append_snapshot` copies `retention_class` and `expires_at`
  from the `Listing` (`persist.py:105-106`), and `_persist_all` relies on that
  (`pipeline.py:297-302`). Once MS2-D-30 leaves a newer listing untouched
  while appending an older observation, the older snapshot inherits the newer
  deadline. With a 6 h policy, t1 applied, then t0 < t1 appended, the t0
  snapshot expires at t1 + 6 h. Its raw payload expires correctly, because
  `store_raw` takes the fetch-time expiry. `observe_listing` is shared, so the
  local path is exposed too, and the bounded-source Actor denial (MS2-D-33)
  does not close the defect.
- **Contract (Binding).**
  `append_snapshot(listing, normalized, *, observed_at, raw=None,
  retention: ObservationRetention | None = None)`.
  `ObservationRetention(retention_class, expires_at)` is a frozen value type in
  `persist.py`.
  - `None` copies from the listing, exactly as today. Existing callers and
    frozen tests are unchanged.
  - The ordering-guarded observation path always passes it explicitly: the
    source policy's class for this observation (the MS2-D-25 registry for
    imports, `adapter_retention` locally) and that policy's expiry computed
    from the observation's own `observed_at` (`None` for indefinite classes).
    For a local run this equals the batch `expires_at`, so local output is
    unchanged.
- **Newer-absence restriction.** For a bounded class, when newer absence
  exists (the effective absence of MS2-D-35, or the scope watermark used to
  create the row delisted, strictly after `observed_at`), the snapshot's
  `expires_at := min(own deadline, that absence time)`. This is the same
  pull-forward `mark_delisted` applies to existing snapshots. If the result
  is at or before the transaction's `now`, the snapshot is not written,
  because newer evidence already retired that content. Indefinite
  (`merchant_fact`) classes keep a NULL expiry and always append as history;
  `mark_delisted` never expires them either.
- **Effect.** Each snapshot expires on its own deadline. The purge sweep
  removes the t0 snapshot at t0 + 6 h while the listing and the t1 snapshot
  remain.
- **Ownership.** D10 makes this change, together with `observe_listing`.
  `store_raw` is unchanged.
- *Rejected (a):* skipping older snapshots for bounded classes. That drops
  history MS2-D-30 keeps.
- *Rejected (b):* recomputing snapshot expiry in the purge sweeper. The DR-001
  CHECK pair and the sweep index read the stored column.

**MS2-D-38 — Hardware Radar Actors are built, versioned, tested, deployed, and
managed in this repository (D-prep D1, F5a; revision 5).**
- **Ownership (owner-overridden).** Owner direction, 2026-09-24: "All Apify
  Actors used specifically by Hardware Radar must be built, versioned, tested,
  deployed, and managed from the `hw-radar` project/repository." Actor source,
  schemas, tests, build and deploy definitions, versioning, cost and usage
  integration, and operator procedures all live here. A change to the
  Actor↔hw-radar contract lands atomically in one hw-radar PR. Hardware Radar
  does not depend on the apify-actors venture's code, lifecycle state, product
  records, admission process, implementation work, or token. Revision 1–4 text
  that placed the Actor in that repository is withdrawn (MS2-D-14, F5, R2).
- **Layout (architect decision; reviewable).** One top-level directory per
  Actor, `actors/hw-radar-<purpose>/`, outside the Django package:
  - `.actor/actor.json`: `actorSpecification: 1`, `name`, `version` in
    `MAJOR.MINOR` form whose major equals the contract major (v1 ⇒ `1.x`), an
    explicit `defaultMemoryMbytes` and `maxMemoryMbytes`, and the input schema
    reference. `actor.json` has no run-timeout field (research input
    `apify-ops.md` A4), so the default run timeout is the Actor's
    `timeBudgetSecs` input default, and hw-radar always sends an explicit
    `timeout` run option (MS2-D-15, MS2-D-26).
  - `.actor/input_schema.json` (Apify dialect) and, if used,
    `.actor/dataset_schema.json`, both conformance-tested against the contract
    (MS2-D-14);
  - `contract/hw-radar-{input,listing,run}-v1.schema.json`, the contract
    artifact (MS2-D-14);
  - its own `pyproject.toml` and `uv.lock` (the Apify Python SDK lives only
    there), a Dockerfile, the Actor source, `tests/`, and `fixtures/`;
  - an `.actorignore` limiting what `apify push` uploads (MS2-D-43).
- **Boundaries (binding).** No Django import, no `hw_radar` import, no DB
  credentials, no DB writes, and no proxy construction. An Actor test enforces
  the import and proxy rules by scanning the Actor source. Tests are
  deterministic and fixture-driven, with no network access.
- **Structure.** The Actor has a pure core (input → bounded fetch plan → rows
  plus `OUTPUT`) and a thin SDK entry point. The first Actor carries no shared
  internal framework, base classes, or plugin registry. *Reopen if* a second
  Actor duplicates a real seam.
- **Gate integration (architect decision).** D1 extends `scripts/check.py` so
  CI and the local gate also run, in the Actor's own project environment
  (`uv run --project actors/<name> --locked …`): basedpyright (strict), pytest
  with coverage (threshold ≥ the root's 85), and pip-audit. Root
  `ruff format --check .` and `ruff check .` already cover `actors/` (root
  config `src`/`extend-exclude`). The Actor's `pyproject.toml` carries its own
  `[tool.ruff]` `target-version` equal to the Actor image's Python, because the
  root's `py314` target may emit syntax an older image cannot parse. hw-radar's
  pytest runs the contract-conformance tests (MS2-D-14) and never imports the
  Actor.
  - *Rejected (a):* a uv workspace member. One shared lockfile would put the
    Apify SDK and its transitive dependencies into hw-radar's resolution and
    pip-audit surface, and an SDK pin could block hw-radar upgrades.
  - *Rejected (b):* Actor code under `src/hw_radar/`. It couples the Actor's
    build context to the Django package and invites the forbidden imports.
- **Naming.** Actors in the shared account are named `hw-radar-<purpose>`. The
  first is `hw-radar-synthetic-collector` (MS2-D-42). Actor ids and the account
  owner are configuration, never committed.
- **Runtime permission.** Each Actor uses Apify's *Limited permissions* level:
  it reads its input and writes only its default dataset and KV store (research
  input `apify-ops.md` A6). Full permissions are never requested.

**MS2-D-39 — Three clocks, never derived from one another (Slices D, E;
revision 5, owner-clarified (s2, 2026-09-24)).**
- **Clocks.**
  - *Observation (event) time:* when merchant state was observed. For an Actor
    run it is the run's `startedAt` (MS2-D-13), or a per-row `observedAt` if one
    is ever adopted (MS2-D-30 *Reopen if*).
  - *Processing (import) time:* hw-radar's own clock when it admits, polls,
    imports, or cleans up (`admitted_at`, `import_attempts`, `next_attempt_at`,
    the local `now`).
  - *Billing / finalization time:* Apify's clock of when charges accrue
    (`startedAt`..`finishedAt` for compute, operation times for reads and
    deletes, storage lifetime) and when a usage figure becomes final
    (MS2-D-41). It also defines the billing cycle (MS2-D-40).
- **Which clock governs each transition:**

  | Transition | Clock | Decisions |
  | --- | --- | --- |
  | Listing lifecycle: content writes, relist, creation, `last_observed_at` | observation | MS2-D-30, MS2-D-35 |
  | Continuity record and break | observation (break event time) | MS2-D-31, MS2-D-36 |
  | Delist and relist evidence (`mark_delisted(when=…)`, absence watermarks) | observation | MS2-D-35 |
  | Retention expiry of listings, snapshots, raw payloads, evaluations | observation (`expires_policy(observed_at)`) | MS2-D-33, MS2-D-37 |
  | Remote storage cleanup deadline | processing (anchored at `admitted_at`, which precedes any observation) | MS2-D-25, MS2-D-33 |
  | Import stage progress, retries, backoff | processing | MS2-D-22, MS2-D-23 |
  | Budget reservation and its admission cycle | processing (`reserved_at`), compared with Apify's cycle bounds under the boundary guard | MS2-D-40 |
  | Usage finalization and reconciliation | billing / finalization | MS2-D-41 |
  | Cycle attribution of charges (revision 10, ED-12) | billing, approximated by processing stamps (`reserved_at`, `final_charge_op_at`) and Apify's `finishedAt`, widened by the cycle-boundary guard | MS2-D-34, MS2-D-40 |
  | NULL-scope continuity gap on the local path (revision 10, ED-12) | processing (`ScraperRun.started_at = timezone.now()`, `pipeline.py:444-446`, read by the predecessor lookup `:140-145`) | MS2-D-31, MS2-D-36 |
  | Listing audit stamps `first_seen` (`auto_now_add`, `market.py:284`) and `last_seen` (`auto_now`, `:290`) (revision 10, ED-12) | processing; `first_seen` is read by no guard, `last_seen` only by stale absence (R23) | MS2-D-30, R23 |

- **Rules.** An import's processing time is never used as observation time. A
  locally observed termination is never used as `finishedAt` (MS2-D-33). An
  observation time never attributes a charge to a cycle. Revision 10 (entry
  gate, ED-12) withdraws "`last_seen` is the one known processing-time stamp
  on the listing path". The processing-time stamps are `first_seen` and
  `last_seen` on the listing and, on the local path, `ScraperRun.started_at`,
  which the NULL-scope gap reads. Cycle attribution is not purely the billing
  clock: its interval endpoints are processing stamps and Apify's `finishedAt`,
  widened by the boundary guard. Each such mix fails closed: the continuity gap
  is overestimated, and the guard over-counts. R23 records `last_seen`'s
  effect.
- **Preserved property (R23, verbatim):** "a delayed import may delay a correct
  delist, never cause a false delist". Any change to `last_seen` stamping must
  come with out-of-order tests proving it.

**MS2-D-40 — The billing cycle is the budget period, and the cash ceiling is
enforced as project allocation plus account prepaid headroom (Slice E, migration
0022; revision 5; OQ23 resolved).**
- **Revision 12 (owner decision (s5, 2026-09-25), R25).** The runtime makes no account read.
  Revision 12 does not rewrite the bullets below. It amends them where marked,
  and MS2-D-48 carries the replacement design. In short: the cycle comes from
  a configured anchor, and `P` comes from the operator-verified
  `HW_RADAR_APIFY_ACCOUNT_LIMIT_USD`. Check 1 and
  `external_liability_exceeded` are retired, and check 2 (the
  external-liability check) is the only account check left.
- **Cycle discovery (owner-overridden).** Admission reads
  `GET /v2/users/me/limits` for `monthlyUsageCycle.startAt/endAt`, the account
  limit, and the current usage, and `GET /v2/users/me` or the same response for
  the plan's base price and prepaid usage credit (D3 confirms which endpoint
  carries each field). It stores one `ApifyBudgetCycle` row per cycle:
  `cycle_start`, `cycle_end`, the project allocation opened for the cycle, and
  the latest observed account figures (`account_prepaid_credit_usd`,
  `account_base_price_usd`, `account_limit_usd`, `account_usage_usd`,
  `account_observed_at`). Consumed finalized usage, outstanding reservations,
  and remaining project budget are computed from reservation rows under the
  budget lock, never stored as counters that could drift from those rows.
  - A snapshot older than `HW_RADAR_APIFY_ACCOUNT_SNAPSHOT_MAX_AGE_S` (default
    900 s, an assumption) is refreshed before admission. A failed refresh denies
    with `account_state_unobservable`. `now` past the stored `cycle_end`,
    before a new cycle is observed, denies with `cycle_unknown`.
    Revision 12 (owner decision (s5, 2026-09-25), R25): withdrawn. No snapshot exists, so it cannot go stale
    and no refresh is made. `cycle_unknown` now comes from the anchor rules
    of MS2-D-48 *Cycle*, and `account_state_unobservable` from its
    *Account settings*.
  - *Retention check* (revision 10, entry gate, ED-09). The limits read also
    parses `limits.dataRetentionDays`, a required integer
    (`api/v2/users-me-limits-get`) that the D3 client does not parse yet (D3
    follow-up). Paid admission is denied with `unbounded_component` when the
    value is missing or unparseable, or when `…_STORAGE_MAX_LIFETIME` is
    shorter than `dataRetentionDays` days. Revision 11 (R10-07) withdraws
    "storage whose deletion fails lives up to the account's retention, so the
    storage bound must cover it". The docs do not settle that expiry
    (MS2-D-25), so failed deletion is the recurring liability of MS2-D-26
    `storage_hours`, and this check is kept only as a consistency guard. The
    field is shared and owner-controlled; Hardware Radar never changes it.
    Revision 11 also denies with `unbounded_component` when the observed
    cycle (`cycle_end − cycle_start`) exceeds 744 h, the full cycle
    `storage_hours` covers.
    Revision 12 (owner decision (s5, 2026-09-25), R25): the value comes from the operator-verified
    `HW_RADAR_APIFY_ACCOUNT_DATA_RETENTION_DAYS` instead of the limits read.
    An unset or invalid value now denies `account_state_unobservable`,
    and a lifetime shorter than it still denies `unbounded_component`. The
    744 h check is kept, although a cycle derived from an anchor on day
    1–28 is always 28–31 days long.
  - *Account reads* are counted and capped per cycle (MS2-D-32 *API calls*,
    revision 10); their full bound is a standing debit in `HR_cycle` and
    against `A`. Revision 11 (R10-05): reads made while no cycle row covers
    `now` (an empty ledger, or a rollover) are counted and capped on
    `ApifyCycleDiscovery` and debited by its interval (MS2-D-32 *Cycle
    discovery*).
    Revision 12 (owner decision (s5, 2026-09-25), R25): withdrawn with the account reads (MS2-D-32).
- **Project allocation.** `A = HW_RADAR_APIFY_CYCLE_TARGET_USD −
  HW_RADAR_APIFY_OPERATOR_ALLOWANCE_USD`.
  - The target defaults to 12.00, and settings validation rejects a value above
    12.00, the owner's operating target. Raising it is an owner decision.
  - The operator allowance is the cap of the `operator` admission class. It
    defaults to **1.00** per billing cycle, the owner's setting (revision 9,
    owner decision (s4, 2026-09-25), [OQ29](../../resolved-questions.md#oq29--operator-allowance-size)),
    which replaces the 0.50 assumption of revisions 5–8. At the default target,
    `A = 12.00 − 1.00 = 11.00`. Operator consumption stays inside the cycle
    target and both account checks. Changing the allowance is an owner
    decision. Revision 6 (review R5-05): it is no longer an untracked
    deduction. Builds and operator reads through the Console, CLI, or
    MCP are reserved in the same ledger before they run (MS2-D-46).
- **Account prepaid headroom** (revision 6 rewrites this bullet for review
  findings R5-01 and R5-02). Let `P = account_prepaid_credit_usd −
  HW_RADAR_APIFY_ACCOUNT_MARGIN_USD`, and let `HR_cycle` be Hardware Radar's
  own debit for the cycle: the settled amount of every `reconciled` row whose
  charge interval touches the cycle, plus the full estimate of every
  unreconciled row (all classes, operator included), plus any carried handoff
  consumption (MS2-D-45). Revision 11 adds the standing account-read debit,
  each reconciled row's `monitoring_bound_usd` while its monitoring interval
  touches the cycle (MS2-D-34, R10-03), and each `ApifyCycleDiscovery`
  allowance whose interval touches it (R10-05). A paid run is admitted only
  if **both** hold:
  1. *Snapshot check (R5-01):* `account_usage_usd + HR_cycle − HR_included +
     estimate ≤ P`.
     - `HR_included` is the settled amount of reconciled rows whose whole charge
       interval ended before `account_observed_at −
       HW_RADAR_APIFY_USAGE_INCLUSION_LAG_S`. That setting has **no default**;
       unset means no trustworthy inclusion watermark exists, so
       `HR_included = 0` and all of Hardware Radar's reconciled cycle spend is
       debited locally on top of the snapshot. The double counting of spend the
       snapshot already contains is accepted: it fails closed.
     - Reconciliation therefore never makes spend disappear from this check,
       however stale or lagging the snapshot. The owner may set the lag only
       from F5a evidence that the account usage figure includes a charge within
       that lag.
     - Revision 12 (owner decision (s5, 2026-09-25), R25): **check 1 is retired.** It needs the
       observed `account_usage_usd`, which the runtime can no longer read.
       `HR_included` and `HW_RADAR_APIFY_USAGE_INCLUSION_LAG_S` go with it.
       The owner's decision accepts the loss: the runtime sees the other
       workloads only through `E` and Apify's hard limit (R39). Reconciled
       spend still never disappears, because check 2 debits it in
       `HR_cycle`.
  2. *External-liability check (R5-02):* `HR_cycle + estimate +
     HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD ≤ P`.
     - `HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD` is an **owner-supplied** upper
       bound on everything other workloads sharing the account may consume in
       one billing cycle. Equivalently, `P −` that bound is Hardware Radar's
       owner-allocated share of the prepaid allowance. It is purely a
       Hardware Radar setting: Hardware Radar never reads another workload's
       code, records, or lifecycle state to derive it.
     - Revision 9 (owner decision (s4, 2026-09-25),
       [OQ26](../../resolved-questions.md#oq26--external-liability-bound-for-the-shared-apify-account); R33 resolved): it defaults to
       **5.00** per billing cycle, the owner's setting. Hardware Radar
       reserves up to $5.00 of the shared account's prepaid usage for
       consumption outside its ledger, which it neither controls nor
       observes. The bound is a conservative Hardware Radar accounting bound,
       not permission for another project to spend $5. Changing it is an
       owner decision. Revision 6's "no default" is withdrawn.
     - The default applies only when the variable is absent. An explicitly
       empty, non-numeric, negative, or non-finite value is never replaced by
       the default: every paid admission is then denied with
       `external_liability_unbounded`. A mis-rendered environment therefore
       fails closed rather than silently falling back. Paid admission is also
       denied, whatever the bound's value, when the snapshot is older than
       `HW_RADAR_APIFY_ACCOUNT_SNAPSHOT_MAX_AGE_S`, or when
       `account_usage_usd − HR_cycle`, a lower bound on other workloads'
       consumption, already exceeds the declared bound
       (`external_liability_exceeded`, which also trips the overrun latch and is
       reported, because the owner's bound no longer holds).
     - Revision 12 (owner decision (s5, 2026-09-25), R25): the stale-snapshot denial and
       `external_liability_exceeded` are retired (no observed usage). Their
       fail-closed role now falls to Apify's hard limit: a start refused
       with HTTP 402 trips `account_limit_refused` (MS2-D-48 *Hard-limit
       refusal*). An explicitly empty or invalid bound still denies
       `external_liability_unbounded`.
     - The bound covers a whole cycle, and a reservation that straddles a cycle
       edge (MS2-D-34) is checked against both cycles' bounds, so the external
       liability is reserved over the whole outstanding-work horizon.
       Revision 10 (entry-gate review note, not an ED finding): the next
       cycle's `P` cannot be evaluated before that cycle is observed, so
       admission evaluates it with the current snapshot. The first snapshot
       of the new cycle re-checks every carried row (MS2-D-47's committed-total
       check), and a failure trips the latch.
       Revision 12 (owner decision (s5, 2026-09-25), R25): both cycles use the same configured `P`.
       MS2-D-47's re-check applies the external-liability check only.
  - The margin defaults to 10% of the observed prepaid credit. It absorbs price
    and rounding error only. **It does not bound uncoordinated consumption by
    other workloads**; only the owner's declared bound does. Revision 5's claim
    that the cash ceiling "rests on this admission margin" is withdrawn.
    Revision 12 (owner decision (s5, 2026-09-25), R25): the default is 10% of the configured
    `HW_RADAR_APIFY_ACCOUNT_LIMIT_USD`. Its role is unchanged: price and
    rounding error only. It now also absorbs part of Apify's documented
    enforcement deviation of about 10% (R39).
  - Hardware Radar therefore uses the lesser of its target, its declared share,
    and the observed remaining allowance, and it never relies on pay-as-you-go
    overage. Whether the account as a whole stays within its prepaid credit
    also depends on the other workloads honoring the owner's bound; Hardware
    Radar cannot enforce that, and it detects a breach only at the next
    snapshot.
    Revision 12 (owner decision (s5, 2026-09-25), R25): the runtime no longer detects such a breach at
    all. Apify's hard limit refuses further starts, and the refusal trips
    `account_limit_refused`. The operator's cycle reconciliation (MS2-D-48)
    is how a breach is found after the fact (R39).
- **Cash-ceiling guard.** If the observed base price exceeds
  `HW_RADAR_APIFY_CASH_CEILING_USD` (20.00), every paid admission is denied with
  `cash_ceiling_exceeded_by_plan`. With the verified Starter plan ($19 base,
  $19 prepaid credit), cash outlay is $19 plus any overage. The two checks keep
  Hardware Radar's own admitted work within its share; they are not proof that
  the account incurs no overage.
  Revision 12 (owner decision (s5, 2026-09-25), R25): the base price is the operator-verified
  `HW_RADAR_APIFY_ACCOUNT_BASE_PRICE_USD`, not an observed value. The guard
  and its denial reason are unchanged.
- **Account backstop (owner-clarified).** The account-level usage limit is a
  secondary defense only, **not proof of zero overage**: Apify documents that
  enforcement may deviate by up to about 10%, so a $19 limit can still let
  usage pass $19. Verified account state 2026-09-24: it equals the
  prepaid credit ($19). The plan recommends keeping it at or below the prepaid
  credit and never raising it for Hardware Radar. No agent changes any billing
  or account setting without explicit owner authority. The project-level
  controls apply regardless of the account limit.
  Revision 12 (owner decision (s5, 2026-09-25), R25): the owner now also relies on the limit as the
  account-wide cap on the other workloads' spend, which the runtime can no
  longer observe. The project-level controls still apply in full, and
  Apify's documented deviation of about 10% is recorded in R39.
- **Cycle boundary.** A reservation whose charge interval (MS2-D-34) comes
  within `HW_RADAR_APIFY_CYCLE_BOUNDARY_GUARD_S` (default 3600 s, an
  assumption) of a cycle edge counts in both cycles. Unsettled reservations are
  carried into each new cycle at full estimate. A new run whose worst-case
  interval could straddle the cycle end must fit the current cycle's remaining
  allocation in full; it then also counts against the next cycle once that
  opens.
  - *Posting lag* (revision 10, entry gate, ED-16). Monthly usage has a daily
    breakdown (`dailyServiceUsages[].date` at 00:00Z), and when timed storage
    is posted is undocumented. The guard absorbs clock skew, not posting lag.
    A storage charge posted into a later daily bucket than its interval would
    be missing from class-cap attribution for that cycle; the account
    snapshot (check 1) still sees it. The amount is negligible at contract
    caps. It is a recorded residual that F5a step 4 measures, including the
    day the charge posts to. The default stays 3600 s. The review suggested
    86400 s, but under MS2-D-41's eligibility rule (revision 10) a guard that
    long would put every settlement read past the finalize deadline, so
    `stable_reads` could never succeed. The owner may raise the guard, and the
    finalize deadline with it, only from F5a evidence.
- **Secondary metric.** The trailing-31-day total is reported and logged at
  warning level when it exceeds `A`. It never admits or denies.
- **Verified account state, 2026-09-24** (read-only API; recorded separately from
  the rule above, which names no plan): plan STARTER; base price $19; prepaid
  usage credit $19; account limit $19; cycle 2026-09-05T00:00:00Z →
  2026-10-04T23:59:59.999Z; cycle usage about $0.09, all from other workloads
  sharing the account; data retention 31 days; the residential-proxy feature is
  available on the account.
- *Rejected (a):* one fixed "$20 minus deduction" cap (revision 4). It
  conflated cash with consumption and ignored other workloads.
- *Rejected (b):* the account limit as the enforcement point. It is shared,
  approximate (about 10%), and an owner billing setting.
  Revision 12 (owner decision (s5, 2026-09-25), R25): still rejected as Hardware Radar's enforcement
  point, since admission enforces `A`, the operator allowance, and check 2.
  The owner does accept the limit as the only account-wide bound on
  the other workloads (R39).
- *Rejected (c)* (revision 6): the 10% margin as the shared-account bound
  (review R5-02). Another workload can consume more than the margin between a
  snapshot and the end of an admitted run.
- *Rejected (d)* (revision 6): adding only unreconciled reservations to the
  snapshot (review R5-01). A reconciled run's spend then vanished from the check
  while the snapshot still predated it.
- *Reopen if* the owner changes plans, target, or cash ceiling, or Apify
  documents per-workload limits.
  Revision 12 (owner decision (s5, 2026-09-25), R25): or if Apify offers a scoped-token permission for account
  limits and usage, which would allow restoring check 1 (MS2-D-48 *Reopen if*).

**MS2-D-41 — Reservation → reconciliation lifecycle with provisional and
finalized usage (Slices D, E; revision 5, owner-clarified (s2, 2026-09-24)).**
This decision refines MS2-D-17, MS2-D-26, and MS2-D-32.
- **Flow.** `eligibility (check_admission) → estimate maximum cost → reserve →
  start run → poll completion → import bounded output → obtain finalized usage →
  account post-run retrieval, transfer, and storage cost → reconcile → release
  the unused reservation`.
- **Reserve.** Under the budget lock, the MS2-D-40 class check and both
  account checks run against the worst-case estimate. A reservation exactly
  equal to the remaining amount is admitted; one cent more is denied.
  Concurrent reservations serialize on the lock, so this ledger never
  oversubscribes Hardware Radar's allocation or its declared share. (Revision 6:
  the lock serializes one database only; other environments are excluded by
  MS2-D-45 and other workloads are bounded only by the owner's declared
  external liability, MS2-D-40.)
- **Usage states.** `reserved → usage_provisional → usage_finalized →
  reconciled`. Revision 6 (review R5-03) replaces revision 5's rule that the
  first read ≥ 10 s after `finishedAt` is final. **Elapsed time is only a
  minimum polling delay, never evidence of finality.**
  - *Evidence, kept apart from state.* Every usage read is appended to an
    immutable `ApifyUsageRead` row (`provider_run`, `read_at`,
    `usage_total_usd`, the `usageUsd` breakdown, `finished_at` as reported, and
    the unit-price settings version in force). Reads are never updated or
    deleted; reconciliation state lives on the reservation and cites the read
    rows it used.
  - *Provisional.* The first non-null `usage_total_usd` makes the row
    `usage_provisional`. No read earlier than
    `HW_RADAR_APIFY_USAGE_SETTLE_DELAY_S` (default 10 s, Apify's guidance)
    after `finishedAt` can count toward settlement.
  - *Eligible read* (Binding; revision 10, entry gate, ED-08). Whether the
    run's own `usage` later includes post-run reads of its default dataset,
    or the deletes, is undocumented. Under `stable_reads` a run could
    otherwise finalize before stage 1 reads the dataset and before cleanup.
    If Apify then posted those operations to the run, selector 4 would see an
    "upward correction" that double-counts with the post-run bound and could
    trip the latch falsely (MS2-D-47). So a read is eligible for the
    settlement predicate only if `read_at ≥ max(finishedAt,
    final_charge_op_at) + …_USAGE_SETTLE_DELAY_S +
    …_CYCLE_BOUNDARY_GUARD_S`; for a build row, `finishedAt` alone. The
    guard absorbs skew between the processing clock (`final_charge_op_at`)
    and the billing clock. The unfinalized deadline below is measured from
    the same `max(finishedAt, final_charge_op_at)`, so a late cleanup does not
    force `bound_unfinalized` by itself. Revision 11 (R10-04):
    `final_charge_op_at` is the write-once work-completion anchor (MS2-D-32
    *Settlement*). The polls and settlement reads that feed this predicate
    never move it, so two eligible reads, about 3,610 s and 3,670 s after
    the anchor at the defaults, fit well inside the 86,400 s deadline. While
    the anchor is null (import or deletion still open), no read is
    eligible and the deadline has not started. Earlier reads are still appended as
    evidence and still count in `max(execution bound, every observed read)`.
  - *Settlement predicate.* Under `HW_RADAR_APIFY_RUN_USAGE_SETTLEMENT =
    stable_reads`, the row becomes `usage_finalized` only when
    `HW_RADAR_APIFY_USAGE_STABLE_READS` (default 2) consecutive eligible reads,
    each at least `HW_RADAR_APIFY_USAGE_STABLE_INTERVAL_S` (default 60 s) after
    the previous one, return an identical `usage_total_usd` and an identical
    `usageUsd` breakdown. If F5a finds a documented platform finality signal,
    it may replace this predicate only through a plan revision.
  - *Default is the bound.* `HW_RADAR_APIFY_RUN_USAGE_SETTLEMENT` defaults to
    `bound`: the settled run usage is `max(execution bound, every observed
    read)`, so no capacity is returned. The owner may switch to `stable_reads`
    only after F5a's 0/10/30/120 s readings (plus the stable-read trail)
    converge. If those readings disagree at the last sample, the setting stays
    `bound`, and the disagreement is recorded as F5a evidence.
  - *Unfinalized runs.* A row that has not met the predicate by
    `HW_RADAR_APIFY_USAGE_FINALIZE_DEADLINE_S` (default 86400 s, an assumption)
    after `max(finishedAt, final_charge_op_at)` (revision 10; was
    `finishedAt`) is settled at `max(execution bound, every observed
    read)` with `settlement_basis = bound_unfinalized`. It stays visibly
    `unreconciled_stale` in the report until then, and at every cycle end it is
    carried at its full estimate. A null value never settles below the bound.
  - *Upward correction* (revision 7 replaces revision 6's text; reviews R5-03
    and R6-02). After reconciliation, selector 4 (MS2-D-23) keeps re-reading the
    run's (or build's) usage, at most every stable interval, until
    `correction_monitor_until = last_charge_at +
    HW_RADAR_APIFY_CORRECTION_WINDOW_S` (default 604800 s, seven days, an
    assumption) **and then until a successful closing read at or after that
    deadline commits** (revision 8, reviews R5-03 and R5-04). Elapsed time
    alone never closes monitoring, just as it never finalizes usage. A later
    read above the settled run usage is an upward correction, applied by
    MS2-D-47 in the same transaction as the read. A later lower read never
    returns capacity, because historical re-reads may be repriced. Revision
    10 (entry gate, ED-01) replaces "`GET` run and build reads must be
    verified free in E2; if they are billable, selector 4's reads are capped":
    selector 4's reads are always counted, capped by
    `…_MAX_CORRECTION_READS`, spaced to fit that cap, and priced at the
    API-call bound (MS2-D-32 *API calls*). "At most every stable interval" is
    only the minimum spacing. Revision 11 (R10-03): their cost is the row's
    monitoring allowance, debited in every cycle in which a further read is
    possible (MS2-D-34 *Monitoring charges*), not part of the settled amount.
  - *Window residual.* A correction that arises after the committed closing
    read is not observed (R37). Revision 8: a correction that arises inside
    the window but is not read until after the deadline, for example during a
    poller outage, is still observed, because the closing read applies it.
    Under the default `bound` settlement this is low-risk: the settled run usage
    already equals the enforced execution bound, so a larger figure would itself
    be an anomaly. The owner may choose `stable_reads` only when F5a's read
    trail also shows no correction after a period no longer than half the
    window (R37).
- **Post-run cost.** Dataset and KV reads, deletes, transfer, and timed storage
  after the run are account usage. Until F5a records the measurement
  (`HW_RADAR_APIFY_POST_RUN_COST_MODE=bound`, the default), the post-run cost
  equals the full post-run liability bound (MS2-D-32). After the owner reviews
  F5a's evidence, the mode may be set to `counted`: hw-radar's operation
  counters × verified unit prices × `(1 + margin)`. "Do not mark the budget
  reconciled merely because the Actor process stopped." Revision 11 (R10-03):
  in either mode the post-run cost excludes the monitoring allowance, which
  reconciliation never releases (MS2-D-34 *Monitoring charges*).
- **Reconcile.** Settled amount = settled run usage (per *Usage states*,
  including its basis) + post-run cost.
  - Lower than the reservation: the difference returns to capacity at
    reconciliation.
  - Higher than the reservation: the overrun latch trips (MS2-D-26), which
    pauses all further paid work. An overrun never triggers another paid call to
    repair itself.
- **Visibility.** Every budget error is operational state, not only a log line:
  the `budget_paused` freshness state carries its reason, and the spend report
  lists overruns, unsettled rows, stale snapshots, and the cycle figures.
- *Rejected:* trusting the value in the response that first reports
  `SUCCEEDED`. Apify documents it as preliminary.

**MS2-D-42 — The first Actor proof uses a controlled synthetic source (Slice D
fixtures, F5a; revision 5, owner-clarified (s2, 2026-09-24); OQ24 part (a)).**
- **Source.** The Actor `hw-radar-synthetic-collector` fetches static fixture
  pages committed in this public repository, over HTTPS, from
  `raw.githubusercontent.com` at a **pinned commit SHA** passed in its input.
  The base URL is fixed in the Actor code, and the input carries only the SHA and
  fixture paths.
  - *Why:* content addressed by a commit is deterministic and immutable, owned by
    the project, free of merchant terms-of-use questions, not short-retention,
    and it exercises real HTTP fetches, request and byte caps, and transfer usage.
  - *Rejected (a):* fixtures baked into the Actor image. That exercises no
    network fetch, transfer, or request limit.
  - *Rejected (b):* a named Apify storage as the source. Named storages persist
    indefinitely and cost money, and they are not a collection source.
  - *Rejected (c):* a hw-radar-hosted endpoint. The deployment has no public
    ingress.
  - *Rejected (d):* public scraping sandboxes. They are not project-owned, not
    pinned, and carry their own terms.
  - *Reopen if* GitHub raw-content rate limits or terms make the pinned fetch
    unreliable at the proof's small request count.
- **Fault injection.** Only this Actor accepts a `faultMode` input:
  `none | truncate_items | truncate_pages | truncate_time | truncate_bytes |
  partial_failure | contradictory_report | count_mismatch | unknown_schema |
  fail`. Each mode produces deterministic output for one MS2-D-14 row. The
  field exists only in this Actor's input schema, never in `hw-radar-input-v1`'s
  common part, so a merchant Actor cannot receive it.
- **Site and retention.** The synthetic site has a `SourceSite` and
  `SourceConfig` created by an idempotent management command (F5a), not a
  migration. It is registered in `SOURCE_RETENTION` with an indefinite class
  (`merchant_fact`), because MS2-D-33 denies bounded-class sites an Actor path.
- **Environment.** The proof runs from a non-production hw-radar environment,
  so synthetic rows never enter the production catalog. Revision 6 (review
  R5-04): revision 5's "lower the second environment's target by the first's
  reported consumption" is withdrawn. Moving paid admission between
  environments follows MS2-D-45: one Hardware Radar ledger authority per cycle,
  and a handoff only after the first environment is disabled and every one of
  its liabilities is settled.

**MS2-D-43 — Operator and agent workflow for Hardware Radar Actors (F5a, E7;
revision 5, owner-clarified (s2, 2026-09-24)).** The procedure is binding; the
command spellings are illustrative and are verified in D-prep and F5a.
- **Tool boundaries (research input `apify-ops.md`, 2026-09-24).** The Apify MCP
  server cannot create, push, build, or version an Actor; deployment is the
  Apify CLI (`apify push`), the REST API, or the GitHub integration. The MCP can
  run Actors and read runs, logs, datasets, and KV records only with a token or
  OAuth, beyond the four anonymous tools the project's `.mcp.json` loads today.
  It has no account-usage tool. Rollback is a build-tag reassignment. There is
  no self-service "disable Actor".
- **Create or update.** Edit `actors/<name>/` and its contract in one hw-radar PR
  with the gate green. Then, from a clean checkout of the reviewed commit on
  `dev` or `main` (never a PR branch), run `apify push` from `actors/<name>/`
  with the owner-provisioned operator/deploy key (*Credentials* below). The
  build lands under the `candidate` build tag. Record the git commit, Actor version, and build number
  in `actors/<name>/DEPLOYMENTS.md`.
  - D-prep verifies, through the Actor version's `sourceFiles` returned by the
    API, that `apify push` uploads only `actors/<name>/`. If it uploads more,
    the fallback is a Git-source Actor
    (`<repo>#<branch>:actors/<name>`) with **no** push webhook, built only by
    an explicit build API call.
  - *Rejected:* GitHub-integration auto-build on push. It deploys automatically
    from pushed code, which the owner ruled out, and it ignores `actor.json`'s
    version and tag.
- **Build.** `apify push` builds; a rebuild is an explicit build API call.
  Revision 6: every build is preceded by an operator reservation and followed
  by its recorded cost (MS2-D-46).
- **Smoke run.** A management command (`apify_smoke`, F5a) starts the
  `candidate` build on the synthetic source **through hw-radar's own admission
  and ledger**, imports it, and prints the contract checks. Never the MCP
  `call-actor` tool or Console "Start", which bypass the ledger.
- **Inspect output.** The authoritative inspection is hw-radar's import and
  `provider_run` record. Operator reads through the MCP (if widened), CLI, or
  Console are allowed read-only, each preceded by an operator reservation
  (MS2-D-46).
- **Measure resource usage.** `provider_run` records `stats` and the finalized
  `usageUsd` breakdown. F5a runs the measurement protocol: the finalization
  delta at 0, 10, 30, and 120 s; the account usage diff around a dataset read;
  the storage accrual after deletion; and the `date`-parameter cycle read. The
  account-limit enforcement experiment is **not** run on the shared account
  without explicit owner sign-off.
- **Deploy (promote).** After a green smoke, reassign the `prod` build tag to
  the candidate build. hw-radar settings name the Actor
  (`HW_RADAR_APIFY_ACTOR_ID`) and the build tag (`HW_RADAR_APIFY_ACTOR_BUILD`,
  default `prod`). Each run records the resolved build number, and a start whose
  build is outside the contract's version line is aborted and trips the latch
  (MS2-D-26).
- **Roll back.** Reassign `prod` to the previous build (a retag, no rebuild).
  The previous build keeps a `rollback` tag, because untagged builds unused for
  90 days are garbage-collected.
- **Disable execution.** Hardware Radar-side kill switches, in order of reach:
  `HW_RADAR_APIFY_ENABLED=false` (all paid admission); setting the source's
  `collection_provider=local` or disabling the source; removing the `prod` tag;
  and, in an emergency, aborting active runs through hw-radar's abort call.
  Revoking the runtime token is the last resort.
- **Credentials** (revision 9, owner decision (s4, 2026-09-25),
  [OQ25](../../resolved-questions.md#oq25--hardware-radar-apify-credential-and-mcp-tool-scope)). Two roles use two separate
  credentials. Neither is ever the apify-actors venture's token, and CI holds no
  Apify credential.
  - *Operator/deploy:* the owner's dedicated Hardware Radar key at OpenBao
    `secret/apps/hw-radar/agent/apify`. It is **unscoped** (full account),
    because Apify does not allow scoped tokens to create or modify Actors. It is
    used only for `apify push`, explicit builds, and operator inspection, each
    under an operator reservation (MS2-D-46). It is operator-held: it is never
    rendered to the production app environment, never read by hw-radar's
    runtime, and never an MCP credential.
  - *Runtime:* a separate, owner-created **scoped** token (Apify Console, "Limit
    token permissions": run only Hardware Radar-owned Actors and read their runs
    and default storages; MS2-D-15 lists every call the runtime makes). It lives
    at `secret/apps/hw-radar/apify` and is rendered by the OpenBao Agent as
    `HW_RADAR_APIFY_TOKEN` (NFR-003). It does not exist yet (R25 residual).
    Revision 12 (owner decision (s5, 2026-09-25), R25): the token exists as of 2026-09-25. It needs
    **Read** on the Actor in addition to Run, and it needs no account
    permission, because the runtime reads no account state (MS2-D-48).
    Rendering it to production is deferred until Slice E live admission is
    ready. `HW_RADAR_APIFY_ENABLED=false` (the default) remains the fail-closed
    kill switch regardless of which credentials are present.
  - Agents deploy only on an explicit owner or orchestrator instruction for that
    deployment.
- **MCP tool filter (owner decision (s4, 2026-09-25), OQ25, R24 resolved;
  `.mcp.json` is not edited by this plan).** `.mcp.json` stays unchanged, with
  the four anonymous read-only tools. MCP is an operator surface, not the
  runtime protocol. The target filter below is enabled only once a scoped read
  credential and operator reservations (MS2-D-46) exist, and it adds only read
  tools:
  `https://mcp.apify.com?tools=search-actors,fetch-actor-details,search-apify-docs,fetch-apify-docs,get-actor-run,get-actor-run-list,get-actor-log,get-dataset,get-dataset-items,get-dataset-schema,get-key-value-store,get-key-value-store-keys,get-key-value-store-record`.
  - It excludes `call-actor` and the RAG web-browser Actor tool (both
    spend-capable), `abort-actor-run`, and the task tools.
  - Spend: the listed tools start no run, but dataset and KV reads after a run
    are billed account usage. Each inspection session is reserved first
    (MS2-D-46).
  - Authentication: any non-anonymous tool requires OAuth (account-wide) or a
    bearer token. A token cannot be committed to `.mcp.json` in this public
    repository, so it must come from a local, uncommitted configuration. The
    unscoped operator/deploy key is not used for the MCP (revision 9).
  - Hardware Radar's runtime never depends on the MCP.

**MS2-D-44 — Production Actor-backed merchant sources pass a source-admission
decision (F5b; revision 5, owner-clarified (s2, 2026-09-24); OQ24 part (b)).**
- **Artifact.** Each candidate gets a record under
  `docs/research/source-admission/` from the template in that directory's
  README: the business value; URLs reviewed; Terms date and retrieval date;
  automated-access language; robots directives for the proposed endpoints;
  official API or structured alternatives; expected cadence and volume;
  retention constraints across Actor memory, dataset, KV store, staging, raw,
  and canonical storage; authentication or login; anti-bot behavior; browser or
  proxy need; maintenance and cost; and a recommendation of
  `eligible | permission-required | exclude`.
- **Rules.** Newegg stays excluded (R1). Robots permission is not contractual
  permission, and the absence of an obvious prohibition is not permission. A
  source that needs residential proxies, automatic proxy rotation, CAPTCHA
  solving, a paid unblocker, or a paid third-party Actor fails admission unless
  the owner revisits it. A bounded-retention source also fails an Actor path
  under MS2-D-33.
- **No grandfathering.** Existing local connectors are not admitted to an Actor
  path by virtue of existing. A finding that an existing, disabled local
  connector conflicts with this acquisition posture is recorded separately (its
  own admission record or an open question), not folded into an Actor decision.
- **Gate.** F5b starts only after the owner answers OQ24 for a named candidate
  with an `eligible` record.

**MS2-D-45 — One Hardware Radar ledger authority per billing cycle across
environments (Slice E, migration 0022; revision 6, review R5-04).**
- **Hazard.** Each hw-radar environment has its own database, ledger, and
  advisory lock, so two enabled environments could each admit up to the target
  in one cycle. Revision 5's handoff subtracted only reported consumption, so a
  proof environment's still-running or late-finalizing run could settle after
  production had been given the remaining target.
- **Authority record.** A new `ApifyLedgerAuthority` table records, per billing
  cycle, whether this environment holds Hardware Radar's paid-admission
  authority: `cycle_start`, `kind` (`origin | handoff | continued`),
  `ledger_id` (a per-environment identifier setting), `carried_consumption_usd`,
  `attested_by`, `created_at`, `handed_off_at`, and (revision 7)
  `handed_off_to` (the destination ledger id) and `handoff_record_digest`. Every paid admission (all
  classes) is denied with `ledger_authority_missing` unless the current cycle
  has an authority row that is not handed off.
  - `origin`: created by the owner-only command `apify_ledger_claim --origin
    --reason`, attesting that no other environment admitted paid Hardware Radar
    work in this cycle. It is the only row whose truth rests on an attestation
    rather than a check (R35).
  - `continued`: created automatically at cycle rollover for the environment
    that held authority at the previous cycle's end and never handed it off.
  - `handoff`: created only by importing the previous holder's export (below).
- **Handoff, fully drained.** `apify_ledger_handoff --export --to
  <destination ledger id>` in the current holder succeeds only if
  `HW_RADAR_APIFY_ENABLED` is false and the ledger has **no** reservation in any
  state other than `reconciled`, `released`, or `denied` (so no running run, no
  provisional or unfinalized usage, no retained storage, no open operator
  reservation), **and** (revision 7, reviews R5-04 and R6-01) no reconciled row
  whose correction monitoring is still open (MS2-D-41, selector 4).
  Revision 8 (reviews R5-03 and R5-04) makes "closed" mean *evidenced*, not
  elapsed. Every reconciled row that carries a correction obligation (a set
  `correction_monitor_until`) must have a committed
  `correction_monitor_closed_at` and a `correction_closing_read` whose
  `read_at ≥ correction_monitor_until`. No reservation may have an
  `ApifyUsageRead`, taken after its `reconciled_at`, whose usage total exceeds
  its `settled_run_usage_usd`; that would be a correction not yet applied
  (pre-settlement reads are governed by the MS2-D-41 settlement predicate,
  not by this check). A row past its deadline without
  a committed closing read, including one whose closing read keeps failing,
  blocks the export exactly as an open window does.
  - *Exclusive destination.* The export, under the budget advisory lock, marks
    its own authority `handed_off`, persists `handed_off_to` and the record's
    digest, and writes a handoff record: source ledger id, **destination
    ledger id**, cycle start, the settled consumption of every class that
    touches the cycle (including every correction applied by a closing read),
    and the drain attestation, which lists each obligation-bearing row's
    closing read (`read_at` and amount; revision 8). The source can never admit
    again that cycle. The binding is irrevocable: a repeated export to the same
    destination returns the same record (idempotent retry), and an export to
    any other destination is refused.
  - *Import.* `--import` refuses a record whose destination is not this
    environment's `HW_RADAR_APIFY_LEDGER_ID`, whose cycle is not the current
    one, or that shows any open liability, open correction monitoring, or an
    obligation-bearing row without closing-read evidence (revision 8). It
    creates the `handoff` authority with `carried_consumption_usd`, keyed by the
    record digest, so re-importing the same record is a no-op. Carried
    consumption counts in `HR_cycle` and against the class caps (MS2-D-40).
  - *Non-reservation debits* (Binding; revision 11, R10-05). The record also
    carries, as separate lines inside the carried consumption, the source's
    debits for the cycle that are not reservations. These are its full
    standing account-read debit (`…_MAX_ACCOUNT_READS_PER_CYCLE ×
    api_call_bound`; the actual reads are not attributable, so the whole
    allowance moves), every `ApifyCycleDiscovery` allowance whose interval
    touches the cycle, and the monitoring allowance of every row whose
    monitoring interval touches it (each closed, by the drain rule above).
    The destination counts them in `HR_cycle`, and in `A` for the runtime
    lines. It opens its own `ApifyBudgetCycle` row for the cycle with its own
    account-read counter and standing debit, because its reads are separate
    calls. A same-cycle handoff therefore counts both environments'
    allowances; that over-counts and fails closed.
    Revision 12 (owner decision (s5, 2026-09-25), R25): the standing account-read and discovery lines
    are withdrawn (MS2-D-48 *Claim and handoff*). The record carries the
    settled runtime and operator lines, the monitoring allowance, and the
    carried consumption. Neither environment has an account-read counter.
  - *Serialization.* Every admission (all classes) re-reads its authority row
    inside the same budget-locked transaction that creates the reservation, and
    the export takes that lock. So an admission either commits first (and the
    export then sees an open reservation and refuses) or sees `handed_off` and is
    denied. Upward corrections (MS2-D-47) take the same lock.
  - *Corrections after handoff* (revision 8 withdraws revision 7's "no
    correction after handoff, by construction"; review R5-04). Closure proves
    only that a successful read at or after the deadline saw no uncounted
    usage and that any correction it saw is in the exported totals. It does not
    prove provider finality. A correction that arises after the closing read is
    not observed by either environment; that is the separate, acknowledged R37
    residual, not a property the handoff guarantees away.
  - *Feasibility.* Handoff inside a cycle stays possible: the source must stop
    admitting at least one correction window (default seven days) before the
    handoff, and every closing read must then succeed. Revision 7 therefore
    keeps the simpler prohibition and does not transfer monitoring
    obligations.
- **Unsettled handoff is not supported.** If the old environment cannot drain
  (for example a permanently unfinalized run, a correction window still
  open, or a closing read that has not yet succeeded), the new environment
  waits for the next cycle, or for the old run to settle at its bound
  (`bound_unfinalized`, MS2-D-41) and its closing read to commit
  (revision 8).
  Revision 7: at a cycle rollover, the `continued` authority stays with the old
  holder; a new environment still needs an exported, destination-bound record. Transferring open liabilities between
  databases was rejected because it needs cross-database reconciliation
  ownership the design does not otherwise require.
- *Rejected (a):* one shared ledger database for all environments. It couples
  a proof environment to production's database for a one-time handoff.
- *Rejected (b):* revision 5's target reduction by reported consumption.
- *Reopen if* more than one environment must admit paid work in the same cycle.

**MS2-D-46 — Operator operations are reserved in the same ledger (Slice E,
F5a; revision 6, review R5-05).**
- **Class.** Builds, rebuilds, and operator inspection through the Console, CLI,
  or MCP (dataset, KV, and log reads) use admission class `operator`, capped by
  `HW_RADAR_APIFY_OPERATOR_ALLOWANCE_USD`. Runtime classes are capped by `A`
  (MS2-D-40), so runtime plus operator stays within the target.
- **Before execution.** The operator (or agent) runs `apify_operator_reserve
  --kind build|inspect|probe --reason …` (revision 11 adds `probe`), which
  creates an `operator` reservation under the budget lock at a conservative
  bound and passes both account checks (MS2-D-40):
  - build: `HW_RADAR_APIFY_OPERATOR_BUILD_BOUND_USD`, default 0.41 (the fixed
    4,096 MB build memory × the fixed 1,800 s build timeout × $0.20/CU, research
    input `apify-billing.md` B6–B7), plus (revision 10, ED-01) the build's
    API-call bound `(…_MAX_RUN_POLLS + …_MAX_CORRECTION_READS) ×
    api_call_bound` (MS2-D-32). Revision 11 (R10-03, R10-12) keeps that sum
    but holds its `…_MAX_CORRECTION_READS × api_call_bound` part as the
    build's monitoring allowance (MS2-D-34). The whole build reservation is
    `build_reservation = …_OPERATOR_BUILD_BOUND_USD + (…_MAX_RUN_POLLS +
    …_MAX_CORRECTION_READS) × api_call_bound`. That is about $0.423 at the
    defaults (`72 × $0.000181 ≈ $0.013` of calls), so two build reservations
    fit the 1.00 allowance and a third does not;
  - inspect (revision 7, review R5-05; replaces revision 6's flat $0.01): an
    **inspection envelope** limited to the named run's default storages, with
    finite limits `HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_ITEMS` (dataset items
    read, default 1,000), `…_OPERATOR_INSPECT_MAX_RECORD_READS` (KV record,
    key-list, and log reads, default 20), and `…_OPERATOR_INSPECT_MAX_BYTES`
    (bytes transferred out, default 10 MB); the defaults are assumptions. The
    bound is priced from the verified unit-price settings: items × the dataset
    read price, record reads × the API-call bound (revision 10, ED-01; this
    replaces "× the KV read price, log reads priced as KV reads unless E2
    verifies them free", and it also covers key-list reads, which the KV read
    price understated), bytes × the higher of the two transfer prices, all ×
    `(1 + margin)`. With a missing price, paid inspection is denied
    (`pricing_unverified`). The operator counts operations against the envelope
    (for example with the MCP or CLI item `limit`) and must reserve a new
    envelope before exceeding it. If the allowance cannot fit one, no further
    paid inspection is done that cycle. Revision 12 (owner decision (s5,
    2026-09-25), R25): an operator-key account read (`GET /v2/users/me`,
    `/limits`, or `/usage/monthly`, outside the application) counts as one
    record read of an `inspect` envelope, priced at `api_call_bound`. The
    envelope therefore also budgets the operator verification after
    authority exists, the cycle reconciliation, and the F5a account-usage
    measurements (MS2-D-48). The "named run's default storages" limit does
    not apply to these reads;
  - probe (revision 11, R10-06): the **capability-probe envelope** for the
    R25 403-versus-404 check (MS2-D-25 *404 rule*). It covers creating one
    throwaway **unnamed** dataset with the operator key (a named storage is
    retained indefinitely), the runtime token's `DELETE` attempts against it,
    the operator key's cleanup `DELETE`, and their retries, all within
    `…_OPERATOR_PROBE_MAX_CALLS` (default 10, an assumption). The bound is
    `…_OPERATOR_PROBE_MAX_CALLS × (api_call_bound + dataset write price)`,
    covering each call's transfer and the create and delete as unpriced
    "other operations" priced as dataset writes, plus the timed storage of
    one `max_item_bytes` dataset over `storage_hours` (MS2-D-26), all ×
    `(1 + margin)`. The operator counts calls against the envelope and
    reserves a new one before exceeding it. `--settle` is refused until the
    operator records the dataset id and its verified deletion. An unsettled
    probe counts at its full bound in every cycle, like a `delete_failed`
    row, and blocks a handoff export. A settled probe reconciles at its full
    bound, like an inspection, with `last_charge_at` equal to the settle time
    and no correction obligation.

  When the allowance cannot fit the bound, the command refuses and the
  operation must not be performed (`operator_allowance_exhausted`). This is a
  procedural stop: the Console, CLI, and MCP cannot be intercepted, so R36
  records the residual.
- **After execution.** `apify_operator_reserve --settle <id>` records the build's
  cost read from the build record once it meets the MS2-D-41 settlement
  predicate (with the MS2-D-41 correction window afterwards), or, for an
  inspection envelope, the envelope's full bound. An inspection never settles
  below its envelope, because per-read attribution in the shared account is not
  separable. Unsettled operator reservations count at
  their bound, block a handoff export (MS2-D-45), and appear in the spend
  report.
- **Build identity and settlement** (revision 8, review R7-01; refines *After
  execution*). An operator reservation never has a `provider_run`: it is null
  for every `operator` row (every kind), as for denials. A build does not exist
  when it is reserved, so for a build `--settle <id> --build-id <build id>`
  first **binds** the provider build id to the reservation in its own
  budget-locked, committed transaction (`provider_build_id`, unique when set,
  written once; a different id for a bound row is refused). The command does
  not wait for the build. From then on the poller owns the row from persisted
  state alone: selector 2 (MS2-D-23) reads the build record (MS2-D-15) until
  the build is terminal and its usage meets the MS2-D-41 predicate, or
  settles it at `max(build bound, every observed read)` (`bound` mode, a
  record without dollar usage, or the finalize deadline). Reconciliation sets
  `last_charge_at` from the build's `finishedAt` and, from revision 11,
  `reconciled_at` (MS2-D-34), and
  `correction_monitor_until`, and selector 4 then monitors the build and
  closes it by a closing read, like a run. A build row with no bound id stays
  counted at its bound. Every build read is an `ApifyUsageRead` that cites the
  reservation with a null `provider_run`. An inspection has no provider figure
  to re-read, so its `--settle` reconciles it at once at the envelope's bound,
  with `last_charge_at` equal to the settle time and no correction obligation.
- **Reporting.** The cycle report and the handoff record include operator
  consumption, so Hardware Radar's reported consumption covers every
  attributable operation that went through this procedure.
- *Rejected:* revision 5's untracked $0.50 deduction. It bounded nothing.

**MS2-D-47 — An upward correction re-checks every budget invariant (Slice E;
revision 7, review R6-02).** This decision refines MS2-D-26 and MS2-D-41.
- **Hazard.** Revision 6 tripped the latch only when a corrected run exceeded
  its own reservation. Capacity a run released at reconciliation may already be
  reserved by other work, so a correction still below the original estimate
  can push an aggregate past its limit.
- **Apply, atomically.** Under the budget advisory lock, one transaction:
  1. appends the `ApifyUsageRead`;
  2. raises `settled_run_usage_usd` and `actual_usd` on the reservation (never
     lowers them);
  3. recomputes, for **every** billing cycle the row's charge interval touches
     (MS2-D-34; the charge's billing clock, never the correction's processing
     time, MS2-D-39), the committed totals with no new estimate: the row's class
     cap (`watch_refresh`, `discovery`, or `operator`), the runtime allocation
     `A`, the operator allowance, the target, and both account checks of
     MS2-D-40 (snapshot and external liability) against the latest snapshot;
     Revision 12 (owner decision (s5, 2026-09-25), R25): the external-liability check only, against the
     configured `P` (MS2-D-48). An unset or invalid account setting is
     reported as an unverifiable breach, which fails closed;
     Rev-12 Codex follow-up (R12-04): "an unset or invalid account setting"
     means exactly the six cases of `budget.account_setting_problem(cfg,
     now)`: anchor, limit, base price, retention, verified-on, and margin.
     The correction's own processing time is passed as `now`. Admission and
     corrections share this one contract (E9.2). The cash-ceiling,
     retention-versus-lifetime, and 744 h checks stay admission guards;
  4. trips the overrun latch with `post_admission_invariant_breach` if **any**
     of them is exceeded, or with the existing overrun reason if the row's
     settled amount exceeds its own reservation;
  5. (revision 8, reviews R5-03 and R5-04) if the read is the row's closing
     read (MS2-D-23 selector 4), sets `correction_monitor_closed_at` and
     `correction_closing_read`. Closure therefore commits with the correction
     or not at all.

  The same transaction runs for every selector-4 read, including one that
  finds no correction, so no read is ever committed without its correction.
- **Effect.** Paid work pauses at once through the latch (MS2-D-26); no future
  admission has to fail first. The breach and the correction appear in the spend
  report.
- *Rejected:* checking only the corrected row against its own reservation
  (revision 6).

**MS2-D-48 — The runtime reads no Apify account state: configured billing cycle
and operator-verified account settings (Slice E; revision 12, owner decision
(s5, 2026-09-25), R25).** This decision amends MS2-D-15, -17, -32, -34, -40,
-45, -46, and -47 where they are marked.
- **Evidence (2026-09-25, `GET` only).** The runtime token
  `HW_RADAR_APIFY_TOKEN` got `403 insufficient-permissions` from
  `/v2/users/me`, `/v2/users/me/limits`, and `/v2/users/me/usage/monthly`.
  It got 403 on a foreign dataset and `404 record-not-found` on a nonexistent
  one. Apify's scoped-token form offers account-level permissions only for
  Actors, Tasks, Schedules, and Storages, and resource-specific permissions
  apply only to existing resources (docs.apify.com/platform/integrations/api,
  retrieved 2026-09-25). No account, usage, or limits permission is
  documented. At the same time, the operator key read plan STARTER, prepaid
  usage credit $19, account usage limit $19, cycle 2026-09-05T00:00:00Z →
  2026-10-04T23:59:59.999Z, and data retention 31 days. The account is shared
  with the separate apify-actors venture.
- **Owner decision (2026-09-25; [OQ30](../../resolved-questions.md#oq30--runtime-apify-account-reads-r25)).**
  The owner chose the option "Drop runtime account reads (Recommended)". The
  runtime stops calling the account endpoints. A configured anchor supplies
  the billing cycle, and the operator key checks the anchor before enabling.
  Apify's own $19 limit, equal to the prepaid credit and verified, caps
  account-wide cash. Hardware Radar's ledger, the $12 target, and the $5
  allowance for the account's other workloads all stay. The runtime can no
  longer see the apify-actors venture's actual spend, only Apify's hard cap.
  Production never needs the unscoped key.
- **Credential boundary (Binding).** The unscoped operator key
  (`secret/apps/hw-radar/agent/apify`) is never rendered to any application
  runtime, whether production or proof. Every operator read that uses it
  happens outside the application, from the workstation (Apify CLI or
  `curl`) or the Apify Console. Its result enters Hardware Radar only as the
  configuration below, together with the evidence date
  `…_ACCOUNT_VERIFIED_ON`.
- **Cycle (Binding).** `HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR` is an ISO-8601
  timestamp of one observed cycle start, which is the
  `monthlyUsageCycle.startAt` the operator read. It has no default. Settings
  parse it to `None` when it is absent, empty, unparseable, naive, not at UTC
  offset zero, not exactly `00:00:00.000`, or on a day of the month above
  28. A valid anchor is normalized to UTC.
  - *Derivation* (pure, `budget.billing_cycle_bounds(anchor, now)`). Let
    `k = 12 × (now.year − a.year) + (now.month − a.month)` and let
    `start(k)` be the UTC midnight on day `a.day` of the month `k` months
    after the anchor's month. Each `start(k)` is computed from the anchor
    directly, never from the previous cycle, so no drift accumulates. If
    `start(k) > now`, then `k −= 1`. The cycle is `[start(k), start(k + 1) −
    1 ms]`, which matches Apify's `…T23:59:59.999Z` end stamps. A `now` before
    the anchor has no cycle. A `now` in the sub-millisecond gap after
    `cycle_end` is outside the cycle and is denied `cycle_unknown`, which
    fails closed.
  - *Month-length edge cases.* Days 29–31 are rejected rather than clamped.
    Apify does not document how an anniversary cycle anchored on day 29–31
    runs through a shorter month, and a wrong guess would silently move a
    boundary by up to three days. On days 1–28 every month has the anchor
    day, so a cycle lasts 28–31 days (at most 744 h). Year rollover and
    February (leap or not) need no special case. All arithmetic is in UTC,
    and no local time or DST applies.
  - *Materialization* (`ledger.ensure_cycle(config, now)`, called with the
    budget lock held). It replaces API discovery. It derives the cycle and
    handles four cases:
    1. A row with the derived `cycle_start` and the same `cycle_end` exists.
       Return it.
    2. A row with the derived start but a different end exists (an earlier
       anchor change clamped it). This is a conflict.
    3. A recorded row starts inside the derived cycle (`start(k) <
       row.cycle_start ≤ derived end`), which means the anchor moved
       backward into recorded history. This is a conflict.
    4. Otherwise, clamp every older row that overlaps the derived cycle
       (`row.cycle_start < start(k) ≤ row.cycle_end`) to end at `start(k) −
       1 ms`. This is revision 5's plan-change clamp, kept. Then create the
       row: `cycle_start`, `cycle_end`, `allocation_usd`, `opened_at`,
       `account_read_count` 0, and `account_*` null. Run
       `_ensure_continued_authority` (MS2-D-45).
    A conflict creates nothing, is logged at error level, and makes the
    caller deny `cycle_unknown` (detail: the anchor conflicts with a recorded
    cycle). Only the owner resolves a conflict, by correcting the anchor.
    Hardware Radar never repairs recorded cycles itself. `reserve`,
    `claim_origin`, `export_handoff`, and `import_handoff` call
    `ensure_cycle`. The read-only report and `reconcile.invariant_breaches`
    read recorded rows only.
  - *Drift* (a plan change or other billing change that moves the real
    cycle). The runtime cannot observe it, because no run, build, or storage
    response carries the billing cycle. The controls are procedural and
    fail-closed:
    - a billing or plan change is an owner action, and no agent makes one
      (MS2-D-40);
    - the operator procedure below requires `HW_RADAR_APIFY_ENABLED=false`
      and re-verification of every configured value before re-enabling;
    - a start that Apify refuses at the hard limit trips the latch (*Hard-limit
      refusal*);
    - the operator's cycle reconciliation compares the configured values
      with the account (*Operator reconciliation*).
    The residual is R39. A forward anchor change is absorbed by the clamp. A
    backward one denies until the owner resolves it.
- **Account settings (Binding).** All four have no default, because each value
  belongs to one account. An unset or invalid value denies every paid
  admission with `account_state_unobservable`, and the detail names the
  setting:
  - `HW_RADAR_APIFY_ACCOUNT_LIMIT_USD` (Decimal). The operator-verified
    lesser of the account usage limit (`limits.maxMonthlyUsageUsd`) and the
    prepaid usage credit (`plan.monthlyUsageCreditsUsd`). It must be finite
    and greater than 0. It replaces the observed prepaid credit in `P`, so
    admission never plans against overage.
  - `HW_RADAR_APIFY_ACCOUNT_BASE_PRICE_USD` (Decimal). The plan's monthly
    base price, finite and at least 0, used by the cash-ceiling guard.
  - `HW_RADAR_APIFY_ACCOUNT_DATA_RETENTION_DAYS` (int). `limits.dataRetentionDays`,
    at least 1, used by the MS2-D-40 retention consistency check.
  - `HW_RADAR_APIFY_ACCOUNT_VERIFIED_ON` (ISO date). The UTC date of the
    operator verification that produced the anchor and the three values
    above. Valid only if it is on or after the anchor's date and not after
    `now`'s UTC date. It is evidence, not an expiry: revision 12 adds no
    re-verification age (owner point in R39).
    Rev-12 Codex follow-up: the owner answered on 2026-09-25 with "No expiry; warn
    only". Admission never ages it out. `apify_spend_report` prints a
    non-blocking warning when it predates the current cycle's start
    (E9.6).
  - `HW_RADAR_APIFY_ACCOUNT_MARGIN_USD` is unchanged. Absent means 10% of
    `…_ACCOUNT_LIMIT_USD`, and present but invalid means
    `budget_setting_invalid`.
- **Admission (Binding; answers "what replaces the observed usage").**
  Nothing replaces the observed usage. `decide_admission` keeps its blanket
  stops and the estimate. It then applies, in order:
  1. ledger authority;
  2. `cycle_unknown` when there is no cycle, or `now` is outside it;
  3. `account_state_unobservable` for an invalid account setting;
  4. the retention check;
  5. the 744 h check;
  6. the cash-ceiling guard on the configured base price;
  7. the class caps: runtime and operator debits as before, without the
     standing or discovery terms;
  8. the external-liability check, `HR_cycle + new + E ≤ P` for the current
     cycle and the next, with `P = ACCOUNT_LIMIT_USD −
     account_margin_usd(ACCOUNT_LIMIT_USD)` and `HR_cycle = runtime committed
     + operator committed + carried handoff`.

  Check 1 (`usage + HR_cycle − HR_included + new ≤ P`) and the
  `external_liability_exceeded` latch check are removed with their inputs.
  At the verified values, `P = 19.00 − 1.90 = 17.10`, and check 2 lets
  `HR_cycle` reach at most `17.10 − 5.00 = 12.10`, so the $12 target binds
  first. A lower configured limit makes check 2 bind.
- **Hard-limit refusal (Binding).** Apify documents HTTP 402 on `POST
  /v2/acts/{actorId}/runs` when "the user has exceeded their usage limit,
  does not have enough credits, or the request lacks authentication and
  payment credentials" (`api/v2/act-runs-post`, retrieved 2026-09-25). The
  `error.type` for the usage-limit cause is not documented; the example is
  `x402-payment-required`. So the classification uses the status code, not
  the type. When `client.start_run` raises `ApifyApiError` with
  `status_code == 402`, the start job:
  - records `start_error` as today, including the `error_type`;
  - after that commit (ED-05 order), trips the overrun latch with the new
    `LatchReason.ACCOUNT_LIMIT_REFUSED` (`account_limit_refused`) for the
    row;
  - returns `START_FAILED` with the reason `account_limit_refused`.

  The row stays unstarted and its reservation stays open. Selector 3 handles
  it at its deadline as `orphaned_start`, unchanged: the rule for a lost
  response is not relaxed for a 402. A 402 means that the account has reached
  its hard limit. The other workloads may have exceeded `E`, Hardware Radar
  may have under-counted (R38), or the configured state may be wrong (R39).
  Each needs the owner, and `apify_budget_reset --reason` is the reset. A 402
  on a poll, read, or delete keeps that call's existing handling, so the
  drain never stops.
  - *Durable recovery* (Rev-12 Codex follow-up, R12-01; Binding). The 402 and its
    latch trip commit in two transactions. If the process is lost between
    them, the persisted `stage_detail.start_error.status_code == 402` is the
    durable record, and `trip_stranded_start_refusals_locked` repairs the
    missing trip in two places:
    - inside every `reserve`, under the budget lock and before the latch is
      read, so no later admission of any class can slip through;
    - in each poll tick's stranded-latch sweep.
    A row that has any `account_limit_refused` trip, open or cleared, is
    never re-tripped. A cleared trip is the owner's deliberate reset (R21
    rule), not an unrecorded one. The estimator-bump clear does not apply
    to this reason. *Rejected:* one transaction for the provider-row write
    and the trip. The ED-05 lock order forbids taking the budget lock under
    the provider-row lock. *Rejected:* waiting for the row's `orphaned_start`
    deadline. Other sources and operator reservations would be admitted in
    the meantime.
- **Retired, kept, and repurposed (answers the fate list).**

  | Item | Revision 12 |
  | --- | --- |
  | `ApifyCycleDiscovery`, `…_MAX_DISCOVERY_READS`, `…_DISCOVERY_READ_INTERVAL_S`, `reset_discovery`, `discovery_status`, `apify_budget_reset --discovery`, the discovery allowance | retired: code and settings removed; the table stays, empty (*Schema*) |
  | Standing account-read debit, `…_MAX_ACCOUNT_READS_PER_CYCLE`, `PerCallBounds.account_read` | retired; `ApifyBudgetCycle.account_read_count` stays 0 |
  | `account_read_useful`, `refresh_account_snapshot`, `AccountReader`, `RefreshOutcome` | retired; `LedgerAdmission.admit(request)` calls only `reserve` |
  | `…_ACCOUNT_SNAPSHOT_MAX_AGE_S`, `…_USAGE_INCLUSION_LAG_S`, `HR_included` | retired with check 1 |
  | `…_ACCOUNT_MARGIN_USD` | kept; default base is the configured limit |
  | `cycle_unknown` | kept; repurposed to anchor unset, invalid, in the future, or conflicting |
  | `account_state_unobservable` | kept; repurposed to an account setting unset or invalid |
  | `external_liability_exceeded`, `AdmissionDecision.trip_latch` | retired (no producer) |
  | `account_limit_refused` | new `LatchReason` |

- **Schema (no change).** `0021` and `0022` have not been deployed to
  production (it runs migrations up to `0020`). However, `0022` is merged on
  `dev` and applied in development and leg databases. Revision 12 therefore
  changes no model and adds no migration. `ApifyCycleDiscovery` stays an
  empty table, and `ApifyBudgetCycle.account_prepaid_credit_usd`,
  `account_base_price_usd`, `account_limit_usd`, `account_usage_usd`,
  `account_observed_at`, and `account_data_retention_days` stay null.
  `makemigrations --check` must still report no changes. `denial_reason` and
  the latch `reason` are free text (E3), so the vocabulary changes need no
  migration.
  - *Rejected:* editing `0022` in place. Databases that already applied it
    would diverge from the migration history.
  - *Rejected:* a `0023` drop migration now. It is a schema change that the
    brief asks to avoid, and it is not needed for correctness. A later slice
    that touches this schema may drop the dead table and columns under its
    own plan text.
- **Claim and handoff stay cycle-scoped.** Authority rows are still keyed by
  `cycle_start`, now the derived start, and `continued` still passes only
  between adjacent recorded cycles. `claim_origin` materializes the cycle, so
  bootstrap no longer needs a denied start first. The export record keeps
  version 1 and drops the `discovery_allowance_usd` and
  `standing_account_read_usd` lines, which have no source any more. No
  record has been exported from a deployed environment. An older record that
  still carries them is accepted and over-counts, which fails closed. Two
  environments must configure the same anchor. A mismatched anchor makes
  `import_handoff` refuse the record with `wrong_cycle`.
- **Operator verification (procedural; before enabling).** With the operator
  key, outside the application, the operator reads `GET /v2/users/me/limits`
  and `GET /v2/users/me` (or the Console's Billing pages) and checks four
  things:
  - `monthlyUsageCycle.startAt` equals the anchor, or is a later start on
    the same day and time;
  - `…_ACCOUNT_LIMIT_USD` equals the lesser of `maxMonthlyUsageUsd` and
    `monthlyUsageCreditsUsd`;
  - the base price and `dataRetentionDays` match their settings;
  - the limit is at or below the prepaid credit (R3).

  The operator then sets `…_ACCOUNT_VERIFIED_ON` and records the check (date,
  values, no identifiers) in STATUS. The same check is required before
  re-enabling after any plan or billing change. The first verification
  happens before the environment holds ledger authority, so it cannot be
  reserved: it is at most three `GET` calls per verification, unledgered,
  within the account margin, and recorded under R36. Every later operator
  account read is reserved as an operator `inspect` envelope (MS2-D-46), and
  each read counts as one of its record reads.
  - Rev-12 Codex follow-up (R12-03; Binding). The kill switch, an open latch,
    an invalid account setting, and `cycle_unknown` all deny the operator
    class too. "Every later operator account read is reserved" therefore
    holds only while admission works, and it is qualified as follows:
    1. *Planned change* (the owner announces a plan or billing change):
       reserve the `inspect` envelope **before** setting
       `HW_RADAR_APIFY_ENABLED=false`. Then disable, make the reads
       outside the application, correct the settings, and settle the
       envelope. Settlement is not admission, so it works while disabled
       (ED-07).
    2. *Recovery verification* (no envelope can be admitted: a 402 latch,
       a `cycle_unknown` or `account_state_unobservable` denial, an
       exhausted operator allowance, or an unannounced billing change). A
       bounded exception, like the bootstrap one, applies:
       - at most three operator-key `GET`s (`/v2/users/me`,
         `/v2/users/me/limits`, and `/v2/users/me/usage/monthly`) per
         triggering event;
       - made outside the application;
       - unledgered, covered by the margin, and recorded in STATUS (date,
         trigger, call count, the values read, no identifiers) and under
         R36;
       - a second recovery verification for the same event needs the
         owner's approval.
    3. Never enable paid work, clear the latch, or loosen a setting just to
       obtain an envelope for inspection. The latch is cleared only by the
       owner's `apify_budget_reset --reason`, after the configuration is
       correct.
    *Rejected:* an operator-class exemption from the kill switch or the
    latch. It would weaken the unchanged blanket stops.
- **Operator reconciliation (procedural; required each billing cycle and
  during F5a — rev-12 Codex round 2, R12-202, aligning with the C-011
  clarification).** Under an `inspect` reservation, the operator reads the
  cycle's monthly usage with the operator key and compares it with
  `apify_spend_report`. If other workloads (account usage minus Hardware
  Radar's settled spend) exceed `E`, or a configured value no longer matches,
  the operator sets `HW_RADAR_APIFY_ENABLED=false` and tells the owner. This
  is the after-the-fact detection that check 1 and
  `external_liability_exceeded` used to provide inside the runtime (R38,
  R39).
  Rev-12 Codex follow-up: the owner confirmed R38 on 2026-09-25 ("Still
  accepted"). This end-of-cycle comparison of the ledger with account usage
  is the standing control. F5a still measures API-call billing on the
  operator side, and a real per-call charge revises the estimator bound
  then, through a plan revision.
- **Client surface (MS2-D-15).** `get_account_limits`, `get_monthly_usage`,
  `get_account_plan`, and their result types are removed from
  `acquisition/apify/client.py`. The operator uses the Apify CLI, `curl`, or
  the Console, never the application client.
- *Rejected (a):* proof-only operator-key account reads inside the
  application. The owner rejected them, and they would put the unscoped key
  in an application runtime.
- *Rejected (b):* keeping admission denied until Apify offers an
  account-read scope. The owner rejected it, because it blocks F5a
  indefinitely.
- *Rejected (c):* inferring the cycle from run or usage responses. None
  carries the billing cycle.
- *Rejected (d):* clamping anchor days 29–31 to the month end. The behavior
  is undocumented (*Month-length edge cases*).
- *Rejected (e):* a configured account usage figure. It would be stale at
  once and would give false confidence.
- *Rejected (f):* a verification expiry, such as re-verifying every cycle.
  The owner's decision says "before enabling". The owner may add one (R39).
- *Reopen if* Apify documents a scoped-token permission for account limits
  and usage (check 1 could return, read-only), Apify documents anniversary
  behavior for days 29–31, the account's plan or sharing changes, or a 402
  latch trip or an operator reconciliation finds other workloads above `E`.

## Requirement traceability

MS-2 acceptance bullets are labeled AC-1..AC-8 in master-spec order (:937-944).
Task IDs refer to the slices below.

| Requirement | Source | Slice/task | Proof (test or evidence) |
| --- | --- | --- | --- |
| MS-2 Task 1 — category dispatch, drive matcher intact | spec :927 | A1, A3 | `tests/db/test_ms2_decision_baseline.py` (syrupy baseline unchanged); `tests/unit/test_categories.py`; frozen `test_ladder.py`/`test_resolver.py`/`test_rung0_regression.py` green |
| MS-2 Task 2 — typed GPU/RAM/CPU specs + provenance | spec :928 | B1–B5 | `tests/db/test_category_specs.py` (CHECK pair, 1:1); `tests/unit/test_{gpu,ram,cpu}_rules.py`; `tests/db/test_refdata_categories.py` |
| MS-2 Task 3 — persisted requirements + evaluator | spec :929 | C1–C4 | `tests/unit/test_eligibility_*.py`; `tests/db/test_watch_evaluation.py` |
| MS-2 Task 4 — provider abstraction + one Apify adapter | spec :930 | A4–A6, D1–D8 | `tests/db/test_collection_provider.py`; `tests/unit/test_apify_contract.py`; `tests/db/test_apify_import.py` |
| MS-2 Task 5 — Apify budget admission/accounting | spec :931 | E1–E6 | `tests/unit/test_apify_budget.py`; `tests/db/test_apify_ledger.py` |
| MS-2 Task 6 — 3–5 pilot sources, measured | spec :932 | F1–F4; F5a for the self-owned-Apify execution (rev 9, OQ28) | `pilot_report` output recorded in STATUS; eBay category sweep tests; F5a live evidence (F5b is not an exit condition) |
| MS-2 Task 7 — self-owned Actor, observation-only, built in this repository (rev 5, MS2-D-38) | spec :934 | D1, F5a (exit proof, rev 9, OQ28); F5b optional (OQ24-gated, not an exit condition) | contract conformance tests (`test_apify_contract.py::test_pydantic_models_equal_committed_contract_schemas`); Actor tests in `actors/hw-radar-synthetic-collector/tests/` (no Django import, no proxy, KV holds only `OUTPUT`); F5a live evidence |
| AC-1 — HDD/SSD, GPU, RAM, CPU rows coexist, spine unchanged | spec :937 | B1, B2 | `test_category_specs.py::test_four_categories_coexist_on_one_spine`; `makemigrations --check` shows no spine change |
| AC-2 — match/no_match/unknown per first-class category | spec :938 | C3 | `tests/db/test_watch_evaluation.py` parametrized 4 categories × 3 verdicts |
| AC-3 — real-observation shortlist without score | spec :939 | C4, F6 (owner-gated) | `test_shortlist.py` (fixtures); F6 live evidence; `grep` proves no scoring import |
| AC-4 — local ↔ Actor switch keeps identity/history | spec :940 | D7 | `test_apify_import.py::test_provider_switch_preserves_identity_history_and_watch_state` |
| AC-5 — truncated Actor run cannot delist | spec :941 | A6, D8 | `test_collection_provider.py::test_truncated_remote_run_*`, `::test_local_after_prolonged_truncated_remote_cannot_mass_delist`; `test_apify_import.py::test_truncated_actor_fixture_cannot_delist` |
| AC-6 — duplicate completion/import idempotent | spec :942 | D6, D10 | `test_apify_import.py::test_duplicate_completion_is_noop`, `::test_crash_between_persist_and_mark_replays_once`, `::test_crash_after_observation_commit_resumes_without_duplicates`, `::test_crash_before_finalization_finalizes_once` |
| AC-7 — attributable cost; admission fails closed | spec :943 | E2–E6, E8, F5a | `test_apify_ledger.py` (attribution by source/provider; denial at limit; kill switch; outstanding counted; `::test_overrun_latch_denies_admission_until_reset`; `::test_reservation_straddling_cycle_boundary_counts_in_both_cycles`); F5a live usage and post-run measurement |
| AC-8 — full gate green | spec :944 | every slice | gate + `makemigrations --check` per commit |
| FR-001 — per-source provider choice, freshness SLO kept | spec :246 | D2 (MS2-D-18), F1 | `test_provider_selection.py` (CHECK, default local) |
| FR-003 — identity ladder, no false cross-category merges | spec :248 | A3, B3 | `test_resolver_categories.py::test_cross_category_alias_goes_to_review`; `::test_non_authoritative_alias_never_auto_accepts_new_category` (MS2-D-21) |
| FR-006 — eligibility reasons mandatory | spec :251 | C3 | every `watch_evaluation` row has non-empty `reasons` (DB test) |
| FR-014 — verdict persisted; unknown never passes | spec :259 | C2, C3, C4 | `test_eligibility_aggregate.py` (unknown ≠ match); listing-tier evidence cannot `match` a product clause; `test_shortlist.py::test_prior_match_then_contradictory_evidence_leaves_shortlist` (MS2-D-20) |
| NFR-003 — secrets via OpenBao | spec :267 | D3 | client reads `HW_RADAR_APIFY_TOKEN` (the scoped runtime token) only; test asserts no token in logs/detail_json; rev 9 (OQ25, MS2-D-43): the unscoped operator key is never rendered to production and CI holds no Apify credential (operational evidence, not a test); rev 12 (R25, MS2-D-48): the operator key is never rendered to any application runtime, proof included, and account state enters only as operator-verified settings; `test_apify_client.py::test_client_has_no_account_read_methods` |
| NFR-006 — new category = satellite + rows | spec :270 | B1, B2 | no change to spine models (migration test + diff) |
| IR-007 — provenance-bearing category references | spec :283 | B4 | seed validation rejects a row without `source_url`/`retrieved_on` |
| IR-008 — Actor provider evidence, idempotent, delist-safe, budgeted | spec :284 | D1–D8, E5 | as AC-4..AC-7 |
| DR-001 — retention CHECK pair on evidence | spec :290 | B1, C1 | constraint tests for `*_spec`, `watch_evaluation` |
| DR-003 — provider IDs never keys | spec :292 | D2 | `Listing` key unchanged; `external_run_id` only on `provider_run` |
| DR-004 — eligibility evidence persisted | spec :293 | C1, C3 | `watch_evaluation.reasons` schema test |
| DR-011 — remote run evidence fields; ambiguous ⇒ no delist; duplicate ⇒ no-op | spec :300 | A4, D1, D2, D6 | `provider_run` field test; mapping-table tests; idempotency tests |
| D-021 / §8.5 — provider ⟂ source; truncated ≠ absence; fail-closed spend | spec :398, :423-425 | A5, D7, E3 | as above |
| D-022 / §8.5 — hard vs soft; unknown never passes | spec :399, :426 | C1, C2 | `test_eligibility_soft_threshold.py` (target never changes verdict) |
| R-MS2-01 — first-class vs basic-watch | ADR 0022 :79 | B2, B3 | basic-watch rules exact-alias-only test |
| R-MS2-02 — servers not merged across configurations | ADR 0022 :115 | B3 | `server` rules: `variant_on_demand=False` test |
| R-MS2-03 — typed satellites, no EAV | ADR 0022 :111 | B1 | migration introspection test (typed columns) |
| R-MS2-04 — satellite fields unspecified (gap) | spec :469 | MS2-D-04 | decision table above |
| R-MS2-05 — spine not reopened | ADR 0010 :117-127 | B1 | no `ProductModel`/`ProductFamily` field change |
| R-MS2-06 — drive matcher is the pattern | spec :396 | A1, B3 | registry shape; new categories reuse `decide()` |
| R-MS2-07 — C.3 drive-specific; generalize around | spec :1216 | A1–A3 | drive rules registered by identity (`is`) |
| R-MS2-08 — resolver failure never blocks ingestion | spec :1216 | A3, C3 | unsupported-category path writes `none` edge; evaluator exception isolated (`test_pipeline_evaluator_failure_isolated`) |
| R-MS2-09 — watch schema gap | spec :110, :475 | MS2-D-07 | decision + C1 migration |
| R-MS2-10 — no universal cross-category score | ADR 0022 :93-95 | C4 | shortlist has no score field; ordering is price, never cross-category |
| R-MS2-11 — hard vs soft (gap) | spec :426, :598 | MS2-D-07 | soft-threshold test |
| R-MS2-12 — shortlist read model (gap) | spec :939, :948 | C4 | `shortlist()` / `review_queue()` tests |
| R-MS2-13 — per-run cost recorded; admission stops at budget | ADR 0021 :148 | E3–E6 | ledger tests |
| R-MS2-14 — ADR-0016 pattern reuse only | ADR 0016 :54-69 | MS2-D-17 | no `SearchBudgetGate` import (grep) |
| R-MS2-15 — Actor-side retention (gap) | — | D11 | `test_apify_storage_cleanup.py` (deadline cleanup for successful/rejected/failed/abandoned runs; retries; a 404 counted only under MS2-D-25's *404 rule*, rev 10: `::test_delete_404_counts_as_deleted_only_when_rule_enabled`) |
| ADR 0021 — imports idempotent; completeness evidence; one scheduler | ADR 0021 :92-108 | D1, D5, D6 | as AC-5/AC-6; no Apify schedule (config review) |
| ADR 0012 amendment — polled completion, idempotent | ADR 0012 :94 | D5, D10 | `test_apify_poll_job.py` (both selectors; restart recovery per state) |
| ADR 0017 — recovery probes per selected provider | ADR 0017 | D12 | `test_apify_recovery_probe.py::test_local_success_cannot_clear_actor_provider_failure`, `::test_budget_admitted_actor_probe_recovers_source` |
| DR-001/DR-008 — bounded retention on remote imports | spec :290 | D11 | `test_apify_retention.py::test_bounded_source_import_retains_listings_snapshots_raw_and_evaluations` |
| ADR 0021 — complete-empty distinguishable from failure | ADR 0021 :100 | D1, D8 | `test_apify_import.py::test_complete_empty_run_delists_scope_end_to_end`, `::test_ambiguous_empty_run_cannot_delist` |
| MS-2 Task 6 prerequisite — non-drive corpus replay | spec :932 | B6 | `test_corpus_category_hint.py::test_harvested_non_drive_entry_reaches_category_rules` |
| FR-014 — catalog correction cannot leave a stale pass (MS2-D-29) | spec :259 | C1, C3, C4 | `test_evaluation_binding.py::test_same_target_catalog_correction_excludes_match_from_shortlist_immediately`, `::test_family_membership_change_makes_family_grain_evaluation_pending` |
| IR-008 / CR-004 — delayed imports cannot overwrite, revive, or delist (MS2-D-30) | spec :284 | D2, D10 | `test_apify_import_ordering.py::test_reverse_completion_older_import_does_not_overwrite_newer_listing_state`, `::test_delayed_remote_import_after_switch_back_to_local_preserves_newer_content_and_delist_state` |
| IR-008 / DR-008 — delayed imports cannot restore retired content or create keys a newer complete sweep omitted (MS2-D-35) | spec :284, :290 | D2, D10 | `test_apify_import_ordering.py::test_delayed_observation_after_newer_delist_restores_no_content_and_keeps_expiry`, `::test_delayed_import_of_unknown_key_after_newer_complete_sweep_creates_no_active_listing`, `::test_delayed_observation_cannot_relist_row_delisted_before_newer_complete_sweep`, `::test_stage1_creation_and_newer_complete_sweep_serialize_on_scope_row` |
| ADR 0021 — stale absence needs the relevant scope's continuity (MS2-D-11, -31) | ADR 0021 :92-108 | A4–A6, D2, D10 | `test_collection_provider.py::test_remote_complete_evidence_incomplete_scope_breaks_continuity_then_local_cannot_stale_delist`; `test_scope_continuity.py::test_scope_a_continuity_does_not_authorize_first_incomplete_scope_b_sweep` |
| ADR 0021 — an older run cannot restore continuity a newer break ended (MS2-D-36) | ADR 0021 :92-108 | D2, D10 | `test_scope_continuity.py::test_delayed_null_scope_run_after_newer_scoped_run_cannot_restore_continuity`, `::test_eligible_sweep_older_than_break_is_noop` [NULL, non-null]; frozen `test_source_ebay.py` continuity tests green |
| DR-001 / DR-008 — snapshot retention follows the observation's own time (MS2-D-37) | spec :290 | D10 | `test_persist_observation_retention.py::test_reverse_order_bounded_observation_snapshot_keeps_its_own_deadline`, `::test_older_bounded_observation_after_newer_delist_is_not_snapshotted`, `::test_append_snapshot_default_copies_listing_retention` |
| AC-7 — reservation bounds cumulative work (MS2-D-32) | spec :943 | D10, E1–E4 | `test_apify_ledger.py::test_repeated_pre_commit_reads_are_capped_and_reserved`, `::test_non_null_usage_before_cleanup_completes_does_not_release_liability`, `::test_delayed_deletion_keeps_storage_liability_outstanding` |
| AC-7 — settled spend counts in every billing cycle its charge interval touches (MS2-D-34 as revised in rev 5) | spec :943 | E1, E3, E4 | `test_apify_ledger.py::test_late_cleanup_settled_spend_counts_in_the_cycle_of_its_final_charge`, `::test_reconciled_spend_leaves_a_cycle_its_charge_interval_does_not_touch` |
| C-011 / AC-7 — billing cycle is the period; cash ceiling = project allocation + account prepaid headroom (MS2-D-40, OQ23) | spec :181, :943 | E1–E3 | `test_apify_budget.py::test_cycle_bounds_come_from_account_limits_not_calendar`, `::test_account_headroom_below_project_target_binds`, `::test_project_target_below_account_headroom_binds`, `::test_admission_never_relies_on_overage`, `::test_plan_base_price_above_cash_ceiling_denies_all`; `test_apify_ledger.py::test_arbitrary_non_calendar_cycle_boundary`; rev 12 (R25): the cycle-bounds and headroom tests are rewritten on the configured anchor and limit (E9.1, E9.2) |
| C-011 / AC-7 / NFR-003 — the runtime reads no account state; configured cycle and operator-verified account settings; 402 hard-limit latch (MS2-D-48, rev 12, R25) | spec :181, :267, :943 | E9.1–E9.5 | `test_apify_no_account_reads.py::test_runtime_paths_never_request_account_endpoints`, `::test_no_runtime_module_references_account_endpoints`; `test_apify_budget.py::test_admission_admits_with_configured_cycle_and_limit`, `::test_unset_or_invalid_anchor_denies_cycle_unknown`, `::test_unset_or_invalid_account_setting_denies_account_state_unobservable`, `::test_billing_cycle_bounds_from_anchor`, `::test_billing_cycle_bounds_are_computed_from_the_anchor_not_iterated`; `test_apify_ledger.py::test_anchor_moved_backward_into_recorded_cycle_denies_cycle_unknown`; `test_apify_ledger_authority.py::test_handoff_record_is_cycle_scoped_under_configured_anchor`; `test_apify_admission.py::test_start_refused_with_402_trips_account_limit_latch_and_keeps_reservation`; rev-12 Codex follow-up: `test_apify_crash_windows.py::test_402_trip_lost_between_commits_is_repaired_before_next_admission`, `test_apify_ledger.py::test_correction_with_invalid_account_setting_is_an_invariant_breach`, `test_apify_spend_report.py::test_report_warns_when_account_verification_predates_current_cycle` (E9.3, E9.2, E9.6) |
| AC-7 — provisional → finalized usage; reconcile and release (MS2-D-41) | spec :943 | E4 | `test_apify_ledger.py::test_first_usage_read_is_provisional_until_settle_delay`, `::test_finalized_usage_written_once_not_recomputed`, `::test_reconcile_below_reservation_returns_capacity`, `::test_reconcile_above_reservation_trips_overrun_and_pauses_paid_work`; revision 6 (R5-03): `::test_nonnull_usage_rising_after_ten_seconds_is_not_finalized_early`, `::test_permanently_unfinalized_run_stays_at_bound_and_is_stale_at_cycle_end`, `::test_upward_correction_after_reconciliation_raises_settled_and_trips_latch` |
| AC-7 / C-011 — reconciled spend stays debited; shared-account external liability owner-bounded (MS2-D-40, rev 6 R5-01/R5-02) | spec :943 | E2, E3 | `test_apify_ledger.py::test_repeated_reserve_reconcile_against_one_unchanged_snapshot_keeps_debit`, `::test_refreshed_snapshot_that_still_lags_keeps_reconciled_debit`; `test_apify_budget.py::test_empty_or_invalid_external_liability_denies_all_paid_admission`, `::test_default_external_liability_admits_only_when_invariant_holds` (rev 9, OQ26), `::test_concurrent_external_consumption_within_declared_bound_cannot_push_account_past_prepaid`; rev 12 (R25): the snapshot-check tests are retired with check 1, and reconciled spend stays debited in check 2: `test_apify_ledger.py::test_reconciled_spend_stays_debited_against_configured_limit` (E9.2) |
| AC-7 — one HR ledger authority per cycle; drained handoff (MS2-D-45, rev 6 R5-04) | spec :943 | E1, E3, F5a | `test_apify_ledger_authority.py::test_handoff_export_refused_while_proof_run_is_running`, `::test_handoff_export_refused_while_usage_is_provisional_or_unfinalized`, `::test_second_environment_denied_until_handoff_imported` |
| AC-7 — operator operations reserved in the ledger (MS2-D-46, rev 6 R5-05) | spec :943 | E2, E4, F5a | `test_apify_budget.py::test_operator_allowance_exhausted_refuses_reservation`; `test_apify_ledger.py::test_unsettled_operator_reservation_counts_at_bound` |
| AC-7 — corrections monitored after reconciliation and re-checked against every invariant; exclusive handoff (MS2-D-41, -45, -47, rev 7) | spec :943 | E3, E4 | `test_apify_ledger.py::test_poll_tick_selects_reconciled_cleaned_up_run_for_correction_monitoring`, `::test_below_estimate_upward_correction_after_capacity_reuse_trips_latch`; `test_apify_ledger_authority.py::test_duplicate_export_imported_into_two_ledgers_second_rejected`, `::test_admission_racing_handoff_export_serializes` |
| AC-7 — monitoring closes only on a committed closing read; handoff needs closing-read evidence; operator builds are bound, settled, and monitored (MS2-D-23, -41, -45, -46, -34, rev 8) | spec :943 | D3, E1, E3, E4 | `test_apify_ledger.py::test_outage_spanning_correction_deadline_closing_read_applies_correction`, `::test_failed_closing_read_keeps_obligation_open_and_overdue`, `::test_restart_rereads_reconciled_build_and_applies_upward_correction`; `test_apify_ledger_authority.py::test_handoff_export_refused_after_deadline_until_closing_read_commits` |
| IR-008 / DR-011 — three clocks (MS2-D-39) | spec :285, :301 | D10, E3 | ordering tests in `test_apify_import_ordering.py` (observation clock); `test_apify_storage_cleanup.py::test_deadline_is_anchored_at_admission_not_terminal_observation` (processing clock); `test_apify_ledger.py::test_cycle_attribution_uses_charge_interval_not_observation_time` (billing clock) |
| DR-001/DR-008 — absolute remote retention deadline (MS2-D-33) | spec :290 | D5, D11 | `test_apify_storage_cleanup.py::test_restart_after_source_ttl_rejects_expired_content_before_persistence`, `::test_unobserved_run_past_deadline_is_aborted_and_cleaned` |
| IR-008 / AC-5 / AC-7 — Slice D entry-gate amendments (rev 10, ED-01..ED-20) | spec :284, :941, :943 | D2, D3 follow-up, D10, D11, E2, E4 | `test_persist_observation_ordering.py::test_current_eligible_observation_bumps_last_seen_and_ineligible_does_not`, `::test_listing_observed_in_previous_run_is_not_stale_delisted_within_grace`; `test_apify_import_ordering.py::test_concurrent_scope_move_is_not_delisted_by_other_scope_sweep`, `::test_overlapping_scopes_sharing_listings_do_not_deadlock_or_consume_read_cap`; `test_pipeline_transactions.py::test_local_crash_mid_persist_rolls_back_whole_batch_and_next_poll_repairs`; `test_apify_budget.py::test_api_calls_priced_at_bound_without_billing_verification`, `::test_storage_lifetime_below_data_retention_days_denies`; `test_apify_ledger.py::test_usage_component_outside_allowlist_trips_latch`, `::test_settlement_ignores_reads_before_final_charge_op_plus_guard`; `test_apify_poll_job.py::test_kill_switch_off_still_drains_imports_cleanup_and_closing_reads` |

### Synthetic Actor proof acceptance (owner §21, revision 5)

The owner's 18 acceptance items for the first Actor proof (MS2-D-42). "Fixture"
proofs run in the gate; "live" proofs are F5a evidence recorded in STATUS.

Revision 9 (owner decision (s4, 2026-09-25),
[OQ28](../../resolved-questions.md#oq28--can-ms-2-exit-on-the-synthetic-proof-alone)): these items, evidenced by F5a, are
the complete Apify portion of MS-2 exit. No production Actor-backed merchant
source (F5b) is required to close MS-2.

| # | Owner acceptance item | Task | Proof |
| --- | --- | --- | --- |
| 1 | Provider abstraction starts the correct Actor | D5, F5a | `test_apify_poll_job.py::test_start_uses_configured_actor_and_build_tag`; live: `provider_run.actor_ref`/`build_number` match settings |
| 2 | Input-schema validation rejects bad requests before paid execution where possible | D1, D3, F5a | `test_apify_contract.py::test_invalid_input_rejected_before_start_request` (no HTTP call recorded); live: Apify's own rejection of an invalid input observed and recorded (MS2-D-15) |
| 3 | Run bounded by timeout, memory, item, and request limits | D1, D3, E2 | `test_start_sends_memory_and_timeout_run_options`; Actor `tests/test_limits.py::test_item_page_request_byte_caps_enforced`; `test_apify_budget.py` component formula |
| 4 | No residential proxy | D1, E4 | Actor `tests/test_boundaries.py::test_no_proxy_configuration_or_django_import`; `test_apify_ledger.py::test_proxy_usage_trips_latch`; live: zero proxy component in `usageUsd` |
| 5 | Versioned output contract | D1 | `test_apify_contract.py::test_pydantic_models_equal_committed_contract_schemas`, `::test_unknown_schema_version_is_failed` |
| 6 | Source/provider scope metadata in output | D1, D4 | `test_apify_contract.py::test_scope_mismatch_is_failed`; `test_apify_import.py::test_imported_rows_carry_scope_and_category_hint` |
| 7 | Complete vs intentionally truncated distinguishable | D1, D8, F5a | `test_apify_contract.py` mapping table incl. `::test_truncation_reason_distinguishes_items_pages_time_bytes`; live: `faultMode=truncate_items` run classified `truncated`/`item_limit` |
| 8 | Contradictory completion metadata fails closed | D1, D8 | `test_apify_contract.py::test_contradictory_report_is_failed` [complete+limit, count mismatch, status mismatch]; `test_apify_import.py::test_contradictory_report_rejected_and_breaks_continuity` |
| 9 | Importing the same output twice has no duplicate canonical effects | D6 | `test_duplicate_completion_is_noop`, `test_replayed_dataset_read_does_not_duplicate_snapshots`, `test_duplicated_dataset_import_is_noop` |
| 10 | Late import cannot overwrite newer listing state | D10 | `test_reverse_completion_older_import_does_not_overwrite_newer_listing_state` |
| 11 | Truncated import cannot delist | A6, D8 | `test_truncated_actor_fixture_cannot_delist` [item, page, time, bytes] |
| 12 | Provider switch preserves listing identity | D7 | `test_provider_switch_preserves_identity_history_and_watch_state` |
| 13 | Run usage/cost evidence captured | D3, E4, F5a | `test_apify_ledger.py::test_finalized_usage_written_once_not_recomputed`; live: `usage_total_usd`, `usageUsd`, `stats` on `provider_run` |
| 14 | Post-run retrieval cost/usage measured | E4, F5a | `test_apify_ledger.py::test_post_run_cost_uses_bound_until_measured`; live: account usage diff around the dataset read (MS2-D-43); rev 12 (R25): the account usage diff is an operator-key read outside the application under an `inspect` reservation (MS2-D-48, F5a step 4) |
| 15 | Reservation and reconciliation correct | E3, E4 | `test_reconcile_below_reservation_returns_capacity`, `test_reconcile_above_reservation_trips_overrun_and_pauses_paid_work`, `test_reservation_exactly_equal_to_remaining_is_admitted` |
| 16 | Budget exhaustion prevents a new paid run | E2, E5 | `test_one_cent_over_remaining_is_denied`; `test_denied_start_records_denial_and_starts_nothing` |
| 17 | `budget_paused` visible as freshness/operational state | E5 | `test_denied_start_shows_budget_paused_with_reason_in_shortlist` |
| 18 | Full gate green | every slice | gate (incl. the Actor project's commands, MS2-D-38) + `makemigrations --check` |

## Slice order and migration assignment

| Slice | PR scope | Migrations | Depends on | Why this position |
| --- | --- | --- | --- | --- |
| **A** | Seams: category registry + hint; provider seam + run evidence + completeness gate | none | — | Every later slice plugs into these seams. Behavior-preserving, so it is safe to land first. |
| **B** | GPU/RAM/CPU satellites, category rows, rules, reference seeds, cross-category guard | `0018_category_spec_satellites`, `0019_seed_categories` | A | C's product clauses read the satellites. |
| **C** | Watch + requirement satellites + evaluator + `watch_evaluation` (with `catalog_fingerprint`) + shortlist read model | `0020_watch_requirements` | B | D's provider-switch test must prove watch state survives (AC-4). |
| **D-prep** | D1 (the `actors/hw-radar-synthetic-collector/` Actor project with its committed contract schemas, fault-injection fixtures, and own tests; hw-radar's conformance-tested contract models, `classify_run` with truncation reasons, fixtures; the Actor gate commands in `scripts/check.py`) and D3 (httpx client, ten or eleven calls; revision 8 adds get build). Pure code with no DB schema, no pipeline wiring, and no Apify deployment (revision 5, MS2-D-38). | none | A | Has no dependency on B or C, so it may be developed and merged any time after A. |
| **D** (core) | D2, D4–D12: `provider_run`, staged import, scoped absence, per-scope ordered continuity (`scope_sweep_continuity`, NULL-scope lane watermarks), ordering and absence watermarks (`Listing.last_observed_at`, `Listing.last_absence_at`, per-scope `last_complete_sweep_at`), per-observation snapshot retention, provider selection, poll selectors, probes, retention, cleanup. Production admission = deny-all. Starts only after the *Slice D entry gate* | `0021_provider_runs` | C, D-prep, entry gate | D7 needs `WatchEvaluation` (C), and stage 4 needs the evaluator. Live runs are impossible until E replaces deny-all. D is fail-closed by construction. |
| **E** | Spend ledger, billing-cycle table, account-headroom admission, provisional/finalized usage, reconcile, `budget_paused`, spend report (revision 5, MS2-D-40, -41) | `0022_apify_spend_ledger` | D | Runtime reservations attach to `provider_run`; operator rows have none, and build rows carry `provider_build_id` (revision 8). |
| **F** | Pilot sources (eBay category sweeps, SPD check), measurement, synthetic Actor proof (F5a), OQ24-gated merchant Actor source (F5b, not an MS-2 exit condition, rev 9 OQ28), end-to-end evidence | none planned | B–E | Integration and measurement last (ADR 0022 "measure before breadth"). |

**Merge order = dependency graph:** A → B → C → D (core) → E → F. D-prep merges
at any point after A and before D (core). Revision 1's "if D merges first,
renumber" alternative is withdrawn: D (core) needs C's code and schema, and
renumbering migrations cannot satisfy that dependency. For parallel work, only
D-prep runs alongside B/C. Every slice leaves `dev` deployable with all sources
still disabled.

The migration names in this table are planning allocations (revision 5): each
slice re-verifies the live graph and records drift before numbering (*Global
constraints*). Slice B verified `0018`/`0019` with no drift.

## Slice A — Seams (behavior-preserving, no migration)

**Scope:** MS2-D-02, -03, -10, -11. No schema change, no drive decision change, no
adapter edit.

**Files created:**
- `src/hw_radar/matching/categories.py`
- `src/hw_radar/acquisition/providers.py`
- `tests/unit/test_categories.py`
- `tests/unit/test_ladder_veto.py`
- `tests/unit/test_contracts_ms2.py`
- `tests/unit/test_provider_evidence.py`
- `tests/db/test_ms2_decision_baseline.py` (+ its `__snapshots__/`)
- `tests/db/test_resolver_dispatch.py`
- `tests/db/test_persist_category_hint.py`
- `tests/db/test_collection_provider.py`

**Files changed:**
- `src/hw_radar/matching/ladder.py`
- `src/hw_radar/matching/resolver.py`
- `src/hw_radar/acquisition/contracts.py`
- `src/hw_radar/acquisition/persist.py`
- `src/hw_radar/acquisition/pipeline.py`
- `src/hw_radar/catalog/models/ops.py`
- `src/hw_radar/catalog/models/__init__.py` (exports only)

**Frozen for this slice:** every existing file under `tests/`, and
`src/hw_radar/acquisition/sources/*`, `heartbeat.py`, `poller/service.py`,
`matching/{vocab,mpn,types}.py`, and `matching/grammars/*`.

Focused test command pattern (DB tests need the dev DB; one pytest process at a
time):
`HW_RADAR_DB_PORT=5433 uv run pytest <test path> -q`.

### A0 — Characterization baseline (on unmodified code)

1. Create `tests/db/test_ms2_decision_baseline.py` with
   `test_synthetic_corpus_decisions_baseline(snapshot)` (syrupy, already a dev
   dependency):
   - Load `tests/fixtures/matching_corpus/synthetic.jsonl` + `.meta.json` via
     `load_corpus` / `load_meta`.
   - Seed the catalog with the `seeded_catalog` fixture from
     `tests/db/test_ratification_corpus.py:152-189`. Import the fixture, or copy
     it and its `_manufacturer`/`_seed_model` helpers verbatim into the new file.
     Do not edit the original.
   - Inside a rolled-back atomic block, call `evaluate_corpus(entries, meta)`.
   - Assert `snapshot == [(p.entry_id, p.outcome, p.grain, p.rung,
     p.manufacturer_key, p.family_norm, p.model_norm, p.variant_tuple) for p in
     predictions]` (`Prediction`, `src/hw_radar/matching/eval/evaluate.py:61-78`).
2. Add `test_ladder_golden_baseline(snapshot)`. It runs every
   `ladder.decide(...)` input tuple used in `tests/unit/test_ladder.py`
   (re-declared locally) and snapshots the `Verdict` fields `outcome`, `grain`,
   `rung`, `method`, `confidence`, the target fields `(grain, family_id,
   model_id, variant_id, family_key)`, and the full sorted evidence *items*
   (keys and values). As landed, this is stronger than sorted keys, and it
   deliberately excludes `TargetRef.category_slug`, which A3 adds.
3. Run `HW_RADAR_DB_PORT=5433 uv run pytest tests/db/test_ms2_decision_baseline.py --snapshot-update -q`
   **on the unmodified base**, then run it again without `--snapshot-update`. It
   should be green.
4. Commit: `test(ms2): pin drive decision baseline before category dispatch`.

This is the before/after oracle for the whole slice. No later Slice A commit may
run `--snapshot-update`.

### A1 — Category registry + ladder veto injection

1. **RED.** `tests/unit/test_ladder_veto.py`:
   - `test_custom_veto_is_consulted_at_rung1`: a rung-1 viable single-target hit
     that would ACCEPT under the default. With `veto=lambda e, h: ["probe"]` it
     returns `Outcome.REVIEW`, `rung=1`, and `evidence["veto"] == ["probe"]`.
   - `test_custom_veto_is_consulted_at_rung0_prior` and
     `test_custom_veto_filters_oem_fanout`: the same probe at the other two
     sites.
   - `test_default_veto_is_contradictions`:
     `inspect.signature(ladder.decide).parameters["veto"].default is ladder.contradictions`.

   Fails: `decide()` has no `veto` parameter (TypeError).
2. **GREEN.** In `ladder.py`:
   - Add `type Veto = Callable[[ExtractedAttributes, HardAttrs], list[str]]`.
   - Add `*, veto: Veto = contradictions` to `decide`.
   - Replace the three `contradictions(` calls inside `decide` with `veto(`. Leave
     the rung-2 capacity check as is.
   - Rename the two local result variables that were named `veto` (rung 0 at
     `:161`, rung 1 at `:196`) to `vetoed`. Otherwise the recipe yields
     `veto = veto(...)`, which rebinds the annotated callable parameter to a list
     and fails strict basedpyright. The evidence key stays `"veto"` and its
     values are byte-identical. The OEM fan-out filter (`:225`) calls
     `veto(...)` inline.
3. **RED.** `tests/unit/test_categories.py`:
   - `test_drive_rules_bind_existing_functions_by_identity`: `rules_for("drive")`
     has `extract is vocab.extract`,
     `extract_candidates is mpn.extract_candidates`, `decode is grammars.decode`,
     and `veto is ladder.contradictions`.
   - `test_registered_categories_is_drive_only_in_slice_a`.
   - `test_dispatch_category_none_is_legacy_drive`.
   - `test_dispatch_category_passes_hint_through`.
   - `test_rules_for_unregistered_is_none`.
   - `test_invalid_slug_rejected`: `dispatch_category("GPU!")` raises
     `ValueError`.

   Fails: module missing.
4. **GREEN.** Create `src/hw_radar/matching/categories.py` (pure, no ORM). The
   **binding** surface:

   ```python
   DRIVE: Final = "drive"
   LEGACY_DEFAULT_CATEGORY: Final = DRIVE
   CATEGORY_SLUG_MAX_LENGTH: Final = 50
   CATEGORY_SLUG_RE: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

   class CandidateExtractor(Protocol):
       def __call__(self, title: str, *, structured_mpn: str | None = None,
                    source_key: str = "") -> list[MpnCandidate]: ...

   @dataclass(frozen=True)
   class CategoryRules:
       slug: str
       extract: Callable[[str], ExtractedAttributes]
       extract_candidates: CandidateExtractor
       decode: Callable[[str], DecodeResult | None]
       veto: ladder.Veto

   def rules_for(slug: str) -> CategoryRules | None: ...
   def registered_categories() -> frozenset[str]: ...
   def dispatch_category(hint: str | None) -> str: ...  # None -> DRIVE; validates slug
   ```

   `rules_for` builds the `CategoryRules` on each call from the current module
   attributes (MS2-D-02); it does not bind them at import.
   Module docstring: record the legacy-default trap (MS2-D-03) and that the
   ORM-bound spec readers live in `resolver._SPEC_READERS`.
5. Run `uv run pytest tests/unit/test_categories.py tests/unit/test_ladder_veto.py tests/unit/test_ladder.py -q`
   and `uv run basedpyright src/hw_radar/matching/ladder.py src/hw_radar/matching/categories.py`
   (0 errors), then the full gate. Commit:
   `feat(matching): add category rules registry and injectable ladder veto`.

### A2 — Category hint on the ingestion contract

1. **RED.** `tests/unit/test_contracts_ms2.py`:
   - `test_category_hint_defaults_to_none`.
   - `test_category_hint_accepts_slug` (`"gpu"`, `"basic-watch"`).
   - `test_category_hint_rejects_non_slug` (`"GPU"`, `"gpu card"`, and a
     51-character string).
   - `test_attrs_may_not_carry_reserved_category_hint_key`: `ParsedListing(...,
     attrs={"category_hint": "gpu"})` raises `ValidationError`.
   - `test_normalized_listing_inherits_hint`.

   Fails: unknown field / no validation.
2. **GREEN.** In `contracts.py`:
   - Add `CATEGORY_HINT_ATTR: Final = "category_hint"`.
   - Add `ParsedListing.category_hint: str | None = Field(default=None, max_length=CATEGORY_SLUG_MAX_LENGTH, pattern=CATEGORY_SLUG_RE.pattern)`.
     Import the pattern constant from `hw_radar.matching.categories`. That
     module is pure Python and imports no Django or acquisition module, so no
     import cycle arises.
   - Add a `model_validator(mode="after")` that rejects `CATEGORY_HINT_ATTR` in
     `attrs`.
3. **RED.** `tests/db/test_persist_category_hint.py`:
   - `test_snapshot_attrs_unchanged_without_hint`: `append_snapshot` with no hint
     writes `attrs_json == dict(record.attrs)` exactly.
   - `test_snapshot_attrs_carry_hint_when_set`: with `category_hint="gpu"`, it
     writes `{**attrs, "category_hint": "gpu"}`.
4. **GREEN.** In `persist.append_snapshot`, merge the hint under
   `CATEGORY_HINT_ATTR` only when non-null.
5. Run the focused tests plus the frozen `tests/db/test_persist.py`,
   `tests/db/test_ratification_corpus.py`, and `tests/unit/test_contracts.py`,
   then the gate. Commit:
   `feat(acquisition): add optional category hint to the listing contract`.

### A3 — Resolver category dispatch

1. **RED.** `tests/db/test_resolver_dispatch.py`. Define local fixtures mirroring
   `tests/db/test_resolver.py:33-121`; do not import from or edit that file.
   Create snapshots with `persist.append_snapshot` using a `NormalizedListing`
   (`fx_rate=1`, `fx_pair="USD/USD"`, `fx_source="identity"`).
   - `test_no_hint_resolves_as_drive_and_records_provenance`: the ST16000NM001G
     title with no snapshot yields the same grain/method/rung as the frozen
     `test_rung1_parity_*` case, plus `evidence["category"] == "drive"` and
     `evidence["category_source"] == "legacy_default"`.
   - `test_explicit_drive_hint_is_decision_identical`: the same listing with
     `category_hint="drive"` produces identical
     `(outcome, grain, rung, method, targets)`, with `category_source == "hint"`.
   - `test_unregistered_hint_writes_none_edge_and_never_runs_drive_rules`:
     `category_hint="gpu"` on a drive-shaped title that would rung-1 accept
     yields a current edge `grain=none` with
     `evidence["unsupported_category"] is True` and `evidence["category"] == "gpu"`.
     `Listing.product_model` stays None. The edge evidence has no
     `mpn_hypothesis` key, which `ladder.decide()` always writes
     (`ladder.py:154`). That proves the drive ladder never ran, without relying
     on a monkeypatch. `rules_for` resolves module functions per call, so a
     monkeypatch would be honored. The frozen `test_resolver.py` error-edge
     tests (≈:242–314) depend on that.
   - Invalid-hint case (as landed): a non-string or non-slug hint in
     `attrs_json` yields the error edge, never a drive fallback.
   - `test_rung2_provisional_family_uses_dispatch_category`: the
     `test_rung2_decode_*` title makes a family whose `category.slug == "drive"`.
   - `test_spec_readers_match_registry`:
     `set(resolver._SPEC_READERS) == categories.registered_categories()`.
   - `test_unsupported_repoll_does_not_spam_edges`: resolve twice yields one edge.

   Fails: no dispatch/evidence keys, and "gpu" currently resolves as drive.
2. **GREEN.** In `resolver.py`:
   - Add a frozen `_SpecReader(model_attrs, family_attrs)` and `_SPEC_READERS`
     with `{DRIVE: _SpecReader(lambda m: _hard_attrs_from_spec(_spec_of(m)), _family_agreement_attrs)}`.
   - Add `_category_hint(listing) -> str | None` from the latest snapshot's
     `attrs_json`. It may share one query with `_structured_mpn`; the returned
     values must be identical.
   - Thread the reader through `_prior_from_listing(listing, spec)` and
     `_alias_hits(candidates, source_site_id, spec)`, and the decoder through
     `_first_decode(candidates, decode)`.
   - `_run_ladder` resolves `slug = categories.dispatch_category(hint)`. For an
     unregistered slug it returns the canonical title,
     `ExtractedAttributes()`, `[]`, and a `Verdict(Outcome.NONE, Grain.NONE,
     evidence={"category": slug, "category_source": ..., "unsupported_category": True})`.
     Otherwise it calls `rules.extract` / `rules.extract_candidates` /
     `rules.decode` and passes `veto=rules.veto` to `ladder.decide`, then adds
     the two provenance keys to `verdict.evidence`.
   - Add `category_slug: str | None = None` to `ladder.TargetRef`. `_run_ladder`
     stamps it via `dataclasses.replace` when `target.family_key` is set.
   - `_materialize` uses `Category.objects.get(slug=target.category_slug)` and
     raises `ValueError` if `family_key` is set without a slug. That routes to the
     existing error-edge fallback, never a silent drive default.
   - `_materialize`'s signature is unchanged; the frozen monkeypatch test at
     `test_resolver.py:360` depends on it.
   - Do not bump `MATCHER_VERSION`: no rule changed.
3. Run the focused tests, then the frozen `tests/unit/test_ladder.py`,
   `tests/db/test_resolver.py`, `tests/db/test_rung0_regression.py`,
   `tests/db/test_ratification_corpus.py`, and `tests/db/test_ms2_decision_baseline.py`
   (baseline must be unchanged). Then run the gate. Commit:
   `feat(matching): dispatch resolution by category with drive as legacy default`.

### A4 — Provider run-evidence contract + completeness gate (pure)

1. **RED.** `tests/unit/test_provider_evidence.py`:
   - `test_completeness_values_are_the_adr0021_taxonomy`:
     `set(RunCompleteness.values) == {"complete","truncated","partial_failure","failed"}`.
   - `test_evidence_is_frozen_and_forbids_extra`.
   - `test_empty_reason_rejected`.
   - `test_remote_provider_cannot_be_stale_absence_eligible`: `APIFY` +
     `stale_absence_eligible=True` raises `ValidationError`.
   - A parametrized `test_gate_delist_scope` over 4 completeness values × 2
     eligibility values × `scope.complete ∈ {True, False}`. Expected:
     - `complete` → the input scope, unchanged;
     - `truncated` + eligible → a scope with `complete is False` and the other
       fields equal;
     - `truncated` + ineligible → `None`;
     - `partial_failure` and `failed` → `None`.
     - `complete` + `scope.complete is False` + ineligible (remote) → `None`
       (`test_gate_remote_complete_evidence_with_incomplete_scope_is_none`,
       added during Slice A verification). With eligible local evidence, the
       scope passes unchanged.
   - `test_gate_passes_none_scope_through`.
   - `test_continuity_counts_only_complete_or_eligible_truncated`, called with
     the two-argument signature from revision 3.
   - Revision 3 (F-04 residual) adds:
     - `test_remote_complete_evidence_with_incomplete_or_missing_scope_does_not_count`:
       APIFY `complete` evidence with `scope.complete=False`, and with
       `scope=None`, both return False. With `scope.complete=True` it returns
       True.
     - `test_local_continuity_mapping_unchanged`: for every local evidence the
       local mapping can produce, the result equals the revision-2 rule, for
       every scope value (None, complete, incomplete).

   Fails: names missing.
2. **GREEN:**
   - In `catalog/models/ops.py`, add `ProviderKind` and `RunCompleteness`
     `TextChoices` and export them.
   - In `contracts.py`, add `ProviderRunEvidence` (MS2-D-11 fields,
     `ConfigDict(frozen=True, extra="forbid")`, the validator above).
   - In the new `acquisition/providers.py`, add `gate_delist_scope(scope:
     DelistScope | None, evidence: ProviderRunEvidence) -> DelistScope | None`
     and `counts_toward_sweep_continuity(evidence: ProviderRunEvidence, scope:
     DelistScope | None) -> bool` (MS2-D-11 as amended in revision 3). Each
     docstring states the ADR-0021/D5 invariant ("only `complete` may keep
     `complete=True`; remote runs never reach stale absence"). The continuity
     docstring adds that a remote run counts only when both the evidence and
     the scope say complete.
3. Run `HW_RADAR_DB_PORT=5433 uv run python manage.py makemigrations --check --dry-run`.
   It must report no changes, because the choices are unused by any field.
   Then run the gate. Commit:
   `feat(acquisition): add provider run-evidence contract and completeness gate`.

### A5 — CollectionProvider seam + LocalCollectionProvider

1. **RED.** Add to `tests/unit/test_provider_evidence.py`, using in-memory fake
   adapters:
   - `test_local_provider_delegates_fetch_and_parse`.
   - `test_local_evidence_complete_when_adapter_proves_complete`.
   - `test_local_evidence_truncated_eligible_when_adapter_sweep_incomplete`.
   - `test_local_evidence_truncated_eligible_when_adapter_has_no_delist_detector`
     (reason `completeness_not_asserted`).
   - `test_local_evidence_for_non_full_run_records_absence_not_evaluated`.
   - `test_local_provider_satisfies_protocol`: `provider: CollectionProvider =
     LocalCollectionProvider(adapter)` type-checks under basedpyright.

   Then `tests/db/test_collection_provider.py::test_run_source_records_local_provider_evidence`:
   a FakeAdapter run on the seeded `demo` site stores
   `run.detail_json["provider"]` with `{"evidence_version": 1, "provider_kind":
   "local", "provider_key": "local", "completeness": "truncated",
   "completeness_reason": "completeness_not_asserted", "stale_absence_eligible": true}`.
   The existing keys `body_bytes`, `resolver_errors`, `grain_counts`, and
   `listings_delisted` keep their prior semantics.
2. **GREEN:**
   - In `contracts.py`, add the `CollectionProvider` Protocol (MS2-D-10, with
     `run_evidence(batch, parsed, scope, *, run_kind)`).
   - In `providers.py`, add `LocalCollectionProvider` and the reason constants.
   - In `pipeline.py`:
     - Move the body of `run_source` to `run_collection(provider, resolver, *,
       retention_class, expires_policy, run_kind, fetch_timeout_s)`.
     - `run_source(adapter, resolver, **kwargs)` returns `await
       run_collection(LocalCollectionProvider(adapter), resolver, ...)`, and its
       signature is unchanged.
     - The FULL-run delist stage becomes: `scope = provider.delist_scope(batch,
       parsed)`, then `evidence = provider.run_evidence(...)`.
     - If `counts_toward_sweep_continuity(evidence, scope)` (the ungated
       provider scope), call `_record_sweep_continuity`. Otherwise call the new
       `_break_sweep_continuity(site)`, which sets the FULL lane's
       `continuous_since := None` (MS2-D-11). The helper sits beside
       `_record_sweep_continuity` and is a no-op without a `SourceConfig`.
     - `_apply_delist` runs on `gate_delist_scope(scope, evidence)` when that
       is not None.
     - Non-FULL runs compute evidence with `scope=None`.
     - Write `detail_json["provider"] = evidence.model_dump(mode="json")`.
   - Keep `_classify_batch` on `provider.expects_json`, and keep the
     zero-record parser-rot guard.
   - One documented difference: continuity is now recorded after
     `delist_scope()`. If `delist_scope` raises, the run fails as before and no
     longer advances `continuous_since`. That is strictly more conservative.
     Write it in the delist-stage comment.
3. Run the frozen `tests/db/test_pipeline.py`, `test_pipeline_demo.py`,
   `test_source_{ebay,goharddrive,seagate,serverpartdeals,wd}.py`,
   `test_poller_jobs.py`, `test_poller_heartbeat.py`,
   `test_poller_retention_wiring.py`, `test_ms1_acceptance.py`, and
   `tests/unit/test_poller*.py`, one process at a time. Then run the gate.
   Commit: `refactor(acquisition): route collection through a provider seam`.

### A6 — Truncated or failed provider results cannot delist (DB proof)

1. **RED.** In `tests/db/test_collection_provider.py`, use a
   `FakeRemoteProvider(provider_kind=APIFY, site_key="demo")` whose
   `delist_scope` **claims** `complete=True` and whose `run_evidence` returns a
   configurable completeness. Seed an active listing `k-old` on `demo` with
   `last_seen` well past any grace (queryset `.update`), and the lane with an old
   `continuous_since`. The batch parses only `k-new`.
   - `test_truncated_remote_run_claiming_complete_scope_cannot_delist`: `k-old`
     is still active and `detail_json["listings_delisted"] == 0`.
   - `test_partial_failure_and_failed_remote_runs_cannot_delist`: parametrized,
     same assertions.
   - `test_truncated_remote_run_does_not_advance_continuity`: with
     `continuous_since=None` before the run, it is still `None` after.
   - `test_ineligible_run_breaks_existing_continuity`: with an old non-null
     `continuous_since`, a successful truncated remote FULL run leaves it
     `None`. The test is parametrized over `truncated`, `partial_failure`, and
     `failed` evidence, which all reach the success path in this fake.
   - `test_local_after_prolonged_truncated_remote_cannot_mass_delist`: seed an
     old non-null `continuous_since`, a prior successful local FULL run, and
     several active listings whose `last_seen` is well past the grace. Then run
     several truncated remote runs at intervals within the continuity
     tolerance. Then run a local fake whose `DelistDetector` returns an
     incomplete scope with a short grace. Expected: zero delists, and
     `continuous_since` restarts at the local run's `observed_at`.
   - `test_complete_remote_evidence_with_incomplete_scope_cannot_stale_delist`
     (added during Slice A verification): remote `complete` evidence with a
     `complete=False` scope delists nothing.
   - `test_remote_complete_evidence_incomplete_scope_breaks_continuity_then_local_cannot_stale_delist`
     (revision 3, F-04 residual). Seed an old non-null `continuous_since`, a
     prior successful local FULL run, and active listings whose `last_seen` is
     well past the grace. Run several remote FULL runs with `complete` evidence
     and a `complete=False` scope, at intervals within the continuity
     tolerance. After the first one, `continuous_since` is `None`. Then run a
     local incomplete sweep with a short grace. Expected: zero delists, and
     `continuous_since` restarts at the local run's `observed_at`.
   - `test_complete_remote_run_delists_absent_listing` (positive control): `k-old`
     is delisted with `DelistReason.ABSENT_FROM_SWEEP`.
   - `test_local_incomplete_scope_keeps_stale_absence_path`: a local fake adapter
     implements `DelistDetector` with `complete=False` and a grace shorter than
     continuity. `k-old` is delisted as `ABSENT_STALE`, mirroring eBay
     `test_source_ebay.py:356-365`.

   Expected: green once A5 is wired correctly. The positive and negative controls
   prove the gate is live. If any negative case fails, fix the wiring. Never
   weaken the test.
2. Commit: `test(acquisition): prove non-complete provider runs cannot delist`.

**Revision 3 correction (F-04 residual).** A0–A6 had already landed when round
2 reported the residual. The fix lands on `dev` as a Slice A correction:
- scope: `acquisition/providers.py` (the two-argument
  `counts_toward_sweep_continuity`), its one call in `acquisition/pipeline.py`,
  and the Slice A-owned `tests/unit/test_provider_evidence.py` and
  `tests/db/test_collection_provider.py`;
- tests: the three named in A4 and A6 above. The existing unit continuity test
  moves to the two-argument call with its expectations unchanged.

The frozen pre-MS-2 test files stay unmodified, and so does the A0 baseline.

### A7 — Slice close-out

1. Full gate plus `makemigrations --check --dry-run`, both green.
   `git diff --stat <slice-base> -- tests/unit/test_ladder.py tests/db/test_resolver.py tests/db/test_rung0_regression.py tests/db/test_ratification_corpus.py tests/db/test_pipeline.py tests/db/test_pipeline_demo.py tests/db/test_listing_delist.py tests/db/test_source_*.py tests/db/test_poller_*.py tests/db/test_ms1_acceptance.py src/hw_radar/acquisition/sources src/hw_radar/acquisition/heartbeat.py src/hw_radar/poller`
   shows no changes.
2. Update `docs/TODO.md` (Slice A done; Slice B next) and the `docs/STATUS.md`
   focus line. Commit `docs: record MS-2 slice A completion`.

**Slice A acceptance:**
- The syrupy baseline is unchanged.
- All frozen regression files pass unmodified.
- Truncated, partial, and failed provider results cannot delist, and they break
  any existing continuity. Complete ones can delist. Remote `complete` evidence
  with an incomplete or missing scope cannot delist, and it breaks continuity
  (revision 3).
- The local continuity mapping is unchanged.
- An unregistered category never runs drive rules.
- There is no migration.

**Exit evidence:**
- gate output with the test count (≥ 552 + new);
- the empty frozen-file diff;
- the commit list.

## Slice B — First-class category specs and rules

**Scope:** MS2-D-04, -05, -06, -21, -27.

**Task order:** B1 → B2 → B3 → B4a → B4b → B4c → B6 → B5. B5 is the close-out
and runs last.

**Files:**
- `catalog/models/identity.py` (satellites + choices);
- migrations `0018`, `0019`;
- `matching/categories.py` (register gpu/ram/cpu + basic-watch);
- new `matching/rules/{gpu,ram,cpu,basic}.py`;
- `matching/resolver.py` (spec readers, cross-category guard, `auto_accept`,
  `variant_on_demand`);
- `refdata/{contracts,persist}.py` (category-discriminated spec payload);
- `refdata/seeds/`;
- `catalog/admin.py`;
- `matching/eval/{corpus,evaluate}.py` and
  `catalog/management/commands/harvest_corpus.py` (B6 only);
- **existing Slice A tests, narrowly (B3; revision 3, review N-03).** Slice A
  pinned a drive-only registry, and B3 registers more categories, so these two
  files are explicitly *not* frozen for B. Only the edits listed here are
  allowed:
  - `tests/unit/test_categories.py`:
    - `test_registered_categories_is_drive_only_in_slice_a` (≈:19–20) is
      renamed `test_registered_categories_matches_ms2_registry`. It asserts
      the exact set `{drive, gpu, ram, cpu, nic, hba, motherboard, server}`.
    - `test_rules_for_unregistered_is_none` (≈:35–36) uses the sentinel slug
      `zz-unregistered` instead of `gpu`.
    - The `dispatch_category("gpu")` pass-through assertion (≈:31) may stay,
      since it tests dispatch, not registration.
  - `tests/db/test_resolver_dispatch.py`:
    `test_unregistered_hint_writes_none_edge_and_never_runs_drive_rules`
    (≈:158–173) hints `zz-unregistered` instead of `gpu`, and asserts
    `evidence["category"] == "zz-unregistered"`. Every other assertion stays:
    NONE grain, `unsupported_category`, no `mpn_hypothesis`, no model.
  - `zz-unregistered` is a permanent sentinel. The exact-set registry
    assertion excludes it, and the `categories.py` module docstring reserves
    the `zz-` prefix for tests, never to be registered. A future category
    therefore cannot silently turn the unsupported-category tests green for
    the wrong reason.
  - Unchanged: `test_drive_rules_bind_existing_functions_by_identity`, the
    explicit-drive and legacy-default resolver tests, the invalid-slug tests,
    `test_spec_readers_match_registry`, and the A0 baseline
    (`tests/db/test_ms2_decision_baseline.py`, with no `--snapshot-update`).
    B's frozen-file diff lists every other pre-B test file.
  - Revision 5 (implementation-driven clarification from B4a): one more allowed
    edit. `tests/unit/test_refdata_contracts.py` (≈:55–56) gains
    `assert isinstance(spec, SeedDriveSpec)` type narrowing before its unchanged
    `capacity_tb`/`rpm` assertions. `SeedModel.spec` became a tagged union, so
    strict basedpyright needs the narrowing; no assertion changes.
  - Revision 5 (implementation-driven clarification from B4c): tests that pin
    the shipped seed corpus are scoped to drive documents, with **no**
    expected-value or snapshot changes, because any seed addition would
    otherwise touch them:
    - `tests/unit/test_refdata_loader.py::test_repo_seed_corpus_totals`: the
      drive assertions are scoped to `category == "drive"` documents, and new
      exact whole-corpus assertions (manufacturer keys, model and alias totals,
      per-category model counts) cover the first-party GPU, RAM, and CPU seeds;
    - `tests/db/test_refdata_import.py`: the `docs` fixture is drive-filtered;
    - the B-era `tests/db/test_refdata_categories.py`:
      `test_drive_documents_validate_unchanged` and
      `test_drive_import_byte_identical` are drive-filtered, with the snapshot
      unchanged.

- **B1 — Satellites (0018).** Add `GpuSpec`, `RamSpec`, and `CpuSpec` per the
  MS2-D-04 table, each with `retention_constraints("<table>")` and
  `retention_indexes("<table>_expires")`. Revision 5 (implementation-driven
  clarification): the live graph was verified with no drift, so B1 is
  `0018_category_spec_satellites` and B2 is `0019_seed_categories`. The
  satellites are 1:1 on `ProductModel` with `primary_key=True`, like `DriveSpec`,
  and use the choices `GpuChipVendor`, `GpuInterface`, `GpuCooling`,
  `RamGeneration`, and `RamModuleType`.
  - Tests: `tests/db/test_category_specs.py` covers the CHECK pair (empty class
    rejected; bounded without expiry rejected), 1:1 uniqueness, and
    `test_four_categories_coexist_on_one_spine` (AC-1).
  - The migration applies to an empty DB and to a DB holding drive rows; the
    drive-row counts are unchanged.
- **B2 — Category rows (0019, data).** `get_or_create` the rows `gpu`, `ram`,
  `cpu`, `nic`, `hba`, `motherboard`, and `server`. `drive` already exists from
  `0001:7-13`. The reverse deletes only rows with no families.
  - Tests: forward and reverse migration.
- **B3 — Rules.**
  - **Extractors.** Pure `extract`/`extract_candidates`/`veto` per category. The
    typed per-category attribute payload rides on `ExtractedAttributes` as a new
    optional `category_attrs` field (default None). The typed catalog payload
    rides on `HardAttrs` as a new optional `category` field. The drive fields
    and the drive `contradictions()` are untouched.
  - **Brands and part numbers.** Brand tables are category-local.
  - **Offer terms.** Condition, packaging, and warranty extraction reuse
    `vocab`'s tables through a new public helper. `vocab.extract` output must
    stay identical; the A0 baseline guards this.
  - **Rungs.** Rung 1 only. The `auto_accept=False` → `review`
    (`auto_accept_disabled`) behavior applies to gpu/ram/cpu. The cross-category
    guard applies to prior and alias targets.
  - **Basic-watch.** Basic-watch rules are exact-alias-only; `server` sets
    `variant_on_demand=False`.
  - **Acceptance policy (MS2-D-21).** Add `AcceptancePolicy` and
    `CategoryRules.acceptance`, and apply the policy in `_run_ladder` after
    `decide`. Accepted non-drive edges record `alias_source_kind`.
  - **Category change writes an edge.** This is a residual from Slice A. The
    resolver's no-spam rule writes no new edge when the outcome is unchanged.
    So a listing whose current edge is a `none` or `review` with
    `category=drive` keeps that stale category when a later snapshot hints
    `gpu`. B3 treats a change of `evidence["category"]` as a decision-input
    change and writes a new edge. For drive listings the category never changes,
    so drive behavior and the A0 baseline are unaffected.
  - **Version.** Bump `MATCHER_VERSION` because rules were added. The drive
    baseline snapshot must still match (decisions, not the version string).
  - Tests:
    - `tests/unit/test_{gpu,ram,cpu}_rules.py`: extraction and veto tables.
    - `tests/db/test_resolver_categories.py`: rung-1 review while auto-accept is
      off; `test_cross_category_alias_goes_to_review`; server no-variant.
    - `test_non_authoritative_alias_never_auto_accepts_new_category`. With
      auto-accept enabled through a test-only registration, the same token is
      aliased by `catalog_authoritative`, by `manual`, and by `listing_derived`.
      Only the authoritative alias accepts; the other two yield `review` with
      `acceptance_policy` evidence.
    - `test_alias_learned_from_earlier_observation_goes_to_review`: a listing
      learns a `listing_derived` alias, and a later listing hits it.
    - `test_family_grain_fanout_is_review_for_new_categories`.
    - `test_prior_from_non_authoritative_edge_is_review`.
    - `test_drive_acceptance_unchanged` (the A0 baseline).
    - `test_category_change_writes_new_edge_even_when_outcome_unchanged`.
    - Revision 5 (owner §29 category list; named so each exists explicitly):
      - `test_exact_authoritative_alias_accepts_when_enabled` [gpu, ram, cpu]:
        with a test-only `auto_accept=True` registration, an exact
        `catalog_authoritative` alias at model grain accepts.
      - `test_fuzzy_or_merchant_only_evidence_never_auto_accepts` [gpu, ram,
        cpu]: attribute-only evidence with no alias hit, and a hit only on a
        `listing_derived` or merchant-title token, yield `review` or `none`,
        never `accept`.
      - `test_hard_attribute_contradiction_blocks_accept` [gpu, ram, cpu]: an
        exact authoritative alias whose extracted hard attribute contradicts the
        target spec (for example VRAM, DDR generation, or socket) yields
        `review` with `veto` evidence.
    - The N-03 edits to `tests/unit/test_categories.py` and
      `tests/db/test_resolver_dispatch.py` listed under *Files*, landing in
      the same commit as the registration so the gate never goes red.
- **B4 — Reference seeds.** B4 is split into three sub-tasks. Drive seed
  documents stay valid, unchanged.
  - **B4a — Contract (`refdata/contracts.py`).**
    - Add a `category` discriminator to `SeedDocument`, where `drive` is the
      default when absent, so existing drive documents validate unchanged.
    - Make `SeedModel.spec` a tagged union
      `SeedDriveSpec | SeedGpuSpec | SeedRamSpec | SeedCpuSpec`. Each variant's
      fields mirror its satellite by name, following the existing convention
      (`contracts.py:57-58`).
    - Add per-row provenance: `source_url` and `retrieved_on` on every non-drive
      `SeedModel`. The per-document block alone no longer satisfies IR-007 for
      new categories.
    - Extend the `SeedProvenance.source_kind` Literal with a non-first-party
      value that maps to alias `source_kind=manual`. This follows MS2-D-06:
      authoritative status applies only to first-party manufacturer pages.
      Revision 5 (implementation-driven clarification): the value is
      `non_first_party`, and the importer refuses such documents until R32
      clears (MS2-D-06). Revision 9 (owner decision (s4, 2026-09-25), OQ27):
      R32 is resolved without a new retention class, so the refusal is the
      MS-2 behavior. The landed contract value, its `manual` mapping, and the
      refusal need no code change.
    - Tests: `test_refdata_categories.py::test_drive_documents_validate_unchanged`,
      `::test_non_drive_row_without_source_url_rejected`,
      `::test_spec_variant_must_match_document_category`, and
      `::test_non_first_party_row_maps_to_manual`.
  - **B4b — Importer (`refdata/persist.py`).**
    - Replace the hard-coded `slug="drive"` lookup (`:152`) with the document's
      category.
    - Replace the `DriveSpec` write (`:208`) with a category → satellite
      dispatch table, keyed like `_SPEC_READERS`.
    - The `catalog_authoritative` / `manufacturer_reference` gating
      (`:225-228`) applies only to first-party rows. Conflict detection stays
      unchanged.
    - Tests: `::test_gpu_document_imports_to_gpu_spec_and_gpu_category`, and
      `::test_drive_import_byte_identical` (the frozen `test_refdata_*` suites
      stay green).
  - **B4c — Seeds.** Author a small curated seed per category, enough for C's
    representative cases, from the first-party sources in MS2-D-06. Record the
    URL and date for every row. The suggested scope is homelab and used-server
    relevance:
    - CPU: about 10–15 rows (recent EPYC SP5 and Xeon LGA4677, plus AM5 and
      LGA1700/1851 if consumer coverage is wanted);
    - GPU: about 8–12 rows (NVIDIA V100/A100/H100 class, high-VRAM GeForce, and
      AMD Instinct);
    - RAM: about 10–15 rows (DDR4 and DDR5 RDIMM at 16/32/64 GB, sourced from
      Micron's decoder first).

    Community decode grammars (Samsung, SK hynix) and OEM↔module-maker
    equivalences are seeded as `manual` or not at all.

    Revision 5 (implementation-driven clarification): the row counts above are
    superseded by what first-party sources support, researched 2026-09-24: CPU
    9, GPU 8, and RAM 2 first-party rows. Three more Micron-authored PDFs are
    hosted on third-party domains, so they count as non-first-party and are
    blocked by R32. RAM expansion is a follow-up.

    Revision 9 (owner decision (s4, 2026-09-25),
    [OQ27](../../resolved-questions.md#oq27--retention-class-for-non-first-party-reference-data)): the RAM-expansion follow-up is
    **withdrawn**. Its only purpose was admitting those three third-party-hosted
    Micron PDFs, and MS-2 adds no class that would admit them. The existing
    first-party RAM rows stay. Because the importer refuses `non_first_party`
    documents, the "`manual`" option above is unavailable through it in MS-2:
    community decode grammars and OEM↔module-maker equivalences may inform
    research or manual review only.
- **B6 — Category hint through the corpus tooling (MS2-D-27).** Add
  `ListingFields.category_hint`. `_staging_entry` writes the key only when it is
  non-null, and `_ingest` passes the hint through. Revision 5 (implementation-driven
  clarification): B6 landed on `dev` as `a789e98`. The hint is validated with
  `CATEGORY_SLUG_RE`, and its key sits inside the staging entry's `listing`
  block.
  - Tests (new file `tests/db/test_corpus_category_hint.py`):
    - `test_harvested_non_drive_entry_reaches_category_rules`: a `ParsedListing`
      with `category_hint="gpu"` and a drive-shaped title that the drive rules
      would accept at rung 1 goes through `_staging_entry`, a JSONL round trip,
      `load_corpus`, and `evaluate_corpus`. The resulting edge has
      `evidence["category"] == "gpu"` and never a drive model target.
    - `test_unhinted_staging_entry_bytes_unchanged`.
    - `test_existing_drive_corpus_loads_unchanged`.
    - The A0 baseline is unchanged.
- **B5 — Admin + close-out.** Register the satellites in Django admin, then run
  the gate and update TODO/STATUS.

**Slice B residual (session 2):** reversing migration `0019` may delete an
adopted pre-existing family-less row where only the drive exists on the
deployed schema (no family row to fall back to). Not yet a blocking issue
(0019 is unmerged into production; deployed schema is still `0017`), but a
rollback-path caveat for whoever writes or reviews the `0019` reverse.

**Acceptance:**
- AC-1 holds.
- GPU/RAM/CPU exact-alias hits reach `review` while auto-accept is off.
- Cross-category hits never auto-accept.
- With auto-accept enabled, only authoritative aliases at model or variant
  grain accept for the new categories.
- A harvested non-drive corpus entry replays through its own category rules.
- The drive baseline is unchanged.
- The only edits to existing tests are the listed N-03 edits.

**Exit evidence:** migration apply/rollback transcript on a DB holding drive data;
gate; `git diff <slice-base> -- tests/unit/test_categories.py
tests/db/test_resolver_dispatch.py`, which shows only the N-03 edits; and the
empty frozen-file diff for every other pre-B test file.

## Slice C — Watches, eligibility, shortlist read model

**Scope:** MS2-D-07, -08, -09, -20, -29.

**Files:**
- new `catalog/models/watch.py`;
- migration `0020`;
- new package `hw_radar/eligibility/` (`requirements.py`, `evaluate.py`,
  `shortlist.py`, `service.py`);
- `acquisition/pipeline.py` (post-resolution evaluation stage, isolated);
- `catalog/models/market.py` (`mark_delisted` pull-forward for evaluations);
- the purge registry;
- new management commands `evaluate_watches` and `show_shortlist`.

- **C1 — Schema (0020).** Add `Watch`, the four requirement satellites
  (ArrayFields of the satellite choices), and `WatchEvaluation`
  (`RetentionGoverned`, CHECK pair, unique `(watch, listing)`, index
  `(watch, verdict)`, and the MS2-D-09 input-binding fields, including the
  nullable `resolution` FK with `SET_NULL` and `catalog_fingerprint`,
  MS2-D-29).
  - Tests: constraints, including the at-most-one target CHECK and the retention
    pair; also the migration test.
- **C2 — Requirement service.** Per-category Pydantic `RequirementSpec`s back
  `save_requirement(watch, spec)`. It is the single writer: it enforces category
  = satellite, bumps `requirement_version`, and requires a target for
  basic-watch categories.
  - Tests: validation matrix; version bump on each edit; no bump on a no-op save.
- **C3 — Evaluator.** Implement `evaluate_listing(listing_id)` for all enabled
  watches of the dispatch category, with a clause registry per category and
  clause/aggregate semantics per MS2-D-08.
  - It writes or updates `WatchEvaluation`, mirroring listing retention and
    stamping the input binding (MS2-D-20). The catalog fingerprint comes from
    the same `catalog_inputs()` call that feeds the product clauses
    (MS2-D-29).
  - It runs in the pipeline after resolve via a `ListingEvaluator` protocol.
    `run_collection(..., evaluator=None)` binds the production `WatchEvaluator`
    (MS2-D-20), so poll, heartbeat-fired, and recovery-probe runs all evaluate
    without caller changes. `heartbeat.py` and `poller/service.py` are not
    edited in C. A listing whose resolution raised is skipped. Failures are
    counted in `detail_json["evaluator_errors"]` and never block ingestion.
  - Tests:
    - `tests/unit/test_eligibility_clauses.py`: per-clause tri-state tables,
      including listing-tier contradiction ⇒ `no_match`, listing-tier agreement
      ⇏ `match`, and review-resolution ⇒ `unknown`.
    - `tests/unit/test_eligibility_aggregate.py`.
    - `tests/db/test_watch_evaluation.py` (AC-2): a parametrized drive/gpu/ram/cpu
      × match/no_match/unknown set built on B's curated seeds and accepted
      resolutions. Revision 5 (owner §29) names two cases explicitly:
      `test_missing_required_catalog_attribute_is_unknown` [drive, gpu, ram,
      cpu] (a required clause whose catalog value is NULL or whose spec row is
      absent yields `unknown`, never `match`), and
      `test_hard_catalog_contradiction_is_no_match` [drive, gpu, ram, cpu].
    - Soft-threshold test.
    - Delist pull-forward and purge-registry test.
    - `test_pipeline_evaluator_failure_isolated`.
    - `tests/db/test_evaluation_binding.py`:
      - `test_prior_match_then_contradictory_evidence_updates_verdict`: a new
        over-budget snapshot turns `match` into `no_match`.
      - `test_evaluator_failure_after_prior_match_leaves_shortlist`: the
        evaluator raises on the new snapshot, so the old `match` row is
        `pending` and absent from `shortlist()`.
      - `test_resolution_change_makes_evaluation_pending`.
      - `test_heartbeat_fired_run_updates_watch_evaluation`: `run_heartbeat`
        with a transitioning fake probe fires the FULL run, and the evaluation
        is re-bound to the new snapshot with no evaluator argument passed.
      - `test_recovery_probe_run_evaluates`.
      - `test_listing_with_resolver_error_is_not_evaluated`.
      - Revision 3 (MS2-D-29, F-03 residual):
        - `test_same_target_catalog_correction_excludes_match_from_shortlist_immediately`.
          A GPU listing has an accepted model-grain edge; a test-only
          auto-accept registration or a manual edge may be used. The watch
          requires `coolings=[active]`, and the evaluation is `match` in
          `shortlist()`. A corrected seed is re-imported through
          `refdata.persist`, setting that model's `GpuSpec.cooling` to
          `passive`, with the same alias and the same target. There is no new
          observation and no resolver run. Immediately afterwards the row is
          absent from `shortlist()` and appears as `pending` in
          `review_queue()`. `evaluate_watches --pending` then persists
          `no_match`.
        - `test_catalog_correction_by_queryset_update_is_detected`: the same
          correction made with `GpuSpec.objects.filter(...).update(...)`,
          which proves that no writer hook is needed.
        - `test_family_membership_change_makes_family_grain_evaluation_pending`.
        - `test_unrelated_catalog_edit_keeps_evaluation_current` (a negative
          control: no churn).
- **C4 — Read model.** Implement `shortlist(watch_id)` and
  `review_queue(watch_id)` with freshness `fresh | stale` (stale = latest
  snapshot older than 2 × the source's `cadence_baseline_s`; a labeled
  assumption, tunable). E adds `budget_paused`. Add the `show_shortlist`
  command.
  - Only current rows (MS2-D-20) qualify. `review_queue()` labels rows
    `unknown` or `pending`. `evaluate_watches --pending` re-evaluates non-current
    rows.
  - Tests: ordering by landed USD; only current evaluations are included (a
    stale requirement version, snapshot, resolution, evaluator version, or
    catalog fingerprint each excludes the row and shows it as `pending`);
    `test_prior_match_then_contradictory_evidence_leaves_shortlist`; delisted
    and expired rows are excluded; the result has no score field; a `grep` guard
    asserts no import of any scoring module.
- **C5 — Close-out:** gate; TODO/STATUS.

**Implementation-driven clarifications (session 2, C code-complete):**
- Contradiction confidence is ≥0.85 per `CategoryPolicy`.
- Catalog-yes/title-no is `no_match` (MS2-D-08, taken literally).
- Soft-only catalog edits do not bump `requirement_version`; this deviates
  from the plan's "bumps on each edit" text because the evaluator never reads
  soft fields.
- Unit price = `price + known shipping (+ stated tax)` in USD, divided by the
  title quantity (none parses as 1; a low-confidence quantity that only
  bounds the value yields `quantity_uncertain`).
- Unknown shipping yields `shipping_unknown` (an `unknown` outcome) unless the
  price alone already exceeds the requirement's max, which is `no_match`.
- A family-grain spec value counts only when every family member has it and
  they agree.
- Only the currently accepted catalog edge supplies catalog evidence.
- `EVALUATOR_VERSION` is `ms2c.2`.

**Acceptance:**
- AC-2 holds.
- The shortlist runs on fixture observations with no ADR-0011 artifact (AC-3's
  code half).
- Evaluator failures are isolated, and they fail closed: the affected rows
  become `pending`.
- Evaluation runs on the poll, heartbeat-fired, and probe paths.
- A catalog correction to an evaluated target makes its evaluation `pending`
  immediately (MS2-D-29).

## Slice D — Apify provider (source-agnostic)

**Scope:** MS2-D-12…-16, -18, -22…-25, -28, -30, -31, -33, -35…-37, the
D-side counters of -32, and (revision 5) -38, -39, and the D-side fixtures of
-42.

**PRs:** D-prep is D1 + D3. D (core) runs in the order D2 → D4 → D10 → D5 →
D6 → D7 → D8 → D11 → D12 → D9. The staged importer (D10) comes before the jobs
and the tests that import through it. See *Slice order*.

**Slice D entry gate (revision 4).** Before D (core) implementation starts at
D2, the D/E asynchronous-ordering design gets a focused design review against
the then-current code. D-prep (D1, D3) is not gated.
- **Design under review:** the watermarks (MS2-D-30, -35), per-scope ordered
  continuity (MS2-D-31, -36), per-observation retention (MS2-D-37), the charge
  horizon (MS2-D-32, -34), and the absolute retention deadline (MS2-D-33).
- **Re-verify against code:** the seams these decisions cite still behave as
  cited. That means `persist.upsert_listing` and `append_snapshot`
  (`persist.py:52-107`); `_persist_all`'s relist and snapshot calls
  (`pipeline.py:289-302`); `_record_sweep_continuity`,
  `_break_sweep_continuity`, and `_apply_delist` (`pipeline.py:111-218`);
  `Listing.mark_delisted` and `mark_relisted` (`market.py:330-388`,
  `:493-518`); `SourceLaneState` (`ops.py:208-247`; `lane_state()`
  `:192-206`); and the frozen continuity tests in `test_source_ebay.py` and
  `test_collection_provider.py`. (Revision 10 re-pointed these citations to
  the current code, ED-11.)
- **Re-verify externally** (these are already D3 and E2 gates; the review
  confirms the design does not depend on an unverified answer):
  - Apify wire fields: `usageTotalUsd`, `buildNumber`, the `usage` component
    keys, platform `maxItems` semantics, and the response to aborting a run
    that already finished (MS2-D-15, -33; D3).
  - Storage expiry: default-storage expiry for the chosen plan, and whether a
    per-run or per-storage expiry exists (MS2-D-25, -33 *Reopen if*).
  - Billing completeness: whether run usage covers the importer's reads, the
    deletes, and timed default storage; whether `GET` run polls bill; and the
    direction of transfer pricing (MS2-D-32; E2). Revision 10 (ED-01): the
    docs cannot settle the last two, so they are priced by conservative
    bounds rather than gated, and F5a measures them; see *Gate result*.
- **Also settle:** the lock order and contention of the MS2-D-35 scope-row
  locks against the poller's lane-state writes, and risk R23.
- **Revision 5 scope extension (owner-clarified (s2, 2026-09-24)).** The review
  also covers, against the D3 wire facts:
  - billing-cycle accounting: cycle discovery, the stored cycle row, straddling
    reservations, and the boundary guard (MS2-D-34, -40);
  - usage finalization: the provisional → finalized rule and the settle delay
    (MS2-D-41);
  - post-run usage: the bound-until-measured allowance and the account-diff
    measurement (MS2-D-32, -41);
  - the three clocks: each transition in the MS2-D-39 table is checked against
    the code that will implement it.
- **Outcome.** Record the review and its disposition in *Review lineage*. A
  finding that changes a contract revises this plan before D2 starts.
- **Gate result (revision 10, 2026-09-25).** Done: architect review, READY
  WITH PLAN AMENDMENTS (ED-01..ED-20). Every finding is applied in revision 10
  (*Review lineage*, *Slice D entry gate*), so D2 may start. The seams above
  were re-verified at `493abca`; this revision re-points the drifted
  citations. Lock order and contention are settled in MS2-D-35
  *Serialization*, and R23 is kept as an accepted MS-2 residual. The external
  facts the docs cannot settle (poll billing, transfer direction, post-run
  usage inclusion, timed-storage posting) are priced by conservative bounds,
  not gated (ED-01), and F5a measures them.

**Files:**
- new `acquisition/apify/{client,contract,provider,jobs}.py`;
- revision 5 (MS2-D-38): the new Actor project `actors/hw-radar-synthetic-collector/`
  (`.actor/`, `contract/hw-radar-{input,listing,run}-v1.schema.json`,
  `pyproject.toml`, `uv.lock`, Dockerfile, `.actorignore`, source, `tests/`,
  `fixtures/`, `DEPLOYMENTS.md`), and `scripts/check.py` (the Actor project's
  gate commands). Revision 1–4's `acquisition/apify/schemas/` location is
  withdrawn;
- `tests/fixtures/apify_contract/v1/*`;
- `catalog/models/provider.py` (`ProviderRun`);
- migration `0021`;
- `contracts.py` (`ParsedListing.collection_scope`, `DelistScope.scope_key`);
- `persist.py` (scope write; insert-if-absent snapshot for provider imports;
  the ordering-guarded `observe_listing`, MS2-D-30, -35; and the
  `ObservationRetention` parameter of `append_snapshot`, MS2-D-37);
- `catalog/models/market.py` (`Listing.last_observed_at`, MS2-D-30;
  `Listing.last_absence_at`, raised by `mark_delisted`, MS2-D-35),
  `catalog/models/ops.py` (the NULL-scope watermarks on `SourceLaneState`,
  MS2-D-35, -36), and `catalog/models/provider.py` (`ScopeSweepContinuity`,
  MS2-D-31, -35, -36);
- `pipeline.py` (scope-filtered and newer-observation-guarded `_apply_delist`
  that raises the complete-sweep watermark; ordered per-scope continuity with
  timestamped breaks and the NULL-scope break rule; absence-gated relist and
  creation; explicit snapshot retention in `_persist_all`; stage functions
  extracted from `run_collection`; local-only `_median_body_bytes`);
- new `acquisition/retention_policy.py`;
- new `acquisition/apify/importer.py` (the stage machine);
- `poller/service.py` (apify start branch, provider-dispatched recovery probe,
  and the `apify-poll` job).

- **D1 — Contract and the Actor skeleton** (revision 5 rewrites the location,
  MS2-D-14, -38, -42).
  - Create `actors/hw-radar-synthetic-collector/` per MS2-D-38. Verify the
    Apify Python template layout and the Actor image's Python version against
    current official docs (research input `apify-ops.md` left both unconfirmed),
    and record the URL and date in the Actor README.
  - Commit the contract schemas under the Actor's `contract/`. Add the
    hw-radar Pydantic models in `acquisition/apify/contract.py` and
    `classify_run(remote_status, output, dataset_count, usable_count) ->
    (RunCompleteness, reason, TruncationReason | None)` per the MS2-D-14 mapping,
    including `contradictory_report` and `scope_mismatch`. Add `TruncationReason`
    (MS2-D-11) without touching `RunCompleteness`.
  - The listing row schema carries per-field `maxLength` caps, which supply the
    per-row byte cap in MS2-D-26. The input schema carries `maxItems`,
    `maxPages`, `maxRequests`, `maxBytes`, and `timeBudgetSecs`, and has no
    proxy field. The synthetic Actor's input adds `fixtureCommit`,
    `fixturePaths`, and `faultMode` (MS2-D-42).
  - The Actor's pure core enforces every cap itself, writes merchant content
    only to the default dataset, and writes only `OUTPUT` to the default KV
    store.
  - Extend `scripts/check.py` with the Actor project's basedpyright, pytest with
    coverage, and pip-audit commands (MS2-D-38). No Apify push or build happens
    in D-prep.
  - Add frozen fixtures: complete, **complete-empty**, **ambiguous-empty**
    (`SUCCEEDED`, empty dataset, `itemsDeclared` missing), **nonempty-unusable**,
    truncated-by-pages, truncated-by-items, truncated-by-bytes, timed-out,
    partial-with-errors, failed-with-items, missing-OUTPUT, unknown-schema,
    count-mismatch, complete-with-limit-hit, status-mismatch, and
    scope-mismatch. The Actor's own tests regenerate each fixture from its
    `faultMode`, so the hw-radar fixtures and the Actor's output cannot drift.
  - Tests: `tests/unit/test_apify_contract.py` covers:
    - `test_pydantic_models_equal_committed_contract_schemas` (the drift guard,
      reading the files under `actors/`);
    - `test_apify_input_schema_matches_contract_input` (MS2-D-14);
    - `test_invalid_input_rejected_before_start_request`;
    - the mapping table over every fixture;
    - "unknown or missing ⇒ never complete", and
      `test_unknown_schema_version_is_failed`;
    - `test_complete_empty_requires_all_zero_counts_and_pages_fetched`;
    - `test_empty_without_evidence_is_failed_ambiguous`;
    - `test_nonempty_unusable_is_failed_not_complete_empty`;
    - revision 5: `test_truncation_reason_distinguishes_items_pages_time_bytes`,
      `test_contradictory_report_is_failed` [complete + limit hit, count
      mismatch, status mismatch], and `test_scope_mismatch_is_failed`.
  - Actor tests (`actors/hw-radar-synthetic-collector/tests/`, run by the
    Actor project's pytest, no network): `test_limits.py::test_item_page_request_byte_caps_enforced`;
    `test_boundaries.py::test_no_proxy_configuration_or_django_import` (a
    source scan); `test_output.py::test_every_fault_mode_emits_schema_valid_output`;
    `test_output.py::test_kv_store_holds_only_output_record`.
  - The Slice A test files stay unmodified, including
    `test_completeness_values_are_the_adr0021_taxonomy` and
    `test_run_source_records_local_provider_evidence`.
- **D2 — Schema (0021).**
  - Add `ProviderRun` with the MS2-D-13 fields, including the
    MS2-D-22/-23/-25/-32/-33 state columns; `SourceConfig.collection_provider`
    + CHECK; `Listing.collection_scope` + index; `Listing.last_observed_at`
    (nullable, no backfill, MS2-D-30); `Listing.last_absence_at` (nullable,
    no backfill, MS2-D-35); the NULL-scope watermarks on `SourceLaneState`,
    all nullable and written only on the FULL lane row
    (`last_eligible_sweep_at`, `continuity_broken_at`,
    `last_complete_sweep_at`; MS2-D-35, -36); and the `scope_sweep_continuity`
    table with the same three watermarks (MS2-D-31, -35, -36).
  - Revision 10 (entry gate): `provider_run.scope_key` is NOT NULL (ED-03,
    MS2-D-13), and `provider_run` gains `run_poll_count` and
    `correction_read_count` (integer, default 0; ED-01, MS2-D-32).
  - `_apply_delist` scope filter (None ⇔ NULL).
  - Tests: CHECK, uniqueness of `(provider_kind, external_run_id)` and of the
    idempotency key (null run ids allowed for starting rows),
    `test_provider_run_scope_key_is_required` (revision 10),
    `test_scoped_complete_sweep_cannot_delist_other_scopes`, and the
    `(source_site, collection_scope)` uniqueness of `scope_sweep_continuity`.
    Every revision-4 column is nullable with no backfill, so deployed rows
    upgrade with the column NULL, which each guard reads as "no bound".
    Frozen eBay delist tests stay green.
- **D3 — Client.** Add a thin `httpx.AsyncClient` wrapper for the seven run and
  storage calls plus the account reads (MS2-D-15, revision 5), with dataset
  pagination and the token from `HW_RADAR_APIFY_TOKEN` (the scoped runtime
  token, never the operator/deploy key; revision 9, MS2-D-43). The client
  never sends `maxItems` or `maxTotalChargeUsd`. The run response parser keeps
  `usage_total_usd` nullable and returns `finishedAt`, so E can apply the
  settle rule (MS2-D-41).
  - Start passes `memory` (MB) and `timeout` (s) as run options, following the
    MS2-D-15 naming trap. It returns the run's `options` so the caller can verify
    them.
  - Tests: `httpx.MockTransport`; the token never appears in logs, errors, or
    `detail_json`. `test_start_sends_memory_and_timeout_run_options` asserts the
    query parameters and that the httpx request timeout is independent of them.
    `test_delete_404_is_success`.
  - Before merge, verify the wire names still unconfirmed in MS2-D-15 against
    the official API reference: `usageTotalUsd`, `buildNumber`, the `usage`
    component keys (the proxy keys matter to MS2-D-26), the account-limits and
    monthly-usage fields MS2-D-40 reads, and whether invalid input is rejected
    before a billable run exists. Record the URL and date in the client
    docstring. (`maxItems` is settled as not applicable, revision 5.)
  - Revision 5 tests: `test_account_limits_parsed_into_cycle_bounds`,
    `test_monthly_usage_parsed_with_date_parameter`, and
    `test_client_never_sends_max_items_or_max_total_charge`.
  - Revision 8 (review R7-01): the get-build read (MS2-D-15) parses the build's
    status, `finishedAt`, and nullable usage the same way as a run's; its wire
    names are verified with the others. Test:
    `test_build_record_parsed_with_nullable_usage`.
  - **D3 follow-up (revision 10, entry gate).** D3 has landed, so these client
    changes land in D (core) with D5, whose start job is their first
    consumer. They are part of `acquisition/apify/client.py` in D *Files*:
    - (ED-17) the start call moves from the deprecated `/v2/acts/…/runs`
      (`client.py:433`; `api/v2`: "deprecated but still fully functional")
      to the current `/v2/actors/…/runs` path. Re-verify the path against the
      API reference when making the change;
    - (ED-20) the start sends `restartOnError=false` explicitly, and the
      parsed start response exposes it for the MS2-D-26 options check;
    - (ED-10) the usage parser surfaces unparseable `usage` / `usageUsd`
      entries instead of dropping them (`client.py:732-740`), so the
      allowlist latch can see them. The module docstring marks the component
      keys "confirmed 2026-09-25" (`client.py:84-86`);
    - (ED-09) `get_account_limits` parses `limits.dataRetentionDays`;
    - (ED-01) every response body is read up to
      `…_MAX_API_RESPONSE_BYTES`, and a larger body raises;
    - (ED-19) `test_delete_404_is_success` stays: the client still reports a
      404 as `absent`, and MS2-D-25's *404 rule* decides what the caller does
      with it.
  - Tests: `test_start_uses_actors_path_and_sends_restart_on_error_false`,
    `test_unparseable_usage_component_is_surfaced_not_dropped`,
    `test_account_limits_parse_data_retention_days`, and
    `test_oversized_response_body_raises`.
  - **D3 follow-up, revision 11 (R10-01, R10-08).** It lands with the
    revision-10 items, in the same client module:
    - every Apify client socket sets `SO_RCVBUF` to
      `HTTP_RECEIVE_BUFFER_BYTES` (65536) through
      `httpx.AsyncHTTPTransport(socket_options=…)`, and the client refuses,
      before sending, a request body above `MAX_API_REQUEST_BODY_BYTES`
      (16384) (MS2-D-32 *Wire ceiling*);
    - a dataset page body is read up to `…_MAX_DATASET_PAGE_BYTES`, and
      every other body up to `…_MAX_API_RESPONSE_BYTES`;
    - `iter_dataset_items` takes a required `page_size`, and the importer
      passes the derived `page_limit`. The 1000-item default
      (`client.py:135`) stays only for direct calls outside the importer;
    - the pure `max_serialized_row_bytes(schema)` function and `page_limit`
      live in `acquisition/apify/contract.py`, next to the models they
      bound.
  - Tests (revision 11): `test_client_sets_receive_buffer_socket_option`;
    `test_request_body_over_cap_refused_before_send`;
    `test_dataset_page_cap_distinct_from_control_response_cap`;
    `test_httpcore_constants_match_wire_ceiling` (it pins httpcore's
    `READ_NUM_BYTES` = 65536 and `MAX_INCOMPLETE_EVENT_SIZE` = 102400, so a
    dependency upgrade that changes either fails the gate); and, in
    `tests/unit/test_apify_contract.py`,
    `test_serialized_row_bound_covers_worst_case_escaped_row` (a row with
    every string field at `maxLength` in 4-byte code points, serialized with
    `ensure_ascii=True` and indented, fits `max_item_bytes`) and
    `test_page_limit_derived_from_row_bound_and_page_cap`.
  - Landed 2026-09-25 (revisions 10 and 11, client side; the contract items
    landed with D4). `/v2/actors/…/runs` re-verified against the OpenAPI
    document (`v2-2026-09-24T114302Z`). Its `RunOptions` omit
    `restartOnError`, so the echo is nullable and the client also exposes
    `stats.restartCount` as the observed-restart signal for E4.
- **D1 follow-up (revision 11, R10-02).** The Actor's byte check runs after a
  chunk arrives (`actors/hw-radar-synthetic-collector/src/synthetic_collector/core.py:344-353`),
  and `tests/test_limits.py:158-175` accepts a 100-byte chunk under
  `maxBytes=10`. So `maxBytes` is not a transfer ceiling. This follow-up
  makes the overshoot bounded and matches the MS2-D-26 *Data transfer* row.
  It lands in D (core) with D5, before any F5a deploy, in one hw-radar PR
  that changes `actors/` (MS2-D-38):
  - `_fetch_page` counts **wire** body bytes in a **run-wide cumulative
    raw-input counter** (final resolution check, R10-02). The counter lives
    on the run, not the response: it carries every earlier response's total,
    including responses that failed or were abandoned. It is advanced and
    checked on each raw receive **before** decoding (iterating the raw
    stream, e.g. `aiter_raw()`), so compressed input that yields little or
    no decoded output still advances it. The fetch stops on the first raw
    read that makes the cumulative total cross `maxBytes`. That read is at
    most one network read (httpcore's `READ_NUM_BYTES`, 65536, pinned by the
    Actor's lockfile and a test);
  - `new_http_client` (`main.py:52-59`) builds its transport with
    `SO_RCVBUF` = 65536, so the bytes in flight when a response is abandoned
    (over the byte cap, past the deadline, or a non-200 body that is never
    read) are at most twice that;
  - both constants are module constants in the Actor source. A hw-radar
    drift test, `tests/unit/test_apify_contract.py::test_actor_transfer_constants_match_estimator`,
    reads them from the Actor source, as the schema drift guard reads the
    contract files, and fails if the estimator's `HTTP_READ_CHUNK_BYTES` or
    `request_wire_overhead` no longer covers them.
  - Actor tests: `test_limits.py::test_byte_cap_counts_wire_bytes_and_stops_on_first_crossing_chunk`
    (a 100-byte chunk under `maxBytes=10`: one chunk read, `limitsHit.bytes`,
    no second request) and
    `test_limits.py::test_http_client_pins_receive_buffer`, plus
    `test_limits.py::test_byte_cap_is_cumulative_across_responses` (the cap
    is crossed in a later response, with earlier totals carried) and
    `test_limits.py::test_byte_cap_counts_compressed_raw_bytes` (a gzip body
    crosses the cap in raw bytes while decoding little). The existing
    `test_byte_and_time_limits_binding_on_one_chunk_are_both_reported`
    stays unchanged.
  - Landed 2026-09-25 (Actor side). The drift test above and the estimator
    test below land in **E2** with the estimator constants they compare
    against (`HTTP_READ_CHUNK_BYTES`, `request_wire_overhead`); the Actor's
    constants are plain literals so the drift guard can read them.
  - hw-radar estimator test (E2):
    `test_apify_budget.py::test_transfer_bound_covers_actor_overshoot_of_one_read_and_in_flight_window`.
    With `maxBytes = B`, it covers `B − 1` counted bytes, then a final
    65,536-byte chunk, then `2 × 65,536` in flight, plus `maxRequests`
    non-200 responses each abandoned with a full window in flight. All of
    it fits the *Data transfer* component.
- **D4 — Import provider.** Add `ApifyImportProvider(provider_run)` as a
  `CollectionProvider` of kind `apify`:
  - `fetch` reads the dataset into a `RawBatch` of dataset rows only, with
    `fetched_at=startedAt`. `OUTPUT` is stored on `provider_run` (MS2-D-14);
  - `parse` validates rows, rejects `siteKey` ≠ the run's site, rejects rows over
    the byte cap, and carries `category_hint`/`collection_scope`;
  - `delist_scope` returns a scope only for `complete` (including
    complete-empty), with `scope_key`;
  - `run_evidence` comes from `classify_run` and is never stale-eligible;
  - retention comes from `source_retention(site_key)` (MS2-D-25), never from
    defaults;
  - `_classify_batch` is not applied to dataset rows, and `_median_body_bytes`
    counts local runs only (MS2-D-28). Test:
    `test_provider_switch_does_not_soft_block_imports_or_local_runs`.
  - Revision 5 test: `test_imported_rows_carry_scope_and_category_hint`.
- **D5 — Jobs.**
  - The start job runs through `check_admission`, then a `BudgetAdmission`
    protocol. Its production binding is `DenyAllAdmission`, so live starts are
    impossible until E.
  - The start job creates the `provider_run` row with `admitted_at` and the
    absolute deadline before the start request (MS2-D-33).
  - The `apify-poll` interval job runs the three MS2-D-23 selectors. Terminal
    rows enter the MS2-D-22 stage machine (D10). Storage cleanup and overdue
    storage are their own units of work (D11).
  - Actor failure maps to ADR-0017 lifecycle events via the existing
    classification, at stage 5 or at reject.
  - Tests (`tests/db/test_apify_poll_job.py`):
    - `test_active_selector_polls_only_non_terminal_runs`;
    - `test_outstanding_selector_picks_terminal_rows_with_unfinished_import`;
    - `test_overdue_selector_picks_rows_past_deadline_regardless_of_remote_status`
      (parametrized: null, non-terminal, and terminal remote status);
    - `test_one_failing_row_does_not_block_others`;
    - revision 5: `test_start_uses_configured_actor_and_build_tag` (the Actor
      id and build tag come from settings) and
      `test_start_build_outside_contract_version_line_aborts` (MS2-D-38).
    - revision 11 (R10-06): `test_start_option_mismatch_abort_is_counted_cleanup_attempt`
      (`storage_cleanup_attempts` is 1 and committed before the abort is
      sent; the attempt deletes only after terminal evidence) and
      `test_start_request_is_never_retried` (a transport error on start
      leaves the row for `orphaned_start` handling, MS2-D-33).
  - Landed 2026-09-25: `acquisition/apify/jobs.py` (`start_provider_run`,
    `apify_poll_tick`), the `poll_source` apify branch and the `apify-poll`
    job in `poller/service.py`, and the D5 tests above plus D10's two restart
    tests. The run input comes from a per-site `ActorRunSpec` in
    `jobs.RUN_SPECS`, empty in production (F5a registers the synthetic site).
    A start mismatch rejects the import as `start_option_mismatch` (ADR-0017
    `UNKNOWN`) before the abort; D10's other reject classes were checked
    against ADR-0017 and kept. The cleanup and overdue units are logging
    hooks until D11 binds them.
  - Follow-up 2026-09-25: the plan sets no rule for concurrent FULL runs
    of one scope (MS2-D-30/-36 only make them safe), so the start job now
    refuses a FULL start with `scope_run_outstanding` while a FULL
    `provider_run` of the same `source_site` and `scope_key` has an import
    that is neither finalized nor rejected. The refusal comes before budget
    admission (no ledger row, no account read, no spend), next to the
    MS2-D-33 refusals. Storage state is not consulted. PROBE runs keep
    D12's per-source rule. Test:
    `test_apify_poll_job.py::test_full_start_refused_while_same_scope_run_outstanding`.
- **D6 — Idempotency (AC-6).**
  - `test_duplicate_completion_is_noop`: a second import of the same run adds no
    `ScraperRun`, `OfferSnapshot`, or `RawPayload` rows.
  - `test_crash_between_persist_and_mark_replays_once`: a crash inside the
    stage-1 transaction rolls back, and the replay imports exactly once.
  - `test_replayed_dataset_read_does_not_duplicate_snapshots`.
  - `test_retry_reuses_scraper_run`.
  - Revision 5 (owner §29): `test_duplicated_dataset_import_is_noop` (the same
    dataset imported through two `provider_run` claims of one run adds nothing)
    and `test_duplicated_remote_completion_observed_twice_imports_once` (two
    poll ticks both observe `SUCCEEDED`).
- **D7 — Provider switch (AC-4).** On a synthetic fixture source, run a local
  fake adapter, then import an Apify fixture with the same keys and scope, then
  switch back to local.
  - Listing pks are identical, snapshot history is continuous, and no listing is
    created.
  - `WatchEvaluation` rows persist.
  - Covered by `test_provider_switch_preserves_identity_history_and_watch_state`.
    Revision 5 (owner §29) adds assertions to it: the price history across the
    switch is contiguous (every snapshot, local and imported, on the same
    listing pk, ordered by `observed_at`), and no `external_run_id`, dataset id,
    or `import_idempotency_key` appears in any `Listing` or `OfferSnapshot` key
    column.
- **D8 — Truncation and emptiness (AC-5, ADR 0021 :100).**
  `test_truncated_actor_fixture_cannot_delist` runs end to end through import;
  so does the timed-out case. Revision 5 parametrizes it over the truncation
  reasons [item, page, time, bytes] and asserts the stored
  `provider_run.truncation_reason`. Also revision 5 (owner §29):
  `test_partial_failure_and_failed_actor_fixtures_cannot_delist` and
  `test_contradictory_report_rejected_and_breaks_continuity`. Two more tests
  also run end to end:
  - `test_complete_empty_run_delists_scope_end_to_end`: the complete-empty
    fixture passes classification, passes the zero-record parser-rot guard, and
    delists only its own scope's listings as `ABSENT_FROM_SWEEP`.
  - `test_ambiguous_empty_run_cannot_delist` and
    `test_nonempty_unusable_run_is_rejected_and_cannot_delist`: each FULL
    run is rejected, delists nothing, and breaks continuity for its scope.
    The PROBE counterparts are in D12.
- **D6–D8 landed 2026-09-25** in `tests/db/test_apify_import.py` (the file
  the traceability rows name), end to end through `import_provider_run` over
  the real `ApifyClient` with `httpx.MockTransport`; the local legs of D7 run
  through `run_collection`. D5's poll job was on a parallel leg, so each "poll
  tick" in D6 is one `import_provider_run` call. MS2-D-11's
  `ProviderRunEvidence.truncation_reason` landed with them, as a documented
  deviation: it is forbidden for local and non-truncated evidence but
  **optional** for non-local truncated evidence, because the frozen remote
  fakes in `test_collection_provider.py` build that evidence without one. The
  `provider_run_truncation_reason_coherent` CHECK enforces the requirement, and
  both Apify evidence builders copy the reason from the classification.
- **D10 — Durable staged import (MS2-D-22, MS2-D-23).** Extract the stage
  functions from `run_collection`. The frozen pipeline tests must stay green.
  Then implement the importer state machine.
  - **Local transactions (Binding; revision 10, entry gate, ED-04).** D10
    introduces transactions to the local path, which today has none (MS2-D-22
    *Code shape*). The local path composes the stage functions inside two
    transactions, each run within one `sync_to_async` call so that no
    transaction spans an `await`:
    1. *persist*: the scope-row locks, listing observation and creation,
       snapshots, and raw payloads;
    2. *delist*: the continuity record or break, the scoped delist, and the
       complete-sweep watermark raise.

    Resolution and evaluation stay outside both, as today. Both transactions
    follow the MS2-D-35 lock order and in-memory retry. A crash mid-persist
    rolls back the whole local batch, where today the rows before the crash
    stay persisted; the next poll repairs it. A crash between the two
    transactions leaves observations without absence, which fails toward
    keeping listings active, and the next complete sweep delists. These frozen
    files must stay green unmodified: `tests/db/test_pipeline.py`,
    `tests/db/test_source_ebay.py`, `tests/db/test_collection_provider.py`,
    and `tests/db/test_poller_jobs.py`. The Slice A remote fakes in
    `test_collection_provider.py` drive non-local evidence with a scope-less
    `DelistScope` through `run_collection`. `run_collection` keeps accepting
    that as a test-only path, even though the real importer is always scoped
    (MS2-D-13, ED-03).
  - Tests (`tests/db/test_apify_import.py`, crash injection by raising a
    `BaseException` subclass from a patched stage boundary):
    - `test_crash_after_observation_commit_resumes_without_duplicates`;
    - `test_crash_during_resolution_resumes_and_evaluates`;
    - `test_crash_during_evaluation_resumes_and_evaluates`;
    - `test_crash_before_finalization_finalizes_once` (the lifecycle outcome is
      applied once, and the `ScraperRun` goes SUCCESS once).

    Each asserts that no rows are duplicated, the same `ScraperRun` pk is
    reused, and the `WatchEvaluation` ends bound to the imported snapshot.
  - Restart recovery for each state (`test_apify_poll_job.py`):
    `test_restart_recovers_each_unfinished_import_state` (parametrized over the
    four intermediate states) and
    `test_restart_retries_storage_cleanup_after_finalized_import`.
  - Read caps (MS2-D-32), in `test_apify_import.py`:
    `test_stage1_process_loss_rereads_count_against_read_cap` (revision 11,
    R10-10, renames revision 3's `test_stage1_rollback_retry_counts_each_dataset_read`:
    each simulated process loss before the stage-1 commit makes the next
    invocation's read a counted read, and an in-process retry never does)
    and `test_read_cap_exhausted_rejects_import_and_cleans_up`.
  - In-memory retry (MS2-D-35, revision 11, R10-10), in
    `test_apify_import.py`:
    - `test_forced_retryable_sqlstate_retried_in_memory_without_reread`,
      parametrized over `40P01`, `40001`, and `23505` injected into the
      stage-1 and stage-2 transactions: the retry commits,
      `dataset_read_count` stays 1, and the committed counts and
      `import_listing_ids` equal a clean single attempt's, with nothing
      doubled from the aborted attempt;
    - `test_retry_exhaustion_leaves_no_partial_effects_and_next_tick_rereads_counted`:
      four injected aborts leave no new `Listing`, `OfferSnapshot`, or
      `RawPayload` row, keep `import_state` unchanged, record
      `stage_detail.retry_exhausted` and a backed-off `next_attempt_at`,
      and the next tick's stage 1 increments `dataset_read_count` to 2;
    - `test_local_persist_retry_exhaustion_rolls_back_like_a_crash`.
  - Dataset page size (R10-08), in `test_apify_import.py`:
    `test_max_size_valid_batch_imports_without_tripping_response_cap` (500
    rows, each at the schema's worst-case serialized size, served in pages of
    `page_limit`; the import finalizes and no latch trips). Its E-side
    counterpart, `test_apify_ledger.py::test_over_cap_control_response_trips_latch`,
    is owned by E4.
  - Ordering guards (MS2-D-30), in the new file
    `tests/db/test_apify_import_ordering.py`:
    - `test_reverse_completion_older_import_does_not_overwrite_newer_listing_state`.
      Remote runs R1 (started t1) and R2 (started t2 > t1) cover the same
      listings with different title, condition, international flag, and
      retention expiry. R2 finalizes first, then R1 imports. The current
      columns keep R2's values, and `last_observed_at == t2`. R1's snapshots
      are appended as history, and the latest snapshot is still R2's.
    - `test_older_import_does_not_revive_listing_delisted_by_newer_evidence`.
    - `test_older_complete_import_does_not_delist_listing_observed_by_newer_run`.
      This includes a listing first seen by the newer run.
    - `test_delayed_remote_import_after_switch_back_to_local_preserves_newer_content_and_delist_state`.
      A remote run is admitted at t0, then the source switches back to local.
      A local complete sweep at t1 changes listing X's title and delists
      listing Y. The t0 import then completes: X keeps its t1 content, and Y
      stays delisted with its `delisted_at` unchanged. The test is
      parametrized over a delete-on-delist class, where Y also stays redacted,
      and a merchant-fact class. Nothing the local sweep observed is delisted.
      Revision 4 adds one assertion: X's `expires_at` and Y's content columns
      are also unchanged (MS2-D-35).
    - `test_legacy_listing_without_watermark_accepts_first_observation`.
    - Revision 4 (MS2-D-35, N-01 residual), in the same file:
      - `test_delayed_observation_after_newer_delist_restores_no_content_and_keeps_expiry`
        (timeline 1). Listing X is observed at t0 and delisted at t2 by a
        complete sweep. An import observed at t1 (t0 < t1 < t2) then
        completes. X stays delisted with `delisted_at == t2`, its content
        columns and `expires_at` are unchanged, and `last_observed_at` stays
        t0. Parametrized over a delete-on-delist class, where X also stays
        redacted (`is_content_redacted()` holds, as in the existing
        switch-back test), and a merchant-fact class. Also parametrized over
        the t2 delist coming from a complete sweep or from stale absence. The
        stale case raises no scope watermark, so it isolates
        `last_absence_at`.
      - `test_delayed_import_of_unknown_key_after_newer_complete_sweep_creates_no_active_listing`
        (timeline 2). A complete sweep of scope S at t2 omits key K, which has
        no row. The t1 import then creates K already delisted
        (`ABSENT_FROM_SWEEP`, `delisted_at == last_absence_at == t2`),
        never active. Parametrized over S as a non-null scope and as the NULL
        scope, and over a merchant-fact class (the t1 snapshot is kept as
        history) and a bounded delete-on-delist class (the row is redacted and
        no snapshot is written). A later current-eligible observation at t3
        relists the same pk.
      - `test_delayed_observation_cannot_relist_row_delisted_before_newer_complete_sweep`:
        a row delisted at t0.5, a complete sweep of its scope at t2, then an
        import observed at t1. The row stays delisted.
      - `test_stage1_creation_and_newer_complete_sweep_serialize_on_scope_row`:
        two threads interleave the t1 import's stage 1 with the t2 sweep's
        delist stage under the scope-row lock. In both commit orders, K ends
        delisted.
    - Revision 10 (entry gate), in the same file:
      - `test_concurrent_scope_move_is_not_delisted_by_other_scope_sweep`
        (ED-06): scope S′'s stage 2 selects X as a candidate while a
        current-eligible import on scope S moves X into S at t1 < t2 and
        commits. X is not delisted, and it keeps S's content.
      - `test_overlapping_scopes_sharing_listings_do_not_deadlock_or_consume_read_cap`
        (ED-05): two stage-1 transactions on disjoint scope sets share
        listings, and a local NULL-scope persist overlaps a scoped import.
        All commit with no deadlock escaping the in-memory retry, and
        `dataset_read_count` is 1 for each import.
      - `test_run_outcome_and_stage1_interleave_without_deadlock` (ED-05):
        two threads interleave `apply_run_outcome(FULL)` with a stage-1
        transaction on the same source.
      - `test_delayed_import_bumps_last_seen_only_forward_and_only_when_current_eligible`
        (ED-02, R23).
  - `last_seen` (MS2-D-30, ED-02), in the new file
    `tests/db/test_persist_observation_ordering.py`:
    - `test_current_eligible_observation_bumps_last_seen_and_ineligible_does_not`
      (import path and local path);
    - `test_listing_observed_in_previous_run_is_not_stale_delisted_within_grace`
      (a local truncated sweep N+1 inside the grace after run N saw the
      listing: zero delists).
  - Local transactions (ED-04), in `tests/db/test_pipeline_transactions.py`:
    `test_local_crash_mid_persist_rolls_back_whole_batch_and_next_poll_repairs`.
  - Snapshot retention (MS2-D-37, M-01), in the new file
    `tests/db/test_persist_observation_retention.py`:
    - `test_reverse_order_bounded_observation_snapshot_keeps_its_own_deadline`.
      A bounded fixture source has a 6 h class. The t1 observation is applied
      first, then t0 < t1. The listing keeps t1's state and
      `expires_at == t1 + 6 h`. The t0 snapshot has `expires_at == t0 + 6 h`.
      After the clock passes t0 + 6 h, the purge sweep removes the t0
      snapshot while the listing and the t1 snapshot remain.
    - `test_older_bounded_observation_after_newer_delist_is_not_snapshotted`:
      the newer delist time is in the past, so no t1 snapshot row exists.
    - `test_merchant_fact_older_observation_appends_history_with_null_expiry`.
    - `test_append_snapshot_default_copies_listing_retention`: the
      compatibility default is byte-identical to today's behavior.
  - Per-scope continuity (MS2-D-31), in the new file
    `tests/db/test_scope_continuity.py`:
    - `test_scope_a_continuity_does_not_authorize_first_incomplete_scope_b_sweep`,
      parametrized with scope B as a non-null scope and as the legacy NULL
      scope. Scope A is swept completely for longer than the grace. Then the
      first incomplete local sweep of scope B runs with a short grace.
      Expected: zero delists, and B's continuity starts at that sweep.
    - `test_remote_scope_a_runs_then_local_incomplete_scope_b_sweep_cannot_stale_delist`:
      the same scenario with remote runs for A, then a switch to the local
      provider.
    - `test_run_not_sweeping_null_scope_breaks_null_scope_continuity`.
    - `test_older_eligible_sweep_does_not_move_scope_watermark_back`.
    - `test_legacy_null_scope_only_runs_keep_todays_continuity` (a
      behavior-preservation control).
    - Revision 4 (MS2-D-36, N-02 residual):
      - `test_delayed_null_scope_run_after_newer_scoped_run_cannot_restore_continuity`.
        NULL-scope continuity is established. A GPU-only FULL run at t_g
        breaks it (`continuity_broken_at == t_g`). An older NULL-scope sweep
        observed at t_n < t_g then reaches its continuity record: continuity
        stays null. Then a local incomplete NULL sweep runs, with a short
        grace and a listing aged past it. Expected: zero stale delists, and
        `continuous_since` equals that local sweep's time. Revision 10 (entry
        gate, ED-03): the importer cannot produce a NULL-scope remote run
        (`scope_key` is NOT NULL), so the delayed NULL-scope sweep is reached
        in one of two ways. Either the extracted stage-2 continuity function is
        called for the NULL scope with the older event time t_n, or a
        heartbeat-fired local FULL run is interleaved with the FULL-lane run.
        The test is parametrized over both. The rule under test is unchanged.
      - `test_overlapping_local_full_runs_serialize_on_lane_row` (revision
        10, ED-13): a heartbeat-fired FULL run and the FULL-lane run of one
        source overlap. `continuous_since` is never lost to an unlocked
        update, and the older sweep's stale absence is skipped.
      - `test_eligible_sweep_older_than_break_is_noop`, parametrized over
        the NULL scope and a non-null scope.
      - `test_late_older_break_still_breaks` (fail-safe direction).
    - Scope complete-sweep watermark (MS2-D-35):
      `test_only_gated_complete_full_scopes_raise_complete_sweep_watermark`,
      parametrized over complete, complete-empty, truncated, stale-absence,
      PROBE, and rejected runs.
  - Landed 2026-09-25: `acquisition/stages.py` and
    `acquisition/apify/importer.py` with the tests above except the two
    `test_apify_poll_job.py` restart tests, which land with D5.
    `Listing.mark_delisted` raises `last_absence_at`.
- **D11 — Source retention and storage cleanup (MS2-D-25).**
  - Tests (`tests/db/test_apify_retention.py`):
    - `test_bounded_source_import_retains_listings_snapshots_raw_and_evaluations`:
      a synthetic bounded fixture source with a 6 h TTL class. After import,
      every listing, snapshot, raw payload, and watch evaluation carries the
      class and `expires_at`. After expiry the purge sweep removes all of them.
    - `test_unregistered_source_import_rejected_not_merchant_fact`.
    - `test_registry_matches_every_local_adapter_retention`.
  - Tests (`tests/db/test_apify_storage_cleanup.py`):
    - `test_cleanup_after_successful_import`;
    - `test_cleanup_after_rejected_import`;
    - `test_cleanup_after_failed_remote_run`;
    - `test_cleanup_of_abandoned_run_at_deadline`;
    - `test_deadline_before_stage1_rejects_import_and_deletes_storage`;
    - `test_cleanup_failure_retries_with_backoff`;
    - `test_bounded_source_deadline_is_half_ttl`;
    - revision 3 (MS2-D-33, F-10 residual):
      - `test_deadline_is_anchored_at_admission_not_terminal_observation`:
        terminal status first observed long after admission leaves
        `storage_cleanup_due_at` unchanged.
      - `test_restart_after_source_ttl_rejects_expired_content_before_persistence`:
        a bounded fixture source is used, and the poller is "down" (clock
        moved) past the source TTL. On restart the import is rejected with
        `retention_expired` or `storage_deadline`. There are no new
        `Listing`, `OfferSnapshot`, or `RawPayload` rows, and selector 3
        deletes the storage.
      - `test_unobserved_run_past_deadline_is_aborted_and_cleaned`: the row
        still says `RUNNING` because termination was never observed. Past the
        deadline, selector 3 aborts, deletes both storages, and rejects the
        import, with no selector-1 observation.
      - `test_timeout_exceeding_retention_window_denied`.
      - `test_bounded_source_actor_start_denied`.
    - revision 10 (entry gate):
      - `test_overdue_abort_error_or_nonterminal_read_retries_and_deletes_only_after_terminal`
        (ED-15; revision 11, R10-09, narrows its cases to "neither response
        proves termination"): a non-2xx abort with a non-terminal or failed
        read retries; a 2xx non-terminal abort with a non-terminal read
        retries; each attempt increments `storage_cleanup_attempts`, and
        nothing is deleted.
      - revision 11 (R10-09):
        `test_non2xx_abort_with_terminal_read_proceeds_to_delete_in_same_attempt`
        and
        `test_terminal_abort_response_proceeds_to_delete_without_confirming_read`
        (the mock `GET` would fail or answer non-terminal, and the test
        asserts it is never sent). In both, the terminal observation is
        persisted, deletion runs in the same attempt, and
        `storage_cleanup_attempts` is 1.
      - `test_delete_404_counts_as_deleted_only_when_rule_enabled` (ED-19):
        with `…_DELETE_404_IS_ABSENT` false, a 404 is a failed attempt and
        leads to `delete_failed` at the cap; with it true, a 404 on the run's
        own recorded storage id is `deleted`.
      - `test_run_poll_cap_stops_polling_and_settles_at_bound` (ED-01).
  - Landed 2026-09-25: `acquisition/apify/storage_cleanup.py` holds the
    selector-2 cleanup unit and the selector-3 overdue unit, now the
    `apify_poll_tick` defaults. An attempt is counted and committed before
    its first call; a failed attempt records its errors and backs off from
    the attempt count (it does not raise); per-storage progress lives in
    `stage_detail.storage_verified_deleted`. At
    `HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS` (10) the row is `delete_failed` and
    both selectors drop it. `HW_RADAR_APIFY_DELETE_404_IS_ABSENT` (false)
    is the 404 rule. The overdue unit rejects a still-`pending` import
    before its calls (`_reject(only_from=PENDING)`), which also settles a
    lost-response PROBE for D12's one-outstanding rule. `orphaned_start` is
    the column `provider_run.orphaned_start_at`, added to the undeployed
    `0021`. The reject reasons keep D10's landed names
    (`storage_deadline_passed`, `content_past_ttl`) for this section's
    `storage_deadline` and `retention_expired`. The poll-cap test covers
    the D side (polling stops; selector 3 cleans the row without spending
    polls). Settlement at the bound (`bound_unfinalized`) and
    `final_charge_op_at` are E's.
- **D12 — Provider-dispatched recovery probes (MS2-D-24).**
  - Tests (`tests/db/test_apify_recovery_probe.py`; admission is bound to a
    test-only `AllowAllAdmission`, and production stays deny-all):
    - `test_local_success_cannot_clear_actor_provider_failure`: a paused
      apify-provider source also has a registered local fake that would succeed.
      The local fake's `fetch` is never called, and the source stays paused.
    - `test_budget_admitted_actor_probe_recovers_source`: a PROBE fixture import
      finalizes `complete` and the source is reactivated.
    - `test_actor_probe_never_delists_or_touches_continuity`.
    - `test_rejected_probe_records_probe_failure_and_leaves_continuity_unchanged`
      (revision 3, F-07 residual). It is parametrized over invalid `OUTPUT`,
      ambiguous-empty output, and storage-deadline expiry. Each case starts
      with a non-null `continuous_since`, both for the lane and for the
      probe's scope row. Each ends with the outcome `PROBE_FAILURE`, both
      continuity values unchanged, zero delists, and the source still paused.
    - `test_partial_failure_probe_keeps_source_paused`.
    - `test_denied_actor_probe_starts_nothing_and_stays_paused`.
    - `test_one_outstanding_probe_per_source`.
    - `test_local_provider_probe_path_unchanged`.
  - Landed 2026-09-25: `recovery_probe_job` in `poller/service.py` branches
    on `collection_provider` before the adapter lookup; an `apify` source
    skips the probe while a PROBE `provider_run` of its site has an undecided
    import (checked before `check_admission`, so no bucket token is spent),
    then calls `start_provider_run(run_kind=PROBE)`. The rule lives in the
    poller, so `jobs.py` is unchanged. A lost-response probe stays
    outstanding until D11 settles it, which fails closed. Importer fix
    (MS2-D-24 *Probe outcome*): stage 5 emitted `PROBE_SUCCESS` for any
    finalized probe, including `partial_failure`; it now emits
    `PROBE_FAILURE` for anything but `complete` or `truncated`. The tests
    also include `test_truncated_probe_recovers_source`. A budget denial is
    only logged until E records it as a ledger row.
- **D9 — Close-out.** This runs last in D (core). Gate; TODO/STATUS. Record
  that live Actor runs remain owner-gated.
  - *Landed 2026-09-25 (`4ba8dce`).* A read-only verifier pass held 10 of the
    11 acceptance bullets below; the storage-deadline bullet holds with the
    exceptions this plan already accepts: a start whose response was lost has no
    storage ids, so selector 3 marks `orphaned_start_at` and rejects the import
    without deleting (R21, E latch); a row reaching `…_MAX_DELETE_ATTEMPTS` becomes
    `delete_failed` (E latch, operator-visible); and overdue cleanup begins at
    the deadline rather than completing by it. Renamed tests: the Actor's
    `test_byte_cap_is_cumulative_across_responses` and
    `test_byte_cap_counts_compressed_raw_bytes` are covered by
    `test_byte_count_is_run_wide_across_responses_including_failed_ones` and the
    `compressed-*` cases of
    `test_byte_cap_counts_wire_bytes_and_stops_on_first_crossing_chunk`. D11
    asserts the reject reasons D10 names (`storage_deadline_passed`,
    `content_past_ttl`). Live Actor runs remain owner-gated.

**Acceptance:**
- AC-4, AC-5, and AC-6 are proven against fixtures.
- Every import crash window resumes to exactly one finalized result, and the
  result is evaluated.
- Remote retention never defaults, and Apify storage is deleted by an
  absolute deadline in every run outcome, whether or not termination was
  observed.
- A delayed or reordered import never overwrites newer listing state, writes
  content to or revives a listing that newer absence retired, creates an
  active listing for a key a newer complete sweep omitted, or delists a newer
  observation.
- Every snapshot carries its own observation's retention deadline.
- One scope's continuity never authorizes another scope's stale absence, and
  an older run never restores continuity that a newer break ended, on any
  scope.
- A rejected probe records `PROBE_FAILURE` and leaves continuity unchanged.
- Recovery probes honor the selected provider.
- A complete-empty result is distinguishable from a failed one.
- No code path can start a live run (deny-all).
- The contract schemas are committed once, in the Actor's directory, and both
  hw-radar's models and the Actor's output are tested against them (revision 5,
  MS2-D-14, -38).

## Slice E — Apify spend ledger and admission

**Scope:** MS2-D-17, -26, -32, -34, the E-side latch wiring of -33,
(revision 5) -40 and -41, (revision 6) -45 and -46, and (revision 7) -47
(revision 8 revises -23 closure, -34, -45, and -46 without new IDs).

**Files:**
- new `acquisition/apify/budget.py`;
- `catalog/models/provider.py` (`ApifySpendReservation`, `ApifyBudgetLatch`,
  and, revision 5, `ApifyBudgetCycle`);
- migration `0022`;
- `acquisition/apify/jobs.py` (bind the real admission; the reconcile unit in
  the outstanding selector);
- settings keys (values are not secret):
  - `HW_RADAR_APIFY_ENABLED`, default false;
  - the unit prices `…_USD_PER_CU`, `…_DATASET_*`, `…_KV_*`, and
    `…_TRANSFER_USD_PER_GB`. Revision 10 (ED-01): the transfer setting holds
    the higher of the verified external and internal prices, because every
    byte is priced direction-agnostically (MS2-D-26). They carry no live default; each carries its URL
    and date;
  - `…_MARGIN`, `…_ESTIMATOR_VERSION`, `…_MAX_TIMEOUT_S`, and
    `…_STORAGE_CLEANUP_MAX`;
  - revision 5 (MS2-D-40, -41, -43): `…_CYCLE_TARGET_USD` (12.00, validated
    ≤ 12.00), `…_OPERATOR_ALLOWANCE_USD` (1.00, the owner's setting, revision
    9, OQ29; revisions 5–8 assumed 0.50), `…_WATCH_REFRESH_RESERVE_USD`
    (3.00), `…_ACCOUNT_MARGIN_USD` (default 10% of the observed prepaid
    credit), `…_CASH_CEILING_USD` (20.00), `…_ACCOUNT_SNAPSHOT_MAX_AGE_S`
    (900), `…_CYCLE_BOUNDARY_GUARD_S` (3600), `…_USAGE_SETTLE_DELAY_S` (10),
    `…_POST_RUN_COST_MODE` (`bound`), `…_ACTOR_ID` (no default), and
    `…_ACTOR_BUILD` (`prod`);
  - revision 6 (MS2-D-40, -41, -45, -46): `…_EXTERNAL_LIABILITY_USD` (**5.00**,
    the owner's setting, revision 9, OQ26, R33 resolved; applied only when the
    variable is absent, while an explicitly empty or invalid value denies all
    paid admission; revision 6's "no default" is withdrawn),
    `…_USAGE_INCLUSION_LAG_S` (no default; unset means no inclusion
    watermark), `…_RUN_USAGE_SETTLEMENT` (`bound`), `…_USAGE_STABLE_READS` (2),
    `…_USAGE_STABLE_INTERVAL_S` (60), `…_USAGE_FINALIZE_DEADLINE_S` (86400),
    `…_LEDGER_ID` (no default), `…_OPERATOR_BUILD_BOUND_USD` (0.41), and
    ~~`…_OPERATOR_INSPECT_BOUND_USD` (0.01)~~ (withdrawn in revision 7).
    `…_USAGE_SETTLE_DELAY_S` (10) is now only a minimum polling delay;
  - revision 7 (MS2-D-41, -46): `…_CORRECTION_WINDOW_S` (604800),
    `…_OPERATOR_INSPECT_MAX_ITEMS` (1000),
    `…_OPERATOR_INSPECT_MAX_RECORD_READS` (20), and
    `…_OPERATOR_INSPECT_MAX_BYTES` (10 MB);
  - revision 10 (entry gate; MS2-D-25, -32): `…_MAX_API_RESPONSE_BYTES`
    (262144), `…_MAX_RUN_POLLS` (60), `…_MAX_CORRECTION_READS` (12),
    `…_MAX_ACCOUNT_READS_PER_CYCLE` (3000), and `…_DELETE_404_IS_ABSENT`
    (false; set true only after the R25 capability probe records a 403 for
    inaccessible storage). The numeric defaults are assumptions; an unset or
    invalid cap denies live admission (`unbounded_component`);
  - revision 11 (MS2-D-26, -32, -40, -46; R10-01, R10-05, R10-06, R10-08):
    `…_API_CALL_OVERHEAD_BYTES` (262144), `…_MAX_DATASET_PAGE_BYTES`
    (1048576), `…_MAX_DISCOVERY_READS` (24),
    `…_DISCOVERY_READ_INTERVAL_S` (300), `…_OPERATOR_PROBE_MAX_CALLS` (10),
    and `…_CALL_BILLING_RESIDUAL_ACCEPTED`. The last holds the date of the
    owner's recorded acceptance of R38 and defaults to `2026-09-25` (owner
    accepted R38 that day; the default applies only when the variable is
    absent). A present-but-empty value or one that is not a valid date denies
    every paid admission with `call_billing_residual_unaccepted`. The numeric defaults are
    assumptions; an unset or invalid value denies live admission
    (`unbounded_component`). Code constants, not settings:
    `HTTP_RECEIVE_BUFFER_BYTES` (65536), `HTTP_READ_CHUNK_BYTES` (65536),
    `MAX_API_REQUEST_BODY_BYTES` (16384), and `DATASET_PAGE_ENVELOPE_BYTES`
    (1024);
  - withdrawn in revision 5 (owner-overridden by OQ23 and MS2-D-41):
    `…_SAFETY_MARGIN_USD`, `…_CAP_DEDUCTION_USD`, and `…_OVERRUN_TOLERANCE`;
  - revision 3 (MS2-D-32, -33): `…_MAX_DATASET_READS` (3),
    `…_MAX_KV_READS` (3), `…_MAX_DELETE_ATTEMPTS` (10), `…_MAX_KV_WRITES`,
    `…_MAX_KV_BYTES`, `…_IMPORT_MARGIN` (1 h), and `…_STORAGE_MAX_LIFETIME`,
    which has no default and denies live admission while unset. The defaults
    in parentheses are assumptions, except the owner's $12 target and $20 cash
    ceiling and (revision 9) the owner's $5.00 external-liability bound and
    $1.00 operator allowance. (Verified account state 2026-09-24: data retention 31 days, which
    the operator uses to set `…_STORAGE_MAX_LIFETIME`.)

  The revision-1 `…_PER_RUN_OVERHEAD_USD` key is withdrawn.
  - Revision 12 (owner decision (s5, 2026-09-25), R25): new, none with a default (MS2-D-48):
    `…_BILLING_CYCLE_ANCHOR` (ISO-8601 UTC midnight, day 1–28),
    `…_ACCOUNT_LIMIT_USD` (Decimal > 0), `…_ACCOUNT_BASE_PRICE_USD`
    (Decimal ≥ 0), `…_ACCOUNT_DATA_RETENTION_DAYS` (int ≥ 1), and
    `…_ACCOUNT_VERIFIED_ON` (ISO date). Removed: `…_MAX_ACCOUNT_READS_PER_CYCLE`,
    `…_MAX_DISCOVERY_READS`, `…_DISCOVERY_READ_INTERVAL_S`,
    `…_ACCOUNT_SNAPSHOT_MAX_AGE_S`, and `…_USAGE_INCLUSION_LAG_S`.
    `…_ACCOUNT_MARGIN_USD` now defaults to 10% of `…_ACCOUNT_LIMIT_USD`.
- new commands `apify_spend_report` and `apify_budget_reset`; revision 6 adds
  `apify_ledger_claim`, `apify_ledger_handoff` (MS2-D-45), and
  `apify_operator_reserve` (MS2-D-46).

- **E1 — Schema (0022).** Add `ApifySpendReservation` with:
  - `provider_run` OneToOne null (null for denials and, revision 8, for every
    `operator` row, MS2-D-46) and `source_site`;
  - `admission_class` (`watch_refresh | discovery | operator`, revision 6) and
    `status`
    (`reserved | usage_provisional | usage_finalized | reconciled | released |
    denied`, MS2-D-32, MS2-D-41);
  - revision 5: `usage_provisional_usd`, `usage_finalized_usd` and
    `usage_finalized_at` (written once), `post_run_cost_usd` and
    `post_run_cost_mode`, and `denial_reason` values for the MS2-D-40 denials;
  - revision 6: `settlement_basis` (`stable_reads | bound | bound_unfinalized`),
    `settled_run_usage_usd` (raised by upward corrections, never lowered), and
    `operator_kind` (`build | inspect`, operator rows only); revision 7 adds
    `correction_monitor_until`, `next_usage_read_at`,
    `correction_monitor_closed_at`, and, for inspection rows, the envelope
    limits (MS2-D-41, -46, -47). Revision 8 (reviews R5-03, R5-04, R7-01)
    adds `correction_closing_read` (FK to `ApifyUsageRead`, null until the
    closing read commits; set in the same transaction as
    `correction_monitor_closed_at`) and `provider_build_id` (nullable, unique
    when set, written once; a CHECK allows it only on `operator_kind = build`
    rows, and a build row cannot become `reconciled` without it). On a build
    row, `settled_run_usage_usd` holds the settled build usage. No new table.
    Revision 5's
    "written once" `usage_finalized_usd` is kept as the first finalized figure;
    the immutable history lives in `ApifyUsageRead`;
  - revision 11 (R10-03, R10-06): `monitoring_bound_usd` (Decimal 10,4, set
    at admission; 0 for inspection and probe rows) and
    `monitoring_charge_last_at` (nullable; stamped by every selector-4 read's
    pre-send increment, MS2-D-34 *Monitoring charges*); `operator_kind` gains
    `probe` (MS2-D-46), and a probe row carries its envelope limit like an
    inspection row;
  - `estimate_usd` and `actual_usd` (Decimal 10,4); `execution_bound_usd` and
    `post_run_liability_usd` (Decimal 10,4, MS2-D-32); a `component_bounds`
    JSON breakdown; `estimator_version`; `reserved_at` (admission time,
    provenance only, never changed); `reconciled_at`; `last_charge_at`
    (nullable, set at reconciliation, the window anchor, MS2-D-34); and
    `denial_reason`;
  - indexes on `reserved_at`, `status`, and `(status, last_charge_at)`.
  - `ApifyBudgetLatch` (MS2-D-26): `tripped_at`, `provider_run` null, `reason`,
    `cleared_at`, `cleared_reason`, and `estimator_version`.
  - `ApifyBudgetCycle` (revision 5, MS2-D-40): `cycle_start` (unique),
    `cycle_end`, `allocation_usd`, `account_prepaid_credit_usd`,
    `account_base_price_usd`, `account_limit_usd`, `account_usage_usd`,
    `account_observed_at`, and `opened_at`. It is not retention-bearing (no
    merchant content). Revision 10 (entry gate, ED-01, ED-09) adds
    `account_read_count` (integer, default 0) and
    `account_data_retention_days` (the latest observed value). Operator build
    reservations carry their own `run_poll_count` and
    `correction_read_count` (integer, default 0), because they have no
    `provider_run` (MS2-D-32, -46).
  - Revision 12 (owner decision (s5, 2026-09-25), R25): no schema change (MS2-D-48 *Schema*). The
    `ApifyBudgetCycle.account_*` columns and `account_read_count` stay
    unwritten, and `ApifyCycleDiscovery` stays empty.
  - Revision 11 (R10-05): `ApifyCycleDiscovery` (MS2-D-32 *Cycle
    discovery*): `opened_at`, `read_count` (integer, default 0),
    `last_read_at` (nullable), `closed_at` (nullable), `cycle_start`
    (nullable; the cycle it found), and `close_reason` (`discovered |
    owner_reset`). A partial unique index allows one row with a null
    `closed_at`. It is not retention-bearing.
  - Revision 6: `ApifyUsageRead` (append-only evidence, MS2-D-41):
    `provider_run` null, `reservation`, `read_at`, `usage_total_usd`,
    `usage_usd` JSON, `finished_at_reported`, and `price_settings_version`; no
    update or delete path. Revision 8: a build read has a null `provider_run`
    and identifies its build through `reservation.provider_build_id`.
    `ApifyLedgerAuthority` (MS2-D-45) with the fields
    listed there (revision 7 adds `handed_off_to` and
    `handoff_record_digest`), unique on `cycle_start`; the imported record's
    digest is unique.
  - Landed 2026-09-25: the six models in `catalog/models/provider.py` and
    `0022_apify_spend_ledger` (tables `apify_spend_reservation`,
    `apify_usage_read`, `apify_budget_latch`, `apify_budget_cycle`,
    `apify_cycle_discovery`, `apify_ledger_authority`), with
    `monitoring_call_pending_since` (MS2-D-34, R10-03). Choices the plan left
    open: `source_site` is nullable (operator rows) but required on runtime
    rows; `estimate_usd` and `monitoring_bound_usd` are NULL only on a
    denial (a `pricing_unverified` denial has no estimate); `denial_reason`
    and the latch `reason` are free text with a denied ⇔ non-blank CHECK, not
    a closed vocabulary, because E2 emits the reasons and the kill-switch
    reason is unnamed; the inspection envelope is
    `envelope_max_items|_record_reads|_bytes` and the probe's is
    `envelope_max_calls`; Apify's account figures and `usage_total_usd` keep
    `ProviderRun.usage_total_usd`'s (14,8), and hw-radar's money is (10,4);
    the one-open discovery row is a partial unique index on `close_reason`
    where `closed_at` is NULL. The schema holds single-row invariants only;
    write-once rules (`usage_finalized_usd`, `provider_build_id`,
    `provider_run.final_charge_op_at`) and `ApifyUsageRead`'s append-only
    rule are E3/E4 service rules. Tests: `tests/db/test_apify_budget_schema.py`,
    `tests/db/test_apify_ledger_migration.py`.
- **E2 — Pure policy.** Implement `estimate_run_cost` (the MS2-D-26 component
  sum) and `decide_admission`.
  - Before writing the price defaults, re-verify every unit price on the
    official pricing page and record the URL and date. A unit price that
    cannot be verified keeps live admission denied (`pricing_unverified`).
    Revision 10 (entry gate, ED-01) withdraws "re-verify the direction
    semantics of data transfer; if they cannot be verified, keep live
    admission denied". The docs cannot settle that question, or whether
    `GET` polls and account reads bill. Only a live run can, and F5a is that
    run, so gating on it was circular. Transfer is instead priced on every
    byte at `max(external, internal)` (MS2-D-26), and API calls at the
    API-call bound (MS2-D-32). E2 verifies only what the docs can show: the
    unit prices. F5a measures the rest, and a lower bound needs a plan
    revision citing F5a evidence.
  - Tests (`tests/unit/test_apify_budget.py`):
    - the component formula;
    - `test_exact_boundary_admitted_and_epsilon_over_denied` (equality at the
      class cap is admitted; +0.0001 is denied);
    - revision 5 (owner §29 budget list; MS2-D-40):
      `test_reservation_exactly_equal_to_remaining_is_admitted`;
      `test_one_cent_over_remaining_is_denied`;
      `test_cycle_bounds_come_from_account_limits_not_calendar`;
      `test_account_headroom_below_project_target_binds` (the account's
      remaining prepaid allowance is smaller than the target);
      `test_project_target_below_account_headroom_binds`;
      `test_admission_never_relies_on_overage` (an account limit raised above
      the prepaid credit adds no admissible headroom);
      `test_plan_base_price_above_cash_ceiling_denies_all`;
      `test_stale_or_unreadable_account_snapshot_denies`;
      `test_target_setting_above_twelve_rejected`;
      `test_trailing_window_is_report_only`;
    - revision 6 (R5-02): ~~`test_unset_external_liability_denies_all_paid_admission`~~
      (replaced in revision 9, OQ26, by the two tests that follow it);
      `test_empty_or_invalid_external_liability_denies_all_paid_admission`
      (the variable present but empty, non-numeric, negative, or non-finite:
      every class, `operator` included, is denied with
      `external_liability_unbounded`, and the 5.00 default is never
      substituted);
      `test_default_external_liability_admits_only_when_invariant_holds`
      (the variable absent: the bound is 5.00. The fixture isolates check 2 by
      setting the Hardware Radar target above `P − 5.00` and leaving check 1
      slack, because with the verified $19 credit and $1.90 margin the $12
      target binds first (R34); in that fixture a reservation with
      `HR_cycle + estimate + 5.00 = P` is admitted, one cent more is denied,
      and a stale snapshot still denies);
      `test_external_liability_consumes_share_before_target`;
      `test_concurrent_external_consumption_within_declared_bound_cannot_push_account_past_prepaid`
      (a declared bound E, an admitted run, then other workloads consume up to
      E before the run completes: the sum stays ≤ `P`);
      `test_observed_external_consumption_above_bound_denies_and_trips_latch`;
      `test_straddling_reservation_checked_against_both_cycles_external_bound`;
    - revision 6 (R5-05): `test_operator_reservation_counts_against_operator_class_and_account_checks`;
      `test_operator_allowance_exhausted_refuses_reservation`;
    - revision 9 (OQ29): `test_default_operator_allowance_fits_two_build_bounds_not_three`
      (at the 1.00 default and the 0.41 build bound, two unsettled build
      reservations are admitted and a third is refused with
      `operator_allowance_exhausted`; the runtime allocation `A` is 11.00);
    - revision 7 (R5-05): `test_inspection_envelope_priced_from_unit_price_settings`;
      `test_inspection_envelope_denied_when_a_price_is_missing`;
      `test_inspection_settles_at_full_envelope_never_below`;
    - withdrawn with the OQ23 deduction (revision 5):
      `test_safety_margin_and_oq23_deduction_lower_hard_cap` and
      `test_unset_cap_deduction_denies_live_admission`;
    - `test_missing_unit_price_denies_live_admission`;
    - `test_estimate_includes_capped_reads_deletes_and_storage_lifetime`
      (MS2-D-32): the reservation grows linearly with
      `…_MAX_DATASET_READS`, and it prices storage over `storage_hours`
      (revision 11: `max(…_STORAGE_MAX_LIFETIME, 744 h + 2 × guard)`), not
      over the cleanup deadline;
    - `test_unset_storage_lifetime_denies_live_admission`
      (`unbounded_component`);
    - revision 10 (entry gate):
      `test_transfer_priced_on_every_byte_at_higher_direction_price` and
      `test_api_calls_priced_at_bound_without_billing_verification` (ED-01:
      with prices set and every cap valid, admission is granted with no
      poll-billing or transfer-direction fact recorded, and a missing or
      invalid cap denies with `unbounded_component`);
      `test_account_read_bound_is_standing_cycle_debit` (ED-01);
      `test_storage_lifetime_below_data_retention_days_denies` and
      `test_missing_data_retention_days_denies` (ED-09). Revision 11: the
      first of these also needs R38 accepted in its fixture;
    - revision 11:
      `test_per_call_bounds_derived_from_settings_by_endpoint` (R10-01: each
      *Per-call bound* row's formula, recomputed from the settings; none
      hard-coded);
      `test_admission_denied_until_call_billing_residual_accepted` (R10-01:
      unset, empty, or not a date denies every class, `operator` included,
      with `call_billing_residual_unaccepted`; a valid date admits, all else
      equal); `test_transfer_bound_covers_actor_overshoot_of_one_read_and_in_flight_window`
      (R10-02, D1 follow-up);
      `test_estimate_prices_four_calls_per_cleanup_attempt_and_the_start_call`
      (R10-06); `test_probe_envelope_priced_and_denied_when_allowance_short`
      (R10-06); `test_storage_hours_cover_a_full_cycle_when_lifetime_is_shorter`
      and `test_observed_cycle_longer_than_744_hours_denies` (R10-07);
      `test_page_limit_below_one_or_kv_cap_above_response_cap_denies`
      (R10-08);
    - discovery is denied above `A − …_WATCH_REFRESH_RESERVE_USD` while
      watch_refresh is still admitted up to `A` (revision 5, MS2-D-17);
    - outstanding reservations are counted;
    - the kill switch;
    - `test_tripped_latch_denies_everything`;
    - invalid inputs.
  - Landed 2026-09-25 (E2). `acquisition/apify/budget.py` is DB-free, with
    its own `BudgetClass`/`OperatorKind`/`DenialReason` enums: `estimate_run_cost`,
    `estimate_operator_cost`, the per-call bounds, `standing_account_read_debit`,
    `project_allocation`, `settle_envelope`, and `decide_admission` over a
    caller-built `LedgerState` (E3 sums `CycleDebits` from rows; decide adds the
    standing debit itself). The Slice E settings keys are in `settings.py` and
    never raise: invalid → `None` (the account margin → NaN), so admission denies.
    No unit price was re-verified, because none has a default. Each key cites
    `apify.com/pricing` with the plan's retrieval date, and the operator
    re-verifies before setting it. Choices this plan leaves open (E3/E5 review):
    `budget_setting_invalid` for a bad allocation, ceiling, or snapshot-age
    setting, including a target above 12.00; `…_MARGIN`, `…_MAX_TIMEOUT_S`,
    `…_MAX_KV_WRITES`, and `…_MAX_KV_BYTES` have no default, so unset denies with
    `unbounded_component`. `…_ESTIMATOR_VERSION` defaults to `1`, and
    `…_STORAGE_MAX_LIFETIME` is in seconds. Prices are eight keys (`…_USD_PER_CU`;
    dataset and KV reads and writes per 1,000; dataset and KV storage per GB-hour;
    `…_TRANSFER_USD_PER_GB`). The build reservation carries no margin, following
    its formula, and the monitoring allowance is held outside the margin. Carried
    handoff consumption and discovery allowances count against the runtime class
    caps. Both D1 follow-up tests landed:
    `test_apify_contract.py::test_actor_transfer_constants_match_estimator` and
    `test_apify_budget.py::test_transfer_bound_covers_actor_overshoot_of_one_read_and_in_flight_window`.
    E5 hand-off: `jobs.BudgetRequest` must also carry `maxRequests` and
    `maxBytes`, which `RunShape` prices.
- **E3 — Ledger service.** Implement the cycle snapshot (MS2-D-40) and
  `reserve()` under the budget advisory lock, with the revision-5 MS2-D-34
  predicate: a row counts in every billing cycle its charge interval touches,
  and unreconciled rows count at full estimate in every cycle from their
  admission cycle onward.
  - Tests (`tests/db/test_apify_ledger.py`):
    - `test_concurrent_reservations_one_admitted_at_boundary` (two threads);
    - `test_reservation_straddling_cycle_boundary_counts_in_both_cycles`
      (replaces `test_run_spanning_window_boundary_is_counted`);
    - `test_arbitrary_non_calendar_cycle_boundary` (a 5th→4th cycle, and a
      cycle shortened by a plan change);
    - `test_cycle_rollover_carries_unsettled_reservations`;
    - `test_reconciled_spend_leaves_a_cycle_its_charge_interval_does_not_touch`
      (replaces revision 4's `test_reconciled_spend_rolls_off_31_days_after_last_charge`);
    - `test_late_cleanup_settled_spend_counts_in_the_cycle_of_its_final_charge`
      (MS2-D-34, F-08 residual, revision-5 form; replaces revision 4's
      `test_late_cleanup_settled_spend_counts_until_31_days_after_final_charge`).
      A run is reserved two days before a cycle ends and misses its cleanup
      deadline. Deletion succeeds three days into the next cycle, and the row
      is reconciled with `last_charge_at` there. The settled amount counts in
      both cycles, and a new reservation in the second cycle that fits only
      without it is denied;
    - revision 6 (R5-01): `test_repeated_reserve_reconcile_against_one_unchanged_snapshot_keeps_debit`
      (several reserve → reconcile cycles against one snapshot: each settled
      amount stays in the snapshot check, and the admission that would pass
      the prepaid limit is denied);
      `test_refreshed_snapshot_that_still_lags_keeps_reconciled_debit`;
      `test_inclusion_watermark_unset_debits_all_reconciled_cycle_spend`;
      `test_inclusion_lag_set_drops_only_rows_ended_before_watermark`;
    - revision 6 (R5-04, `tests/db/test_apify_ledger_authority.py`):
      `test_admission_denied_without_cycle_authority`;
      `test_handoff_export_refused_while_proof_run_is_running`;
      `test_handoff_export_refused_while_usage_is_provisional_or_unfinalized`;
      `test_handoff_export_refused_with_open_operator_reservation`;
      `test_second_environment_denied_until_handoff_imported`;
      `test_drained_handoff_carries_settled_consumption_into_second_environment`;
      `test_exporting_environment_can_never_admit_again_in_that_cycle`;
      `test_authority_continues_at_cycle_rollover`;
    - revision 7 (R5-04, R6-01):
      `test_handoff_export_refused_while_correction_monitoring_open`
      (an obligation still before its deadline; revision 8 withdraws the
      "impossible by construction" gloss);
      `test_duplicate_export_imported_into_two_ledgers_second_rejected`;
      `test_export_to_second_destination_refused_and_same_destination_retry_idempotent`;
      `test_reimport_of_same_record_is_noop`;
      `test_admission_racing_handoff_export_serializes` (two threads: exactly
      one of "reservation committed, export refused" or "export committed,
      admission denied");
    - revision 8 (R5-04):
      `test_handoff_export_refused_after_deadline_until_closing_read_commits`
      (the outage scenario of E4: on day 8, after the deadline and before the
      restarted poller's closing read, the export is refused; once the closing
      read commits the $1 correction, the export succeeds, its record carries
      $1 and the closing read, and the destination's import counts $1);
      `test_handoff_export_refused_with_read_above_settled_usage` (a seeded
      post-reconciliation `ApifyUsageRead` above the row's settled usage is an
      unapplied correction and blocks the export; a pre-settlement read is not
      counted by this check);
      `test_import_refuses_record_without_closing_read_evidence`;
    - revision 11 (R10-05, MS2-D-32 *Cycle discovery*):
      `test_empty_ledger_bootstrap_read_is_counted_before_it_is_sent` (an
      injected crash after the commit and before the call still leaves
      `read_count` 1);
      `test_exhausted_cycle_read_cap_then_rollover_discovers_next_cycle`
      (the old row is at its cap; after `cycle_end`, discovery finds the new
      cycle, and its allowance counts in both cycles);
      `test_failed_discovery_reads_count_and_stop_at_cap` (transport
      errors count; at the cap admission stays `cycle_unknown` and the report
      shows `cycle_discovery_exhausted`, until the owner's
      `apify_budget_reset --discovery` opens a new row);
      `test_same_cycle_handoff_carries_account_read_and_monitoring_debits`
      (the record carries the source's full standing account-read debit, its
      discovery allowances, and the closed monitoring allowances; the
      destination counts them plus its own account-read debit);
    - `test_cycle_attribution_uses_charge_interval_not_observation_time`
      (MS2-D-39);
    - `test_unreconciled_reservation_never_ages_out`;
    - `test_stuck_reservation_still_counted`.
  - Landed 2026-09-25 (E3). `acquisition/apify/ledger.py`: `reserve` and
    `reserve_operator` take `pg_advisory_xact_lock(BUDGET_LOCK_KEY)` and
    persist the admitted or denied row in the same transaction;
    `refresh_account_snapshot` counts every account and discovery read before
    it is sent; `claim_origin`, `export_handoff`, `import_handoff`,
    `reset_discovery`. Choices this plan left open: `next_cycle` is always
    evaluated (an unreconciled row reaches every later cycle), with the next
    cycle modeled as `(cycle_end, +∞)`; with no cycle row, admission reports
    `cycle_unknown` rather than `ledger_authority_missing`; `continued`
    authority passes only between adjacent observed cycles (gap ≤ guard);
    the snapshot's `account_observed_at` moves only after both the limits and
    plan reads succeed; the handoff record is canonical JSON with a sha256
    digest and money as strings. E1 open question answered: one digest
    column was not enough, because an imported `handoff` row can be exported
    onward and the export would overwrite the import key, so 0022 adds
    `imported_record_digest` (the import key) and `handoff_record` (the stored
    export, for idempotent retry). `denial_reason` stays free text. An
    inspect or probe denial caused by an unset envelope limit is returned
    but not persisted, because the E1 envelope CHECK needs every limit.
    Commands: `apify_ledger_claim`, `apify_ledger_handoff`,
    `apify_operator_reserve` (reserve only), and `apify_budget_reset
    --discovery`; E4 adds `--settle` and the latch reset. The plan's
    shortened-cycle case is
    `test_cycle_shortened_by_plan_change_moves_the_boundary`.
  - Revision 12 (owner decision (s5, 2026-09-25), R25): the discovery, snapshot-refresh, and
    inclusion-watermark tests above (revision 6 R5-01 and revision 11
    R10-05) are deleted or rewritten by E9.2. `refresh_account_snapshot` and
    `reset_discovery` are removed, and `ensure_cycle` materializes the cycle
    from the anchor. The E3 choice "with no cycle row, admission reports
    `cycle_unknown`" still holds.
- **E4 — Reconcile and the overrun latch.** Settle under the same lock per
  MS2-D-32 and MS2-D-41 (revision 7 wording). The first non-null
  `usage_total_usd` makes the row `usage_provisional`. It becomes
  `usage_finalized` only by the MS2-D-41 settlement predicate (stable reads) or
  settles at its bound (`bound` mode, or `bound_unfinalized` at the deadline);
  elapsed time alone never finalizes it. `reconciled` requires a terminal
  import, verified deletion, settled run usage, and the post-run cost (`bound`
  until F5a's measurement, MS2-D-41). Reconciliation sets `last_charge_at` and
  `correction_monitor_until` in the same locked transaction (MS2-D-34,
  MS2-D-41). The reconcile unit joins the MS2-D-23 outstanding selector (2);
  after reconciliation the row moves to the correction-monitoring selector (4).
  Revision 8: the row leaves selector 4 only when a successful closing read at
  or after `correction_monitor_until` commits together with any correction it
  finds (MS2-D-23, MS2-D-47); an elapsed deadline alone never closes it.
  Operator build rows reconcile through the same unit from their bound
  `provider_build_id` (MS2-D-46).
  A null or unsettled value keeps the estimate and is retried. A row unsettled
  at its admission cycle's end is flagged `unreconciled_stale`.
  - Latch trips: an actual above the reservation, non-zero proxy usage, a dataset
    count over `max_items`, a KV store over its byte cap, a start-option
    mismatch (the start job aborts that run), an exhausted delete-attempt cap,
    or an `orphaned_start` (MS2-D-33). Revision 10 (entry gate): "non-zero
    proxy usage" becomes any usage component outside the MS2-D-26 allowlist
    or any unparseable component (ED-10); an observed restart counts as a
    start-option mismatch (ED-20); and an API response over
    `…_MAX_API_RESPONSE_BYTES` trips it (ED-01). Every trip runs in a
    transaction that takes the budget lock first (MS2-D-26 *Serialization*,
    ED-05). While the kill switch is off, this unit and selectors 1–4 keep
    running (MS2-D-17, ED-07).
  - Tests:
    - `test_overrun_latch_denies_admission_until_reset`;
    - `test_estimator_version_bump_clears_latch`;
    - `test_proxy_usage_trips_latch`;
    - revision 10 (entry gate):
      `test_usage_component_outside_allowlist_trips_latch` [`REQUEST_QUEUE_WRITES`,
      `PROXY_SERPS`, `KEY_VALUE_STORE_LISTS`, an unknown key, an unparseable
      value] (ED-10); `test_settlement_ignores_reads_before_final_charge_op_plus_guard`
      (ED-08); `test_correction_read_cap_keeps_obligation_open_and_overdue`
      (ED-01); `test_latch_trip_never_requested_while_holding_provider_run_lock`
      (ED-05); and, in `tests/db/test_apify_poll_job.py`,
      `test_kill_switch_off_still_drains_imports_cleanup_and_closing_reads`
      (ED-07: with `HW_RADAR_APIFY_ENABLED=false`, selectors 1–4 still import,
      delete, settle, and close, while starts, probes, and operator
      reservations are denied);
    - revision 11:
      - R10-03, through the scheduler:
        `test_closing_read_after_cycle_boundary_debits_the_new_cycle` (a run
        reconciled in cycle Y whose closing read is sent in Y+1: Y+1's
        `HR_cycle` includes `monitoring_bound_usd`, and an admission that fits
        only without it is denied);
        `test_monitoring_allowance_carried_through_prolonged_outage` (the
        poller is down across two cycle boundaries; the allowance counts in
        each cycle while a read is still possible, and after the cap it stops
        in later cycles but stays in the cycles its reads touched);
        `test_counted_mode_never_releases_unused_monitoring_allowance_at_reconciliation`;
        `test_correction_deadline_not_moved_by_monitoring_reads`; and
        `test_build_monitoring_read_after_cycle_boundary_is_debited` (the
        same timeline for an operator build row);
      - R10-04: `test_settlement_polls_do_not_move_completion_anchor` (two
        eligible stable reads at about anchor + 3,610 s and + 3,670 s
        finalize, and `final_charge_op_at` is unchanged by every poll) and
        `test_no_read_eligible_before_import_and_deletion_barriers` (with
        cleanup deferred past the deadline, reads are appended but none is
        eligible and no finalize deadline runs until the anchor is set);
      - R10-07: `test_failed_deletion_with_no_later_runs_counts_full_cycle_storage_in_every_cycle`
        (the latch trips, no run follows, and the row counts at its full
        estimate, including a full cycle of storage, in each of three later
        cycles, with retention shorter than a cycle);
      - R10-08: `test_over_cap_control_response_trips_latch` (a `GET` run
        body over `…_MAX_API_RESPONSE_BYTES` raises for that call and trips
        `api_response_over_cap`, while D10's maximum-size valid batch trips
        nothing);
    - `test_dataset_over_cap_trips_latch`;
    - `test_start_option_mismatch_aborts_and_trips_latch`;
    - `test_reconcile_concurrent_with_admission_serializes`: the reconcile
      thread and the reserve thread interleave under the lock, and the admission
      decision sees either the pre-reconcile or the post-reconcile total, never a
      torn one;
    - `test_late_usage_reconciled_on_later_tick`;
    - revision 3 (MS2-D-32, F-08 residual):
      - `test_repeated_pre_commit_reads_are_capped_and_reserved`: stage 1
        loses its process (or, revision 11, R10-10, exhausts its in-memory
        retries) before committing, repeatedly. Each new invocation
        increments `dataset_read_count` before reading, and the reservation
        already covered
        `…_MAX_DATASET_READS` reads. The read after the cap is refused, and
        the import is rejected with `read_cap_exhausted`.
      - `test_delayed_deletion_keeps_storage_liability_outstanding`: cleanup
        fails past the deadline, and the reservation stays counted at its full
        estimate in admission beyond its admission cycle. Once deletion is
        verified and usage re-read, the reservation is `reconciled`.
      - `test_non_null_usage_before_cleanup_completes_does_not_release_liability`:
        usage arrives while the import is at `observations_committed` and the
        storage is `retained`. The status is `usage_provisional` or
        `usage_finalized` (revision 5 names), and admission still counts the
        full estimate.
      - `test_usage_read_before_final_charge_op_does_not_reconcile`;
      - `test_delete_cap_exhausted_trips_latch`;
      - `test_orphaned_start_trips_latch`.
    - revision 5 (MS2-D-41; owner §29):
      - `test_first_usage_read_is_provisional_until_settle_delay`;
      - `test_finalized_usage_written_once_not_recomputed` (revision 6
        meaning: the first finalized figure is recorded once as evidence; a
        later *lower* read changes nothing, while a later higher read is an
        upward correction, below);
      - `test_unsettled_usage_keeps_full_reservation_and_retries`;
      - `test_post_run_cost_uses_bound_until_measured` and
        `test_post_run_cost_counted_mode_uses_operation_counters`;
      - `test_reconcile_below_reservation_returns_capacity`;
      - `test_reconcile_above_reservation_trips_overrun_and_pauses_paid_work`;
      - `test_overrun_never_admits_repair_run_or_dataset_reread`;
      - `test_process_stop_alone_never_reconciles`.
    - revision 6 (R5-03; these replace revision 5's single-read finalization):
      - `test_nonnull_usage_rising_after_ten_seconds_is_not_finalized_early`
        (reads at 10 s and 30 s differ; the row stays provisional at its
        bound, and the later, higher figure is the one settled);
      - `test_stable_reads_predicate_requires_identical_consecutive_reads`;
      - `test_bound_mode_settles_at_max_of_execution_bound_and_reads`;
      - `test_permanently_unfinalized_run_stays_at_bound_and_is_stale_at_cycle_end`;
      - `test_upward_correction_after_reconciliation_raises_settled_and_trips_latch`;
      - `test_later_lower_read_never_returns_capacity`;
      - revision 7 (R5-03), through the scheduler, not a direct call:
        `test_poll_tick_selects_reconciled_cleaned_up_run_for_correction_monitoring`
        (a reconciled run with storage deleted is selected by selector 4, and a
        rising read raises its settled amount);
        ~~`test_correction_monitoring_stops_at_window_end`~~ (withdrawn in
        revision 8: elapsed time no longer closes monitoring);
      - revision 8 (R5-03), through the scheduler:
        `test_outage_spanning_correction_deadline_closing_read_applies_correction`
        (`stable_reads`; run A reserves $2, settles at $0.50 on day 0, and run
        B reserves into the released capacity; the provider raises A to $1 on
        day 6 while the poller is down from day 5 to day 8; the deadline is day
        7. The restarted poller selects the expired, unclosed row, and its
        closing read, in one locked transaction, appends the read, raises A's
        settled amount to $1 in every cycle A's charge interval touches, trips
        the latch with `post_admission_invariant_breach` when B's reuse makes
        an aggregate exceed its limit, and only then closes monitoring);
        `test_failed_closing_read_keeps_obligation_open_and_overdue` (a
        transport error, a missing record, and a null usage total each leave
        `correction_monitor_closed_at` null, back off, and show
        `correction_close_overdue` in the spend report; a later successful read
        closes the row);
        `test_closing_read_rollback_leaves_monitoring_open` (a failure injected
        after the read is appended rolls back the read, the correction, and the
        closure together);
        `test_correction_monitoring_closes_only_after_successful_closing_read`
        (replaces the withdrawn test: before the deadline reads never close;
        the first successful read at or after it does);
      - revision 7 (R6-02):
        `test_below_estimate_upward_correction_after_capacity_reuse_trips_latch`
        (A reserves $2 and settles to $0.50; B reserves into the released
        capacity; A corrects to $1, still below $2: the latch trips with
        `post_admission_invariant_breach`);
        `test_upward_correction_breaching_external_liability_check_trips_latch`;
        `test_correction_attributed_to_charge_interval_cycles_not_read_time`;
      - `test_usage_reads_are_append_only_evidence`.
    - revision 6 (R5-05): `test_operator_build_settles_from_build_cost` and
      `test_unsettled_operator_reservation_counts_at_bound`. Revision 8
      (R7-01) sharpens the first: the settle command only binds the build id;
      the poller reads the build record, settles the row, and sets
      `last_charge_at` to the build's `finishedAt`.
    - revision 8 (R7-01): `test_restart_rereads_reconciled_build_and_applies_upward_correction`
      (a build reservation is bound, settled, and reconciled; a new poller
      process, starting from persisted state only, selects it by
      `provider_build_id`, reads a higher build usage, applies the correction
      under the lock, and re-checks the operator allowance and the other
      MS2-D-47 invariants); `test_operator_reservation_has_no_provider_run`;
      `test_rebinding_a_different_build_id_is_refused`;
      `test_build_row_without_build_id_stays_at_bound_and_never_reconciles`;
      `test_inspection_settle_sets_last_charge_at_and_no_correction_obligation`.
  - Landed 2026-09-25 (E4). `acquisition/apify/reconcile.py` holds settlement,
    selector 4, build binding and settlement, and the envelope settle; the
    latch primitives (`trip_latch`, `clear_latch`, the estimator-version clear
    inside `reserve`) are in `ledger.py`. `jobs.apify_poll_tick` runs the
    selector-2 reconcile unit, bound-build reads, and selector 4, and takes
    `ledger_config` and `clock`. D hand-offs: stage 5, Reject, and the
    verified-deletion commit stamp `final_charge_op_at` once; the latch trips
    on `delete_failed` at the cap, `orphaned_start_at`, a start mismatch, an
    observed restart, a dataset over `max_items`, and an over-cap response,
    each after the row transaction commits. Every test named above is in
    `test_apify_ledger.py` except the kill-switch test and
    `test_start_option_mismatch_aborts_and_trips_latch` (`test_apify_poll_job.py`).
    E3 residuals: 0022 exempts `denied` rows from the envelope CHECK (a
    denied envelope row is persisted with its set limits) and adds
    `ApifySpendReservation.reason`, `probe_dataset_id`, and
    `ApifyCycleDiscovery.reason` (an `owner_reset` close needs it).
    `ApifyUsageRead` refuses update and delete at the model and queryset
    level, and refuses a read whose `provider_run` is not its reservation's.
    Choices this plan left open: `bound` mode settles on the first eligible
    non-null read; the stable-read settled figure is the highest eligible
    read; the latch trip is idempotent per open (reason, run); a stage-1
    re-read is blocked only when `dataset_read_count ≥ 1`; counted post-run
    storage is priced over admission-to-deletion hours and falls back to the
    bound when a price is unset; stale selector-4 markers are resolved at
    every tick against the process start (`jobs.PROCESS_STARTED_AT`), because
    the poller service is outside this slice. Not wired: the KV-store byte-cap
    trip (the importer never sees the OUTPUT record's size; `provider.py`
    would have to expose it).
- **E5 — Wire admission.** Replace `DenyAllAdmission` with the ledger in the
  start job, and derive `budget_paused` into C's freshness.
  - Tests: `test_denied_start_records_denial_and_starts_nothing`;
    `test_denied_start_shows_budget_paused_with_reason_in_shortlist`; a later
    successful import clears it.
  - Landed 2026-09-25 (E5). `jobs.BUDGET_ADMISSION` is `LedgerAdmission`;
    `DenyAllAdmission` is removed, and no permissive binding exists in
    production code. The admission protocol is async
    (`admit(request, reader)`): when `ledger.account_read_useful` says the
    request passes every rule before the account-state checks, it refreshes
    the snapshot (counted reads, no transaction across an await), then calls
    `ledger.reserve(..., source_site_id=...)`, which persists the admitted or
    denied row (probe denials included) and trips the latch on
    `external_liability_exceeded`. `BudgetRequest` gained `source_site_id`,
    `max_requests`, and `max_bytes`; `BudgetDecision` gained
    `reservation_id`, and the start job creates the provider_run and attaches
    it to that reservation in one budget-locked commit. The shortlist
    (`eligibility.shortlist`) has `Freshness.BUDGET_PAUSED` and
    `ShortlistRow.budget_paused_reason`: the latch (reason `overrun_latch`,
    `apify` sources only), or a newest ledger row that is a denial with no
    SUCCESS `ScraperRun` finished after it. With the production defaults
    (kill switch off, no Actor id, no `RUN_SPECS`, no prices) no start
    request and no account read is sent. E4 residuals folded in: the OUTPUT
    record's size is exposed by `provider.py` and trips `kv_store_over_cap`
    above `…_MAX_KV_BYTES`; stale selector-4 markers are resolved once at
    poller start (`poller.service.run`), no longer every tick; `report.py`
    uses `reconcile.correction_close_overdue` and `is_unreconciled_stale`
    (it now flags `unreconciled_stale` rows). Also: an over-cap account read
    trips `api_response_over_cap` instead of reading as a transient failure.
    Choices this plan left open: the snapshot refresh is skipped when a
    setting already denies; "a later successful import" is a SUCCESS
    `ScraperRun` whose `finished_at` is after the denial; `budget_paused`
    overrides `fresh`/`stale`. Bootstrap order: the first enabled start
    discovers the cycle and is denied `ledger_authority_missing`, after which
    `apify_ledger_claim` can claim it.
  - Revision 12 (owner decision (s5, 2026-09-25), R25): `admit(request)` loses its `reader`, and
    `account_read_useful` and the refresh step are removed. "No account
    read is sent" now holds in every configuration, not only with the
    production defaults. The bootstrap order becomes: set the anchor and
    the account settings, then `apify_ledger_claim` (which materializes the
    cycle), then start. A start refused with 402 trips `account_limit_refused`
    (E9.3).
  - Crash windows closed 2026-09-25 (verifier findings d1, d2). (d2) The
    `orphaned_start` and `delete_attempts_exhausted` trips now commit in the
    same transaction as the row mark: `storage_cleanup._begin` and `_finish`
    take the budget lock before the provider_run lock. This supersedes E4's
    "each after the row transaction commits" for these two reasons. Before,
    selectors 2 and 3 dropped the marked row, so a process loss between the
    two commits lost the trip for good. Every tick also runs
    `storage_cleanup.trip_stranded_latches`. It trips orphaned rows,
    `delete_failed` rows, and rows still `retained` at the attempt cap, but
    only when that (reason, run) has never had a trip, open or cleared. The
    owner's reset therefore stays final (R21). (d1) Every tick runs
    `reconcile.release_unattached_reservations`. It releases runtime
    reservations still `reserved` with no provider_run after
    `HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S` (default 900). A
    released row gets `actual_usd` 0 and `reason` `unattached_reservation`;
    operator rows are never released. This complies with MS2-D-32's
    `released` ("a start that never ran") and MS2-D-34's "none is released
    while charge-producing work remains". By MS2-D-33 row-before-start, the
    start request is sent only after `_create_run` commits the attached run,
    so such a row never reached Apify. `_create_run` attaches under the
    budget lock only a reservation that is still open and unattached.
    Otherwise it rolls back and the start returns `denied`
    `reservation_not_open` without sending anything. The spend report lists
    released rows under "Released rows". Tests:
    `test_apify_crash_windows.py`.
- **E6 — Attribution report (AC-7).** Revision 5: `apify_spend_report` prints,
  per billing cycle (the authoritative view): the cycle bounds, the project
  allocation, consumed finalized usage, outstanding reservations, remaining
  project budget, and the observed account usage, prepaid credit, and remaining
  prepaid allowance; revision 6 adds the declared external liability, the
  inclusion-watermark status, the ledger authority and any carried handoff
  consumption, and operator consumption by kind; then per-source and
  per-provider totals from the ledger and
  `provider_run`; unsettled and overrun rows with their reasons (revision 8:
  including `correction_close_overdue` rows, MS2-D-23); and a
  trailing-31-day trend line labeled secondary (MS2-D-17). The calendar-month
  view of revision 4 is withdrawn.
  - Test: output for a seeded ledger spanning two cycles.
  - Landed 2026-09-25 (E6). `acquisition/apify/report.py` (`build_report`,
    `render_report`) and the read-only `apify_spend_report [--cycles N]`
    command; it writes no row, takes no lock, and prints the stored snapshot
    (flagged `[STALE]` past `…_ACCOUNT_SNAPSHOT_MAX_AGE_S`) without calling
    Apify. Cycle placement re-implements E3's MS2-D-34 predicate per row
    for attribution, and
    `test_apify_spend_report.py::test_cycle_totals_match_ledger_cycle_debits`
    pins it to `cycle_debits`. Choices this plan left open: remaining project
    budget is the `watch_refresh` class check's `A − (runtime + carried +
    discovery + standing)`; remaining prepaid allowance is observed `prepaid −
    account usage`, with the MS2-D-40 snapshot headroom and the observed
    non-Hardware-Radar usage (the latch check's left side) printed beside it;
    denials attribute to the cycle containing `reserved_at`; `per provider`
    groups ledger debits by `provider_run.provider_kind` (operator rows and
    unattached runtime rows separately) and adds the runs admitted in the
    cycle with their observed usage; `correction_close_overdue` means an open
    obligation past `correction_monitor_until` or at `…_MAX_CORRECTION_READS`;
    overruns are reconciled rows with `actual_usd > estimate_usd` or basis
    `bound_unfinalized` (`api_call_cap_exhausted`), plus every latch trip.
    Owner free text (`attested_by`, `cleared_reason`) and Apify run, dataset,
    build, and Actor identifiers are never printed. An unpriceable setting
    prints `unavailable (<reason>)` instead of failing the report.
  - Revision 12 (owner decision (s5, 2026-09-25), R25): the report no longer prints observed usage,
    remaining prepaid, the `[STALE]` flag, the inclusion watermark, the
    discovery status, or the standing debit. It prints the configured
    account state (anchor, limit, `P`, base price, retention, verified-on)
    and the external-liability headroom `P − HR_cycle − E`, and it states
    that the runtime does not observe the other workloads' spend (E9.2).
- **E8 — Budget-admitted probe (MS2-D-24 with the real ledger).**
  `test_budget_admitted_actor_probe_recovers_source_with_ledger`: a probe is
  admitted under the `discovery` class, reserved, imported, and reconciled, and
  the source recovers. `test_probe_denied_when_discovery_exhausted`.
  - Landed 2026-09-25 (E8), in `tests/db/test_apify_recovery_probe.py`. The
    probe is started by `recovery_probe_job` through the real
    `LedgerAdmission`, which refreshes a stale snapshot through the fake
    account endpoints, reserves under `discovery`, and attaches the run;
    three real `apify_poll_tick`s import it (the source recovers), delete its
    storage, and settle it through the reconcile unit (`bound`, actual ≤
    estimate, no latch). Nothing is staged directly. The exhausted case
    fills the discovery class (A − watch-refresh reserve) and gets a
    `class_cap` denial row under `discovery` with no call sent.
- **E7 — Close-out.** This runs last. Gate; TODO/STATUS. Record the owner
  tasks (revision 9, owner decision (s4, 2026-09-25); replaces revision 5's
  list): keep the account-level usage limit at or below the prepaid credit;
  create the scoped runtime token at `secret/apps/hw-radar/apify`, rendered as
  `HW_RADAR_APIFY_TOKEN`, and verify whether it can read the account limits
  and monthly usage (R25 residual; if it cannot, account-headroom admission
  denies with `account_state_unobservable`, and the operator/deploy key is
  never a fallback). Record as settled: the MCP filter stays unchanged until
  its conditions hold (R24), the external-liability bound is 5.00 by default
  (R33), and the operator allowance is 1.00 by default (R36). OQ23 is
  resolved; no deduction setting remains.
  - *Landed 2026-09-25 (`40b7295`).* A read-only verifier held all 16
    acceptance claims below at `ca4b9c2`. It found two crash windows, fixed in
    `40b7295`: the orphaned-start and delete-attempts-exhausted latch trips now
    commit with their row mark and are re-detected every tick, and runtime
    reservations left without a `provider_run` past
    `HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S` (900) are released (MS2-D-32
    *Settlement*: `released` = a start that never ran; row-before-start makes
    that provable). Owner tasks are recorded in `docs/TODO.md` as the F5a
    prerequisites, including the settings with no plan default (the eight unit
    prices, `…_MARGIN`, `…_MAX_TIMEOUT_S`, `…_MAX_KV_WRITES`,
    `…_MAX_KV_BYTES`, `…_STORAGE_MAX_LIFETIME`, `…_LEDGER_ID`, `…_ACTOR_ID`).
    Settled as recorded above: R24, R33, R36; OQ23.
  - Revision 12 (owner decision (s5, 2026-09-25), R25): the owner created the scoped token, and it
    cannot read the account. The "verify whether it can read the account
    limits and monthly usage" task is closed by MS2-D-48, since the runtime
    makes no account read. The remaining owner tasks are the token's
    **Read** grant on the Actor (R25) and setting the five MS2-D-48
    settings from the operator verification.

**Acceptance:** AC-7 holds. Admission fails closed on the kill switch, at the
class cap, at either account check (with reconciled spend still debited and the
owner's external-liability bound reserved), with the external-liability bound
explicitly empty or invalid (revision 9; absent means the owner's 5.00
default), without a ledger authority for the cycle (revision 6), on a tripped overrun latch, on
missing prices, on an unknown cycle or unobservable account state, on a plan
above the cash ceiling, on any component without an enforceable bound, on
an unaccepted R38 (revision 11), and on missing settings. Every external call
is counted before it is sent and priced by the MS2-D-32 *Per-call bound*;
monitoring and cycle-discovery allowances count in every cycle in which their
calls remain possible (revision 11). Reconciliation and admission are serialized. No reservation
ages out unreconciled, and none is released while charge-producing work remains
or before its usage meets the settlement predicate (or settles at its bound)
and its post-run cost is accounted; a later upward correction still trips the
latch, and correction monitoring closes only on a committed, successful
closing read (revision 8). Operator operations are reserved in the same ledger,
and a bound build is settled and monitored from persisted state. Settled spend
counts in every billing cycle its charge interval touches, however late cleanup
succeeded (revision 5).
Revision 12 (owner decision (s5, 2026-09-25), R25): "either account check" now means check 2
against the configured `P`. "Unknown cycle or unobservable account state"
means an unset, invalid, future, or conflicting anchor, or an unset or
invalid account setting. The runtime sends no account read in any path,
and a 402 start refusal trips the latch. Cycle-discovery allowances no
longer exist.

### Implementation tasks (rev 12)

Revision 12 (owner decision (s5, 2026-09-25), R25). Design authority: MS2-D-48.
These tasks land as one PR into `dev` (branch off `dev`). Each numbered task
is one signed conventional commit that leaves the gate green. The gate is the
*Global constraints* command (ruff, basedpyright, pytest with coverage,
pip-audit) plus `makemigrations --check --dry-run`, which must report no
changes, because revision 12 has no schema change. Every behavior task starts
with its failing test (correct-reason RED). Paths are under `src/hw_radar/`
and `tests/`.

*Rev-12 Codex follow-up (R12-01..R12-05; owner R39 answers, 2026-09-25).*
This section was revised in place before any E9 task ran. No E9 task has been
dispatched, so no task ID is superseded.
- E9.2 now also carries every retirement that its field and signature
  changes force (R12-02), and it lists every affected test double and
  fixture by line.
- E9.2 defines the one account-setting validation contract that admission
  and corrections share (R12-04).
- E9.3 adds durable recovery for a 402 whose latch was never recorded
  (R12-01).
- E9.4 keeps only the client, the settings, and the static scan.
- E9.5 gains the command docstrings (R12-05).
- E9.6 is new: the report's verification-age warning (owner R39 point 1).
- The close-out moves to E9.7.

**Do not change:** the reservation estimate, the MS2-D-34 predicate apart
from the discovery term, the lock key and lock order, the latch semantics
(except that the `account_limit_refused` trip is exempt from the
estimator-bump clear, E9.3), row-before-start, operator reservations, `E`,
the target, the cash ceiling, the operator allowance, R38, any model, or any
migration.

**E9.1 — Settings and pure cycle derivation (additive).**
- `settings.py`:
  - Widen `_env_date` to `default: date | None = None`.
  - Add `_env_cycle_anchor(name) -> datetime | None`. It returns `None` when
    the value is absent, empty, unparseable by `datetime.fromisoformat`,
    naive, at a UTC offset other than zero, not exactly `00:00:00.000000`,
    or on a day above 28. It returns a valid value converted to
    `tzinfo=UTC`.
  - Add the five settings, none with a default:
    `HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR = _env_cycle_anchor(...)`,
    `HW_RADAR_APIFY_ACCOUNT_LIMIT_USD = _env_decimal(...)`,
    `HW_RADAR_APIFY_ACCOUNT_BASE_PRICE_USD = _env_decimal(...)`,
    `HW_RADAR_APIFY_ACCOUNT_DATA_RETENTION_DAYS = _env_int(...)`, and
    `HW_RADAR_APIFY_ACCOUNT_VERIFIED_ON = _env_date(...)`.
  - Comment each with MS2-D-48, and state that the value is
    operator-verified outside the application.
- `acquisition/apify/budget.py`:
  - Add the five fields to `BudgetSettings` and `load_budget_settings`:
    `billing_cycle_anchor: datetime | None`,
    `account_limit_usd: Decimal | None`,
    `account_base_price_usd: Decimal | None`,
    `account_data_retention_days: int | None`, and
    `account_verified_on: date | None`.
    Rev-12 Codex round 2 (R12-02): the fields have no defaults, so this
    commit also adds them to the two direct constructors,
    `tests/unit/test_apify_budget.py` `CFG` and `tests/db/ledger_support.py`
    `BUDGET`, with the values E9.2's inventory lists (anchor `C1_START`, limit
    `19.00`, base price `19.00`, retention 31, verified-on
    `C1_START.date()`). The retired fields stay until E9.2 removes them, so
    E9.1 is gate-green on its own.
  - Add the pure `billing_cycle_bounds(anchor: datetime, now: datetime) ->
    tuple[datetime, datetime] | None` with MS2-D-48 *Derivation* exactly:
    compute `start(k)` from the anchor, never by iteration; the end is the
    next start − 1 ms; `None` when `now < anchor`. Export it.
- Tests:
  - `tests/unit/test_settings.py`:
    - `test_billing_cycle_anchor_parsing`, parametrized. Valid:
      `2026-09-05T00:00:00Z`, `2026-09-05T00:00:00+00:00`, day 1, day 28.
      `None`: empty, garbage, a naive value, `+02:00`, `00:00:01Z`, days
      29, 30, and 31, and a date-only value.
    - `test_account_settings_have_no_default`: all five absent → `None`.
  - `tests/unit/test_apify_budget.py`:
    - `test_billing_cycle_bounds_from_anchor`, parametrized. The observed
      cycle: anchor `2026-09-05T00:00Z`, `now` 2026-09-25 →
      `(2026-09-05T00:00Z, 2026-10-04T23:59:59.999Z)`. `now` equal to the
      next start → the next cycle. `now` equal to `cycle_end` → the current
      cycle. Anchor day 28: Jan 28 → Feb 28, and Feb 28 → Mar 28 in both 2027
      and leap 2028. Dec → Jan across a year.
    - `test_billing_cycle_bounds_are_computed_from_the_anchor_not_iterated`:
      `now` 61 months after a day-28 anchor starts on day 28.
    - `test_billing_cycle_before_anchor_is_unknown`.
    - `test_every_derived_cycle_is_at_most_744_hours`: 48 consecutive
      cycles for anchor days 1 and 28.

**E9.2 — Switch admission to the configured state and retire every runtime
account read with its dependents (one commit; rev-12 Codex follow-up R12-02
and R12-04).** This commit is large on purpose. Once `BudgetSettings` loses
a field, or `admit` loses its `reader`, every consumer has to change in the
same commit, or basedpyright and pytest go red.
- RED first:
  `tests/db/test_apify_no_account_reads.py::test_runtime_paths_never_request_account_endpoints`.
  Use one `httpx.MockTransport` that records every request and fails on any
  path starting with `/v2/users`. Drive the real paths:
  - `start_provider_run` through `LedgerAdmission`, once admitted and once
    denied (anchor unset);
  - `recovery_probe_job`;
  - `apify_poll_tick`s through poll → import → storage delete → settlement
    → selector-4 monitoring → closing read;
  - an operator build row bound and read through selector 2.

  Assert that the recorded paths are non-empty and none is under
  `/v2/users`, that every `ApifyBudgetCycle.account_read_count` is 0, and
  that `ApifyCycleDiscovery` has no row. It fails on the current code,
  because the refresh step reads `/v2/users/me/limits`.
- `budget.py`:
  - Change `AccountSnapshot` to the configured state. Keep the name to
    keep the diff small, and update the docstring. Its fields are
    `cycle_start`, `cycle_end`, `account_limit_usd`, `base_price_usd`,
    `data_retention_days`, and `verified_on`. Remove `observed_at`,
    `prepaid_credit_usd`, and `account_usage_usd`.
  - Change `account_margin_usd(cfg, limit)` to 10% of the configured limit
    when absent.
  - **Account-setting validation contract (R12-04; Binding).** Add a pure
    `account_setting_problem(cfg: BudgetSettings, now: datetime) ->
    AccountSettingProblem | None`, where the frozen dataclass
    `AccountSettingProblem` holds `setting: str` and `reason:
    DenialReason`. It returns the first failing check, in this order:
    1. anchor `None` → `BILLING_CYCLE_ANCHOR`, `cycle_unknown`;
    2. limit `None`, not finite, or ≤ 0 → `ACCOUNT_LIMIT_USD`,
       `account_state_unobservable`;
    3. base price `None`, not finite, or < 0 → `ACCOUNT_BASE_PRICE_USD`,
       `account_state_unobservable`;
    4. retention `None` or < 1 → `ACCOUNT_DATA_RETENTION_DAYS`,
       `account_state_unobservable`;
    5. verified-on `None`, after `now`'s UTC date, or before the anchor's
       date → `ACCOUNT_VERIFIED_ON`, `account_state_unobservable`;
    6. margin present but invalid → `ACCOUNT_MARGIN_USD`,
       `budget_setting_invalid`.

    It checks setting validity only. The cash-ceiling comparison, the
    retention-versus-lifetime check, and the 744 h check stay admission
    guards, because a correction cannot change them. `decide_admission` calls
    it after authority. The anchor case is reached only when no cycle could
    be derived, so the anchor still denies `cycle_unknown`, as MS2-D-48
    states.
  - In `decide_admission`, apply the MS2-D-48 *Admission* order through
    that function. Delete check 1, the `external_liability_exceeded` block,
    and the `ACCOUNT_SNAPSHOT_MAX_AGE_S` validation. In `_hr_cycle` and the
    runtime class check, drop `standing`, `discovery_allowance_usd`, and
    `hr_included_usd`.
  - Remove `CycleDebits.discovery_allowance_usd` and `hr_included_usd`,
    `standing_account_read_debit`, `PerCallBounds.account_read`,
    `DenialReason.EXTERNAL_LIABILITY_EXCEEDED`, `AdmissionDecision.trip_latch`,
    `BudgetSettings.max_account_reads_per_cycle` and
    `account_snapshot_max_age_s`, and their lines in `load_budget_settings`.
- `acquisition/apify/ledger.py`:
  - Add `ensure_cycle(config, now) -> ApifyBudgetCycle | None` with MS2-D-48
    *Materialization* exactly. Assert the caller is in an atomic block, as
    `take_budget_lock` does.
  - `reserve`, `claim_origin`, `export_handoff`, and `import_handoff` call
    it instead of `current_cycle`, each after the lock, as today (lines 660,
    1062, 1189, and 1318). Keep `current_cycle` for read-only callers.
  - `_snapshot(cycle)` becomes `_snapshot(cycle, cfg)`: the row's bounds
    plus the settings.
  - `reserve` loses its latch-creating branch.
  - `_tally` loses `observed_at`, the watermark, and the discovery term.
  - `_build_record` drops the `discovery_allowance_usd` and
    `standing_account_read_usd` lines. `HANDOFF_RECORD_VERSION` stays 1.
  - Add `LatchReason.ACCOUNT_LIMIT_REFUSED = "account_limit_refused"`.
  - Delete, in this commit (R12-02; moved from E9.4): `AccountReader`,
    `RefreshOutcome`, `_Plan`, `_plan_limits_read` (reads
    `account_snapshot_max_age_s`, line 824), `_count_cycle_read` (reads
    `max_account_reads_per_cycle`, line 859), `_record_cycle`,
    `_record_snapshot`, `refresh_account_snapshot`, `_trip_over_cap`,
    `account_read_useful`, `discovery_status`, `reset_discovery`,
    `DISCOVERY_EXHAUSTED`, `_discovery_allowance`, the `LedgerConfig`
    fields `max_discovery_reads`, `discovery_read_interval_s`, and
    `usage_inclusion_lag_s` and their `load_ledger_config` lines, the
    `AccountLimits`, `AccountPlan`, `ApifyError`, and `httpx` imports if
    unused, and the retired names in `__all__`.
  - Update the module docstring.
- `acquisition/apify/jobs.py`:
  - `BudgetAdmission.admit(self, request)` and `LedgerAdmission.admit` call
    only `reserve`. The start job calls `admit(request)`.
  - Remove the `AccountReader`, `account_read_useful`, and
    `refresh_account_snapshot` imports.
  - Update the module and class docstrings: "no account read" now holds in
    every configuration.
- `acquisition/apify/reconcile.py` `invariant_breaches(resv, config, now)`:
  - Add the explicit `now` parameter. The one caller (line 962) passes its
    own `now`.
  - Call `account_setting_problem(cfg, now)` first. On a problem, append
    exactly `unverifiable: <setting>`, skip every account check, and keep
    the class and allocation checks. Any breach trips
    `post_admission_invariant_breach`, as today.
  - Otherwise compute `usable = limit − account_margin_usd(cfg, limit)`,
    and apply only the external-liability check per touched cycle.
  - Drop `standing`, discovery, "account snapshot unobserved", and
    "snapshot check", and update the docstring.
- `acquisition/apify/report.py`:
  - Remove observed usage, remaining prepaid, snapshot headroom, observed
    non-Hardware-Radar usage, `[STALE]`, the watermark, `standing`, and
    `discovery`, together with the `discovery_status`,
    `standing_account_read_debit`, and `account_margin_usd(prepaid)` uses.
  - Per cycle, add the configured account state (anchor, limit, `P`, base
    price, retention days, verified-on) and `external-liability headroom =
    P − HR_cycle − E`. Add the fixed line "other workloads' spend: not
    observed by the runtime (MS2-D-48)".
  - Print the derived current cycle even when its row is not yet
    materialized, and label it so. The report stays read-only and never
    calls `ensure_cycle`.
  - `_place` drops the watermark argument. Keep the `_tally` cross-file
    contract. Update the module docstring.
- `catalog/management/commands/apify_budget_reset.py` (moved from E9.4,
  because `reset_discovery` is deleted here): remove `--discovery`, the
  `reset_discovery` import, and the discovery half of the docstring and
  help text.
- **Test and fixture inventory (R12-02; verified against the tree at
  `84c5cd0`).** Change every item in this commit:
  - `tests/db/ledger_support.py`:
    - module docstring line 5;
    - `BUDGET` lines 94 and 113–114: drop the two retired fields (E9.1
      already added the five new ones, R12-02 round 2);
    - delete `STANDING` (line 130);
    - `config()`;
    - `cycle()` lines 145–162: stop writing the `account_*` fields;
    - delete `account_client` (line 287 and its section).
  - `tests/unit/test_apify_budget.py`:
    - fixtures: `CFG` (lines 102–103), `snap()` (114–116), `usable()`
      (170–172), and the check-1 binding helper (180–196), whose docstring
      describes check 1;
    - delete `test_observed_external_consumption_above_bound_denies_and_trips_latch`
      (line 449), `test_inclusion_watermark_reduces_only_the_snapshot_check`
      (806), `test_account_read_bound_is_standing_cycle_debit` (654),
      and `test_snapshot_exactly_at_max_age_is_fresh`;
    - drop `"max_account_reads_per_cycle"` from the parametrization of
      `test_api_calls_priced_at_bound_without_billing_verification` (line
      633), and `{"account_snapshot_max_age_s": None}` from the one at line
      840;
    - remove the `standing_account_read_debit` terms from
      `test_exact_boundary_admitted_and_epsilon_over_denied` (258),
      `test_default_external_liability_admits_only_when_invariant_holds`
      (402–405), `test_external_liability_consumes_share_before_target`
      (422), and the concurrent-external-consumption test (435–438);
    - `test_straddling_reservation_checked_against_both_cycles_external_bound`
      (468) and
      `test_operator_reservation_counts_against_operator_class_and_account_checks`
      (488–492);
    - `test_outstanding_reservations_are_counted` (797): its
      `discovery_allowance_usd` input goes;
    - the rewrites, deletions, and additions listed below.
  - `tests/db/test_apify_admission.py`: the `AccountLimits`/`AccountPlan`
    imports (38–39) and `STANDING` (129).
  - The admission doubles, whose `admit(self, request, reader:
    AccountReader)` becomes `admit(self, request)` and whose `AccountReader`
    import is dropped:
    - `tests/db/test_apify_poll_job.py`, lines 52 and 126;
    - `tests/db/test_apify_storage_cleanup.py`, lines 48 and 181;
    - `tests/db/test_apify_crash_windows.py`, lines 65 and 304
      (`ReleasedUnderStart`);
    - `tests/db/test_apify_recovery_probe.py`, lines 48 and 105.
  - `tests/db/test_apify_recovery_probe.py`: the fake account endpoints
    (577–610), the `account_read_count == 2` assertion (652), and
    `STANDING` (691).
  - `tests/db/test_apify_ledger.py`: the imports of `STANDING` and
    `account_client` (50, 54), and every `refresh_account_snapshot` and
    `STANDING` use (153, 279, 294, 402–437, 496–571, 1020, 1540, and 1565),
    plus the test at 1039 for the new `invariant_breaches` signature.
  - `tests/db/test_apify_ledger_authority.py`: `STANDING` (38, 233, and
    410) and the `standing`/`discovery` line assertions (399–400).
  - `tests/db/test_apify_spend_report.py`: `STANDING` (34, 218, 232, and
    238) and the discovery and watermark expectations.
  - `tests/db/test_apify_budget_schema.py`: **unchanged**. It tests the
    unchanged columns and tables directly.
- Named test changes:
  - `test_apify_budget.py`:
    - Rewrite `test_cycle_bounds_come_from_account_limits_not_calendar` →
      `test_cycle_bounds_come_from_configured_anchor_not_calendar`.
    - `test_stale_or_unreadable_account_snapshot_denies` →
      `test_unset_or_invalid_account_setting_denies_account_state_unobservable`,
      parametrized over contract cases 2–5.
    - `test_missing_data_retention_days_denies` → expects
      `account_state_unobservable`.
    - Rewrite on the configured values:
      `test_account_headroom_below_project_target_binds`,
      `test_project_target_below_account_headroom_binds`,
      `test_plan_base_price_above_cash_ceiling_denies_all`,
      `test_configured_account_margin_replaces_the_ten_percent_default`, and
      `test_concurrent_external_consumption_within_declared_bound_cannot_push_account_past_prepaid`
      (renamed `…_past_configured_limit`).
    - Add `test_admission_admits_with_configured_cycle_and_limit` (all set,
      `HR_cycle` 0: admitted; with `P = 17.10`, a reservation that would
      take `HR_cycle + new + 5.00` above 17.10 is denied `account_headroom`)
      and `test_unset_or_invalid_anchor_denies_cycle_unknown` (unset, and a
      `now` before the anchor).
    - Add `test_account_setting_problem_contract`, parametrized over cases
      1–6 with the exact setting and reason, plus `None` when all are
      valid.
  - `test_apify_ledger.py`:
    - Delete `test_empty_ledger_bootstrap_read_is_counted_before_it_is_sent`,
      `test_exhausted_cycle_read_cap_then_rollover_discovers_next_cycle`,
      `test_failed_discovery_reads_count_and_stop_at_cap`,
      `test_discovery_reset_stores_the_owners_reason`,
      `test_external_liability_exceeded_trips_latch`,
      `test_repeated_reserve_reconcile_against_one_unchanged_snapshot_keeps_debit`,
      `test_refreshed_snapshot_that_still_lags_keeps_reconciled_debit`,
      `test_inclusion_watermark_unset_debits_all_reconciled_cycle_spend`, and
      `test_inclusion_lag_set_drops_only_rows_ended_before_watermark`.
    - Rewrite `test_unknown_cycle_denies` (anchor unset: denied
      `cycle_unknown`, and no cycle row is created) and
      `test_arbitrary_non_calendar_cycle_boundary` (from a day-5 anchor).
    - Rewrite `test_cycle_shortened_by_plan_change_moves_the_boundary` →
      `test_anchor_moved_forward_clamps_the_recorded_cycle`.
    - Rewrite `test_upward_correction_breaching_external_liability_check_trips_latch`
      on the configured limit.
    - Add:
      - `test_first_reserve_materializes_the_configured_cycle_row`: derived
        bounds, the allocation, null `account_*`, and `account_read_count`
        0.
      - `test_rollover_materializes_next_cycle_and_continues_authority`.
      - `test_anchor_moved_backward_into_recorded_cycle_denies_cycle_unknown`:
        no row is created, and the existing rows are unchanged.
      - `test_reconciled_spend_stays_debited_against_configured_limit`:
        repeated reserve → reconcile, and the admission that would exceed
        `P − E` is denied `account_headroom`.
      - `test_correction_with_invalid_account_setting_is_an_invariant_breach`
        (R12-04), parametrized over contract cases 1–6. An upward correction
        with that setting invalid yields exactly `unverifiable: <setting>`
        and one open `post_admission_invariant_breach` trip. Verified-on is
        evaluated at the correction's own `now`.
  - `test_apify_ledger_authority.py`:
    - Rewrite `test_same_cycle_handoff_carries_account_read_and_monitoring_debits`
      → `test_same_cycle_handoff_carries_monitoring_debits_without_account_read_lines`.
      The record's `lines` keys are exactly `settled_runtime_usd`,
      `settled_operator_usd`, `monitoring_allowance_usd`, and
      `carried_in_usd`.
    - Add `test_claim_origin_materializes_configured_cycle_without_a_denied_start`.
    - Add `test_handoff_record_is_cycle_scoped_under_configured_anchor`:
      authority rows are keyed by the derived `cycle_start`, and a
      destination whose anchor derives another cycle refuses the import with
      `wrong_cycle`.
  - `test_apify_admission.py`: delete
    `test_stale_snapshot_is_refreshed_then_start_admitted`,
    `test_settings_denial_skips_the_account_read`, and
    `test_over_cap_account_read_trips_latch`.
  - `test_apify_recovery_probe.py`: E8's two tests run on the configured
    cycle.
  - `test_apify_spend_report.py`:
    - Delete `test_inclusion_watermark_set_reports_included_settlement`.
    - Rewrite `test_empty_ledger_reports_no_cycle_and_discovery_exhaustion`
      → `test_empty_ledger_reports_no_cycle`.
    - Update `test_report_for_seeded_ledger_spanning_two_cycles`.
    - Add `test_report_prints_configured_account_state_without_observed_usage`.
    - Keep `test_cycle_totals_match_ledger_cycle_debits` green.
  - The command's test module: `apify_budget_reset --discovery` is rejected
    as an unknown option.
- **Completion check (Binding).** After this commit, this search over `src/`
  and `tests/` returns hits only in `client.py` and its unit tests (removed
  in E9.4), `settings.py` and `tests/unit/test_settings.py` (removed in
  E9.4), `catalog/models/provider.py`, migration `0022`, and
  `tests/db/test_apify_budget_schema.py`:

  ```
  rg -n 'AccountReader|account_snapshot_max_age_s|max_account_reads_per_cycle|standing_account_read_debit|discovery_allowance|hr_included|EXTERNAL_LIABILITY_EXCEEDED|refresh_account_snapshot|account_read_useful|reset_discovery|discovery_status|usage_inclusion_lag|max_discovery_reads|STANDING\b'
  ```

**E9.3 — Hard-limit refusal (402) and its durable recovery (rev-12 Codex
follow-up R12-01).**
- RED first, in `tests/db/test_apify_admission.py`:
  - `test_start_refused_with_402_trips_account_limit_latch_and_keeps_reservation`:
    the transport answers the start with 402 `{"error": {"type":
    "x402-payment-required", "message": "…"}}`. Expect:
    - `StartResult(START_FAILED, "account_limit_refused", row)`;
    - `stage_detail["start_error"]` with `status_code` 402 and
      `error_type`;
    - exactly one open latch with reason `account_limit_refused` for the row;
    - the reservation still `reserved`, neither released nor reconciled;
    - later ticks leave the row to selector 3's `orphaned_start` at its
      deadline, unchanged.
  - `test_start_error_other_than_402_does_not_trip_account_limit_latch` (a
    500 and a transport error).
- RED first, in `tests/db/test_apify_crash_windows.py`:
  - `test_402_trip_lost_between_commits_is_repaired_before_next_admission`.
    Monkeypatch the start job's `trip_latch` to raise a simulated process
    loss after `_record_start_error` committed. Then, with no tick in
    between, call `reserve` for a different source and `reserve_operator`
    (`inspect`). Expect:
    - exactly one latch row with reason `account_limit_refused` for the
      402 row, open;
    - both new requests persisted as `denied` with `overrun_latch`, and no
      new admitted reservation;
    - the original reservation unchanged (`reserved`, same estimate, same
      `provider_run`).
  - `test_402_trip_lost_between_commits_is_repaired_by_the_next_tick`: the
    same crash, then one `apify_poll_tick` → one open trip.
  - `test_owner_cleared_402_trip_is_never_retripped`: after `apify_budget_reset
    --reason`, neither `reserve` nor a tick recreates the trip. The row's
    cleared trip is the record that it was handled.
  - `test_estimator_bump_does_not_clear_account_limit_refused`.
- `jobs.start_provider_run`: in the `except` around `client.start_run`, after
  `_record_start_error` commits, if `isinstance(exc, ApifyApiError) and
  exc.status_code == 402`, call `await
  sync_to_async(trip_start_refusal)(row.pk)` and return the result above.
  Rev-12 Codex round 2 (R12-201): the direct callback must not use
  `trip_latch`, whose idempotence covers only open trips (`ledger.py`
  `trip_latch_locked`). A start callback delayed past a repair and an owner
  reset would otherwise re-trip an already-handled 402. `ledger.trip_start_refusal(provider_run_id)`
  takes the budget lock and trips `account_limit_refused` for the row only
  when the row has no trip with that reason, open **or cleared** (the same
  predicate as `trip_stranded_start_refusals_locked` below, shared as one
  helper). Other reasons keep `trip_latch`'s semantics. Test:
  `test_delayed_402_callback_after_repair_and_owner_clear_does_not_retrip`
  (pause the callback after `_record_start_error` commits, run a tick's
  repair, owner-clear with `apify_budget_reset`, resume the callback, and
  assert no new trip and that admission is not paused). Otherwise, keep the
  current behavior. Add a comment that cites MS2-D-48 and why the type string
  is not matched.
- `ledger.py`: add `trip_stranded_start_refusals_locked(estimator_version,
  now) -> list[int]` (the caller holds the budget lock).
  - It selects the `ProviderRun` rows with
    `stage_detail__start_error__status_code=402` that have **no**
    `budget_latch_trips` row with reason `account_limit_refused`, whether
    open **or cleared**. Any existing trip, including an owner-cleared one,
    means the condition was recorded, and it is never re-tripped (the same
    rule as `storage_cleanup._stranded`, R21).
  - For each selected row it calls `trip_latch_locked`, and logs at error
    level.
  - It is called in two places:
    1. `reserve`, right after `take_budget_lock()` and
       `_clear_on_estimator_bump`, before `latch_tripped()`. Every later
       admission of any class therefore sees the repaired trip in its own
       transaction.
    2. `storage_cleanup.trip_stranded_latches`, inside its existing
       budget-locked transaction. Extend its lock-free precheck with the
       same query, so a tick with nothing stranded still takes no lock.

    No separate startup hook is needed, because (1) precedes every
    admission and (2) runs on the first tick after a restart.
- `_clear_on_estimator_bump`: exclude `reason = account_limit_refused`. An
  estimator bump replaces a price bound, and it says nothing about the
  account's hard limit. Only the owner's reset clears this trip.
- Make the E5 and E6 report and shortlist text show the new latch reason. No
  code change is expected beyond the enum member.

**E9.4 — Remove the client account reads and the retired settings.**
- `client.py`:
  - Delete `get_account_limits`, `get_monthly_usage`, `get_account_plan`,
    `AccountLimits`, `AccountPlan`, `MonthlyUsage`, `DailyUsage`, and any
    helper used only by them (check `_service_usd` and
    `_opt_positive_int`).
  - Delete the account bullet at lines 10–12 of the module docstring.
  - Delete the "Limits" and "Monthly usage" documentation bullets and URLs
    at lines 114–122 (R12-02), and the `GET /v2/users/me` wire-name note at
    line 135. After this, no `/v2/users` literal is left for the static scan
    to find.
- `settings.py`: delete `HW_RADAR_APIFY_MAX_ACCOUNT_READS_PER_CYCLE`,
  `…_MAX_DISCOVERY_READS`, `…_DISCOVERY_READ_INTERVAL_S`,
  `…_ACCOUNT_SNAPSHOT_MAX_AGE_S`, and `…_USAGE_INCLUSION_LAG_S`. Nothing
  reads them after E9.2. Update the `ACCOUNT_MARGIN_USD` comment.
- Tests:
  - `tests/unit/test_apify_client.py`: delete
    `test_account_limits_parsed_into_cycle_bounds`,
    `test_account_limits_without_valid_cycle_fail_closed`,
    `test_account_plan_parsed_from_users_me`,
    `test_account_limits_parse_data_retention_days`, and every monthly-usage
    test. Add `test_client_has_no_account_read_methods`.
  - `tests/unit/test_settings.py`: remove the five entries from the
    defaults map (lines 186–192), update
    `test_budget_keys_absent_take_the_plan_defaults`, and add
    `test_retired_account_read_settings_are_absent`.
  - RED first:
    `tests/db/test_apify_no_account_reads.py::test_no_runtime_module_references_account_endpoints`.
    No file under `src/hw_radar/` may contain `/v2/users` or `users/me`. At
    `84c5cd0` the only hits are in `client.py`. It fails until those are
    gone, and it is green at this task's commit.

**E9.5 — Environment and docs (R12-05 adds the command docstrings).**
- Do not add the new settings to any committed environment file. They are
  account values, rendered by the operator from the verification.
- `catalog/management/commands/apify_ledger_claim.py`, lines 7–8: replace
  "(run a snapshot refresh first)" with the configured-anchor rule. The
  claim itself materializes the cycle, and it is refused `cycle_unknown`
  when the anchor is unset, invalid, in the future, or conflicting.
- `catalog/management/commands/apify_spend_report.py`, lines 3–12: replace
  the stored-snapshot and staleness wording with the configured account
  state, the external-liability headroom, the verification-age warning
  (E9.6), and "other workloads' spend is not observed by the runtime".
- Re-read the docstrings of `apify_budget_reset.py`, `jobs.py`, `ledger.py`,
  `report.py`, and `reconcile.py` (all edited in E9.2). This search must
  return nothing:

  ```
  rg -n -i 'snapshot refresh|stale snapshot|cycle discovery|account read' src/hw_radar/acquisition/apify src/hw_radar/catalog/management/commands
  ```

  Any hit is either rewritten, or marked as retired with a MS2-D-48
  pointer.
- In `docs/TODO.md`, the F5a prerequisites gain the operator verification
  and the five settings, and lose "verify the runtime token's account reads".
  The Actor **Read** grant for the runtime token is added.
- `docs/STATUS.md`: one line for revision 12.
- `docs/handoff/credentials.md` is already updated by the revision-12
  decision record.

**E9.6 — Verification-age warning (owner R39 point 1, 2026-09-25).**
- RED first, in `tests/db/test_apify_spend_report.py`:
  - `test_report_warns_when_account_verification_predates_current_cycle`:
    `…_ACCOUNT_VERIFIED_ON` before the derived current cycle's start prints
    one warning line that names the setting and both dates.
  - `test_report_has_no_verification_warning_when_verified_this_cycle`.
- In `tests/unit/test_apify_budget.py` or `tests/db/test_apify_ledger.py`:
  `test_old_verification_does_not_affect_admission`. An old but valid
  verified-on date still admits, which proves the warning never gates.
- `report.py`: compute the warning from the settings and the derived current
  cycle (read-only), and render it in the cycle header. The warning is
  informational only. `decide_admission`, `ensure_cycle`, and
  `invariant_breaches` never read it.

**E9.7 — Close-out.**
- Gate.
- `makemigrations --check --dry-run` reports no changes.
- The regression tests of E9.2 through E9.6 pass.
- The E9.2 completion search holds, with only the listed files.
- Record in this plan's E9 a *Landed* note with the commit IDs.
- The Codex bounded review of revision 12 has run
  (REVISION_REQUIRED, R12-01..R12-05, all resolved by the rev-12 Codex
  follow-up; see *Review lineage*). Any further review round follows the
  lineage note.

## Slice F — Pilot sources, measurement, end-to-end proof

**Scope:** MS2-D-19; MS-2 Tasks 6–7; AC-3 live half.

**Files:**
- `acquisition/sources/ebay.py` (category sweeps);
- possibly `serverpartdeals.py`;
- new command `pilot_report`;
- `docs/STATUS.md` evidence.

- **F1 — eBay category sweeps.**
  - **Facts** (developer.ebay.com Browse `item_summary/search` and Taxonomy
    docs; the eBay prep report retrieved these 2026-09-24, some via verbatim
    search-engine snippets because the pages blocked direct fetch):
    - US leaf categories (tree `0` for `EBAY_US`): GPU / Graphics & Video
      Cards **27386**, RAM **170083**, CPU **164**.
    - eBay has **no dedicated datacenter-accelerator category**. Datacenter
      accelerators and consumer cards share 27386, so disambiguation falls to
      GPU extraction (R8).
    - `category_ids` accepts **one** ID per request, so each category is its
      own sweep.
    - `limit` is at most 200. `offset` is 0–9,999 and a multiple of `limit`. A
      result set is capped at **10,000 items**, so `complete=True` is
      achievable only when `total ≤ 10,000`.
    - Today `fetch()` issues exactly one GET (`ebay.py:192-208`) and has no
      pagination loop. `_sweep_is_complete` (`:268-291`) needs no `next` href
      and `total ≤ seen`, so a category whose `total` exceeds 200 can never be
      proven complete without a loop.
    - The Browse daily quota is **unconfirmed**. Several secondary sources say
      5,000/day, but the official limits table only footnotes Buy APIs. Even
      four sweeps at a 5-minute cadence (about 1,152 calls/day) fit within that
      figure. Confirm it with `getRateLimits` before setting cadence.
    - Category IDs change periodically, with quarterly category-change notices.
  - **Sweeps.** Configure per-category sweeps: GPU 27386, RAM 170083, CPU 164,
    one `category_ids` per request. Re-verify the IDs via the Taxonomy API
    (`getCategorySuggestions` / category subtree) at implementation time, record
    the date, and add a periodic re-verification TODO. Each sweep has a
    `category_hint` and `collection_scope="ebay:<slug>:<query_id>"`, which is the
    MS2-D-12 key format. The legacy drive keyword sweep keeps scope None and hint
    None, so no data backfill is needed.
  - **Pagination.** Add a pagination loop, `limit=200` with `offset` cycling,
    until there is no `next` href or a page cap below the 10,000-item ceiling.
    Hitting the page cap makes the sweep incomplete: it is never
    `complete=True`.
  - **Multi-scope delist.** The adapter returns one scope per sweep via a new
    optional multi-scope capability that the pipeline applies per scope. Each
    per-scope absence uses MS2-D-12's `collection_scope` filter, so a complete
    GPU sweep can never delist RAM, CPU, or drive listings. Continuity is
    recorded or broken per swept scope (MS2-D-31). The legacy drive sweep
    stays in every eBay run, so the NULL-scope lane keeps today's continuity.
  - **Retention.** Retention and delete-on-delist are unchanged (class
    `ebay_listing_observation`, ≤6 h, delete-on-delist). The API License
    Agreement's obligations attach to the Browse mechanism, not to a category.
    The 2026-09-24 check found nothing category-specific.
  - **Tests** (cassettes):
    - per-sweep completeness;
    - `test_pagination_reaches_complete_when_total_within_cap`;
    - `test_page_cap_hit_is_incomplete`;
    - every non-drive item carries a hint;
    - a GPU complete sweep cannot delist drive listings (scoped absence);
    - the frozen drive tests stay green.
  - **Landed 2026-09-25** (`d2bc220`, `20fe3b8`, `1ef8aae`; evidence
    [`docs/evidence/2026-09-25-f1-f3-pilot.md`](../../evidence/2026-09-25-f1-f3-pilot.md)).
    Facts re-verified live: tree `0` version `134`; the three IDs stand, and eBay
    also has server leaves 11210 (Server Memory) and 56088 (Server CPUs);
    `getRateLimits` reports `buy.browse` at 5,000 per 86,400 s (R8's quota question
    closed); offsets past 10,000 still returned items and offset 50,000 returned a
    silent empty page. Category-only totals (≈109k–1.3M) can never be complete, so
    the pilot sweeps are query-scoped (RTX 3090 in 27386, 32GB DDR4 ECC RDIMM in
    11210, EPYC 7302 in 56088; fixed-price only). **Deviation from the task list
    above:** a verifier showed offset paging over a live, re-ranked set can skip an
    item while `total` and the id count still agree, so only a **single-page** sweep
    can be `complete`. Every multi-page sweep is incomplete
    (`multi_page_unprovable`), and its absence goes through the grace path.
    `test_pagination_reaches_complete_when_total_within_cap` is therefore replaced by
    `test_multi_page_sweep_within_cap_is_unprovable` and
    `test_single_page_sweep_is_complete`. Each scope's delist also excludes every key
    seen anywhere in the run. Category sweeps share a 60 s deadline; a failure fails
    only its own sweep. `probe()` stays one GET. Per-scope outcomes land in
    `ScraperRun.detail_json["scopes"]`.
- **F2 — ServerPartDeals breadth check.** If non-drive collections exist, add
  hinted, scoped collection sweeps. Otherwise record "drive-only" as a finding.
  - **Landed 2026-09-25: no change.** RAM collections and some GPU products
    exist, but the site's Terms of Service prohibit "spider, crawl, or scrape"
    and "any automated use of the Service", so the connector is not broadened
    ([record](../../research/source-admission/2026-09-25-serverpartdeals.md)). The
    existing drive connector's own conflict is
    [OQ31](../../resolved-questions.md#oq31--existing-local-connectors-whose-terms-prohibit-automated-access).
- **F3 — Measurement.** `pilot_report` summarizes, per source and provider:
  runs, completeness distribution, identifier (MPN) coverage, condition and
  shipping presence, freshness lag, failures, and cost (Task 6).
  - **Landed 2026-09-25** (`0b2246b`, `968fb1e`): `manage.py pilot_report`, JSON
    schema `hw-radar/pilot-report/v1`, grouped by source, provider, scope, and
    category. Unmetered sources print "no direct provider cost (not metered)", and
    missing evidence prints "not recorded". A bounded live pilot into a scratch
    database found 0% condition coverage on every source and no GPU/RAM/CPU
    reference rows (production has none either). It also found and fixed a
    goHardDrive URL/selector drift (`2c8bce6`) and a Scrapy DNS thread-pool defect
    (`b9e01a1`).
- **F4 — Category corpora (prep for owner gate R4).** Harvest GPU/RAM/CPU samples
  with `harvest_corpus`. Its hint round trip landed in B6 (MS2-D-27), so the
  harvested entries replay through their own category rules. F4 changes no
  tooling. Labeling and ratification are owner-in-the-loop, as in MS-1e.
  - **Harvest landed 2026-09-25:** `harvest_corpus --source ebay` wrote 1,888
    unlabeled entries (GPU 851, RAM 991, CPU 16, drive 30) to the git-ignored
    `.harvest/f4-ebay/`. eBay is the only source with category sweeps. CPU is thin
    because the pilot sweep queries one model; widen `CATEGORY_SWEEPS` before
    labeling if the owner wants a broader CPU corpus.
  - **Landed 2026-09-26 (owner decisions, OQ31/OQ32; not an F4 code change):** the local-connector
    Terms review this section's F2 relied on ([`2026-09-25-local-connector-terms-review.md`](../../research/2026-09-25-local-connector-terms-review.md))
    became an owner decision: ServerPartDeals and Seagate-recertified are retired
    (`RETIRED_SOURCES`, `src/hw_radar/acquisition/admission.py`; migration `0023`), for any
    execution venue, local or Apify — F2's "no change" finding stands, permanently. The global
    SA-004 enable order this plan never restated is retired too, replaced by a per-`(source,
    category)` admission matrix (`ADMISSION_MATRIX`, same module); every cell is `NOT_ADMITTED`.
    Separately, OQ32 replaced the five-source ratification floor the MS-1e audit packet found
    unsatisfiable with a metadata-declared ≥3-source floor (`ratification_sources`,
    `MIN_RATIFICATION_SOURCES`), evaluated against production refdata rather than the unit-test
    `seeded_catalog`; F4's owner-in-the-loop labeling and ratification path (above) is otherwise
    unchanged. `matcher_version` bumped to `2026.09.2` in the same window (comparison-phrase MPN
    masking, the Seagate `nm`→Exos false-merge fix, WD SKU→MPN structured evidence).
- **F5 — Actor proof.** Revision 5 (owner-clarified (s2, 2026-09-24); OQ24
  split) replaces revision 4's single owner-gated merchant proof with two tasks.
  Revision 4's step "open the Actor PR in the separate Actor repository" is
  withdrawn (owner-overridden, MS2-D-38). Revision 9 (owner decision (s4,
  2026-09-25), [OQ28](../../resolved-questions.md#oq28--can-ms-2-exit-on-the-synthetic-proof-alone)): F5a alone satisfies
  the Apify portion of MS-2 exit; F5b is not required.
- **F5a — Synthetic proof through a real private Actor (MS2-D-42, -43).** Gated
  on: E merged; the scoped Hardware Radar runtime token created and rendered
  as `HW_RADAR_APIFY_TOKEN` in the proof environment (R25 residual; the
  unscoped operator/deploy key is used only for step 2's deploy and never as
  the runtime token, revision 9); verified unit prices;
  `…_STORAGE_MAX_LIFETIME` set; a valid external-liability bound (the owner's
  5.00 default, R33 resolved in revision 9); the latch clear; and an explicit
  owner or orchestrator instruction to deploy. No merchant legal decision is
  needed. Revision 10 (entry gate, ED-01): F5a is **not** gated on whether
  `GET` polls and account reads bill or on the transfer direction. Those are
  priced by conservative bounds (MS2-D-26, -32), and F5a is the run that
  measures them. Its admission needs only the settings above to be set and
  valid, with `…_STORAGE_MAX_LIFETIME` at least the observed
  `dataRetentionDays` (MS2-D-40) and the API-call caps valid. The R25
  capability probe also records whether a delete of inaccessible storage
  returns 403 (MS2-D-25 *404 rule*); until it does,
  `…_DELETE_404_IS_ABSENT` stays false, which fails closed and does not block
  admission. Revision 11 (R10-01, R10-02, R10-06) adds three gates, none of
  which waits on F5a's own evidence. First, the owner's recorded acceptance
  of R38, set as `…_CALL_BILLING_RESIDUAL_ACCEPTED` (given 2026-09-25). Second, the D1 follow-up
  landed in the deployed Actor build. Third, the capability probe run under
  an operator `probe` reservation (MS2-D-46).
  Revision 12 (owner decision (s5, 2026-09-25), R25): F5a is no longer gated on the runtime
  token's account reads, because the runtime makes none (MS2-D-48). It gains
  three gates. First, E9 has merged. Second, the five MS2-D-48 settings are
  set in the proof environment from an operator verification made with the
  operator key outside the application; that key is never rendered to the
  proof environment. Third, the runtime token has **Read** on the Hardware
  Radar Actor (R25), which runs and builds need. `…_STORAGE_MAX_LIFETIME` is
  checked against the configured `…_ACCOUNT_DATA_RETENTION_DAYS`.
  1. Create the synthetic site with its idempotent setup command, in a
     non-production environment (MS2-D-42), and claim the cycle's ledger
     authority there (`apify_ledger_claim --origin`, MS2-D-45).
     Revision 12 (owner decision (s5, 2026-09-25), R25): before the claim, run the MS2-D-48
     operator verification with the operator key outside the application,
     and set the five settings. The claim then materializes the configured
     cycle, and no denied start is needed first.
  2. Reserve the build (`apify_operator_reserve --kind build`, MS2-D-46), deploy
     `hw-radar-synthetic-collector` by the MS2-D-43 procedure, verify the push
     uploaded only the Actor directory, bind the build to its reservation
     (`--settle <id> --build-id <build id>`, revision 8) so the poller settles
     and monitors it, and record the deployment.
  3. Run through hw-radar's own path:
     `APScheduler/provider admission → budget reservation → Actor start →
     remote execution → bounded output → completion polling → completeness
     report → dataset/output import → idempotency → event-time handling →
     listing identity handling → finalized usage reconciliation`.
     Cover one complete run, one run per truncation reason, one
     `contradictory_report` run, one duplicate import, one delayed older
     import after a newer run, and one local ↔ Actor switch on the synthetic
     site (AC-4 and AC-5 live).
  4. Run the measurement protocol (MS2-D-43): finalization deltas, the post-run
     account usage diff, storage accrual after deletion, and the `date`
     parameter cycle read. Record the results; the owner decides whether
     `…_POST_RUN_COST_MODE` may become `counted`. Revision 6 (R5-03): continue
     reading each run's usage past 120 s until the stable-read predicate holds,
     and record the full trail. If the 0/10/30/120 s readings disagree at the
     last sample, `…_RUN_USAGE_SETTLEMENT` stays `bound`. Also measure the
     account usage inclusion lag, the only evidence on which the owner may set
     `…_USAGE_INCLUSION_LAG_S` (R5-01). Revision 10 (entry gate) adds these
     measurements:
     - whether `GET` run, `GET` build, and account reads bill, and at what
       rate (ED-01);
     - the split of the run's transfer between external and internal (ED-01);
     - whether the run's `usage` / `usageTotalUsd` changes after a post-run
       dataset read and after the deletes. `stable_reads` may be enabled only
       after this is recorded (ED-08);
     - the daily bucket into which post-deletion storage accrual posts (ED-16);
     - (revision 11, R10-01) the per-call multiplicity and metered bytes of
       R38. Each call type (a `GET` run batch, a dataset page of known item
       count, a KV record read, a delete pair, an account-read batch) is
       made in a quiet window with a known count and byte total, and the
       change in the matching `dailyServiceUsages` service quantities is
       recorded. The shared account's other workloads can confound it, so an
       unexplained difference is recorded as such, never subtracted;
     - (revision 11, R10-02) the run's reported transfer against the Actor's
       counted wire bytes, including a `truncate_bytes` run.

     Revision 12 (owner decision (s5, 2026-09-25), R25): every step above that reads account
     state is an **operator-side** read. These are the post-run account usage
     diff, the `date`-parameter cycle read, whether account reads bill, and
     the R38 per-call `dailyServiceUsages` deltas, including the "account-read
     batch" item, which now measures operator-key reads only. They use the
     operator key from the workstation or the Console, never the application,
     and each read counts as one record read of an operator `inspect`
     reservation made before the session (MS2-D-46, -48). The operator
     reserves further envelopes as needed within the 1.00 operator allowance.
     The account usage inclusion lag is no longer measured for a setting,
     because `…_USAGE_INCLUSION_LAG_S` is retired. Record whether the
     observed cycle and limit still match the configured values (R39), and
     run one MS2-D-48 *Operator reconciliation* at the end of the proof.

     Any bound lowered, or R38 narrowed, from these results needs a plan
     revision. F5a shows typical billing, not a worst case (MS2-D-32
     *Rejected (d)*).
  5. Before any production environment admits paid work in the same cycle,
     disable this environment, drain every liability, and hand off
     (`apify_ledger_handoff`, MS2-D-45). A still-running or late-finalizing
     proof run blocks the handoff, and so does any correction obligation
     without a committed closing read (revision 8).
  6. Record every §21 item's live evidence (see *Synthetic Actor proof
     acceptance*) in STATUS.
  **F5a landing (s5, 2026-09-25).** Executed in a non-production proof
  environment; production untouched. Evidence:
  `docs/evidence/2026-09-25-f5a-synthetic-proof.md`. Build `1.0.1` via the REST
  API (15 files from the reviewed commit, so the `apify push` upload-scope
  question did not arise); eleven admitted runs covering every fault mode, all
  classified as designed, with zero delistings; live duplicate import made no
  HTTP call; live Actor ↔ local switch kept identity and history (AC-4, AC-5
  live); Apify rejected a schema-invalid input with HTTP 400 before any run
  existed; the R25 probe recorded 403 for inaccessible storage, so the proof
  environment set `…_DELETE_404_IS_ABSENT=true`. Measurement: run usage is not
  final at `finishedAt` (about 40% low at 0 s, stable by 30 s; one failed run
  read 3× low at 8 s), so `…_RUN_USAGE_SETTLEMENT` stays `bound`; dataset reads
  bill per item returned (as the estimator assumes); the platform's `INPUT`
  write is billed to the run, which uses up `…_MAX_KV_WRITES=2` (finding F-01;
  owner-approved 2026-09-25: the value is 3, and admission denies any value
  below the two-write floor; the Actor contract stays one `OUTPUT` write); API calls bill through external transfer
  (R38 observed, still accepted). Account usage rose $0.00527 for the whole
  proof. The late-older-import scenario is prevented by construction
  (`scope_run_outstanding`) and keeps its fixture proof. Step 5's drain and
  handoff remain before any production environment admits paid work this
  cycle. The kill switch was enabled only per command; the proof environment's
  configuration keeps it off.
- **F5b — Production Actor-backed merchant source (owner-gated: OQ24, R1).**
  Not an MS-2 exit condition (revision 9, OQ28); OQ24 may remain open after
  MS-2 closes. After the owner answers OQ24 for a candidate with an `eligible`
  source-admission record (MS2-D-44):
  1. Build `actors/hw-radar-<source>/` in this repository on the same contract.
  2. Set that source to `collection_provider=apify`.
  3. Run one bounded live run and one deliberately truncated run. Live admission
     requires the E preconditions and a registered retention that is not bounded
     (MS2-D-33).
  4. Switch local ↔ Actor where a local path exists.
  5. Record cost and completeness.
- **F6 — End-to-end exit (owner-gated: R5).** With pilot sources enabled by the
  owner, create one real watch. `show_shortlist` produces a qualifying shortlist
  from real observations with no score (AC-3). Record the evidence in STATUS.

**MS-2 exit:** AC-1..AC-8 evidenced. The live halves of AC-4 and AC-5 are
recorded by F5a on the synthetic source (revision 5). The live half of AC-3 is
recorded after the owner gates clear (F6). Revision 9 (owner decision (s4,
2026-09-25), [OQ28](../../resolved-questions.md#oq28--can-ms-2-exit-on-the-synthetic-proof-alone); R31 resolved): the
controlled synthetic Actor proof (F5a, the 18 items of *Synthetic Actor proof
acceptance*) is sufficient for the Apify portion of MS-2 exit, including MS-2
Task 6's self-owned-Apify execution and Task 7. F5b is not required to close
MS-2. It stays source- and legal-gated by OQ24, which may remain open after
MS-2 closes. Neither an MS-2 deploy nor the synthetic proof ratifies the MS-1e
drive matcher (ADR 0019, R5).

## Open risks and owner gates

| ID | Risk / gate | Owner action | Blocks |
| --- | --- | --- | --- |
| R1 | **Actor-proof source is an owner (legal) decision.** Newegg's Terms of Use (kb.newegg.com policy-agreement page, retrieved 2026-09-24) prohibit access "through any automated means, including ... scripts or web crawlers" and to "'Scrape' ... the Site for any purpose". No non-commercial carve-out was observed. Its robots.txt (retrieved 2026-09-24) fully blocks the `ChangeDetection` price-watch user agent, though it does not disallow product or search paths generally. No sanctioned data feed exists: the affiliate program (Rakuten) is link-based, and the Marketplace API is seller-only. Newegg is therefore **excluded** unless the owner decides otherwise. Whether that KB page is the footer-linked canonical ToU is unconfirmed. B&H, ServerPartDeals, and refurbished server-parts sellers are candidates only after a ToS/robots review. Apify execution does not change permissibility. Revision 5 (owner-clarified (s2, 2026-09-24)): this is now OQ24 part (b) only; each candidate needs a source-admission record (MS2-D-44), and the first Actor proof uses the synthetic source instead (MS2-D-42). | Answer OQ24 for a candidate after its admission record | F5b only |
| R2 | **Withdrawn in revision 5 (owner-overridden, MS2-D-38).** Revision 1–4 said the separate Actor repository's product-admission gate and branch rules applied to an internal Actor. Hardware Radar Actors are now built, versioned, tested, and deployed in this repository, under its own review and gate, with no dependency on that repository's process. | — | none |
| R3 | Account-level Apify configuration. **OQ23 is resolved** (revision 5, owner-overridden; `resolved-questions.md#oq23`): the ~$20 ceiling is the account's total cash outlay, the subscription fee counts, prepaid usage is not charged twice, and Hardware Radar uses at most the lesser of its $12 target and the remaining prepaid allowance (MS2-D-40). No deduction setting remains. Open owner actions: keep the account usage limit at or below the prepaid credit (verified equal, $19, 2026-09-24) and never raise it for Hardware Radar; create the scoped runtime token (the R25 residual; revision 9: the owner's dedicated key exists but is unscoped, so it is the operator/deploy credential only). Revision 12 (owner decision (s5, 2026-09-25), R25): the account limit now also caps the other workloads' spend, which the runtime cannot observe (MS2-D-48). The owner actions add setting the MS2-D-48 account settings from an operator verification, and re-verifying them before re-enabling after any plan or billing change. | Keep the limit; create the scoped runtime token | E live admission; F5a |
| R4 | GPU/RAM/CPU auto-accept needs an owner-ratified category corpus. The drive corpus does not validate other categories. | Label/ratify F4 corpora | Flipping `auto_accept` |
| R5 | MS-1e drive-matcher ratification is still pending, and all sources ship disabled. The real-observation exit proof (AC-3 live) needs the owner to enable pilot sources. | Ratify; enable | F6 |
| R6 | GPU/RAM/CPU reference seeds come from first-party pages. The ToS/licence of each manufacturer spec page should be spot-checked. Curated manual rows are the fallback. | Spot-check | B4 authoritative flag |
| R7 | Apify `usage_total_usd` is nullable, preliminary right after completion (Apify: re-read after about 10 s), and recomputed at current pricing when read later. Post-run dataset reads are account usage, not part of the run figure (revision 5 facts, MS2-D-15). MS2-D-41 therefore finalizes usage by a settle rule, writes the finalized figure once, and adds the post-run cost at its full bound until F5a measures it. Residuals: storage and transfer bounds rely on caps in reviewed Actor code (hw-radar detects a violation only after the fact, via the dataset count and the `usage` breakdown); the transfer direction is unknown, so revision 10 (entry gate, ED-01) prices every byte at the higher transfer price instead of gating on E2, and F5a measures the split; and the 10 s settle delay is unmeasured on this account. | Review the Actor PR's caps; review F5a measurements | E accuracy |
| R8 | eBay category IDs can change (quarterly category-change notices), so they are re-verified via the Taxonomy API. eBay does not separate datacenter accelerators from consumer GPUs (both are in 27386), so disambiguation falls to extraction. The Browse quota (5,000/day) is corroborated by secondary sources only; the official table footnotes Buy APIs. Per-category `total` above 10,000 can never be proven complete. | Confirm the quota via `getRateLimits` | F1 |
| R9 | The deferred MS-2a plan names migrations 0018–0026, which collide with this plan. Its own header requires a rebase at activation (D2). | Rebase at reactivation | MS-2a only |
| R10 | ADR 0022 confirmation #3 and STATUS say "shortlist + exactly-one alert". Master spec §19 puts the alert in MS-4 (applied, D1). §10.1/§11 still show score-first wording. This is a documentation conflict and was not edited here. | Reconcile docs | none |
| R11 | Soft-threshold semantics (target price annotates, never decides) are an inference from ADR 0022, not a stated rule. | Confirm or override MS2-D-07 | C |
| R12 | Watch/requirement persistence (MS2-D-07) is a plan-level decision; the master spec allows "milestone implementations", so no ADR is strictly required. Revision 5 (owner-overridden): the budget period is no longer plan discretion. The owner ruled the Apify billing cycle authoritative, and ADR 0021's 2026-09-24 amendment records it (MS2-D-17, MS2-D-40). | Decide ADR vs plan for MS2-D-07 only | none |
| R13 | Slice A intentionally adds provenance keys to new resolution edges (`category`, `category_source`) and `detail_json["provider"]`. A run whose `delist_scope()` raises no longer advances continuity. An ineligible remote run breaks continuity (MS2-D-11). Remote `complete` evidence with an incomplete or missing scope cannot delist and breaks continuity (revision 3). All of these changes are additive or strictly more conservative. | — | none |
| R14 | Legacy default `category_hint=None ⇒ drive` is a trap for any future multi-category source that forgets to hint. F1's test enforces hints for eBay category sweeps. Every new multi-category collector must copy that test. | — | F1 and later sources |
| R15 | Slice A residual, reported by the coordinator 2026-09-24. A listing whose current edge is `none` or `review` keeps a stale `evidence["category"]` when a later snapshot changes the hint, because no-spam writes no edge when the outcome is unchanged. B3 closes this by treating a category change as a decision-input change. | — | B3 |
| R16 | Evaluations become `pending` whenever new evidence arrives and evaluation fails (MS2-D-20). No scheduled backlog job exists in MS-2, so a persistently failing evaluator leaves rows pending until the next observation or `evaluate_watches --pending`. | Watch F3 pending counts | none |
| R17 | The overrun latch pauses **all** paid Apify admission until the owner resets it or the estimator version is bumped. That is deliberately blunt: one bad estimate can stop Actor-backed freshness (`budget_paused`) until owner action. | Reset after review | E live operation |
| R18 | MS2-D-29 recomputes catalog fingerprints at read time, one batched spec read per shortlist call. It has not been measured at pilot scale. | Watch F3 shortlist latency | none (reopen path in MS2-D-29) |
| R19 | MS2-D-32 pre-reserves storage over the platform's default-storage expiry (revision 11, R10-07: over `storage_hours`, at least a full billing cycle, recurring in every cycle until verified deletion), and it counts unverified post-run components as spent. Estimates are therefore deliberately high, and admission is tighter than actual spend. The operation-cap defaults (3 reads, 10 delete attempts) are assumptions. Whether `GET` run polls and the account reads are billable is unverified; revision 10 (entry gate, ED-01) counts, caps, and prices them at a conservative API-call bound (MS2-D-32), including a standing per-cycle account-read debit, and F5a measures them. Revision 5: the post-run cost also counts at its full bound until F5a measures it (MS2-D-41). | Set `…_STORAGE_MAX_LIFETIME` at or above the account's `dataRetentionDays` (revision 10); lower a bound only by a plan revision from F5a evidence | E live admission (a missing or invalid setting only) |
| R20 | MS2-D-33 denies an Actor path to every bounded-retention source, because no enforceable remote expiry within hours is confirmed. The synthetic proof source is registered indefinite (MS2-D-42). A production Actor-backed merchant source (F5b) must therefore be a merchant-fact source, or D3 must verify a per-run storage expiry. | Consider it in each source-admission record | F5b if a bounded source is chosen |
| R21 | A start whose response is lost leaves a remote run hw-radar cannot identify (`orphaned_start`). MS2-D-33 detects it at the deadline and trips the latch. Automatic discovery would need an extra list-runs call outside MS2-D-15's seven, which is not planned. | Clean up manually on report; reset the latch | E live operation |
| R22 | MS2-D-31's per-scope tolerance uses the FULL lane interval. If Actor runs rotate scopes more slowly than that, per-scope continuity keeps restarting. That fails closed (stale absence does not fire), but it can hide real absence until a complete sweep. | Revisit if per-scope cadence becomes configurable | none |
| R23 | MS2-D-35 residual. Stale-absence sweeps raise no scope watermark, and `last_seen` is `auto_now`, so an import stamps it at persistence time rather than at `observed_at`. A delayed current-eligible import therefore makes a listing look fresher to stale absence by up to the import delay, which the storage deadline bounds (default 24 h). A previously unknown key that a stale sweep would have treated as absent stays active for about one grace longer. This fails toward keeping a listing active, never toward a false delist. Preserved property (revision 5, verbatim): "a delayed import may delay a correct delist, never cause a false delist". Any improvement needs out-of-order tests (MS2-D-39). The fix, stamping `last_seen` from `observed_at` on the import path, touches the `auto_now` contract that `redact_merchant_content` documents. **Kept (Slice D entry gate, 2026-09-25; revision 10, ED-02):** an accepted MS-2 residual, with no change to `last_seen` stamping in D2 or D10. The effect only ever keeps a listing active; it is bounded by `storage_cleanup_due_at − startedAt ≤ …_STORAGE_CLEANUP_MAX` (default 24 h), because stage 1 re-checks the deadline; it touches only stale absence, which remote runs never reach, and bounded classes have no Actor path (MS2-D-33), so it affects only a merchant-fact source's stale sweep after a switch back to local. Stamping from `observed_at` would need `QuerySet.update()` writes that bypass the `auto_now` contract `mark_delisted`, `redact_merchant_content`, and `redact_expired` rely on (`market.py:285-290`, `:369`, `:399-402`, `:477-479`), or removing `auto_now` across every local writer and the frozen tests that backdate `last_seen`. The property is pinned by MS2-D-30's binding `last_seen` rule and D10's tests `test_current_eligible_observation_bumps_last_seen_and_ineligible_does_not`, `test_listing_observed_in_previous_run_is_not_stale_delisted_within_grace`, and `test_delayed_import_bumps_last_seen_only_forward_and_only_when_current_eligible`. *Reopen if* a bounded source gains an Actor path, or remote runs become stale-eligible. | — (accepted at the entry gate) | none |
| R24 | **Resolved (owner decision, 2026-09-25; [OQ25](../../resolved-questions.md#oq25--hardware-radar-apify-credential-and-mcp-tool-scope)).** `.mcp.json` stays unchanged, with the four anonymous read-only Apify tools, so MCP cannot yet inspect runs, logs, datasets, or KV records. MS2-D-43's read-only list is the target filter, enabled only once a scoped read credential and operator reservations (MS2-D-46) exist. `call-actor`, the RAG web browser, abort, and the task tools stay excluded. The unscoped operator/deploy key is not an MCP credential, and any MCP token stays out of this public repository. MCP is an operator surface, not the runtime protocol; MCP dataset reads are billed account usage (operator allowance). | — (enable the target filter only when its conditions hold) | none; operator inspection uses the CLI or Console under reservations until then |
| R25 | **Partly resolved (owner decision, 2026-09-25; [OQ25](../../resolved-questions.md#oq25--hardware-radar-apify-credential-and-mcp-tool-scope)).** The owner created a dedicated Hardware Radar Apify key, stored at OpenBao `secret/apps/hw-radar/agent/apify`, distinct from the apify-actors venture's token, which Hardware Radar never uses. A 2026-09-25 capability probe showed it is unscoped (full account): it reads account limits and monthly usage and can create Actors, which Apify never allows a scoped token to do. It therefore serves the operator/deploy role only and is never rendered to the production app environment (MS2-D-43). **Residual:** the runtime role needs a separate, owner-created scoped token at `secret/apps/hw-radar/apify`, rendered as `HW_RADAR_APIFY_TOKEN`; its production rendering is deferred until Slice E live admission is ready. Unverified until it exists: whether a scoped token can read `/users/me/limits` and `/users/me/usage/monthly` (MS2-D-15, -40; if not, admission denies with `account_state_unobservable`), and whether the owner's scope (run Hardware Radar-owned Actors, read their runs and default storages) also covers the storage deletes MS2-D-33 needs. Revision 10 (entry gate, ED-19): the same capability probe records whether a delete against storage the token cannot access returns 403 rather than 404, using a throwaway storage created with the operator key under an operator reservation; only then may `…_DELETE_404_IS_ABSENT` be set true (MS2-D-25). Until then a 404 on delete is a failed attempt, which fails closed. **Revision 12 (owner decision (s5, 2026-09-25), R25).** The scoped runtime token exists (2026-09-25). Its account reads return 403, and Apify offers no scoped account permission, so the owner dropped runtime account reads (MS2-D-48, [OQ30](../../resolved-questions.md#oq30--runtime-apify-account-reads-r25)). The account-read part of this residual is **closed**. Still open: the token needs **Read** on the Hardware Radar Actor for `GET` runs and builds (its pre-probe got 404), and the 403-versus-404 delete probe under an operator `probe` reservation (`…_DELETE_404_IS_ABSENT` stays false until then). | Create the scoped runtime token; confirm its account reads, storage deletes, and 403-for-inaccessible behavior | D3 live verification; E live admission; F5a |
| R26 | Usage finalization is unverified on this account: Apify documents a preliminary first figure and advises a re-read after about 10 s, but the actual settle time is unmeasured. Revision 6 (R5-03): elapsed time is only a minimum delay; settlement needs identical consecutive reads, and the default `…_RUN_USAGE_SETTLEMENT=bound` returns no capacity until F5a's readings converge. | Review F5a's finalization trail; approve `stable_reads` only if it converges | E accuracy |
| R27 | Post-run consumption (dataset reads, storage, transfer) is unmeasured, so the post-run cost counts at its full bound, which makes admission tighter than actual spend. The account is shared: other workloads (the apify-actors venture) reduce the prepaid allowance Hardware Radar may use (bounded only by R33). Revision 6: two environments in one cycle are governed by the ledger authority and drained handoff (MS2-D-45), replacing revision 5's target reduction. | Approve `counted` mode after F5a | E live admission |
| R28 | The residential-proxy feature is available on the account (verified 2026-09-24), so nothing at the account level stops an Actor from using it. Code tests, the input schema, and the proxy usage latch are the controls (MS2-D-26, MS2-D-38, MS2-D-44). | — | none |
| R29 | Monorepo `apify push` scoping is unconfirmed: whether it uploads only `actors/<name>/` when run there. D-prep verifies it through the version's `sourceFiles`; the fallback is a Git-source Actor with no push webhook (MS2-D-43). | — | F5a deployment |
| R30 | The platform limit may deviate by up to about 10% at enforcement (Apify help center). The account limit is therefore a secondary backstop, not proof of zero overage. Revision 6 (R5-02): the margin does not bound other workloads either; Hardware Radar's own admission stays within the owner-declared share (R33), and whole-account safety also depends on other workloads honoring that bound. Whether the platform aborts a running run at the limit is unverified, and the enforcement experiment must not run on the shared account without owner sign-off. | Sign off before any enforcement experiment | none |
| R31 | **Resolved (owner decision, 2026-09-25; [OQ28](../../resolved-questions.md#oq28--can-ms-2-exit-on-the-synthetic-proof-alone)).** The controlled synthetic Actor proof (F5a) is sufficient for the Apify portion of MS-2 exit, including Task 6's self-owned-Apify execution. F5b is not required to close MS-2; it stays source- and legal-gated by OQ24, which may remain open after MS-2 closes. | — | none |
| R32 | **Resolved (owner decision, 2026-09-25; [OQ27](../../resolved-questions.md#oq27--retention-class-for-non-first-party-reference-data)).** MS-2 adds no production retention class for non-first-party reference data: no `third_party_reference` class and no rewrite of the `*_retention_ttl_coherent` CHECKs. Authoritative reference seeds come only from first-party, manufacturer-authoritative sources; non-first-party PDFs and pages may inform research or manual review but never automatically seed authoritative aliases or specs. The importer's refusal of `non_first_party` documents (MS2-D-06) is the MS-2 behavior. B4c's RAM expansion, whose only purpose was admitting three third-party-hosted Micron PDFs, is withdrawn; the existing first-party RAM rows stay. Reopen only with a concrete source, intended use, provenance model, and actual need. | — | none |
| R33 | **Resolved (owner decision, 2026-09-25; [OQ26](../../resolved-questions.md#oq26--external-liability-bound-for-the-shared-apify-account)).** `HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD` is the maximum that other workloads sharing the Apify account may consume in one billing cycle (equivalently, `P −` it is Hardware Radar's allocated share). It defaults to the owner-set 5.00 per cycle: Hardware Radar reserves up to $5.00 of the prepaid usage for consumption outside its ledger. It is a conservative Hardware Radar accounting bound, not permission for another project to spend $5. Paid admission still fails closed when the value is explicitly empty or invalid, when the account snapshot is stale or unobservable, and when the invariant cannot be satisfied (MS2-D-40 check 2). Residual: Hardware Radar derives the bound from no other workload's code or records, cannot enforce it on those workloads, and detects a breach only when a snapshot shows it (`external_liability_exceeded` trips the latch). **Rev-12 Codex follow-up (R12-05).** Revision 12 supersedes the "stale or unobservable snapshot" denial and the detection through `external_liability_exceeded` in this row (MS2-D-48). Admission now fails closed on an explicitly empty or invalid bound, an invalid operator-verified account setting (`account_state_unobservable`), and a failed check 2 against the configured `P`. A breach by other workloads is caught only by Apify's hard limit (402 → `account_limit_refused`) and by the operator's cycle reconciliation (R39). | — (changing the bound is an owner decision) | none |
| R34 | Revision 6 admission is deliberately conservative and may leave Hardware Radar well under its $12 target: reconciled spend is debited on top of the snapshot while no inclusion watermark exists (R5-01); run usage settles at its execution bound until F5a supports `stable_reads` (R5-03); and the external-liability bound is reserved in full. With the verified figures ($19 prepaid, $1.90 margin) and no watermark, late-cycle headroom falls roughly by Hardware Radar's own settled spend. Revision 9: with the owner's 5.00 bound, check 2 caps Hardware Radar's cycle debit at $12.10 ($17.10 − $5.00), just above the $12 target, so at the verified figures the target binds before check 2. **Rev-12 Codex follow-up (R12-05).** Revision 12 supersedes this row's snapshot debit and its `…_USAGE_INCLUSION_LAG_S` action: the setting and check 1 are retired (MS2-D-48). The remaining conservatism is the `stable_reads` default and the full `E` reservation. At the verified figures, check 2 still caps `HR_cycle` at $12.10, and the target binds first. | Set `…_USAGE_INCLUSION_LAG_S` and `stable_reads` only from F5a evidence; rev 12: set `stable_reads` only from F5a evidence (the inclusion-lag action is withdrawn) | E live capacity |
| R35 | The first ledger authority in a cycle (`apify_ledger_claim --origin`, MS2-D-45) rests on an owner attestation that no other environment admitted paid work that cycle; every later authority is machine-checked (continuation, or a drained handoff bound to one destination ledger, revision 7). Residual: a handoff record is a digest-keyed file, not a cryptographically signed one, so the checks protect against mistakes, not against deliberate hand-editing. | Attest only when true | E live admission |
| R36 | Operator reservations (MS2-D-46) are a procedural control: the Console, CLI, and MCP cannot be intercepted, so an operation run without a reservation is unaccounted. Revision 9 (owner decision, 2026-09-25; [OQ29](../../resolved-questions.md#oq29--operator-allowance-size)): the allowance defaults to 1.00 per cycle, replacing the 0.50 assumption under which one build bound ($0.41) nearly filled it. Revision 11 (R10-12) states the fit by formula. One build reservation is `build_reservation = …_OPERATOR_BUILD_BOUND_USD + (…_MAX_RUN_POLLS + …_MAX_CORRECTION_READS) × api_call_bound` (MS2-D-46). Two fit when `2 × build_reservation ≤ …_OPERATOR_ALLOWANCE_USD < 3 × build_reservation`. At the defaults and Starter prices (approximate; `api_call_bound ≈ $0.000181`, MS2-D-32), one is about $0.423, two about $0.846 (about $0.154 left for inspection and probe envelopes), and a third, about $1.269, does not fit. Revision 9's "$0.82 … leaving $0.18 … $1.23" omitted the call allowance. Under the default `bound` settlement a settled build returns little capacity, so at most two builds fit per cycle, fewer when inspection envelopes are reserved, until F5a evidence allows `stable_reads`. | Reserve before every build or inspection; changing the allowance is an owner decision | F5a deployment cadence |
| R37 | (Revision 7; revised in revision 8.) Correction monitoring (MS2-D-41, selector 4) runs for a fixed window after a row's last charge (default seven days, an assumption) and closes only when a successful closing read at or after the deadline commits. A provider correction that arises after that closing read is not observed by any environment; window closure is not provider finality. Under the default `bound` settlement the settled amount already equals the enforced execution bound, so the exposure matters mainly under `stable_reads`. The window delays a drained handoff by at least one window (MS2-D-45). Revision 8 residual: a closing read that can never succeed (for example, a run or build record no longer returned) keeps its obligation open, visible as `correction_close_overdue`, and blocks handoff indefinitely; that fails closed, and any release of such an obligation would need a plan revision. | Choose `stable_reads` only if F5a's read trail shows no correction after half the window | E accuracy under `stable_reads`; handoff timing |
| R38 | **Owner acceptance required (revision 11, R10-01).** Apify's documents name the billing units: compute, data transfer, proxy, and storage reads, writes, lists, and timed storage (`docs.apify.com/platform/actors/running/usage-and-resources`, `apify.com/pricing`, retrieved 2026-09-25). They do not state (a) how many operations one API call is metered as, (b) which bytes are metered as transfer, or (c) the per-call overhead of the Actor SDK's platform calls inside a run. MS2-D-32 *Per-call bound* derives everything the documents support and enforces wire-byte ceilings: a response-body cap, httpcore's response-header limit, a request-body cap, and a pinned socket receive buffer. For the rest it assumes (a) at most one operation per item or record a call returns or deletes, and one for a call that returns none; (b) at most the call's wire bytes; and (c) that the run's own usage breakdown, settled at `max(execution bound, every observed read)`, reveals the overhead, with an actual above the reservation tripping the latch. If an assumption fails, the enforced call counts limit the damage, but no documented monetary ceiling exists. Snapshot check 1 and `external_liability_exceeded` (MS2-D-40) detect it after the fact, because the account figure contains any under-priced charge. At the defaults, the modeled per-run call and transfer bounds come to a few cents (MS2-D-32 illustrative figures). Revision 12 (owner decision (s5, 2026-09-25), R25): the after-the-fact runtime detection by check 1 and `external_liability_exceeded` is retired. Detection is now the operator's cycle reconciliation, F5a step 4, and Apify's 402 (`account_limit_refused`). The owner accepted R38 before this change; R39 asks whether that acceptance still holds. **Rev-12 Codex follow-up: owner answer 2026-09-25, "Still accepted".** The R38 acceptance stands without after-the-fact runtime detection. F5a step 4 still measures API-call billing on the operator side. A measured real per-call charge revises the estimator bound then, through a plan revision. The operator's end-of-cycle reconciliation (MS2-D-48) compares the ledger with account usage. | **Accepted by the owner 2026-09-25** (`…_CALL_BILLING_RESIDUAL_ACCEPTED` defaults to that date). Re-review if F5a's measurements contradict an assumption | E live admission; F5a |
| R39 | **New (revision 12, owner decision (s5, 2026-09-25), R25).** The runtime no longer observes account state (MS2-D-48). (a) The other workloads' actual spend is invisible. Only `E` (5.00) and Apify's hard limit (verified $19, equal to the prepaid credit) bound it, and Apify documents enforcement deviation of about 10%. (b) Cycle drift: a plan or billing change moves the real cycle, and the runtime keeps the configured anchor until the operator re-verifies. Hardware Radar's spend in one real cycle could then span two derived cycles. Its cash exposure stays behind the account limit, but it could consume the other workloads' share. (c) The configured limit, base price, and retention can go stale in the same way. Mitigations: a billing change is an owner action; re-verification is required before re-enabling; a 402 start refusal latches (`account_limit_refused`); the operator's cycle reconciliation; a backward anchor move denies `cycle_unknown`. **Owner points:** (1) whether to add a re-verification age, for example `…_ACCOUNT_VERIFIED_ON` within the current cycle; (2) whether R38's acceptance stands without after-the-fact runtime detection; (3) whether master spec C-011's "prepaid allowance actually remaining" is satisfied by Apify's limit, or needs a spec clarification. **Rev-12 Codex follow-up: owner points closed 2026-09-25 ([OQ30](../../resolved-questions.md#oq30--runtime-apify-account-reads-r25)).** (1) "No expiry; warn only": no admission expiry, and `apify_spend_report` warns when `…_ACCOUNT_VERIFIED_ON` predates the current cycle start (E9.6). (2) "Still accepted": R38 stands (see R38). (3) "Clarify the spec": master spec C-011 carries a dated 2026-09-25 clarification. The runtime plans against the operator-verified account limit minus the margin, Apify's hard limit enforces the account-wide remaining allowance, and the operator reconciles each cycle. The residuals (a)–(c) stay accepted with those mitigations, plus the durable 402 recovery (R12-01) and the recovery-verification procedure (R12-03). | Closed 2026-09-25 (owner answers 1–3); operator verification before enabling | E9; F5a |

No new ADR or OQ file is created by this plan. Revision 5: OQ23 is resolved and
OQ24 split by the owner's 2026-09-24 decisions, recorded in
`resolved-questions.md`, `open-questions.md`, and ADR 0021's amendment (not by
this plan). Revision 9: OQ25–OQ29 are resolved by the owner's 2026-09-25
decisions ([OQ25](../../resolved-questions.md#oq25--hardware-radar-apify-credential-and-mcp-tool-scope),
[OQ26](../../resolved-questions.md#oq26--external-liability-bound-for-the-shared-apify-account),
[OQ27](../../resolved-questions.md#oq27--retention-class-for-non-first-party-reference-data),
[OQ28](../../resolved-questions.md#oq28--can-ms-2-exit-on-the-synthetic-proof-alone),
[OQ29](../../resolved-questions.md#oq29--operator-allowance-size)), recorded in `resolved-questions.md`
and ADR 0021's 2026-09-25 amendment (not by this plan). R24, R31, R32, R33, and
R36 are resolved, and R25 is resolved except for its runtime-token residual.
Revision 12 (owner decision (s5, 2026-09-25), R25): that residual's account-read part is closed
by MS2-D-48. Its Actor Read grant and delete-probe parts stay open.
R12 still offers an optional ADR for MS2-D-07. The owner items this plan still
needs are:

- the scoped runtime token at `secret/apps/hw-radar/apify` and the
  verification of its account reads (the R25 residual);
  Revision 12 (owner decision (s5, 2026-09-25), R25): the token exists and cannot read the
  account, and MS2-D-48 removes the need. What remains is the token's Actor
  **Read** grant, the delete probe, and the operator verification that sets
  the five MS2-D-48 settings;
- (revision 12) the R39 owner points: a re-verification age, whether R38's
  acceptance stands, and the C-011 wording;
  Rev-12 Codex follow-up: all three were answered by the owner on 2026-09-25 (R39,
  OQ30) and are no longer open;
- the R35 attestation at the first origin claim;
- F4 corpus labeling and ratification (R4), and the MS-1e drive-matcher
  ratification and pilot-source enabling (R5), which stays a distinct gate that
  neither an MS-2 deploy nor the synthetic proof satisfies;
- OQ24 for any production Actor-backed merchant source (F5b), which is not an
  MS-2 exit condition;
- (revision 11) acceptance or rejection of **R38**, the residual on per-call
  billing multiplicity and metered bytes, before any paid admission. It is
  an owner decision, not F5a evidence, so F5a does not gate itself. A
  rejection keeps paid admission, and therefore F5a and the MS-2 Apify exit,
  denied until a documented unit or an enforceable ceiling replaces the
  assumption.

The standing owner actions in R3, R17, and R30 are unchanged. Revision 10
(Slice D entry gate) adds no owner item. It closes R23 as an accepted
residual. It extends the R25 capability probe with the 403-versus-404
delete check, and R7 and R19 with the conservative bounds that replace the
E2 billing gates. Lowering any revision-10 bound needs F5a evidence and a
plan revision. Revision 11 adds one owner item, R38. It lowers no bound, and
it raises the account-read debit and the build reservation by formula
(MS2-D-32, R36).

## Review lineage

**Round 1: cross-agent delegate `42deeff0`** (opposite provider, static
read-only review of revision 1 at `df114de`).
- **Verdict:** REVISION NEEDED, with 12 findings (F-01..F-12).
- **Disposition:** all 12 ACCEPTED and resolved in revision 2. The rows below
  describe revision 2. Where round 2 found a residual (F-03, F-04, F-07, F-08,
  F-10), the round-2 table supersedes the row, and where round 3 found one
  (F-08), the round-3 table supersedes both. Each table records the revision
  that answered it; the decision text above is the current contract.
- The review's "verified correct" items stand unchanged: the migration head, the
  spine and satellite pattern, the listing and snapshot keys, the three veto
  sites, site-wide absence, the gate contents, and the milestone boundary.
- Slice A text also reflects what landed for A0–A3 and the Slice A verifier's
  gate tightening.

| Finding | Sev. | Disposition | Plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| F-01 veto result locals collide with the injected callable | medium | Accepted. The locals are renamed `vetoed` (as landed); the evidence is byte-identical; basedpyright is added to A1 | MS2-D-02 *Ladder veto*; A1 step 2 and step 5 | `test_ladder_veto.py::test_custom_veto_is_consulted_at_rung1`, `::test_custom_veto_is_consulted_at_rung0_prior`; A0 `test_ladder_golden_baseline`; `basedpyright` 0 errors |
| F-02 map misses direct callers; F4 loses the category hint | high | Accepted. Map Q3 is corrected with grep-verified callers. A backward-compatible corpus schema, harvest, and replay task lands in B (MS2-D-27 says why B, not F) | MS2-D-10 *Callers outside the seam*; MS2-D-27; B6; F4; map Q3 | `test_corpus_category_hint.py::test_harvested_non_drive_entry_reaches_category_rules`, `::test_unhinted_staging_entry_bytes_unchanged`, `::test_existing_drive_corpus_loads_unchanged` |
| F-03 stale eligibility stays a shortlist match | high | Accepted. An evaluation is bound to its snapshot, resolution, requirement, and evaluator version. A non-current row is `pending` and never qualifies. The production evaluator is the `run_collection` default, so poll, heartbeat, probe, and import paths all evaluate | MS2-D-09; MS2-D-20; C1, C3, C4; MS2-D-22 stage 4 | `test_evaluation_binding.py::test_prior_match_then_contradictory_evidence_updates_verdict`, `::test_evaluator_failure_after_prior_match_leaves_shortlist`, `::test_heartbeat_fired_run_updates_watch_evaluation`, `::test_recovery_probe_run_evaluates`; `test_shortlist.py::test_prior_match_then_contradictory_evidence_leaves_shortlist` |
| F-04 skipped continuity lets truncated runs bridge a gap | high | Accepted (orchestrator decision). An ineligible successful FULL run **breaks** continuity via `_break_sweep_continuity`; eligible runs are unchanged; rejected remote runs also break it | MS2-D-11 *Continuity*; A5 step 2; A6; Slice A acceptance; R13 | `test_collection_provider.py::test_ineligible_run_breaks_existing_continuity`, `::test_local_after_prolonged_truncated_remote_cannot_mass_delist`; `test_apify_import.py::test_ambiguous_empty_run_cannot_delist` |
| F-05 import commit point precedes required work | high | Accepted. A durable stage machine runs pending → observations_committed → absence_applied → resolved → evaluated → finalized, with compare-and-set transitions, `ScraperRun` reuse, and the terminal marker only after every effect | MS2-D-13; MS2-D-22; D10 | `test_apify_import.py::test_crash_after_observation_commit_resumes_without_duplicates`, `::test_crash_during_resolution_resumes_and_evaluates`, `::test_crash_during_evaluation_resumes_and_evaluates`, `::test_crash_before_finalization_finalizes_once`, `::test_retry_reuses_scraper_run` |
| F-06 poller selection excludes the work it promises to retry | high | Accepted. Remote status is separate from local work, and two selectors run: active, and outstanding (import, cleanup, reconcile, late usage) | MS2-D-16; MS2-D-23; D5; D10; E4 | `test_apify_poll_job.py::test_outstanding_selector_picks_terminal_rows_with_unfinished_import`, `::test_restart_recovers_each_unfinished_import_state`, `::test_restart_retries_storage_cleanup_after_finalized_import`; `test_apify_ledger.py::test_late_usage_reconciled_on_later_tick` |
| F-07 recovery probes ignore the selected provider | high | Accepted. Probes dispatch by provider. A remote probe gets budget admission (discovery class), a bounded start, async completion, retention, and PROBE semantics, and it never authorizes absence | MS2-D-16; MS2-D-24; D12; E8 | `test_apify_recovery_probe.py::test_local_success_cannot_clear_actor_provider_failure`, `::test_budget_admitted_actor_probe_recovers_source`, `::test_actor_probe_never_delists_or_touches_continuity`; `::test_budget_admitted_actor_probe_recovers_source_with_ledger` |
| F-08 estimate called a hard bound without bounding every cost | high | Accepted. Every charge component is bounded by an enforced option or disallowed (no proxies). There is a safety margin below $20, the OQ23 deduction gates live admission, and an overrun latch applies. Reconcile and admission share one lock. Attribution is pinned at `reserved_at`, the window is 31 days + max timeout, and unreconciled rows never age out | MS2-D-17; MS2-D-26; E1–E4; R3; R7; R17 | `test_apify_budget.py::test_exact_boundary_admitted_and_epsilon_over_denied`, `::test_unset_cap_deduction_denies_live_admission`; `test_apify_ledger.py::test_overrun_latch_denies_admission_until_reset`, `::test_reconcile_concurrent_with_admission_serializes`, `::test_run_spanning_window_boundary_is_counted`, `::test_concurrent_reservations_one_admitted_at_boundary`, `::test_unreconciled_reservation_never_ages_out`, `::test_proxy_usage_trips_latch` |
| F-09 no authoritative-alias gate for new categories | high | Accepted. For new categories a category `AcceptancePolicy` allows only `catalog_authoritative` hits at model or variant grain. Manual and listing-derived aliases stay reviewable; drive is untouched | MS2-D-05; MS2-D-21; B3 | `test_resolver_categories.py::test_non_authoritative_alias_never_auto_accepts_new_category`, `::test_alias_learned_from_earlier_observation_goes_to_review`, `::test_family_grain_fanout_is_review_for_new_categories`, `::test_prior_from_non_authoritative_edge_is_review`, `::test_drive_acceptance_unchanged` |
| F-10 remote retention lacks an execution and cleanup path | high | Accepted. A code registry of source retention is forwarded explicitly on every remote import, and unknown retention is rejected. Deadline-based cleanup of the dataset and KV store covers every run outcome, with retries | MS2-D-13; MS2-D-14; MS2-D-25; D4; D11 | `test_apify_retention.py::test_bounded_source_import_retains_listings_snapshots_raw_and_evaluations`, `::test_unregistered_source_import_rejected_not_merchant_fact`; `test_apify_storage_cleanup.py::test_cleanup_after_rejected_import`, `::test_cleanup_of_abandoned_run_at_deadline`, `::test_cleanup_failure_retries_with_backoff` |
| F-11 complete-empty enumeration classified as failure | medium | Accepted. Proven complete-empty (all counts zero, complete evidence) is `complete`; non-empty unusable output and ambiguous empty output are `failed`. OUTPUT is not a `RawItem`, so the parser-rot guard holds | MS2-D-14; D1; D4; D8 | `test_apify_contract.py::test_complete_empty_requires_all_zero_counts_and_pages_fetched`, `::test_empty_without_evidence_is_failed_ambiguous`; `test_apify_import.py::test_complete_empty_run_delists_scope_end_to_end`, `::test_ambiguous_empty_run_cannot_delist`, `::test_nonempty_unusable_run_is_rejected_and_cannot_delist` |
| F-12 invalid D-before-B merge alternative | medium | Accepted. The alternative is removed. D is split into D-prep (D1 and D3, depending on A only) and D (core, depending on C and D-prep). Merge order follows the dependency graph | *Slice order and migration assignment*; Slice D *PRs* | Dependency table; no migration in D-prep (`makemigrations --check` in D-prep's gate) |

**Also folded in revision 2** (not review findings):
- The Slice A verifier's `gate_delist_scope` tightening (MS2-D-11, A4, A6).
- The landed A0–A3 details (MS2-D-02, A0–A3).
- The MS2-D-28 soft-block hazard, found while re-reading `_classify_batch`.
- The R15 Slice A residual, closed in B3.
- The prep evidence: eBay categories (F1, R8), Apify API facts (MS2-D-15),
  Newegg terms (R1, MS2-D-19), and Slice B reference-data sources and importer
  gaps (MS2-D-06, B4a–B4c).

**Round 2: cross-agent delegate `537cd698`** (opposite provider, static
read-only review of revision 2 at `e82a83a` plus the Slice A code).
- **Verdict:** REVISION NEEDED. Seven round-1 findings were RESOLVED (F-01,
  F-02, F-05, F-06, F-09, F-11, F-12), five were PARTIAL (F-03, F-04, F-07,
  F-08, F-10), and three findings were new (N-01, N-02, N-03).
- **Disposition:** all eight open items ACCEPTED and resolved in revision 3.
  F-04 is resolved by an orchestrator decision implemented in Slice A code.
  The rest are plan changes for Slices B–E.
- The review's "verified correct" items stand: callable veto sites, category
  dispatch, the scope gate, the continuity break for non-complete evidence,
  and migration ordering from `0018`.
- Round 3 found residuals in F-08, N-01, and N-02. For those three, the
  round-3 table supersedes the rows below.

| Finding | Sev. | Status | Decision / plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| F-03 residual: binding omits mutable catalog inputs | high | Accepted; resolved | MS2-D-29 (new; read-time catalog fingerprint; atomic invalidation rejected); MS2-D-09; MS2-D-20 *Validity* (five conditions); C1, C3, C4; Slice C acceptance | `test_evaluation_binding.py::test_same_target_catalog_correction_excludes_match_from_shortlist_immediately`, `::test_catalog_correction_by_queryset_update_is_detected`, `::test_family_membership_change_makes_family_grain_evaluation_pending`, `::test_unrelated_catalog_edit_keeps_evaluation_current` |
| F-04 residual: complete evidence + incomplete scope still counted toward continuity | high | Accepted (orchestrator decision); resolved in Slice A code | MS2-D-11 *Continuity* (`counts_toward_sweep_continuity(evidence, scope)`: local unchanged; non-local needs complete evidence and a complete scope); A4, A5, A6; *Revision 3 correction*; Slice A acceptance; R13 | `test_provider_evidence.py::test_remote_complete_evidence_with_incomplete_or_missing_scope_does_not_count`, `::test_local_continuity_mapping_unchanged`; `test_collection_provider.py::test_remote_complete_evidence_incomplete_scope_breaks_continuity_then_local_cannot_stale_delist` |
| F-07 residual: Reject broke continuity for probes | medium | Accepted; resolved | MS2-D-22 *Reject* (FULL only; PROBE → `PROBE_FAILURE`); MS2-D-24 *Probe outcome*; MS2-D-11; D12 | `test_apify_recovery_probe.py::test_rejected_probe_records_probe_failure_and_leaves_continuity_unchanged` [invalid OUTPUT, ambiguous-empty, storage deadline] |
| F-08 residual: reservation not an upper bound on authorized work | high | Accepted; resolved | MS2-D-32 (new; operation caps, execution/post-run split, settlement after the final charge-producing operation, `unbounded_component`); MS2-D-17 *Reconciliation*; MS2-D-26 table, *Reservation*, *Window*, *Late usage*; MS2-D-13 counters; MS2-D-22 stage 1; E settings, E1–E4; R7, R19 | `test_apify_ledger.py::test_repeated_pre_commit_reads_are_capped_and_reserved`, `::test_delayed_deletion_keeps_storage_liability_outstanding`, `::test_non_null_usage_before_cleanup_completes_does_not_release_liability`, `::test_usage_read_before_final_charge_op_does_not_reconcile`, `::test_delete_cap_exhausted_trips_latch`; `test_apify_budget.py::test_estimate_includes_capped_reads_deletes_and_storage_lifetime`, `::test_unset_storage_lifetime_denies_live_admission`; `test_apify_import.py::test_stage1_rollback_retry_counts_each_dataset_read`, `::test_read_cap_exhausted_rejects_import_and_cleans_up` |
| F-10 residual: cleanup clock started at observed termination | high | Accepted; resolved | MS2-D-33 (new; absolute deadline at admission, bounded-source Actor denial, overdue selector, expired-content rejection); MS2-D-25 *Cleanup deadline* and *Cleanup*; MS2-D-23 selector 3; MS2-D-13; MS2-D-15 abort; MS2-D-16; D5, D11; F5; R20, R21 | `test_apify_storage_cleanup.py::test_restart_after_source_ttl_rejects_expired_content_before_persistence`, `::test_unobserved_run_past_deadline_is_aborted_and_cleaned`, `::test_deadline_is_anchored_at_admission_not_terminal_observation`, `::test_timeout_exceeding_retention_window_denied`, `::test_bounded_source_actor_start_denied`; `test_apify_poll_job.py::test_overdue_selector_picks_rows_past_deadline_regardless_of_remote_status`; `test_apify_ledger.py::test_orphaned_start_trips_latch` |
| N-01: delayed imports lack ordering against newer observations | high | Accepted; resolved | MS2-D-30 (new; `Listing.last_observed_at` watermark; guarded upsert, relist, and delist under row locks; history appends); MS2-D-12; MS2-D-13 item 5; MS2-D-22 stages 1–2; D2, D10 | `test_apify_import_ordering.py::test_reverse_completion_older_import_does_not_overwrite_newer_listing_state`, `::test_older_import_does_not_revive_listing_delisted_by_newer_evidence`, `::test_older_complete_import_does_not_delist_listing_observed_by_newer_run`, `::test_delayed_remote_import_after_switch_back_to_local_preserves_newer_content_and_delist_state`, `::test_legacy_listing_without_watermark_accepts_first_observation` |
| N-02: continuity is per source, not per scope | high | Accepted; resolved by per-scope tracking in Slice D (migration `0021`); disabling stale absence for new scopes rejected | MS2-D-31 (new; `scope_sweep_continuity`; the NULL scope keeps the lane mechanism plus a break rule); MS2-D-11; MS2-D-12; MS2-D-22 stage 2 and *Reject*; D2, D10; F1; R22 | `test_scope_continuity.py::test_scope_a_continuity_does_not_authorize_first_incomplete_scope_b_sweep` [non-null B, NULL B], `::test_remote_scope_a_runs_then_local_incomplete_scope_b_sweep_cannot_stale_delist`, `::test_run_not_sweeping_null_scope_breaks_null_scope_continuity`, `::test_older_eligible_sweep_does_not_move_scope_watermark_back`, `::test_legacy_null_scope_only_runs_keep_todays_continuity` |
| N-03: Slice B contradicts frozen Slice A registry tests | medium | Accepted; resolved | Slice B *Files* (narrow authorization; `zz-unregistered` sentinel); B3 tests; Slice B acceptance and exit evidence | renamed `test_categories.py::test_registered_categories_matches_ms2_registry`; `::test_rules_for_unregistered_is_none` and `test_resolver_dispatch.py::test_unregistered_hint_writes_none_edge_and_never_runs_drive_rules` on `zz-unregistered`; A0 `test_ms2_decision_baseline.py` unchanged |

**Migrations:** no number changes. The new schema lands in migrations that
have not yet merged:
- `0020`: `watch_evaluation.catalog_fingerprint`;
- `0021`: `Listing.last_observed_at`, `scope_sweep_continuity`, and the new
  `provider_run` columns;
- `0022`: the reservation status values and liability columns.

**Round 3: cross-agent delegate `f1b156e6`** (opposite provider, static
read-only review of revision 3 at `f4dab71` plus the Slice A code).
- **Verdict:** REVISION NEEDED. Five items were RESOLVED (F-03, F-04, F-07,
  F-10, N-03), three were PARTIAL (F-08, N-01, N-02), and one finding was new
  (M-01). Slice B was found unblocked.
- **Disposition:** all four open items ACCEPTED and resolved in revision 4 as
  plan changes to Slices D and E. No Slice A, B, or C contract changes;
  MS2-D-11 gains only a forward note to MS2-D-36. A focused design review now
  gates Slice D (core) (*Slice D entry gate*).
- The review's "verified correct" items stand: the F-04 gate and reset in
  Slice A code, catalog fingerprinting, state-neutral rejected probes,
  admission-time deadlines with the bounded-source Actor denial, Slice B's
  narrow test-update authorization, and the migration assignment B
  `0018`/`0019`, C `0020`, D `0021`, E `0022` on head `0017`.

| Item | Sev. | Status | Decision / plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| F-08 residual: settled spend aged out by `reserved_at` although cleanup may succeed later | high | Accepted; resolved | MS2-D-34 (new; `last_charge_at` window anchor; `reserved_at` provenance only; component-time accounting rejected); MS2-D-26 *Window and attribution*; MS2-D-32 *Window*; *Things not to do*; E1 (`last_charge_at`, index), E3, E4, E6; Slice E scope and acceptance | `test_apify_ledger.py::test_late_cleanup_settled_spend_counts_until_31_days_after_final_charge`, `::test_reconciled_spend_rolls_off_31_days_after_last_charge` (replaces `::test_reconciled_spend_rolls_off_after_window`), `::test_unreconciled_reservation_never_ages_out` |
| N-01 residual: current-content writes and unknown-key creation ignore newer absence | high | Accepted; resolved | MS2-D-35 (new; `Listing.last_absence_at`; per-scope `last_complete_sweep_at`; one current-eligible predicate for content, relist, and creation; unknown keys older than the scope watermark created as delisted history anchors; scope-row lock serialization); MS2-D-30 *Watermarks* and *Guards*; MS2-D-22 stages 1–2; D2, D10; Slice D acceptance; R23 | `test_apify_import_ordering.py::test_delayed_observation_after_newer_delist_restores_no_content_and_keeps_expiry` [delete-on-delist (stays redacted), merchant-fact] × [complete, stale delist], `::test_delayed_import_of_unknown_key_after_newer_complete_sweep_creates_no_active_listing` [non-null, NULL scope] × [merchant-fact, bounded delete-on-delist], `::test_delayed_observation_cannot_relist_row_delisted_before_newer_complete_sweep`, `::test_stage1_creation_and_newer_complete_sweep_serialize_on_scope_row`, `::test_delayed_remote_import_after_switch_back_to_local_preserves_newer_content_and_delist_state` (redaction assertion kept, content and expiry assertions added); `test_scope_continuity.py::test_only_gated_complete_full_scopes_raise_complete_sweep_watermark` |
| N-02 residual: NULL-scope break reversible by a delayed older run | high | Accepted; resolved | MS2-D-36 (new; per-scope `last_eligible_sweep_at` + `continuity_broken_at`, NULL scope on the FULL lane row; breaks carry event time; the NULL scope keeps the `ScraperRun` predecessor lookup so frozen eBay pause tests keep their meaning); MS2-D-31 *Legacy NULL scope*, *Record and break*; MS2-D-11 *Continuity*; MS2-D-22 stage 2 and *Reject*; D2, D10 | `test_scope_continuity.py::test_delayed_null_scope_run_after_newer_scoped_run_cannot_restore_continuity`, `::test_eligible_sweep_older_than_break_is_noop` [NULL, non-null], `::test_late_older_break_still_breaks`, `::test_legacy_null_scope_only_runs_keep_todays_continuity`; frozen `test_source_ebay.py` continuity tests green |
| M-01 (new): historical snapshots inherit the newer listing's retention deadline | high | Accepted; resolved | MS2-D-37 (new; an optional `ObservationRetention` argument to `append_snapshot`, whose omitted default copies from the listing; explicit per-observation class and expiry on the guarded path; newer-absence pull-forward, and no write when already retired); MS2-D-30 *History*; D *Files* (`persist.py`, owned by D10); D10 | `test_persist_observation_retention.py::test_reverse_order_bounded_observation_snapshot_keeps_its_own_deadline`, `::test_older_bounded_observation_after_newer_delist_is_not_snapshotted`, `::test_merchant_fact_older_observation_appends_history_with_null_expiry`, `::test_append_snapshot_default_copies_listing_retention` |

**Migrations (round 3):** no number changes. The new columns land in
migrations that have not yet merged, all nullable with no backfill:
- `0021` (Slice D, D2): `Listing.last_absence_at`; on `SourceLaneState`,
  `last_eligible_sweep_at`, `continuity_broken_at`, and
  `last_complete_sweep_at`; and on `scope_sweep_continuity`,
  `continuity_broken_at` and `last_complete_sweep_at`.
- `0022` (Slice E, E1): `ApifySpendReservation.last_charge_at` and the
  `(status, last_charge_at)` index.
- `0018`–`0020` are unchanged.

**Round 4: cross-agent delegate `a4b2e45b`** (opposite provider, static
read-only review of revision 4 at `ea80849`). Recorded in revision 5; the
round's record previously lived only in the handoff documents.
- **Verdict:** READY WITH ADVISORIES. All four round-3 items (F-08, N-01, N-02,
  M-01) RESOLVED at plan level; no new findings (R4-01 onward: none). Slice B
  was found clear to proceed.
- **Advisories (accepted, no plan change needed):** complete and record the
  mandatory *Slice D entry gate* before D2, including R23 and the scope/lane
  lock order; keep production deny-all admission until E's verification
  conditions hold. External Apify assumptions were not checked (research
  disabled) and remain D3, E2, and live-admission obligations.

**Revision 5 (owner decisions, s2, 2026-09-24).** Not a review response: it
encodes owner decisions made after round 4 converged (see the revision-5
changelog at the top, and MS2-D-38..-44). Rounds 1–4 above are unchanged, and
every decision not named in the changelog keeps its round-4 status.
- **Review status:** a Codex review of revision 5 is **pending**. The
  orchestrator runs round 5 after this revision lands; its verdict and
  disposition are recorded here as *Round 5*. (Done: see *Round 5* below.)
- **Design choices beyond the owner's words**, offered for that review:
  the contract artifact location and conformance direction (MS2-D-14); the
  Actor project and gate layout (MS2-D-38); the `TruncationReason` vocabulary
  (MS2-D-11); the two-check admission with its operator allowance, account
  margin, watch-refresh reserve, and boundary guard defaults (MS2-D-40); the
  settle-delay finalization rule and the `bound`/`counted` post-run modes
  (MS2-D-41); the pinned raw-GitHub synthetic source and the non-production
  proof environment (MS2-D-42); and `apify push` from a reviewed checkout with
  a `candidate` → `prod` tag promotion (MS2-D-43).

**Round 5: cross-agent delegate `0469e098`** (Codex, opposite provider, static
read-only review of revision 5 at `3970234`).
- **Verdict:** REVISION NEEDED, five findings (R5-01..R5-05): four high, one
  medium. **All five accepted → revision 6.**
- The review's "verified correct" items stand: repository ownership, the
  in-build-context contract, the cash/consumption distinction, single-database
  lock serialization, billing-cycle discovery, reconciliation preconditions,
  the D-core ordering decisions, the OQ24 split, the 18 §21 mappings, the
  migration chain, and the separate `TruncationReason`.

| Finding | Sev. | Status | Decision / plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| R5-01 reconciliation removes spend from account-headroom admission while the snapshot predates it | high | Accepted; resolved | MS2-D-40 *Account prepaid headroom* check 1 (`HR_cycle` includes reconciled spend; `…_USAGE_INCLUSION_LAG_S` unset ⇒ no watermark); *Rejected (d)*; E settings; F5a step 4; R34 | `test_apify_ledger.py::test_repeated_reserve_reconcile_against_one_unchanged_snapshot_keeps_debit`, `::test_refreshed_snapshot_that_still_lags_keeps_reconciled_debit`, `::test_inclusion_watermark_unset_debits_all_reconciled_cycle_spend`, `::test_inclusion_lag_set_drops_only_rows_ended_before_watermark` |
| R5-02 a 10% margin cannot bound uncoordinated shared-account consumption | high | Accepted; resolved; owner gate R33 | MS2-D-40 check 2 (`…_EXTERNAL_LIABILITY_USD`, no default, unset ⇒ deny; breach detection), margin bullet, *Cash-ceiling guard*, *Account backstop*, *Rejected (c)*; MS2-D-41 *Reserve*; R30, R33; ADR 0021 amendment addendum | `test_apify_budget.py::test_unset_external_liability_denies_all_paid_admission`, `::test_concurrent_external_consumption_within_declared_bound_cannot_push_account_past_prepaid`, `::test_observed_external_consumption_above_bound_denies_and_trips_latch`, `::test_straddling_reservation_checked_against_both_cycles_external_bound`. Revision 9 note (OQ26, not a review finding): the bound now defaults to the owner's 5.00, and the first test is replaced by `::test_empty_or_invalid_external_liability_denies_all_paid_admission` and `::test_default_external_liability_admits_only_when_invariant_holds` (E2) |
| R5-03 ten elapsed seconds treated as irreversible finalization | high | Accepted; resolved | MS2-D-41 *Usage states* (evidence table, stable-read predicate, default `bound`, unfinalized deadline, upward correction), *Reconcile*; E1 (`ApifyUsageRead`, `settlement_basis`); F5a step 4 (disagreement ⇒ stay `bound`); R26, R34 | `test_apify_ledger.py::test_nonnull_usage_rising_after_ten_seconds_is_not_finalized_early`, `::test_permanently_unfinalized_run_stays_at_bound_and_is_stale_at_cycle_end`, `::test_upward_correction_after_reconciliation_raises_settled_and_trips_latch`, `::test_later_lower_read_never_returns_capacity`, `::test_usage_reads_are_append_only_evidence` |
| R5-04 second-environment handoff omits outstanding liabilities | high | Accepted; resolved | MS2-D-45 (new; ledger authority, drained handoff, unsettled handoff unsupported); MS2-D-42 *Environment*; E1 (`ApifyLedgerAuthority`); F5a steps 1 and 5; R27, R35 | `test_apify_ledger_authority.py::test_handoff_export_refused_while_proof_run_is_running`, `::test_handoff_export_refused_while_usage_is_provisional_or_unfinalized`, `::test_second_environment_denied_until_handoff_imported`, `::test_drained_handoff_carries_settled_consumption_into_second_environment` |
| R5-05 operator allowance is an untracked deduction | medium | Accepted; resolved | MS2-D-46 (new; `operator` class, pre-execution reservation, settle, reporting, handoff); MS2-D-40 *Project allocation*; MS2-D-43 *Build*, *Inspect output*, MCP spend; E6 report; F5a step 2; R36 | `test_apify_budget.py::test_operator_reservation_counts_against_operator_class_and_account_checks`, `::test_operator_allowance_exhausted_refuses_reservation`; `test_apify_ledger.py::test_operator_build_settles_from_build_cost`, `::test_unsettled_operator_reservation_counts_at_bound`; `test_apify_ledger_authority.py::test_handoff_export_refused_with_open_operator_reservation` |

**Migrations (round 5):** no number changes. The new tables
(`ApifyUsageRead`, `ApifyLedgerAuthority`) and columns (`settlement_basis`,
`settled_run_usage_usd`, `operator_kind`) land in Slice E's unmerged `0022`.

**Revision 6 review status:** a Codex review of revision 6 is pending (round 6).
(Done: see *Round 6* below.)

**Round 6: cross-agent delegate `6af5388b`** (Codex, opposite provider, static
read-only review of revision 6 at `badad4e`).
- **Verdict:** REVISION NEEDED. R5-01 and R5-02 RESOLVED; R5-03, R5-04, and
  R5-05 PARTIAL; R6-01 and R6-02 new (high). **All accepted → revision 7.**
- The review's "verified correct" items stand: the fail-closed defaults, visible
  `budget_paused` reasons, the shared lock for reserve/reconcile/latch/operator
  reservations, the three-clock separation and R23, the migration assignment of
  the revision-6 tables to `0022`, and the absence of over-engineering.

| Finding | Sev. | Status | Decision / plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| R5-03 residual: obsolete first-read finalization in E4; no selector reaches a reconciled, cleaned-up run | high | Accepted; resolved | MS2-D-23 selector 4; MS2-D-41 *Upward correction*, *Window residual*; E4 text; E1 fields; E settings; R37 | `test_apify_ledger.py::test_poll_tick_selects_reconciled_cleaned_up_run_for_correction_monitoring`, `::test_correction_monitoring_stops_at_window_end` |
| R5-04 residual: exported reconciled rows can still correct upward after handoff | high | Accepted; resolved by prohibition (handoff stays feasible within a cycle after one correction window) | MS2-D-45 *Handoff, fully drained*, *No correction after handoff*, *Feasibility*, *Unsettled handoff* | `test_apify_ledger_authority.py::test_handoff_export_refused_while_correction_monitoring_open` |
| R5-05 residual: inspection session unbounded in operations | medium | Accepted; resolved | MS2-D-46 inspection envelope and settlement; E settings, E1; R36 | `test_apify_budget.py::test_inspection_envelope_priced_from_unit_price_settings`, `::test_inspection_envelope_denied_when_a_price_is_missing`, `::test_inspection_settles_at_full_envelope_never_below` |
| R6-01 one export importable into several ledgers | high | Accepted; resolved | MS2-D-45 *Exclusive destination*, *Import*, *Serialization*; `ApifyLedgerAuthority.handed_off_to`, `handoff_record_digest`; R35 | `test_apify_ledger_authority.py::test_duplicate_export_imported_into_two_ledgers_second_rejected`, `::test_export_to_second_destination_refused_and_same_destination_retry_idempotent`, `::test_reimport_of_same_record_is_noop`, `::test_admission_racing_handoff_export_serializes` |
| R6-02 below-estimate correction can breach aggregates after capacity reuse | high | Accepted; resolved | MS2-D-47 (new); MS2-D-41 *Upward correction* | `test_apify_ledger.py::test_below_estimate_upward_correction_after_capacity_reuse_trips_latch`, `::test_upward_correction_breaching_external_liability_check_trips_latch`, `::test_correction_attributed_to_charge_interval_cycles_not_read_time` |

**Migrations (round 6):** no number changes; the new columns land in Slice E's
unmerged `0022`.

**Revision 7 review status:** a Codex review of revision 7 is pending (round 7).
(Done: see *Round 7* below.)

**Round 7 — delegate `01639c2a` (codex), REVISION NEEDED: R5-03/R5-04 partial
(closure without closing read), R7-01 new (medium); R5-01/02/05, R6-01/02
resolved; all accepted → revision 8.** Static read-only review of revision 7 at
`df595f6`.
- The review's "verified correct" items stand: fail-closed external-liability,
  pricing, enablement, and ledger-authority defaults; `budget_paused` reasons
  while the latch is tripped; the shared budget lock for reserve,
  reconciliation, operator reservations, handoff export, and correction
  application; the three-clock table and R23; the `0022` assignment of the
  revision-6/7 schema; and no over-engineering.
- Revision 8 changes only the closure rule, the handoff evidence check, and the
  operator build identity with the operator charge horizons; it adds no table,
  task, decision ID, or migration number.

| Finding | Sev. | Status | Decision / plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| R5-03 residual: expired `correction_monitor_until` closes monitoring without a closing read, so a correction during a poller outage spanning the deadline is never applied | high | Accepted; resolved | MS2-D-23 selector 4 (no `> now` condition; *Every read*, *Closing read*, *Failed closing read*); MS2-D-41 *Upward correction*, *Window residual*; MS2-D-47 step 5; E1 `correction_closing_read`; E4 text; E6 `correction_close_overdue`; R37 | `test_apify_ledger.py::test_outage_spanning_correction_deadline_closing_read_applies_correction`, `::test_failed_closing_read_keeps_obligation_open_and_overdue`, `::test_closing_read_rollback_leaves_monitoring_open`, `::test_correction_monitoring_closes_only_after_successful_closing_read` (replaces `::test_correction_monitoring_stops_at_window_end`) |
| R5-04 residual: handoff export trusts elapsed closure and claims no correction after handoff by construction | high | Accepted; resolved | MS2-D-45 *Handoff, fully drained* (closing-read evidence, no unapplied read), *Exclusive destination* (record lists closing reads), *Import*, *Corrections after handoff* (replaces *No correction after handoff, by construction*), *Feasibility*, *Unsettled handoff*; R37 | `test_apify_ledger_authority.py::test_handoff_export_refused_after_deadline_until_closing_read_commits`, `::test_handoff_export_refused_with_read_above_settled_usage`, `::test_import_refuses_record_without_closing_read_evidence` |
| R7-01 operator build reservation has no durable build id, build-record read, or build charge horizon | medium | Accepted; resolved | MS2-D-15 (get build, ten or eleven calls); D3; MS2-D-23 selector 2 (bound builds) and 4; MS2-D-34 *Operator rows*; MS2-D-46 *Build identity and settlement* (null `provider_run` for every operator row; `--settle --build-id` binds); E1 `provider_build_id`; *Slice order* E row | `test_apify_ledger.py::test_restart_rereads_reconciled_build_and_applies_upward_correction`, `::test_operator_reservation_has_no_provider_run`, `::test_rebinding_a_different_build_id_is_refused`, `::test_build_row_without_build_id_stays_at_bound_and_never_reconciles`, `::test_inspection_settle_sets_last_charge_at_and_no_correction_obligation`; D3 `test_build_record_parsed_with_nullable_usage` |

**Migrations (round 7):** no number changes. `correction_closing_read` and
`provider_build_id` land in Slice E's unmerged `0022`.

**Revision 8 review status:** Codex round 8 is done; see *Round 8* below.

**Round 8 — delegate `b73b6633` (codex), READY, no new findings; plan converged
at revision 8.** Static read-only review of revision 8. No further revision is
required; the plan's Apify budget/ledger design (revisions 5–8) is converged.

**Revision 9 targeted review — delegate `be0b1419` (codex), 2026-09-25: three
low findings, no critical/high/medium.** Scope: the revision 9 diff only
(owner decisions OQ25–OQ29), not the converged revision 8 text. It confirmed
the budget arithmetic (`P = $17.10`, check 2 caps Hardware Radar at $12.10 so
the $12 target binds first; `A = $11`; two $0.41 build bounds fit in $1.00,
three do not), that every fail-closed path survives (present-but-empty or
invalid liability values deny every class), that no passage lets the runtime
use the operator key or gives CI a credential, and that F5a alone supplies the
Apify exit while F6 and MS-1e stay separate. Findings, all accepted and fixed
in revision 9: the E2 default-liability test now states a fixture that
isolates check 2; OQ26's record marks the former "denied while unset" rule as
history; OQ25's verification note no longer names the Apify account. Noted
limitation (unchanged): an absent variable cannot be told apart from an
accidental omission, so the default is chosen deliberately.

**Slice D entry gate (revision 4 gate) — architect review 2026-09-25.**
Read-only design review of the D/E asynchronous-ordering design against the
code at `493abca` (plan line numbers in the report refer to that commit; revision
9 at `c31a6d4` changed no reviewed code under `acquisition/`, `catalog/`, or
`actors/`). External facts came from official Apify docs only, retrieved
2026-09-25, with no API calls.
- **Verdict:** READY WITH PLAN AMENDMENTS. The ordering design (MS2-D-30,
  -31, -35, -36, -37) is sound against current code, and none of its
  invariants is reopened. Counts: 0 critical, 2 high (ED-01, ED-02), 8 medium
  (ED-03..ED-10), 10 low (ED-11..ED-20).
- **Disposition:** all 20 accepted and resolved in revision 10. Two
  resolutions differ from the review's exact text, each for a stated reason:
  ED-05 ensures scope rows in a separate committed transaction rather than a
  savepoint, and ED-16 keeps the 3600 s guard. ED-19 chooses a setting-gated
  rule over the "second `GET` also 404s" check. No owner decision was
  needed, and none is raised.
- The review's "verified correct" items stand: the seam behavior at
  `493abca` (apart from the transaction presumption, ED-04), the D-prep
  client's eleven calls with Decimal money and nullable usage, `classify_run`'s
  documented `admitted` deviation, and the absence of lock cycles between the
  scope-row locks and the poller's lane-state writes under the three conditions
  now in MS2-D-35.

| Finding | Sev. | Status | Decision / plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| ED-01 circular F5a gate: live admission waited on E2 verifying poll billing and transfer direction, which only F5a can measure | high | Accepted; resolved. API calls (run polls, correction reads, overdue abort and confirm, account reads) are counted, capped, and priced at a conservative API-call bound; transfer is priced on every byte at `max(external, internal)`; admission denies only on an unset or invalid bound; F5a measures, and lowering a bound needs a plan revision. The $12 target, $5.00 external liability, $1.00 operator allowance, `bound` settlement default, and every other fail-closed path are unchanged | MS2-D-13 counters; MS2-D-15; MS2-D-26 table (*API calls*, *Data transfer*), *Reservation*; MS2-D-32 *API calls*, *Live admission stays denied*; MS2-D-40 *Account reads*; MS2-D-41 *Upward correction*; MS2-D-46 build and inspect bounds; D2, D3 follow-up; E settings, E1, E2; F5a gate and step 4; R7, R19 | `test_apify_budget.py::test_api_calls_priced_at_bound_without_billing_verification`, `::test_transfer_priced_on_every_byte_at_higher_direction_price`, `::test_account_read_bound_is_standing_cycle_debit`; `test_apify_storage_cleanup.py::test_run_poll_cap_stops_polling_and_settles_at_bound`; `test_apify_ledger.py::test_correction_read_cap_keeps_obligation_open_and_overdue`; D3 `test_oversized_response_body_raises` |
| ED-02 `observe_listing` could silently stop bumping `last_seen` (false stale delist) | high | Accepted; resolved. Binding `last_seen` rule; R23 kept as an accepted MS-2 residual, with no stamping change | MS2-D-30 *Current state*; D10; R23 | `test_persist_observation_ordering.py::test_current_eligible_observation_bumps_last_seen_and_ineligible_does_not`, `::test_listing_observed_in_previous_run_is_not_stale_delisted_within_grace`; `test_apify_import_ordering.py::test_delayed_import_bumps_last_seen_only_forward_and_only_when_current_eligible` |
| ED-03 `provider_run.scope_key` nullable although the landed contract requires the scope; D10 test needed an impossible NULL-scope remote run | medium | Accepted; resolved. `scope_key` NOT NULL; remote runs always apply the NULL-scope break; the D10 test reaches the delayed NULL-scope record through the stage-2 continuity function or an interleaved heartbeat-fired local FULL run; `run_collection` keeps the Slice A scope-less remote fakes as a test-only path | MS2-D-13; MS2-D-31 *Which scopes a run swept*; D2; D10 | `test_provider_run_scope_key_is_required`; `test_scope_continuity.py::test_delayed_null_scope_run_after_newer_scoped_run_cannot_restore_continuity` (rewritten, parametrized) |
| ED-04 plan presumed local-path transactions that do not exist | medium | Accepted; resolved. D10 introduces two local transactions (persist; delist) inside single `sync_to_async` calls; a crash mid-persist rolls back the whole batch; frozen files listed | MS2-D-22 *Code shape*; D10 *Local transactions* | `test_pipeline_transactions.py::test_local_crash_mid_persist_rolls_back_whole_batch_and_next_poll_repairs`; frozen `test_pipeline.py`, `test_source_ebay.py`, `test_collection_provider.py`, `test_poller_jobs.py` green |
| ED-05 no order among listing rows or for mid-transaction scope-row inserts; lock conflicts spent the read cap | medium | Accepted; resolved, with one change: scope and lane rows are ensured in their own committed transaction, not a savepoint, because a savepoint insert stays locked until the outer commit. Total order budget lock → `provider_run` → `SourceConfig` → FULL lane → scope rows by key → existing listings by pk → new keys → children; in-memory retry on `40P01`/`40001`/`23505`; the budget lock is never requested while holding a row lock | MS2-D-22 stage 1; MS2-D-26 *Serialization*; MS2-D-35 *Serialization*; E4 | `test_apify_import_ordering.py::test_overlapping_scopes_sharing_listings_do_not_deadlock_or_consume_read_cap`, `::test_run_outcome_and_stage1_interleave_without_deadlock`; `test_apify_ledger.py::test_latch_trip_never_requested_while_holding_provider_run_lock` |
| ED-06 delist re-check tested only `last_observed_at`, so a concurrent scope move could be delisted by another scope | medium | Accepted; resolved. The under-lock re-check repeats the full candidate predicate, which `select_for_update().order_by("pk")` gives under READ COMMITTED | MS2-D-30 *Absence*; D10 | `test_apify_import_ordering.py::test_concurrent_scope_move_is_not_delisted_by_other_scope_sweep` |
| ED-07 kill switch "denies everything" would stop draining | medium | Accepted; resolved. The kill switch denies new starts, probes, and operator reservations only; selectors 1–4 keep running | MS2-D-17 *Admission*; E4 | `test_apify_poll_job.py::test_kill_switch_off_still_drains_imports_cleanup_and_closing_reads` |
| ED-08 `stable_reads` could finalize before post-run reads and deletes, and later postings would look like corrections | medium | Accepted; resolved. Eligible settlement reads start at `max(finishedAt, final_charge_op_at) + settle delay + boundary guard`; the finalize deadline uses the same anchor; F5a records whether run usage changes after post-run reads and deletes before `stable_reads` may be enabled | MS2-D-41 *Eligible read*, *Unfinalized runs*; F5a step 4 | `test_apify_ledger.py::test_settlement_ignores_reads_before_final_charge_op_plus_guard` |
| ED-09 storage-expiry figures not settled by the docs; account-level retention exists | medium | Accepted; resolved. `dataRetentionDays` is parsed and checked against `…_STORAGE_MAX_LIFETIME` (deny `unbounded_component`); the post-run row is reworded; the "7 days on Free" figure is withdrawn; the bounded-source denial stands | MS2-D-25 *Cleanup deadline*; MS2-D-26 table; MS2-D-32; MS2-D-33 *Reopen if*; MS2-D-40 *Retention check*; D3 follow-up; E1; R19 | `test_apify_budget.py::test_storage_lifetime_below_data_retention_days_denies`, `::test_missing_data_retention_days_denies`; D3 `test_account_limits_parse_data_retention_days` |
| ED-10 proxy latch keyed on known proxy names failed open; request-queue usage unbounded | medium | Accepted; resolved. Allowlist of bounded components; anything else, or an unparseable value, trips the latch; the client surfaces unparseable entries | MS2-D-26 table and *Overrun latch*; D3 follow-up; E4 | `test_apify_ledger.py::test_usage_component_outside_allowlist_trips_latch`; D3 `test_unparseable_usage_component_is_surfaced_not_dropped` |
| ED-11 drifted code citations | low | Accepted; resolved. Re-pointed to `c31a6d4` (the code is identical to `493abca`) | *Interfaces reused verbatim*; MS2-D-11, -14, -25, -28, -30, -31, -35, -36, -37; *Slice D entry gate* | none (citation edit) |
| ED-12 three-clock table and rules overstated `last_seen` as the only processing stamp | low | Accepted; resolved | MS2-D-39 table and *Rules* | none (text) |
| ED-13 heartbeat-fired FULL runs can overlap the FULL-lane run | low | Accepted; resolved (fail-closed behavior change recorded) | MS2-D-30 *Local path*; MS2-D-36; D10 | `test_scope_continuity.py::test_overlapping_local_full_runs_serialize_on_lane_row` |
| ED-14 docs contradict "`maxTotalChargeUsd` applies only to pay-per-event" | low | Accepted; resolved (contradiction recorded; the design does not depend on it; the client test stays) | MS2-D-15 facts; *Things not to do* | `test_client_never_sends_max_items_or_max_total_charge` (unchanged) |
| ED-15 abort on a finished run: response code unverifiable; step order undefined on abort error | low | Accepted; resolved. The abort is advisory, confirmed by `GET` run; a terminal status from either source is success; a non-2xx abort or a non-terminal read retries; deletion only after terminal | MS2-D-15; MS2-D-33 *Overdue selector*; MS2-D-32 *Overdue path* | `test_apify_storage_cleanup.py::test_overdue_abort_error_or_nonterminal_read_retries_and_deletes_only_after_terminal` |
| ED-16 cycle guard absorbs skew, not daily-bucket posting lag | low | Accepted; resolved as a recorded residual measured by F5a. The suggested 86400 s default is rejected, because with the ED-08 eligibility rule it would make `stable_reads` impossible | MS2-D-40 *Cycle boundary*; F5a step 4 | none (F5a evidence) |
| ED-17 deprecated `/v2/acts/` path | low | Accepted; resolved in the D3 follow-up | D3 follow-up | `test_start_uses_actors_path_and_sends_restart_on_error_false` |
| ED-18 stage-2 text omitted the NULL-scope break | low | Accepted; resolved | MS2-D-22 stage 2 | covered by `test_scope_continuity.py::test_run_not_sweeping_null_scope_breaks_null_scope_continuity` |
| ED-19 any 404 on delete counted as verified deletion | low | Accepted; resolved with a chosen rule. A 404 counts only for the run's own recorded storage id and only once `…_DELETE_404_IS_ABSENT` is set, after the R25 probe records a 403 for inaccessible storage. The review's "second `GET` also 404s" check is rejected, because a masking token would 404 that read too | MS2-D-25 *404 rule*; MS2-D-32; E settings; F5a gate; R25; traceability R-MS2-15 | `test_apify_storage_cleanup.py::test_delete_404_counts_as_deleted_only_when_rule_enabled`; D3 `test_delete_404_is_success` (client level, unchanged) |
| ED-20 `restartOnError` unset; a restart could exceed the compute bound | low | Accepted; resolved. Sent explicitly false; an observed restart trips the latch as a start-option mismatch | MS2-D-26 compute row; D3 follow-up; E4 | `test_start_uses_actors_path_and_sends_restart_on_error_false` |

**Migrations (entry gate):** no number changes. `0021` (D2):
`provider_run.scope_key` NOT NULL, plus `run_poll_count` and
`correction_read_count`. `0022` (E1): `ApifyBudgetCycle.account_read_count`
and `account_data_retention_days`, plus the two poll counters on operator build
reservations.

**Also folded in revision 10** (a review note, not an ED finding): the next
cycle's external-liability check for a straddling reservation is evaluated with
the current snapshot and re-checked at the new cycle's first snapshot, where a
failure trips the latch (MS2-D-40 check 2).

**Revision 10 targeted review — delegate (codex) 2026-09-25.** A read-only
review of revision 10 at `3c46094` (line numbers in the report refer to that
commit) against the plan, the D-prep code, and the Actor. Codex made no
external research or API calls. Revision 11's author retrieved the official
Apify docs and pricing page on 2026-09-25 for R10-01 and R10-07.
- **Verdict:** changes required. 3 high (R10-01..R10-03), 6 medium
  (R10-04..R10-09), and 3 low (R10-10..R10-12), all new and all owned by the
  plan. The review confirmed that the ED-02 `last_seen` rule, the ED-03
  scope key and rewritten test, the ED-04 local transactions, the ED-06
  locked candidate re-check, the ED-07 drain behavior, and early scope-row
  creation are sound. It found no demonstrated lock cycle, a fail-closed
  default-false 404 rule, and feasible default settlement timing once the
  anchor is fixed.
- **Disposition:** all 12 accepted and resolved in revision 11. Owner
  decisions are not reopened: the $12 target, the $5.00 external liability,
  the $1.00 operator allowance, F5a sufficiency, and `bound` settlement.
  One new owner item is raised, R38, because the documents cannot bound
  per-call multiplicity or metered bytes. No revision-10 bound is lowered.

| Finding | Sev. | Status | Decision / plan location (changed text) | Named tests |
| --- | --- | --- | --- | --- |
| R10-01 `api_call_bound` asserted, not derived; client-read bytes need not bound provider-billed transfer; the latch detects the first breach but does not prevent it | high | Accepted; resolved. A per-endpoint derivation from the documented units (closed list of usage parts; pricing units with no API-request, delete, or account unit; per-item dataset pagination). Wire-byte ceilings: body caps, httpcore's header limit, a request-body cap, and a pinned `SO_RCVBUF`, which bounds bytes delivered after an abandoned response. A table of every call. The undocumented remainder (multiplicity, metered bytes, Actor SDK overhead) is R38, an owner-accepted residual gating all paid admission, which is not circular with F5a. No bound is lowered: the no-storage call keeps its one-operation margin | MS2-D-15; MS2-D-26 table (*API calls*, storage row), *Reservation*; MS2-D-32 *Documented billing units*, *Per-call bound*, *Live admission stays denied*, *Rejected (d)*; D3 follow-up (revision 11); E settings, E2; F5a gate and step 4; R38; owner items | `test_apify_budget.py::test_per_call_bounds_derived_from_settings_by_endpoint`, `::test_admission_denied_until_call_billing_residual_accepted`; D3 `test_client_sets_receive_buffer_socket_option`, `test_request_body_over_cap_refused_before_send`, `test_httpcore_constants_match_wire_ceiling` |
| R10-02 the Actor increments `bytes_read` before checking, so `maxBytes` is not a transfer ceiling | high | Accepted; resolved. The transfer row adds `maxRequests × request_wire_overhead + HTTP_READ_CHUNK_BYTES`. The D1 follow-up counts wire bytes (`num_bytes_downloaded`), stops at the first crossing chunk (at most one 64 KiB network read), and pins the receive buffer, so abandoned-body bytes in flight are bounded, including non-200 bodies. A drift test ties the Actor constants to the estimator | MS2-D-26 *Data transfer*; D1 follow-up (revision 11); E2 | Actor `test_limits.py::test_byte_cap_counts_wire_bytes_and_stops_on_first_crossing_chunk`, `::test_http_client_pins_receive_buffer`; `test_apify_contract.py::test_actor_transfer_constants_match_estimator`; `test_apify_budget.py::test_transfer_bound_covers_actor_overshoot_of_one_read_and_in_flight_window` |
| R10-03 monitoring reads after `last_charge_at` escape the cycle debit, and `counted` mode releases their allowance early (runs and builds) | high | Accepted; resolved. `monitoring_bound_usd` is held outside the settled amount and debited over a second interval, open-ended while a further call is possible and ending at the last read's send stamp. Carried into every cycle; never released at reconciliation. `last_charge_at` also covers pre-reconciliation polls (`reconciled_at`). The correction deadline stays fixed | MS2-D-23 selector 4; MS2-D-32 *Correction reads*, *Reservation split*; MS2-D-34 *Horizon*, *Monitoring charges*, *Operator rows*, *Cycle predicate*; MS2-D-40 `HR_cycle`; MS2-D-41; MS2-D-46 build bound; E1 | `test_apify_ledger.py::test_closing_read_after_cycle_boundary_debits_the_new_cycle`, `::test_monitoring_allowance_carried_through_prolonged_outage`, `::test_counted_mode_never_releases_unused_monitoring_allowance_at_reconciliation`, `::test_correction_deadline_not_moved_by_monitoring_reads`, `::test_build_monitoring_read_after_cycle_boundary_is_debited` |
| R10-04 `final_charge_op_at` ambiguous: metering reads would move the eligibility anchor, and a latest-operation stamp does not prove the barriers | medium | Accepted; resolved. The existing `0021` column becomes write-once, set at the commit of the later of the import-terminal and verified-deletion barriers. Metering calls never move it; null means no eligible read and no deadline | MS2-D-13; MS2-D-32 *Settlement*; MS2-D-34; MS2-D-41 *Eligible read* | `test_apify_ledger.py::test_settlement_polls_do_not_move_completion_anchor`, `::test_no_read_eligible_before_import_and_deletion_barriers` |
| R10-05 account reads: no row to debit on an empty ledger; an exhausted cycle blocks discovery; failed discovery calls; no handoff treatment | medium | Accepted; resolved. A new `ApifyCycleDiscovery` table: a durable, capped, spaced discovery allowance counted before each call and debited over its interval; an owner reset at the cap. The handoff record carries the standing account-read, discovery, and monitoring debits as separate lines | MS2-D-32 *Cycle discovery*; MS2-D-34; MS2-D-40; MS2-D-45 *Non-reservation debits*; E1; E settings | `test_apify_ledger.py::test_empty_ledger_bootstrap_read_is_counted_before_it_is_sent`, `::test_exhausted_cycle_read_cap_then_rollover_discovers_next_cycle`, `::test_failed_discovery_reads_count_and_stop_at_cap`, `::test_same_cycle_handoff_carries_account_read_and_monitoring_debits` |
| R10-06 not every external call is accounted: the start-option-mismatch abort, start or control responses, the 404 capability probe's storage | medium | Accepted; resolved. Every call is in the *Per-call bound* table. The start call is priced and never retried. The mismatch abort is overdue attempt 1 (`storage_cleanup_attempts`), and an attempt is abort + `GET` + two deletes. A new operator `probe` envelope covers create, delete attempts, cleanup, and one storage's lifetime; it settles only after verified deletion | MS2-D-25 *404 rule*; MS2-D-26 compute row; MS2-D-32 *Deletes*, *Overdue path*, table; MS2-D-46 *probe*; D5; E1; E2 | `test_apify_poll_job.py::test_start_option_mismatch_abort_is_counted_cleanup_attempt`, `::test_start_request_is_never_retried`; `test_apify_budget.py::test_estimate_prices_four_calls_per_cleanup_attempt_and_the_start_call`, `::test_probe_envelope_priced_and_denied_when_allowance_short` |
| R10-07 contradictory storage model: "indefinitely" (ten most recent) versus "expires at platform expiry" or retention | medium | Accepted; resolved with one model. A failed deletion is a recurring liability. Storage is priced over `storage_hours ≥ 744 h + 2 × guard` (a full cycle), and an undeleted row counts at full estimate in every cycle. A cycle over 744 h denies. The finite-expiry sentences are withdrawn. The 2026-09-25 docs still conflict (`platform/storage` versus `actors/running/runs-and-builds`) | MS2-D-25 *Cleanup deadline*; MS2-D-26 storage row; MS2-D-32 *Deletes*, *Reservation split*, *Rejected (b)*, *(c)*; MS2-D-40 *Retention check*; R19 | `test_apify_budget.py::test_storage_hours_cover_a_full_cycle_when_lifetime_is_shorter`, `::test_observed_cycle_longer_than_744_hours_denies`; `test_apify_ledger.py::test_failed_deletion_with_no_later_runs_counts_full_cycle_storage_in_every_cycle` |
| R10-08 a valid 500-row dataset page can exceed the 262,144-byte cap and trip the global latch | medium | Accepted; resolved. A serialized-row bound derived from the schema (12 bytes per code point, keys, and whitespace; 32,735 bytes for v1) and a separate `…_MAX_DATASET_PAGE_BYTES` give `page_limit` (32) and `pages_per_read` (17). The importer always sends `page_limit` | MS2-D-32 *Response caps*, *Dataset page size*; D3 follow-up (revision 11); E settings | `test_apify_contract.py::test_serialized_row_bound_covers_worst_case_escaped_row`, `::test_page_limit_derived_from_row_bound_and_page_cap`; `test_apify_import.py::test_max_size_valid_batch_imports_without_tripping_response_cap`; `test_apify_ledger.py::test_over_cap_control_response_trips_latch`; `test_apify_budget.py::test_page_limit_below_one_or_kv_cap_above_response_cap_denies` |
| R10-09 abort/`GET` terminal-evidence precedence undefined | medium | Accepted; resolved. Terminal evidence from either valid response decides first: it is persisted and deletion follows in the same attempt; a terminal abort skips the `GET`. The attempt retries only when neither proves termination | MS2-D-33 step 1; D11 | `test_apify_storage_cleanup.py::test_non2xx_abort_with_terminal_read_proceeds_to_delete_in_same_attempt`, `::test_terminal_abort_response_proceeds_to_delete_without_confirming_read`; the revision-10 test narrowed to "neither terminal" |
| R10-10 retry exhaustion has no next action; rollback does not restore Python state; the retained test name implies in-process re-reads | low | Accepted; resolved. Each attempt rebuilds state from the immutable batch. A fourth abort exhausts the invocation with no partial effect, recorded and backed off; the next invocation makes the counted re-read. The test is renamed to process-loss recovery | MS2-D-22 stage 1; MS2-D-35 *In-memory retry*; D10; E4 test text | `test_apify_import.py::test_stage1_process_loss_rereads_count_against_read_cap` (renamed from `test_stage1_rollback_retry_counts_each_dataset_read`), `::test_forced_retryable_sqlstate_retried_in_memory_without_reread`, `::test_retry_exhaustion_leaves_no_partial_effects_and_next_tick_rereads_counted`, `::test_local_persist_retry_exhaustion_rolls_back_like_a_crash` |
| R10-11 the total order omits Reject's `SourceConfig` lock and the HEARTBEAT lane | low | Accepted; resolved. Outcome transactions are named (stage 5, Reject, and the poller jobs). Exactly one lane row follows `SourceConfig`, HEARTBEAT only in `poll_heartbeat`. Reject order: `provider_run` → `SourceConfig` → FULL lane → scope row, with prerequisites ensured beforehand | MS2-D-22 *Reject*; MS2-D-35 order items 2–4, *Conditions of the order* | covered by `test_apify_import_ordering.py::test_run_outcome_and_stage1_interleave_without_deadlock` and the D12 rejected-probe tests (no new test: no cycle was demonstrated) |
| R10-12 R36 figures omit the revision-10 call allowance | low | Accepted; resolved. R36 and MS2-D-46 state the formula, then approximate figures: about $0.423 per build, $0.846 for two, $0.154 left; a third does not fit. The account-read figure is recomputed the same way (about $0.54) | MS2-D-32 *Account reads*; MS2-D-46 build; R36 | `test_apify_budget.py::test_default_operator_allowance_fits_two_build_bounds_not_three` (unchanged: still two, not three) |

**Migrations (revision 11):** no number changes. `0021` (D2): **no column
change**. `final_charge_op_at` is write-once at the work-completion barrier,
and `storage_cleanup_attempts` also counts the start-option-mismatch abort;
both are semantic notes for the D2/D10 implementation. `0022` (E1):
`ApifySpendReservation.monitoring_bound_usd` and `monitoring_charge_last_at`,
`operator_kind` value `probe`, and the `ApifyCycleDiscovery` table.

**Revision 11 resolution check — delegate (codex) 2026-09-25, final targeted
round.** 10 of 12 findings resolved. R10-02 and R10-03 were partly resolved,
with no new critical or high finding. Both were completed in-house in revision
11 without another review round:
- R10-02: the Actor's byte counter is run-wide and cumulative, counted per raw
  receive before decoding, with multi-response and gzip tests (*D1 follow-up*).
- R10-03: a durable `monitoring_call_pending_since` marker keeps the
  monitoring interval open until the final counted read completes. A stale
  marker resolves at poller start, and the boundary test is added
  (MS2-D-34 *Monitoring charges*).

The column goes into Slice E's unmerged `0022`. The review loop closes here:
later medium or low issues are handled in implementation review.

**Revision 12 (owner decision (s5, 2026-09-25), R25).** No review round
produced this revision. The owner decided on 2026-09-25 to drop runtime
account reads after the scoped-token evidence. MS2-D-48 and E9 carry it.
A Codex targeted review of revision 12 is **pending**, and it should run
before E9 starts. The review should focus on the anchor derivation and
conflict rules, the admission order, the 402 classification, the
retired-machinery list, and whether any invariant listed as unchanged was
weakened.

**Rev-12 Codex follow-up: Codex bounded review of revision 12 (delegate
`ea9029f8`, result sha256 `2f2f2175…`, 2026-09-25).**
- **Verdict:** REVISION_REQUIRED. Five findings, all ACCEPTED with no part
  rejected, and all resolved in place within revision 12.
- The review confirmed the configured-cycle arithmetic, the lock order of
  `ensure_cycle` in `reserve`, `claim_origin`, `export_handoff`, and
  `import_handoff`, the backward-overlap conflict rule, and that no other
  producer of account requests exists.

| Finding | Severity | Disposition | Where | Proof |
| --- | --- | --- | --- | --- |
| R12-01 a 402 whose latch trip is lost between the two commits does not pause later admission | high | Accepted; resolved. The commit order is kept (ED-05). The 402 persisted in `stage_detail.start_error` is repaired under the budget lock inside every `reserve`, before the latch is read, and in each tick's stranded sweep. Any existing trip, open or owner-cleared, blocks a re-trip. The estimator bump no longer clears this reason | MS2-D-48 *Hard-limit refusal*; E9.3 | `test_apify_crash_windows.py::test_402_trip_lost_between_commits_is_repaired_before_next_admission`, `::test_402_trip_lost_between_commits_is_repaired_by_the_next_tick`, `::test_owner_cleared_402_trip_is_never_retripped`, `::test_estimator_bump_does_not_clear_account_limit_refused` |
| R12-02 the E9 commits cannot all be gate-green; the double and fixture inventory is incomplete | medium | Accepted; resolved. Every cited location was verified against `84c5cd0`. E9.2 now retires every ledger, budget, and command dependent of the removed fields and signature in the same commit. It lists all four admission doubles, `ledger_support` (including `STANDING` and `account_client`), the unit fixtures, and every test using a removed name, and deletes `test_observed_external_consumption_above_bound_denies_and_trips_latch`. It adds a binding `rg` completion check. E9.4 removes the `client.py:114–122` URLs. Additional items found beyond the review: `STANDING` uses in five test modules, `test_apify_recovery_probe.py:577–652`, the unit parametrizations at lines 633 and 840, the `apify_budget_reset --discovery` import dependency, and the `invariant_breaches` test at 1039 | E9.2, E9.4 | the E9.2 completion search; the gate at each commit |
| R12-03 re-verification deadlocks: disabling, an open latch, or invalid settings deny the envelope the verification needs | medium | Accepted; resolved. For a planned change, the envelope is reserved before disabling. When none can be admitted, a bounded recovery verification applies: at most 3 operator-key GETs per triggering event, outside the application, unledgered, recorded in STATUS and R36, with a second verification needing the owner. Paid work is never enabled and the latch never cleared just to inspect. An operator-class exemption from the blanket stops is rejected | MS2-D-48 *Operator verification* | procedural (R36); no runtime change |
| R12-04 the promise to validate corrections is broader than E9.2 implements | medium | Accepted; resolved with the broad contract. `budget.account_setting_problem(cfg, now)` covers anchor, limit, base price, retention, verified-on, and margin. Admission and `invariant_breaches(resv, config, now)` share it, with an explicit `now`. The cash-ceiling, lifetime, and 744 h checks are named as admission-only | MS2-D-47 inline; E9.2 | `test_apify_budget.py::test_account_setting_problem_contract`; `test_apify_ledger.py::test_correction_with_invalid_account_setting_is_an_invariant_breach` |
| R12-05 R33/R34 and two command docstrings still direct retired behavior | low | Accepted; resolved. R33/R34 carry superseding notes and R34's action is narrowed. E9.5 lists `apify_ledger_claim.py:7–8` and `apify_spend_report.py:3–12` and adds a docstring search | R33, R34; E9.5 | the E9.5 search |

**Owner answers to the R39 points (2026-09-25, bounded choice; recorded in
[OQ30](../../resolved-questions.md#oq30--runtime-apify-account-reads-r25)).**
1. "No expiry; warn only (Recommended)": the report warning is E9.6.
2. "Still accepted (Recommended)": R38 stands.
3. "Clarify the spec (Recommended)": the dated amendment of master spec
   C-011 was made in the same change.

R39 is closed. A further review round is optional: the follow-up adds no
decision, and each change is covered by a named test or procedure.

**Codex round 2 (bounded delegate, 2026-09-25): REVISION_REQUIRED.** R12-01,
-03, -04, and -05 closed. Dispositions of what remained (applied by the
orchestrator in place, no new decision):

| Finding | Severity | Disposition | Where | Proof |
| --- | --- | --- | --- | --- |
| R12-02 (partial) E9.1 adds required `BudgetSettings` fields but the constructors change only in E9.2 | medium | Accepted; resolved. E9.1 adds the five fields to both direct constructors (`CFG`, `BUDGET`); E9.2 only drops the retired fields | E9.1, E9.2 | E9.1 gate |
| R12-201 a delayed direct 402 callback re-trips a trip the owner already cleared (`trip_latch` is idempotent over open trips only) | medium | Accepted; resolved. The callback uses budget-locked `ledger.trip_start_refusal`, sharing the open-or-cleared predicate with the repair sweep; other reasons unchanged | E9.3 | `test_delayed_402_callback_after_repair_and_owner_clear_does_not_retrip` |
| R12-202 operator reconciliation "recommended" contradicts C-011's "each billing cycle" | low | Accepted; resolved. Reconciliation is required each billing cycle and during F5a | MS2-D-48 *Operator reconciliation* | procedure |

## Next slice after A

First the Slice A F-04 correction lands (see *Revision 3 correction* under A6).
Then **Slice B — first-class category specs and rules** (migrations `0018`,
`0019`), starting with B1 (satellites + CHECK pair + AC-1 coexistence test). D-prep
(D1 + D3) may proceed in parallel after A (see *Slice order*).
