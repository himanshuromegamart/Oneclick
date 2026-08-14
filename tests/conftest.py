"""Shared pytest fixtures.

The suite runs against a real Postgres (ArrayField, tsvector and the partial
unique constraints have no SQLite equivalent) but never touches Cloudinary,
Redis or the SMS gateway - those are swapped for in-memory doubles by
``config.settings.testing``.
"""

from __future__ import annotations

import io

import pytest
from django.core.cache import cache

from apps.accounts.constants import UserRole
from apps.accounts.models import User
from apps.accounts.sms import InMemorySMSBackend
from apps.files.storage import InMemoryStorageBackend


@pytest.fixture(autouse=True)
def _isolate_state():
    """Reset every piece of cross-test global state.

    Without this, a throttle counter or an OTP cooldown from one test silently
    changes the next - the classic "passes alone, fails in the suite" bug.
    """
    cache.clear()
    InMemorySMSBackend.clear()
    InMemoryStorageBackend.clear()
    yield
    cache.clear()
    InMemorySMSBackend.clear()
    InMemoryStorageBackend.clear()


@pytest.fixture
def admin(db) -> User:
    """An account that can open the dashboard."""
    return User.objects.create_user(
        phone_number="9000000001", full_name="Sarah Admin", role=UserRole.ADMIN
    )


@pytest.fixture
def member(db) -> User:
    """A mobile-app account. Same powers in the app; no dashboard."""
    return User.objects.create_user(
        phone_number="9000000002", full_name="Ramesh Kumar", role=UserRole.USER
    )


@pytest.fixture
def other_member(db) -> User:
    """A second app account, for "can A touch B's file" tests."""
    return User.objects.create_user(
        phone_number="9000000003", full_name="Priya Sharma", role=UserRole.USER
    )


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


def _client_for(user: User):
    from rest_framework.test import APIClient

    from apps.accounts.services import AuthService

    client = APIClient()
    tokens = AuthService().issue_tokens(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.access}")
    client.user = user
    return client


@pytest.fixture
def admin_client(admin):
    return _client_for(admin)


@pytest.fixture
def member_client(member):
    return _client_for(member)


@pytest.fixture
def root_folder(db, admin):
    """A top-level category."""
    from apps.folders.repositories import FolderRepository

    return FolderRepository().create_folder(name="Quotation", parent=None, created_by=admin)


@pytest.fixture
def child_folder(root_folder, admin):
    """A subcategory inside it."""
    from apps.folders.repositories import FolderRepository

    return FolderRepository().create_folder(name="Water ATM", parent=root_folder, created_by=admin)


@pytest.fixture
def file_service():
    from apps.files.services import FileService

    return FileService(storage=InMemoryStorageBackend())


@pytest.fixture
def sample_file(child_folder, member, file_service):
    """Uploaded by `member`, so tests can check what `admin` may do to it."""
    return file_service.upload(
        member,
        folder_id=child_folder.pk,
        file_obj=io.BytesIO(b"%PDF-1.4 sample specification"),
        filename="spec-500-lph.pdf",
        size_bytes=29,
        content_type="application/pdf",
        tags=["spec", "500lph"],
    )
