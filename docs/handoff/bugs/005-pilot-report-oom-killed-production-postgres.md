# Bug 005: `pilot_report` OOM-killed production PostgreSQL; the poller never reconnected

Status: production mitigated 2026-10-03 (session 10); code fixed (`982eebe`, `8ac812a`, `60a19b4`), released in `1208526` (PR #47, 2026-10-03)
Found: 2026-10-03 10:23Z, running `manage.py pilot_report --since 2026-09-27T01:09:00Z` in production
for the eBay × CPU pilot review
Severity: about 50 s of collection outage (one scheduled eBay run delayed); no data loss, no cost impact.

## Symptom

The report died with `OperationalError: terminating connection due to administrator command`.
`postgresql@17-main` had failed with result `oom-kill` (1.4 GB memory peak in a 4 GiB CT). After
PostgreSQL restarted, every poller job kept raising `the connection is closed`, while `deadman_job`
still logged success each minute, so the Uptime Kuma heartbeat stayed green.

## Cause

Three faults stacked:

1. `pilot_report._listing_facts` loaded every in-window `OfferSnapshot` with
   `select_related("raw_payload")` only to read `raw_payload.endpoint`. eBay stores one ~28 KB page
   payload per scope sweep, shared by ~50 snapshots, so the join copied it per row: 10.6k snapshots
   carried 565 MB of JSONB (216 distinct payloads, 6 MB), which Python then decoded.
2. PostgreSQL ran the provisioning-time `timescaledb-tune` output, computed from the Proxmox host's RAM:
   `shared_buffers` 64 GB, `work_mem` 1 GiB, `maintenance_work_mem` 2 GB, 10 autovacuum workers.
3. The poller is a long-running non-request process and never called `close_old_connections()`, so a
   dropped connection stayed broken until a service restart.

## Fix

- Production, 10:23–10:27Z: restarted PostgreSQL and the poller/web services; installed the drop-in
  `conf.d/50-ct116-memory.conf` (`shared_buffers` 1GB, `effective_cache_size` 2GB, `work_mem` 8MB,
  `maintenance_work_mem` 256MB, `autovacuum_max_workers` 3), owner-approved. The generated
  `postgresql.conf` is untouched; homelab docs record the drop-in.
- `982eebe`: `pilot_report` reads narrow, streamed value rows; the provider is classified in SQL, so no
  payload body or `import_listing_ids` is loaded (`test_report_never_loads_raw_payload_bodies`).
- `8ac812a`: `poller.executor.ConnectionHygieneExecutor` runs `close_old_connections()` before and after
  every job on the thread that owns its connection (`tests/db/test_poller_db_recovery.py`).
- `60a19b4`: `deadman_job` withholds the push while `SELECT 1` fails, per spec §18.5 and ADR-0017
  (`tests/db/test_poller_deadman.py`). It gates on DB reachability only, not on recent collection success.

## Lesson

- A report that is cheap on a test fixture can be unbounded on real history: shared payloads multiply
  through a join. Select only the columns a report reads; run new production reports first with a small
  `--since` window.
- Auto-tuners inside an LXC read the host's memory. Size PostgreSQL from the container limit.
- A long-running Django process must recycle connections itself, and a liveness heartbeat that ignores
  job failures cannot detect a stuck poller.
