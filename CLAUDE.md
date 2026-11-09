# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The project rules (stack, UI, djust specifics, the permission checklist for new
pages and actions, and "every task ships with tests") are in AGENTS.md and
apply to every change:

@AGENTS.md

Requirements are in `docs/SRS.md`. Cite its `FR-x.y` / § IDs in code, tests
and commits.

## Commands

All Python runs from `.venv` (`.venv/bin/python`). Settings read `.env`.

| Task | Command |
|---|---|
| Install Python and npm packages | `make install` |
| Build CSS and run the dev server (uvicorn, port 8000, reload) | `make dev` |
| Rebuild Tailwind CSS (`static/css/output.css`, not in git) | `make css` / `make css-watch` |
| Apply migrations (also creates missing essential roles) | `make migrate` |
| Reset essential roles to their default permissions | `.venv/bin/python manage.py reset_role_permissions ["Media Buyer" …]` |
| Full test suite | `make test` |
| One app / class / test | `.venv/bin/python manage.py test accounts.tests.CustomRoleTests.test_names_must_be_unique` |
| djust and Django checks | `make check`. The `djust.C401` watchdog warning is known and dev-only. |
| Extract and compile Arabic translations | `make messages` (then fill the empty `msgstr` in `locale/ar/LC_MESSAGES/django.po` and run it again) |
| Demo data (idempotent) | `.venv/bin/python manage.py seed_demo`: every login uses `demo-pass-123`. Brokers: `demo`, `sara`. Internal: `platform`, `lead`, `buyer`, `content`, `finance`. |
| Tests against PostgreSQL in Docker | `make docker-test` |
| App in Docker on :8000 (migrate, seed, serve) | `make docker-up` / `make docker-down` |

Tests need a local PostgreSQL database `leadshub` (`createdb leadshub`).
Django creates `test_leadshub` itself.

## Architecture

Django 5.2 with djust 1.2.8. Pages are djust LiveViews, rendered first over
HTTP and then kept live over a WebSocket (channels, served by uvicorn from
`leadshub/asgi.py`). The project package is `leadshub/`, which also holds the
live views and base templates. There are two domain apps:

- **`accounts/`**: users, tenants and access control.
  - `User` (custom `AUTH_USER_MODEL`).
  - `BrokerOffice` is the tenant. `Membership` links a user to one office.
  - `RoleProfile` is a Django `Group` with an English and Arabic name and an
    audience.
  - `Capability` is an unmanaged model that only carries the custom
    permissions.
- **`catalog/`**: bilingual master data (Developer, Area, UnitType, Project)
  on the abstract `BilingualNamed` (`name_en`/`name_ar`, `display_name`).

### Access control (read several files to see it whole)

- **Model** (`accounts/roles.py`): roles are Django groups, and what they
  grant are permissions called capabilities.
  - Each user belongs to one **audience**: internal, client (broker) or
    developer. Each audience has an entry capability that opens its pages:
    `access_back_office`, `access_client_portal` or `access_developer_view`.
  - The eight essential roles (the `Role` enum and `ROLE_PERMISSIONS`, with
    `ROLE_INCLUDES` for lead ⊇ buyer) are created with default permissions
    by `sync_role_groups` on `post_migrate` (`accounts/apps.py`). After that
    the database is the source of truth: their permissions are edited in the
    admin, and `migrate` only restores each role's audience entry and
    `REQUIRED_PERMISSIONS`.
  - Custom roles exist only in the database. They are created in the admin
    through `RoleForm` (`accounts/admin.py`), which writes the group's
    permissions and is limited by `AUDIENCE_PERMISSIONS`.
  - `role_errors()` enforces one audience per user.
- **Pages** (`leadshub/views.py`, `leadshub/urls.py`): each LiveView sets
  `permission_required`. djust enforces it in three places:
  - on HTTP, through `PermissionRequiredLiveViewMixin.dispatch`, which runs
    before `TenantMixin`, so other audiences get 403 rather than 404;
  - on the WebSocket mount;
  - on every event (`LIVEVIEW_CONFIG["reauth_on_event"]`).
- **Navigation:** `/` (`accounts.views.home`) and login both use
  `landing_url()` to send each audience to its own home:
  `/back-office/`, `/portal/` or `/developer/`.
- **Admin** (`accounts/admin_site.py`): the admin site replaces the
  `django.contrib.admin` entry in `INSTALLED_APPS`. It lets users in by
  `access_back_office`, not `is_staff`, and sends sign-in to the app's login
  page.
- **Tenancy:** djust's `TenantMiddleware` and `TenantMixin` call
  `accounts.tenancy.resolve_office` to bind the user's office on both HTTP and
  WebSocket requests.
- **Test guards** in `leadshub/tests.py`:
  - `EveryPageIsGuardedTests` fails for any URL without a guard.
  - `PageAccessTests` checks every role against every page, and the WebSocket
    auth path through `djust.auth.core.check_view_auth`.

### Templates and i18n

- Two template engines are configured in `settings.TEMPLATES`. djust's Rust
  engine renders the app's templates and searches only its listed `DIRS`. A
  second `DjangoTemplates` engine renders the admin, which uses the Unfold
  (Tailwind) theme; djust's engine can't run Unfold's custom filters.
- Live templates extend `leadshub/templates/leadshub/base.html` and put the
  live region in `<div dj-root dj-view="leadshub.views.X">`.
- Live views also need their module listed in `LIVEVIEW_ALLOWED_MODULES`.
- `templates/403.html` is the translated "access denied" page.
- URLs come from `i18n_patterns` (`/en/`, `/ar/`).
  - `PageLanguageMixin` (`leadshub/live.py`) keeps WebSocket renders in the
    page's language.
  - Build translated labels in `get_context_data()`, not `mount()`, so they
    follow the page language.
  - `leadshub/formats/ar` keeps Latin digit grouping for money.

### Testing helpers

- `accounts/test_utils.py`:
  - `make_user(username, *roles, office=None, **fields)` creates a user with
    password `pass-12345`.
  - `make_office(name, **fields)` creates a broker office.
- Live views are tested with `djust.testing.LiveViewTestClient`. Its
  `mount()` doesn't run auth checks, so test access over HTTP or with
  `check_view_auth`.
