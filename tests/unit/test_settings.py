import importlib.util
from datetime import UTC, date, datetime
from decimal import Decimal
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


# ── Slice E budget keys: absent / empty / invalid semantics ─────────────────

_BUDGET_DEFAULTS: dict[str, object] = {
    "HW_RADAR_APIFY_USD_PER_CU": None,
    "HW_RADAR_APIFY_DATASET_READS_USD_PER_1000": None,
    "HW_RADAR_APIFY_DATASET_WRITES_USD_PER_1000": None,
    "HW_RADAR_APIFY_DATASET_STORAGE_USD_PER_GB_HOUR": None,
    "HW_RADAR_APIFY_KV_READS_USD_PER_1000": None,
    "HW_RADAR_APIFY_KV_WRITES_USD_PER_1000": None,
    "HW_RADAR_APIFY_KV_STORAGE_USD_PER_GB_HOUR": None,
    "HW_RADAR_APIFY_TRANSFER_USD_PER_GB": None,
    "HW_RADAR_APIFY_MARGIN": None,
    "HW_RADAR_APIFY_ESTIMATOR_VERSION": "1",
    "HW_RADAR_APIFY_MAX_TIMEOUT_S": None,
    "HW_RADAR_APIFY_CYCLE_TARGET_USD": Decimal("12.00"),
    "HW_RADAR_APIFY_OPERATOR_ALLOWANCE_USD": Decimal("1.00"),
    "HW_RADAR_APIFY_WATCH_REFRESH_RESERVE_USD": Decimal("3.00"),
    "HW_RADAR_APIFY_CASH_CEILING_USD": Decimal("20.00"),
    "HW_RADAR_APIFY_ACCOUNT_MARGIN_USD": None,
    "HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR": None,
    "HW_RADAR_APIFY_ACCOUNT_LIMIT_USD": None,
    "HW_RADAR_APIFY_ACCOUNT_BASE_PRICE_USD": None,
    "HW_RADAR_APIFY_ACCOUNT_DATA_RETENTION_DAYS": None,
    "HW_RADAR_APIFY_ACCOUNT_VERIFIED_ON": None,
    "HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD": Decimal("5.00"),
    "HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED": date(2026, 9, 25),
    "HW_RADAR_APIFY_OPERATOR_BUILD_BOUND_USD": Decimal("0.41"),
    "HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_ITEMS": 1000,
    "HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_RECORD_READS": 20,
    "HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_BYTES": 10_000_000,
    "HW_RADAR_APIFY_OPERATOR_PROBE_MAX_CALLS": 10,
    "HW_RADAR_APIFY_MAX_KV_WRITES": None,
    "HW_RADAR_APIFY_MAX_KV_BYTES": None,
    "HW_RADAR_APIFY_STORAGE_MAX_LIFETIME": None,
    "HW_RADAR_APIFY_API_CALL_OVERHEAD_BYTES": 262144,
    "HW_RADAR_APIFY_MAX_CORRECTION_READS": 12,
    "HW_RADAR_APIFY_CYCLE_BOUNDARY_GUARD_S": 3600,
    "HW_RADAR_APIFY_USAGE_SETTLE_DELAY_S": 10,
    "HW_RADAR_APIFY_USAGE_STABLE_READS": 2,
    "HW_RADAR_APIFY_USAGE_STABLE_INTERVAL_S": 60,
    "HW_RADAR_APIFY_USAGE_FINALIZE_DEADLINE_S": 86400,
    "HW_RADAR_APIFY_CORRECTION_WINDOW_S": 604800,
    "HW_RADAR_APIFY_POST_RUN_COST_MODE": "bound",
    "HW_RADAR_APIFY_RUN_USAGE_SETTLEMENT": "bound",
    "HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S": 900,
    "HW_RADAR_APIFY_LEDGER_ID": "",
}


def _load_budget(monkeypatch: pytest.MonkeyPatch, **env: str) -> ModuleType:
    for key in _BUDGET_DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    return _load_settings(monkeypatch, **env)


def test_budget_keys_absent_take_the_plan_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = _load_budget(monkeypatch)
    assert {key: getattr(loaded, key) for key in _BUDGET_DEFAULTS} == _BUDGET_DEFAULTS


@pytest.mark.parametrize("raw", ["", " ", "five", "-1", "-0.01", "inf", "-inf", "NaN", "sNaN"])
def test_invalid_budget_decimal_parses_to_none_never_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    keys = (
        "HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD",
        "HW_RADAR_APIFY_CYCLE_TARGET_USD",
        "HW_RADAR_APIFY_TRANSFER_USD_PER_GB",
        "HW_RADAR_APIFY_MARGIN",
    )
    loaded = _load_budget(monkeypatch, **dict.fromkeys(keys, raw))
    for key in keys:
        assert getattr(loaded, key) is None, key


