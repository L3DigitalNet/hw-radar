---
schema_version: '1.1'
id: 'adr-0021-hw-radar-hybrid-acquisition-apify'
title: 'ADR 0021: Hybrid acquisition — local collectors + self-owned Apify Actors'
description: 'Use a hybrid acquisition model: retain inexpensive direct/API/structured-data collectors locally, add self-owned private Apify Actors selectively for sources where managed execution materially helps, keep Hardware Radar as the authority for normalization/matching/persistence, and enforce a hard Apify spend ceiling of $20/month.'
doc_type: 'adr'
status: 'active'
created: '2026-09-24'
updated: '2026-09-24'
reviewed: '2026-09-24'
owner: 'Chris Purcell'
consumer: 'mix'
tags:
  - 'adr'
  - 'acquisition'
  - 'apify'
  - 'scraping'
  - 'cost-control'
  - 'orchestration'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/adr/adr-0012-orchestration-apscheduler.md'
  - 'docs/adr/adr-0014-scraping-runtime-escalation-stack.md'
  - 'docs/adr/adr-0017-resilient-acquisition.md'
  - 'docs/specs/hw-radar-master-spec.md'
supersedes: []
superseded_by: null
source: []
confidence: 'high'
visibility: 'public'
license: null
project:
  decision_makers:
    - 'chris'
  consulted: []
  informed: []
---

# ADR 0021: Hybrid acquisition — local collectors + self-owned Apify Actors

MADR status: **accepted**.

## Context and Problem Statement

