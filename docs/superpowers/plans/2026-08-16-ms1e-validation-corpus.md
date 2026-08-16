# MS-1e — Validation corpus + ratification tooling: Implementation Plan

> For the executing agent: work top to bottom; every task is TDD (failing test →
> implement → green → commit). Design source of truth:
> `docs/superpowers/specs/2026-07-06-ms1e-validation-corpus-ratification-design.md`
> (all E-*/SA-* IDs below refer to it). Deviations go to the spec Deviations Log / OQ
> process, never silently.

**Goal:** the deterministic evaluation harness + harvest tooling of design E-1: a
versioned JSONL corpus schema, an Approach-A evaluator over the production resolver,
the tri-state `precision_verdict` + composite `ms1_ratification_gate`, the
`harvest_corpus` management command, the rung-0 regression suite, and the
skip-guarded executable ratification gate. **No live harvest, no labels, no ADR flip
in this milestone** — those are the deferred owner-in-the-loop step (design §6).

## Global constraints

- Full verification gate green at every commit: `uv run python -m scripts.check`
  (ruff format --check, ruff check, basedpyright, coverage run -m pytest,
  coverage report, pip-audit). DB tests need TimescaleDB at
  `HW_RADAR_DB_PORT=5433` on this workstation.
- The eval module contains **no matching logic** (design §2): it orchestrates
  `CatalogResolver` and owns only corpus I/O, metrics math, and verdicts.
- One normalizer, both sides (SA-003): labels hold display values; the loader
  normalizes via the production `canonicalize_title` / `normalize_alias_text`
  (`src/hw_radar/matching/normalize.py:38,47`). `manufacturer_key` compares exactly.
- Source keys are the five real registry keys from
  `src/hw_radar/acquisition/sources/__init__.py:13` (SA-002); schema validation
  rejects anything else.
- Public-repo rule: no secrets, no raw payloads committed; credential names +
  OpenBao path only in `docs/handoff/credentials.md`.
- Conventional, GPG-signed commits on the leg branch.

## Interfaces reused verbatim (do not reimplement)

- `ParsedListing` — `src/hw_radar/acquisition/contracts.py:35` (fields:
  `source_listing_key`, `url`, `title`, `price`, `currency`, `condition_label`,
  `attrs`, …).
- Ingest chain: `fx.stamp(parsed, observed_date)` →
  `persist.upsert_listing(site, normalized, retention_class)` →
  `persist.append_snapshot(listing, normalized, observed_at=…)`
  (`src/hw_radar/acquisition/fx.py:31`, `src/hw_radar/acquisition/persist.py:52,74`;
  `append_snapshot` writes `attrs_json` from `normalized.attrs`).
- `CatalogResolver().resolve_listing(listing_id)` —
  `src/hw_radar/matching/resolver.py:552`; writes `ListingResolution` edges +
  denorm fields on `Listing`. `Outcome` (`matching/ladder.py:97`), `Grain`
  (`matching/types.py:14`).
- Variant identity enums on `ProductVariant`
  (`src/hw_radar/catalog/models/identity.py:149`): `Condition`, `Packaging`,
  `RecertChannel`, `WarrantyChannel`.
- Adapter protocol `SourceAdapter` (`acquisition/contracts.py:61`): `async fetch()`
  → `RawBatch`; `parse(batch)` → `list[ParsedListing]`.
- Management-command shape: mirror
  `src/hw_radar/catalog/management/commands/import_refdata.py`.

## Phases

Phases A, B, and C are file-disjoint and may execute in parallel worktrees.
Phase D integrates and closes.

### Phase A — eval module + corpus schema + fixtures + ratification gate

Files: `src/hw_radar/matching/eval/{__init__,corpus,evaluate,report}.py`,
`tests/fixtures/matching_corpus/synthetic.jsonl`,
`tests/fixtures/matching_corpus/synthetic.meta.json`,
`tests/unit/test_corpus_schema.py`, `tests/unit/test_corpus_eval.py`,
`tests/db/test_ratification_corpus.py`.

