"""Resetting a password from the user list.

Two things carry the risk. The reset must not sign the person out of their
phone - an owner fixing a forgotten password should not knock somebody off
mid-job. And it must not sign the *admin* out of the dashboard either, which is
what Django does by default when you change your own password.
"""

from __future__ import annotations

import uuid

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db

PASSWORD = "dashboard-password-tests"
NEW = "a-brand-new-password-9"


def signed_in(user) -> Client:
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    client = Client()
    assert client.login(username=user.phone_number, password=PASSWORD)
    return client


@pytest.fixture
def browser(admin):
    return signed_in(admin)


def reset(browser, target, password=NEW, follow=False):
    return browser.post(
        reverse("dashboard:users"),
        {"action": "set_password", "user_id": str(target.pk), "password": password},
        follow=follow,
    )


class TestChangingSomebodyElsesPassword:
    def test_the_new_password_works(self, browser, member):
        reset(browser, member)

        member.refresh_from_db()
        assert member.check_password(NEW)

    def test_the_old_password_stops_working(self, browser, member):
        member.set_password("the-old-one-123")
        member.save(update_fields=["password"])

        reset(browser, member)

        member.refresh_from_db()
        assert not member.check_password("the-old-one-123")

    def test_they_can_sign_in_to_the_app_with_it(self, browser, member, api_client):
        reset(browser, member)

        response = api_client.post(
            "/api/v1/auth/login/",
            {"phone_number": member.phone_number, "password": NEW},
            format="json",
        )
        assert response.status_code == 200

    def test_it_redirects_back_to_the_list(self, browser, member):
        response = reset(browser, member)

        assert response.status_code == 302
        assert response.url == reverse("dashboard:users")

    def test_it_says_who_was_changed(self, browser, member):
        response = reset(browser, member, follow=True)

        assert member.full_name.encode() in response.content

    def test_no_current_password_is_asked_for(self, browser, member):
        """The whole point is that the person has lost theirs."""
        content = browser.get(reverse("dashboard:users")).content.lower()

        assert b"current password" not in content


class TestItDoesNotSignAnybodyOut:
    """The requirement that is easy to break and stays invisible until somebody
    complains their app logged them out."""

    def test_a_live_mobile_token_still_works_afterwards(self, browser, member):
        """A JWT carries no password material, so nothing about it should stop
        working because the password behind it changed."""
        from apps.accounts.services import AuthService

        tokens = AuthService().issue_tokens(member)
        phone = Client()

        before = phone.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {tokens.access}")
        assert before.status_code == 200

        reset(browser, member)

        after = phone.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {tokens.access}")
        assert after.status_code == 200, "the phone was signed out by a password reset"

    def test_a_refresh_token_still_works_afterwards(self, browser, member, api_client):
        from apps.accounts.services import AuthService

        tokens = AuthService().issue_tokens(member)

        reset(browser, member)

        response = api_client.post(
            "/api/v1/auth/token/refresh/", {"refresh": tokens.refresh}, format="json"
        )
        assert response.status_code == 200

    def test_the_admin_stays_signed_in_after_changing_their_own(self, browser, admin):
        """Django keys a session to a hash of the password, so without
        update_session_auth_hash this logs the admin straight out."""
        reset(browser, admin)

        response = browser.get(reverse("dashboard:users"))
        assert response.status_code == 200, "the admin was logged out of their own dashboard"

    def test_the_admin_can_use_their_new_password(self, browser, admin):
        reset(browser, admin)

        assert Client().login(username=admin.phone_number, password=NEW)


class TestValidation:
    def test_a_weak_password_is_refused(self, browser, member):
        member.set_password("the-old-one-123")
        member.save(update_fields=["password"])

        response = browser.post(
            reverse("dashboard:users"),
            {"action": "set_password", "user_id": str(member.pk), "password": "abc"},
            follow=True,
        )

        member.refresh_from_db()
        assert member.check_password("the-old-one-123"), "a weak password was accepted"
        assert b"too short" in response.content.lower()

    def test_an_all_numeric_password_is_refused(self, browser, member):
        browser.post(
            reverse("dashboard:users"),
            {"action": "set_password", "user_id": str(member.pk), "password": "84927163504"},
        )

        member.refresh_from_db()
        assert not member.check_password("84927163504")

    def test_an_unknown_account_is_reported_not_crashed(self, browser):
        response = browser.post(
            reverse("dashboard:users"),
            {"action": "set_password", "user_id": str(uuid.uuid4()), "password": NEW},
            follow=True,
        )

        assert response.status_code == 200
        assert b"no longer exists" in response.content

    def test_a_malformed_id_does_not_crash(self, browser):
        """The id comes off a query string or a form field, so it can be junk."""
        response = browser.post(
            reverse("dashboard:users"),
            {"action": "set_password", "user_id": "not-a-uuid", "password": NEW},
            follow=True,
        )

        assert response.status_code == 200

    def test_a_malformed_id_in_the_url_does_not_crash(self, browser):
        response = browser.get(reverse("dashboard:users"), {"set_password": "not-a-uuid"})

        assert response.status_code == 200

    def test_it_does_not_fall_through_to_the_create_form(self, browser, member):
        """Both forms post to this page. A password reset must not come back
        covered in "this field is required" from the other one."""
        response = reset(browser, member, follow=True)

        assert b"This field is required" not in response.content


class TestWhoMayDoIt:
    def test_anonymous_cannot(self, member):
        response = Client().post(
            reverse("dashboard:users"),
            {"action": "set_password", "user_id": str(member.pk), "password": NEW},
        )

        assert response.status_code == 302
        member.refresh_from_db()
        assert not member.check_password(NEW)

    def test_a_mobile_only_account_cannot(self, member, other_member):
        response = signed_in(member).post(
            reverse("dashboard:users"),
            {"action": "set_password", "user_id": str(other_member.pk), "password": NEW},
        )

        assert response.status_code == 302
        other_member.refresh_from_db()
        assert not other_member.check_password(NEW)


class TestTheButtonAndDialog:
    def test_every_row_offers_it(self, browser, member, other_member):
        content = browser.get(reverse("dashboard:users")).content

        assert content.count(b"Change password") >= 2

    def test_the_button_names_the_account_it_is_for(self, browser, member):
        content = browser.get(reverse("dashboard:users")).content.decode()

        assert f'data-password-for="{member.pk}"' in content

    def test_the_dialog_is_closed_by_default(self, browser, member):
        content = browser.get(reverse("dashboard:users")).content.decode()

        assert "<dialog" in content
        assert 'id="set-password" open' not in content

    def test_it_opens_without_javascript(self, browser, member):
        """The trigger is a real link; the server renders the dialog open."""
        content = browser.get(
            reverse("dashboard:users"), {"set_password": str(member.pk)}
        ).content.decode()

        assert 'id="set-password" open' in content
        assert member.full_name in content

    def test_there_is_one_dialog_not_one_per_row(self, browser, member, other_member):
        content = browser.get(reverse("dashboard:users")).content

        assert content.count(b"<dialog") == 1
