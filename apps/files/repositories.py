"""File data access."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import F, QuerySet
from django.utils import timezone

from apps.core.repositories import BaseRepository
from apps.files.models import (
    FileAsset,
    FileFavorite,
    FileVersion,
    RecentFile,
    ShareLink,
)


class FileRepository(BaseRepository[FileAsset]):
    model = FileAsset
    default_select_related = ("folder", "created_by")

    def in_folder(self, folder) -> QuerySet[FileAsset]:
        return self.get_queryset().filter(folder=folder).order_by("-created_at")

    def name_taken(self, folder, name: str, exclude_id=None) -> bool:
        qs = FileAsset.objects.filter(folder=folder, name__iexact=name)
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)
        return qs.exists()

    def find_duplicate(self, folder, checksum: str) -> FileAsset | None:
        """Same bytes already in this folder?

        Used to warn on re-upload rather than to block it - two identically
        named revisions of a price list are a real workflow.
        """
        if not checksum:
            return None
        return self.get_queryset().filter(folder=folder, checksum=checksum).first()

    def deleted_items(self) -> QuerySet[FileAsset]:
        return (
            FileAsset.all_objects.filter(is_deleted=True)
            .select_related("folder", "deleted_by")
            .order_by("-deleted_at")
        )

    def purgeable(self, retention_days: int) -> QuerySet[FileAsset]:
        cutoff = timezone.now() - timedelta(days=retention_days)
        return FileAsset.all_objects.filter(is_deleted=True, deleted_at__lt=cutoff)

    def recent_for(self, user, limit: int = 50) -> QuerySet[RecentFile]:
        return (
            RecentFile.objects.filter(user=user, file__is_deleted=False)
            .select_related("file", "file__folder")
            .order_by("-accessed_at")[:limit]
        )

    def most_downloaded(self, limit: int = 20) -> QuerySet[FileAsset]:
        return self.get_queryset().filter(download_count__gt=0).order_by("-download_count")[:limit]

    def register_download(self, file: FileAsset) -> None:
        """Increment the counter without a read-modify-write race.

        ``F()`` pushes the arithmetic into SQL, so two concurrent downloads
        both count.
        """
        FileAsset.objects.filter(pk=file.pk).update(
            download_count=F("download_count") + 1, last_accessed_at=timezone.now()
        )

    def touch_recent(self, user, file: FileAsset) -> None:
        recent, created = RecentFile.objects.get_or_create(
            user=user, file=file, defaults={"accessed_at": timezone.now()}
        )
        if not created:
            RecentFile.objects.filter(pk=recent.pk).update(
                accessed_at=timezone.now(), access_count=F("access_count") + 1
            )

    def trim_recent(self, user, keep: int = 100) -> int:
        """Keep the recent list bounded - it is a convenience, not a log."""
        ids = list(
            RecentFile.objects.filter(user=user)
            .order_by("-accessed_at")
            .values_list("id", flat=True)[keep:]
        )
        if not ids:
            return 0
        deleted, _ = RecentFile.objects.filter(id__in=ids).delete()
        return deleted


class FileVersionRepository(BaseRepository[FileVersion]):
    model = FileVersion

    def for_file(self, file: FileAsset) -> QuerySet[FileVersion]:
        return self.get_queryset().filter(file=file).order_by("-version_number")

    def next_version_number(self, file: FileAsset) -> int:
        latest = (
            FileVersion.all_objects.filter(file=file)
            .order_by("-version_number")
            .values_list("version_number", flat=True)
            .first()
        )
        return max(latest or 0, file.version_number) + 1

    def prune(self, file: FileAsset, keep: int) -> list[FileVersion]:
        """Drop the oldest versions past the retention limit.

        Returns the rows removed so the caller can delete the underlying blobs
        - the database row and the Cloudinary object must go together.
        """
        surplus = list(self.for_file(file)[keep:])
        if surplus:
            FileVersion.all_objects.filter(pk__in=[v.pk for v in surplus]).delete()
        return surplus


class FileFavoriteRepository(BaseRepository[FileFavorite]):
    model = FileFavorite
    default_select_related = ("file",)

    def toggle(self, user, file: FileAsset) -> bool:
        existing = FileFavorite.all_objects.filter(user=user, file=file).first()
        if existing is None:
            FileFavorite.objects.create(user=user, file=file, created_by=user)
            return True
        if existing.is_deleted:
            existing.restore()
            return True
        existing.delete(deleted_by=user)
        return False

    def for_user(self, user) -> QuerySet[FileFavorite]:
        return (
            self.get_queryset()
            .filter(user=user, file__is_deleted=False)
            .select_related("file", "file__folder")
            .order_by("-created_at")
        )

    def favorite_ids(self, user, file_ids) -> set[str]:
        return {
            str(pk)
            for pk in FileFavorite.objects.filter(user=user, file_id__in=file_ids).values_list(
                "file_id", flat=True
            )
        }


class ShareLinkRepository(BaseRepository[ShareLink]):
    model = ShareLink
    default_select_related = ("file", "created_by")

    def get_by_token(self, token: str) -> ShareLink | None:
        return self.get_queryset().filter(token=token).first()

    def active_for_file(self, file: FileAsset) -> QuerySet[ShareLink]:
        return self.get_queryset().filter(
            file=file, revoked_at__isnull=True, expires_at__gt=timezone.now()
        )

    def created_by_user(self, user) -> QuerySet[ShareLink]:
        return self.get_queryset().filter(created_by=user).order_by("-created_at")

    def expire_stale(self) -> int:
        return ShareLink.objects.filter(
            expires_at__lt=timezone.now(), revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
