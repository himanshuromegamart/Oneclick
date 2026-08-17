from __future__ import annotations

from django.urls import path

from apps.dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("users/", views.users, name="users"),
    path("categories/", views.categories, name="categories"),
    # The explorer. The top level and a category share one view, because they
    # are the same screen - the top level is simply the category that has no
    # parent and holds no documents.
    path("explorer/", views.explorer, name="explorer-root"),
    path("explorer/<uuid:folder_id>/", views.explorer, name="explorer"),
    path("documents/<uuid:file_id>/open/", views.document_open, name="document-open"),
    path("search/", views.search, name="search"),
]
