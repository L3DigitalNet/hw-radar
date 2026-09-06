# MS-2 — Scoring: Design

> Design output (2026-09-06, revision 2 — **owner not in the loop; every §1 decision is _proposed_ and carries a pending disposition**). Instantiates master-spec §19 "MS-2 — Domain logic" as five sequenced sub-milestones, each with its own implementation plan and its own `dev→main` PR, the way the [MS-1 ingestion design](2026-07-05-ms1-ingestion-design.md) instantiated §19 "MS-1 — Core workflow". The master spec and ADRs remain the design source of truth; this document records the MS-2 *decomposition* and the scope decisions it proposes. It introduces no new architecture except where §1 says so explicitly and marks it a proposed decision — every other mechanism below is fixed by an existing ADR or spec section, cited inline.
>
> **Revision 2 (2026-09-06)** answers cross-agent review round 1 (findings SA-001…SA-010) and an independent code-claim verification pass. Material changes: §1 becomes a ratification table with an explicit owner-disposition column; §2.2 defines one scoring population; §2.3 adopts a conservative eBay compliance boundary and a derived-field clearing contract; §3.2.1 fixes the unit-inconsistent veto formula; §3.2.2 folds lot quantity into `$/TB`; §3.2.3 replaces a mathematically false `n_eff` property; §3.3.1–§3.3.3 make cohort bucketing and relaxation deterministic and total and correct a false claim about where N2 attributes are persisted; §3.4.1–§3.4.3 define a complete invalidation model, a canonical-digest write-on-change rule, and an immutable version-dispatch contract.

**Goal:** every listing **in the §2.2 scoring population** carries a **reproducible** 0–100 `deal_score` computed per [ADR 0011](../../adr/adr-0011-composite-deal-score.md) — the weighted geometric mean `base = Π_k max(s_k, 0.02)^{w_k}` at **price 0.50 · fitness 0.25 · seller 0.15 · availability 0.10**, gated by the three non-compensatory veto caps expressed **on the same [0,1] scale as `base`** (device-managed SMR for an enterprise/NAS buyer → `cap = 0.35`; used/seller-refurb with no returns → `cap = 0.60`; seller trust < 0.50 → `cap = 0.60`), with `deal_score = round_half_up(100 · min(base, cap))` — together with the DR-004 per-subscore explanation payload; thin cohorts (`n_eff < 30`) visibly shrink toward neutral and are marked **provisional**; and the documented cohort-relaxation fallback fires when a cohort is too small (master-spec §19 MS-2 acceptance, line 916). §3.2.1 is the normative statement of the formula and its units.

## 1. Proposed owner decisions (2026-09-06)

Revised in revision 2 (2026-09-06) per review finding SA-001. The owner was not available this session. Every row below is a **proposal**, not a settled requirement: each carries an evidence-backed recommended default, the alternatives rejected, the consequence of adopting it, and an **owner disposition that is still pending**. **The MS-2a plan may not be cut while any disposition in this table is `pending`** — that is review finding SA-001 and this document cannot close it; only the owner can.

An override changes the affected sub-milestone's plan, not this decomposition, unless it moves work across a sub-milestone boundary — in which case this document is revised first (§7).

### 1.1 How to ratify

Reply once, one line per decision, in either form:

- `S2-N: accept` — adopt the recommended default verbatim.
- `S2-N: override — <the alternative or value you want instead>` — the named alternative, or a different constant/threshold.

A decision left unanswered stays `pending` and continues to block the MS-2a plan. Dispositions are recorded back into the **Owner disposition** column of this table (and, where an override changes spec text rather than only MS-2 behavior, opened as OQ22+ through the normal process — see §6).

### 1.2 Ratification table

