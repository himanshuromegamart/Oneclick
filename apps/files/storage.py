"""Media storage.

Every byte of user content lives in Cloudinary; Postgres stores only metadata
and the Cloudinary ``public_id``.  That keeps the database small, gives us
CDN delivery and on-the-fly image transformation for free, and means an
application server holds no state.

The provider sits behind :class:`StorageBackend` for the same reason the SMS
gateway does - so tests never touch the network, and so a future migration to
S3 changes one class rather than every call site.

Two upload paths are supported:

**Server-side upload** (``upload``)
    The file passes through Django.  Simple, and lets the backend validate the
    bytes, but it ties up a worker for the duration of the transfer.

**Signed direct upload** (``build_upload_signature``)
    The client uploads straight to Cloudinary using a short-lived signature
    minted here.  This is what the mobile app should use for anything large:
    the phone talks to the CDN edge, and the backend only records the result.
    The API secret never leaves the server.
"""

from __future__ import annotations

import abc
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from django.conf import settings
from django.utils.module_loading import import_string

from apps.core.exceptions import ErrorCode, ExternalServiceError

logger = logging.getLogger(__name__)

#: Cloudinary splits assets by resource type and the delivery URL differs, so
#: the type has to be recorded at upload time rather than inferred later.
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "heic", "bmp", "tiff"}
VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm"}


def resource_type_for(extension: str) -> str:
    ext = extension.lower().lstrip(".")
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return "raw"


#: Characters Cloudinary reads as URL or transformation syntax.
_ATTACHMENT_UNSAFE = re.compile(r"[/\\,]")


