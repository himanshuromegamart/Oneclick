"""Access rules.

Three classes cover the whole product.  The rules they encode are the table in
:mod:`apps.accounts.constants`.
"""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class IsActiveUser(BasePermission):
    """Authenticated and not disabled.

    ``is_active`` is re-checked on every request, so disabling someone takes
    effect immediately even though their access token has not expired yet.
    """

    message = "Your account has been disabled."

    def has_permission(self, request: Request, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)


class CanContribute(IsActiveUser):
    """Read for everyone signed in; writes for Owner and Staff.

    Viewers get a read-only app: they can browse, search and download, but
    every mutating verb is refused.
    """

    message = "Your account has view-only access."

    def has_permission(self, request: Request, view: Any) -> bool:
        if not super().has_permission(request, view):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.can_contribute


class IsOwnerOrCreator(CanContribute):
    """Object-level rule: the Owner may change anything, others only their own.

    This is what lets staff manage the documents they uploaded without being
    able to touch anyone else's.
    """

    message = "You can only modify items you added yourself."

    def has_object_permission(self, request: Request, view: Any, obj: Any) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return request.user.can_modify(obj)


class IsOwnerRole(IsActiveUser):
    """Restricted to the Owner: deleting other people's items, purging, users."""

    message = "This action is restricted to the account owner."

    def has_permission(self, request: Request, view: Any) -> bool:
        return super().has_permission(request, view) and request.user.is_owner
