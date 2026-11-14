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
| Send a test email through the configured SMTP server (Mailtrap sandbox in development; settings in `.env.example`) | `make test-email TO=you@example.com` |
| Deploy to Fly.io by hand (pushes to `master` deploy through `.github/workflows/deploy.yml` after the tests pass; setup in `docs/DEPLOY.md`) | `make fly-deploy` |
| Local S3-compatible storage for uploads (RustFS in Docker, then the bucket) | `make storage-up` |
| Create the uploads bucket if missing (local S3 servers only) | `.venv/bin/python manage.py ensure_storage_bucket` |
| Tests against PostgreSQL in Docker | `make docker-test` |
| App in Docker on :8000 (migrate, seed, serve) | `make docker-up` / `make docker-down` |
| Docker after `requirements.txt` / `Dockerfile` changes (code is mounted and reloads by itself) | `make docker-build` |
| Docker: apply new migrations / follow server and CSS logs | `make docker-migrate` / `make docker-logs` |

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
  - `services.py` holds the office team rules (invite, deactivate, campaign
    access, opening an office from the admin). Each function takes the person
    acting and refuses what they may not do. `selectors.py` holds the reads,
    such as `office_members()` with the flags the team page shows.
  - Invitations: an account is created with no usable password, and the
    emailed link is a Django password-reset token.
    `accounts.views.AcceptInvitationView` sets the password and signs the
    person in. The sign-in name is the email address in lower case.
  - Self sign-up (`/register/`, ROADMAP Q6): `services.register_office`
    creates a `BrokerOffice` with status *pending*, an `OfficeRegistration`
    and an applicant with no role. They confirm their email
    (`accounts.tokens`), then staff approve or reject the office with the
    admin's list actions (`approve_registrations` / `reject_registrations`).
    Approval gives the applicant the Broker Owner role. Until then `home`
    sends them to `/register/status/`. Emails go out in the sign-up's
    language.
- **`catalog/`**: bilingual master data (Developer, Area, UnitType, Project,
  AudiencePreset) on the abstract `BilingualNamed` (`name_en`/`name_ar`,
  `display_name`). Other apps read it through `catalog/selectors.py`.
- **`creatives/`**: the creative library (SRS §3.2).
  - `Creative.objects.usable()` is what brokers may see: published, with the
    offer still on.
  - The admin is for Content Managers.
  - `services.may_open_file` decides who opens a creative's file.
- **Emails** (`leadshub/emails.py`): `send_after_commit()` sends every email
  in the LeadsHub layout (`leadshub/templates/email/`), as HTML with a
  plain-text copy, in the reader's language. AGENTS.md says how to add one.
