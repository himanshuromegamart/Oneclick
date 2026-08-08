"""Success-side response envelope.

Mirrors the error envelope in :mod:`apps.core.exceptions` so that *every*
response - success or failure - has ``success``, ``data``, ``error`` and
``meta`` keys.  A client can therefore check one boolean and never guess at the
payload shape.
"""

from __future__ import annotations

from typing import Any

from rest_framework import status as http_status
from rest_framework.response import Response


def envelope(
    data: Any = None,
    meta: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "error": None,
        "meta": {"request_id": request_id, **(meta or {})},
    }


def ok(
    data: Any = None,
    status: int = http_status.HTTP_200_OK,
    meta: dict[str, Any] | None = None,
    request: Any = None,
    headers: dict[str, str] | None = None,
) -> Response:
    request_id = getattr(request, "request_id", None) if request is not None else None
    return Response(envelope(data, meta, request_id), status=status, headers=headers)


def created(data: Any = None, request: Any = None, **kwargs: Any) -> Response:
    return ok(data, status=http_status.HTTP_201_CREATED, request=request, **kwargs)


def accepted(data: Any = None, request: Any = None, **kwargs: Any) -> Response:
    """202: work was queued to Celery and is not finished yet."""
    return ok(data, status=http_status.HTTP_202_ACCEPTED, request=request, **kwargs)


def no_content(request: Any = None) -> Response:
    # A body is still returned so the client parser never has to special-case
    # an empty response.
    return ok(None, status=http_status.HTTP_200_OK, request=request)
