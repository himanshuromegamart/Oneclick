# Sarah Aqua Soft — Document Manager (Backend)

A backend API for a mobile app that keeps company documents organised in
categories, like folders on a computer. People log in with a mobile number and
a password (or an OTP), add categories, upload documents, and share them with
customers by link.

The phone app is the main interface. There are also two browser consoles for
running the place — a dashboard at `/dashboard/` and the Django admin at
`/admin/` — and only Admin accounts can open either.

---

## What it does

**Categories, subcategories and folders are all the same thing** — a box that
holds other boxes and files, exactly like folders on a computer. What you call
it just depends on where it sits. Anyone signed in creates them from the app;
nothing is fixed in code.

```
Quotation
    Water ATM
        500 LPH
        1000 LPH
    Water Cooler
        40L
Documents
    Aadhaar
    GST
Certificates
    ISO
```

Opening one is a single request — `GET /api/v1/browse/?parent_id=<id>` returns
the subfolders and the documents inside it together, in one list, with the
breadcrumb. That is what the app's main screen uses.

**Files.** PDF, Word, Excel, images, video, and so on. Upload into any folder,
then rename, move, copy, download, and keep previous versions. Deleted items go
to a recycle bin and can be restored.

**Login.** Two ways, both giving the same access:

- **Password** — mobile number + password
- **OTP** — a code sent by SMS

An account can have either or both. There is no signup screen — you create the
accounts (see below).

**Search.** Finds a document by name, tag or description, and forgives typos —
searching `brochre` still finds `Brochure`.

**Sharing.** Produces a link a customer can open without an account. It
expires, can limit how many times it's downloaded, and can be switched off at
any time.

---

## Who can do what

Two roles, and **one** difference between them.

| | Admin | User |
|---|:---:|:---:|
| Everything in the mobile app — browse, search, upload, share, rename, delete, restore, delete permanently | ✅ | ✅ |
| The web dashboard at `/dashboard/` | ✅ | — |
| The Django admin console at `/admin/` | ✅ | — |

That is the entire permission system. Inside the app the two roles are
identical — there is no "only your own files" rule, so anyone can delete or
rename anything, including documents somebody else uploaded.

Account creation is the one thing kept to Admins. It lives only in the two
browser consoles, both of which a User cannot open. Without that, a User could
create themselves an Admin account and the restriction would mean nothing.

> **The last admin is protected.** The console refuses to demote, disable or
> delete the only remaining active Admin — there is no shell on the host to
> undo it with. Promote somebody else first.

---

## Setting it up

### 1. What you need

- Python 3.13
- PostgreSQL 14 or newer
- Redis (for caching and background jobs)

### 2. Install

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac / Linux

pip install -r requirements/development.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Setting | Where it comes from |
|---|---|
| `POSTGRES_*` | Your database name, user and password |
| `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY` | Generate: `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `CLOUDINARY_*` | Cloudinary dashboard → Account Details |
| `SMS_*` | Your NimbusIT account |

While developing, leave `SMS_BACKEND=apps.accounts.sms.ConsoleSMSBackend` — the
OTP is printed in the terminal and no SMS credits are spent. Switch to
`NimbusITSMSBackend` to send real messages.

### 4. Create the database and the first user

```bash
python manage.py migrate

python manage.py create_user --phone 9876543210 --name "Your Name" --role admin
```

### 5. Run it

```bash
python manage.py runserver
```

Open <http://localhost:8000/api/docs/> for the interactive API documentation.

---

## The dashboard

`https://<your-service>/dashboard/` — the everyday console. Three screens:

| Screen | What it is for |
|---|---|
| **Search** | Find anything, anywhere — on every page, so it is never a click away |
| **All categories** | Walk into a category, see what is inside, add and upload there |
| **Category tree** | The whole tree as one flat list, for adding at any depth |
| **Users** | Create accounts, set passwords, choose Admin or User |

**All categories** is the one to use day to day. It opens on your main categories; click
one and you are inside it, looking at its subcategories and its documents in a
single list — the way a folder works on a computer. The two buttons at the top
add a category or upload documents **into wherever you are standing**, so there
is no parent to pick and nothing to get wrong. The breadcrumb walks back up.

Subcategories and documents share one row design, because at that point they
are the same kind of thing: something that lives in a category. Categories sort
first, then documents.

