# Connect Business Managers with Facebook Login — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Platform Admin adds or reconnects a Business Manager by logging in with Facebook Login for Business, never by pasting a token.

**Architecture:** Two admin views on `MetaBusinessManagerAdmin` do the work:
- `connect/` redirects to Meta's OAuth dialog with `config_id` and a session `state`.
- `connect/callback/` checks `state` and calls one service, `meta_sync.connect_business()`.

That service exchanges the code for a system-user token, reads the business, runs the existing `check_access`, then creates or updates the row. The views reach Meta only through `sync.py`, so tests swap in `FakeMeta`.

**Tech Stack:** Django 5.2 admin (Unfold), `requests`, `facebook-business` SDK, Fernet storage (`ad_channels/crypto.py`).

**Spec:** `docs/superpowers/specs/2026-11-16-meta-business-login-design.md`

## Global Constraints

- **Token type:** BISU (system-user) tokens only. Login is a server redirect. No JS SDK.
- **Login dialog:**
  - URL: `https://www.facebook.com/{META_API_VERSION}/dialog/oauth`
  - Parameters: `client_id`, `redirect_uri`, `state`, `config_id`, `response_type=code`, `override_default_response_type=true`
- **Code exchange:** `GET https://graph.facebook.com/{META_API_VERSION}/oauth/access_token` with `client_id`, `client_secret`, `redirect_uri`, `code`. The response JSON holds `access_token`.
- **Business lookup:** `GET /me?fields=client_business_id`, then `GET /{business_id}?fields=name`.
- **New setting:** `META_LOGIN_CONFIG_ID` (env). When it or `META_APP_ID` is unset, both buttons are hidden and `connect/` shows an error message.
- **Secrets:**
  - The token, the code and `META_APP_SECRET` never appear in a response body, a message, a log line or a `MetaError` text.
  - Views carry `sensitive_variables` and `sensitive_post_parameters`.
- **Permissions:**
  - `connect/` and `connect/callback/` need `ad_channels.add_metabusinessmanager`.
  - A reconnect (`?bm=<pk>`) needs `ad_channels.change_metabusinessmanager` for that object.
  - The Media Buying Lead (view only) sees no buttons.
- **Language:** every new string goes through `gettext_lazy`, and Arabic is filled in `locale/ar/LC_MESSAGES/django.po` (`make messages`).
- **Spec IDs:** cite US-META-01 and FR-6.10 in docstrings, tests and commits.

## Review Focus

1. **Admin language:** an admin on `/ar/admin/` must get the same `redirect_uri` as on `/en/`, or Meta rejects it. Always build it from `reverse(...)` under `translation.override("en")`. After the callback, return to the page in the language the admin started in, stored in the session. Test: Task 2, `test_redirect_uri_is_language_independent`.
2. **Callback replay:** opening the same callback URL twice fails the second time, because `state` is popped on first use. Test: Task 2, `test_state_is_single_use`.
3. **Business already owned by an inactive row:** connecting a business whose row is deactivated updates its token but leaves `is_active` as it was. Test: Task 1, `test_connect_existing_inactive_keeps_inactive`.
4. **Network failure during the code exchange:** the admin sees "Couldn't reach Meta; try again later." and nothing is saved. Test: Task 1, `test_exchange_network_error_is_scrubbed`.
5. **The token has no `client_business_id`** (wrong configuration type, e.g. a user-token configuration): refuse with a message naming the configuration. Test: Task 1, `test_missing_client_business_id_refused`.

---

### Task 1: Meta calls and the `connect_business` service

**Files:**
- Modify: `ad_channels/meta_ads/client.py` (add `exchange_code`, `MetaClient.business_for_token`)
- Modify: `ad_channels/meta_ads/sync.py` (add `ConnectError`, `exchange_code` seam, `connect_business`)
- Test: `ad_channels/tests.py` (new `ExchangeCodeTests`, `ConnectBusinessTests`; extend `FakeMeta`)

