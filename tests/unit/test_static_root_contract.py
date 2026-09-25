"""Cross-file contract for production static files (bug 001).

Four artifacts must agree on where collected static files live and how they are
checked, and none of them can see the others at runtime:

- ``PRODUCTION_STATIC_ROOT`` in ``src/hw_radar/settings.py`` (source of truth);
- the ``/static/`` ``alias`` in ``deploy/nginx/hw-radar.conf``;
- the preflight and mode enforcement in ``deploy/deploy-remote.sh``;
- the static smoke step in ``.github/workflows/deploy.yml``.

Drift between any two is invisible until production: nginx 404s or 403s on
``/static/`` while ``/healthz`` stays green, which is exactly how bug 001
shipped. These are text-level checks because the artifacts are shell, nginx
config, and YAML that only run on the production host.
"""

import re
from pathlib import Path

from django.contrib.staticfiles import finders

from hw_radar.settings import PRODUCTION_STATIC_ROOT

REPO = Path(__file__).resolve().parents[2]
NGINX_CONF = REPO / "deploy" / "nginx" / "hw-radar.conf"
DEPLOY_SCRIPT = REPO / "deploy" / "deploy-remote.sh"
DEPLOY_WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"
PROVISIONING = REPO / "docs" / "runbooks" / "provisioning.md"
SMOKE_ASSET = "/static/admin/css/base.css"


def _code_lines(path: Path) -> list[str]:
    """Return non-comment, non-blank lines, stripped, so prose cannot satisfy a check."""
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _index_of(lines: list[str], needle: str) -> int:
    matches = [i for i, line in enumerate(lines) if needle in line]
    assert len(matches) == 1, f"expected exactly one line containing {needle!r}, got {matches}"
    return matches[0]


def test_production_static_root_is_outside_app_root() -> None:
    # The app root is hwradar:hwradar 0750 and its group reads the secret render;
    # anything nginx serves must live elsewhere.
    assert PRODUCTION_STATIC_ROOT.is_absolute()
    assert not PRODUCTION_STATIC_ROOT.is_relative_to("/opt/hw-radar")


def test_nginx_static_alias_matches_production_static_root() -> None:
    aliases = re.findall(
        r"location\s+/static/\s*\{[^}]*?\balias\s+([^;\s]+);",
        NGINX_CONF.read_text(encoding="utf-8"),
    )
    assert len(aliases) == 1
    # nginx `alias` for a prefix location needs the trailing slash, or
    # /static/x maps to <root>x instead of <root>/x.
    assert aliases[0] == f"{PRODUCTION_STATIC_ROOT}/"


def test_nginx_has_single_server_name_for_smoke_host_header() -> None:
    # The smoke step's awk takes field 2 of the first server_name line as the
    # Host header; a multi-name or missing directive would send the wrong host.
    names = re.findall(r"^\s*server_name\s+([^;]+);", NGINX_CONF.read_text(encoding="utf-8"), re.M)
    assert len(names) == 1
    assert len(names[0].split()) == 1


def test_deploy_script_preflights_static_root_before_migrate() -> None:
    lines = _code_lines(DEPLOY_SCRIPT)
    # The path comes from Django settings, never a second hardcoded copy.
    assert not any(str(PRODUCTION_STATIC_ROOT) in line for line in lines)
    read = _index_of(lines, "print(settings.STATIC_ROOT)")
    exists = _index_of(lines, '[[ -d "$static_root" ]]')
    writable = _index_of(lines, '[[ -w "$static_root" ]]')
    owned = _index_of(lines, '[[ -O "$static_root" ]]')
    legacy = _index_of(lines, "/opt/hw-radar | /opt/hw-radar/*)")
    migrate = _index_of(lines, "manage.py migrate")
    assert read < min(exists, writable, owned, legacy)
    assert max(exists, writable, owned, legacy) < migrate


def test_deploy_script_collects_with_umask_and_enforces_modes() -> None:
    lines = _code_lines(DEPLOY_SCRIPT)
    umask = _index_of(lines, "umask 022")
    collect = _index_of(lines, "manage.py collectstatic")
    # `00755`: GNU chmod keeps a directory's setgid bit under plain `0755`.
    dirs = _index_of(lines, 'find "$static_root" -type d -exec chmod 00755 {} +')
    files = _index_of(lines, 'find "$static_root" -type f -exec chmod 0644 {} +')
    assert umask < collect < min(dirs, files)


def test_deploy_script_does_not_create_or_escalate() -> None:
    # deploy has no rights under /var/lib and sudo only for the unit restart;
    # the directory is root-provisioned, so the script must not try to make it.
    lines = _code_lines(DEPLOY_SCRIPT)
    for forbidden in ("mkdir", "chown", "install -d"):
        assert not any(forbidden in line for line in lines), forbidden
    sudo = [line for line in lines if "sudo" in line]
    assert sudo == [
        "sudo -n /usr/bin/systemctl restart hw-radar-web.service hw-radar-poller.service"
    ]


def test_workflow_smoke_fetches_a_collected_asset_through_nginx() -> None:
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    assert f"asset={SMOKE_ASSET}" in text
    assert '"http://127.0.0.1$asset"' in text  # port 80 = nginx, not gunicorn :8000
    assert "/opt/hw-radar/app/deploy/nginx/hw-radar.conf" in text
    # The asset must really be produced by collectstatic, or the smoke test
    # would fail on every deploy regardless of permissions.
    assert finders.find(SMOKE_ASSET.removeprefix("/static/")) is not None


def test_provisioning_runbook_creates_the_static_root() -> None:
    text = PROVISIONING.read_text(encoding="utf-8")
    assert f"install -d -o deploy -g deploy -m 0755 {PRODUCTION_STATIC_ROOT}" in text
    assert f"install -d -o root -g root -m 0755 {PRODUCTION_STATIC_ROOT.parent}" in text
