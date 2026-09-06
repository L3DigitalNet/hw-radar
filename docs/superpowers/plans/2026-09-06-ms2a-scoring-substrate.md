# MS-2a — Scoring substrate: Implementation Plan

> For the executing agent: work top to bottom within a phase; every task is TDD
> (failing test → implement → green → signed commit). Design source of truth:
> `docs/superpowers/specs/2026-09-06-ms2-scoring-design.md` (revision 12,
> owner-ratified 2026-09-06 — all 27 §1.2 rows `accept`). Every `S2-*` / `SA-*` ID
> and every `§` below refers to that document; every `file:line` was read at
> `cfb3a744` before it was written down. Deviations from the design go to the spec
> Deviations Log / OQ process, never silently — the "Open at plan time" list at the
> end is the only place this plan admits an unsettled question, and none of them may
> be closed by inventing design.

**Goal:** the data model, retention wiring, and clearing/serialization contract that
scoring needs, per design §3 "MS-2a — Scoring substrate": `catalog/models/scoring.py`
(`ListingScore` hypertable, append-only `CohortBaseline` versions, the
`CohortBaselineCurrent` pointer, `SellerRatingObservation`), the §2.1 field additions
on `Listing` / `OfferSnapshot` / `DriveSpec` / `SourceConfig`, the §3.4.4 `ScoringRun`
model, the §2.3(b) clearing hooks, the four `purge_expired` hooks including
`lock_parents_for_delete` with its three per-model implementations (§3.4.2, SA-009),
and `scoring/contracts.py`. **No scoring math (MS-2b), no cohort service (MS-2c), no
`score-refresh` job (MS-2d), no adapter changes (MS-2e)** — §3 requires this
sub-milestone to be mergeable and deployable on its own **without changing any
observable behavior**.

## Global constraints

- Full verification gate green at every commit: `uv run python -m scripts.check`
  (ruff format --check, ruff check, basedpyright, coverage run -m pytest, coverage
  report, pip-audit). DB tests need live TimescaleDB; on this workstation use
  `HW_RADAR_DB_PORT=5433`.
- **Heavy-test guard:** a worker runs **one test file per `pytest` invocation**
  (`uv run pytest tests/db/test_x.py`). The whole-suite battery belongs to the
  integrating leg (Phase F), not to a task commit.
- **No scoring math in this sub-milestone** (§3, MS-2a last bullet). No subscore,
  veto, rubric, cohort-key derivation, digest computation, relaxation, or `$/TB`
  arithmetic lands here — those are MS-2b/MS-2c. Columns that will hold those values
  are created and constrained; nothing computes them.
- **No scheduler job.** `poller/service.py` is not touched; `score-refresh` is MS-2d
  (S2-3). The §3.3.5(g) baseline prune is MS-2d as well.
- **Sources stay disabled.** No `SourceConfig.enabled` flip, no adapter change, no
  network. MS-1e's owner-in-the-loop ratification is still pending.
- Public-repo rule: no secrets, no private hostnames, no raw merchant payloads
  committed. Fixtures are synthetic.
- Conventional, GPG-signed commits on the leg branch; one commit per task.
- **Migration numbering is pinned by this plan** (next free number is `0018`;
  `0017_identity_retention_checks.py` is the current leaf). The chain is linear —
  Django rejects multiple leaf nodes — so a leg that lands out of order renumbers and
  re-points `dependencies` before merge, and Phase F's `makemigrations --check` is
  what catches drift.

## Interfaces reused verbatim (do not reimplement)

- Retention substrate — `RetentionGoverned`
  (`src/hw_radar/catalog/models/base.py:99`), `retention_constraints(prefix)` (`:53`,
  emits `<prefix>_retention_class_set` + `<prefix>_retention_ttl_coherent`),
  `retention_indexes(name)` (`:77`), `RetentionClass` (`:4`),
  `BOUNDED_RETENTION_CLASSES` (`:44`), `INDEFINITE_RETENTION_CLASSES` (`:37`).
  **No new `RetentionClass` value is added** (S2-2a).
- Hypertable precedent — `src/hw_radar/catalog/migrations/0003_offer_snapshot_hypertable.py`
  (`migrations.RunSQL("SELECT create_hypertable(...)", reverse_sql=migrations.RunSQL.noop)`,
  forward-only with the noop-reverse comment); the interval form is
  `by_range('observed_at', INTERVAL '1 month')` from
  `0010_heartbeat_timescale.py:7-11`. Hypertable assertion precedent:
  `tests/db/test_market.py:43` and `tests/db/test_heartbeat_models.py:63`
  (`SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = …`).
- Sweep hook discovery protocol — `purge_expired` resolves per-model hooks with
  `getattr(model, "deletion_exempt_q", None)`
  (`src/hw_radar/catalog/management/commands/purge_expired.py:127`) and
  `getattr(model, "redact_expired", None)` (`:144`); the per-batch transaction and
  delete are `purge_expired.py:213-221` (`for pks in _batches(...)` → `with
  transaction.atomic():` → `_deletable(model, now).filter(pk__in=pks).delete()`).
  Registry: `retention_governed_models()` (`:98`); predicates `_expired` (`:150`),
  `_deletable` (`:158`); `DEFAULT_BATCH_SIZE = 5_000` (`:65`).
