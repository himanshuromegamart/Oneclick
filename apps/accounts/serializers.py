"""Serialisers for login and profile.

Shape and format validation only - business rules live in the service layer.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.accounts.constants import Platform, UserRole
from apps.accounts.models import Device, User
from apps.core.validators import normalize_phone_number


class PhoneNumberField(serializers.CharField):
    """Accepts any common Indian format and normalises to E.164."""

    def to_internal_value(self, data: Any) -> str:
        value = super().to_internal_value(data)
        # Raises a DomainError, which the global handler renders with a stable
        # error code - clearer for the app than DRF's generic 400.
        return normalize_phone_number(value)


class OTPRequestSerializer(serializers.Serializer):
    phone_number = PhoneNumberField(max_length=20)


class OTPVerifySerializer(serializers.Serializer):
    phone_number = PhoneNumberField(max_length=20)
    otp = serializers.CharField(min_length=4, max_length=8, trim_whitespace=True)
    # Optional: login works without it, it only powers the "signed-in devices"
    # list, so an app that cannot produce a stable id is not locked out.
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    platform = serializers.ChoiceField(
        choices=Platform.choices, required=False, default=Platform.UNKNOWN
    )
    model_name = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    app_version = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")


class TokenRefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True, default="")
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")


class AuthTokenSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    access_expires_in = serializers.IntegerField()
    refresh_expires_in = serializers.IntegerField()


class OTPChallengeSerializer(serializers.Serializer):
    phone_number = serializers.CharField()
    expires_in_seconds = serializers.IntegerField()
    resend_available_in_seconds = serializers.IntegerField()
    attempts_allowed = serializers.IntegerField()


class UserSummarySerializer(serializers.ModelSerializer):
    """Compact form embedded in file and folder payloads."""

    class Meta:
        model = User
        fields = ("id", "full_name")
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    # Surfaced so the app can hide buttons the server would refuse anyway.
    can_contribute = serializers.BooleanField(read_only=True)
    is_owner = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "phone_number",
            "full_name",
            "email",
            "role",
            "role_display",
            "can_contribute",
            "is_owner",
            "is_active",
            "last_login_at",
            "created_at",
        )
        read_only_fields = fields


class ProfileUpdateSerializer(serializers.Serializer):
    """What a user may change about themselves.

    Excludes role, phone number and is_active - self-service escalation is the
    classic hole in an access model this simple.
    """

    full_name = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(required=False, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class SetupCreateUserSerializer(serializers.Serializer):
    """Body for the guarded account-bootstrap endpoint."""

    setup_key = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
        help_text="The SETUP_KEY configured on the server. Never stored or logged.",
    )
    phone_number = PhoneNumberField(
        max_length=20, help_text="Indian mobile number. Any common format is accepted."
    )
    full_name = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(
        choices=UserRole.choices,
        default=UserRole.OWNER,
        help_text="owner = full control, staff = upload and manage own files, viewer = read-only.",
    )


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = (
            "id",
            "device_id",
            "platform",
            "model_name",
            "app_version",
            "is_active",
            "login_count",
            "last_seen_at",
        )
        read_only_fields = fields
