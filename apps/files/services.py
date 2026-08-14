"""File business rules: upload, versioning, move/copy, recycle bin, sharing."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import timedelta
from typing import Any, BinaryIO

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.exceptions import (
    ConflictError,
    ErrorCode,
    PermissionDeniedError,
    ResourceNotFound,
    ValidationFailed,
)
from apps.core.validators import validate_node_name, validate_tags, validate_upload
from apps.files.models import (
    FileAsset,
    ShareLink,
    category_for_extension,
    guess_mime_type,
)
from apps.files.repositories import (
    FileFavoriteRepository,
    FileRepository,
    FileVersionRepository,
    ShareLinkRepository,
)
from apps.files.storage import (
    StorageBackend,
    compute_checksum,
    get_storage_backend,
    resource_type_for,
)
from apps.folders.models import Folder
from apps.folders.repositories import FolderRepository

logger = logging.getLogger(__name__)


class FileService:
    """Everything that changes a file."""

    def __init__(
        self,
        repository: FileRepository | None = None,
        folder_repository: FolderRepository | None = None,
        version_repository: FileVersionRepository | None = None,
        storage: StorageBackend | None = None,
    ) -> None:
        self.repo = repository or FileRepository()
        self.folders = folder_repository or FolderRepository()
        self.versions = version_repository or FileVersionRepository()
        self._storage = storage

    @property
    def config(self) -> dict:
        """Read settings at use time - see FolderService.config for why."""
        return settings.STORAGE_SETTINGS

    @property
    def storage(self) -> StorageBackend:
        # Resolved lazily so constructing the service never requires
        # Cloudinary credentials (management commands, tests).
        if self._storage is None:
            self._storage = get_storage_backend()
        return self._storage

    # -- helpers ----------------------------------------------------------
    def _resolve_folder(self, folder_id: Any) -> Folder:
        folder = self.folders.get_by_id(folder_id)
        if folder is None:
            raise ResourceNotFound(
                detail="The destination folder does not exist.",
                details={"folder_id": str(folder_id)},
            )
        return folder

    def _unique_name(self, folder: Folder, name: str, exclude_id=None) -> str:
        """``report.pdf`` -> ``report (1).pdf`` when the name is taken.

        Auto-renaming rather than rejecting matches what a desktop file manager
        does, and avoids failing an upload the user has already waited for.
        """
        if not self.repo.name_taken(folder, name, exclude_id=exclude_id):
            return name

        stem, _, suffix = name.rpartition(".")
        if not stem:  # no extension
            stem, suffix = name, ""
        counter = 1
        while True:
            candidate = f"{stem} ({counter}){'.' + suffix if suffix else ''}"
            if not self.repo.name_taken(folder, candidate, exclude_id=exclude_id):
                return candidate[:255]
            counter += 1

    # -- upload -----------------------------------------------------------
    @transaction.atomic
    def upload(
        self,
        actor: User,
        *,
        folder_id: Any,
        file_obj: BinaryIO,
        filename: str,
        size_bytes: int,
        content_type: str = "",
        description: str = "",
        tags: list[str] | None = None,
    ) -> FileAsset:
        folder = self._resolve_folder(folder_id)
        safe_name, extension = validate_upload(filename, size_bytes, content_type)
        clean_tags = validate_tags(tags)

        checksum = compute_checksum(file_obj)
        duplicate = self.repo.find_duplicate(folder, checksum)

        stored = self.storage.upload(
            file_obj,
            filename=safe_name,
            folder_path=str(folder.pk),
            resource_type=resource_type_for(extension),
        )

        file = FileAsset.objects.create(
            folder=folder,
            name=self._unique_name(folder, safe_name),
            description=description.strip(),
            extension=extension,
            mime_type=guess_mime_type(safe_name) or content_type or "",
            category=category_for_extension(extension),
            size_bytes=stored.size_bytes or size_bytes,
            public_id=stored.public_id,
            resource_type=stored.resource_type,
            secure_url=stored.secure_url,
            thumbnail_url=stored.thumbnail_url,
            checksum=checksum,
            width=stored.width,
            height=stored.height,
            duration_seconds=stored.duration_seconds,
            tags=clean_tags,
            created_by=actor,
            updated_by=actor,
        )

        self.folders.recount(folder)
        self._reindex(file)

        logger.info(
            "file_uploaded",
            extra={
                "file_id": str(file.pk),
                "folder_id": str(folder.pk),
                "actor_id": str(actor.pk),
                "size_bytes": file.size_bytes,
                "duplicate_of": str(duplicate.pk) if duplicate else None,
            },
        )
        return file

    def build_upload_signature(self, actor: User, folder_id: Any, filename: str) -> dict[str, Any]:
        """Mint a direct-to-Cloudinary upload signature.

        The mobile client uses this for large media so the bytes never transit
        a Django worker.  Validation still happens here - the signature is only
        issued for a folder the caller can write to and a filename that passes
        the extension policy.
        """
        folder = self._resolve_folder(folder_id)
        safe_name, extension = validate_upload(filename, size_bytes=1)
        signature = self.storage.build_upload_signature(
            folder_path=str(folder.pk), resource_type=resource_type_for(extension)
        )
        return {**asdict(signature), "suggested_name": safe_name, "folder_id": str(folder.pk)}

    @transaction.atomic
    def register_direct_upload(
        self,
        actor: User,
        *,
        folder_id: Any,
        filename: str,
        public_id: str,
        secure_url: str,
        size_bytes: int,
        resource_type: str = "raw",
        checksum: str = "",
        content_type: str = "",
        tags: list[str] | None = None,
    ) -> FileAsset:
        """Record a file the client uploaded straight to Cloudinary.

        The client reports the outcome; the server re-validates name, size and
        extension because a client-supplied payload is never trusted.
        """
        folder = self._resolve_folder(folder_id)
        safe_name, extension = validate_upload(filename, size_bytes, content_type)

        file = FileAsset.objects.create(
            folder=folder,
            name=self._unique_name(folder, safe_name),
            extension=extension,
            mime_type=guess_mime_type(safe_name) or content_type or "",
            category=category_for_extension(extension),
            size_bytes=size_bytes,
            public_id=public_id,
            resource_type=resource_type,
            secure_url=secure_url,
            checksum=checksum,
            tags=validate_tags(tags),
            created_by=actor,
            updated_by=actor,
        )
        self.folders.recount(folder)
        self._reindex(file)
        return file

    @transaction.atomic
    def upload_new_version(
        self,
        actor: User,
        file: FileAsset,
        *,
        file_obj: BinaryIO,
        filename: str,
        size_bytes: int,
        note: str = "",
    ) -> FileAsset:
        """Replace a file's contents, archiving the previous revision."""
        from apps.files.models import FileVersion

        _, extension = validate_upload(filename, size_bytes)
        if extension != file.extension:
            raise ValidationFailed(
                detail=(
                    f"A new version must have the same file type "
                    f"(.{file.extension}, not .{extension})."
                ),
                details={"expected": file.extension, "received": extension},
            )

        # Archive the current bytes before overwriting the pointer.
        FileVersion.objects.create(
            file=file,
            version_number=file.version_number,
            public_id=file.public_id,
            resource_type=file.resource_type,
            secure_url=file.secure_url,
            size_bytes=file.size_bytes,
            checksum=file.checksum,
            note=note[:255],
            created_by=actor,
        )

        stored = self.storage.upload(
            file_obj,
            filename=file.name,
            folder_path=str(file.folder_id),
            resource_type=resource_type_for(extension),
        )

        file.public_id = stored.public_id
        file.secure_url = stored.secure_url
        file.thumbnail_url = stored.thumbnail_url
        file.resource_type = stored.resource_type
        file.size_bytes = stored.size_bytes or size_bytes
        file.checksum = compute_checksum(file_obj)
        file.version_number = self.versions.next_version_number(file)
        file.updated_by = actor
        file.save()

        # Prune old revisions and delete their blobs, or storage grows forever.
        for stale in self.versions.prune(file, keep=self.config["MAX_FILE_VERSIONS"]):
            self.storage.delete(stale.public_id, stale.resource_type)

        self.folders.recount(file.folder)
        logger.info(
            "file_version_created",
            extra={
                "file_id": str(file.pk),
                "version": file.version_number,
                "actor_id": str(actor.pk),
            },
        )
        return file

    # -- mutations --------------------------------------------------------
    @transaction.atomic
    def rename(self, actor: User, file: FileAsset, new_name: str) -> FileAsset:
        name = validate_node_name(new_name, kind="file")

        # Renaming must not change the file type - the stored blob and the
        # client's preview mode both depend on the extension.
        new_extension = name.rpartition(".")[2].lower() if "." in name else ""
        if new_extension != file.extension:
            name = f"{name.rpartition('.')[0] or name}.{file.extension}"

        if self.repo.name_taken(file.folder, name, exclude_id=file.pk):
            raise ConflictError(
                detail=f"A file named '{name}' already exists in this folder.",
                code=ErrorCode.FILE_NAME_CONFLICT,
            )

        file.name = name
        file.updated_by = actor
        file.save(update_fields=["name", "updated_by", "updated_at"])
        self._reindex(file)
        return file

    @transaction.atomic
    def move(self, actor: User, file: FileAsset, target_folder_id: Any) -> FileAsset:
        target = self._resolve_folder(target_folder_id)
        if target.pk == file.folder_id:
            return file

        source = file.folder
        file.folder = target
        file.name = self._unique_name(target, file.name, exclude_id=file.pk)
        file.updated_by = actor
        file.save(update_fields=["folder", "name", "updated_by", "updated_at"])

        self.folders.recount(source)
        self.folders.recount(target)
        return file

    @transaction.atomic
    def copy(self, actor: User, file: FileAsset, target_folder_id: Any) -> FileAsset:
        """Duplicate the metadata row, pointing at the same stored object.

        The bytes are *not* re-uploaded: two rows sharing a ``public_id`` is
        cheap and correct as long as deletion accounts for it, which
        :meth:`purge` does by checking for other references first.
        """
        target = self._resolve_folder(target_folder_id)

        copy = FileAsset.objects.create(
            folder=target,
            name=self._unique_name(target, file.name),
            description=file.description,
            extension=file.extension,
            mime_type=file.mime_type,
            category=file.category,
            size_bytes=file.size_bytes,
            public_id=file.public_id,
            resource_type=file.resource_type,
            secure_url=file.secure_url,
            thumbnail_url=file.thumbnail_url,
            checksum=file.checksum,
            width=file.width,
            height=file.height,
            duration_seconds=file.duration_seconds,
            tags=list(file.tags),
            created_by=actor,
            updated_by=actor,
        )
        self.folders.recount(target)
        self._reindex(copy)
        return copy

    @transaction.atomic
    def update_metadata(self, actor: User, file: FileAsset, payload: dict[str, Any]) -> FileAsset:
        if "description" in payload:
            file.description = (payload["description"] or "").strip()
        if "tags" in payload:
            file.tags = validate_tags(payload["tags"])
        file.updated_by = actor
        file.save(update_fields=["description", "tags", "updated_by", "updated_at"])
        self._reindex(file)
        return file

    @transaction.atomic
    def delete(self, actor: User, file: FileAsset) -> FileAsset:
        """Recycle-bin delete. The blob is untouched until the purge job runs."""
        folder = file.folder
        file.delete(deleted_by=actor)
        self.folders.recount(folder)
        logger.info("file_deleted", extra={"file_id": str(file.pk), "actor_id": str(actor.pk)})
        return file

    @transaction.atomic
    def restore(self, actor: User, file: FileAsset) -> FileAsset:
        folder = Folder.all_objects.filter(pk=file.folder_id).first()
        if folder is None or folder.is_deleted:
            raise ConflictError(
                detail="Restore the containing folder first.",
                details={"folder_id": str(file.folder_id)},
            )

        file.name = self._unique_name(folder, file.name, exclude_id=file.pk)
        file.restore()
        file.save(update_fields=["name", "updated_at"])
        self.folders.recount(folder)
        return file

    def purge(self, actor: User, file: FileAsset) -> None:
        """Permanently destroy a file and its stored bytes. Irreversible.

        Restricted to the owner: unlike every other delete in the product, this
        one cannot be undone.
        """
        if not actor.is_owner:
            raise PermissionDeniedError(
                detail="Only the account owner can permanently delete files."
            )

        # A copy shares the blob; only remove it when nothing else points there.
        others = FileAsset.all_objects.filter(public_id=file.public_id).exclude(pk=file.pk).exists()
        if not others:
            self.storage.delete(file.public_id, file.resource_type)
        for version in self.versions.for_file(file):
            self.storage.delete(version.public_id, version.resource_type)

        folder_id = file.folder_id
        file.hard_delete()

        folder = Folder.all_objects.filter(pk=folder_id).first()
        if folder is not None and not folder.is_deleted:
            self.folders.recount(folder)
        logger.warning("file_purged", extra={"file_id": str(file.pk), "actor_id": str(actor.pk)})

    # -- delivery ---------------------------------------------------------
    def _delivery_payload(self, file: FileAsset, url: str) -> dict[str, Any]:
        """The response body shared by preview and download.

        Both carry the full identity of the file, because the client must never
        have to infer it from the URL. The signed URL contains an opaque
        public_id with no filename in it, and for raw files Cloudinary's
        download endpoint answers ``Content-Type: application/octet-stream``
        regardless of what the file is - it has no format recorded for raw
        assets. So the type has to come from here, where it is known.
        """
        return {
            "url": url,
            "file_name": file.name,
            # Kept alongside file_name so an existing client reading `name`
            # does not break on this change.
            "name": file.name,
            "extension": file.extension,
            "mime_type": file.mime_type or guess_mime_type(file.name),
            "size_bytes": file.size_bytes,
            "thumbnail_url": file.thumbnail_url
            or self.storage.thumbnail_url(file.public_id, file.resource_type),
            "expires_in_seconds": settings.CLOUDINARY["SIGNED_URL_TTL_SECONDS"],
            "is_previewable": file.is_previewable,
        }

    def download_url(
        self, actor: User, file: FileAsset, as_attachment: bool = True
    ) -> dict[str, Any]:
        url = self.storage.signed_url(
            file.public_id,
            file.resource_type,
            attachment_name=file.name if as_attachment else "",
        )
        self.repo.register_download(file)
        self.repo.touch_recent(actor, file)
        return self._delivery_payload(file, url)

    def preview_url(self, actor: User, file: FileAsset) -> dict[str, Any]:
        """Inline URL - no ``attachment`` flag, so the client renders it."""
        self.repo.touch_recent(actor, file)
        url = self.storage.signed_url(file.public_id, file.resource_type)
        return self._delivery_payload(file, url)

    # -- search index -----------------------------------------------------
    @staticmethod
    def _reindex(file: FileAsset) -> None:
        from apps.files.search import index_file

        index_file(file)


