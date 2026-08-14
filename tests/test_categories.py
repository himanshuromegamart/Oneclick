"""Categories and subcategories.

A category *is* a folder: "Quotation", "Water ATM" and "500 LPH" are the same
kind of row at different depths, so nesting is unlimited and there is no fixed
top-level list.

The invariants under test are the ones whose violation corrupts the structure:
cycles (a branch orphaned from the root), stale paths after a move (children
that become unreachable), and duplicate sibling names.
"""

from __future__ import annotations

import pytest

from apps.core.exceptions import ConflictError, ValidationFailed
from apps.folders.models import Folder
from apps.folders.repositories import FolderRepository
from apps.folders.services import FolderService

pytestmark = pytest.mark.django_db


@pytest.fixture
def service() -> FolderService:
    return FolderService()


@pytest.fixture
def tree(admin) -> dict[str, Folder]:
    """Quotation > Water ATM > 500 LPH, plus a sibling and a second root."""
    repo = FolderRepository()
    quotation = repo.create_folder(name="Quotation", parent=None, created_by=admin)
    water_atm = repo.create_folder(name="Water ATM", parent=quotation, created_by=admin)
    lph_500 = repo.create_folder(name="500 LPH", parent=water_atm, created_by=admin)
    cooler = repo.create_folder(name="Water Cooler", parent=quotation, created_by=admin)
    documents = repo.create_folder(name="Documents", parent=None, created_by=admin)
    return {
        "quotation": quotation,
        "water_atm": water_atm,
        "lph_500": lph_500,
        "cooler": cooler,
        "documents": documents,
    }


class TestNesting:
    def test_top_level_category_sits_at_depth_zero(self, root_folder):
        assert root_folder.depth == 0
        assert root_folder.is_root

    def test_subcategory_records_its_parent_in_the_path(self, tree):
        assert tree["water_atm"].depth == 1
        assert tree["water_atm"].path == f"/{tree['quotation'].pk}/"

    def test_third_level_records_the_whole_chain(self, tree):
        lph = tree["lph_500"]
        assert lph.depth == 2
        assert str(tree["quotation"].pk) in lph.path
        assert str(tree["water_atm"].pk) in lph.path

    def test_nesting_is_not_limited_to_two_levels(self, admin):
        """Categories inside categories inside categories, as deep as needed."""
        repo = FolderRepository()
        parent = repo.create_folder(name="Level 0", parent=None, created_by=admin)
        for level in range(1, 15):
            parent = repo.create_folder(name=f"Level {level}", parent=parent, created_by=admin)

        assert parent.depth == 14
        assert len(FolderRepository().ancestors(parent)) == 14

    def test_depth_ceiling_is_enforced(self, admin, service, settings):
        """Unlimited in practice, but bounded so a runaway client cannot
        overflow the stored path."""
        settings.FOLDER_SETTINGS = {**settings.FOLDER_SETTINGS, "MAX_DEPTH": 3}
        repo = FolderRepository()

        parent = repo.create_folder(name="L0", parent=None, created_by=admin)
        for level in range(1, 4):
            parent = repo.create_folder(name=f"L{level}", parent=parent, created_by=admin)

        with pytest.raises(ValidationFailed):
            service.create(admin, {"name": "Too deep", "parent_id": parent.pk})


class TestNaming:
    def test_duplicate_sibling_name_is_rejected(self, tree, admin, service):
        with pytest.raises(ConflictError):
            service.create(admin, {"name": "Water ATM", "parent_id": tree["quotation"].pk})

    def test_duplicate_check_ignores_case(self, tree, admin, service):
        with pytest.raises(ConflictError):
            service.create(admin, {"name": "water atm", "parent_id": tree["quotation"].pk})

    def test_same_name_is_fine_under_a_different_parent(self, tree, admin, service):
        folder = service.create(admin, {"name": "Water ATM", "parent_id": tree["documents"].pk})
        assert folder.name == "Water ATM"

    def test_deleted_sibling_does_not_block_the_name(self, tree, admin, service):
        service.delete(admin, tree["water_atm"])
        recreated = service.create(admin, {"name": "Water ATM", "parent_id": tree["quotation"].pk})
        assert recreated.pk != tree["water_atm"].pk


class TestMove:
    def test_move_carries_the_children_with_it(self, tree, admin, service):
        service.move(admin, tree["water_atm"], tree["documents"].pk)

        lph = Folder.objects.get(pk=tree["lph_500"].pk)
        moved = Folder.objects.get(pk=tree["water_atm"].pk)

        # The grandchild must follow, or it becomes unreachable from the root.
        assert lph.path == moved.subtree_prefix
        assert str(tree["documents"].pk) in lph.path
        assert str(tree["quotation"].pk) not in lph.path

    def test_move_to_top_level(self, tree, admin, service):
        moved = service.move(admin, tree["water_atm"], None)
        assert moved.parent_id is None
        assert moved.depth == 0
        assert Folder.objects.get(pk=tree["lph_500"].pk).depth == 1

    def test_category_cannot_move_into_itself(self, tree, admin, service):
        with pytest.raises(ValidationFailed):
            service.move(admin, tree["water_atm"], tree["water_atm"].pk)

    def test_category_cannot_move_into_its_own_subcategory(self, tree, admin, service):
        # The cycle that would orphan the whole branch.
        with pytest.raises(ValidationFailed):
            service.move(admin, tree["water_atm"], tree["lph_500"].pk)

    def test_move_into_a_clashing_name_is_rejected(self, tree, admin, service):
        FolderRepository().create_folder(
            name="Water ATM", parent=tree["documents"], created_by=admin
        )
        with pytest.raises(ConflictError):
            service.move(admin, tree["water_atm"], tree["documents"].pk)


