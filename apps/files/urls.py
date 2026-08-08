from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.files.views import (
    BrowseView,
    FileViewSet,
    PublicShareView,
    SearchView,
    ShareLinkViewSet,
)

app_name = "files"

router = DefaultRouter()
# Exposed as "documents" - the business word for what these are.
router.register("documents", FileViewSet, basename="document")
router.register("share-links", ShareLinkViewSet, basename="share-link")

urlpatterns = [
    path("", include(router.urls)),
    # The main screen of the app: one call returns a folder's subfolders and
    # files together, plus its breadcrumb.
    path("browse/", BrowseView.as_view(), name="browse"),
    path("search/", SearchView.as_view(), name="search"),
    path("share/<str:token>/", PublicShareView.as_view(), name="public-share"),
]
