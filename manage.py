#!/usr/bin/env python
"""Django command-line utility."""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - defensive
        raise ImportError(
            "Django is not importable. Activate the virtualenv and run "
            "`pip install -r requirements/development.txt`."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
