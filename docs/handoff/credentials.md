# Credential References

Last updated: 2026-08-16

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

## Reference Pattern

Use OpenBao-backed runtime rendering for production secrets and local `.env`
files only for development. Record names and lookup paths only when needed; never
copy secret values into docs, commits, logs, or test fixtures.
