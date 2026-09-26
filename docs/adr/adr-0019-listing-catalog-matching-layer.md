---
schema_version: '1.1'
id: 'adr-0019-hw-radar-listing-catalog-matching-layer'
title: 'ADR 0019: Listing→catalog matching layer — grain-elastic resolution against the MPN matrix'
description: 'Fix the matching layer that joins noisy marketplace listings to the ADR-0018 manufacturer spec catalog: a single shared normalizer, a four-layer rules-only extraction stack with per-rule provenance tiers, a five-rung conservative match ladder (auto-accept only exact/deterministic rungs, hard-attribute contradictions force review), grain-elastic attachment with agreement-set drive_spec inheritance, an append-only listing_resolution edge table as the resolution state, grain-addressed aliases (OEM part numbers resolve to family/model only — the relationship is many-to-many), a view-based unknown_model backfill queue with an occurrence-triggered discovery loop, and a two-part precision contract (labeled-corpus validation gates ratification; production runs zero-tolerance on confirmed false merges).'
doc_type: 'adr'
status: 'active'
created: '2026-07-04'
updated: '2026-07-04'
reviewed: null
owner: ''
consumer: 'mix'
tags:
  - 'adr'
  - 'entity-resolution'
  - 'matching'
  - 'mpn'
  - 'data-model'
  - 'normalization'
aliases: []
related:
  - 'docs/adr/README.md'
  - 'docs/adr/adr-0010-canonical-data-model.md'
  - 'docs/adr/adr-0011-composite-deal-score.md'
  - 'docs/adr/adr-0016-search-api-self-governance.md'
  - 'docs/adr/adr-0018-manufacturer-spec-catalog.md'
  - 'docs/specs/hw-radar-master-spec.md'
  - 'docs/research/entity-resolution-for-cross-marketplace-hard-drive-and-ssd-price-tracking.md'
  - 'docs/research/programmatic-identity-and-warranty-verification-for-used-enterprise-hdd-listings.md'
  - 'docs/research/2026-07-04-ssd-vendor-part-number-decoding-and-spec-catalog-bootstrap-datasets.md'
  - 'docs/research/2026-07-04-oem-part-number-cross-referencing-for-server-pull-drives.md'
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

# ADR 0019: Listing→catalog matching layer — grain-elastic resolution against the MPN matrix

MADR status: **proposed** — flips to _accepted_ when the pre-ratification validation corpus (rule 8) passes; the design itself is owner-approved (2026-07-04).

## Context and Problem Statement

[ADR 0018](adr-0018-manufacturer-spec-catalog.md) seeded an authoritative MPN matrix and named its own residual risk: "the catalog without a reliable matcher is inert reference data." This ADR is that queued follow-up. The matching layer must join noisy recert listings — buried/partial/absent part numbers, refurbisher house SKUs, OEM-pull labels — onto the ADR-0010 identity ladder, against a fan-out where one enterprise family spans dozens of MPNs (SATA/SAS, 512e/4Kn, SED/FIPS/SIE, packaging/firmware suffixes).

Four researched constraints shape the solution space:

