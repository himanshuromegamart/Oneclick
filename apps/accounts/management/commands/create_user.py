"""Create, update or disable an account from the server.

This is the *only* way an account comes into existence - there is no
registration screen and no user-management API. Examples::

    python manage.py create_user --phone 9876543210 --name "Sarah Aqua" --role owner
    python manage.py create_user --phone 9812345678 --name "Ramesh" --role staff
    python manage.py create_user --phone 9812345678 --role viewer --update
    python manage.py create_user --phone 9812345678 --disable
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.constants import UserRole
from apps.accounts.models import User
from apps.core.exceptions import ValidationFailed
from apps.core.validators import normalize_phone_number


class Command(BaseCommand):
    help = "Create or modify a user account."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--phone", required=True, help="Mobile number, e.g. 9876543210")
        parser.add_argument("--name", default="", help="Full name (required when creating)")
        parser.add_argument("--email", default="", help="Email address (optional)")
        parser.add_argument(
            "--role",
            choices=[value for value, _ in UserRole.choices],
            default=UserRole.STAFF,
            help="owner = full control, staff = upload and manage own files, viewer = read-only",
        )
        parser.add_argument("--update", action="store_true", help="Update an existing user.")
        parser.add_argument("--disable", action="store_true", help="Switch the account off.")
        parser.add_argument("--enable", action="store_true", help="Switch the account back on.")

    def handle(self, *args, **options) -> None:
        try:
            phone = normalize_phone_number(options["phone"])
        except ValidationFailed as exc:
            raise CommandError(str(exc.detail)) from exc

        existing = User.all_objects.filter(phone_number=phone).first()

        if options["disable"] or options["enable"]:
            if existing is None:
                raise CommandError(f"No user with {phone}.")
            existing.is_active = options["enable"]
            existing.save(update_fields=["is_active", "updated_at"])
            state = "enabled" if options["enable"] else "disabled"
            self.stdout.write(self.style.SUCCESS(f"{existing.full_name} {state}."))
            return

        if existing is not None:
            if not options["update"]:
                raise CommandError(
                    f"{phone} already exists. Pass --update to change it, "
                    "or --disable to switch it off."
                )
            if options["name"]:
                existing.full_name = options["name"].strip()
            if options["email"]:
                existing.email = options["email"].strip()
            existing.role = options["role"]
            existing.is_deleted = False
            existing.is_active = True
            existing.full_clean(exclude=["password"])
            existing.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Updated {existing.full_name} <{existing.phone_number}> "
                    f"as {existing.get_role_display()}."
                )
            )
            return

        if not options["name"]:
            raise CommandError("--name is required when creating a user.")

        user = User.objects.create_user(
            phone_number=phone,
            full_name=options["name"],
            email=options["email"],
            role=options["role"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {user.full_name} <{user.phone_number}> as {user.get_role_display()}.\n"
                "They can now log in from the app - an OTP will be sent to this number."
            )
        )
