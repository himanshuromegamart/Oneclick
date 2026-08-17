"""Global search in the dashboard.

The point of a *global* search is that you do not know where the thing is - so
the result has to say where it is. Half these tests are about the location
line, because a list of bare names is the failure mode: four products each have
a "500 LPH", and a result that only says "500 LPH" has answered nothing.
"""

from __future__ import annotations

import io

import pytest
from django.test import Client
from django.urls import reverse

from apps.dashboard import nodes
from apps.dashboard.nodes import PATH_SEPARATOR
from apps.files.models import FileAsset
from apps.folders.models import Folder
from apps.folders.repositories import FolderRepository

pytestmark = pytest.mark.django_db

PASSWORD = "search-test-password"


def signed_in(user) -> Client:
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    client = Client()
    assert client.login(username=user.phone_number, password=PASSWORD)
    return client


@pytest.fixture
def browser(admin):
    return signed_in(admin)


@pytest.fixture
def tree(admin, file_service):
    """Products > Water Cooler > 500 LPH, and Products > Water ATM > 500 LPH.

    Two categories with the same name in different places - the case that
    makes the location line necessary rather than decorative.
    """
    repo = FolderRepository()
    products = repo.create_folder(name="Products", parent=None, created_by=admin)
    cooler = repo.create_folder(name="Water Cooler", parent=products, created_by=admin)
    atm = repo.create_folder(name="Water ATM", parent=products, created_by=admin)
    cooler_lph = repo.create_folder(name="500 LPH", parent=cooler, created_by=admin)
    atm_lph = repo.create_folder(name="500 LPH", parent=atm, created_by=admin)

    brochure = file_service.upload(
        admin,
        folder_id=cooler_lph.pk,
        file_obj=io.BytesIO(b"%PDF-1.4 cooler brochure"),
        filename="Brochure.pdf",
        size_bytes=23,
        content_type="application/pdf",
    )
    return {
        "products": products,
        "cooler": cooler,
        "atm": atm,
        "cooler_lph": cooler_lph,
        "atm_lph": atm_lph,
        "brochure": brochure,
    }


def results_for(browser, term):
    return browser.get(reverse("dashboard:search"), {"q": term})


class TestWhoCanSearch:
    def test_anonymous_is_sent_to_login(self):
        response = Client().get(reverse("dashboard:search"), {"q": "x"})
        assert response.status_code == 302
        assert "login" in response.url

    def test_a_user_account_is_turned_away(self, member):
        response = signed_in(member).get(reverse("dashboard:search"), {"q": "x"})
        assert response.status_code == 302
        assert reverse("dashboard:login") in response.url


class TestTheBoxIsEverywhere:
    """The moment you want to search is the moment you gave up navigating, so
    the box must not itself need navigating to."""

    @pytest.mark.parametrize("page", ["home", "explorer-root", "categories", "users", "search"])
    def test_every_page_carries_the_search_box(self, browser, page):
        content = browser.get(reverse(f"dashboard:{page}")).content

        assert reverse("dashboard:search").encode() in content
        assert b'name="q"' in content

    def test_the_term_stays_in_the_box(self, browser, tree):
        """So you can adjust it rather than retype it."""
        content = results_for(browser, "brochure").content

        assert b'value="brochure"' in content


class TestItSearchesEverything:
    def test_it_finds_a_category(self, browser, tree):
        content = results_for(browser, "Water Cooler").content
        assert b"Water Cooler" in content

    def test_it_finds_a_document(self, browser, tree):
        content = results_for(browser, "Brochure").content
        assert b"Brochure.pdf" in content

    def test_it_finds_both_kinds_at_once(self, browser, tree, admin, file_service):
        file_service.upload(
            admin,
            folder_id=tree["cooler"].pk,
            file_obj=io.BytesIO(b"%PDF-1.4 x"),
            filename="Water Cooler manual.pdf",
            size_bytes=10,
            content_type="application/pdf",
        )

        results = nodes.search("Water Cooler")

        assert results.categories, "expected the category"
        assert results.documents, "expected the document"

    def test_it_searches_the_whole_tree_not_one_branch(self, browser, tree):
        """Global: a term buried three levels down is found from anywhere."""
        results = nodes.search("500 LPH")

        assert len(results.categories) == 2

    def test_it_tolerates_a_typo(self, browser, tree):
        """The same Postgres trigram search the mobile app uses."""
        content = results_for(browser, "brochre").content

        assert b"Brochure.pdf" in content

    def test_it_ignores_case(self, browser, tree):
        assert b"Water Cooler" in results_for(browser, "water cooler").content