1. **False merges poison the price-history moat asymmetrically.** A 16 TB observation appended under an 18 TB model corrupts cohort baselines and scores silently; a missed match merely sits in a review queue. The entity-resolution research is explicit: disagreement on capacity/interface/form factor is a hard non-match, and matching must be conservative before it is complete.
2. **"Family known, exact MPN unknown" is the common recert case, not an edge case** — and manufacturer part numbers attach at _different grains_: base codes (Samsung `MZ-77E1T0`) are model-grain, orderable/region/security suffixes (`MZ-77E1T0B/AM`; Kioxia's documented base→SIE/SED/FIPS single-character swap) are variant-grain. The two-tier base-code vs orderable-SKU structure recurs across every vendor surveyed (SSD part-number research, 2026-07-04).
3. **Part-number grammars are only partially decodable, with uneven authority.** Only Kingston publishes a complete current official legend; Seagate/WD suffix semantics and SK hynix/Nytro grammars are community-inferred or per-datasheet only. A decoder can validate a token and read family/capacity/generation off the string; it must never assert variant-level attributes from the code alone.
4. **OEM part numbers (Dell/EMC, HPE, Lenovo/IBM, NetApp) are many-to-many with manufacturer MPNs** — one NetApp `X477A-R6` was fulfilled by both an HGST and a WD model over its production run — and **no public bulk OEM→MPN source exists** (OEM cross-reference research, 2026-07-04). OEM aliases can therefore never resolve a variant, and their mapping table cannot be pre-seeded.

## Considered Options

- **Option 1 — In-place resolution columns:** nullable identity FKs + status/confidence directly on `listing`, overwritten on re-resolution.
- **Option 2 — Resolution-edge architecture with a conservative grain-elastic ladder** (chosen): an append-only `listing_resolution` edge table as the resolution state, a rules-only extraction stack, and a five-rung ladder that auto-accepts only exact/deterministic matches.
- **Option 3 — Off-the-shelf probabilistic ER framework** (Splink/dedupe) for the fuzzy tier.
- **Option 4 — LLM/NER-first title extraction** from day one.

## Decision Outcome

Chosen option: **Option 2**, with the Option 4 machinery designed-in as a deferred escalation hook (the ADR-0014 pattern: define the interface, defer the heavy runtime).

### The load-bearing rules (this is the decision)

1. **One normalizer, both sides.** Catalog aliases at reference ingest and listing candidates at observation ingest pass through the **identical** normalization code path, and a CI test asserts that parity. Two drifting normalizers are the classic silent killer of alias joins.
2. **Extraction is rules-only in v1, four deterministic layers** — text canonicalization; controlled-vocabulary attribute extraction (capacity→bytes, interface, form factor, sector format, recording tech, security, condition incl. for-parts, packaging, **quantity/lot**, brand normalization incl. the WD↔SanDisk rebrand); MPN-candidate extraction from title/description/structured fields (refurbisher house SKUs recognized as **source-local** aliases, never canonical); and per-vendor **MPN grammar decoders**. Every extracted attribute carries per-attribute confidence and provenance. An NER/LLM fallback for low-confidence residues is an interface stub, deferred until the rules' measured residual justifies it.
3. **The decoder contract is deliberately weak:** validate that a token is a plausible MPN for its vendor and derive **family/capacity/generation only**; variant-level semantics (interface variant, sector format, SED/FIPS, endurance) come from the catalog/datasheets, never from the code string. Each grammar rule carries a provenance tier — `vendor_official` / `corroborated_community` / `inferred` — that flows into match confidence, so a match leaning on an inferred rule (Samsung suffixes, Nytro `XA/XS/XP`) scores below one on Kingston's official legend.
4. **A five-rung conservative match ladder; only rungs 0–2 auto-accept.** (0) re-observation via source-local alias; (1) exact `brand + normalized full MPN` against grain-tagged `product_alias`; (2) valid grammar decode with no catalog hit → attach at family grain, flag provisional, enqueue backfill; (3) attribute-blocked candidate scoring (`brand + capacity` blocking, `pg_trgm` + attribute agreement) → **review queue with ranked suggestions, never auto-accepted**; (4) manual back-office decision, which **writes a revocable learned alias** so the same string resolves at rung 1 next time. A **hard-attribute contradiction (capacity, interface, form factor, sector/security when known) forces non-match or review at every rung** — including rung 0, which re-checks on every re-observation to survive relist/edit abuse — and revoking a learned alias cascades a re-run of the listings it resolved.
5. **Grain-elastic attachment with agreement-set inheritance.** A listing attaches at the most specific grain its evidence supports (family / model / variant); ADR-0010's `listing → product_variant` edge is the **goal state, not an ingest invariant**. A coarse-grain listing inherits only the `drive_spec` fields on which **all** candidate MPNs under the attached grain agree; disagreeing fields stay `unknown` (never guessed — the suitability research's rule). Scoring consumes what is known and degrades gracefully.
6. **Resolution state is an append-only `listing_resolution` edge** — `(listing_id, grain, target_id, method, confidence, matcher_version, evidence, resolved_at, superseded_by)` — with denormalized current-resolution FKs on `listing` (most-specific-wins, lower grains NULL). Re-resolution (catalog refresh, matcher-version bump, alias revocation) appends; it never overwrites. This is the DR-004 explanation-payload posture applied to identity: store the why, not just the outcome.
7. **Aliases are grain-addressed and provenance-classed.** `product_alias` rows address `(grain, target_id)`: base codes → model, orderable/security/packaging SKUs → variant, **OEM part numbers → family or model only** (the N:N research verdict, encoded structurally). Alias rows carry a `source_kind` — `catalog_authoritative` / `listing_derived` / `manual` — and the OEM→MPN table is populated **lazily**: when a listing self-labels both an OEM-shaped token and a manufacturer MPN, emit a `listing_derived` alias whose confidence rises with independent corroborating sellers; a small human-paced manual queue (HPE PartSurfer lookup, targeted search) covers high-value gaps. No bulk import exists to be had; no scheduled automation against PartSurfer.
8. **A two-part precision contract, ratification-gated.** Before this ADR flips to _accepted_: a hand-labeled corpus of ~150–200 real titles from the primary recert sources must show **≥ 99.5% precision on auto-accepted matches** (the ADR-0011 validate-before-ratify precedent). In production the target operationalizes as **zero unresolved confirmed false merges** — each one found triggers a veto rule, a `matcher_version` bump, and a re-run of affected listings — because audit sampling cannot statistically confirm 99.5% at launch volume. Coverage (≥ 80% of primary-recert-source listings at model grain or better by end of MS-2) is an **expectation that signals catalog/rule gaps, never a gate to loosen precision**.
9. **The resolver never gates ingestion.** A resolver failure persists the listing at `grain = none` with the error in the evidence payload; the `unknown_model` backfill queue is a **view** over listings below model grain (plus the rung-2 set), worked by catalog-refresh re-runs, an occurrence-threshold-triggered targeted reference fetch (the ADR-0018 discovery loop made concrete), and the deal-signal-ordered manual queue. All thresholds (trgm similarity, occurrence trigger, auto-accept boundary) are settings rows (ADR-0016 pattern), not constants.
10. **Bootstrap posture: first-party only; no vendored external datasets.** smartmontools `drivedb.h` is GPL-2.0-or-later (vendoring it would put GPL content in this MIT repo — a file the PyPI-scoped license gate would not even catch; if SMART interpretation is ever needed, subprocess a system `smartctl`); Icecat's content license is a channel-partner agreement — at most a **runtime enrichment trial**, never committed data; TechPowerUp/PCPartPicker have no usable API. ADR-0018's Option-3 bootstrap allowance therefore collapses in practice to first-party datasheets + the grammar decoders + lazily learned aliases.

