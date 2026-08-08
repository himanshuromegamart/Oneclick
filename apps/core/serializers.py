"""Small shared response serialisers.

These exist so every endpoint has a *typed* response in the OpenAPI schema.
An untyped ``responses={200: None}`` generates a bare object, which leaves the
mobile team guessing at the payload - the opposite of what the schema is for.
"""

from __future__ import annotations

from rest_framework import serializers


class DetailSerializer(serializers.Serializer):
    """A simple human-readable acknowledgement."""

    detail = serializers.CharField()


class DeleteResultSerializer(serializers.Serializer):
    """Result of a soft delete that may cascade."""

    detail = serializers.CharField()
    affected = serializers.IntegerField(
        required=False, help_text="Rows moved to the recycle bin, including descendants."
    )


class FavoriteToggleSerializer(serializers.Serializer):
    """The resulting state after starring or unstarring something."""

    is_favorite = serializers.BooleanField()


class PDFGenerationSerializer(serializers.Serializer):
    detail = serializers.CharField()
    pdf_status = serializers.CharField()


class UploadSignatureSerializer(serializers.Serializer):
    """Everything the client needs to upload directly to Cloudinary."""

    signature = serializers.CharField()
    timestamp = serializers.IntegerField()
    api_key = serializers.CharField()
    cloud_name = serializers.CharField()
    folder = serializers.CharField()
    public_id = serializers.CharField()
    resource_type = serializers.CharField()
    upload_url = serializers.URLField()
    expires_in_seconds = serializers.IntegerField()
    suggested_name = serializers.CharField()
    folder_id = serializers.CharField()


class SharedFileSerializer(serializers.Serializer):
    """Payload returned when a public share token is redeemed."""

    name = serializers.CharField()
    size_bytes = serializers.IntegerField()
    mime_type = serializers.CharField()
    url = serializers.URLField()
    expires_in_seconds = serializers.IntegerField()


class SharedQuotationSerializer(serializers.Serializer):
    quotation_number = serializers.CharField()
    customer_name = serializers.CharField()
    grand_total = serializers.CharField()
    valid_until = serializers.DateField(allow_null=True)
    url = serializers.URLField()
    expires_in_seconds = serializers.IntegerField()


class QuotationPDFDownloadSerializer(serializers.Serializer):
    url = serializers.URLField()
    expires_in_seconds = serializers.IntegerField()
    generated_at = serializers.DateTimeField(allow_null=True)


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    checks = serializers.DictField(required=False)
    service = serializers.CharField(required=False)
    version = serializers.CharField(required=False)
    components = serializers.DictField(required=False)
