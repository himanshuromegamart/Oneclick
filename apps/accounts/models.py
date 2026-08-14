"""Identity tables.

Two models only: the person, and the phone they logged in from.  There is no
role table, no permission table and no department table - the access rules are
a single ``role`` field, documented in :mod:`apps.accounts.constants`.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractBaseUser
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from apps.accounts.constants import Platform, UserRole
from apps.accounts.managers import UserManager
from apps.core.models import TimeStampedModel, UUIDModel

PHONE_VALIDATOR = RegexValidator(
    regex=r"^\+91[6-9]\d{9}$",
    message="Phone number must be stored as +91XXXXXXXXXX.",
)


class User(UUIDModel, TimeStampedModel, AbstractBaseUser):
    """A person who can use the app.

    Created only by ``manage.py create_user``.  Authentication is by OTP, so
    there is no usable password and no registration endpoint.
    """

    phone_number = models.CharField(
        max_length=16, unique=True, db_index=True, validators=[PHONE_VALIDATOR]
    )
    full_name = models.CharField(max_length=150)
    email = models.EmailField(blank=True, default="")

    # Defaults to the lesser role, so a caller that forgets to pass one creates
    # an account without dashboard access rather than with it.
    role = models.CharField(
        max_length=10, choices=UserRole.choices, default=UserRole.USER, db_index=True
    )

    is_active = models.BooleanField(default=True, db_index=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    # Soft delete: removing a user must not orphan the files they uploaded.
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()
    all_objects = models.Manager()

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ("full_name",)
        indexes = [models.Index(fields=["is_active", "is_deleted"])]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.phone_number})"

    # -- access rules -----------------------------------------------------
    @property
    def is_admin(self) -> bool:
        """The one thing role decides: may this person open the dashboard?

        Everything inside the mobile app is open to both roles, so this flag
        should never be used to gate app features - only the browser consoles
        and the account management that lives in them.
        """
        return self.role == UserRole.ADMIN

    @property
    def is_owner(self) -> bool:
        """Deprecated alias for :attr:`is_admin`.

        Kept because the mobile app already reads ``is_owner`` off the profile
        payload; removing it would break a shipped client to rename a word.
        """
        return self.is_admin

    # -- Django admin -----------------------------------------------------
    #
    # The admin asks the user object three questions. Answering them from the
    # `role` field means admin access needs no `is_staff`/`is_superuser`
    # columns, no PermissionsMixin, and no migration - and it can never drift
    # out of step with the dashboard's own rule, because there is only one
    # source of truth for who is privileged.
    @property
    def is_staff(self) -> bool:
        """May open the admin site at all."""
        return self.is_admin

    @property
    def is_superuser(self) -> bool:
        return self.is_admin

    def has_perm(self, perm: str, obj=None) -> bool:
        """Admins may do anything in the admin site; nobody else gets in."""
        return self.is_admin

    def has_perms(self, perm_list, obj=None) -> bool:
        return self.is_admin

    def has_module_perms(self, app_label: str) -> bool:
        return self.is_admin

    @property
    def can_contribute(self) -> bool:
        """May create categories and upload files - which is everybody.

        Retained as a constant rather than deleted because the mobile app
        reads it to decide whether to show its upload button.
        """
        return True

    def owns(self, obj) -> bool:
        """True when this user uploaded/created ``obj``."""
        return getattr(obj, "created_by_id", None) == self.pk

    def can_modify(self, obj) -> bool:
        """Anyone signed in may change anything.

        There is no "only your own files" rule: the roles differ by dashboard
        access alone. Kept as a method so restoring an ownership rule later is
        a change here rather than at every call site.
        """
        return True

    def soft_delete(self) -> None:
        self.is_deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "is_active", "deleted_at", "updated_at"])


class Device(UUIDModel, TimeStampedModel):
    """A phone that has logged in.

    Kept so the owner can see where the app is signed in and sign a lost handset
    out.  Nothing else depends on it, so a client that cannot supply a stable
    device id still works.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    device_id = models.CharField(max_length=128, db_index=True)
    platform = models.CharField(max_length=10, choices=Platform.choices, default=Platform.UNKNOWN)
    model_name = models.CharField(max_length=120, blank=True, default="")
    app_version = models.CharField(max_length=40, blank=True, default="")

    is_active = models.BooleanField(default=True, db_index=True)
    login_count = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ("-last_seen_at",)
        constraints = [
            models.UniqueConstraint(fields=["user", "device_id"], name="uniq_user_device")
        ]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self) -> str:
        return f"{self.model_name or self.platform} / {self.user.full_name}"

    def revoke(self) -> None:
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])


class OTPRequest(UUIDModel, TimeStampedModel):
    """A single issued one-time password.

    The code is stored **hashed**, so a database dump cannot be replayed into
    account access and no one with read access to the table can log in as a
    user.
    """

    phone_number = models.CharField(max_length=16, db_index=True, validators=[PHONE_VALIDATOR])
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="otp_requests"
    )
    code_hash = models.CharField(max_length=255)

    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    delivered = models.BooleanField(default=False)
    delivery_reference = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["phone_number", "-created_at"]),
            models.Index(fields=["expires_at", "consumed_at"]),
        ]

    def __str__(self) -> str:
        return f"OTP for {self.phone_number}"

    @classmethod
    def issue(
        cls,
        *,
        phone_number: str,
        raw_code: str,
        user: User | None,
        ttl_seconds: int,
        max_attempts: int,
        ip_address: str | None = None,
    ) -> OTPRequest:
        return cls.objects.create(
            phone_number=phone_number,
            user=user,
            code_hash=make_password(raw_code),
            expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
            max_attempts=max_attempts,
            ip_address=ip_address,
        )

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def attempts_remaining(self) -> int:
        return max(self.max_attempts - self.attempts, 0)

    def verify(self, raw_code: str) -> bool:
        """Check a candidate code, counting the attempt either way.

        The counter is incremented before the comparison so a crash or a race
        cannot hand an attacker a free guess.
        """
        self.attempts += 1
        self.save(update_fields=["attempts", "updated_at"])
        return check_password(raw_code, self.code_hash)

    def consume(self) -> None:
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at", "updated_at"])