**Rejected — Option 1 (in-place columns):** needs a bolt-on audit log anyway to honor C.3's store-provenance step, destroys the why-did-this-match trail on every catalog-refresh re-run, and makes grain upgrades (family → model → variant over time) invisible.

**Rejected — Option 3 (probabilistic ER framework):** Splink-class engines solve pairwise linkage _without_ a reference set — the exact problem ADR-0018 eliminated. What remains is dictionary lookup plus a small fuzzy residual that `pg_trgm` + hard-attribute vetoes handle in SQL; the engine's m/u calibration also needs training data that will not exist until MS-1–MS-2, clashing with the conservative posture.

**Rejected — Option 4 (LLM/NER-first):** adds an API dependency, cost, and prompt-stability surface before any data shows the rules' residual is large enough to justify it; the narrow domain plus a seeded catalog is exactly where deterministic rules are strongest. Deferred behind a designed interface, like Playwright in ADR-0014.

### Consequences

- **Good** — the catalog's value is realized: matched listings inherit authoritative `drive_spec`; the alias matrix turns resolution into lookup for the exact-MPN majority.
- **Good** — grain-elastic attach keeps the tool's core inventory (family-only recert listings) visible and scoreable without fabricating variant-level facts; the agreement set safely surfaces the attributes vetoes need most (CMR/SMR).
- **Good** — the edge table + `matcher_version` make every rule change a diffable experiment and every match auditable; learned aliases make the review queue self-shrinking.
- **Good** — the OEM N:N verdict is encoded structurally (grain-capped aliases), so a Dell/NetApp part number can never silently assert a wrong variant.
- **Bad (accepted)** — a listing can carry a stale coarse resolution until the next re-resolution trigger; grain upgrades change `drive_spec` → score, and the alert-side dedup semantics of that cascade are **deferred to the carried MS-3 `watch_match_state` ADR slot** (named interaction, not silently dropped).
- **Bad (accepted)** — rung 3 never auto-accepts, so fuzzy-only listings queue for a human; the bet is that catalog seeding + learned aliases keep that queue small, and the labeled corpus measures the bet before ratification.
- **Bad (accepted)** — per-vendor grammar decoders are hand-maintained against datasheets (Kingston-style official legends exist for almost no one), and OEM alias coverage accrues only as dual-labeled listings are observed. The dual-labeling rate itself is spot-checked, not yet validated at scale against the named target merchants (flagged in the OEM research) — the MS-1 connector work should confirm it.

