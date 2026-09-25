# Deployed State

Last updated: 2026-09-06

## Current Deployment

- The service deploys from `main` via the GitHub Actions Deploy workflow.
  Latest confirmed deployed increment: PR #22 dev→main merge commit
  `f3303b1`, deploy run 34056023371. Verified 2026-09-06 via `/healthz`,
  reporting release `f3303b1`, `database: true`.
- Migrations 0014-0017 are now applied in production: 0014 (partial
  `expires_at` indexes), 0015 (`SourceLaneState.continuous_since` / CR-004
  grace), 0016 (identity partial indexes), 0017 (`listing_derived_alias`
  class plus the DR-001 CHECK pair with backfill).
- Production runtime uses the deployment assets under `deploy/` and the Django
  settings in `src/hw_radar/settings.py`; the app exposes `/healthz` for
  release and database health checks.
- The Deploy workflow's GitHub production environment requires a manual
  reviewer approval before it runs on each push to `main`. An unapproved run
  sits pending and dies at GitHub's 30-day cap; this caused production to go
  stale at MS-1b from 2026-07-05 until PR #20's run succeeded only after an
  owner-authorized approval. Every future merge to `main` needs the same
  approval or the deploy will not run.
- The approval can be given via the `pending_deployments` API by the owner's
  account; PR #22's run 34056023371 was approved that way, on the owner's
  ratification.
- PR #15's deploy run separately failed the pip-audit gate on `cryptography`
  49.0.0 (PYSEC-2026-3552); fixed this session by upgrading to 50.0.0.

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
