"""Admin dashboard.

A small server-rendered console for the two jobs that need doing outside the
mobile app: adding people, and shaping the category tree.

Session-authenticated rather than JWT - it is a browser, and a token in
JavaScript would be a step down from an HttpOnly cookie.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.models import User
from apps.core.exceptions import DomainError
from apps.dashboard.forms import CategoryForm, LoginForm, UserForm
from apps.files.models import FileAsset
from apps.folders.models import Folder
from apps.folders.services import FolderService

logger = logging.getLogger(__name__)


def _is_owner(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.is_owner)


def owner_required(view):
    """Only owners reach the dashboard.

    Checked on every request rather than at login, so disabling an account
    takes effect immediately instead of when their session happens to expire.
    """

    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect(f"{reverse('dashboard:login')}?next={request.path}")
        if not _is_owner(request.user):
            messages.error(request, "Your account does not have dashboard access.")
            logout(request)
            return redirect("dashboard:login")
        return view(request, *args, **kwargs)

    wrapper.__name__ = view.__name__
    wrapper.__doc__ = view.__doc__
    return wrapper


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if _is_owner(request.user):
        return redirect("dashboard:home")

    form = LoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["phone_number"],
            password=form.cleaned_data["password"],
        )
        if user is None or not _is_owner(user):
            # One message for every failure - wrong number, wrong password,
            # disabled account. Being specific would turn this form into a way
            # to find out which numbers exist.
            form.add_error(None, "Incorrect mobile number or password.")
            logger.info("dashboard_login_failed", extra={"ip": request.META.get("REMOTE_ADDR")})
        else:
            login(request, user)
            logger.info("dashboard_login", extra={"user_id": str(user.pk)})
            return redirect(request.GET.get("next") or "dashboard:home")

    return render(request, "dashboard/login.html", {"form": form})


@never_cache
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    messages.success(request, "Signed out.")
    return redirect("dashboard:login")


@never_cache
@owner_required
def home(request: HttpRequest) -> HttpResponse:
    """Landing page: the numbers, and the most recent activity."""
    recent_categories = Folder.objects.select_related("parent", "created_by").order_by(
        "-created_at"
    )[:6]
    recent_files = FileAsset.objects.select_related("folder").order_by("-created_at")[:6]

    context = {
        "stats": {
            "users": User.objects.count(),
            "categories": Folder.objects.count(),
            "top_level": Folder.objects.filter(parent__isnull=True).count(),
            "documents": FileAsset.objects.count(),
        },
        "recent_categories": recent_categories,
        "recent_files": recent_files,
        "active": "home",
    }
    return render(request, "dashboard/home.html", context)


@never_cache
@owner_required
@require_http_methods(["GET", "POST"])
def users(request: HttpRequest) -> HttpResponse:
    form = UserForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.save()
        logger.info(
            "dashboard_user_created",
            extra={"actor_id": str(request.user.pk), "user_id": str(user.pk)},
        )
        messages.success(
            request,
            f"{user.full_name} can now sign in with {user.phone_number} and that password.",
        )
        return redirect("dashboard:users")

    return render(
        request,
        "dashboard/users.html",
        {
            "form": form,
            "users": User.objects.order_by("-created_at"),
            "active": "users",
        },
    )


@never_cache
@owner_required
@require_http_methods(["GET", "POST"])
def categories(request: HttpRequest) -> HttpResponse:
    form = CategoryForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            # Through the service, so the tree rules that protect the API -
            # depth limit, duplicate names, cycles - apply here too.
            folder = FolderService().create(
                request.user,
                {
                    "name": form.cleaned_data["name"],
                    "parent_id": (
                        form.cleaned_data["parent"].pk if form.cleaned_data["parent"] else None
                    ),
                    "description": form.cleaned_data.get("description", ""),
                },
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
        else:
            where = folder.parent.name if folder.parent else "the top level"
            messages.success(request, f"“{folder.name}” added to {where}.")
            return redirect("dashboard:categories")

    tree = (
        Folder.objects.select_related("parent", "created_by")
        .annotate(
            document_count=Count("files", filter=Q(files__is_deleted=False), distinct=True),
            child_count=Count("children", filter=Q(children__is_deleted=False), distinct=True),
        )
        .order_by("path", "position", "name")
    )

    return render(
        request,
        "dashboard/categories.html",
        {"form": form, "tree": tree, "active": "categories"},
    )