| # | Decision (proposed) | Recommended default | Alternatives rejected | Consequence | Owner disposition |
| --- | --- | --- | --- | --- | --- |
| S2-1 | Where scores live | **A new `listing_score` TimescaleDB hypertable**, composite PK `(listing_id, scored_at)`, append-only, write-on-change (§3.4.2); plus denormalized current-score columns on `listing` (`current_deal_score`, `current_score_provisional`, `current_scored_at`) as a read-path convenience only, cleared per §2.3. | (a) **Columns on `offer_snapshot`** — the score is not an observation of the merchant, it is our conclusion about one, and a rescore triggered by a cohort shift or a grain upgrade (Appendix C.3.3: "a grain upgrade re-scores the listing") has no new observation to hang on, so it would force a synthetic snapshot row and corrupt DR-005's "re-run ⇒ new observations" invariant. (b) **One mutable current row per listing** — NFR-007 requires historical scores to be reproducible from stored inputs, and overwriting discards the very inputs (`n_eff`, `λ`, cohort membership) the reproduction needs. | New table + migration in MS-2a. Follows the existing `offer_snapshot` hypertable precedent (`catalog/migrations/0003_offer_snapshot_hypertable.py`, composite PK on `(listing_id, observed_at)`). The denormalized columns follow the `listing.product_model` / `resolution_grain` precedent in `catalog/models/market.py` — refreshed by exactly one writer, never the source of truth. **Resolves the §9 ambiguity flagged in §6** in favor of the `listing_score` table named in DR-004 and in §9's own downstream-groups paragraph. | **pending** |
| S2-2a | Retention class of scoring rows | **`listing_score` inherits its listing's `retention_class`** (it is `RetentionGoverned`), so an eBay-derived score row carries `ebay_listing_observation` and expires with the listing; every other source's score is `merchant_fact` (indefinite). **No new `RetentionClass` value is added.** | (a) A new indefinite `listing_score` class — the DR-004 payload stores "input facts used", i.e. the eBay item price and seller evidence, so an indefinite class would retain eBay observation data past the IR-002 / DR-001 ≤ 6 h freshness and delete-on-delist obligation. (b) Storing the payload without input facts to dodge the obligation — defeats DR-004 and NFR-007. | Reuses the DR-008 eBay carve-out pattern verbatim (heartbeat rows already override class TTLs per source), so `retention_constraints("listing_score")` and the existing `purge_expired` sweep cover the new table with no change to `catalog/models/base.py`. Pairs with the §2.3 clearing contract: expiry of the authoritative row must also clear the S2-1 denormalized copy. | **pending** |
| S2-2b | eBay's contribution to cohort baselines (**compliance; counsel-adjacent**) | **eBay observations are EXCLUDED from `cohort_baseline` inputs.** Baselines are built from the four non-eBay top-5 sources only; eBay listings are still **scored against** those baselines and their own `listing_score` rows stay bounded per S2-2a. `cohort_baseline` therefore holds no eBay-derived aggregate and stays `merchant_fact` / indefinite. Enforced by the §2.3 source-composition invariant and its test. | (a) **Including eBay in the aggregate and relying on the aggregate being "only a summary"** — rejected: master-spec line 404 prohibits building or acting on a derived **eBay price model**, and an indefinitely retained weighted-quantile summary of eBay prices is exactly a derived price model that survives the line-710 six-hour/delete-on-delist posture. Inferring permission from "it is only an aggregate" is the inference this design refuses to make. (b) **A bounded eBay-inclusive aggregate that expires on the same 6 h clock** — rejected here as *not this document's call*: it may well be defensible, but it is an owner/counsel decision, not a design derivation. | **Cost, stated plainly:** eBay is likely the highest-volume of the five sources, so excluding it lowers `n_eff` across the board, pushes more cohorts below 30, and makes relaxation (§3.3.2) and the provisional marker fire far more often than they would otherwise. Rankings still work — every listing is compared against the same non-eBay yardstick — but confidence is lower and MS-2d should report the size of the effect. **This is the decision most worth an owner override**, and (b) is the override to name. | **pending** |
| S2-3 | Recompute mechanism | **A dedicated scheduled `score-refresh` APScheduler job on a 60 s interval**, two-phase (refresh dirty `cohort_baseline` rows → rescore the affected listings per §3.4.1). It is **never** inline in the acquisition pipeline. | (a) **Incremental scoring inside the persist stage** — rejected twice over: the price subscore is *cohort-relative*, so one new cheap listing changes every peer's percentile and a per-listing incremental score would disagree with its own cohort's baseline within the same run; and putting a cross-source read on the per-source pipeline's critical path breaks the NFR-001 per-source isolation that `acquisition/pipeline.py` is built around. (b) **A 15-minute or hourly full recompute** — FR-001's tightest freshness SLO is p95 ≤ 3 min transition-to-alert for drop-prone sources with a cheap signal, and MS-4 watch matching consumes the score, so a 15-minute scoring lag would consume the entire budget before the alert path starts. | One `scheduler.add_job(..., "interval", seconds=SCORE_REFRESH_SECONDS, id="score-refresh")` alongside the existing `retention-sweep` / `bucket-checkpoint` jobs in `poller/service.py:build_scheduler`. The existing `job_defaults={"max_instances": 1, "coalesce": True}` already gives the idempotence and overlap protection this needs. §14 runtime bound: the full refresh must complete inside its own 60 s interval at v1 scale; MS-2d measures and records it (see S2-9). | **pending** |
| S2-4 | Cohort dirtiness | **Derived, not queued**: a cohort's dirtiness is computed from stored dependency stamps at each tick, per the complete model in §3.4.1 — which covers new observations, window-boundary exits, retention deletions, refdata changes, version bumps, and a decay staleness ceiling. | A `score_recompute_queue` table written by the persist stage — a second source of truth for something already derivable, it re-couples scoring to the acquisition path (contradicting S2-3), and a lost enqueue silently freezes a cohort's scores with no detectable symptom. | No new coupling and no queue table; the dirty check is a bounded set of indexed aggregate queries per refresh. A cohort with no dependency change and a fresh-enough baseline is skipped entirely, so a quiet system does near-zero work at 60 s. **Round-1 finding SA-003 was against the round-1 wording** ("dirty iff a newer snapshot exists"), which was incomplete; §3.4.1 is the replacement. | **pending** |
| S2-5 | Where cohort statistics are computed | **In Python**, inside a pure `scoring/` library, fed by one bounded SQL pull per refresh (the 90-day window of `(listing_id, observed_at, USD landed price, capacity_tb, quantity, cohort key parts, source_site)`). | (a) A **TimescaleDB continuous aggregate** — rejected on a hard technical ground, not preference: ADR 0011's weights are `w = 2^(−age_days/30)`, a **now-relative** exponential decay, and a continuous aggregate materializes over fixed time buckets. Every weight changes on every tick, so the materialization would be stale by construction and would have to be fully re-materialized each run anyway. The cohort key also requires joins through `product_variant` → `product_model` → `drive_spec`, which is fragile inside a CAGG definition. (b) **SQL window functions** — `percentile_cont` computes an *unweighted* percentile; there is no built-in weighted-percentile aggregate, so the decay would have to be emulated by row duplication. | Mirrors the Appendix C.3 "pure-function library plus a DB-facing service" split that `matching/` already implements (`matching/ladder.py` pure, `matching/resolver.py` DB-facing). The whole ADR-0011 math becomes table-testable with no database — including the mock-data archetypes the ADR was ratified on. Reopen if profiling at MS-2d shows the SQL pull, not the Python, dominates the refresh budget. | **pending** |
| S2-6 | Where score-affecting constants live | **Versioned code under `algorithm_version`** (§3.4.3), not in ADR-0016 settings rows. | The ADR-0016 settings-row pattern used for cadence and matcher thresholds — rejected here specifically: NFR-007 requires a historical score to be reproducible from stored inputs, and a mutable row changed by `UPDATE` silently invalidates every score computed before the change unless the change also bumps a version — at which point the version, not the row, is doing the work. | `algorithm_version` is a stored column on every `listing_score` row (DR-004: "algorithm version; thresholds and margins"). A constant change is a code change, a version bump, and a full rescore. The tunables ADR 0016 legitimately owns (poll cadence, occurrence thresholds) are untouched. The constant set this covers is enumerated in §3.4.3. | **pending** |
| S2-7 | A listing with no usable capacity | **Scores `s_price = 0.5` with `price_basis = "capacity_unavailable"`, `λ = 0`, and the `provisional` marker set** — it is still scored. | (a) **Not scoring the listing** — §19 MS-2 acceptance (line 916) says **every** listing has a 0–100 score; §2.2 bounds "every" to the scoring population, and a listing in that population is always scored. (b) **`s_price = 0`** — a missing input is not evidence of a bad deal, and the 0.50 price weight would drive the listing below genuinely overpriced peers and poison the MS-3 dashboard ordering. | 0.5 is not an invention: it is exactly the limit of ADR 0011's own formula `s_price = λ·(1−q) + (1−λ)·0.5` as `λ → 0`, which is the mechanism the ADR already prescribes for a cohort with no usable evidence. The explanation payload states the reason, so the glass box stays honest (FR-006). §3.3.2 names the two other `price_basis` values that reach this path. | **pending** |
| S2-8 | Cap posture on unknown values | **Caps fire only on affirmative evidence; `unknown` never triggers a cap** — it raises a risk flag instead. Concretely: `recording_tech = smr_dm` caps at 0.35; `smr_hm` and `smr_unknown` do not (flag only); `returns_policy = no_returns` on a used/seller-refurb variant caps at 0.60; `unknown` does not (flag only). | Treating `unknown` as the unsafe value ("fail closed") — a cap is a hard, non-compensatory disqualifier, so a false positive is far more damaging than a false negative, and this repository already ratified that posture for identity in ADR 0019 (only deterministic rungs auto-accept; C.3.5 "a false merge corrupts … a miss only queues"). Host-managed SMR is additionally a *deliberate* enterprise choice, not the trap the ADR names. | The risk-flag list is a first-class part of the DR-004 payload, so an unknown-SMR or unknown-returns listing is visibly qualified in the MS-3 listing detail rather than silently capped. **Verified reach limit:** `smr_dm` is only ever available at model grain (§3.3.3), so the SMR veto is structurally model-grain-only — below model grain every SMR listing lands on the flag-only path by construction, not by choice. If the owner prefers fail-closed, it is a one-constant change under S2-6 plus a version bump. | **pending** |
| S2-9 | Delivery shape | **Approach A decomposition + PR per sub-milestone** — five plans, five `dev→main` PRs, mirroring MS-1's S-6. | A single MS-2 plan — the pure math library and the DB/scheduler service have disjoint verification strategies (table-driven no-DB tests vs. live-TimescaleDB tests), and MS-1's five-PR cadence is the established repository rhythm. | Five plan documents under `docs/superpowers/plans/`, five PRs; `main` (and prod) advances per increment. | **pending** |
| S2-10 | Dependency on ADR-0019 ratification | **MS-2 does not wait for the MS-1e / ADR-0019 ratification.** | Blocking MS-2 on ADR 0019 flipping `proposed → accepted` — §19's MS-2 acceptance (line 916) names the ≥ 80% model-grain figure as "a coverage **expectation, not a gate** — C.3.5; scoring cohorts consume the resolved grain", and names no ratification dependency; the ratification is MS-1's own acceptance criterion (line 908). | MS-2 proceeds against whatever resolution state exists. Below-model-grain behavior is specified in §3.3.3: listings under model grain fall back to persisted N2-extracted attributes at a lower provenance, or to S2-7's neutral price subscore; the shortfall is *reported* by the MS-2e coverage report and routed to the catalog/matcher backlog — C.3.5: "shortfall signals catalog/rule gaps, never a reason to loosen precision." | **pending** |
| S2-11 | Missing shipping | **A flag, not a numeric haircut, at MS-2.** | Applying a percentage penalty to `$/TB` when `shipping_price IS NULL` — C.4 says "missing shipping → penalty/flag" but neither ADR 0011 nor the spec fixes a magnitude, and inventing one would silently change every affected listing's rank. | `shipping_unknown` enters the risk flags and the user-facing explanation text; `$/TB` is computed from `total_landed_price` as it stands (item + known shipping + known tax). If the owner wants a haircut, it is a constant under S2-6. | **pending** |
| S2-12 | Per-source seller policy priors | **`SourceConfig` rows** (ADR 0016 row pattern), defaulting to **0.60 for eBay** (major marketplace) and **0.50 for every other source**, per C.4. | Hard-coding the priors — unlike S2-6's score-shaping constants, the prior is a *per-source classification* judgement the owner should be able to change per source without a deploy, and the row value is stamped into the explanation payload and the §3.4.1 input digest so reproducibility survives. | New `SourceConfig.seller_policy_prior` float column in MS-2a. **Load-bearing arithmetic detail:** the veto is `seller trust < 0.50` (strict), so the 0.50 "other" prior sits exactly on the boundary and does **not** trip the cap. An implementation using `<=` would cap every first-party merchant listing at 60. MS-2b carries an explicit boundary test at 0.50. | **pending** |
| S2-13 | Reproducing historical scores after a version bump | **An immutable version-dispatch contract**: every persisted `algorithm_version` keeps an executable, frozen module under `scoring/versions/`, with per-version golden fixtures run on every change (§3.4.3). | A **per-row reproducibility manifest** embedding the full constant set in `inputs_json` — rejected: constants are only half the algorithm. The *code path* — rubric structure, relaxation order, rounding mode, digest canonicalization — cannot be embedded in a row, so a manifest would guarantee the constants and still not guarantee reproduction, which is precisely what NFR-007 asks for. | One module per released version plus a hash manifest and a CI immutability check; the version-module directory grows monotonically until a retirement command proves no row cites a version. Costs a little duplication; buys NFR-007 literally. **New decision in revision 2** (review finding SA-010). | **pending** |
| S2-14 | Terminal behavior when relaxation is exhausted | **The terminal cohort stays provisional with its actual `n_eff`** and `λ = min(1, n_eff/30)`; it does **not** collapse to the neutral price subscore. The neutral path (S2-7) is reserved for a cohort with **zero** usable observations. | **Falling back to neutral price when the tier-relaxed cohort still has `n_eff < 30`** — rejected: ADR 0011's warm-up shrinkage is already the designed answer to thin evidence, and `s_price = λ·(1−q) + (1−λ)·0.5` degrades continuously toward 0.5 as `n_eff → 0`. A hard switch would discard real evidence and put a discontinuity in the score surface at an arbitrary threshold. | `relaxation_step = "tier"` plus `relaxation_exhausted = true` on the baseline and in the payload; the listing is visibly provisional with its true `n_eff`. Under S2-2b this path will be common early. **New decision in revision 2** (review finding SA-004). | **pending** |
| S2-15 | Lot quantity in `$/TB` | **`$/TB` divides by validated lot quantity** (master-spec line 1200): `dollars_per_tb = usd_total_landed_price / (capacity_tb_per_unit × quantity)`. Quantity comes from the existing N2 extractor, persisted per §3.3.3, with the missing/invalid/low-confidence semantics in §3.2.2. Cohort membership still uses **per-unit** capacity. | **Ignoring quantity** (the round-1 wording) — contradicts master-spec line 1200 outright and scores a 4-pack as four times more expensive than it is, corrupting the cohort distribution it also feeds. | No new extractor: `matching/vocab.py` `extract()` already returns `quantity` as an `Attribute[int]` with confidence, layer and `source_text`. MS-2a/2c must **persist** that output (§3.3.3) — today it is computed in `matching/resolver.py` and discarded. **New decision in revision 2** (review finding SA-007). | **pending** |
| S2-16 | Which listings are in scope | **One population predicate** (§2.2): a top-5-source listing that is `active()` (not delisted, not past `expires_at`) and has at least one `offer_snapshot`. A listing leaving the population has its denormalized score fields cleared (§2.3); it is rescored on re-entry. | (a) **"Every listing ever persisted"** — would require scoring redacted, delisted and expired rows whose merchant content is gone by contract. (b) **"Every listing with a snapshot, including delisted"** — retains and displays a current score for an offer that no longer exists, and for eBay conflicts with delete-on-delist. | The goal statement, the MS-2d exit criterion, and the MS-2 final acceptance all quantify over this one predicate. Note the deliberate asymmetry with baselines: the *observation* population feeding a baseline is broader in time (90 days of non-eBay snapshots, including those of listings that have since left the population) — §2.2 states why. **New decision in revision 2** (review finding SA-009). | **pending** |

**Scope fence (unchanged from the brief and §19):** MS-2 scores the top-5 recert sources' listings only. No new sources, no alerting (MS-4), no UI (MS-3) — MS-2 produces the payload FR-006 says MS-3 renders.

## 2. Module layout

New code under `src/hw_radar/`, split on the same seam `matching/` already uses — a pure library plus one DB-facing service:

```
hw_radar/
├── scoring/                     # MS-2b + MS-2c + MS-2d
│   ├── contracts.py             # Pydantic v2: CohortKey, CohortStats, SubscoreSet,
│   │                            #   ScoreExplanation, ScoredListing (the DR-004 payload
│   │                            #   shape) — mirrors acquisition/contracts.py's style
│   ├── cohort.py                # PURE: cohort-key derivation, capacity bucketing,
│   │                            #   and the relaxation ladder (§3.3.1, §3.3.2)
│   ├── stats.py                 # PURE: w = 2^(-age_days/30); n_eff = (Σw)²/Σ(w²);
│   │                            #   weighted cheapness percentile q on ln($/TB);
│   │                            #   λ = min(1, n_eff/30)
│   ├── subscores.py             # PURE: price / fitness / seller / availability rubrics,
│   │                            #   including the §3.2.2 quantity-aware $/TB
│   ├── vetoes.py                # PURE: the three non-compensatory caps + risk flags
│   ├── compose.py               # PURE: dispatch on algorithm_version (§3.4.3)
│   ├── digest.py                # PURE: canonical JSON + SHA-256 for the input
│   │                            #   fingerprint (§3.4.1) and decision digest (§3.4.2)
│   ├── explain.py               # PURE: machine payload + user-facing text (FR-006)
│   ├── versions/                # PURE, IMMUTABLE once released (S2-13)
│   │   ├── __init__.py          #   algorithm_version → module registry
│   │   ├── v1.py                #   the complete v1 constant set + score() entry point
│   │   └── MANIFEST.json        #   per-version SHA-256, gate-checked (§3.4.3)
│   ├── baseline.py              # DB-facing: dirty-cohort detection, the 90-day SQL pull,
│   │                            #   cohort_baseline writes, source-composition invariant
│   └── service.py               # DB-facing: ScoringService — rescore a cohort, write
│                                #   listing_score, refresh listing denorm columns
├── poller/service.py            # + the score-refresh job (S2-3)
├── matching/resolver.py         # + persist N2 extraction on the listing (§3.3.3)
├── acquisition/sources/ebay.py  # + seller feedback and return terms (§3, MS-2e)
└── catalog/models/
    ├── scoring.py               # NEW: ListingScore, CohortBaseline, SellerRatingObservation
    ├── market.py                # + Listing.returns_policy / returns_window_days /
    │                            #   extracted_attrs_json / current_deal_score /
    │                            #   current_score_provisional / current_scored_at;
    │                            #   OfferSnapshot.usd_total_landed_price
    ├── identity.py              # DriveSpec.market_tier → TextChoices (MarketTier)
    └── ops.py                   # + RunKind.SCORE; SourceConfig.seller_policy_prior
```

Everything except `baseline.py`, `service.py` and the models is a **pure-function library** — no I/O, no Django imports beyond types — so the entire ADR-0011 math is table-testable against the mock-data archetypes with no database, exactly as `matching/ladder.py` is testable without one.

### 2.1 MS-2 data-model additions (fields ADR 0011 needs that do not exist today)

Verified against `src/hw_radar/catalog/models/` and `src/hw_radar/matching/` at `17310c3`. Each row is a migration or a field addition in MS-2a.

