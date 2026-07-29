# MS-1e — Validation corpus + ADR-0019 ratification: Design

> Brainstorm output (2026-07-06, owner-approved). Instantiates master-spec §19 "MS-1
> close" and the `docs/superpowers/specs/2026-07-05-ms1-ingestion-design.md` §MS-1e
> sub-milestone: the labeled-corpus evaluation that ratifies ADR-0019. Introduces no
> new matching architecture — the resolver, ladder, normalizer, and catalog are all
> fixed by ADR-0019 / master-spec Appendix C.3 and already implemented (MS-1a–d). This
> document records the *evaluation harness + harvest tooling* design and the owner scope
> decision taken 2026-07-06 (build the deterministic harness + harvest tooling now; the
> live harvest, label audit, and ratification run are a deferred owner-in-the-loop step).

**Goal:** a repeatable, deterministic **evaluation harness** that runs a versioned,
hand-labeled corpus of real listings through the **production** rung-0–2 resolver
(`CatalogResolver().resolve_listing`) and computes a tri-state ratification verdict —
`PASS` (≥ 99.5% auto-accept precision on a ≥ 100-decision denominator), `INSUFFICIENT_CORPUS`
(< 100 auto-accepted decisions), or `FAIL` (< 99.5%) — plus the reported per-source
model-grain coverage, the per-source ≥ 1-family-grain resolution floor (an MS-1 gate),
and the OEM dual-label spot-check rate. Ships with the harness proven against a synthetic
fixture in CI; the real-corpus ratification test is skip-guarded until the live harvest lands.

## 1. Owner scope decisions (2026-07-06)

These settle what the MS-1e sub-milestone left to plan time:

