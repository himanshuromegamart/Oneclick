"""File metadata.

Postgres never stores file bytes - only the Cloudinary ``public_id`` and enough
metadata to list, search, preview and re-download an asset without calling the
provider.  Listing a folder therefore costs one indexed query and no network
round trips.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.db.models.functions import Lower, Upper
from django.utils import timezone

from apps.core.managers import SoftDeleteQuerySet
from apps.core.models import BaseModel


class FileCategory(models.TextChoices):
    """Coarse bucket driving the icon and preview mode on the client."""

    DOCUMENT = "document", "Document"
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    SPREADSHEET = "spreadsheet", "Spreadsheet"
    PRESENTATION = "presentation", "Presentation"
    ARCHIVE = "archive", "Archive"
    OTHER = "other", "Other"


_CATEGORY_BY_EXTENSION = {
    **{e: FileCategory.IMAGE for e in ("jpg", "jpeg", "png", "webp", "gif", "heic", "bmp", "tiff")},
    **{e: FileCategory.VIDEO for e in ("mp4", "mov", "avi", "mkv", "webm")},
    **{e: FileCategory.SPREADSHEET for e in ("xls", "xlsx", "csv")},
    **{e: FileCategory.PRESENTATION for e in ("ppt", "pptx")},
    **{e: FileCategory.ARCHIVE for e in ("zip", "rar", "7z")},
    **{e: FileCategory.DOCUMENT for e in ("pdf", "doc", "docx", "txt", "rtf")},
}


def category_for_extension(extension: str) -> str:
    return _CATEGORY_BY_EXTENSION.get(extension.lower().lstrip("."), FileCategory.OTHER)


class FileQuerySet(SoftDeleteQuerySet):
    def in_folder(self, folder):
        return self.filter(folder=folder)

    def of_category(self, category: str):
        return self.filter(category=category)

    def with_tag(self, tag: str):
        return self.filter(tags__contains=[tag.lower()])


class FileManager(models.Manager.from_queryset(FileQuerySet)):  # type: ignore[misc]
    def get_queryset(self) -> FileQuerySet:
        return super().get_queryset().filter(is_deleted=False)


class AllFilesManager(models.Manager.from_queryset(FileQuerySet)):  # type: ignore[misc]
    """Includes recycled files."""


class FileAsset(BaseModel):
    """One stored file. The leaf of the composite tree."""

    folder = models.ForeignKey(
        "folders.Folder", on_delete=models.CASCADE, related_name="files", db_index=True
    )

    name = models.CharField(max_length=255, db_index=True)
    description = models.TextField(blank=True, default="")
    extension = models.CharField(max_length=20, db_index=True)
    mime_type = models.CharField(max_length=120, blank=True, default="")
    category = models.CharField(
        max_length=20, choices=FileCategory.choices, default=FileCategory.OTHER, db_index=True
    )
    size_bytes = models.BigIntegerField(default=0)

    # -- storage ----------------------------------------------------------
    storage_provider = models.CharField(max_length=20, default="cloudinary")
    public_id = models.CharField(max_length=512, db_index=True)
    resource_type = models.CharField(max_length=20, default="raw")
    #: Unsigned base URL kept for reference. Delivery always uses a freshly
    #: signed URL, because this one may not be usable on its own.
    secure_url = models.URLField(max_length=1024, blank=True, default="")
    thumbnail_url = models.URLField(max_length=1024, blank=True, default="")

    #: SHA-256 of the bytes. Enables duplicate detection within a folder and
    #: lets a client verify an interrupted download.
    checksum = models.CharField(max_length=64, blank=True, default="", db_index=True)

    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    tags = ArrayField(
        models.CharField(max_length=50),
        default=list,
        blank=True,
        help_text="Lowercase, de-duplicated free-text labels.",
    )

    version_number = models.PositiveIntegerField(default=1)
    download_count = models.PositiveIntegerField(default=0)
    last_accessed_at = models.DateTimeField(null=True, blank=True)

    #: Maintained by a trigger-free service call after every write; see
    #: apps/search/services.py.
    search_vector = SearchVectorField(null=True, editable=False)

    objects = FileManager()
    all_objects = AllFilesManager()

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "folder",
                condition=models.Q(is_deleted=False),
                name="uniq_file_name_per_folder",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gte=0), name="file_size_non_negative"
            ),
        ]
        indexes = [
            models.Index(fields=["folder", "is_deleted", "-created_at"]),
            models.Index(fields=["category", "is_deleted"]),
            models.Index(fields=["extension", "is_deleted"]),
            models.Index(fields=["created_by", "-created_at"]),
            models.Index(fields=["is_deleted", "-deleted_at"]),
            models.Index(fields=["-download_count"]),
            # Full-text search over name + description + tags.
            GinIndex(fields=["search_vector"], name="file_search_vector_idx"),
            # Tag containment (`tags @> ARRAY['iso']`).
            GinIndex(fields=["tags"], name="file_tags_idx"),
            # Trigram index backing fuzzy (typo-tolerant) name matching.
            # Without it every misspelled search is a sequential scan, which
            # is exactly the query that must stay fast at millions of rows.
            GinIndex(
                OpClass(Upper("name"), name="gin_trgm_ops"),
                name="file_name_trigram_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def size_display(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    @property
    def is_previewable(self) -> bool:
        return self.category in {FileCategory.IMAGE, FileCategory.VIDEO} or self.extension == "pdf"


class FileVersion(BaseModel):
    """A superseded revision of a file.

    Re-uploading keeps the old bytes rather than overwriting them, so an
    accidental replacement is recoverable.  Old versions are pruned to
    ``MAX_FILE_VERSIONS`` so storage cost stays bounded.
    """

    file = models.ForeignKey(FileAsset, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()

    public_id = models.CharField(max_length=512)
    resource_type = models.CharField(max_length=20, default="raw")
    secure_url = models.URLField(max_length=1024, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("-version_number",)
        constraints = [
            models.UniqueConstraint(
                fields=["file", "version_number"], name="uniq_file_version_number"
            )
        ]
        indexes = [models.Index(fields=["file", "-version_number"])]

    def __str__(self) -> str:
        return f"{self.file.name} v{self.version_number}"


class FileFavorite(BaseModel):
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="file_favorites"
    )
    file = models.ForeignKey(FileAsset, on_delete=models.CASCADE, related_name="favorited_by")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "file"], name="uniq_user_file_favorite")
        ]
        indexes = [models.Index(fields=["user", "-created_at"])]


class RecentFile(models.Model):
    """Per-user "recently opened" list.

    Not a :class:`BaseModel`: this is a small, high-churn table where a row is
    overwritten rather than versioned, and soft deletion would only make it
    grow.  One row per (user, file), touched on every access.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="recent_files")
    file = models.ForeignKey(FileAsset, on_delete=models.CASCADE, related_name="recent_entries")
    accessed_at = models.DateTimeField(default=timezone.now, db_index=True)
    access_count = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("-accessed_at",)
        constraints = [
            models.UniqueConstraint(fields=["user", "file"], name="uniq_user_recent_file")
        ]
        indexes = [models.Index(fields=["user", "-accessed_at"])]

    def __str__(self) -> str:
        return f"{self.file_id} @ {self.accessed_at:%Y-%m-%d %H:%M}"


