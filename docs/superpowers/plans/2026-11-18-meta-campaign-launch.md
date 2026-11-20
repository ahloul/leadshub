# Launch Ready Campaigns on Meta: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A back-office page where Media Buyers and their lead list ready
campaigns and create, view, change and stop their Meta campaigns through the
Marketing API (US-META-02).

**Architecture:** A `MetaLaunch` row per LeadsHub campaign in `ad_channels/`
saves each Meta ID as soon as Meta returns it. `ad_channels/services.py` runs
the build synchronously during the click (through djust `start_async()`), so
a failed build resumes from its last saved step. Write calls live on the
existing `MetaClient`. Reads of campaigns and review tasks go through
`reviews.selectors` and `campaigns.services`.

**Tech Stack:** Django 5.2, djust 1.2.8 LiveViews, `facebook-business` SDK,
PostgreSQL, Tailwind v4.

**Spec:** `docs/superpowers/specs/2026-11-18-meta-campaign-launch-design.md`

## Global Constraints

- Capability: `accounts.launch_meta_campaigns`, label `_("Launch and manage campaigns on Meta")`. Given to Media Buyer; Media Buying Lead gets it via `ROLE_INCLUDES`.
- Pages need `["accounts.access_back_office", "accounts.launch_meta_campaigns"]`. Every changing handler: `@live.permission_required("accounts.launch_meta_campaigns")` above `@live.event_handler()`.
- "Ready" = `reviews.selectors.awaiting_deployment()` with `campaign.channel == "meta"`.
- Meta campaign: `objective="OUTCOME_LEADS"`, `special_ad_categories=["HOUSING"]`, `special_ad_category_country=["EG"]`, created `PAUSED`, name = `campaign.code`.
- Ad set: name = `campaign.code`, `optimization_goal="LEAD_GENERATION"`, `billing_event="IMPRESSIONS"`, `destination_type="ON_AD"`, `promoted_object={"page_id": page_id}`, created `PAUSED`.
- Targeting: `{"geo_locations": {"countries": ["EG"]}}` plus matched interests. Never `age_min`, `age_max`, `genders`, `zips` (FR-5.14).
- Budgets: USD in cents, `ROUND_HALF_UP`. Ad accounts whose currency isn't `USD` are refused.
- Creative types: `image` and `reel` only; `carousel` and `landing_page` are refused with a message.
- Reel processing wait: at most 60 seconds.
- A `building` launch older than 5 minutes counts as crashed and may be resumed.
- Meta errors are stored and shown only after `client.scrub()` (already done by `MetaError`).
- Every string in English and Arabic; logical utilities (`ms-`/`me-`), `dark:` on every color, `dir="ltr"` on IDs and money.
- Schema: fold into `ad_channels/migrations/0001_initial.py` (early-dev rule). Capability: new `accounts` migration from `makemigrations accounts`.
- Tests never call Meta. Cite `US-META-02` and FR IDs in code, tests and commits.

## Review Focus

1. A build that crashed mid-way leaves `status="building"` forever: after 5 minutes Build must be allowed again (Task 4 test `test_stale_building_can_resume`).
2. A creative's file is missing from storage at build time: fail resumably naming the creative, not a 500 (Task 4 `test_missing_creative_file_fails_resumably`).
3. None of the preset's interests match on Meta: still build with Egypt only and list the skipped names (Task 4 `test_no_interest_matches_builds_country_only`).
4. A budget like `30.005` USD: sent as `3001` cents, never a float (Task 4 `test_budget_cents_round_half_up`).
5. The page is open in Arabic: refusals and labels are Arabic, Meta's own message stays as Meta wrote it (Task 7 `test_arabic_page_shows_arabic_refusal`).

---

### Task 1: Capability `launch_meta_campaigns`

**Files:**
- Modify: `accounts/roles.py` (`CAPABILITIES`, `AUDIENCE_PERMISSIONS[Audience.INTERNAL]`, `ROLE_PERMISSIONS[Role.MEDIA_BUYER]`)
- Create: `accounts/migrations/0009_launch_meta_campaigns.py` (`makemigrations accounts`, then add a `RunPython` grant to the Media Buyer and Media Buying Lead groups, same pattern as `ad_channels/migrations/0002_grant_role_permissions.py`)
- Modify: `locale/ar/LC_MESSAGES/django.po`
- Test: `accounts/tests.py`

