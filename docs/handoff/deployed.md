# Deployed State

Last updated: 2026-09-26 (release `478baf0`)

## Current Deployment

- Deploys run from `main` via the Deploy workflow. Latest: `478baf0` (PR #38 merge, matcher
  `2026.09.2`, retirement migration `0023_retire_oq31_sources`), Deploy run 36248125289, approved
  via `pending_deployments`, deployed 2026-09-26 ~14:33Z. Earlier: `c2adae0` (Slice F), `508b1f0`
  (Slices D/E), `ac8d608` (migrations 0018-0020).
- Host-verified after `478baf0`: `RELEASE` and `/healthz` report `478baf0`, `database: true`; web
  and poller units active, 0 restarts. Poller logged `poller started (0 source job(s))`; migration
  `0023_retire_oq31_sources` applied, `migrate --plan` empty; all 6 `SourceConfig` rows
  `enabled=False`; 0 `listings`, 0 `scraper_runs`; no `APIFY` env var rendered;
  `MATCHER_VERSION` `2026.09.2` live.
- Post-deploy `import_refdata --category cpu` (rehearsed first on a production-shaped DB): 2
  manufacturers, 2 families, 9 models, 9 specs, 19 aliases; `product_model` 15 -> 24 (cpu 9,
  drive 15 unchanged). GPU and RAM refdata are still 0 rows in production.
- Expected at the monthly refresh (2026-10-01 07:00Z): re-imports only present categories (drive,
  cpu; skips gpu/ram) and adds 253 first-party drive models (drive 15 -> 268), moving the 3
  HC550 models to family "Ultrastar" and leaving an empty "Ultrastar DC HC550" family reported as
  unreconciled (harmless; owner may delete it later). Rehearsed on the production-shaped DB.
- Delist rule amendment (ADR 0020, 2026-09-26): no eBay scope (legacy drive or category) may
  stale-delist unless the sweep is provably complete.
- Apify stays fail-closed in production: no `APIFY` variable is rendered, so admission denies by
  construction; no ledger authority, cycle, reservation, latch, or provider run exists. The F5a
  proof environment holds the 2026-09-05 cycle's ledger authority; production may claim paid
  admission only after that environment's correction monitoring closes (2026-10-02 ~21:09Z) and
  `apify_ledger_handoff` runs.
- Static files: `STATIC_ROOT` `/var/lib/hw-radar/staticfiles` (deploy-owned, 0755/0644, no
  `hwradar` link); the deploy smoke fetches `base.css` through nginx.
  [Bug 001](bugs/001-nginx-static-403.md) is fixed and verified.
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

Every live cell below is `NOT_ADMITTED` today. "not verified" marks a gate this document cannot
confirm from the repo alone; verify it live before admitting that cell. Seagate-recertified sells
drives only, so its GPU/RAM/CPU cells are `NOT_APPLICABLE` and are not shown as separate rows.

| Source × category | Terms/policy | Connector/API | Retention | Completeness | Refdata seeded (prod) | Corpus ratified | Identifier quality | Condition/shipping | Cost/budget | Cell state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eBay × drive | Official Browse API | Legacy keyword sweep, live | `ebay_listing_observation` ≤6h, delete-on-delist; never stale-delists an unprovably-complete scope (ADR 0020 amendment, 2026-09-26) | not verified | Yes (MS-1 drive seed) | ADR-0019 pending — expanded corpus FAIL on draft labels, 369/271 | not verified | 0% condition coverage (pilot, 2026-09-25) | No direct cost (unmetered) | `NOT_ADMITTED` |
| eBay × CPU | Official Browse API | Query-scoped sweep, live-verified 2026-09-25 | Same class as above; same amendment applies | Single-page only provable (F1) | Yes — CPU refdata now seeded in prod (2026-09-26) | Would-accept 104/99 = 95.19%, `auto_accept` False; owner CPU audit pending | not verified | 0% condition coverage (pilot) | No direct cost (unmetered) | `NOT_ADMITTED` |
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

After each flip, watch `scraper_runs` until the first run reaches `status = 'success'` and at
least one resolved listing has a non-`none` grain (`detail_json` on the run, or the resulting
`listing_resolution` rows), before admitting the next cell — a run stuck at `grain=none` means the
resolver isn't matching and needs investigation first.

The MS-1e owner-in-the-loop drive-matcher ratification (design §6) must complete and `ADR-0019`
flip to accepted before any drive cell is admitted; every source above gates on it (`AGENTS.md`,
MS-2 plan risk R5).
