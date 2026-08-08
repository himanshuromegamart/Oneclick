"""Local development settings.

Optimised for fast feedback, never for safety.  Nothing here may leak into a
deployed environment: ``DJANGO_SETTINGS_MODULE`` selects the module explicitly
and production defaults to :mod:`config.settings.production`.
"""

from __future__ import annotations

from importlib.util import find_spec

from config.env import env_bool, env_list

from .base import *
from .base import LOGGING, REST_FRAMEWORK

DEBUG = True
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["*"])

# Human-readable logs beat JSON when you are reading a terminal.
LOGGING["handlers"]["console"]["formatter"] = "plain"
LOGGING["root"]["level"] = "DEBUG"

# Browsable API is handy locally; it is never enabled in production.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ),
}

CORS_ALLOW_ALL_ORIGINS = True

# Throttles get in the way while developing against the API from a device.
if env_bool("DISABLE_THROTTLING", True):
    REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = ()

# Optional convenience app. Added only when installed, so `manage.py` still
# runs for someone who installed requirements/base.txt rather than
# requirements/development.txt.
if find_spec("django_extensions") is not None:
    INSTALLED_APPS = [*INSTALLED_APPS, "django_extensions"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