Deleting from here is a recycle-bin delete — nothing is destroyed. Deleting a
category takes everything inside it, and the confirmation says so.

**Search suggests as you type.** Results appear in a panel under the box after
the second character, so most of the time you never press Enter — you see the
thing and click it. Arrow keys move, Enter opens, Escape closes. Pressing Enter
without picking anything goes to the full results page, and if the script fails
to load the box is still an ordinary form with a Search button.

**Search is global and forgiving.** The box sits on every page, and searches
categories and documents together across the whole tree — you never have to
know which category something went into. Spelling does not have to be exact:
"brochre" finds "Brochure". Every result says **where** it is
(`in Products › Water Cooler`), which is the part that matters when four
products each have a "500 LPH".

It is the same Postgres full-text and trigram search the mobile app uses, so a
term that finds something on a phone finds it here too.

**Documents are served by this app, not linked to Cloudinary directly.** They
have to be. Cloudinary stores PDF, Word and Excel as *raw* assets and serves
them from its download API, which answers `Content-Type:
application/octet-stream` with the storage id as the filename — whatever the
file actually is. A browser sent there cannot open a PDF, because it has never
been told it is one; it saves an extensionless blob instead. Streaming the
bytes through `/dashboard/documents/<id>/open/` is what lets the response say
`application/pdf` and `price-list.pdf`. The trade is that the bytes pass
through the server rather than straight from the CDN.

---

## The Django admin

`https://<your-service>/admin/` — the deeper console, for the things the
dashboard does not cover: the recycle bin, share links, devices, OTP
diagnostics, and editing any field directly.

**Only an Admin can get in.** Console access is the `role` field, not a separate
staff flag, so the two can never drift apart. A User account is bounced back to
the login screen — with the same message a wrong password gets, so the form
cannot be used to work out which numbers are admins.

The check runs on every request, not just at sign-in, so demoting somebody takes
effect on their next click rather than whenever their session expires.

**You need a password to sign in** — the admin has no OTP form. Set one when
creating the account, or via `POST /api/v1/auth/change-password/`.

Sign in with your mobile number (any format — `9876543210` works) and password.

What you can do there:

| Screen | Use it for |
|---|---|
| **Users** | Create accounts, set passwords, change roles, disable people |
| **Categories** | Browse the tree, rename, move, restore from the recycle bin |
| **Documents** | Find files, edit tags, restore, permanently delete |
| **Share links** | See active links and revoke any of them |
| **Devices** | See where each account is signed in |
| **OTP requests** | Diagnose "the code never arrived" (codes are hashed, never shown) |

Deleting a user in the admin **disables and hides** them rather than erasing
them, so the documents they uploaded keep their history. Same for categories
and files — they go to the recycle bin, and only "Delete permanently" destroys
anything.

Uploading is not available in the admin: a file row typed in by hand would
point at no stored object and every download of it would fail. Upload through
the app or the API.

> Set `ADMIN_URL` (e.g. `ADMIN_URL=secret-console/`) to move it off `/admin/`.
> That will not stop a determined attacker, but it removes the service from the
> constant background scanning aimed at that exact path.

---

## Managing users

Besides the admin console, accounts can be created from the server:

```bash
# Add someone for the mobile app (the default)
python manage.py create_user --phone 9812345678 --name "Ramesh" --role user

# Add someone who can also open the dashboard
python manage.py create_user --phone 9811111111 --name "Suresh" --role admin

# Change an existing person's role
python manage.py create_user --phone 9812345678 --role admin --update

# Switch an account off (they keep their files; they just cannot log in)
python manage.py create_user --phone 9812345678 --disable

# Switch it back on
python manage.py create_user --phone 9812345678 --enable
```

---

## Deploying to Render

The repository contains a Render blueprint ([render.yaml](render.yaml)), so the
service is defined in code rather than clicked together in a dashboard.

### 1. Create the service

Render → **New** → **Blueprint** → pick this repository. Render reads
`render.yaml` and creates a web service that builds with
[render-build.sh](render-build.sh) (install → collectstatic → migrate) and runs
under Gunicorn.

> **`render.yaml` only applies to Blueprint services.** If you create the web
> service by hand instead (New → Web Service), Render ignores the file
> completely — including the `generateValue: true` entries. You then have to
> set **every** variable yourself, `DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY`
> included, or the app refuses to boot with
> `ImproperlyConfigured: Required environment variable 'DJANGO_SECRET_KEY' is not set.`
>
> Generate them with:
> ```bash
> python -c "import secrets; print(secrets.token_urlsafe(64))"
> ```

