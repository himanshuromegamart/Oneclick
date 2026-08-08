"""Folder business rules.

The tree has four invariants that must never be violated, because breaking any
one of them corrupts the structure in a way that is hard to repair:

1. **No cycles.**  Moving a folder into its own descendant would detach the
   branch from the root and make it unreachable.
2. **Bounded depth.**  Nesting is unlimited by product design, but a hard
   ceiling stops a runaway client from producing paths that overflow the
   ``path`` column.
3. **Unique sibling names.**  Enforced in the database, checked here first so
   the user gets a clear message instead of an integrity error.
4. **System folders are immovable.**  Seeded top-level categories are
   referenced by the mobile app's home screen.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from apps.accounts.models import User
from apps.core.exceptions import ConflictError, ErrorCode, ResourceNotFound, ValidationFailed
from apps.core.validators import validate_node_name
from apps.folders.models import Folder
from apps.folders.repositories import FolderFavoriteRepository, FolderRepository

logger = logging.getLogger(__name__)

TREE_CACHE_KEY = "folders:tree:v1"


class FolderService:
    def __init__(self, repository: FolderRepository | None = None) -> None:
        self.repo = repository or FolderRepository()

    @property
    def config(self) -> dict:
        """Read settings at use time, not construction time.

        A service instance can outlive a settings change (a long-lived worker,
        or a test that overrides a limit), and a snapshot taken in ``__init__``
        would silently keep enforcing the stale value.
        """
        return settings.FOLDER_SETTINGS

    # -- guards -----------------------------------------------------------
    def _assert_depth_allowed(self, parent: Folder | None) -> None:
        depth = 0 if parent is None else parent.depth + 1
        if depth > self.config["MAX_DEPTH"]:
            raise ValidationFailed(
                detail=f"Folders cannot be nested more than {self.config['MAX_DEPTH']} levels deep.",
                code=ErrorCode.FOLDER_DEPTH_EXCEEDED,
                details={"max_depth": self.config["MAX_DEPTH"], "attempted_depth": depth},
            )

    def _assert_name_available(self, parent: Folder | None, name: str, exclude_id=None) -> None:
        if self.repo.sibling_name_taken(parent, name, exclude_id=exclude_id):
            location = parent.name if parent else "the top level"
            raise ConflictError(
                detail=f"A folder named '{name}' already exists in {location}.",
                code=ErrorCode.FOLDER_NAME_CONFLICT,
                details={"name": name},
            )

    def _assert_child_limit(self, parent: Folder | None) -> None:
        if parent is None:
            return
        limit = self.config["MAX_CHILDREN_PER_FOLDER"]
        if self.repo.children(parent).count() >= limit:
            raise ValidationFailed(
                detail=f"A folder cannot contain more than {limit} subfolders.",
                details={"limit": limit},
            )

    def _resolve_parent(self, parent_id: Any) -> Folder | None:
        if not parent_id:
            return None
        parent = self.repo.get_by_id(parent_id)
        if parent is None:
            raise ResourceNotFound(
                detail="The destination folder does not exist.",
                details={"parent_id": str(parent_id)},
            )
        return parent

    # -- commands ---------------------------------------------------------
    @transaction.atomic
    def create(self, actor: User, payload: dict[str, Any]) -> Folder:
        name = validate_node_name(payload["name"], kind="folder")
        parent = self._resolve_parent(payload.get("parent_id"))

        self._assert_depth_allowed(parent)
        self._assert_name_available(parent, name)
        self._assert_child_limit(parent)

        folder = self.repo.create_folder(
            name=name,
            parent=parent,
            created_by=actor,
            description=(payload.get("description") or "").strip(),
            icon=(payload.get("icon") or "").strip(),
            color=(payload.get("color") or "").strip(),
            is_pinned=bool(payload.get("is_pinned", False)),
        )
        self._invalidate_tree_cache()
        logger.info(
            "folder_created",
            extra={
                "folder_id": str(folder.pk),
                "parent_id": str(parent.pk) if parent else None,
                "actor_id": str(actor.pk),
                "depth": folder.depth,
            },
        )
        return folder

    @transaction.atomic
    def rename(self, actor: User, folder: Folder, new_name: str) -> Folder:
        if folder.is_system:
            raise ValidationFailed(detail="System categories cannot be renamed.")

        name = validate_node_name(new_name, kind="folder")
        if name == folder.name:
            return folder

        self._assert_name_available(folder.parent, name, exclude_id=folder.pk)
        folder.name = name
        folder.updated_by = actor
        folder.save(update_fields=["name", "updated_by", "updated_at"])
        self._invalidate_tree_cache()
        return folder

    @transaction.atomic
    def move(self, actor: User, folder: Folder, new_parent_id: Any) -> Folder:
        if folder.is_system:
            raise ValidationFailed(detail="System categories cannot be moved.")

        new_parent = self._resolve_parent(new_parent_id)

        if new_parent is not None:
            if new_parent.pk == folder.pk:
                raise ValidationFailed(
                    detail="A folder cannot be moved into itself.", code=ErrorCode.FOLDER_CYCLE
                )
            # The cycle check is a string prefix test against the materialised
            # path - no traversal, no recursion, O(1).
            if new_parent.is_descendant_of(folder):
                raise ValidationFailed(
                    detail="A folder cannot be moved inside one of its own subfolders.",
                    code=ErrorCode.FOLDER_CYCLE,
                    details={"folder_id": str(folder.pk), "target_id": str(new_parent.pk)},
                )

        if (folder.parent_id or None) == (new_parent.pk if new_parent else None):
            return folder

        # Depth must hold for the deepest descendant, not just the moved node.
        deepest = self.repo.descendants(folder, include_self=True).order_by("-depth").first()
        relative_height = (deepest.depth - folder.depth) if deepest else 0
        new_depth = (new_parent.depth + 1 if new_parent else 0) + relative_height
        if new_depth > self.config["MAX_DEPTH"]:
            raise ValidationFailed(
                detail=(
                    "This move would nest folders deeper than the "
                    f"{self.config['MAX_DEPTH']}-level limit."
                ),
                code=ErrorCode.FOLDER_DEPTH_EXCEEDED,
                details={"resulting_depth": new_depth},
            )

        self._assert_name_available(new_parent, folder.name, exclude_id=folder.pk)
        self._assert_child_limit(new_parent)

        folder = self.repo.move(folder, new_parent, moved_by=actor)
        self._invalidate_tree_cache()
        logger.info(
            "folder_moved",
            extra={
                "folder_id": str(folder.pk),
                "new_parent_id": str(new_parent.pk) if new_parent else None,
                "actor_id": str(actor.pk),
            },
        )
        return folder

    @transaction.atomic
    def delete(self, actor: User, folder: Folder) -> int:
        if folder.is_system:
            raise ValidationFailed(detail="System categories cannot be deleted.")

        count = self.repo.soft_delete_subtree(folder, deleted_by=actor)
        self._invalidate_tree_cache()
        logger.info(
            "folder_deleted",
            extra={"folder_id": str(folder.pk), "affected": count, "actor_id": str(actor.pk)},
        )
        return count

    @transaction.atomic
    def restore(self, actor: User, folder: Folder) -> Folder:
        """Bring a recycled folder back.

        A folder cannot be restored under a parent that is itself deleted -
        the result would be invisible.  The user is told to restore the parent
        first rather than being given a silent no-op.
        """
        if folder.parent_id:
            parent = Folder.all_objects.filter(pk=folder.parent_id).first()
            if parent is not None and parent.is_deleted:
                raise ConflictError(
                    detail=f"Restore the parent folder '{parent.name}' first.",
                    details={"parent_id": str(parent.pk)},
                )

        # The original name may have been reused while this was in the bin.
        # The rename must be persisted *before* the subtree is un-flagged:
        # `restore_subtree` clears `is_deleted` with a bulk UPDATE, at which
        # point the partial unique index starts applying to this row. Saving
        # the new name afterwards would be too late - the restore itself would
        # raise IntegrityError.
        if self.repo.sibling_name_taken(folder.parent, folder.name, exclude_id=folder.pk):
            folder.name = self._deduplicate_name(folder.parent, folder.name)
            Folder.all_objects.filter(pk=folder.pk).update(name=folder.name)

        self.repo.restore_subtree(folder)
        folder.refresh_from_db()
        self._invalidate_tree_cache()
        return folder

    def _deduplicate_name(self, parent: Folder | None, name: str) -> str:
        """``Reports`` -> ``Reports (restored)`` -> ``Reports (restored 2)``."""
        candidate = f"{name} (restored)"
        counter = 2
        while self.repo.sibling_name_taken(parent, candidate):
            candidate = f"{name} (restored {counter})"
            counter += 1
        return candidate[:255]

    # -- reads ------------------------------------------------------------
    def breadcrumb(self, folder: Folder) -> list[dict[str, Any]]:
        trail = [
            {"id": str(ancestor.pk), "name": ancestor.name, "depth": ancestor.depth}
            for ancestor in self.repo.ancestors(folder)
        ]
        trail.append({"id": str(folder.pk), "name": folder.name, "depth": folder.depth})
        return trail

    def tree(self, root_id: Any = None, max_depth: int | None = None) -> list[dict[str, Any]]:
        """Build a nested tree in a single query.

        Every folder is fetched flat and assembled in memory.  For a tree of a
        few thousand nodes this is far cheaper than one query per level, and
        the result is cached because the tree changes rarely and is requested
        on every app launch.
        """
        cache_key = f"{TREE_CACHE_KEY}:{root_id or 'all'}:{max_depth or 'full'}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        queryset = self.repo.get_queryset().order_by("depth", "position", "name")

        if root_id:
            root = self.repo.get_or_raise(root_id, message="Folder not found.")
            queryset = queryset.filter(path__startswith=root.subtree_prefix)
            base_depth = root.depth
        else:
            base_depth = -1

        if max_depth is not None:
            queryset = queryset.filter(depth__lte=base_depth + max_depth)

        nodes: dict[str, dict[str, Any]] = {}
        for folder in queryset:
            nodes[str(folder.pk)] = {
                "id": str(folder.pk),
                "name": folder.name,
                "parent_id": str(folder.parent_id) if folder.parent_id else None,
                "depth": folder.depth,
                "icon": folder.icon,
                "color": folder.color,
                "is_system": folder.is_system,
                "is_pinned": folder.is_pinned,
                "file_count": folder.file_count,
                "subfolder_count": folder.subfolder_count,
                "children": [],
            }

        roots: list[dict[str, Any]] = []
        for node in nodes.values():
            parent = nodes.get(node["parent_id"] or "")
            if parent is not None:
                parent["children"].append(node)
            else:
                # Either a true root, or a node whose parent fell outside the
                # requested slice - both belong at the top of this response.
                roots.append(node)

        cache.set(cache_key, roots, self.config["TREE_CACHE_SECONDS"])
        return roots

    def statistics(self, folder: Folder) -> dict[str, int]:
        return self.repo.subtree_statistics(folder)

    @staticmethod
    def _invalidate_tree_cache() -> None:
        """Drop every cached tree slice.

        ``delete_pattern`` is a django-redis extension; the fallback keeps the
        code working against LocMemCache in tests, where a stale tree between
        cases would be a confusing failure.
        """
        try:
            cache.delete_pattern(f"{TREE_CACHE_KEY}:*")  # type: ignore[attr-defined]
        except AttributeError:
            cache.clear()


class FolderFavoriteService:
    def __init__(self, repository: FolderFavoriteRepository | None = None) -> None:
        self.repo = repository or FolderFavoriteRepository()

    def toggle(self, user: User, folder: Folder) -> bool:
        return self.repo.toggle(user, folder)

    def list_for(self, user: User):
        return self.repo.for_user(user)
