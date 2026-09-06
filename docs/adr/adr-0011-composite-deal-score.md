---
schema_version: '1.1'
id: 'adr-0011-hw-radar-composite-deal-score'
title: 'ADR 0011: Composite deal score — weighted geometric mean with veto caps'
description: 'Score each listing 0–100 as a weighted geometric mean of four normalized subscores (cohort cheapness-percentile price, Bayesian+Wilson seller trust, fitness rubric, availability) at weights 0.50/0.25/0.15/0.10, gated by three non-compensatory veto caps, with a warm-up full-confidence target of n_eff ≥ 30 and a stored per-subscore explanation payload.'
doc_type: 'adr'
status: 'active'
created: '2026-07-04'
updated: '2026-09-06'
reviewed: null
owner: ''
consumer: 'mix'
tags:
  - 'adr'
  - 'scoring'
  - 'deal-score'
  - 'geometric-mean'
  - 'explainability'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/specs/hw-radar-master-spec.md'
  - 'docs/resolved-questions.md'
  - 'docs/research/principled-deal-score-for-hard-drive-listings.md'
  - 'docs/research/drive-deal-scoring-model-test-results.md'
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

# ADR 0011: Composite deal score — weighted geometric mean with veto caps

MADR status: **accepted**.

## Context and Problem Statement

The spec's `## Scoring System` section was **empty**: the tool's whole purpose is to rank listings 0–100 for a value-focused enterprise/recert buyer, but the aggregation math, the factors, their weights, and the disqualifiers were never specified. The score must be (a) **self-adjusting** as street prices move (2026 is an abnormal, supply-constrained market — hard-coded "good below $X/TB" thresholds age badly), (b) **explainable** listing-by-listing (a glass box the owner can inspect and override), and (c) **hard to game** by a single strong dimension.

Research report [`principled-deal-score-for-hard-drive-listings`](../research/principled-deal-score-for-hard-drive-listings.md) delivered a concrete design; per the owner's directive, it was **validated against mock data before ratification** — see [`drive-deal-scoring-model-test-results`](../research/drive-deal-scoring-model-test-results.md) (resolved-questions.md OQ11).

## Considered Options

- **Option 1 — Weighted geometric mean over four normalized subscores, with a small number of non-compensatory veto caps.** (chosen)
- **Option 2 — Weighted arithmetic sum.** Fully compensatory: a huge price win cancels a terrible seller or wrong drive class.
- **Option 3 — TOPSIS** (distance from ideal). Result depends on the current candidate matrix, so a score means different things scan-to-scan.

## Decision Outcome

Chosen option: **Option 1**, validated by the mock-data test (all three vetoes bound; ranking intuitive; 6/8 archetypes hit their a-priori band).

**Four subscores → weighted geometric mean.** Each subscore is normalized to `[0,1]`; the base score is the weighted product `Π_k max(s_k, 0.02)^{w_k}` at weights **price 0.50 · fitness 0.25 · seller 0.15 · availability 0.10**. The geometric mean is chosen over an arithmetic sum precisely because it is **multiplicative**: a weak dimension bites harder and cannot be fully washed out by one strong dimension, which matches how a careful buyer weighs a risky storage listing.

- **Price** — a **cohort-relative weighted price percentile** on `ln($/TB)`, not an absolute threshold: `q` is the ascending price rank (low `q` = cheap), so `1−q` is the cheapness term the formula applies. Cohort key = capacity · tier · interface/form · condition; 90-day rolling window with **30-day half-life** decay. Warm-up shrinkage `s_price = λ·(1−q) + (1−λ)·0.5` pulls a thin cohort toward neutral.
- **Seller trust** — cross-marketplace positive-equivalent rate with **Beta-Binomial shrinkage** (prior μ₀ = 0.95, κ = 20) plus a **Wilson lower bound** (z = 1.2816): `s_seller = 0.6·p_post + 0.4·LB`. No ratings → conservative policy prior (0.60 major marketplace, 0.50 otherwise), treated as an explicit missing-data state.
- **Fitness** — an explicit rubric `s_fit = 0.5·T + 0.3·W + 0.2·C` over suitability (enterprise/CMR … consumer/SMR), verified warranty tier, and condition bucket.
- **Availability** — a bounded rubric (in-stock 1.0 → out-of-stock 0.0).

