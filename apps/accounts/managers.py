"""User manager.

There is no self-registration and no user-management API.  Accounts are created
from the server with ``manage.py create_user``, which is the only creation path
in the product.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth.models import BaseUserManager

from apps.core.validators import normalize_phone_number

if TYPE_CHECKING:  # pragma: no cover
    from apps.accounts.models import User


class UserManager(BaseUserManager):
    """Creates users with a normalised phone number and no usable password."""

    use_in_migrations = True

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

    def create_user(self, phone_number: str, full_name: str = "", **extra: Any) -> User:
        from apps.accounts.constants import UserRole

        phone = normalize_phone_number(phone_number)
        extra.setdefault("role", UserRole.USER)

        user = self.model(phone_number=phone, full_name=full_name.strip(), **extra)
        # OTP is the only credential. A usable password would be a second,
        # unmonitored way in.
        user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number: str, full_name: str = "", **extra: Any) -> User:
        from apps.accounts.constants import UserRole

        extra["role"] = UserRole.ADMIN
        extra.setdefault("is_active", True)
        return self.create_user(phone_number=phone_number, full_name=full_name, **extra)

    def get_by_natural_key(self, username: str):
        """Look up by phone number, normalising it first.

        This is what the admin login form calls. Without the normalisation the
        owner would have to type the stored E.164 form exactly - `+919876543210`
        - and `9876543210` would fail with "please enter the correct phone
        number", which reads like a wrong password.
        """
        try:
            phone = normalize_phone_number(username)
        except Exception:
            phone = username
        return self.get(**{self.model.USERNAME_FIELD: phone})

    def active(self):
        return self.get_queryset().filter(is_active=True)

    def by_phone(self, phone_number: str) -> User | None:
        return self.get_queryset().filter(phone_number=normalize_phone_number(phone_number)).first()