| # | Decision | Consequence |
| --- | --- | --- |
| E-1 | **Build the deterministic evaluation harness + harvest tooling in this milestone; defer the live harvest, label drafts, owner audit, and ratification run to a scheduled owner-in-the-loop follow-up.** | MS-1e merges with the harness proven on a synthetic fixture and the real-corpus test skip-guarded. Ratification (ADR flip) happens later, out of this PR, when a real corpus has been harvested and audited. No fake green. |
| E-2 | **Eval architecture = Approach A (full production path).** Each corpus entry is rebuilt into a `ParsedListing`, normalized + `upsert_listing`-ed, snapshotted (`OfferSnapshot.attrs_json` = the entry's `attrs`) into a rolled-back test-DB transaction, then run through the real `CatalogResolver().resolve_listing`; the harness reads back the denorm grain + target FK + resolution-edge rung. | The gate certifies exactly the code path that writes to price history, on the exact input surface the resolver consumes (title + `attrs_json["mpn"]` hook). No proxy/second-code-path drift (the ADR-0019 rule 1 hazard). |
| E-2b | **The precision corpus measures rungs 1–2; rung 0 is covered by a required behavioral regression suite, not the corpus denominator (SA-001).** A corpus of distinct first-observed listings structurally cannot exercise rung 0 (re-observation via source-local alias) — on a first observation `prior = NONE`, so the resolver evaluates rungs 1–2. Rung 0's contract is *veto-on-re-observation* (relist/edit abuse), a behavior, not a precision sample. | Ratification exit therefore requires **both**: ≥ 99.5% on the rung-1/2 corpus **and** the rung-0 regression suite green (unchanged re-observation inherits with no edge spam; a re-observation whose hard attribute now contradicts is demoted to REVIEW, not silently re-inherited). The spec never claims the corpus denominator alone is full ADR-0019 coverage. |
| E-3 | **`expected_target` is a grain-shaped natural key: a controlled `manufacturer_key` plus loader-normalized display values, never a catalog PK (SA-003).** Family grain: `manufacturer_key` (stored `Manufacturer.normalized_name`, exact) + `family`. Model grain: adds `model_number`. Variant grain: adds the `ProductVariant` sellable-identity enums `condition` / `packaging` / `recert_channel` / `warranty_channel`. `none` grain: all null. | The committed corpus outlives any DB seed; the loader normalizes `family`/`model_number` display values through the production `canonicalize_title` / `normalize_alias_text` (one normalizer, both sides) and matches `manufacturer_key` exactly against the *predicted* target's stored fields. A "variant string" cannot represent `ProductVariant` identity, which is `(product_model, condition, packaging, recert_channel, warranty_channel)`. The fixture doubles as a durable post-`matcher_version`-bump regression asset. |
| E-4 | **The real labeled corpus is committed to this public repo** — `title` + the connector's *actual* `ParsedListing.attrs` keys + the persisted scalar fields (price, currency, `condition_label`, source_listing_key, url) only; **never** full raw payloads. | Titles + these fields are public marketplace data (repo-safe per AGENTS.md Public-Repo Rule); full payloads (seller handles/extra URLs) stay transient in the harvest staging file, uncommitted. |
| E-5 | **Labeling per ingestion-design S-5**: Claude drafts every label; owner audits a random ~20% sample + **every** entry where the label disagrees with the matcher prediction. | Bounded owner time while keeping the ratification gate meaningfully independent. Audit status is tracked per entry. |

## 2. Module layout

New code under `src/hw_radar/`, plus a Django management command and test fixtures:

```
hw_radar/
├── matching/
│   └── eval/                        # MS-1e — corpus evaluation (pure orchestration over the resolver)
│       ├── __init__.py
│       ├── corpus.py                # Pydantic: CorpusEntry, CorpusMeta, GroundTruthLabel, TargetKey; load + validate JSONL
│       ├── evaluate.py              # Approach A: per entry → upsert Listing → resolve_listing → Prediction; aggregate → EvalReport
│       └── report.py                # EvalReport + tri-state Verdict; precision / coverage / per-source floor / OEM-rate math
├── catalog/
│   └── management/commands/
│       └── harvest_corpus.py        # manual harvest: drives each adapter directly, bypasses scheduler + enabled flag
│                                    # (catalog app hosts commands — acquisition is a plain package, not an installed app;
│                                    #  sits beside the existing import_refdata command)
tests/
├── fixtures/matching_corpus/
│   ├── synthetic.jsonl              # ~10 hand-built entries with known outcomes (always-on harness unit tests)
│   ├── synthetic.meta.json
│   ├── corpus.jsonl                 # the real harvested + labeled corpus (added by the deferred ratification step)
│   └── corpus.meta.json
├── unit/test_corpus_eval.py         # harness math on the synthetic fixture (no live data)
├── unit/test_corpus_schema.py       # Pydantic validation invariants
├── unit/test_harvest_corpus.py      # command drives a fake adapter → JSONL; eBay-creds-absent → eBay-only skip; --out guard
├── db/test_rung0_regression.py      # rung-0 half of "rungs 0-2": re-observation inherit + veto-on-change demotion (E-2b)
└── db/test_ratification_corpus.py   # the executable ratification gate; absent→skip, present→assert PASS/INSUFFICIENT
```

The eval module is deliberately thin: it *orchestrates* the existing resolver and owns only
the corpus I/O, the metrics math, and the tri-state verdict. It contains **no matching logic**
— that would be the second-code-path drift E-2 exists to prevent.

## 3. Corpus fixture — schema & versioning

A JSONL corpus plus a sidecar manifest under `tests/fixtures/matching_corpus/`.

**`corpus.jsonl`** — one object per listing:

```json
{
  "id": "spd-0001",
  "source": "serverpartdeals",
  "title": "Seagate Exos X18 ST18000NM000J 18TB SATA Recertified Enterprise HDD",
  "listing": {
    "source_listing_key": "…connector's stable per-listing key…",
    "url": "https://…",
    "price": "199.00",
    "currency": "USD",
    "condition_label": "Recertified",
    "attrs": { "sku": "…", "variant_title": "…" }
  },
  "label": {
    "expected_grain": "model",
    "expected_target": {
      "manufacturer_key": "seagate",
      "family": "Exos X18",
      "model_number": "ST18000NM000J",
      "variant": null
    },
    "oem_dual_label": false,
    "audit_status": "claude_draft",
    "notes": ""
  }
}
```

- **`source`** ∈ the five real adapter-registry / `SourceSite.normalized_name` keys: `serverpartdeals`,
  `goharddrive`, `wd-recertified`, `seagate-recertified`, `ebay` (SA-002 — schema validation **rejects**
  any other key; no parallel short-name namespace).
- **`listing`** mirrors the exact `ParsedListing` fields the pipeline persists — `source_listing_key`,
  `url`, `price`, `currency`, **`condition_label`** (the raw marketplace condition text; the field is
  literally `ParsedListing.condition_label`, persisted as `condition_label_raw` — SA-NEW-001), and
  **`attrs`** = the connector's *actual* `ParsedListing.attrs` dict (e.g. ServerPartDeals `sku`/
  `variant_title`, WD `saleable`). The eval reconstructs a real `ParsedListing` from exactly these fields
  (no invented keys) and writes `attrs` verbatim into `OfferSnapshot.attrs_json`, so the resolver's
  `_structured_mpn` hook (`attrs_json["mpn"]`, dormant for today's connectors) and the title-driven
  extraction path both see production-identical input (SA-004). MPN today comes from the **title**.
- **`label.expected_grain`** ∈ `none | family | model | variant`.
- **`label.expected_target`** mixes one controlled key with human-readable display values (E-3):
  - **`manufacturer_key`** is the stored `Manufacturer.normalized_name` **controlled key** — `seagate`,
    `western_digital`, `toshiba`, … — compared **exactly** (no title canonicalization; `Manufacturer`
    keys are seeded from `SeedDocument.manufacturer_key`, not from `canonicalize_title`). `western_digital`
    also covers WD- and SanDisk-branded listings per the C.3.1 Optimus rebrand vocabulary (`vocab.py`
    `_BRANDS` maps `western digital` / `wd` / `sandisk` → `western_digital`); a WD listing labels
    `manufacturer_key: "western_digital"`. Schema validation rejects a `manufacturer_key` absent from the
    seeded manufacturer set.
  - **`family`** and **`model_number`** are display values the loader normalizes (`canonicalize_title` →
    `"exos x18"`; `normalize_alias_text` → `"st18000nm000j"`) before comparing to the predicted target's
    stored fields.
  - the variant grain's **`variant`** object carries `condition`/`packaging`/`recert_channel`/
    `warranty_channel` as `ProductVariant` **enum-choice values** (exact match).

  Grain/key consistency is a validated invariant: `variant` grain requires the `variant` object; `model`
  requires `model_number`; `family` requires `family`; every non-`none` grain requires `manufacturer_key`;
  `none` requires all of family/model/variant null (a listing that *should not* auto-accept — genuinely
  unresolvable or a hard-attribute-contradiction case). Note the deliberate split:
  `listing.condition_label` is raw marketplace text (input side); `expected_target.variant.condition` is
  the normalized `ProductVariant.condition` enum (label side).
- **`label.oem_dual_label`** — the OEM-token-AND-MPN spot-check flag (ADR-0019 rule 7 / consequence);
  measured against ServerPartDeals + eBay listings.
- **`label.audit_status`** ∈ `claude_draft | owner_confirmed | owner_corrected`.

**`corpus.meta.json`** — `corpus_version` (string), `harvested_at` range, per-source counts,
`matcher_version` at labeling time, and an audit rollup (counts by `audit_status`).

**Target-key comparison — one normalizer, both sides (ADR-0019 rule 1; SA-003).** The loader compares a
label to the predicted target by running the **production** normalizers on both: `family` through
`canonicalize_title()` (→ `"exos x18"` — casefold, dash-fold, noise-strip, space-kept) and `model_number`
through `normalize_alias_text()` (→ `"st18000nm000j"` — casefold, strip every non-alphanumeric), then
comparing against the resolver's stored `ProductFamily.normalized_name` / `ProductModel.normalized_model_number`.
Labels therefore hold display values (`"Exos X18"`, `"ST18000NM000J"`); the loader — never a hand-authored
second convention — produces the normalized keys. `manufacturer_key` is the exception: it is already the
stored `Manufacturer.normalized_name` controlled key and compares exactly (no canonicalization — that would
turn `western_digital` into `western digital` and never match). Variant enum fields compare as exact choice
values.

## 4. Harvest command — `manage.py harvest_corpus`

A Django management command that harvests real listings **without touching go-live gates**:
it instantiates each connector adapter directly and calls `await adapter.fetch()` →
`adapter.parse(batch) → list[ParsedListing]`, bypassing the poller scheduler and the
`SourceConfig.enabled` flag entirely (it is a manual tool, not a scheduled job).

- **Flags:** `--source {serverpartdeals,goharddrive,wd-recertified,seagate-recertified,ebay} | --all`
  (the real registry keys, SA-002), `--limit N`, `--out PATH`.
- **`--limit N`** caps **parsed** listings per source (applied after `parse()`, so a multi-item batch is
  truncated deterministically); the command records per-source `harvested` / `skipped_malformed` counts
  in the staging output for harvest-quality visibility.
- **Isolation (NFR-001):** one source failing never halts the others; each source's outcome is
  logged and the sweep continues.
- **eBay creds:** the connector reads `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` (production keyset in
  OpenBao at `secret/api-keys/commerce/ebay`, verified live per ingestion-design S-4; Buy-API access
  is eBay-mutable — re-run the token-mint + one-search smoke at harvest time, never print the token).
  If the env vars are absent the command **skips eBay only** and harvests the other four. Implementation
  adds `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` (names + the OpenBao path, **no values**) to
  `docs/handoff/credentials.md`.
- **Robots:** the httpx-tier sources (ServerPartDeals, WD, Seagate, eBay) go through the
  `acquisition/http.py` robots preflight; **goHardDrive is Scrapy-backed and obeys Scrapy's
  `ROBOTSTXT_OBEY`, not the httpx preflight** (SA-006 non-blocking) — the harvest path reuses each
  connector's existing fetch, so both guards apply exactly as in production.
- **Output — `--out` safety (SA-006):** default staging path is a git-ignored `.harvest/` dir (added to
  `.gitignore`); the command **refuses** to write staging output under a tracked path (e.g.
  `tests/fixtures/matching_corpus/`) unless `--allow-repo-output` is passed — which is reserved for
  writing the *curated, labeled* corpus, never raw staging. Staging entries are *unlabeled*
  (`id`, `source`, `title`, `listing`) with an optional `oem_dual_label` heuristic pre-fill; labeling is
  the separate S-5 step and the command never invents labels.

## 5. Evaluation harness — `src/hw_radar/matching/eval/`

For each corpus entry, the harness (Approach A, E-2) reproduces the production ingest path exactly:

1. **Rebuild → persist.** Reconstruct a `ParsedListing` from the entry's `title` + `listing` fields
   (`source_listing_key`, `url`, `price`, `currency`, `condition_label`, `attrs`), run it through the same
   normalize + `upsert_listing` + `append_snapshot` calls `run_source` uses — so an `OfferSnapshot` with
   `attrs_json = entry.listing.attrs` and an FX-stamped USD price exists before resolution, against the
   seeded catalog, all inside a rolled-back test-DB transaction. The corpus is US-priced (USD) in v1;
   an entry carrying a non-USD currency is FX-stamped via the production FX path so the snapshot invariant
   holds.
2. **Resolve.** Call `CatalogResolver().resolve_listing(listing.pk)` — the exact production resolver.
3. **Read back** a `Prediction(grain, target_natural_key, rung, outcome)` from the denorm fields + the
   current `listing_resolution` edge (rung/method live in the edge evidence).

`report.py` aggregates predictions into an `EvalReport` with a tri-state verdict:

| Metric | Rule |
| --- | --- |
| **Auto-accepts** | predictions where `outcome == ACCEPT and rung ∈ {1, 2}` — a first-observation corpus can't produce rung 0 (E-2b); rung 0 is covered by the separate regression suite, not this denominator |
| **Precision** | `correct_auto_accepts / total_auto_accepts`; *correct* ⇔ predicted grain **and** natural-key target match the label at that grain (rung-2 family attach checked at family grain) |
| **Verdict** | `total_auto_accepts < 100` → **INSUFFICIENT_CORPUS**; else `precision ≥ 0.995` → **PASS**; else **FAIL**. `INSUFFICIENT_CORPUS` and `FAIL` are both **non-pass** and both block the ADR flip — neither is silently skipped when a corpus is present (SA-005) |
| **Coverage** (reported, not gated) | per-source % of listings resolved at model grain or better (MS-2's ≥ 80% expectation) |
| **Per-source floor** (MS-1 gate) | every one of the 5 sources resolves ≥ 1 listing at family grain or better; a source stuck at `grain = none` fails MS-1 (surfaces a catalog/extraction gap) |
| **OEM dual-label rate** (reported) | % of ServerPartDeals + eBay listings printing both an OEM token and an MPN |

The `100` auto-accept floor is a named constant (ingestion-design §MS-1e denominator rule: the gate must
not be passable on trivially few matches or `grain = none` rows). A corpus that resolves 12 lucky matches
reports `INSUFFICIENT_CORPUS`, **never** `PASS`.

**Two distinct results, so precision can't masquerade as full readiness (SA-NEW-002).** `EvalReport`
carries `precision_verdict` (the tri-state above — precision + denominator only) **and** a composite
`ms1_ratification_gate`, which is `PASS` **only** when *all* of: `precision_verdict == PASS`; the
per-source family floor is met (every one of the 5 sources resolves ≥ 1 listing at family grain or
better); and the rung-0 regression suite (E-2b) is green. A precision `PASS` with one source stuck at
`grain = none` yields `ms1_ratification_gate = FAIL` — the ADR flip and MS-1 close key off the composite
gate, never bare precision. (If eBay is legitimately absent because access regressed, that is an
owner-decided OQ per §8, not a silent floor pass.)

## 6. Ratification procedure (deferred, owner-in-the-loop)

Documented as a runbook; executed after MS-1e merges, not in its PR:

1. `manage.py harvest_corpus --all --limit …` (live, manual) → staging JSONL of ~150–200 titles
   spanning all 5 sources.
2. Claude drafts `label` for every entry → labeled `corpus.jsonl` + `corpus.meta.json`.
3. Owner audits a random ~20% sample **plus every entry where the label disagrees with the matcher
   prediction** (S-5); corrections set `audit_status = owner_corrected`.
4. Run `tests/db/test_ratification_corpus.py` (which evaluates `ms1_ratification_gate`) **and** the rung-0
   regression suite (E-2b).
5. **PASS** — `ms1_ratification_gate == PASS` (precision `PASS` on ≥ 100 rung-1/2 auto-accepts **and** the
   per-source family floor met across all 5 sources **and** the rung-0 regression suite green) → flip
   ADR-0019 MADR status `proposed → accepted`, record the result in its **Confirmation** section, drop the
   D-019 / C.3 "(proposed)" qualifiers in the master spec, update the ADR-index row, and close the TODO
   ratification item.
6. **INSUFFICIENT_CORPUS** (< 100 auto-accepts) → harvest more titles; **never** an ADR flip (SA-005).
7. **FAIL** — `precision_verdict` FAIL (< 99.5%), a per-source floor miss, or a rung-0 regression failure →
   veto-rule fix / catalog or extraction gap fix + `matcher_version` bump + re-run (master-spec C.3.5
   loop). **The gate is never loosened.**

MS-1 close additionally requires (ingestion-design §MS-1e): the FR-003 acceptance case green as a
resolver-driven test (a recert + a new listing of the same drive → one `product_model`, two
`product_variant`s), the per-source resolution floor (§5), and the coverage report emitted for MS-2.

## 7. Testing strategy

- **Always-on harness unit tests** (`tests/unit/test_corpus_eval.py`) drive `synthetic.jsonl`
  (~10 hand-built entries with known outcomes): precision math; the tri-state boundaries (a `< 100`
  case must yield `INSUFFICIENT_CORPUS`, **not** `PASS`; a `99.4%` case must `FAIL`); coverage; the
  per-source floor (a source stuck at `none` fails); the OEM rate; a deliberate capacity-contradiction
  entry that must land in review, **not** auto-accept (the poison case ADR-0019 rule 1 is built to stop);
  and a variant-grain case where two entries share one `ProductModel` but differ by variant
  `condition`, asserting the eval compares real variant identity, not a model-collapsed key (SA-003).
- **Rung-0 regression suite** (DB-backed, `tests/db/test_rung0_regression.py`, E-2b): unchanged
  re-observation inherits the prior accept with no edge spam; a re-observation whose hard attribute now
  contradicts is demoted to REVIEW, not silently re-inherited. This is the rung-0 half of the "rungs 0–2"
  contract the first-observation corpus can't reach.
- **Schema-validation tests** (`tests/unit/test_corpus_schema.py`): grain/target-key consistency
  (variant grain requires the variant object), malformed-entry rejection, and **rejection of any source
  key outside the five real registry keys** (SA-002).
- **Harvest-command test** (`tests/unit/test_harvest_corpus.py`): a fake adapter → asserts JSONL
  shape and staging fields (incl. per-source `harvested`/`skipped_malformed` counts); eBay-creds-absent →
  eBay-only skip, other sources still harvested; and **`--out` under a tracked path is refused without
  `--allow-repo-output`** (SA-006).
- **Ratification test** (`tests/db/test_ratification_corpus.py`): distinguishes **corpus absent** →
  `pytest.skip("corpus not yet harvested")` (pre-harvest CI stays green) from **corpus present** →
  compute `ms1_ratification_gate` and **assert `PASS`**; a present-but-below-floor corpus asserts
  `INSUFFICIENT_CORPUS` and **fails** (never a silent skip, SA-005). Separate parametrized fixtures cover
  absent / below-floor / failing-precision / **precision-passes-but-one-source-at-`grain=none`**
  (composite gate must FAIL, SA-NEW-002) / fully-passing. This *is* the executable ratification gate.
- **Full gate** (`uv run python -m scripts.check`) green at every commit: ruff format + check,
  basedpyright, pytest + coverage, pip-audit.

## 8. Risks / open edges

| Risk | Posture |
| --- | --- |
| Live endpoints drifted since the 2026-07-04 recon; harvest yields too few titles for a source | Harvest is manual/plan-time (never CI); a thin source surfaces its catalog/extraction gap via the §5 per-source floor — that is a finding MS-1 must expose, not hide |
| eBay Buy-API access regressed (terms are eBay-mutable) | Re-run the two-step smoke at harvest time; if regressed, harvest the other four and raise an OQ (defer-eBay decision is the owner's) — the harness tolerates a missing source |
| Corpus precision misses 99.5% | Master-spec C.3.5 loop: veto fix + `matcher_version` bump + re-run; never loosen the gate |
| Synthetic fixture diverges from real matcher behavior | The synthetic fixture tests only the *harness math*, not matcher quality; the real gate is Approach A against the production resolver — the two never substitute for each other |

## 9. Execution process

`superpowers:writing-plans` plan doc (`docs/superpowers/plans/2026-07-06-ms1e-…`) → execution with the
verification gate → `dev → main` PR (merge commit, CI + dependency-review green). The live harvest,
label audit, and ratification (§6) are a **separate, later** owner-in-the-loop step, not part of this
PR. Deviations from this design or the spec go to the spec Deviations Log / OQ process.
