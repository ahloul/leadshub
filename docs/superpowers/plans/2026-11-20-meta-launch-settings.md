# Meta Launch Settings and EGP Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before a Meta build, the media buyer adjusts budget, dates, interests and creatives inside the broker's approval. The values are saved on `MetaLaunch` and EGP ad accounts are converted at a locked rate.

**Architecture:** A `LaunchSettings` value is validated against the broker's `Campaign`, saved on `MetaLaunch` when the build claims it, and from then on the build, budget change and refresh read only the launch. One function converts USD to the account's minor units with the launch's locked `fx_rate`.

**Tech Stack:** Django 5.2, djust 1.2.8 LiveViews, facebook_business SDK (behind `FakeMetaLauncher` in tests), PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-11-20-meta-launch-settings-design.md` (builds on `2026-11-18-meta-campaign-launch-design.md`)

## Global Constraints

- Never write to the broker's `Campaign` or `Brief` from `ad_channels` (SRS §2.2, FR-6.1).
- Budget input is USD. The limits are `1 ≤ budget_usd ≤ campaign.budget_usd`, and the budget type is always `campaign.budget_kind`.
- Dates must be inside the broker's window: `starts_on ≥ campaign.starts_on`. If the broker set `ends_on`, the launch's `ends_on ≤ campaign.ends_on` and can't be empty. A total budget needs an `ends_on`. `starts_on ≤ ends_on`.
- Never age or gender targeting (FR-5.14); interests only.
- Supported currencies are `USD` (rate `1`) and `EGP` (rate from `settings.META_EGP_PER_USD`). Anything else is refused.
- The rate is locked on the launch at its first claim. Later calls use `launch.fx_rate`, never the setting.
- Minor units are `round_half_up(budget_usd × fx_rate × 100)`.
- One new migration: `ad_channels/0004_launch_settings.py`. Never edit 0001–0003.
- Commit nothing until the user says so (standing instruction). Each task ends at a green test run, not a commit.
- No `Co-Authored-By` lines in any commit.
- Every new user-facing string goes through `gettext`/`{% translate %}` and gets an Arabic translation.
- Run commands through `docker compose -f docker-compose-dev.yml --profile test run --rm -T test python manage.py <cmd>`, written below as `$DM <cmd>`.

## Review Focus

1. **Build again after the ad set exists.** If the media buyer then changes settings, the change must be refused with a clear message, not silently ignored: `create_adset` is skipped on resume.
2. **The rate setting changes after a launch exists.** Budget change and refresh still use the launch's `fx_rate`.
3. **Junk input on the form:** `abc`, `NaN`, `-5`, `1e9`, an empty budget, a bad date, an unknown or duplicate creative id. Each gives a page error, never a crash.
4. **EGP rounding:** $30 at 50.55 is EGP 1,516.50, so the build sends `151650`. A refresh that reads `151651` back must give USD rounded to cents.
5. **Launches made before this change:** `starts_on` is null and `creative_ids` is empty. Resume, budget change and refresh must still work, falling back to the campaign's values.

---

### Task 1: Launch fields, rate setting and money conversion

**Files:**
- Modify: `ad_channels/models.py` (the `MetaLaunch` fields)
- Create: `ad_channels/migrations/0004_launch_settings.py`, via `$DM makemigrations ad_channels --name launch_settings`
- Modify: `leadshub/settings.py`, next to the `META_*` settings (around line 305)
- Modify: `.env.example`
- Modify: `ad_channels/services.py`. Replace `budget_cents` (line 71) with `meta_minor_units` and add `_account_rate`.
- Test: `ad_channels/tests_launch.py`, new class `MoneyTests(SimpleTestCase)`

**Interfaces:**
- Produces these `MetaLaunch` fields:
  - `starts_on = DateField(null=True, blank=True)`
  - `interests = JSONField(default=list, blank=True)`
  - `creative_ids = JSONField(default=list, blank=True)`
  - `currency = CharField(max_length=3, default="USD")`
  - `fx_rate = DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"))`
  
  Each has a translated `verbose_name`.
- Produces `settings.META_EGP_PER_USD: Decimal | None`, parsed from env `META_EGP_PER_USD`; empty or missing means `None`. Its comment must say: "Stand-in until M6's Finance-kept daily rate (SRS §2.1, §4.4; ROADMAP Q1–Q4). Broker budgets are USD; EGP ad accounts are charged budget × this rate."
- Produces `services.meta_minor_units(budget_usd: Decimal, fx_rate: Decimal) -> int`.
- Produces `services._account_rate(currency: str) -> Decimal`. It raises `LaunchError` for an unsupported currency or when EGP has no rate set.

- [ ] **Step 1: Write the failing tests**

