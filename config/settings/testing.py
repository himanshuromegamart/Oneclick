"""Settings used by the automated test suite.

The suite must never touch Redis, Cloudinary or the NimbusIT SMS gateway, so
every external dependency is swapped for an in-memory or fake implementation.
"""

from __future__ import annotations

from .base import *
from .base import LOGGING, OTP_SETTINGS, REST_FRAMEWORK, STORAGES

DEBUG = False
ALLOWED_HOSTS = ["*"]
SECRET_KEY = "test-only-secret-key"  # noqa: S105 - never used outside the suite

# In-memory cache: no Redis dependency, and state resets between test cases.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "sarah-aqua-tests",
    }
}

# Tasks run inline so tests can assert on their effects without a worker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_RESULT_BACKEND = "cache"
CELERY_CACHE_BACKEND = "default"

# Fakes for the two outbound integrations.
SMS_SETTINGS = {**SMS_SETTINGS, "BACKEND": "apps.accounts.sms.InMemorySMSBackend"}
CLOUDINARY = {
    **CLOUDINARY,
    "CLOUD_NAME": "test-cloud",
    "API_KEY": "test-key",
    "API_SECRET": "test-secret",
}
STORAGE_BACKEND = "apps.files.storage.InMemoryStorageBackend"

OTP_SETTINGS = {**OTP_SETTINGS, "DEBUG_BYPASS_CODE": ""}

# Throttling is exercised by dedicated tests that re-enable it explicitly.
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": ()}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Plain static storage, not the hashed-manifest one. The manifest backend
# refuses to resolve any file that collectstatic has not processed, so every
# admin page would raise "Missing staticfiles manifest entry" in a test run.
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOGGING["root"]["level"] = "ERROR"
LOGGING["handlers"]["console"]["formatter"] = "plain"
