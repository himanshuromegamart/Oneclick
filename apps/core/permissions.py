"""Permission primitives used by every app.

The RBAC rules themselves live in :mod:`apps.accounts.permissions`; this module
holds only the framework-level pieces that do not depend on the accounts app,
so ``core`` stays importable from anywhere without a circular import.
"""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class IsActiveUser(BasePermission):
    """Authenticated *and* not disabled.

    An admin disabling a user must take effect immediately, even though that
    user may hold an access token that has not expired yet.  Checking
    ``is_active`` on every request is what makes revocation immediate.
    """

    message = "Your account has been disabled. Contact your administrator."

    def has_permission(self, request: Request, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)


class ReadOnly(BasePermission):
    def has_permission(self, request: Request, view: Any) -> bool:
        return request.method in SAFE_METHODS


class IsOwnerOrReadOnly(BasePermission):
    """Writes restricted to the row's creator; reads open to any viewer."""

    message = "Only the creator of this record can modify it."

    def has_object_permission(self, request: Request, view: Any, obj: Any) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return getattr(obj, "created_by_id", None) == request.user.pk
