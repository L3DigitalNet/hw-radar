# Deployed State

Last updated: 2026-09-25 (release `c2adae0`)

## Current Deployment

- Deploys run from `main` via the Deploy workflow. Latest: `c2adae0` (PR #36:
  MS-2 Slice F, eBay category sweeps, `pilot_report`, goHardDrive and Scrapy DNS
  fixes; no migrations), run 36201686493, deployed 2026-09-25T23:47Z. A
  docs-only follow-up merge may move `RELEASE` past it with identical code.
  Earlier: `508b1f0` (PR #35, Slices D/E, run 36194557063), `531916e` (PR #34),
  and `ac8d608` (run 36078378772), which shipped migrations 0018-0020.
- Host-verified after `c2adae0`: `RELEASE` and `/healthz` (local + public)
  report `c2adae0`, `database: true`; login 200; public
  `/static/admin/css/base.css` 200; web and poller active, 0 restarts; no
  warning-level journal entries; catalog/listing row counts unchanged.
- Migrations 0021 (`provider_runs`) and 0022 (`apify_spend_ledger`) applied
  2026-09-25T22:10Z; both are additive. `migrate --plan` empty,
  `makemigrations --check` clean, pre/post catalog row counts identical, new
  provider and ledger tables empty.
- Apify stays fail-closed in production: no `APIFY` variable is rendered
  (`HW_RADAR_APIFY_ENABLED` false, no token, no Actor id, no prices or caps, so
  admission denies by construction); no ledger authority, cycle, reservation,
  latch, or provider run exists, including after several `apify_poll_job`
  ticks. The F5a proof environment holds the 2026-09-05 cycle's ledger
  authority; production may claim paid admission only after that
  environment's correction monitoring closes (2026-10-02 ~21:09Z) and
  `apify_ledger_handoff` runs.
- All `SourceConfig` rows still `enabled=False` (provider `local`); the poller
  logged `poller started (0 source job(s))` at 23:47Z; no `scraper_runs` rows, so
  the new eBay category sweeps are deployed but have never run.
- Static files: `STATIC_ROOT` `/var/lib/hw-radar/staticfiles` (deploy-owned,
  0755/0644, no `hwradar` link); the deploy smoke fetches `base.css` through
  nginx. [Bug 001](bugs/001-nginx-static-403.md) is fixed and verified.
- Runtime assets live under `deploy/`; `/healthz` reports release and DB health.
- The production environment needs a reviewer approval per push to `main`; an
  unapproved run dies at GitHub's 30-day cap (production sat stale at MS-1b
  2026-07-05 until PR #20). The owner's account can approve via the
  `pending_deployments` API (e.g. runs 34056023371, 36078378772, 36116766253).

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

Every live cell below is `NOT_ADMITTED` today. "not verified" marks a gate this document cannot
confirm from the repo alone; verify it live before admitting that cell. Seagate-recertified sells
drives only, so its GPU/RAM/CPU cells are `NOT_APPLICABLE` and are not shown as separate rows.

| Source × category | Terms/policy | Connector/API | Retention | Completeness | Refdata seeded (prod) | Corpus ratified | Identifier quality | Condition/shipping | Cost/budget | Cell state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eBay × drive | Official Browse API | Legacy keyword sweep, live | `ebay_listing_observation` ≤6h, delete-on-delist | not verified | Yes (MS-1 drive seed) | ADR-0019 pending (17/100 auto-accepts, OQ32 floor) | not verified | 0% condition coverage (pilot, 2026-09-25) | No direct cost (unmetered) | `NOT_ADMITTED` |
| eBay × CPU | Official Browse API | Query-scoped sweep, live-verified 2026-09-25 | Same class as above | Single-page only provable (F1) | No — import planned | Focused EPYC corpus (9354/9654/7763/7742) being prepared; owner audit pending | not verified | 0% condition coverage (pilot) | No direct cost (unmetered) | `NOT_ADMITTED` |
| eBay × GPU | Official Browse API | Query-scoped sweep, live-verified 2026-09-25 | Same class as above | Single-page only provable (F1) | No — 0 rows in production | Harvested unlabeled (F4: 851 GPU entries); not ratified | not verified | 0% condition coverage (pilot) | No direct cost (unmetered) | `NOT_ADMITTED` |
| eBay × RAM | Official Browse API | Query-scoped sweep, live-verified 2026-09-25 | Same class as above | Single-page only provable (F1) | No — 0 rows in production | Harvested unlabeled (F4: 991 RAM entries); not ratified | not verified | 0% condition coverage (pilot) | No direct cost (unmetered) | `NOT_ADMITTED` |
| WD-recertified × drive | not verified | Live connector, deployed | not verified | not verified | Yes (MS-1 drive seed) | ADR-0019 pending (same gate as eBay×drive) | SKU carries no part number (MS-1e finding F2) | not verified | No direct cost (unmetered) | `NOT_ADMITTED` |
| goHardDrive × drive | not verified | Live connector; URL/selector drift fixed 2026-09-25 (`2c8bce6`) | not verified | not verified | Yes (MS-1 drive seed) | ADR-0019 pending (same gate as eBay×drive) | not verified | not verified | No direct cost (unmetered) | `NOT_ADMITTED` |
| ServerPartDeals × (all) | Terms prohibit automated access (OQ31) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `RETIRED` |
| Seagate-recertified × drive | Terms prohibit automated access (OQ31) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | `RETIRED` |

### Operator live checklist (per cell, immediately before it flips to `ADMITTED` / its source enables)

Run this checklist against the **live production system** — not from this document — immediately
before a specific `(source, category)` cell is admitted or its source's `enabled` bit flips.
Every box below is unchecked: nothing has been enabled.

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
- [ ] Source-policy status re-checked against current Terms (not the date of this table).
- [ ] Rollback path confirmed: flip the cell back to `NOT_ADMITTED` / disable the source, release.
- [ ] Observability (`scraper_runs` alerting, CT-116 disk-space alert) is active, and this cell's
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

After each flip, watch `scraper_runs` until the first run reaches `status = 'success'` and at
least one resolved listing has a non-`none` grain (`detail_json` on the run, or the resulting
`listing_resolution` rows), before admitting the next cell — a run stuck at `grain=none` means the
resolver isn't matching and needs investigation first.

The MS-1e owner-in-the-loop drive-matcher ratification (design §6) must complete and `ADR-0019`
flip to accepted before any drive cell is admitted; every source above gates on it (`AGENTS.md`,
MS-2 plan risk R5).
