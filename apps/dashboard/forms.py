"""Forms for the admin dashboard.

Each one delegates its real rules to the same validators and services the API
uses, so the dashboard and the app cannot disagree about what is allowed.
"""

from __future__ import annotations

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.accounts.constants import UserRole
from apps.accounts.models import User
from apps.core.exceptions import ValidationFailed
from apps.core.validators import normalize_phone_number, validate_node_name
from apps.dashboard.nodes import PATH_SEPARATOR
from apps.folders.models import Folder

__all__ = [
    "PATH_SEPARATOR",
    "CategoryForm",
    "InlineCategoryForm",
    "LoginForm",
    "SetPasswordForm",
    "UploadForm",
    "UserForm",
]


class LoginForm(forms.Form):
    phone_number = forms.CharField(
        label="Mobile number",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "placeholder": "9876543210",
                "autofocus": True,
                "autocomplete": "username",
                "inputmode": "numeric",
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={"placeholder": "Your password", "autocomplete": "current-password"}
        ),
    )

    def clean_phone_number(self) -> str:
        """Accept any common format, so nobody has to know we store E.164."""
        try:
            return normalize_phone_number(self.cleaned_data["phone_number"])
        except ValidationFailed:
            # Deliberately vague: a "no such number" message here would let
            # anyone check which numbers have accounts.
            raise forms.ValidationError("Enter a valid 10-digit mobile number.") from None


class UserForm(forms.Form):
    """Create a user."""

    full_name = forms.CharField(
        label="Full name",
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Ramesh Kumar", "autofocus": True}),
    )
    phone_number = forms.CharField(
        label="Mobile number",
        max_length=20,
        help_text="They sign in with this number.",
        widget=forms.TextInput(attrs={"placeholder": "9876543210", "inputmode": "numeric"}),
    )
    email = forms.EmailField(
        label="Email",
        required=False,
        widget=forms.EmailInput(attrs={"placeholder": "optional"}),
    )
    password = forms.CharField(
        label="Password",
        help_text="At least 8 characters, not all numbers, not a common word.",
        widget=forms.PasswordInput(
            attrs={"placeholder": "Set a password", "autocomplete": "new-password"}
        ),
    )
    # Spelt out in the option text rather than left to the bare word, because a
    # closed dropdown shows only the chosen line - and "Admin" on its own does
    # not tell you what you are handing over.
    role = forms.ChoiceField(
        label="Role",
        choices=[
            (UserRole.USER, "User - mobile app only"),
            (UserRole.ADMIN, "Admin - mobile app and this dashboard"),
        ],
        # Defaults to User: dashboard access is the one thing a role decides,
        # so the default is the account that cannot reach it. Granting admin
        # should be a deliberate choice, not what happens when nobody looks.
        initial=UserRole.USER,
        help_text="Both roles can do everything in the mobile app.",
    )

    def clean_phone_number(self) -> str:
        try:
            phone = normalize_phone_number(self.cleaned_data["phone_number"])
        except ValidationFailed as exc:
            raise forms.ValidationError(str(exc.detail)) from exc

        # all_objects: a disabled or removed account still holds the number, so
        # ignoring those would fail later with an unhelpful database error.
        if User.all_objects.filter(phone_number=phone).exists():
            raise forms.ValidationError("This mobile number already has an account.")
        return phone

    def clean_password(self) -> str:
        password = self.cleaned_data["password"]
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise forms.ValidationError(list(exc.messages)) from exc
        return password

    def save(self) -> User:
        user = User.objects.create_user(
            phone_number=self.cleaned_data["phone_number"],
            full_name=self.cleaned_data["full_name"],
            email=self.cleaned_data.get("email", ""),
            role=self.cleaned_data["role"],
        )
        user.set_password(self.cleaned_data["password"])
        user.save(update_fields=["password", "updated_at"])
        return user


