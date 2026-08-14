"""The guarded account-bootstrap endpoint.

This endpoint can mint an admin account over plain HTTP, so its guards are the
only thing between a public URL and full control of the system. The negative
tests matter more than the happy path.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

URL = "/api/v1/setup/create-user/"

PAYLOAD = {
    "setup_key": "a-long-enough-setup-key",
    "phone_number": "9876500001",
    "full_name": "First Owner",
    "role": "admin",
}


@pytest.fixture
def setup_key_enabled(settings):
    settings.SETUP_KEY = "a-long-enough-setup-key"
    return settings.SETUP_KEY


class TestDisabledByDefault:
    def test_returns_404_when_no_key_is_configured(self, api_client, settings):
        """No SETUP_KEY means the endpoint should not appear to exist at all."""
        settings.SETUP_KEY = ""

        response = api_client.post(URL, PAYLOAD, format="json")

        assert response.status_code == 404
        assert not User.all_objects.exists()

    def test_404_is_indistinguishable_from_a_missing_route(self, api_client, settings):
        """A 403 would confirm the endpoint exists and invite guessing."""
        settings.SETUP_KEY = ""

        response = api_client.post(URL, PAYLOAD, format="json")
        assert response.data["error"]["code"] == "NOT_FOUND"

    def test_a_short_key_is_refused_rather_than_trusted(self, api_client, settings):
        """A one-character SETUP_KEY would be brute-forced instantly."""
        settings.SETUP_KEY = "x"

        response = api_client.post(URL, {**PAYLOAD, "setup_key": "x"}, format="json")

        assert response.status_code == 500
        assert not User.all_objects.exists()


class TestKeyChecking:
    def test_correct_key_creates_the_account(self, api_client, setup_key_enabled):
        response = api_client.post(URL, PAYLOAD, format="json")

        assert response.status_code == 201
        assert response.data["data"]["role"] == "admin"
        assert response.data["data"]["phone_number"] == "+919876500001"

        user = User.objects.get(phone_number="+919876500001")
        assert user.is_owner
        # OTP is the only credential - no password is ever set.
        assert not user.has_usable_password()

    def test_wrong_key_is_refused(self, api_client, setup_key_enabled):
        response = api_client.post(URL, {**PAYLOAD, "setup_key": "wrong-key-here"}, format="json")

        assert response.status_code == 403
        assert response.data["error"]["code"] == "PERMISSION_DENIED"
        assert not User.all_objects.exists()

    def test_missing_key_is_refused(self, api_client, setup_key_enabled):
        body = {k: v for k, v in PAYLOAD.items() if k != "setup_key"}

        response = api_client.post(URL, body, format="json")

        assert response.status_code == 400
        assert not User.all_objects.exists()

    def test_a_key_that_is_a_prefix_of_the_real_one_is_refused(self, api_client, setup_key_enabled):
        """Guards against a comparison that stops at the first difference."""
        response = api_client.post(URL, {**PAYLOAD, "setup_key": "a-long"}, format="json")

        assert response.status_code == 403
        assert not User.all_objects.exists()

    def test_the_key_is_never_echoed_back(self, api_client, setup_key_enabled):
        response = api_client.post(URL, PAYLOAD, format="json")

        assert "setup_key" not in response.data["data"]
        assert setup_key_enabled not in str(response.data)


class TestAccountCreation:
    def test_can_create_each_role(self, api_client, setup_key_enabled):
        for index, role in enumerate(["admin", "user", "user"], start=2):
            response = api_client.post(
                URL,
                {**PAYLOAD, "phone_number": f"987650000{index}", "role": role},
                format="json",
            )
            assert response.status_code == 201, role
            assert response.data["data"]["role"] == role

    def test_role_defaults_to_owner(self, api_client, setup_key_enabled):
        """The first account created this way is almost always the admin."""
        body = {k: v for k, v in PAYLOAD.items() if k != "role"}

        response = api_client.post(URL, body, format="json")

        assert response.status_code == 201
        assert response.data["data"]["role"] == "admin"

    def test_duplicate_number_is_refused(self, api_client, setup_key_enabled):
        api_client.post(URL, PAYLOAD, format="json")

        response = api_client.post(URL, PAYLOAD, format="json")

        assert response.status_code == 409
        assert User.objects.filter(phone_number="+919876500001").count() == 1

    def test_invalid_phone_number_is_refused(self, api_client, setup_key_enabled):
        response = api_client.post(URL, {**PAYLOAD, "phone_number": "123"}, format="json")

        assert response.status_code == 400
        assert not User.all_objects.exists()

    def test_number_is_normalised(self, api_client, setup_key_enabled):
        response = api_client.post(
            URL, {**PAYLOAD, "phone_number": "+91 98765 00001"}, format="json"
        )

        assert response.status_code == 201
        assert response.data["data"]["phone_number"] == "+919876500001"


class TestCreatedAccountWorks:
    def test_the_new_owner_can_log_in(self, api_client, setup_key_enabled):
        """End to end: bootstrap an account, then use it."""
        import re

        from apps.accounts.sms import InMemorySMSBackend

        assert api_client.post(URL, PAYLOAD, format="json").status_code == 201

        api_client.post("/api/v1/auth/otp/request/", {"phone_number": "9876500001"}, format="json")
        code = re.search(r"\b(\d{6})\b", InMemorySMSBackend.last().message).group(1)

        response = api_client.post(
            "/api/v1/auth/otp/verify/",
            {"phone_number": "9876500001", "otp": code, "device_id": "d1"},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["data"]["user"]["is_owner"] is True


class TestSchema:
    def test_endpoint_appears_in_the_api_docs(self):
        """It has to be reachable from Swagger - that is the point of it."""
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)

        assert "/api/v1/setup/create-user/" in schema["paths"]
        assert "post" in schema["paths"]["/api/v1/setup/create-user/"]
