"""Category API.

A category *is* a folder: "Quotation", "Water ATM" and "500 LPH" are the same
kind of row at different depths. Any contributor can add one anywhere, to any
depth - there is no fixed list of top-level categories.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.permissions import CanContribute, IsActiveUser
from apps.core.exceptions import PermissionDeniedError
from apps.core.pagination import CursorPageNumberPagination
from apps.core.responses import created, ok
from apps.core.serializers import DeleteResultSerializer, FavoriteToggleSerializer
from apps.folders.models import FolderFavorite
from apps.folders.repositories import FolderRepository
from apps.folders.serializers import (
    BreadcrumbItemSerializer,
    FolderCreateSerializer,
    FolderMoveSerializer,
    FolderSerializer,
    FolderStatisticsSerializer,
    FolderTreeNodeSerializer,
    FolderUpdateSerializer,
)
from apps.folders.services import FolderFavoriteService, FolderService


class FolderViewSet(viewsets.ViewSet):
    """``/api/v1/categories/``

    One route for every level of the tree. A "category", a "subcategory" and a
    nested "folder" are the same row at different depths - ``parent_id`` is the
    only difference - so they share one endpoint and one identity.
    """

    permission_classes = (IsActiveUser, CanContribute)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.repo = FolderRepository()
        self.service = FolderService()
        # NB: not `favorites` - an instance attribute of that name would shadow
        # the `favorites` @action method, since DRF resolves handlers with
        # getattr(self, action_name).
        self.favorite_service = FolderFavoriteService()

    def _context(self, request: Request, folders) -> dict:
        """Pre-load the caller's stars so the serialiser stays query-free."""
        ids = [f.pk for f in folders]
        favorite_ids = {
            str(pk)
            for pk in FolderFavorite.objects.filter(
                user=request.user, folder_id__in=ids
            ).values_list("folder_id", flat=True)
        }
        return {"favorite_folder_ids": favorite_ids, "request": request}

    # -- reads ------------------------------------------------------------
    @extend_schema(
        tags=["categories"],
        parameters=[
            OpenApiParameter("parent_id", str, description="Omit for top-level categories."),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: FolderSerializer(many=True)},
        summary="List categories in one level",
    )
    def list(self, request: Request) -> Response:
        parent_id = request.query_params.get("parent_id")
        parent = self.repo.get_or_raise(parent_id) if parent_id else None
        queryset = self.repo.children(parent)

        paginator = CursorPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            FolderSerializer(page, many=True, context=self._context(request, page)).data
        )

    @extend_schema(
        tags=["categories"], responses={200: FolderSerializer}, summary="Category details"
    )
    def retrieve(self, request: Request, pk: str) -> Response:
        folder = self.repo.get_or_raise(pk, message="Category not found.")
        return ok(
            FolderSerializer(folder, context=self._context(request, [folder])).data,
            request=request,
        )

    @extend_schema(
        tags=["categories"],
        parameters=[
            OpenApiParameter("root_id", str, description="Subtree root. Omit for the whole tree."),
            OpenApiParameter("max_depth", int, description="Levels to include below the root."),
        ],
        responses={200: FolderTreeNodeSerializer(many=True)},
        summary="Whole category tree",
        description=(
            "The full hierarchy as nested nodes, in a single query. Cached "
            "server-side. Use `max_depth=1` to expand lazily on a slow "
            "connection."
        ),
    )
    @action(detail=False, methods=["get"])
    def tree(self, request: Request) -> Response:
        max_depth = request.query_params.get("max_depth")
        nodes = self.service.tree(
            root_id=request.query_params.get("root_id"),
            max_depth=int(max_depth) if max_depth and max_depth.isdigit() else None,
        )
        return ok(nodes, request=request)

    @extend_schema(
        tags=["categories"],
        responses={200: FolderSerializer(many=True)},
        summary="Direct subcategories",
    )
    @action(detail=True, methods=["get"])
    def children(self, request: Request, pk: str) -> Response:
        folder = self.repo.get_or_raise(pk, message="Category not found.")
        queryset = self.repo.children(folder)
        paginator = CursorPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            FolderSerializer(page, many=True, context=self._context(request, page)).data
        )

    @extend_schema(
        tags=["categories"],
        responses={200: BreadcrumbItemSerializer(many=True)},
        summary="Breadcrumb trail",
        description="Ancestors root-first, ending with the category itself.",
    )
    @action(detail=True, methods=["get"])
    def breadcrumb(self, request: Request, pk: str) -> Response:
        folder = self.repo.get_or_raise(pk, message="Category not found.")
        return ok(self.service.breadcrumb(folder), request=request)

    @extend_schema(
        tags=["categories"],
        responses={200: FolderStatisticsSerializer},
        summary="File and subcategory counts",
    )
    @action(detail=True, methods=["get"])
    def statistics(self, request: Request, pk: str) -> Response:
        folder = self.repo.get_or_raise(pk, message="Category not found.")
        return ok(self.service.statistics(folder), request=request)

    @extend_schema(
        tags=["categories"],
        responses={200: FolderSerializer(many=True)},
        summary="Deleted categories (recycle bin)",
    )
    @action(detail=False, methods=["get"])
    def deleted(self, request: Request) -> Response:
        queryset = self.repo.deleted_items()
        if not request.user.is_owner:
            # Everyone else only sees what they deleted themselves.
            queryset = queryset.filter(created_by=request.user)
        paginator = CursorPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            FolderSerializer(page, many=True, context={"request": request}).data
        )

    @extend_schema(
        tags=["categories"],
        responses={200: FolderSerializer(many=True)},
        summary="Starred categories",
    )
    @action(detail=False, methods=["get"])
    def favorites(self, request: Request) -> Response:
        folders = [
            favorite.folder
            for favorite in self.favorite_service.list_for(request.user)
            if not favorite.folder.is_deleted
        ]
        return ok(
            FolderSerializer(folders, many=True, context=self._context(request, folders)).data,
            request=request,
        )

    # -- writes -----------------------------------------------------------
    @extend_schema(
        tags=["categories"],
        request=FolderCreateSerializer,
        responses={201: FolderSerializer},
        summary="Create a category or subcategory",
        description=(
            "Creates a node anywhere in the tree. Omit `parent_id` for a new "
            "top-level category; pass one to nest it. Nesting is unlimited."
        ),
    )
    def create(self, request: Request) -> Response:
        serializer = FolderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        folder = self.service.create(request.user, serializer.validated_data)
        return created(
            FolderSerializer(folder, context=self._context(request, [folder])).data,
            request=request,
        )

    @extend_schema(
        tags=["categories"],
        request=FolderUpdateSerializer,
        responses={200: FolderSerializer},
        summary="Rename or restyle a category",
    )
    def partial_update(self, request: Request, pk: str) -> Response:
        folder = self.repo.get_or_raise(pk, message="Category not found.")
        self._assert_can_modify(request, folder)

        serializer = FolderUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if "name" in data:
            folder = self.service.rename(request.user, folder, data.pop("name"))
        if data:
            folder = self.repo.update(folder, updated_by=request.user, **data)

        return ok(
            FolderSerializer(folder, context=self._context(request, [folder])).data,
            request=request,
        )

    @extend_schema(
        tags=["categories"],
        request=FolderMoveSerializer,
        responses={200: FolderSerializer},
        summary="Move a category",
        description=(
            "Moves the category and everything inside it. Returns "
            "`400 FOLDER_CYCLE` if the destination sits inside the category "
            "being moved."
        ),
    )
    @action(detail=True, methods=["post"])
    def move(self, request: Request, pk: str) -> Response:
        folder = self.repo.get_or_raise(pk, message="Category not found.")
        self._assert_can_modify(request, folder)

        serializer = FolderMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        folder = self.service.move(request.user, folder, serializer.validated_data.get("parent_id"))
        return ok(
            FolderSerializer(folder, context=self._context(request, [folder])).data,
            request=request,
        )

    @extend_schema(
        tags=["categories"],
        responses={200: DeleteResultSerializer},
        summary="Delete a category",
        description=(
            "Moves the category and everything inside it to the recycle bin, "
            "where it can be restored. Only the owner may delete a category "
            "someone else created."
        ),
    )
    def destroy(self, request: Request, pk: str) -> Response:
        folder = self.repo.get_or_raise(pk, message="Category not found.")
        self._assert_can_modify(request, folder)

        affected = self.service.delete(request.user, folder)
        return ok(
            {"detail": "Category moved to the recycle bin.", "affected": affected}, request=request
        )

    @extend_schema(
        tags=["categories"],
        responses={200: FolderSerializer},
        summary="Restore a deleted category",
    )
    @action(detail=True, methods=["post"])
    def restore(self, request: Request, pk: str) -> Response:
        folder = self.repo.get_deleted_or_raise(pk)
        self._assert_can_modify(request, folder)

        folder = self.service.restore(request.user, folder)
        return ok(
            FolderSerializer(folder, context=self._context(request, [folder])).data,
            request=request,
        )

    @extend_schema(
        tags=["categories"],
        request=None,
        responses={200: FavoriteToggleSerializer},
        summary="Star or unstar a category",
    )
    @action(detail=True, methods=["post"], url_path="favorite")
    def toggle_favorite(self, request: Request, pk: str) -> Response:
        folder = self.repo.get_or_raise(pk, message="Category not found.")
        return ok(
            {"is_favorite": self.favorite_service.toggle(request.user, folder)}, request=request
        )

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _assert_can_modify(request: Request, folder) -> None:
        """Owner may change anything; others only what they created."""
        if not request.user.can_modify(folder):
            raise PermissionDeniedError(
                detail="You can only change categories you created yourself."
            )
