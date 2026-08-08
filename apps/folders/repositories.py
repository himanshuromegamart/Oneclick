"""Folder data access, including the subtree rewrite that makes moves safe."""

from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction
from django.db.models import Count, F, Q, QuerySet, Sum, Value
from django.db.models.functions import Concat, Substr

from apps.core.repositories import BaseRepository
from apps.folders.models import PATH_SEPARATOR, Folder, FolderFavorite


class FolderRepository(BaseRepository[Folder]):
    model = Folder
    default_select_related = ("parent", "created_by")

    # -- reads ------------------------------------------------------------
    def roots(self) -> QuerySet[Folder]:
        return self.get_queryset().filter(parent__isnull=True).order_by("position", "name")

    def children(self, parent: Folder | None) -> QuerySet[Folder]:
        return self.get_queryset().filter(parent=parent).order_by("position", "name")

    def descendants(self, folder: Folder, include_self: bool = False) -> QuerySet[Folder]:
        qs = self.get_queryset().filter(path__startswith=folder.subtree_prefix)
        if include_self:
            qs = self.get_queryset().filter(
                Q(path__startswith=folder.subtree_prefix) | Q(pk=folder.pk)
            )
        return qs.order_by("depth", "position", "name")

    def ancestors(self, folder: Folder) -> list[Folder]:
        """Breadcrumb trail, root first. One query regardless of depth."""
        ids = folder.ancestor_ids
        if not ids:
            return []
        by_id = {f.pk: f for f in self.get_queryset().filter(pk__in=ids)}
        # Reorder to match the path, which is authoritative for sequence.
        return [by_id[pk] for pk in ids if pk in by_id]

    def sibling_name_taken(self, parent: Folder | None, name: str, exclude_id=None) -> bool:
        qs = Folder.objects.filter(parent=parent, name__iexact=name)
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)
        return qs.exists()

    def deleted_items(self) -> QuerySet[Folder]:
        return (
            Folder.all_objects.filter(is_deleted=True)
            .select_related("parent", "deleted_by")
            .order_by("-deleted_at")
        )

    def next_position(self, parent: Folder | None) -> int:
        last = (
            Folder.objects.filter(parent=parent)
            .order_by("-position")
            .values_list("position", flat=True)
            .first()
        )
        return (last or 0) + 10

    # -- writes -----------------------------------------------------------
    @transaction.atomic
    def create_folder(self, *, name: str, parent: Folder | None, created_by, **extra) -> Folder:
        folder = Folder(
            name=name, parent=parent, created_by=created_by, updated_by=created_by, **extra
        )
        folder.rebuild_path(parent)
        if not folder.position:
            folder.position = self.next_position(parent)
        folder.save()
        if parent is not None:
            self.recount(parent)
        return folder

    @transaction.atomic
    def move(self, folder: Folder, new_parent: Folder | None, moved_by=None) -> Folder:
        """Reparent a folder and rewrite its subtree's materialised paths.

        The rewrite is a single UPDATE that splices the new prefix onto every
        descendant's path::

            new_path = <new prefix> || substr(old_path, len(<old prefix>) + 1)

        Doing it row-by-row in Python would be O(n) queries and would leave the
        tree inconsistent if the request died halfway; this is one statement
        inside one transaction.
        """
        old_prefix = folder.subtree_prefix
        old_parent = folder.parent

        folder.parent = new_parent
        folder.rebuild_path(new_parent)
        folder.updated_by = moved_by
        folder.position = self.next_position(new_parent)
        folder.save(
            update_fields=["parent", "path", "depth", "position", "updated_by", "updated_at"]
        )

        new_prefix = folder.subtree_prefix
        depth_delta = len(new_prefix.strip(PATH_SEPARATOR).split(PATH_SEPARATOR)) - len(
            old_prefix.strip(PATH_SEPARATOR).split(PATH_SEPARATOR)
        )

        Folder.all_objects.filter(path__startswith=old_prefix).update(
            path=Concat(Value(new_prefix), Substr("path", len(old_prefix) + 1)),
            depth=F("depth") + depth_delta,
        )

        # Files store their folder FK, not a path, so they follow automatically.
        for parent in filter(None, (old_parent, new_parent)):
            self.recount(parent)
        return folder

    @transaction.atomic
    def soft_delete_subtree(self, folder: Folder, deleted_by=None) -> int:
        """Recycle a folder and everything beneath it.

        Descendants are flagged too, so a restore can bring the whole branch
        back exactly as it was, and so a listing never shows a live child under
        a deleted parent.
        """
        from django.utils import timezone

        from apps.files.models import FileAsset

        now = timezone.now()
        subtree_ids = list(
            Folder.objects.filter(path__startswith=folder.subtree_prefix).values_list(
                "id", flat=True
            )
        )
        all_ids = [folder.pk, *subtree_ids]

        count = Folder.all_objects.filter(pk__in=all_ids, is_deleted=False).update(
            is_deleted=True, deleted_at=now, deleted_by=deleted_by, updated_at=now
        )
        FileAsset.all_objects.filter(folder_id__in=all_ids, is_deleted=False).update(
            is_deleted=True, deleted_at=now, deleted_by=deleted_by, updated_at=now
        )

        if folder.parent_id:
            parent = Folder.objects.filter(pk=folder.parent_id).first()
            if parent:
                self.recount(parent)
        return count

    @transaction.atomic
    def restore_subtree(self, folder: Folder) -> int:
        """Restore a recycled branch.

        Only rows deleted in the *same* operation are revived, identified by
        sharing the folder's ``deleted_at`` timestamp.  Without that filter a
        restore would also resurrect items the user had deleted individually
        beforehand, which is not what "undo this delete" means.
        """
        from apps.files.models import FileAsset

        deleted_at = folder.deleted_at
        subtree_ids = list(
            Folder.all_objects.filter(path__startswith=folder.subtree_prefix).values_list(
                "id", flat=True
            )
        )
        all_ids = [folder.pk, *subtree_ids]

        folder_filter = Q(pk__in=all_ids, is_deleted=True)
        file_filter = Q(folder_id__in=all_ids, is_deleted=True)
        if deleted_at is not None:
            folder_filter &= Q(deleted_at=deleted_at)
            file_filter &= Q(deleted_at=deleted_at)

        count = Folder.all_objects.filter(folder_filter).update(
            is_deleted=False, deleted_at=None, deleted_by=None
        )
        FileAsset.all_objects.filter(file_filter).update(
            is_deleted=False, deleted_at=None, deleted_by=None
        )

        if folder.parent_id:
            parent = Folder.objects.filter(pk=folder.parent_id).first()
            if parent:
                self.recount(parent)
        return count

    def recount(self, folder: Folder) -> Folder:
        """Refresh the denormalised counters for one folder.

        Direct children only - a recursive total would have to be recomputed
        for every ancestor on every upload. The API exposes recursive totals
        through the statistics endpoint, which aggregates on demand.
        """
        from apps.files.models import FileAsset

        stats = FileAsset.objects.filter(folder=folder).aggregate(
            count=Count("id"), size=Sum("size_bytes")
        )
        folder.file_count = stats["count"] or 0
        folder.total_size_bytes = stats["size"] or 0
        folder.subfolder_count = Folder.objects.filter(parent=folder).count()
        folder.save(
            update_fields=["file_count", "subfolder_count", "total_size_bytes", "updated_at"]
        )
        return folder

    def subtree_statistics(self, folder: Folder) -> dict[str, int]:
        """Recursive totals, computed on demand in two indexed queries."""
        from apps.files.models import FileAsset

        subtree_ids = list(
            Folder.objects.filter(path__startswith=folder.subtree_prefix).values_list(
                "id", flat=True
            )
        )
        all_ids = [folder.pk, *subtree_ids]
        aggregate = FileAsset.objects.filter(folder_id__in=all_ids).aggregate(
            file_count=Count("id"), total_size=Sum("size_bytes")
        )
        return {
            "subfolder_count": len(subtree_ids),
            "direct_subfolder_count": Folder.objects.filter(parent=folder).count(),
            "file_count": aggregate["file_count"] or 0,
            "total_size_bytes": aggregate["total_size"] or 0,
            "depth": folder.depth,
        }

    def bulk_recount(self, folders: Iterable[Folder]) -> None:
        for folder in folders:
            self.recount(folder)


class FolderFavoriteRepository(BaseRepository[FolderFavorite]):
    model = FolderFavorite
    default_select_related = ("folder",)

    def toggle(self, user, folder: Folder) -> bool:
        """Star/unstar. Returns the resulting state."""
        existing = FolderFavorite.all_objects.filter(user=user, folder=folder).first()
        if existing is None:
            FolderFavorite.objects.create(user=user, folder=folder, created_by=user)
            return True
        if existing.is_deleted:
            existing.restore()
            return True
        existing.delete(deleted_by=user)
        return False

    def for_user(self, user) -> QuerySet[FolderFavorite]:
        return self.get_queryset().filter(user=user).order_by("-created_at")
