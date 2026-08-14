"""Serialisers for login and profile.

Shape and format validation only - business rules live in the service layer.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
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
    # Both are constant now that the roles differ only in dashboard access; they
    # stay in the payload because the shipped mobile app already reads them.
    can_contribute = serializers.BooleanField(read_only=True)
    is_admin = serializers.BooleanField(read_only=True)
    is_owner = serializers.BooleanField(read_only=True)
    # Lets the app offer "Sign in with password" only when it would actually work.
    has_password = serializers.SerializerMethodField()

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
            "is_admin",
            "is_owner",
            "has_password",
            "is_active",
            "last_login_at",
            "created_at",
        )
        read_only_fields = fields

    def get_has_password(self, obj: User) -> bool:
        return obj.has_usable_password()


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


class PasswordField(serializers.CharField):
    """A password, run through Django's configured validators.

    Validating here means the same minimum length and common-password checks
    apply wherever a password is set - the setup endpoint, a password change,
    or the management command.
    """

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("write_only", True)
        kwargs.setdefault("style", {"input_type": "password"})
        kwargs.setdefault("trim_whitespace", False)
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> str:
        value = super().to_internal_value(data)
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value


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
        default=UserRole.ADMIN,
        help_text=(
            "admin = mobile app plus the web dashboard, user = mobile app only. "
            "Defaults to admin because this endpoint exists to create the first "
            "account, which has to be able to reach the dashboard."
        ),
    )
    password = PasswordField(
        required=False,
        allow_blank=True,
        default="",
        help_text=(
            "Optional. Set one to enable password sign-in at /auth/login/. "
            "Leave blank and the account can only sign in with an OTP."
        ),
    )

    def validate_password(self, value: str) -> str:
        # An empty string means "no password" and must skip validation, which
        # would otherwise reject it for being too short.
        return value


class PasswordLoginSerializer(serializers.Serializer):
    """Body for ``POST /auth/login/``."""

    phone_number = PhoneNumberField(max_length=20)
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    platform = serializers.ChoiceField(
        choices=Platform.choices, required=False, default=Platform.UNKNOWN
    )
    model_name = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    app_version = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")


class ChangePasswordSerializer(serializers.Serializer):
    """Body for ``POST /auth/change-password/``."""

    current_password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        default="",
        style={"input_type": "password"},
        trim_whitespace=False,
        help_text="Required if you already have a password. Omit when setting your first one.",
    )
    new_password = PasswordField()


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
