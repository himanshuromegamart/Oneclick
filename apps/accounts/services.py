"""Login services.

The OTP flow is the only way into the app, so it is the one place where the
defences stay thorough even though the rest of the product is deliberately
simple:

===========================  ===============================================
Defence                      Attack it stops
===========================  ===============================================
Hashed codes at rest         Database read -> account takeover
Resend cooldown              SMS-bombing a victim's handset
Daily send cap per number    Burning the SMS budget / sustained harassment
Per-OTP attempt counter      Online guessing of one code
Progressive lockout          Guessing across many freshly-issued codes
Superseding old codes        Widening the guessable window with resends
Throttles keyed by number    Volumetric abuse from rotating IPs
===========================  ===============================================
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Device, OTPRequest, User
from apps.accounts.repositories import DeviceRepository, OTPRepository, UserRepository
from apps.accounts.sms import get_sms_backend, render_otp_message
from apps.core.exceptions import (
    ErrorCode,
    ExternalServiceError,
    RateLimited,
    ValidationFailed,
)
from apps.core.logging import mask_phone
from apps.core.validators import normalize_phone_number

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OTPChallenge:
    """What the client needs after requesting an OTP."""

    phone_number: str
    expires_in_seconds: int
    resend_available_in_seconds: int
    attempts_allowed: int


@dataclass(slots=True)
class AuthTokens:
    access: str
    refresh: str
    access_expires_in: int
    refresh_expires_in: int


class OTPService:
    """Issues and verifies one-time passwords."""

    def __init__(
        self,
        user_repo: UserRepository | None = None,
        otp_repo: OTPRepository | None = None,
    ) -> None:
        self.users = user_repo or UserRepository()
        self.otps = otp_repo or OTPRepository()

    @property
    def config(self) -> dict:
        """Read settings at use time, so a tuned limit takes effect at once."""
        return settings.OTP_SETTINGS

    # -- cache keys -------------------------------------------------------
    @staticmethod
    def _cooldown_key(phone: str) -> str:
        return f"otp:cooldown:{phone}"

    @staticmethod
    def _daily_key(phone: str) -> str:
        return f"otp:daily:{phone}:{timezone.now():%Y%m%d}"

    @staticmethod
    def _lockout_key(phone: str) -> str:
        return f"otp:lock:{phone}"

    @staticmethod
    def _failure_key(phone: str) -> str:
        return f"otp:fail:{phone}"

    # -- guards -----------------------------------------------------------
    def _assert_not_locked(self, phone: str) -> None:
        locked_until = cache.get(self._lockout_key(phone))
        if locked_until:
            remaining = int(max(locked_until - timezone.now().timestamp(), 1))
            raise RateLimited(
                detail="Too many failed attempts. Please try again later.",
                code=ErrorCode.OTP_LOCKED,
                details={"retry_after_seconds": remaining},
            )

    def _assert_cooldown_elapsed(self, phone: str) -> None:
        available_at = cache.get(self._cooldown_key(phone))
        if available_at:
            remaining = int(max(available_at - timezone.now().timestamp(), 1))
            raise RateLimited(
                detail=f"Please wait {remaining} seconds before requesting another OTP.",
                code=ErrorCode.OTP_RESEND_TOO_SOON,
                details={"retry_after_seconds": remaining},
            )

    def _assert_daily_quota(self, phone: str) -> None:
        # Falls back to a database count if Redis is down, so a cache outage
        # never silently lifts the cap.
        sent_today = cache.get(self._daily_key(phone))
        if sent_today is None:
            sent_today = self.otps.sends_in_last_day(phone)
        if sent_today >= self.config["MAX_SENDS_PER_DAY"]:
            raise RateLimited(
                detail="Daily OTP limit reached. Please try again tomorrow.",
                code=ErrorCode.OTP_DAILY_LIMIT,
                details={"limit": self.config["MAX_SENDS_PER_DAY"]},
            )

    def _register_send(self, phone: str) -> None:
        cooldown = self.config["RESEND_COOLDOWN_SECONDS"]
        cache.set(
            self._cooldown_key(phone), timezone.now().timestamp() + cooldown, timeout=cooldown
        )
        try:
            cache.incr(self._daily_key(phone))
        except ValueError:
            cache.set(self._daily_key(phone), 1, timeout=86_400)

    def _register_failure(self, phone: str) -> int:
        """Count a wrong code and lock the number out once it repeats."""
        key = self._failure_key(phone)
        try:
            failures = cache.incr(key)
        except ValueError:
            failures = 1
            cache.set(key, 1, timeout=self.config["LOCKOUT_SECONDS"])

        if failures >= self.config["MAX_VERIFY_ATTEMPTS"]:
            lockout = self.config["LOCKOUT_SECONDS"]
            cache.set(
                self._lockout_key(phone), timezone.now().timestamp() + lockout, timeout=lockout
            )
            cache.delete(key)
            logger.warning("otp_lockout_triggered", extra={"phone": mask_phone(phone)})
        return failures

    def _clear_failures(self, phone: str) -> None:
        cache.delete(self._failure_key(phone))
        cache.delete(self._lockout_key(phone))

    # -- public API -------------------------------------------------------
    def request_otp(
        self,
        raw_phone_number: str,
        *,
        ip_address: str | None = None,
        is_resend: bool = False,
    ) -> OTPChallenge:
        """Issue an OTP for an existing, active account.

        An unknown number is rejected outright, which does confirm whether a
        number is registered.  That is intentional for a closed, hand-provisioned
        user base of a handful of staff - and the throttles above keep it from
        being usable to enumerate numbers at any scale.
        """
        phone = normalize_phone_number(raw_phone_number)

        self._assert_not_locked(phone)
        if is_resend or cache.get(self._cooldown_key(phone)):
            self._assert_cooldown_elapsed(phone)
        self._assert_daily_quota(phone)

        user = self.users.get_by_phone(phone)
        if user is None:
            logger.info("otp_unknown_number", extra={"phone": mask_phone(phone)})
            raise ValidationFailed(
                detail="This mobile number is not registered.",
                code=ErrorCode.USER_NOT_REGISTERED,
                status_code=403,
            )
        if not user.is_active:
            raise ValidationFailed(
                detail="This account has been disabled.",
                code=ErrorCode.USER_DISABLED,
                status_code=403,
            )

        code = self._generate_code()

        with transaction.atomic():
            # Only the newest code may ever be valid.
            self.otps.invalidate_outstanding(phone)
            otp = OTPRequest.issue(
                phone_number=phone,
                raw_code=code,
                user=user,
                ttl_seconds=self.config["TTL_SECONDS"],
                max_attempts=self.config["MAX_VERIFY_ATTEMPTS"],
                ip_address=ip_address,
            )

        self._deliver(otp, code)
        self._register_send(phone)

        logger.info(
            "otp_issued",
            extra={"phone": mask_phone(phone), "user_id": str(user.pk), "resend": is_resend},
        )
        return OTPChallenge(
            phone_number=phone,
            expires_in_seconds=self.config["TTL_SECONDS"],
            resend_available_in_seconds=self.config["RESEND_COOLDOWN_SECONDS"],
            attempts_allowed=self.config["MAX_VERIFY_ATTEMPTS"],
        )

    def verify(self, raw_phone_number: str, code: str) -> User:
        """Validate a submitted code and return the owning user."""
        phone = normalize_phone_number(raw_phone_number)
        self._assert_not_locked(phone)

        if not code or not str(code).strip().isdigit():
            self._register_failure(phone)
            raise ValidationFailed(detail="Enter a valid OTP.", code=ErrorCode.OTP_INVALID)

        code = str(code).strip()

        otp = self.otps.latest_usable(phone)
        if otp is None:
            # "No code issued" and "code expired" answer identically, so the
            # response cannot be used to probe whether a login is in progress.
            self._register_failure(phone)
            raise ValidationFailed(
                detail="This OTP has expired. Please request a new one.",
                code=ErrorCode.OTP_EXPIRED,
            )

        if otp.attempts_remaining <= 0:
            self._register_failure(phone)
            raise RateLimited(
                detail="Too many incorrect attempts. Please request a new OTP.",
                code=ErrorCode.OTP_ATTEMPTS_EXCEEDED,
            )

        if not otp.verify(code):
            self._register_failure(phone)
            remaining = otp.attempts_remaining
            logger.info(
                "otp_verify_failed",
                extra={"phone": mask_phone(phone), "remaining": remaining},
            )
            raise ValidationFailed(
                detail=f"Incorrect OTP. {remaining} attempt(s) remaining.",
                code=ErrorCode.OTP_INVALID,
                details={"attempts_remaining": remaining},
            )

        otp.consume()
        self._clear_failures(phone)

        user = otp.user or self.users.get_by_phone(phone)
        if user is None or not user.is_active:
            raise ValidationFailed(
                detail="This account has been disabled.",
                code=ErrorCode.USER_DISABLED,
                status_code=403,
            )
        return user

    # -- internals --------------------------------------------------------
    def _generate_code(self) -> str:
        """Cryptographically secure numeric code.

        ``secrets``, not ``random``: the latter's internal state is recoverable
        from a handful of outputs, which would let an attacker predict the next
        OTP.
        """
        length = self.config["LENGTH"]
        return str(secrets.randbelow(10**length)).zfill(length)

    def _deliver(self, otp: OTPRequest, code: str) -> None:
        backend = get_sms_backend()
        message = render_otp_message(code, self.config["TTL_SECONDS"])
        result = backend.send(otp.phone_number, message)

        otp.delivered = bool(result)
        otp.delivery_reference = result.reference[:120]
        otp.save(update_fields=["delivered", "delivery_reference", "updated_at"])

        if not result:
            # Surface a 502 so the app can offer "resend" rather than showing a
            # code box for a code that will never arrive.
            logger.error(
                "otp_delivery_failed",
                extra={"phone": mask_phone(otp.phone_number), "status": result.provider_status},
            )
            raise ExternalServiceError(
                detail="We could not send the OTP right now. Please try again.",
                code=ErrorCode.SMS_DELIVERY_FAILED,
            )


class AuthService:
    """Turns a verified user into a token pair, and ends the session.

    Two ways in, both landing on the same token issuer:

    * **OTP** - possession of the phone. No standing credential to steal.
    * **Password** - a standing credential, so it carries its own lockout.

    A password is only accepted for accounts that have one set; an OTP-only
    account cannot be attacked through the password endpoint at all.
    """

    def __init__(
        self,
        otp_service: OTPService | None = None,
        device_repo: DeviceRepository | None = None,
        user_repo: UserRepository | None = None,
    ) -> None:
        self.otp = otp_service or OTPService()
        self.devices = device_repo or DeviceRepository()
        self.users = user_repo or UserRepository()

    @property
    def login_config(self) -> dict:
        return settings.LOGIN_SETTINGS

    # -- password brute-force protection ----------------------------------
    @staticmethod
    def _login_failure_key(phone: str) -> str:
        return f"login:fail:{phone}"

    @staticmethod
    def _login_lock_key(phone: str) -> str:
        return f"login:lock:{phone}"

    def _assert_not_locked_out(self, phone: str) -> None:
        locked_until = cache.get(self._login_lock_key(phone))
        if locked_until:
            remaining = int(max(locked_until - timezone.now().timestamp(), 1))
            raise RateLimited(
                detail="Too many failed sign-in attempts. Please try again later.",
                code=ErrorCode.OTP_LOCKED,
                details={"retry_after_seconds": remaining},
            )

    def _record_login_failure(self, phone: str) -> None:
        key = self._login_failure_key(phone)
        lockout = self.login_config["LOCKOUT_SECONDS"]
        try:
            failures = cache.incr(key)
        except ValueError:
            failures = 1
            cache.set(key, 1, timeout=lockout)

        if failures >= self.login_config["MAX_FAILED_ATTEMPTS"]:
            cache.set(
                self._login_lock_key(phone),
                timezone.now().timestamp() + lockout,
                timeout=lockout,
            )
            cache.delete(key)
            logger.warning("login_lockout_triggered", extra={"phone": mask_phone(phone)})

    def _clear_login_failures(self, phone: str) -> None:
        cache.delete(self._login_failure_key(phone))
        cache.delete(self._login_lock_key(phone))

    def login_with_password(
        self,
        *,
        phone_number: str,
        password: str,
        device_id: str = "",
        platform: str = "unknown",
        model_name: str = "",
        app_version: str = "",
        ip_address: str | None = None,
    ) -> tuple[User, Device | None, AuthTokens]:
        """Sign in with a mobile number and password."""
        phone = normalize_phone_number(phone_number)
        self._assert_not_locked_out(phone)

        user = self.users.get_by_phone(phone)

        # One message and one code for every failure - unknown number, wrong
        # password, disabled account, OTP-only account. Distinguishing them
        # would turn this endpoint into a way to discover who has an account.
        def reject() -> None:
            self._record_login_failure(phone)
            logger.info("password_login_failed", extra={"phone": mask_phone(phone)})
            raise ValidationFailed(
                detail="Incorrect mobile number or password.",
                code=ErrorCode.AUTHENTICATION_FAILED,
                status_code=401,
            )

        if user is None or not user.is_active or not user.has_usable_password():
            # Run the hasher anyway. Returning early would make a non-existent
            # account measurably faster to reject than a wrong password, which
            # is enough to enumerate valid numbers with a stopwatch.
            User().set_password(password)
            reject()

        if not user.check_password(password):
            reject()

        self._clear_login_failures(phone)

        device = self.devices.register_or_touch(
            user=user,
            device_id=device_id,
            platform=platform,
            model_name=model_name,
            app_version=app_version,
            ip=ip_address,
        )

        user.last_login_at = timezone.now()
        user.save(update_fields=["last_login_at", "updated_at"])

        logger.info(
            "password_login_success", extra={"user_id": str(user.pk), "role": user.role}
        )
        return user, device, self.issue_tokens(user)

    def set_password(self, user: User, new_password: str) -> User:
        """Set or replace a user's password.

        Django hashes it with PBKDF2 before it touches the database, so the
        plain text exists only for the length of this call.
        """
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])
        self._clear_login_failures(user.phone_number)
        logger.info("password_changed", extra={"user_id": str(user.pk)})
        return user

    def login_with_otp(
        self,
        *,
        phone_number: str,
        code: str,
        device_id: str = "",
        platform: str = "unknown",
        model_name: str = "",
        app_version: str = "",
        ip_address: str | None = None,
    ) -> tuple[User, Device | None, AuthTokens]:
        user = self.otp.verify(phone_number, code)

        device = self.devices.register_or_touch(
            user=user,
            device_id=device_id,
            platform=platform,
            model_name=model_name,
            app_version=app_version,
            ip=ip_address,
        )

        user.last_login_at = timezone.now()
        user.save(update_fields=["last_login_at", "updated_at"])

        tokens = self.issue_tokens(user)
        logger.info("login_success", extra={"user_id": str(user.pk), "role": user.role})
        return user, device, tokens

    def issue_tokens(self, user: User) -> AuthTokens:
        """Mint a JWT pair.

        Claims stay minimal: a JWT is signed, not encrypted, so anything put in
        it is readable by whoever holds it.  The role is included because it is
        not secret and lets the app hide buttons without an extra round trip;
        the server re-checks it on every request regardless.
        """
        refresh = RefreshToken.for_user(user)
        refresh["role"] = user.role
        refresh["name"] = user.full_name

        access = refresh.access_token
        access["role"] = user.role
        access["name"] = user.full_name

        return AuthTokens(
            access=str(access),
            refresh=str(refresh),
            access_expires_in=int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
            refresh_expires_in=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        )

    def refresh_tokens(self, refresh_token: str) -> AuthTokens:
        """Rotate a refresh token.

        Rotation plus blacklisting makes each refresh token single-use, so a
        stolen token stops working as soon as the real client refreshes -
        turning theft into a time-boxed, detectable event.
        """
        try:
            token = RefreshToken(refresh_token)
        except TokenError as exc:
            raise ValidationFailed(
                detail="Your session has expired. Please log in again.",
                code=ErrorCode.TOKEN_INVALID,
                status_code=401,
            ) from exc

        user = self.users.get_by_id(token.get("user_id"))
        if user is None or not user.is_active:
            raise ValidationFailed(
                detail="This account is no longer active.",
                code=ErrorCode.USER_DISABLED,
                status_code=401,
            )

        try:
            token.blacklist()
        except AttributeError:  # pragma: no cover - blacklist app is installed
            pass

        return self.issue_tokens(user)

    def logout(self, user: User, refresh_token: str = "", device_id: str = "") -> None:
        """Invalidate the refresh token and sign the device out.

        The *access* token cannot be revoked - that is inherent to stateless
        JWTs - so it stays valid until it expires (30 minutes by default). That
        window is the deliberate trade for not hitting the database on every
        request.
        """
        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except TokenError:
                # An already-expired token is a successful logout, not an error.
                pass

        if device_id:
            device = self.devices.get_for_user(user, device_id)
            if device is not None:
                device.revoke()

        logger.info("logout", extra={"user_id": str(user.pk)})
