from __future__ import annotations

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "Accounts & Access Control"

    def ready(self) -> None:
        # Imported for the side effect of registering the OpenAPI security
        # scheme; without it the published schema documents no authentication.
        from apps.accounts import schema  # noqa: F401
