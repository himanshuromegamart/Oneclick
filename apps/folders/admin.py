"""Admin screens for the category tree."""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from apps.folders.models import Folder, FolderFavorite
from apps.folders.repositories import FolderRepository
from apps.folders.services import FolderService


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = (
        "indented_name",
        "depth",
        "subfolder_count",
        "file_count",
        "created_by",
        "is_deleted",
    )
    list_filter = ("depth", "is_deleted", "is_pinned")
    search_fields = ("name",)
    ordering = ("path", "position", "name")
    readonly_fields = (
        "id",
        "path",
        "depth",
        "file_count",
        "subfolder_count",
        "total_size_bytes",
        "created_at",
        "updated_at",
    )
    autocomplete_fields = ("parent",)

    fieldsets = (
        (None, {"fields": ("name", "parent", "description")}),
        ("Appearance", {"fields": ("icon", "color", "position", "is_pinned")}),
        ("Counts", {"fields": ("subfolder_count", "file_count", "total_size_bytes")}),
        (
            "Record",
            {
                "fields": ("id", "path", "depth", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Category", ordering="path")
    def indented_name(self, obj: Folder) -> str:
        """Show the hierarchy, which a flat list otherwise hides completely."""
        return format_html("{}{}", "— " * obj.depth, obj.name)

    def get_queryset(self, request):
        return Folder.all_objects.select_related("parent", "created_by")

    def save_model(self, request, obj: Folder, form, change) -> None:
        """Route a reparent through the service, so its rules still apply.

        Saving the model directly would let the admin create a cycle - a
        category inside its own subtree - which detaches the branch from the
        root and makes it unreachable everywhere else.
        """
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user

        parent_changed = change and "parent" in form.changed_data
        if parent_changed:
            new_parent = form.cleaned_data.get("parent")
            obj.save()
            FolderService().move(request.user, obj, new_parent.pk if new_parent else None)
            return

        obj.rebuild_path(obj.parent)
        obj.save()

    def delete_model(self, request, obj: Folder) -> None:
        affected = FolderService().delete(request.user, obj)
        messages.info(
            request,
            f"Moved to the recycle bin along with everything inside it "
            f"({affected} categor{'y' if affected == 1 else 'ies'}).",
        )

    actions = ("restore_selected", "recount_selected")

    @admin.action(description="Restore from the recycle bin")
    def restore_selected(self, request, queryset) -> None:
        service = FolderService()
        restored = 0
        for folder in queryset.filter(is_deleted=True):
            try:
                service.restore(request.user, folder)
                restored += 1
            except Exception as exc:
                messages.warning(request, f"{folder.name}: {exc}")
        messages.success(request, f"Restored {restored}.")

    @admin.action(description="Recalculate file counts")
    def recount_selected(self, request, queryset) -> None:
        repo = FolderRepository()
        for folder in queryset:
            repo.recount(folder)
        messages.success(request, f"Recounted {queryset.count()}.")


@admin.register(FolderFavorite)
class FolderFavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "folder", "created_at")
    search_fields = ("user__full_name", "folder__name")
    autocomplete_fields = ("user", "folder")