| Need (ADR 0011 / C.4) | Today | MS-2 addition |
| --- | --- | --- |
| USD **landed** price for `$/TB` (FR-004 folds shipping + tax into `$/TB`) | `OfferSnapshot.usd_item_price` is a stored generated column but multiplies **`item_price`** by `fx_rate` — item only, not landed; `total_landed_price` is generated but stays in the **native currency** | New stored generated column `usd_total_landed_price = total_landed_price × fx_rate`, reusing the existing NULL-propagation comment's rule (a NULL `fx_rate` must propagate to NULL, never be treated as USD) |
| `$/TB` | **No `dollars_per_tb` column exists**, and it cannot be a row-local generated column: capacity lives on `drive_spec` (per `product_model`), not on the snapshot row, and per S2-15 the divisor also needs lot quantity | Computed in `scoring/subscores.py` (§3.2.2) and **persisted on the `listing_score` row** as a stored input fact (DR-004). See the §6 spec observation |
| Lot quantity for `$/TB` (master-spec line 1200) | `matching/vocab.py` `extract()` **does** return `quantity` as `Attribute[int]` (patterns `lot of N` @0.95, `N-pack` @0.9, `qty: N` @0.9, `Nx` @0.7), but `extract()` is called only in `matching/resolver.py` and its output is **discarded** — nothing persists it | `Listing.extracted_attrs_json` (below) carries it; §3.2.2 defines validation and fallback semantics |
| N2-extracted attributes as a scoring fallback below model grain | **Not persisted anywhere.** `acquisition/persist.py` writes `attrs_json=dict(normalized.attrs)` — **adapter**-supplied attributes only (WD: `saleable`; ServerPartDeals: `sku` / `variant_title`). `matching/resolver.py` reads `attrs_json` only for a structured `"mpn"` key. The round-1 claim that N2 attributes are persisted in `offer_snapshot.attrs_json` was **false** and is corrected in §3.3.3 | New `Listing.extracted_attrs_json` (JSONField, default `{}`), written by `matching/resolver.py` on every evaluation from the `ExtractedAttributes` it already computes. Merchant-derived content, so it joins `Listing.REDACTED_CONTENT_FIELDS` (the JSONField blanks to `{}` with no migration, per that field-set's own rule) |
| "no returns" — veto cap #2 | **No returns field on any model** | `Listing.returns_policy` (TextChoices `returns_accepted` / `no_returns` / `unknown`, default `unknown`) + nullable `Listing.returns_window_days`; the four merchant sources publish a site-level policy, so their rows are a per-source constant recorded in the MS-2e plan. **eBay is not free:** `acquisition/sources/ebay.py` parses no `returnTerms` today, and the adapter consumes `item_summary/search` — see §3, MS-2e |
| Seller trust — subscore + veto cap #3 | `Seller` carries only `source_site`, `name`, `normalized_name`, `notes` — **no rating fields**; `seller_rating_observation` does not exist | New append-only `SellerRatingObservation` (`seller` FK, `observed_at`, `positive_count`, `total_count`, `rating_percent`, `source_site`, `RetentionGoverned`) — the table §9's downstream-groups paragraph already names |
| eBay seller feedback (the only live rating input among the top 5) | `acquisition/sources/ebay.py` extracts `seller.username` only (`_seller_username`) — `feedbackPercentage` / `feedbackScore` are **not captured** | Extend the eBay adapter to capture both into `SellerRatingObservation` (MS-2e), alongside `returnTerms` — one adapter extension covering all three fields |
| Market tier — a **cohort-key component** and the fitness `T` input | `DriveSpec.market_tier` is a free-text `CharField(max_length=50, blank=True, default="")`; `refdata/contracts.py` passes it through as a bare `str` | Convert to a `MarketTier` TextChoices (`enterprise` / `nas` / `surveillance` / `consumer` / `unknown`) + data migration. The migration must also define the parent hierarchy §3.3.2 consumes. Free text cannot be a stable cohort-key component: two spellings shatter one cohort into two thin ones and hand both to the warm-up machinery. **Tier is not extractable from a title** — `vocab.extract()` produces no tier at all, so an unresolved listing's tier is `unknown` by construction (§3.3.3) |
| Cohort interface / form-factor component | `DriveSpec.interface` and `DriveSpec.form_factor` are free-text `CharField`s | **No schema change** — normalized to a canonical token by `scoring/cohort.py` (pure), because unlike `market_tier` these already arrive from the ADR-0018 catalog in a small, closed vocabulary; the normalizer is table-tested and a spelling outside the vocabulary raises the cohort component to `unknown` rather than silently forking it. Revisit if MS-2c measures real fragmentation |
| Invalidation stamps (§3.4.1) | Nothing today records why a score is current | `ListingScore.inputs_digest` (the canonical input fingerprint) and `ListingScore.decision_digest` (§3.4.2); `CohortBaseline.source_composition`, `CohortBaseline.observation_count`, `CohortBaseline.oldest_observed_at`, `CohortBaseline.relaxation_exhausted` |
| Scoring run visibility (NFR-004) | `RunKind` has `full` / `heartbeat` / `reference` / `probe` | `RunKind.SCORE`, so each `score-refresh` writes a `ScraperRun` row with counts and runtime — the §14 measurement S2-3 promises |
| Per-source seller prior (S2-12) | `SourceConfig` has no scoring fields | `SourceConfig.seller_policy_prior` float, default 0.50, eBay's row seeded to 0.60 |

### 2.2 The scoring population (one predicate, used everywhere)

Review finding SA-009: the round-1 document quantified over "every listing" in the goal, "top-5-source listings" in the scope fence, and "every active listing" in the MS-2d exit. Those are three different sets. **One predicate, `P`, replaces all three** (decision S2-16).

A listing is in `P` when **all three** hold:

1. **Source** — its `source_site` is one of the five MS-1 primary recert sources (WD Recertified, Seagate Recertified, ServerPartDeals, goHardDrive, eBay Browse; master-spec line 906).
2. **Live** — it satisfies `Listing.objects.active()`, which is `delisted_at IS NULL AND (expires_at IS NULL OR expires_at > now())` (verified in `catalog/models/market.py`; the second clause exists because the hourly retention sweep can lag a bounded eBay listing's six-hour window).
3. **Observed** — at least one `offer_snapshot` row exists for it. A listing with no observation has no price at all; it is **out of population**, not neutral-scored. S2-7's neutral path covers a missing *capacity*, never a missing price.

Consequences, stated so the acceptance tests can be written against them:

- **Entering `P`** — a new listing enters on its first snapshot and is scored on the next tick. A delisted listing re-entering through `mark_relisted()`, or an expired listing whose `expires_at` is pushed forward by a fresh snapshot, re-enters and is rescored on the next tick (it has no current decision digest, so §3.4.2 always writes).
- **Leaving `P`** — on delist or expiry the listing's denormalized `current_*` score fields are cleared in the same transaction as the delist/redaction (§2.3). Its historical `listing_score` rows are **not** deleted by leaving `P`; they are governed solely by their own retention class (S2-2a), so bounded eBay rows are deleted by the sweep and `merchant_fact` rows persist for audit.
- **Baselines quantify over a different set.** `cohort_baseline` is built from **observations**, not from members of `P`: the 90-day window of non-eBay `offer_snapshot` rows (S2-2b), *including* rows belonging to listings that have since been delisted or have left `P`. A price observed last month is valid market evidence regardless of whether that offer still exists, and those rows are `merchant_fact` / indefinite so they are lawfully retained. This asymmetry is deliberate; §3.4.1 keeps it consistent by stamping the baseline from observation state, not listing state.

`P` is the population in the goal statement, in the MS-2d exit criterion, and in the MS-2 final acceptance in §3.

### 2.3 Compliance boundary and derived-field clearing

Review finding SA-002, blocking. Two obligations, both enforced in code and both tested.

**(a) Source composition of every baseline.** Per S2-2b, `cohort_baseline` is built from non-eBay observations only.

- `CohortBaseline.source_composition` is a JSON object mapping `source_site.normalized_name → {observation_count, weight_sum}`, written on every baseline computation.
- **Invariant:** no key of `source_composition` may name a source whose `retention_class` is in `BOUNDED_RETENTION_CLASSES`, and the 90-day SQL pull (§S2-5) filters those sources out at the query level rather than dropping them in Python — the filter is the enforcement, the composition record is the proof.
- **Test (MS-2c, live DB):** seed a cohort with both eBay and non-eBay observations; assert the baseline's `observation_count`, `n_eff` and quantile summary equal those computed from the non-eBay subset alone, and that `source_composition` names no eBay source. A second assertion covers the degenerate case: a cohort whose only observations are eBay produces **no baseline at all**, and its eBay listings take the §3.3.2 relaxation ladder and then, if still empty, S2-7's neutral path with `price_basis = "no_cohort_evidence"`.
- Master-spec authority: line 399 (retention and licensing are enforced **in the schema**, not by convention), line 404 (do not build or act on a derived eBay price model), line 710 (eBay Browse ≤ 6 h / delete-on-delist / no derived eBay price model).

**(b) Clearing the denormalized score on Listing.** The S2-1 convenience columns (`current_deal_score`, `current_score_provisional`, `current_scored_at`) are a copy of an authoritative `listing_score` row. When that authoritative row goes, the copy must go with it, in the same transaction — otherwise an expired eBay score survives its own evidence, which is exactly the exposure the review named.

Three hooks, all of which already exist and are extended rather than invented:

| Trigger | Hook (verified to exist today) | MS-2 change |
| --- | --- | --- |
| Listing delisted | `Listing.mark_delisted()` — already atomic and already blanks `REDACTED_CONTENT_FIELDS` in the same transaction for delete-on-delist sources | Clear the three `current_*` columns in that same transaction, for **every** delist regardless of retention class, because a delisted listing has left `P` (§2.2) |
| Bounded listing row kept but expired | `Listing.redact_expired()` — the sweep's redaction path for deletion-exempt rows | Same three columns cleared alongside the content redaction |
| Bounded `listing_score` rows deleted by the sweep | `purge_expired` deletes bounded `RetentionGoverned` rows per model, but **does not** clear denormalized fields on any parent — verified | `ListingScore` declares a `clear_parent_denorm(listing_ids)` classmethod that the sweep calls inside the same per-model transaction as the delete, dispatched the same way the sweep already dispatches `deletion_exempt_q()` and `redact_expired()` per model. This keeps the knowledge in the model that owns the denormalization, not in the generic sweeper |

**Tests (MS-2a for the model hooks, MS-2d for the sweep):**

1. Seed an eBay listing with a `listing_score` row; advance the clock past the six-hour TTL; run `purge_expired`; assert the score rows are gone **and** `listing.current_deal_score IS NULL AND current_scored_at IS NULL AND current_score_provisional IS NULL`.
2. Delist a scored listing; assert the `current_*` columns are cleared in the same transaction as the delist mark (assert on the post-commit row, and assert the mark and the clear cannot be observed separately).
3. The `source_composition` tests in (a).
4. A negative control: a `merchant_fact` listing's score row survives a sweep with its `current_*` columns intact.

**This boundary is conservative on purpose and the owner may relax it.** S2-2b records that a less restrictive eBay boundary — for example a bounded, six-hour-expiring, eBay-inclusive aggregate — is an owner/counsel decision, not a design derivation. This document does not infer that permission.

## 3. Sub-milestones

Dependency order: **2a substrate → (2b pure library ∥ 2a) → 2c cohort service → 2d scoring service + job → 2e inputs, explanation surface, coverage.** MS-2b is pure and depends only on the contracts, so it may run concurrently with MS-2a; MS-2c is the first work that needs both.

| Sub-milestone | Goal | Deliverables | Exit criteria | Depends on |
| --- | --- | --- | --- | --- |
| **MS-2a** | The data model, retention wiring, and clearing contract scoring needs | `catalog/models/scoring.py` (`ListingScore` hypertable, `CohortBaseline`, `SellerRatingObservation`); the §2.1 field additions; the §2.3(b) clearing hooks; `scoring/contracts.py` | Gate green; migrations apply from empty and are hypertable-verified; `retention_constraints("listing_score")` proves an eBay-classed row **must** carry `expires_at` and a `merchant_fact` row **must not**; the §2.3 clearing tests pass; no scoring math yet | — |
| **MS-2b** | The whole ADR-0011 computation as a pure, DB-free library | `scoring/cohort.py`, `stats.py`, `subscores.py`, `vetoes.py`, `compose.py`, `digest.py`, `explain.py`, `versions/v1.py` | Gate green; the ADR-0011 mock-data archetypes reproduce their published bands from a table-driven fixture; all three caps demonstrably **bind** at exactly 35 / 60 (§3.2.1); the §3.2.3 `n_eff` property set and its counterexample fixture; the §3.2.2 quantity table; the 0.50 seller-prior boundary test (S2-12) | `scoring/contracts.py` (MS-2a) |
| **MS-2c** | Cohort statistics against real observations, with deterministic relaxation | `scoring/baseline.py`: dirty-cohort detection, the 90-day non-eBay SQL pull, `cohort_baseline` writes, the §3.3.2 ladder wired to real data; the §3.3.3 extraction persistence in `matching/resolver.py` | Gate green (live TimescaleDB); the §2.3(a) source-composition tests; a seeded thin cohort relaxes condition → adjacent capacity → parent tier and records exactly which step fired; exhaustion yields `relaxation_exhausted` with the actual `n_eff` (S2-14); a cohort with `n_eff < 30` yields `λ < 1` and a `provisional` marker; `Listing.extracted_attrs_json` is populated and redacts to `{}` | MS-2a, MS-2b |
| **MS-2d** | Scored listings on a schedule, idempotently, with complete invalidation | `scoring/service.py`; the `score-refresh` APScheduler job; the §3.4.1 invalidation model; §3.4.2 write-on-change; §3.4.3 version dispatch; `ScraperRun(run_kind=score)` | Gate green; **every listing in `P` (§2.2)** carries a `deal_score` after one refresh; a second refresh with unchanged inputs writes **zero** new `listing_score` rows; each §3.4.1 trigger class, exercised in isolation with no new snapshot, updates exactly the affected baselines and listings; a rescore is byte-identical for the same `(inputs, algorithm_version)`; measured refresh runtime recorded against the S2-3 60 s budget | MS-2c |
| **MS-2e** | Real subscore inputs, the glass-box payload, and the coverage report | eBay adapter extension (feedback + return terms); per-source `returns_policy`; `seller_policy_prior` seeding; DR-004 payload completeness; the C.3.5 model-grain coverage report | Gate green; every `listing_score` row carries input facts, `algorithm_version`, thresholds/margins, `n_eff`/`λ`, risk flags, machine payload and user-facing text; the coverage report states per-source model-grain-or-better % against the ≥ 80% **expectation** | MS-2d |

### MS-2a — Scoring substrate

- **`ListingScore`** — hypertable on `scored_at`, composite PK `(listing_id, scored_at)`, monthly chunks (the `0003_offer_snapshot_hypertable.py` precedent). Columns: `deal_score` (smallint, 0–100), `base_score` (numeric, [0,1]), the four subscores (numeric, [0,1]), `cohort_key`, `cohort_baseline` FK, `n_eff`, `lambda_shrinkage`, `is_provisional`, `cap_applied` (**nullable numeric in [0,1]**, NULL when no veto fired — §3.2.1) + `cap_reasons` (jsonb array), `risk_flags` (jsonb array), `dollars_per_tb`, `price_basis`, `quantity`, `quantity_basis`, `inputs_json`, `inputs_digest`, `decision_digest`, `explanation_json`, `explanation_text`, `algorithm_version`, `matcher_grain`, plus `RetentionGoverned` per S2-2a.
- **`CohortBaseline`** — plain table (not a hypertable: one row per cohort key per computation, ~10² cohorts at v1). Columns: `cohort_key`, `computed_at`, `observation_count`, `oldest_observed_at`, `n_eff`, `ln_price_per_tb` distribution summary (the quantile points the percentile and the IQR margin need), `source_composition` (§2.3a), `window_days = 90`, `half_life_days = 30`, `relaxation_step` (`none` / `condition` / `capacity` / `tier`), `relaxation_exhausted`, `algorithm_version`. `retention_class = merchant_fact`, indefinite — lawful precisely because S2-2b keeps eBay observations out of it.
- **`SellerRatingObservation`** — append-only, `RetentionGoverned`; eBay-sourced rows carry `ebay_listing_observation` (IR-002).
- The §2.1 field additions, including the `market_tier` enum data migration (which must also land the §3.3.2 parent map) and `Listing.extracted_attrs_json`.
- The §2.3(b) clearing hooks and their tests.
- **No scoring math in this sub-milestone** — it must be mergeable and deployable on its own without changing any observable behavior.

### MS-2b — The pure scoring library

Every quantity below is ADR 0011's, reproduced exactly:

- **Weighting and decay:** `w = 2^(−age_days/30)` (30-day half-life) over a **90-day rolling window**; `n_eff = (Σw)²/Σ(w²)`.
- **Price:** `q` = the **weighted cheapness percentile** of this listing's `ln($/TB)` within its cohort; `s_price = λ·(1−q) + (1−λ)·0.5` with **`λ = min(1, n_eff/30)`**. `n_eff < 30` ⇒ `is_provisional = True`. (The ratified target is 30, not 50 — the ADR's own Decision Outcome records why: a 64-observation cohort reaches only `n_eff ≈ 49` under a 30-day half-life, so a `/50` target would leave narrow real cohorts perpetually provisional.)
- **Seller trust:** Beta-Binomial shrinkage with prior **μ₀ = 0.95, κ = 20**, plus a **Wilson lower bound at z = 1.2816**: `s_seller = 0.6·p_post + 0.4·LB`. No ratings ⇒ the S2-12 policy prior as an **explicit missing-data state** (flagged in the payload, never silently averaged in).
- **Fitness:** `s_fit = 0.5·T + 0.3·W + 0.2·C`.
  - `T` (suitability) from `DriveSpec.recording_tech`, `market_tier`, `media_type`, and — per **OQ16** — `dwpd` for SSDs: the endurance class folds into `T`, and the cohort key stays ADR 0011's four-part key.
  - `W` (verified warranty) from `ProductVariant.warranty_channel` + `warranty_months`. **Stubbed at the unverified tier for all of MS-2**, with a `warranty_unverified` provisional marker: `verification_event` exists as a table, but C.4 states that the manufacturer warranty-status lookup that would populate it "is **not yet scheduled in §7/§19**; the unverified-tier rubric fallback applies until it is." MS-2 does not schedule it.
  - `C` (condition) from `ProductVariant.condition` + `recert_channel`.
- **Availability:** a bounded rubric over the four `StockStatus` values that actually exist (`in_stock` / `out_of_stock` / `preorder` / `unknown`). ADR 0011 fixes only the endpoints — **in-stock 1.0, out-of-stock 0.0**; the `preorder` and `unknown` interior values are constants under S2-6 (versioned code, bumped and rescored when changed), not settings rows.
- **Accepted v1 calibration notes carried forward from ADR 0011, not "fixed":** the middle band is mildly generous (a median-priced backordered listing scored 69), the `0.02` subscore floor flattens the expensive tail (a top-decile-priced listing scored 14), and the seller-trust prior is sticky-high by design so the `< 0.50` veto fires only on substantial negative evidence. MS-2 reproduces the ratified model; re-fitting constants against real observations is explicitly out of scope.

#### 3.2.1 Normative composition, vetoes, and units

Review finding SA-005, blocking: the round-1 document put `base` in [0,1], named the caps as `35` and `60`, and then wrote `round(100 · min(base, cap))`. Implemented literally, `min(base, 35)` is always `base` and **no veto can ever bind**. The following supersedes every other statement of the formula in this document.

**All subscores, `base`, and `cap` are dimensionless fractions in [0,1]. Only `deal_score` is on the 0–100 scale.**

```text
s_k        ∈ [0, 1]                      for k ∈ {price, fitness, seller, availability}
base       = Π_k max(s_k, 0.02)^{w_k}    w = {price 0.50, fitness 0.25, seller 0.15, availability 0.10}
cap        = 1.0                          when no veto fires
             min over triggered vetoes:
               device-managed SMR for an enterprise/NAS buyer  → 0.35
               used/seller-refurb with no returns              → 0.60
               seller trust < 0.50 (strict)                    → 0.60
deal_score = round_half_up(100 · min(base, cap))    ∈ {0, …, 100}
```

Veto trigger conditions, per S2-8 (affirmative evidence only): `recording_tech = smr_dm` **and** the cohort's `market_tier ∈ {enterprise, nas}`; `condition ∈ {used, refurbished}` **or** `recert_channel = seller`, **and** `returns_policy = no_returns`; `s_seller < 0.50` strictly.

Storage and payload units:

| Quantity | Storage | Range | Payload `scale` |
| --- | --- | --- | --- |
| `s_price`, `s_fit`, `s_seller`, `s_avail` | numeric(6,5) | [0,1] | `unit` |
| `base_score` | numeric(6,5) | [0,1] | `unit` |
| `cap_applied` | numeric(4,3), **nullable** — NULL when no veto fired | [0,1] | `unit` |
| `deal_score` | smallint | 0–100 | `centile` |

`cap_applied` is a fraction, **not** an integer — this is a deliberate change from round 1, where an integer `cap_applied` was half of the unit ambiguity. Every numeric field in the machine payload carries its `scale` tag so a consumer (MS-3) cannot mistake one for the other.

**Rounding is pinned to half-up**, not Python's default banker's rounding: `deal_score = int(Decimal(100 * min(base, cap)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))`. This is a reproducibility requirement, not a preference — a rounding mode that depends on the interpreter's default is not a stored input.

> **Divergence to pin in a fixture.** The ratification prototype in `docs/research/drive-deal-scoring-model-test-results.md:261-270` uses bare Python `round()`, i.e. banker's rounding, with the caps already correctly normalized to `0.35` / `0.60`. The two rules differ **only** at an exact `.5` product. MS-2b carries a fixture at that boundary asserting the half-up result, and the golden archetype fixtures are re-derived under half-up rather than copied from the prototype's output.

**Binding tests (MS-2b):** for each veto in isolation and for all combinations, construct `base` strictly above the cap and assert `deal_score` is exactly `35` or exactly `60`, `cap_applied` is exactly `0.35` or `0.60`, and `cap_reasons` names every veto that fired (not only the binding one). A negative control asserts `cap_applied IS NULL` and `deal_score = round_half_up(100·base)` when no veto fires.

#### 3.2.2 Dollars per TB, with lot quantity

Review finding SA-007, blocking; decision S2-15. Master-spec line 1200 requires `offer_snapshot` `$/TB` to divide by the parsed lot quantity, and the round-1 document omitted it entirely.

```text
total_offered_capacity_tb = capacity_tb_per_unit × quantity
dollars_per_tb            = usd_total_landed_price / total_offered_capacity_tb
```

**Cohort membership uses per-unit capacity, never total.** A 2-pack of 8 TB drives is an 8 TB drive offered twice; it belongs in the 8 TB cohort and competes there on its per-terabyte price. Mixing total capacity into the cohort key would put it in a nonexistent 16 TB bucket.

**Quantity source.** `matching/vocab.py` `extract()` returns `quantity` as an `Attribute[int]` carrying `value`, `confidence`, `layer` (N2) and `source_text`. Its patterns and confidences are, verbatim from the source of truth: `lot of N` @ 0.95, `N-pack` @ 0.90, `qty: N` @ 0.90, `Nx` @ 0.70; the regex bounds `N` to three digits. **No extractor work is needed — only persistence**, which §3.3.3 adds. No adapter supplies a structured quantity today (`persist.py` writes adapter attrs only: WD `saleable`, ServerPartDeals `sku` / `variant_title`), so the title extraction is currently the sole source; the precedence rule below is forward-looking for the day one does.

**Semantics, exhaustive:**

| Case | `quantity` used | `quantity_basis` | Risk flag | Rationale |
| --- | --- | --- | --- | --- |
| Structured adapter quantity present | that value | `structured` | — | Higher provenance tier than a title regex |
| Structured and extracted disagree | structured | `structured_over_extracted` | `quantity_conflict` | Both values recorded in `inputs_json`; the merchant's own field wins |
| Extracted, `confidence ≥ QUANTITY_MIN_CONFIDENCE` | extracted | `extracted` | — | Threshold proposed at **0.85**, derived from the source of truth: it admits `lot of N`, `N-pack` and `qty: N`, and rejects the `Nx` pattern at 0.70, which is the one that also matches speed and generation tokens |
| Extracted, `confidence < QUANTITY_MIN_CONFIDENCE` | 1 | `default_single` | `quantity_low_confidence` | A weak signal must not multiply the denominator |
| Extracted value `≤ 0` or `> MAX_LOT_QUANTITY` | 1 | `default_single` | `quantity_invalid` | `MAX_LOT_QUANTITY` proposed at **100**; the regex admits up to 999, and a 3-digit match above 100 is far more likely a capacity or model fragment than a real lot |
| No quantity signal at all | 1 | `default_single` | `quantity_assumed` | The common case; a single-unit assumption is correct for the overwhelming majority of listings and is stated in the payload rather than hidden |
| Capacity unavailable | — | — | `capacity_unavailable` | `dollars_per_tb` is NULL and S2-7's neutral price path applies regardless of quantity |

`QUANTITY_MIN_CONFIDENCE` and `MAX_LOT_QUANTITY` are constants under S2-6 (versioned code, §3.4.3). `quantity`, `quantity_basis`, and the full provenance record (`{value, confidence, layer, source_text}`) are persisted in `inputs_json` and enter the §3.4.1 input digest, so a change of quantity interpretation rescores the listing.

**Tests (MS-2b table-driven, MS-2c against seeded rows):** equivalent 1-pack and N-pack offers at proportional prices produce identical `dollars_per_tb`; explicit `lot of 4` text; absent quantity; a zero and a 999 quantity; conflicting structured vs. extracted; and an `Nx` title asserting the low-confidence path.

#### 3.2.3 `n_eff` and `λ` — the properties that are actually true

Review finding SA-008: the round-1 test strategy required `n_eff` to be **monotonic in observation count**, which is false for `n_eff = (Σw)²/Σ(w²)` with unequal weights. The reviewer's counterexample is correct and becomes a pinned regression fixture rather than a bug to fix.

**Counterexample fixture.** Ten observations at age 90 days (`w = 2^(−3) = 0.125`) give `Σw = 1.25`, `Σw² = 0.15625`, so `n_eff = 1.5625 / 0.15625 = 10.0` exactly. Adding **one** current observation (`w = 1`) gives `Σw = 2.25`, `Σw² = 1.15625`, so `n_eff = 5.0625 / 1.15625 ≈ 4.378`. Adding data **decreased** `n_eff` — correctly, because one recent observation now dominates the weighted evidence. The fixture asserts the decrease.

**The properties MS-2b actually tests:**

1. **Scale invariance** — `n_eff(c·w) = n_eff(w)` for every `c > 0`.
2. **Bounds** — `1 ≤ n_eff ≤ n` for any `n ≥ 1` strictly positive weights.
3. **Equality at uniform weights** — all weights equal ⇒ `n_eff = n` exactly (the fixture above is its own witness at `n = 10`).
4. **Permutation invariance** — `n_eff` does not depend on observation order.
5. **Conditional monotonicity** — adding an observation whose weight equals the current mean weight increases `n_eff` by exactly 1 (this is the intuition the false property was reaching for, stated in a form that holds).
6. **λ boundary** — `λ = min(1, n_eff/30)`: `λ = 1` exactly at `n_eff = 30`, `λ < 1` strictly below, and `λ` is continuous at 30.

Property-generated cases are compared against a deliberately naive reference implementation of the two summations, so the property tests cannot pass by sharing a bug with the implementation.

### MS-2c — Cohort statistics and relaxation

#### 3.3.1 Cohort key and capacity buckets

**Cohort key** = **capacity bucket · market tier · interface/form-factor · condition** (ADR 0011; four-part for SSDs too, OQ16). Stored as a deterministic string on both `cohort_baseline` and every `listing_score` row, so a historical score names the exact cohort it was scored in (NFR-007).

Component derivation:

- **Capacity bucket** — from **per-unit** `DriveSpec.capacity_tb` (`DecimalField(max_digits=7, decimal_places=3)`, verified). Exact decimal equality would shatter cohorts, so the value is mapped to the nearest member of an ordered constant tuple `CAPACITY_BUCKETS_TB` within a relative tolerance of **5%**; a value outside every tolerance band becomes its own ad-hoc bucket (its value rounded to 3 dp) and carries the `capacity_bucket_adhoc` flag, so an unexpected capacity is visible rather than silently merged.
  - **`CAPACITY_BUCKETS_TB` is derived, not hand-written.** MS-2c ships a management command that enumerates the distinct `drive_spec.capacity_tb` values in the seeded ADR-0018 catalog and emits the ordered tuple; the generator and its output are committed together under `scoring/versions/` (S2-6), so the bucket set is pinned to an `algorithm_version` and a catalog change that introduces a new capacity is a deliberate version bump, not a silent cohort reshuffle. Hard-coding a list here would be an invention with no source of truth behind it.
  - A listing with no usable capacity has **no bucket** and therefore no cohort: it takes S2-7's neutral price path with `price_basis = "capacity_unavailable"` and never enters the relaxation ladder.
- **Market tier** — `DriveSpec.market_tier` after the §2.1 `MarketTier` migration; `unknown` for a listing below model grain (§3.3.3).
- **Interface / form factor** — canonicalized in `scoring/cohort.py`; a token outside the closed vocabulary canonicalizes to `unknown` rather than forking the cohort.
- **Condition** — `ProductVariant.condition`.

#### 3.3.2 The relaxation ladder — deterministic and total

Review finding SA-004, blocking. The ladder is exactly three steps, **cumulative**, evaluated in order, stopping at the first step whose cohort reaches `n_eff ≥ 30`:

| Step | Name | What it does | Cumulative effect |
| --- | --- | --- | --- |
| 0 | `none` | The exact four-part key | — |
| 1 | `condition` | Drop the condition component entirely | key = capacity · tier · interface/form-factor |
| 2 | `capacity` | Add the **immediately adjacent buckets on both sides** (`i−1` and `i+1`) of the listing's bucket index in `CAPACITY_BUCKETS_TB` | condition still dropped; capacity widened to `{i−1, i, i+1}` |
| 3 | `tier` | Replace the tier with its **parent** per the map below | condition still dropped, capacity still widened, tier widened |

Adjacency is **symmetric — both neighbours, not one**, because `$/TB` is already capacity-normalized and there is no a-priori reason a cheaper-per-TB comparison should come from larger drives rather than smaller. Symmetry also disposes of ties: there is no "nearest" choice to make. At the ends of the bucket tuple only the one existing neighbour is added. The ladder **does not** widen to ±2: three steps is what ADR 0011 (line 68) and master-spec line 1243 prescribe, and step 4 would be a new mechanism, not a relaxation of an existing one.

**Parent-tier map** (a versioned constant under S2-6; landed by the §2.1 `MarketTier` migration):

| Tier | Parent | Parent membership |
| --- | --- | --- |
| `enterprise` | `high_duty` | enterprise, nas, surveillance |
| `nas` | `high_duty` | enterprise, nas, surveillance |
| `surveillance` | `high_duty` | enterprise, nas, surveillance |
| `consumer` | `client` | consumer, unknown |
| `unknown` | `client` | consumer, unknown |

The map is **total** (every tier value has exactly one parent) and every parent **strictly widens** (each has ≥ 2 members), so step 3 always does something. `unknown` is grouped with `consumer` deliberately: an unclassified listing has to land somewhere, and putting it with client drives biases the resulting distribution *cheaper*, which lowers a genuine enterprise listing's cheapness percentile and therefore its score. The error direction is conservative — the same posture S2-8 takes on caps, where a false disqualification costs more than a missed one. Unknown-tier listings also carry a `tier_unknown` risk flag throughout.

**Unknown-value relaxation generally.** An `unknown` component is a literal token at step 0 — it matches only other `unknown`s and never silently matches everything. It is then widened by the ordinary steps: condition disappears at step 1, tier maps to `client` at step 3. Interface/form-factor has no relaxation step and stays as-is, including as `unknown`.

**Terminal behavior (decision S2-14).** If step 3's cohort still has `n_eff < 30`, the ladder is exhausted and:

- the listing is scored against that terminal cohort with its **actual** `n_eff` and `λ = min(1, n_eff/30)`;
- `cohort_baseline.relaxation_step = "tier"` and `relaxation_exhausted = true`;
- `is_provisional = true` and the explanation states both the relaxation path and the exhaustion.

Only when the terminal cohort has **zero** usable observations does the listing fall to S2-7's neutral subscore, with `price_basis = "no_cohort_evidence"` (distinct from `"capacity_unavailable"`, which never reaches the ladder at all). Under S2-2b's eBay exclusion this path is expected to be common early, which is exactly why it is specified rather than left implicit.

**Recorded on every score:** the step that fired, the resulting cohort key, `n_eff`, `λ`, and `relaxation_exhausted`. A relaxed cohort must be visibly relaxed, never silently substituted.

**Tests (MS-2c, table-driven over seeded fixtures):** both adjacency directions; a listing in the smallest and in the largest bucket (one-sided widening); an ad-hoc bucket; unknown tier and unknown condition; each of the three steps being the first to reach 30; full exhaustion with a nonzero `n_eff`; and full exhaustion with zero observations. Each case asserts one exact cohort key, `n_eff`, `λ`, `relaxation_step`, and `relaxation_exhausted`.

#### 3.3.3 Input precedence below model grain — corrected

**The round-1 statement of this ladder was factually wrong and its data source did not exist.** Verified at `17310c3`:

- `acquisition/persist.py` writes `attrs_json=dict(normalized.attrs)` — the **adapter's** attributes only (WD: `saleable`; ServerPartDeals: `sku` / `variant_title`). No N2 output reaches it.
- `matching/vocab.py` `extract()` is called in exactly one place, `matching/resolver.py`, where its `ExtractedAttributes` are used to materialize catalog rows and then **discarded**.
- `matching/resolver.py` reads `attrs_json` only for a structured `"mpn"` key.

So the round-1 fallback ladder had no data source at all. The fix is to **persist the extraction**, in this milestone, before anything depends on it.

**MS-2a adds `Listing.extracted_attrs_json`** (JSONField, default `{}`); **MS-2c extends `matching/resolver.py`** to write it from the `ExtractedAttributes` it already computes on every evaluation, in the same save as the existing denormalization refresh.

- *Rejected alternative:* storing the extraction in `ListingResolution.evidence`, which is already a JSONField the resolver writes. Rejected because the resolution edge is append-only audit whose current row may be a `none`-grain edge, whereas the extraction is a property of the listing's title that scoring needs **even when resolution fails**; burying a scoring input in an audit blob also couples score invalidation to edge supersession and hides the field from ordinary queries.
- *Retention:* the extraction is merchant-derived content, so it joins `Listing.REDACTED_CONTENT_FIELDS`, whose own rule is that every member must blank at the database level — a JSONField takes `{}`, so no migration accompanies redaction.
- *Scope correction:* the round-1 document claimed the eBay adapter extension was "the one acquisition-layer change MS-2 makes". MS-2 makes **two** changes outside `scoring/`: this resolver write, and the MS-2e eBay adapter extension (§3).

**The corrected precedence ladder**, highest provenance first:

1. Authoritative `drive_spec` / `product_variant` fields, at **model grain or better**.
2. `Listing.extracted_attrs_json` (N2), tagged at a lower provenance tier in the payload.
3. S2-7's neutral price subscore when even capacity is unavailable.

**What layer 2 can and cannot supply**, verified against `vocab.extract()`:

| Cohort / scoring input | Available at layer 2? | Consequence below model grain |
| --- | --- | --- |
| Capacity | Yes (`capacity_bytes`) | Converted to TB and bucketed per §3.3.1 |
| Interface, form factor | Yes | Canonicalized; unrecognized spelling → `unknown` |
| Condition (+ `recert_channel`) | Yes | Feeds the condition component and fitness `C` |
| Lot quantity | Yes | Per §3.2.2 |
| Recording tech | **Partially** | `_recording()` yields only `"cmr"` or `"smr"` — never `smr_dm` / `smr_hm` / `smr_unknown` (PMR is deliberately unmapped). An extracted `"smr"` maps to **`smr_unknown`** |
| **Market tier** | **No — not extractable at all** | `vocab.extract()` produces no tier. An unresolved listing's tier component is **`unknown`**, it carries the `tier_unknown` risk flag, and its cohort relaxes to the `client` parent at step 3 (§3.3.2) |

**Two consequences that must be stated rather than discovered during implementation:**

- **The SMR veto is model-grain-only.** `RecordingTech.SMR_DEVICE_MANAGED` (`smr_dm`) is reachable only through `DriveSpec.recording_tech`. Below model grain, every SMR listing lands on `smr_unknown`, which under S2-8 raises a flag and never caps. This is a structural reach limit, not a policy choice, and it means MS-2's SMR protection is only as good as model-grain coverage — which is precisely the number the MS-2e coverage report publishes.
- **Unresolved listings never form a tier-specific cohort.** They aggregate into `unknown`-tier cohorts and relax into `client`. Their scores are still produced, still provisional-flagged where thin, and still explained.

A listing never fails to score because the matcher has not reached it.

Dirty detection and the 90-day pull are specified in §3.4.1 and S2-5; the pull excludes eBay sources per S2-2b.

### MS-2d — Scoring service and the scheduled job

- `ScoringService.refresh()` — the two-phase run of S2-3, wrapped in a `ScraperRun(run_kind=score)` row with counts, runtime, and `RunFailureClass` on error, so NFR-004 covers scoring the same way it covers acquisition.
- **Failure isolation:** a cohort that fails to score is logged, flagged, and skipped — it never aborts the refresh for other cohorts, mirroring NFR-001's per-source isolation.

#### 3.4.1 The invalidation model

Review finding SA-003, blocking: the round-1 rule ("a cohort is dirty when a snapshot has `observed_at` later than `baseline.computed_at`", plus grain changes and version bumps) invalidates on **insertions only**, so a score could stay current forever while its inputs expired, its window slid, its seller's rating changed, or its refdata was corrected.

The replacement has two halves: **which baselines are stale**, and **which listings must be rescored**.

**A. Baseline staleness.** A `cohort_baseline` row is stale when **any** of the following holds:

| # | Trigger | Detection (no queue, per S2-4) |
| --- | --- | --- |
| B1 | New observation in the cohort | `EXISTS(offer_snapshot with observed_at > baseline.computed_at)` for the cohort's members |
| B2 | **An observation left the 90-day window** | `baseline.oldest_observed_at < now() − 90 days` — the stored oldest timestamp is what makes this detectable **without any new snapshot**, which is the round-1 gap |
| B3 | Retention deleted observations from the window | `count(live observations in window) ≠ baseline.observation_count` |
| B4 | Cohort-attribute / refdata change | any `drive_spec`, `product_model`, or `product_variant` row reachable from a member listing has `updated_at > baseline.computed_at` |
| B5 | Membership change from re-resolution | any member listing's current `listing_resolution` row id differs from the one recorded at baseline time (this catches a **same-grain target change**, which a grain comparison alone misses) |
| B6 | `algorithm_version` bump | `baseline.algorithm_version ≠ ALGORITHM_VERSION` |
| B7 | **Decay staleness ceiling** | `now() − baseline.computed_at > BASELINE_MAX_AGE` |

B7 exists because `w = 2^(−age_days/30)` is **now-relative**: every weight changes on every tick, so strictly speaking every baseline is always slightly stale. `BASELINE_MAX_AGE` is proposed at **1 hour** (a constant under S2-6): over an hour the uniform weight drift is `2^(−(1/24)/30) ≈ 0.999`, roughly one part in a thousand and very nearly proportional across the cohort, which cannot move a weighted quantile materially at v1 scale. The ceiling turns "always stale" into "bounded stale", which is what makes a 60 s tick affordable and what makes §3.4.2's zero-write idempotence achievable at all — without it, `n_eff` and `λ` would drift on every tick and every listing would write a new row every minute.

**B. Listing rescore triggers.** A listing in `P` (§2.2) is rescored when **any** of the following holds:

| # | Trigger | Reaches |
| --- | --- | --- |
| L1 | Its cohort's baseline was recomputed (`baseline.computed_at > score.scored_at`) | Every listing in that cohort |
| L2 | Its **canonical input fingerprint** changed (below) | That listing |
| L3 | Its `resolution_grain` changed, or its current `listing_resolution` id changed at the same grain | That listing (and, via B5, its old and new cohorts) |
| L4 | A `SellerRatingObservation` was written for its seller | Every listing in `P` for that seller |
| L5 | `SourceConfig.seller_policy_prior` changed for its source | Every listing in `P` for that source |
| L6 | It re-entered `P` (relist, or a snapshot pushing `expires_at` forward) | That listing — it has no current decision digest, so §3.4.2 always writes |
| L7 | `algorithm_version` bump | The entire population (a full rescore, S2-6) |

L4 and L5 are covered mechanically by L2 once the seller aggregate and the prior are part of the fingerprint; they are listed separately because the **fan-out** differs and the MS-2d test must exercise each fan-out shape independently.

**The canonical input fingerprint** (`ListingScore.inputs_digest`) is `SHA-256` over the canonical JSON serialization (sorted keys, no insignificant whitespace, all floats quantized to 6 decimal places) of exactly these inputs:

`usd_total_landed_price`, `fx_rate`, source currency, `stock_status`, `capacity_tb_per_unit`, `quantity` + `quantity_basis`, `condition`, `recert_channel`, `returns_policy` + `returns_window_days`, `recording_tech`, `market_tier`, canonical `interface`, canonical `form_factor`, `media_type`, `dwpd`, `warranty_channel` + `warranty_months`, the seller rating aggregate as of scoring (`positive_count`, `total_count`), `seller_policy_prior`, current `listing_resolution` id + `grain`, `cohort_key`, `cohort_baseline` id + `computed_at`, and `algorithm_version`.

Because the fingerprint includes the baseline's identity and `computed_at`, L1 is implied by L2 — but the refresh evaluates L1 first as a set operation, so a recomputed baseline rescores its cohort in one query rather than by fingerprinting every listing individually.

**Delist, expiry, and redaction are *not* rescore triggers.** A listing leaving `P` is not rescored; its denormalized fields are cleared per §2.3(b) and its history is left to its retention class.

**Test obligation (MS-2d exit).** Each of B1–B7 and L1–L7 is exercised **in isolation, without adding an unrelated snapshot**, asserting that the next refresh updates exactly the affected baselines and listings and nothing else. B2 and B7 require clock advancement across the 90-day and one-hour boundaries respectively; B3 requires running the retention sweep; B5 and L3 require a same-grain re-resolution.

#### 3.4.2 Write-on-change, keyed on the whole decision

Review finding SA-006, blocking: appending a row only when `deal_score`, the cap set, `is_provisional`, or `algorithm_version` changed leaves the current row describing obsolete inputs whenever a subscore, `$/TB`, cohort key, matcher grain, risk flag, seller evidence, margin, or explanation text moves without shifting the rounded score. DR-004 requires the stored row to be the most recent automated decision, not merely a matching number.

**Rule.** `ListingScore.decision_digest` is `SHA-256` over the canonical JSON of the **complete DR-004 decision payload**: `inputs_json` (which itself contains everything the §3.4.1 fingerprint covers, plus provenance records), all four subscores, `base_score`, `cap_applied` and `cap_reasons`, `deal_score`, `dollars_per_tb`, `price_basis`, `quantity` and `quantity_basis`, `n_eff`, `lambda_shrinkage`, `is_provisional`, `cohort_key`, `cohort_baseline` id, `relaxation_step`, `relaxation_exhausted`, `matcher_grain`, `risk_flags`, thresholds and margins, `explanation_json`, `explanation_text`, and `algorithm_version`.

A new row is appended **iff** the freshly computed `decision_digest` differs from the current row's. Zero writes occur **only** when the complete canonical decision is byte-identical.

Canonicalization is pinned in `scoring/digest.py`: sorted keys, UTF-8, no insignificant whitespace, and **all floats quantized to 6 decimal places before serialization**. Without the quantization, ordinary floating-point noise would defeat idempotence; with it, and with §3.4.1's B7 staleness ceiling holding `n_eff` and `λ` still between baseline recomputations, a quiet system genuinely writes nothing. **The two mechanisms are load-bearing together** — remove the ceiling and this rule becomes a write amplifier at 60 s.

**Tests (MS-2d):** change each explanation-only field in turn (a risk flag; the seller evidence counts; `dollars_per_tb` by a sub-rounding amount; the cohort key via relaxation; `matcher_grain`) while holding `deal_score`, the cap set, `is_provisional` and `algorithm_version` constant, and assert a new row **and** an updated `listing.current_*` pointer. Then repeat with a genuinely identical canonical payload and assert **zero** writes and an untouched `current_scored_at`.

#### 3.4.3 Immutable version dispatch (reproducing history)

Review finding SA-010; decision S2-13. Storing `algorithm_version` proves *which* algorithm ran; it does not keep that algorithm runnable. Once a constant changes and the version bumps, a golden test over the current version passes while every older row becomes underivable from the tree — and NFR-007 asks for reproducibility, not for a value in a column.

**Contract:**

1. Each released version is a frozen module `scoring/versions/v<N>.py` exporting the complete constant set and a pure entry point `score(inputs) -> Decision`. `scoring/versions/__init__.py` maps `algorithm_version → module`; `scoring/compose.py` dispatches through it and never inlines a constant.
2. **Immutability is gate-enforced**, not conventional: `scoring/versions/MANIFEST.json` records a SHA-256 per released version module, and a check in `scripts.check` fails if any released module's hash changes. Adding `v<N+1>.py` and a new manifest entry is the only sanctioned way forward.
3. **Every version's golden fixtures run on every change** — `tests/golden/scoring/v<N>/` for each released `N`, not just the current one. This is what catches the subtle case: a version module may import shared pure helpers, and if a helper change moves a historical output the historical fixtures go red and the change must be made version-local instead.
4. The constant set a version owns: the four component weights, the `0.02` subscore floor, the three cap values, the veto trigger predicates, the half-life and window, the `λ` target of 30, the seller prior `μ₀`/`κ` and Wilson `z`, the fitness sub-weights and rubric tables, the availability rubric's interior values, `CAPACITY_BUCKETS_TB` and its 5% tolerance, the parent-tier map, `QUANTITY_MIN_CONFIDENCE`, `MAX_LOT_QUANTITY`, `BASELINE_MAX_AGE`, the rounding mode, and the digest canonicalization rules.
5. **Retirement is proven, not assumed.** A version module may be deleted only when a management command shows no `listing_score` row cites it — which happens naturally as bounded eBay rows expire, and never for `merchant_fact` rows unless they are purged.

*Rejected alternative:* a per-row reproducibility manifest embedding the constant set in `inputs_json`. Rejected because the constants are only half the algorithm: the rubric structure, the relaxation order, the rounding mode and the digest canonicalization are code, not values, and a row cannot carry them. A manifest would make the constants auditable while still leaving the score underivable — the appearance of NFR-007 compliance without the substance.

**Test (MS-2d):** persist fixtures under `v1`; introduce a `v2` that changes a constant; assert both `v1` and `v2` rows re-derive exactly through dispatch, and assert the manifest check fails when `v1.py` is edited.

### MS-2e — Real inputs, the glass box, and coverage

- **eBay adapter extension** — one change covering three fields: `seller.feedbackPercentage` and `seller.feedbackScore` into `SellerRatingObservation`, and return terms into `Listing.returns_policy` / `returns_window_days`. Today `acquisition/sources/ebay.py` extracts the seller **username only** (`_seller_username`) and parses no return terms whatsoever. Cassette-backed, PII-scrubbed per DR-006.
  - **Plan-time investigation, flagged rather than assumed:** the adapter consumes the Browse `item_summary/search` response. Whether return terms are available on that response, or require a per-item Browse `getItem` call, must be confirmed against current eBay documentation and a live cassette **before** the MS-2e plan commits to it — a per-item call would add one request per listing per poll and would need its own quota and cadence analysis. Until that is confirmed, eBay `returns_policy` stays `unknown`, which under S2-8 means a flag and no cap. The round-1 document asserted "eBay Browse exposes `returnTerms`" as settled; it is not, and MS-2 must not plan on it.
- Per-source `returns_policy` population for the four merchant sources (site-level policy constants) and `seller_policy_prior` seeding (S2-12).
- **DR-004 completeness test:** every `listing_score` row carries input facts used, `algorithm_version`, thresholds and margins, confidence (`n_eff` / `λ` + the provisional marker), risk flags, the machine-readable payload, and the user-facing text. FR-006's percentile **+ margin-in-IQR**, seller evidence, fitness rubric pieces, quantity provenance, the relaxation path, and any cap reason are all present — MS-3 renders this payload; it does not compute anything.
- **Coverage report:** per-source model-grain-or-better percentage against C.3.5's **≥ 80% expectation**. Because §3.3.3 makes the SMR veto model-grain-only, this report is also the measurement of how much of the SMR protection is actually in force — the report states both figures. A shortfall is reported and routed to the catalog/matcher backfill queue (`unknown_model_backfill`), never treated as an MS-2 blocker and never a reason to loosen ADR 0019's precision contract.
- **Baseline-composition report (S2-2b):** the share of in-scope listings whose cohort baseline is thin or exhausted, so the cost of the conservative eBay boundary is a measured number the owner can weigh when disposing of S2-2b.

**MS-2 exit = spec §19 MS-2 acceptance (line 916), quantified over `P` (§2.2):** every listing in `P` has a 0–100 score reproducible from stored inputs with a per-factor breakdown; thin-cohort listings (`n_eff < 30`) visibly shrink toward neutral and are marked provisional; the documented cohort-relaxation fallback fires on small cohorts and records which step fired; the ≥ 80% model-grain coverage figure is measured and reported as an expectation.

## 4. Error handling & ops (MS-2 posture)

- The `score-refresh` job writes a `ScraperRun(run_kind=score)` row per run — status, listings scored, cohorts refreshed, runtime, failure class (NFR-004). Scoring becomes visible in the same place acquisition already is.
- A per-cohort failure is isolated (MS-2d); a total refresh failure surfaces as a failed `ScraperRun` and, from MS-4, an operator alert. **No email alerts until MS-4** (ADR 0013) — the MS-1 posture is unchanged: DB rows, structured logs, and the §18.5 dead-man's switch.
- `max_instances=1` + `coalesce=True` are already the poller's `job_defaults`; scoring inherits them, so a slow refresh degrades to lower frequency rather than to concurrent double-writes. §14's "single user; `max_instances=1` per job; no locking/queueing design needed" holds for scoring too.
- **Capacity watch:** `listing_score` is a new write path onto the same disk as `offer_snapshot` and the raw payloads. The §3.4.2 decision digest is the bound — paired with the §3.4.1 B7 staleness ceiling, which is what actually keeps a 60 s tick from writing a row per listing per minute. The MS-1d disk-space alert (CT-116) covers the growth, and the TimescaleDB-aware dump pipeline must be confirmed to include the new tables before the first production refresh — the same pre-real-data check MS-1d ran, re-verified rather than assumed.
- An `algorithm_version` bump triggers a full rescore; at v1 scale that is a single long refresh, not a migration, and it is announced in the sub-milestone PR that bumps it.
- The §2.3(b) clearing hooks run inside existing transactions; a crash between the delist mark and the clear is impossible by construction, and the MS-2a test asserts the two cannot be observed separately.

## 5. Testing strategy

- **Unit (no DB) — the bulk of MS-2:** the entire `scoring/` pure library. Table-driven fixtures reproduce the ADR-0011 mock-data archetypes and their published bands (re-derived under the §3.2.1 half-up rounding); each of the three caps has a fires/does-not-fire pair **and a binding assertion at exactly 35 / 60**; `n_eff` and `λ` carry the §3.2.3 property set plus the non-monotonicity counterexample fixture; the §3.2.2 quantity table is exhaustive; the §3.3.2 relaxation table covers both adjacency directions, bucket ends, unknown values, each first-succeeding step, and both exhaustion outcomes; the seller prior has an explicit **0.50 boundary** test (S2-12); the geometric mean has a floor test proving `max(s_k, 0.02)` prevents a zero subscore from annihilating the product; `scoring/digest.py` has canonicalization tests (key order, float quantization, byte-identical round trip).
- **DB (live TimescaleDB):** the `listing_score` hypertable DDL and retention constraints (including the eBay-classed row that **must** carry `expires_at`); the §2.3 source-composition and clearing tests; the 90-day windowed non-eBay pull; relaxation over seeded thin cohorts; each §3.4.1 trigger in isolation with clock advancement; write-on-change idempotence and its explanation-only-change counterpart; the grain-upgrade and same-grain-retarget rescore paths.
- **Reproducibility (NFR-007):** a golden test that re-derives a stored historical score from its own `inputs_json` + `algorithm_version` **through the §3.4.3 version dispatch** and asserts equality — run for **every** released version on every change, not only the current one. This is the single test that proves DR-004 and NFR-007 together, and it is the one that fails if any score-shaping constant escapes into a mutable row (S2-6) or if a shared helper change moves a historical output.
- **Version immutability:** the `MANIFEST.json` hash check in `scripts.check`, with a negative test that edits a released version module and asserts the gate goes red.
- **Explanation payload:** snapshot-tested (syrupy) so an accidental payload-shape change is a visible diff, since MS-3 renders it.
- Gate (`uv run python -m scripts.check`) green at every commit; each sub-milestone PR runs CI + dependency review.

## 6. Risks / open edges

| Risk / open edge | Posture |
| --- | --- |
| **Every §1 decision is `pending` (SA-001).** The MS-2a plan cannot be cut until the owner disposes of S2-1…S2-16 | **Open and unresolvable by this document.** §1.1 states the ratification form. This is the single blocking item between revision 2 and plan creation |
| **The conservative eBay boundary (S2-2b) costs statistical power.** Excluding the likely highest-volume source shrinks `n_eff` everywhere, so relaxation and the provisional marker fire far more often | Accepted deliberately: the design refuses to infer permission for a derived eBay price model (master-spec 404, 710). MS-2e measures the cost so the owner can dispose of S2-2b with a number in hand. A bounded eBay-inclusive aggregate is the named override |
| **eBay return terms may not be on the response the adapter consumes** (§3, MS-2e). The round-1 document asserted availability; the adapter parses none, and `item_summary/search` may not carry it | Surfaced as an MS-2e plan-time investigation with a defined fallback (`returns_policy = unknown`, flag only, no cap). Not planned on until confirmed against current eBay documentation and a live cassette |
| **The SMR veto is structurally model-grain-only** (§3.3.3): `smr_dm` exists only on `DriveSpec`, and title extraction can never produce it | Stated, not worked around. MS-2e reports the fraction of listings where the veto can actually fire. Closing the gap is a catalog-coverage problem, not a scoring one |
| **Spec hygiene — master-spec §C.4 lines 1255-1262 carry the mixed-unit formula** that produced SA-005: `base` in [0,1] in the same block as caps written as `35` / `60` | **Surfaced, deliberately not fixed here.** §3.2.1 is normative for MS-2 and matches `docs/research/drive-deal-scoring-model-test-results.md:261-270`, which uses `0.35` / `0.60`. A spec editorial pass should normalize lines 1255-1262; that edit is outside this document's single-file scope |
| **Spec hygiene — master-spec line 1200** requires `offer_snapshot` `$/TB` to divide by lot quantity, but `$/TB` is not and cannot be an `offer_snapshot` column (capacity is not row-local) | Surfaced. §3.2.2 honors the requirement on the `listing_score` row instead. Same editorial pass |
| **Spec §9 names two homes for the score.** `docs/specs/hw-radar-master-spec.md:441` — the `offer_snapshot` row is "time-series price/stock/FX/score"; `:451` — the downstream scoring group is "(`cohort_baseline`, `seller_rating_observation`, `listing_score`)", and DR-004 is titled "`listing_score` / explanation payload" | **Surfaced, not resolved here.** S2-1 chooses `listing_score` on the DR-004 + §9-downstream-groups reading and states why the snapshot column cannot carry a rescore. Same editorial pass; that edit is outside this document's scope |
| **§9:447 lists `dollars_per_tb` among "stored generated columns for row-local economics", and C.4's input table sources `$/TB` from "`offer_snapshot` generated columns"** — but capacity lives on `drive_spec`, so `$/TB` is not row-local and cannot be a generated column there. No such column exists today | Surfaced. §2.1 computes `$/TB` in the scoring library and persists it on `listing_score`; the only generated column MS-2 adds is `usd_total_landed_price`, which *is* row-local. Same editorial pass |
| **`OfferSnapshot.usd_item_price` multiplies `item_price`, not `total_landed_price`**, so the existing USD column cannot serve FR-004's "fold known domestic shipping (+ tax where known) into `$/TB`" | Surfaced; fixed by the §2.1 `usd_total_landed_price` addition rather than by changing the existing column, which other read paths may rely on |
| **The C.4 "other" seller policy prior is 0.50 and the veto is `seller trust < 0.50`** — the default sits exactly on the cap boundary | Not a contradiction, a trap: a `<=` implementation would cap every unrated first-party merchant listing at 60. S2-12 pins the strict inequality and MS-2b carries the boundary test |
| **Rounding-mode divergence from the ratification prototype** (§3.2.1): the research prototype uses banker's rounding, MS-2 pins half-up | Deliberate and pinned by a fixture at the exact `.5` boundary. Reproducibility requires a stated mode; the interpreter default is not one |
| Below-80% model-grain coverage at MS-2 close | Expected, not fatal (S2-10). §3.3.3's provenance ladder and S2-7's neutral price subscore keep every listing in `P` scored; MS-2e reports the shortfall to the backfill queue. C.3.5: shortfall signals catalog/rule gaps, never a reason to loosen precision |
| ADR 0019 is still `proposed` (`adr-0019…md:46`) when MS-2 starts | MS-2 does not wait (S2-10). If ratification later forces a `matcher_version` bump and a re-resolution sweep, the affected listings rescore through §3.4.1's L3/B5 triggers — no MS-2 rework |
| Cohort fragmentation from free-text `interface` / `form_factor` | §2.1 canonicalizes in the pure library rather than migrating the columns, and MS-2c **measures** real fragmentation. If measured fragmentation is material, a follow-up enum migration mirrors the `market_tier` change; recorded as a plan-time decision, not pre-committed |
| `CAPACITY_BUCKETS_TB` is generated from the seeded catalog (§3.3.1), so a catalog gap produces ad-hoc buckets | By design: an ad-hoc bucket is flagged and visible rather than silently merged. If ad-hoc buckets are common at MS-2c, the catalog seed is the defect, not the bucketing |
| The `verification_event` warranty lookup is unscheduled (C.4), so fitness `W` runs on the unverified-tier fallback for all of MS-2 | Accepted and marked provisional in every payload. Scheduling the lookup is a spec-level change (§7/§19), not an MS-2 decision |
| `preorder` / `unknown` availability values are not fixed by ADR 0011 | Constants under S2-6 (versioned code + rescore on change), with the ADR-fixed endpoints untouched. Owner-tunable by a one-line change and a version bump |
| A refresh that exceeds its 60 s interval | `coalesce=True` + `max_instances=1` make this degrade to a longer effective interval, not to overlap. MS-2d measures and records the real runtime; if it approaches the interval, the interval — not the design — is the tunable |
| `docs/open-questions.md` currently lists **none open** (verified 2026-09-06; the next question opened there takes **OQ22**). This document opens none | Every §1 item is a *proposed decision* inside MS-2's own boundary, not a spec-level open question. The spec-hygiene rows above are editorial corrections, not questions. If the owner rejects a decision on grounds that change spec text, that becomes OQ22 through the normal process — this document does not pre-empt it |

## 7. Execution process

Each sub-milestone: a plan document under `docs/superpowers/plans/2026-09-…-ms2<x>-….md` → execution with the full verification gate → a `dev→main` PR (merge commit, CI + dependency-review green). Deviations from this design or the spec go to the spec Deviations Log / OQ process, per Appendix B.

**Before the MS-2a plan is cut**, the owner disposes of every row in §1.2 per §1.1. A rejected decision changes that sub-milestone's plan; it does not change this decomposition unless it moves work across a sub-milestone boundary, in which case this document is revised first.