**Interfaces:**
- Produces:
  - `client.exchange_code(code: str, redirect_uri: str) -> str`: returns the access token. Raises `MetaError` with `code` and `META_APP_SECRET` scrubbed out. Uses `requests.get(..., timeout=10)`. A Graph error body (`{"error": {"message", "code"}}`) becomes `MetaError(f"{message} (Meta error {code})")`. A `requests.RequestException` becomes `MetaError("Couldn't reach Meta; try again later.")`.
  - `MetaClient.business_for_token() -> tuple[str, str]`: returns `(business_id, name)`. Raises `MetaError("This login didn't return a business. Check that the Facebook Login for Business configuration uses a system-user token.", token_rejected=True)` when `client_business_id` is missing.
  - `sync.exchange_code(code, redirect_uri) -> str`: a thin seam that tests patch, like `client_for`.
  - `sync.ConnectError(Exception)`: its message is safe to show.
  - `sync.connect_business(code: str, redirect_uri: str, *, expected: MetaBusinessManager | None = None) -> tuple[MetaBusinessManager, bool]`: returns `(bm, created)`. Steps:
    1. `exchange_code`.
    2. `client_for(token).business_for_token()`.
    3. When `expected` is given and the ID differs, raise `ConnectError(_("You picked a different business than %(bm)s. Nothing was changed."))`.
    4. `client_for(token).check_access(business_id)`.
    5. `update_or_create` on `meta_business_id`. The name comes from Meta only on create. Set the token, `token_status=VALID`, `token_error=""`, `token_checked_at=now`. Leave `is_active` alone.

    Every `MetaError` becomes a `ConnectError(_("Meta refused the connection: %(reason)s"))`, and nothing is saved.

- [ ] **Step 1: Extend `FakeMeta`** with `business = ("111", "BM One")`, `business_error = None`, a `business_for_token()` that returns or raises them, plus `exchanged = []` and `exchange_error = None`. `MetaTestCase.setUp` also patches `ad_channels.meta_ads.sync.exchange_code` with a function that records `(code, redirect_uri)`, raises `exchange_error` if set, and otherwise returns `TOKEN`.

- [ ] **Step 2: Write the failing tests**

```python
class ConnectBusinessTests(MetaTestCase):
    def test_creates_bm_with_encrypted_valid_token(self):
        bm, created = connect_business("the-code", "https://x/cb/")
        self.assertTrue(created)
        self.assertEqual((bm.meta_business_id, bm.name), ("111", "BM One"))
        self.assertEqual(bm.get_token(), TOKEN)
        self.assertNotIn(TOKEN, bm.token_ciphertext)
        self.assertEqual(bm.token_status, MetaBusinessManager.TokenStatus.VALID)
        self.assertEqual(self.meta.exchanged, [("the-code", "https://x/cb/")])

    def test_existing_business_updates_token_no_new_row(self):
        old = make_bm(name="Kept name", token=NEW_TOKEN, status=MetaBusinessManager.TokenStatus.INVALID)
        bm, created = connect_business("c", "u")
        self.assertFalse(created)
        self.assertEqual(MetaBusinessManager.objects.count(), 1)
        old.refresh_from_db()
        self.assertEqual((old.name, old.get_token(), old.token_status), ("Kept name", TOKEN, "valid"))

    def test_connect_existing_inactive_keeps_inactive(self):
        make_bm(is_active=False)
        bm, _ = connect_business("c", "u")
        self.assertFalse(bm.is_active)

    def test_reconnect_to_different_business_refused(self):
        other = make_bm(name="Other", business_id="222", token=NEW_TOKEN)
        with self.assertRaisesMessage(ConnectError, "different business"):
            connect_business("c", "u", expected=other)
        other.refresh_from_db()
        self.assertEqual(other.get_token(), NEW_TOKEN)
        self.assertFalse(MetaBusinessManager.objects.filter(meta_business_id="111").exists())

    def test_meta_errors_save_nothing(self):
        for attr in ("exchange_error", "business_error", "check_error"):
            with self.subTest(attr):
                setattr(self.meta, attr, REJECTED)
                with self.assertRaises(ConnectError):
                    connect_business("c", "u")
                setattr(self.meta, attr, None)
        self.assertEqual(MetaBusinessManager.objects.count(), 0)


@override_settings(META_APP_ID="1", META_APP_SECRET="app-secret-xyz", META_API_VERSION="v25.0")
class ExchangeCodeTests(TestCase):
    # Patch ad_channels.meta_ads.client.requests.get
    def test_returns_access_token_and_sends_params(self): ...  # assert URL + params dict exactly as in Global Constraints
    def test_graph_error_is_scrubbed(self): ...  # error message echoing code and secret → neither in str(error)
    def test_exchange_network_error_is_scrubbed(self): ...  # ConnectionError → "Couldn't reach Meta; try again later."
    def test_missing_client_business_id_refused(self): ...  # MetaClient.business_for_token with the SDK call mocked to return {"id": "me"}
```

- [ ] **Step 3: Run them to make sure they fail.** Run `.venv/bin/python manage.py test ad_channels.tests.ConnectBusinessTests ad_channels.tests.ExchangeCodeTests`. Expected: ImportError for `connect_business` and `exchange_code`.

