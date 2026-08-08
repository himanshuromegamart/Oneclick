"""Enable the Postgres extensions search depends on.

``pg_trgm`` powers typo tolerance and ``unaccent`` lets accented spellings
match. Both ship with Postgres but are off in a fresh database, and creating an
extension needs elevated rights - so it runs first, on its own, where a
permissions failure is obvious rather than mysterious.
"""

from __future__ import annotations

from django.contrib.postgres.operations import TrigramExtension, UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        TrigramExtension(),
        UnaccentExtension(),
    ]
