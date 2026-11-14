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

The build order, the decisions already made and the open business questions
are in [docs/ROADMAP.md](docs/ROADMAP.md). Read a milestone's section before
starting it, and don't build the part marked `OPEN QUESTION` until the
business has answered.

## Stack and setup

- **Database:** PostgreSQL only (settings read `DB_*` from `.env`; see
  `.env.example`). Run `createdb leadshub`, then `make migrate`.
- **File storage:** uploads use the S3 API: S3 in production, RustFS in
  Docker locally (`make storage-up`, with the `AWS_*` variables from
  `.env.example`). Without `AWS_STORAGE_BUCKET_NAME` they stay on disk in
  `media/`.
- **CSS:** Tailwind v4 via `npm` (`make css`, `make css-watch`). Never load the
  Tailwind CDN. Shared component classes (`.card`, `.input`, `.btn-primary`,
  `.btn-secondary`) are in `static/css/input.css`.
- `make install` installs Python and npm packages; `make dev` builds CSS and
  runs the server.
- **Docker (development and testing only):** `make docker-test` runs the
  suite against PostgreSQL. `make docker-up` serves the app on
  http://localhost:8000 with demo logins from `manage.py seed_demo` (password
  `demo-pass-123`; brokers `demo`, `sara`; internal `platform`, `lead`,
  `buyer`, `content`, `finance`). `make docker-down` stops it. The compose
  file has throwaway secrets and `DEBUG=True`, so never deploy it as is.
  - The project folder is mounted into the containers. The server restarts
    on changes to Python, templates and compiled translations, and the `css`
    service rebuilds Tailwind, so code changes need no image rebuild.
  - Run `make docker-build` only after `requirements.txt` or the
    `Dockerfile` changes, and `make docker-migrate` after adding migrations.
    `make docker-logs` follows the server and CSS logs.

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
  `super().mount()`. It works around a djust 1.2.8 gap where work over the
  WebSocket ignored the page language: it activates the page language around
  every render and every event handler (form errors, messages and emails a
  handler makes). See its docstring for when to remove it.
- **Every page declares who may open it.** Live views set
  `permission_required` and use djust's `LoginRequiredLiveViewMixin`, then
  Django's `PermissionRequiredMixin` (`django.contrib.auth.mixins`), in that
  order and before `TenantMixin`, so anonymous users go to login and other
  audiences get 403, not 404. Django's mixin accepts a list of permissions,
  and djust replays it on the WebSocket mount. Don't use djust's
  `PermissionRequiredLiveViewMixin`: in 1.2.8 it takes only one permission
  over HTTP and raises `TypeError` for a list. A test fails if a URL has no
  guard (`EveryPageIsGuardedTests`).
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
- **Emails** use the LeadsHub layout. Send them with
  `leadshub.emails.send_after_commit(name, context, recipients, language=...)`,
  never `send_mail`. It sends the email once the transaction commits, as HTML
  with a plain-text copy, in the reader's language (right-to-left for Arabic).
  Each email has three templates in its app, for example
  `accounts/email/confirm_email`:
  - `<name>_subject.txt`, wrapped in `{% autoescape off %}`;
  - `<name>_body.txt`, extending `email/base.txt`;
  - `<name>_body.html`, extending `email/base.html` (blocks `title`,
    `preheader`, `heading`, `content`, `footer`) and using the parts in
    `leadshub/templates/email/`: `button.html` (one main action),
    `link_fallback.html` (the link written out) and `details.html` (label and
    value rows built in Python with `gettext_lazy`).
  Email clients drop most CSS, so keep styles inline and use the layout's
  `start`/`end` context values instead of `left`/`right`. Django's engine
  renders them: with `DEBUG` on, djust's adds attributes to anything that
  looks like an HTML tag.
- **Theme setting** is shared with the admin: `localStorage["adminTheme"]`
  holds `"light"`, `"dark"` or `"auto"` as JSON.