**Interfaces:**
- Produces: permission string `"accounts.launch_meta_campaigns"`.

- [ ] **Step 1: Write the failing test** in `RoleMatrixTests`:

```python
def test_media_buyers_and_their_lead_launch_on_meta(self):
    for role in (Role.MEDIA_BUYER, Role.MEDIA_BUYING_LEAD):
        self.assertIn("accounts.launch_meta_campaigns", self.perms(role))
    for role in (Role.CONTENT_MANAGER, Role.FINANCE_MANAGER, Role.BROKER_OWNER, Role.DEVELOPER_VIEW):
        self.assertNotIn("accounts.launch_meta_campaigns", self.perms(role))
```

- [ ] **Step 2: Run** `.venv/bin/python manage.py test accounts.tests.RoleMatrixTests`. Expected: FAIL.
- [ ] **Step 3: Implement** the capability, the grants and the migration. Arabic label: `"إطلاق الحملات على ميتا وإدارتها"`. Run `make messages`.
- [ ] **Step 4: Run** `.venv/bin/python manage.py test accounts`. Expected: PASS (including the existing audience test).
- [ ] **Step 5: Commit** `feat(accounts): launch_meta_campaigns capability for media buyers (US-META-02)`

---

### Task 2: `MetaLaunch` model and the cross-app hooks

**Files:**
- Modify: `ad_channels/models.py`, `ad_channels/migrations/0001_initial.py`
- Modify: `reviews/selectors.py`, `reviews/services.py`, `campaigns/services.py`
- Test: `ad_channels/tests.py`, `reviews/tests.py`

**Interfaces:**
- Produces:
  - `MetaLaunch` with fields from spec §5.2. `status` is `MetaLaunch.Status` TextChoices: `BUILDING="building"`, `BUILD_FAILED="build_failed"`, `ACTIVE="active"`, `PAUSED="paused"`, `ARCHIVED="archived"`. `campaign = OneToOneField("campaigns.Campaign", PROTECT, related_name="meta_launch")`. `ad_account = ForeignKey(MetaAdAccount, PROTECT, related_name="launches")`. `ads = JSONField(default=list)`. `budget_kind` (copy of `campaigns.BudgetKind` values), `budget_usd` Decimal(10,2), `ends_on` Date null. `skipped_interests = JSONField(default=list)`. `Meta.ordering = ["-launched_at"]`.
  - `MetaLaunch.is_stale_build(now=None) -> bool`: `status == BUILDING` and `updated_at` older than 5 minutes.
  - `reviews.selectors.ready_for_meta() -> QuerySet[ReviewTask]`: `awaiting_deployment()` filtered to `campaign__channel="meta"` and `campaign__meta_launch__isnull=True` or `campaign__meta_launch__status__in=["building","build_failed"]`.
  - `reviews.selectors.ready_task_for(campaign_id) -> ReviewTask | None`.
  - `reviews.services.record_deployment(*, task_id: int, actor, now=None) -> ReviewTask`: sets `deployed_by`/`deployed_at`; raises `ValidationError` if not `awaiting_deployment`. No decider check.
  - `campaigns.services.end_campaign(campaign_id: int) -> Campaign`: locks and calls `campaign.end()`.

- [ ] **Step 1: Write the failing tests**
  - `ad_channels.tests.MetaLaunchModelTests.test_one_launch_per_campaign` (second create raises `IntegrityError`).
  - `test_ad_account_in_use_cannot_be_deleted` (raises `ProtectedError`).
  - `test_stale_build_after_five_minutes` (4 min → False, 6 min → True).
  - `reviews.tests.ReadyForMetaTests.test_lists_approved_meta_campaigns_only` (an approved Google campaign, an open task and a deployed task are absent).
  - `test_record_deployment_needs_an_approved_task` and `test_record_deployment_ignores_assignment` (buyer2 records a task assigned to buyer).