- Market models — `Listing` (`src/hw_radar/catalog/models/market.py:166`) with
  `REDACTED_CONTENT_FIELDS` (`:230`), `seller` FK (`:241`), `Meta` (`:301`),
  `mark_delisted()` (`:330`), `redact_expired()` (`:454`, a bulk
  `kept.update(**cls.REDACTED_CONTENT_FIELDS)` at `:480`), `deletion_exempt_q()`
  (`:409`), `mark_relisted()` (`:482`), `ListingQuerySet.active()` (`:126`);
  `OfferSnapshot` (`:510`) with `CompositePrimaryKey("listing_id", "observed_at")`
  (`:513`), `observed_at` (`:515`), generated `total_landed_price` (`:520`),
  `stock_status` (`:527`), `fx_rate` (`:531`), generated `usd_item_price` (`:535`,
  carrying the NULL-propagation comment §2.1 cites), `Meta` (`:553`); `Seller`
  (`:64`); `StockStatus` (`:57-61`).
- Ops models — `RunStatus` (`src/hw_radar/catalog/models/ops.py:77`),
  `RunFailureClass` (`:83`), `SourceConfig` (`:93`), `ScraperRun` (`:206`, non-null
  `PROTECT` FK to `SourceSite` at `:207`, `db_table = "scraper_runs"` `:223`, index
  `:225`). `RunKind` (`:70`) is **not** extended (§2.1, S2-19).
- Identity — `DriveSpec` (`src/hw_radar/catalog/models/identity.py:206`),
  `market_tier` free-text today (`:222`); `ProductModel` (`:131`); refdata
  pass-through `market_tier: str = ""` (`src/hw_radar/refdata/contracts.py:72`).
- Mutable-freshness-stamp precedent — `ListingResolution.last_evaluated_at`
  (`src/hw_radar/catalog/models/resolution.py:62`).
- Write path (read-only reference; unchanged by this milestone) —
  `acquisition/persist.py:52` `upsert_listing`, `:74` `append_snapshot` (creates at
  `:86`, sets `expires_at=listing.expires_at` at `:101`).
- Runtime DB role — `hw_radar` (`src/hw_radar/settings.py:92`); it owns the tables,
  which is why §3.3.5(a) uses a trigger rather than `REVOKE`.
- Admin registration style — `src/hw_radar/catalog/admin.py:25-38` (plain
  `admin.site.register`) and `:41` (`@admin.register` + a class for anything with
  behavior).
- Registry invariants test to mirror — `tests/unit/test_purge_registry.py`.

## Phases

Phase A is DB-free and file-disjoint from everything; it may run in a parallel
worktree from the start. Phases B and C are disjoint in product source
(`market.py` / `identity.py` / `ops.py` versus the new `scoring.py`) and may be
authored in parallel, but the migration chain is linear, so a parallel C leg rebases
onto B's merged head before generating `0022`. Phase D needs B and C. Phases E and F
close.

### Phase A — `scoring/contracts.py`

Files: `src/hw_radar/scoring/__init__.py`, `src/hw_radar/scoring/contracts.py`,
`tests/unit/test_scoring_contracts.py`.

- **A1 — contracts (§2 module layout).** Pydantic v2 models `CohortKey`,
  `CohortStats`, `SubscoreSet`, `ScoreExplanation`, `ScoredListing`, mirroring
  `src/hw_radar/acquisition/contracts.py`'s style. It is a **current-version façade
  only**: §3.4.3 item 1 forbids any released `scoring/versions/v<N>/` package from
  importing it, so this module imports nothing from `scoring/` and nothing from
  Django. Field membership is taken from the design where the design fixes it —
  `SubscoreSet` = the four subscores in `[0,1]` (§3.2.1); `CohortStats` = `n_eff`,
  `lambda_shrinkage`, `q`, `q25`, `q50`, `q75`, `margin_iqr`, `relaxation_step`,
  `relaxation_exhausted`, `baseline_digest` with §3.4.1 group-4 nullability (`n_eff`
  and `λ` never null, the rest nullable); `ScoredListing` = the DR-004 payload shape
  of §3.4.2's decision list. See "Open at plan time" #4 before widening this.
  Tests: `test_scoring_contracts.py` — each model validates a well-formed instance;
  out-of-range subscores and a negative `n_eff` are rejected; `extra="forbid"`
  rejects an unknown key; the neutral `CohortStats` shape (`n_eff = 0`, `λ = 0`,
  `q`/quartiles/`margin_iqr` `None`) validates, and `n_eff = None` does not
  (§3.2.6, SA-027).

### Phase B — the §2.1 field additions

Files: `src/hw_radar/catalog/models/market.py`,
`src/hw_radar/catalog/models/identity.py`, `src/hw_radar/catalog/models/ops.py`,
`src/hw_radar/catalog/models/__init__.py`,
`src/hw_radar/catalog/migrations/0018_listing_scoring_columns.py`,
`0019_offer_snapshot_ingest_stamp.py`, `0020_market_tier_enum.py`,
`0021_scoring_run_seller_prior.py`, `src/hw_radar/catalog/admin.py`,
`tests/db/test_scoring_listing_fields.py`, `tests/db/test_ingest_stamp.py`,
`tests/db/test_market_tier.py`, `tests/db/test_scoring_run.py`.

