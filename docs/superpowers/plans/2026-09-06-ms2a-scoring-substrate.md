# MS-2a — Scoring substrate: Implementation Plan

> For the executing agent: work top to bottom within a phase; every task is TDD
> (failing test → implement → green → signed commit). Design source of truth:
> `docs/superpowers/specs/2026-09-06-ms2-scoring-design.md` (**revision 14**,
> owner-ratified 2026-09-06 — all 27 §1.2 rows `accept`). Every `S2-*` / `SA-*` ID
> and every bare `§` or `:line` below refers to that document at revision 14;
> every repository `file:line` was re-read and re-verified when this revision was
> written. Deviations from the design go to the spec Deviations Log / OQ process,
> never silently.
>
> **Review lineage:** Codex `delegate` second opinion
> `a626c2f0-5919-4fe2-a480-2c51f8f15c1b` (2026-09-06) round 1 → **revision 2**;
> pass 2 (`8755be2a`) → **revision 3**; pass 3 (`b92dd220`) → **revision 4** (this
> document). Revision 2 closed pass-1 findings 1–14 and adopted design revision 13's
> seven settled rules. Revision 3 closed pass-2 findings 10, 14, 17 and 18 and
> adopted **design revision 14**'s three settled rules for the design-owned findings
> 3, 15 and 16 — the `REDACTED_CONTENT_FIELDS` save-guard exception (B1),
> `raw_payload` as nullable `SET_NULL` (C4), and the two source-to-class retention
> triggers (C5). Revision 4 closes pass-3 findings 16 (named trigger DDL, the
> `normalized_name = 'ebay'` discriminator, parameterized UPDATE controls, the
> `pg_trigger` event-mask check), 17 (`matcher_grain` and `relaxation_step` domain
> CHECKs) and 19 (stale hold language removed from acceptance). Every finding from
> all three passes is now closed and the plan carries **no blocking open question**.

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
- Admin registration style (C2 only; see finding 14) — `src/hw_radar/catalog/admin.py:25-38` (plain
  `admin.site.register`) and `:41` (`@admin.register` + a class for anything with
  behavior).
- Registry invariants test to mirror — `tests/unit/test_purge_registry.py`.

## Phases

Phase A is DB-free and shares no file with any other phase; it may run in a parallel
worktree from the start. **Phases B and C are not file-disjoint** (finding 13): both
edit `src/hw_radar/catalog/models/__init__.py` (the model re-exports), and the
migration chain is linear, so they **run sequentially in one worktree** — B, then C,
then D. `src/hw_radar/catalog/admin.py` is touched by **C2 only** (the mandated
read-only `CohortBaseline` registration); B registers nothing (finding 14). The
shared re-export file is owned task by task in the order below; nothing else in the
plan grants concurrent write access to it. Phase D needs B and C; E and F close.

Task-size note (finding 13): each lettered task below is one migration **or** one
model **or** one hook, and the two concurrency fixtures (D4a, D4b) are separate
commits, so no task carries two schema objects. D3 and D4 are the largest and are
split accordingly.

### Phase A — `scoring/contracts.py`

Files: `src/hw_radar/scoring/__init__.py`, `src/hw_radar/scoring/contracts.py`,
`tests/unit/test_scoring_contracts.py`.

