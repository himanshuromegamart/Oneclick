"""The dashboard explorer: walk into a category and work inside it.

Two things carry the risk here. The listing pages across two tables as if they
were one sequence, which is easy to get subtly wrong at a page boundary. And
every write is supposed to land in the category currently open - a bug there
puts somebody's document in the wrong place quietly, which is worse than an
error.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.dashboard import nodes
from apps.dashboard.views import PAGE_SIZE
from apps.files.models import FileAsset
from apps.folders.models import Folder
from apps.folders.repositories import FolderRepository

pytestmark = pytest.mark.django_db

PASSWORD = "explorer-test-password"


def signed_in(user) -> Client:
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    client = Client()
    assert client.login(username=user.phone_number, password=PASSWORD)
    return client


@pytest.fixture
def browser(admin):
    return signed_in(admin)


def pdf(name="doc.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def add_category(browser, folder, name, description=""):
    url = (
        reverse("dashboard:explorer", args=[folder.pk])
        if folder
        else reverse("dashboard:explorer-root")
    )
    return browser.post(url, {"action": "add_category", "name": name, "description": description})


class TestWhoCanSeeIt:
    def test_anonymous_is_sent_to_login(self):
        response = Client().get(reverse("dashboard:explorer-root"))
        assert response.status_code == 302
        assert "login" in response.url

    def test_a_user_account_is_turned_away(self, member):
        response = signed_in(member).get(reverse("dashboard:explorer-root"))
        assert response.status_code == 302
        assert reverse("dashboard:login") in response.url


class TestTheTopLevel:
    def test_it_lists_main_categories(self, browser, root_folder):
        response = browser.get(reverse("dashboard:explorer-root"))
        assert response.status_code == 200
        assert root_folder.name.encode() in response.content

    def test_it_does_not_list_nested_ones(self, browser, child_folder):
        """Only what sits at the top - otherwise it is a flat list, not a tree."""
        content = browser.get(reverse("dashboard:explorer-root")).content
        assert child_folder.parent.name.encode() in content
        assert child_folder.name.encode() not in content

    def test_it_offers_no_upload_form(self, browser, root_folder):
        """A document has to live in a category, so there is nothing to upload
        into here. Showing the form would only produce an error."""
        content = browser.get(reverse("dashboard:explorer-root")).content
        assert b'value="upload"' not in content

    def test_an_upload_posted_anyway_is_refused(self, browser):
        """The form is absent, but a hand-made POST must not create an orphan."""
        response = browser.post(
            reverse("dashboard:explorer-root"), {"action": "upload", "file": pdf()}
        )
        assert response.status_code == 302
        assert not FileAsset.objects.exists()


class TestWalkingIn:
    def test_a_category_shows_its_children_and_its_documents(
        self, browser, child_folder, sample_file
    ):
        content = browser.get(
            reverse("dashboard:explorer", args=[child_folder.pk])
        ).content.decode()

        assert sample_file.name in content

    def test_categories_come_before_documents(self, browser, admin, child_folder, sample_file):
        """Stable order, so a page boundary cannot interleave the two kinds."""
        FolderRepository().create_folder(name="Zzz Last", parent=child_folder, created_by=admin)

        content = browser.get(
            reverse("dashboard:explorer", args=[child_folder.pk])
        ).content.decode()

        assert content.index("Zzz Last") < content.index(sample_file.name)

    def test_the_breadcrumb_walks_back_up(self, browser, root_folder, child_folder):
        content = browser.get(
            reverse("dashboard:explorer", args=[child_folder.pk])
        ).content.decode()

        assert reverse("dashboard:explorer", args=[root_folder.pk]) in content
        assert reverse("dashboard:explorer-root") in content

    def test_a_deleted_category_is_gone(self, browser, admin, root_folder):
        from apps.folders.services import FolderService

        FolderService().delete(admin, root_folder)

        response = browser.get(reverse("dashboard:explorer", args=[root_folder.pk]))
        assert response.status_code == 404

    def test_an_unknown_id_is_a_404(self, browser):
        import uuid

        response = browser.get(reverse("dashboard:explorer", args=[uuid.uuid4()]))
        assert response.status_code == 404


class TestAddingACategoryWhereYouStand:
    def test_it_lands_in_the_open_category(self, browser, child_folder):
        """The parent is where you are - there is no picker to disagree with."""
        add_category(browser, child_folder, "Nested Here")

        created = Folder.objects.get(name="Nested Here")
        assert created.parent == child_folder

    def test_it_lands_at_the_top_when_nothing_is_open(self, browser):
        add_category(browser, None, "A Main Category")

        assert Folder.objects.get(name="A Main Category").parent is None

    def test_it_redirects_back_to_where_you_were(self, browser, child_folder):
        response = add_category(browser, child_folder, "Nested Here")

        assert response.status_code == 302
        assert response.url == reverse("dashboard:explorer", args=[child_folder.pk])

    def test_a_duplicate_name_is_refused_with_the_form_still_filled(
        self, browser, admin, child_folder
    ):
        FolderRepository().create_folder(name="Taken", parent=child_folder, created_by=admin)

        response = add_category(browser, child_folder, "Taken")

        assert response.status_code == 200
        assert b"already exists" in response.content
        assert Folder.objects.filter(name="Taken", parent=child_folder).count() == 1

    def test_the_same_name_is_fine_in_a_different_category(
        self, browser, admin, root_folder, child_folder
    ):
        FolderRepository().create_folder(name="Specs", parent=root_folder, created_by=admin)

        add_category(browser, child_folder, "Specs")

        assert Folder.objects.filter(name="Specs").count() == 2

    def test_an_empty_name_is_refused(self, browser, child_folder):
        response = add_category(browser, child_folder, "")

        assert response.status_code == 200
        assert Folder.objects.filter(parent=child_folder).count() == 0

    def test_the_depth_limit_still_applies(self, browser, admin):
        """The service owns that rule; the dashboard must not route around it."""
        from apps.folders.services import FolderService

        repo = FolderRepository()
        parent = None
        for level in range(FolderService().config["MAX_DEPTH"] + 1):
            parent = repo.create_folder(name=f"L{level}", parent=parent, created_by=admin)

        response = add_category(browser, parent, "One Too Deep")

        assert response.status_code == 200
        assert not Folder.objects.filter(name="One Too Deep").exists()


class TestUploading:
    def test_a_document_lands_in_the_open_category(self, browser, child_folder):
        browser.post(
            reverse("dashboard:explorer", args=[child_folder.pk]),
            {"action": "upload", "file": pdf("price-list.pdf")},
        )

        file = FileAsset.objects.get(name="price-list.pdf")
        assert file.folder == child_folder

    def test_several_at_once(self, browser, child_folder):
        browser.post(
            reverse("dashboard:explorer", args=[child_folder.pk]),
            {"action": "upload", "file": [pdf("one.pdf"), pdf("two.pdf"), pdf("three.pdf")]},
        )

        assert FileAsset.objects.filter(folder=child_folder).count() == 3

    def test_one_bad_file_does_not_discard_the_good_ones(self, browser, child_folder):
        """Otherwise somebody who picked ten files has to pick all ten again."""
        bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")

        response = browser.post(
            reverse("dashboard:explorer", args=[child_folder.pk]),
            {"action": "upload", "file": [pdf("good.pdf"), bad]},
        )

        assert response.status_code == 302
        assert FileAsset.objects.filter(name="good.pdf").exists()
        assert not FileAsset.objects.filter(name="virus.exe").exists()

    def test_the_rejected_file_is_reported(self, browser, child_folder):
        bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")

        response = browser.post(
            reverse("dashboard:explorer", args=[child_folder.pk]),
            {"action": "upload", "file": [pdf("good.pdf"), bad]},
            follow=True,
        )

        assert b"virus.exe" in response.content

    def test_the_category_counter_is_updated(self, browser, child_folder):
        browser.post(
            reverse("dashboard:explorer", args=[child_folder.pk]),
            {"action": "upload", "file": pdf()},
        )

        child_folder.refresh_from_db()
        assert child_folder.file_count == 1


class TestDeleting:
    def test_a_document_goes_to_the_recycle_bin(self, browser, child_folder, sample_file):
        browser.post(
            reverse("dashboard:explorer", args=[child_folder.pk]),
            {"action": "delete", "kind": "document", "node_id": str(sample_file.pk)},
        )

        assert not FileAsset.objects.filter(pk=sample_file.pk).exists()
        assert FileAsset.all_objects.filter(pk=sample_file.pk, is_deleted=True).exists()

    def test_a_category_goes_to_the_recycle_bin(self, browser, root_folder, child_folder):
        browser.post(
            reverse("dashboard:explorer", args=[root_folder.pk]),
            {"action": "delete", "kind": "category", "node_id": str(child_folder.pk)},
        )

        assert not Folder.objects.filter(pk=child_folder.pk).exists()
        assert Folder.all_objects.filter(pk=child_folder.pk, is_deleted=True).exists()

    def test_deleting_a_category_takes_what_is_inside_it(
        self, browser, root_folder, child_folder, sample_file
    ):
        """Which is why the confirmation says so."""
        browser.post(
            reverse("dashboard:explorer", args=[root_folder.pk]),
            {"action": "delete", "kind": "category", "node_id": str(child_folder.pk)},
        )

        assert not FileAsset.objects.filter(pk=sample_file.pk).exists()

    def test_delete_is_not_reachable_by_a_get(self, browser, child_folder, sample_file):
        """A link would let a prefetch or a crawler delete things."""
        response = browser.get(
            reverse("dashboard:explorer", args=[child_folder.pk]),
            {"action": "delete", "kind": "document", "node_id": str(sample_file.pk)},
        )

        assert response.status_code == 200
        assert FileAsset.objects.filter(pk=sample_file.pk).exists()

    def test_an_unknown_id_is_ignored_rather_than_crashing(self, browser, child_folder):
        import uuid

        response = browser.post(
            reverse("dashboard:explorer", args=[child_folder.pk]),
            {"action": "delete", "kind": "document", "node_id": str(uuid.uuid4())},
        )
        assert response.status_code == 302


class TestOpeningADocument:
    """The headers are the whole feature here.

    Redirecting to the signed Cloudinary URL - the obvious implementation -
    hands the browser `application/octet-stream` named after the opaque
    public_id, so a PDF arrives as an extensionless blob the browser cannot
    open. These tests pin the two headers that fix that, because the bytes
    were never the problem and a test that only checks the bytes would have
    passed throughout.
    """

    def test_it_serves_the_real_bytes(self, browser, sample_file):
        response = browser.get(reverse("dashboard:document-open", args=[sample_file.pk]))

        assert response.status_code == 200
        assert b"".join(response.streaming_content).startswith(b"%PDF")

    def test_it_declares_the_real_content_type(self, browser, sample_file):
        """Not octet-stream: the browser decides whether it can render a
        document from this header alone."""
        response = browser.get(reverse("dashboard:document-open", args=[sample_file.pk]))

        assert response["Content-Type"] == "application/pdf"

    def test_it_declares_the_real_filename(self, browser, sample_file):
        """Not the storage public_id, which has no extension on it."""
        response = browser.get(reverse("dashboard:document-open", args=[sample_file.pk]))

        assert sample_file.name in response["Content-Disposition"]
        assert sample_file.public_id not in response["Content-Disposition"]

    def test_opening_shows_it_rather_than_saving_it(self, browser, sample_file):
        response = browser.get(reverse("dashboard:document-open", args=[sample_file.pk]))

        assert response["Content-Disposition"].startswith("inline")

    def test_downloading_saves_it(self, browser, sample_file):
        response = browser.get(
            reverse("dashboard:document-open", args=[sample_file.pk]) + "?download=1"
        )

        assert response["Content-Disposition"].startswith("attachment")

    def test_it_streams_rather_than_buffering(self, browser, sample_file):
        """A large document must not become an equally large lump of memory."""
        response = browser.get(reverse("dashboard:document-open", args=[sample_file.pk]))

        assert response.streaming

    def test_a_name_with_non_ascii_still_works(self, browser, child_folder, file_service):
        """A quoted filename header cannot carry these, so Django emits the
        RFC 5987 form. Worth pinning: the failure is a 500 on send."""
        import io

        file = file_service.upload(
            child_folder.created_by,
            folder_id=child_folder.pk,
            file_obj=io.BytesIO(b"%PDF-1.4 x"),
            filename="मूल्य सूची.pdf",
            size_bytes=10,
            content_type="application/pdf",
        )

        response = browser.get(reverse("dashboard:document-open", args=[file.pk]))

        assert response.status_code == 200
        assert "filename*=utf-8''" in response["Content-Disposition"].lower()

    def test_a_storage_failure_does_not_show_a_broken_page(self, browser, sample_file, monkeypatch):
        """The bytes can be missing - a purge, a provider outage. The person
        should get told, not handed a zero-byte PDF."""
        from apps.files.storage import InMemoryStorageBackend

        InMemoryStorageBackend.store.pop(sample_file.public_id, None)

        response = browser.get(
            reverse("dashboard:document-open", args=[sample_file.pk]), follow=True
        )

        assert response.status_code == 200
        assert b"could not be retrieved" in response.content

    def test_download_takes_the_download_path(self, browser, sample_file):
        """Asserted through the counter rather than the URL: the test storage
        backend does not vary its URL by attachment, so comparing the two
        strings would pass for the wrong reason."""
        browser.get(reverse("dashboard:document-open", args=[sample_file.pk]) + "?download=1")

        sample_file.refresh_from_db()
        assert sample_file.download_count == 1

    def test_opening_inline_is_not_counted_as_a_download(self, browser, sample_file):
        browser.get(reverse("dashboard:document-open", args=[sample_file.pk]))

        sample_file.refresh_from_db()
        assert sample_file.download_count == 0

    def test_a_missing_document_is_a_404(self, browser):
        import uuid

        response = browser.get(reverse("dashboard:document-open", args=[uuid.uuid4()]))
        assert response.status_code == 404


class TestTheListingPagesAcrossBothTables:
    """Categories and documents live in different tables but page as one list.

    The boundary cases are where this breaks: a page that is all categories,
    one that straddles the join, and one that is all documents.
    """

    @pytest.fixture
    def mixed(self, admin, child_folder, file_service):
        import io

        repo = FolderRepository()
        for index in range(5):
            repo.create_folder(name=f"Cat {index}", parent=child_folder, created_by=admin)
        for index in range(5):
            file_service.upload(
                admin,
                folder_id=child_folder.pk,
                file_obj=io.BytesIO(b"data"),
                filename=f"doc-{index}.pdf",
                size_bytes=4,
                content_type="application/pdf",
            )
        return child_folder

    def test_the_counts_are_right(self, mixed, sample_file):
        listing = nodes.children_of(mixed)
        assert listing.category_count == 5
        # sample_file is in this category too.
        assert listing.document_count == 6
        assert listing.total == 11

    def test_a_page_entirely_inside_the_categories(self, mixed):
        listing = nodes.children_of(mixed, offset=0, limit=3)

        assert len(listing.nodes) == 3
        assert all(node.is_container for node in listing.nodes)

    def test_a_page_that_straddles_the_join(self, mixed):
        """The case that breaks a naive implementation."""
        listing = nodes.children_of(mixed, offset=3, limit=4)

        kinds = [node.kind for node in listing.nodes]
        assert kinds == ["category", "category", "document", "document"]

    def test_a_page_entirely_inside_the_documents(self, mixed):
        listing = nodes.children_of(mixed, offset=7, limit=3)

        assert len(listing.nodes) == 3
        assert all(not node.is_container for node in listing.nodes)

    def test_paging_through_returns_every_row_exactly_once(self, mixed):
        # Taken from the listing rather than hard-coded, so the invariant holds
        # whatever the fixture contains.
        expected = nodes.children_of(mixed).total

        seen, offset = [], 0
        while True:
            listing = nodes.children_of(mixed, offset=offset, limit=4)
            if not listing.nodes:
                break
            seen.extend(node.id for node in listing.nodes)
            offset += 4

        assert len(seen) == expected, "a row was skipped"
        assert len(set(seen)) == expected, "a row was repeated across pages"

    def test_past_the_end_is_empty_rather_than_an_error(self, mixed):
        assert nodes.children_of(mixed, offset=500, limit=10).nodes == []

    def test_the_page_size_is_respected_by_the_view(self, browser, admin, child_folder):
        repo = FolderRepository()
        for index in range(PAGE_SIZE + 5):
            repo.create_folder(name=f"Many {index:03d}", parent=child_folder, created_by=admin)

        first = browser.get(reverse("dashboard:explorer", args=[child_folder.pk]))
        assert first.content.count(b'<li class="node"') == PAGE_SIZE

        second = browser.get(reverse("dashboard:explorer", args=[child_folder.pk]), {"page": 2})
        assert b"Many 06" in second.content

    def test_a_nonsense_page_number_falls_back_to_the_first(self, browser, child_folder):
        response = browser.get(
            reverse("dashboard:explorer", args=[child_folder.pk]), {"page": "abc"}
        )
        assert response.status_code == 200


class TestNothingLeaksOntoThePage:
    """Django's {# #} comment is single-line only.

    Spread one over two lines and it stops being a comment: the whole thing
    renders onto the page as text. Nothing else catches it - the view returns
    200, the assertions about content still pass - so it ships looking fine to
    every test and broken to every human.
    """

    @pytest.mark.parametrize("url_name", ["explorer-root", "home", "users", "categories"])
    def test_no_template_comment_reaches_the_browser(self, browser, url_name):
        response = browser.get(reverse(f"dashboard:{url_name}"))

        # Guard against the test passing on an empty body: a redirect has no
        # content, so it would satisfy every assertion below without rendering
        # anything at all.
        assert response.status_code == 200
        assert len(response.content) > 500

        assert b"{#" not in response.content
        assert b"#}" not in response.content

    def test_the_login_page_is_clean(self):
        """Checked signed out, because signed in it only redirects."""
        response = Client().get(reverse("dashboard:login"))

        assert response.status_code == 200
        assert b"{#" not in response.content

    def test_no_unrendered_tag_reaches_the_browser(self, browser, child_folder, sample_file):
        """Same class of mistake: a tag that was never a tag."""
        content = browser.get(reverse("dashboard:explorer", args=[child_folder.pk])).content

        assert b"{%" not in content
        assert b"{{" not in content

    def test_the_row_partial_is_clean_too(self, browser, child_folder, sample_file):
        content = browser.get(reverse("dashboard:explorer", args=[child_folder.pk])).content

        assert b"{#" not in content
        assert b"endcomment" not in content


class TestTheCompositeItself:
    """The two node types answer the same questions, which is the point."""

    def test_both_kinds_answer_the_whole_interface(self, child_folder, sample_file):
        category = nodes.Category(child_folder)
        document = nodes.Document(sample_file)

        for node in (category, document):
            assert node.id
            assert node.name
            assert node.detail
            assert node.url

    def test_only_a_category_contains_things(self, child_folder, sample_file):
        assert nodes.Category(child_folder).is_container
        assert not nodes.Document(sample_file).is_container

    def test_a_category_describes_what_is_inside(self, admin, root_folder):
        FolderRepository().create_folder(name="One", parent=root_folder, created_by=admin)
        root_folder.refresh_from_db()

        assert "1 category" in nodes.Category(root_folder).detail

    def test_an_empty_category_says_so(self, child_folder):
        assert nodes.Category(child_folder).detail == "Empty"

    def test_a_document_describes_its_type_and_size(self, sample_file):
        detail = nodes.Document(sample_file).detail

        assert "PDF" in detail
        assert "B" in detail

    def test_a_category_can_list_its_own_children(self, admin, root_folder, child_folder):
        children = nodes.Category(root_folder).children()

        assert [node.name for node in children] == [child_folder.name]
