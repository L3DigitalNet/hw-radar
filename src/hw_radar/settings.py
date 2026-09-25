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
Production values arrive via the bao-agent tmpfs render (systemd
EnvironmentFile=/run/bao-agent/hw-radar.env) - never a plaintext file at rest.
"""

import os
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
