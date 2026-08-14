"""Password sign-in.

A password is a standing credential - unlike an OTP it does not expire on its
own - so most of what matters here is what happens when it is *wrong*.
"""

from __future__ import annotations

import re

import pytest

from apps.accounts.models import User
from apps.accounts.services import AuthService
from apps.accounts.sms import InMemorySMSBackend
from apps.core.exceptions import RateLimited, ValidationFailed

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"
LOGIN_URL = "/api/v1/auth/login/"


@pytest.fixture
def password_user(db):
    user = User.objects.create_user(
        phone_number="9800000001", full_name="Password User", role="admin"
    )
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    return user


class TestPasswordLogin:
    def test_correct_password_returns_tokens(self, api_client, password_user):
        response = api_client.post(
            LOGIN_URL,
            {"phone_number": "9800000001", "password": PASSWORD, "device_id": "d1"},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["data"]["tokens"]["access"]
        assert response.data["data"]["user"]["is_owner"] is True

    def test_the_token_actually_works(self, api_client, password_user):
        tokens = api_client.post(
            LOGIN_URL, {"phone_number": "9800000001", "password": PASSWORD}, format="json"
        ).data["data"]["tokens"]

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        assert api_client.get("/api/v1/auth/me/").status_code == 200

    def test_login_updates_last_login(self, api_client, password_user):
        api_client.post(
            LOGIN_URL, {"phone_number": "9800000001", "password": PASSWORD}, format="json"
        )

        password_user.refresh_from_db()
        assert password_user.last_login_at is not None

    def test_device_id_is_optional(self, api_client, password_user):
        response = api_client.post(
            LOGIN_URL, {"phone_number": "9800000001", "password": PASSWORD}, format="json"
        )

        assert response.status_code == 200
        assert response.data["data"]["device"] is None

    def test_any_phone_format_works(self, api_client, password_user):
        for spelling in ("9800000001", "+919800000001", "0 98000 00001"):
            response = api_client.post(
                LOGIN_URL, {"phone_number": spelling, "password": PASSWORD}, format="json"
            )
            assert response.status_code == 200, spelling


class TestFailuresAreIndistinguishable:
    """Every failure must look identical, or the endpoint becomes a way to
    discover which numbers have accounts."""

    def _failure(self, api_client, **body):
        return api_client.post(LOGIN_URL, body, format="json")

    def test_wrong_password(self, api_client, password_user):
        response = self._failure(api_client, phone_number="9800000001", password="wrong-one-here")
        assert response.status_code == 401
        assert response.data["error"]["code"] == "AUTHENTICATION_FAILED"

    def test_unknown_number(self, api_client, db):
        response = self._failure(api_client, phone_number="9800009999", password="anything-here")
        assert response.status_code == 401
        assert response.data["error"]["code"] == "AUTHENTICATION_FAILED"

    def test_disabled_account(self, api_client, password_user):
        password_user.is_active = False
        password_user.save(update_fields=["is_active"])

        response = self._failure(api_client, phone_number="9800000001", password=PASSWORD)
        assert response.status_code == 401

    def test_account_with_no_password(self, api_client, db):
        User.objects.create_user(phone_number="9800000002", full_name="OTP Only")

        response = self._failure(api_client, phone_number="9800000002", password="anything-here")
        assert response.status_code == 401

    def test_all_four_give_the_same_message(self, api_client, password_user, db):
        User.objects.create_user(phone_number="9800000003", full_name="OTP Only")
        messages = {
            self._failure(api_client, phone_number=phone, password=password).data["error"][
                "message"
            ]
            for phone, password in [
                ("9800000001", "wrong-one-here"),
                ("9800009999", "anything-here"),
                ("9800000003", "anything-here"),
            ]
        }
        assert len(messages) == 1, f"failures are distinguishable: {messages}"


class TestLockout:
    def test_repeated_failures_lock_the_number(self, password_user, settings):
        settings.LOGIN_SETTINGS = {**settings.LOGIN_SETTINGS, "MAX_FAILED_ATTEMPTS": 3}
        auth = AuthService()

        for _ in range(3):
            with pytest.raises((ValidationFailed, RateLimited)):
                auth.login_with_password(phone_number="9800000001", password="nope-nope-nope")

        # Even the correct password is refused now - the lock is on the number,
        # so an attacker cannot simply keep going.
        with pytest.raises(RateLimited):
            auth.login_with_password(phone_number="9800000001", password=PASSWORD)

    def test_a_successful_login_clears_the_counter(self, password_user, settings):
        settings.LOGIN_SETTINGS = {**settings.LOGIN_SETTINGS, "MAX_FAILED_ATTEMPTS": 3}
        auth = AuthService()

        for _ in range(2):
            with pytest.raises(ValidationFailed):
                auth.login_with_password(phone_number="9800000001", password="nope-nope-nope")

        auth.login_with_password(phone_number="9800000001", password=PASSWORD)

        # Counter reset, so two more failures must not trip the lock.
        for _ in range(2):
            with pytest.raises(ValidationFailed):
                auth.login_with_password(phone_number="9800000001", password="nope-nope-nope")


class TestPasswordStrength:
    def test_short_password_is_refused_at_creation(self, api_client, settings):
        settings.SETUP_KEY = "a-long-enough-setup-key"

        response = api_client.post(
            "/api/v1/setup/create-user/",
            {
                "setup_key": "a-long-enough-setup-key",
                "phone_number": "9800000004",
                "full_name": "Weak",
                "password": "abc",
            },
            format="json",
        )
        assert response.status_code == 400
        assert "password" in response.data["error"]["field_errors"]

    def test_all_numeric_password_is_refused(self, api_client, settings):
        settings.SETUP_KEY = "a-long-enough-setup-key"

        response = api_client.post(
            "/api/v1/setup/create-user/",
            {
                "setup_key": "a-long-enough-setup-key",
                "phone_number": "9800000005",
                "full_name": "Numeric",
                "password": "12345678",
            },
            format="json",
        )
        assert response.status_code == 400


class TestSetupEndpointWithPassword:
    def test_creating_with_a_password_enables_password_login(self, api_client, settings):
        settings.SETUP_KEY = "a-long-enough-setup-key"

        created = api_client.post(
            "/api/v1/setup/create-user/",
            {
                "setup_key": "a-long-enough-setup-key",
                "phone_number": "9800000006",
                "full_name": "New Owner",
                "role": "admin",
                "password": PASSWORD,
            },
            format="json",
        )
        assert created.status_code == 201
        assert created.data["data"]["has_password"] is True
        # The password must never come back in the response.
        assert PASSWORD not in str(created.data)

        response = api_client.post(
            LOGIN_URL, {"phone_number": "9800000006", "password": PASSWORD}, format="json"
        )
        assert response.status_code == 200

    def test_creating_without_a_password_leaves_otp_only(self, api_client, settings):
        settings.SETUP_KEY = "a-long-enough-setup-key"

        created = api_client.post(
            "/api/v1/setup/create-user/",
            {
                "setup_key": "a-long-enough-setup-key",
                "phone_number": "9800000007",
                "full_name": "OTP Only",
            },
            format="json",
        )
        assert created.status_code == 201
        assert created.data["data"]["has_password"] is False


class TestChangePassword:
    def _client_for(self, user):
        from rest_framework.test import APIClient

        client = APIClient()
        tokens = AuthService().issue_tokens(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.access}")
        return client

    def test_change_with_correct_current_password(self, password_user):
        client = self._client_for(password_user)

        response = client.post(
            "/api/v1/auth/change-password/",
            {"current_password": PASSWORD, "new_password": "a-brand-new-secret"},
            format="json",
        )
        assert response.status_code == 200

        password_user.refresh_from_db()
        assert password_user.check_password("a-brand-new-secret")

    def test_wrong_current_password_is_refused(self, password_user):
        client = self._client_for(password_user)

        response = client.post(
            "/api/v1/auth/change-password/",
            {"current_password": "not-the-password", "new_password": "a-brand-new-secret"},
            format="json",
        )
        assert response.status_code == 400

        password_user.refresh_from_db()
        assert password_user.check_password(PASSWORD)

    def test_otp_only_user_can_set_a_first_password(self, db):
        """Without this, an OTP-only account could never gain a password
        without shell access."""
        user = User.objects.create_user(phone_number="9800000008", full_name="OTP Only")
        client = self._client_for(user)

        response = client.post(
            "/api/v1/auth/change-password/",
            {"new_password": "my-first-password"},
            format="json",
        )
        assert response.status_code == 200

        user.refresh_from_db()
        assert user.has_usable_password()

    def test_needs_authentication(self, api_client):
        response = api_client.post(
            "/api/v1/auth/change-password/", {"new_password": "whatever-here"}, format="json"
        )
        assert response.status_code == 401


class TestOTPStillWorks:
    """Password login is an addition, not a replacement."""

    def test_otp_login_still_works_for_a_password_user(self, api_client, password_user):
        api_client.post("/api/v1/auth/otp/request/", {"phone_number": "9800000001"}, format="json")
        code = re.search(r"\b(\d{6})\b", InMemorySMSBackend.last().message).group(1)

        response = api_client.post(
            "/api/v1/auth/otp/verify/",
            {"phone_number": "9800000001", "otp": code, "device_id": "d1"},
            format="json",
        )
        assert response.status_code == 200

    def test_both_routes_issue_equivalent_tokens(self, api_client, password_user):
        by_password = api_client.post(
            LOGIN_URL, {"phone_number": "9800000001", "password": PASSWORD}, format="json"
        ).data["data"]["tokens"]

        api_client.post("/api/v1/auth/otp/request/", {"phone_number": "9800000001"}, format="json")
        code = re.search(r"\b(\d{6})\b", InMemorySMSBackend.last().message).group(1)
        by_otp = api_client.post(
            "/api/v1/auth/otp/verify/",
            {"phone_number": "9800000001", "otp": code},
            format="json",
        ).data["data"]["tokens"]

        for tokens in (by_password, by_otp):
            api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
            assert api_client.get("/api/v1/auth/me/").status_code == 200


class TestManagementCommand:
    def test_create_with_password(self, db):
        from django.core.management import call_command

        call_command(
            "create_user",
            "--phone",
            "9800000009",
            "--name",
            "CLI User",
            "--role",
            "admin",
            "--password",
            PASSWORD,
        )

        user = User.objects.get(phone_number="+919800000009")
        assert user.check_password(PASSWORD)

    def test_weak_password_is_refused(self, db):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command(
                "create_user", "--phone", "9800000010", "--name", "Weak", "--password", "abc"
            )

    def test_password_can_be_added_to_an_existing_user(self, db):
        from django.core.management import call_command

        user = User.objects.create_user(phone_number="9800000011", full_name="Later")
        assert not user.has_usable_password()

        call_command("create_user", "--phone", "9800000011", "--password", PASSWORD, "--update")

        user.refresh_from_db()
        assert user.check_password(PASSWORD)
