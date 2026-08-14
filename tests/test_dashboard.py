"""The admin dashboard.

Server-rendered and session-authenticated. What matters most is who gets in,
and that the two create flows apply the same rules the API does rather than
writing rows directly.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.folders.models import Folder

pytestmark = pytest.mark.django_db

PASSWORD = "dashboard-test-password"


def signed_in(user: User) -> Client:
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    client = Client()
    assert client.login(username=user.phone_number, password=PASSWORD)
    return client


@pytest.fixture
def client_owner(owner):
    return signed_in(owner)


class TestSigningIn:
    def test_login_page_loads(self):
        response = Client().get(reverse("dashboard:login"))
        assert response.status_code == 200

    def test_owner_can_sign_in(self, owner):
        owner.set_password(PASSWORD)
        owner.save(update_fields=["password"])

        response = Client().post(
            reverse("dashboard:login"),
            {"phone_number": owner.phone_number, "password": PASSWORD},
        )
        assert response.status_code == 302
        assert response.url == reverse("dashboard:home")

    def test_an_unformatted_number_works(self, owner):
        """Nobody should have to know the number is stored as +91…"""
        owner.set_password(PASSWORD)
        owner.save(update_fields=["password"])

        response = Client().post(
            reverse("dashboard:login"), {"phone_number": "9000000001", "password": PASSWORD}
        )
        assert response.status_code == 302

    def test_wrong_password_is_refused(self, owner):
        owner.set_password(PASSWORD)
        owner.save(update_fields=["password"])

        response = Client().post(
            reverse("dashboard:login"),
            {"phone_number": owner.phone_number, "password": "not-it-at-all"},
        )
        assert response.status_code == 200
        assert b"Incorrect mobile number or password" in response.content

    def test_every_failure_reads_the_same(self, owner, db):
        """Otherwise the form tells an attacker which numbers have accounts."""
        owner.set_password(PASSWORD)
        owner.save(update_fields=["password"])

        wrong_password = Client().post(
            reverse("dashboard:login"),
            {"phone_number": owner.phone_number, "password": "not-it-at-all"},
        )
        unknown_number = Client().post(
            reverse("dashboard:login"),
            {"phone_number": "9111111119", "password": "not-it-at-all"},
        )
        assert b"Incorrect mobile number or password" in wrong_password.content
        assert b"Incorrect mobile number or password" in unknown_number.content

    def test_disabled_account_cannot_sign_in(self, owner):
        owner.set_password(PASSWORD)
        owner.is_active = False
        owner.save(update_fields=["password", "is_active"])

        response = Client().post(
            reverse("dashboard:login"),
            {"phone_number": owner.phone_number, "password": PASSWORD},
        )
        assert response.status_code == 200

    def test_signing_out_ends_the_session(self, client_owner):
        client_owner.get(reverse("dashboard:logout"))
        response = client_owner.get(reverse("dashboard:home"))
        assert response.status_code == 302
        assert "login" in response.url


class TestWhoCanSeeIt:
    @pytest.mark.parametrize("page", ["home", "users", "categories"])
    def test_anonymous_is_sent_to_login(self, page):
        response = Client().get(reverse(f"dashboard:{page}"))
        assert response.status_code == 302
        assert "login" in response.url

    @pytest.mark.parametrize("page", ["home", "users", "categories"])
    def test_owner_sees_every_page(self, client_owner, page):
        assert client_owner.get(reverse(f"dashboard:{page}")).status_code == 200

    def test_a_non_owner_is_turned_away(self, staff):
        """Access is re-checked per request, so a role change takes effect at
        once rather than when the session happens to expire."""
        client = signed_in(staff)

        response = client.get(reverse("dashboard:home"))
        assert response.status_code == 302
        assert "login" in response.url

    def test_demoting_someone_mid_session_locks_them_out(self, owner, client_owner):
        assert client_owner.get(reverse("dashboard:home")).status_code == 200

        owner.role = "staff"
        owner.save(update_fields=["role"])

        assert client_owner.get(reverse("dashboard:home")).status_code == 302


class TestCreatingUsers:
    def test_a_user_is_created_and_can_sign_in(self, client_owner):
        response = client_owner.post(
            reverse("dashboard:users"),
            {
                "full_name": "Ramesh Kumar",
                "phone_number": "9812345670",
                "email": "",
                "password": "a-strong-enough-pass",
            },
        )
        assert response.status_code == 302

        user = User.objects.get(phone_number="+919812345670")
        assert user.full_name == "Ramesh Kumar"
        assert user.check_password("a-strong-enough-pass")

        assert Client().login(username="9812345670", password="a-strong-enough-pass")

    def test_the_role_is_always_owner(self, client_owner):
        """There is no role field on the form - the decision is made in code."""
        client_owner.post(
            reverse("dashboard:users"),
            {
                "full_name": "Auto Owner",
                "phone_number": "9812345671",
                "email": "",
                "password": "a-strong-enough-pass",
            },
        )
        assert User.objects.get(phone_number="+919812345671").is_owner

    def test_the_form_offers_no_role_choice(self, client_owner):
        response = client_owner.get(reverse("dashboard:users"))
        assert b'name="role"' not in response.content

    def test_a_duplicate_number_is_refused(self, client_owner, staff):
        response = client_owner.post(
            reverse("dashboard:users"),
            {
                "full_name": "Clone",
                "phone_number": staff.phone_number,
                "email": "",
                "password": "a-strong-enough-pass",
            },
        )
        assert response.status_code == 200
        assert b"already has an account" in response.content

    def test_a_weak_password_is_refused(self, client_owner):
        response = client_owner.post(
            reverse("dashboard:users"),
            {
                "full_name": "Weak",
                "phone_number": "9812345672",
                "email": "",
                "password": "abc",
            },
        )
        assert response.status_code == 200
        assert not User.objects.filter(phone_number="+919812345672").exists()

    def test_an_invalid_number_is_refused(self, client_owner):
        response = client_owner.post(
            reverse("dashboard:users"),
            {
                "full_name": "Bad Number",
                "phone_number": "123",
                "email": "",
                "password": "a-strong-enough-pass",
            },
        )
        assert response.status_code == 200
        assert User.objects.filter(full_name="Bad Number").count() == 0


class TestCreatingCategories:
    def test_a_top_level_category(self, client_owner):
        response = client_owner.post(
            reverse("dashboard:categories"),
            {"name": "Certificates", "parent": "", "description": ""},
        )
        assert response.status_code == 302

        folder = Folder.objects.get(name="Certificates")
        assert folder.parent is None
        assert folder.depth == 0

    def test_a_subcategory_under_a_chosen_parent(self, client_owner, root_folder):
        response = client_owner.post(
            reverse("dashboard:categories"),
            {"name": "Water ATM", "parent": str(root_folder.pk), "description": ""},
        )
        assert response.status_code == 302

        folder = Folder.objects.get(name="Water ATM")
        assert folder.parent == root_folder
        assert folder.depth == 1

    def test_nesting_can_go_deeper_still(self, client_owner, child_folder):
        client_owner.post(
            reverse("dashboard:categories"),
            {"name": "500 LPH", "parent": str(child_folder.pk), "description": ""},
        )
        assert Folder.objects.get(name="500 LPH").depth == 2

    def test_the_parent_dropdown_lists_existing_categories(self, client_owner, child_folder):
        response = client_owner.get(reverse("dashboard:categories"))

        assert b'name="parent"' in response.content
        assert str(child_folder.pk).encode() in response.content
        # The top-level option has to be there, or only subcategories could be made.
        assert b"Top level" in response.content

    def test_a_duplicate_name_in_the_same_parent_is_refused(self, client_owner, root_folder):
        response = client_owner.post(
            reverse("dashboard:categories"),
            {"name": root_folder.name, "parent": "", "description": ""},
        )
        assert response.status_code == 200
        assert b"already exists" in response.content

    def test_the_same_name_is_allowed_under_a_different_parent(self, client_owner, root_folder):
        response = client_owner.post(
            reverse("dashboard:categories"),
            {"name": root_folder.name, "parent": str(root_folder.pk), "description": ""},
        )
        assert response.status_code == 302
        assert Folder.objects.filter(name=root_folder.name).count() == 2

    def test_an_illegal_name_is_refused(self, client_owner):
        response = client_owner.post(
            reverse("dashboard:categories"), {"name": "bad/name", "parent": "", "description": ""}
        )
        assert response.status_code == 200
        assert not Folder.objects.filter(name="bad/name").exists()

    def test_the_creator_is_recorded(self, client_owner, owner):
        client_owner.post(
            reverse("dashboard:categories"), {"name": "Traceable", "parent": "", "description": ""}
        )
        assert Folder.objects.get(name="Traceable").created_by == owner


class TestPagesRender:
    """A template error only shows up when the page is actually rendered."""

    def test_overview_shows_the_counts(self, client_owner, root_folder, sample_file):
        response = client_owner.get(reverse("dashboard:home"))

        assert response.status_code == 200
        assert b"Categories" in response.content
        assert root_folder.name.encode() in response.content

    def test_the_category_list_shows_nesting(self, client_owner, child_folder):
        response = client_owner.get(reverse("dashboard:categories"))

        assert child_folder.name.encode() in response.content
        assert child_folder.parent.name.encode() in response.content

    def test_the_user_list_shows_accounts(self, client_owner, owner):
        response = client_owner.get(reverse("dashboard:users"))

        assert owner.full_name.encode() in response.content
        assert owner.phone_number.encode() in response.content

    def test_pages_are_mobile_ready(self, client_owner):
        """Without the viewport tag a phone renders it at desktop width."""
        response = client_owner.get(reverse("dashboard:home"))
        assert b'name="viewport"' in response.content

    def test_forms_carry_a_csrf_token(self, client_owner):
        for page in ("users", "categories"):
            response = client_owner.get(reverse(f"dashboard:{page}"))
            assert b"csrfmiddlewaretoken" in response.content, page