### 2. Fill in the secrets

Render prompts for the values marked `sync: false`. They are never stored in
git.

For a Blueprint service, `DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY` are
generated automatically. For a hand-made service, add them yourself.

| Variable | Value |
|---|---|
| `DATABASE_URL` | Your Neon connection string (see below) |
| `CLOUDINARY_CLOUD_NAME` / `_API_KEY` / `_API_SECRET` | Cloudinary dashboard |
| `SMS_USER_ID` / `SMS_PASSWORD` / `SMS_SENDER_ID` | NimbusIT account |
| `SMS_ENTITY_ID` / `SMS_TEMPLATE_ID` | Your DLT registration |

`DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY` are generated by Render
automatically — you never see or set them. Rotating either signs everyone out,
which is exactly what you want if one ever leaks.

### 3. The database (Neon)

Paste the Neon **pooled** connection string into `DATABASE_URL`:

```
postgresql://USER:PASSWORD@ep-xxxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require
```

Use the *pooler* host. Render's free instance opens and closes connections
often, and Neon's direct endpoint has a much lower connection ceiling than the
pooled one.

`sslmode=require` is enforced in code regardless — Neon rejects unencrypted
connections, and so does this app.

The build step creates the tables and the two Postgres extensions the search
needs (`pg_trgm`, `unaccent`). Nothing to run by hand.

### 4. Create the first user

**If you have shell access** (Render Shell tab, on a paid instance):

```bash
python manage.py create_user --phone 9876543210 --name "Your Name" --role admin
```

**If you don't** (the free plan puts Shell behind an upgrade), use the setup
endpoint instead:

1. Add `SETUP_KEY` to the environment — a long random string, not a word:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Open `https://<service>.onrender.com/api/docs/` and find
   **POST /api/v1/setup/create-user/**
3. Click **Try it out** and send:
   ```json
   {
     "setup_key": "<the SETUP_KEY you set>",
     "phone_number": "9876543210",
     "full_name": "Your Name",
     "role": "admin",
     "password": "a-password-you-will-remember"
   }
   ```
   Including `password` lets you sign in at `POST /api/v1/auth/login/`
   immediately — which matters if SMS delivery is not working yet. Leave it out
   and the account can only sign in with an OTP.
4. **Delete `SETUP_KEY` from the environment.** The endpoint switches itself
   off and returns 404 again.

That last step matters. While `SETUP_KEY` is set, anyone who learns it can
create themselves an admin account — the endpoint deliberately bypasses the
"accounts are created by an administrator" rule, which is the whole reason it
is disabled unless you explicitly turn it on.

Its guards, if you want to leave it on longer: the key is compared in constant
time, attempts are limited to 10 an hour per IP, a key shorter than 8
characters is rejected outright, and every attempt — success or failure — is
logged.

### 5. Check it

| URL | Should show |
|---|---|
| `https://<service>.onrender.com/live/` | `{"status": "alive"}` |
| `https://<service>.onrender.com/ready/` | `{"status": "ready", …}` |
| `https://<service>.onrender.com/health/` | Component-by-component detail |
| `https://<service>.onrender.com/api/docs/` | Interactive API documentation |

Render's own health check uses `/live/`, which deliberately touches no
dependency: a database hiccup should page you, not make the platform restart a
container that is working fine. Point uptime monitoring at `/ready/` instead —
that one does check Postgres.

### Things to know about the free plan

- **The service sleeps after 15 minutes idle.** The next request takes 30–60
  seconds to wake it. Fine for internal use; if that becomes annoying,
  the paid instance removes it.
- **No Redis.** The app detects that and falls back to an in-process cache, so
  everything still works — the folder tree is just recomputed instead of
  cached. Add a Redis URL before running more than one instance, because
  otherwise each instance counts rate limits separately.
- **No background worker.** Nothing in the request path needs one. The only
  cost is that the recycle bin never empties itself; deleted files keep
  occupying Cloudinary storage. Run the purge by hand from the Shell tab
  occasionally, or add a worker later:
  ```bash
  python manage.py shell -c "from apps.files.tasks import purge_recycle_bin; purge_recycle_bin()"
  ```
- **Disk is ephemeral.** That is fine here — files live in Cloudinary and data
  lives in Neon. Nothing is written to local disk.

### Mobile access

A native Android/iOS app sends no `Origin` header, so CORS never applies to it —
the API is reachable from a phone out of the box. `CORS_ALLOW_ALL_ORIGINS` is
on for browser callers, which is safe here because the API uses no cookies and
no sessions: every request is authorised by an explicit `Authorization: Bearer`
token, which a browser will not attach on its own. Set `CORS_ALLOWED_ORIGINS`
to a specific list if a web front end is ever added.

---

## Running with Docker

```bash
docker compose up --build
```

Starts Postgres, Redis, the API, a background worker and NGINX together. The
API lands on <http://localhost:8000>. Migrations run automatically on start.

---

## Background jobs

A worker handles housekeeping on a schedule. It is optional day to day — the
app works without it — but without it the recycle bin never empties and storage
keeps growing.

```bash
celery -A config worker --loglevel=info
celery -A config beat   --loglevel=info
```

| Job | What it does |
|---|---|
| `purge_recycle_bin` | Permanently removes items deleted more than 30 days ago |
| `purge_deleted_folders` | Same, for categories |
| `expire_share_links` | Marks lapsed share links as expired |
| `trim_recent_files` | Keeps each person's "recent" list to a sensible length |
| `refresh_folder_counters` | Re-checks the file counts shown on each category |

---

## Tests

```bash
pytest              # needs a running Postgres
pytest --cov        # with a coverage report
```

207 tests cover the OTP flow and its rate limits, the permission rules, the
category tree (including the cases that would corrupt it), file handling,
browsing and search.

---

## How the code is arranged

```
apps/
  core/       Shared foundations: base models, error format, paging, logging
  accounts/   Users, OTP login, JWT          -> /api/v1/auth/...
  folders/    The category tree              -> /api/v1/categories/
  files/      Documents, sharing, search     -> /api/v1/documents/
config/       Settings, URLs, Celery
docs/         API reference for whoever builds the phone app
```

The Python packages are named `folders` and `files`; the public routes are
`/categories/` and `/documents/`, because that is the language the business
uses. Renaming the packages too would have churned every import for no gain.

Inside each app the layering is consistent:

- **models** — database tables
- **repositories** — all the database queries live here, and nowhere else
- **services** — the business rules
- **views** — read the request, call a service, return the answer

The point of the split is that a rule is written once and can be tested without
starting a web server.

---

## Things worth knowing

**Files are not stored on the server.** They go to Cloudinary; the database
keeps only the details (name, size, who uploaded it, which category). Downloads
use a link that expires after 15 minutes, so a link copied out of the app stops
working — sharing stays deliberate.

**Deleting is reversible.** Everything goes to a recycle bin first, and anyone
signed in can restore it. Permanent deletion is the one action with no undo,
and it is open to both roles — so treat it as a real button, not a safe one.

**Switching an account off is immediate.** The person is locked out on their
next action, even if they were already logged in.

**OTP protections.** Codes are stored scrambled, expire in 5 minutes, allow 5
wrong attempts, then lock that number for 30 minutes. There is a 60-second wait
between resends and a cap of 10 per number per day, so nobody can be spammed
and the SMS bill cannot run away.

**One security note to settle with NimbusIT.** Their documented address starts
with `http://`, not `https://`, which means the OTP travels unencrypted. The
system tries `https://` first; if NimbusIT don't support it, change
`SMS_BASE_URL` in `.env` to the `http://` address. Worth asking them for an
HTTPS endpoint.

---

## Before going live

- [ ] Set `DJANGO_SETTINGS_MODULE=config.settings.production`
- [ ] Generate fresh `DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY` (not the dev ones)
- [ ] Set `DJANGO_ALLOWED_HOSTS` to your real domain
- [ ] Put a TLS certificate on NGINX and keep `SECURE_SSL_REDIRECT=true`
- [ ] Set `POSTGRES_SSLMODE=require`
- [ ] Switch `SMS_BACKEND` to `NimbusITSMSBackend`
- [ ] Confirm the SMS text matches your approved DLT template word for word
- [ ] Turn on automatic database backups
- [ ] Run the Celery worker and beat scheduler
- [ ] Confirm `.env` is not in version control
