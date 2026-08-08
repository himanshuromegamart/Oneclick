from __future__ import annotations

from django.urls import include, path

from apps.accounts.views import (
    LogoutView,
    MeView,
    MyDevicesView,
    RefreshTokenView,
    RequestOTPView,
    ResendOTPView,
    VerifyOTPView,
)

app_name = "accounts"

auth_patterns = [
    path("otp/request/", RequestOTPView.as_view(), name="otp-request"),
    path("otp/resend/", ResendOTPView.as_view(), name="otp-resend"),
    path("otp/verify/", VerifyOTPView.as_view(), name="otp-verify"),
    path("token/refresh/", RefreshTokenView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("devices/", MyDevicesView.as_view(), name="my-devices"),
]

urlpatterns = [path("auth/", include((auth_patterns, "auth")))]
