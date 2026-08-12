"""Files: upload, versions, move/copy, recycle bin, sharing and search."""

from __future__ import annotations

import io

import pytest
from django.utils import timezone

from apps.core.exceptions import (
    ConflictError,
    PermissionDeniedError,
    ResourceNotFound,
    ValidationFailed,
)
from apps.files.models import FileAsset, FileVersion, ShareLink
from apps.files.services import ShareService
from apps.files.storage import InMemoryStorageBackend

pytestmark = pytest.mark.django_db


def upload(service, user, folder, name="doc.pdf", content=b"hello world"):
    return service.upload(
        user,
        folder_id=folder.pk,
        file_obj=io.BytesIO(content),
        filename=name,
        size_bytes=len(content),
        content_type="application/pdf",
    )


class TestUpload:
    def test_metadata_is_recorded(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder, "Price List.pdf")

        assert file.name == "Price List.pdf"
        assert file.extension == "pdf"
        assert file.category == "document"
        assert file.checksum
        assert file.created_by == staff

    def test_category_counters_are_updated(self, file_service, staff, child_folder):
        upload(file_service, staff, child_folder)
        child_folder.refresh_from_db()

        assert child_folder.file_count == 1
        assert child_folder.total_size_bytes > 0

    def test_duplicate_name_is_auto_numbered(self, file_service, staff, child_folder):
        first = upload(file_service, staff, child_folder, "report.pdf")
        second = upload(file_service, staff, child_folder, "report.pdf")

        # Matches what a desktop file manager does, rather than failing an
        # upload the user already waited for.
        assert first.name == "report.pdf"
        assert second.name == "report (1).pdf"

    @pytest.mark.parametrize("filename", ["virus.exe", "run.bat", "shell.sh"])
    def test_executables_are_refused(self, file_service, staff, child_folder, filename):
        with pytest.raises(ValidationFailed):
            upload(file_service, staff, child_folder, filename)

    def test_oversize_upload_is_refused(self, file_service, staff, child_folder, settings):
        settings.STORAGE_SETTINGS = {**settings.STORAGE_SETTINGS, "MAX_UPLOAD_BYTES": 10}
        with pytest.raises(ValidationFailed):
            upload(file_service, staff, child_folder, "big.pdf", b"x" * 100)

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("photo.jpg", "image"),
            ("clip.mp4", "video"),
            ("sheet.xlsx", "spreadsheet"),
            ("deck.pptx", "presentation"),
            ("notes.pdf", "document"),
        ],
    )
    def test_files_are_categorised_for_the_app_icon(
        self, file_service, staff, child_folder, filename, expected
    ):
        assert upload(file_service, staff, child_folder, filename).category == expected


class TestVersioning:
    def test_new_version_keeps_the_old_one(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder, "spec.pdf", b"v1 content")
        original_public_id = file.public_id

        file = file_service.upload_new_version(
            staff,
            file,
            file_obj=io.BytesIO(b"v2 content updated"),
            filename="spec.pdf",
            size_bytes=18,
            note="Corrected pricing",
        )

        assert file.version_number == 2
        # The old bytes must survive - that is the point of versioning.
        assert FileVersion.objects.get(file=file, version_number=1).public_id == original_public_id
        assert file.public_id != original_public_id

    def test_version_must_keep_the_same_file_type(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder, "spec.pdf")

        with pytest.raises(ValidationFailed):
            file_service.upload_new_version(
                staff, file, file_obj=io.BytesIO(b"img"), filename="spec.jpg", size_bytes=3
            )

    def test_old_versions_are_pruned(self, file_service, staff, child_folder, settings):
        settings.STORAGE_SETTINGS = {**settings.STORAGE_SETTINGS, "MAX_FILE_VERSIONS": 2}
        file = upload(file_service, staff, child_folder, "spec.pdf")

        for index in range(4):
            file = file_service.upload_new_version(
                staff,
                file,
                file_obj=io.BytesIO(f"v{index}".encode()),
                filename="spec.pdf",
                size_bytes=2,
            )

        # Storage is billed per gigabyte, so history has to stay bounded.
        assert FileVersion.objects.filter(file=file).count() <= 2


