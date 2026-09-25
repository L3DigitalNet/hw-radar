# Credential References

Last updated: 2026-09-25

Never store credential values in this repository.

## Runtime Environment Variables

- `HW_RADAR_SECRET_KEY`
- `HW_RADAR_ALLOWED_HOSTS`
- `HW_RADAR_CSRF_TRUSTED_ORIGINS`
- `HW_RADAR_DB_NAME`
- `HW_RADAR_DB_USER`
- `HW_RADAR_DB_PASSWORD`
- `HW_RADAR_DB_HOST`
- `HW_RADAR_DB_PORT`
- `HW_RADAR_KUMA_PUSH_URL`
- `EBAY_CLIENT_ID` — OpenBao `secret/api-keys/commerce/ebay`
- `EBAY_CLIENT_SECRET` — OpenBao `secret/api-keys/commerce/ebay`
- `HW_RADAR_APIFY_TOKEN` — OpenBao `secret/apps/hw-radar/apify`. Runtime, **scoped** token
  (run only Hardware Radar-owned Actors, read their runs/default storages). Not yet created by
  the owner; production rendering is deferred until Slice E live admission is ready.
  `HW_RADAR_APIFY_ENABLED` defaults to `false` as the fail-closed kill switch.

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
