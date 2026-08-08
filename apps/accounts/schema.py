"""OpenAPI extensions.

drf-spectacular cannot introspect a custom authentication class, so without
this the generated schema documents no security scheme at all - and the mobile
team's generated client would omit the Authorization header entirely.

The module is imported from :meth:`AccountsConfig.ready` purely for its
registration side effect.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ActiveUserJWTScheme(OpenApiAuthenticationExtension):
    """Documents :class:`~apps.accounts.authentication.ActiveUserJWTAuthentication`."""

    target_class = "apps.accounts.authentication.ActiveUserJWTAuthentication"
    name = "jwtAuth"
    match_subclasses = True
    priority = 1

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "Get a token pair from `POST /api/v1/auth/otp/verify/`, then send "
                "it as `Authorization: Bearer <access>`.\n\n"
                "Access tokens last 30 minutes. Refresh with "
                "`POST /api/v1/auth/token/refresh/` - refresh tokens are "
                "single-use and rotate on every call, so store the new one."
            ),
        }
