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
from apps.dashboard.forms import PATH_SEPARATOR
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
def client_admin(admin):
    return signed_in(admin)


class TestSigningIn:
    def test_login_page_loads(self):
        response = Client().get(reverse("dashboard:login"))
        assert response.status_code == 200

    def test_an_admin_can_sign_in(self, admin):
        admin.set_password(PASSWORD)
        admin.save(update_fields=["password"])

        response = Client().post(
            reverse("dashboard:login"),
            {"phone_number": admin.phone_number, "password": PASSWORD},
        )
        assert response.status_code == 302
        assert response.url == reverse("dashboard:home")

    def test_an_unformatted_number_works(self, admin):
        """Nobody should have to know the number is stored as +91…"""
        admin.set_password(PASSWORD)
        admin.save(update_fields=["password"])

        response = Client().post(
            reverse("dashboard:login"), {"phone_number": "9000000001", "password": PASSWORD}
        )
        assert response.status_code == 302

    def test_wrong_password_is_refused(self, admin):
        admin.set_password(PASSWORD)
        admin.save(update_fields=["password"])

        response = Client().post(
            reverse("dashboard:login"),
            {"phone_number": admin.phone_number, "password": "not-it-at-all"},
        )
        assert response.status_code == 200
        assert b"Incorrect mobile number or password" in response.content

    def test_every_failure_reads_the_same(self, admin, db):
        """Otherwise the form tells an attacker which numbers have accounts."""
        admin.set_password(PASSWORD)
        admin.save(update_fields=["password"])

        wrong_password = Client().post(
            reverse("dashboard:login"),
            {"phone_number": admin.phone_number, "password": "not-it-at-all"},
        )
        unknown_number = Client().post(
            reverse("dashboard:login"),
            {"phone_number": "9111111119", "password": "not-it-at-all"},
        )
        assert b"Incorrect mobile number or password" in wrong_password.content
        assert b"Incorrect mobile number or password" in unknown_number.content

    def test_disabled_account_cannot_sign_in(self, admin):
        admin.set_password(PASSWORD)
        admin.is_active = False
        admin.save(update_fields=["password", "is_active"])

        response = Client().post(
            reverse("dashboard:login"),
            {"phone_number": admin.phone_number, "password": PASSWORD},
        )
        assert response.status_code == 200

    def test_signing_out_ends_the_session(self, client_admin):
        client_admin.get(reverse("dashboard:logout"))
        response = client_admin.get(reverse("dashboard:home"))
        assert response.status_code == 302
        assert "login" in response.url


class TestWhoCanSeeIt:
    @pytest.mark.parametrize("page", ["home", "users", "categories"])
    def test_anonymous_is_sent_to_login(self, page):
        response = Client().get(reverse(f"dashboard:{page}"))
        assert response.status_code == 302
        assert "login" in response.url

    @pytest.mark.parametrize("page", ["home", "users", "categories"])
    def test_an_admin_sees_every_page(self, client_admin, page):
        assert client_admin.get(reverse(f"dashboard:{page}")).status_code == 200

    def test_a_user_account_is_turned_away(self, member):
        """Access is re-checked per request, so a role change takes effect at
        once rather than when the session happens to expire."""
        client = signed_in(member)

        response = client.get(reverse("dashboard:home"))
        assert response.status_code == 302
        assert "login" in response.url

    def test_demoting_someone_mid_session_locks_them_out(self, admin, client_admin):
        assert client_admin.get(reverse("dashboard:home")).status_code == 200

        admin.role = "user"
        admin.save(update_fields=["role"])

        assert client_admin.get(reverse("dashboard:home")).status_code == 302