- **Tenancy:** the broker office comes from the signed-in user
  (`accounts/tenancy.py`). Client-facing queries must stay inside that office.
- **URL state:** `dj-patch` only adds or overwrites URL parameters; it can't
  remove one.
  - A link that only sets values can use it, if it states each value it
    changes (`?page=1`, not an empty query).
  - To remove parameters, such as clearing filters, call a handler that runs
    `self.live_patch(params=...)`, which sets them exactly (`LibraryView`).
- **Uploads are private** (`leadshub/files.py`).
  - A file field's `.url` points at `PrivateFileView`. That view asks the app
    that owns the file (by the first folder of its name) whether the
    signed-in person may open it, then streams it. There is never a public
    media URL.
  - A new kind of upload stores its files under its own folder, for example
    `upload_to` returning `projectx/<random>/<name>`.
  - It adds its rule `may_open_file(user, name)` to `_access_rules()`, and
    tests that another office or audience gets 404.
  - Limit types and sizes with validators (`FileExtensionValidator` and
    `creatives.models.MaxFileSize`). Never accept HTML or SVG: the site
    would serve them as its own pages.

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
   `LoginRequiredLiveViewMixin, PermissionRequiredMixin[, TenantMixin], PageLanguageMixin, LiveView`
   (djust's login mixin, Django's permission mixin) and set
   `permission_required`. A page usually needs its audience's entry
   capability plus the specific one, for example
   `["accounts.access_client_portal", "accounts.run_campaigns"]`.
4. **Guard each action.** Put djust's `permission_required` above
   `event_handler()` on every handler that changes data or reveals more than
   the page itself. Inside a view class, the `permission_required` attribute
   hides a bare decorator of the same name, so write
   `from djust import decorators as live` and
   `@live.permission_required("accounts.x")` above `@live.event_handler()`.
   (djust's S009 check doesn't recognise an import alias.) Put the rule itself
   in the app's `services.py`: it takes the person acting, checks their
   capability, and refuses an object outside their office (tenancy) or
   otherwise not theirs.
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
  with `as_live()` / `as_live_field()` (in templates `{% live_form view %}` /
  `{% live_field view "name" %}`). They draw fields with the project's
  component classes (`LIVEVIEW_CONFIG["leadshub"]` in settings and
  `leadshub.live.LiveFormAdapter`). Those classes are named in Python, which
  Tailwind doesn't scan, so their styles live in `static/css/input.css`. Use
  explicit-field `ModelForm`s for single-model editors. Take lengths and
  choices from the model.
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
  `assert_patch()`. Use `LiveComponentTestClient` for components. Its gaps,
  and the workarounds in `leadshub/tests.py`:
  - `mount()` runs no auth checks and doesn't bind the office. For a
    `TenantMixin` view, mount a subclass that calls `self._ensure_tenant()`
    first, as djust's live mount does (`_team_client`).
  - `render()` can't draw `{% live_form %}`. Use `render_with_patches()`,
    the WebSocket render.
  - It skips djust's WebSocket consumer. To test the real live path (mount
    auth, office binding, the re-check on every event), use a
    `TransactionTestCase` with Channels' `WebsocketCommunicator` and a real
    session (`TeamPageOverTheSocketTests`).
- Tests that save files use `creatives.test_utils.TemporaryUploadsMixin`,
  which keeps uploads in a temporary folder.
- A request to an `/ar/` page leaves Arabic active in the test thread. A test
  that reads translated text made outside a request starts with
  `self.enterContext(translation.override("en"))`.
- Cover what the change claims: validation errors, permissions (another user's
  object is refused), rendering, navigation, and that no sensitive data leaks.
- Consider `LiveViewSmokeTest` (with `fuzz = True`) for render and XSS coverage,
  and `@performance_test(max_queries=...)` for list views.
- Before finishing, run `make test` and `make check` and fix what they report;
  don't silence them. Unit tests don't prove browser behavior such as focus or
  visible feedback, so check those in a browser when a change depends on them.
