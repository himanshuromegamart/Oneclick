"""SMS delivery.

The Strategy pattern is used here for a practical reason: OTP delivery is the
one dependency that is both external and on the critical login path.  Isolating
it behind :class:`SMSBackend` means the test suite runs without network access,
local development prints codes to the console, and swapping providers later is
a settings change rather than a refactor.

.. warning::

   ``SMS_BASE_URL`` defaults to **https**.  The endpoint named in the original
   brief (``http://nimbusit.biz/...``) is plain HTTP, which would put every OTP
   on the wire in clear text.  If NimbusIT genuinely has no TLS endpoint, the
   value must be overridden explicitly in the environment - the insecure choice
   should be a deliberate, visible act, not a default.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings
from django.utils.module_loading import import_string

from apps.core.exceptions import ErrorCode, ExternalServiceError
from apps.core.logging import mask_phone

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SMSResult:
    """Outcome of a single send attempt."""

    success: bool
    reference: str = ""
    provider_status: str = ""
    raw_response: str = ""

    def __bool__(self) -> bool:
        return self.success


class SMSBackend(abc.ABC):
    """Interface every SMS provider adapter implements."""

    @abc.abstractmethod
    def send(self, phone_number: str, message: str, **kwargs: Any) -> SMSResult:
        """Deliver ``message`` to ``phone_number``. Must not raise on provider
        rejection - return ``SMSResult(success=False)`` instead, so the caller
        decides whether to retry."""


class NimbusITSMSBackend(SMSBackend):
    """Adapter for the NimbusIT ``SendSingleApi`` endpoint.

    The provider takes credentials as query parameters and answers with a short
    text body; anything that is not an explicit success marker is treated as a
    failure so a silently-undelivered OTP never looks like a success.
    """

    #: Substrings NimbusIT uses to mark acceptance.
    _SUCCESS_MARKERS = ("success", "sent", "submitted", "accepted")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or settings.SMS_SETTINGS

    def _params(self, phone_number: str, message: str) -> dict[str, str]:
        # The provider expects a bare 10-digit national number, not E.164.
        national = phone_number.removeprefix("+91")
        params = {
            "UserID": self.config["USER_ID"],
            "Password": self.config["PASSWORD"],
            "PhNo": national,
            "Text": message,
            "SenderId": self.config["SENDER_ID"],
        }
        # DLT fields: mandatory for transactional SMS in India. The operator
        # drops the message if the text does not match the approved template
        # registered against this TemplateId.
        if self.config.get("ENTITY_ID"):
            params["EntityId"] = self.config["ENTITY_ID"]
        if self.config.get("TEMPLATE_ID"):
            params["TemplateId"] = self.config["TEMPLATE_ID"]
        return params

    def send(self, phone_number: str, message: str, **kwargs: Any) -> SMSResult:
        if not self.config.get("USER_ID") or not self.config.get("PASSWORD"):
            raise ExternalServiceError(
                detail="SMS gateway is not configured.",
                code=ErrorCode.SMS_DELIVERY_FAILED,
            )

        try:
            response = requests.get(
                self.config["BASE_URL"],
                params=self._params(phone_number, message),
                timeout=self.config["TIMEOUT_SECONDS"],
            )
        except requests.Timeout:
            logger.warning("sms_timeout", extra={"phone": mask_phone(phone_number)})
            return SMSResult(success=False, provider_status="timeout")
        except requests.RequestException as exc:
            logger.warning(
                "sms_transport_error",
                extra={"phone": mask_phone(phone_number), "error": str(exc)},
            )
            return SMSResult(success=False, provider_status="transport_error")

        body = (response.text or "").strip()
        # Truncate: the provider occasionally echoes a full HTML error page and
        # there is no reason to store that against every OTP row.
        snippet = body[:300]

        if response.status_code != 200:
            logger.warning(
                "sms_http_error",
                extra={
                    "phone": mask_phone(phone_number),
                    "status_code": response.status_code,
                    "body": snippet,
                },
            )
            return SMSResult(
                success=False, provider_status=str(response.status_code), raw_response=snippet
            )

        lowered = body.lower()
        succeeded = any(marker in lowered for marker in self._SUCCESS_MARKERS)
        if not succeeded:
            logger.warning(
                "sms_rejected", extra={"phone": mask_phone(phone_number), "body": snippet}
            )

        return SMSResult(
            success=succeeded,
            reference=body.split("|")[-1].strip() if "|" in body else body[:120],
            provider_status="ok" if succeeded else "rejected",
            raw_response=snippet,
        )


class ConsoleSMSBackend(SMSBackend):
    """Prints the message. Used in local development."""

    def send(self, phone_number: str, message: str, **kwargs: Any) -> SMSResult:
        logger.warning("sms_console_delivery", extra={"phone": phone_number, "body": message})
        return SMSResult(success=True, reference="console", provider_status="ok")


@dataclass(slots=True)
class SentMessage:
    phone_number: str
    message: str


class InMemorySMSBackend(SMSBackend):
    """Records messages instead of sending them. Used by the test suite.

    The outbox lives on the class so a test can assert against it without
    holding a reference to the instance the service happened to construct.
    """

    outbox: list[SentMessage] = []

    @classmethod
    def clear(cls) -> None:
        cls.outbox.clear()

    @classmethod
    def last(cls) -> SentMessage | None:
        return cls.outbox[-1] if cls.outbox else None

    def send(self, phone_number: str, message: str, **kwargs: Any) -> SMSResult:
        type(self).outbox.append(SentMessage(phone_number, message))
        return SMSResult(success=True, reference="in-memory", provider_status="ok")


def get_sms_backend() -> SMSBackend:
    """Factory resolving ``SMS_SETTINGS['BACKEND']`` to an instance."""
    backend_path = settings.SMS_SETTINGS["BACKEND"]
    backend_class = import_string(backend_path)
    return backend_class()


def render_otp_message(otp: str, ttl_seconds: int) -> str:
    template = settings.SMS_SETTINGS["TEMPLATE"]
    return template.format(otp=otp, minutes=max(ttl_seconds // 60, 1))
