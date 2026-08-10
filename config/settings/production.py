"""Production settings.

Every value that could weaken security is required from the environment rather
than defaulted, so a misconfigured deploy fails at boot instead of silently
running insecurely.
"""

from __future__ import annotations

from importlib.util import find_spec

import dj_database_url
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration

from config.env import ImproperlyConfigured, env_bool, env_int, env_list, env_str

from .base import *

DEBUG = False

# No defaults: a production boot without these must fail.
SECRET_KEY = env_str("DJANGO_SECRET_KEY")

# Render injects RENDER_EXTERNAL_HOSTNAME with the service's public hostname.
# Appending it means the app answers on its own URL without anyone having to
# remember to set ALLOWED_HOSTS after the first deploy.
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", [])
_RENDER_HOST = env_str("RENDER_EXTERNAL_HOSTNAME", "")
if _RENDER_HOST and _RENDER_HOST not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_RENDER_HOST)
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "Set DJANGO_ALLOWED_HOSTS (or deploy on Render, which provides "
        "RENDER_EXTERNAL_HOSTNAME)."
    )

# ---------------------------------------------------------------------------
# CORS
#
# A native Android/iOS app sends no Origin header, so CORS does not apply to it
# at all - these settings only matter for a browser. Allowing every origin is
# therefore acceptable here *because* the API carries no cookies and no session:
# every request is authorised by an explicit Bearer token, which a browser will
# not attach automatically. CORS_ALLOW_CREDENTIALS stays False (set in base.py),
# which is what makes that true.
#
# Set CORS_ALLOWED_ORIGINS to lock it down if a web client is ever added.
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", [])
CORS_ALLOW_ALL_ORIGINS = env_bool("CORS_ALLOW_ALL_ORIGINS", not CORS_ALLOWED_ORIGINS)

SIMPLE_JWT = {**SIMPLE_JWT, "SIGNING_KEY": env_str("JWT_SIGNING_KEY")}

# ---------------------------------------------------------------------------
# Transport security
#
# TLS terminates at NGINX, which forwards X-Forwarded-Proto.  Django must trust
# that header to know the original request was HTTPS.
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)

# The load balancer's health probe reaches the container over plain HTTP from
# inside the network. Without this exemption Django would answer the probe with
# a 301 to https, the platform would read that as "unhealthy", and it would
# restart a perfectly good container in a loop.
SECURE_REDIRECT_EXEMPT = [r"^live/$", r"^ready/$", r"^health/$"]
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31_536_000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------------------
# Admin session security
#
# The admin is the only part of this service that uses cookies, and it is also
# the part that can change anything. Both cookies are HTTPS-only here, so a
# session cannot be lifted off a plain-HTTP request.
# ---------------------------------------------------------------------------
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Django checks the Origin header on unsafe requests over HTTPS. Without the
# host listed here, logging into the admin fails with "CSRF verification
# failed" even though the credentials are right.
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if "*" not in host]
if _RENDER_HOST:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_RENDER_HOST}")
CSRF_TRUSTED_ORIGINS.extend(env_list("CSRF_TRUSTED_ORIGINS", []))
CSRF_TRUSTED_ORIGINS = sorted(set(CSRF_TRUSTED_ORIGINS))

# ---------------------------------------------------------------------------
# Database
#
# Managed providers (Neon, Render Postgres, Supabase) hand out a single
# connection URL, so DATABASE_URL is the preferred input. The discrete
# POSTGRES_* variables remain as a fallback for a self-hosted server.
# ---------------------------------------------------------------------------
_DATABASE_URL = env_str("DATABASE_URL", "")

if _DATABASE_URL:
    DATABASES["default"] = dj_database_url.parse(
        _DATABASE_URL,
        conn_max_age=env_int("POSTGRES_CONN_MAX_AGE", 120),
        conn_health_checks=True,
        # Neon terminates unencrypted connections, and so should we.
        ssl_require=True,
    )
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"]["connect_timeout"] = 10
else:
    DATABASES["default"].update(
        {
            "NAME": env_str("POSTGRES_DB"),
            "USER": env_str("POSTGRES_USER"),
            "PASSWORD": env_str("POSTGRES_PASSWORD"),
            "HOST": env_str("POSTGRES_HOST"),
            "PORT": env_str("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 120),
            "OPTIONS": {
                "sslmode": env_str("POSTGRES_SSLMODE", "require"),
                "connect_timeout": 10,
            },
        }
    )

# ---------------------------------------------------------------------------
# Cache
#
# Redis is optional. Without it the app still serves every request - the folder
# tree is simply recomputed instead of cached, and throttle counters live in
# process memory. That is fine for one instance; with several, per-process
# counters mean each replica enforces the limits separately, so add Redis
# before scaling out.
# ---------------------------------------------------------------------------
if not env_str("REDIS_URL", ""):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "sarah-aqua-fallback",
        }
    }

# ---------------------------------------------------------------------------
# Required integration credentials
# ---------------------------------------------------------------------------
CLOUDINARY = {
    **CLOUDINARY,
    "CLOUD_NAME": env_str("CLOUDINARY_CLOUD_NAME"),
    "API_KEY": env_str("CLOUDINARY_API_KEY"),
    "API_SECRET": env_str("CLOUDINARY_API_SECRET"),
}

SMS_SETTINGS = {
    **SMS_SETTINGS,
    "USER_ID": env_str("SMS_USER_ID"),
    "PASSWORD": env_str("SMS_PASSWORD"),
    "SENDER_ID": env_str("SMS_SENDER_ID"),
}

# A bypass code in production would be a permanent backdoor into every account.
OTP_SETTINGS = {**OTP_SETTINGS, "DEBUG_BYPASS_CODE": ""}

# ---------------------------------------------------------------------------
# Observability
#
# Prometheus metrics are a nice-to-have, not a requirement. Wiring them in only
# when the package is present means a slimmer install (or a host that has not
# got it yet) still boots instead of crashing at import time.
# ---------------------------------------------------------------------------
if find_spec("django_prometheus") is not None:
    INSTALLED_APPS = ["django_prometheus", *INSTALLED_APPS]
    MIDDLEWARE = [
        "django_prometheus.middleware.PrometheusBeforeMiddleware",
        *MIDDLEWARE,
        "django_prometheus.middleware.PrometheusAfterMiddleware",
    ]

_SENTRY_DSN = env_str("SENTRY_DSN", "")
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        environment=env_str("SENTRY_ENVIRONMENT", "production"),
        release=env_str("APP_VERSION", "unknown"),
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=float(env_str("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
        # PII must never leave the boundary: phone numbers and JWTs are PII.
        send_default_pii=False,
        max_request_body_size="never",
    )
