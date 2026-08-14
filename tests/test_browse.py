"""The combined browse listing.

One call returns a folder's subfolders and files together, which is what a
file-browser screen actually needs.
"""

from __future__ import annotations

import io

import pytest

pytestmark = pytest.mark.django_db


def make_folder(user, name, parent=None):
    from apps.folders.repositories import FolderRepository

    return FolderRepository().create_folder(name=name, parent=parent, created_by=user)


def make_file(file_service, user, folder, name):
    return file_service.upload(
        user,
        folder_id=folder.pk,
        file_obj=io.BytesIO(b"contents"),
        filename=name,
        size_bytes=8,
        content_type="application/pdf",
    )


@pytest.fixture
def mixed_folder(admin, member, file_service):
    """A folder holding 2 subfolders and 3 files."""
    parent = make_folder(admin, "Water ATM")
    make_folder(admin, "500 LPH", parent)
    make_folder(admin, "1000 LPH", parent)
    make_file(file_service, member, parent, "brochure.pdf")
    make_file(file_service, member, parent, "price-list.pdf")
    make_file(file_service, member, parent, "warranty.pdf")
    return parent


class TestBrowse:
    def test_top_level_lists_categories(self, member_client, root_folder):
        response = member_client.get("/api/v1/browse/")

        assert response.status_code == 200
        assert [item["name"] for item in response.data["data"]] == ["Quotation"]
        assert response.data["meta"]["folder"] is None
        assert response.data["meta"]["breadcrumb"] == []

    def test_returns_folders_and_files_in_one_call(self, member_client, mixed_folder):
        response = member_client.get(f"/api/v1/browse/?parent_id={mixed_folder.pk}")

        assert response.status_code == 200
        assert len(response.data["data"]) == 5
        assert response.data["meta"]["counts"] == {"folders": 2, "files": 3, "total": 5}

    def test_folders_sort_before_files(self, member_client, mixed_folder):
        """A file browser shows folders first - and a stable order means
        pagination cannot interleave the two types."""
        types = [
            item["type"]
            for item in member_client.get(f"/api/v1/browse/?parent_id={mixed_folder.pk}").data[
                "data"
            ]
        ]

        assert types == ["folder", "folder", "file", "file", "file"]

    def test_each_item_says_what_it_is(self, member_client, mixed_folder):
        items = member_client.get(f"/api/v1/browse/?parent_id={mixed_folder.pk}").data["data"]

        folder = next(item for item in items if item["type"] == "folder")
        file = next(item for item in items if item["type"] == "file")

        # Type-specific fields the app needs to render each row.
        assert "subfolder_count" in folder
        assert "size_display" in file
        assert "extension" in file

    def test_breadcrumb_comes_with_the_listing(self, member_client, admin):
        parent = make_folder(admin, "Quotation")
        child = make_folder(admin, "Water ATM", parent)
        grandchild = make_folder(admin, "500 LPH", child)

        meta = member_client.get(f"/api/v1/browse/?parent_id={grandchild.pk}").data["meta"]

        assert [crumb["name"] for crumb in meta["breadcrumb"]] == [
            "Quotation",
            "Water ATM",
            "500 LPH",
        ]
        assert meta["folder"]["name"] == "500 LPH"

    def test_can_show_only_folders(self, member_client, mixed_folder):
        items = member_client.get(f"/api/v1/browse/?parent_id={mixed_folder.pk}&type=folder").data[
            "data"
        ]

        assert len(items) == 2
        assert all(item["type"] == "folder" for item in items)

    def test_can_show_only_files(self, member_client, mixed_folder):
        items = member_client.get(f"/api/v1/browse/?parent_id={mixed_folder.pk}&type=file").data[
            "data"
        ]

        assert len(items) == 3
        assert all(item["type"] == "file" for item in items)

    def test_empty_folder_returns_an_empty_list(self, member_client, child_folder):
        response = member_client.get(f"/api/v1/browse/?parent_id={child_folder.pk}")

        assert response.status_code == 200
        assert response.data["data"] == []
        assert response.data["meta"]["counts"]["total"] == 0

    def test_unknown_folder_is_a_404(self, member_client):
        import uuid

        assert member_client.get(f"/api/v1/browse/?parent_id={uuid.uuid4()}").status_code == 404

    def test_needs_a_login(self, api_client):
        assert api_client.get("/api/v1/browse/").status_code == 401

    def test_viewer_can_browse(self, member_client, mixed_folder):
        assert member_client.get(f"/api/v1/browse/?parent_id={mixed_folder.pk}").status_code == 200


