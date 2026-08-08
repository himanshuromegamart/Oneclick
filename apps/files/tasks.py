"""Background maintenance for stored media."""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(name="apps.files.tasks.purge_recycle_bin", bind=True, max_retries=3)
def purge_recycle_bin(self, retention_days: int | None = None) -> dict[str, int]:
    """Permanently delete files that have sat in the recycle bin past retention.

    Runs nightly.  Storage is billed per gigabyte, so "soft delete forever"
    would quietly become the largest line on the invoice.

    Each file is handled independently: one Cloudinary failure must not stop
    the rest of the batch, so failures are counted and reported rather than
    raised.
    """
    from apps.files.models import FileAsset
    from apps.files.repositories import FileRepository, FileVersionRepository
    from apps.files.storage import get_storage_backend

    days = retention_days or settings.STORAGE_SETTINGS["RECYCLE_BIN_RETENTION_DAYS"]
    repo = FileRepository()
    versions = FileVersionRepository()
    storage = get_storage_backend()

    purged = failed = 0
    for file in repo.purgeable(days).iterator(chunk_size=100):
        try:
            shared = (
                FileAsset.all_objects.filter(public_id=file.public_id).exclude(pk=file.pk).exists()
            )
            if not shared:
                storage.delete(file.public_id, file.resource_type)
            for version in versions.for_file(file):
                storage.delete(version.public_id, version.resource_type)
            file.hard_delete()
            purged += 1
        except Exception:
            logger.exception("purge_failed", extra={"file_id": str(file.pk)})
            failed += 1

    logger.info("recycle_bin_purged", extra={"purged": purged, "failed": failed, "days": days})
    return {"purged": purged, "failed": failed}


@shared_task(name="apps.files.tasks.purge_deleted_folders")
def purge_deleted_folders(retention_days: int | None = None) -> int:
    """Hard-delete folders whose retention window has passed.

    Runs after :func:`purge_recycle_bin` so the files inside are already gone;
    the cascade then has nothing left to remove.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.folders.models import Folder

    days = retention_days or settings.STORAGE_SETTINGS["RECYCLE_BIN_RETENTION_DAYS"]
    cutoff = timezone.now() - timedelta(days=days)

    queryset = Folder.all_objects.filter(is_deleted=True, deleted_at__lt=cutoff)
    count = queryset.count()
    queryset.delete()

    logger.info("deleted_folders_purged", extra={"count": count})
    return count


@shared_task(name="apps.files.tasks.expire_share_links")
def expire_share_links() -> int:
    """Mark elapsed share links revoked so listings show the real state."""
    from apps.files.repositories import ShareLinkRepository

    count = ShareLinkRepository().expire_stale()
    logger.info("share_links_expired", extra={"count": count})
    return count


@shared_task(name="apps.files.tasks.rebuild_search_index")
def rebuild_search_index() -> int:
    """Recompute every file's search vector.

    Not needed in normal operation - the vector is written inline on each
    upload and edit. This exists for after a restore from backup or a change to
    the search weightings.
    """
    from apps.files.search import reindex_all

    return reindex_all()


@shared_task(name="apps.files.tasks.trim_recent_files")
def trim_recent_files(keep: int = 100) -> int:
    """Keep every user's recent list bounded."""
    from apps.accounts.models import User
    from apps.files.repositories import FileRepository

    repo = FileRepository()
    total = 0
    for user in User.objects.filter(is_active=True).iterator(chunk_size=200):
        total += repo.trim_recent(user, keep=keep)
    logger.info("recent_files_trimmed", extra={"removed": total})
    return total


@shared_task(name="apps.files.tasks.refresh_folder_counters")
def refresh_folder_counters() -> int:
    """Re-derive the denormalised folder counters.

    The services keep these current, but a crash mid-transaction or a direct
    database edit can leave them stale.  A nightly reconciliation means a wrong
    count is a temporary cosmetic issue rather than permanent drift.
    """
    from apps.folders.models import Folder
    from apps.folders.repositories import FolderRepository

    repo = FolderRepository()
    count = 0
    for folder in Folder.objects.all().iterator(chunk_size=200):
        repo.recount(folder)
        count += 1
    logger.info("folder_counters_refreshed", extra={"folders": count})
    return count
