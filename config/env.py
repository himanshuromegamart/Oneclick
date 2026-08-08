"""Typed environment-variable readers.

Every setting that varies by environment or is sensitive must come through one
of these helpers.  Keeping the coercion in one place means a malformed value
fails loudly at boot rather than silently behaving as ``False``/``0`` deep
inside a request.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ``.env`` is for local development only; in production the values are injected
# by the orchestrator and no file exists.  ``override=False`` guarantees real
# environment variables always win over the file.
load_dotenv(_PROJECT_ROOT / ".env", override=False)


class ImproperlyConfigured(Exception):
    """Raised when a required environment variable is missing or malformed."""


_MISSING = object()

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f", ""}


def env_str(name: str, default: str | object = _MISSING) -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        if default is _MISSING:
            raise ImproperlyConfigured(f"Required environment variable {name!r} is not set.")
        return default  # type: ignore[return-value]
    return raw


def env_bool(name: str, default: bool | object = _MISSING) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        if default is _MISSING:
            raise ImproperlyConfigured(f"Required environment variable {name!r} is not set.")
        return bool(default)
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ImproperlyConfigured(f"Environment variable {name!r} is not a valid boolean: {raw!r}")


def env_int(name: str, default: int | object = _MISSING) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        if default is _MISSING:
            raise ImproperlyConfigured(f"Required environment variable {name!r} is not set.")
        return int(default)  # type: ignore[arg-type]
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"Environment variable {name!r} is not a valid integer: {raw!r}"
        ) from exc


def env_list(name: str, default: list[str] | object = _MISSING, sep: str = ",") -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        if default is _MISSING:
            raise ImproperlyConfigured(f"Required environment variable {name!r} is not set.")
        return list(default)  # type: ignore[arg-type]
    return [item.strip() for item in raw.split(sep) if item.strip()]
