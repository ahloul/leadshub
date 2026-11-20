# Launch ready campaigns on Meta: design (US-META-02)

Date: 2026-11-18. Branch: `feat/meta-campaigns`. SRS §4.1, §4.2, §6.1;
FR-4.3, FR-4.4, FR-5.14, FR-6.2, FR-6.10, FR-6.11, FR-7.6.

## 1. Intent

Brokers build campaigns in LeadsHub. Once one is ready to launch, the
media buying team turns it into a real Meta campaign through the Marketing
API, on one of the platform's ad accounts (FR-6.10). Today that step is
manual: a buyer builds it in Ads Manager from the brief and types the ad set
ID into "Mark deployed". This feature replaces the manual step with a
back-office page that lists ready campaigns and lets the team create, view,
change and stop their Meta campaigns.

## 2. Goals, non-goals, anti-goals

**Goals**

- A back-office list of campaigns ready to launch, plus the ones already
  launched on Meta.
- **Create:** build the Meta campaign, ad set and ads from a ready campaign,
  paused first and then switched on. The LeadsHub campaign goes Live with its
  ad set ID saved (the routing key, FR-7.6).
- **Read:** what was built, with its Meta IDs and current Meta status.
- **Update:** pause or resume, and change the budget and end date.
- **Delete:** stop the campaign on Meta (archive). LeadsHub keeps the record
  and the campaign becomes Ended.

**Non-goals (later milestones)**

- Google campaigns. The list shows only `channel = meta`.
- Pooling: joining an existing ad set (FR-7.1, M7). Each campaign gets its
  own ad set for now.
- The lead webhook and routing (M7), wallet pauses (M6), relaunch after an
  account is disabled (M9).
- Changing the review flow. Reviews only decide which campaigns count as ready.

**Anti-goals (a failure even if it works)**

- Two Meta campaigns for one LeadsHub campaign.
- A half-built campaign on Meta that nobody can see or clean up.
- A token, ad spend or account health reaching a broker page (§2.2, FR-9.14).
- Age or gender targeting sent to Meta (FR-5.14).

## 3. Approaches considered

| | Approach | Trade-off |
|---|---|---|
| 1 | Build in a queued job (`leadshub/jobs.py`), page shows progress | Most robust, more moving parts |
| **2 (picked)** | **Build during the click; the page waits for Meta** | Simplest. A slow call keeps the buyer waiting. A crash mid-build needs the resume rule in §5.3. |
| 3 | Auto-launch from `AdPlatformClient.launch()` on approval | No screen to choose account or page, no edits: not the feature |

**The user picked 2.** To keep anti-goal "half-built and invisible" from
happening anyway, each Meta ID is saved the moment Meta returns it (§5.3), so
pressing Build again continues instead of starting over. The click runs
through djust's `start_async()` so the page shows "Building on Meta…" instead
of freezing.

## 4. Decisions made with the user

- **Who:** a new capability `launch_meta_campaigns`, given to Media Buyer.
  Media Buying Lead gets it through `ROLE_INCLUDES`. Changing who has it
  later is an admin change, no code.
- **Ready means:** approved by review and not launched yet
  (`ReviewTask.awaiting_deployment`). The review code is not changed.
- **Synchronous build** (approach 2).

## 5. Design

### 5.1 Where it lives

In `ad_channels/` next to the Business Managers and ad accounts.

- `ad_channels/models.py`: `MetaLaunch`.
- `ad_channels/meta_ads/client.py`: new write methods on `MetaClient`
  (create campaign, ad set, image, video, ad creative, ad; update status and
  budget; list pages and lead forms). No database access, as today.
- `ad_channels/services.py` (new): the rules. Each function takes the person
  acting and checks `launch_meta_campaigns`.
- `ad_channels/selectors.py` (new): the list.
- `ad_channels/views.py`: two live views.
- Reads of campaigns and review tasks go through `campaigns.services` /
  `reviews.selectors`, not their models (CLAUDE.md "one app per domain").

### 5.2 Model: `MetaLaunch`

One row per LeadsHub campaign launched on Meta.