class TestItSaysWhereThingsAre:
    def test_a_document_shows_the_category_it_is_in(self, browser, tree):
        results = nodes.search("Brochure")

        expected = PATH_SEPARATOR.join(["Products", "Water Cooler", "500 LPH"])
        assert results.documents[0].location == expected

    def test_a_category_shows_what_it_is_inside(self, browser, tree):
        results = nodes.search("Water Cooler")

        assert results.categories[0].location == "Products"

    def test_a_top_level_category_says_so(self, browser, tree):
        results = nodes.search("Products")

        assert results.categories[0].location == "Top level"

    def test_two_categories_of_the_same_name_are_told_apart(self, browser, tree):
        """Without this the whole feature is a list of identical rows."""
        results = nodes.search("500 LPH")

        locations = sorted(hit.location for hit in results.categories)
        assert locations == [
            PATH_SEPARATOR.join(["Products", "Water ATM"]),
            PATH_SEPARATOR.join(["Products", "Water Cooler"]),
        ]

    def test_the_location_reaches_the_page(self, browser, tree):
        content = results_for(browser, "500 LPH").content.decode()

        assert PATH_SEPARATOR.join(["Products", "Water ATM"]) in content
        assert PATH_SEPARATOR.join(["Products", "Water Cooler"]) in content

    def test_the_explorer_does_not_show_locations(self, browser, tree):
        """You already know where you are; repeating it on every row is noise."""
        content = browser.get(
            reverse("dashboard:explorer", args=[tree["cooler"].pk])
        ).content.decode()

        assert "node-where" not in content


class TestLocationsCostOneQuery:
    """Writing out where a result lives must not mean walking the tree.

    The naive version follows ``.parent`` up for every row. It is invisible on
    the three shallow rows a test usually has, and turns a page of results into
    hundreds of queries in production.
    """

    #: search_folders, search_files, and one lookup for every name mentioned.
    #: Deliberately absolute rather than a comparison between two searches -
    #: the search is fuzzy, so two different terms can match the same rows and
    #: a comparison then passes whatever the implementation does.
    MAX_QUERIES = 5

    def _chain(self, admin, depth: int) -> None:
        repo = FolderRepository()
        parent = None
        for level in range(depth):
            parent = repo.create_folder(name=f"Level{level}Unique", parent=parent, created_by=admin)

    def test_a_shallow_tree(self, admin, django_assert_max_num_queries):
        self._chain(admin, 2)

        with django_assert_max_num_queries(self.MAX_QUERIES):
            nodes.search("Level1Unique")

    def test_a_deep_tree_costs_the_same(self, admin, django_assert_max_num_queries):
        self._chain(admin, 12)

        with django_assert_max_num_queries(self.MAX_QUERIES):
            results = nodes.search("Level11Unique")

        # And it did actually produce the deep location, rather than passing by
        # returning nothing.
        assert results.categories
        assert "Level0Unique" in results.categories[0].location


class TestNoResults:
    def test_it_says_so_rather_than_showing_an_empty_page(self, browser, tree):
        content = results_for(browser, "zzzznothing").content

        assert b"Nothing matches" in content

    def test_an_empty_term_prompts_instead_of_listing_everything(self, browser, tree):
        content = browser.get(reverse("dashboard:search")).content

        assert b"Search everything" in content
        assert b"Products" not in content

    def test_whitespace_is_treated_as_empty(self, browser, tree):
        assert nodes.search("   ").is_empty

    def test_a_deleted_document_is_not_found(self, browser, tree, admin):
        from apps.files.services import FileService

        FileService().delete(admin, tree["brochure"])

        assert b"Brochure.pdf" not in results_for(browser, "Brochure").content

    def test_a_deleted_category_is_not_found(self, browser, tree, admin):
        from apps.folders.services import FolderService

        FolderService().delete(admin, tree["atm"])

        results = nodes.search("Water ATM")

        # Checked by id, not by emptiness: the search is fuzzy, so "Water
        # Cooler" legitimately still matches this term.
        assert str(tree["atm"].pk) not in [hit.node.id for hit in results.categories]

    def test_a_category_deleted_with_its_parent_is_not_found(self, browser, tree, admin):
        """Deleting a category takes its subtree, and search must agree."""
        from apps.folders.services import FolderService

        FolderService().delete(admin, tree["products"])

        results = nodes.search("500 LPH")
        assert not results.categories


