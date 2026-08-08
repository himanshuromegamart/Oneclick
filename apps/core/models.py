"""Abstract base models shared by every domain table.

The composition is deliberate rather than one god-class:

* :class:`UUIDModel`      - opaque primary keys
* :class:`TimeStampedModel` - created_at / updated_at
* :class:`AuditModel`     - who created and last changed the row
* :class:`SoftDeleteModel` - recycle-bin semantics
* :class:`BaseModel`      - all of the above, the default for domain tables

UUID primary keys are used everywhere because IDs are handed to a mobile client
and appear in share links.  Sequential integers would leak record counts and
make neighbouring records guessable.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.managers import AllObjectsManager, SoftDeleteManager

if TYPE_CHECKING:  # pragma: no cover
    from apps.accounts.models import User


class UUIDModel(models.Model):
    """Primary key that is opaque, non-enumerable and client-generatable."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """Creation and modification timestamps maintained by the ORM."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        abstract = True


class AuditModel(models.Model):
    """Records the acting user for creation and last update.

    ``on_delete=SET_NULL`` keeps the audit trail intact if a user row is ever
    removed - the history matters more than the reference.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_created",
        db_index=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_updated",
    )

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Recycle-bin semantics for a table.

    ``is_deleted`` is a real column rather than a ``deleted_at IS NULL`` check
    so it can participate in composite indexes and partial unique constraints
    cheaply.
    """

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_deleted",
    )

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    def delete(self, using: str | None = None, keep_parents: bool = False, **kwargs) -> None:
        """Flag the row as deleted. Use :meth:`hard_delete` to really remove it."""
        deleted_by: User | None = kwargs.pop("deleted_by", None)
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = deleted_by
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])

    def hard_delete(self, using: str | None = None, keep_parents: bool = False) -> None:
        super().delete(using=using, keep_parents=keep_parents)

    def restore(self) -> None:
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])


class BaseModel(UUIDModel, TimeStampedModel, AuditModel, SoftDeleteModel):
    """The default base for domain tables: UUID + timestamps + audit + soft delete."""

    class Meta:
        abstract = True
        ordering = ("-created_at",)