- **B1 — `Listing` scoring columns + the `SCORING_INPUT_FIELDS` guard (§2.1).**
  Migration `0018`. Adds `extracted_attrs_json` (JSONField, default `{}`, and a new
  member of `REDACTED_CONTENT_FIELDS` blanking to `{}` — §2.1 says that field-set's
  own rule needs no migration), `returns_policy` (TextChoices
  `returns_accepted` / `no_returns` / `unknown`, default `unknown`),
  `returns_window_days` (nullable int), the five S2-1/§2.3(b) denorm columns
  `current_deal_score`, `current_score_provisional`, `current_scored_at`,
  `current_cohort_key`, `current_offer_observed_at` (all nullable), and
  `scoring_inputs_changed_at` (`DateTimeField(null=True, db_index=True)`).
  Declares `SCORING_INPUT_FIELDS = {extracted_attrs_json, returns_policy,
  returns_window_days, seller, source_site}` and the **first `save()` override in
  this repository** (no model overrides `save()` today — the only `def save*` in
  `src/hw_radar` is `scheduling/checkpoint.py:17 save_buckets`, a module function),
  advancing `scoring_inputs_changed_at` when a member is written — full
  save **or** `update_fields`. Tests (`test_scoring_listing_fields.py`): the stamp
  advances on a full save that changes a member, on `save(update_fields=["seller"])`,
  and **not** on `save(update_fields=["last_seen"])` or on a member-free full save;
  the five `current_*` columns persist and accept NULL; `extracted_attrs_json` blanks
  to `{}` through `redact_expired()`; `current_offer_observed_at` round-trips a
  timezone-aware value. See "Open at plan time" #2 for the delist/redaction half.
- **B2 — `OfferSnapshot`: landed USD price + the §2.2.2 ingestion stamp.**
  Migration `0019`, with a `RunPython` backfill. Adds the stored generated column
  `usd_total_landed_price = total_landed_price × fx_rate` beside `usd_item_price`
  (`market.py:535`), carrying that column's NULL-propagation rule verbatim (a NULL
  `fx_rate` propagates to NULL; §2.1, §2.2.1 eligibility); `ingested_at`
  (`DateTimeField(db_default=Now(), editable=False)`); `ingest_stamp_backfilled`
  (`BooleanField(default=False)`); and an index on `("ingested_at",)`. The backfill
  sets `ingested_at := observed_at` and `ingest_stamp_backfilled := true` for every
  pre-existing row. Tests (`test_ingest_stamp.py`): `append_snapshot` cannot set
  `ingested_at` — passing one does not reach the row and the database assigns the
  value (§2.2.2, S2-24); a row written after the migration carries
  `ingest_stamp_backfilled = false`; a migration test asserts a pre-existing row is
  backfilled with the flag `true` and `ingested_at == observed_at`;
  `usd_total_landed_price` is NULL exactly when `fx_rate` is NULL and equals
  `total_landed_price × fx_rate` otherwise. **No B1 cursor, no high-water mark** —
  that is MS-2d.
- **B3 — `DriveSpec.market_tier` → `MarketTier` TextChoices + the §3.3.2 parent
  map.** Migration `0020` (`AlterField` + `RunPython` data migration). Values
  `enterprise` / `nas` / `surveillance` / `consumer` / `unknown` (§2.1). The data
  migration normalizes existing free text and lands the §3.3.2 parent map as the
  versioned constant it is: `enterprise|nas|surveillance → high_duty`,
  `consumer|unknown → client`; the map is **total** and every parent has ≥ 2 members.
  `refdata/contracts.py:72` keeps its `str` shape and the refdata loader path is
  **not** changed — §2.1 assigns the conversion to the field plus its data migration,
  and a later import writes through the same `AlterField`. Tests (`test_market_tier.py`): a
  round-trip through the migration maps each seeded spelling to its enum member and
  an unrecognized spelling to `unknown`; the parent map is total over
  `MarketTier.values` and each parent has ≥ 2 members (derived from the map, not
  hardcoded); a `DriveSpec` refuses an off-enum value at validation.
  **The map is data only — `scoring/cohort.py` and the ladder are MS-2b/MS-2c.**
- **B4 — `ScoringRun` (§3.4.4, S2-19) + `SourceConfig.seller_policy_prior`
  (S2-12).** Migration `0021`, including a `RunPython` seeding eBay's row to `0.60`
  and every other source to the `0.50` default (the `0005_seed_sources.py` seeding
  precedent). `ScoringRun` is a plain `models.Model` in `ops.py` beside `ScraperRun`,
  **not** `RetentionGoverned` (it holds counts, not merchant content), with exactly
  the §3.4.4 field table — `started_at`, `finished_at`, `status` (reusing
  `RunStatus`, `ops.py:77`), `failure_class` (reusing `RunFailureClass`, `:83`),
  `algorithm_version`, `cohorts_evaluated`, `cohorts_refreshed`, `baseline_writes`,
  `listings_rescored`, `score_writes`, `baseline_versions_pruned`, `error`,
  `detail_json` — `db_table = "scoring_run"`, one index on `("-started_at",)`, **no
  `source_site` field and no run-kind discriminator**. `RunKind.SCORE` is **not**
  added. Register it in the admin beside `ScraperRun` (`admin.py:37`). Tests
  (`test_scoring_run.py`): a `ScoringRun` persists with no source at all and defaults
  the six counts to 0; the **negative control** — constructing a `ScraperRun` without
  `source_site` still raises (§3.4.4 test 3); `seller_policy_prior` defaults to
  `0.50` and eBay's seeded row reads `0.60`.

