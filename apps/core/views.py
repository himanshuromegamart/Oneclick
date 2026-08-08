"""Operational endpoints consumed by the orchestrator and load balancer.

The three probes are deliberately different:

``/live/``   - is the process alive?  No dependency checks, so a Redis outage
               never causes Kubernetes to kill otherwise-healthy pods.
``/ready/``  - can this pod serve traffic?  Checks Postgres and Redis, and is
               what the load balancer polls.
``/health/`` - richer detail for humans and dashboards.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.db.utils import OperationalError
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class LivenessView(APIView):
    """Process-level liveness. Must never touch a dependency."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = ()

    @extend_schema(tags=["health"], responses={200: dict})
    def get(self, request: Request) -> Response:
        return Response({"status": "alive"}, status=status.HTTP_200_OK)


def _check_database() -> tuple[bool, str]:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True, "ok"
    except OperationalError as exc:
        logger.error("readiness_database_failed", exc_info=exc)
        return False, "unreachable"


def _check_cache() -> tuple[bool, str]:
    try:
        cache.set("health:ping", "pong", timeout=10)
        return (True, "ok") if cache.get("health:ping") == "pong" else (False, "read_write_failed")
    except Exception as exc:
        logger.error("readiness_cache_failed", exc_info=exc)
        return False, "unreachable"


class ReadinessView(APIView):
    """Dependency-aware readiness probe."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = ()

    @extend_schema(tags=["health"], responses={200: dict, 503: dict})
    def get(self, request: Request) -> Response:
        db_ok, db_detail = _check_database()
        cache_ok, cache_detail = _check_cache()
        ready = db_ok and cache_ok
        return Response(
            {
                "status": "ready" if ready else "not_ready",
                "checks": {"database": db_detail, "cache": cache_detail},
            },
            status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class HealthView(APIView):
    """Human-facing health summary."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = ()

    @extend_schema(tags=["health"], responses={200: dict})
    def get(self, request: Request) -> Response:
        db_ok, db_detail = _check_database()
        cache_ok, cache_detail = _check_cache()
        payload: dict[str, Any] = {
            "service": "sarah-aqua-soft-erp",
            "version": getattr(settings, "APP_VERSION", "1.0.0"),
            "api_versions": settings.REST_FRAMEWORK["ALLOWED_VERSIONS"],
            "status": "healthy" if (db_ok and cache_ok) else "degraded",
            "components": {
                "database": {"healthy": db_ok, "detail": db_detail},
                "cache": {"healthy": cache_ok, "detail": cache_detail},
                "storage": {
                    "healthy": bool(settings.CLOUDINARY["CLOUD_NAME"]),
                    "detail": "configured" if settings.CLOUDINARY["CLOUD_NAME"] else "unconfigured",
                },
                "sms": {
                    "healthy": bool(settings.SMS_SETTINGS["USER_ID"]),
                    "detail": "configured" if settings.SMS_SETTINGS["USER_ID"] else "unconfigured",
                },
            },
        }
        code = (
            status.HTTP_200_OK
            if payload["status"] == "healthy"
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return Response(payload, status=code)