Hardware Radar originally committed to a predominantly local acquisition stack: direct APIs or structured HTTP where available, then Scrapy/curl_cffi/Playwright escalation inside the hw-radar deployment. That architecture is implemented far enough to prove the common ingestion boundary, but the owner subsequently adopted Apify and already operates a separate `L3DigitalNet/apify-actors` monorepo. _(History as of 2026-09-24. That repository is an unrelated venture; Hardware Radar's Actors live in this repository — see the [2026-09-24 amendment](#amendment--2026-09-24-actor-ownership-billing-cycle-budget-and-actor-proof-owner-clarification).)_

The new question is not whether Apify can replace scraping in the abstract. It is whether Hardware Radar should move collection work to Apify, keep it local, or use both while preserving the product's existing normalization, identity, retention, scheduling, and evidence contracts.

The owner also set a controlling cost constraint: **Hardware Radar must not spend more than $20/month on Apify**. This rules out architectures that assume open-ended browser/proxy usage or default to third-party per-result Actors.

## Considered Options

- **Option 1 — Keep acquisition predominantly local.** Generalize the existing collectors and use official APIs/structured endpoints wherever possible.
- **Option 2 — Hybrid acquisition.** Keep inexpensive direct/local collectors, add Apify selectively, and prefer self-owned Actors for project-specific collectors. **(chosen)**
- **Option 3 — Apify-first acquisition.** Run most collection through Apify, including sources that are already cheap and reliable locally.

## Decision Outcome

Chosen option: **Option 2 — hybrid acquisition, with self-owned Actors as the default Apify path.**

### Provider selection is per source

A marketplace/source and its execution provider are separate concepts. A listing from eBay, Newegg, ServerPartDeals, or another merchant remains the same source listing regardless of whether the bytes arrived from:

1. an official API;
2. a local hw-radar HTTP/Scrapy collector;
3. a self-owned private Apify Actor; or
4. an explicitly approved third-party Actor.

Do **not** fork listing identity or price history when the collection provider changes.

Existing cheap paths stay local unless measurement shows a concrete benefit from moving them. In particular, official APIs and simple structured JSON endpoints are not migrated merely for uniformity.

### Self-owned Actors

Hardware Radar-specific Actors live in the existing private `L3DigitalNet/apify-actors` monorepo and are treated as **internal infrastructure first**, not as Store products. Publication/monetization is a separate decision. _(Location superseded 2026-09-24 by the [amendment](#amendment--2026-09-24-actor-ownership-billing-cycle-budget-and-actor-proof-owner-clarification) below: Hardware Radar's Actors are built, versioned, tested, deployed, and managed in the `hw-radar` repository. The internal-infrastructure framing stands.)_

Actors own source-specific acquisition concerns:

- fetching and pagination;
- source-specific extraction;
- bounded retries/timeouts/concurrency;
- run diagnostics;
- query/category scope;
- evidence about pagination/completeness.

Actors do **not** own canonical product identity, cross-source matching, category semantics, price history, watch eligibility, alert state, or writes to the hw-radar system-of-record.

The Actor output is a versioned observation contract consumed by an hw-radar provider adapter.

### Scheduling and ingestion ownership

The hw-radar poller remains the scheduling/admission owner ([ADR 0012](adr-0012-orchestration-apscheduler.md)). For an Apify-backed source it starts a bounded Actor run, records the provider run/build identifiers, and imports completed output asynchronously.

Do not configure an independent Apify schedule for the same production job. There is one scheduling owner per job.

Imports must be idempotent. Duplicate completion notifications, retries, or repeated dataset reads must not create duplicate listings/observations.

### Completeness is evidence, not an assumption

Every provider result must preserve enough metadata to distinguish:

- a complete enumeration that found no matching offers;
- a bounded/truncated run that stopped at a page/request/time/cost limit;
- a failed or partially failed collection.

A bounded or failed Actor run is **never** evidence that unseen listings disappeared. Existing delist/absence logic may consume provider absence only when the provider contract proves the relevant sweep was complete.

### Cost contract

Hardware Radar's Apify consumption has a **hard maximum of $20/month**.

Implementation shall:

- track attributable run cost/usage by source and provider;
- use a lower operating target (initially **$12/month**) so tests, retries, and estimation error have headroom;
- reserve budget before admitting paid work and reconcile actual usage afterward;
- stop admitting nonessential Apify work before the hard limit can be crossed;
- degrade broad discovery before active-watch refreshes;
- surface `budget_paused` / stale state rather than implying freshness;
- make browser execution and residential-proxy use explicit per-source decisions;
- never automatically escalate to paid residential proxies or paid third-party Actors after a failure.

If the Apify account is shared with other workloads, Hardware Radar may use only its explicitly allocated remaining budget; it must not assume the account's full allowance is available.

### Third-party Actors

Third-party Store Actors are an exception path, not the default. A third-party Actor may be adopted only when a representative benchmark shows that its total cost, reliability, data contract, and maintenance benefit beat both a local collector and a self-owned Actor for that source.

## Consequences

- **Good** — preserves working official-API/structured-data paths and the current ingestion/matching investment.
- **Good** — adds managed execution where it is useful without making Apify the system of record.
- **Good** — owning Actors controls output schema, resource limits, tests, and source-specific behavior.
- **Good** — provider changes are reversible because the canonical identity/history remains in hw-radar.
- **Bad (accepted)** — two execution environments must be operated and observed.
- **Bad (accepted)** — self-owned Actors outsource execution, not selector/parser maintenance; source drift is still our responsibility.
- **Constraint** — browser/proxy-heavy sources may not fit the $20/month ceiling at the desired cadence and must be reduced, skipped, or handled another way rather than silently exceeding budget.

## Confirmation

This decision is confirmed when:

1. at least one source can be collected through a self-owned private Actor and imported through the common hw-radar pipeline;
2. switching that source between direct/local and Actor-backed collection preserves the same source-listing identity;
3. a deliberately truncated Actor run cannot trigger false delists;
4. per-run/provider cost is recorded and admission stops safely at the configured project budget;
5. no production job is independently scheduled in both APScheduler and Apify.

## More Information

- **Amends, does not supersede:** [ADR 0012](adr-0012-orchestration-apscheduler.md) — APScheduler remains the scheduling/admission owner; some jobs may execute remotely.
- **Amends, does not supersede:** [ADR 0014](adr-0014-scraping-runtime-escalation-stack.md) — HTTP-first / structured-data-first / browser-last remains the per-source technique rule; execution may now be local or in a self-owned Actor.
- [ADR 0017](adr-0017-resilient-acquisition.md) continues to govern per-source isolation, breaker state, and graceful degradation.

## Amendment — 2026-09-24: Actor ownership, billing-cycle budget, and Actor proof (owner clarification)

Owner decisions of 2026-09-24 (session 2). This amendment clarifies and, where
marked, overrides the decision above; the original text stays as the record of
what was first decided. The implementing contract is the MS-2 plan, revision 5
([`2026-09-24-ms2-multi-category-watch-core.md`](../superpowers/plans/2026-09-24-ms2-multi-category-watch-core.md),
MS2-D-38..-44).

**1. Actor ownership (overrides *Self-owned Actors*, first sentence).** "All
Apify Actors used specifically by Hardware Radar must be built, versioned,
tested, deployed, and managed from the `hw-radar` project/repository." Actor
source, input and output schemas, tests, build and deploy definitions,
versioning, cost and usage integration, and operator procedures live in this
repository under `actors/hw-radar-<purpose>/`. A change to the Actor↔hw-radar
contract lands atomically in one hw-radar pull request. Hardware Radar does not
depend on the `L3DigitalNet/apify-actors` repository's code, lifecycle state,
product records, admission process, implementation work, or credentials. The
Actor boundaries above stand: no DB credentials, no hw-radar model or Django
imports, observation output only.

**2. Cost contract: cash ceiling plus attributable consumption (clarifies *Cost
contract*; resolves [OQ23](../resolved-questions.md#oq23--apify-paid-plan-base-fee-vs-the-20month-hardware-radar-ceiling)).**
- The hard owner ceiling is the account's total Apify **cash outlay**, about
  $20 per billing cycle. A plan's subscription fee counts, because it is actual
  money; the prepaid platform usage it buys is not charged a second time.
- Hardware Radar's **attributable platform consumption** has an operating target
  of at most $12 per cycle, and it may use only the lesser of that target and
  the prepaid allowance actually remaining after the account's other workloads.
  It must not assume the whole prepaid allowance is available.
- No pay-as-you-go overage is relied on for normal operation.
- The rule is stated in these terms, not as a plan name. It never means "$12 plus
  $19", or "$20 of run charges on top of the subscription".

**3. Account backstop.** The account-level usage limit is a secondary defense
only. The owner's recommendation is to keep it at or below the prepaid credit
and never raise it for Hardware Radar. Agents make no billing or account setting
change without explicit owner authority. The project-level controls are
mandatory regardless of the account limit, because the limit is shared and the
platform documents enforcement deviation of up to about 10%.

**4. Budget period.** The authoritative period is the account's actual Apify
billing cycle, discovered from the API (for example `monthlyUsageCycle` in the
account limits), never a hard-coded calendar month. Hardware Radar stores the
cycle start and end, its allocation for the cycle, consumed finalized usage,
outstanding reservations, remaining project budget, and the remaining account
prepaid allowance where observable. A rolling window may remain only as a
secondary trend or safety metric. Charges count toward every cycle their
billing time can touch, and admission is conservative for a run whose worst-case
cost could straddle a cycle boundary.

**5. Three clocks.** Observation (event) time, processing (import) time, and
billing (finalization) time are distinct and never derived from one another.
Listing lifecycle, continuity, delist/relist, and content retention follow
observation time; import progress and the remote-storage deadline follow
processing time; usage finalization, reconciliation, and cycle attribution
follow billing time.

**6. Reservation and reconciliation.** Paid work follows `eligibility →
estimate maximum cost → reserve → start run → poll completion → import bounded
output → obtain finalized usage → account post-run retrieval, transfer, and
storage cost → reconcile → release the unused reservation`. Reservations are
concurrency-safe and never oversubscribe; a lower actual returns capacity; a
higher actual enters an overrun state that pauses further paid work and never
triggers another paid call to repair itself. Budget errors are visible
operational state. The first cost figure at completion is provisional until
finalized, and post-run consumption is accounted conservatively until measured:
the budget is not reconciled merely because the Actor process stopped.
`maxTotalChargeUsd` and the `maxItems` run option apply only to pay-per-event
and pay-per-result Actors and are not relied on for Hardware Radar's own
Actors; cost is bounded structurally (timeout, memory, request, page, item, and
byte limits, concurrency, no proxy, and admission).

**7. Proxies and escalation (sharpens *Cost contract*, last bullets).** No
residential proxy, no automatic proxy rotation, no CAPTCHA solving, no paid
unblocker, and no paid third-party Actor, ever, including as a fallback. A
source that needs any of them fails admission unless the owner revisits it.

**8. Actor proof (clarifies *Confirmation* 1–3).** The first Actor proof uses a
controlled synthetic source through a real private Apify Actor, so it proves
compute, lifecycle, dataset retrieval, delayed completion, cost accounting, and
API behavior without a merchant legal decision. A production Actor-backed
merchant source is a separate source-admission decision per candidate
(`docs/research/source-admission/`); it stays open as
[OQ24](../open-questions.md#oq24--production-actor-backed-merchant-source-admission).
Existing local connectors are not grandfathered into an Actor path.

**9. Operations.** Deployment is an explicit, reviewed operator or agent action
(the Apify CLI or REST API; the Apify MCP server cannot deploy), never automatic
from pull-request code. Hardware Radar-side kill switches, not a platform
toggle, disable execution. Runtime credentials come only from a Hardware
Radar-scoped OpenBao reference, never the venture's token. The operator
workflow is MS2-D-43 in the MS-2 plan.

**Addendum (MS-2 plan revision 6, review round 5).** Sharpens items 2, 3, 4, and 6
without changing the owner's rule:
- Hardware Radar's admission debits its own reconciled spend for the cycle on top
  of the account usage figure until there is evidence that the figure already
  includes it; reconciliation never makes spend disappear from the check.
- The account is shared, and neither the ~10% margin nor the account usage limit
  bounds what other workloads consume. The owner supplies a Hardware Radar-side
  bound on other workloads' consumption per cycle (equivalently, Hardware Radar's
  share of the prepaid allowance). Until it is set, Hardware Radar's paid
  admission is denied. The account limit (currently $19, with enforcement
  deviation of up to about 10%) is a secondary backstop, not proof of zero
  overage.
- Elapsed time after a run is not evidence that its usage is final. Usage counts
  at its execution bound until a stable-read settlement rule is met, and a later
  upward correction still counts.
- Hardware Radar keeps one budget across environments: one environment holds
  paid-admission authority per cycle, and a handoff happens only after it is
  disabled and every liability is settled.
- Operator builds and inspections are reserved in the same ledger before they
  run.
- (Plan revision 7, review round 6.) A run's usage is still re-read for a fixed
  window after reconciliation; any upward correction re-checks every budget limit
  and pauses paid work if one is exceeded. A handoff between environments waits
  until those windows close and is bound to exactly one destination.
  Inspections are reserved as finite, priced envelopes.
- (Plan revision 8, review round 7.) A correction window closes only when a
  successful usage read at or after its end has been recorded, with any
  correction it finds already counted; an elapsed window or a failed read
  closes nothing, and a handoff needs that closing evidence. Closure is not
  proof that the provider will never correct the figure later; that remains an
  acknowledged residual. Operator builds are recorded by their provider build
  identifier, so a build's cost stays attributable and monitored after it
  settles.

**Verified account state, 2026-09-24** (read-only API; a record of fact, not
part of the rule): plan Starter, $19 base price, $19 prepaid usage credit,
account usage limit $19; billing cycle 2026-09-05T00:00:00Z →
2026-10-04T23:59:59.999Z; current cycle usage about $0.09 from other workloads
sharing the account; 31-day data retention; residential proxy available on the
account (so policy, not the account, prevents its use).

## Amendment — 2026-09-25: Credential namespace, runtime/operator authority split, and policy values (owner clarification)

Owner decisions of 2026-09-25 (owner-pasted brief), resolving
[OQ25](../resolved-questions.md#oq25--hardware-radar-apify-credential-and-mcp-tool-scope),
[OQ26](../resolved-questions.md#oq26--external-liability-bound-for-the-shared-apify-account),
[OQ28](../resolved-questions.md#oq28--can-ms-2-exit-on-the-synthetic-proof-alone), and
[OQ29](../resolved-questions.md#oq29--operator-allowance-size). This amendment clarifies the
2026-09-24 amendment; the text above stays as the record of what was first decided.

**10. Dedicated credential namespace (sharpens item 9).** Hardware Radar's Apify credentials
live under their own OpenBao namespace and are never the `apify-actors` venture's credential,
matching the workstation's `secret/apps/<project>/agent/<provider>` convention. Two roles, two
credentials:

- **Operator/deploy role:** an **unscoped** key at OpenBao `secret/apps/hw-radar/agent/apify`
  (fields `token`, `org_id`). Apify does not allow a scoped token to create or modify Actors, so
  operator work — `apify push`/build, and operator inspection under operator reservations —
  needs this key. It is exported per-command as `APIFY_TOKEN` for the Apify CLI/API and is
  **never rendered to the production application environment**.
- **Runtime role:** a separate, **scoped** token at OpenBao `secret/apps/hw-radar/apify`, env
  `HW_RADAR_APIFY_TOKEN` — limited to running Hardware Radar-owned Actors and reading their runs
  and default storages. The owner has not yet created this token; production rendering is
  deferred until Slice E live admission is ready, and `HW_RADAR_APIFY_ENABLED=false` (default)
  remains the fail-closed kill switch regardless of credential state.
- `.mcp.json` is unchanged (four anonymous read-only tools). It stays an operator surface, not
  the runtime protocol, and widens only once a scoped read credential and operator reservations
  exist.

**11. External-liability bound and operator allowance are owner-set policy values, not
architecture (resolves OQ26, OQ29).** `HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD = 5.00` and
`HW_RADAR_APIFY_OPERATOR_ALLOWANCE_USD = 1.00`, both per Apify billing cycle, are owner-tunable
settings inside the cash ceiling and attributable-consumption rule item 2 already states; they
are not part of the architecture itself and may be revised without an ADR change. The
external-liability bound reserves headroom in the shared account's prepaid usage for workloads
Hardware Radar does not control or observe; paid admission, including the F5a synthetic
proof, still fails closed whenever the account snapshot or the external-liability invariant
cannot be satisfied. The $5.00 is a Hardware Radar accounting bound, not permission for any
other workload to spend that amount. The operator allowance is the ledger class item 6
reserves for Actor builds and bounded operator inspection.

**12. MS-2's Apify exit is the synthetic proof (resolves OQ28, sharpens item 8).** The
controlled synthetic Actor proof (task F5a) is sufficient to close the Apify portion of MS-2.
The production Actor-backed merchant pilot (task F5b) is not required to close MS-2; it stays
source/legal-gated by [OQ24](../open-questions.md#oq24--production-actor-backed-merchant-source-admission),
which may remain open after MS-2 closes.
