"""The admin console.

The admin can change anything, so who is let in matters more than what the
screens look like. Access is tied to the owner role - there is no separate
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
def admin_owner(owner):
    return _with_password(owner)


class TestWhoCanGetIn:
    def test_owner_can_log_in(self, admin_owner):
        client = Client()
        assert client.login(username=admin_owner.phone_number, password=PASSWORD)

        response = client.get("/admin/")
        assert response.status_code == 200

    def test_owner_can_log_in_with_an_unformatted_number(self, admin_owner):
        """The stored form is +91...; nobody should have to know that."""
        client = Client()
        assert client.login(username="9000000001", password=PASSWORD)

    def test_staff_is_refused(self, staff):
        _with_password(staff)
        client = Client()
        client.login(username=staff.phone_number, password=PASSWORD)

        response = client.get("/admin/", follow=True)
        # Redirected back to the login screen rather than shown the console.
        assert response.status_code == 200
        assert "/admin/login/" in response.request["PATH_INFO"]

    def test_viewer_is_refused(self, viewer):
        _with_password(viewer)
        client = Client()
        client.login(username=viewer.phone_number, password=PASSWORD)

        response = client.get("/admin/", follow=True)
        assert "/admin/login/" in response.request["PATH_INFO"]

    def test_anonymous_is_redirected_to_login(self):
        response = Client().get("/admin/", follow=True)
        assert "/admin/login/" in response.request["PATH_INFO"]

    def test_disabled_owner_cannot_log_in(self, admin_owner):
        admin_owner.is_active = False
        admin_owner.save(update_fields=["is_active"])

        assert not Client().login(username=admin_owner.phone_number, password=PASSWORD)

    def test_owner_without_a_password_cannot_log_in(self, owner):
        """OTP-only accounts have no admin credential, which is correct."""
        assert not Client().login(username=owner.phone_number, password=PASSWORD)


class TestPermissionHooks:
    """These three answers are what the admin actually asks the user object."""

    def test_owner_answers_yes(self, owner):
        assert owner.is_staff
        assert owner.is_superuser
        assert owner.has_perm("anything")
        assert owner.has_module_perms("accounts")

    def test_staff_answers_no(self, staff):
        assert not staff.is_staff
        assert not staff.has_perm("anything")
        assert not staff.has_module_perms("accounts")

    def test_viewer_answers_no(self, viewer):
        assert not viewer.is_staff
        assert not viewer.has_module_perms("accounts")


class TestScreensLoad:
    """A screen that raises on open is useless, and only shows up when opened."""

    @pytest.fixture
    def client(self, admin_owner):
        client = Client()
        client.login(username=admin_owner.phone_number, password=PASSWORD)
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

    def test_user_edit_screen(self, client, staff):
        assert client.get(reverse("admin:accounts_user_change", args=[staff.pk])).status_code == 200

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
    def client(self, admin_owner):
        client = Client()
        client.login(username=admin_owner.phone_number, password=PASSWORD)
        return client

    def test_create_a_user_with_a_password(self, client):
        response = client.post(
            reverse("admin:accounts_user_add"),
            {
                "phone_number": "9700000001",
                "full_name": "Created In Admin",
                "email": "",
                "role": "staff",
                "is_active": "on",
                "new_password": "a-good-enough-password",
            },
        )
        assert response.status_code == 302

        user = User.objects.get(phone_number="+919700000001")
        assert user.check_password("a-good-enough-password")
        assert user.role == "staff"

    def test_create_a_user_without_a_password(self, client):
        response = client.post(
            reverse("admin:accounts_user_add"),
            {
                "phone_number": "9700000002",
                "full_name": "OTP Only",
                "email": "",
                "role": "viewer",
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
                "role": "staff",
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
                "role": "staff",
                "is_active": "on",
                "new_password": "abc",
            },
        )
        # Re-rendered with errors rather than redirecting on success.
        assert response.status_code == 200
        assert not User.objects.filter(phone_number="+919700000004").exists()

    def test_deleting_a_user_is_a_soft_delete(self, client, staff):
        """A hard delete would orphan or cascade away their uploads."""
        client.post(reverse("admin:accounts_user_delete", args=[staff.pk]), {"post": "yes"})

        staff.refresh_from_db()
        assert staff.is_deleted
        assert not staff.is_active


class TestApiIsUnaffectedByCsrf:
    """Adding CSRF middleware for the admin must not break the token API.

    DRF wraps every APIView in csrf_exempt, but that is worth proving rather
    than assuming - a regression here would break every mobile write.
    """

    def test_unauthenticated_post_still_reaches_the_view(self, api_client, staff):
        response = api_client.post(
            "/api/v1/auth/otp/request/", {"phone_number": staff.phone_number}, format="json"
        )
        assert response.status_code == 200

    def test_authenticated_post_works_without_a_csrf_token(self, staff_client):
        response = staff_client.post(
            "/api/v1/categories/", {"name": "No CSRF Needed"}, format="json"
        )
        assert response.status_code == 201

    def test_authenticated_delete_works(self, staff_client):
        created = staff_client.post("/api/v1/categories/", {"name": "To Delete"}, format="json")
        folder_id = created.data["data"]["id"]

        assert staff_client.delete(f"/api/v1/categories/{folder_id}/").status_code == 200