def _generate_share_token() -> str:
    # 32 bytes of entropy: the token is the only thing protecting the file, so
    # it must be infeasible to guess even at high request volume.
    return secrets.token_urlsafe(32)


class ShareLink(BaseModel):
    """A revocable, expiring external link to a file.

    Sharing has to work for recipients who have no account - a customer sent a
    catalogue or a price list - so the link itself carries the authority. Every
    control here exists to bound that authority: it expires, it can cap
    downloads, and it can be revoked at any time.
    """

    file = models.ForeignKey(FileAsset, on_delete=models.CASCADE, related_name="share_links")

    token = models.CharField(
        max_length=64, unique=True, default=_generate_share_token, db_index=True
    )
    expires_at = models.DateTimeField(db_index=True)
    max_downloads = models.PositiveIntegerField(
        null=True, blank=True, help_text="Null means unlimited until expiry."
    )
    download_count = models.PositiveIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)

    recipient_note = models.CharField(max_length=255, blank=True, default="")
    last_accessed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["expires_at", "revoked_at"]),
            models.Index(fields=["created_by", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Share {self.token[:8]}…"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_exhausted(self) -> bool:
        return self.max_downloads is not None and self.download_count >= self.max_downloads

    @property
    def is_usable(self) -> bool:
        return not (self.is_expired or self.is_revoked or self.is_exhausted or self.is_deleted)

    def register_access(self) -> None:
        self.download_count += 1
        self.last_accessed_at = timezone.now()
        self.save(update_fields=["download_count", "last_accessed_at", "updated_at"])

    def revoke(self) -> None:
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at", "updated_at"])

    @staticmethod
    def default_expiry(hours: int = 168):
        return timezone.now() + timedelta(hours=hours)