- **A1 — corpus schema (`corpus.py`).** Pydantic models `TargetKey`,
  `GroundTruthLabel`, `CorpusEntry`, `CorpusMeta` + `load_corpus(jsonl_path)` /
  `load_meta(path)` implementing design §3 exactly: source-key whitelist (SA-002),
  grain/target-key consistency invariants, `audit_status` enum,
  `oem_dual_label` flag, USD-only currency (rejects `currency != "USD"`), required
  `CorpusMeta.observed_at` (strictly timezone-aware UTC: naive, malformed, and
  non-UTC-offset values are rejected; PA-004), strict extra-field rejection
  (`extra="forbid"` or equivalent on every model level — entry, listing, label,
  target, variant, meta — so a committed `raw_payload`, seller field, or arbitrary
  nested key can never slip in; PA-002), and corpus-level uniqueness —
  unique `id` and unique `(source, source_listing_key)` — failing at load before
  any DB writes (protects the distinct-first-observation premise; review SA-004).
  Manufacturer-key membership against the seeded set is
  validated at load time by the evaluator (A2), not hardcoded in the schema.
  Tests: `test_corpus_schema.py` — valid entries at every grain; each consistency
  violation rejected; unknown source key rejected; malformed JSON line rejected;
  non-USD rejected; both duplicate cases rejected; extra-field injection rejected
  at every nesting level (incl. `raw_payload` and seller keys); `observed_at`
  naive / malformed / non-UTC-offset rejected.
- **A2 — evaluator (`evaluate.py`).**
  `evaluate_corpus(entries, meta) -> list[Prediction]` (DB-touching). Isolation
  contract (PA-001): the evaluator owns no transaction management — the caller
  wraps each evaluation in a transaction/savepoint it rolls back, and the
  evaluator writes only inside that scope; `meta.observed_at` is threaded to
  every `fx.stamp` `observed_date` and `append_snapshot` `observed_at`. Two
  evaluations of the same corpus in independently rolled-back scopes must be
  possible and identical (the fixed `observed_at` would otherwise collide on the
  snapshot composite key if state leaked). Per entry rebuild a `ParsedListing`
  from `title` + `listing`
  fields, run `fx.stamp` → `upsert_listing` → `append_snapshot` (attrs verbatim →
  `attrs_json`, SA-004; `observed_date`/`observed_at` taken from the corpus's
  fixed `meta.observed_at`, never wall-clock), then `CatalogResolver().resolve_listing(listing.pk)`, and
  read back `Prediction(grain, manufacturer_key, family_norm, model_norm,
  variant_tuple, rung, outcome)` from the denorm fields + latest resolution edge.
  Label comparison per SA-003 (loader-normalized display values; exact
  `manufacturer_key`; exact variant enums). Unknown `manufacturer_key` (absent from
  seeded `Manufacturer.normalized_name` set) is a hard validation error.
- **A3 — report (`report.py`).** `EvalReport` confined to corpus-derived results
  (§5 as revised): auto-accepts (`outcome == ACCEPT and rung ∈ {1,2}`), precision,
  tri-state `precision_verdict` (`MIN_AUTO_ACCEPTS = 100` named constant;
  `PASS_PRECISION = 0.995`), per-source coverage (reported), per-source
  ≥1-family-grain floor, OEM dual-label rate over serverpartdeals+ebay, and
  `audit_gate` (design §3: disagreement coverage + seeded ceil(0.20·N) sample
  floor via `random.Random(corpus_version)` over id-sorted entries + meta-rollup
  consistency). The composite is a pure function
  `ms1_ratification_gate(report, rung0_status)` with `rung0_status ∈
  {PASS, FAIL, NOT_RUN}` supplied by the caller; PASS requires precision PASS ∧
  floor ∧ audit_gate PASS ∧ rung0 PASS; rung0 FAIL → FAIL, NOT_RUN → INCOMPLETE.
  Tests: `test_corpus_eval.py` drives synthetic predictions/fixture through the
  math: 99.4% → FAIL; <100 accepts → INSUFFICIENT_CORPUS (never PASS); floor miss
  → composite FAIL despite precision PASS; audit-gate math incl. reproducible
  sample selection; composite with rung0 FAIL/NOT_RUN/PASS; determinism (same
  input twice → identical report); OEM rate; variant-identity case (two
  entries, one model, different `condition` — must compare variant identity, not a
  model-collapsed key); poison capacity-contradiction entry lands REVIEW.
- **A4 — synthetic fixture.** ~10 hand-built entries with known outcomes spanning
  all five sources and all grains incl. one `none` contradiction case; matching
  `synthetic.meta.json`. The fixture feeds A1/A3 unit tests and the DB-backed
  harness test.
