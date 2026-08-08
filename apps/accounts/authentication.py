"""JWT authentication.

Extends SimpleJWT with one check a plain JWT cannot make on its own: **the
account is still live**. A token minted before someone was disabled or removed
would otherwise keep working until it expired.

The check costs one indexed lookup, served from a short-lived cache on the hot
path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.tokens import Token

if TYPE_CHECKING:  # pragma: no cover
    from apps.accounts.models import User

# NOTE: the model is imported *inside* the method below, never at module level.
# DRF resolves DEFAULT_AUTHENTICATION_CLASSES while building the APIView class
# body, which happens during app loading - a module-level model import here
# would re-enter a half-initialised apps.accounts.models and raise ImportError.

logger = logging.getLogger(__name__)

# 60 seconds is the compromise: it removes a query from every request while
# capping the window between "owner disables an account" and "that account is
# locked out" at a minute. The cache is also cleared eagerly on logout and
# profile changes, so the TTL only matters if that path is missed.
USER_CACHE_TTL = 60


class ActiveUserJWTAuthentication(JWTAuthentication):
    """The project's only authentication class."""

    def get_user(self, validated_token: Token) -> User:
        user_id = validated_token.get("user_id")
        if not user_id:
            raise InvalidToken("Token contains no recognisable user identification.")

        user = self._load_user(str(user_id))

        if user is None:
            raise AuthenticationFailed("User not found.", code="user_not_found")
        if user.is_deleted:
            raise AuthenticationFailed("This account has been removed.", code="user_deleted")
        if not user.is_active:
            raise AuthenticationFailed("Your account has been disabled.", code="user_inactive")

        return user

    @staticmethod
    def _load_user(user_id: str) -> User | None:
        from apps.accounts.models import User

        cache_key = f"auth:user:{user_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached if cached != "__missing__" else None

        # `role` is a plain char field, not a relation - nothing to join.
        user = User.all_objects.filter(pk=user_id).first()
        cache.set(cache_key, user if user is not None else "__missing__", USER_CACHE_TTL)
        return user


def invalidate_auth_cache(user_id: Any) -> None:
    """Clear the cached user so a change takes effect on the next request."""
    cache.delete(f"auth:user:{user_id}")


def client_ip(request: Request) -> str | None:
    """Best-effort client IP.

    ``X-Forwarded-For`` is only meaningful because NGINX overwrites it; the
    left-most entry is the original client. Never trust this header when the
    app is exposed directly.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