def _attachment_flag_name(filename: str) -> str:
    """Make a filename safe to embed in ``fl_attachment:``.

    **The extension must be removed.** Cloudinary parses a trailing ``.ext`` in
    a transformation segment as a format specifier, so
    ``fl_attachment:report.pdf`` is rejected outright with
    ``400 Invalid flag in transformation: pdf`` and the download returns
    nothing at all.

    Putting the extension in the ``public_id`` instead does not help either -
    that makes Cloudinary sign a different string and delivery fails with
    ``401 deny or ACL failure``.

    So the delivered file arrives without an extension. Clients should name the
    saved file from the ``name`` field in the API response, which does carry
    it; the flag exists only to force a download rather than an inline view.
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    stem = _ATTACHMENT_UNSAFE.sub("-", stem).strip()
    return stem or "download"


@dataclass(slots=True)
class StoredObject:
    """The result of a successful upload."""

    public_id: str
    secure_url: str
    resource_type: str
    size_bytes: int
    format: str = ""
    thumbnail_url: str = ""
    checksum: str = ""
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UploadSignature:
    """Everything a client needs to upload directly to the provider."""

    signature: str
    timestamp: int
    api_key: str
    cloud_name: str
    folder: str
    public_id: str
    resource_type: str
    upload_url: str
    expires_in_seconds: int


class StorageBackend(abc.ABC):
    """Interface implemented by every media provider adapter."""

    @abc.abstractmethod
    def upload(
        self,
        file_obj: BinaryIO,
        *,
        filename: str,
        folder_path: str,
        resource_type: str = "auto",
        **options: Any,
    ) -> StoredObject: ...

    @abc.abstractmethod
    def delete(self, public_id: str, resource_type: str = "raw") -> bool: ...

    @abc.abstractmethod
    def signed_url(
        self,
        public_id: str,
        resource_type: str = "raw",
        *,
        attachment_name: str = "",
        ttl_seconds: int | None = None,
    ) -> str: ...

    @abc.abstractmethod
    def build_upload_signature(
        self, *, folder_path: str, resource_type: str = "auto", public_id: str = ""
    ) -> UploadSignature: ...

    def thumbnail_url(self, public_id: str, resource_type: str, width: int = 400) -> str:
        return ""


class CloudinaryStorageBackend(StorageBackend):
    """Production adapter."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or settings.CLOUDINARY
        self._configured = False

    def _configure(self) -> None:
        """Configure the SDK lazily.

        Import and configuration happen on first use rather than at module
        import, so a missing credential fails on the first upload with a clear
        error instead of preventing the whole process from booting.
        """
        if self._configured:
            return
        import cloudinary

        if not self.config.get("CLOUD_NAME"):
            raise ExternalServiceError(
                detail="Media storage is not configured.", code=ErrorCode.STORAGE_ERROR
            )
        cloudinary.config(
            cloud_name=self.config["CLOUD_NAME"],
            api_key=self.config["API_KEY"],
            api_secret=self.config["API_SECRET"],
            secure=True,
        )
        self._configured = True

    def upload(
        self,
        file_obj: BinaryIO,
        *,
        filename: str,
        folder_path: str,
        resource_type: str = "auto",
        **options: Any,
    ) -> StoredObject:
        self._configure()
        import cloudinary.uploader

        public_id = f"{uuid.uuid4().hex}"
        full_folder = f"{self.config['UPLOAD_FOLDER']}/{folder_path}".strip("/")

        try:
            result = cloudinary.uploader.upload(
                file_obj,
                public_id=public_id,
                folder=full_folder,
                resource_type=resource_type,
                # Cloudinary would otherwise append a random suffix on
                # collision, silently producing a public_id we did not record.
                unique_filename=False,
                overwrite=False,
                # Assets must not be publicly enumerable: delivery always goes
                # through a signed URL minted by this backend.
                type="authenticated",
                use_filename=False,
                **options,
            )
        except Exception as exc:
            logger.error("cloudinary_upload_failed", exc_info=exc, extra={"filename": filename})
            raise ExternalServiceError(
                detail="The file could not be uploaded. Please try again.",
                code=ErrorCode.UPLOAD_FAILED,
            ) from exc

        actual_type = result.get("resource_type", resource_type)
        return StoredObject(
            public_id=result["public_id"],
            secure_url=result["secure_url"],
            resource_type=actual_type,
            size_bytes=result.get("bytes", 0),
            format=result.get("format", ""),
            thumbnail_url=self.thumbnail_url(result["public_id"], actual_type),
            checksum=result.get("etag", ""),
            width=result.get("width"),
            height=result.get("height"),
            duration_seconds=result.get("duration"),
            raw={k: v for k, v in result.items() if k in {"version", "created_at", "etag"}},
        )

    def delete(self, public_id: str, resource_type: str = "raw") -> bool:
        self._configure()
        import cloudinary.uploader

        try:
            result = cloudinary.uploader.destroy(
                public_id, resource_type=resource_type, type="authenticated", invalidate=True
            )
        except Exception as exc:
            logger.error("cloudinary_delete_failed", exc_info=exc, extra={"public_id": public_id})
            return False
        return result.get("result") in {"ok", "not found"}

    def signed_url(
        self,
        public_id: str,
        resource_type: str = "raw",
        *,
        attachment_name: str = "",
        ttl_seconds: int | None = None,
    ) -> str:
        """Mint a time-limited delivery URL.

        Expiring URLs mean a link copied out of the app stops working, so
        sharing a document is an explicit, revocable act rather than a side
        effect of viewing it.
        """
        self._configure()
        import cloudinary.utils

        ttl = ttl_seconds or self.config["SIGNED_URL_TTL_SECONDS"]
        expires_at = int(time.time()) + ttl

        if resource_type == "raw":
            # Raw assets - PDF, Word, Excel, archives - must go through the
            # download API, not the CDN URL builder.
            #
            # Cloudinary keeps the file extension inside a raw public_id
            # ("…/abc123.pdf"), but cloudinary_url() treats a trailing ".ext"
            # as a format and strips it before signing. The CDN then verifies
            # the signature over the full id including ".pdf", the two never
            # match, and delivery fails with:
            #
            #     HTTP 401  x-cld-error: deny or ACL failure
            #
            # The error names access control, which sends you looking at ACLs
            # and API keys; the signature is the actual cause. No combination
            # of cloudinary_url arguments fixes it - passing the extension as
            # `format` produces a byte-identical signature.
            #
            # private_download_url signs the whole public_id and is
            # Cloudinary's documented call for authenticated assets.
            return cloudinary.utils.private_download_url(
                public_id,
                "",  # the extension is already part of public_id for raw
                resource_type="raw",
                type="authenticated",
                attachment=bool(attachment_name),
                expires_at=expires_at,
            )

        # Images and video keep their extension in a separate `format` field,
        # so their public_ids carry no dot and the CDN builder works - which is
        # what we want, since it is also what supports thumbnails.
        options: dict[str, Any] = {
            "resource_type": resource_type,
            "type": "authenticated",
            "sign_url": True,
            "expires_at": expires_at,
            "secure": True,
        }
        if attachment_name:
            options["flags"] = f"attachment:{_attachment_flag_name(attachment_name)}"

        url, _ = cloudinary.utils.cloudinary_url(public_id, **options)
        return url

    def build_upload_signature(
        self, *, folder_path: str, resource_type: str = "auto", public_id: str = ""
    ) -> UploadSignature:
        self._configure()
        import cloudinary.utils

        timestamp = int(time.time())
        full_folder = f"{self.config['UPLOAD_FOLDER']}/{folder_path}".strip("/")
        target_public_id = public_id or uuid.uuid4().hex

        params = {
            "timestamp": timestamp,
            "folder": full_folder,
            "public_id": target_public_id,
            "type": "authenticated",
        }
        signature = cloudinary.utils.api_sign_request(params, self.config["API_SECRET"])

        return UploadSignature(
            signature=signature,
            timestamp=timestamp,
            api_key=self.config["API_KEY"],
            cloud_name=self.config["CLOUD_NAME"],
            folder=full_folder,
            public_id=target_public_id,
            resource_type=resource_type,
            upload_url=(
                f"https://api.cloudinary.com/v1_1/{self.config['CLOUD_NAME']}"
                f"/{resource_type}/upload"
            ),
            expires_in_seconds=self.config["UPLOAD_SIGNATURE_TTL_SECONDS"],
        )

    def thumbnail_url(self, public_id: str, resource_type: str, width: int = 400) -> str:
        """A small, CDN-cached preview.

        Only images and videos get one; a PDF thumbnail would need a rendering
        pass, so the client shows a type icon instead.
        """
        if resource_type not in {"image", "video"}:
            return ""
        self._configure()
        import cloudinary.utils

        url, _ = cloudinary.utils.cloudinary_url(
            public_id,
            resource_type=resource_type,
            type="authenticated",
            sign_url=True,
            secure=True,
            format="jpg" if resource_type == "video" else None,
            transformation=[
                {"width": width, "crop": "limit", "quality": "auto", "fetch_format": "auto"}
            ],
            expires_at=int(time.time()) + self.config["SIGNED_URL_TTL_SECONDS"],
        )
        return url