**Three non-compensatory veto caps** (cap the max score regardless of price): **device-managed SMR for an enterprise/NAS buyer → 35**; **used/seller-refurb with no returns → 60**; **seller trust < 0.50 → 60**. Everything else stays a soft, continuous penalty.

**Warm-up target changed by the test: `n_eff ≥ 30`, not 50.** The source report used `λ = min(1, n_eff/50)` illustratively; the mock-data test showed that even a 64-observation cohort reaches only `n_eff ≈ 49` under the 30-day half-life, so narrow real cohorts would sit **perpetually provisional** at a `/50` target. The ratified value is **`λ = min(1, n_eff/30)`**, paired with the documented cohort-relaxation fallback (relax condition → adjacent capacity → parent tier) when a cohort is too small. `n_eff = (Σw)²/Σ(w²)`.

**Explainability is a stored, first-class output.** Every listing persists a per-subscore payload (percentile + margin-in-IQR, seller evidence, fitness rubric pieces, and any cap reason) — the exact shape rendered by the OQ6 listing-detail "why it matched" view.

Options 2 and 3 were rejected: the arithmetic sum is too compensatory for risky hardware, and TOPSIS is a batch-ranking method whose output is not a portable, stable per-listing score.

### Consequences

- **Good** — price is a **relative** signal, so the score tracks a moving market automatically; the vetoes keep cheap-but-unsuitable listings from floating to the top (the test's SMR/used-no-return/bad-seller archetypes all pinned at their caps).
- **Good** — **glass-box** and controllable: the owner can inspect any score and tighten or relax a cap to taste.
- **Neutral (accepted, per the test's calibration findings):** the middle band is mildly generous (a median-priced, backordered listing scored 69) and the `s_price` floor (0.02) flattens the expensive tail (a top-decile-priced listing scored 14). Both are accepted as-is for v1 — the model's *shape* is right and its constants are cheap to re-fit once real observations accrue.
- **Bad (accepted)** — the seller-trust prior is strong (trust is "sticky high"), so the `< 0.50` veto only fires on sellers with substantial negative evidence. This is intended: the veto is a genuine-disqualifier gate, not a quality knob.

### Confirmation

The mock-data test ([`drive-deal-scoring-model-test-results`](../research/drive-deal-scoring-model-test-results.md)) is the pre-ratification confirmation. Implementation confirmation is milestone **MS-2**: every listing carries a **reproducible** 0–100 score with a **per-factor breakdown**; thin-cohort listings (`n_eff < 30`) visibly shrink toward neutral and are marked **provisional**; the cohort-relaxation fallback fires when a cohort is too small.

## More Information

- **Fills** the previously-empty spec `## Scoring System`; maps onto milestone **MS-2**.
- **Subscore internals** settled elsewhere: price (shipping/tax folded into `$/TB`, gap #11; cold-start warm-up, gap #12), fitness (suitability taxonomy, recert risk), seller (cross-marketplace Bayesian+Wilson).
- **Findings:** resolved-questions.md **OQ11**; research [`principled-deal-score`](../research/principled-deal-score-for-hard-drive-listings.md) + [test results](../research/drive-deal-scoring-model-test-results.md).
- **OQ16 (owner-resolved 2026-07-04):** the cohort key stays this ADR's four-part key for SSDs too — the DWPD endurance class folds into the fitness rubric's suitability (`T`) component instead of partitioning the cohort ([resolved-questions.md OQ16](../resolved-questions.md#oq16--ssd-cohort-key-endurance-dimension-dwpd)). No amendment to the Decision Outcome.
