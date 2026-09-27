# Bug 004: eBay credentials were never rendered in production

Status: fixed 2026-09-26 (session 9), before any eBay cell was admitted
Found: 2026-09-26, running the eBay × CPU per-cell checklist (SA-004) against production
Severity: would have blocked eBay collection outright; no data or cost impact, because every
eBay cell was `NOT_ADMITTED` and the source was disabled.

## Symptom

A read-only production probe of the six EPYC query scopes failed every page with
`page_failed:KeyError`, and minting a Browse token raised `KeyError: 'EBAY_CLIENT_ID'`. The
production env bundle rendered only `DJANGO_SECRET_KEY`, `HW_RADAR_ALLOWED_HOSTS`,
`HW_RADAR_DB_PASSWORD`, and `HW_RADAR_KUMA_PUSH_URL`.

## Cause

The eBay application keys existed only in the workstation OpenBao
(`secret/api-keys/commerce/ebay`), where every earlier live eBay run (F1–F4 pilot, harvests)
was executed. The production container's bao-agent template renders only the service-store
record `services/apps/hw-radar/config`, which had never held eBay fields. Two things hid it:
every eBay cell shipped disabled, so no production run ever needed a token; and
`credentials.md` named the workstation path with no production source, as if one path served
both. The workstation record is also login-shaped: its fields are `username` (the client ID)
and `password` (the client secret), not `client_id`/`client_secret`.

## Fix

- The owner approved provisioning the credentials into the existing bundle, so no policy
  change was needed. The two fields were streamed with the homelab tool
  `bao-services-write.sh --patch --stdin`, and values never appeared in argv or output. The
  fields are `ebay_client_id` and `ebay_client_secret`, and the record is now version 4.
- Two template lines were added to the container's `agent.hcl` (homelab `a8526b9c`; the prior
  file is kept on the container). bao-agent was restarted, and both names now render into the
  `0640 root:hwradar` bundle.
- `credentials.md` now names the fields on both sides.

## Lesson

A live proof run from the workstation does not prove the production container can make the
same call. Before admitting any source, probe it from the production runtime with the
production env, and check every credential that source reads by name. A per-cell checklist
item that says "credentials if needed" has to be run live, not read from docs.