| Field | Notes |
|---|---|
| `campaign` | OneToOne → `campaigns.Campaign`, `PROTECT`. Unique: blocks a second launch. |
| `ad_account` | FK → `MetaAdAccount`, `PROTECT` (as the model docstring asks) |
| `page_id`, `page_name` | The Facebook page the ads run under |
| `instagram_actor_id` | Optional, for Instagram placements |
| `lead_form_id`, `lead_form_name` | The instant form the ads use |
| `meta_campaign_id`, `meta_adset_id` | Filled as Meta returns them |
| `ads` | JSON list: `{creative_pk, image_hash or video_id, creative_id, ad_id}` per creative |
| `status` | `building`, `build_failed`, `active`, `paused`, `archived` |
| `last_error` | Meta's message, token scrubbed (as `MetaError` does) |
| `daily_budget_usd` / `lifetime_budget_usd`, `ends_on` | What is on Meta now |
| `launched_by`, `launched_at`, `updated_at` | |
| `meta_status_checked_at` | Last refresh from Meta |

Schema goes into `ad_channels/migrations/0001_initial.py` (early-dev rule),
and the capability grant into `0002_grant_role_permissions.py`.

### 5.3 Create: the build sequence

The buyer opens a ready campaign and chooses:

1. **Ad account:** from `MetaAdAccount.objects.available_for_launch()`.
2. **Facebook page:** loaded from Meta, the pages the account's Business
   Manager can use (`owned_pages` + `client_pages`).
3. **Lead form:** loaded from Meta, the page's existing instant forms.

Then **Build on Meta** runs, each step saving its ID before the next:

1. Campaign: `objective=OUTCOME_LEADS`, `special_ad_categories=["HOUSING"]`,
   `special_ad_category_country=["EG"]`, `status=PAUSED`, name = campaign code.
2. Ad set: name = campaign code (§4.1 brief), budget in cents (daily or
   lifetime), start = later of `starts_on` and now, end = `ends_on`,
   `optimization_goal=LEAD_GENERATION`, `destination_type=ON_AD`,
   `promoted_object={page_id}`, targeting per §5.4, `status=PAUSED`.
3. Per creative: upload (image → hash, reel → video ID and wait until Meta
   has processed it), ad creative with the page, the form, the offer text and
   a call to action, then the ad (`PAUSED`).
4. Switch campaign, ad set and ads to `ACTIVE`.
5. Record the launch: `campaigns.services.mark_live(adset_id, ad_account)`
   (campaign → Live) and a new `reviews.services.record_deployment(task,
   actor)` that sets `deployed_by` and `deployed_at`. This skips the
   "who may decide" check, since launching is now its own capability.

**Resume:** if any step fails, `status = build_failed` with the error shown.
Pressing Build again skips every step whose ID is saved. **Discard build**
deletes what was created on Meta and removes the `MetaLaunch`, so the
campaign is back in the ready list.

### 5.4 Targeting (FR-5.14)

From the campaign's audience preset:

- Location: Egypt (`countries: ["EG"]`). The preset's `geo_targeting` is
  free text and isn't sent (see §8).
- Interests: each name in `meta_interests` is looked up through Meta's
  targeting search. Names with no match are skipped and listed on the page.