### Confirmation

Pre-ratification: the labeled-corpus run meets rule 8 and the result is recorded here. Implementation confirmation (MS-1–MS-2, alongside the entity-resolver hardening): the normalizer-parity CI test exists and passes; a recert listing with an exact MPN resolves at rung 1 and inherits full `drive_spec`; a family-only listing attaches at family/model grain with only agreement-set fields populated and appears in the backfill view; a decoded-but-unknown MPN crossing the occurrence threshold enqueues a targeted catalog fetch; a manual correction writes a learned alias that resolves the next occurrence at rung 1, and revoking it re-runs the listings it resolved; a capacity-contradicting exact-alias hit lands in review, not in the price history.

## More Information

- **Extends** [ADR 0010](adr-0010-canonical-data-model.md) (grain ladder; adds the edge table, grain-addressed aliases, and the goal-state reading of the listing→variant edge) and **completes** [ADR 0018](adr-0018-manufacturer-spec-catalog.md)'s queued follow-up. **Relates** [ADR 0011](adr-0011-composite-deal-score.md) (explanation-payload posture; scoring consumes agreement-set fields) and [ADR 0016](adr-0016-search-api-self-governance.md) (thresholds as settings rows). Operational detail — vocabularies, ladder mechanics, queue mechanics, testing invariants — lives in the master spec, Appendix C.3.
- **Primary research:** [`entity-resolution-…`](../research/entity-resolution-for-cross-marketplace-hard-drive-and-ssd-price-tracking.md) (conservative asymmetric matching, blocking, parsed schema), [`programmatic-identity-and-warranty-verification-…`](../research/programmatic-identity-and-warranty-verification-for-used-enterprise-hdd-listings.md) (HDD decoder references and their limits), [`2026-07-04-ssd-vendor-part-number-decoding-…`](../research/2026-07-04-ssd-vendor-part-number-decoding-and-spec-catalog-bootstrap-datasets.md) (SSD grammars, provenance tiers, bootstrap-dataset license verdicts), [`2026-07-04-oem-part-number-cross-referencing-…`](../research/2026-07-04-oem-part-number-cross-referencing-for-server-pull-drives.md) (the N:N verdict and lazy-learning strategy).

## Amendment — 2026-09-26: Family-grain resolution, ratification-catalog source, source-diversity gate, and matcher-trap findings (owner clarification)

