"""Admin screens for documents and sharing."""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from apps.files.models import FileAsset, FileVersion, RecentFile, ShareLink
from apps.files.services import FileService


class FileVersionInline(admin.TabularInline):
    model = FileVersion
    extra = 0
    can_delete = False
    readonly_fields = (
        "version_number",
        "size_bytes",
        "checksum",
        "note",
        "created_by",
        "created_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None) -> bool:
        # Versions come from uploads, not from typing into a form.
        return False


@admin.register(FileAsset)
class FileAssetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "folder",
        "category",
        "size_display",
        "version_number",
        "download_count",
        "created_by",
        "is_deleted",
    )
    list_filter = ("category", "extension", "is_deleted")
    search_fields = ("name", "description", "folder__name", "created_by__full_name")
    ordering = ("-created_at",)
    autocomplete_fields = ("folder",)
    inlines = (FileVersionInline,)
    readonly_fields = (
        "id",
        "extension",
        "mime_type",
        "category",
        "size_bytes",
        "checksum",
        "public_id",
        "resource_type",
        "version_number",
        "download_count",
        "preview",
        "created_by",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (None, {"fields": ("name", "folder", "description", "tags")}),
        ("Preview", {"fields": ("preview",)}),
        (
            "Stored file",
            {
                "fields": (
                    "extension",
                    "mime_type",
                    "category",
                    "size_bytes",
                    "checksum",
                    "public_id",
                    "resource_type",
                    "version_number",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Record",
            {
                "fields": ("id", "download_count", "created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Preview")
    def preview(self, obj: FileAsset) -> str:
        if obj.thumbnail_url:
            return format_html(
                '<img src="{}" style="max-height:200px;border-radius:6px">', obj.thumbnail_url
            )
        return "No preview for this file type."

    def get_queryset(self, request):
        return FileAsset.all_objects.select_related("folder", "created_by")

    def has_add_permission(self, request) -> bool:
        """Uploading happens through the API.

        A file row typed in by hand would point at no stored object, and every
        download of it would fail.
        """
        return False

    def delete_model(self, request, obj: FileAsset) -> None:
        FileService().delete(request.user, obj)
        messages.info(request, "Moved to the recycle bin. Use 'Delete permanently' to destroy it.")

    actions = ("restore_selected", "purge_selected")

    @admin.action(description="Restore from the recycle bin")
    def restore_selected(self, request, queryset) -> None:
        service = FileService()
        restored = 0
        for file in queryset.filter(is_deleted=True):
            try:
                service.restore(request.user, file)
                restored += 1
            except Exception as exc:
                messages.warning(request, f"{file.name}: {exc}")
        messages.success(request, f"Restored {restored}.")

    @admin.action(description="Delete permanently (cannot be undone)")
    def purge_selected(self, request, queryset) -> None:
        service = FileService()
        purged = 0
        for file in queryset.filter(is_deleted=True):
            try:
                service.purge(request.user, file)
                purged += 1
            except Exception as exc:
                messages.warning(request, f"{file.name}: {exc}")
        messages.success(
            request,
            f"Permanently deleted {purged}. Only files already in the recycle bin are destroyed.",
        )


@admin.register(ShareLink)
class ShareLinkAdmin(admin.ModelAdmin):
    list_display = (
        "file",
        "state",
        "download_count",
        "max_downloads",
        "expires_at",
        "created_by",
        "created_at",
    )
    list_filter = ("revoked_at",)
    search_fields = ("file__name", "recipient_note", "created_by__full_name")
    ordering = ("-created_at",)
    readonly_fields = ("id", "token", "download_count", "last_accessed_at", "created_at")

    @admin.display(description="State")
    def state(self, obj: ShareLink) -> str:
        if obj.is_usable:
            return format_html('<span style="color:#0a7">active</span>')
        reason = "revoked" if obj.is_revoked else "expired" if obj.is_expired else "used up"
        return format_html('<span style="color:#999">{}</span>', reason)

    def has_add_permission(self, request) -> bool:
        return False

    actions = ("revoke_selected",)

    @admin.action(description="Revoke immediately")
    def revoke_selected(self, request, queryset) -> None:
        count = 0
        for link in queryset.filter(revoked_at__isnull=True):
            link.revoke()
            count += 1
        messages.success(request, f"Revoked {count}. Those links stop working at once.")


@admin.register(RecentFile)
class RecentFileAdmin(admin.ModelAdmin):
    list_display = ("user", "file", "accessed_at", "access_count")
    search_fields = ("user__full_name", "file__name")
    ordering = ("-accessed_at",)

    def has_add_permission(self, request) -> bool:
        return False
