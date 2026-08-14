"""Root URL configuration.

Everything the app calls lives under ``/api/v1/``. The version sits in the path
so a URL in a log or a bug report is unambiguous, and a future ``/api/v2/`` can
run alongside during a migration.

There is no admin site and no web frontend - this is a backend-only service.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


def api_root(request):
    """Service banner - a quick "is this the right host?" check."""
    return JsonResponse(
        {
            "service": "Sarah Aqua Soft - Document Manager API",
            "versions": {"v1": "/api/v1/"},
            "documentation": "/api/docs/",
            "dashboard": "/dashboard/",
            "admin": f"/{settings.ADMIN_URL}",
            "health": "/health/",
        }
    )


v1_patterns = [
    path("", include("apps.accounts.urls")),
    path("", include("apps.folders.urls")),
    path("", include("apps.files.urls")),
]

urlpatterns = [
    # The owner's console. Path is configurable via ADMIN_URL so it can be
    # moved off the default that scanners hammer.
    path(settings.ADMIN_URL, admin.site.urls),
    # The everyday console. Server-rendered and session-authenticated, unlike
    # everything under /api/, which is stateless and JWT-authenticated.
    # Django's own admin stays available for the deeper, rarer jobs.
    path("dashboard/", include("apps.dashboard.urls")),
    path("", api_root, name="api-root"),
    path("", include("apps.core.urls")),
    path("api/v1/", include((v1_patterns, "v1"), namespace="v1")),
    # OpenAPI schema plus readable docs for whoever builds the app.
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
