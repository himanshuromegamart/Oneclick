"""Roles.

Two roles, one difference between them: an Admin can open the web dashboard
and the Django admin site, and a User cannot.  Inside the mobile app they are
identical.

=========================  =====  ====
Action                     Admin  User
=========================  =====  ====
Browse categories            Y      Y
View / download              Y      Y
Search                       Y      Y
Create / rename category     Y      Y
Delete category              Y      Y
Upload file                  Y      Y
Rename / delete any file     Y      Y
Share                        Y      Y
Restore / purge              Y      Y
-------------------------  -----  ----
Web dashboard                Y      -
Django admin site            Y      -
=========================  =====  ====

That single line below the rule is the whole access model.  It is deliberately
this blunt: the owner asked for exactly one restriction, so anything subtler
would be a rule nobody remembers when it matters.

Two consequences worth naming, because they are not obvious from the table:

* A User can delete documents somebody else uploaded, and can purge them
  permanently.  There is no "only your own files" rule any more.
* Account creation is not on the table at all - it exists only in the
  dashboard and the setup endpoint, both of which are Admin-gated.  That is
  what stops a User from simply creating themselves an Admin account and
  walking around the one restriction.
"""

from __future__ import annotations

from django.db import models


class UserRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    USER = "user", "User"


class Platform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"
    UNKNOWN = "unknown", "Unknown"