class TestDeleteAndRestore:
    def test_delete_recycles_the_whole_branch(self, tree, admin, service):
        service.delete(admin, tree["water_atm"])

        assert not Folder.objects.filter(pk=tree["water_atm"].pk).exists()
        assert not Folder.objects.filter(pk=tree["lph_500"].pk).exists()
        # A sibling branch is untouched.
        assert Folder.objects.filter(pk=tree["cooler"].pk).exists()

    def test_delete_also_recycles_the_files_inside(
        self, child_folder, member, admin, file_service, service
    ):
        import io

        file = file_service.upload(
            member,
            folder_id=child_folder.pk,
            file_obj=io.BytesIO(b"data"),
            filename="inside.pdf",
            size_bytes=4,
        )
        service.delete(admin, child_folder)

        from apps.files.models import FileAsset

        assert not FileAsset.objects.filter(pk=file.pk).exists()

    def test_restore_brings_the_branch_back(self, tree, admin, service):
        service.delete(admin, tree["water_atm"])
        service.restore(admin, Folder.all_objects.get(pk=tree["water_atm"].pk))

        assert Folder.objects.filter(pk=tree["water_atm"].pk).exists()
        assert Folder.objects.filter(pk=tree["lph_500"].pk).exists()

    def test_restore_is_blocked_while_the_parent_is_deleted(self, tree, admin, service):
        service.delete(admin, tree["quotation"])

        with pytest.raises(ConflictError):
            service.restore(admin, Folder.all_objects.get(pk=tree["water_atm"].pk))

    def test_restore_renames_around_a_name_taken_meanwhile(self, tree, admin, service):
        service.delete(admin, tree["water_atm"])
        FolderRepository().create_folder(
            name="Water ATM", parent=tree["quotation"], created_by=admin
        )

        restored = service.restore(admin, Folder.all_objects.get(pk=tree["water_atm"].pk))
        assert restored.name == "Water ATM (restored)"


class TestReads:
    def test_breadcrumb_is_root_first_and_includes_self(self, tree, service):
        trail = service.breadcrumb(tree["lph_500"])
        assert [item["name"] for item in trail] == ["Quotation", "Water ATM", "500 LPH"]

    def test_tree_nests_children_under_parents(self, tree, service):
        by_name = {node["name"]: node for node in service.tree()}

        assert "Quotation" in by_name
        assert {child["name"] for child in by_name["Quotation"]["children"]} == {
            "Water ATM",
            "Water Cooler",
        }

    def test_tree_can_be_depth_limited(self, tree, service):
        assert "500 LPH" not in {node["name"] for node in service.tree(max_depth=1)}

    def test_statistics_count_the_whole_subtree(self, tree, service):
        stats = service.statistics(tree["quotation"])
        assert stats["subfolder_count"] == 3
        assert stats["direct_subfolder_count"] == 2


class TestCategoryEndpoints:
    def test_create_a_category_and_a_subcategory(self, admin_client):
        response = admin_client.post("/api/v1/categories/", {"name": "Certificates"}, format="json")
        assert response.status_code == 201
        parent_id = response.data["data"]["id"]

        response = admin_client.post(
            "/api/v1/categories/", {"name": "ISO", "parent_id": parent_id}, format="json"
        )
        assert response.status_code == 201
        assert response.data["data"]["depth"] == 1

    def test_list_top_level_categories(self, admin_client, root_folder):
        response = admin_client.get("/api/v1/categories/")
        assert response.status_code == 200
        assert len(response.data["data"]) == 1

    def test_tree_endpoint(self, admin_client, tree):
        response = admin_client.get("/api/v1/categories/tree/")
        assert response.status_code == 200
        assert len(response.data["data"]) == 2  # two top-level categories

    def test_breadcrumb_endpoint(self, admin_client, tree):
        response = admin_client.get(f"/api/v1/categories/{tree['lph_500'].pk}/breadcrumb/")
        assert response.status_code == 200
        assert len(response.data["data"]) == 3

    def test_cycle_move_returns_a_stable_error_code(self, admin_client, tree):
        response = admin_client.post(
            f"/api/v1/categories/{tree['water_atm'].pk}/move/",
            {"parent_id": str(tree["lph_500"].pk)},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "FOLDER_CYCLE"

    def test_a_user_can_delete_a_category_someone_else_made(self, member_client, root_folder):
        # root_folder was created by `admin`. There is no ownership rule any
        # more - the roles differ by dashboard access alone.
        response = member_client.delete(f"/api/v1/categories/{root_folder.pk}/")
        assert response.status_code == 200

    def test_a_user_can_delete_their_own_category(self, member_client):
        created = member_client.post("/api/v1/categories/", {"name": "Mine"}, format="json")
        folder_id = created.data["data"]["id"]

        assert member_client.delete(f"/api/v1/categories/{folder_id}/").status_code == 200