class TestRenameMoveCopy:
    def test_rename_keeps_the_extension(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder, "old.pdf")
        assert file_service.rename(staff, file, "new-name.pdf").name == "new-name.pdf"

    def test_rename_cannot_change_the_file_type(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder, "doc.pdf")
        # The stored bytes are a PDF; the name must not claim otherwise.
        assert file_service.rename(staff, file, "doc.exe").name.endswith(".pdf")

    def test_move_updates_both_category_counters(
        self, file_service, staff, root_folder, child_folder
    ):
        file = upload(file_service, staff, child_folder)
        file_service.move(staff, file, root_folder.pk)

        child_folder.refresh_from_db()
        root_folder.refresh_from_db()
        assert child_folder.file_count == 0
        assert root_folder.file_count == 1

    def test_copy_reuses_the_stored_object(self, file_service, staff, root_folder, child_folder):
        file = upload(file_service, staff, child_folder)
        copy = file_service.copy(staff, file, root_folder.pk)

        assert copy.pk != file.pk
        # Re-uploading identical bytes would double the storage bill.
        assert copy.public_id == file.public_id


class TestRecycleBin:
    def test_delete_is_reversible(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        file_service.delete(staff, file)

        assert not FileAsset.objects.filter(pk=file.pk).exists()
        assert FileAsset.all_objects.filter(pk=file.pk, is_deleted=True).exists()

    def test_restore_brings_it_back(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        file_service.delete(staff, file)

        restored = file_service.restore(staff, FileAsset.all_objects.get(pk=file.pk))
        assert FileAsset.objects.filter(pk=restored.pk).exists()

    def test_restore_is_blocked_while_the_category_is_deleted(
        self, file_service, staff, owner, child_folder
    ):
        from apps.folders.services import FolderService

        file = upload(file_service, staff, child_folder)
        FolderService().delete(owner, child_folder)

        with pytest.raises(ConflictError):
            file_service.restore(staff, FileAsset.all_objects.get(pk=file.pk))

    def test_purge_is_owner_only(self, file_service, staff, owner, child_folder):
        file = upload(file_service, staff, child_folder)
        file_service.delete(staff, file)
        deleted = FileAsset.all_objects.get(pk=file.pk)

        with pytest.raises(PermissionDeniedError):
            file_service.purge(staff, deleted)

        file_service.purge(owner, deleted)
        assert not FileAsset.all_objects.filter(pk=file.pk).exists()

    def test_purge_keeps_bytes_a_copy_still_needs(
        self, file_service, owner, root_folder, child_folder
    ):
        file = upload(file_service, owner, child_folder)
        copy = file_service.copy(owner, file, root_folder.pk)

        file_service.delete(owner, file)
        file_service.purge(owner, FileAsset.all_objects.get(pk=file.pk))

        # Removing the shared object would have broken the surviving copy.
        assert copy.public_id in InMemoryStorageBackend.store


class TestSharing:
    def test_link_resolves_to_a_download_url(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        share = ShareService(storage=InMemoryStorageBackend())

        link = share.create_for_file(staff, file, expires_in_hours=24)
        _, payload = share.resolve(link.token)

        assert payload["name"] == file.name
        assert payload["url"]

    def test_links_never_expire_by_default(self, file_service, staff, child_folder):
        """Omitting an expiry means the link keeps working until it is revoked."""
        file = upload(file_service, staff, child_folder)
        share = ShareService(storage=InMemoryStorageBackend())

        link = share.create_for_file(staff, file)

        assert link.expires_at is None
        assert link.is_expired is False
        assert link.is_usable is True

        # Still resolvable a long time later - nothing lapses on its own.
        _, payload = share.resolve(link.token)
        assert payload["url"]

    def test_a_link_with_no_expiry_survives_the_sweep(self, file_service, staff, child_folder):
        """The nightly job must not revoke links that were never given a date."""
        from apps.files.repositories import ShareLinkRepository

        file = upload(file_service, staff, child_folder)
        share = ShareService(storage=InMemoryStorageBackend())
        forever = share.create_for_file(staff, file)
        lapsing = share.create_for_file(staff, file, expires_in_hours=1)

        ShareLink.objects.filter(pk=lapsing.pk).update(
            expires_at=timezone.now() - timezone.timedelta(hours=2)
        )
        swept = ShareLinkRepository().expire_stale()

        forever.refresh_from_db()
        lapsing.refresh_from_db()
        assert swept == 1
        assert forever.revoked_at is None
        assert lapsing.revoked_at is not None

    def test_an_explicit_expiry_is_still_honoured(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        share = ShareService(storage=InMemoryStorageBackend())

        link = share.create_for_file(staff, file, expires_in_hours=24)
        assert link.expires_at is not None

    def test_expired_link_is_refused(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        share = ShareService(storage=InMemoryStorageBackend())
        link = share.create_for_file(staff, file)

        ShareLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timezone.timedelta(hours=1)
        )

        with pytest.raises(ValidationFailed) as exc:
            share.resolve(link.token)
        assert exc.value.status_code == 410

    def test_revoked_link_is_refused(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        share = ShareService(storage=InMemoryStorageBackend())
        link = share.create_for_file(staff, file)

        share.revoke(staff, link)
        with pytest.raises(ValidationFailed):
            share.resolve(link.token)

    def test_download_cap_is_enforced(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        share = ShareService(storage=InMemoryStorageBackend())
        link = share.create_for_file(staff, file, max_downloads=1)

        share.resolve(link.token)
        with pytest.raises(ValidationFailed):
            share.resolve(link.token)

    def test_token_is_hard_to_guess(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        link = ShareService(storage=InMemoryStorageBackend()).create_for_file(staff, file)
        # The token is the only thing protecting the file.
        assert len(link.token) >= 40

    def test_deleting_the_file_kills_the_link(self, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        share = ShareService(storage=InMemoryStorageBackend())
        link = share.create_for_file(staff, file)

        file_service.delete(staff, file)
        # Otherwise "delete" would not actually stop distribution.
        with pytest.raises(ResourceNotFound):
            share.resolve(link.token)

    def test_public_endpoint_needs_no_login(self, api_client, file_service, staff, child_folder):
        file = upload(file_service, staff, child_folder)
        link = ShareService(storage=InMemoryStorageBackend()).create_for_file(staff, file)

        response = api_client.get(f"/api/v1/share/{link.token}/")
        assert response.status_code == 200
        assert response.data["data"]["name"] == file.name


class TestFavoritesAndRecent:
    def test_favorite_toggles(self, file_service, staff, child_folder):
        from apps.files.services import FileFavoriteService

        file = upload(file_service, staff, child_folder)
        favorites = FileFavoriteService()

        assert favorites.toggle(staff, file) is True
        assert favorites.toggle(staff, file) is False

    def test_download_updates_recent_and_the_counter(self, file_service, staff, child_folder):
        from apps.files.repositories import FileRepository

        file = upload(file_service, staff, child_folder)
        file_service.download_url(staff, file)

        file.refresh_from_db()
        assert file.download_count == 1
        assert FileRepository().recent_for(staff).count() == 1


class TestSearch:
    @pytest.fixture
    def library(self, file_service, staff, child_folder, root_folder):
        def make(name, folder, tags=None, description=""):
            return file_service.upload(
                staff,
                folder_id=folder.pk,
                file_obj=io.BytesIO(b"content bytes here"),
                filename=name,
                size_bytes=18,
                content_type="application/pdf",
                description=description,
                tags=tags or [],
            )

        return {
            "cooler": make(
                "Water Cooler Brochure.pdf",
                child_folder,
                tags=["brochure"],
                description="Industrial water coolers 40L and 80L",
            ),
            "atm": make("Water ATM Specification.pdf", child_folder, tags=["spec"]),
            "iso": make("ISO Certificate.pdf", root_folder, tags=["certificate"]),
        }

    def test_matches_on_name(self, library):
        from apps.files.search import search_files

        assert library["cooler"] in list(search_files("Brochure"))

    def test_matches_on_description(self, library):
        from apps.files.search import search_files

        assert library["cooler"] in list(search_files("industrial"))

    def test_matches_on_tag(self, library):
        from apps.files.search import search_files

        assert library["iso"] in list(search_files("certificate"))

    def test_stemming_matches_plurals(self, library):
        from apps.files.search import search_files

        assert library["cooler"] in list(search_files("coolers"))

    def test_a_typo_still_finds_the_file(self, library):
        """Search has to be forgiving - people type fast on a phone."""
        from apps.files.search import search_files

        assert library["cooler"] in list(search_files("Brochre"))

    def test_irrelevant_term_finds_nothing(self, library):
        from apps.files.search import search_files

        assert not list(search_files("helicopter"))

    def test_search_within_one_category_covers_its_subtree(self, library, root_folder):
        from apps.files.search import search_files

        results = list(search_files("", {"folder_id": root_folder.pk}))
        assert len(results) == 3

    def test_search_endpoint(self, staff_client, library):
        response = staff_client.get("/api/v1/search/?q=Brochure")
        assert response.status_code == 200
        assert response.data["data"]

    def test_search_endpoint_needs_a_login(self, api_client):
        assert api_client.get("/api/v1/search/?q=x").status_code == 401
