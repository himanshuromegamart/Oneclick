"""The admin console.

The admin can change anything, so who is let in matters more than what the
screens look like. Access is tied to the admin role - there is no separate
is_staff flag that could drift out of step with the API's rules.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "admin-console-password"


def _with_password(user: User) -> User:
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    return user


@pytest.fixture
def admin_with_password(admin):
    return _with_password(admin)


class TestWhoCanGetIn:
    def test_an_admin_can_log_in(self, admin_with_password):
        client = Client()
        assert client.login(username=admin_with_password.phone_number, password=PASSWORD)

        response = client.get("/admin/")
        assert response.status_code == 200

    def test_an_admin_can_log_in_with_an_unformatted_number(self, admin_with_password):
        """The stored form is +91...; nobody should have to know that."""
        client = Client()
        assert client.login(username="9000000001", password=PASSWORD)

    def test_a_user_account_is_refused(self, member):
        """Correct credentials, wrong role - the console is admin-only."""
        _with_password(member)
        client = Client()
        client.login(username=member.phone_number, password=PASSWORD)

        response = client.get("/admin/", follow=True)
        # Redirected back to the login screen rather than shown the console.
        assert response.status_code == 200
        assert "/admin/login/" in response.request["PATH_INFO"]

    def test_anonymous_is_redirected_to_login(self):
        response = Client().get("/admin/", follow=True)
        assert "/admin/login/" in response.request["PATH_INFO"]

    def test_disabled_admin_cannot_log_in(self, admin_with_password):
        admin_with_password.is_active = False
        admin_with_password.save(update_fields=["is_active"])

        assert not Client().login(username=admin_with_password.phone_number, password=PASSWORD)

    def test_admin_without_a_password_cannot_log_in(self, admin):
        """OTP-only accounts have no admin credential, which is correct."""
        assert not Client().login(username=admin.phone_number, password=PASSWORD)


class TestPermissionHooks:
    """These answers are what the admin actually asks the user object."""

    def test_an_admin_answers_yes(self, admin):
        assert admin.is_staff
        assert admin.is_superuser
        assert admin.has_perm("anything")
        assert admin.has_module_perms("accounts")

    def test_a_user_answers_no(self, member):
        assert not member.is_staff
        assert not member.is_superuser
        assert not member.has_perm("anything")
        assert not member.has_module_perms("accounts")


class TestScreensLoad:
    """A screen that raises on open is useless, and only shows up when opened."""

    @pytest.fixture
    def client(self, admin_with_password):
        client = Client()
        client.login(username=admin_with_password.phone_number, password=PASSWORD)
        return client

    @pytest.mark.parametrize(
        "url_name",
        [
            "admin:accounts_user_changelist",
            "admin:accounts_device_changelist",
            "admin:accounts_otprequest_changelist",
            "admin:folders_folder_changelist",
            "admin:files_fileasset_changelist",
            "admin:files_sharelink_changelist",
        ],
    )
    def test_list_screens(self, client, url_name):
        assert client.get(reverse(url_name)).status_code == 200

    def test_user_add_screen(self, client):
        assert client.get(reverse("admin:accounts_user_add")).status_code == 200

    def test_user_edit_screen(self, client, member):
        assert (
            client.get(reverse("admin:accounts_user_change", args=[member.pk])).status_code == 200
        )

    def test_category_screens(self, client, root_folder):
        assert client.get(reverse("admin:folders_folder_add")).status_code == 200
        assert (
            client.get(reverse("admin:folders_folder_change", args=[root_folder.pk])).status_code
            == 200
        )

    def test_document_edit_screen(self, client, sample_file):
        assert (
            client.get(reverse("admin:files_fileasset_change", args=[sample_file.pk])).status_code
            == 200
        )


class TestCreatingUsersThroughTheAdmin:
    @pytest.fixture
    def client(self, admin_with_password):
        client = Client()
        client.login(username=admin_with_password.phone_number, password=PASSWORD)
        return client

    def test_create_a_user_with_a_password(self, client):
        response = client.post(
            reverse("admin:accounts_user_add"),
            {
                "phone_number": "9700000001",
                "full_name": "Created In Admin",
                "email": "",
                "role": "user",
                "is_active": "on",
                "new_password": "a-good-enough-password",
            },
        )
        assert response.status_code == 302

        user = User.objects.get(phone_number="+919700000001")
        assert user.check_password("a-good-enough-password")
        assert user.role == "user"

    def test_create_a_user_without_a_password(self, client):
        response = client.post(
            reverse("admin:accounts_user_add"),
            {
                "phone_number": "9700000002",
                "full_name": "OTP Only",
                "email": "",
                "role": "user",
                "is_active": "on",
                "new_password": "",
            },
        )
        assert response.status_code == 302

        user = User.objects.get(phone_number="+919700000002")
        # OTP is then the only way in, which is the product default.
        assert not user.has_usable_password()

    def test_phone_number_is_normalised(self, client):
        client.post(
            reverse("admin:accounts_user_add"),
            {
                "phone_number": "0 97000 00003",
                "full_name": "Messy Number",
                "email": "",
                "role": "user",
                "is_active": "on",
                "new_password": "",
            },
        )
        assert User.objects.filter(phone_number="+919700000003").exists()

    def test_weak_password_is_rejected(self, client):
        response = client.post(
            reverse("admin:accounts_user_add"),
            {
                "phone_number": "9700000004",
                "full_name": "Weak",
                "email": "",
                "role": "user",
                "is_active": "on",
                "new_password": "abc",
            },
        )
        # Re-rendered with errors rather than redirecting on success.
        assert response.status_code == 200
        assert not User.objects.filter(phone_number="+919700000004").exists()

    def test_deleting_a_user_is_a_soft_delete(self, client, member):
        """A hard delete would orphan or cascade away their uploads."""
        client.post(reverse("admin:accounts_user_delete", args=[member.pk]), {"post": "yes"})

        member.refresh_from_db()
        assert member.is_deleted
        assert not member.is_active


class TestTheLastAdminCannotBeRemoved:
    """Only an Admin can reach either console, and there is no shell on the
    host to repair it from - so losing the last one is unrecoverable.

    Easy to do by accident, because the person doing it is usually editing
    their own account.
    """

    @pytest.fixture
    def client(self, admin_with_password):
        client = Client()
        client.login(username=admin_with_password.phone_number, password=PASSWORD)
        return client

    def _edit(self, client, user, **overrides):
        payload = {
            "phone_number": user.phone_number,
            "full_name": user.full_name,
            "email": "",
            "role": "admin",
            "is_active": "on",
            "new_password": "",
        }
        payload.update(overrides)
        return client.post(reverse("admin:accounts_user_change", args=[user.pk]), payload)

    def test_demoting_the_only_admin_is_refused(self, client, admin_with_password):
        response = self._edit(client, admin_with_password, role="user")

        assert response.status_code == 200  # form re-rendered with the error
        # Checked explicitly: any validation failure would give a 200, and this
        # test is worthless if it passes for the wrong reason.
        assert b"only active admin" in response.content
        admin_with_password.refresh_from_db()
        assert admin_with_password.is_admin

    def test_disabling_the_only_admin_is_refused(self, client, admin_with_password):
        response = self._edit(client, admin_with_password, is_active="")

        assert response.status_code == 200
        assert b"only active admin" in response.content
        admin_with_password.refresh_from_db()
        assert admin_with_password.is_active

    def test_deleting_the_only_admin_is_refused(self, client, admin_with_password):
        client.post(
            reverse("admin:accounts_user_delete", args=[admin_with_password.pk]),
            {"post": "yes"},
        )

        admin_with_password.refresh_from_db()
        assert not admin_with_password.is_deleted

    def test_demoting_is_allowed_once_a_second_admin_exists(
        self, client, admin_with_password, member
    ):
        member.role = "admin"
        member.save(update_fields=["role"])

        response = self._edit(client, admin_with_password, role="user")

        assert response.status_code == 302
        admin_with_password.refresh_from_db()
        assert not admin_with_password.is_admin

    def test_an_inactive_second_admin_does_not_count(self, client, admin_with_password, member):
        """A disabled admin cannot sign in, so they are not a way back in."""
        member.role = "admin"
        member.is_active = False
        member.save(update_fields=["role", "is_active"])

        self._edit(client, admin_with_password, role="user")

        admin_with_password.refresh_from_db()
        assert admin_with_password.is_admin

    def test_a_user_account_can_still_be_deleted(self, client, admin_with_password, member):
        """The guard must not block ordinary removals."""
        client.post(reverse("admin:accounts_user_delete", args=[member.pk]), {"post": "yes"})

        member.refresh_from_db()
        assert member.is_deleted


class TestApiIsUnaffectedByCsrf:
    """Adding CSRF middleware for the admin must not break the token API.

    DRF wraps every APIView in csrf_exempt, but that is worth proving rather
    than assuming - a regression here would break every mobile write.
    """

    def test_unauthenticated_post_still_reaches_the_view(self, api_client, member):
        response = api_client.post(
            "/api/v1/auth/otp/request/", {"phone_number": member.phone_number}, format="json"
        )
        assert response.status_code == 200

    def test_authenticated_post_works_without_a_csrf_token(self, member_client):
        response = member_client.post(
            "/api/v1/categories/", {"name": "No CSRF Needed"}, format="json"
        )
        assert response.status_code == 201

    def test_authenticated_delete_works(self, member_client):
        created = member_client.post("/api/v1/categories/", {"name": "To Delete"}, format="json")
        folder_id = created.data["data"]["id"]

        assert member_client.delete(f"/api/v1/categories/{folder_id}/").status_code == 200