class TestCreatingUsers:
    def test_a_user_is_created_and_can_sign_in(self, client_admin):
        response = client_admin.post(
            reverse("dashboard:users"),
            {
                "full_name": "Ramesh Kumar",
                "phone_number": "9812345670",
                "email": "",
                "password": "a-strong-enough-pass",
                "role": "user",
            },
        )
        assert response.status_code == 302

        user = User.objects.get(phone_number="+919812345670")
        assert user.full_name == "Ramesh Kumar"
        assert user.check_password("a-strong-enough-pass")

        assert Client().login(username="9812345670", password="a-strong-enough-pass")

    def test_the_form_offers_both_roles(self, client_admin):
        content = client_admin.get(reverse("dashboard:users")).content
        assert b'name="role"' in content
        assert b'value="user"' in content
        assert b'value="admin"' in content

    @pytest.mark.parametrize(("role", "expected_admin"), [("admin", True), ("user", False)])
    def test_the_chosen_role_is_what_gets_saved(self, client_admin, role, expected_admin):
        client_admin.post(
            reverse("dashboard:users"),
            {
                "full_name": f"Person {role}",
                "phone_number": "9812345671" if expected_admin else "9812345672",
                "email": "",
                "password": "a-strong-enough-pass",
                "role": role,
            },
        )
        phone = "+919812345671" if expected_admin else "+919812345672"
        assert User.objects.get(phone_number=phone).is_admin is expected_admin

    def test_the_default_is_the_role_without_dashboard_access(self, client_admin):
        """Granting admin should take a deliberate choice, not inattention."""
        from apps.dashboard.forms import UserForm

        assert UserForm().fields["role"].initial == "user"

    def test_an_unknown_role_is_refused(self, client_admin):
        """The dropdown is not the only thing standing between a POST and the
        database - a hand-rolled form post must be rejected too."""
        response = client_admin.post(
            reverse("dashboard:users"),
            {
                "full_name": "Sneaky",
                "phone_number": "9812345673",
                "email": "",
                "password": "a-strong-enough-pass",
                "role": "superadmin",
            },
        )
        assert response.status_code == 200
        assert not User.objects.filter(phone_number="+919812345673").exists()

    def test_a_duplicate_number_is_refused(self, client_admin, member):
        response = client_admin.post(
            reverse("dashboard:users"),
            {
                "full_name": "Clone",
                "phone_number": member.phone_number,
                "email": "",
                "password": "a-strong-enough-pass",
            },
        )
        assert response.status_code == 200
        assert b"already has an account" in response.content

    def test_a_weak_password_is_refused(self, client_admin):
        response = client_admin.post(
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

    def test_an_invalid_number_is_refused(self, client_admin):
        response = client_admin.post(
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
    def test_a_top_level_category(self, client_admin):
        response = client_admin.post(
            reverse("dashboard:categories"),
            {"name": "Certificates", "parent": "", "description": ""},
        )
        assert response.status_code == 302

        folder = Folder.objects.get(name="Certificates")
        assert folder.parent is None
        assert folder.depth == 0

    def test_a_subcategory_under_a_chosen_parent(self, client_admin, root_folder):
        response = client_admin.post(
            reverse("dashboard:categories"),
            {"name": "Water ATM", "parent": str(root_folder.pk), "description": ""},
        )
        assert response.status_code == 302

        folder = Folder.objects.get(name="Water ATM")
        assert folder.parent == root_folder
        assert folder.depth == 1

    def test_nesting_can_go_deeper_still(self, client_admin, child_folder):
        client_admin.post(
            reverse("dashboard:categories"),
            {"name": "500 LPH", "parent": str(child_folder.pk), "description": ""},
        )
        assert Folder.objects.get(name="500 LPH").depth == 2

    def test_the_parent_dropdown_lists_existing_categories(self, client_admin, child_folder):
        response = client_admin.get(reverse("dashboard:categories"))

        assert b'name="parent"' in response.content
        assert str(child_folder.pk).encode() in response.content
        # The top-level option has to be there, or only subcategories could be made.
        assert b"Top level" in response.content

    def test_a_duplicate_name_in_the_same_parent_is_refused(self, client_admin, root_folder):
        response = client_admin.post(
            reverse("dashboard:categories"),
            {"name": root_folder.name, "parent": "", "description": ""},
        )
        assert response.status_code == 200
        assert b"already exists" in response.content

    def test_the_same_name_is_allowed_under_a_different_parent(self, client_admin, root_folder):
        response = client_admin.post(
            reverse("dashboard:categories"),
            {"name": root_folder.name, "parent": str(root_folder.pk), "description": ""},
        )
        assert response.status_code == 302
        assert Folder.objects.filter(name=root_folder.name).count() == 2

    def test_an_illegal_name_is_refused(self, client_admin):
        response = client_admin.post(
            reverse("dashboard:categories"), {"name": "bad/name", "parent": "", "description": ""}
        )
        assert response.status_code == 200
        assert not Folder.objects.filter(name="bad/name").exists()

    def test_the_creator_is_recorded(self, client_admin, admin):
        client_admin.post(
            reverse("dashboard:categories"), {"name": "Traceable", "parent": "", "description": ""}
        )
        assert Folder.objects.get(name="Traceable").created_by == admin


class TestParentPicker:
    """The dropdown has to stay usable once there are hundreds of categories."""

    def test_options_show_the_full_path(self, client_admin, child_folder):
        """A bare name is ambiguous - several products have a "500 LPH"."""
        from apps.folders.repositories import FolderRepository

        leaf = FolderRepository().create_folder(
            name="500 LPH", parent=child_folder, created_by=child_folder.created_by
        )

        response = client_admin.get(reverse("dashboard:categories"))
        expected = PATH_SEPARATOR.join([child_folder.parent.name, child_folder.name, leaf.name])
        assert expected.encode() in response.content

    def test_same_name_under_different_parents_is_distinguishable(
        self, client_admin, admin, root_folder
    ):
        from apps.folders.repositories import FolderRepository

        repo = FolderRepository()
        atm = repo.create_folder(name="Water ATM", parent=root_folder, created_by=admin)
        cooler = repo.create_folder(name="Water Cooler", parent=root_folder, created_by=admin)
        repo.create_folder(name="500 LPH", parent=atm, created_by=admin)
        repo.create_folder(name="500 LPH", parent=cooler, created_by=admin)

        content = client_admin.get(reverse("dashboard:categories")).content

        assert f"Water ATM{PATH_SEPARATOR}500 LPH".encode() in content
        assert f"Water Cooler{PATH_SEPARATOR}500 LPH".encode() in content

    def test_the_select_is_marked_searchable(self, client_admin, root_folder):
        """The JS upgrades anything carrying this attribute."""
        response = client_admin.get(reverse("dashboard:categories"))
        assert b"data-searchable" in response.content

    def test_a_top_level_option_is_always_offered(self, client_admin, root_folder):
        response = client_admin.get(reverse("dashboard:categories"))
        assert b"Top level" in response.content

    def test_labels_do_not_cost_a_query_per_ancestor(self, client_admin, admin):
        """Paths come from the stored materialised path, not by walking parents.

        The naive version of this feature - following .parent up the tree to
        build each label - is invisible on the two categories a test usually
        has, and quietly turns the page into hundreds of queries in
        production. So this pins the count instead of trusting the code.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.folders.repositories import FolderRepository

        repo = FolderRepository()
        url = reverse("dashboard:categories")

        def render_query_count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert client_admin.get(url).status_code == 200
            return len(queries)

        parent = repo.create_folder(name="Level 0", parent=None, created_by=admin)
        shallow = render_query_count()

        for level in range(1, 12):
            parent = repo.create_folder(name=f"Level {level}", parent=parent, created_by=admin)
        deep = render_query_count()

        # A chain of 12 is both deeper and larger than a chain of 1, so equality
        # here rules out scaling on either.
        assert deep == shallow
        assert f"Level 0{PATH_SEPARATOR}Level 1".encode() in client_admin.get(url).content

    def test_a_large_tree_still_renders(self, client_admin, admin, root_folder):
        from apps.folders.models import Folder

        Folder.objects.bulk_create(
            [
                Folder(
                    name=f"Category {index:03d}",
                    parent=root_folder,
                    path=root_folder.subtree_prefix,
                    depth=1,
                    created_by=admin,
                )
                for index in range(200)
            ]
        )

        response = client_admin.get(reverse("dashboard:categories"))
        assert response.status_code == 200
        assert b"Category 199" in response.content


class TestPagesRender:
    """A template error only shows up when the page is actually rendered."""

    def test_overview_shows_the_counts(self, client_admin, root_folder, sample_file):
        response = client_admin.get(reverse("dashboard:home"))

        assert response.status_code == 200
        assert b"Categories" in response.content
        assert root_folder.name.encode() in response.content

    def test_the_category_list_shows_nesting(self, client_admin, child_folder):
        response = client_admin.get(reverse("dashboard:categories"))

        assert child_folder.name.encode() in response.content
        assert child_folder.parent.name.encode() in response.content

    def test_the_user_list_shows_accounts(self, client_admin, admin):
        response = client_admin.get(reverse("dashboard:users"))

        assert admin.full_name.encode() in response.content
        assert admin.phone_number.encode() in response.content

    def test_pages_are_mobile_ready(self, client_admin):
        """Without the viewport tag a phone renders it at desktop width."""
        response = client_admin.get(reverse("dashboard:home"))
        assert b'name="viewport"' in response.content

    def test_forms_carry_a_csrf_token(self, client_admin):
        for page in ("users", "categories"):
            response = client_admin.get(reverse(f"dashboard:{page}"))
            assert b"csrfmiddlewaretoken" in response.content, page
