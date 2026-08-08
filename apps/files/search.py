"""Search over files and categories.

Postgres does this on its own, so there is no second datastore to run, secure
or keep in sync:

* ``tsvector`` + GIN gives ranked full-text matching with stemming, so
  *"cooler"* finds *"coolers"*.
* ``pg_trgm`` word similarity gives typo tolerance, so *"brochre"* still finds
  *"Brochure"*. This is what makes search feel forgiving instead of literal.

Both indexes live in the same transaction as the data, so a file is findable
the instant it is uploaded - no sync lag and no "it uploaded but search can't
see it" class of bug.

The search vector is refreshed by an explicit call after every write rather
than a database trigger, so the logic is visible in Python and testable without
a migration.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
    TrigramWordSimilarity,
)
from django.db.models import F, Func, Q, QuerySet, TextField, Value
from django.db.models.functions import Greatest

from apps.files.models import FileAsset
from apps.folders.models import Folder

logger = logging.getLogger(__name__)

#: Below this word-similarity score a match is noise rather than a near-miss.
FUZZY_THRESHOLD = 0.4

# Weights: a term in a file's *name* should outrank the same term buried in a
# description. A is strongest.
WEIGHT_NAME = "A"
WEIGHT_TAGS = "B"
WEIGHT_DESCRIPTION = "C"


class ArrayToString(Func):
    """``array_to_string(tags, ' ')`` - flattens the tag array for indexing.

    ``output_field`` is mandatory: Django cannot infer a result type when an
    expression mixes an ``ArrayField`` with a ``CharField`` separator, and
    ``SearchVector`` inspects it to decide how to cast. Without it every tagged
    upload fails with ``FieldError: Expression contains mixed types``.
    """

    function = "array_to_string"
    arity = 2
    output_field = TextField()


def index_file(file: FileAsset) -> None:
    """Refresh one file's search vector.

    A single-row UPDATE, cheap enough to run inline on every upload, rename and
    tag edit.
    """
    FileAsset.all_objects.filter(pk=file.pk).update(
        search_vector=(
            SearchVector("name", weight=WEIGHT_NAME, config="english")
            + SearchVector(
                ArrayToString(F("tags"), Value(" ")), weight=WEIGHT_TAGS, config="english"
            )
            + SearchVector("description", weight=WEIGHT_DESCRIPTION, config="english")
            + SearchVector("extension", weight=WEIGHT_DESCRIPTION, config="english")
        )
    )


def reindex_all(batch_size: int = 500) -> int:
    """Full rebuild. For after a weighting change or a restore from backup."""
    count = 0
    for file in FileAsset.all_objects.only("id").iterator(chunk_size=batch_size):
        index_file(file)
        count += 1
    logger.info("search_reindex_complete", extra={"files": count})
    return count


def search_files(term: str, filters: dict[str, Any] | None = None) -> QuerySet[FileAsset]:
    """Ranked, typo-tolerant file search."""
    filters = filters or {}
    queryset = _apply_filters(FileAsset.objects.select_related("folder", "created_by"), filters)

    if not term:
        return queryset.annotate(rank=Value(0.0)).order_by("-created_at")

    query = SearchQuery(term, config="english", search_type="websearch")
    return (
        queryset.annotate(
            # Take the better of the two signals: full text wins when it
            # matches, word-similarity catches the typos it misses.
            rank=Greatest(
                SearchRank(F("search_vector"), query),
                TrigramWordSimilarity(term, "name"),
            )
        )
        .filter(Q(search_vector=query) | Q(rank__gt=FUZZY_THRESHOLD))
        .order_by("-rank", "-created_at")
    )


def search_folders(term: str) -> QuerySet[Folder]:
    """Category search by name, tolerant of typos."""
    queryset = Folder.objects.select_related("parent")
    if not term:
        return queryset.annotate(rank=Value(0.0)).order_by("name")

    return (
        queryset.annotate(rank=TrigramWordSimilarity(term, "name"))
        .filter(Q(name__icontains=term) | Q(rank__gt=FUZZY_THRESHOLD))
        .order_by("-rank", "name")
    )


def _apply_filters(queryset: QuerySet[FileAsset], filters: dict[str, Any]) -> QuerySet[FileAsset]:
    if filters.get("folder_id"):
        # Searching "inside" a category means its whole subtree - what anyone
        # expects from a file browser.
        folder = Folder.objects.filter(pk=filters["folder_id"]).first()
        if folder is not None:
            subtree = list(
                Folder.objects.filter(path__startswith=folder.subtree_prefix).values_list(
                    "id", flat=True
                )
            )
            queryset = queryset.filter(folder_id__in=[folder.pk, *subtree])

    if filters.get("category"):
        queryset = queryset.filter(category=filters["category"])
    if filters.get("extension"):
        queryset = queryset.filter(extension=str(filters["extension"]).lower().lstrip("."))
    if filters.get("tag"):
        queryset = queryset.filter(tags__contains=[str(filters["tag"]).lower()])
    if filters.get("uploaded_by"):
        queryset = queryset.filter(created_by_id=filters["uploaded_by"])
    if filters.get("date_from"):
        queryset = queryset.filter(created_at__date__gte=filters["date_from"])
    if filters.get("date_to"):
        queryset = queryset.filter(created_at__date__lte=filters["date_to"])
    return queryset
