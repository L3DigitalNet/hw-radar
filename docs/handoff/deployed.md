# Deployed State

Last updated: 2026-09-27 (release `8f529a8`; eBay × CPU live)

## Current Deployment

- Deploys run from `main` via the Deploy workflow. Latest: `8f529a8` (PR #42 documentation-only),
  Deploy run 36316335683, succeeded 2026-09-27 11:58Z after eligible-owner environment approval.
- `8f529a8` made no code, dependency, migration, or settings change. The prior application release
  `646809d` (PR #41) remains the rollback pointer; before it were `5f2d300` and `ded1000`.
- Post-deploy health checks reported release `8f529a8` and `database: true`; no migrations were
  pending, `base.css` returned 200, and web and poller were active with zero automatic restarts.
- Runtime versions remain matcher `2026.09.4`, evaluator `ms2c.3`, and migration `0024`.
- The deployed `admission.py` matches this repository. Only eBay is enabled every 600 seconds and
  only eBay × CPU is admitted; all other sources remain disabled and no `APIFY` environment entries render.
- **Live since 2026-09-27 01:09Z: eBay enabled** (the only enabled source) after the eBay × CPU
  checklist passed: six EPYC query scopes every 600 s; production watch 1 requires a stated
  unlock. F6 is proven: [evidence](../evidence/2026-09-27-ebay-cpu-sa004-f6.md).
- eBay credentials render in production since 2026-09-26 (`EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET`
  from the hw-radar service-store bundle; [bug 004](bugs/004-ebay-credentials-not-rendered-in-production.md)).
- Production CPU refdata (`import_refdata --category cpu`, 2026-09-26): 2 families, 9 models, 9
  specs, 19 aliases. GPU and RAM refdata are still 0 rows in production.
- Monthly refresh (2026-10-01 07:00Z, rehearsed): re-imports drive + cpu only, adds 253 drive
  models, moves the 3 HC550 models to "Ultrastar"; the empty "Ultrastar DC HC550" family stays
  (owner, 2026-09-26: leave it; no manual deletion).
- Apify stays fail-closed in production: no `APIFY` variable is rendered, so admission denies by
  construction; no ledger authority, cycle, reservation, latch, or provider run exists. The F5a
  proof environment holds the 2026-09-05 cycle's ledger authority; production may claim paid
  admission only after that environment's correction monitoring closes (2026-10-02 ~21:09Z) and
  `apify_ledger_handoff` runs.
- Static files: `STATIC_ROOT` `/var/lib/hw-radar/staticfiles` (deploy-owned, 0755/0644); the
  deploy smoke fetches `base.css` through nginx ([bug 001](bugs/001-nginx-static-403.md), fixed).
- The production environment needs a reviewer approval per push to `main`; an unapproved run dies
  at GitHub's 30-day cap. The owner's account approves via the `pending_deployments` API.

## Public-Safe Boundary

Private hostnames, private IPs, container IDs, and credential values are not
recorded in this public repo. Store those facts in the private infrastructure
systems of record.

## Source × Category Admission Matrix (replaces the SA-004 enable order)

**Owner decision, 2026-09-26 ([OQ31](../resolved-questions.md#oq31--existing-local-connectors-whose-terms-prohibit-automated-access)):**
the global SA-004 enable order (ServerPartDeals → goHardDrive → WD → Seagate → eBay) is retired;
its prior text is in git history (`git log -p -- docs/handoff/deployed.md`), not repeated here.
Enablement is now per `(source, category)` cell: `ADMISSION_MATRIX` in
`src/hw_radar/acquisition/admission.py` is the canonical source (`is_admitted(source, category)`).
`SourceConfig.enabled` remains the operator go-live switch; the matrix is the ceiling above it — an
admitted cell enables nothing by itself, an enabled row collects nothing the matrix does not admit,
and an unrelated source or category is never a prerequisite for another cell. Changing a cell or
the retired set is a reviewed code change and a release, never a settings value.

ServerPartDeals and Seagate-recertified are **retired** (`RETIRED_SOURCES`; migration
`0023_retire_oq31_sources` sets both `SourceConfig` rows `enabled=False`, lifecycle `skip`): their
reviewed current Terms conflict with the automated collection their connectors perform, for any
execution venue (local, private scheduling, or Apify alike) — permission-required, not
production-enableable. Code and history are preserved. Re-admission needs a fresh
source-admission review on materially changed Terms or written merchant permission; nothing
re-admits a retired source automatically.

Only eBay × CPU is `ADMITTED` (2026-09-27, [evidence](../evidence/2026-09-27-ebay-cpu-sa004-f6.md));
every other live cell is `NOT_ADMITTED`. "not verified" marks a gate this document cannot
confirm from the repo alone; verify it live before admitting that cell. Seagate-recertified sells
drives only, so its GPU/RAM/CPU cells are `NOT_APPLICABLE` and are not shown as separate rows.

| Source × category | Terms/policy | Connector/API | Retention | Completeness | Refdata seeded (prod) | Corpus ratified | Identifier quality | Condition/shipping | Cost/budget | Cell state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eBay × drive | Official Browse API | Legacy keyword sweep, live | `ebay_listing_observation` ≤6h, delete-on-delist; never stale-delists an unprovably-complete scope (ADR 0020 amendment, 2026-09-26) | not verified | Yes (MS-1 drive seed) | Ratified 2026-09-26 — ADR-0019 accepted (composite PASS, 366/366) | not verified | 0% condition coverage (pilot, 2026-09-25) | No direct cost (unmetered) | `NOT_ADMITTED` |
| eBay × CPU | Official Browse API | Query-scoped sweep, live-verified 2026-09-25 | Same class as above; same amendment applies | Six EPYC scopes, each single-page complete (live 2026-09-27) | Yes — CPU refdata seeded in prod (2026-09-26) | AMD EPYC ratified (OQ34): 105/105 under production rules, matcher `2026.09.4`; other CPU families review | EPYC exact alias; OEM/ES/QS parts resolve to `none` (live: 112/112) | Unknown shipping keeps a price clause `unknown`; buying watches set `require_vendor_unlocked` | No direct cost; about 864 Browse calls/day at 600 s | **`ADMITTED`**, eBay enabled 2026-09-27 |
| eBay × GPU | Official Browse API | Query-scoped sweep, live-verified 2026-09-25 | Same class as above | Single-page only provable (F1) | No — 0 rows in production | Harvested unlabeled (F4: 851 GPU entries); not ratified | not verified | 0% condition coverage (pilot) | No direct cost (unmetered) | `NOT_ADMITTED` |
| eBay × RAM | Official Browse API | Query-scoped sweep, live-verified 2026-09-25 | Same class as above | Single-page only provable (F1) | No — 0 rows in production | Harvested unlabeled (F4: 991 RAM entries); not ratified | not verified | 0% condition coverage (pilot) | No direct cost (unmetered) | `NOT_ADMITTED` |
| WD-recertified × drive | not verified | Live connector, deployed | not verified | not verified | Yes (MS-1 drive seed) | Ratified (ADR-0019 accepted 2026-09-26) | SKU carries no part number (MS-1e finding F2) | not verified | No direct cost (unmetered) | `NOT_ADMITTED` |
| goHardDrive × drive | not verified | Live connector; URL/selector drift fixed 2026-09-25 (`2c8bce6`) | not verified | not verified | Yes (MS-1 drive seed) | Ratified (ADR-0019 accepted 2026-09-26) | not verified | not verified | No direct cost (unmetered) | `NOT_ADMITTED` |
| ServerPartDeals × (all) | Terms prohibit automated access (OQ31) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `RETIRED` |
| Seagate-recertified × drive | Terms prohibit automated access (OQ31) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `RETIRED` |

### Operator live checklist (per cell, immediately before it flips to `ADMITTED` / its source enables)

Run this checklist against the **live production system** — not from this document — immediately
before a specific `(source, category)` cell is admitted or its source's `enabled` bit flips.
The boxes are the reusable template; eBay × CPU's completed run is in its
[evidence](../evidence/2026-09-27-ebay-cpu-sa004-f6.md), and every other cell is unchecked.

- [ ] Current release migrated (`migrate --plan` empty); web and poller services healthy, 0
      unexpected restarts.
- [ ] Bounded-retention TTL enforcement (`purge_expired` + hourly poller job) covers the class this
      cell collects; the hourly logical dump covers every table it writes, and the restore path
      (runbook §18.6) has been read by whoever is about to flip it.
- [ ] `SourceConfig` for this source is correct (normalized name, credentials if needed, cadence).
- [ ] Completeness behavior verified for this cell's sweep shape: only a single-page-provable scope
      is ever treated as complete; never delist on an unprovably complete scope (F1 finding).
- [ ] This category's refdata is seeded in production (`import_refdata --category <slug>`) and its
      matching corpus is owner-ratified, with `matcher_version` matching the ratified run
      (`src/hw_radar/matching/__init__.py`).
- [ ] CPU cells: every buying watch sets `require_vendor_unlocked` (unknown lock never passes).
- [ ] Source-policy status re-checked against current Terms (not the date of this table).
- [ ] Rollback path confirmed: flip the cell back to `NOT_ADMITTED` / disable the source, release.
- [ ] Observability (`scraper_runs` alerting, production-container disk-space alert) is active, and this cell's
      cost/budget stays inside the Hardware Radar Apify ceiling if it ever runs through Apify — no
      admitted cell currently does.

### Per-cell enable procedure

Once a cell's checklist passes, flip it via a direct SQL `UPDATE` against the `SourceConfig` row
(the ADR-0016 settings-row pattern: an operator-tunable row flipped by `UPDATE`, not a deploy) —
never all cells at once:

```sql
UPDATE source_config
SET enabled = true
FROM source_site
WHERE source_config.source_site_id = source_site.id
  AND source_site.normalized_name = '<source-normalized-name>';
```

The poller reads enabled rows once at start, so restart only `hw-radar-poller.service` after the
flip (and after a disable); the first full-lane run fires one `cadence_baseline_s` later.

After each flip, watch `scraper_runs` until the first run reaches `status = 'success'` and at
least one resolved listing has a non-`none` grain (`detail_json` on the run, or the resulting
`listing_resolution` rows), before admitting the next cell — a run stuck at `grain=none` means the
resolver isn't matching and needs investigation first.

The MS-1e drive-matcher ratification (design §6) completed 2026-09-26 and `ADR-0019` is accepted,
so the `x drive` cells (MS-2 plan risk R5) now wait only on their own per-cell checklists and an
owner decision to admit them. Non-drive cells gate on their own category corpus: CPU is ratified for
AMD EPYC only (OQ34); GPU/RAM have none yet.