```python
class MoneyTests(SimpleTestCase):
    def test_usd_is_cents(self):
        self.assertEqual(services.meta_minor_units(Decimal("30"), Decimal("1")), 3000)

    def test_egp_is_piastres_at_the_rate_rounded_half_up(self):
        self.assertEqual(services.meta_minor_units(Decimal("30"), Decimal("50.55")), 151650)
        self.assertEqual(services.meta_minor_units(Decimal("0.01"), Decimal("50.5")), 51)  # 50.5 → 51

    @override_settings(META_EGP_PER_USD=Decimal("50"))
    def test_rates(self):
        self.assertEqual(services._account_rate("USD"), Decimal("1"))
        self.assertEqual(services._account_rate("EGP"), Decimal("50"))

    @override_settings(META_EGP_PER_USD=None)
    def test_egp_without_a_rate_is_refused(self):
        with self.assertRaisesMessage(services.LaunchError, "META_EGP_PER_USD"):
            services._account_rate("EGP")

    def test_other_currencies_are_refused(self):
        with self.assertRaisesMessage(services.LaunchError, "EUR"):
            services._account_rate("EUR")
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `$DM test ad_channels.tests_launch.MoneyTests`
Expected: FAIL with `AttributeError: ... 'meta_minor_units'`.

- [ ] **Step 3: Implement**

1. Add the model fields and the migration.
2. Add the setting and the comment.
3. Add `META_EGP_PER_USD=` to `.env.example`, with the same comment.
4. Implement the two functions. The EGP-without-rate message names `META_EGP_PER_USD`. The other-currency message names the currency and says only USD and EGP accounts launch.
5. Update the three current `budget_cents(...)` callers in `services.py` to `meta_minor_units(..., launch.fx_rate)`: `_run_build` line 428 and `change_budget` line 560. Update `refresh` line 602 to divide: `(Decimal(state.budget_cents) / 100 / launch.fx_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)`.

- [ ] **Step 4: Run and confirm they pass**

Run: `$DM test ad_channels.tests_launch.MoneyTests && $DM makemigrations --check --dry-run`
Expected: PASS, then "No changes detected".

### Task 2: Build with the media buyer's settings and the account's rate

**Files:**
- Modify: `ad_channels/services.py`. Change `build` (line 340), `_claim` (line 159), `_run_build` (line 418), `_start` and `_end` (around line 226), `_targeting` (line 241) and `_campaign_errors` (line 204). Remove the USD-only check at lines 356–361.
- Test: `ad_channels/tests_launch.py`, new class `LaunchSettingsTests(BuildSetup)`. Update the existing non-USD refusal test in `BuildTests` to expect the new behaviour.

**Interfaces:**
- Consumes these from Task 1: `meta_minor_units`, `_account_rate`, and the `MetaLaunch` fields.
- Produces `@dataclass(frozen=True) class LaunchSettings` with fields `budget_usd: Decimal`, `starts_on: date`, `ends_on: date | None`, `interests: tuple[str, ...]` and `creative_ids: tuple[int, ...]`.
- Produces `default_settings(campaign, launch=None) -> LaunchSettings`. It returns the launch's saved values when present and not null, and otherwise the broker's: `budget_usd`, `starts_on`, `ends_on`, `audience.interests` (empty if no audience), and the creative pks ordered by pk.
- Produces `settings_errors(campaign, settings, today) -> list[str]`, which enforces the Global Constraints rules. Interests are stripped, blanks dropped and duplicates removed case-insensitively. More than 25 interests is an error. Every creative id must belong to the campaign, and the list can't be empty.
- Produces `build(*, actor, campaign_id, ad_account_id, page_id, lead_form_id, settings=None, now=None)`. `None` means `default_settings(campaign, existing_launch)`.
- On a claim, `_claim(..., settings, currency, fx_rate)` writes `budget_usd`, `starts_on`, `ends_on`, `interests`, `creative_ids` and `currency`. It writes `fx_rate` only when the launch is new or has no `meta_adset_id` yet; otherwise it keeps the saved rate. When `launch.meta_adset_id` is set and the settings differ from the saved ones, it raises `LaunchError("The ad set is already on Meta with the saved settings. Discard the build to change them.")`.
- `_run_build`, `_start`, `_end` and `_targeting` take their values from `launch`, falling back to the campaign when `starts_on` is null or `creative_ids`/`interests` are empty (Review Focus 5). Creatives are built in `launch.creative_ids` order.

- [ ] **Step 1: Write the failing tests** (in `LaunchSettingsTests`, one per behaviour)

```python
def settings(self, **changes):
    base = services.default_settings(self.campaign)
    return dataclasses.replace(base, **changes)

