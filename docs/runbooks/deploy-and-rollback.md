# Runbook: deploy & rollback

## Deploy (normal path)

1. Commit to `dev`; CI (`check`) runs on push.
2. Open/merge the `dev -> main` PR. CI `check` + `dependency-review` must be green.
3. The merge triggers `deploy.yml`: gate re-runs, then the deploy job waits for the production Environment reviewer approval.
4. Approve -> ephemeral tailnet join -> rsync -> on-CT `uv sync --frozen --no-dev` -> `migrate` -> `collectstatic` into `/var/lib/hw-radar/staticfiles` (preflight fails the deploy if that directory is not provisioned) -> restart -> healthz smoke test -> static smoke test (`/static/admin/css/base.css` through the CT nginx must return 200).

## Rollback

1. Find the previous release SHA: `git log --first-parent main` or the last green Deploy run in Actions.
2. Actions -> Deploy -> Run workflow -> `ref` = that SHA -> approve the environment gate.
3. Old code redeploys against the newer schema. Verify `/healthz` reports the rolled-back `release` SHA.

## Failure modes

- Smoke test fails: the workflow fails loudly; immediately rollback via the same workflow_dispatch path.
- `bao-agent` render missing (`/run/bao-agent/hw-radar.env` absent): units fail dependencies; check `systemctl status bao-agent`.