- **A1 — contracts (§2 module layout).** Pydantic v2 models `CohortKey`,
  `CohortStats`, `SubscoreSet`, `ScoreExplanation`, `ScoredListing`, mirroring
  `src/hw_radar/acquisition/contracts.py`'s style. It is a **current-version façade
  only**: §3.4.3 item 1 forbids any released `scoring/versions/v<N>/` package from
  importing it, so this module imports nothing from `scoring/` and nothing from
  Django. **Scope is settled by design revision 14 (`:99-102`): MS-2a lands the five
  *names* plus exactly the fields its own schema already fixes; MS-2b widens them
  with computed fields.** Concretely: `SubscoreSet` = the four subscore columns in
  `[0,1]` (C3's CHECKs); `CohortStats` = the persisted `n_eff` /
  `lambda_shrinkage` (never null) plus the §3.4.1 group-4 nullable `q`, `q25`, `q50`,
  `q75`, `margin_iqr`, `relaxation_step`, `relaxation_exhausted`, `baseline_digest`;
  `CohortKey` = the `cohort_key` string plus its `price_basis` companion;
  `ScoredListing` = the `ListingScore` column set of C3; `ScoreExplanation` = the
  `explanation_json` / `explanation_text` pair. Nothing computed, nothing MS-2b owns.
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
`0021_scoring_run_seller_prior.py`,
`src/hw_radar/refdata/persist.py` (B3's loader normalization only),
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
  `src/hw_radar` is `src/hw_radar/acquisition/scheduling/checkpoint.py:17
  save_buckets`, a module function), advancing `scoring_inputs_changed_at` when a
  member is written — full save **or** `update_fields` — **and on nothing else.**
  **The delist and redaction paths deliberately do *not* advance it** (design
  revisions 13–14, §2.1 `:170` and the §2.3(b) rows at `:291-292`): revision 5's "plus
  the delist/redaction paths" is withdrawn as an error. `redact_expired()` is a bulk
  `QuerySet.update()` that bypasses `save()` entirely (`market.py:453-480`), so a
  `save()`-driven stamp could not fire there in any case; and it does not need to,
  because both paths remove the listing from `P` and §2.3(b) clears `current_*` in
  the same transaction, so re-entry is carried by **C1**
  (`current_scored_at IS NULL`, unconditional), which is strictly stronger than C3's
  stamp comparison. D1 owns the five `current_*` columns on those paths; B1 owns only
  the stamp.
  Tests (`test_scoring_listing_fields.py`): the stamp advances on a full save that
  changes a member and on `save(update_fields=["seller"])`; it does **not** advance
  on `save(update_fields=["last_seen"])`, on a member-free full save, on
  `mark_delisted()`, or on the bulk `redact_expired()` path — the last two asserted
  explicitly rather than assumed, because `redact_expired()` blanks
  `extracted_attrs_json`, which *is* a `SCORING_INPUT_FIELDS` member (`:292`); the
  five `current_*` columns persist and accept NULL; `extracted_attrs_json` blanks to
  `{}` through `redact_expired()`; `current_offer_observed_at` round-trips a
  timezone-aware value.
  **The one exception, settled in design revision 14 (`:170`, `:291`; pass-2
  finding 3).** `mark_delisted()` reaches `save()` through
  `redact_merchant_content()` (`market.py:379-401`) for a `DELETE_ON_DELIST_CLASSES`
  listing, and that save writes `extracted_attrs_json` — a member of **both**
  `REDACTED_CONTENT_FIELDS` and `SCORING_INPUT_FIELDS` — so an unqualified guard
  would advance the stamp on exactly the path §2.1 says must not advance it. The
  guard therefore advances the stamp on a `SCORING_INPUT_FIELDS` write **except when
  `update_fields` is exactly the `REDACTED_CONTENT_FIELDS` key set** — the redaction
  signature every redaction caller uses. The comparison is on the **declared set**,
  not on the written values: a value-level test would also suppress a genuine write
  that happened to equal the blank, and would need re-deriving whenever
  `REDACTED_CONTENT_FIELDS` changes.
  The fixture for this **must be an eBay / delete-on-delist listing**, because a
  class outside `DELETE_ON_DELIST_CLASSES` never reaches the redaction call and would
  pass vacuously. Tests: `mark_delisted()` on a delete-on-delist listing leaves the
  stamp unchanged; a direct `redact_merchant_content()` call leaves it unchanged; a
  save whose `update_fields` is a **strict subset** of `REDACTED_CONTENT_FIELDS`
  containing `extracted_attrs_json` **does** advance it (the exception is exact, not
  a prefix); and a genuine `extracted_attrs_json` write outside the redaction
  signature advances it even when the new value equals the blank.
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
  map.** Migration `0020` (`AlterField` + `RunPython` data migration + a database
  CHECK). Values `enterprise` / `nas` / `surveillance` / `consumer` / `unknown`
  (§2.1). The data migration normalizes existing free text; an unrecognized spelling
  becomes `unknown`, which is the value §2.1 already assigns to an unclassifiable
  listing ("tier is not extractable from a title … `unknown` by construction").
  **Django `choices` are not a database constraint and the loader is not typed
  (finding 6):** `SeedSpec.market_tier` is a bare `str = ""`
  (`src/hw_radar/refdata/contracts.py:72`, in `SeedSpec` at `:56-74`) and
  `refdata/persist.py:205-208` writes it straight into
  `DriveSpec.objects.update_or_create(..., defaults=spec_defaults)`, so a future
  import would reinstate free text after the migration. B3 therefore lands **both**
  halves: a `CheckConstraint` on `market_tier ∈ MarketTier.values` (the enforcement)
  and a loader-side normalization in `refdata/persist.py` mapping an unrecognized
  spelling to `unknown` before the `update_or_create` (the ergonomic half, so an
  import fails cleanly rather than at the CHECK). `refdata/contracts.py` keeps its
  `str` shape — narrowing the Pydantic type is a refdata-schema change this
  sub-milestone does not own.
  **Where the parent map lives.** §3.3.2 (`:768`) calls it "a versioned constant
  under S2-6; landed by the §2.1 `MarketTier` migration", while §3.4.3 item 4
  (`:1528`) lists "the parent-tier map" in the constant set a **released version**
  owns — i.e. `scoring/versions/v1/`, which is MS-2b. This plan takes the reading
  that avoids two authoritative copies: **B3 lands the map as inert data in the
  `0020` migration module** (`enterprise|nas|surveillance → high_duty`,
  `consumer|unknown → client`) so §3.3.2's "landed by" is satisfied and the values
  are auditable at the point the enum is created, and **MS-2b's `versions/v1/`
  constant is the copy the ladder reads**, with an MS-2b task asserting the two
  agree. See the non-blocking reconciliation recorded under "Open at plan time".
  Tests (`test_market_tier.py`): a round-trip through the migration maps each seeded
  spelling to its enum member and an unrecognized spelling to `unknown`; the CHECK
  **rejects** an off-enum value written by raw SQL (the negative control that a
  `choices=` list cannot give); a post-migration `import_refdata` run carrying an
  unrecognized `market_tier` lands `unknown` and leaves the CHECK satisfied
  (the loader negative test finding 6 asks for); the parent map is total over
  `MarketTier.values` and each parent has ≥ 2 members, derived from the map itself
  rather than hardcoded. **No ladder logic — `scoring/cohort.py` is MS-2b/MS-2c.**
- **B4 — `ScoringRun` (§3.4.4, S2-19) + `SourceConfig.seller_policy_prior`
  (S2-12).** Migration `0021`. It adds the **column and its `0.50` default only**:
  seeding eBay's row to `0.60` is expressly an **MS-2e** deliverable
  (design `:1600`, "`seller_policy_prior` seeding (S2-12)", and the MS-2e row at
  `:347`), so no `RunPython` seeding and no eBay-value test lands here (finding 11).
  §2.1 (`:174`) states the target values; MS-2e applies them.
  `ScoringRun` is a plain `models.Model` in `ops.py` beside `ScraperRun`,
  **not** `RetentionGoverned` (it holds counts, not merchant content), with exactly
  the §3.4.4 field table — `started_at`, `finished_at`, `status` (reusing
  `RunStatus`, `ops.py:77`), `failure_class` (reusing `RunFailureClass`, `:83`),
  `algorithm_version`, `cohorts_evaluated`, `cohorts_refreshed`, `baseline_writes`,
  `listings_rescored`, `score_writes`, `baseline_versions_pruned`, `error`,
  `detail_json` — `db_table = "scoring_run"`, one index on `("-started_at",)`, **no
  `source_site` field and no run-kind discriminator**. `RunKind.SCORE` is **not**
  added. **`ScoringRun` is *not* registered in the admin** (finding 14): §3.4.4
  (`:1538-1573`) says nothing about it, unlike §3.3.5(a), which mandates the
  read-only `CohortBaseline` registration C2 lands. An unrequested registration is
  both scope the design did not adopt and an observable operator surface, which the
  "no observable behavior change" constraint (§3 `:384`) forbids; if operator parity
  with `ScraperRun` is wanted later it arrives with the job that writes the rows
  (MS-2d). B4 therefore touches no admin file. Tests
  (`test_scoring_run.py`): a `ScoringRun` persists with no source at all and defaults
  the six counts to 0; the **negative control** — constructing a `ScraperRun` without
  `source_site` still raises (§3.4.4 test 3); `seller_policy_prior` exists on every
  `SourceConfig` row and defaults to `0.50` — **including eBay's**, whose `0.60` is
  MS-2e's to write.

### Phase C — `catalog/models/scoring.py`

Files: `src/hw_radar/catalog/models/scoring.py`,
`src/hw_radar/catalog/models/__init__.py`, `src/hw_radar/catalog/admin.py`,
`src/hw_radar/catalog/migrations/0022_cohort_baseline.py`,
`0023_cohort_baseline_immutable.py`, `0024_listing_score.py`,
`0025_seller_rating_observation.py`, `0026_retention_source_triggers.py`,
`tests/db/test_cohort_baseline.py`, `tests/db/test_retention_source_triggers.py`,
`tests/db/test_listing_score_model.py`,
`tests/db/test_seller_rating_observation.py`.

**Order matters and finding 1 is why:** `ListingScore.cohort_baseline` is a
`PROTECT` FK to `CohortBaseline`, and a linear Django chain cannot resolve a model a
*later* migration introduces. The referenced model therefore lands **first** —
`CohortBaseline` in `0022`, its trigger in `0023`, `ListingScore` in `0024`,
`SellerRatingObservation` in `0025`, and the two source-to-class triggers of C5 in
`0026` — last, because each trigger reads a table that must already exist.
- **C1 — the two baseline tables (§3.3.5a/b/e, S2-20).** Migration `0022`, models
  only. `CohortBaseline`: `UniqueConstraint(cohort_key, baseline_digest)`, columns
  `cohort_key`,
  `baseline_digest`, `computed_at`, `price_event_count`, `oldest_event_at`,
  `anchor_event_at`, `n_eff`, `q25`, `q50`, `q75`, `observation_vector`,
  `source_composition`, `window_days`, `half_life_days`, `relaxation_step`,
  `relaxation_exhausted`, `algorithm_version`. **No detection stamp and no
  `raw_snapshot_count` on this table** (SA-025). `CohortBaselineCurrent`: PK
  `cohort_key`, `current_version` FK (`PROTECT`, **`null=True`**),
  `last_evaluated_at`, `last_promoted_at`, `ingest_high_water`,
  `next_window_exit_at` (**nullable**), `price_event_digest` (**NOT NULL**),
  `member_projection_digest`, `raw_snapshot_count`, `source_raw_snapshot_counts`.
  **Both tables are `RetentionGoverned`** (finding 7) — §3's "plain table" (`:350`)
  means *not a hypertable*, in contrast with `ListingScore`, and not *ungoverned*:
  §3 (`:352-353`)
  gives each `retention_class = merchant_fact`, indefinite, and §3.3.5(e) (`:1054`)
  restates it for the version while the pointer "inherits the same class". They
  therefore inherit `RetentionGoverned` (`base.py:99`) and **must** declare
  `retention_constraints("cohort_baseline")` /
  `retention_constraints("cohort_baseline_current")` and
  `retention_indexes("cohort_baseline_expires")` /
  `retention_indexes("cohort_baseline_current_expires")`, because
  `tests/unit/test_purge_registry.py:124-164` asserts the partial index and the CHECK
  pair for **every** model in `retention_governed_models()` and would otherwise go red
  the moment these models land. `retention_class` defaults to `merchant_fact` on both
  models and `expires_at` stays NULL, which the `_retention_ttl_coherent` CHECK then
  enforces — the pair is what makes "these rows are never swept" a database fact
  rather than a convention, and `purge_expired._expired` (`:150`) can never select
  them because the class filter and the NULL `expires_at` are independent guards.
  Tests (`test_cohort_baseline.py`, C1's half): a version and a pointer persist with
  `merchant_fact` / NULL `expires_at`; setting `expires_at` on either is **rejected by
  the CHECK**; a duplicate `(cohort_key, baseline_digest)` is rejected;
  `current_version` and `next_window_exit_at` accept NULL while `price_event_digest`
  does not, so the §3.3.5(i) baseline-void state is representable and its
  empty-projection digest is not confusable with an absent one (SA-002); and the
  `relaxation_step` domain CHECK rejects an off-enum value inserted by **raw SQL**
  (pass-3 finding 17), which is the only chance this table ever gets — the C2 trigger
  makes the row immutable, so a malformed step cannot be repaired in place.
- **C2 — the immutability trigger and the read-only admin (§3.3.5a, SA-003).**
  Migration `0023`: a `RunSQL` pair creating `cohort_baseline_no_update()` and the
  `BEFORE UPDATE ... FOR EACH ROW` trigger `cohort_baseline_immutable` exactly as
  §3.3.5(a) writes them, with a reversing `DROP` in **trigger-then-function** order
  (dropping the function first fails while the trigger depends on it) — the
  `0001`/`0003`/`0007`/`0010` RunSQL precedent. A `CohortBaseline.save()` override
  refuses updates in Python with a message naming §3.3.5(a) — defence in depth and
  the error message, **not** the guarantee — and `django.contrib.admin` registers the
  model read-only (`has_add_permission`, `has_change_permission`,
  `has_delete_permission` all `False`, via the `@admin.register` class form at
  `admin.py:41`). Tests (`test_cohort_baseline.py`, C2's half), which are SA-003's
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
  an `UPDATE` of the `cohort_baseline_current` pointer succeeds. Forward **and
  reverse** migration tests: `0023` applies, the trigger and function exist
  (`pg_trigger` / `pg_proc`), and `migrate catalog 0022` drops them in
  trigger-then-function order without error (finding 1's second half).

- **C3 — `ListingScore` (§3, §3.3.5c, S2-1, S2-2a).** Migration `0024` (after C1/C2,
  so the FK target exists): the model, then a `RunSQL`
  `create_hypertable('listing_score', by_range('scored_at', INTERVAL '1 month'))`
  with `reverse_sql=migrations.RunSQL.noop` on the `0003` precedent.
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
  `inputs_json` group 4.
  **`quantity` and `quantity_basis` are NOT NULL on every path, neutral paths
  included** (design revision 14 `:461`, `:1247`): revision 12's `—` cells in
  §3.2.2's last row were a documentation error, not a nullability exception.
  `matching/vocab.py` `_quantity(title)` is a pure title-regex read that never
  consults capacity, so a `capacity_unavailable` row still carries the ladder's own
  outcome — `1` / `default_single` / the `quantity_assumed` flag when the title bears
  no quantity signal. **No nullability, no sentinel, and no fifth `quantity_basis`
  value.**
  The three §3.3.5(c) CHECKs land here verbatim:
  `(cohort_baseline_id IS NULL) = (price_basis IN ('capacity_unavailable',
  'price_unavailable', 'no_cohort_evidence'))`, `(cohort_key IS NULL) = (price_basis =
  'capacity_unavailable')`, `(offer_observed_at IS NULL) = (price_basis =
  'price_unavailable')`.
  **Range enforcement is database-level and explicit** (finding 9): §3 names
  `deal_score` 0–100 and `base_score` / the four subscores / `cap_applied` in `[0,1]`,
  and a plan that only names a range lets a migration omit it silently. C3 adds
  `CheckConstraint`s — `deal_score BETWEEN 0 AND 100`, `base_score BETWEEN 0 AND 1`,
  one per subscore, and `cap_applied IS NULL OR cap_applied BETWEEN 0 AND 1` (§3.2.1:
  NULL when no veto fired) — plus the matching Django validators for form-level
  feedback.
  **Every persisted closed-vocabulary scoring enum also gets a database domain
  CHECK** (finding 17), for the same reason B3 gives `market_tier` one: Django
  `choices` are validated only by `full_clean()`, which no writer on these paths
  calls, so an off-enum value would reach the table — and for `price_basis` that is
  not merely untidy, it **defeats the three §3.3.5(c) biconditionals**, since a value
  outside the four-member vocabulary satisfies every one of them with all three
  related columns non-NULL. C3 therefore adds `price_basis IN ('cohort',
  'capacity_unavailable', 'price_unavailable', 'no_cohort_evidence')` (§3.2.6's four
  values), `quantity_basis IN ('structured', 'structured_over_extracted',
  'extracted', 'default_single')` (§3.2.2's table), and **`matcher_grain IN ('none',
  'family', 'model', 'variant')`** — the `ResolutionGrain` values
  (`base.py:117-124`), whose string values §C.3.3 shares verbatim with
  `matching.types.Grain`, so the CHECK is written from that enum rather than
  hand-listed; C1 adds **`relaxation_step IN ('none', 'condition', 'capacity',
  'tier')`** on `CohortBaseline` (§3 `:352`), which matters more there than anywhere
  else because the row is immutable — a malformed step can never be corrected in
  place, only pruned; C4 adds `count_basis IN ('total_count', 'net_score_proxy',
  'unavailable')` (§3.2.5). That is **every** persisted closed-vocabulary scoring
  enum this milestone creates (pass-3 finding 17); `retention_class` is excluded
  because `retention_constraints()` already constrains it and C5's triggers pin its
  source mapping. Each is asserted by a
  **raw-SQL insert** of an off-enum value that must raise `IntegrityError` — a test
  going through the ORM alone would prove nothing, because the ORM never validates
  either.
  Tests (`test_listing_score_model.py`): `listing_score` is a hypertable (the
  `timescaledb_information.hypertables` query of `tests/db/test_market.py:43`);
  `retention_constraints("listing_score")` proves an `ebay_listing_observation` row
  **must** carry `expires_at` and a `merchant_fact` row **must not** (both
  directions); one row per neutral `price_basis` value persists with the exact stored
  FK (NULL), `cohort_key` (NULL only for `capacity_unavailable`), `n_eff = 0` and
  `lambda_shrinkage = 0` **as stored zeros** (SA-027's MS-2a half).
  **The CHECK tests are a truth table, not four spot checks** (finding 8): for each
  of the four `price_basis` values × each of the three constrained columns
  (`cohort_baseline_id`, `cohort_key`, `offer_observed_at`) × {NULL, non-NULL}, assert
  accept or reject **against the database**, so the parametrization covers the cases
  revision 1 missed — a non-NULL baseline FK on `price_unavailable` and on
  `no_cohort_evidence`, and a NULL `cohort_key` on `cohort`, `price_unavailable` and
  `no_cohort_evidence`. The twelve accepting cells are the row shapes §3.3.5(c) and
  §3.4.1 group 4 fix; every other cell must raise `IntegrityError`.
  Range tests: `deal_score` accepts 0 and 100 and rejects −1 and 101; `base_score`
  and each subscore accept `0` and `1` and reject just outside; `cap_applied` accepts
  NULL, 0 and 1 and rejects 1.0001 — each rejection asserted at the database, so a
  validator-only implementation goes red.
  Also: `offer_observed_at` is absent from `inputs_json` (SA-001); deleting a cited
  `CohortBaseline` raises `ProtectedError`.
- **C4 — `SellerRatingObservation` (§3 `:354`, §3.2.5, S2-21).** Migration `0025`.
  Append-only, `RetentionGoverned`, `retention_constraints` /`retention_indexes`,
  `UniqueConstraint(seller, observed_at)`. Fields per §3.2.5: `seller` FK,
  `observed_at`, `source_site`, `rating_percent_raw` and `feedback_count_raw` (both
  nullable, stored verbatim as received), `p_obs` (`numeric(5,4)`), `n`
  (`PositiveIntegerField`), `y` (`numeric(12,4)`), `count_basis`
  (`total_count` / `net_score_proxy` / `unavailable`), `is_usable` (bool),
  `raw_payload` FK — **nullable `SET_NULL`, taking `OfferSnapshot.raw_payload`'s
  shape verbatim** (`market.py:545-551`; design revision 14 `:372`, pass-2
  finding 15). The reason is a sweep-ordering fact: `purge_expired` walks models in
  `_meta.label` order (`purge_expired.py:98-108`), so `RawPayload` is swept **before**
  `SellerRatingObservation`. `PROTECT` would abort the sweep — an undeletable expired
  payload raises and the batch loop's stall check turns it into a `CommandError` that
  stops the whole retention pass — and `CASCADE` would delete rating rows out from
  under the **L9** hook, which is dispatched on `SellerRatingObservation`'s own delete
  and would never see them. `SET_NULL` drops the provenance pointer, keeps the rating
  row on its own class's clock, and leaves L9 firing only on the rating's own
  deletion; nothing a score needs is lost, because §3.2.5 stores the normalized
  `p_obs` / `n` / `y` in group 3 precisely so a re-derivation never re-reads a raw
  payload.
  **The retention class is per row and follows the row's `source_site`** — the S2-2a
  rule applied to seller evidence (`:354`): an eBay-sourced row carries
  `ebay_listing_observation` (IR-002) with a **non-NULL `expires_at`** on the
  six-hour clock, and a row from any of the four first-party merchant sources carries
  **`merchant_fact`, indefinite, with `expires_at` NULL**.
  **No normalization arithmetic here** — `p_obs`/`n`/`y` are written by MS-2e under
  the §3.2.5(b) contract. Tests (`test_seller_rating_observation.py`): a row persists
  and round-trips its `Decimal` values without float drift; a duplicate
  `(seller, observed_at)` is rejected; **the coherence CHECK is asserted per class** —
  a row classed `ebay_listing_observation` without `expires_at` is rejected and one
  with it accepted, a `merchant_fact` row with `expires_at` is rejected and one
  without it accepted (that is the *class* half; the *source* half is C5's trigger);
  **the `SET_NULL` behaviour is asserted end to end** — sweep an expired `RawPayload`
  cited by a live rating and assert the rating survives with `raw_payload_id IS
  NULL`, the sweep exits zero, and **no** L9 fan-out ran (revision 14 `:372`);
  `count_basis` refuses an off-enum value **at the database**, asserted by a raw-SQL
  insert that must raise `IntegrityError` rather than by an ORM path that never
  validates `choices` (finding 17; the CHECK itself is listed with C3's enum
  domains).
- **C5 — the source-to-class retention triggers (design revision 14 `:354-358`,
  `:368`; pass-2 finding 16).** Migration `0026`. The generic `RetentionGoverned`
  pair is kept for TTL coherence and **cannot** carry this rule:
  `retention_constraints()` emits exactly "class non-empty" and "class agrees with
  `expires_at` nullability" (`base.py:53-74`), and neither reads `source_site`, so an
  eBay-sourced rating carrying `merchant_fact` + NULL `expires_at` — indefinite
  retention of eBay-derived seller evidence, the exposure §2.3 exists to prevent —
  passes every constraint on the table. Two `BEFORE INSERT OR UPDATE` triggers are
  added in the **same RunSQL-with-reversing-DROP shape** §3.3.5(a) uses for
  `cohort_baseline_immutable`, dropped trigger-then-function:

  | DDL name (function → trigger) | Table | What it derives, and from what |
  | --- | --- | --- |
  | `seller_rating_retention_class()` → `seller_rating_retention_class_check` | `seller_rating_observation` | joins `NEW.source_site_id` to its `source_site` row and rejects unless `retention_class` = `ebay_listing_observation` when that row is eBay, `merchant_fact` otherwise |
  | `listing_score_retention_class()` → `listing_score_retention_class_check` | `listing_score` | reads `NEW.listing_id` and rejects unless `retention_class` equals that listing's own class — the S2-2a "a score inherits its listing's class" rule, which `retention_constraints("listing_score")` can no more see than the pair above can see a source |

  The names are pinned here because three things reference them: the reversing
  `DROP TRIGGER … ; DROP FUNCTION …`, the tests' error-origin assertion, and F1's
  catalog check.
  **The eBay discriminator is `source_site.normalized_name = 'ebay'`, and it is
  fail-closed** (pass-3 finding 16b). `SourceSite` offers several candidate columns
  (`market.py:39-48`); `normalized_name` is the one that is **unique** (`:43`) and the
  one migration `0005_seed_sources.py:55` seeds as `'ebay'`, whereas `source_type` is
  `marketplace` — a class that a future non-eBay marketplace would also carry, which
  would silently widen the eBay rule to sources it was never argued for. The design
  writes the rule as `required_class(source)` (`:358-363`) without naming the column,
  so this plan names it: the trigger's derivation is *"`ebay_listing_observation` when
  `normalized_name = 'ebay'`, `merchant_fact` otherwise"*, which fails **closed** —
  an unrecognized or renamed source demands `merchant_fact`, the indefinite class,
  and any eBay-derived row that lost its discriminator is refused rather than
  silently retained forever.
  It reads the row's **own** `source_site_id` rather than a denormalized `is_ebay`
  boolean: the design rejects the boolean because nothing keeps a second copy of that
  fact true across a re-attribution, and rejects writer-side enforcement for SA-003's
  reason — an enumeration of supported writer paths has to be maintained forever, and
  `redact_expired()` already proves this codebase writes outside it.
  Tests (`tests/db/test_retention_source_triggers.py`), **all written as raw SQL so
  they prove the database refuses and not the model**: an eBay-sourced
  `seller_rating_observation` inserted `merchant_fact` + NULL `expires_at` is
  rejected; a first-party one inserted `ebay_listing_observation` + a TTL is rejected;
  the two mirror cases on `listing_score` against an eBay listing and a
  `merchant_fact` listing are rejected; the **four matching combinations are
  accepted**, so the triggers are proven not over-broad; and a `bulk_create` is
  included, because that is a path a `save()`-level rule would miss.
  **The `QuerySet.update()` controls must be parameterized, or they prove nothing**
  (pass-3 finding 16a). Updating `retention_class` alone to the wrong value leaves
  `expires_at` matching the *old* class, so the pre-existing
  `*_retention_ttl_coherent` CHECK (`base.py:61-72`) rejects the statement — and the
  test would pass green against a trigger that was accidentally `BEFORE INSERT` only.
  Each UPDATE control therefore moves **both** columns together —
  `update(retention_class=<wrong class>, expires_at=<value coherent with that wrong
  class>)`, i.e. a TTL when moving to `ebay_listing_observation` and `None` when
  moving to `merchant_fact` — so the coherence CHECK is satisfied and **only** the
  trigger can refuse. The assertion is on the refusal's origin: the error names the
  trigger function above, not a constraint name. Both tables get this control in both
  directions.
  Forward and reverse migration tests as in C2, plus an **event-mask assertion**: for
  each trigger, `pg_trigger.tgtype` proves it fires on **both** `INSERT` and
  `UPDATE`, which is the static counterpart of the parameterized control and fails
  immediately if a later edit narrows the trigger to one event.

### Phase D — the sweeper plumbing and the four `purge_expired` hooks

Files: `src/hw_radar/catalog/models/market.py`,
`src/hw_radar/catalog/models/scoring.py`,
`src/hw_radar/catalog/management/commands/purge_expired.py`,
`tests/db/test_score_clearing.py`, `tests/db/test_invalidation_hooks.py`,
`tests/db/test_lock_parents_for_delete.py`, `tests/db/test_seller_anchor_phantom.py`.

- **D0 — the sweeper's hook plumbing, stated exactly (finding 2).** The plan's
  revision-1 claim that `getattr` is already a generic dispatcher was wrong, and so
  was "the only change MS-2a makes to `purge_expired`". What the command does today:
  `_deletion_exempt_q` resolves **one** named hook (`purge_expired.py:127`),
  `_redact_expired` resolves **one** other (`:144`), and the batch loop
  (`:213-221`) is `for pks in _batches(...)` → `with transaction.atomic():` →
  `_deletable(model, now).filter(pk__in=pks).delete()`. **`QuerySet.delete()` returns
  counts, not keys**, and the DELETE deliberately re-applies the whole predicate
  (`:215-220`), so a child row whose `expires_at` was pushed forward between the
  SELECT and the DELETE **survives** — dispatching the initially selected `pks` would
  clear a pointer whose evidence still exists. MS-2a therefore adds, inside the
  existing `transaction.atomic()` and in this order:

  1. `lock_parents_for_delete(pks)` (D3), before anything else in the block;
  2. a **key snapshot** — one `values_list` over the batch's rows for the fields the
     invalidation hooks need, taken *before* the DELETE because the rows are gone
     afterwards. The field tuple comes from an optional class attribute
     `INVALIDATION_KEY_FIELDS`, defaulting to the model's pk, so `OfferSnapshot` and
     `ListingScore` declare nothing (their `CompositePrimaryKey` **is**
     `(listing_id, observed_at)` / `(listing_id, scored_at)`,
     `market.py:513`) and only `SellerRatingObservation` declares
     `("seller_id", "observed_at")` (§3.4.1 `:1195` gives the hook that pair).
     **The two projections are not the same call, and the difference is
     load-bearing** (finding 18): the default is
     `values_list("pk", flat=True)` — the same `flat=True` `_batches()` already uses
     for exactly this reason (`purge_expired.py:171-182`), which yields a composite
     pk as the bare `(listing_id, observed_at)` tuple the §3.4.2(5) hook expects —
     while a declared multi-field key is `values_list(*INVALIDATION_KEY_FIELDS)`,
     which yields the same flat-tuple shape per row. Dropping `flat=True` from the
     default would wrap each composite key in a one-tuple and the hooks would silently
     match nothing;
  3. the existing DELETE, unchanged;
  4. a **survivor re-query** — `model._default_manager.filter(pk__in=pks)` — so the
     **actually deleted** key set is the snapshot minus the survivors' keys. This is
     the set the hooks receive, never the selected `pks`;
  5. `getattr`-dispatch of `clear_parent_denorm` / `invalidate_listing_scores` /
     `invalidate_seller_scores` with that set, when the model declares one, still
     inside the same transaction.

  The dispatch mechanism is the one the design names ("dispatched … the way it
  already dispatches `deletion_exempt_q` and `redact_expired`", §3 `:374`), and the
  sweeper still gains **no per-model special case** — but it does gain the five steps
  above, which is more than §3's "the only change MS-2a makes to `purge_expired`
  itself" (`:383`) reads on its face; that sentence is about the *lock* hook's
  asymmetry, and this plan records the wider dispatch surface rather than
  contradicting it silently. Tests (`test_invalidation_hooks.py`, D0's half): a
  batch in which one selected row is refreshed past its expiry between SELECT and
  DELETE dispatches the hook with **only** the truly deleted keys, and the refreshed
  row's pointer is untouched; a model declaring no hook is swept exactly as before;
  and an **exact-shape assertion** on what each hook receives (finding 18) — a
  composite-pk model's hook gets `(listing_id, observed_at)` two-tuples of the
  declared column types, `SellerRatingObservation`'s gets `(seller_id, observed_at)`
  two-tuples, and neither receives one-tuples or model instances.
- **D1 — the §2.3(b) clearing contract.** `Listing.mark_delisted()`
  (`market.py:330-377`) clears the five `current_*` columns in the same transaction
  as the delist mark, for **every** delist regardless of retention class (the listing
  has left `P`, §2.2). `Listing.redact_expired()` (`:453-480`) clears them **inside
  its own bulk `QuerySet.update()` call**, not through a model hook that a bulk path
  would never run (design revision 14 `:292`).
  **The redaction queryset must be widened (finding 12).** Today it ends with
  `.exclude(**cls.REDACTED_CONTENT_FIELDS)` (`:475`) — "already-blank rows are
  skipped" — so a row redacted before MS-2a, or on an earlier sweep pass, is filtered
  out and would keep a stale non-NULL pointer forever. The predicate becomes "content
  not yet blank **or** any of the five `current_*` columns not yet NULL", so the pass
  is still idempotent (its fixed point is both halves clean) and still skips fully
  repaired rows.
  `ListingScore.clear_parent_denorm(deleted_keys)` is a classmethod taking the
  **actually deleted** `(listing_id, scored_at)` pairs from D0 and issuing, per pair,
  exactly the §2.3(b) guarded update — the five columns nulled `WHERE id =
  %(listing_id)s AND current_scored_at = %(scored_at)s`. **The `AND current_scored_at
  = …` predicate is the whole mechanism**; the withdrawn
  `clear_parent_denorm(listing_ids)` signature must not reappear.
  Tests (`test_score_clearing.py`) — the six §2.3(b) tests, led by the
  SA-002 regression: (1) an eBay listing with an **expired old** score row and a
  **newer unexpired** row that `current_*` points at — sweep, assert the old row is
  deleted and the pointer still identifies the newer row unchanged; (2) the pointer's
  own row expires — sweep, assert the row is gone and all five `current_*` are NULL;
  (3) interleaving — with a batch already selected, write a newer score row and
  repoint `current_*`, then run the clear: it no-ops, and running it twice is
  idempotent; (4) delist a scored listing and assert the clear and the mark cannot be
  observed separately (assert on the post-commit row); delisting clears
  **unconditionally**, and `scoring_inputs_changed_at` is asserted **unchanged**
  (design revision 14 `:291`); (5) **the redaction path** — design revision 14
  (`:332`) replaces revision 12's "`source_composition` tests in (a)", which §2.3(a)
  assigns to MS-2c, with a test MS-2a can actually run: seed a deletion-exempt
  bounded listing whose `expires_at` has passed and which carries a current score,
  run `redact_expired()`, and assert the content fields **and** the five `current_*`
  columns are blanked in the **same** bulk `update()` (post-commit row; the two
  cannot be observed separately) and that `scoring_inputs_changed_at` is
  **unchanged**. Its negative control is finding 12's: an **already
  content-redacted** row that still holds a non-NULL pointer is repaired by the same
  pass, which fails against the unwidened `.exclude(...)` queryset; (6) the negative
  control — a `merchant_fact` listing's score row survives a sweep with its
  `current_*` columns intact.
- **D2 — L8 and L9 invalidation hooks (§3.4.1, §3.4.2(5)–(6)).**
  `OfferSnapshot.invalidate_listing_scores(deleted_keys)` issues, per deleted
  `(listing_id, observed_at)` pair, the §3.4.2(5) update — the same five columns
  nulled `WHERE id = %(listing_id)s AND current_offer_observed_at = %(observed_at)s`.
  `SellerRatingObservation.invalidate_seller_scores(deleted_keys)` clears the same
  five columns for every listing in `P` of an affected seller, **guarded on the
  score's stored group-3 `observed_at` equalling a deleted row's** (§3.4.2(6)); the
  guard is cross-row, which is why D3's pre-lock is required beneath it. Both take
  D0's **actually deleted** key set — for `SellerRatingObservation` the
  `("seller_id", "observed_at")` tuples its `INVALIDATION_KEY_FIELDS` declares — and
  both run inside the same per-model transaction as the delete. Tests (`test_invalidation_hooks.py`): deleting the **selected** snapshot
  clears exactly that listing's five columns; deleting a **non-selected** snapshot
  updates nothing and costs no fan-out; a `price_unavailable` listing
  (`current_offer_observed_at IS NULL`) is **never** cleared by any deleted snapshot —
  correct, not a gap (§3.4.2(5), SA-005); deleting the seller rating a score cites
  clears that listing while a **non-selected** rating clears nothing (L9's negative
  control); the clear is idempotent under repeated batches.
- **D3 — `lock_parents_for_delete(pks)` and its three implementations (§3.4.2(2),
  SA-006/SA-009).** A fourth optional classmethod under the **same** discovery
  protocol, called inside the batch's existing `transaction.atomic()` **before**
  everything else in the block (D0 step 1), and before the `DELETE`
  (`purge_expired.py:213-221`). It is the only change MS-2a makes to the sweeper's
  **delete protocol** (§3 `:383`); D0 records the dispatch plumbing that comes with
  it. Implementations, per §3's table and §3.4.2(2):

  | Model | What it locks, in order |
  | --- | --- |
  | `OfferSnapshot` | `SELECT id FROM listing WHERE id IN (distinct listing_ids) ORDER BY id FOR UPDATE` |
  | `ListingScore` | the same listing-only lock, for the same reason |
  | `SellerRatingObservation` | `SELECT id FROM seller WHERE id IN (distinct seller_ids) ORDER BY id FOR UPDATE` **first** — the anchor — then `SELECT id FROM listing WHERE id IN (bounded fan-out) ORDER BY id FOR UPDATE` |

  A listing-only implementation for `SellerRatingObservation` is **a defect, not a
  simplification**: the affected set is the mutable predicate "listings in `P` for
  that seller", so locking its current members cannot exclude a listing that joins
  afterwards. The global order is `seller` → `listing`, ascending `id` within each
  class — the class order is the invariant stated at design `:1360`, and §3.4.2(3)
  explains why it has no cycle; no writer may take `listing` before `seller`.
  **How the lock is proved (finding 10).** `pg_locks` alone is not an
  identity-level inventory of held row locks: a granted `FOR UPDATE` row lock
  normally surfaces as the holder's `transactionid` entry plus, once someone waits, a
  `tuple` entry — so an assertion that merely finds *some* row in `pg_locks` can pass
  without establishing that the seller row is the anchor. The primary oracle is
  therefore **behavioural**: with the sweep's transaction open on connection 1, a
  second connection issues `SELECT … FROM seller WHERE id = :id FOR UPDATE NOWAIT`
  and must raise a lock-not-available error, while the same probe against an
  **unrelated** seller succeeds — that pair is what identifies the specific row.
  **The corroboration needs a third connection, and that is not a detail
  (finding 10):** `NOWAIT` raises immediately, so the probe can never *be* the
  persistent waiter `pg_locks` would show. The `pg_locks` half therefore uses a
  separate **blocking** connection — connection 3 issues the same
  `SELECT … FOR UPDATE` **without** `NOWAIT` and is left waiting — and only while it
  waits does the test read `pg_locks` joined to `pg_stat_activity`, asserting the
  waiter's ungranted entry and `pg_blocking_pids(<conn 3 pid>) == [<conn 1 pid>]`.
  The test then releases connection 1 and joins connection 3, so no fixture exits
  holding a blocked backend. That is the corroborating detail the design's exit
  criterion names (`:343`); the NOWAIT pair above remains the primary row-identity
  oracle.
  Tests (`test_lock_parents_for_delete.py`): per model, the hook is asserted against
  that lock order — for `OfferSnapshot` and `ListingScore`, `listing` rows only,
  proved by the same NOWAIT probe on a listing row and its unrelated-listing control;
  for `SellerRatingObservation`, distinct `seller` rows `FOR UPDATE` in ascending
  `id` order **before** any listing row, proved by the seller probe **and** by the
  ordering of the statements the hook issues (captured with
  `django.test.utils.CaptureQueriesContext`, so "seller before listing" is asserted
  on the SQL and not inferred); a batch spanning two sellers locks both, ascending;
  the hook is discovered by `getattr` (a model without it is unaffected, asserted
  against an existing swept model).
- **D4 — the fixture-12 `P`-entry phantom, *lock half only* (design revision 14
  `:343`).** MS-2a is where the hook is introduced and the first point at which a
  wrong implementation is detectable, so the deleter side runs here; **the whole-table
  assertion — that no `listing_score` row anywhere cites the deleted rating's
  `observed_at` — is MS-2d's**, where the real refresh exists. Revision 13 states this
  split because MS-2a lands no refresh, and "tick the refresh" in an MS-2a criterion
  would name MS-2d work.
  Two connections: seed a listing for the seller with **no snapshot**, so it is
  outside `P`; barrier — let `SellerRatingObservation.lock_parents_for_delete` select
  and lock the fan-out, then `append_snapshot` that listing into `P` on the second
  connection, then run the **lock-only stand-in**, then let the sweep's `DELETE` and
  L9 commit. The stand-in performs **only the refresh's lock sequence** — `seller`
  `FOR SHARE`, then `listing` `FOR UPDATE` — and **re-derives no `D_selected` and
  writes no score**: revision 14 is explicit that a duplicate re-derivation in an
  MS-2a test would drift from the real one, and MS-2a lands no scoring math.
  Assertions: the stand-in **blocks on the seller anchor** — not on the listing row,
  which the sweep never locked (the D3 NOWAIT-probe oracle, applied to the phantom's
  own listing to show it is *not* locked); the phantom listing is **explicitly
  asserted absent from the sweep's lock set**, so the test proves the anchor and not
  the fan-out; the stand-in unblocks only after the sweep commits, and the rating it
  would have read is gone by then. **Negative control (the assertion that makes the
  criterion meaningful):** the same test against a listing-only implementation of
  `SellerRatingObservation.lock_parents_for_delete` must go **red** — a
  monkeypatched listing-only variant the test expects to fail, since without the
  anchor the stand-in never blocks.

### Phase E — registry and retention coverage

Files: `tests/unit/test_purge_registry.py`, `tests/db/test_purge_expired.py`.

- **E1 — registry-derived retention tests.** `tests/unit/test_purge_registry.py`
  holds **seven** tests: six derive their subject from the app registry or the class
  hierarchy, and one —
  `test_registry_is_not_empty_and_includes_the_bounded_tables` (`:39-50`) — is a
  **hand-written label set** guarding the degenerate pass. Only that one needs
  editing: add `catalog.ListingScore`, `catalog.SellerRatingObservation`,
  `catalog.CohortBaseline` and `catalog.CohortBaselineCurrent` to its expected
  labels. The other six then cover the new models automatically — they must carry
  `retention_class`/`expires_at`, the partial `expires_at` index from
  `retention_indexes(...)` (`:124-140`), and the DR-001 CHECK pair from
  `retention_constraints(...)` (`:142-164`), which is exactly why C1 declares both on
  the baseline tables (finding 7). Add one **new** registry-derived property for this
  milestone: every model declaring any of the four sweep hooks
  (`clear_parent_denorm`, `invalidate_listing_scores`, `invalidate_seller_scores`,
  `lock_parents_for_delete`) is in `retention_governed_models()`, so a hook can never
  be declared on a table the sweep never visits. `ScoringRun` is deliberately not
  `RetentionGoverned` and is asserted absent from the registry. In
  `tests/db/test_purge_expired.py`, add the sweeper-side coverage the registry test
  cannot give: one end-to-end sweep over an expired `ListingScore` batch and an
  expired eBay-classed `SellerRatingObservation` batch, asserting the rows are
  deleted and that each model's D0–D3 hooks ran inside the batch transaction; plus
  the control that a `merchant_fact` `SellerRatingObservation` row and both baseline
  tables are never selected by the sweep at all.

### Phase F — integration + close

- **F1 —** Integrate A–E onto `dev` (orchestrator-only), renumber migrations if any
  leg landed out of order, and run the **full battery serially**:
  `uv run python -m scripts.check` plus
  `uv run python manage.py makemigrations --check --dry-run`, and a migrate-from-empty
  run proving `0018`–`0026` apply from a fresh database and the two hypertables and
  all three triggers exist afterwards, **by name and by event mask** — a `pg_trigger`
  query asserting `cohort_baseline_immutable`, `seller_rating_retention_class_check`
  and `listing_score_retention_class_check` are present and that the latter two carry
  both the INSERT and UPDATE bits in `tgtype` (pass-3 finding 16). Fix regressions.
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
| 2 | Migrations apply from empty and are hypertable-verified | F1; C3 hypertable assertion on the `tests/db/test_market.py:43` pattern |
| 3 | `retention_constraints("listing_score")` proves an eBay-classed row **must** carry `expires_at` and a `merchant_fact` row **must not** | C3 |
| 3b | The two source-to-class triggers (`seller_rating_retention_class_check`, `listing_score_retention_class_check`) reject an eBay-sourced rating classed `merchant_fact`, a first-party rating classed `ebay_listing_observation`, and both mirror cases on `listing_score`, while accepting the four matching combinations — proved by raw SQL, `bulk_create`, and a **parameterized** `QuerySet.update()` that moves `retention_class` and `expires_at` together so only the trigger can refuse; `pg_trigger.tgtype` proves both fire on INSERT **and** UPDATE (revision 14 `:354-370`; pass-3 finding 16) | C5 |
| 4 | The six §2.3(b) conditional-clearing tests pass, led by the SA-002 regression | D1 |
| 5 | A released `CohortBaseline` version refuses `UPDATE` through `save()`, `QuerySet.update()`, `bulk_update()`, the admin and raw SQL under the runtime role — the refusal coming from the §3.3.5(a) **database trigger** on the bulk and raw paths — while `INSERT`, an uncited `DELETE` and a pointer `UPDATE` all still succeed | C2 |
| 6 | A duplicate `(cohort_key, baseline_digest)` is rejected; deleting a cited version raises `ProtectedError` | C2; C3 |
| 7 | `CohortBaselineCurrent.current_version` and `next_window_exit_at` accept NULL and `price_event_digest` does not (§3.3.5i, SA-002) | C1 |
| 8 | `Listing.current_offer_observed_at` and `ListingScore.offer_observed_at` persist with the §2.1 nullability, and `offer_observed_at` is absent from `inputs_json` (SA-001) | B1; C3 |
| 9 | The three §3.3.5(c) CHECKs reject a `cohort`-basis score with a NULL baseline, a `capacity_unavailable` score carrying a baseline or a cohort key, and both directions of the `offer_observed_at` biconditional; each neutral path is accepted (S2-26) — **and every persisted closed-vocabulary scoring enum carries a database domain CHECK proved by a raw-SQL off-enum insert**: `price_basis`, `quantity_basis` and `matcher_grain` on `ListingScore`, `relaxation_step` on the immutable `CohortBaseline`, and `count_basis` on `SellerRatingObservation` (findings 17). Without the `price_basis` domain, an off-enum value satisfies all three biconditionals | C1; C3; C4 |
| 10 | `ingested_at` is assigned by the database, cannot be supplied by `append_snapshot`, and the migration backfills it from `observed_at` with `ingest_stamp_backfilled` set (§2.2.2, S2-24) | B2 |
| 11 | `scoring_inputs_changed_at` advances for every `SCORING_INPUT_FIELDS` write and for no other — including **not** on `mark_delisted()` (asserted on a delete-on-delist listing, which is the only class that reaches `redact_merchant_content()`) and **not** on the bulk `redact_expired()` path, under revision 14's exception for an `update_fields` equal to the `REDACTED_CONTENT_FIELDS` key set (`:170`, `:291-292`) | B1 |
| 12 | `lock_parents_for_delete` asserted per model against §3.4.2's lock order; the seller lock proved by a `FOR UPDATE NOWAIT` probe from a second connection with an unrelated-seller control, corroborated by a **third, genuinely blocking** connection whose ungranted `pg_locks` entry and `pg_blocking_pids()` name the sweep's backend (finding 10) | D3 |
| 13 | Fixture 12's **lock half** (revision 14 `:343`): the lock-only stand-in blocks on the `seller` anchor, the phantom listing is absent from the sweep's lock set, and the same test goes **red** against a listing-only hook. The whole-table citation assertion is MS-2d's | D4 |
| 14 | A `ScoringRun` persists with no source while a sourceless `ScraperRun` still raises | B4 |
| 15 | `makemigrations --check` clean | F1 |
| 16 | No scoring math; no observable behavior change (§3 `:384`) | Review of the diff: no `scoring/` module beyond `contracts.py`, no `poller/service.py` change, no adapter change, no `SourceConfig.enabled` flip, and **no admin registration other than C2's mandated read-only `CohortBaseline`** (finding 14) |

Additional acceptance carried from the sections MS-2a is assigned in:

- Retention registry coverage extends `tests/unit/test_purge_registry.py`'s one
  hand-written label set and inherits its six registry-derived properties (E1), and
  the two baseline tables satisfy them because C1 declares the CHECK pair and the
  partial index on both (finding 7).
- Neutral-path column values persist as SA-027's MS-2a half requires: `n_eff = 0` and
  `lambda_shrinkage = 0` as **stored zeros**, and `quantity` / `quantity_basis`
  populated rather than NULL, on all three neutral `price_basis` values (C3,
  revision 14 `:461`). The byte-identical explanation reproduction half of SA-027 is
  MS-2d.
- `SellerRatingObservation`'s class/`expires_at` coherence is asserted for both the
  bounded eBay class and the indefinite merchant class (C4, revision 14 `:354`), and
  the **source-to-class** half is enforced by C5's `seller_rating_retention_class`
  trigger — with the `listing_score` sibling covering S2-2a inheritance — under
  acceptance item 3b (revision 14 `:354-370`).
- Every `ListingScore` range is enforced at the database and probed just outside its
  boundary (C3, finding 9).
- The §3.3.2 parent-tier map is landed by the `MarketTier` migration and is total,
  with every parent strictly widening (B3), and `market_tier` is protected by a
  database CHECK plus loader normalization so a later import cannot reinstate free
  text (B3, finding 6).

## Open at plan time

**None blocking.** Codex pass 2 (`8755be2a`) returned three design-owned items
(findings 3, 15, 16); **design revision 14 settles all three**, and the tasks above
implement the settled rules:

| Pass-2 finding | Settled by (design revision 14) | Where it landed |
| --- | --- | --- |
| 3. `mark_delisted()` re-enters the save guard through `redact_merchant_content()` | `:170`, `:291` — `Listing.save()` advances the stamp on a `SCORING_INPUT_FIELDS` write **except when `update_fields` is exactly the `REDACTED_CONTENT_FIELDS` key set**; the comparison is on the declared set, not the written values, and the fixture must be an eBay delete-on-delist listing | B1 |
| 15. `SellerRatingObservation.raw_payload` deletion semantics | `:372` — nullable `SET_NULL`, `OfferSnapshot.raw_payload`'s shape verbatim (`market.py:545-551`), because `purge_expired` sweeps in `_meta.label` order: `PROTECT` aborts the sweep, `CASCADE` bypasses L9 | C4 |
| 16. The source-to-class mapping is unenforced by the generic CHECK pair | `:354-358`, `:363`, `:368`, `:370` — a `BEFORE INSERT OR UPDATE` trigger per table derives the required class from `NEW.source_site_id` (rating) and `NEW.listing_id` (score) and rejects a mismatch, with four-way raw-SQL / `bulk_create` / `QuerySet.update()` negative tests and the four accepted combinations | C5 |

Everything else is settled. Revision 1 of this plan carried five open items;
**design revision 13 settles all five**, and the tasks above implement the settled
rules:

| Revision-1 open item | Settled by (design revision 13) | Where it landed |
| --- | --- | --- |
| 1. Retention class of a non-eBay `SellerRatingObservation` row | `:354` — the class is per row and follows `source_site`: eBay ⇒ `ebay_listing_observation` + non-NULL `expires_at`; the four first-party merchant sources ⇒ `merchant_fact`, indefinite | C4 |
| 2. `scoring_inputs_changed_at` on the delist/redaction paths | `:170`, `:291-292` — it advances **only** on a `Listing.save()` write of a `SCORING_INPUT_FIELDS` member; both other paths leave `P` and re-enter through C1 | B1 (positive and negative tests), D1 |
| 3. `quantity` / `quantity_basis` on the `capacity_unavailable` path | `:453-459`, `:461`, `:1247` — the `—` cells were a documentation error: the §3.2.2 ladder never consults capacity, so both columns stay NOT NULL on every path and such a row carries the ladder's own outcome (`1` / `default_single` / `quantity_assumed` when the title bears no signal). No nullability, no sentinel, no fifth `quantity_basis` value | C3 |
| 4. Field membership of `scoring/contracts.py` in MS-2a | `:99-102` — the five names plus exactly the fields the MS-2a schema fixes; MS-2b widens them with computed fields | A1 |
| 5. What plays the refresh's part in fixture 12 | `:343` — MS-2a asserts the deleter side and blocking with a **lock-only** stand-in that re-derives no `D_selected`; the whole-table citation assertion is MS-2d's | D4 |

One **non-blocking** reconciliation is recorded rather than resolved, because it is a
plan-level reading and not a gap that stops a task:

- **Where the parent-tier map lives.** §3.3.2 (`:768`) calls it "a versioned constant
  under S2-6; landed by the §2.1 `MarketTier` migration", while §3.4.3 item 4
  (`:1528`) lists it in the constant set a released version owns
  (`scoring/versions/v1/`, MS-2b). B3 lands it as inert data in the `0020` migration
  and MS-2b's version constant is the copy the ladder reads, with an MS-2b task
  asserting the two agree. If MS-2b's plan prefers a single home, this plan's B3 test
  is the only thing that has to move.

## Deliberately excluded MS-2a mentions

Grep hits for "MS-2a" in design revision 14 that this plan does **not** turn into a
task, with why. Section references are used rather than line numbers here, because
these are whole-passage exclusions:

- **§3.2.6 and §3.4.1 group 4** (the neutral-path representation) — these settle a
  *contradiction* MS-2a "could not choose a column nullability" for. The plan carries
  the resolution as C3's NOT NULL/nullable rules; the four-combination provisional
  matrix and `s_price = 0.5` are MS-2b table-driven tests (§3, MS-2b exit).
- **§3.2.6's SA-027 `suggested_validation` ("MS-2a/MS-2d")** — the stored-column half
  is C3; the `inputs_json` group-4 payload and byte-identical explanation reproduction
  through §3.4.3 dispatch need `compose` / `explain` / `dispatch`, which are
  MS-2b/MS-2d.
- **§3.3.3** — MS-2a adds `Listing.extracted_attrs_json` (B1); **MS-2c** writes it
  from `matching/resolver.py`. No resolver change here.
- **§3.4.1's dependency × mutation coverage rows** ("MS-2a asserts the class" for
  `drive_spec` / `product_model` / `product_variant` being `merchant_fact`) — covered
  by E1's registry-derived CHECK-pair property rather than by a bespoke test; the
  trigger and candidate-clause behaviour around them is MS-2d.
- **§2.2.2's test list** — the *field* half is B2; the B1 high-water trigger, the two
  insert shapes and the negative control are explicitly "MS-2d for the trigger".
- **§2.2.1's selector tests and §2.3(a)'s `source_composition` tests** — MS-2c/MS-2d
  by their own headings. Revision 13 (`:332`) replaces the §2.3(b) list's item 5 with
  the redaction-path test MS-2a can run, which is D1 test 5.
- **§1.2's ratification rows and the §6/§7 narrative** — ratification and audit
  history. They are the *precondition* for cutting this plan, not work in it.
- **§3.3.5(f)/(g) prune and §3.3.5(h) measurement** — the prune runs inside the
  `score-refresh` job (MS-2d) and the growth measurement is MS-2c. MS-2a lands only
  the `PROTECT` FK that makes prune condition 2 a database invariant.
