import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from django.conf import settings

SETTINGS_PATH = Path(__file__).resolve().parents[2] / "src" / "hw_radar" / "settings.py"
_ENV_KEYS = ("HW_RADAR_ENV", "DJANGO_SECRET_KEY", "HW_RADAR_ALLOWED_HOSTS", "HW_RADAR_STATIC_ROOT")


def _load_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> ModuleType:
    """Execute settings.py in a throwaway module under a patched environment.

    Isolation matters: reloading the real `hw_radar.settings` would mutate the
    singleton `django.conf.settings` points at and leak production values into
    every later test. spec_from_file_location loads a fresh, unregistered module
    that never touches Django's global settings.
    """
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("hw_radar._settings_probe", SETTINGS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_argon2id_is_primary_password_hasher() -> None:
    assert settings.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"


def test_hardened_cookie_flags() -> None:
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_SAMESITE == "Lax"
    assert settings.CSRF_COOKIE_SAMESITE == "Lax"
    # Dev runs plain http, so the Secure flags must be off here (prod asserted below).
    assert settings.SESSION_COOKIE_SECURE is False
    assert settings.CSRF_COOKIE_SECURE is False


def test_custom_user_model_is_the_stub() -> None:
    assert settings.AUTH_USER_MODEL == "accounts.User"


def test_strong_password_floor() -> None:
    min_length = next(
        v["OPTIONS"]["min_length"]
        for v in settings.AUTH_PASSWORD_VALIDATORS
        if v["NAME"].endswith("MinimumLengthValidator")
    )
    assert min_length >= 16


def test_production_requires_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # ADR-0009: no SECRET_KEY fallback in production — fail loud at import.
    with pytest.raises(KeyError):
        _load_settings(
            monkeypatch, HW_RADAR_ENV="production", HW_RADAR_ALLOWED_HOSTS="radar.example.net"
        )


def test_production_requires_allowed_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    # No deployment host is hardcoded; production must declare its public host(s).
    with pytest.raises(KeyError):
        _load_settings(monkeypatch, HW_RADAR_ENV="production", DJANGO_SECRET_KEY="x" * 50)


def test_production_hardens_cookies_and_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    prod = _load_settings(
        monkeypatch,
        HW_RADAR_ENV="production",
        DJANGO_SECRET_KEY="x" * 50,
        HW_RADAR_ALLOWED_HOSTS="radar.example.net",
    )
    assert prod.SESSION_COOKIE_SECURE is True
    assert prod.CSRF_COOKIE_SECURE is True
    assert prod.DEBUG is False
    assert prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")


def test_production_csrf_origins_derive_from_public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    prod = _load_settings(
        monkeypatch,
        HW_RADAR_ENV="production",
        DJANGO_SECRET_KEY="x" * 50,
        HW_RADAR_ALLOWED_HOSTS="radar.example.net,radar.example.org",
    )
    assert prod.CSRF_TRUSTED_ORIGINS == [
        "https://radar.example.net",
        "https://radar.example.org",
    ]
    # Loopback is allowed for the on-CT healthz smoke test but never a CSRF origin.
    assert "127.0.0.1" in prod.ALLOWED_HOSTS
    assert "https://127.0.0.1" not in prod.CSRF_TRUSTED_ORIGINS


def test_production_allowed_hosts_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    # A comma-with-space value (the natural way a human writes a list) must not
    # yield leading-space hosts that never match the Host header.
    prod = _load_settings(
        monkeypatch,
        HW_RADAR_ENV="production",
        DJANGO_SECRET_KEY="x" * 50,
        HW_RADAR_ALLOWED_HOSTS="radar.example.net, radar.example.org ",
    )
    assert "radar.example.net" in prod.ALLOWED_HOSTS
    assert "radar.example.org" in prod.ALLOWED_HOSTS
    assert " radar.example.org" not in prod.ALLOWED_HOSTS
    assert prod.CSRF_TRUSTED_ORIGINS == [
        "https://radar.example.net",
        "https://radar.example.org",
    ]


def test_production_static_root_defaults_outside_app_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bug 001: nginx cannot read under the hwradar-only /opt/hw-radar, so the
    # production default must be the dedicated directory nginx is configured for.
    prod = _load_settings(
        monkeypatch,
        HW_RADAR_ENV="production",
        DJANGO_SECRET_KEY="x" * 50,
        HW_RADAR_ALLOWED_HOSTS="radar.example.net",
    )
    assert Path("/var/lib/hw-radar/staticfiles") == prod.STATIC_ROOT
    assert prod.STATIC_ROOT == prod.PRODUCTION_STATIC_ROOT
    assert not prod.STATIC_ROOT.is_relative_to("/opt/hw-radar")


def test_dev_static_root_default_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    dev = _load_settings(monkeypatch)
    assert SETTINGS_PATH.parents[2] / "staticfiles" == dev.STATIC_ROOT


def test_static_root_env_override_wins_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    prod = _load_settings(
        monkeypatch,
        HW_RADAR_ENV="production",
        DJANGO_SECRET_KEY="x" * 50,
        HW_RADAR_ALLOWED_HOSTS="radar.example.net",
        HW_RADAR_STATIC_ROOT="/srv/static-override",
    )
    assert Path("/srv/static-override") == prod.STATIC_ROOT


def test_no_deployment_hostname_hardcoded() -> None:
    # Public-repo guard (AGENTS.md): the settings module must embed no real host.
    assert "l3digital" not in SETTINGS_PATH.read_text(encoding="utf-8")
