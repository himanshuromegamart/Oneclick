"""Collapse owner/staff/viewer into admin/user.

The roles now differ by one thing only - whether the account can open the web
dashboard - so the three-way split had nothing left to express.

owner  -> admin   (kept dashboard access)
staff  -> user    (mobile app only)
viewer -> user    (mobile app only; gains write access, which is the point)

Reversible, because a migration that cannot be rolled back turns a bad deploy
into a restore-from-backup. Going back maps admin -> owner and user -> staff;
nobody becomes a viewer again, since that role no longer exists anywhere to
tell the two apart.
"""

from __future__ import annotations

from django.db import migrations, models

FORWARD = {"owner": "admin", "staff": "user", "viewer": "user"}
BACKWARD = {"admin": "owner", "user": "staff"}


def _remap(model, mapping) -> None:
    for old, new in mapping.items():
        # _base_manager, because it is the only one guaranteed to exist on a
        # historical model and the only one that is unfiltered. The normal
        # manager hides soft-deleted users, and leaving those on a dead role
        # value would break them the moment somebody was restored.
        model._base_manager.filter(role=old).update(role=new)


def forwards(apps, schema_editor) -> None:
    _remap(apps.get_model("accounts", "User"), FORWARD)


def backwards(apps, schema_editor) -> None:
    _remap(apps.get_model("accounts", "User"), BACKWARD)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [
        # Data first, then the field: the other order would leave rows holding
        # values that are no longer valid choices in between.
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[("admin", "Admin"), ("user", "User")],
                db_index=True,
                default="user",
                max_length=10,
            ),
        ),
    ]
