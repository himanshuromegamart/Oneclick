# API Reference — Sarah Aqua Soft Document Manager

Complete reference for whoever builds the mobile app.

Every example below is **real output captured from the running server**, not
invented.

- **Base URL:** `https://<your-domain>/api/v1/`
- **Interactive docs:** `/api/docs/` — try any endpoint from a browser
- **Machine-readable schema:** `/api/schema/` — feed this to a code generator

### The three route groups

| Route | What it holds |
|---|---|
| `/api/v1/categories/` | Categories, subcategories and folders — all of them |
| `/api/v1/documents/` | Files: PDFs, images, video, Word, Excel |
| `/api/v1/browse/` | Both together — one call per screen |

> **`/categories/` covers every level of the tree.** A category, a subcategory
> and a folder nested five levels deep are the same kind of row; only
> `parent_id` differs. One route means an item keeps the same URL even when you
> move it.

---

## Contents

1. [The response envelope](#1-the-response-envelope)
2. [Pagination](#2-pagination)
3. [Authentication](#3-authentication)
4. [Roles: who can do what](#4-roles-who-can-do-what)
5. [Browse — the main screen](#5-browse--the-main-screen)
6. [Categories, subcategories and folders](#6-categories-subcategories-and-folders)
7. [Documents](#7-documents)
8. [Search](#8-search)
9. [Sharing with customers](#9-sharing-with-customers)
10. [Field reference](#10-field-reference)
11. [Error codes](#11-error-codes)
12. [Building the client](#12-building-the-client)

---

## 1. The response envelope

**Every response has the same four keys** — success or failure, list or single
object. Write one parser and it handles the whole API.

```json
{
  "success": true,
  "data":    "<the payload: object, array, or null>",
  "error":   null,
  "meta":    { "request_id": "..." }
}
```

### Success

```json
{
  "success": true,
  "data": { "is_favorite": true },
  "meta": { "request_id": "b6fecab8b217466982ea9c30f05a0a5d" }
}
```

### Failure

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
| `error.details` | Extra context, e.g. `retry_after_seconds`, `attempts_remaining` |
| `error.field_errors` | Per-field messages for form validation |

`field_errors` when a form is wrong:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "name: This field may not be blank.",
    "field_errors": { "name": ["This field may not be blank."] }
  }
}
```

Highlight each field from the key and show its first message.

### `meta.request_id`

Present on every response, and written to the server log. When a user reports a
problem, this one string finds the exact request. Show it on your error screen.

You can send your own instead: `X-Request-Id: <anything>` comes back unchanged,
in both `meta.request_id` and the `X-Request-Id` response header.

---

## 2. Pagination

Three kinds of list, and they differ — worth reading once.

### a) Not paginated

Short lists that will never grow large return a plain array with no
`meta.pagination`:

`/auth/devices/`, `/documents/recent/`, `/documents/favorites/`,
`/categories/favorites/`, `/categories/tree/`,
`/categories/<id>/breadcrumb/`, `/documents/<id>/versions/`

```json
{ "success": true, "data": [], "meta": { "request_id": "..." } }
```

### b) Page-number pagination

Used by `/categories/`, `/documents/`, `/search/`, `/share-links/`, both
`deleted/` lists and `/categories/<id>/children/`.

```
GET /api/v1/documents/?folder_id=<id>&page=2&page_size=50
```

| Parameter | Default | Max |
|---|---|---|
| `page` | 1 | — |
| `page_size` | 25 | 200 |

```json
"meta": {
  "request_id": "...",
  "pagination": {
    "count": 84,
    "page": 1,
    "page_size": 25,
    "total_pages": 4,
    "has_next": true,
    "has_previous": false,
    "next": "http://.../?page=2",
    "previous": null
  }
}
```

Infinite scroll: load page 1, then while `has_next` is true request `page + 1`
and append. `next` is a ready-made URL if you prefer to follow it directly.

### c) Browse pagination

`/browse/` spans two tables (categories and documents) so it has its own
counter. Same field names, plus `meta.counts`:

```json
"meta": {
  "folder": { "id": "...", "name": "SubA", "depth": 1 },
  "breadcrumb": [],
  "counts":     { "folders": 2, "files": 3, "total": 5 },
  "pagination": { "count": 5, "page": 1, "page_size": 50,
                  "total_pages": 1, "has_next": false, "has_previous": false }
}
```

`page_size` defaults to **50** here (max 200) because this is the main screen.

A page boundary can fall in the middle — page 1 might be
`[folder, folder, file]` and page 2 `[file, file]`. That is correct; no row is
dropped or repeated.

Asking for a page past the end returns `data: []` with HTTP 200, not an error.

---

## 3. Authentication

Mobile number + OTP. **There is no signup** — accounts are created on the server
by the owner.

### Step 1 — request an OTP

```http
POST /api/v1/auth/otp/request/
Content-Type: application/json

{ "phone_number": "9999900003" }
```

Any common format is accepted and normalised: `9999900003`, `+919999900003`,
`919999900003`, `09999900003`, `+91 99999 00003`.

**200**

```json
{
  "success": true,
  "data": {
    "phone_number": "+919999900003",
    "expires_in_seconds": 300,
    "resend_available_in_seconds": 60,
    "attempts_allowed": 5
  },
  "meta": { "request_id": "5e2f7ee326d84bfeb2b2f168aed65220" }
}
```

| Field | What to do with it |
|---|---|
| `expires_in_seconds` | Count down; after this, ask them to resend |
| `resend_available_in_seconds` | Keep "Resend" disabled this long |
| `attempts_allowed` | How many wrong guesses before the number locks |

Failures: `403 USER_NOT_REGISTERED`, `403 USER_DISABLED`,
`429 OTP_RESEND_TOO_SOON`, `429 OTP_DAILY_LIMIT`, `429 OTP_LOCKED`,
`502 SMS_DELIVERY_FAILED`.

### Step 2 — verify

```http
POST /api/v1/auth/otp/verify/

{
  "phone_number": "9999900003",
  "otp": "482915",
  "device_id": "doc-device",
  "platform": "android",
  "model_name": "Pixel 8",
  "app_version": "1.0.0"
}
```

Only `phone_number` and `otp` are required. The rest just populates the
"signed-in devices" list — send them if you can.

**200**

```json
{
  "success": true,
  "data": {
    "tokens": {
      "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
      "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
      "access_expires_in": 1800,
      "refresh_expires_in": 2592000
    },
    "user": {
      "id": "028b2fbf-36ca-4a73-af48-0ba817cc41aa",
      "phone_number": "+919999900003",
      "full_name": "Doc Owner",
      "email": "",
      "role": "owner",
      "role_display": "Owner",
      "can_contribute": true,
      "is_owner": true,
      "is_active": true,
      "last_login_at": "2026-08-07T16:40:19.154484+05:30",
      "created_at": "2026-08-07T16:39:24.785977+05:30"
    },
    "device": {
      "id": "a3ff3120-1d8b-47b8-a5a7-761d7a0c2435",
      "device_id": "doc-device",
      "platform": "android",
      "model_name": "Pixel 8",
      "app_version": "1.0.0",
      "is_active": true,
      "login_count": 1,
      "last_seen_at": "2026-08-07T16:40:19.150546+05:30"
    }
  }
}
```

`device` is `null` when no `device_id` was sent.

Store both tokens in secure storage — Keychain on iOS,
EncryptedSharedPreferences on Android. Not in plain preferences.

Failures: `400 OTP_INVALID` (with `details.attempts_remaining`),
`400 OTP_EXPIRED`, `429 OTP_ATTEMPTS_EXCEEDED`, `429 OTP_LOCKED`.

### Step 3 — use the token

```
Authorization: Bearer <access>
```

On every request except `/auth/otp/*`, `/auth/token/refresh/`,
`/share/<token>/` and the health probes.

### Step 4 — refresh

Access tokens last **30 minutes**; refresh tokens **30 days**.

```http
POST /api/v1/auth/token/refresh/

{ "refresh": "eyJ..." }
```

```json
{ "access": "eyJ...", "refresh": "eyJ...",
  "access_expires_in": 1800, "refresh_expires_in": 2592000 }
```

> **Refresh tokens are single-use.** Every call returns a *new* refresh token.
> Save it and throw the old one away. Sending a spent token gives
> `401 TOKEN_INVALID` — log out and show the OTP screen.

Recommended client rule: on any `401`, refresh **once** and retry the original
request. If the refresh also fails, log out. Never loop.

### Resend

```http
POST /api/v1/auth/otp/resend/
{ "phone_number": "9999900003" }
```

Same response as the request endpoint. Returns `429 OTP_RESEND_TOO_SOON` inside
the cooldown, with `details.retry_after_seconds`.

### Log out

```http
POST /api/v1/auth/logout/
{ "refresh": "eyJ...", "device_id": "doc-device" }
```

Both fields optional but send them. Response: `{ "detail": "Logged out." }`.

The access token stays technically valid until it expires, so **delete both
tokens from the device** too.

### Current user

```http
GET   /api/v1/auth/me/
PATCH /api/v1/auth/me/     { "full_name": "New Name", "email": "a@b.com" }
```

Returns the `user` object shown above. Only `full_name` and `email` can be
changed — sending `role` is ignored.

### Signed-in devices

```http
GET /api/v1/auth/devices/
```

A plain array of device objects. Not paginated.

---

## 4. Roles: who can do what

Three roles, on the `role` field of the user.

| Action | `owner` | `staff` | `viewer` |
|---|:---:|:---:|:---:|
| Browse, search, view, download | yes | yes | yes |
| Create / rename a category | yes | yes | no |
| Upload a document | yes | yes | no |
| Rename / move / delete **own** items | yes | yes | no |
| Rename / move / delete **anyone's** items | yes | no | no |
| Share a document | yes | yes | no |
| See the whole recycle bin | yes | own only | no |
| Delete permanently (purge) | yes | no | no |

"Own" = the person who uploaded or created it (`created_by.id`).

**In the app:** use `can_contribute` from `/auth/me/` to hide the "+" button,
and compare `item.created_by.id` with the current user id to decide whether to
offer rename/delete on a row. The server enforces all of this regardless —
hiding buttons is only so the user is not offered something that will fail.

Violations return `403 PERMISSION_DENIED`.

---

## 5. Browse — the main screen

**One call returns a category's subcategories and documents together.** Use this
instead of calling `/categories/` and `/documents/` separately.

```http
GET /api/v1/browse/                    # top level
GET /api/v1/browse/?parent_id=<id>     # inside a category
```

| Parameter | Meaning |
|---|---|
| `parent_id` | Which category to open. Omit for the top level. |
| `type` | `folder` or `file` to show only one kind. Omit for both. |
| `page`, `page_size` | Default 50, max 200 |

**200** (real output, trimmed)

```json
{
  "success": true,
  "data": [
    {
      "type": "folder",
      "id": "50ec7e36-4569-4d77-9b3b-d920ce15ef78",
      "name": "Leaf",
      "parent_id": "5c58562d-12a4-41b6-9b7f-52b957dbf0a3",
      "depth": 2,
      "description": "",
      "icon": "",
      "color": "",
      "is_favorite": false,
      "has_children": false,
      "file_count": 0,
      "subfolder_count": 0,
      "total_size_bytes": 0,
      "created_by": { "id": "028b2fbf-...", "full_name": "Doc Owner" },
      "created_at": "2026-08-07T16:41:20.541009+05:30"
    },
    {
      "type": "file",
      "id": "88f7655f-3e6d-4abc-af9b-d0f0f8d4ea63",
      "name": "brochure.pdf",
      "description": "Product brochure",
      "folder_id": "5c58562d-12a4-41b6-9b7f-52b957dbf0a3",
      "folder_name": "SubA",
      "extension": "pdf",
      "mime_type": "application/pdf",
      "category": "document",
      "size_bytes": 909,
      "size_display": "909 B",
      "thumbnail_url": "",
      "tags": ["brochure", "atm"],
      "version_number": 1,
      "download_count": 0,
      "is_favorite": false,
      "is_previewable": true,
      "created_by": { "id": "028b2fbf-...", "full_name": "Doc Owner" },
      "created_at": "2026-08-07T16:42:32.734585+05:30"
    }
  ],
  "meta": {
    "request_id": "...",
    "folder": { "id": "5c58562d-...", "name": "SubA", "depth": 1 },
    "breadcrumb": [
      { "id": "1686338e-...", "name": "DocRoot", "depth": 0 },
      { "id": "5c58562d-...", "name": "SubA",    "depth": 1 }
    ],
    "counts": { "folders": 1, "files": 2, "total": 3 },
    "pagination": { "count": 3, "page": 1, "page_size": 50,
                    "total_pages": 1, "has_next": false, "has_previous": false }
  }
}
```

**Reading it**

- `item.type` is `"folder"` or `"file"` — switch your row layout on it
- **Folders always come first**, then files; each group sorted by name
- `meta.folder` is the category you are inside — `null` at the top level
- `meta.breadcrumb` is the full path, root first — render it as the header
- `meta.counts` gives you a "2 folders, 3 files" subtitle

**Navigating**

- Tap a `folder` row → `GET /browse/?parent_id=<that row's id>`
- Tap a `file` row → `GET /documents/<id>/download/` or `/preview/`
- Back → the `parent_id` of the second-to-last breadcrumb entry, or the top level

Documents always live inside a category, so the **top level contains categories
only**.

---

## 6. Categories, subcategories and folders

> **They are all the same thing.** One route, one model. Whether something is a
> "category", a "subcategory" or a "folder" depends only on how deep it sits,
> and `parent_id` decides that.

| What you want | Request |
|---|---|
| Top-level category | `POST /categories/` with **no** `parent_id` |
| Subcategory inside it | `POST /categories/` with `parent_id` = the category |
| Folder inside that | `POST /categories/` with `parent_id` = the subcategory |
| Deeper still | Same call, `parent_id` = whatever is open |

So the **"+ New Folder"** button always sends the same request — just pass the
id of the category currently on screen.

### Create

```http
POST /api/v1/categories/

{
  "name": "Water ATM",
  "parent_id": "1686338e-108f-4224-8fbe-061d4925b493",
  "description": "",
  "icon": "droplet",
  "color": "#0B6FB5",
  "is_pinned": false
}
```

Only `name` is required. `icon` and `color` are free-form — the backend stores
them without interpreting them, so you can add icons without a server change.

**201**

```json
{
  "id": "5c58562d-12a4-41b6-9b7f-52b957dbf0a3",
  "name": "Water ATM",
  "parent_id": "1686338e-108f-4224-8fbe-061d4925b493",
  "depth": 1,
  "description": "",
  "icon": "droplet",
  "color": "#0B6FB5",
  "position": 10,
  "is_system": false,
  "is_pinned": false,
  "is_favorite": false,
  "has_children": false,
  "file_count": 0,
  "subfolder_count": 0,
  "total_size_bytes": 0,
  "created_by": { "id": "028b2fbf-...", "full_name": "Doc Owner" },
  "created_at": "2026-08-07T16:40:43.709148+05:30",
  "updated_at": "2026-08-07T16:40:43.709187+05:30"
}
```

Nesting is unlimited in practice; the hard ceiling is 32 levels.

Failures: `409 FOLDER_NAME_CONFLICT` (a sibling already has that name,
case-insensitive), `400 VALIDATION_ERROR` (illegal characters), `403
PERMISSION_DENIED` (viewer).

### All category endpoints

| Method | Path | Returns |
|---|---|---|
| `GET` | `/categories/?parent_id=<id>` | One level, paginated. Omit `parent_id` for the top level |
| `GET` | `/categories/<id>/` | One category |
| `GET` | `/categories/tree/` | The whole hierarchy, nested |
| `GET` | `/categories/<id>/children/` | Direct subcategories, paginated |
| `GET` | `/categories/<id>/breadcrumb/` | Path from the root to it |
| `GET` | `/categories/<id>/statistics/` | Counts for the whole subtree |
| `GET` | `/categories/favorites/` | Starred |
| `GET` | `/categories/deleted/` | Recycle bin |
| `POST` | `/categories/` | Create |
| `PATCH` | `/categories/<id>/` | Rename / restyle |
| `POST` | `/categories/<id>/move/` | Reparent |
| `POST` | `/categories/<id>/favorite/` | Star / unstar |
| `POST` | `/categories/<id>/restore/` | Restore from the bin |
| `DELETE` | `/categories/<id>/` | Send to the bin |

### Tree

```http
GET /api/v1/categories/tree/?root_id=<optional>&max_depth=<optional>
```

Nested nodes in one query, cached server-side — safe to call on every app
launch.

```json
[
  {
    "id": "1686338e-...", "name": "DocRoot", "parent_id": null, "depth": 0,
    "icon": "", "color": "", "is_system": false, "is_pinned": false,
    "file_count": 0, "subfolder_count": 2,
    "children": [
      {
        "id": "5c58562d-...", "name": "SubA", "parent_id": "1686338e-...",
        "depth": 1, "file_count": 0, "subfolder_count": 1,
        "children": [
          { "id": "50ec7e36-...", "name": "Leaf", "depth": 2,
            "subfolder_count": 0, "children": [] }
        ]
      },
      { "id": "609ea992-...", "name": "SubB", "depth": 1, "children": [] }
    ]
  }
]
```

On a slow connection use `?max_depth=1` and expand lazily.

### Breadcrumb

```http
GET /api/v1/categories/<id>/breadcrumb/
```

```json
[
  { "id": "1686338e-...", "name": "DocRoot", "depth": 0 },
  { "id": "5c58562d-...", "name": "SubA",    "depth": 1 },
  { "id": "50ec7e36-...", "name": "Leaf",    "depth": 2 }
]
```

Root first, ending with the category itself. `/browse/` already includes this,
so you rarely need to call it separately.

### Statistics

```http
GET /api/v1/categories/<id>/statistics/
```

```json
{
  "subfolder_count": 3,
  "direct_subfolder_count": 2,
  "file_count": 0,
  "total_size_bytes": 0,
  "depth": 0
}
```

`subfolder_count` covers the **whole subtree**; `direct_subfolder_count` counts
only one level down.

### Rename / restyle

```http
PATCH /api/v1/categories/<id>/
{ "name": "New Name", "icon": "folder", "color": "#FF0000", "is_pinned": true }
```

All fields optional; send only what changed.

### Move

```http
POST /api/v1/categories/<id>/move/
{ "parent_id": "<destination>" }
```

`parent_id: null` moves it to the top level. Moves the category **and everything
inside it**.

Returns `400 FOLDER_CYCLE` if the destination sits inside the category being
moved — that would detach the branch from the tree. Grey those out in your
picker: any category whose breadcrumb contains the one being moved.

### Delete and restore

```http
DELETE /api/v1/categories/<id>/
```

```json
{ "detail": "Category moved to the recycle bin.", "affected": 4 }
```

Soft delete — the category, its subcategories **and their documents** all go to
the bin. `affected` is how many categories moved; show it in the confirmation
("This will remove 4 folders").

```http
POST /api/v1/categories/<id>/restore/
```

Brings the whole branch back. Returns `409 CONFLICT` if its parent is still
deleted — restore the parent first. If the name was taken meanwhile, it comes
back as `"Water ATM (restored)"`.

---

## 7. Documents

### Upload

`multipart/form-data` — **not** JSON.

```http
POST /api/v1/documents/
Content-Type: multipart/form-data
Authorization: Bearer <access>

folder_id:   5c58562d-12a4-41b6-9b7f-52b957dbf0a3
file:        <binary>
description: Product brochure          (optional)
tags:        brochure                  (optional, repeat for several)
tags:        atm
```

`folder_id` is the id of the category you are currently in — at any depth.

**201**

```json
{
  "id": "88f7655f-3e6d-4abc-af9b-d0f0f8d4ea63",
  "name": "brochure.pdf",
  "description": "Product brochure",
  "folder_id": "5c58562d-...",
  "folder_name": "SubA",
  "extension": "pdf",
  "mime_type": "application/pdf",
  "category": "document",
  "size_bytes": 909,
  "size_display": "909 B",
  "thumbnail_url": "",
  "width": null,
  "height": null,
  "duration_seconds": null,
  "tags": ["brochure", "atm"],
  "version_number": 1,
  "download_count": 0,
  "checksum": "ed9426affe88f331535af362cec11cd2356989f3dae1d8dfd1bdbc6fdfefb1da",
  "is_favorite": false,
  "is_previewable": true,
  "created_by": { "id": "028b2fbf-...", "full_name": "Doc Owner" },
  "created_at": "2026-08-07T16:42:32.734585+05:30",
  "updated_at": "2026-08-07T16:42:32.734632+05:30"
}
```

After uploading, re-request `/browse/?parent_id=<same id>` to refresh the screen.

**Limits**

- Max **200 MB** per file, else `400 FILE_TOO_LARGE`. Check the size on the
  phone first rather than making the user wait for a rejection.
- Executables (`.exe .bat .sh .msi .dll .js .jar .ps1 .vbs .scr`) are refused
  with `400 FILE_TYPE_BLOCKED`
- Allowed: pdf, doc(x), xls(x), ppt(x), csv, txt, rtf, jpg, jpeg, png, webp,
  gif, heic, bmp, tiff, mp4, mov, avi, mkv, webm, zip, rar, 7z, dwg, dxf

A name already used in that category is **renamed, not refused**: `report.pdf`
becomes `report (1).pdf`, like a desktop file manager.

### Very large files (optional)

Skip the server entirely — the phone uploads straight to Cloudinary:

**1.** Get a signature

```http
POST /api/v1/documents/upload-signature/
{ "folder_id": "<id>", "filename": "big-video.mp4" }
```

```json
{
  "signature": "a1b2c3d4e5f6...",
  "timestamp": 1786101358,
  "api_key": "<your-cloudinary-api-key>",
  "cloud_name": "<your-cloud-name>",
  "folder": "sarah-aqua-soft/50ec7e36-...",
  "public_id": "<generated-public-id>",
  "resource_type": "video",
  "upload_url": "https://api.cloudinary.com/v1_1/<your-cloud-name>/video/upload",
  "expires_in_seconds": 600,
  "suggested_name": "big-video.mp4",
  "folder_id": "50ec7e36-..."
}
```

**2.** POST the file to `upload_url` with those fields.

**3.** Tell the backend it worked

```http
POST /api/v1/documents/register-upload/
{
  "folder_id": "<id>", "filename": "big-video.mp4",
  "public_id": "<from Cloudinary>", "secure_url": "<from Cloudinary>",
  "size_bytes": 52428800, "resource_type": "video"
}
```

Returns the same object as a normal upload.

### Download

`/download/` does **not** return the file — it returns a link.

```http
GET /api/v1/documents/<id>/download/
```

```json
{
  "url": "https://res.cloudinary.com/<your-cloud-name>/raw/authenticated/s--SIGNATURE--/fl_attachment:brochure.pdf/v1/sarah-aqua-soft/5c58.../6ec4...",
  "expires_in_seconds": 900,
  "name": "brochure.pdf",
  "size_bytes": 909,
  "mime_type": "application/pdf"
}
```

Fetch that URL directly — it comes from a CDN, not this server, so it is fast
and does not tie up the API.

> **The link expires in 15 minutes.** Never cache it. Request a fresh one each
> time the user taps download.

Calling this also bumps `download_count` and adds the file to the user's
"recent" list.

### Preview

```http
GET /api/v1/documents/<id>/preview/
```

```json
{
  "url": "https://res.cloudinary.com/<your-cloud-name>/raw/authenticated/s--SIGNATURE--/v1/...",
  "thumbnail_url": "",
  "expires_in_seconds": 900,
  "is_previewable": true
}
```

Same as download but without the "save as" flag, so it renders inline. Use it
for images, video and PDFs — check `is_previewable` first.

### Versions

```http
GET  /api/v1/documents/<id>/versions/     # list previous versions
POST /api/v1/documents/<id>/versions/     # upload a replacement
```

Both on the **same URL** — `GET` lists, `POST` replaces.

Upload (multipart): `file` = the new file, `note` = optional reason.

```json
[
  {
    "id": "2ec85b54-...",
    "version_number": 1,
    "size_bytes": 909,
    "checksum": "ed9426aff...",
    "note": "Updated prices",
    "created_by": { "id": "028b2fbf-...", "full_name": "Doc Owner" },
    "created_at": "2026-08-07T16:44:19.914162+05:30"
  }
]
```

The replacement must have the **same extension** — a PDF can only be replaced by
a PDF. The current file's `version_number` becomes 2, 3, and so on. Up to 20 old
versions are kept, then the oldest is discarded.

### All document endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/documents/?folder_id=<id>` | Files in a category, paginated |
| `GET` | `/documents/<id>/` | One file (also marks it "recent") |
| `GET` | `/documents/recent/` | Last 50 opened, newest first |
| `GET` | `/documents/favorites/` | Starred |
| `GET` | `/documents/deleted/` | Recycle bin |
| `GET` | `/documents/<id>/download/` | Download link |
| `GET` | `/documents/<id>/preview/` | Inline preview link |
| `GET` | `/documents/<id>/versions/` | Version history |
| `POST` | `/documents/` | Upload |
| `POST` | `/documents/<id>/versions/` | Replace with a new version |
| `POST` | `/documents/upload-signature/` | Direct-upload signature |
| `POST` | `/documents/register-upload/` | Record a direct upload |
| `PATCH` | `/documents/<id>/` | Rename, description, tags |
| `POST` | `/documents/<id>/move/` | `{ "folder_id": "..." }` |
| `POST` | `/documents/<id>/copy/` | `{ "folder_id": "..." }` |
| `POST` | `/documents/<id>/favorite/` | Star / unstar |
| `POST` | `/documents/<id>/share/` | Create a share link |
| `POST` | `/documents/<id>/restore/` | Restore from the bin |
| `DELETE` | `/documents/<id>/` | Send to the bin |
| `DELETE` | `/documents/<id>/purge/` | Destroy permanently (owner only) |

List filters for `/documents/`: `category`, `extension`, `tag`, and `ordering`
(`-created_at`, `name`, `-size_bytes`, `-download_count`).

### Rename and edit

```http
PATCH /api/v1/documents/<id>/
{ "name": "new-name.pdf", "description": "...", "tags": ["iso", "2025"] }
```

The extension cannot be changed — renaming `doc.pdf` to `doc.exe` keeps `.pdf`,
because the stored bytes are still a PDF. Tags are lowercased and de-duplicated
automatically.

### Delete, restore, purge

```http
DELETE /api/v1/documents/<id>/          -> { "detail": "File moved to the recycle bin." }
POST   /api/v1/documents/<id>/restore/  -> the file object
DELETE /api/v1/documents/<id>/purge/    -> { "detail": "File permanently deleted." }
```

Delete is reversible. **Purge is not** — it removes the stored file and every
old version, and only the owner can do it. Always confirm before calling it.

Items sit in the bin for 30 days, then a nightly job removes them for good.

---

## 8. Search

```http
GET /api/v1/search/?q=brochure
```

Searches file **names, tags and descriptions**, ranked by relevance and tolerant
of typos — `brochre` finds `brochure.pdf`, and `coolers` finds "cooler".

| Filter | Meaning |
|---|---|
| `q` | The search text. Omit to list everything matching the filters. |
| `folder_id` | Search inside this category **and everything under it** |
| `category` | `document`, `image`, `video`, `spreadsheet`, `presentation`, `archive`, `other` |
| `extension` | e.g. `pdf` |
| `tag` | Exact tag |
| `uploaded_by` | User id |
| `date_from`, `date_to` | `YYYY-MM-DD` |

Returns the standard **paginated** list of file objects (page size 25 by
default) — same shape as `/documents/`.

Debounce input at about 300 ms rather than firing a request per keystroke.
Rate limit: 60 searches/minute.

---

## 9. Sharing with customers

A share link opens **without an account** — the recipient is a customer, not a
user.

### Create

```http
POST /api/v1/documents/<id>/share/

{ "expires_in_hours": 168, "max_downloads": 10, "note": "For Ramesh Traders" }
```

All three optional. Default expiry 7 days, maximum 90. `max_downloads: null`
means unlimited until it expires.

**201**

```json
{
  "id": "eecabfb1-c58f-4b5a-ab29-ba8cf4b1dfe3",
  "token": "OrCwy6wXqJb089IQSPVM3rIbTwzPUKMAYUsrz9Yn1V8",
  "share_url": "https://your-domain/api/v1/share/OrCwy6wXqJb089IQSPVM3rIbTwzPUKMAYUsrz9Yn1V8/",
  "expires_at": "2026-08-14T16:45:34.070025+05:30",
  "max_downloads": 10,
  "download_count": 0,
  "revoked_at": null,
  "recipient_note": "For Ramesh Traders",
  "is_usable": true,
  "last_accessed_at": null,
  "created_by": { "id": "028b2fbf-...", "full_name": "Doc Owner" },
  "created_at": "2026-08-07T16:45:34.071657+05:30"
}
```

Send `share_url` over WhatsApp, SMS or email.

### Manage

```http
GET    /api/v1/share-links/           # links you created, paginated
DELETE /api/v1/share-links/<id>/      # revoke immediately
```

`is_usable` is the one field to show — it accounts for expiry, revocation and
the download cap together.

### What the customer gets

```http
GET /api/v1/share/<token>/            # no Authorization header
```

```json
{
  "name": "brochure.pdf",
  "size_bytes": 11,
  "mime_type": "application/pdf",
  "url": "https://res.cloudinary.com/<your-cloud-name>/raw/authenticated/s--SIGNATURE--/fl_attachment:brochure.pdf/...",
  "expires_in_seconds": 900
}
```

Returns `410 SHARE_LINK_EXPIRED` once it lapses, is revoked or hits the cap, and
`404` if the file was deleted.

---

## 10. Field reference

### User

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `phone_number` | string | Always `+91XXXXXXXXXX` |
| `full_name` | string | |
| `email` | string | May be empty |
| `role` | enum | `owner` / `staff` / `viewer` |
| `role_display` | string | "Owner" / "Staff" / "Viewer" — ready to display |
| `can_contribute` | bool | Can create and upload. Hide the "+" when false |
| `is_owner` | bool | Full control |
| `is_active` | bool | |
| `last_login_at` | datetime or null | |

### Category (also called subcategory or folder)

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `name` | string | |
| `parent_id` | uuid or null | null = top level |
| `depth` | int | 0 = top level |
| `description` | string | |
| `icon`, `color` | string | Yours to define; the backend just stores them |
| `position` | int | Manual sort order within the parent |
| `is_system` | bool | Cannot be renamed, moved or deleted |
| `is_pinned` | bool | For a "pinned" section in the app |
| `is_favorite` | bool | Starred **by the current user** |
| `has_children` | bool | Has subcategories or files — show a chevron |
| `file_count` | int | Files directly inside (not the subtree) |
| `subfolder_count` | int | Direct subcategories |
| `total_size_bytes` | int | Size of the files directly inside |
| `created_by` | object | `{ id, full_name }` — compare with the current user |
| `created_at`, `updated_at` | datetime | ISO 8601 with offset |

### Document

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `name` | string | With extension |
| `description` | string | |
| `folder_id` | uuid | Which category it is in |
| `folder_name` | string | Ready to display, no extra lookup |
| `extension` | string | Lowercase, no dot |
| `mime_type` | string | |
| `category` | enum | `document` `image` `video` `spreadsheet` `presentation` `archive` `other` — **use this for the icon** |
| `size_bytes` | int | |
| `size_display` | string | `"909 B"`, `"2.4 MB"` — ready to display |
| `thumbnail_url` | string | Images and video only; empty otherwise |
| `width`, `height` | int or null | Images and video |
| `duration_seconds` | float or null | Video |
| `tags` | string[] | Lowercase |
| `version_number` | int | 1 = never replaced |
| `download_count` | int | |
| `checksum` | string | SHA-256; verify an interrupted download |
| `is_favorite` | bool | Starred by the current user |
| `is_previewable` | bool | Images, video and PDFs |
| `created_by` | object | Who uploaded it |
| `created_at`, `updated_at` | datetime | |

> Note the two uses of the word "category": the **route** `/categories/` is the
> folder tree, while the **field** `category` on a document is its file type
> (`document`, `image`, `video`...). They are unrelated.

### Dates

All datetimes are ISO 8601 with a timezone offset, in IST:
`2026-08-07T16:42:32.734585+05:30`. Parse the offset — do not assume UTC.

---

## 11. Error codes

| Code | HTTP | Meaning | What the app should do |
|---|---|---|---|
| `VALIDATION_ERROR` | 400 | Bad input | Show `field_errors` on the form |
| `AUTHENTICATION_FAILED` | 401 | Missing/bad token | Refresh once, then log out |
| `TOKEN_INVALID` | 401 | Refresh token spent or expired | Log out, show OTP screen |
| `PERMISSION_DENIED` | 403 | Role does not allow this | Show the message; hide the button next time |
| `USER_NOT_REGISTERED` | 403 | No account for that number | "Contact your administrator" |
| `USER_DISABLED` | 403 | Account switched off | Log out |
| `NOT_FOUND` | 404 | Gone or never existed | Refresh the list |
| `CONFLICT` | 409 | Clashes with existing data | Show the message |
| `FOLDER_NAME_CONFLICT` | 409 | Sibling has that name | Ask for a different name |
| `SHARE_LINK_EXPIRED` | 410 | Link lapsed/revoked/used up | "This link is no longer available" |
| `THROTTLED` | 429 | Too many requests | Wait `details.retry_after_seconds` |
| `OTP_INVALID` | 400 | Wrong code | Show `details.attempts_remaining` |
| `OTP_EXPIRED` | 400 | Code timed out | Offer Resend |
| `OTP_ATTEMPTS_EXCEEDED` | 429 | Too many wrong guesses on this code | Offer Resend |
| `OTP_RESEND_TOO_SOON` | 429 | Inside the cooldown | Count down `retry_after_seconds` |
| `OTP_DAILY_LIMIT` | 429 | Too many codes today | "Try again tomorrow" |
| `OTP_LOCKED` | 429 | Number locked after repeated failures | Show the wait time |
| `FILE_TOO_LARGE` | 400 | Over 200 MB | Check size before uploading |
| `FILE_TYPE_BLOCKED` | 400 | Type not allowed | Filter the file picker |
| `FOLDER_CYCLE` | 400 | Would move a category into itself | Grey out that destination |
| `FOLDER_DEPTH_EXCEEDED` | 400 | Past 32 levels | Rare |
| `SMS_DELIVERY_FAILED` | 502 | SMS gateway unreachable | Offer Resend |
| `INTERNAL_ERROR` | 500 | Server fault | Show `meta.request_id` |

### Rate limits

| Endpoint | Limit | Keyed by |
|---|---|---|
| OTP request | 5/hour | phone number |
| OTP verify | 10/hour | phone number |
| Upload | 120/hour | user |
| Download / preview | 300/hour | user |
| Search | 60/minute | user |
| Everything else | 90/minute, 3000/day | user |

On `429`, `error.details.retry_after_seconds` says how long to wait, and the
`Retry-After` header carries the same number.

---

## 12. Building the client

**Token handling.** Keep one place that owns the tokens. On `401`, refresh once
and retry the original request; if the refresh fails, log out. Never retry in a
loop. Every refresh returns a **new** refresh token — persist it immediately.

**Retrying.** Safe to retry: `429` (after the stated wait), `502`, `503`, and
network timeouts. **Do not blindly retry `POST /documents/`** — if the first
attempt actually succeeded and the response was lost, a retry uploads the file
twice. Re-list the category and check first.

**Caching.** `/categories/tree/` is safe to cache for a few minutes. **Never
cache a download or preview URL** — they expire in 15 minutes. Cache file
metadata, not links.

**Offline.** The API is online-only. Cache the last browse response so the
screen has something to show, but mark it stale and refresh on reconnect.

**Background uploads.** Use `WorkManager` on Android and `URLSession` background
tasks on iOS, so a large upload survives the app being backgrounded.

**Useful headers to send**

| Header | Why |
|---|---|
| `X-Request-Id` | Your own trace id; comes back in `meta.request_id` |
| `X-Device-Id` | Helps identify a device in the logs |
| `X-App-Version` | Tells support which build a user is on |
| `X-Platform` | `android` / `ios` |

**Getting started checklist**

1. Log in and store the tokens
2. `GET /browse/` for the home screen
3. Tap a category → `GET /browse/?parent_id=<id>`
4. "+" → `POST /categories/` (with the open category as `parent_id`) or
   `POST /documents/` (multipart)
5. Tap a document → `GET /documents/<id>/download/`, then fetch the returned URL
6. Search box → `GET /search/?q=...`
7. Share → `POST /documents/<id>/share/`, send `share_url`

Everything else — favourites, recent, versions, the recycle bin — is optional
polish you can add later.
