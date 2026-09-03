# Sarah Aqua Soft — Flutter Integration Guide

Everything the mobile app needs, and the handful of things that will waste your
day if nobody tells you first.

- **Base URL:** `https://oneclick-jj14.onrender.com/api/v1/`
- **Interactive docs:** [`/api/docs/`](https://oneclick-jj14.onrender.com/api/docs/) — try any endpoint in a browser
- **OpenAPI schema:** [`/api/schema/`](https://oneclick-jj14.onrender.com/api/schema/) — feed this to a code generator
- **Full endpoint reference:** [`API.md`](API.md) — this file covers the client side; that one covers every field

---

## Read this part first

Four things account for almost every "it doesn't work" on this API. None of
them are guessable from the endpoint list.

### 1. Serialise your token refreshes, or the app will log itself out

Refresh tokens **rotate**. The moment you use one, it is blacklisted and a new
pair comes back. Use the old one again and you get:

```json
{ "success": false, "error": { "code": "TOKEN_INVALID" } }
```

The trap: on app resume, several requests 401 at once, each fires a refresh,
the first succeeds and the rest fail — and the app treats that as "session
expired" and logs the user out. It looks random and it is not.

**Fix:** one refresh at a time, behind a lock. Every other request waits for it
and retries with the new token.

```dart
Future<String>? _refreshing;

Future<String> _getFreshToken() {
  // Everybody who arrives while a refresh is in flight awaits the same future
  // instead of starting a second one.
  return _refreshing ??= _doRefresh().whenComplete(() => _refreshing = null);
}

Future<String> _doRefresh() async {
  final res = await dio.post('/auth/token/refresh/', data: {'refresh': _refresh});
  final tokens = res.data['data'];
  await _store(tokens['access'], tokens['refresh']);  // store BOTH: it rotated
  return tokens['access'];
}
```

Store the **new refresh token** every time. Keeping the old one is the same bug
by a slower route.

### 2. Never work out a file's type from its URL

The signed URL contains an opaque id with no filename in it, and for PDFs and
Office files Cloudinary answers `Content-Type: application/octet-stream`
whatever the file really is. A PDF downloaded from that URL and saved as-is
gets an extensionless blob the phone cannot open.

The download/preview response already tells you everything:

```json
{
  "url": "https://...",
  "file_name": "Price List.pdf",
  "extension": "pdf",
  "mime_type": "application/pdf",
  "size_bytes": 245678,
  "thumbnail_url": "",
  "is_previewable": true,
  "expires_in_seconds": 900
}
```

Save using `file_name`, open using `mime_type`. Never parse the URL.

### 3. Roles changed — `role` values are different now

There used to be three roles. There are now two, and **the strings changed**:

| Old | New |
|---|---|
| `owner` | `admin` |
| `staff` | `user` |
| `viewer` | `user` |

Existing accounts were migrated automatically. If anything compares
`role == "owner"`, update it.

`is_owner` still exists and still works — it is now an alias of `is_admin`, kept
so the shipped build did not break. Prefer `is_admin` in new code.

### 4. Delete your permission-gating code

The two roles are **identical inside the app**. Both can browse, upload,
rename, share, delete, restore and permanently delete — including items
somebody else created. The only thing `role` decides is access to the web
dashboard, which the app has nothing to do with.

So:

- `can_contribute` is always `true`. Stop hiding the "+" button.
- There is no "only your own files" rule. Stop comparing `created_by.id`
  against the current user to decide whether to show delete.

The server no longer refuses any of it, so that logic can only produce buttons
that are wrongly hidden.

---

## The response envelope

**Every** response has the same four keys — success or failure, list or single
object. Write one parser.

```json
{
  "success": true,
  "data": "<object, array, or null>",
  "error": null,
  "meta": { "request_id": "b6fecab8b217466982ea9c30f05a0a5d" }
}
```

On failure:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "FOLDER_NAME_CONFLICT",
    "message": "A folder named 'SubA' already exists in DocRoot.",
    "details": { "name": "SubA" },
    "field_errors": {}
  },
  "meta": { "request_id": "4684bfa134cc424f8de601b4cad4b481" }
}
```

| Key | Use it for |
|---|---|
| `error.code` | **Branch on this.** These strings never change. |
| `error.message` | Show to the user. Wording may change — never match on it. |
| `error.details` | Extra context, e.g. `retry_after_seconds` |
| `error.field_errors` | Per-field messages, for form validation |

**Show `meta.request_id` on your error screen.** It is written to the server
log, so one string finds the exact request when something is reported.

---

## Signing in

Two ways in, both returning the same token pair. There is **no signup** —
accounts are created by an admin.

### Password

```http
POST /auth/login/
Content-Type: application/json

