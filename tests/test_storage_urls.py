"""How delivery URLs are built for each Cloudinary resource type.

Regression cover for a bug that broke every PDF download in production: raw
assets were served through cloudinary_url(), which strips a trailing ".ext"
before signing while the CDN verifies the signature including it. Every URL
came back 401 "deny or ACL failure" - an error that points at access control
rather than at the signature, which is why it was not obvious.

These tests assert the *choice of method* rather than the URL text, so they
stay meaningful without reaching Cloudinary.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.files.storage import CloudinaryStorageBackend

CONFIG = {
    "CLOUD_NAME": "test-cloud",
    "API_KEY": "test-key",
    "API_SECRET": "test-secret",
    "UPLOAD_FOLDER": "test",
    "SIGNED_URL_TTL_SECONDS": 900,
    "UPLOAD_SIGNATURE_TTL_SECONDS": 600,
}

RAW_ID = "test/folder/abc123def456.pdf"
IMAGE_ID = "test/folder/abc123def456"


@pytest.fixture
def backend():
    storage = CloudinaryStorageBackend(config=CONFIG)
    storage._configured = True  # skip the SDK configure call
    return storage


class TestRawUsesTheDownloadApi:
    """Raw assets must never go through the CDN URL builder."""

    def test_raw_calls_private_download_url(self, backend):
        with patch("cloudinary.utils.private_download_url", return_value="ok") as private:
            with patch("cloudinary.utils.cloudinary_url") as cdn:
                backend.signed_url(RAW_ID, "raw")

        assert private.called, "raw must use private_download_url"
        assert not cdn.called, "cloudinary_url cannot sign a raw public_id correctly"

    def test_the_extension_is_not_split_off(self, backend):
        """Passing the extension separately produces the same broken signature,
        so the whole public_id must go through untouched."""
        with patch("cloudinary.utils.private_download_url", return_value="ok") as private:
            backend.signed_url(RAW_ID, "raw")

        args, kwargs = private.call_args
        assert args[0] == RAW_ID, "the full public_id, extension included"
        assert args[1] == "", "no separate format argument"
        assert kwargs["resource_type"] == "raw"
        assert kwargs["type"] == "authenticated"

    def test_an_attachment_name_sets_the_attachment_flag(self, backend):
        with patch("cloudinary.utils.private_download_url", return_value="ok") as private:
            backend.signed_url(RAW_ID, "raw", attachment_name="Quotation.pdf")

        assert private.call_args.kwargs["attachment"] is True

    def test_preview_does_not_force_a_download(self, backend):
        with patch("cloudinary.utils.private_download_url", return_value="ok") as private:
            backend.signed_url(RAW_ID, "raw")

        assert private.call_args.kwargs["attachment"] is False

    def test_the_url_expires(self, backend):
        import time

        with patch("cloudinary.utils.private_download_url", return_value="ok") as private:
            backend.signed_url(RAW_ID, "raw", ttl_seconds=300)

        expires_at = private.call_args.kwargs["expires_at"]
        assert 250 < expires_at - int(time.time()) <= 300

    def test_raw_without_an_extension_takes_the_same_path(self, backend):
        """Files uploaded from memory have no extension in the public_id.
        They must not take a different route, or only one of the two shapes
        would ever be exercised."""
        with patch("cloudinary.utils.private_download_url", return_value="ok") as private:
            backend.signed_url(IMAGE_ID, "raw")

        assert private.call_args[0][0] == IMAGE_ID


class TestImagesUseTheCdn:
    """Images keep the CDN builder - it is what supports transformations."""

    @pytest.mark.parametrize("resource_type", ["image", "video"])
    def test_media_calls_cloudinary_url(self, backend, resource_type):
        with patch("cloudinary.utils.cloudinary_url", return_value=("url", {})) as cdn:
            with patch("cloudinary.utils.private_download_url") as private:
                backend.signed_url(IMAGE_ID, resource_type)

        assert cdn.called
        assert not private.called

    def test_media_is_signed_and_authenticated(self, backend):
        with patch("cloudinary.utils.cloudinary_url", return_value=("url", {})) as cdn:
            backend.signed_url(IMAGE_ID, "image")

        kwargs = cdn.call_args.kwargs
        assert kwargs["sign_url"] is True
        assert kwargs["type"] == "authenticated"
        assert kwargs["secure"] is True

    def test_attachment_name_drops_its_extension(self, backend):
        """A dot in fl_attachment is read as a format and rejects the URL with
        400 'Invalid flag in transformation'."""
        with patch("cloudinary.utils.cloudinary_url", return_value=("url", {})) as cdn:
            backend.signed_url(IMAGE_ID, "image", attachment_name="Holiday Photo.png")

        assert cdn.call_args.kwargs["flags"] == "attachment:Holiday Photo"
