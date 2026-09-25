# Deployed State

Last updated: 2026-09-25

## Current Deployment

- Deploys run from `main` via the Deploy workflow. Latest: `531916e` (PR #34:
  bug 001 static fix, docs, pydantic/gunicorn updates; no migrations), run
  36122297450, 2026-09-25T10:11:51Z. Earlier the same day: `96ce005` (run
  36116766253), and `ac8d608` (run 36078378772), which shipped the schema; the
  superseded `c728613` run 36077354704 was cancelled unapproved.
- Host-verified after each run: `RELEASE` and `/healthz` (local + public) report
  the run's SHA, `database: true`; login 200; web, poller, bao-agent, nginx,
  PostgreSQL active, 0 restarts; no warning-level journal entries.
- Migrations 0018-0020 applied 2026-09-25T08:50Z: spec satellites, category
  rows (`gpu`, `ram`, `cpu`, `nic`, `hba`, `motherboard`, `server` beside
  `drive`), watch requirements/`watch_evaluation`. `migrate --plan` empty and
  `makemigrations --check` clean. Pre/post row counts match; new tables empty.
- All `SourceConfig` rows still `enabled=False`; the poller logged
  `poller started (0 source job(s))`; no `scraper_runs` rows; the env render
  has no Apify variable (names checked only; OQ25), so no Actor run can start.
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

## MS-1d: Operational Gate (SA-004) and Enable Runbook

MS-1d ships five source connectors (ServerPartDeals, goHardDrive, WD, Seagate,
eBay), but every `SourceConfig.enabled` row is left `False` by migration 0005.
Flipping a source to `enabled=True` is a deliberate **operational** act, done
by an operator against the running database — never by a migration or a
deploy. It must not happen until the checklist below has been verified.

### Pre-real-data operational gate (Codex SA-004)

Verify each item below before the **first** `enabled=True` flip on any
source. The handoff record for a prior session claims these were "wired
2026-07-05c" — treat that as a claim to re-verify, not a fact to assume, since
none of it is checkable from this repo.

- [ ] The hourly TimescaleDB-aware logical dump job covers the three new
      MS-1d tables: `availability_heartbeat_observation`,
      `availability_heartbeat_event`, and `raw_payload`.
- [ ] The CT-116 disk-space alert is active. Raw payloads are retained and
      grow over time; this is the early-warning signal before disk pressure
      becomes an incident.
- [ ] Raw-payload storage stays DB-resident for MS-1d — there is no
      disk-path payload stage in this design, so the dump above is the only
      backup surface that matters today. If a future stage writes payloads
      to disk instead of the database, the CT-116 subvolume holding them
      must be added to restic `BACKUP_PATHS` **before** that stage ships,
      not after.
- [ ] The restore path for the dump above is documented (runbook §18.6) and
      has been read by whoever is about to flip the first source live.
- [ ] **Bounded-retention TTL enforcement exists for the class being enabled.**
      Landed 2026-08-16 (dev `5a7f5b7`, pending owner ratification): `purge_expired` plus an hourly
      poller job physically deletes bounded-class rows past `expires_at`, with a per-row
      deletion-exemption policy — delete-on-delist classes and rows with resolution history are
      redacted/kept, other bounded classes are hard-deleted.
      Re-verify against the live system before the first `enabled=True` flip.

Do not flip any source's `enabled` to `True` until every box above is
checked by an operator against the live system, not from this document.

### Per-source enable procedure

Once the SA-004 gate passes, enable sources one at a time — never all at
once — via a direct SQL `UPDATE` against the `SourceConfig` row (the
ADR-0016 settings-row pattern: an operator-tunable row flipped by `UPDATE`,
not a deploy):

```sql
UPDATE source_config
SET enabled = true
FROM source_site
WHERE source_config.source_site_id = source_site.id
  AND source_site.normalized_name = '<source-normalized-name>';
```

After each flip:

1. Watch `scraper_runs` for that `source_site` until the first run reaches
   `status = 'success'`.
2. Confirm at least one resolved listing from that run has a non-`none`
   grain (check `detail_json` on the run, or the resulting
   `listing_resolution` rows) — a successful run with everything stuck at
   `grain=none` means the catalog resolver isn't matching and should be
   investigated before enabling the next source.
3. Only then proceed to the next source in the list.

Enable order: **ServerPartDeals → goHardDrive → WD → Seagate → eBay.**
eBay is last and, per below, stays blocked after the other four are live.

### eBay go-live block (CR-004) — status update 2026-08-16

The eBay delete-on-delist soft-delete path (DR-008) landed on `dev` (`5a7f5b7`): `delisted_at` /
`delist_reason` fields (migration 0012), a 6h absence grace before delisting, and revive-on-sight.
Owner-ruled IR-002 merchant-content redaction (7 fields, class-scoped via
`DELETE_ON_DELIST_CLASSES`) landed alongside it.

eBay's `enabled=True` flip is **still blocked**, now on ratification and deliberate enable, not on
missing code:

- The MS-1e owner-in-the-loop ratification step (design §6) must complete and `ADR-0019` must flip
  to accepted before any source is enabled.
- ServerPartDeals, goHardDrive, WD, and Seagate gate only on the SA-004 checklist above.
- eBay additionally requires this ratification step; per the enable order in this document, it is
  enabled last regardless.