- Lookalike: not sent (needs a custom audience that doesn't exist yet).
- Never age, gender or zip-code fields.

### 5.5 Read, update, delete

- **List** `/back-office/meta/campaigns/`: two tabs (ready, launched),
  filters in the URL, paginated. Shows code, office, project, budget,
  start/end, Meta status. Pages need
  `["accounts.access_back_office", "ad_channels.launch_meta_campaigns"]`.
- **Detail** `/back-office/meta/campaigns/<campaign_id>/`: the build form
  for a ready campaign, or the launch details for a launched one. A
  **Refresh from Meta** button reads the current status and budget.
- **Pause / Resume:** sets Meta status. The LeadsHub campaign stays Live
  (no "paused by staff" state exists, see §8).
- **Change budget / end date:** sent to the ad set. The budget may not go
  above the broker's `budget_usd`.
- **Stop (delete):** archive campaign on Meta, `MetaLaunch.status =
  archived`, LeadsHub campaign → Ended (`campaign.end()`). The ad set ID
  stays saved so late leads still route (M7).

Every handler that changes something carries
`@live.permission_required("ad_channels.launch_meta_campaigns")`.

## 6. Edge cases

### Readiness and races

1. Two people press Build on the same campaign: row lock on the campaign and
   the unique `MetaLaunch.campaign`. The second gets "already being built".
2. The campaign stops being ready between list and click (sent back,
   rejected): check `awaiting_deployment` again inside the build.
3. A Google campaign: never listed; the service refuses it too.
4. A creative was archived or its offer expired after approval: refuse the
   build and name the creative.
5. `starts_on` is in the past: start now. `ends_on` is in the past or today:
   refuse.
6. Lifetime budget with no end date: refuse (Meta requires one).
7. Budget below Meta's minimum for the account: show Meta's message, keep
   the build resumable.

### Accounts, tokens, pages and forms

8. The chosen ad account left `available_for_launch()` since the page
   loaded: check again at build time.
9. Meta rejects the token mid-build (`token_rejected`): mark the Business
   Manager's token invalid, as sync does, and stop.
10. The ad account's currency isn't USD while the budget is USD: refuse
    (see §8).
11. The Business Manager can't use any page, or the page has no lead forms,
    or lead ads terms aren't accepted on the page: say so and stop before
    creating anything.
12. Destination "own account": the broker's page must be shared with the
    platform's Business Manager. If it isn't in the page list, say so.
13. No Instagram account linked to the page: build for Facebook placements
    only and note it.

### Partial builds

14. Failure after the campaign or ad set exists: saved IDs, `build_failed`,
    Build again resumes, Discard cleans up on Meta.
15. Meta created an object but the response was lost (timeout): before
    creating a campaign or ad set, look it up by name (the campaign code)
    under the account and reuse it.
16. A reel still processing: wait up to 60 seconds, then fail resumably
    with "video still processing, try again in a minute".
17. Discard fails on Meta: keep the row with the error; nothing is hidden.

### After launch

18. Someone edits the campaign in Ads Manager: Refresh from Meta shows the
    real status and budget; LeadsHub doesn't overwrite it silently.
19. Meta deleted or disabled the campaign: Refresh shows it; no automatic
    relaunch (M9).
20. Budget raised above the broker's figure: refused.
21. Stop on an already archived campaign: no-op.
22. The ad account or Business Manager is deleted in the admin: blocked by
    `PROTECT`.

### Access and data

23. A role without `launch_meta_campaigns`: 403 on the pages, refused on
    every handler (tested with `send_event`).
24. Broker and developer audiences: 403 (`PAGE_AUDIENCES`).
25. Tokens never rendered; Meta errors stored only after `scrub()`.
26. Nothing new on broker pages: no account, page ID or spend leaks.
27. All labels in English and Arabic, RTL, light and dark, phone width.

## 7. Tests

- No real Meta calls: a fake `MetaClient` records calls and can fail at
  any step.
- Services: each edge case above that has a rule (1–17, 20–23).
- Pages: `PAGE_AUDIENCES` entries, `EveryPageIsGuardedTests`, handler
  refusals, list rendering, `RoleMatrixTests` update.
- `make test` and `make check` pass.

## 8. Decided with the user (2026-11-18, all defaults)

1. **Page and lead form:** picked from what Meta returns. Existing forms
   only; LeadsHub doesn't create forms.
2. **Ad account currency:** USD only. Accounts in another currency are
   refused until FX exists (M6). *Superseded on 2026-11-20 by
   `2026-11-20-meta-launch-settings-design.md`: USD and EGP accounts, EGP
   at the `META_EGP_PER_USD` setting, locked per launch.*
3. **Location targeting:** Egypt-wide. The preset's free-text geo isn't sent.
4. **Pause by staff:** the broker keeps seeing Live while it's paused on Meta.
5. **Creative types:** images and reels in v1. Carousels and landing-page
   creatives are refused with a message, for later.

## 9. Left to the builder

- Exact Graph API fields per object and the call-to-action type.
- Page layout, within the existing back-office patterns (`reviews/`
  templates).
- How long to poll a video, within the 60-second cap.
