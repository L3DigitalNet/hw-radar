# Architecture Notes

Last updated: 2026-09-06

## Component Graph

- Django project core: `src/hw_radar/{settings,urls,wsgi}.py`
- Apps: `accounts`, `web`, `catalog`, `acquisition`, `matching`, and `poller`
- Reference-data module: `refdata` (ADR-0018 truncated pipeline — seed
  documents, importer, discovery loop, monthly refresh; not a Django app)
- Data model: ADR-0010 identity ladder plus market/evidence tables and
  TimescaleDB `offer_snapshot` observations
- Runtime jobs: APScheduler poller service (UTC-pinned), daily
  maintenance/recovery jobs, monthly refdata refresh, and dead-man heartbeat
  support
- Deployment: systemd units, nginx config, and `deploy/deploy-remote.sh`

## Standing Backlog

- MS-1e owner-in-the-loop ratification (design implemented, ratification pending;
  sources ship disabled until it lands)
- MS-2 scoring: designed and owner-ratified, not implemented
- MS-3 operator-facing product UI: not implemented
- MS-4 alerting: not implemented
