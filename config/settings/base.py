"""Base Django settings shared by every environment.

Environment-specific modules (``development``, ``testing``, ``production``)
import everything from here and override only what differs.  Nothing in this
module may contain a real secret: every sensitive value is read from the
environment via :mod:`config.env`.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from config.env import env_bool, env_int, env_list, env_str

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "insecure-development-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["*"])

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    # The admin site is the owner's console: with no shell on the hosting plan,
    # it is the only way to inspect and repair data directly. It brings
    # sessions, messages and CSRF along with it - all of which the JSON API
    # itself neither uses nor needs.
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.postgres",  # SearchVector, ArrayField, GIN/trigram indexes
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "django_celery_beat",
    "django_celery_results",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.folders",  # categories and subcategories
    "apps.files",  # documents, plus search and sharing
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django_structlog.middlewares.RequestMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    # CSRF is here for the admin site's HTML forms. It does not affect the API:
    # DRF wraps every APIView in csrf_exempt, and the API authenticates with a
    # Bearer token that no browser attaches on its own.
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "apps.core.middleware.CurrentRequestMiddleware",
    "apps.core.middleware.APIExceptionMiddleware",
]

# The admin lives at a configurable path. Moving it off the default /admin/
# will not stop a determined attacker, but it does remove the service from the
# large volume of untargeted scanning traffic aimed at that exact URL.
ADMIN_URL = env_str("ADMIN_URL", "admin/")

# Session cookies exist only for the admin. Locking them down costs nothing.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# How long an admin stays signed in. 12 hours covers a working day without
# asking for the password again mid-task.
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 12)

# Keep this False, or SESSION_COOKIE_AGE above is silently meaningless: with it
# True, Django writes a cookie carrying no expiry at all, so the browser drops
# it whenever it decides the session ended. Mobile browsers do that eagerly -
# switching apps can be enough - which reads as being logged out instantly.
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", False)

# Refresh the cookie on each request, so an admin who is actively working is
# never signed out mid-edit.
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_HTTPONLY = False  # the admin's JavaScript reads this one
CSRF_COOKIE_SAMESITE = "Lax"

# Deliberate design decisions, not oversights - silenced so a real warning is
# not lost in the noise. drf-spectacular cannot introspect a plain ViewSet's
# queryset; every action declares its request and response explicitly with
# @extend_schema, and tests/test_api_surface.py asserts the schema builds.
SILENCED_SYSTEM_CHECKS = [
    "drf_spectacular.W001",
    "drf_spectacular.W002",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                # Required by the admin, which uses the messages framework for
                # its "saved successfully" banners.
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env_str("POSTGRES_DB", "sarah_aqua_erp"),
        "USER": env_str("POSTGRES_USER", "sarah"),
        "PASSWORD": env_str("POSTGRES_PASSWORD", "sarah"),
        "HOST": env_str("POSTGRES_HOST", "localhost"),
        "PORT": env_str("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        "ATOMIC_REQUESTS": False,
        "OPTIONS": {},
    }
}

# ---------------------------------------------------------------------------
# Cache / Redis
# ---------------------------------------------------------------------------
REDIS_URL = env_str("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "IGNORE_EXCEPTIONS": True,
            "SOCKET_CONNECT_TIMEOUT": 3,
            "SOCKET_TIMEOUT": 3,
        },
        "KEY_PREFIX": "sas",
    }
}
DJANGO_REDIS_IGNORE_EXCEPTIONS = True

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env_str("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "Asia/Kolkata"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 15 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 13 * 60
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "apps.files.tasks.*": {"queue": "media"},
    "apps.accounts.tasks.*": {"queue": "default"},
}
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("apps.accounts.authentication.ActiveUserJWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.CursorPageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_PARSER_CLASSES": (
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ),
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Throttling is off. Turn it back on with THROTTLING_ENABLED=true - the
    # classes and rates below are kept intact so that is a one-variable change
    # rather than a rewrite.
    #
    # What this does NOT switch off: the per-account lockouts on OTP and
    # password login. Those live in the service layer and count failed
    # credential attempts, which is the protection that actually matters
    # against someone guessing their way into an account.
    "DEFAULT_THROTTLE_CLASSES": (
        (
            "apps.core.throttling.ScopedBurstThrottle",
            "apps.core.throttling.ScopedSustainedThrottle",
        )
        if env_bool("THROTTLING_ENABLED", False)
        else ()
    ),
    "DEFAULT_THROTTLE_RATES": {
        "burst": "90/min",
        "sustained": "3000/day",
        "otp_request": "5/hour",
        "otp_verify": "10/hour",
        "anon": "30/min",
        "upload": "120/hour",
        "download": "300/hour",
        "search": "60/min",
        # Deliberately tight. This endpoint creates accounts, so a slow drip is
        # the difference between a guessable key being a risk and being a
        # certainty: 10 tries an hour makes even a weak key impractical to
        # brute-force remotely.
        "setup": "10/hour",
        # Keyed by phone number, so a distributed attack on one account is
        # still bounded and a shared office IP does not lock everyone out.
        "login": "20/hour",
    },
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "UNAUTHENTICATED_USER": None,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Sarah Aqua Soft ERP API",
    "DESCRIPTION": (
        "Mobile-first document management and quotation backend. "
        "All endpoints are versioned under /api/v1/ and authenticated with JWT."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v[0-9]",
    "SORT_OPERATIONS": False,
    "ENUM_NAME_OVERRIDES": {
        "UserRoleEnum": "apps.accounts.constants.UserRole.choices",
    },
}

# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_MINUTES", 30)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("JWT_REFRESH_DAYS", 30)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env_str("JWT_SIGNING_KEY", SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_TYPE_CLAIM": "token_type",
    "JTI_CLAIM": "jti",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
}

# ---------------------------------------------------------------------------
# OTP / SMS  (NimbusIT)
# ---------------------------------------------------------------------------
OTP_SETTINGS = {
    "LENGTH": env_int("OTP_LENGTH", 6),
    "TTL_SECONDS": env_int("OTP_TTL_SECONDS", 300),
    "MAX_VERIFY_ATTEMPTS": env_int("OTP_MAX_VERIFY_ATTEMPTS", 5),
    "RESEND_COOLDOWN_SECONDS": env_int("OTP_RESEND_COOLDOWN_SECONDS", 60),
    "MAX_SENDS_PER_DAY": env_int("OTP_MAX_SENDS_PER_DAY", 10),
    "LOCKOUT_SECONDS": env_int("OTP_LOCKOUT_SECONDS", 1800),
    "DEBUG_BYPASS_CODE": env_str("OTP_DEBUG_BYPASS_CODE", ""),
}

# NimbusIT SMS gateway.
#
# Credentials come from the environment - never hard-coded here, so the repo
# stays safe to share and the values can be rotated without a code change.
#
# NOTE ON TRANSPORT: the provider's documented endpoint is plain HTTP, which
# puts the OTP on the wire in clear text. `SMS_BASE_URL` defaults to the https
# form; if NimbusIT rejects it, override the variable with the http URL. Doing
# it that way keeps the insecure choice explicit and reversible rather than
# baked into the code.
# ---------------------------------------------------------------------------
# Setup endpoint
#
# Creating the first account needs shell access, which some hosts put behind a
# paid plan. SETUP_KEY enables a guarded endpoint that can create users over
# HTTP instead.
#
# The key is read from the environment and never hard-coded: this repository is
# public, and a key committed to it would be a permanent, world-readable way
# into every account.
#
# Leaving SETUP_KEY empty disables the endpoint entirely - which is what it
# should be once the accounts you need exist.
# ---------------------------------------------------------------------------
SETUP_KEY = env_str("SETUP_KEY", "")
SETUP_KEY_MIN_LENGTH = env_int("SETUP_KEY_MIN_LENGTH", 8)

#: Master switch for request throttling. Read by the views that opt in to a
#: scope of their own, so one variable governs all of it.
THROTTLING_ENABLED = env_bool("THROTTLING_ENABLED", False)

# ---------------------------------------------------------------------------
# Password login
#
# A second way in, alongside OTP. Useful when the SMS gateway is unavailable,
# and the only practical option where sending an SMS is not possible at all.
#
# A password is a *standing* credential - unlike an OTP it does not expire on
# its own - so it needs its own brute-force protection rather than borrowing
# the OTP flow's.
# ---------------------------------------------------------------------------
LOGIN_SETTINGS = {
    "MAX_FAILED_ATTEMPTS": env_int("LOGIN_MAX_FAILED_ATTEMPTS", 5),
    "LOCKOUT_SECONDS": env_int("LOGIN_LOCKOUT_SECONDS", 900),
    "MIN_PASSWORD_LENGTH": env_int("MIN_PASSWORD_LENGTH", 8),
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": LOGIN_SETTINGS["MIN_PASSWORD_LENGTH"]},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SMS_SETTINGS = {
    "BACKEND": env_str("SMS_BACKEND", "apps.accounts.sms.NimbusITSMSBackend"),
    "BASE_URL": env_str("SMS_BASE_URL", "https://nimbusit.biz/api/SmsApi/SendSingleApi"),
    "USER_ID": env_str("SMS_USER_ID", ""),
    "PASSWORD": env_str("SMS_PASSWORD", ""),
    "SENDER_ID": env_str("SMS_SENDER_ID", ""),
    # DLT registration - mandatory for transactional SMS in India.
    "ENTITY_ID": env_str("SMS_ENTITY_ID", ""),
    "TEMPLATE_ID": env_str("SMS_TEMPLATE_ID", ""),
    # Must match the approved DLT template word for word, or the operator
    # rejects the message.
    "TEMPLATE": env_str(
        "SMS_OTP_TEMPLATE",
        "{otp} is your OTP for login. Do not share it with anyone.",
    ),
    "TIMEOUT_SECONDS": env_int("SMS_TIMEOUT_SECONDS", 10),
}

# ---------------------------------------------------------------------------
# Cloudinary
# ---------------------------------------------------------------------------
CLOUDINARY = {
    "CLOUD_NAME": env_str("CLOUDINARY_CLOUD_NAME", ""),
    "API_KEY": env_str("CLOUDINARY_API_KEY", ""),
    "API_SECRET": env_str("CLOUDINARY_API_SECRET", ""),
    "SECURE": True,
    "UPLOAD_FOLDER": env_str("CLOUDINARY_UPLOAD_FOLDER", "sarah-aqua-soft"),
    "SIGNED_URL_TTL_SECONDS": env_int("CLOUDINARY_SIGNED_URL_TTL", 900),
    "UPLOAD_SIGNATURE_TTL_SECONDS": env_int("CLOUDINARY_UPLOAD_SIGNATURE_TTL", 600),
}

STORAGE_SETTINGS = {
    "MAX_UPLOAD_BYTES": env_int("MAX_UPLOAD_BYTES", 200 * 1024 * 1024),
    "RECYCLE_BIN_RETENTION_DAYS": env_int("RECYCLE_BIN_RETENTION_DAYS", 30),
    "MAX_FILE_VERSIONS": env_int("MAX_FILE_VERSIONS", 20),
    "SHARE_LINK_TTL_HOURS": env_int("SHARE_LINK_TTL_HOURS", 168),
    "ALLOWED_EXTENSIONS": env_list(
        "ALLOWED_EXTENSIONS",
        [
            "pdf",
            "doc",
            "docx",
            "xls",
            "xlsx",
            "ppt",
            "pptx",
            "csv",
            "txt",
            "rtf",
            "jpg",
            "jpeg",
            "png",
            "webp",
            "gif",
            "heic",
            "bmp",
            "tiff",
            "mp4",
            "mov",
            "avi",
            "mkv",
            "webm",
            "zip",
            "rar",
            "7z",
            "dwg",
            "dxf",
        ],
    ),
    "BLOCKED_EXTENSIONS": env_list(
        "BLOCKED_EXTENSIONS",
        ["exe", "bat", "cmd", "sh", "msi", "dll", "so", "js", "jar", "ps1", "vbs", "scr"],
    ),
}

# ---------------------------------------------------------------------------
# Domain tuning
# ---------------------------------------------------------------------------
FOLDER_SETTINGS = {
    "MAX_DEPTH": env_int("FOLDER_MAX_DEPTH", 32),
    "MAX_CHILDREN_PER_FOLDER": env_int("FOLDER_MAX_CHILDREN", 5000),
    "TREE_CACHE_SECONDS": env_int("FOLDER_TREE_CACHE_SECONDS", 300),
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOW_ALL_ORIGINS = env_bool("CORS_ALLOW_ALL_ORIGINS", False)
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", [])
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "origin",
    "user-agent",
    "x-requested-with",
    "x-device-id",
    "x-app-version",
    "x-platform",
    "x-request-id",
    "x-idempotency-key",
]

# ---------------------------------------------------------------------------
# i18n / static
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Logging (structured JSON)
# ---------------------------------------------------------------------------
LOG_LEVEL = env_str("LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "apps.core.logging.JSONFormatter",
        },
        "plain": {
            "format": "%(asctime)s %(levelname)-8s %(name)s %(message)s",
        },
    },
    "filters": {
        "request_id": {"()": "apps.core.logging.RequestIDFilter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": env_str("LOG_FORMATTER", "json"),
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "django.security": {"level": "INFO", "propagate": True},
        "apps": {"level": LOG_LEVEL, "propagate": True},
        "celery": {"level": "INFO", "propagate": True},
    },
}

DATA_UPLOAD_MAX_MEMORY_SIZE = STORAGE_SETTINGS["MAX_UPLOAD_BYTES"]
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
