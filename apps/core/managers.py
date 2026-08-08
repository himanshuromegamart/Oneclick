"""Query sets and managers implementing soft deletion.

Soft deletion is a repository-wide invariant: rows are never removed by normal
application code, they are flagged.  That is what makes the recycle bin and
restore features possible, and it keeps foreign keys from cascading away
history.

Two managers are exposed on every soft-deletable model:

``objects``
    Live rows only.  This is the default manager, so ordinary code cannot
    accidentally serve deleted rows.
``all_objects``
    Every row including deleted ones.  Used by the recycle bin, restore flows
    and the purge task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from django.db import models
from django.utils import timezone

if TYPE_CHECKING:  # pragma: no cover
    from apps.accounts.models import User


class SoftDeleteQuerySet(models.QuerySet):
    """Query set whose ``delete()`` flags rows instead of removing them."""

    def alive(self) -> Self:
        return self.filter(is_deleted=False)

    def dead(self) -> Self:
        return self.filter(is_deleted=True)

    def delete(self, deleted_by: User | None = None) -> tuple[int, dict[str, int]]:
        """Soft-delete every row in the query set in a single UPDATE."""
        count = self.alive().update(
            is_deleted=True,
            deleted_at=timezone.now(),
            deleted_by=deleted_by,
        )
        return count, {self.model._meta.label: count}

    def hard_delete(self) -> tuple[int, dict[str, int]]:
        """Permanently remove rows. Reserved for the retention purge task."""
        return super().delete()

    def restore(self) -> int:
        return self.dead().update(is_deleted=False, deleted_at=None, deleted_by=None)


class SoftDeleteManager(models.Manager):
    """Default manager: only ever returns live rows."""

    def get_queryset(self) -> SoftDeleteQuerySet:
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def alive(self) -> SoftDeleteQuerySet:
        return self.get_queryset()


class AllObjectsManager(models.Manager):
    """Escape hatch manager that sees deleted rows too."""

    def get_queryset(self) -> SoftDeleteQuerySet:
        return SoftDeleteQuerySet(self.model, using=self._db)

    def dead(self) -> SoftDeleteQuerySet:
        return self.get_queryset().dead()

    def alive(self) -> SoftDeleteQuerySet:
        return self.get_queryset().alive()

    def get_including_deleted(self, **kwargs: Any) -> models.Model:
        return self.get_queryset().get(**kwargs)
