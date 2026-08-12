from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.accounts.serializers import UserSummarySerializer
from apps.files.models import FileAsset, FileVersion, ShareLink


class FileSerializer(serializers.ModelSerializer):
    created_by = UserSummarySerializer(read_only=True)
    folder_id = serializers.UUIDField(read_only=True)
    folder_name = serializers.CharField(source="folder.name", read_only=True)
    size_display = serializers.CharField(read_only=True)
    is_favorite = serializers.SerializerMethodField()

    class Meta:
        model = FileAsset
        fields = (
            "id",
            "name",
            "description",
            "folder_id",
            "folder_name",
            "extension",
            "mime_type",
            "category",
            "size_bytes",
            "size_display",
            "thumbnail_url",
            "width",
            "height",
            "duration_seconds",
            "tags",
            "version_number",
            "download_count",
            "checksum",
            "is_favorite",
            "is_previewable",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_is_favorite(self, obj: FileAsset) -> bool:
        return str(obj.pk) in self.context.get("favorite_file_ids", set())


class FileUploadSerializer(serializers.Serializer):
    """Multipart upload through the API server."""

    folder_id = serializers.UUIDField()
    file = serializers.FileField()
    description = serializers.CharField(
        max_length=2000, required=False, allow_blank=True, default=""
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False, default=list
    )


class UploadSignatureRequestSerializer(serializers.Serializer):
    folder_id = serializers.UUIDField()
    filename = serializers.CharField(max_length=255)


class DirectUploadCallbackSerializer(serializers.Serializer):
    """Reported by the client after a successful direct-to-Cloudinary upload."""

    folder_id = serializers.UUIDField()
    filename = serializers.CharField(max_length=255)
    public_id = serializers.CharField(max_length=512)
    secure_url = serializers.URLField(max_length=1024)
    size_bytes = serializers.IntegerField(min_value=1)
    resource_type = serializers.CharField(max_length=20, default="raw")
    checksum = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    content_type = serializers.CharField(
        max_length=120, required=False, allow_blank=True, default=""
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False, default=list
    )


class FileVersionUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class FileUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    tags = serializers.ListField(child=serializers.CharField(max_length=50), required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class FileMoveSerializer(serializers.Serializer):
    folder_id = serializers.UUIDField()


class FileVersionSerializer(serializers.ModelSerializer):
    created_by = UserSummarySerializer(read_only=True)

    class Meta:
        model = FileVersion
        fields = (
            "id",
            "version_number",
            "size_bytes",
            "checksum",
            "note",
            "created_by",
            "created_at",
        )
        read_only_fields = fields


class ShareLinkCreateSerializer(serializers.Serializer):
    expires_in_hours = serializers.IntegerField(
        required=False,
        min_value=1,
        allow_null=True,
        help_text=(
            "Optional. Omit it and the link never expires - it then stops "
            "working only when you revoke it, when the download cap is "
            "reached, or when the file is deleted."
        ),
    )
    max_downloads = serializers.IntegerField(
        required=False,
        min_value=1,
        allow_null=True,
        help_text="Optional. Omit for unlimited downloads.",
    )
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class ShareLinkSerializer(serializers.ModelSerializer):
    created_by = UserSummarySerializer(read_only=True)
    share_url = serializers.SerializerMethodField()
    is_usable = serializers.BooleanField(read_only=True)

    class Meta:
        model = ShareLink
        fields = (
            "id",
            "token",
            "share_url",
            "expires_at",
            "max_downloads",
            "download_count",
            "revoked_at",
            "recipient_note",
            "is_usable",
            "last_accessed_at",
            "created_by",
            "created_at",
        )
        read_only_fields = fields

    def get_share_url(self, obj: ShareLink) -> str:
        request = self.context.get("request")
        path = f"/api/v1/share/{obj.token}/"
        return request.build_absolute_uri(path) if request else path


class SignedURLSerializer(serializers.Serializer):
    url = serializers.URLField()
    expires_in_seconds = serializers.IntegerField()
    name = serializers.CharField(required=False)
    size_bytes = serializers.IntegerField(required=False)
    mime_type = serializers.CharField(required=False)
