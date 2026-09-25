# Bug 001: nginx cannot serve `/static/` (403) in production

Status: open, owner decision needed (production host permission change)
Found: 2026-09-25, during post-deploy verification of `ac8d608`
Severity: low. Only Django admin styling is affected; the app's own templates
load no static assets, and `/healthz` and the login flow are unaffected.

## Symptom

`GET /static/admin/css/base.css` returns 403 at both the public edge and the
CT-local nginx. The nginx error log reads
`open() "/opt/hw-radar/app/staticfiles/admin/css/base.css" failed (13: Permission denied)`.
Occurrences date back to 2026-07-05 (the day of provisioning), so no deploy
introduced it.

## Cause

The nginx workers run as `www-data`. `/opt/hw-radar` is `hwradar:hwradar 0750`,
so `www-data` cannot traverse into `app/staticfiles/`. The collected
`staticfiles/admin` subtree is also group-only (`2770`). The provisioning
runbook (`docs/runbooks/provisioning.md`) creates the app directory for
`deploy:hwradar` but never grants nginx read access.

## Fix

Not yet applied. Rejected option: adding `www-data` to group `hwradar` would work, but that group can also read
the bao-agent secret render (`/run/bao-agent/hw-radar.env`, `root:hwradar
0640`). That would expose application secrets to the web-server workers.

Candidate fixes (owner to choose):

1. Move `HW_RADAR_STATIC_ROOT` outside `/opt/hw-radar` (e.g. a `deploy`-owned
   `0755` directory), and update `deploy/nginx/hw-radar.conf` and the
   runbook to match.
2. Grant `www-data` narrow POSIX ACLs: `--x` on `/opt/hw-radar` and
   `/opt/hw-radar/app`, plus read (with a default ACL) on `staticfiles/`.

Both change the production host and deployment assets, so this session left
them for owner authorization.

## Lesson

A green deploy smoke test (`/healthz`) does not exercise static serving. The
post-deploy check should fetch one collected static asset through nginx, and
any provisioning step that locks down the app root must say how nginx reaches
`STATIC_ROOT`.