class CategoryForm(forms.Form):
    """Create a category or a subcategory.

    They are the same thing - a subcategory is simply one with a parent - so
    this is one form with an optional parent, rather than two that would drift
    apart.
    """

    name = forms.CharField(
        label="Name",
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "Water ATM", "autofocus": True}),
    )
    parent = forms.ModelChoiceField(
        label="Inside",
        queryset=Folder.objects.none(),
        required=False,
        empty_label="Top level",
        help_text="Start typing to search. Leave as top level for a main category.",
        widget=forms.Select(attrs={"data-searchable": "true"}),
    )
    description = forms.CharField(
        label="Description",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional"}),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Rebuilt per request so a category added a moment ago is selectable.
        folders = list(Folder.objects.order_by("path", "position", "name"))
        self.fields["parent"].queryset = Folder.objects.order_by("path", "position", "name")

        # Label each option with its full path rather than its bare name.
        # Names repeat - a tree of any size has several "500 LPH" under
        # different products - and a list of identical labels is worse than no
        # list at all. The path also gives the search something to match on, so
        # typing "cooler 40" finds Products > Water Cooler > 40 Litre.
        #
        # The ancestor ids come from the stored materialised path, so building
        # every label costs one query however deep the tree goes.
        names = {folder.pk: folder.name for folder in folders}

        def full_path(folder: Folder) -> str:
            parts = [names[pk] for pk in folder.ancestor_ids if pk in names]
            parts.append(folder.name)
            return PATH_SEPARATOR.join(parts)

        self.fields["parent"].label_from_instance = full_path

    def clean_name(self) -> str:
        try:
            return validate_node_name(self.cleaned_data["name"], kind="category")
        except ValidationFailed as exc:
            raise forms.ValidationError(str(exc.detail)) from exc

    def clean(self) -> dict:
        cleaned = super().clean()
        name, parent = cleaned.get("name"), cleaned.get("parent")

        # Checked here so the user sees it on the field rather than as a 409
        # from the service layer, which enforces the same rule in the database.
        if name and Folder.objects.filter(parent=parent, name__iexact=name).exists():
            where = parent.name if parent else "the top level"
            self.add_error("name", f"“{name}” already exists in {where}.")
        return cleaned


class InlineCategoryForm(forms.Form):
    """Add a category from inside the explorer.

    The parent is not a field: it is wherever you are standing. That is the
    whole point of the explorer - you navigate to the place, then add there -
    and a parent picker would let the two disagree.
    """

    name = forms.CharField(
        label="Name",
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "New category name", "autofocus": True}),
    )
    description = forms.CharField(
        label="Description",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Optional"}),
    )

    def __init__(self, *args, parent: Folder | None = None, **kwargs) -> None:
        self.parent = parent
        super().__init__(*args, **kwargs)

    def clean_name(self) -> str:
        try:
            return validate_node_name(self.cleaned_data["name"], kind="category")
        except ValidationFailed as exc:
            raise forms.ValidationError(str(exc.detail)) from exc

    def clean(self) -> dict:
        cleaned = super().clean()
        name = cleaned.get("name")

        if name and Folder.objects.filter(parent=self.parent, name__iexact=name).exists():
            where = self.parent.name if self.parent else "the top level"
            self.add_error("name", f"“{name}” already exists in {where}.")
        return cleaned


class MultipleFileInput(forms.ClearableFileInput):
    """The stock widget refuses ``multiple``; this is Django's documented opt-in."""

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """A file field that cleans every selected file, not just the last one."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None) -> list:
        clean_one = super().clean
        if isinstance(data, list | tuple):
            return [clean_one(item, initial) for item in data]
        return [clean_one(data, initial)]


class UploadForm(forms.Form):
    """Put documents into the category currently open.

    Deliberately thin: every rule about what may be uploaded - size, extension,
    name - lives in the file service, which the API uses too. Duplicating any
    of it here would mean two places to change and one to forget.
    """

    file = MultipleFileField(
        label="Documents",
        help_text="PDF, image, Word, Excel. You can select more than one.",
    )


class SetPasswordForm(forms.Form):
    """Reset somebody's password from the user list.

    No current password: this is an admin resetting an account, and the whole
    reason it exists is that the person has lost or never had theirs. That
    makes it a privileged action, which is why it lives behind the dashboard
    and nowhere else.

    Nothing here chooses a hashing algorithm. ``set_password`` uses whatever
    ``PASSWORD_HASHERS`` says, so a reset password is stored exactly like every
    other one and cannot drift from them.
    """

    password = forms.CharField(
        label="New password",
        help_text="At least 8 characters, not all numbers, not a common word.",
        widget=forms.PasswordInput(
            attrs={"placeholder": "New password", "autocomplete": "new-password"}
        ),
    )

    def clean_password(self) -> str:
        password = self.cleaned_data["password"]
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise forms.ValidationError(list(exc.messages)) from exc
        return password
