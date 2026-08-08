"""Domain exceptions and the single DRF exception handler.

Every error the API returns has the same shape, so the mobile client can write
one parser and one retry policy:

.. code-block:: json

    {
      "success": false,
      "error": {
        "code": "FOLDER_CYCLE",
        "message": "A folder cannot be moved inside its own subtree.",
        "details": {"folder_id": "..."},
        "field_errors": {}
      },
      "meta": {"request_id": "..."}
    }

``code`` is a stable machine-readable string.  Clients branch on ``code``,
never on ``message`` - messages are free to change or be translated.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class ErrorCode:
    """Stable error codes shared with the mobile team.

    Kept as plain constants (not an enum) because they are also embedded in the
    API documentation and must never be renumbered.
    """

    # Generic
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    THROTTLED = "THROTTLED"
    CONFLICT = "CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"

    # Authentication / OTP
    USER_NOT_REGISTERED = "USER_NOT_REGISTERED"
    USER_DISABLED = "USER_DISABLED"
    OTP_INVALID = "OTP_INVALID"
    OTP_EXPIRED = "OTP_EXPIRED"
    OTP_ATTEMPTS_EXCEEDED = "OTP_ATTEMPTS_EXCEEDED"
    OTP_RESEND_TOO_SOON = "OTP_RESEND_TOO_SOON"
    OTP_DAILY_LIMIT = "OTP_DAILY_LIMIT"
    OTP_LOCKED = "OTP_LOCKED"
    SMS_DELIVERY_FAILED = "SMS_DELIVERY_FAILED"
    TOKEN_INVALID = "TOKEN_INVALID"  # noqa: S105 - an error code, not a credential
    DEVICE_MISMATCH = "DEVICE_MISMATCH"

    # Folders
    FOLDER_CYCLE = "FOLDER_CYCLE"
    FOLDER_DEPTH_EXCEEDED = "FOLDER_DEPTH_EXCEEDED"
    FOLDER_NAME_CONFLICT = "FOLDER_NAME_CONFLICT"
    FOLDER_NOT_EMPTY = "FOLDER_NOT_EMPTY"

    # Files
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FILE_TYPE_BLOCKED = "FILE_TYPE_BLOCKED"
    FILE_NAME_CONFLICT = "FILE_NAME_CONFLICT"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    STORAGE_ERROR = "STORAGE_ERROR"

    # Quotations
    QUOTATION_LOCKED = "QUOTATION_LOCKED"
    QUOTATION_PDF_PENDING = "QUOTATION_PDF_PENDING"
    SHARE_LINK_EXPIRED = "SHARE_LINK_EXPIRED"


class DomainError(APIException):
    """Base class for expected, business-rule failures.

    Services raise these; the handler below turns them into the envelope.
    Unexpected failures (bugs) are deliberately *not* modelled here - they
    become a 500 with no internal detail leaked.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = ErrorCode.VALIDATION_ERROR
    default_detail = "The request could not be processed."

    def __init__(
        self,
        detail: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(detail or self.default_detail, code or self.default_code)
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code


class ValidationFailed(DomainError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = ErrorCode.VALIDATION_ERROR


class ResourceNotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = ErrorCode.NOT_FOUND
    default_detail = "The requested resource does not exist."


class PermissionDeniedError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = ErrorCode.PERMISSION_DENIED
    default_detail = "You do not have permission to perform this action."


class AuthenticationError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_code = ErrorCode.AUTHENTICATION_FAILED
    default_detail = "Authentication failed."


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_code = ErrorCode.CONFLICT
    default_detail = "The request conflicts with the current state of the resource."


class RateLimited(DomainError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = ErrorCode.THROTTLED
    default_detail = "Too many requests. Please try again later."


class ExternalServiceError(DomainError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = ErrorCode.SERVICE_UNAVAILABLE
    default_detail = "An upstream service is unavailable."


def _envelope(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    field_errors: Any = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "field_errors": field_errors or {},
        },
        "meta": {"request_id": request_id},
    }


def _flatten_drf_detail(detail: Any) -> tuple[str, Any]:
    """Split a DRF error detail into (message, field_errors).

    DRF serialiser errors arrive as ``{"field": ["msg", ...]}`` while simple
    errors arrive as a string or list.  The mobile client wants a single
    human-readable message *and* a per-field map, so produce both.
    """
    if isinstance(detail, dict):
        field_errors = {
            key: [str(item) for item in (value if isinstance(value, list) else [value])]
            for key, value in detail.items()
        }
        first_key = next(iter(field_errors), None)
        message = (
            f"{first_key}: {field_errors[first_key][0]}"
            if first_key and field_errors[first_key]
            else "Validation failed."
        )
        return message, field_errors
    if isinstance(detail, list):
        return (str(detail[0]) if detail else "Validation failed."), {}
    return str(detail), {}


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """DRF ``EXCEPTION_HANDLER``: normalise everything into the envelope."""
    request = context.get("request")
    request_id = getattr(request, "request_id", None)

    # Translate Django-level exceptions into their DRF equivalents first so the
    # rest of the function only deals with one family.
    if isinstance(exc, Http404):
        exc = ResourceNotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = PermissionDeniedError()
    elif isinstance(exc, DjangoValidationError):
        exc = ValidationFailed(
            detail="; ".join(exc.messages) if exc.messages else "Validation failed."
        )
    elif isinstance(exc, IntegrityError):
        # A unique/FK violation that slipped past validation is a conflict, not
        # a server fault - usually two concurrent writers.
        logger.warning("integrity_error", exc_info=exc, extra={"request_id": request_id})
        exc = ConflictError(detail="This change conflicts with existing data.")

    if isinstance(exc, DomainError):
        message, field_errors = _flatten_drf_detail(exc.detail)
        return Response(
            _envelope(
                code=str(exc.get_codes()) if isinstance(exc.detail, str) else exc.default_code,
                message=message,
                details=exc.details,
                field_errors=field_errors,
                request_id=request_id,
            ),
            status=exc.status_code,
        )

    response = drf_exception_handler(exc, context)

    if response is None:
        # Nothing recognised it: this is an unhandled bug.  Log the traceback
        # server-side and return an opaque 500 - internal details must never
        # reach the client.
        logger.exception("unhandled_exception", extra={"request_id": request_id})
        return Response(
            _envelope(
                code=ErrorCode.INTERNAL_ERROR,
                message="An unexpected error occurred. Please try again.",
                request_id=request_id,
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    message, field_errors = _flatten_drf_detail(response.data)
    code = _code_for_status(response.status_code)
    details: dict[str, Any] = {}
    if isinstance(exc, Throttled) and exc.wait is not None:
        details["retry_after_seconds"] = int(exc.wait)
        response["Retry-After"] = str(int(exc.wait))

    response.data = _envelope(
        code=code,
        message=message,
        details=details,
        field_errors=field_errors,
        request_id=request_id,
    )
    return response


def _code_for_status(status_code: int) -> str:
    return {
        400: ErrorCode.VALIDATION_ERROR,
        401: ErrorCode.AUTHENTICATION_FAILED,
        403: ErrorCode.PERMISSION_DENIED,
        404: ErrorCode.NOT_FOUND,
        405: ErrorCode.VALIDATION_ERROR,
        409: ErrorCode.CONFLICT,
        413: ErrorCode.FILE_TOO_LARGE,
        429: ErrorCode.THROTTLED,
        502: ErrorCode.SERVICE_UNAVAILABLE,
        503: ErrorCode.SERVICE_UNAVAILABLE,
    }.get(status_code, ErrorCode.INTERNAL_ERROR)