### Phase C — `catalog/models/scoring.py`

Files: `src/hw_radar/catalog/models/scoring.py`,
`src/hw_radar/catalog/models/__init__.py`, `src/hw_radar/catalog/admin.py`,
`src/hw_radar/catalog/migrations/0022_listing_score.py`,
`0023_cohort_baseline.py`, `0024_seller_rating_observation.py`,
`tests/db/test_listing_score_model.py`, `tests/db/test_cohort_baseline.py`,
`tests/db/test_seller_rating_observation.py`.

- **C1 — `ListingScore` (§3, §3.3.5c, S2-1, S2-2a).** Migration `0022`: the model,
  then a `RunSQL` `create_hypertable('listing_score', by_range('scored_at', INTERVAL
  '1 month'))` with `reverse_sql=migrations.RunSQL.noop` on the `0003` precedent.
  Composite PK `(listing_id, scored_at)`. Columns exactly as §3 enumerates —
  `deal_score` (smallint 0–100), `base_score`, the four subscores, `cohort_key`,
  `cohort_baseline` FK (`PROTECT`, nullable), `n_eff`, `lambda_shrinkage`,
  `is_provisional`, `provisional_reasons`, `cap_applied` (nullable), `cap_reasons`,
  `risk_flags`, `dollars_per_tb` (nullable), `price_basis`, `quantity`,
  `quantity_basis`, `inputs_json`, `inputs_digest`, `decision_digest`,
  `explanation_json`, `explanation_text`, `algorithm_version`, `matcher_grain`,
  `offer_observed_at` (nullable), plus `RetentionGoverned` and
  `retention_constraints("listing_score")` / `retention_indexes("listing_score_expires")`.
  **Nullability is fixed by §3, not left to the migration:** `n_eff` and
  `lambda_shrinkage` are NOT NULL on every path (a neutral score stores `0`);
  `dollars_per_tb`, `cohort_key`, `cohort_baseline_id`, `cap_applied` and
  `offer_observed_at` are nullable under the rules below; **every other column is NOT
  NULL**. `q`, `q25`, `q50`, `q75` and `margin_iqr` are **not columns** — they live in
  `inputs_json` group 4. The three §3.3.5(c) CHECKs land here verbatim:
  `(cohort_baseline_id IS NULL) = (price_basis IN ('capacity_unavailable',
  'price_unavailable', 'no_cohort_evidence'))`, `(cohort_key IS NULL) = (price_basis =
  'capacity_unavailable')`, `(offer_observed_at IS NULL) = (price_basis =
  'price_unavailable')`. The FK ordering means `0022` follows `0023` if authored in
  the other order — keep the pinned numbering and let `CohortBaseline` land first if
  the executor prefers; the plan's dependency is on the model, not the file name.
  Tests (`test_listing_score_model.py`): `listing_score` is a hypertable (the
  `timescaledb_information.hypertables` query of `tests/db/test_market.py:43`);
  `retention_constraints("listing_score")` proves an `ebay_listing_observation` row
  **must** carry `expires_at` and a `merchant_fact` row **must not** (both
  directions); one row per neutral `price_basis` value persists with the exact stored
  FK (NULL), `cohort_key` (NULL only for `capacity_unavailable`), `n_eff = 0` and
  `lambda_shrinkage = 0` **as stored zeros** (SA-027's MS-2a half); each CHECK is
  asserted **in both directions** — a `cohort`-basis row with a NULL FK is rejected
  **by the database**, a `capacity_unavailable` row carrying a FK or a cohort key is
  rejected, a `price_unavailable` row carrying an `offer_observed_at` is rejected, and
  a `cohort`-basis row with a NULL `offer_observed_at` is rejected (§3.3.5c revision
  9); `offer_observed_at` is absent from `inputs_json` (SA-001); deleting a cited
  `CohortBaseline` raises `ProtectedError`.
- **C2 — `CohortBaseline` versions + the immutability trigger (§3.3.5a/b, S2-20).**
  Migration `0023`: the two models plus a `RunSQL` pair creating
  `cohort_baseline_no_update()` and the `BEFORE UPDATE ... FOR EACH ROW` trigger
  `cohort_baseline_immutable` exactly as §3.3.5(a) writes them, with a reversing
  `DROP` (the `0001`/`0003`/`0007`/`0010` RunSQL precedent). `CohortBaseline`: plain
  table, `UniqueConstraint(cohort_key, baseline_digest)`, columns `cohort_key`,
  `baseline_digest`, `computed_at`, `price_event_count`, `oldest_event_at`,
  `anchor_event_at`, `n_eff`, `q25`, `q50`, `q75`, `observation_vector`,
  `source_composition`, `window_days`, `half_life_days`, `relaxation_step`,
  `relaxation_exhausted`, `algorithm_version`; `retention_class = merchant_fact`,
  indefinite. **No detection stamp and no `raw_snapshot_count` on this table**
  (SA-025). A `save()` override refuses updates in Python with a message naming
  §3.3.5(a) — defence in depth and the error message, **not** the guarantee — and
  `django.contrib.admin` registers the model read-only (`has_add_permission`,
  `has_change_permission`, `has_delete_permission` all `False`, via the
  `@admin.register` class form at `admin.py:41`). `CohortBaselineCurrent`: PK
  `cohort_key`, `current_version` FK (`PROTECT`, **`null=True`**),
  `last_evaluated_at`, `last_promoted_at`, `ingest_high_water`,
  `next_window_exit_at` (**nullable**), `price_event_digest` (**NOT NULL**),
  `member_projection_digest`, `raw_snapshot_count`, `source_raw_snapshot_counts`;
  `merchant_fact`, indefinite. Tests (`test_cohort_baseline.py`), which are SA-003's
  `suggested_validation`: a released version refuses `UPDATE` through **each** of
  `Model.save()`, `QuerySet.update()`, `bulk_update()`, an admin change POST, and raw
  SQL on the ordinary application connection under the runtime role
  (`settings.py:92`) — and the bulk and raw refusals are asserted to come from **the
  database** (the trigger's `RAISE EXCEPTION` message / `psycopg` error class), not
  from Python, because a test that only proves "it raised" passes against the
  `save()`-only mechanism the finding is about; the row is byte-identical afterwards;
  the admin registration reports no add, change or delete permission. **Negative
  controls, so the trigger is proven not to be over-broad:** an `INSERT` of a new
  version succeeds, a `DELETE` of an uncited version succeeds (§3.3.5f needs it), and
  an `UPDATE` of the `cohort_baseline_current` pointer succeeds. Also: a duplicate
  `(cohort_key, baseline_digest)` is rejected; `current_version` and
  `next_window_exit_at` accept NULL while `price_event_digest` does not, so the
  §3.3.5(i) baseline-void state is representable and its empty-projection digest is
  not confusable with an absent one (SA-002).
- **C3 — `SellerRatingObservation` (§3, §3.2.5, S2-21).** Migration `0024`.
  Append-only, `RetentionGoverned`, `retention_constraints` /`retention_indexes`,
  `UniqueConstraint(seller, observed_at)`. Fields per §3.2.5: `seller` FK,
  `observed_at`, `source_site`, `rating_percent_raw` and `feedback_count_raw` (both
  nullable, stored verbatim as received), `p_obs` (`numeric(5,4)`), `n`
  (`PositiveIntegerField`), `y` (`numeric(12,4)`), `count_basis`
  (`total_count` / `net_score_proxy` / `unavailable`), `is_usable` (bool),
  `raw_payload` FK. eBay-sourced rows carry `ebay_listing_observation` (IR-002); see
  "Open at plan time" #1 for the non-eBay class. **No normalization arithmetic here**
  — `p_obs`/`n`/`y` are written by MS-2e under the §3.2.5(b) contract. Tests
  (`test_seller_rating_observation.py`): a row persists and round-trips its `Decimal`
  values without float drift; a duplicate `(seller, observed_at)` is rejected; the
  retention CHECK pair holds in both directions for the bounded eBay class and an
  indefinite class; `count_basis` refuses an off-enum value.

### Phase D — the four `purge_expired` hooks

Files: `src/hw_radar/catalog/models/market.py`,
`src/hw_radar/catalog/models/scoring.py`,
`src/hw_radar/catalog/management/commands/purge_expired.py`,
`tests/db/test_score_clearing.py`, `tests/db/test_invalidation_hooks.py`,
`tests/db/test_lock_parents_for_delete.py`, `tests/db/test_seller_anchor_phantom.py`.

- **D1 — the §2.3(b) clearing contract.** `Listing.mark_delisted()`
  (`market.py:330`) and `Listing.redact_expired()` (`:454`) clear the five `current_*`
  columns in the same transaction as the delist mark / content redaction, for **every**
  delist regardless of retention class (the listing has left `P`, §2.2).
  `ListingScore.clear_parent_denorm(deleted_keys)` is a classmethod taking the deleted
  `(listing_id, scored_at)` pairs and issuing, per pair, exactly the §2.3(b) guarded
  update — the five columns nulled `WHERE id = %(listing_id)s AND current_scored_at =
  %(scored_at)s`. **The `AND current_scored_at = …` predicate is the whole
  mechanism**; the withdrawn `clear_parent_denorm(listing_ids)` signature must not
  reappear. It is dispatched by `purge_expired` through the existing `getattr`
  protocol (`purge_expired.py:127,144`) inside the batch's own `transaction.atomic()`
  (`:213-221`). Tests (`test_score_clearing.py`) — the six §2.3(b) tests, led by the
  SA-002 regression: (1) an eBay listing with an **expired old** score row and a
  **newer unexpired** row that `current_*` points at — sweep, assert the old row is
  deleted and the pointer still identifies the newer row unchanged; (2) the pointer's
  own row expires — sweep, assert the row is gone and all five `current_*` are NULL;
  (3) interleaving — with a batch already selected, write a newer score row and
  repoint `current_*`, then run the clear: it no-ops, and running it twice is
  idempotent; (4) delist a scored listing and assert the clear and the mark cannot be
  observed separately (assert on the post-commit row); delisting clears
  **unconditionally**; (5) `source_composition` has no MS-2a surface — replaced by the
  `redact_expired()` path asserting the same five columns clear alongside the content
  redaction (§2.3b row 2); (6) the negative control — a `merchant_fact` listing's
  score row survives a sweep with its `current_*` columns intact.
- **D2 — L8 and L9 invalidation hooks (§3.4.1, §3.4.2(5)–(6)).**
  `OfferSnapshot.invalidate_listing_scores(deleted_keys)` issues, per deleted
  `(listing_id, observed_at)` pair, the §3.4.2(5) update — the same five columns
  nulled `WHERE id = %(listing_id)s AND current_offer_observed_at = %(observed_at)s`.
  `SellerRatingObservation.invalidate_seller_scores(deleted_keys)` clears the same
  five columns for every listing in `P` of an affected seller, **guarded on the
  score's stored group-3 `observed_at` equalling a deleted row's** (§3.4.2(6)); the
  guard is cross-row, which is why D3's pre-lock is required beneath it. Both are
  dispatched by the same `getattr` protocol inside the same per-model transaction as
  the delete. Tests (`test_invalidation_hooks.py`): deleting the **selected** snapshot
  clears exactly that listing's five columns; deleting a **non-selected** snapshot
  updates nothing and costs no fan-out; a `price_unavailable` listing
  (`current_offer_observed_at IS NULL`) is **never** cleared by any deleted snapshot —
  correct, not a gap (§3.4.2(5), SA-005); deleting the seller rating a score cites
  clears that listing while a **non-selected** rating clears nothing (L9's negative
  control); the clear is idempotent under repeated batches.
- **D3 — `lock_parents_for_delete(pks)` and its three implementations (§3.4.2(2),
  SA-006/SA-009).** A fourth optional classmethod under the **same** discovery
  protocol, called inside the batch's existing `transaction.atomic()` **before** the
  `DELETE` (`purge_expired.py:213-221`). **This is the only change MS-2a makes to
  `purge_expired` itself** (§3). Implementations, per §3's table and §3.4.2(2):

  | Model | What it locks, in order |
  | --- | --- |
  | `OfferSnapshot` | `SELECT id FROM listing WHERE id IN (distinct listing_ids) ORDER BY id FOR UPDATE` |
  | `ListingScore` | the same listing-only lock, for the same reason |
  | `SellerRatingObservation` | `SELECT id FROM seller WHERE id IN (distinct seller_ids) ORDER BY id FOR UPDATE` **first** — the anchor — then `SELECT id FROM listing WHERE id IN (bounded fan-out) ORDER BY id FOR UPDATE` |

  A listing-only implementation for `SellerRatingObservation` is **a defect, not a
  simplification**: the affected set is the mutable predicate "listings in `P` for
  that seller", so locking its current members cannot exclude a listing that joins
  afterwards. The global order is `seller` → `listing`, ascending `id` within each
  class (§3.4.2(3)); no writer may take `listing` before `seller`. Tests
  (`test_lock_parents_for_delete.py`): per model, the hook is asserted against that
  lock order — for `OfferSnapshot` and `ListingScore`, `listing` rows only; for
  `SellerRatingObservation`, distinct `seller` rows `FOR UPDATE` in ascending `id`
  order **before** any listing row — and a test **inspects `pg_locks` inside the
  sweep's transaction** to prove the seller lock is actually held rather than merely
  intended; a batch spanning two sellers locks both, ascending; the hook is discovered
  by `getattr` (a model without it is unaffected, asserted against an existing swept
  model).
- **D4 — the §3.4.2 fixture-12 `P`-entry phantom, with its negative control.**
  MS-2a is where the hook is introduced and the first point at which a wrong
  implementation is detectable, so the fixture runs here as well as in MS-2d.
  Two connections, per §3.4.2 fixture 12: seed a listing for the seller with **no
  snapshot**, so it is outside `P`; barrier — let
  `SellerRatingObservation.lock_parents_for_delete` select and lock the fan-out, then
  `append_snapshot` that listing into `P` on a second connection, then run the
  refresh **stand-in**, then let the sweep's `DELETE` and L9 commit. Because MS-2d's
  refresh does not exist yet, the stand-in is a test-only transaction that takes
  **exactly** §3.4.2(3)'s refresh lock order — `seller` `FOR SHARE`, then its one
  `listing` `FOR UPDATE` — and then writes a `listing_score` row citing the rating;
  it is a harness, not a design decision, and it must not migrate into product code.
  Assertions: the stand-in **blocks on the seller anchor** — not on the listing row,
  which the sweep never locked; it writes nothing until the sweep commits; the
  phantom listing is **explicitly asserted absent from the sweep's lock set**, so the
  test proves the anchor and not the fan-out; and after both transactions commit,
  **no row anywhere in `listing_score` — not merely no current pointer — cites the
  deleted rating's `observed_at`**, asserted by a whole-table query. **Negative
  control (the assertion that makes the criterion meaningful):** the same assertion
  run against a listing-only implementation of the hook must go **red** — implemented
  as a parametrized/monkeypatched listing-only variant the test expects to fail.

### Phase E — registry and retention coverage

Files: `tests/unit/test_purge_registry.py`, `tests/db/test_purge_expired.py`.

- **E1 — registry-derived retention tests.** Extend
  `tests/unit/test_purge_registry.py` (whose four properties are all derived from the
  app registry, never a hand-written list) so the new `RetentionGoverned` models are
  covered by the same derivations: `catalog.ListingScore` and
  `catalog.SellerRatingObservation` join the bounded-table membership assertion; every
  swept model still carries `retention_class`/`expires_at`, the partial `expires_at`
  index from `retention_indexes(...)`, and the DR-001 CHECK pair from
  `retention_constraints(...)`. Add one **registry-derived** property for this
  milestone: every model declaring any of the four sweep hooks
  (`clear_parent_denorm`, `invalidate_listing_scores`, `invalidate_seller_scores`,
  `lock_parents_for_delete`) is in `retention_governed_models()`, so a hook can never
  be declared on a table the sweep never visits. `CohortBaseline` /
  `CohortBaselineCurrent` are `merchant_fact` and indefinite, so the existing CHECK
  pair asserts they can never carry `expires_at`; `ScoringRun` is deliberately not
  `RetentionGoverned` and is asserted absent from the registry. In
  `tests/db/test_purge_expired.py`, add the sweeper-side coverage the registry test
  cannot give: one end-to-end sweep over an expired `ListingScore` batch and an
  expired `SellerRatingObservation` batch, asserting the rows are deleted and that
  each model's D1–D3 hooks were dispatched inside the batch transaction.

### Phase F — integration + close

- **F1 —** Integrate A–E onto `dev` (orchestrator-only), renumber migrations if any
  leg landed out of order, and run the **full battery serially**:
  `uv run python -m scripts.check` plus
  `uv run python manage.py makemigrations --check --dry-run`, and a migrate-from-empty
  run proving `0018`–`0024` apply from a fresh database and the two hypertables and
  the trigger exist afterwards. Fix regressions.
- **F2 —** Docs and status: `docs/STATUS.md`, `docs/TODO.md` (narrow the MS-2 item to
  MS-2b onward), `docs/handoff/specs-plans.md` (this plan's row), handoff closeout.
  No spec edit — a deviation goes to the Deviations Log / OQ process (§7).
- **F3 —** Push `dev`, open the `dev → main` PR (merge commit, CI + dependency-review
  green), per §7's one-PR-per-sub-milestone rule. MS-2b's pure library, MS-2c's
  cohort service and MS-2d's job stay out of this PR.

## Acceptance

Every item is a design §3 MS-2a exit criterion or a pinned fixture from the section
it cites.

| # | Exit criterion (§3, MS-2a) | Proved by |
| --- | --- | --- |
| 1 | Gate green | `uv run python -m scripts.check` at every commit; F1 |
| 2 | Migrations apply from empty and are hypertable-verified | F1; C1 hypertable assertion on the `tests/db/test_market.py:43` pattern |
| 3 | `retention_constraints("listing_score")` proves an eBay-classed row **must** carry `expires_at` and a `merchant_fact` row **must not** | C1 |
| 4 | The six §2.3(b) conditional-clearing tests pass, led by the SA-002 regression | D1 |
| 5 | A released `CohortBaseline` version refuses `UPDATE` through `save()`, `QuerySet.update()`, `bulk_update()`, the admin and raw SQL under the runtime role — the refusal coming from the §3.3.5(a) **database trigger** on the bulk and raw paths — while `INSERT`, an uncited `DELETE` and a pointer `UPDATE` all still succeed | C2 |
| 6 | A duplicate `(cohort_key, baseline_digest)` is rejected; deleting a cited version raises `ProtectedError` | C2; C1 |
| 7 | `CohortBaselineCurrent.current_version` and `next_window_exit_at` accept NULL and `price_event_digest` does not (§3.3.5i, SA-002) | C2 |
| 8 | `Listing.current_offer_observed_at` and `ListingScore.offer_observed_at` persist with the §2.1 nullability, and `offer_observed_at` is absent from `inputs_json` (SA-001) | B1; C1 |
| 9 | The three §3.3.5(c) CHECKs reject a `cohort`-basis score with a NULL baseline, a `capacity_unavailable` score carrying a baseline or a cohort key, and both directions of the `offer_observed_at` biconditional; each neutral path is accepted (S2-26) | C1 |
| 10 | `ingested_at` is assigned by the database, cannot be supplied by `append_snapshot`, and the migration backfills it from `observed_at` with `ingest_stamp_backfilled` set (§2.2.2, S2-24) | B2 |
| 11 | `scoring_inputs_changed_at` advances for every `SCORING_INPUT_FIELDS` write and for no other | B1 |
| 12 | `lock_parents_for_delete` asserted per model against §3.4.2's lock order, with a `pg_locks` inspection inside the sweep's transaction proving the seller lock is held | D3 |
| 13 | Fixture 12 (`P`-entry phantom) at whole-table level — no `listing_score` row anywhere cites the deleted rating's `observed_at` — **and** the same assertion goes red against a listing-only hook | D4 |
| 14 | A `ScoringRun` persists with no source while a sourceless `ScraperRun` still raises | B4 |
| 15 | `makemigrations --check` clean | F1 |
| 16 | No scoring math; no observable behavior change | Review of the diff: no `scoring/` module beyond `contracts.py`, no `poller/service.py` change, no adapter change, no `SourceConfig.enabled` flip |

Additional acceptance carried from the sections MS-2a is assigned in:

- Retention registry tests are registry-derived and mirror
  `tests/unit/test_purge_registry.py` (E1).
- Neutral-path column values persist as SA-027's MS-2a half requires: `n_eff = 0` and
  `lambda_shrinkage = 0` as **stored zeros** on all three neutral `price_basis` values
  (C1). The byte-identical explanation reproduction half of SA-027 is MS-2d.
- The §3.3.2 parent-tier map is landed by the `MarketTier` migration and is total,
  with every parent strictly widening (B3).

## Open at plan time

Each item is something the design does not settle for MS-2a. **None may be closed by
inventing design** — take them to the owner or to the OQ/Deviations process (§7)
before the task that needs them.

1. **Retention class of a non-eBay `SellerRatingObservation` row** (§3 MS-2a bullet 3,
   §3.2.5, S2-2a). The design fixes only "eBay-sourced rows carry
   `ebay_listing_observation` (IR-002)". The DR-001 CHECK pair requires *some* class
   on every row, and §2.1 notes eBay is the only live rating input among the top five,
   so a non-eBay row may be unreachable today — but the column still needs a rule.
2. **How `scoring_inputs_changed_at` advances on the delist/redaction paths** (§2.1,
   §2.3b). §2.1 says the stamp is "advanced by `Listing.save()` … plus the
   delist/redaction paths", yet `redact_expired()` is a bulk
   `kept.update(**REDACTED_CONTENT_FIELDS)` (`market.py:454,480`) that bypasses
   `save()` entirely, and `mark_delisted()` saves `delisted_at`/`delist_reason`, which
   are not `SCORING_INPUT_FIELDS` members. Whether those two paths must set the stamp
   explicitly, and with which value, is not stated.
3. **`quantity` / `quantity_basis` on the `capacity_unavailable` path** (§3, §3.2.2).
   §3 makes every unlisted-as-nullable column NOT NULL, which includes both; §3.2.2's
   exhaustive table gives the capacity-unavailable row "—" for both and §3.4.1's
   group-4 neutral table does not cover them. The stored value on that path is
   undefined.
4. **The field membership of `scoring/contracts.py` in MS-2a** (§2 module layout).
   §2 names the five Pydantic models; their field sets are fixed only indirectly by
   MS-2b/MS-2c sections (§3.2.1, §3.2.6, §3.3.1, §3.3.4, §3.4.1 group 4). How much of
   that shape MS-2a must land — versus a minimal façade MS-2b widens — is not stated,
   and widening it later is cheap only while no released version imports it (§3.4.3
   item 1 forbids that import outright).
5. **What plays the refresh's part in MS-2a's fixture 12** (§3 MS-2a exit,
   §3.4.2 fixture 12). The criterion requires "the refresh must block on the `seller`
   anchor" in a sub-milestone whose own scope excludes the refresh (MS-2d). D4 uses a
   test-only stand-in taking exactly §3.4.2(3)'s refresh lock order; that is a harness
   derivation, not a design statement, and it is recorded here so the plan review can
   confirm or replace it.

## Deliberately excluded MS-2a mentions

Grep hits for "MS-2a" in the design that this plan does **not** turn into a task,
with why:

- §3.2.6 / §3.4.1 group 4 (lines 662, 1197, 1214) — these settle a *contradiction*
  MS-2a "could not choose a column nullability" for. The plan carries the resolution
  as C1's NOT NULL/nullable rules; the four-combination provisional matrix and
  `s_price = 0.5` are MS-2b table-driven tests (§3, MS-2b exit).
- §3.2.6 line 666, SA-027's `suggested_validation` "MS-2a/MS-2d" — the stored-column
  half is C1; the `inputs_json` group-4 payload and byte-identical explanation
  reproduction through §3.4.3 dispatch need `compose`/`explain`/`dispatch`, which are
  MS-2b/MS-2d.
- §3.3.3 line 773 — MS-2a adds `Listing.extracted_attrs_json` (B1); **MS-2c** writes
  it from `matching/resolver.py`. No resolver change here.
- §3.4.1 coverage rows (lines 1283, 1370) — "MS-2a asserts the class" for
  `drive_spec` / `product_model` / `product_variant` being `merchant_fact`. Covered by
  E1's registry-derived CHECK-pair property rather than by a bespoke test; the trigger
  and candidate-clause behavior around them is MS-2d.
- §2.2.2 tests (lines 243–244, 259) — the *field* half is B2; the B1 high-water
  trigger, the two insert shapes and the negative control are explicitly "MS-2d for
  the trigger".
- §2.2.1 (line 317 context) and §2.3(b) test list item 5 — the current-offer selector
  and the `source_composition` tests are MS-2c/MS-2d by their own headings; MS-2a
  carries only the columns and the clearing hooks they later exercise.
- §1.2 rows and §6/§7 narrative (lines 46, 56–82, 1601, 1603, 1646–1657) — ratification
  and audit history. They are the *precondition* for cutting this plan, not work in it.
- §3.3.5(f)/(g) prune and §3.3.5(h) measurement — the prune runs inside the
  `score-refresh` job (MS-2d) and the growth measurement is MS-2c. MS-2a lands only
  the `PROTECT` FK that makes condition 2 a database invariant.