def test_valid_budget_values_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = _load_budget(
        monkeypatch,
        HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD=" 4.50 ",
        HW_RADAR_APIFY_USD_PER_CU="0.20",
        HW_RADAR_APIFY_MAX_KV_WRITES=" 3 ",
        HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED="2026-10-01",
        HW_RADAR_APIFY_POST_RUN_COST_MODE="counted",
        HW_RADAR_APIFY_ACCOUNT_MARGIN_USD="0",
    )
    assert Decimal("4.50") == loaded.HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD
    assert Decimal("0.20") == loaded.HW_RADAR_APIFY_USD_PER_CU
    assert loaded.HW_RADAR_APIFY_MAX_KV_WRITES == 3
    assert date(2026, 10, 1) == loaded.HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED
    assert loaded.HW_RADAR_APIFY_POST_RUN_COST_MODE == "counted"
    # Zero is a valid margin, distinct from both "absent" and "invalid".
    assert Decimal(0) == loaded.HW_RADAR_APIFY_ACCOUNT_MARGIN_USD


@pytest.mark.parametrize("raw", ["", "abc", "-1", "1.5"])
def test_invalid_budget_int_parses_to_none(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    loaded = _load_budget(
        monkeypatch,
        HW_RADAR_APIFY_MAX_CORRECTION_READS=raw,
        HW_RADAR_APIFY_STORAGE_MAX_LIFETIME=raw,
        HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S=raw,
    )
    assert loaded.HW_RADAR_APIFY_MAX_CORRECTION_READS is None
    assert loaded.HW_RADAR_APIFY_STORAGE_MAX_LIFETIME is None
    # None never releases (reconcile.release_unattached_reservations).
    assert loaded.HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S is None


@pytest.mark.parametrize("raw", ["", "2026-09-32", "yes"])
def test_empty_or_invalid_residual_acceptance_parses_to_none(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    loaded = _load_budget(monkeypatch, HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED=raw)
    assert loaded.HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED is None


def test_present_invalid_account_margin_is_nan_not_the_ten_percent_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for raw in ("", "ten", "-1"):
        margin = _load_budget(
            monkeypatch, HW_RADAR_APIFY_ACCOUNT_MARGIN_USD=raw
        ).HW_RADAR_APIFY_ACCOUNT_MARGIN_USD
        assert isinstance(margin, Decimal)
        assert margin.is_nan(), raw


def test_unknown_settlement_mode_parses_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = _load_budget(
        monkeypatch,
        HW_RADAR_APIFY_POST_RUN_COST_MODE="Bound",
        HW_RADAR_APIFY_RUN_USAGE_SETTLEMENT="",
    )
    assert loaded.HW_RADAR_APIFY_POST_RUN_COST_MODE is None
    assert loaded.HW_RADAR_APIFY_RUN_USAGE_SETTLEMENT is None


# ── Configured account state (MS2-D-48) ─────────────────────────────────────

_ACCOUNT_KEYS = (
    "HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR",
    "HW_RADAR_APIFY_ACCOUNT_LIMIT_USD",
    "HW_RADAR_APIFY_ACCOUNT_BASE_PRICE_USD",
    "HW_RADAR_APIFY_ACCOUNT_DATA_RETENTION_DAYS",
    "HW_RADAR_APIFY_ACCOUNT_VERIFIED_ON",
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-05T00:00:00Z", datetime(2026, 9, 5, tzinfo=UTC)),
        ("2026-09-05T00:00:00+00:00", datetime(2026, 9, 5, tzinfo=UTC)),
        (" 2026-09-01T00:00:00Z ", datetime(2026, 9, 1, tzinfo=UTC)),
        ("2026-02-28T00:00:00Z", datetime(2026, 2, 28, tzinfo=UTC)),
        ("", None),
        ("garbage", None),
        ("2026-09-05T00:00:00", None),  # naive
        ("2026-09-05T00:00:00+02:00", None),
        ("2026-09-05T00:00:01Z", None),
        ("2026-09-05T00:00:00.000001Z", None),
        ("2026-09-29T00:00:00Z", None),
        ("2026-09-30T00:00:00Z", None),
        ("2026-08-31T00:00:00Z", None),
        ("2026-09-05", None),  # date-only: no zone, so refused like a naive value
    ],
)
def test_billing_cycle_anchor_parsing(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: datetime | None
) -> None:
    loaded = _load_budget(monkeypatch, HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR=raw)
    anchor = loaded.HW_RADAR_APIFY_BILLING_CYCLE_ANCHOR
    assert anchor == expected
    if expected is not None:
        assert anchor.tzinfo is UTC


def test_account_settings_have_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = _load_budget(monkeypatch)
    for key in _ACCOUNT_KEYS:
        assert getattr(loaded, key) is None, key


_RETIRED_ACCOUNT_READ_KEYS = (
    "HW_RADAR_APIFY_MAX_ACCOUNT_READS_PER_CYCLE",
    "HW_RADAR_APIFY_MAX_DISCOVERY_READS",
    "HW_RADAR_APIFY_DISCOVERY_READ_INTERVAL_S",
    "HW_RADAR_APIFY_ACCOUNT_SNAPSHOT_MAX_AGE_S",
    "HW_RADAR_APIFY_USAGE_INCLUSION_LAG_S",
)


def test_retired_account_read_settings_are_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # MS2-D-48: nothing reads them; a value left in an environment is ignored.
    loaded = _load_budget(monkeypatch, **dict.fromkeys(_RETIRED_ACCOUNT_READ_KEYS, "5"))
    for key in _RETIRED_ACCOUNT_READ_KEYS:
        assert not hasattr(loaded, key), key