class InMemoryStorageBackend(StorageBackend):
    """Test double. Keeps uploaded bytes in a class-level dictionary."""

    store: dict[str, bytes] = {}

    @classmethod
    def clear(cls) -> None:
        cls.store.clear()

    def upload(
        self,
        file_obj: BinaryIO,
        *,
        filename: str,
        folder_path: str,
        resource_type: str = "auto",
        **options: Any,
    ) -> StoredObject:
        data = file_obj.read()
        public_id = f"{folder_path}/{uuid.uuid4().hex}".strip("/")
        type(self).store[public_id] = data
        return StoredObject(
            public_id=public_id,
            secure_url=f"https://test.local/{public_id}",
            resource_type=resource_type if resource_type != "auto" else "raw",
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            thumbnail_url="",
        )

    def delete(self, public_id: str, resource_type: str = "raw") -> bool:
        type(self).store.pop(public_id, None)
        return True

    def signed_url(
        self,
        public_id: str,
        resource_type: str = "raw",
        *,
        attachment_name: str = "",
        ttl_seconds: int | None = None,
    ) -> str:
        return f"https://test.local/{public_id}?signed=1"

    def build_upload_signature(
        self, *, folder_path: str, resource_type: str = "auto", public_id: str = ""
    ) -> UploadSignature:
        return UploadSignature(
            signature="test-signature",
            timestamp=int(time.time()),
            api_key="test-key",
            cloud_name="test-cloud",
            folder=folder_path,
            public_id=public_id or uuid.uuid4().hex,
            resource_type=resource_type,
            upload_url="https://test.local/upload",
            expires_in_seconds=600,
        )


def get_storage_backend() -> StorageBackend:
    """Factory. ``STORAGE_BACKEND`` overrides the default in tests."""
    path = getattr(settings, "STORAGE_BACKEND", "apps.files.storage.CloudinaryStorageBackend")
    return import_string(path)()


def compute_checksum(file_obj: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
    """SHA-256 of a file, streamed so a 200 MB video never lands in memory."""
    digest = hashlib.sha256()
    position = file_obj.tell()
    file_obj.seek(0)
    for chunk in iter(lambda: file_obj.read(chunk_size), b""):
        digest.update(chunk)
    file_obj.seek(position)
    return digest.hexdigest()
