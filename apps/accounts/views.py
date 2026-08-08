"""Login and profile API.

Five endpoints, no user administration: accounts are created on the server with
``manage.py create_user``.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import asdict

from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import client_ip, invalidate_auth_cache
from apps.accounts.models import User
from apps.accounts.permissions import IsActiveUser
from apps.accounts.repositories import DeviceRepository, UserRepository
from apps.accounts.serializers import (
    AuthTokenSerializer,
    DeviceSerializer,
    LogoutSerializer,
    OTPChallengeSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    ProfileUpdateSerializer,
    SetupCreateUserSerializer,
    TokenRefreshRequestSerializer,
    UserSerializer,
)
from apps.accounts.services import AuthService, OTPService
from apps.core.exceptions import (
    ConflictError,
    PermissionDeniedError,
    ResourceNotFound,
    ValidationFailed,
)
from apps.core.logging import mask_phone
from apps.core.responses import created, ok
from apps.core.serializers import DetailSerializer
from apps.core.throttling import PhoneNumberScopedThrottle, SetupThrottle

logger = logging.getLogger(__name__)


class RequestOTPView(APIView):
    """``POST /api/v1/auth/otp/request/`` - send a login OTP."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = (PhoneNumberScopedThrottle,)
    throttle_scope = "otp_request"
    is_resend = False

    @extend_schema(
        tags=["auth"],
        request=OTPRequestSerializer,
        responses={200: OpenApiResponse(OTPChallengeSerializer, "OTP sent.")},
        summary="Request a login OTP",
        description=(
            "Sends a 6-digit code by SMS to a registered mobile number.\n\n"
            "* `403 USER_NOT_REGISTERED` - the number has no account\n"
            "* `403 USER_DISABLED` - the account is switched off\n"
            "* `429` - throttled; `details.retry_after_seconds` says how long"
        ),
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        challenge = OTPService().request_otp(
            serializer.validated_data["phone_number"],
            ip_address=client_ip(request),
            is_resend=self.is_resend,
        )
        return ok(asdict(challenge), request=request)


class ResendOTPView(RequestOTPView):
    """``POST /api/v1/auth/otp/resend/``

    Separate from the request endpoint so the resend cooldown always applies
    and the two can be throttled independently.
    """

    is_resend = True

    @extend_schema(
        tags=["auth"],
        request=OTPRequestSerializer,
        responses={200: OpenApiResponse(OTPChallengeSerializer, "OTP re-sent.")},
        summary="Resend the login OTP",
        auth=[],
    )
    def post(self, request: Request) -> Response:
        return super().post(request)


class VerifyOTPView(APIView):
    """``POST /api/v1/auth/otp/verify/`` - exchange an OTP for tokens."""

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = (PhoneNumberScopedThrottle,)
    throttle_scope = "otp_verify"

    @extend_schema(
        tags=["auth"],
        request=OTPVerifySerializer,
        responses={200: OpenApiResponse(AuthTokenSerializer, "Logged in.")},
        summary="Verify the OTP and log in",
        description=(
            "Returns `tokens`, the signed-in `user`, and the registered "
            "`device` (null when no `device_id` was supplied)."
        ),
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user, device, tokens = AuthService().login_with_otp(
            phone_number=data["phone_number"],
            code=data["otp"],
            device_id=data["device_id"],
            platform=data["platform"],
            model_name=data["model_name"],
            app_version=data["app_version"],
            ip_address=client_ip(request),
        )
        return ok(
            {
                "tokens": asdict(tokens),
                "user": UserSerializer(user).data,
                "device": DeviceSerializer(device).data if device else None,
            },
            request=request,
        )


class RefreshTokenView(APIView):
    """``POST /api/v1/auth/token/refresh/``"""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(
        tags=["auth"],
        request=TokenRefreshRequestSerializer,
        responses={200: OpenApiResponse(AuthTokenSerializer, "New token pair.")},
        summary="Refresh the access token",
        description=(
            "Refresh tokens are single-use: the one you send is blacklisted and "
            "a new pair comes back. Replaying an old one returns "
            "`401 TOKEN_INVALID`."
        ),
        auth=[],
    )
    def post(self, request: Request) -> Response:
        serializer = TokenRefreshRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tokens = AuthService().refresh_tokens(serializer.validated_data["refresh"])
        return ok(asdict(tokens), request=request)


class LogoutView(APIView):
    """``POST /api/v1/auth/logout/``"""

    permission_classes = (IsActiveUser,)

    @extend_schema(
        tags=["auth"],
        request=LogoutSerializer,
        responses={200: DetailSerializer},
        summary="Log out",
        description=(
            "Blacklists the refresh token. The access token stays technically "
            "valid until it expires, so the app must discard it too."
        ),
    )
    def post(self, request: Request) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        device_id = data.get("device_id") or request.META.get("HTTP_X_DEVICE_ID", "")
        AuthService().logout(
            request.user, refresh_token=data.get("refresh", ""), device_id=device_id
        )
        invalidate_auth_cache(request.user.pk)
        return ok({"detail": "Logged out."}, request=request)


class MeView(APIView):
    """``GET|PATCH /api/v1/auth/me/``"""

    permission_classes = (IsActiveUser,)

    @extend_schema(tags=["auth"], responses={200: UserSerializer}, summary="Current user")
    def get(self, request: Request) -> Response:
        return ok(UserSerializer(request.user).data, request=request)

    @extend_schema(
        tags=["auth"],
        request=ProfileUpdateSerializer,
        responses={200: UserSerializer},
        summary="Update your own name or email",
    )
    def patch(self, request: Request) -> Response:
        serializer = ProfileUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        user = request.user
        for field, value in serializer.validated_data.items():
            setattr(user, field, value)
        user.save(update_fields=[*serializer.validated_data.keys(), "updated_at"])
        invalidate_auth_cache(user.pk)
        return ok(UserSerializer(user).data, request=request)


class SetupCreateUserView(APIView):
    """``POST /api/v1/setup/create-user/`` - create an account without shell access.

    Exists because creating the first account normally needs a server shell,
    which some hosts put behind a paid plan. It is a deliberate hole in the
    "no self-registration" rule, so it is fenced in on four sides:

    * **Off by default.** Without ``SETUP_KEY`` in the environment the endpoint
      returns 404 and is invisible - no key, no endpoint.
    * **Constant-time key comparison**, so response timing cannot be used to
      recover the key one character at a time.
    * **Throttled to 10 attempts an hour per IP**, which is what makes even a
      short key impractical to guess remotely.
    * **Every attempt is logged**, successes and failures alike, so an attempt
      to find the key is visible in the logs.

    Unset ``SETUP_KEY`` once the accounts you need exist. Leaving it enabled
    indefinitely means anyone who learns the key can mint themselves an owner
    account.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()
    throttle_classes = (SetupThrottle,)

    @extend_schema(
        tags=["setup"],
        request=SetupCreateUserSerializer,
        responses={
            201: OpenApiResponse(UserSerializer, "Account created."),
            403: OpenApiResponse(description="Wrong setup key."),
            404: OpenApiResponse(description="Endpoint disabled (SETUP_KEY not configured)."),
            409: OpenApiResponse(description="That mobile number already has an account."),
        },
        summary="Create a user with the setup key",
        description=(
            "Creates an account without needing shell access on the server.\n\n"
            "Send the server's `SETUP_KEY` along with the account details. The "
            "new user logs in with an OTP to their mobile number - no password "
            "is involved.\n\n"
            "**Disable this once your accounts exist** by removing `SETUP_KEY` "
            "from the environment. While it is set, anyone holding the key can "
            "create an owner account."
        ),
        auth=[],
    )
    def post(self, request: Request) -> Response:
        configured_key = getattr(settings, "SETUP_KEY", "")
        client = client_ip(request)

        # Absent key means the feature is switched off. A 404 rather than a 403
        # so a disabled endpoint is indistinguishable from one that was never
        # deployed - there is nothing here to probe at.
        if not configured_key:
            logger.warning("setup_endpoint_disabled_attempt", extra={"ip": client})
            raise ResourceNotFound(detail="Not found.")

        if len(configured_key) < settings.SETUP_KEY_MIN_LENGTH:
            logger.error(
                "setup_key_too_short",
                extra={"required": settings.SETUP_KEY_MIN_LENGTH},
            )
            raise ValidationFailed(
                detail=(
                    "The server's setup key is too short to be safe. Set a "
                    f"SETUP_KEY of at least {settings.SETUP_KEY_MIN_LENGTH} characters."
                ),
                status_code=500,
            )

        serializer = SetupCreateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # compare_digest, not ==, so the time taken does not depend on how many
        # leading characters were correct.
        if not secrets.compare_digest(str(data["setup_key"]), str(configured_key)):
            logger.warning(
                "setup_key_rejected",
                extra={"ip": client, "phone": mask_phone(data["phone_number"])},
            )
            raise PermissionDeniedError(detail="Invalid setup key.")

        if UserRepository().get_by_phone(data["phone_number"]) is not None:
            raise ConflictError(detail="An account already exists for this mobile number.")

        user = User.objects.create_user(
            phone_number=data["phone_number"],
            full_name=data["full_name"],
            email=data["email"],
            role=data["role"],
        )

        logger.warning(
            "setup_user_created",
            extra={
                "ip": client,
                "user_id": str(user.pk),
                "role": user.role,
                "phone": mask_phone(user.phone_number),
            },
        )
        return created(UserSerializer(user).data, request=request)


class MyDevicesView(APIView):
    """``GET /api/v1/auth/devices/`` - where this account is signed in."""

    permission_classes = (IsActiveUser,)

    @extend_schema(
        tags=["auth"], responses={200: DeviceSerializer(many=True)}, summary="Signed-in devices"
    )
    def get(self, request: Request) -> Response:
        devices = DeviceRepository().for_user(request.user)
        return ok(DeviceSerializer(devices, many=True).data, request=request)
