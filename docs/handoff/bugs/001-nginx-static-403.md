# Bug 001: nginx cannot serve `/static/` (403) in production

Status: open; fix implemented on dev, pending production deployment and verification
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

Implemented on dev, pending production deployment. The owner chose to move
collected static files out of `/opt/hw-radar` (candidate 1).

- `src/hw_radar/settings.py`: production `STATIC_ROOT` defaults to
  `PRODUCTION_STATIC_ROOT` = `/var/lib/hw-radar/staticfiles`. The
  `HW_RADAR_STATIC_ROOT` override is kept, and dev/test still use
  `BASE_DIR/staticfiles`.
- `deploy/deploy-remote.sh`: reads the effective `STATIC_ROOT` from settings.
  Before `migrate`, it fails loudly if that path is inside `/opt/hw-radar`,
  missing, not writable, or not owned by `deploy`. The error names the
  provisioning step. It runs `collectstatic` under `umask 022` and then sets
  modes explicitly (dirs `0755` with setgid cleared, files `0644`). It
  creates nothing under `/var/lib`, and its only sudo call is still the unit
  restart.
- `deploy/nginx/hw-radar.conf`: `/static/` aliases
  `/var/lib/hw-radar/staticfiles/`.
- `.github/workflows/deploy.yml`: a new static smoke step fetches
  `/static/admin/css/base.css` through the CT nginx. It uses the conf's
  `server_name` as the Host header and fails the job on anything other than 200.
- `docs/runbooks/provisioning.md`: step 3 provisions
  `/var/lib/hw-radar/staticfiles` as `deploy:deploy 0755`, with the parent
  `root:root 0755`, and forbids adding `www-data` to `hwradar`. A one-time
  migration section covers existing hosts.
- `tests/unit/test_static_root_contract.py` pins the settings, nginx, deploy
  script, workflow, and runbook against each other.

Rejected: adding `www-data` to group `hwradar`, or granting it ACLs into
`/opt/hw-radar`. That group can also read the bao-agent secret render
(`/run/bao-agent/hw-radar.env`, `root:hwradar 0640`), so either option would
expose application secrets to the web-server workers.

Remaining before close: provision the directory, install the new nginx conf,
deploy, and confirm that the static smoke step and a styled admin page work in
production.

## Lesson

A green deploy smoke test (`/healthz`) does not exercise static serving. The
post-deploy check should fetch one collected static asset through nginx, and
any provisioning step that locks down the app root must say how nginx reaches
`STATIC_ROOT`.
