"""The tree as the explorer sees it: one interface for two kinds of thing.

A category holds other categories and it holds documents. The screen has to
draw both in the same list, sorted together, with the same columns - so the
view should not be asking "is this a folder or a file?" on every row.

This is the composite pattern. :class:`Node` is the interface, :class:`Category`
is the composite (it can contain other nodes) and :class:`Document` is the leaf.
The template renders ``Node``; it never learns which one it has.

The pattern earns its place here rather than being decoration: the listing is a
single ordered sequence mixing both types, and the two "add" flows, the delete
flow and the breadcrumb all want to treat a row uniformly. Without it every one
of those grows an ``{% if %}``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from django.urls import reverse

from apps.files.models import FileAsset
from apps.files.repositories import FileRepository
from apps.folders.models import Folder
from apps.folders.repositories import FolderRepository


class Node(ABC):
    """One row in a category listing.

    Everything the template needs, named the same way for both kinds.
    """

    #: "category" or "document" - for the delete form and the row icon only.
    kind: str

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def detail(self) -> str:
        """The grey second line: what is inside, or how big it is."""

    @property
    @abstractmethod
    def url(self) -> str:
        """Where clicking the row goes."""

    @property
    def is_container(self) -> bool:
        """True when this node can hold other nodes.

        The one question the template legitimately asks, because a container
        opens in place and a leaf opens in a new tab.
        """
        return False

    @property
    def created_by(self) -> Any:
        return None

    @property
    def created_at(self) -> Any:
        return None


class Category(Node):
    """A composite: holds both other categories and documents."""

    kind = "category"

    def __init__(self, folder: Folder) -> None:
        self.folder = folder

    @property
    def id(self) -> str:
        return str(self.folder.pk)

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def detail(self) -> str:
        # Counters are maintained on the row, so this costs no extra query.
        subfolders, files = self.folder.subfolder_count, self.folder.file_count
        if not subfolders and not files:
            return "Empty"

        parts = []
        if subfolders:
            parts.append(f"{subfolders} categor{'y' if subfolders == 1 else 'ies'}")
        if files:
            parts.append(f"{files} document{'' if files == 1 else 's'}")
        return " · ".join(parts)

    @property
    def url(self) -> str:
        return reverse("dashboard:explorer", args=[self.folder.pk])

    @property
    def is_container(self) -> bool:
        return True

    @property
    def created_by(self) -> Any:
        return self.folder.created_by

    @property
    def created_at(self) -> Any:
        return self.folder.created_at

    def children(self, offset: int = 0, limit: int | None = None) -> list[Node]:
        """The nodes directly inside this one."""
        listing = children_of(self.folder, offset=offset, limit=limit)
        return listing.nodes


class Document(Node):
    """A leaf: it holds nothing."""

    kind = "document"

    def __init__(self, file: FileAsset) -> None:
        self.file = file

    @property
    def id(self) -> str:
        return str(self.file.pk)

    @property
    def name(self) -> str:
        return self.file.name

    @property
    def detail(self) -> str:
        extension = self.file.extension.upper() if self.file.extension else "FILE"
        return f"{extension} · {self.file.size_display}"

    @property
    def url(self) -> str:
        return reverse("dashboard:document-open", args=[self.file.pk])

    @property
    def created_by(self) -> Any:
        return self.file.created_by

    @property
    def created_at(self) -> Any:
        return self.file.created_at

    @property
    def category(self) -> str:
        """image / video / document / … - drives the row icon."""
        return self.file.category


class Listing:
    """One page of a category's contents, plus the counts behind it."""

    def __init__(
        self,
        nodes: list[Node],
        category_count: int,
        document_count: int,
        offset: int,
        limit: int | None,
    ) -> None:
        self.nodes = nodes
        self.category_count = category_count
        self.document_count = document_count
        self.offset = offset
        self.limit = limit

    @property
    def total(self) -> int:
        return self.category_count + self.document_count

    @property
    def is_empty(self) -> bool:
        return self.total == 0

    @property
    def has_next(self) -> bool:
        return self.limit is not None and self.offset + self.limit < self.total

    @property
    def has_previous(self) -> bool:
        return self.offset > 0


def children_of(folder: Folder | None, *, offset: int = 0, limit: int | None = None) -> Listing:
    """Categories then documents, as one sequence, reading only one page.

    The two live in different tables, so the slice is applied across both as
    if they were already concatenated: categories fill the page first, and
    documents take whatever room is left. That keeps a category holding ten
    thousand documents to a LIMIT/OFFSET query rather than loading the lot to
    slice it in Python.

    Categories always sort before documents, so the order stays stable while
    somebody scrolls and a page boundary can never interleave the two.
    """
    categories_qs = FolderRepository().children(folder)
    # A document must live inside a category, so the top level holds none.
    documents_qs = (
        FileRepository().in_folder(folder).order_by("name") if folder is not None else None
    )

    category_count = categories_qs.count()
    document_count = documents_qs.count() if documents_qs is not None else 0

    if limit is None:
        folders = list(categories_qs[offset:])
        files = (
            list(documents_qs[max(offset - category_count, 0) :])
            if documents_qs is not None
            else []
        )
    else:
        folders = list(categories_qs[offset : offset + limit]) if offset < category_count else []

        files = []
        remaining = limit - len(folders)
        if documents_qs is not None and remaining > 0:
            # Once past the categories, keep counting from where they ended.
            file_offset = max(offset - category_count, 0)
            files = list(documents_qs[file_offset : file_offset + remaining])

    nodes: list[Node] = [Category(f) for f in folders] + [Document(f) for f in files]
    return Listing(nodes, category_count, document_count, offset, limit)
