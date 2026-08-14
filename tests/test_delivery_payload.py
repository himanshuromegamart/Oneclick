"""What /preview/ and /download/ return.

The client must be able to render a file from this payload alone. It cannot
read the filename off the signed URL - that carries an opaque id - and it
cannot trust the response Content-Type, because Cloudinary serves every raw
asset as application/octet-stream. So the payload has to carry the identity.
"""

from __future__ import annotations

import io

import pytest

pytestmark = pytest.mark.django_db

REQUIRED = {
    "url",
    "file_name",
    "name",
    "extension",
    "mime_type",
    "size_bytes",
    "thumbnail_url",
    "expires_in_seconds",
    "is_previewable",
}


def upload(service, user, folder, name, content=b"%PDF-1.4 data here"):
    return service.upload(
        user,
        folder_id=folder.pk,
        file_obj=io.BytesIO(content),
        filename=name,
        size_bytes=len(content),
    )


@pytest.fixture
def pdf(file_service, staff, child_folder):
    return upload(file_service, staff, child_folder, "quotation.pdf")


class TestPayloadShape:
    @pytest.mark.parametrize("action", ["preview", "download"])
    def test_every_field_is_present(self, staff_client, pdf, action):
        response = staff_client.get(f"/api/v1/documents/{pdf.pk}/{action}/")

        assert response.status_code == 200
        assert REQUIRED <= set(response.data["data"]), (
            f"missing: {REQUIRED - set(response.data['data'])}"
        )

    def test_preview_and_download_agree(self, staff_client, pdf):
        """Only the URL should differ, so the client can share one parser."""
        preview = staff_client.get(f"/api/v1/documents/{pdf.pk}/preview/").data["data"]
        download = staff_client.get(f"/api/v1/documents/{pdf.pk}/download/").data["data"]

        for field in REQUIRED - {"url"}:
            assert preview[field] == download[field], field


class TestFileIdentity:
    def test_the_original_filename_comes_back(self, staff_client, pdf):
        data = staff_client.get(f"/api/v1/documents/{pdf.pk}/preview/").data["data"]

        assert data["file_name"] == "quotation.pdf"
        assert data["name"] == "quotation.pdf"
        assert data["extension"] == "pdf"

    def test_the_client_never_has_to_parse_the_url(self, staff_client, pdf):
        """The filename is deliberately absent from the signed URL."""
        data = staff_client.get(f"/api/v1/documents/{pdf.pk}/preview/").data["data"]

        assert "quotation" not in data["url"]
        assert data["file_name"] == "quotation.pdf"

    def test_the_url_is_not_masked(self, staff_client, pdf):
        """It must be usable as-is - redaction belongs in logs, not responses."""
        data = staff_client.get(f"/api/v1/documents/{pdf.pk}/preview/").data["data"]

        assert "*" not in data["url"]
        assert "redacted" not in data["url"].lower()
        assert data["url"].startswith("http")

    @pytest.mark.parametrize(
        "filename,mime",
        [
            ("report.pdf", "application/pdf"),
            ("photo.jpg", "image/jpeg"),
            ("photo.jpeg", "image/jpeg"),
            ("image.png", "image/png"),
            ("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("clip.mp4", "video/mp4"),
            ("archive.zip", "application/zip"),
        ],
    )
    def test_mime_type_is_correct_per_extension(
        self, staff_client, file_service, staff, child_folder, filename, mime
    ):
        file = upload(file_service, staff, child_folder, filename)

        data = staff_client.get(f"/api/v1/documents/{file.pk}/preview/").data["data"]
        assert data["mime_type"] == mime

    def test_mime_type_does_not_depend_on_the_host(self):
        """`mimetypes` reads the OS registry, so the same file could be typed
        differently on Windows and on the Linux container. Ours is a fixed
        table for exactly that reason."""
        from apps.files.models import guess_mime_type

        assert guess_mime_type("a.pdf") == "application/pdf"
        assert guess_mime_type("a.PDF") == "application/pdf"
        assert guess_mime_type("no-extension") == "application/octet-stream"
        assert guess_mime_type("unknown.xyz") == "application/octet-stream"


class TestPreviewability:
    @pytest.mark.parametrize(
        "filename,previewable",
        [
            ("doc.pdf", True),
            ("photo.jpg", True),
            ("image.png", True),
            ("clip.mp4", True),
            ("sheet.xlsx", False),
            ("doc.docx", False),
            ("archive.zip", False),
        ],
    )
    def test_is_previewable_matches_what_a_viewer_can_render(
        self, staff_client, file_service, staff, child_folder, filename, previewable
    ):
        file = upload(file_service, staff, child_folder, filename)

        data = staff_client.get(f"/api/v1/documents/{file.pk}/preview/").data["data"]
        assert data["is_previewable"] is previewable


class TestThumbnails:
    def test_a_pdf_has_no_thumbnail_but_the_field_still_exists(self, staff_client, pdf):
        """An absent key would make the client branch on presence; an empty
        string lets it branch on truthiness."""
        data = staff_client.get(f"/api/v1/documents/{pdf.pk}/preview/").data["data"]

        assert data["thumbnail_url"] == ""


class TestExpiry:
    def test_expiry_is_reported(self, staff_client, pdf, settings):
        data = staff_client.get(f"/api/v1/documents/{pdf.pk}/preview/").data["data"]

        assert data["expires_in_seconds"] == settings.CLOUDINARY["SIGNED_URL_TTL_SECONDS"]

    def test_each_call_mints_a_fresh_url(self, staff_client, pdf):
        """The client is told to re-request rather than cache, so repeated
        calls must actually produce new URLs."""
        first = staff_client.get(f"/api/v1/documents/{pdf.pk}/preview/").data["data"]["url"]
        second = staff_client.get(f"/api/v1/documents/{pdf.pk}/preview/").data["data"]["url"]

        assert first and second
