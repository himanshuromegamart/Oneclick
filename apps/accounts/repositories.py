"""Data access for identity tables."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import Device, OTPRequest, User
from apps.core.repositories import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    def get_by_phone(self, phone_number: str) -> User | None:
        return self.get_queryset().filter(phone_number=phone_number).first()

    def get_active_by_phone(self, phone_number: str) -> User | None:
        return self.get_queryset().filter(phone_number=phone_number, is_active=True).first()


class DeviceRepository(BaseRepository[Device]):
    model = Device
    default_select_related = ("user",)

    def get_for_user(self, user: User, device_id: str) -> Device | None:
        return Device.objects.filter(user=user, device_id=device_id).first()

    def for_user(self, user: User) -> QuerySet[Device]:
        return self.get_queryset().filter(user=user)

    def register_or_touch(
        self,
        *,
        user: User,
        device_id: str,
        platform: str = "unknown",
        model_name: str = "",
        app_version: str = "",
        ip: str | None = None,
    ) -> Device | None:
        """Upsert the device row for this login.

        Returns ``None`` when the client sent no device id - device tracking is
        a convenience, not a requirement, so login still succeeds without it.
        """
        if not device_id:
            return None

        device, _ = Device.objects.get_or_create(
            user=user, device_id=device_id, defaults={"platform": platform}
        )
        device.platform = platform or device.platform
        device.model_name = model_name or device.model_name
        device.app_version = app_version or device.app_version
        device.last_ip = ip or device.last_ip
        device.last_seen_at = timezone.now()
        device.login_count += 1
        device.is_active = True
        device.save()
        return device


class OTPRepository(BaseRepository[OTPRequest]):
    model = OTPRequest

    def latest_usable(self, phone_number: str) -> OTPRequest | None:
        """The most recent OTP that can still be verified."""
        return (
            OTPRequest.objects.filter(
                phone_number=phone_number,
                consumed_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )

    def sends_in_last_day(self, phone_number: str) -> int:
        since = timezone.now() - timedelta(days=1)
        return OTPRequest.objects.filter(phone_number=phone_number, created_at__gte=since).count()

    def invalidate_outstanding(self, phone_number: str) -> int:
        """Consume every live OTP for a number.

        Called before issuing a new one so only the newest code ever works -
        otherwise each resend would widen the window an attacker can guess in.
        """
        return OTPRequest.objects.filter(
            phone_number=phone_number, consumed_at__isnull=True, expires_at__gt=timezone.now()
        ).update(consumed_at=timezone.now())

    def purge_expired(self, older_than_days: int = 7) -> int:
        cutoff = timezone.now() - timedelta(days=older_than_days)
        deleted, _ = OTPRequest.objects.filter(created_at__lt=cutoff).delete()
        return deleted
