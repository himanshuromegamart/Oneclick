"""Create, update or disable an account from the server.

This is the *only* way an account comes into existence - there is no
registration screen and no user-management API. Examples::

    python manage.py create_user --phone 9876543210 --name "Sarah Aqua" --role owner
    python manage.py create_user --phone 9812345678 --name "Ramesh" --role staff
    python manage.py create_user --phone 9812345678 --role viewer --update
    python manage.py create_user --phone 9812345678 --disable

Add --password to enable password sign-in as well as OTP:

    python manage.py create_user --phone 9876543210 --name "Sarah Aqua" \
        --role owner --password "a-strong-password"
"""

from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
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
            default=UserRole.USER,
            help="admin = mobile app + web dashboard, user = mobile app only",
        )
        parser.add_argument(
            "--password",
            default="",
            help=(
                "Optional. Enables password sign-in at /auth/login/. "
                "Without one the account can only sign in with an OTP."
            ),
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
            if options["password"]:
                self._validate_password(options["password"])
                existing.set_password(options["password"])
            existing.full_clean(exclude=["password"])
            existing.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Updated {existing.full_name} <{existing.phone_number}> "
                    f"as {existing.get_role_display()}."
                    + (" Password set." if options["password"] else "")
                )
            )
            return

        if not options["name"]:
            raise CommandError("--name is required when creating a user.")

        if options["password"]:
            self._validate_password(options["password"])

        user = User.objects.create_user(
            phone_number=phone,
            full_name=options["name"],
            email=options["email"],
            role=options["role"],
        )

        if options["password"]:
            user.set_password(options["password"])
            user.save(update_fields=["password", "updated_at"])
            how = "They can sign in with this password, or with an OTP."
        else:
            how = "They sign in with an OTP sent to this number."

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {user.full_name} <{user.phone_number}> "
                f"as {user.get_role_display()}.\n{how}"
            )
        )

    @staticmethod
    def _validate_password(password: str) -> None:
        """Apply the same rules the API applies, so the two cannot diverge."""
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise CommandError("Password rejected: " + " ".join(exc.messages)) from exc
