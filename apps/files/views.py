"""File API: upload, browse, download, share, recycle bin and search."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import CanContribute, IsActiveUser
from apps.core.exceptions import PermissionDeniedError
from apps.core.pagination import CursorPageNumberPagination
from apps.core.responses import created, ok
from apps.core.serializers import (
    DetailSerializer,
    FavoriteToggleSerializer,
    SharedFileSerializer,
    UploadSignatureSerializer,
)
from apps.core.throttling import DownloadThrottle, SearchThrottle, UploadThrottle
from apps.files.repositories import (
    FileFavoriteRepository,
    FileRepository,
    FileVersionRepository,
    ShareLinkRepository,
)
from apps.files.search import search_files
from apps.files.serializers import (
    DirectUploadCallbackSerializer,
    FileMoveSerializer,
    FileSerializer,
    FileUpdateSerializer,
    FileUploadSerializer,
    FileVersionSerializer,
    FileVersionUploadSerializer,
    ShareLinkCreateSerializer,
    ShareLinkSerializer,
    SignedURLSerializer,
    UploadSignatureRequestSerializer,
)
from apps.files.services import FileFavoriteService, FileService, ShareService


class FileViewSet(viewsets.ViewSet):
    """``/api/v1/documents/``"""

    permission_classes = (IsActiveUser, CanContribute)
    parser_classes = (JSONParser, MultiPartParser, FormParser)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.repo = FileRepository()
        self.service = FileService()
        # Not `favorites`: that name belongs to the @action below, and an
        # instance attribute would shadow it at dispatch time.
        self.favorite_service = FileFavoriteService()
        self.shares = ShareService()

    def get_throttles(self):
        current = getattr(self, "action", "")
        if current in {"create", "register_upload"} or (
            current == "versions" and self.request.method == "POST"
        ):
            return [UploadThrottle()]
        if current in {"download", "preview"}:
            return [DownloadThrottle()]
        return super().get_throttles()

    def _context(self, request: Request, files) -> dict:
        return {
            "favorite_file_ids": FileFavoriteRepository().favorite_ids(
                request.user, [f.pk for f in files]
            ),
            "request": request,
        }

    @staticmethod
    def _assert_can_modify(request: Request, file) -> None:
        """Owner may change any file; everyone else only what they uploaded."""
        if not request.user.can_modify(file):
            raise PermissionDeniedError(detail="You can only change files you uploaded yourself.")

    # -- reads ------------------------------------------------------------
    @extend_schema(
        tags=["files"],
        parameters=[
            OpenApiParameter("folder_id", str, required=True, description="Category to list."),
            OpenApiParameter("category", str, description="document|image|video|spreadsheet|…"),
            OpenApiParameter("extension", str),
            OpenApiParameter("tag", str),
            OpenApiParameter("ordering", str, description="-created_at | name | -size_bytes"),
        ],
        responses={200: FileSerializer(many=True)},
        summary="List files in a category",
    )
    def list(self, request: Request) -> Response:
        from apps.folders.repositories import FolderRepository

        folder = FolderRepository().get_or_raise(
            request.query_params.get("folder_id"), message="Category not found."
        )
        queryset = self.repo.in_folder(folder)

        category = request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)
        extension = request.query_params.get("extension")
        if extension:
            queryset = queryset.filter(extension=extension.lower().lstrip("."))
        tag = request.query_params.get("tag")
        if tag:
            queryset = queryset.filter(tags__contains=[tag.lower()])

        ordering = request.query_params.get("ordering", "-created_at")
        if ordering.lstrip("-") in {"created_at", "name", "size_bytes", "download_count"}:
            queryset = queryset.order_by(ordering)

        paginator = CursorPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            FileSerializer(page, many=True, context=self._context(request, page)).data
        )

    @extend_schema(tags=["files"], responses={200: FileSerializer}, summary="File details")
    def retrieve(self, request: Request, pk: str) -> Response:
        file = self.repo.get_or_raise(pk, message="File not found.")
        self.repo.touch_recent(request.user, file)
        return ok(
            FileSerializer(file, context=self._context(request, [file])).data, request=request
        )

    @extend_schema(
        tags=["files"], responses={200: FileSerializer(many=True)}, summary="Recently opened"
    )
    @action(detail=False, methods=["get"])
    def recent(self, request: Request) -> Response:
        files = [entry.file for entry in self.repo.recent_for(request.user, limit=50)]
        return ok(
            FileSerializer(files, many=True, context=self._context(request, files)).data,
            request=request,
        )

    @extend_schema(
        tags=["files"], responses={200: FileSerializer(many=True)}, summary="Starred files"
    )
    @action(detail=False, methods=["get"])
    def favorites(self, request: Request) -> Response:
        files = [f.file for f in self.favorite_service.list_for(request.user)]
        return ok(
            FileSerializer(files, many=True, context=self._context(request, files)).data,
            request=request,
        )

    @extend_schema(
        tags=["files"],
        responses={200: FileSerializer(many=True)},
        summary="Recycle bin",
        description=(
            "Deleted files awaiting permanent removal. The owner sees "
            "everything; anyone else sees only what they deleted themselves."
        ),
    )
    @action(detail=False, methods=["get"])
    def deleted(self, request: Request) -> Response:
        queryset = self.repo.deleted_items()
        if not request.user.is_owner:
            queryset = queryset.filter(created_by=request.user)

        paginator = CursorPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            FileSerializer(page, many=True, context={"request": request}).data
        )

    # -- delivery ---------------------------------------------------------
    @extend_schema(
        tags=["files"],
        responses={200: SignedURLSerializer},
        summary="Get a download link",
        description=(
            "Returns a short-lived signed URL. The app downloads from that URL "
            "directly - the bytes never pass through this server."
        ),
    )
    @action(detail=True, methods=["get"])
    def download(self, request: Request, pk: str) -> Response:
        file = self.repo.get_or_raise(pk, message="File not found.")
        return ok(self.service.download_url(request.user, file), request=request)

    @extend_schema(
        tags=["files"], responses={200: SignedURLSerializer}, summary="Get a preview link"
    )
    @action(detail=True, methods=["get"])
    def preview(self, request: Request, pk: str) -> Response:
        file = self.repo.get_or_raise(pk, message="File not found.")
        return ok(self.service.preview_url(request.user, file), request=request)

    # -- writes -----------------------------------------------------------
    @extend_schema(
        tags=["files"],
        request=FileUploadSerializer,
        responses={201: FileSerializer},
        summary="Upload a file",
        description=(
            "Multipart upload of a PDF, image, video, Word/Excel file and so "
            "on. Good for everyday documents; for very large media use "
            "`/files/upload-signature/` and upload straight to Cloudinary."
        ),
    )
    def create(self, request: Request) -> Response:
        serializer = FileUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        upload = data["file"]

        file = self.service.upload(
            request.user,
            folder_id=data["folder_id"],
            file_obj=upload.file,
            filename=upload.name,
            size_bytes=upload.size,
            content_type=getattr(upload, "content_type", "") or "",
            description=data["description"],
            tags=data["tags"],
        )
        return created(
            FileSerializer(file, context=self._context(request, [file])).data, request=request
        )

    @extend_schema(
        tags=["files"],
        request=UploadSignatureRequestSerializer,
        responses={200: UploadSignatureSerializer},
        summary="Get a direct-upload signature (large files)",
    )
    @action(detail=False, methods=["post"], url_path="upload-signature")
    def upload_signature(self, request: Request) -> Response:
        serializer = UploadSignatureRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return ok(
            self.service.build_upload_signature(
                request.user,
                serializer.validated_data["folder_id"],
                serializer.validated_data["filename"],
            ),
            request=request,
        )

    @extend_schema(
        tags=["files"],
        request=DirectUploadCallbackSerializer,
        responses={201: FileSerializer},
        summary="Register a completed direct upload",
    )
    @action(detail=False, methods=["post"], url_path="register-upload")
    def register_upload(self, request: Request) -> Response:
        serializer = DirectUploadCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file = self.service.register_direct_upload(request.user, **serializer.validated_data)
        return created(
            FileSerializer(file, context=self._context(request, [file])).data, request=request
        )

    @extend_schema(
        methods=["GET"],
        tags=["files"],
        responses={200: FileVersionSerializer(many=True)},
        summary="List previous versions",
    )
    @extend_schema(
        methods=["POST"],
        tags=["files"],
        request=FileVersionUploadSerializer,
        responses={200: FileSerializer},
        summary="Upload a new version",
        description="Replaces the contents. The previous version is kept and stays listed here.",
    )
    @action(detail=True, methods=["get", "post"], url_path="versions")
    def versions(self, request: Request, pk: str) -> Response:
        """One route, two verbs.

        GET and POST must live on the same action: registering them as two
        separate `@action`s with the same `url_path` makes the router keep only
        one, and the other verb silently 405s.
        """
        file = self.repo.get_or_raise(pk, message="File not found.")

        if request.method == "GET":
            return ok(
                FileVersionSerializer(FileVersionRepository().for_file(file), many=True).data,
                request=request,
            )

        self._assert_can_modify(request, file)
        serializer = FileVersionUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]

        file = self.service.upload_new_version(
            request.user,
            file,
            file_obj=upload.file,
            filename=upload.name,
            size_bytes=upload.size,
            note=serializer.validated_data["note"],
        )
        return ok(
            FileSerializer(file, context=self._context(request, [file])).data, request=request
        )

    @extend_schema(
        tags=["files"],
        request=FileUpdateSerializer,
        responses={200: FileSerializer},
        summary="Rename a file or edit its tags",
    )
    def partial_update(self, request: Request, pk: str) -> Response:
        file = self.repo.get_or_raise(pk, message="File not found.")
        self._assert_can_modify(request, file)

        serializer = FileUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if "name" in data:
            file = self.service.rename(request.user, file, data.pop("name"))
        if data:
            file = self.service.update_metadata(request.user, file, data)

        return ok(
            FileSerializer(file, context=self._context(request, [file])).data, request=request
        )

    @extend_schema(
        tags=["files"],
        request=FileMoveSerializer,
        responses={200: FileSerializer},
        summary="Move a file to another category",
    )
    @action(detail=True, methods=["post"])
    def move(self, request: Request, pk: str) -> Response:
        file = self.repo.get_or_raise(pk, message="File not found.")
        self._assert_can_modify(request, file)

        serializer = FileMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file = self.service.move(request.user, file, serializer.validated_data["folder_id"])
        return ok(
            FileSerializer(file, context=self._context(request, [file])).data, request=request
        )

    @extend_schema(
        tags=["files"],
        request=FileMoveSerializer,
        responses={201: FileSerializer},
        summary="Copy a file into another category",
    )
    @action(detail=True, methods=["post"])
    def copy(self, request: Request, pk: str) -> Response:
        file = self.repo.get_or_raise(pk, message="File not found.")
        serializer = FileMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        copy = self.service.copy(request.user, file, serializer.validated_data["folder_id"])
        return created(
            FileSerializer(copy, context=self._context(request, [copy])).data, request=request
        )

    @extend_schema(
        tags=["files"],
        responses={200: DetailSerializer},
        summary="Delete a file",
        description=(
            "Moves the file to the recycle bin, where it can be restored. You "
            "can delete files you uploaded; the owner can delete any file."
        ),
    )
    def destroy(self, request: Request, pk: str) -> Response:
        file = self.repo.get_or_raise(pk, message="File not found.")
        self._assert_can_modify(request, file)

        self.service.delete(request.user, file)
        return ok({"detail": "File moved to the recycle bin."}, request=request)

    @extend_schema(tags=["files"], responses={200: FileSerializer}, summary="Restore a file")
    @action(detail=True, methods=["post"])
    def restore(self, request: Request, pk: str) -> Response:
        file = self.repo.get_deleted_or_raise(pk)
        self._assert_can_modify(request, file)

        file = self.service.restore(request.user, file)
        return ok(
            FileSerializer(file, context=self._context(request, [file])).data, request=request
        )

    @extend_schema(
        tags=["files"],
        responses={200: DetailSerializer},
        summary="Permanently delete a file (owner only)",
        description="Irreversible. Removes the stored file and every archived version.",
    )
    @action(detail=True, methods=["delete"], url_path="purge")
    def purge(self, request: Request, pk: str) -> Response:
        file = self.repo.get_deleted_or_raise(pk)
        self.service.purge(request.user, file)
        return ok({"detail": "File permanently deleted."}, request=request)

    @extend_schema(
        tags=["files"],
        request=None,
        responses={200: FavoriteToggleSerializer},
        summary="Star or unstar a file",
    )
    @action(detail=True, methods=["post"], url_path="favorite")
    def toggle_favorite(self, request: Request, pk: str) -> Response:
        file = self.repo.get_or_raise(pk, message="File not found.")
        return ok(
            {"is_favorite": self.favorite_service.toggle(request.user, file)}, request=request
        )

    @extend_schema(
        tags=["files"],
        request=ShareLinkCreateSerializer,
        responses={201: ShareLinkSerializer},
        summary="Create a share link",
        description=(
            "Produces a link anyone can open without an account - for sending a "
            "catalogue or price list to a customer. It expires, can cap the "
            "number of downloads, and can be revoked at any time."
        ),
    )
    @action(detail=True, methods=["post"])
    def share(self, request: Request, pk: str) -> Response:
        file = self.repo.get_or_raise(pk, message="File not found.")
        serializer = ShareLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = self.shares.create_for_file(
            request.user,
            file,
            expires_in_hours=serializer.validated_data.get("expires_in_hours"),
            max_downloads=serializer.validated_data.get("max_downloads"),
            note=serializer.validated_data["note"],
        )
        return created(
            ShareLinkSerializer(link, context={"request": request}).data, request=request
        )


class ShareLinkViewSet(viewsets.ViewSet):
    """``/api/v1/share-links/`` - manage the links you have created."""

    permission_classes = (IsActiveUser, CanContribute)

    @extend_schema(
        tags=["files"], responses={200: ShareLinkSerializer(many=True)}, summary="Your share links"
    )
    def list(self, request: Request) -> Response:
        links = ShareLinkRepository().created_by_user(request.user)
        paginator = CursorPageNumberPagination()
        page = paginator.paginate_queryset(links, request, view=self)
        return paginator.get_paginated_response(
            ShareLinkSerializer(page, many=True, context={"request": request}).data
        )

    @extend_schema(tags=["files"], responses={200: DetailSerializer}, summary="Revoke a share link")
    def destroy(self, request: Request, pk: str) -> Response:
        link = ShareLinkRepository().get_or_raise(pk, message="Share link not found.")
        ShareService().revoke(request.user, link)
        return ok({"detail": "Share link revoked."}, request=request)


class PublicShareView(APIView):
    """``GET /api/v1/share/<token>/`` - open a shared file.

    Unauthenticated by design: the recipient is a customer, not a user. The
    token *is* the credential, which is why it carries 256 bits of entropy,
    expires, and can be revoked or download-capped.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = (DownloadThrottle,)

    @extend_schema(
        tags=["share"],
        responses={200: SharedFileSerializer, 410: None},
        summary="Open a shared file",
        description="Returns `410 SHARE_LINK_EXPIRED` once the link lapses or is revoked.",
        auth=[],
    )
    def get(self, request: Request, token: str) -> Response:
        _, payload = ShareService().resolve(token)
        return ok(payload, request=request)


