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
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Q
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
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
from apps.files.models import FileAsset, guess_mime_type
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

    noun = "Subcategory" if here else "Category"
    messages.success(request, f"{noun} “{folder.name}” added.")
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


def _perform_delete(request: HttpRequest) -> None:
    """Recycle-bin delete for either kind of node - nothing is destroyed here.

    Split from the redirect because the same row partial, and so the same
    form, is rendered by both the explorer and the search results, and each
    needs to send you back where you were.
    """
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
            _perform_delete(request)
            return _redirect_here(here)

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
            # What to call the things being listed and added. Inside a category
            # they are subcategories; at the top level they are categories. The
            # model does not distinguish them - depth is the only difference -
            # but the screen should, because that is the word the person using
            # it would use. Decided here so the template does not repeat the
            # same {% if %} five times.
            "child_noun": "subcategory" if here else "category",
            # Stem for the {{ n|pluralize:"y,ies" }} filter, which needs the
            # word without its final "y".
            "child_stem": "subcategor" if here else "categor",
            "page": page,
            "page_size": PAGE_SIZE,
            "next_page": page + 1,
            "previous_page": page - 1,
        },
    )


@never_cache
@admin_required
@require_http_methods(["GET", "POST"])
def search(request: HttpRequest) -> HttpResponse:
    """Find anything, anywhere in the tree.

    Global by design: the explorer answers "what is in here", and this answers
    "where is that" - which is the question you have when you cannot remember
    which category something went into, and the only one the tree cannot
    answer by itself.

    The same Postgres search the mobile app uses, so a term that finds
    something on a phone finds it here, typo tolerance included.
    """
    term = request.GET.get("q", "")

    # The results use the same row partial as the explorer, delete button and
    # all, and that form posts back to whatever page drew it. Handling it here
    # is what stops the button being decoration on this page.
    if request.method == "POST" and request.POST.get("action") == "delete":
        _perform_delete(request)
        term = request.POST.get("q", term)
        return redirect(f"{reverse('dashboard:search')}?q={quote(term)}")

    results = nodes.search(term)

    return render(
        request,
        "dashboard/search.html",
        {"active": "search", "term": term.strip(), "results": results},
    )


#: Rows in the type-ahead panel. Small on purpose: a dropdown is for
#: recognising the thing you already had in mind, not for browsing. Anything
#: longer belongs on the full results page.
SUGGEST_LIMIT = 8

#: Below this, a term matches too much to be worth a round trip - one letter
#: would return the first eight rows of the database and teach nothing.
SUGGEST_MIN_LENGTH = 2


@never_cache
@admin_required
def search_suggest(request: HttpRequest) -> JsonResponse:
    """Type-ahead results for the search box, as JSON.

    Separate from the search page rather than the same view content-negotiating,
    because the two answer different questions: this one is deliberately short
    and shaped for one row of a dropdown.
    """
    term = request.GET.get("q", "").strip()
    if len(term) < SUGGEST_MIN_LENGTH:
        return JsonResponse({"q": term, "results": [], "more_url": ""})

    found = nodes.search(term, limit=SUGGEST_LIMIT)

    # Categories first, same order as everywhere else, then documents fill
    # whatever room is left.
    hits = (found.categories + found.documents)[:SUGGEST_LIMIT]

    return JsonResponse(
        {
            "q": term,
            "results": [
                {
                    "kind": hit.node.kind,
                    "tone": hit.node.tone,
                    "name": hit.node.name,
                    "detail": hit.node.detail,
                    "location": hit.location,
                    "url": hit.node.url,
                    "is_container": hit.node.is_container,
                }
                for hit in hits
            ],
            "more_url": f"{reverse('dashboard:search')}?q={quote(term)}",
        }
    )


@never_cache
@admin_required
def document_open(request: HttpRequest, file_id) -> HttpResponse:
    """Serve the document from our own domain, with its real type and name.

    The obvious implementation redirects to the signed Cloudinary URL, and it
    does not work for the documents this product is mostly made of. Cloudinary
    keeps PDF, Word and Excel as *raw* assets and serves them from its download
    API, which answers:

        Content-Type: application/octet-stream
        Content-Disposition: attachment; filename="764c3f7f6eb14a38…"

    - whatever the file actually is. So the browser cannot open a PDF (it has
    not been told it is one) and saves an extensionless blob named after the
    opaque public_id instead. The bytes were always correct; only the headers
    were wrong.

    Streaming it ourselves is what lets us state ``application/pdf`` and
    ``price-list.pdf``. The cost is that the bytes pass through this server
    rather than going straight from the CDN, which for internal documents of a
    few megabytes is a fair trade for links that work.

    ``?download=1`` saves instead of showing.
    """
    file = FileAsset.objects.filter(pk=file_id).first()
    if file is None:
        raise Http404("Document not found.")

    # Some types are never safe to display from our own domain, whatever was
    # asked for: an uploaded HTML or SVG page rendered here would be running
    # script on the origin that holds the admin's session. Downloading it is
    # harmless, so that is the only option those get.
    forced = file.extension.lower() in {
        item.lower() for item in settings.STORAGE_SETTINGS["NEVER_INLINE_EXTENSIONS"]
    }
    as_attachment = bool(request.GET.get("download")) or forced

    try:
        chunks = FileService().open_stream(request.user, file, as_attachment=as_attachment)
    except DomainError as exc:
        messages.error(request, str(exc.detail))
        return _redirect_here(file.folder)

    response = StreamingHttpResponse(
        chunks,
        # A forced-download type is sent as bytes rather than under its real
        # type, so no browser can be tempted to render it anyway.
        content_type=(
            "application/octet-stream" if forced else (file.mime_type or guess_mime_type(file.name))
        ),
    )
    response["Content-Disposition"] = content_disposition_header(as_attachment, file.name)
    # Nothing here is a shared asset, and the URL is behind a session.
    response["X-Content-Type-Options"] = "nosniff"
    return response