{ "phone_number": "9876543210", "password": "…", "device_id": "…", "platform": "android" }
```

Only `phone_number` and `password` are required. Any phone format is accepted —
the server normalises it.

### OTP

```http
POST /auth/otp/request/     { "phone_number": "9876543210" }
POST /auth/otp/verify/      { "phone_number": "9876543210", "code": "123456" }
```

> **SMS is not working yet.** The gateway (NimbusIT) is unreachable from the
> server, so OTP requests return `502 SMS_DELIVERY_FAILED`. Use password login.
> The OTP code is complete and will work once the gateway is sorted — do not
> delete your OTP screens.

### What comes back

```json
{
  "tokens": {
    "access": "eyJ…", "refresh": "eyJ…",
    "access_expires_in": 86400, "refresh_expires_in": 2592000
  },
  "user": {
    "id": "…", "full_name": "…", "phone_number": "+919876543210",
    "role": "admin", "role_display": "Admin",
    "is_admin": true, "is_owner": true, "can_contribute": true,
    "has_password": true, "is_active": true
  },
  "device": null
}
```

Send the access token on every request:

```
Authorization: Bearer <access>
```

### How long a login lasts

| Token | Lifetime |
|---|---|
| Access | **24 hours** |
| Refresh | 30 days |

Refresh before the access token expires, or on the first 401 — using the lock
from section 1.

> If users report being logged out every half hour, the server's
> `JWT_ACCESS_MINUTES` has been left at `30`. It should be `1440`.

### Failed logins

Every failure returns the same `401 AUTHENTICATION_FAILED` — "Incorrect mobile
number or password" — whether the number is unknown, the password is wrong, or
the account is disabled. That is deliberate; do not try to tell them apart.

Five failures locks that **number** for 15 minutes (`429`, with
`details.retry_after_seconds`). The lock follows the number, not the IP, so
switching networks does not clear it.

---

## The main screen

**One call returns a category's subcategories and its documents together.** Use
this instead of calling `/categories/` and `/documents/` separately — it is one
round trip, one spinner, one pagination cursor.

```http
GET /browse/?parent_id=<id>      # omit parent_id for the top level
```

```json
{
  "data": [
    { "type": "folder", "id": "…", "name": "Water ATM", "subfolder_count": 2, "file_count": 5 },
    { "type": "file",   "id": "…", "name": "Price List.pdf", "extension": "pdf",
      "size_display": "240.1 KB", "is_previewable": true, "thumbnail_url": "" }
  ],
  "meta": {
    "folder": { "id": "…", "name": "Water ATM", "depth": 1 },
    "breadcrumb": [ { "id": "…", "name": "Products", "depth": 0 } ],
    "counts": { "folders": 2, "files": 3, "total": 5 },
    "pagination": { "count": 5, "page": 1, "page_size": 50, "total_pages": 1,
                    "has_next": false, "has_previous": false }
  }
}
```

Branch on `type` — the fields after it differ. Folders always sort before
files, so a page boundary never interleaves them.

`meta.breadcrumb` is the whole path, so the header needs no extra call.

### Categories, subcategories and folders are the same thing

One route covers every level: `/categories/`. A category, a subcategory and a
folder five levels deep are the same row — only `parent_id` differs. An item
keeps the same URL even when it is moved.

To create one, post a `parent_id` (or `null` for a top-level category):

```http
POST /categories/     { "name": "500 LPH", "parent_id": "<id or null>" }
```

---

## Documents

| Method | Path | What |
|---|---|---|
| `POST` | `/documents/` | Upload (multipart: `folder_id`, `file`) |
| `GET` | `/documents/<id>/` | One document |
| `PATCH` | `/documents/<id>/` | Rename, retag |
| `DELETE` | `/documents/<id>/` | To the recycle bin (reversible) |
| `GET` | `/documents/<id>/download/` | Signed URL, as an attachment |
| `GET` | `/documents/<id>/preview/` | Signed URL, inline |
| `POST` | `/documents/<id>/restore/` | Out of the recycle bin |
| `DELETE` | `/documents/<id>/purge/` | Destroy permanently — **no undo** |
| `GET` | `/documents/deleted/` | The recycle bin |
| `GET` | `/documents/recent/`, `/documents/favorites/` | Not paginated |

### Upload limits

**10 MB per file, hard.** That is Cloudinary's free-plan ceiling, not ours:

```
File size too large. Got 11534345. Maximum is 10485760.
```

Over that you get `413`-style `FILE_TOO_LARGE` from our own validator before
the upload is sent, so check the size client-side and tell the user early.

Allowed: PDF, Word, Excel, PowerPoint, CSV, TXT, RTF, images (jpg, png, webp,
gif, heic, bmp, tiff), video (mp4, mov, avi, mkv, webm), archives (zip, rar,
7z), CAD (dwg, dxf), and saved web pages (html, htm). Executables are refused
(`FILE_TYPE_BLOCKED`).

### Previewing

`is_previewable` is `true` for images, video and PDF. For everything else,
offer download rather than a viewer.

Signed URLs expire — `expires_in_seconds` (15 minutes). Fetch one when the user
taps, not when you draw the list, or it will have expired by the time it is
used.

---

## Search

```http
GET /search/?q=brochure
```

Typo-tolerant: `brochre` finds `Brochure`. Searches names, tags and
descriptions across the whole tree.

---

## Sharing with a customer

```http
POST /documents/<id>/share/     { "expires_in_hours": 168 }
```

Returns a token. The customer opens `/api/v1/share/<token>/` with no account and no
app. Links can be revoked at any time, and by default they **never expire** —
omit `expires_in_hours` for that, or pass it for a link that lapses. A link
also stops working when the download cap is reached or the file is deleted.

---

## Pagination

Three kinds, and they differ:

**Not paginated** — `/auth/devices/`, `/documents/recent/`,
`/documents/favorites/`, `/categories/favorites/`, `/categories/tree/`,
`/categories/<id>/breadcrumb/`, `/documents/<id>/versions/`. Plain array, no
`meta.pagination`.

**Page-number** — `/categories/`, `/documents/`, `/search/`, `/share-links/`,
both `deleted/` lists. `?page=2&page_size=50` (default 25, max 200).

**Browse** — same shape, `page_size` defaults to **50** (max 200), plus
`meta.counts`.

Infinite scroll: load page 1, then while `has_next` request `page + 1` and
append. Asking past the end returns `data: []` with HTTP 200, not an error.

---

## Error codes worth handling

| Code | HTTP | What to do |
|---|---|---|
| `AUTHENTICATION_FAILED` | 401 | Wrong credentials — show the login error |
| `TOKEN_INVALID` | 401 | Refresh (once, behind the lock). If refresh fails, sign out |
| `USER_DISABLED` | 401 | Account switched off — sign out, show why |
| `THROTTLED` | 429 | Back off using `details.retry_after_seconds` |
| `VALIDATION_ERROR` | 400 | Highlight fields from `error.field_errors` |
| `FILE_TOO_LARGE` | 400 | Over 10 MB — say so before uploading |
| `FILE_TYPE_BLOCKED` | 400 | Not an allowed extension |
| `FOLDER_NAME_CONFLICT` | 409 | A sibling has that name (case-insensitive) |
| `FOLDER_DEPTH_EXCEEDED` | 400 | Deeper than 32 levels |
| `SHARE_LINK_EXPIRED` | 410 | The link is expired, revoked or used up |
| `SMS_DELIVERY_FAILED` | 502 | Gateway down — fall back to password login |
| `UPLOAD_FAILED` / `STORAGE_ERROR` | 502 | Storage problem, worth a retry |

---

## The server sleeps

It is on Render's free plan. After ~15 minutes with no traffic the instance
spins down, and the next request takes **30–60 seconds** while it wakes.

Do not treat that as a failure:

- Set a generous connect/receive timeout (60s) on the first request after
  launch.
- Show "waking up…" rather than an error if the first call is slow.
- A `GET /health/` on app start warms it while the user is on the login
  screen. Note it is at the root, not under `/api/v1/`.

---

## Quick checklist

- [ ] One refresh at a time, behind a lock; store the rotated refresh token
- [ ] Save files using `file_name` / `mime_type`, never parsed from the URL
- [ ] `role` compared against `admin` / `user`, not `owner` / `staff` / `viewer`
- [ ] Permission-gating and "own files only" logic removed
- [ ] `meta.request_id` shown on the error screen
- [ ] File size checked against 10 MB before upload
- [ ] Signed URLs fetched on tap, not when the list is drawn
- [ ] 60s timeout on the first request, with a "waking up" state
- [ ] `error.code` branched on — never `error.message`
