"""Structured JSON logging.

Production logs are machine-parsed, so every record is a single JSON object on
one line.  Two things matter beyond formatting:

* **Correlation** - every record carries the ``request_id`` so one mobile
  request can be traced across web workers and Celery tasks.
* **Redaction** - phone numbers, OTPs, JWTs and Cloudinary secrets must never
  be written to disk.  :class:`SensitiveDataFilter` scrubs them centrally so a
  careless ``logger.info(payload)`` somewhere cannot leak them.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from typing import Any

# Set by RequestIDMiddleware and copied into Celery task headers.
current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)

_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)

_SENSITIVE_KEYS = re.compile(
    r"(otp|password|passwd|secret|token|authorization|api_key|api_secret|signature)",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"\b(\+?\d{2})?(\d{10})\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")


def mask_phone(phone: str) -> str:
    """``+919876543210`` -> ``+91******3210``. Enough to debug, not to dial."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 4:
        return "***"
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def _redact(value: Any, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEYS.search(key):
        return "***redacted***"
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        value = _JWT_RE.sub("***jwt***", value)
        value = _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), value)
        return value
    return value


class SensitiveDataFilter(logging.Filter):
    """Scrub secrets from both the message and any structured extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        for key, value in list(record.__dict__.items()):
            if key not in _STANDARD_ATTRS:
                record.__dict__[key] = _redact(value, key)
        return True


class RequestIDFilter(SensitiveDataFilter):
    """Attach correlation ids, then apply redaction."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id.get()
        if not hasattr(record, "user_id"):
            record.user_id = current_user_id.get()
        return super().filter(record)


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        return json.dumps(payload, default=str, ensure_ascii=False)
