# Meta launch settings and EGP ad accounts (US-META-02 follow-up)

Builds on `2026-11-18-meta-campaign-launch-design.md`. Status: draft for approval.

## Intent

Before building a ready campaign on Meta, the media buyer adjusts how it runs
on Meta: budget, dates, audience interests and which approved creatives to
use. Ad accounts bill in EGP while the broker's budget stays in USD.

The broker's campaign is never rewritten. What the broker asked for stays on
`Campaign` and in the immutable `Brief`; what runs on Meta is saved on
`MetaLaunch`. Anything outside the broker's approval (a higher budget,
another project or offer, new creatives) goes back to the broker through
reject → Needs Changes, as SRS §3.1 and §4.1 (FR-6.1) require. The media
buyer has no "create & run campaigns" right (SRS §2.2).

## Goals

1. Editable before the build, on the Meta campaign page:
   - **Budget (USD):** at least 1, at most the broker's `budget_usd`. The
     budget type (daily or total) stays the broker's.
   - **Start and last day:** inside the broker's window. Start ≥ the broker's
     start, last day ≤ the broker's last day when they set one. A total budget
     needs a last day.
   - **Interests:** one per line, prefilled from the audience preset. Only
     interests; never age or gender (FR-5.14).
   - **Creatives:** a non-empty subset of the campaign's creatives. The same
     usable and buildable checks apply as today.
2. EGP ad accounts. USD accounts keep working unchanged. Any other currency is
   refused.
3. The page shows where the media buyer's values differ from the broker's.

## Non-goals

- Editing the target, project, offer, destination, channel or budget type.
  These go through reject → Needs Changes.
- A budget above the broker's.
- Editing interests, dates or creatives after the build. Budget and last day
  stay editable after the build as today (`change_budget`).
- A Finance rate table (M6). For now one setting holds the rate.

## Anti-goals

- The broker sees Meta-side numbers, actual spend or the EGP amounts
  (SRS §2.2 confidentiality).
- A running campaign's EGP budget moves because someone changed the rate
  setting.

## Approach (chosen: "split")

| Option | Trade-off |
|---|---|
| **Settings on the launch** (chosen) | The broker's data stays untouched and both versions are kept. Fits SRS §2.1, §2.2 and §4.1. |
| Rewrite the broker's campaign | One source of truth, but the media buyer authors the campaign and skips review. Breaks SRS §2.2 and FR-6.1. |
| Media buyer types an EGP budget with no rate | Simplest, but no cap ties it to the broker's approved USD. |

## Design

### Data (new migration `ad_channels/0004`)

`MetaLaunch` gains these fields:

- `starts_on` (date)
- `interests` (JSON list of names)
- `creative_ids` (JSON list of creative pks, in order)
- `currency` (`USD` or `EGP`)
- `fx_rate` (decimal: EGP per USD, `1` for USD accounts)

The existing `budget_usd` and `ends_on` now hold the media buyer's values, not
copies of the broker's.

### Rate

- New setting `META_EGP_PER_USD` (Decimal, from `.env`, default unset).
- Code note at the setting and at the conversion: a stand-in until M6's
  Finance-kept daily rate (SRS §2.1 Finance, §4.4; ROADMAP Q1–Q4).
- An EGP account with no rate set: the build refuses with a clear message.
- The rate is locked on the launch at the first build attempt. Budget changes
  and refresh use `launch.fx_rate`, never the current setting.

### Money conversion (one function, `ad_channels/services.py`)

`meta_minor_units(budget_usd, fx_rate) -> int` returns
`round_half_up(budget_usd × fx_rate × 100)`: cents for USD, piastres for EGP.
The old `budget_cents` is replaced by it.

- **Build:** sends `meta_minor_units(launch.budget_usd, launch.fx_rate)`.
- **Change budget:** the input is USD, capped at the broker's budget, and
  converted the same way.
- **Refresh:** `budget_usd = minor / 100 / fx_rate`, rounded to cents.

### Flow

1. The media buyer picks the account, page and form (as today). They then edit
   the settings form, which is prefilled from the broker's campaign, or from the
   launch on "Build again".
2. `build(actor, campaign_id, ad_account_id, page_id, lead_form_id, settings)`
   validates the settings against the broker's campaign and saves them on the
   launch in the claim. From there it reads only the launch: `_start`, `_end`,
   `_targeting` and the creatives come from `launch`, not `campaign`.
3. Validation errors show on the page like today's `problems`.

### Page

- The build section gets the settings fields: budget, start, last day,
  interests textarea and creative checkboxes.
- The "On Meta" card shows `$30 a day ≈ EGP 1,500 at 50.00` and marks values
  that differ from the broker's ("broker: $40").

### Tests

- Each limit: budget > broker's, start before broker's, last day after
  broker's, total budget with no last day, empty creatives, a creative not
  on the campaign.
- EGP: the right minor units are sent on build, budget change and refresh; a
  missing rate is refused; another currency is refused; changing the setting
  after the build doesn't change the launch.
- The broker's campaign is unchanged after a build with edited settings.
- Permissions: a role without `launch_meta_campaigns` is refused.
- Arabic strings, RTL and dark mode on the new fields.

## Left to the builder

- Field layout, widgets and the exact wording of the messages.
- Whether the settings form is a Django form (preferred: `FormMixin`) or plain
  inputs.

## Calls made for the user (check these)

1. The rate is a `.env` setting, not an admin screen.
2. The media buyer edits budgets in USD; EGP is shown, not typed.
3. The rate is locked per launch.
4. The budget type stays the broker's.
5. Interests, dates and creatives can't be edited after the build.
