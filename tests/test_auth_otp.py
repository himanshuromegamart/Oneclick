"""OTP login.

This is the only way into the app, so the tests assert on the defences, not
just the happy path: an OTP that can be replayed, guessed without limit, or
issued to a disabled account is a real breach.
"""

from __future__ import annotations

import re

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import OTPRequest, User
from apps.accounts.services import AuthService, OTPService
from apps.accounts.sms import InMemorySMSBackend
from apps.core.exceptions import RateLimited, ValidationFailed

pytestmark = pytest.mark.django_db


def sent_code() -> str:
    """Pull the OTP out of the last SMS the in-memory backend captured."""
    message = InMemorySMSBackend.last()
    assert message is not None, "no SMS was sent"
    match = re.search(r"\b(\d{4,8})\b", message.message)
    assert match, f"no code found in {message.message!r}"
    return match.group(1)


class TestRequestOTP:
    def test_registered_user_receives_a_code(self, member):
        challenge = OTPService().request_otp(member.phone_number)

        assert challenge.phone_number == member.phone_number
        assert len(InMemorySMSBackend.outbox) == 1

    def test_code_is_never_stored_in_plain_text(self, member):
        OTPService().request_otp(member.phone_number)
        code = sent_code()

        otp = OTPRequest.objects.latest("created_at")
        assert code not in otp.code_hash
        assert otp.code_hash.startswith(("pbkdf2", "argon2", "bcrypt", "md5$"))

    def test_unknown_number_is_rejected(self, db):
        with pytest.raises(ValidationFailed) as exc:
            OTPService().request_otp("9111111111")
        assert exc.value.status_code == 403
        assert not InMemorySMSBackend.outbox

    def test_disabled_account_cannot_request(self, member):
        member.is_active = False
        member.save(update_fields=["is_active"])

        with pytest.raises(ValidationFailed):
            OTPService().request_otp(member.phone_number)

    def test_resend_is_blocked_during_cooldown(self, member):
        service = OTPService()
        service.request_otp(member.phone_number)

        with pytest.raises(RateLimited) as exc:
            service.request_otp(member.phone_number, is_resend=True)
        assert "retry_after_seconds" in exc.value.details

    def test_daily_cap_is_enforced(self, member, settings):
        settings.OTP_SETTINGS = {**settings.OTP_SETTINGS, "MAX_SENDS_PER_DAY": 2}
        service = OTPService()

        service.request_otp(member.phone_number)
        cache.delete(service._cooldown_key(member.phone_number))
        service.request_otp(member.phone_number)
        cache.delete(service._cooldown_key(member.phone_number))

        with pytest.raises(RateLimited):
            service.request_otp(member.phone_number)

    def test_issuing_a_new_code_kills_the_previous_one(self, member, settings):
        settings.OTP_SETTINGS = {**settings.OTP_SETTINGS, "RESEND_COOLDOWN_SECONDS": 0}
        service = OTPService()

        service.request_otp(member.phone_number)
        first_code = sent_code()
        cache.clear()
        service.request_otp(member.phone_number)

        # Otherwise every resend would widen the window an attacker can guess in.
        with pytest.raises(ValidationFailed):
            service.verify(member.phone_number, first_code)


class TestVerifyOTP:
    def test_correct_code_returns_the_user(self, member):
        service = OTPService()
        service.request_otp(member.phone_number)

        assert service.verify(member.phone_number, sent_code()) == member

    def test_code_cannot_be_replayed(self, member):
        service = OTPService()
        service.request_otp(member.phone_number)
        code = sent_code()

        service.verify(member.phone_number, code)
        with pytest.raises(ValidationFailed):
            service.verify(member.phone_number, code)

    def test_wrong_code_is_rejected_and_counted(self, member):
        service = OTPService()
        service.request_otp(member.phone_number)

        with pytest.raises(ValidationFailed):
            service.verify(member.phone_number, "000000")

        assert OTPRequest.objects.latest("created_at").attempts == 1

    def test_expired_code_is_rejected(self, member):
        service = OTPService()
        service.request_otp(member.phone_number)
        code = sent_code()

        OTPRequest.objects.update(expires_at=timezone.now() - timezone.timedelta(seconds=1))

        with pytest.raises(ValidationFailed):
            service.verify(member.phone_number, code)

    def test_repeated_failures_lock_the_number_out(self, member, settings):
        settings.OTP_SETTINGS = {**settings.OTP_SETTINGS, "MAX_VERIFY_ATTEMPTS": 3}
        service = OTPService()
        service.request_otp(member.phone_number)

        for _ in range(3):
            with pytest.raises((ValidationFailed, RateLimited)):
                service.verify(member.phone_number, "111111")

        # Even the correct code must now fail - the lockout is on the number,
        # so requesting a fresh code does not reset the attacker's budget.
        with pytest.raises(RateLimited):
            service.verify(member.phone_number, sent_code())

    def test_non_numeric_input_is_rejected(self, member):
        service = OTPService()
        service.request_otp(member.phone_number)

        with pytest.raises(ValidationFailed):
            service.verify(member.phone_number, "abcdef")


