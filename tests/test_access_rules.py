"""Who can do what.

The access model is two roles that differ by exactly one thing: an Admin can
open the web dashboard, a User cannot. Everything else is identical.

These tests are the specification. Most of them exist to prove a *negative* -
that no second difference has crept back in - which is why so many of them
assert that a User succeeds. If someone later adds a role check to an API view,
one of these fails.
"""

from __future__ import annotations

import io

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db

PASSWORD = "access-rules-password"


def upload_as(file_service, user, folder, name="doc.pdf"):
    return file_service.upload(
        user,
        folder_id=folder.pk,
        file_obj=io.BytesIO(b"file contents"),
        filename=name,
        size_bytes=13,
        content_type="application/pdf",
    )


def browser_for(user) -> Client:
    """A session-authenticated browser, which is how the dashboard is used."""
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    client = Client()
    client.login(username=user.phone_number, password=PASSWORD)
    return client


class TestRoleFlags:
    def test_admin_is_admin(self, admin):
        assert admin.is_admin

    def test_user_is_not(self, member):
        assert not member.is_admin

    def test_is_owner_still_answers_for_the_shipped_app(self, admin, member):
        """The mobile app reads `is_owner`; it must keep tracking `is_admin`."""
        assert admin.is_owner is admin.is_admin
        assert member.is_owner is member.is_admin

    def test_both_roles_may_contribute(self, admin, member):
        assert admin.can_contribute
        assert member.can_contribute


class TestTheOneRestriction:
    """A User is refused the dashboard, and nothing else."""

    def test_a_user_cannot_sign_in_to_the_dashboard(self, member):
        member.set_password(PASSWORD)
        member.save(update_fields=["password"])

        response = Client().post(
            reverse("dashboard:login"),
            {"phone_number": member.phone_number, "password": PASSWORD},
        )

        assert response.status_code == 200  # re-rendered form, not a redirect
        assert not response.wsgi_request.user.is_authenticated

    def test_the_refusal_does_not_reveal_that_the_password_was_right(self, member):
        """Otherwise this form tells an attacker which numbers exist, and which
        of those are admins - worth more than the login it just refused."""
        member.set_password(PASSWORD)
        member.save(update_fields=["password"])

        def errors_for(password: str) -> list[str]:
            response = Client().post(
                reverse("dashboard:login"),
                {"phone_number": member.phone_number, "password": password},
            )
            assert response.status_code == 200
            return response.context["form"].non_field_errors()

        # Right password on a User account, and an outright wrong password,
        # have to be indistinguishable.
        assert list(errors_for(PASSWORD)) == list(errors_for("not-the-password"))
        assert errors_for(PASSWORD)  # and it did actually refuse

    def test_an_admin_can_sign_in_to_the_dashboard(self, admin):
        assert browser_for(admin).get(reverse("dashboard:home")).status_code == 200

    @pytest.mark.parametrize("page", ["home", "users", "categories"])
    def test_every_dashboard_page_refuses_a_user(self, member, page):
        response = browser_for(member).get(reverse(f"dashboard:{page}"))
        assert response.status_code == 302
        assert reverse("dashboard:login") in response.url

    def test_demoting_an_admin_locks_them_out_on_the_next_click(self, admin):
        """Checked per request, not at login - so it does not wait for the
        session to expire."""
        browser = browser_for(admin)
        assert browser.get(reverse("dashboard:home")).status_code == 200

        admin.role = "user"
        admin.save(update_fields=["role"])

        assert browser.get(reverse("dashboard:home")).status_code == 302

    def test_a_user_cannot_open_the_django_admin_site(self, member):
        response = browser_for(member).get(reverse("admin:index"))
        assert response.status_code in (302, 403)

    def test_a_user_can_still_use_the_mobile_api(self, member_client):
        """The point of the whole change: no dashboard, full app."""
        assert member_client.get("/api/v1/auth/me/").status_code == 200


class TestUsersHaveFullAppAccess:
    """Everything a User was previously refused."""

    def test_a_user_can_create_a_category(self, member_client):
        response = member_client.post(
            "/api/v1/categories/", {"name": "Certificates"}, format="json"
        )
        assert response.status_code == 201

    def test_a_user_can_delete_a_category(self, member_client, root_folder):
        assert member_client.delete(f"/api/v1/categories/{root_folder.pk}/").status_code == 200

    def test_a_user_can_upload(self, member_client, child_folder):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = member_client.post(
            "/api/v1/documents/",
            {
                "folder_id": str(child_folder.pk),
                "file": SimpleUploadedFile("mine.pdf", b"data", content_type="application/pdf"),
            },
            format="multipart",
        )
        assert response.status_code == 201

    def test_a_user_can_browse_and_search(self, member_client, sample_file):
        assert member_client.get("/api/v1/categories/").status_code == 200
        assert member_client.get("/api/v1/search/?q=spec").status_code == 200

    def test_a_user_can_download(self, member_client, sample_file):
        assert member_client.get(f"/api/v1/documents/{sample_file.pk}/download/").status_code == 200


