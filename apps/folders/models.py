"""The folder tree.

A folder *is* a category: "Quotation", "Water ATM" and "500 LPH" are the same
kind of row at different depths.  There is no separate Category and
Subcategory table, and no fixed set of top-level categories - an administrator
creates whatever hierarchy the business needs, to any depth.

Storage strategy: **adjacency list + materialised path.**

``parent_id`` gives correctness (one authoritative edge per node, enforced by a
foreign key).  ``path`` is a denormalised ``/uuid/uuid/uuid/`` string that
turns the expensive questions into a single indexed prefix scan:

============================  ==========================================
Question                      Query
============================  ==========================================
Whole subtree of X            ``path LIKE '/…/X/%'``           - 1 query
Breadcrumb for X              parse ``path``, one ``IN`` query - 1 query
Is Y inside X?                ``Y.path.startswith(X.path)``    - 0 queries
Depth of X                    stored column                    - 0 queries
============================  ==========================================

The alternative - recursive CTEs - would work, but every listing screen in the
app asks one of these questions, and a prefix index on a ``varchar`` is both
faster and simpler to reason about than a recursive query per request.

The cost is that a move must rewrite the ``path`` of the moved node's
descendants.  That is accepted: moves are rare, listings are constant.
:meth:`FolderRepository.move` does it in one bulk UPDATE.
"""

from __future__ import annotations

import uuid as uuid_lib
from typing import TYPE_CHECKING

from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models.functions import Lower, Upper

from apps.core.managers import SoftDeleteQuerySet
from apps.core.models import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    pass

PATH_SEPARATOR = "/"


class FolderQuerySet(SoftDeleteQuerySet):
    """Tree-shaped reads, expressed once.

    Extends :class:`SoftDeleteQuerySet` rather than ``models.QuerySet`` so the
    soft-delete contract survives: overriding ``objects`` with a plain query
    set would silently start serving recycled folders.
    """

    def roots(self):
        return self.filter(parent__isnull=True)

    def children_of(self, folder: Folder | None):
        return self.filter(parent=folder)

    def descendants_of(self, folder: Folder, include_self: bool = False):
        """Everything under ``folder``, at any depth, in one indexed scan."""
        qs = self.filter(path__startswith=folder.subtree_prefix)
        return qs | self.filter(pk=folder.pk) if include_self else qs

    def ancestors_of(self, folder: Folder):
        return self.filter(pk__in=folder.ancestor_ids).order_by("depth")

    def at_depth(self, depth: int):
        return self.filter(depth=depth)


class FolderManager(models.Manager.from_queryset(FolderQuerySet)):  # type: ignore[misc]
    """Default manager: live folders only, with the tree helpers attached."""

    def get_queryset(self) -> FolderQuerySet:
        return super().get_queryset().filter(is_deleted=False)


class AllFoldersManager(models.Manager.from_queryset(FolderQuerySet)):  # type: ignore[misc]
    """Sees deleted folders too - used by the recycle bin and restore."""


class Folder(BaseModel):
    """A node in the category tree. Composite pattern: a folder holds folders
    and files interchangeably, and callers treat both through the same API."""

    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )

    #: ``/`` for a root, otherwise ``/<ancestor-uuid>/…/`` ending in a separator.
    #: Never set by hand - always via :meth:`rebuild_path`.
    path = models.CharField(max_length=2048, db_index=True, editable=False, default=PATH_SEPARATOR)
    depth = models.PositiveSmallIntegerField(default=0, db_index=True, editable=False)

    description = models.TextField(blank=True, default="")
    #: Free-form UI hints owned by the mobile client; the backend never
    #: interprets them, which keeps new icon sets from needing a deploy.
    icon = models.CharField(max_length=60, blank=True, default="")
    color = models.CharField(max_length=16, blank=True, default="")

    #: Manual ordering within a parent. Ties break by name.
    position = models.PositiveIntegerField(default=0)

    is_system = models.BooleanField(
        default=False,
        help_text="Seeded top-level categories that cannot be renamed or deleted.",
    )
    is_pinned = models.BooleanField(default=False)

    #: Denormalised counters. Kept current by the folder/file services so a
    #: listing screen does not need an aggregate per row.
    file_count = models.PositiveIntegerField(default=0, editable=False)
    subfolder_count = models.PositiveIntegerField(default=0, editable=False)
    total_size_bytes = models.BigIntegerField(default=0, editable=False)

    objects = FolderManager()
    all_objects = AllFoldersManager()

    class Meta:
        ordering = ("position", "name")
        constraints = [
            # Two live siblings may not share a name, case-insensitively -
            # "Water ATM" and "water atm" in one folder would be unusable on a
            # phone. Partial (is_deleted=False) so a deleted folder in the
            # recycle bin never blocks re-creating the same name.
            models.UniqueConstraint(
                Lower("name"),
                "parent",
                condition=models.Q(is_deleted=False),
                name="uniq_folder_name_per_parent",
            ),
            # Postgres treats NULLs as distinct, so the constraint above does
            # not cover roots. This one does.
            models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(is_deleted=False, parent__isnull=True),
                name="uniq_root_folder_name",
            ),
            models.CheckConstraint(
                condition=models.Q(depth__gte=0), name="folder_depth_non_negative"
            ),
        ]
        indexes = [
            models.Index(fields=["parent", "is_deleted", "position"]),
            models.Index(fields=["path"], name="folder_path_idx"),
            models.Index(fields=["is_deleted", "-updated_at"]),
            models.Index(fields=["depth", "is_deleted"]),
            # Backs typo-tolerant folder search; see apps/search/services.py.
            GinIndex(
                OpClass(Upper("name"), name="gin_trgm_ops"),
                name="folder_name_trigram_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    # -- path helpers -----------------------------------------------------
    @property
    def subtree_prefix(self) -> str:
        """Prefix matching every descendant of this folder (excluding itself)."""
        return f"{self.path}{self.pk}{PATH_SEPARATOR}"

    @property
    def ancestor_ids(self) -> list[uuid_lib.UUID]:
        """Ancestors, root-first, parsed from the materialised path."""
        return [
            uuid_lib.UUID(segment)
            for segment in self.path.strip(PATH_SEPARATOR).split(PATH_SEPARATOR)
            if segment
        ]

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    def rebuild_path(self, parent: Folder | None) -> None:
        """Recompute ``path`` and ``depth`` from a (new) parent.

        Does not save, and does not touch descendants - the repository does
        both, so a move stays a single transaction.
        """
        if parent is None:
            self.path = PATH_SEPARATOR
            self.depth = 0
        else:
            self.path = parent.subtree_prefix
            self.depth = parent.depth + 1

    def is_ancestor_of(self, other: Folder) -> bool:
        return other.path.startswith(self.subtree_prefix)

    def is_descendant_of(self, other: Folder) -> bool:
        return self.path.startswith(other.subtree_prefix)

    def save(self, *args, **kwargs) -> None:
        # Guarantee the invariant even if a caller bypasses the service layer.
        if not self.path or self.path == PATH_SEPARATOR:
            self.rebuild_path(self.parent)
        super().save(*args, **kwargs)


class FolderFavorite(BaseModel):
    """A user's starred folder."""

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="folder_favorites"
    )
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name="favorited_by")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "folder"], name="uniq_user_folder_favorite")
        ]
        indexes = [models.Index(fields=["user", "-created_at"])]
