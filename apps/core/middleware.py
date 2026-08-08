"""Request-scoped middleware."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.core.logging import current_request_id, current_user_id

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"


class RequestIDMiddleware:
    """Assign or adopt a correlation id and log one line per request.

    The mobile client may supply ``X-Request-Id``; honouring it lets a support
    ticket with a screenshot be traced straight to the server log. Untrusted
    input is length-capped so it cannot bloat every log line.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = (request.META.get(REQUEST_ID_HEADER) or "").strip()[:64]
        request_id = incoming or uuid.uuid4().hex
        request.request_id = request_id  # type: ignore[attr-defined]
        token = current_request_id.set(request_id)
        started = time.perf_counter()

        try:
            response = self.get_response(request)
        finally:
            current_request_id.reset(token)

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response["X-Request-Id"] = request_id

        user = getattr(request, "user", None)
        logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "user_id": str(user.pk) if user is not None and user.is_authenticated else None,
                "device_id": request.META.get("HTTP_X_DEVICE_ID"),
                "app_version": request.META.get("HTTP_X_APP_VERSION"),
            },
        )
        return response


class CurrentRequestMiddleware:
    """Expose the acting user id to the logging context.

    Deliberately *not* a thread-local "current user" that models reach into -
    that hides data flow.  Services take the acting user as an explicit
    argument; this only feeds observability.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            current_user_id.set(str(user.pk))
        return response


class APIExceptionMiddleware:
    """Last-resort net for exceptions raised outside DRF's handler.

    DRF only wraps exceptions raised inside a view.  Anything thrown by
    middleware above it, or by URL resolution, would otherwise return Django's
    HTML error page - unparseable for a mobile client.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if not request.path.startswith("/api/"):
            return None
        request_id = getattr(request, "request_id", None)
        logger.exception("unhandled_middleware_exception", extra={"request_id": request_id})
        return JsonResponse(
            {
                "success": False,
                "data": None,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred. Please try again.",
                    "details": {},
                    "field_errors": {},
                },
                "meta": {"request_id": request_id},
            },
            status=500,
        )
