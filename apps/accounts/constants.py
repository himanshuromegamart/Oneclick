"""Roles.

Deliberately simple: three roles as a single field on the user, not a
permission table.  The whole access model fits in the table below, which means
an owner can reason about "who can do what" without opening an admin screen.

=================  =====  =====  ======
Action             Owner  Staff  Viewer
=================  =====  =====  ======
Browse categories    Y      Y      Y
View / download      Y      Y      Y
Search               Y      Y      Y
Create category      Y      Y      -
Rename category      Y      Y      -
Delete category      Y      -      -
Upload file          Y      Y      -
Rename own file      Y      Y      -
Delete own file      Y      Y      -
Delete any file      Y      -      -
Share                Y      Y      -
Restore / purge      Y      -      -
=================  =====  =====  ======

"Own file" means the user uploaded it.  That is the rule behind "a user can see
and delete their own documents": Staff manage what they added, and the Owner
manages everything.
"""

from __future__ import annotations

from django.db import models


class UserRole(models.TextChoices):
    OWNER = "owner", "Owner"
    STAFF = "staff", "Staff"
    VIEWER = "viewer", "Viewer"


class Platform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"
    UNKNOWN = "unknown", "Unknown"


#: Roles allowed to add or rename categories and upload files.
CONTRIBUTOR_ROLES = frozenset({UserRole.OWNER, UserRole.STAFF})
