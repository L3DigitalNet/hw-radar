#!/usr/bin/env bash
# Runs on the CT, invoked by deploy.yml over Tailscale SSH after rsync.
# Requirements: GNU coreutils/findutils (the `chmod 00755` setgid-clearing form
# below is GNU semantics) and a root-provisioned static directory; see
# docs/runbooks/provisioning.md step 3.
set -euo pipefail

cd /opt/hw-radar/app

export PATH="$HOME/.local/bin:$PATH"

# uv-managed interpreters + cache must live OUTSIDE /home: the web/poller units
# run as hwradar with ProtectHome=true, and uv builds .venv/bin/python as a symlink
# to the managed interpreter. Under the deploy user's default ~/.local/share/uv,
# that target is hidden from hwradar's namespace and the service fails to exec.
# /opt/uv is created deploy:hwradar 0755 at provisioning (docs/runbooks/provisioning.md).
export UV_PYTHON_INSTALL_DIR=/opt/uv/python
export UV_CACHE_DIR=/opt/uv/cache

export HW_RADAR_ENV=production
set -a
# Secrets come only from the bao-agent tmpfs render (ADR-0009), never a file at rest.
# The deploy SSH user must have read access to this render (group membership on
# /run/bao-agent — see docs/runbooks/provisioning.md) or `source` fails closed here.
# shellcheck disable=SC1091  # rendered at runtime by bao-agent (ADR-0009)
source /run/bao-agent/hw-radar.env
set +a

uv python install
uv sync --frozen --no-dev

# Every manage.py call below uses .venv/bin/python directly, NOT `uv run`: `uv run`
# re-resolves and would sync the dev dependency group back into the production venv
# (CR-NEW-001). `--frozen --no-dev` above builds a prod-only venv; invoking its
# interpreter directly keeps it that way.

# Static root preflight — runs BEFORE migrate so a host that is missing the
# provisioning step fails with nothing changed. The path is read from Django
# settings (the single source of truth, PRODUCTION_STATIC_ROOT) rather than
# repeated here, so the check, collectstatic, and settings cannot disagree.
# Deliberately no mkdir/chown: deploy has no rights under /var/lib and no sudo
# beyond the unit restart; the directory is created once by root at provisioning.
static_root="$(.venv/bin/python manage.py shell --no-imports -c \
  'from django.conf import settings; print(settings.STATIC_ROOT)')"
static_fail() {
  echo "deploy-remote: static root '$static_root' $1." >&2
  echo "deploy-remote: provision it as root per docs/runbooks/provisioning.md" \
    "step 3 (static directory: deploy-owned, mode 0755, outside /opt/hw-radar)." >&2
  exit 1
}
# Collecting under /opt/hw-radar recreates bug 001: nginx (www-data) cannot
# traverse the 0750 app root, and the only ways to let it in would also expose
# the hwradar-readable secret render. Catches a stray HW_RADAR_STATIC_ROOT.
case "$static_root" in
  /opt/hw-radar | /opt/hw-radar/*) static_fail "is inside /opt/hw-radar, which nginx cannot read" ;;
  /*) ;;
  *) static_fail "is not an absolute path" ;;
esac
[[ -d "$static_root" ]] || static_fail "does not exist"
[[ -w "$static_root" ]] || static_fail "is not writable by $(id -un)"
# Ownership is required, not just write access: the mode enforcement after
# collectstatic chmods every entry, which only the owner may do.
[[ -O "$static_root" ]] || static_fail "is not owned by $(id -un)"

.venv/bin/python manage.py migrate --noinput

# umask 022 makes new files 0644 / dirs 0755 as they are written, so nginx can
# read every asset even mid-deploy. The explicit chmod pass then fixes anything
# the umask cannot: entries left from an earlier run with other modes.
umask 022
.venv/bin/python manage.py collectstatic --noinput
# NOTE: `00755`, not `0755` — GNU chmod keeps a directory's setgid bit under a
# 4-digit numeric mode, so `0755` would leave an inherited 2755 in place.
find "$static_root" -type d -exec chmod 00755 {} +
find "$static_root" -type f -exec chmod 0644 {} +
sudo -n /usr/bin/systemctl restart hw-radar-web.service hw-radar-poller.service