def test_defaults_are_the_brokers_values(self): ...  # budget, starts_on, ends_on, interests, creative ids
def test_build_uses_the_edited_budget_dates_interests_and_creatives(self):
    # budget 20 of the broker's 30, a later start, an earlier end, one interest, one of two creatives
    # assert create_adset budget_cents == 2000, start/end match, one interest searched, one ad built
def test_brokers_campaign_is_unchanged(self):  # Campaign row equal before/after (budget_usd, dates, creatives)
def test_budget_above_the_brokers_is_refused(self): ...
def test_budget_below_one_is_refused(self): ...
def test_start_before_the_brokers_start_is_refused(self): ...
def test_last_day_after_the_brokers_last_day_is_refused(self): ...
def test_removing_the_brokers_last_day_is_refused(self): ...
def test_total_budget_needs_a_last_day(self): ...
def test_start_after_last_day_is_refused(self): ...
def test_creative_not_on_the_campaign_is_refused(self): ...
def test_no_creatives_is_refused(self): ...
def test_nothing_is_called_on_meta_when_settings_are_invalid(self):  # self.meta.calls has no create_*
@override_settings(META_EGP_PER_USD=Decimal("50"))
def test_egp_account_gets_piastres_and_locks_the_rate(self):
    self.meta.currency = "EGP"
    launch = self.build(settings=self.settings(budget_usd=Decimal("30")))
    self.assertEqual(self.meta.called("create_adset")[0]["budget_cents"], 150000)
    self.assertEqual((launch.currency, launch.fx_rate), ("EGP", Decimal("50")))
@override_settings(META_EGP_PER_USD=None)
def test_egp_without_rate_is_refused_before_any_create(self): ...
def test_eur_account_is_refused(self): ...
def test_changed_settings_after_the_ad_set_exists_are_refused(self):
    # fail_on create_ad → BUILD_FAILED with meta_adset_id saved; build again with another budget → LaunchError
def test_build_again_with_the_same_settings_resumes(self): ...
def test_launch_from_before_this_change_still_resumes(self):
    # a BUILD_FAILED launch with starts_on=None, creative_ids=[] → build() with settings=None succeeds
```

- [ ] **Step 2: Run and confirm they fail**

Run: `$DM test ad_channels.tests_launch.LaunchSettingsTests`
Expected: FAIL on `AttributeError: ... 'default_settings'`.

- [ ] **Step 3: Implement** the interfaces above.

Validation order in `build` is: `_require`, the ready task, `settings_errors` plus `_campaign_errors` for the selected creatives plus `_destination_errors`, then the account, then the Meta reads (currency → `_account_rate`, page, form), then the claim. That way invalid settings never reach Meta.

- [ ] **Step 4: Run and confirm they pass, plus the existing build tests**

Run: `$DM test ad_channels.tests_launch`
Expected: all OK.

### Task 3: Budget change and refresh with the locked rate

**Files:**
- Modify: `ad_channels/services.py`, `change_budget` and `refresh` (Task 1 already switched the arithmetic; this task proves it).
- Test: `ad_channels/tests_launch.py`, inside `ManageLaunchTests`.

**Interfaces:**
- Consumes these: `MetaLaunch.fx_rate` and `meta_minor_units`.
- Changes `change_budget`'s start-date rule: `ends_on` must also be `≥ launch.starts_on` when that is set.

- [ ] **Step 1: Write the failing tests**

```python
@override_settings(META_EGP_PER_USD=Decimal("50"))
def test_budget_change_on_an_egp_launch_uses_the_locked_rate(self):
    # launch built at 50; then override_settings(META_EGP_PER_USD=Decimal("60")) around change_budget(20)
    # assert update_adset budget_cents == 100000 (20 × 50 × 100)
def test_refresh_converts_egp_back_to_usd(self):
    # launch.fx_rate=50.55; fake state.budget_cents=151651 → launch.budget_usd == Decimal("30.00")