- [ ] **Step 2: Run** `.venv/bin/python manage.py test ad_channels reviews`. Expected: FAIL.
- [ ] **Step 3: Implement.** Add the model to `0001_initial.py` by hand, with `("campaigns", "<latest>")` in its dependencies. Then `makemigrations --check` must say "No changes detected". Delete the dev DB's `django_migrations` row for `ad_channels` and re-migrate, or reset the dev DB.
- [ ] **Step 4: Run** the same tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ad_channels): MetaLaunch record per launched campaign (US-META-02, FR-7.6)`

---

### Task 3: `MetaClient` write and read calls

**Files:**
- Modify: `ad_channels/meta_ads/client.py`
- Create: `ad_channels/test_utils.py` (`FakeMetaLauncher`)
- Test: `ad_channels/tests.py` (`MetaClientWriteTests`, SDK calls mocked with `mock.patch.object`)

**Interfaces:**
- Produces, on `MetaClient` (all wrap `self._errors()`, all return plain values):
  - `list_pages(business_id: str) -> list[PageInfo]`, where `PageInfo(id: str, name: str, instagram_actor_id: str)` (empty string if none). Owned pages, then client pages, deduplicated.
  - `list_lead_forms(page_id: str) -> list[LeadFormInfo]`, where `LeadFormInfo(id: str, name: str)`. Active forms only. Read with the page's access token obtained from `/{page_id}?fields=access_token`.
  - `ad_account_currency(account_id: str) -> str`
  - `find_by_name(account_id: str, edge: str, name: str) -> str | None`, where `edge` is `"campaigns"` or `"adsets"`.
  - `create_campaign(account_id: str, name: str) -> str`
  - `create_adset(account_id: str, *, name, campaign_id, page_id, budget_kind: str, budget_cents: int, start: datetime, end: datetime | None, targeting: dict) -> str`
  - `search_interest(name: str) -> str | None` (first exact case-insensitive match).
  - `upload_image(account_id: str, filename: str, data: bytes) -> str` (image hash).
  - `upload_video(account_id: str, filename: str, data: bytes) -> str` (video ID).
  - `video_ready(video_id: str) -> bool`
  - `create_ad_creative(account_id: str, *, name, page_id, instagram_actor_id, lead_form_id, message: str, image_hash: str = "", video_id: str = "") -> str`
  - `create_ad(account_id: str, *, name, adset_id, creative_id) -> str`
  - `set_status(object_id: str, status: str) -> None` (`"ACTIVE"`, `"PAUSED"`, `"ARCHIVED"`).
  - `update_adset(adset_id: str, *, budget_kind: str, budget_cents: int, end: datetime | None) -> None`
  - `delete_object(object_id: str) -> None`
  - `read_launch(campaign_id: str, adset_id: str) -> LaunchState`, where `LaunchState(campaign_status: str, adset_status: str, budget_cents: int, end: datetime | None)`.
- `FakeMetaLauncher` implements the same methods in memory: records `calls` as `(method, kwargs)`, returns IDs `"c1"`, `"s1"`, `"img1"`, `"v1"`, `"cr1"`, `"ad1"`…, and has `fail_on: dict[str, MetaError]` and `currency = "USD"`.

- [ ] **Step 1: Write the failing tests**
  - `test_create_adset_never_sends_age_or_gender`: the dict passed to the SDK has no `age_min`, `age_max`, `genders`, `zips` in `targeting`.
  - `test_create_campaign_sends_housing_and_egypt_paused`.
  - `test_errors_come_out_as_meta_error_without_the_token`.
  - `test_list_pages_merges_owned_and_client_pages`.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test ad_channels.tests.MetaClientWriteTests`. Expected: FAIL.
