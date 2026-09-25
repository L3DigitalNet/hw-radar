"""Django settings for Hardware Radar.

Single env-driven module - no settings package, no per-env files.
Environment contract (see .env.example for dev values):
  HW_RADAR_ENV              "dev" (default) | "production"
  DJANGO_SECRET_KEY         REQUIRED in production (rendered from OpenBao, ADR-0009)
  HW_RADAR_DB_NAME/_USER/_PASSWORD/_HOST/_PORT
  HW_RADAR_ALLOWED_HOSTS    REQUIRED in production (comma-separated public host(s));
                            no deployment host is hardcoded (public repo). CSRF
                            trusted origins are derived from it.
  HW_RADAR_STATIC_ROOT      optional collectstatic target override; defaults to
                            PRODUCTION_STATIC_ROOT in production (served by nginx)
                            and BASE_DIR/staticfiles otherwise
  HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES
                            optional; byte cap of one Apify dataset page body
                            (default 1048576). The importer derives its page size
                            from it (MS2-D-32 *Dataset page size*)
  HW_RADAR_APIFY_MAX_API_RESPONSE_BYTES
                            optional; byte cap of every other Apify response body
                            (control calls and KV records; default 262144)
  HW_RADAR_APIFY_MAX_DATASET_READS / HW_RADAR_APIFY_MAX_KV_READS
                            optional; per-run caps on full dataset reads and OUTPUT
                            record reads (default 3 each, MS2-D-32 *Operation caps*)
  HW_RADAR_APIFY_ENABLED    optional kill switch for new Actor starts; only the
                            literal "true" enables (default false). The apify-poll
                            job drains already-started runs either way (ED-07)
  HW_RADAR_APIFY_ACTOR_ID / HW_RADAR_APIFY_ACTOR_BUILD / HW_RADAR_APIFY_ACTOR_NAME
                            optional; the Actor a start targets (no default: an
                            unset id refuses every start), its build tag (default
                            "prod"), and the Actor name its OUTPUT must report
                            (default "hw-radar-synthetic-collector", MS2-D-38)
  HW_RADAR_APIFY_STORAGE_CLEANUP_MAX / HW_RADAR_APIFY_IMPORT_MARGIN
                            optional, in seconds; the remote-storage deadline after
                            admission (default 86400) and the import time a start's
                            timeout must leave before it (default 3600, MS2-D-33)
  HW_RADAR_APIFY_MAX_RUN_POLLS
                            optional; per-run cap on status polls (default 60,
                            MS2-D-32 *Run polls*)
  HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS
                            optional; per-run cap on storage-cleanup attempts
                            (default 10, MS2-D-32 *Operation caps*)
  HW_RADAR_APIFY_DELETE_404_IS_ABSENT
                            optional; only the literal "true" lets a 404 on a
                            storage delete count as deleted (default false,
                            MS2-D-25 *404 rule*; set only after the R25 probe)
  HW_RADAR_APIFY_* budget keys (Slice E, MS2-D-26/-32/-40/-41/-46)
                            optional; unit prices, the reservation margin, caps,
                            allocations, and ledger timings read by
                            acquisition.apify.budget. Parsed without raising: an
                            invalid value becomes None (or, for the account margin,
                            NaN) so budget admission denies instead of the process
                            failing to start. See the block at the end of the Apify
                            section for each key's absent/empty/invalid semantics
Production values arrive via the bao-agent tmpfs render (systemd
EnvironmentFile=/run/bao-agent/hw-radar.env) - never a plaintext file at rest.
"""

import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

ENV = os.environ.get("HW_RADAR_ENV", "dev")
IS_PRODUCTION = ENV == "production"
DEBUG = not IS_PRODUCTION

SECRET_KEY = (
    os.environ["DJANGO_SECRET_KEY"]
    if IS_PRODUCTION
    else os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key")
)

# No deployment host is baked in (public repo — AGENTS.md forbids committed
# hostnames). Production declares its public host(s) via HW_RADAR_ALLOWED_HOSTS
# (rendered from OpenBao); missing = fail loud, like SECRET_KEY. Loopback is always
# allowed so the on-CT /healthz smoke test resolves. CSRF origins derive from the
# configured public hosts only — never the loopback entries.
_LOCAL_HOSTS = ["localhost", "127.0.0.1"]
if IS_PRODUCTION:
    _public_hosts = [
        h.strip() for h in os.environ["HW_RADAR_ALLOWED_HOSTS"].split(",") if h.strip()
    ]
