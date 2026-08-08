from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.accounts.serializers import UserSummarySerializer
from apps.folders.models import Folder


class FolderSerializer(serializers.ModelSerializer):
    """Full representation of one folder."""

    created_by = UserSummarySerializer(read_only=True)
    parent_id = serializers.UUIDField(read_only=True, allow_null=True)
    is_favorite = serializers.SerializerMethodField()
    has_children = serializers.SerializerMethodField()

    class Meta:
        model = Folder
        fields = (
            "id",
            "name",
            "parent_id",
            "depth",
            "description",
            "icon",
            "color",
            "position",
            "is_system",
            "is_pinned",
            "is_favorite",
            "has_children",
            "file_count",
            "subfolder_count",
            "total_size_bytes",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_is_favorite(self, obj: Folder) -> bool:
        # Populated by the view via a prefetch, so this stays O(1) per row
        # instead of one query each.
        favorites: set[str] = self.context.get("favorite_folder_ids", set())
        return str(obj.pk) in favorites

    def get_has_children(self, obj: Folder) -> bool:
        return obj.subfolder_count > 0 or obj.file_count > 0


class FolderCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    description = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, default=""
    )
    icon = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    color = serializers.CharField(max_length=16, required=False, allow_blank=True, default="")
    is_pinned = serializers.BooleanField(required=False, default=False)


class FolderUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    icon = serializers.CharField(max_length=60, required=False, allow_blank=True)
    color = serializers.CharField(max_length=16, required=False, allow_blank=True)
    is_pinned = serializers.BooleanField(required=False)
    position = serializers.IntegerField(required=False, min_value=0)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class FolderMoveSerializer(serializers.Serializer):
    parent_id = serializers.UUIDField(
        required=False, allow_null=True, help_text="Omit or null to move to the top level."
    )


class BreadcrumbItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    depth = serializers.IntegerField()


class FolderTreeNodeSerializer(serializers.Serializer):
    """Recursive tree node. Declared for the OpenAPI schema; the service
    already returns plain dictionaries in this shape."""

    id = serializers.CharField()
    name = serializers.CharField()
    parent_id = serializers.CharField(allow_null=True)
    depth = serializers.IntegerField()
    icon = serializers.CharField()
    color = serializers.CharField()
    is_system = serializers.BooleanField()
    is_pinned = serializers.BooleanField()
    file_count = serializers.IntegerField()
    subfolder_count = serializers.IntegerField()
    children = serializers.ListField(child=serializers.DictField())


class FolderStatisticsSerializer(serializers.Serializer):
    subfolder_count = serializers.IntegerField()
    direct_subfolder_count = serializers.IntegerField()
    file_count = serializers.IntegerField()
    total_size_bytes = serializers.IntegerField()
    depth = serializers.IntegerField()
