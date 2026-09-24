"""Django settings for Hardware Radar.

Single env-driven module - no settings package, no per-env files.
Environment contract (see .env.example for dev values):
  HW_RADAR_ENV              "dev" (default) | "production"
  DJANGO_SECRET_KEY         REQUIRED in production (rendered from OpenBao, ADR-0009)
  HW_RADAR_DB_NAME/_USER/_PASSWORD/_HOST/_PORT
  HW_RADAR_ALLOWED_HOSTS    REQUIRED in production (comma-separated public host(s));
                            no deployment host is hardcoded (public repo). CSRF
                            trusted origins are derived from it.
  HW_RADAR_STATIC_ROOT      collectstatic target (prod: served by nginx)
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
STATIC_ROOT = Path(os.environ.get("HW_RADAR_STATIC_ROOT", BASE_DIR / "staticfiles"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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