class TestTokens:
    def test_login_returns_tokens_and_registers_the_device(self, member):
        auth = AuthService()
        auth.otp.request_otp(member.phone_number)

        user, device, tokens = auth.login_with_otp(
            phone_number=member.phone_number,
            code=sent_code(),
            device_id="pixel-8-abc",
            platform="android",
            model_name="Pixel 8",
        )

        assert user == member
        assert device.device_id == "pixel-8-abc"
        assert tokens.access and tokens.refresh

        member.refresh_from_db()
        assert member.last_login_at is not None

    def test_login_works_without_a_device_id(self, member):
        """Device tracking is a convenience, not a requirement."""
        auth = AuthService()
        auth.otp.request_otp(member.phone_number)

        user, device, tokens = auth.login_with_otp(
            phone_number=member.phone_number, code=sent_code()
        )
        assert user == member
        assert device is None
        assert tokens.access

    def test_refresh_rotates_and_burns_the_old_token(self, member):
        auth = AuthService()
        tokens = auth.issue_tokens(member)

        rotated = auth.refresh_tokens(tokens.refresh)
        assert rotated.refresh != tokens.refresh

        # Replaying a spent token must fail - that is what turns theft into a
        # time-boxed, detectable event.
        with pytest.raises(ValidationFailed):
            auth.refresh_tokens(tokens.refresh)

    def test_refresh_fails_once_the_account_is_disabled(self, member):
        auth = AuthService()
        tokens = auth.issue_tokens(member)

        User.objects.filter(pk=member.pk).update(is_active=False)

        with pytest.raises(ValidationFailed):
            auth.refresh_tokens(tokens.refresh)


class TestAuthEndpoints:
    def test_full_login_round_trip(self, api_client, member):
        response = api_client.post(
            "/api/v1/auth/otp/request/", {"phone_number": member.phone_number}, format="json"
        )
        assert response.status_code == 200
        assert response.data["success"] is True

        response = api_client.post(
            "/api/v1/auth/otp/verify/",
            {
                "phone_number": member.phone_number,
                "otp": sent_code(),
                "device_id": "test-device",
                "platform": "android",
            },
            format="json",
        )
        assert response.status_code == 200
        assert response.data["data"]["tokens"]["access"]
        assert response.data["data"]["user"]["role"] == "user"

    def test_any_common_phone_format_works(self, api_client, member):
        """The app must not have to normalise the number itself."""
        for spelling in ("9000000002", "+919000000002", "0 90000 00002"):
            cache.clear()
            response = api_client.post(
                "/api/v1/auth/otp/request/", {"phone_number": spelling}, format="json"
            )
            assert response.status_code == 200, spelling

    def test_unknown_number_returns_a_stable_error_code(self, api_client, db):
        response = api_client.post(
            "/api/v1/auth/otp/request/", {"phone_number": "9111111111"}, format="json"
        )
        assert response.status_code == 403
        assert response.data["error"]["code"] == "USER_NOT_REGISTERED"

    def test_protected_endpoint_requires_a_token(self, api_client):
        assert api_client.get("/api/v1/auth/me/").status_code == 401

    def test_me_returns_the_current_user(self, member_client, member):
        response = member_client.get("/api/v1/auth/me/")
        assert response.status_code == 200
        assert response.data["data"]["phone_number"] == member.phone_number
        assert response.data["data"]["can_contribute"] is True

    def test_user_can_edit_their_own_name(self, member_client):
        response = member_client.patch(
            "/api/v1/auth/me/", {"full_name": "Ramesh Kumar"}, format="json"
        )
        assert response.status_code == 200
        assert response.data["data"]["full_name"] == "Ramesh Kumar"

    def test_user_cannot_promote_themselves(self, member_client):
        """Self-service escalation is the classic hole in a simple role model."""
        member_client.patch("/api/v1/auth/me/", {"role": "admin"}, format="json")

        response = member_client.get("/api/v1/auth/me/")
        assert response.data["data"]["role"] == "user"

    def test_logout_succeeds(self, member_client):
        assert member_client.post("/api/v1/auth/logout/", {}, format="json").status_code == 200


class TestCreateUserCommand:
    """`manage.py create_user` is the only way an account is created."""

    def test_creates_an_account(self, db):
        from django.core.management import call_command

        call_command("create_user", "--phone", "9876543210", "--name", "Owner", "--role", "admin")

        user = User.objects.get(phone_number="+919876543210")
        assert user.is_owner
        # OTP is the only credential.
        assert not user.has_usable_password()

    def test_rejects_a_duplicate_without_update(self, db, member):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("create_user", "--phone", member.phone_number, "--name", "Clone")

    def test_updates_a_role(self, db, member):
        from django.core.management import call_command

        call_command("create_user", "--phone", member.phone_number, "--role", "user", "--update")

        member.refresh_from_db()
        assert member.role == "user"
        assert not member.is_admin

    def test_disables_and_reenables(self, db, member):
        from django.core.management import call_command

        call_command("create_user", "--phone", member.phone_number, "--disable")
        member.refresh_from_db()
        assert not member.is_active

        call_command("create_user", "--phone", member.phone_number, "--enable")
        member.refresh_from_db()
        assert member.is_active

    def test_rejects_an_invalid_number(self, db):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("create_user", "--phone", "123", "--name", "Nope")