- [ ] **Step 3: Implement** with `facebook_business.adobjects` (`AdAccount.create_campaign`, `create_ad_set`, `create_ad_image`, `create_ad_video`, `create_ad_creative`, `create_ad`; `Page`, `AdVideo`, `TargetingSearch`). The lead form CTA is `{"type": "SIGN_UP", "value": {"lead_gen_form_id": ...}}`.
- [ ] **Step 4: Run** the same tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ad_channels): Meta write calls for campaigns, ad sets and ads (US-META-02)`

---

### Task 4: Build service (create) with resume and discard

**Files:**
- Create: `ad_channels/services.py`
- Test: `ad_channels/tests.py`, class `BuildTests` (setup reuses the `reviews.tests.ReviewSetup` pattern: an approved Meta campaign; patch `ad_channels.meta_ads.sync.client_for` with `FakeMetaLauncher`)

**Interfaces:**
- Consumes: Task 2 selectors/services, Task 3 client methods, `MetaAdAccount.objects.available_for_launch()`.
- Produces:
  - `LAUNCH = "accounts.launch_meta_campaigns"`
  - `class LaunchError(Exception)`: message safe to show.
  - `build_options(*, actor, campaign_id: int, ad_account_id: int | None = None, page_id: str = "") -> BuildOptions`, where `BuildOptions(accounts: list[MetaAdAccount], pages: list[PageInfo], forms: list[LeadFormInfo], problems: list[str])`. Pages load once an account is chosen, forms once a page is chosen.
  - `build(*, actor, campaign_id: int, ad_account_id: int, page_id: str, lead_form_id: str, now=None) -> MetaLaunch`
  - `discard_build(*, actor, campaign_id: int) -> None`
- The client comes from `sync.client_for(bm.get_token())` so tests patch one seam.

`build` order (each step saves its ID and `updated_at` in its own short transaction, then continues):
1. Check, inside `select_for_update` on the `MetaLaunch` (created if missing): actor has `LAUNCH` (else `PermissionDenied`); `ready_task_for(campaign_id)` exists; campaign channel is Meta; no other live build (`BUILDING` and not stale); the account is in `available_for_launch()`; currency is `USD`; every creative is `usable()` and its type is `image` or `reel`; `ends_on` is after today; lifetime budget has `ends_on`. Set `status=BUILDING`.
2. Campaign (reuse `find_by_name` result if any) → 3. Ad set (same) → 4. Per creative: upload, wait for reel ≤ 60 s, ad creative, ad → 5. `set_status` ACTIVE on ads, ad set, campaign → 6. `campaigns.services.mark_live(campaign_id, adset_id=..., ad_account_id=account.meta_account_id)` and `reviews.services.record_deployment(...)`; `status=ACTIVE`.
On `MetaError`: save `last_error`, `status=BUILD_FAILED`; if `token_rejected`, set the Business Manager's `token_status=INVALID` (as `sync` does); raise `LaunchError`.

- [ ] **Step 1: Write the failing tests** (one per rule; names state the rule)
  - `test_build_creates_everything_paused_then_activates` (call order in `fake.calls`; campaign Live; `external_id == "s1"`; task deployed).
  - `test_role_without_capability_is_refused` (Content Manager → `PermissionDenied`).
  - `test_campaign_no_longer_ready_is_refused` (task rejected after list).
  - `test_google_campaign_is_refused`
  - `test_second_build_while_building_is_refused`
  - `test_stale_building_can_resume` (Review Focus 1)
  - `test_account_no_longer_available_is_refused`
  - `test_non_usd_account_is_refused` (`fake.currency = "EGP"`)
  - `test_expired_creative_is_refused_by_name`
  - `test_carousel_creative_is_refused`
  - `test_past_start_starts_now` and `test_end_date_passed_is_refused` and `test_lifetime_budget_without_end_is_refused`
  - `test_failure_mid_build_resumes_without_duplicates` (`fail_on["create_ad"]`, then build again: one `create_campaign` call in total)
  - `test_lost_response_reuses_existing_campaign_by_name`
  - `test_token_rejected_marks_business_manager_invalid`
  - `test_reel_still_processing_fails_resumably` (`video_ready` always False; patch the sleep)
  - `test_missing_creative_file_fails_resumably` (Review Focus 2)
  - `test_no_interest_matches_builds_country_only` (Review Focus 3; `skipped_interests == ["Real estate", "Compounds"]`)
  - `test_budget_cents_round_half_up` (Review Focus 4; `Decimal("30.005")` → `3001`)
  - `test_discard_deletes_on_meta_and_returns_campaign_to_ready`
  - `test_discard_failure_keeps_the_record_with_the_error`
  - `test_build_options_lists_only_available_accounts` and `test_page_without_forms_is_a_problem`
- [ ] **Step 2: Run** `.venv/bin/python manage.py test ad_channels.tests.BuildTests`. Expected: FAIL.
- [ ] **Step 3: Implement** `build_options`, `build`, `discard_build`. Helper `budget_cents(amount: Decimal) -> int` uses `quantize(Decimal("0.01"), ROUND_HALF_UP) * 100`.
- [ ] **Step 4: Run** the same tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ad_channels): build ready campaigns on Meta with resume (US-META-02, FR-6.10, FR-5.14)`