class ShareService:
    """External share links."""

    def __init__(
        self,
        repository: ShareLinkRepository | None = None,
        storage: StorageBackend | None = None,
    ) -> None:
        self.repo = repository or ShareLinkRepository()
        self._storage = storage

    @property
    def config(self) -> dict:
        return settings.STORAGE_SETTINGS

    @property
    def storage(self) -> StorageBackend:
        if self._storage is None:
            self._storage = get_storage_backend()
        return self._storage

    def create_for_file(
        self,
        actor: User,
        file: FileAsset,
        *,
        expires_in_hours: int | None = None,
        max_downloads: int | None = None,
        note: str = "",
    ) -> ShareLink:
        # No expiry unless one is asked for. The link then stays valid until it
        # is revoked, hits its download cap, or the file is deleted - so
        # revoking is the way to withdraw it, and the share-links screen is
        # where you go to do that.
        expires_at = None
        if expires_in_hours:
            expires_at = timezone.now() + timedelta(hours=max(1, expires_in_hours))

        link = ShareLink.objects.create(
            file=file,
            expires_at=expires_at,
            max_downloads=max_downloads,
            recipient_note=note[:255],
            created_by=actor,
            updated_by=actor,
        )
        logger.info(
            "share_link_created",
            extra={"file_id": str(file.pk), "link_id": str(link.pk), "actor_id": str(actor.pk)},
        )
        return link

    def resolve(self, token: str) -> tuple[ShareLink, dict[str, Any]]:
        """Redeem a token. Called by an unauthenticated public endpoint."""
        link = self.repo.get_by_token(token)
        if link is None:
            raise ResourceNotFound(detail="This link is not valid.")
        if not link.is_usable:
            raise ValidationFailed(
                detail="This link has expired or is no longer available.",
                code=ErrorCode.SHARE_LINK_EXPIRED,
                status_code=410,
            )

        target = link.file
        if target is None or target.is_deleted:
            raise ResourceNotFound(detail="The shared file is no longer available.")

        link.register_access()
        return link, {
            "name": target.name,
            "size_bytes": target.size_bytes,
            "mime_type": target.mime_type,
            "url": self.storage.signed_url(
                target.public_id, target.resource_type, attachment_name=target.name
            ),
            "expires_in_seconds": settings.CLOUDINARY["SIGNED_URL_TTL_SECONDS"],
        }

    def revoke(self, actor: User, link: ShareLink) -> ShareLink:
        if link.created_by_id != actor.pk and not actor.is_owner:
            raise PermissionDeniedError(detail="You can only revoke links you created.")
        link.revoke()
        return link


class FileFavoriteService:
    def __init__(self, repository: FileFavoriteRepository | None = None) -> None:
        self.repo = repository or FileFavoriteRepository()

    def toggle(self, user: User, file: FileAsset) -> bool:
        return self.repo.toggle(user, file)

    def list_for(self, user: User):
        return self.repo.for_user(user)
