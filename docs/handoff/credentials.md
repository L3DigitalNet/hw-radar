# Credential References

Last updated: 2026-09-26

Never store credential values in this repository.

## Runtime Environment Variables

- `DJANGO_SECRET_KEY` (required in production)
- `HW_RADAR_ENV`
- `HW_RADAR_ALLOWED_HOSTS` (`CSRF_TRUSTED_ORIGINS` is derived from it; no separate variable)
- `HW_RADAR_STATIC_ROOT` (optional `collectstatic` target override)
- `HW_RADAR_DB_NAME`
- `HW_RADAR_DB_USER`
- `HW_RADAR_DB_PASSWORD`
- `HW_RADAR_DB_HOST`
- `HW_RADAR_DB_PORT`
- `HW_RADAR_KUMA_PUSH_URL`
- `EBAY_API_BASE` (optional endpoint override)
- `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` — eBay Developer application keys. Workstation OpenBao
  `secret/api-keys/commerce/ebay`, fields `username` (client ID) and `password` (client secret):
  the record is login-shaped but holds the API keys. Production: the CT's bao-agent renders both
  from the service-store record `services/apps/hw-radar/config`, fields `ebay_client_id` and
  `ebay_client_secret` (added 2026-09-26 for eBay x CPU; template in the private homelab repo).
- `HW_RADAR_APIFY_TOKEN` — OpenBao `secret/apps/hw-radar/apify`. Runtime, **scoped** token,
  created by the owner 2026-09-25. It needs **Run** and **Read** on the Hardware Radar Actor:
  Read covers `GET /v2/actor-runs/{id}` polls and the `GET /v2/actor-builds/{id}` build reads
  made by the poll job's runtime client, and the 2026-09-25 pre-probe got 404 on both without it.
  It also needs read and delete on those runs' default storages for MS2-D-33 cleanup. The delete
  403-versus-404 behavior was probed 2026-09-25 (403 for inaccessible storage, 404 for nonexistent;
  R25). The owner's grant also includes `List runs` and `Manage runs` (abort on a start mismatch). It needs **no** account limits or usage
  permission: those reads return 403 for scoped tokens, and the runtime makes none (MS-2 plan
  MS2-D-48, [OQ30](../resolved-questions.md#oq30--runtime-apify-account-reads-r25)). Production
  rendering is deferred until production paid admission is intentionally configured (after the
  F5a proof-environment drain and `apify_ledger_handoff`). The other `HW_RADAR_APIFY_*` budget and
  admission settings are non-secret configuration read in `src/hw_radar/settings.py`. `HW_RADAR_APIFY_ENABLED` defaults
  to `false` as the fail-closed kill switch.

## Apify — Operator/Deploy Credential

Hardware Radar's Apify credentials live in their own namespace and are never the
`apify-actors` venture's credential.

- OpenBao `secret/apps/hw-radar/agent/apify` (fields `token`, `org_id`) — **unscoped**
  operator/deploy key. Apify does not allow a scoped token to create or modify Actors, so
  operator work (`apify push`/build, operator inspection under operator reservations) needs
  this key. Exported per-command as `APIFY_TOKEN` for the Apify CLI/API; never persisted and
  never rendered to the production application environment.

## MCP

`.mcp.json` is unchanged: four anonymous read-only Apify tools. It stays an operator surface,
not the runtime protocol, and widens only once a scoped read credential and operator
reservations exist — to the read-only list MS2-D-43 names (`get-actor-run`,
`get-actor-run-list`, `get-actor-log`, `get-dataset`, `get-dataset-items`,
`get-dataset-schema`, `get-key-value-store`, `get-key-value-store-keys`,
`get-key-value-store-record`). `call-actor`, the RAG web browser, abort, and task tools stay
excluded.

## Reference Pattern

Use OpenBao-backed runtime rendering for production secrets and local `.env`
files only for development. Record names and lookup paths only when needed; never
copy secret values into docs, commits, logs, or test fixtures.
