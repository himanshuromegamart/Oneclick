"""Admin screens for accounts.

This is the owner's console. On a hosting plan without shell access it is the
only way to create a user, reset a password or disable someone, so it favours
being obvious over being clever.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.html import format_html

from apps.accounts.models import Device, OTPRequest, User
from apps.core.validators import normalize_phone_number


class UserAdminForm(forms.ModelForm):
    """Create/edit form with an optional password field.

    ``password`` is deliberately not the raw model field: showing the stored
    hash in an editable box invites someone to paste a plain-text password into
    it, which would save an unusable credential and lock the account out.
    """

    new_password = forms.CharField(
        label="Set password",
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text=(
            "Leave blank to keep the current password. " "The user can also sign in with an OTP."
        ),
    )

    class Meta:
        model = User
        fields = ("phone_number", "full_name", "email", "role", "is_active")

    def clean_phone_number(self) -> str:
        # Accept whatever the owner types; store the one canonical form.
        return normalize_phone_number(self.cleaned_data["phone_number"])

    def clean_new_password(self) -> str:
        password = self.cleaned_data.get("new_password", "")
        if password:
            try:
                validate_password(password)
            except DjangoValidationError as exc:
                raise forms.ValidationError(list(exc.messages)) from exc
        return password

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        password = self.cleaned_data.get("new_password")

        if password:
            user.set_password(password)
        elif not user.password:
            # No password yet: mark it explicitly unusable so OTP is the only
            # way in. That is the product default, not a broken state.
            #
            # Note the test is `not user.password`, not `not user.pk`. The pk is
            # a UUID with a default, so a brand-new instance already has one and
            # `not user.pk` is never true. Leaving the field as "" would make
            # has_usable_password() report True - the app would then offer a
            # password form that could never succeed.
            user.set_unusable_password()

        if commit:
            user.save()
        return user


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    form = UserAdminForm
    change_password_form = AdminPasswordChangeForm

    list_display = (
        "full_name",
        "phone_number",
        "role",
        "is_active",
        "sign_in_methods",
        "last_login_at",
    )
    list_filter = ("role", "is_active", "is_deleted")
    search_fields = ("full_name", "phone_number", "email")
    ordering = ("full_name",)
    readonly_fields = ("id", "last_login_at", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("phone_number", "full_name", "email")}),
        ("Access", {"fields": ("role", "is_active", "new_password")}),
        (
            "Record",
            {
                "fields": ("id", "last_login_at", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Sign-in")
    def sign_in_methods(self, obj: User) -> str:
        methods = ["OTP"]
        if obj.has_usable_password():
            methods.insert(0, "Password")
        return " + ".join(methods)

    def get_queryset(self, request):
        # Include soft-deleted users; the admin is where you go to find them.
        return User.all_objects.all()

    def delete_model(self, request, obj: User) -> None:
        """Soft delete, so the person's uploads keep their author.

        A hard delete would either cascade the files away or orphan them.
        """
        obj.soft_delete()
        messages.info(
            request,
            f"{obj.full_name} was disabled and hidden rather than erased, "
            "so the documents they uploaded keep their history.",
        )

    def delete_queryset(self, request, queryset) -> None:
        for user in queryset:
            user.soft_delete()


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "model_name", "app_version", "is_active", "last_seen_at")
    list_filter = ("platform", "is_active")
    search_fields = ("user__full_name", "user__phone_number", "device_id", "model_name")
    readonly_fields = ("id", "device_id", "login_count", "last_seen_at", "last_ip", "created_at")
    ordering = ("-last_seen_at",)

    def has_add_permission(self, request) -> bool:
        # Devices are recorded at login; typing one in by hand means nothing.
        return False


@admin.register(OTPRequest)
class OTPRequestAdmin(admin.ModelAdmin):
    """Read-only, for diagnosing "the code never arrived" reports.

    The code itself is stored hashed and is not shown - and could not be, which
    is the point.
    """

    list_display = ("phone_number", "delivered", "attempts", "created_at", "expires_at", "state")
    list_filter = ("delivered",)
    search_fields = ("phone_number",)
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "phone_number",
        "user",
        "expires_at",
        "consumed_at",
        "attempts",
        "max_attempts",
        "ip_address",
        "delivered",
        "delivery_reference",
        "created_at",
    )
    exclude = ("code_hash",)

    @admin.display(description="State")
    def state(self, obj: OTPRequest) -> str:
        if obj.consumed_at:
            return format_html('<span style="color:#0a7">used</span>')
        if obj.is_expired:
            return format_html('<span style="color:#999">expired</span>')
        return format_html('<span style="color:#c60">live</span>')

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


admin.site.site_header = "Sarah Aqua Soft"
admin.site.site_title = "Sarah Aqua Soft"
admin.site.index_title = "Manage users, categories and documents"