---

### Task 5: Update and delete services

**Files:**
- Modify: `ad_channels/services.py`
- Test: `ad_channels/tests.py` (`ManageLaunchTests`)

**Interfaces:**
- Produces:
  - `pause(*, actor, campaign_id: int) -> MetaLaunch` / `resume(*, actor, campaign_id: int) -> MetaLaunch`: `set_status` on the Meta campaign; LeadsHub status unchanged.
  - `change_budget(*, actor, campaign_id: int, budget_usd: Decimal, ends_on: date | None) -> MetaLaunch`
  - `stop(*, actor, campaign_id: int) -> MetaLaunch`: ARCHIVED on Meta, `status=ARCHIVED`, `campaigns.services.end_campaign()`. Keeps `meta_adset_id`.
  - `refresh(*, actor, campaign_id: int, now=None) -> MetaLaunch`: `read_launch()`; saves the Meta status, budget and end; sets `meta_status_checked_at`.

- [ ] **Step 1: Write the failing tests**
  - `test_pause_and_resume_keep_the_campaign_live`
  - `test_budget_above_broker_figure_is_refused`
  - `test_budget_change_sent_in_cents_with_end`
  - `test_stop_archives_and_ends_but_keeps_the_ad_set_id`
  - `test_stop_twice_is_a_no_op`
  - `test_refresh_shows_changes_made_in_ads_manager`
  - `test_each_action_refuses_a_role_without_the_capability` (subTest over the five functions)
  - `test_actions_on_a_campaign_not_launched_raise_launch_error`