class TestNoOwnershipRule:
    """ "Only your own files" is gone. Both roles may touch anything."""

    def test_a_user_may_modify_someone_elses_file(
        self, member, other_member, child_folder, file_service
    ):
        theirs = upload_as(file_service, other_member, child_folder)
        assert member.can_modify(theirs)

    def test_a_user_can_delete_a_file_somebody_else_uploaded(
        self, member_client, other_member, child_folder, file_service
    ):
        theirs = upload_as(file_service, other_member, child_folder, name="theirs.pdf")

        assert member_client.delete(f"/api/v1/documents/{theirs.pk}/").status_code == 200

    def test_a_user_can_rename_a_file_somebody_else_uploaded(
        self, member_client, other_member, child_folder, file_service
    ):
        theirs = upload_as(file_service, other_member, child_folder, name="theirs.pdf")

        response = member_client.patch(
            f"/api/v1/documents/{theirs.pk}/", {"name": "renamed.pdf"}, format="json"
        )
        assert response.status_code == 200

    def test_a_user_can_permanently_delete(self, member_client, sample_file, file_service, member):
        """Purge is the one action with no undo, and it is open to both roles."""
        file_service.delete(member, sample_file)

        assert member_client.delete(f"/api/v1/documents/{sample_file.pk}/purge/").status_code == 200

    def test_an_admin_can_delete_anyones_file(self, admin_client, sample_file):
        # sample_file was uploaded by `member`.
        assert admin_client.delete(f"/api/v1/documents/{sample_file.pk}/").status_code == 200


class TestSharedRecycleBin:
    def test_both_roles_see_the_same_deleted_files(
        self, admin_client, member_client, sample_file, file_service, other_member, child_folder
    ):
        theirs = upload_as(file_service, other_member, child_folder, name="theirs.pdf")
        file_service.delete(other_member, theirs)

        as_admin = admin_client.get("/api/v1/documents/deleted/")
        as_member = member_client.get("/api/v1/documents/deleted/")

        assert [row["id"] for row in as_admin.data["data"]] == [
            row["id"] for row in as_member.data["data"]
        ]
        assert len(as_member.data["data"]) == 1

    def test_a_user_sees_categories_somebody_else_deleted(self, member_client, admin, root_folder):
        from apps.folders.services import FolderService

        FolderService().delete(admin, root_folder)

        response = member_client.get("/api/v1/categories/deleted/")
        assert response.status_code == 200
        assert len(response.data["data"]) == 1


class TestShareLinks:
    def test_anyone_can_revoke_a_link_somebody_else_made(self, member_client, admin, sample_file):
        from apps.files.services import ShareService

        link = ShareService().create_for_file(admin, sample_file)

        assert member_client.delete(f"/api/v1/share-links/{link.pk}/").status_code == 200


class TestAccountCreationStaysAdminOnly:
    """The restriction has to hold on the server, or it holds nowhere.

    If a User could create accounts, they could create themselves an Admin and
    the dashboard rule would be decoration.
    """

    def test_there_is_no_api_route_that_creates_users(self, member_client):
        """Only the SETUP_KEY endpoint and the dashboard create accounts."""
        assert member_client.post("/api/v1/users/", {}, format="json").status_code == 404

    def test_a_user_cannot_promote_themselves_through_the_profile(self, member_client, member):
        member_client.patch("/api/v1/auth/me/", {"role": "admin"}, format="json")

        member.refresh_from_db()
        assert member.role == "user"


class TestDisabledAccount:
    def test_disabling_takes_effect_on_the_next_request(self, member_client, member):
        """Even though the access token is still cryptographically valid."""
        assert member_client.get("/api/v1/auth/me/").status_code == 200

        member.is_active = False
        member.save(update_fields=["is_active"])
        from apps.accounts.authentication import invalidate_auth_cache

        invalidate_auth_cache(member.pk)

        assert member_client.get("/api/v1/auth/me/").status_code == 401
