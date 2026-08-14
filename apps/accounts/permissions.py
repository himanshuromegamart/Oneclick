"""Access rules.

One class, because the API has one rule: be signed in and not disabled.

The role split lives entirely in the browser consoles - see
:mod:`apps.accounts.constants` - so there is deliberately nothing here that
looks at it. The earlier ``CanContribute`` and ``IsOwnerOrCreator`` classes are
gone rather than left returning True: a permission class that always passes is
a trap for whoever reads the view next and assumes it still guards something.
"""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request


class IsActiveUser(BasePermission):
    """Authenticated and not disabled.

    ``is_active`` is re-checked on every request, so disabling someone takes
    effect immediately even though their access token has not expired yet.
    """

    message = "Your account has been disabled."

    def has_permission(self, request: Request, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)


class IsAdminRole(IsActiveUser):
    """Admin-only. Not used by the mobile API, which is open to both roles.

    It exists for the account-management endpoints, where the rule has to hold
    on the server: without it a User could create themselves an Admin account
    and the dashboard restriction would mean nothing.
    """

    message = "This action is restricted to admins."

    def has_permission(self, request: Request, view: Any) -> bool:
        return super().has_permission(request, view) and request.user.is_admin