- [ ] **Step 2: Run** `.venv/bin/python manage.py test ad_channels.tests.ManageLaunchTests`. Expected: FAIL.
- [ ] **Step 3: Implement** the five functions.
- [ ] **Step 4: Run** the same tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ad_channels): pause, budget, stop and refresh Meta launches (US-META-02)`

---

### Task 6: List page `/back-office/meta/campaigns/`

**Files:**
- Create: `ad_channels/selectors.py`, `ad_channels/views.py` (`MetaCampaignListView`), `ad_channels/templates/ad_channels/list.html`
- Modify: `leadshub/urls.py` (name `meta_campaigns`), `leadshub/navigation.py` (`_page(_("Meta campaigns"), "rocket_launch", "meta_campaigns", BACK_OFFICE, "accounts.launch_meta_campaigns")` after Review queue), `leadshub/settings.py` (`TEMPLATES[0]["DIRS"]` += `BASE_DIR / "ad_channels" / "templates"`; `LIVEVIEW_ALLOWED_MODULES` += `"ad_channels.views"`)
- Test: `leadshub/tests.py` (`PAGE_AUDIENCES`), `ad_channels/tests.py` (`ListPageTests`)

**Interfaces:**
- Produces:
  - `selectors.ready(page: int = 1) -> Page[ReviewTask]` (25 per page) and `selectors.launched(status: str = "", page: int = 1) -> Page[MetaLaunch]`.
  - URL params: `?tab=ready|launched`, `?status=<MetaLaunch.Status>`, `?page=N`. Clearing filters calls a handler that runs `self.live_patch(params=...)`.
  - Each row links with `dj-navigate` to `meta_campaign` (Task 7).

- [ ] **Step 1: Write the failing tests**
  - `PAGE_AUDIENCES["/en/back-office/meta/campaigns/"] = {Role.MEDIA_BUYING_LEAD, Role.MEDIA_BUYER}` (Platform Admin doesn't get the capability by default).
  - `ListPageTests.test_ready_tab_lists_ready_campaigns_with_office_and_budget`
  - `test_launched_tab_filters_by_status`
  - `test_no_token_or_business_manager_id_in_page`
  - `@performance_test(max_queries=12)` on the ready tab with 30 rows.
- [ ] **Step 2: Run** `.venv/bin/python manage.py test leadshub.tests ad_channels.tests.ListPageTests`. Expected: FAIL.
- [ ] **Step 3: Implement** the view with `LoginRequiredLiveViewMixin, PermissionRequiredMixin, PageLanguageMixin, LiveView`; cards stack on phones, table from `md:`.
- [ ] **Step 4: Run** the same tests and `make check`. Expected: PASS, only the known `djust.C401` warning.
- [ ] **Step 5: Commit** `feat(ad_channels): Meta campaigns list for media buyers (US-META-02)`

---

### Task 7: Detail page `/back-office/meta/campaigns/<campaign_id>/`

**Files:**
- Modify: `ad_channels/views.py` (`MetaCampaignView`), `leadshub/urls.py` (name `meta_campaign`)
- Create: `ad_channels/templates/ad_channels/campaign.html`
- Test: `ad_channels/tests.py` (`DetailPageTests`, `MetaPagesOverTheSocketTests` as a `TransactionTestCase` following `reviews.tests.ReviewPagesOverTheSocketTests`)

**Interfaces:**
- Consumes: Task 4 and 5 services, `build_options()`.
- Produces handlers (each with `@live.permission_required(LAUNCH)` above `@live.event_handler()`):
  - `choose_account(value: str)`, `choose_page(value: str)`, `choose_form(value: str)`: reload `build_options()`.
  - `build_on_meta()`: runs `services.build` via `self.start_async()`; sets `self.building = True` for the "Building on Meta…" state.
  - `discard()`, `pause()`, `resume()`, `save_budget(budget_usd: str, ends_on: str)`, `stop()`, `refresh()`.
  - Errors from `LaunchError`/`ValidationError` go to `self.error`; `PermissionDenied` propagates.
- The page shows the campaign's office, project, creatives, audience preset text, broker budget; then the build form, or the launch details (Meta IDs `dir="ltr"`, status, skipped interests, last error).

- [ ] **Step 1: Write the failing tests**
  - `test_build_from_the_page_makes_the_campaign_live` (`send_event("choose_account", …)` … `send_event("build_on_meta")`)
  - `test_each_handler_refuses_a_role_without_the_capability` (subTest over every handler; user `content`)
  - `test_unknown_or_unready_campaign_is_404`
  - `test_failed_build_shows_the_error_and_a_build_again_button`
  - `test_arabic_page_shows_arabic_refusal` (Review Focus 5)
  - `MetaPagesOverTheSocketTests.test_capability_removed_mid_session_refuses_next_event`
- [ ] **Step 2: Run** `.venv/bin/python manage.py test ad_channels.tests.DetailPageTests ad_channels.tests.MetaPagesOverTheSocketTests`. Expected: FAIL.
- [ ] **Step 3: Implement** the view and template. Buttons show only with the capability; Stop asks for confirmation inline (no browser `confirm()`).
- [ ] **Step 4: Run** the same tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ad_channels): build, pause, budget and stop from the Meta campaign page (US-META-02)`

---

### Task 8: Translations, docs and the full check

**Files:**
- Modify: `locale/ar/LC_MESSAGES/django.po`, `docs/ROADMAP.md` (M4 "Not built yet" and §1 "Built"), `docs/INTEGRATIONS.md` ("What exists today": the Meta launch page), `CLAUDE.md` (one bullet under `ad_channels` in Architecture)

- [ ] **Step 1:** `make messages`, fill every empty Arabic `msgstr`, run `make messages` again. Expected: no empty `msgstr` for the new strings (`grep -c 'msgstr ""' ` unchanged except headers).
- [ ] **Step 2:** Update the three docs in the project's plain style; release note: Media Buyers and their lead can now launch ready campaigns on Meta; databases that existed before need `migrate` (accounts) and a reset for `ad_channels` (folded migration).
- [ ] **Step 3:** `make test`. Expected: all pass.
- [ ] **Step 4:** `make check`. Expected: only `djust.C401`.
- [ ] **Step 5:** Browser check at 375 px and 1280 px, English and Arabic, light and dark, as `buyer` from `seed_demo`: list, detail and the build form. A real build needs a Meta test ad account; if none is configured, say so in the hand-off instead of claiming it works.
- [ ] **Step 6: Commit** `docs: Meta launch page in roadmap and integrations; Arabic labels (US-META-02)`
