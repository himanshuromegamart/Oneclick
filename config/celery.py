"""Celery application factory."""

from __future__ import annotations

import os

from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("sarah_aqua_soft")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@setup_logging.connect
def configure_logging(**_kwargs: object) -> None:
    """Let Django's LOGGING dict own worker logs too, so output stays JSON."""
    from logging.config import dictConfig

    from django.conf import settings

    dictConfig(settings.LOGGING)


@app.task(bind=True, name="config.debug_task")
def debug_task(self) -> str:
    return f"request: {self.request!r}"
