"""Who can do what.

The whole access model is three roles and one ownership rule, so these tests
are the specification: if they pass, the table in
:mod:`apps.accounts.constants` is true.
"""

from __future__ import annotations

import io

import pytest

pytestmark = pytest.mark.django_db


def upload_as(file_service, user, folder, name="doc.pdf"):
    return file_service.upload(
        user,
        folder_id=folder.pk,
        file_obj=io.BytesIO(b"file contents"),
        filename=name,
        size_bytes=13,
        content_type="application/pdf",
    )


class TestRoleFlags:
    def test_owner_can_do_everything(self, owner):
        assert owner.is_owner
        assert owner.can_contribute

    def test_staff_can_contribute_but_is_not_owner(self, staff):
        assert not staff.is_owner
        assert staff.can_contribute

    def test_viewer_is_read_only(self, viewer):
        assert not viewer.is_owner
        assert not viewer.can_contribute


class TestOwnershipRule:
    def test_staff_can_modify_their_own_file(self, staff, child_folder, file_service):
        file = upload_as(file_service, staff, child_folder)
        assert staff.can_modify(file)

    def test_staff_cannot_modify_someone_elses_file(
        self, staff, other_staff, child_folder, file_service
    ):
        file = upload_as(file_service, other_staff, child_folder)
        assert not staff.can_modify(file)

    def test_owner_can_modify_anyones_file(self, owner, staff, child_folder, file_service):
        file = upload_as(file_service, staff, child_folder)
        assert owner.can_modify(file)

    def test_viewer_cannot_modify_even_their_own(self, viewer, child_folder, file_service):
        file = upload_as(file_service, viewer, child_folder)
        assert not viewer.can_modify(file)


class TestViewerIsReadOnly:
    def test_viewer_can_browse_categories(self, viewer_client, root_folder):
        assert viewer_client.get("/api/v1/categories/").status_code == 200

    def test_viewer_can_open_a_file(self, viewer_client, sample_file):
        assert viewer_client.get(f"/api/v1/documents/{sample_file.pk}/").status_code == 200

    def test_viewer_can_download(self, viewer_client, sample_file):
        assert viewer_client.get(f"/api/v1/documents/{sample_file.pk}/download/").status_code == 200

    def test_viewer_can_search(self, viewer_client, sample_file):
        assert viewer_client.get("/api/v1/search/?q=spec").status_code == 200

    def test_viewer_cannot_create_a_category(self, viewer_client):
        assert (
            viewer_client.post("/api/v1/categories/", {"name": "Nope"}, format="json").status_code
            == 403
        )

    def test_viewer_cannot_upload(self, viewer_client, child_folder):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = viewer_client.post(
            "/api/v1/documents/",
            {
                "folder_id": str(child_folder.pk),
                "file": SimpleUploadedFile("x.pdf", b"data", content_type="application/pdf"),
            },
            format="multipart",
        )
        assert response.status_code == 403

    def test_viewer_cannot_delete(self, viewer_client, sample_file):
        assert viewer_client.delete(f"/api/v1/documents/{sample_file.pk}/").status_code == 403


class TestStaffScope:
    def test_staff_can_upload(self, staff_client, child_folder):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = staff_client.post(
            "/api/v1/documents/",
            {
                "folder_id": str(child_folder.pk),
                "file": SimpleUploadedFile("mine.pdf", b"data", content_type="application/pdf"),
            },
            format="multipart",
        )
        assert response.status_code == 201

    def test_staff_can_create_a_category(self, staff_client):
        assert (
            staff_client.post(
                "/api/v1/categories/", {"name": "Certificates"}, format="json"
            ).status_code
            == 201
        )

    def test_staff_can_delete_their_own_file(self, staff_client, sample_file):
        # sample_file was uploaded by `staff`.
        assert staff_client.delete(f"/api/v1/documents/{sample_file.pk}/").status_code == 200

    def test_staff_cannot_delete_another_users_file(
        self, staff_client, other_staff, child_folder, file_service
    ):
        theirs = upload_as(file_service, other_staff, child_folder, name="theirs.pdf")

        response = staff_client.delete(f"/api/v1/documents/{theirs.pk}/")
        assert response.status_code == 403
        assert response.data["error"]["code"] == "PERMISSION_DENIED"

    def test_staff_cannot_rename_another_users_file(
        self, staff_client, other_staff, child_folder, file_service
    ):
        theirs = upload_as(file_service, other_staff, child_folder, name="theirs.pdf")

        response = staff_client.patch(
            f"/api/v1/documents/{theirs.pk}/", {"name": "hijacked.pdf"}, format="json"
        )
        assert response.status_code == 403

    def test_staff_cannot_permanently_delete(self, staff_client, sample_file, file_service, staff):
        file_service.delete(staff, sample_file)

        response = staff_client.delete(f"/api/v1/documents/{sample_file.pk}/purge/")
        assert response.status_code == 403


class TestOwnerScope:
    def test_owner_can_delete_anyones_file(self, owner_client, sample_file):
        # sample_file belongs to `staff`.
        assert owner_client.delete(f"/api/v1/documents/{sample_file.pk}/").status_code == 200

    def test_owner_can_permanently_delete(self, owner_client, sample_file, file_service, staff):
        file_service.delete(staff, sample_file)

        response = owner_client.delete(f"/api/v1/documents/{sample_file.pk}/purge/")
        assert response.status_code == 200

    def test_owner_sees_the_whole_recycle_bin(self, owner_client, sample_file, file_service, staff):
        file_service.delete(staff, sample_file)

        response = owner_client.get("/api/v1/documents/deleted/")
        assert response.status_code == 200
        assert len(response.data["data"]) == 1

    def test_staff_only_sees_their_own_deleted_files(
        self, staff_client, other_staff, child_folder, file_service
    ):
        theirs = upload_as(file_service, other_staff, child_folder, name="theirs.pdf")
        file_service.delete(other_staff, theirs)

        response = staff_client.get("/api/v1/documents/deleted/")
        assert response.status_code == 200
        assert response.data["data"] == []


class TestDisabledAccount:
    def test_disabling_takes_effect_on_the_next_request(self, staff_client, staff):
        """Even though the access token is still cryptographically valid."""
        assert staff_client.get("/api/v1/auth/me/").status_code == 200

        staff.is_active = False
        staff.save(update_fields=["is_active"])
        from apps.accounts.authentication import invalidate_auth_cache

        invalidate_auth_cache(staff.pk)

        assert staff_client.get("/api/v1/auth/me/").status_code == 401