def test_last_day_before_the_launch_start_is_refused(self): ...
```

- [ ] **Step 2: Run and confirm they fail**

Run: `$DM test ad_channels.tests_launch.ManageLaunchTests`
Expected: the start-date test fails. The EGP tests may already pass after Task 1. If they do, that's a finding about Task 1's coverage: record it, then keep them as regression tests.

- [ ] **Step 3: Implement** the `starts_on` rule in `change_budget`.

- [ ] **Step 4: Run and confirm they pass**

Run: `$DM test ad_channels.tests_launch.ManageLaunchTests`
Expected: OK.

### Task 4: The settings form and the launched card

**Files:**
- Modify: `ad_channels/views.py`, `MetaCampaignView`: `mount`, `get_context_data` and `build_on_meta`.
- Modify: `ad_channels/templates/ad_channels/campaign.html`, both the build section (lines 89–133) and the "On Meta" card (lines 39–87).
- Test: `ad_channels/tests_launch.py`, inside `DetailPageTests`.

**Interfaces:**
- Consumes these from Task 2: `default_settings`, `settings_errors` and `LaunchSettings`, plus `build(..., settings=...)`.
- The view keeps the form values as public string state, so they survive re-renders: `budget_usd`, `starts_on`, `ends_on`, `interests` (text, one per line) and `creative_ids` (list of str). They are prefilled in `mount` from `default_settings(campaign, launch)`.
- The handler is `build_on_meta(self, budget_usd="", starts_on="", ends_on="", interests="", creative_ids=None, **kwargs)`. `creative_ids` is a str or a list, because djust may send either for checkboxes. It parses into a `LaunchSettings`. On junk (Review Focus 3) it sets `self.error` to "Check the budget, dates and creatives." and does not start the build. Otherwise it passes the settings to `_build` and on to `services.build`.
- The build section becomes one `<form dj-submit="build_on_meta">`. The account, page and form selects stay `dj-change`. New fields: budget (`inputmode="decimal" dir="ltr"`), start and last day (`type="date" dir="ltr"`), interests `<textarea>`, and a checkbox per campaign creative.
  - Each field shows the broker's value as a hint: "Broker: $30 a day".
  - The layout is mobile first, with `dark:` variants and logical utilities only.
- Context adds `egp_preview`: `budget_usd × fx_rate` when the launch is in EGP. The "On Meta" card shows `$30 a day ≈ EGP 1,500 at 50.00` (numbers in `dir="ltr"`). When a launch value differs from the broker's, it shows a "Broker: …" note.

- [ ] **Step 1: Write the failing tests**

```python
def test_form_is_prefilled_with_the_brokers_values(self): ...   # render contains budget 30, dates, interests
def test_build_event_passes_the_edited_settings(self):
    # send_event("build_on_meta", budget_usd="20", starts_on=..., ends_on=..., interests="Real estate",
    #            creative_ids=[str(pk)]); render_async(); create_adset budget_cents == 2000
def test_junk_budget_shows_an_error_and_builds_nothing(self):  # "abc", "NaN", "-5"
def test_unknown_creative_id_shows_an_error(self): ...
def test_egp_launch_card_shows_the_egp_amount_and_rate(self): ...
def test_changed_values_show_the_brokers_value(self): ...
def test_build_without_the_capability_is_refused(self): ...       # existing pattern: send_event → refused
```

- [ ] **Step 2: Run and confirm they fail**

Run: `$DM test ad_channels.tests_launch.DetailPageTests`
Expected: FAIL. The new fields aren't rendered and the handler takes no settings.

- [ ] **Step 3: Implement** the view and the template.

- [ ] **Step 4: Run and confirm they pass**

Run: `$DM test ad_channels.tests_launch.DetailPageTests ad_channels.tests_launch.MetaPagesOverTheSocketTests`
Expected: OK.

### Task 5: Arabic, docs and the full check

**Files:**
- Modify: `locale/ar/LC_MESSAGES/django.po` (then `.mo`)
- Modify: `docs/INTEGRATIONS.md` (the EGP rate and the launch settings)
- Modify: `docs/ROADMAP.md`. Replace "USD ad accounts only (until FX, M6)" at line 484 with "USD and EGP ad accounts (EGP at the `META_EGP_PER_USD` setting until M6)".
- Modify: `docs/superpowers/specs/2026-11-18-meta-campaign-launch-design.md`. In §8, "USD only" points to the new spec.
- Modify: `CLAUDE.md`, the `ad_channels` bullet: one line saying the launch holds the media buyer's settings and the locked rate.
- Test: `ad_channels/tests_launch.py`, inside `ArabicPageTests`.

- [ ] **Step 1: Write the failing test.** `test_settings_form_in_arabic` renders the page under `/ar/` and asserts the Arabic labels for budget, start, last day, interests and creatives.
- [ ] **Step 2: Run it and confirm it fails.** `$DM test ad_channels.tests_launch.ArabicPageTests`. Expected: FAIL, the labels are untranslated.
- [ ] **Step 3: Run `$DM makemessages -l ar`**, fill every empty `msgstr`, remove any `python-format` flag msgfmt rejects, then run `$DM compilemessages`.
- [ ] **Step 4: Full check.**
  - `$DM test` should give all OK. That's about 590 tests and takes about 5 minutes.
  - `$DM djust_check` should show only C401.
  - `$DM makemigrations --check --dry-run` should print "No changes detected".
- [ ] **Step 5: Browser check.** On the dev server (`make docker-migrate` first):
  - Edit the settings and confirm the errors show.
  - Check the EGP preview after a build.
  - Check `/ar/` and dark mode.
  - Check phone width if the window can be resized.