class TestTheResultCap:
    def test_a_huge_result_set_is_capped_and_says_so(self, browser, admin):
        repo = FolderRepository()
        for index in range(nodes.SEARCH_LIMIT + 5):
            repo.create_folder(name=f"Widget {index:03d}", parent=None, created_by=admin)

        results = nodes.search("Widget")

        assert len(results.categories) == nodes.SEARCH_LIMIT
        assert results.is_capped
        assert b"best matches" in results_for(browser, "Widget").content

    def test_a_normal_result_set_is_not_marked_capped(self, browser, tree):
        assert not nodes.search("Brochure").is_capped


class TestTypeAhead:
    """The JSON behind search-as-you-type.

    The panel is drawn by JavaScript, so what is testable here is the contract
    it draws from - and that contract is where the mistakes would be.
    """

    def suggest(self, browser, term):
        return browser.get(reverse("dashboard:search-suggest"), {"q": term})

    def test_it_answers_json(self, browser, tree):
        response = self.suggest(browser, "Brochure")

        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")

    def test_it_returns_matches(self, browser, tree):
        payload = self.suggest(browser, "Brochure").json()

        assert [row["name"] for row in payload["results"]] == ["Brochure.pdf"]

    def test_every_row_carries_what_the_panel_draws(self, browser, tree):
        row = self.suggest(browser, "Brochure").json()["results"][0]

        # Pinned because the panel reads exactly these; a rename here is a
        # silently blank dropdown, which no other test would catch.
        assert set(row) == {
            "kind",
            "tone",
            "name",
            "detail",
            "location",
            "url",
            "is_container",
        }

    def test_a_row_says_where_it_is(self, browser, tree):
        row = self.suggest(browser, "Brochure").json()["results"][0]

        assert row["location"] == PATH_SEPARATOR.join(["Products", "Water Cooler", "500 LPH"])

    def test_a_row_links_to_the_thing(self, browser, tree):
        row = self.suggest(browser, "Water Cooler").json()["results"][0]

        assert row["url"] == reverse("dashboard:explorer", args=[tree["cooler"].pk])

    def test_categories_come_first(self, browser, tree, admin, file_service):
        file_service.upload(
            admin,
            folder_id=tree["cooler"].pk,
            file_obj=io.BytesIO(b"%PDF-1.4 x"),
            filename="Water Cooler manual.pdf",
            size_bytes=10,
            content_type="application/pdf",
        )

        kinds = [row["kind"] for row in self.suggest(browser, "Water Cooler").json()["results"]]

        assert kinds.index("category") < kinds.index("document")

    def test_it_is_capped_to_a_dropdown_worth(self, browser, admin):
        from apps.dashboard.views import SUGGEST_LIMIT

        repo = FolderRepository()
        for index in range(SUGGEST_LIMIT + 6):
            repo.create_folder(name=f"Widget {index:03d}", parent=None, created_by=admin)

        payload = self.suggest(browser, "Widget").json()

        assert len(payload["results"]) == SUGGEST_LIMIT

    def test_it_offers_a_way_to_the_full_results(self, browser, tree):
        payload = self.suggest(browser, "500 LPH").json()

        assert payload["more_url"].startswith(reverse("dashboard:search"))
        assert "500" in payload["more_url"]

    def test_one_letter_is_not_worth_a_round_trip(self, browser, tree):
        """It would match half the database and teach nothing."""
        payload = self.suggest(browser, "P").json()

        assert payload["results"] == []

    def test_an_empty_term_returns_nothing(self, browser, tree):
        assert self.suggest(browser, "").json()["results"] == []

    def test_it_echoes_the_term(self, browser, tree):
        """The script drops replies that no longer match what is typed, which
        is what stops a slow answer overwriting a newer one."""
        assert self.suggest(browser, "Brochure").json()["q"] == "Brochure"

    def test_a_typo_still_suggests(self, browser, tree):
        payload = self.suggest(browser, "brochre").json()

        assert [row["name"] for row in payload["results"]] == ["Brochure.pdf"]

    def test_it_is_admin_only(self, member, tree):
        response = signed_in(member).get(reverse("dashboard:search-suggest"), {"q": "x"})

        assert response.status_code == 302

    def test_anonymous_gets_nothing(self, tree):
        response = Client().get(reverse("dashboard:search-suggest"), {"q": "Brochure"})

        assert response.status_code == 302
        assert b"Brochure" not in response.content

    def test_markup_cannot_get_into_a_name_in_the_first_place(
        self, browser, tree, admin, file_service
    ):
        """The real protection, and it is upstream of this endpoint: the name
        validator refuses angle brackets, so no row can carry markup at all."""
        from apps.core.exceptions import ValidationFailed

        with pytest.raises(ValidationFailed):
            file_service.upload(
                admin,
                folder_id=tree["cooler"].pk,
                file_obj=io.BytesIO(b"%PDF-1.4 x"),
                filename="<img src=x onerror=alert(1)>.pdf",
                size_bytes=10,
                content_type="application/pdf",
            )

    def test_a_name_needing_escaping_survives_as_text(self, browser, tree, admin, file_service):
        """Ampersands are legal in a name. They must arrive as an ampersand,
        not as an entity and not as the start of anything."""
        file_service.upload(
            admin,
            folder_id=tree["cooler"].pk,
            file_obj=io.BytesIO(b"%PDF-1.4 x"),
            filename="Price & Terms.pdf",
            size_bytes=10,
            content_type="application/pdf",
        )

        payload = self.suggest(browser, "Terms").json()

        assert "Price & Terms.pdf" in [row["name"] for row in payload["results"]]