- [ ] **Step 4: Implement** the Interfaces above. `business_for_token` uses `User(fbid="me", api=self._api).api_get(fields=["client_business_id"])`, then `Business(fbid=id, api=self._api).api_get(fields=["name"])`, both inside `self._errors()`. Decorate everything that holds the token or code with `@sensitive_variables`.

- [ ] **Step 5: Run the same command.** Expected: all PASS.

- [ ] **Step 6: Commit** with the message `feat(ad_channels): connect a Business Manager from a Facebook Login code (US-META-01, FR-6.10)`.

---

### Task 2: Connect and callback views

**Files:**
- Modify: `ad_channels/admin.py` (`MetaBusinessManagerAdmin.get_urls`, `connect_view`, `callback_view`)
- Modify: `leadshub/settings.py` (`META_LOGIN_CONFIG_ID = os.environ.get("META_LOGIN_CONFIG_ID") or None`, next to `META_APP_ID`)
- Modify: `.env.example` (add `META_LOGIN_CONFIG_ID=`, with a comment listing the configuration settings from the spec's Constraints and the callback URL to register)
- Test: `ad_channels/tests.py` (new `ConnectViewTests`)

**Interfaces:**
- Consumes: `sync.connect_business`, `sync.ConnectError` (Task 1).
- Produces:
  - URL names `admin:ad_channels_metabusinessmanager_connect` and `admin:ad_channels_metabusinessmanager_connect_callback`.
  - `MetaBusinessManagerAdmin.login_configured() -> bool`.
  - Session key `"meta_connect"` = `{"state": str, "bm": int | None, "lang": str}`.

Behavior:
- **`connect_view(request)`:**
  1. Read `?bm=<pk>`. With it, check `has_change_permission(request, bm)`. Without it, check `has_add_permission`. Raise `PermissionDenied` otherwise.
  2. If not `login_configured()`, show an error message and redirect to the changelist.
  3. Set `state = secrets.token_urlsafe(32)` and store the session key.
  4. Redirect to the dialog.
- **`callback_view(request)`:**
  1. Pop the session key.
  2. Refuse with `PermissionDenied` when it is missing, or when `secrets.compare_digest` fails against `GET["state"]`.
  3. Re-check the same permission.
  4. If `GET["error"]` is present, show the message "Facebook login was cancelled." and redirect.
  5. Otherwise call `connect_business(GET["code"], redirect_uri, expected=bm)`.
  6. On success, show "Connected %(bm)s." and redirect to the change page.
  7. On `ConnectError`, show an error message and redirect to the changelist (or the change page for a reconnect).
  8. Every redirect uses the stored `lang` (`translation.override(lang)` around `reverse`).
- **`redirect_uri`:** `request.build_absolute_uri(reverse(callback))` under `translation.override("en")`.

- [ ] **Step 1: Write the failing tests** (`ConnectViewTests(MetaTestCase)`, signed in as a Platform Admin, `@override_settings(META_APP_ID="1", META_LOGIN_CONFIG_ID="cfg-9")`):

```python
def test_connect_redirects_to_dialog_with_state(self):
    r = self.client.get(BM_URL + "connect/")
    url = urlparse(r["Location"]); q = parse_qs(url.query)
    self.assertEqual(url.netloc + url.path, "www.facebook.com/v25.0/dialog/oauth")
    self.assertEqual(q["config_id"], ["cfg-9"]); self.assertEqual(q["response_type"], ["code"])
    self.assertEqual(q["override_default_response_type"], ["true"])
    self.assertEqual(q["state"], [self.client.session["meta_connect"]["state"]])

def test_redirect_uri_is_language_independent(self):
    en = parse_qs(urlparse(self.client.get(BM_URL + "connect/")["Location"]).query)["redirect_uri"]
    ar = parse_qs(urlparse(self.client.get(BM_URL.replace("/en/", "/ar/") + "connect/")["Location"]).query)["redirect_uri"]
    self.assertEqual(en, ar); self.assertIn("/en/admin/", en[0])

def test_callback_creates_and_opens_change_page(self): ...  # 302 to change page; BM "111" exists; message "Connected"
def test_wrong_or_missing_state_refused_without_calling_meta(self): ...  # 403; self.meta.exchanged == []
def test_state_is_single_use(self): ...  # second identical callback → 403
def test_cancelled_login_saves_nothing(self): ...  # ?error=access_denied → message, count 0
def test_connect_error_shown_and_nothing_saved(self): ...  # self.meta.check_error = REJECTED
def test_reconnect_needs_change_permission_and_same_business(self): ...  # expected passed through; other business → error, token unchanged
def test_media_buying_lead_refused(self): ...  # connect/ and callback → 403
def test_not_configured_shows_error(self): ...  # override META_LOGIN_CONFIG_ID=None → redirect to changelist with error
def test_no_secret_in_any_response(self): ...  # follow=True on callback; assert TOKEN, "the-code", META_APP_SECRET not in content or messages
```

- [ ] **Step 2: Run** `.venv/bin/python manage.py test ad_channels.tests.ConnectViewTests`. Expected: FAIL (404 on `connect/`).
- [ ] **Step 3: Implement** the views, settings and `.env.example` changes as above. Wrap both views with `self.admin_site.admin_view`.
- [ ] **Step 4: Run the same command.** Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(ad_channels): Facebook Login for Business connect and callback views (US-META-01)`.

---

### Task 3: Admin pages without a typed token

**Files:**
- Modify: `ad_channels/admin.py` (`MetaBusinessManagerForm`, `get_fieldsets`, `get_readonly_fields`, changelist and change-form buttons)
- Create: `ad_channels/templates/admin/ad_channels/metabusinessmanager/change_list_object_tools.html` and `submit_line.html` (or Unfold's `change_form_before_template` equivalent). Each extends the Unfold template and adds the button only when its context flag is true.
- Modify: `leadshub/settings.py`: add `ad_channels/templates` to `TEMPLATES[1]["DIRS"]` (the Django engine) if app templates aren't already found there.
- Modify: `locale/ar/LC_MESSAGES/django.po`
- Test: `ad_channels/tests.py`: replace `AddBusinessManagerTests` and `ReplaceTokenTests` with `BusinessManagerPageTests`

**Interfaces:**
- Consumes: the URL names and `login_configured()` from Task 2.
- Produces: context flags `show_connect` (changelist) and `show_reconnect` (change page).

Behavior:
- **Form fields:** remove the `token` field from `MetaBusinessManagerForm`, along with its `clean()` token check and its `save()` token handling. The form edits `name` only.
- **Business ID:** `meta_business_id` is read-only on the change page.
- **Add page:** `has_add_permission` still governs it, but `add_view` redirects to `connect/`. A Business Manager can't be created without a token.
- **Changelist:** the "Add" button is replaced by "Connect with Facebook". It shows when `has_add_permission` and `login_configured()` are both true.
- **Change page:** shows `masked_token`, token status and "Reconnect with Facebook" (`connect/?bm=<pk>`). The button shows when `has_change_permission(obj)` and `login_configured()` are both true.
- **Unchanged:** Sync, Save and sync, Recheck, Deactivate and Reactivate.

- [ ] **Step 1: Write the failing tests:**
  - `test_add_url_redirects_to_connect`
  - `test_changelist_shows_connect_button_for_platform_admin`
  - `test_change_page_shows_reconnect_and_no_token_input` (assert `name="token"` is not in content and `connect/?bm=` is in content)
  - `test_business_id_is_read_only` (POST a new `meta_business_id` and check it is unchanged)
  - `test_name_edit_saves_without_calling_meta` (`self.meta.tokens == []`)
  - `test_media_buying_lead_sees_no_buttons`
  - `test_buttons_hidden_when_not_configured`
  - Keep and adapt `AccessTests` and `ListViewTests`, so `make_bm` replaces every POST that created a Business Manager with a token.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test ad_channels`. Expected: the new tests FAIL. Old tests that post a `token` are removed in this step.
- [ ] **Step 3: Implement** the changes above, then add the Arabic text and run `make messages`.
- [ ] **Step 4: Run** `.venv/bin/python manage.py test ad_channels`. Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(ad_channels): Business Managers are added only through Facebook Login (US-META-01)`.

---

### Task 4: Whole-suite check and browser check

- [ ] **Step 1: Run** `make test`. Expected: 0 failures.
- [ ] **Step 2: Run** `make check`. Expected: only the known `djust.C401` warning.
- [ ] **Step 3: Browser check** with `make docker-dev-up`, signed in as `platform`, at `/en/admin/ad_channels/metabusinessmanager/`. Also open `/ar/` to check RTL and the dark theme.
  - Without `META_LOGIN_CONFIG_ID`: the Connect button is hidden.
  - With it set: Connect lands on facebook.com with `config_id` in the URL.
  - A real login is the project owner's step, because it needs the Meta setup from the spec.
- [ ] **Step 4: Commit** any fixes, with the message `test(ad_channels): ...`.