else:
    _public_hosts = [
        h.strip() for h in os.environ.get("HW_RADAR_ALLOWED_HOSTS", "").split(",") if h.strip()
    ]
ALLOWED_HOSTS = [*_public_hosts, *_LOCAL_HOSTS]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Required by Django's postgres.E005 system check for the ArrayField
    # requirement columns on catalog.Watch and its requirement satellites.
    "django.contrib.postgres",
    "hw_radar.accounts",
    "hw_radar.catalog",
    "hw_radar.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hw_radar.urls"

TEMPLATES: list[dict[str, object]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "hw_radar.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("HW_RADAR_DB_NAME", "hw_radar"),
        "USER": os.environ.get("HW_RADAR_DB_USER", "hw_radar"),
        "PASSWORD": os.environ.get("HW_RADAR_DB_PASSWORD", "hw_radar"),
        "HOST": os.environ.get("HW_RADAR_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("HW_RADAR_DB_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"

# ADR-0005: Argon2id first (argon2-cffi's default variant is argon2id).
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 16},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
# Production collects static files OUTSIDE /opt/hw-radar (bug 001). The app root
# is hwradar:hwradar 0750 and its group can read the bao-agent secret render, so
# the two ways to let nginx (www-data) into it — joining group hwradar, or ACLs
# on the app root — would also hand nginx the application secrets. This path is
# deploy-owned 0755 with no hwradar link. Cross-file contract: must equal the
# `alias` in deploy/nginx/hw-radar.conf and the directory created in
# docs/runbooks/provisioning.md; deploy/deploy-remote.sh reads the effective
# value from these settings. tests/unit/test_static_root_contract.py pins them.
PRODUCTION_STATIC_ROOT = Path("/var/lib/hw-radar/staticfiles")
STATIC_ROOT = Path(
    os.environ.get(
        "HW_RADAR_STATIC_ROOT",
        PRODUCTION_STATIC_ROOT if IS_PRODUCTION else BASE_DIR / "staticfiles",
    )
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# MS2-D-32 *Response caps*: the most bytes one Apify dataset page body may hold
# (1 MiB, an assumption). Cross-file contract: acquisition.apify.provider sends
# page_limit(this, MAX_LISTING_ROW_BYTES) as every page's `limit`, so a
# contract-valid dataset can never produce a page over this cap; and
# acquisition.apify.client reads this same setting as its dataset-page body cap.
HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES = int(
    os.environ.get("HW_RADAR_APIFY_MAX_DATASET_PAGE_BYTES", "1048576")
)
# MS2-D-32 *Response caps*: the most bytes any other Apify response body may
# hold (256 KiB, an assumption) -- run, build, abort, account, delete, and KV
# record calls. Valid content never reaches it, so the client raises on a
# larger body and E4 trips the latch (`api_response_over_cap`). Cross-file
# contract: acquisition.apify.client reads this setting at construction, and
# Slice E prices every API call at wire_bytes(this).
HW_RADAR_APIFY_MAX_API_RESPONSE_BYTES = int(
    os.environ.get("HW_RADAR_APIFY_MAX_API_RESPONSE_BYTES", "262144")
)
# MS2-D-32 *Operation caps* (3 each, an assumption): the most full dataset reads
# and OUTPUT record reads one provider_run may spend. The importer counts each
# read before sending it, so a crash loop cannot re-read a paid dataset without
# bound; at the cap the import is rejected with read_cap_exhausted.
HW_RADAR_APIFY_MAX_DATASET_READS = int(os.environ.get("HW_RADAR_APIFY_MAX_DATASET_READS", "3"))
HW_RADAR_APIFY_MAX_KV_READS = int(os.environ.get("HW_RADAR_APIFY_MAX_KV_READS", "3"))
# MS2-D-17 kill switch, read by acquisition.apify.jobs before budget admission.
# Anything but the literal "true" (a typo, "1", "yes") keeps starts off: a
# misspelled value must never be what makes paid execution possible. It gates
# new starts only; the apify-poll selectors keep draining imports and storage
# for runs that were already admitted (ED-07).
HW_RADAR_APIFY_ENABLED = os.environ.get("HW_RADAR_APIFY_ENABLED", "").strip().lower() == "true"
# The Actor a start targets (MS2-D-38 *Deploy*). The id is account configuration
# and is never committed, so it has no default and an empty value refuses every
# start. The build tag is what `apify` promotes; the name is the one the run's
# OUTPUT.provider.actorName must echo, or classify_run fails it as scope_mismatch.
HW_RADAR_APIFY_ACTOR_ID = os.environ.get("HW_RADAR_APIFY_ACTOR_ID", "")
HW_RADAR_APIFY_ACTOR_BUILD = os.environ.get("HW_RADAR_APIFY_ACTOR_BUILD", "prod")
HW_RADAR_APIFY_ACTOR_NAME = os.environ.get(
    "HW_RADAR_APIFY_ACTOR_NAME", "hw-radar-synthetic-collector"
)
# MS2-D-25/-33, in seconds: storage_cleanup_due_at = admitted_at +
# min(STORAGE_CLEANUP_MAX, bounded_ttl / 2) (24 h, an assumption), and a start is
# refused unless timeout_s + IMPORT_MARGIN fits before that deadline (1 h, an
# assumption), so a run that uses its whole timeout still leaves time to import.
HW_RADAR_APIFY_STORAGE_CLEANUP_MAX = int(
    os.environ.get("HW_RADAR_APIFY_STORAGE_CLEANUP_MAX", "86400")
)
HW_RADAR_APIFY_IMPORT_MARGIN = int(os.environ.get("HW_RADAR_APIFY_IMPORT_MARGIN", "3600"))
# MS2-D-32 *Run polls* (60, an assumption): the most `GET` run calls selector 1
# makes for one provider_run, each counted and committed before it is sent.
HW_RADAR_APIFY_MAX_RUN_POLLS = int(os.environ.get("HW_RADAR_APIFY_MAX_RUN_POLLS", "60"))
# MS2-D-32 *Operation caps* (10, an assumption): the most storage-cleanup
# attempts one provider_run may spend. An attempt is the whole MS2-D-33 overdue
# sequence (abort, confirming GET, one DELETE per storage not yet verified
# deleted), counted and committed before its first call; the start-option-
# mismatch abort is attempt 1. At the cap retries stop and the row is left
# `delete_failed` (E trips the latch on it).
HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS = int(os.environ.get("HW_RADAR_APIFY_MAX_DELETE_ATTEMPTS", "10"))
# MS2-D-25 *404 rule* (ED-19). Whether a scoped token answers 404 rather than
# 403 for storage it cannot access is unobserved; if it does, trusting a 404
# would verify a deletion while the storage kept accruing cost. So a 404 on
# delete counts as deleted only when this is true, which the operator sets
# after the R25 capability probe records a 403 for inaccessible storage. As
# with the kill switch, only the literal "true" enables it.
HW_RADAR_APIFY_DELETE_404_IS_ABSENT = (
    os.environ.get("HW_RADAR_APIFY_DELETE_404_IS_ABSENT", "").strip().lower() == "true"
)


# ── Slice E budget keys (read by acquisition.apify.budget.load_budget_settings) ──
#
# Every key below is parsed without raising. An absent key takes the default the
# plan states (or None where it states none); a present key that is empty or not
# a valid value becomes None, and budget admission denies on None with the reason
# the plan names (`pricing_unverified`, `unbounded_component`,
# `external_liability_unbounded`, `call_billing_residual_unaccepted`, or
# `budget_setting_invalid`). Rejected alternative: raising at import, as the
# older int() keys above do. That would also stop the apify-poll job, which must
# keep draining already-admitted runs while paid admission is off (ED-07), and a
# default substituted for a mis-rendered value would silently widen spend.
# Cross-file contract: acquisition.apify.budget reads each name through
# load_budget_settings and treats None exactly as described here.


def _env_decimal(name: str, default: str | None = None) -> Decimal | None:
    """Return a finite, non-negative Decimal; the default only when `name` is absent."""
    raw = os.environ.get(name)
    if raw is None:
        if default is None:
            return None
        raw = default
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    return value


def _env_int(name: str, default: int | None = None) -> int | None:
    """Return a non-negative int; the default only when `name` is absent."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def _env_choice(name: str, default: str, choices: tuple[str, ...]) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    return value if value in choices else None


def _env_date(name: str, default: date) -> date | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


# Unit prices (MS2-D-26). No live default: an absent, empty, or invalid price
# denies paid admission with `pricing_unverified`. The operator sets each from
# the official pricing page after re-verifying it for the account's plan. Source
# for every price: https://apify.com/pricing (retrieved 2026-09-24 for the plan;
# the same rates at https://docs.apify.com/platform/actors/publishing/monetize/pricing-and-costs,
# retrieved 2026-09-25). Figures the plan recorded for Free/Starter are noted per
# key; a key without one had no figure recorded and must be read off the page.
# Per-operation prices are per 1,000 operations, as the page lists them; storage
# is per GB-hour and transfer per GB, both priced by the estimator per decimal
# GB (10^9 bytes), which prices a byte higher than a GiB would.
# https://apify.com/pricing, 2026-09-24: compute unit, $0.20/CU on Free/Starter.
HW_RADAR_APIFY_USD_PER_CU = _env_decimal("HW_RADAR_APIFY_USD_PER_CU")
# https://apify.com/pricing, 2026-09-24: dataset reads per 1,000.
HW_RADAR_APIFY_DATASET_READS_USD_PER_1000 = _env_decimal(
    "HW_RADAR_APIFY_DATASET_READS_USD_PER_1000"
)
# https://apify.com/pricing, 2026-09-24: dataset writes per 1,000.
HW_RADAR_APIFY_DATASET_WRITES_USD_PER_1000 = _env_decimal(
    "HW_RADAR_APIFY_DATASET_WRITES_USD_PER_1000"
)
# https://apify.com/pricing, 2026-09-24: dataset timed storage per GB-hour.
HW_RADAR_APIFY_DATASET_STORAGE_USD_PER_GB_HOUR = _env_decimal(
    "HW_RADAR_APIFY_DATASET_STORAGE_USD_PER_GB_HOUR"
)
# https://apify.com/pricing, 2026-09-24: key-value store reads per 1,000.
HW_RADAR_APIFY_KV_READS_USD_PER_1000 = _env_decimal("HW_RADAR_APIFY_KV_READS_USD_PER_1000")
# https://apify.com/pricing, 2026-09-24: key-value store writes per 1,000
# ($0.05 on Starter, the plan's MS2-D-32 worked figure).
HW_RADAR_APIFY_KV_WRITES_USD_PER_1000 = _env_decimal("HW_RADAR_APIFY_KV_WRITES_USD_PER_1000")
# https://apify.com/pricing, 2026-09-24: key-value store timed storage per GB-hour.
HW_RADAR_APIFY_KV_STORAGE_USD_PER_GB_HOUR = _env_decimal(
    "HW_RADAR_APIFY_KV_STORAGE_USD_PER_GB_HOUR"
)
# https://apify.com/pricing, 2026-09-24: data transfer per GB. Holds the HIGHER
# of the external and internal prices (ED-01): every byte is priced
# direction-agnostically, because the docs do not say which transfers are which
# ($0.20/GB on Starter, the plan's MS2-D-32 worked figure).
HW_RADAR_APIFY_TRANSFER_USD_PER_GB = _env_decimal("HW_RADAR_APIFY_TRANSFER_USD_PER_GB")

# Reservation = (sum of component bounds) x (1 + MARGIN). The plan states no
# default, so an unset margin denies (`unbounded_component`) until the operator
# sets one. ESTIMATOR_VERSION is recorded on every reservation and latch event;
# bumping it clears the overrun latch (MS2-D-26). MAX_TIMEOUT_S caps a run's
# requested timeout_s (no default: unset denies every run start).
HW_RADAR_APIFY_MARGIN = _env_decimal("HW_RADAR_APIFY_MARGIN")
HW_RADAR_APIFY_ESTIMATOR_VERSION = os.environ.get("HW_RADAR_APIFY_ESTIMATOR_VERSION", "1").strip()
HW_RADAR_APIFY_MAX_TIMEOUT_S = _env_int("HW_RADAR_APIFY_MAX_TIMEOUT_S")

# MS2-D-40 allocation. The target must be <= 12.00 (the owner's operating
# target; budget denies a larger value). OPERATOR_ALLOWANCE 1.00 is the owner's
# setting (OQ29, 2026-09-25), so A = 12.00 - 1.00 = 11.00 at the defaults.
HW_RADAR_APIFY_CYCLE_TARGET_USD = _env_decimal("HW_RADAR_APIFY_CYCLE_TARGET_USD", "12.00")
HW_RADAR_APIFY_OPERATOR_ALLOWANCE_USD = _env_decimal(
    "HW_RADAR_APIFY_OPERATOR_ALLOWANCE_USD", "1.00"
)
HW_RADAR_APIFY_WATCH_REFRESH_RESERVE_USD = _env_decimal(
    "HW_RADAR_APIFY_WATCH_REFRESH_RESERVE_USD", "3.00"
)
HW_RADAR_APIFY_CASH_CEILING_USD = _env_decimal("HW_RADAR_APIFY_CASH_CEILING_USD", "20.00")


# Absent means "10% of the observed prepaid credit" (MS2-D-40), which is only
# known per snapshot, so absent parses to None. A present value that is empty or
# invalid must NOT fall back to that default, so it parses to Decimal("NaN"),
# which budget.account_margin_usd refuses (`budget_setting_invalid`).
def _env_account_margin() -> Decimal | None:
    name = "HW_RADAR_APIFY_ACCOUNT_MARGIN_USD"
    if name not in os.environ:
        return None
    value = _env_decimal(name)
    return Decimal("NaN") if value is None else value


HW_RADAR_APIFY_ACCOUNT_MARGIN_USD = _env_account_margin()
# OQ26 (owner, 2026-09-25): 5.00 per billing cycle, applied ONLY when the
# variable is absent. Empty, non-numeric, negative, or non-finite parses to None,
# and budget denies every class with `external_liability_unbounded`: a
# mis-rendered environment must fail closed, never fall back to 5.00.
HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD = _env_decimal(
    "HW_RADAR_APIFY_EXTERNAL_LIABILITY_USD", "5.00"
)
# R38 (MS2-D-26 *Reservation*): the date of the owner's acceptance of the
# call-billing residual. The owner accepted it on 2026-09-25, applied only when
# absent; empty or not an ISO date parses to None and denies every class with
# `call_billing_residual_unaccepted`.
HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED = _env_date(
    "HW_RADAR_APIFY_CALL_BILLING_RESIDUAL_ACCEPTED", date(2026, 9, 25)
)
# Operator class (MS2-D-46). The build bound is 4,096 MB x 1,800 s x $0.20/CU.
HW_RADAR_APIFY_OPERATOR_BUILD_BOUND_USD = _env_decimal(
    "HW_RADAR_APIFY_OPERATOR_BUILD_BOUND_USD", "0.41"
)
HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_ITEMS = _env_int(
    "HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_ITEMS", 1000
)
HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_RECORD_READS = _env_int(
    "HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_RECORD_READS", 20
)
# 10 MB, decimal (the plan's figure).
HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_BYTES = _env_int(
    "HW_RADAR_APIFY_OPERATOR_INSPECT_MAX_BYTES", 10_000_000
)
HW_RADAR_APIFY_OPERATOR_PROBE_MAX_CALLS = _env_int("HW_RADAR_APIFY_OPERATOR_PROBE_MAX_CALLS", 10)

# Caps and wire ceilings (MS2-D-32). The defaults are assumptions; an invalid
# value parses to None and denies with `unbounded_component`. MAX_KV_WRITES,
# MAX_KV_BYTES, and STORAGE_MAX_LIFETIME (seconds) have no default: live
# admission stays denied until the operator sets them (the lifetime from the
# account's dataRetentionDays, 31 days as verified 2026-09-24).
HW_RADAR_APIFY_MAX_KV_WRITES = _env_int("HW_RADAR_APIFY_MAX_KV_WRITES")
HW_RADAR_APIFY_MAX_KV_BYTES = _env_int("HW_RADAR_APIFY_MAX_KV_BYTES")
HW_RADAR_APIFY_STORAGE_MAX_LIFETIME = _env_int("HW_RADAR_APIFY_STORAGE_MAX_LIFETIME")
HW_RADAR_APIFY_API_CALL_OVERHEAD_BYTES = _env_int("HW_RADAR_APIFY_API_CALL_OVERHEAD_BYTES", 262144)
HW_RADAR_APIFY_MAX_CORRECTION_READS = _env_int("HW_RADAR_APIFY_MAX_CORRECTION_READS", 12)
HW_RADAR_APIFY_MAX_ACCOUNT_READS_PER_CYCLE = _env_int(
    "HW_RADAR_APIFY_MAX_ACCOUNT_READS_PER_CYCLE", 3000
)
HW_RADAR_APIFY_MAX_DISCOVERY_READS = _env_int("HW_RADAR_APIFY_MAX_DISCOVERY_READS", 24)
HW_RADAR_APIFY_DISCOVERY_READ_INTERVAL_S = _env_int("HW_RADAR_APIFY_DISCOVERY_READ_INTERVAL_S", 300)

# Ledger timings and settlement modes (MS2-D-40, -41, -45), in seconds.
# USAGE_INCLUSION_LAG_S has no default: unset (or invalid) means no inclusion
# watermark, so all reconciled cycle spend stays debited on top of the snapshot.
HW_RADAR_APIFY_ACCOUNT_SNAPSHOT_MAX_AGE_S = _env_int(
    "HW_RADAR_APIFY_ACCOUNT_SNAPSHOT_MAX_AGE_S", 900
)
HW_RADAR_APIFY_CYCLE_BOUNDARY_GUARD_S = _env_int("HW_RADAR_APIFY_CYCLE_BOUNDARY_GUARD_S", 3600)
HW_RADAR_APIFY_USAGE_SETTLE_DELAY_S = _env_int("HW_RADAR_APIFY_USAGE_SETTLE_DELAY_S", 10)
HW_RADAR_APIFY_USAGE_INCLUSION_LAG_S = _env_int("HW_RADAR_APIFY_USAGE_INCLUSION_LAG_S")
HW_RADAR_APIFY_USAGE_STABLE_READS = _env_int("HW_RADAR_APIFY_USAGE_STABLE_READS", 2)
HW_RADAR_APIFY_USAGE_STABLE_INTERVAL_S = _env_int("HW_RADAR_APIFY_USAGE_STABLE_INTERVAL_S", 60)
HW_RADAR_APIFY_USAGE_FINALIZE_DEADLINE_S = _env_int(
    "HW_RADAR_APIFY_USAGE_FINALIZE_DEADLINE_S", 86400
)
HW_RADAR_APIFY_CORRECTION_WINDOW_S = _env_int("HW_RADAR_APIFY_CORRECTION_WINDOW_S", 604800)
HW_RADAR_APIFY_POST_RUN_COST_MODE = _env_choice(
    "HW_RADAR_APIFY_POST_RUN_COST_MODE", "bound", ("bound", "counted")
)
HW_RADAR_APIFY_RUN_USAGE_SETTLEMENT = _env_choice(
    "HW_RADAR_APIFY_RUN_USAGE_SETTLEMENT", "bound", ("bound", "stable_reads")
)
# How long an admitted runtime reservation may stay without a provider_run
# before the apify-poll tick releases it (reconcile.release_unattached_reservations).
# The window it covers, ledger.reserve's commit to jobs._create_run's, is a
# few local steps with no Apify call; 900 s (an assumption) is far above it,
# so a slow but live start is not released under itself. Correctness never
# rests on the grace: _create_run re-checks the row under the budget lock and
# refuses a released one. Invalid means never release (the row keeps counting).
HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S = _env_int(
    "HW_RADAR_APIFY_UNATTACHED_RESERVATION_GRACE_S", 900
)
# Per-environment ledger identity (MS2-D-45); no default, never a secret.
HW_RADAR_APIFY_LEDGER_ID = os.environ.get("HW_RADAR_APIFY_LEDGER_ID", "").strip()

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = IS_PRODUCTION
CSRF_COOKIE_SECURE = IS_PRODUCTION
X_FRAME_OPTIONS = "DENY"

if IS_PRODUCTION:
    # Same-origin server-rendered app; no CORS surface exists at MS-0. Trust the
    # configured public host(s) as https origins — loopback is deliberately excluded.
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in _public_hosts]
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