class TestTheBoxStillWorksWithoutJavaScript:
    """The type-ahead is an enhancement. The form underneath must stand alone."""

    def test_the_form_is_a_plain_get_to_the_results_page(self, browser):
        content = browser.get(reverse("dashboard:explorer-root")).content.decode()

        assert 'method="get"' in content
        assert f'action="{reverse("dashboard:search")}"' in content

    def test_it_carries_a_submit_button(self, browser):
        content = browser.get(reverse("dashboard:explorer-root")).content

        assert b'type="submit"' in content

    def test_the_enhancement_is_opt_in_per_form(self, browser):
        """The script only touches a form that names its endpoint."""
        content = browser.get(reverse("dashboard:explorer-root")).content.decode()

        assert f'data-suggest-url="{reverse("dashboard:search-suggest")}"' in content


class TestActingOnAResult:
    def test_a_result_links_to_where_it_lives(self, browser, tree):
        content = results_for(browser, "Water Cooler").content.decode()

        assert reverse("dashboard:explorer", args=[tree["cooler"].pk]) in content

    def test_a_document_result_opens_the_document(self, browser, tree):
        content = results_for(browser, "Brochure").content.decode()

        assert reverse("dashboard:document-open", args=[tree["brochure"].pk]) in content

    def test_deleting_from_the_results_works(self, browser, tree):
        """The row partial is shared with the explorer, and its delete form
        posts to whichever page drew it - so this page has to handle it, or the
        button is decoration."""
        response = browser.post(
            reverse("dashboard:search"),
            {
                "action": "delete",
                "kind": "document",
                "node_id": str(tree["brochure"].pk),
                "q": "Brochure",
            },
        )

        assert response.status_code == 302
        assert not FileAsset.objects.filter(pk=tree["brochure"].pk).exists()

    def test_deleting_returns_you_to_the_same_results(self, browser, tree):
        response = browser.post(
            reverse("dashboard:search"),
            {
                "action": "delete",
                "kind": "category",
                "node_id": str(tree["atm"].pk),
                "q": "500 LPH",
            },
        )

        assert "q=500%20LPH" in response.url or "q=500+LPH" in response.url
        assert not Folder.objects.filter(pk=tree["atm"].pk).exists()