Owner decisions of 2026-09-26, resolving [OQ24](../resolved-questions.md#oq24--production-actor-backed-merchant-source-admission), [OQ31](../resolved-questions.md#oq31--existing-local-connectors-whose-terms-prohibit-automated-access), and [OQ32](../resolved-questions.md#oq32--ms-1e-ratification-gate-when-two-named-sources-are-unusable). This amendment clarifies rules 4 and 8 above and the ratification design they govern; the original text stays as the record of what was first decided. **MADR status remains `proposed`** — this amendment does not itself ratify the ADR; ratification still requires the rule-8 gate to pass per the amended criteria below.

**1. Family-grain rule (clarifies rule 4's rung-2 family attach).** "A correct family-grain resolution is a correct matcher result even if the exact model is not present in the reference catalog, provided the evidence establishes that family and the matcher does not claim a more specific identity than the evidence supports." This does **not** mean: an unknown model may resolve to an arbitrary family; fuzzy title similarity alone establishes a family; a near-model string may resolve to the seeded model it merely resembles; or a family match may skip rule 4's hard-attribute contradiction checks. The exact model stays unknown unless authoritative evidence establishes it. The rule is **drive-only**, inside ADR-0019's grain-elastic ladder; GPU/RAM/CPU auto-accept, once those categories have their own matching/source gates, still begins from authoritative exact model/part-number identity, not a family-grain equivalent.

**2. Ratification-catalog source (clarifies rule 8's precision contract).** The catalog the labeled corpus is scored against is "the canonical first-party drive reference dataset imported through the same production refdata path used by Hardware Radar" ([ADR 0018](adr-0018-manufacturer-spec-catalog.md)'s import path). Synthetic fixtures (`tests/fixtures/matching_corpus/synthetic.jsonl`) are for unit tests only and never substitute for that catalog at ratification.

**3. Source-diversity gate (resolves OQ32; amends rule 8's corpus composition, not its thresholds).** "An ADR-0019 ratification corpus must declare at least three independent, currently admissible validation sources in corpus metadata. Every declared source must produce at least one correct owner-ratified family-or-better auto-accept in the same composite gate run." Current intended candidates: eBay, WD recertified, and goHardDrive, each subject to its own source-policy eligibility. The existing quality thresholds are unchanged — ≥ 100 auto-accepts, ≥ 99.5% precision, audit-gate PASS, rung-0 regression PASS, one full-run composite PASS — "a source-diversity correction, not a quality relaxation." This replaces the five-source per-source floor recorded in the MS-1e ratification design spec (`docs/superpowers/specs/2026-07-06-ms1e-validation-corpus-ratification-design.md`, §5–§6); that spec carries a mirrored dated note rather than a rewrite of its historical five-source text.

**4. Category isolation (clarifies scope; part of the OQ31 consequences).** The MS-1e/ADR-0019 ratification gate governs **drive** matching and drive source × category enablement only. It does not block CPU/GPU/RAM enablement once those categories pass their own matching and source-admission gates — drive ratification is not a prerequisite for the other three categories.

**5. Confirmed matcher traps (owner audit; informs rule 3's decoder contract).** Comparison-context MPNs in a title — e.g. "comparable to ST16000NM002G" — are not identity evidence for the listed model; a decoder/rule must not treat a comparison phrase as an MPN match. The `ST…NM…` prefix does not universally mean Exos: `ST1000NM0001` is a Constellation-family part, not Exos. Fixes for both traps land with a `matcher_version` bump; that work is in progress, and this amendment does not itself state a version number.

**6. Anti-anchoring note on label fingerprints (informs the MS-1e ratification procedure, design spec §6).** The recorded label fingerprints are self-recorded file-integrity evidence — they show the labeled corpus file did not change after hashing — not independent proof of who authored the labels or when.

**Consequence — OQ31, source × category admission matrix.** The global SA-004 enable order (`docs/handoff/deployed.md`) is replaced by a source × category admission matrix: each (source, category) combination is admitted independently, and unrelated sources or categories are never a prerequisite for one another. SA-004's checks run per combination, immediately before that combination's enable bit changes. ServerPartDeals and Seagate-recertified are retired from the currently enableable set — their reviewed current Terms conflict with the automated collection those connectors perform; not enabled privately, and not routed through Apify, since Apify does not change source-policy eligibility. Status: retired / permission-required, not production-enableable under current Terms; code and history are preserved. Re-admission needs materially changed Terms or written merchant permission, confirmed by a fresh source-admission review — it is never automatic.

**Consequence — OQ24, no production merchant source now.** No production Actor-backed merchant source is introduced at this milestone; F5b is deferred until a future source-admission review identifies an actually eligible source. No `permission-required` merchant is treated as approved by that status alone, and no merchant solicitation, Actor, or paid unblocker/proxy path is pursued to reach `eligible`. Reopen only on new source-admission evidence. OQ24/F5b does not block F6 or MS-2.
