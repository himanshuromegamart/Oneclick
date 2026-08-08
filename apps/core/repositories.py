"""Generic repository base.

The repository layer is the only place that knows about the ORM.  Services
depend on repositories, views depend on services, and nothing above the
repository writes a ``QuerySet``.  The payoff is concrete rather than
ceremonial:

* query logic is written once and reused (no duplicated ``select_related``
  chains scattered through views);
* services can be unit-tested against a fake repository with no database;
* a future move of search to an external engine touches one class.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Generic, TypeVar
from uuid import UUID

from django.db import models
from django.db.models import QuerySet

from apps.core.exceptions import ResourceNotFound

TModel = TypeVar("TModel", bound=models.Model)


class BaseRepository(Generic[TModel]):
    """CRUD operations shared by every concrete repository."""

    model: type[TModel]
    #: Relations eagerly joined on every read, to keep list endpoints O(1) queries.
    default_select_related: Sequence[str] = ()
    default_prefetch_related: Sequence[str] = ()

    def __init__(self, model: type[TModel] | None = None) -> None:
        if model is not None:
            self.model = model
        if not getattr(self, "model", None):
            raise ValueError(f"{type(self).__name__} requires a `model`.")

    # -- reads ------------------------------------------------------------
    def get_queryset(self) -> QuerySet[TModel]:
        qs = self.model._default_manager.all()
        if self.default_select_related:
            qs = qs.select_related(*self.default_select_related)
        if self.default_prefetch_related:
            qs = qs.prefetch_related(*self.default_prefetch_related)
        return qs

    def all_including_deleted(self) -> QuerySet[TModel]:
        manager = getattr(self.model, "all_objects", self.model._default_manager)
        return manager.all()

    def get_by_id(self, pk: UUID | str) -> TModel | None:
        return self.get_queryset().filter(pk=pk).first()

    def get_or_raise(self, pk: UUID | str, message: str | None = None) -> TModel:
        instance = self.get_by_id(pk)
        if instance is None:
            raise ResourceNotFound(
                detail=message or f"{self.model._meta.verbose_name.title()} not found.",
                details={"id": str(pk)},
            )
        return instance

    def get_deleted_or_raise(self, pk: UUID | str) -> TModel:
        instance = self.all_including_deleted().filter(pk=pk, is_deleted=True).first()
        if instance is None:
            raise ResourceNotFound(
                detail=f"Deleted {self.model._meta.verbose_name} not found.",
                details={"id": str(pk)},
            )
        return instance

    def filter(self, **kwargs: Any) -> QuerySet[TModel]:
        return self.get_queryset().filter(**kwargs)

    def exists(self, **kwargs: Any) -> bool:
        return self.model._default_manager.filter(**kwargs).exists()

    def count(self, **kwargs: Any) -> int:
        return self.model._default_manager.filter(**kwargs).count()

    # -- writes -----------------------------------------------------------
    def create(self, **kwargs: Any) -> TModel:
        return self.model._default_manager.create(**kwargs)

    def bulk_create(self, instances: Iterable[TModel], batch_size: int = 500) -> list[TModel]:
        return self.model._default_manager.bulk_create(list(instances), batch_size=batch_size)

    def update(self, instance: TModel, **kwargs: Any) -> TModel:
        """Save only the changed fields, so concurrent writers do not clobber
        each other's untouched columns."""
        changed: list[str] = []
        for field, value in kwargs.items():
            if getattr(instance, field, None) != value:
                setattr(instance, field, value)
                changed.append(field)
        if changed:
            if any(f.name == "updated_at" for f in instance._meta.get_fields()):
                changed.append("updated_at")
            instance.save(update_fields=changed)
        return instance

    def soft_delete(self, instance: TModel, deleted_by: Any = None) -> TModel:
        instance.delete(deleted_by=deleted_by)  # type: ignore[call-arg]
        return instance

    def restore(self, instance: TModel) -> TModel:
        instance.restore()  # type: ignore[attr-defined]
        return instance
