from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.folders.views import FolderViewSet

app_name = "folders"

router = DefaultRouter()
# Exposed as "categories" because that is what the business calls them. A
# category, a subcategory and a nested folder are the same row at different
# depths, so they share one route - `parent_id` is what distinguishes them.
router.register("categories", FolderViewSet, basename="category")

urlpatterns = [path("", include(router.urls))]