# ---------------------------------------------------------------------------
# Browse - folders and files together
# ---------------------------------------------------------------------------
class BrowseItemSerializer(serializers.Serializer):
    """One row in a folder listing - either a folder or a file.

    Declared for the OpenAPI schema; the view builds plain dicts in this shape.
    Branch on ``type``: the fields after it differ.
    """

    type = serializers.ChoiceField(choices=["folder", "file"])
    id = serializers.CharField()
    name = serializers.CharField()
    is_favorite = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    created_by = serializers.DictField(allow_null=True)
    # folder only
    subfolder_count = serializers.IntegerField(required=False)
    file_count = serializers.IntegerField(required=False)
    icon = serializers.CharField(required=False)
    color = serializers.CharField(required=False)
    # file only
    extension = serializers.CharField(required=False)
    category = serializers.CharField(required=False)
    size_bytes = serializers.IntegerField(required=False)
    size_display = serializers.CharField(required=False)
    thumbnail_url = serializers.CharField(required=False)
    is_previewable = serializers.BooleanField(required=False)


class BrowseView(APIView):
    """``GET /api/v1/browse/`` - everything inside one folder, in one call.

    This is what a file-browser screen needs: subfolders and documents in a
    single ordered list, the way Windows Explorer or Google Drive shows them.
    Calling ``/folders/`` and ``/files/`` separately would mean two round trips,
    two loading spinners and two pagination cursors to reconcile on the client.

    Folders always sort before files, then each group sorts by name - so the
    order stays stable while the user scrolls and pagination cannot interleave
    the two types.
    """

    permission_classes = (IsActiveUser,)

    @extend_schema(
        tags=["browse"],
        parameters=[
            OpenApiParameter(
                "parent_id",
                str,
                description="Folder to look inside. Omit for the top level.",
            ),
            OpenApiParameter(
                "type", str, description="Optional: `folder` or `file` to show only one kind."
            ),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: BrowseItemSerializer(many=True)},
        summary="Open a folder: subfolders and files together",
        description=(
            "Returns one combined, paginated list - folders first, then files. "
            "`meta.folder` is the folder being viewed (null at the top level) "
            "and `meta.breadcrumb` is the path to it, so the whole screen "
            "renders from a single request."
        ),
    )
    def get(self, request: Request) -> Response:
        from apps.folders.models import FolderFavorite
        from apps.folders.repositories import FolderRepository
        from apps.folders.serializers import FolderSerializer
        from apps.folders.services import FolderService

        folder_repo = FolderRepository()
        parent_id = request.query_params.get("parent_id")
        parent = (
            folder_repo.get_or_raise(parent_id, message="Folder not found.") if parent_id else None
        )

        wanted = request.query_params.get("type", "")
        show_folders = wanted in {"", "folder"}
        show_files = wanted in {"", "file"}

        folders_qs = folder_repo.children(parent) if show_folders else None
        # Files always live inside a folder, so the top level holds folders only.
        files_qs = (
            FileRepository().in_folder(parent).order_by("name")
            if (show_files and parent is not None)
            else None
        )

        folder_count = folders_qs.count() if folders_qs is not None else 0
        file_count = files_qs.count() if files_qs is not None else 0

        page, page_size = self._page_params(request)
        offset = (page - 1) * page_size

        # Slice across the two query sets as if they were one list. Each side
        # is a LIMIT/OFFSET query, so a folder with 10,000 files still reads
        # only the rows on the current page.
        folder_slice = []
        if folders_qs is not None and offset < folder_count:
            folder_slice = list(folders_qs[offset : offset + page_size])

        file_slice = []
        remaining = page_size - len(folder_slice)
        if files_qs is not None and remaining > 0:
            file_offset = max(offset - folder_count, 0)
            file_slice = list(files_qs[file_offset : file_offset + remaining])

        favorite_folder_ids = {
            str(pk)
            for pk in FolderFavorite.objects.filter(
                user=request.user, folder_id__in=[f.pk for f in folder_slice]
            ).values_list("folder_id", flat=True)
        }
        favorite_file_ids = FileFavoriteRepository().favorite_ids(
            request.user, [f.pk for f in file_slice]
        )

        folder_ctx = {"favorite_folder_ids": favorite_folder_ids, "request": request}
        file_ctx = {"favorite_file_ids": favorite_file_ids, "request": request}

        items = [
            {"type": "folder", **FolderSerializer(folder, context=folder_ctx).data}
            for folder in folder_slice
        ] + [{"type": "file", **FileSerializer(file, context=file_ctx).data} for file in file_slice]

        total = folder_count + file_count
        total_pages = max((total + page_size - 1) // page_size, 1)

        return ok(
            items,
            request=request,
            meta={
                "folder": FolderSerializer(parent, context=folder_ctx).data if parent else None,
                "breadcrumb": FolderService().breadcrumb(parent) if parent else [],
                "counts": {"folders": folder_count, "files": file_count, "total": total},
                "pagination": {
                    "count": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "has_next": page < total_pages,
                    "has_previous": page > 1,
                },
            },
        )

    @staticmethod
    def _page_params(request: Request) -> tuple[int, int]:
        try:
            page = max(int(request.query_params.get("page", 1)), 1)
        except ValueError:
            page = 1
        try:
            page_size = int(request.query_params.get("page_size", 50))
        except ValueError:
            page_size = 50
        return page, min(max(page_size, 1), 200)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
class SearchQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    folder_id = serializers.UUIDField(required=False, allow_null=True)
    category = serializers.CharField(required=False, allow_blank=True)
    extension = serializers.CharField(required=False, allow_blank=True)
    tag = serializers.CharField(required=False, allow_blank=True)
    uploaded_by = serializers.UUIDField(required=False, allow_null=True)
    date_from = serializers.DateField(required=False, allow_null=True)
    date_to = serializers.DateField(required=False, allow_null=True)


class SearchView(APIView):
    """``GET /api/v1/search/`` - find a file by name, tag or description."""

    permission_classes = (IsActiveUser,)
    throttle_classes = (SearchThrottle,)

    @extend_schema(
        tags=["search"],
        parameters=[
            OpenApiParameter("q", str, description="What to look for. Minor typos are tolerated."),
            OpenApiParameter("folder_id", str, description="Search inside one category's subtree."),
            OpenApiParameter("category", str, description="document|image|video|spreadsheet|…"),
            OpenApiParameter("extension", str),
            OpenApiParameter("tag", str),
            OpenApiParameter("uploaded_by", str),
            OpenApiParameter("date_from", str, description="YYYY-MM-DD"),
            OpenApiParameter("date_to", str, description="YYYY-MM-DD"),
        ],
        responses={200: FileSerializer(many=True)},
        summary="Search files",
        description=(
            "Ranked search across file names, tags and descriptions, with "
            "typo tolerance - `brochre` still finds `Brochure`."
        ),
    )
    def get(self, request: Request) -> Response:
        serializer = SearchQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        filters = serializer.validated_data
        term = filters.pop("q", "")

        queryset = search_files(term, filters)
        paginator = CursorPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)

        context = {
            "favorite_file_ids": FileFavoriteRepository().favorite_ids(
                request.user, [f.pk for f in page]
            ),
            "request": request,
        }
        return paginator.get_paginated_response(
            FileSerializer(page, many=True, context=context).data
        )