- **A5 — ratification gate (`tests/db/test_ratification_corpus.py`).** Corpus
  absent → `pytest.skip("corpus not yet harvested")`; corpus present → assert the
  corpus-side gate (precision + floor + audit_gate; `rung0_status` is supplied
  externally per design §5/§6 — the suite-level green of the same pytest run is
  what binds rung 0 in). Parametrized tmp-corpus fixtures cover
  absent / below-floor (asserts INSUFFICIENT_CORPUS and fails the gate) /
  failing-precision / precision-pass-but-one-source-`none` (composite FAIL) /
  fully-passing (SA-005, SA-NEW-002), plus audit-gate negatives: all-draft
  corpus, one unaudited disagreement, sample one below the floor, stale meta
  rollup — each `audit_gate != PASS` and a non-pass composite. Also a DB-backed
  end-to-end run of the synthetic fixture through `evaluate_corpus` against a
  seeded catalog, plus a repeat-evaluation determinism test (PA-001): the same
  corpus evaluated twice in independently rolled-back transactions/savepoints
  yields identical predictions and identical complete reports, and the persisted
  snapshot carries exactly `meta.observed_at` (PA-004 enforcement point).

### Phase B — harvest command

Files: `src/hw_radar/catalog/management/commands/harvest_corpus.py`,
`tests/unit/test_harvest_corpus.py`, `.gitignore` (add `.harvest/`),
`docs/handoff/credentials.md` (eBay env-var names + OpenBao path, no values).

- **B1 — command.** Flags `--source {five registry keys}|--all`, `--limit N`
  (post-`parse()` deterministic truncation), `--out PATH` (default `.harvest/`).
  Drives each adapter directly (`fetch()`/`parse()`), bypassing scheduler and
  `SourceConfig.enabled`. Per-source isolation (one failure logs and continues,
  NFR-001); per-source `harvested`/`skipped_malformed` counts in output; eBay
  skipped (others continue) when `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` absent;
  refuses `--out` under a git-tracked path without `--allow-repo-output` (SA-006).
  `skipped_malformed` = adapter-reported malformed drops (B3) + post-`parse()`
  staging-validity rejections (blank `title` or blank `source_listing_key`).
- **B3 — parse-diagnostics contract (PA-003).** The `SourceAdapter` protocol
  gains `last_parse_skipped: int` — the count of raw source records the most
  recent `parse()` call discarded as malformed, reset at the start of every
  `parse()`. Each of the five production adapters increments it at its existing
  internal drop sites; production `run_source` semantics are unchanged (the
  attribute is observational). `harvest_corpus` folds it into the per-source
  `skipped_malformed` count. Tests: per-adapter real-shape batches containing
  valid + malformed records assert harvested/skipped counts reconcile with the
  batch under `--limit` semantics (limit truncation is not "malformed"), plus a
  fake-adapter test that the command sums adapter-reported and staging-validity
  drops.
  Staging entries are unlabeled (`id`, `source`, `title`, `listing` (+ optional
  `oem_dual_label` heuristic pre-fill)); the command never invents labels.
- **B2 — tests.** Fake adapter → JSONL shape + counts; eBay-creds-absent →
  eBay-only skip; tracked-path refusal without `--allow-repo-output`; `--limit`
  truncation. No network, no DB.

### Phase C — rung-0 regression suite (E-2b)

Files: `tests/db/test_rung0_regression.py`.

- **C1 —** DB-backed tests against the production resolver: (a) an unchanged
  re-observation inherits the prior accept with no additional resolution-edge spam;
  (b) a re-observation whose hard attribute now contradicts is demoted to REVIEW,
  not silently re-inherited. Use the existing `tests/db/` conventions
  (`django_db(transaction=True, serialized_rollback=True)` where writes occur,
  `tests/db/conftest.py` cleanup fixture, seeded catalog helpers already used by
  existing resolver DB tests).

### Phase D — integration + docs + close

- **D1 —** Integrate A/B/C onto `dev` (orchestrator-only), run the full battery
  serially, fix regressions.
- **D2 —** Docs: add the design §6 ratification runbook pointer + status updates —
  `docs/STATUS.md`, `docs/TODO.md` (narrow the MS-1e item to the deferred
  owner-in-the-loop step), `docs/handoff/specs-plans.md` (this plan row),
  handoff closeout.
- **D3 —** Push `dev`, open the `dev → main` PR (merge commit, CI +
  dependency-review green). The live harvest, labeling, owner audit, and ADR-0019
  flip stay out of this PR (E-1).

## Acceptance

- All design §7 test surfaces exist and pass; `scripts.check` fully green.
- `tests/db/test_ratification_corpus.py` skips (corpus absent) in CI today, and
  its parametrized non-skip cases prove the gate can never silently pass
  (SA-005 / SA-NEW-002).
- No new matching logic outside the resolver; no committed raw payloads or
  secrets.