- **Uploads** (`leadshub/files.py`): private storage, either S3 / RustFS or
  disk, chosen by the `AWS_*` settings. File URLs go through
  `PrivateFileView`, which asks the owning app's rule.

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
  `permission_required`, often a list (entry capability plus the page's own).
  It is enforced in three places:
  - on HTTP, through Django's `PermissionRequiredMixin`, which runs before
    `TenantMixin`, so other audiences get 403 rather than 404 (djust 1.2.8's
    own mixin can't take a list);
  - on the WebSocket mount (djust's `check_view_auth`);
  - on every event (`LIVEVIEW_CONFIG["reauth_on_event"]`).
  Handlers that change data also carry `@live.permission_required(...)`.
- **Navigation:** `/` (`accounts.views.home`) and login both use
  `landing_url()` to send each audience to its own home:
  `/back-office/`, `/portal/` or `/developer/`. The sidebar links come from
  `leadshub.context_processors.navigation`, which lists the live pages the
  person's capabilities open, as `dj-navigate` links. Each link has an icon
  (`ICONS` there). The sidebar is in `base.html`, always shown on laptops and
  a drawer on smaller screens (`static/js/sidebar.js`).
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
  - `PageLanguageMixin` (`leadshub/live.py`) keeps WebSocket renders and
    event handlers in the page's language.
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
  `check_view_auth`. It also doesn't bind the office, and its `render()`
  can't draw `{% live_form %}`. `leadshub/tests.py` shows the workarounds
  (`_team_client`, `render_with_patches()`) and a test of the real
  WebSocket consumer (`TeamPageOverTheSocketTests`).
- Emails go to `django.core.mail.outbox` once the transaction commits. Wrap
  the action in `self.captureOnCommitCallbacks(execute=True)`. The HTML copy
  is `message.alternatives[0][0]` (`EmailLayoutTests`).
- `creatives/test_utils.py`:
  - `TemporaryUploadsMixin` keeps a test class's uploads in a temporary
    folder.
  - `make_project()` and `make_creative()` build catalog data and a published
    image creative.


## Architecture Conventions

- **Views are thin.** Views handle HTTP concerns (serialization, permissions, status codes). Business logic lives in `services.py`.
- **Services contain business logic.** Services are plain Python functions or classes. They receive validated data from views and return domain objects.
- **Selectors for complex reads.** Read-heavy queries with joins, annotations, or filters go in `selectors.py`, not in views or services.
- **One app per domain.** Each Django app represents a bounded context. Apps communicate through services, not by importing each other's models directly.

```python
# views.py — thin view
class CreateOrderView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CreateOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = OrderService.create(user=request.user, **serializer.validated_data)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)
```

```python
# services.py — business logic
class OrderService:
    @staticmethod
    def create(user: User, product_id: int, quantity: int) -> Order:
        product = Product.objects.get(id=product_id)
        if product.stock < quantity:
            raise InsufficientStockError(product.name)
        order = Order.objects.create(user=user, product=product, quantity=quantity)
        product.stock -= quantity
        product.save(update_fields=["stock"])
        send_order_confirmation.delay(order.id)
        return order
```

## Models

- Custom user model in `apps/users/models.py` extending `AbstractUser`
- Use `models.TextChoices` for enum fields
- Add `__str__`, `Meta.ordering`, and `Meta.verbose_name` on all models
- Use `update_fields` in `.save()` calls to avoid overwriting concurrent changes
- Index frequently queried fields with `db_index=True` or `Meta.indexes`

```python
class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        SHIPPED = "shipped", "Shipped"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"Order #{self.pk} — {self.status}"
```

## DRF Serializers

- Use `ModelSerializer` for standard CRUD
- Separate serializers for create/update vs. read (e.g., `CreateOrderSerializer`, `OrderSerializer`)
- Validate business rules in the serializer's `validate` method or in the service layer — not both
- Use `SerializerMethodField` sparingly — prefer annotations in selectors for computed fields

## Celery Tasks

- Tasks in `tasks.py` within each app
- Always use `.delay()` or `.apply_async()` — never call task functions directly
- Tasks should be idempotent (safe to retry)
- Use `@shared_task(bind=True, max_retries=3)` with exponential backoff
- Pass IDs to tasks, not full objects (objects cannot be serialized reliably)

```python
@shared_task(bind=True, max_retries=3)
def send_order_confirmation(self, order_id: int) -> None:
    try:
        order = Order.objects.select_related("user", "product").get(id=order_id)
        EmailService.send_confirmation(order)
    except Exception as exc:
        self.retry(exc=exc, countdown=2 ** self.request.retries)
```

## Testing

- Factory Boy factories in `tests/factories.py` within each app
- Use `@pytest.mark.django_db` on tests that touch the database
- Mock external services (email, payment) — never call real APIs in tests
- Aim for >80% coverage on services and views

## Git

- Conventional commits: feat:, fix:, chore:, refactor:
- Always include migrations in the same commit as model changes
- Run `python manage.py makemigrations --check` in CI to catch missing migrations

## Do NOT

- Do not put business logic in views or serializers — use services
- Do not import models from other apps directly — go through services or selectors
- Do not use `objects.raw()` or raw SQL unless the ORM genuinely cannot express the query
- Do not use `print()` — use Django's `logging` module
- Do not use `*` imports
- Do not modify migration files after they have been applied