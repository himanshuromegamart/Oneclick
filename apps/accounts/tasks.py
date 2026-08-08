"""Background jobs for the accounts app."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.accounts.repositories import OTPRepository

logger = logging.getLogger(__name__)


@shared_task(name="apps.accounts.tasks.purge_expired_otps")
def purge_expired_otps(older_than_days: int = 7) -> int:
    """Delete spent OTP rows.

    These are hashed, but they are still authentication artefacts and there is
    no reason to keep them once they can no longer be used.  Scheduled nightly
    via Celery Beat.
    """
    deleted = OTPRepository().purge_expired(older_than_days=older_than_days)
    logger.info("otp_purge_complete", extra={"deleted": deleted})
    return deleted


@shared_task(name="apps.accounts.tasks.deactivate_stale_devices")
def deactivate_stale_devices(inactive_days: int = 180) -> int:
    """Revoke devices that have not been seen in a long time.

    A handset that has not checked in for six months is more likely lost or
    replaced than dormant; revoking it shrinks the set of tokens that would
    still work if one were recovered.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.accounts.models import Device

    cutoff = timezone.now() - timedelta(days=inactive_days)
    count = Device.objects.filter(is_active=True, last_seen_at__lt=cutoff).update(
        is_active=False, revoked_at=timezone.now()
    )
    logger.info("stale_devices_revoked", extra={"count": count})
    return count