class TestBrowsePagination:
    def test_paginates_across_both_types(self, member_client, mixed_folder):
        """Page 1 is the two folders plus the first file; page 2 the rest.

        The slice has to span two tables without dropping or repeating a row.
        """
        page1 = member_client.get(f"/api/v1/browse/?parent_id={mixed_folder.pk}&page_size=3").data
        page2 = member_client.get(
            f"/api/v1/browse/?parent_id={mixed_folder.pk}&page_size=3&page=2"
        ).data

        assert [item["type"] for item in page1["data"]] == ["folder", "folder", "file"]
        assert [item["type"] for item in page2["data"]] == ["file", "file"]

        # No row appears twice and none is lost.
        ids = [item["id"] for item in page1["data"] + page2["data"]]
        assert len(ids) == len(set(ids)) == 5

    def test_pagination_metadata(self, member_client, mixed_folder):
        meta = member_client.get(f"/api/v1/browse/?parent_id={mixed_folder.pk}&page_size=3").data[
            "meta"
        ]["pagination"]

        assert meta["count"] == 5
        assert meta["total_pages"] == 2
        assert meta["has_next"] is True
        assert meta["has_previous"] is False

    def test_page_beyond_the_end_is_empty_not_an_error(self, member_client, mixed_folder):
        response = member_client.get(f"/api/v1/browse/?parent_id={mixed_folder.pk}&page=99")

        assert response.status_code == 200
        assert response.data["data"] == []

    def test_page_size_is_capped(self, member_client, mixed_folder):
        """A client cannot ask for the entire table in one response."""
        meta = member_client.get(
            f"/api/v1/browse/?parent_id={mixed_folder.pk}&page_size=99999"
        ).data["meta"]["pagination"]

        assert meta["page_size"] == 200

    def test_garbage_page_values_do_not_crash(self, member_client, mixed_folder):
        response = member_client.get(
            f"/api/v1/browse/?parent_id={mixed_folder.pk}&page=abc&page_size=xyz"
        )
        assert response.status_code == 200


class TestCreateAndUploadFlow:
    """The full journey: make a category, nest folders in it, upload a file.

    This is the path the app takes, proving the endpoints line up.
    """

    def test_category_to_subcategory_to_folder_to_file(self, member_client, child_folder):
        from django.core.files.uploadedfile import SimpleUploadedFile

        # 1. A top-level category.
        response = member_client.post(
            "/api/v1/categories/", {"name": "Certificates"}, format="json"
        )
        assert response.status_code == 201
        category_id = response.data["data"]["id"]

        # 2. A subcategory inside it - same endpoint, just a parent_id.
        response = member_client.post(
            "/api/v1/categories/", {"name": "ISO", "parent_id": category_id}, format="json"
        )
        assert response.status_code == 201
        sub_id = response.data["data"]["id"]

        # 3. A folder inside the subcategory - again the same endpoint.
        response = member_client.post(
            "/api/v1/categories/", {"name": "2025", "parent_id": sub_id}, format="json"
        )
        assert response.status_code == 201
        assert response.data["data"]["depth"] == 2
        folder_id = response.data["data"]["id"]

        # 4. Upload a document into it.
        response = member_client.post(
            "/api/v1/documents/",
            {
                "folder_id": folder_id,
                "file": SimpleUploadedFile(
                    "iso-9001.pdf", b"%PDF-1.4 certificate", content_type="application/pdf"
                ),
            },
            format="multipart",
        )
        assert response.status_code == 201

        # 5. Browsing that folder shows the document.
        response = member_client.get(f"/api/v1/browse/?parent_id={folder_id}")
        assert [item["name"] for item in response.data["data"]] == ["iso-9001.pdf"]
        assert [crumb["name"] for crumb in response.data["meta"]["breadcrumb"]] == [
            "Certificates",
            "ISO",
            "2025",
        ]
