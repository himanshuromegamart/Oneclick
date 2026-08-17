"""Admin dashboard.

A small server-rendered console for the jobs that need doing outside the mobile
app: adding people, and shaping the category tree.

Session-authenticated rather than JWT - it is a browser, and a token in
JavaScript would be a step down from an HttpOnly cookie.

Every write goes through the same services the API uses. The dashboard never
touches the ORM to create or delete something, so the two interfaces cannot
disagree about what is allowed.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.models import User
from apps.core.exceptions import DomainError
from apps.dashboard import nodes
from apps.dashboard.forms import (
    CategoryForm,
    InlineCategoryForm,
    LoginForm,
    UploadForm,
    UserForm,
)
from apps.files.models import FileAsset
from apps.files.services import FileService
from apps.folders.models import Folder
from apps.folders.services import FolderService

#: Rows per explorer page. Generous, because scrolling beats paging when you
#: are looking for something by name, but bounded so one enormous category
#: cannot render a page that never finishes.
PAGE_SIZE = 60

logger = logging.getLogger(__name__)


def _is_admin(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.is_admin)


def admin_required(view):
    """Only admins reach the dashboard. Users get the mobile app and nothing else.

    This is the single restriction the two roles express, so it is enforced in
    exactly two places - here and in :func:`login_view` - and both call the
    same predicate.

    Checked on every request rather than only at login, so demoting somebody to
    User, or disabling them, takes effect on their next click instead of
    whenever their session happens to expire.
    """

    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect(f"{reverse('dashboard:login')}?next={request.path}")
        if not _is_admin(request.user):
            messages.error(
                request,
                "That account is for the mobile app. Only an admin can open this dashboard.",
            )
            logout(request)
            return redirect("dashboard:login")
        return view(request, *args, **kwargs)

    wrapper.__name__ = view.__name__
    wrapper.__doc__ = view.__doc__
    return wrapper


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if _is_admin(request.user):
        return redirect("dashboard:home")

    form = LoginForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["phone_number"],
            password=form.cleaned_data["password"],
        )
        if user is None or not _is_admin(user):
            # One message for every failure - wrong number, wrong password,
            # disabled account, and a correct password on a User account.
            # Being specific would turn this form into a way to find out which
            # numbers have accounts, and which of those are admins.
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
@admin_required
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
@admin_required
@require_http_methods(["GET", "POST"])
def users(request: HttpRequest) -> HttpResponse:
    form = UserForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.save()
        logger.info(
            "dashboard_user_created",
            extra={"actor_id": str(request.user.pk), "user_id": str(user.pk)},
        )
        where = "the app and this dashboard" if user.is_admin else "the mobile app"
        messages.success(
            request,
            f"{user.full_name} can now sign in to {where} "
            f"with {user.phone_number} and that password.",
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
@admin_required
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


# ---------------------------------------------------------------------------
# Explorer - walk into a category and work inside it
# ---------------------------------------------------------------------------
def _folder_or_404(folder_id) -> Folder:
    folder = Folder.objects.filter(pk=folder_id).select_related("parent").first()
    if folder is None:
        # A deleted category is gone as far as the explorer is concerned; the
        # recycle bin is where it can be found.
        raise Http404("Category not found.")
    return folder


def _add_category(request: HttpRequest, here: Folder | None) -> HttpResponse | None:
    """Returns a redirect on success, or None to fall through and re-render."""
    form = InlineCategoryForm(request.POST, parent=here)
    if not form.is_valid():
        return None

    try:
        # Through the service, so the depth limit, name rules and cycle checks
        # that protect the API apply to the dashboard identically.
        folder = FolderService().create(
            request.user,
            {
                "name": form.cleaned_data["name"],
                "parent_id": here.pk if here else None,
                "description": form.cleaned_data.get("description", ""),
            },
        )
    except DomainError as exc:
        form.add_error(None, str(exc.detail))
        return None

    messages.success(request, f"“{folder.name}” added.")
    return _redirect_here(here)


def _upload(request: HttpRequest, here: Folder | None) -> HttpResponse | None:
    if here is None:
        # Not reachable through the UI - the form is only rendered inside a
        # category - but a hand-made POST must not create an orphan.
        messages.error(request, "Open a category first, then upload into it.")
        return _redirect_here(None)

    uploads = request.FILES.getlist("file")
    if not uploads:
        return None

    service = FileService()
    saved, failed = [], []

    for upload in uploads:
        try:
            file = service.upload(
                request.user,
                folder_id=here.pk,
                file_obj=upload.file,
                filename=upload.name,
                size_bytes=upload.size,
                content_type=upload.content_type or "",
            )
        except DomainError as exc:
            # One bad file in a batch must not discard the good ones - the
            # person picked ten and would have to re-pick all ten.
            failed.append(f"{upload.name}: {exc.detail}")
        else:
            saved.append(file.name)

    if saved:
        messages.success(
            request,
            f"Uploaded {saved[0]}." if len(saved) == 1 else f"Uploaded {len(saved)} documents.",
        )
    for problem in failed:
        messages.error(request, problem)

    return _redirect_here(here)


def _delete_node(request: HttpRequest, here: Folder | None) -> HttpResponse:
    """Recycle-bin delete for either kind of node - nothing is destroyed here."""
    kind = request.POST.get("kind")
    node_id = request.POST.get("node_id")

    if kind == "category":
        folder = Folder.objects.filter(pk=node_id).first()
        if folder is not None:
            try:
                FolderService().delete(request.user, folder)
            except DomainError as exc:
                messages.error(request, str(exc.detail))
            else:
                messages.success(request, f"“{folder.name}” moved to the recycle bin.")
    elif kind == "document":
        file = FileAsset.objects.filter(pk=node_id).first()
        if file is not None:
            FileService().delete(request.user, file)
            messages.success(request, f"“{file.name}” moved to the recycle bin.")

    return _redirect_here(here)


def _redirect_here(here: Folder | None) -> HttpResponse:
    if here is None:
        return redirect("dashboard:explorer-root")
    return redirect("dashboard:explorer", folder_id=here.pk)


@never_cache
@admin_required
@require_http_methods(["GET", "POST"])
def explorer(request: HttpRequest, folder_id=None) -> HttpResponse:
    """Open a category: what is inside it, and the tools to add more.

    One view serves the top level and every category below it. They are the
    same screen - the top level is just the category with no parent - so
    splitting them would mean two of everything and one of them going stale.
    """
    here = _folder_or_404(folder_id) if folder_id else None

    category_form = InlineCategoryForm(parent=here)
    upload_form = UploadForm()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_category":
            redirect_to = _add_category(request, here)
            if redirect_to is not None:
                return redirect_to
            # Failed: re-render with the errors attached and the panel open.
            category_form = InlineCategoryForm(request.POST, parent=here)
        elif action == "upload":
            redirect_to = _upload(request, here)
            if redirect_to is not None:
                return redirect_to
            upload_form = UploadForm(request.POST, request.FILES)
        elif action == "delete":
            return _delete_node(request, here)

    try:
        page = max(int(request.GET.get("page", 1)), 1)
    except ValueError:
        page = 1

    listing = nodes.children_of(here, offset=(page - 1) * PAGE_SIZE, limit=PAGE_SIZE)

    return render(
        request,
        "dashboard/explorer.html",
        {
            "active": "explorer",
            "here": here,
            "breadcrumb": FolderService().breadcrumb(here) if here else [],
            "listing": listing,
            "category_form": category_form,
            "upload_form": upload_form,
            "page": page,
            "page_size": PAGE_SIZE,
            "next_page": page + 1,
            "previous_page": page - 1,
        },
    )


@never_cache
@admin_required
def document_open(request: HttpRequest, file_id) -> HttpResponse:
    """Hand the browser a signed URL for the stored file.

    Redirecting rather than embedding the URL in the listing: signed links are
    short-lived, so one minted per click always works, while one minted when
    the page was drawn would expire while somebody read the list.

    ``?download=1`` asks for it as an attachment; without it the browser shows
    a PDF or an image inline, which is what you want when checking a document
    is the right one.
    """
    file = FileAsset.objects.filter(pk=file_id).first()
    if file is None:
        raise Http404("Document not found.")

    service = FileService()
    if request.GET.get("download"):
        payload = service.download_url(request.user, file)
    else:
        payload = service.preview_url(request.user, file)

    return redirect(payload["url"])
