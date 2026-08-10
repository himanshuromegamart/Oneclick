"""Rate limiting.

Two layers protect the API:

1. **Global burst + sustained** throttles on every endpoint, keyed by user (or
   IP when anonymous).  These stop a runaway client or a scraper.
2. **Scoped** throttles on the expensive or abusable endpoints - OTP request,
   OTP verify, upload, download, search - configured per view via
   ``throttle_scope``.

OTP throttling additionally happens *inside* the service layer keyed by phone
number (see :mod:`apps.accounts.services`), because an attacker can rotate IPs
but not the victim's phone number.  The two layers are complementary: this one
protects the server, that one protects the account.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


def throttles(*classes: type) -> tuple[type, ...]:
    """Return ``classes`` only when throttling is switched on.

    Views declare their throttles through this helper so that
    ``THROTTLING_ENABLED`` governs every one of them from a single place. The
    alternative - commenting out ``throttle_classes`` on each view - is how you
    end up with three of them still enforcing limits nobody expects.
    """
    return classes if getattr(settings, "THROTTLING_ENABLED", False) else ()


class _IdentityMixin:
    """Shared key derivation: authenticated user id, else client IP."""

    def _identity(self, request: Any) -> str:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return f"user:{user.pk}"
        return f"ip:{self.get_ident(request)}"  # type: ignore[attr-defined]


class ScopedBurstThrottle(_IdentityMixin, SimpleRateThrottle):
    """Short-window ceiling that absorbs retry storms."""

    scope = "burst"

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self._identity(request)}


class ScopedSustainedThrottle(_IdentityMixin, SimpleRateThrottle):
    """Daily ceiling that catches slow, persistent abuse."""

    scope = "sustained"

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self._identity(request)}


class PhoneNumberScopedThrottle(ScopedRateThrottle):
    """Throttle keyed by the phone number in the request body.

    Used on the OTP endpoints.  Keying on the phone number rather than the
    caller means a distributed attack against one account is still limited, and
    a shared office NAT does not lock out every employee at once.
    """

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        scope = getattr(view, "throttle_scope", None)
        if not scope:
            return None
        phone = ""
        if isinstance(getattr(request, "data", None), dict):
            phone = str(request.data.get("phone_number") or "").strip()
        ident = phone or self.get_ident(request)
        return self.cache_format % {"scope": scope, "ident": ident}


class UploadThrottle(_IdentityMixin, SimpleRateThrottle):
    scope = "upload"

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self._identity(request)}


class DownloadThrottle(_IdentityMixin, SimpleRateThrottle):
    scope = "download"

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self._identity(request)}


class SearchThrottle(_IdentityMixin, SimpleRateThrottle):
    scope = "search"

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self._identity(request)}


class SetupThrottle(SimpleRateThrottle):
    """Throttle for the account-bootstrap endpoint, keyed by IP.

    Keyed by IP rather than by user, because the caller is by definition not
    authenticated yet. This is the only thing standing between a guessable
    SETUP_KEY and an attacker creating themselves an owner account, so the rate
    is deliberately low.
    """

    scope = "setup"

    def get_cache_key(self, request: Any, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
