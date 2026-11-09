# djust application guidance

Before changing live views, forms, templates or navigation, read:
- [Application engineering conventions](https://github.com/djust-org/djust/blob/main/docs/ai/conventions.md)
- [Focused AI reference](https://github.com/djust-org/djust/tree/main/docs/ai)

Use the documentation/source matching the installed djust release; main may
contain newer APIs. Prefer native Django/djust behavior and declarative options
before application wrappers. Keep project-specific instructions in this file.

The installed release is djust 1.2.8 (`requirements.txt`). Its conventions are at
[v1.2.8/docs/ai/conventions.md](https://github.com/djust-org/djust/blob/v1.2.8/docs/ai/conventions.md).
Update this link when you upgrade djust.

# Project rules (apply to every developer and AI agent)

Requirements live in [docs/SRS.md](docs/SRS.md). Reference its `FR-x.y` IDs in
code, tests and commits.

## Stack and setup

- **Database:** PostgreSQL only (settings read `DB_*` from `.env`; see
  `.env.example`). Run `createdb leadshub`, then `make migrate`.
- **CSS:** Tailwind v4 via `npm` (`make css`, `make css-watch`). Never load the
  Tailwind CDN. Shared component classes (`.card`, `.input`, `.btn-primary`,
  `.btn-secondary`) are in `static/css/input.css`.
- `make install` installs Python and npm packages; `make dev` builds CSS and
  runs the server.
- **Docker (testing only):** `make docker-test` runs the suite against
  PostgreSQL. `make docker-up` serves the app on http://localhost:8000 with a
  demo logins from `manage.py seed_demo` (password `demo-pass-123`; brokers
  `demo`, `sara`; internal `platform`, `lead`, `buyer`, `content`, `finance`).
  `make docker-down` stops it. The compose file has throwaway secrets and
  `DEBUG=True`, so never deploy it as is.

## UI: responsive, bilingual, light and dark

- **Responsive:** build mobile first; every page must work at phone, tablet
  and laptop widths (`sm:`/`md:`/`lg:`), with no horizontal scrolling.
- **English and Arabic:** every user-facing string goes through
  `{% translate %}` / `gettext_lazy`. Add the Arabic text, then run
  `make messages`. URLs carry the language (`/en/`, `/ar/`).
- **RTL:** use logical utilities (`ms-`/`me-`/`ps-`/`pe-`/`start-`/`end-`/
  `text-start`), never `ml-`/`mr-`/`left-`/`right-`. Mark numbers, emails and
  phone numbers `dir="ltr"`.
- **Bilingual master data:** models have `name_en`/`name_ar`; templates use
  `display_name`.
- **Dark mode:** every color needs a `dark:` variant. The theme follows the
  device setting until the user toggles it.

## djust specifics in this project

- **Live views** include `PageLanguageMixin` (`leadshub/live.py`) and call
  `super().mount()`. It works around a djust 1.2.8 gap where WebSocket renders
  ignored the page language; see its docstring for when to remove it.
- **Every page declares who may open it.** Live views set
  `permission_required` and use `LoginRequiredLiveViewMixin, PermissionRequiredLiveViewMixin`
  (in that order, before `TenantMixin`), so anonymous users go to login and
  other audiences get 403, not 404. A test fails if a URL has no guard
  (`EveryPageIsGuardedTests`).
- **Three audiences, one per user (any number of roles within it):** internal staff
  (`accounts.access_back_office`), brokers (`access_client_portal`) and
  developers (`access_developer_view`). A user's roles never mix audiences
  (`role_errors`). The Django admin opens by role, not by `is_staff`.
- **Leaders:** a role can build on another (`ROLE_INCLUDES`). A Media Buyer is
  promoted by adding Media Buying Lead.
- **Forms in templates:** loop over `form.visible_fields`, not `form`. djust's
  engine yields field names for a bare form.
- **Roles are dynamic; the database is the source of truth.** The eight
  essential SRS roles are created from `accounts/roles.py` with default
  permissions. After that, Platform Admins add and remove permissions on any
  role in the admin, and `migrate` doesn't overwrite them; it only restores
  the audience entry and `REQUIRED_PERMISSIONS`. Essential roles keep their
  names and can't be deleted. `manage.py reset_role_permissions [role…]`
  restores the defaults. Platform Admins also add custom roles in the admin (`RoleProfile`: English and
  Arabic name, audience, permissions limited by `AUDIENCE_PERMISSIONS`).
  A user can hold several roles. Always check capabilities
  (`permission_required` / `user.has_perm()`), never role names, so custom
  roles work everywhere.
- **Admin UI is django-unfold (Tailwind).** Admin classes subclass
  `unfold.admin.ModelAdmin` / `StackedInline` / `TabularInline` (users:
  Unfold's user forms). Add new admin screens to the sidebar in
  `UNFOLD["SIDEBAR"]` (settings) with the matching view permission. Unfold's
  own strings have Arabic in `locale/unfold/` (outside `make messages`).
- **Template engines:** djust's engine searches only the folders listed in
  `TEMPLATES[0]["DIRS"]` (no `APP_DIRS`), so the admin's templates go to
  Django's engine. Add a new app's `templates/` folder there.
- **Theme setting** is shared with the admin: `localStorage["adminTheme"]`
  holds `"light"`, `"dark"` or `"auto"` as JSON.
- **Tenancy:** the broker office comes from the signed-in user
  (`accounts/tenancy.py`). Client-facing queries must stay inside that office.

## Permission setup for a new page or action

Follow these steps every time a page, event handler or admin screen is added.

1. **Choose the capability.** Reuse one from `CAPABILITIES` in
   `accounts/roles.py` if it fits. Otherwise add a new
   `("codename", _("Label"))`. Then run
   `.venv/bin/python manage.py makemigrations accounts`, add the Arabic label
   and run `make messages`.
2. **Give it to roles.**
   - Add it to the right essential roles in `ROLE_PERMISSIONS`, following the
     SRS §2.2 matrix.
   - Add it to `AUDIENCE_PERMISSIONS` for every audience allowed to hold it,
     so custom roles can be given it. Never give internal capabilities (ad
     spend, margin, account health) to the client or developer audiences.
   - `ROLE_PERMISSIONS` only seeds new databases. For databases that already
     exist, add a data migration that grants the capability to those roles
     (`group.permissions.add(...)`), and mention it in the release notes so
     admins know.
3. **Guard the page.** Use
   `LoginRequiredLiveViewMixin, PermissionRequiredLiveViewMixin[, TenantMixin], PageLanguageMixin, LiveView`
   and set `permission_required`. A page usually needs its audience's entry
   capability plus the specific one, for example
   `["accounts.access_client_portal", "accounts.run_campaigns"]`.
4. **Guard each action.** Put `@permission_required("accounts.x")` from djust
   above `@event_handler()` on every handler that changes data or reveals
   more than the page itself. Inside the handler, also check that the object
   belongs to the user's office (tenancy) or is otherwise theirs.
5. **Hide what can't be used.** Show links and buttons only to users with the
   capability. Hiding is for convenience; the checks in steps 3 and 4 are what
   enforce access.
6. **Admin screens** follow model permissions (`app.view_model`, …). List the
   ones internal roles may hold in `AUDIENCE_PERMISSIONS[Audience.INTERNAL]`
   and give them to the right essential roles.
7. **Never check role names** (`has_role`, group names) to allow access. Check
   capabilities only, so custom roles work.
8. **Tests:**
   - Add the page to `PAGE_AUDIENCES` in `leadshub/tests.py` (role-by-page
     matrix and WebSocket mount checks).
   - For each guarded action, test that a role without the capability is
     refused (`send_event`) and that another office's object is refused.
   - Update `RoleMatrixTests` when the matrix changes.
   - `EveryPageIsGuardedTests` fails if the URL has no guard.

## Django and djust practices

- **State:** set it up in `mount()`. Keep internal objects in `_private`
  attributes and expose only what the template renders.
- **Forms:** use `FormMixin` with `form_class` and Django validation, rendered
  with `as_live()` / `as_live_field()`. Use explicit-field `ModelForm`s for
  single-model editors. Take lengths and choices from the model.
- **Event handlers:** use `@event_handler()`. Validate parameters, and check the
  user may change the object at the moment of the change. Run long work with
  `start_async()` or `@background`.
- **Templates:** pass authorized models and querysets, not hand-built
  dictionaries. Reuse existing templates and components. Never render
  sensitive fields.
- **Navigation:** use `dj-navigate` between live pages and `dj-patch` for state
  within a page. Keep filters and pagination in the URL.
- **Security:** check permissions at every entry point; form validation doesn't
  replace this. `Model.save()` doesn't call `full_clean()`. Send notifications
  only after commit (`transaction.on_commit`).
- **New helpers:** before adding a helper, mixin or wrapper, check djust and
  Django for a built-in way and note why it wasn't enough.

## Every task ships with tests

A feature, fix or refactor isn't done until it has tests for the behavior it
changes and they pass.

- Put tests in the app's `tests.py` or `tests/` package using
  `django.test.TestCase`.
- Test live views with `djust.testing.LiveViewTestClient`: `mount()`,
  `send_event()`, `assert_state()`, `render()`, `assert_redirect()`,
  `assert_patch()`. Use `LiveComponentTestClient` for components.
- Cover what the change claims: validation errors, permissions (another user's
  object is refused), rendering, navigation, and that no sensitive data leaks.
- Consider `LiveViewSmokeTest` (with `fuzz = True`) for render and XSS coverage,
  and `@performance_test(max_queries=...)` for list views.
- Before finishing, run `make test` and `make check` and fix what they report;
  don't silence them. Unit tests don't prove browser behavior such as focus or
  visible feedback, so check those in a browser when a change depends on them.
