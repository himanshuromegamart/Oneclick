from __future__ import annotations

from django.urls import include, path

from apps.accounts.views import (
    ChangePasswordView,
    LogoutView,
    MeView,
    MyDevicesView,
    PasswordLoginView,
    RefreshTokenView,
    RequestOTPView,
    ResendOTPView,
    SetupCreateUserView,
    VerifyOTPView,
)

app_name = "accounts"

auth_patterns = [
    # Password sign-in.
    path("login/", PasswordLoginView.as_view(), name="login"),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
    # OTP sign-in. Kept alongside password login - either issues the same tokens.
    path("otp/request/", RequestOTPView.as_view(), name="otp-request"),
    path("otp/resend/", ResendOTPView.as_view(), name="otp-resend"),
    path("otp/verify/", VerifyOTPView.as_view(), name="otp-verify"),
    path("token/refresh/", RefreshTokenView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("devices/", MyDevicesView.as_view(), name="my-devices"),
]

urlpatterns = [
    path("auth/", include((auth_patterns, "auth"))),
    # Kept off /auth/ deliberately: this is a setup tool, not part of the login
    # flow, and it should be easy to spot and remove when it is no longer needed.
    path("setup/create-user/", SetupCreateUserView.as_view(), name="setup-create-user"),
]
