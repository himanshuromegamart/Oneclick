"""Whole-API structural checks.

These catch mistakes that unit tests miss because they only surface once
Django's routing and DRF's dispatch are involved.
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import get_resolver
from rest_framework import viewsets

pytestmark = pytest.mark.django_db


def _all_viewsets() -> list[type]:
    """Every ViewSet reachable from the root URLconf."""
    found: dict[str, type] = {}

    def walk(patterns) -> None:
        for pattern in patterns:
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns)
                continue
            cls = getattr(getattr(pattern, "callback", None), "cls", None)
            if cls is not None and issubclass(cls, viewsets.ViewSetMixin):
                found[cls.__name__] = cls

    walk(get_resolver().url_patterns)
    return list(found.values())


class TestViewSetIntegrity:
    def test_no_instance_attribute_shadows_a_route_handler(self):
        """An instance attribute must never share a name with a route handler.

        DRF resolves handlers with ``getattr(self, action_name)``, so an
        attribute assigned in ``__init__`` silently replaces the method and the
        route fails at dispatch. This bit `self.favorites` shadowing the
        `favorites` action on two viewsets.
        """
        problems = []
        for cls in _all_viewsets():
            instance = cls()
            handlers = {
                name
                for name in dir(cls)
                if callable(getattr(cls, name, None))
                and getattr(getattr(cls, name), "mapping", None) is not None
            }
            handlers |= {"list", "retrieve", "create", "update", "partial_update", "destroy"}
            problems += [
                f"{cls.__name__}.{name}"
                for name in handlers
                if name in instance.__dict__ and not callable(instance.__dict__[name])
            ]

        assert not problems, f"instance attributes shadowing route handlers: {problems}"


class TestActionRoutes:
    """Routes that a plain unit test would never exercise."""

    def test_category_favorites(self, staff_client, root_folder):
        assert staff_client.post(f"/api/v1/categories/{root_folder.pk}/favorite/").status_code == 200

        response = staff_client.get("/api/v1/categories/favorites/")
        assert response.status_code == 200
        assert len(response.data["data"]) == 1

    def test_file_favorites(self, staff_client, sample_file):
        assert staff_client.post(f"/api/v1/documents/{sample_file.pk}/favorite/").status_code == 200

        response = staff_client.get("/api/v1/documents/favorites/")
        assert response.status_code == 200
        assert len(response.data["data"]) == 1

    def test_recent_files(self, staff_client, sample_file):
        assert staff_client.get("/api/v1/documents/recent/").status_code == 200

    def test_file_versions(self, staff_client, sample_file):
        assert staff_client.get(f"/api/v1/documents/{sample_file.pk}/versions/").status_code == 200

    def test_category_statistics(self, staff_client, child_folder):
        assert staff_client.get(f"/api/v1/categories/{child_folder.pk}/statistics/").status_code == 200

    def test_category_children(self, staff_client, root_folder):
        assert staff_client.get(f"/api/v1/categories/{root_folder.pk}/children/").status_code == 200

    def test_share_link_lifecycle(self, staff_client, sample_file):
        created = staff_client.post(
            f"/api/v1/documents/{sample_file.pk}/share/", {"expires_in_hours": 24}, format="json"
        )
        assert created.status_code == 201
        link_id = created.data["data"]["id"]

        assert staff_client.get("/api/v1/share-links/").status_code == 200
        assert staff_client.delete(f"/api/v1/share-links/{link_id}/").status_code == 200


class TestOperationalEndpoints:
    def test_liveness_needs_no_auth_and_no_dependencies(self, api_client):
        response = api_client.get("/live/")
        assert response.status_code == 200
        assert response.data["status"] == "alive"

    def test_readiness_reports_dependency_state(self, api_client):
        response = api_client.get("/ready/")
        assert response.status_code in {200, 503}
        assert "database" in response.data["checks"]

    def test_api_root_is_reachable(self, api_client):
        assert api_client.get("/").status_code == 200


class TestResponseEnvelope:
    """Every response has the same shape, so the app writes one parser."""

    def test_success_shape(self, staff_client, root_folder):
        response = staff_client.get(f"/api/v1/categories/{root_folder.pk}/")
        assert set(response.data) == {"success", "data", "error", "meta"}
        assert response.data["success"] is True
        assert response.data["error"] is None

    def test_error_shape(self, staff_client):
        response = staff_client.get(f"/api/v1/categories/{uuid.uuid4()}/")
        assert response.status_code == 404
        assert set(response.data) == {"success", "data", "error", "meta"}
        assert response.data["success"] is False
        assert set(response.data["error"]) == {"code", "message", "details", "field_errors"}

    def test_paginated_shape(self, staff_client, root_folder):
        response = staff_client.get("/api/v1/categories/")
        assert "pagination" in response.data["meta"]
        assert "count" in response.data["meta"]["pagination"]

    def test_request_id_is_echoed_back(self, staff_client, root_folder):
        response = staff_client.get(
            f"/api/v1/categories/{root_folder.pk}/", HTTP_X_REQUEST_ID="trace-abc-123"
        )
        assert response["X-Request-Id"] == "trace-abc-123"
        assert response.data["meta"]["request_id"] == "trace-abc-123"


class TestOpenAPISchema:
    def test_schema_generates(self):
        """The API contract must build - a broken schema blocks the app team."""
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)

        assert "/api/v1/categories/" in schema["paths"]
        assert "/api/v1/documents/" in schema["paths"]
        assert "/api/v1/search/" in schema["paths"]
        # The quotation module is gone.
        assert not [path for path in schema["paths"] if "quotation" in path]

    def test_schema_documents_bearer_auth(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        scheme = schema["components"]["securitySchemes"]["jwtAuth"]

        assert scheme["scheme"] == "bearer"
        assert scheme["bearerFormat"] == "JWT"
