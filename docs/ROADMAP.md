# Roadmap — LeadsHub & Project X

This is the build plan for the requirements in [SRS.md](SRS.md): what to build
next, in what order, what each milestone delivers, and which questions the
business still has to answer.

- **Last updated:** 2026-11-12. M1 and M2 are built; M3 is next.
- **The SRS decides what the product does. This file decides the order.**
  Where the SRS states a rule, the build follows it as written. Where the SRS
  is silent, this file proposes a default and marks it for the business to
  confirm.
- **Every milestone follows [AGENTS.md](../AGENTS.md):** the permission
  checklist, English and Arabic, light and dark, and tests with every task.

> **OPEN QUESTIONS: the work waits for these five.**
>
> - **Q1** The full original SRS
> - **Q2** What the daily budget caps
> - **Q3** How a pool's daily spend is divided between its brokers
> - **Q4** What the per-lead amounts mean when charging is daily
> - **Q5** Project X tax and fee rates
>
> They are written out in [section 5](#5-questions-for-the-business), ready to
> put to the business. Another 23 questions (Q7 to Q29) have a default that
> the build uses until the business says otherwise. Q6 (self sign-up) was
> decided on 2026-11-14.

In the milestones, a box that starts with **OPEN QUESTION** means that part of
the work waits for an answer. A box that starts with **CONFIRM** means the
build uses the stated default. Search this file for either word to find them
all.

## 1. Where the project stands

**Built:**

- Sign-in with lockout after failed attempts, roles and capabilities, custom
  roles and the admin.
- Broker offices as tenants, with memberships.
- English and Arabic with right-to-left layout, and light and dark themes.
- The master catalog: developers, areas, unit types and projects, with each
  project's commission rate.
- Three home pages: `/portal/`, `/back-office/` and `/developer/`.
- Sidebar navigation by capability (a drawer on phones), the office team page with email
  invitations, and opening an office with its owner in the admin (M1).
- Brokerage self sign-up with email confirmation and staff approval
  (Q6, decided 2026-11-14).

**Not built:** everything in SRS §3 to §5 (campaigns, creatives, leads, wallet,
review queue, routing, scores, ad accounts, Project X) and the developer
metrics.

**Gaps the first milestones close:**

| Gap | Closed in |
|---|---|
| ~~The page header has no navigation links~~ | M1, done |
| ~~By default a Platform Admin can view broker offices but not create one; today that takes a superuser~~ | M1, done |
| ~~No live view has an event handler yet, so the pattern for guarded actions is not set~~ | M1, done |
| ~~No email settings~~ | M1, done |
| ~~No file storage settings~~ | M2, done |
| No background job runner or scheduler | M4 |
| The channel layer is in-memory, so a worker process can't push updates to browsers | M5 |
| No production deployment; the Docker setup is for testing only | before real brokers |

## 2. Build order

Sizes are relative (S, M, L), not time estimates.

| # | Milestone | SRS | Needs | Size | Waiting on the business |
|---|---|---|---|---|---|
| M1 | Navigation, office onboarding and team management (**built 2026-11-12**; self sign-up 2026-11-14) | §2.1, §2.2 | — | S | Confirm Q7, Q28 |
| M2 | Creative library and audience presets (**built 2026-11-12**) | §3.2 | — | M | Confirm Q29 |
| M3 | Campaign builder | §3.1 | M2 | L | **Open: Q2.** Confirm Q7 to Q11 |
| M4 | Review queue, 4-hour SLA and briefs | §4.1 | M3 | L | **Open: Q2.** Confirm Q12, Q13, Q20 |
| M5 | CRM pipeline and organic lead upload | §3.3 | — | M | Confirm Q14, Q15 |
| M6 | Wallet, daily charging and FX | §3.4, §4.4 | M3 | L | **Open: Q1, Q2, Q3, Q4.** Confirm Q9, Q16 to Q19 |
| M7 | Pools, Meta webhook and weighted-draw routing | §4.3, §6.1 | M4, M5, M6 | L | **Open: Q4.** Confirm Q10, Q21 |
| M8 | Feedback Score and Spend Tiers | §3.5 | M5, M6 | M | Confirm Q13, Q22, Q23 |
| M9 | Ad account spreading and relaunch | §4.2 | M4, M7 | M | Confirm Q24 |
| M10 | Developer metrics view | §2.1, §2.2 | M3, M5, M7 | S | Confirm Q25 |
| MX | Project X | §5 | file storage from M2 | L | **Open: Q5.** Confirm Q26, Q27 |

- The order follows a lead's path: asset, campaign, review, lead, charge,
  routing.
- M1 and M2 can start today. M3, M4 and M5 can be built apart from the pieces
  marked open. M6 can't start until Q1 to Q4 are answered.
- M5 and M6 don't depend on each other.
- MX shares only file storage with the rest. It can run in parallel, or move
  earlier if commissions matter more to the business.

## 3. Decisions already made

These are settled. Don't reopen them in code or in this plan.

| Date | Decision | Basis |
|---|---|---|
| 2026-11-12 | Billing charges actual ad spend, daily. It is not a per-lead price. | §4.4, FR-8.7; project owner |
| 2026-11-12 | The daily budget is a cap, not an average. | §3.1; project owner |
| 2026-11-12 | The media margin and service fee use the §4.4 formulas on actual spend, unchanged: displayed media cost = spend × 1.16, service fee = spend × 0.10. | §4.4, FR-8.7; project owner |

`SRS.md` still describes the charge per lead in §3.4 and §4.3. Q1 and Q4 ask
the business how the two fit together, and `SRS.md` is corrected from the
answers.

## 4. Milestones

### M1. Navigation, office onboarding and team management

SRS §2.1, §2.2. Size S. Start here: it is small, and it is the first page with
event handlers, so it sets the pattern every later milestone repeats (handler
guard, office check, refusal tests).

**Delivers**

- Navigation in the base template for each audience, showing only the links
  the user's capabilities allow. Live pages link with `dj-navigate`.
- Office onboarding: a Platform Admin can create a broker office and its owner
  in the admin. Balances, tier and score stay read-only there.
- A team page at `/portal/team/`, guarded by `access_client_portal` and
  `manage_team`:
  - invite a member by email, with a set-password link built on Django's
    password-reset tokens;
  - deactivate and reactivate a member;
  - grant or revoke `run_campaigns` for a member (the "Conditional" cell in
    §2.2).
- An email backend setting (console output in development).

**Done when**

- An invited member sets a password and lands in the portal.
- A team member, and an owner from another office, are refused by every
  handler (`send_event` tests).
- Granting and revoking take effect on the member's next page load or live
  action (§2.3).

> **CONFIRM Q7, Q28.** Defaults in use: an owner can grant the campaign
> permission only; owners invite and manage team members, not other owners.
> (Q6 is decided: brokerages sign up themselves and staff approve them.)

**Self sign-up (Q6, added 2026-11-14):**

- `/register/` (public, linked from sign-in): company name, the owner's name,
  work email, mobile number and password. It creates a *pending* office and
  signs the person in with no role.
- The person confirms their email from a link (`/register/confirm/…`), and
  the staff who may change offices (`accounts.change_brokeroffice`, Platform
  Admin by default) get an email.
- Until approval, signing in leads to `/register/status/` only: confirm your
  email (with "send the link again"), in review, or turned down.
- In the admin, *Sign-ups to review* (with a count) lists pending offices.
  *Approve* (only once the email is confirmed) makes the applicant the
  Broker Owner; *Reject* keeps the account but shows the decision. Both
  email the applicant in the language they signed up in.
- Offices opened by staff in the admin start active, as before.
- Not built: a limit on how many sign-ups one address can send (the form has
  no rate limit or CAPTCHA yet), and a reason for a rejection.

**Status:** built 2026-11-12, with tests, and checked in headless Chrome on
2026-11-12: phone, tablet and laptop widths, light and dark, English and
Arabic, with no horizontal scrolling. Also checked there: the live actions,
the confirmation before deactivating, accepting an invitation from the
emailed link, and live navigation between pages.

**Known issues (djust 1.2.8, not M1 code).** Found in the browser check;
they need a decision.

1. **A signed-out page doesn't reach the sign-in page.** The WebSocket
   client ignores the server's `navigate` frames; only djust's SSE client
   acts on them.
   - When a session ends while a page is open (sign-out in another tab,
     expiry, or deactivation by an owner), the server refuses the next live
     action and closes the socket.
   - The client then shows "reconnecting" and reconnects about twice a
     second, and each attempt is refused, until the person reloads.
   - Nothing is exposed, but it loads the server.
2. **A reconnect after live navigation shows the first page.** After moving
   from one live page to another (the sidebar links), djust keeps the first
   page's view name in `dj-view`. When the socket reconnects, for example
   after a deploy or when a phone wakes, it mounts that first page's view
   under the new URL until the person reloads.

**Options:**

- Upgrade djust. 1.3.3 is out, but whether it fixes these is unchecked; its
  changelog mentions a `dj-navigate` change (#3749).
- Add a small client script that acts on `navigate` frames. It would wrap
  the browser's WebSocket.
- For issue 2 only, make the sidebar links full page loads.

Where it lives:

- Team rules: `accounts/services.py`. Reads: `accounts/selectors.py`.
- Page: `TeamView` in `leadshub/views.py`, template `leadshub/team.html`.
- Invitation link: `AcceptInvitationView` in `accounts/views.py`. It is
  Django's password-reset confirmation and signs the person in.
- Sidebar links: `leadshub/context_processors.py`.
- Admin: `BrokerOfficeAdmin` asks for the owner on the add form. The user
  list has a "Send a new invitation link" action.

Changes to project-wide rules that came out of M1 (AGENTS.md):

- Pages use Django's `PermissionRequiredMixin`. djust 1.2.8's own mixin takes
  only one permission over HTTP and fails on a list.
- Handler decorators are written `@live.permission_required(...)`.
- `PageLanguageMixin` also runs event handlers in the page language.
- Passwords now go through Django's standard password validators.

### M2. Creative library and audience presets

SRS §3.2, FR-4.2, FR-4.5, FR-5.2 to FR-5.7, FR-5.14. Size M.

**Delivers**

- A `creatives` app. `Creative` carries the FR-5.3 metadata (developer,
  project, area, unit type, language, aspect ratio, offer expiry date), an
  asset type (9:16 reel, carousel, static image, landing page), a publishing
  status, the file and a code such as `CR-2290`.
- Admin screens for Content Managers, with a filter for expired assets
  (FR-5.5) and a sidebar entry. The model permissions go to the Content
  Manager role through a data migration.
- One queryset method that answers "what may a broker use?": published, not
  expired, and matching the project or area (FR-5.7). The library page and
  the builder both call it.
- A read-only library page at `/portal/library/`.
- `AudiencePreset` in `catalog`, with a plain-language name in English and
  Arabic (FR-4.5). The Meta targeting details stay internal. The model has no
  age or gender fields (FR-5.14).
- File storage. Files are served through a view that checks permission,
  because broker uploads and Project X contracts will use the same storage.
  djust 1.2.8 ships an `UploadMixin`; evaluate it before writing upload code.

**Not in this milestone:** broker private uploads (FR-5.8, FR-5.9). They need
the review queue and arrive with M4.

**Done when**

- An expired asset is missing from the library page and the builder queryset,
  tested on both sides of the expiry date.
- An unpublished asset never appears to a broker.
- A file can't be fetched without signing in.
- Only roles with the library permission can manage assets.

> **CONFIRM Q29.** Defaults in use: everyone in a broker office browses the
> library and opens its files; an offer runs through its end date; a project
> asset takes the project's area and developer.

**Status:** built 2026-11-12, with tests, and checked in headless Chrome:
phone and laptop, light and dark, English and Arabic, filters in the URL,
opening a file, and the admin list.

Where it lives:

- Model, queryset (`Creative.objects.usable()`), admin: `creatives/`.
- Library page: `LibraryView` in `leadshub/views.py`, template
  `leadshub/library.html`.
- Audience presets: `catalog.AudiencePreset`; brokers' list in
  `catalog.selectors.audience_presets()`.
- Private files: `leadshub/files.py`.
- Demo data: `manage.py seed_demo` adds a small catalog, a preset and four
  creatives.

Storage (decided 2026-11-12):

- Uploads use the S3 API: S3 in production, and locally RustFS in Docker
  (`make storage-up`). MinIO no longer publishes its server images.
- Without bucket settings, uploads stay on disk in `media/` (tests, quick
  runs).
- The bucket stays private. The app checks permission and streams every
  file.
- Streaming large videos through the app costs server time. If that matters
  in production, switch to short-lived signed links (`url()` in
  `leadshub/files.py`).
- Not run here: the RustFS service, `make storage-up` and `make docker-up`.
  Docker isn't installed on the machine this was built on.

`UploadMixin`: not needed yet, because staff upload in the admin. Broker
uploads (M4) will use it.

### M3. Campaign builder

SRS §3.1, FR-4.1 to FR-4.7. Size L. Needs M2.

**Delivers**

- A `campaigns` app. `Campaign` belongs to an office and has a code such as
  `LH-C-0051`. The six §3.1 states are an explicit state machine: each
  transition is a model method and anything else is refused.
- The five-step wizard at `/portal/campaigns/new/`. The step is in the URL
  and the draft is saved to the database at every step. djust ships a
  `WizardMixin`; check whether it supports database drafts before building
  one.
  1. Target: one project, or a category across an area (FR-4.1).
  2. Destination: the platform dark page or the broker's connected page
     (FR-4.3, FR-4.4).
  3. Creative, from the M2 queryset (FR-4.2).
  4. Audience preset (FR-4.5).
  5. Budget (daily or lifetime), dates, and the CPL and daily-lead estimates
     (FR-4.6, FR-4.7).
- "Run" checks wallet coverage and moves the campaign from Draft to AI
  Building.
- A campaign list and a detail page, with the status filter in the URL. A
  campaign can be edited while it is Draft or Needs Changes.
- Guards: the list and detail pages need `access_client_portal`. The builder
  and every action also need `run_campaigns`. Every handler checks that the
  campaign belongs to the user's office.

**Done when**

- Every allowed state change works and every other one is refused.
- Another office's campaign is refused by every handler.
- An expired or unpublished creative can't be chosen, also when the request
  is forged.
- No page or form mentions age or gender.

> **OPEN QUESTION Q2: what the daily budget caps.** The coverage check, the
> estimates and the wording beside the budget field wait for the answer. The
> rest of the builder can be built first.

> **CONFIRM Q7 to Q11.** Defaults in use: team members without the campaign
> permission can view campaigns but not change them; estimates come from a
> benchmark table that staff keep; coverage follows the rule in Q9; a
> dark-page campaign has one creative; a connected page is a typed handle
> that staff verify.

### M4. Review queue, 4-hour SLA and briefs

SRS §4.1, FR-6.1, FR-6.2, FR-6.7, FR-7.6. Size L. Needs M3.

**Delivers**

- `ReviewTask`, with a code such as `TSK-5521`, for campaign creations,
  campaign edits and media uploads (FR-6.1). Each task is due four hours
  after it is created, around the clock (FR-6.7).
- The queue at `/back-office/queue/`: open tasks ordered by deadline with
  live countdowns, filters (mine, unassigned, near breach), the open count
  and the median decision time.
- Assigning needs `assign_campaign_tasks`. Approving and rejecting need
  `review_campaign_tasks`.
- Rejecting moves the campaign to Needs Changes and shows the reasons to the
  broker. Sending it again opens a new task.
- Approving writes a brief, `BRIEF-TSK-5521`, in the structure of the §4.1
  example. A brief is never changed after it is written.
- "Mark deployed": the media buyer records the Meta `adset_id` and the ad
  account. The campaign becomes Live, and the ad set mapping that M7 routes
  by is created. A minimal list of Business Managers and ad accounts arrives
  here; M9 adds the health and relaunch flow.
- Edits to a Live campaign and broker private uploads (FR-5.8, FR-5.9) enter
  the same queue.
- The first scheduled job: tasks near breach escalate to the people who can
  assign tasks (`assign_campaign_tasks`), which is the Media Buying Lead by
  default. This milestone adds the job runner and scheduler (section 6).

**Done when**

- Countdown, breach and escalation are tested with a controlled clock.
- A generated brief has every key of the §4.1 example.
- Reject reasons are visible to the campaign's office and to no other.
- Each action refuses a role without its capability.

> **OPEN QUESTION Q2: what the daily budget caps.** The budget figure written
> into the brief depends on the answer.

> **CONFIRM Q12, Q13, Q20.** Defaults in use: a campaign becomes Live when
> the buyer records the ad set ID; media uploads are decided by the people
> who manage the creative library; escalation happens when one hour is left;
> notices go out in the app and by email.

### M5. CRM pipeline and organic lead upload

SRS §3.3, FR-9.1, FR-7.11. Size M. Needs nothing, so it can be built alongside
M3 and M4.

**Delivers**

- A `crm` app. `Lead` follows §6.2, with a code such as `LD-48213` and the
  six pipeline stages. The two money fields from §6.2 are added but stay
  empty until Q4 is answered.
- A history of every status change and note, with who and when. The Feedback
  Score (M8) is computed from it, so it must exist from the first lead.
- The pipeline at `/portal/leads/`, guarded by `work_leads`: filters in the
  URL, a lead page, status changes and notes. Phone numbers render
  left-to-right.
- No duplicate suppression (FR-7.11): no uniqueness rule on phone or name.
- CSV upload of organic lead batches for Media Buyers in the back office.
- A lead that arrives appears on open pipeline pages without a reload. In
  production this needs the Redis channel layer (section 6).

**Done when**

- A member can't open or change another office's lead through any handler.
  Under the Q14 default, a member also can't open a colleague's lead.
- Every status change and note lands in the history with the user and time.
- The same person uploaded twice gives two leads.
- A CSV with a broken row is reported, and the batch is not half-saved.

> **CONFIRM Q14, Q15.** Defaults in use: leads are assigned to a person, the
> owner sees all of them and a member sees only their own; the media buyer
> picks the campaign or pool for an organic batch.

### M6. Wallet, daily charging and FX

SRS §3.4, §4.4, FR-8.1 to FR-8.5, FR-8.7, FR-8.11 to FR-8.14, FR-6.13,
FR-9.14. Size L. Needs M3.

**Delivers**

- A `wallet` app with a ledger that is only ever added to. The balances on
  `BrokerOffice` become a running total, updated in the same transaction as
  each entry.
- Top-ups, kept in the currency paid (FR-8.1). Manual credits by Finance come
  first (FR-8.4). Fawry pay codes with the EGP 20,000 minimum (FR-8.2) and
  cards (FR-8.3) sit behind one gateway interface and are switched on when
  the merchant accounts exist.
- A daily USD/EGP rate table that Finance keeps (`manage_fx_and_pricing`).
- Actual ad spend per ad set per day, stored internally. Staff enter it at
  first; reading it from Meta can follow.
- The daily charge (section 3): each day's actual spend is charged as
  displayed media cost (spend × 1.16) and service fee (spend × 0.10), per
  FR-8.7.
- The wallet page at `/portal/wallet/`, guarded by `manage_wallet`: balances,
  a statement with displayed media cost and service fee, days of coverage
  and the low-balance warning (FR-8.11).
- Jobs: warn below three days of coverage, pause campaigns below the 24-hour
  minimum (FR-6.13), and resume them after a top-up.
- An internal finance page, guarded by `view_internal_financials`, with
  actual spend and the margin kept.
- Price overrides that record the operator, time, old price, new price and
  reason (FR-8.12, FR-8.14).

**Done when**

- The charges for a day add up to that day's actual spend × 1.26, apart from
  amounts held back by a cap (Q16).
- Each balance always equals the sum of its ledger entries, also when a
  top-up and a charge happen at the same moment.
- A ledger entry can't be edited or deleted, in the admin or in code.
- No portal page or response contains actual spend, the margin or an ad
  account identifier. A test scans every portal page for them (FR-9.14).
- Every action refuses a role without its capability.

> **OPEN QUESTION Q1, Q2, Q3, Q4.** This milestone can't start until the
> business has answered how the daily charge works: the original finance
> text, what the budget caps, how a pool's spend is divided, and what the
> per-lead amounts mean.

> **CONFIRM Q9, Q16 to Q19.** Defaults in use: coverage follows the rule in
> Q9; a broker is never charged above the cap; EGP is spent before USD; a
> day's charge waits for that day's rate; a refund is a wallet credit or a
> recorded withdrawal.

### M7. Pools, Meta webhook and weighted-draw routing

SRS §4.3, §6.1, FR-7.1 to FR-7.3, FR-7.6, FR-7.8, FR-7.9, FR-7.11, FR-7.12.
Size L. Needs M4, M5 and M6.

**Delivers**

- `CampaignPool`, with a code such as `PL-0042`: dark-page campaigns with the
  same creative, project and audience share one pool and one Meta ad set
  (FR-7.1, FR-7.3). Connected-page campaigns are never pooled (FR-7.2).
- The webhook at `POST /api/v1/webhooks/meta/leadgen` (§6.1). It checks
  Meta's signature, stores the raw event first and ignores a repeated
  `leadgen_id`.
- A worker that follows the §4.3 sequence: fetch the lead from the Graph
  API, map its `adset_id`, run the weighted draw (P = B_i / B_total, FR-7.8),
  insert the lead into the winner's CRM and write the draw log, in one
  transaction.
- `RoutingDrawLog` keeps the seed, the weights, the winner and the time, and
  is never changed (FR-7.9). Any draw can be replayed from its row.
- Leads from an unmapped ad set wait in a staging queue in the back office
  and are released when an admin maps the ad set. They are never dropped
  (FR-7.6).
- The time from Meta's first ping to delivery is stored for every lead and
  checked against the 60-second target (FR-7.12).
- A stand-in for the Graph API and a command that replays sample webhooks,
  so the whole chain runs in tests and demos without Meta.

**Done when**

- Over many seeded draws, each broker's share of leads matches its budget
  share.
- Changing an office's score or tier does not change its odds (FR-11.11).
- The same person registering twice gives two leads and two draws (FR-7.11).
- A webhook with a bad signature is refused, and a repeated one creates
  nothing.
- An unmapped lead survives a restart and is delivered after mapping.

> **OPEN QUESTION Q4: what the per-lead amounts mean.** Step 4 of the §4.3
> sequence says "deduct the fee" when the lead is delivered. What that means
> under daily charging is part of Q4. The rest of routing can be built
> first.

> **CONFIRM Q10, Q21.** Defaults in use: a dark-page campaign has one
> creative; a category campaign is not pooled; a lifetime budget enters the
> draw as budget ÷ scheduled days; a brief for a connected page names no
> pool.

### M8. Feedback Score and Spend Tiers

SRS §3.5, FR-11.2, FR-11.3, FR-11.7, FR-11.11. Size M. Needs M5 and M6.

**Delivers**

- The score, from the M5 history: Coverage 30%, Speed 25%, Depth 25%,
  Completeness 20% (FR-11.2). Not Interested counts the same as Serious
  (FR-11.3).
- Tiers from 90-day spend and the score gate of 70 (FR-11.7), recalculated
  every night.
- The tier benefits: Silver tasks get priority in the review queue (M4), and
  Gold pays an 8% service fee in the daily charge (M6). Platinum is recorded
  only; its benefits are out of scope (section 7).
- A portal page with the score by dimension and the progress to the next
  tier.

**Done when**

- Two offices that differ only in Serious versus Not Interested get the same
  score.
- A tier changes on the right side of each spend boundary and of score 70.
- The M7 routing tests still pass: score and tier never reach the draw.

> **CONFIRM Q13, Q22, Q23.** Defaults in use: Silver tasks are listed first;
> the Speed scale runs from one hour to 72 hours; the score covers the last
> 90 days; spend means what the wallet was charged; a tier change applies
> from the next day.

### M9. Ad account spreading and relaunch

SRS §4.2, FR-6.10 to FR-6.12. Size M. Needs M4 and M7.

**Delivers**

- Business Managers and ad accounts (codes such as `ACC-03`) with a health
  status, managed in the admin under `manage_master_data`.
- A spreading rule that places each new ad set on a healthy account, and a
  warning when fewer than 14 accounts or 3 Business Managers are active
  (FR-6.11).
- When an account is disabled: its campaigns are flagged, their ad set
  mappings are halted so their leads go to staging, and relaunch tasks open
  with new briefs for a healthy account (FR-6.12).

**Done when**

- Disabling an account flags exactly its campaigns and no others.
- A lead that arrives for a halted mapping is staged, not lost.
- No portal page shows an account code or its health (§2.2, FR-9.14).

> **CONFIRM Q24.** Defaults in use: staff mark an account disabled by hand
> at first; the broker sees no change of state during a relaunch.

### M10. Developer metrics view

SRS §2.1, §2.2. Size S. Needs M3, M5 and M7.

**Delivers**

- On `/developer/`, for the developer's own projects: the number of active
  brokers, the number of live campaigns and leads by channel. Guarded by
  `access_developer_view` and `view_project_metrics`.
- The same figures for internal roles in the back office.

**Done when**

- A developer sees figures for their own projects only.
- No response contains a broker's name, a campaign's settings or a
  customer's details. A test scans the page for them.

> **CONFIRM Q25.** Defaults in use: "channel" is how the lead came in;
> figures cover the last 30 days; fewer than three brokers shows as "fewer
> than 3".

### MX. Project X

SRS §5. Size L. Needs only the file storage from M2, so it can be built in
parallel or moved earlier.

**Delivers**

- A `projectx` app. A broker registers a sale: developer and project from
  the catalog, the gross sale value and the proof of sale (§5.1 step 1).
- Staff validate the documents. A valid sale takes the project's commission
  rate, copied onto the sale so later rate changes don't alter it (step 2).
- The §5.2 calculation: gross commission, withholding taxes, bank collection
  fees and net payout (step 3). The rates are dated settings in the admin
  and are copied onto each sale.
- Approval starts a 30-day countdown that the broker sees in the portal. At
  maturity a check collection voucher is generated (step 4).
- New capabilities, because none of the existing 14 covers registering or
  validating a sale, and a new row in the §2.2 matrix.

**Done when**

- The figures on a sale match the §5.2 formulas, and they don't move when
  the project's rate or the tax settings change later.
- A sale can't be approved while the rates are missing.
- A broker can't open another office's sale or its documents.

> **OPEN QUESTION Q5: Project X tax and fee rates.** Registration and
> validation can be built first. Approving a sale waits for the rates.

> **CONFIRM Q26, Q27.** Defaults in use: the Broker Owner registers a sale;
> Platform Admin and Finance Manager validate it; the voucher is a plain
> printable page in Arabic and English.

## 5. Questions for the business

Each question gives what the SRS says, what is unclear, and what the build
does in the meantime. When an answer arrives, write it into
[SRS.md](SRS.md) in a section marked "added during build" (as §2.3 was),
update the milestone, and delete the question here.

### 5.1 Open questions: the work waits for these

#### Q1. The full original SRS

> **OPEN QUESTION. Blocks M6.**

- **Ask:** please share the complete original SRS, or at least its finance
  section (§8).
- **Why:** `SRS.md` is a shorter copy. It cites 46 requirement IDs and its
  numbering has gaps. In the finance section FR-8.6 and FR-8.8 to FR-8.10 are
  missing. Nothing from an original §10 appears, and Project X has no
  requirement IDs at all. The decision that billing charges actual spend
  daily (section 3) is not written anywhere in the copy, so code and tests
  have no requirement to cite for it.
- **It may also answer** Q2, Q3 and Q4 directly.

#### Q2. What the daily budget caps

> **OPEN QUESTION. Blocks the coverage check and estimates in M3, the brief
> in M4, and M6.**

- **The SRS says:** the broker sets a daily or lifetime budget (§3.1 step 5).
  The wallet is charged actual spend × 1.26 (§4.4). The example brief hands
  the broker's own figure, $30 a day, to the media buyer (§4.1).
- **Unclear:** the daily budget is a cap (section 3), but a cap on what?
- **Options**, for a $30 daily budget:
  1. *A cap on ad spend.* Meta gets up to $30. The wallet is charged up to
     $37.80 that day, and the statement shows a media cost of $34.80 against
     a $30 budget.
  2. *A cap on the media cost the broker sees.* Meta gets up to $25.86. The
     statement shows a media cost of up to $30 plus a $2.59 service fee,
     $32.59 in total.
  3. *A cap on the total charged.* Meta gets up to $23.81. The wallet is
     charged up to $30 in total.
- **Under options 2 and 3** the brief must carry the lower figure for Meta,
  not the broker's $30.
- **No default.**

#### Q3. How a pool's daily spend is divided between its brokers

> **OPEN QUESTION. Blocks M6.**

- **The SRS says:** brokers in a pool share one Meta ad set (FR-7.1). Leads
  are given out by a draw weighted by budget share (FR-7.8). Meta reports one
  spend figure per ad set per day.
- **Unclear:** how that day's spend is divided between the brokers for
  billing.
- **Options**, using the §4.3 example pool (Cairo Homes $35, Nour Realty $30,
  Delta Brokers $25):
  1. *By budget share.* Each broker pays the same share of the day's spend
     as their share of the pool's budget (38.89%, 33.33%, 27.78%), whatever
     number of leads the draw gave them that day.
  2. *By leads received.* The day's spend is spread over that day's leads,
     and each broker pays for the leads they won.
- **Note:** only option 1 keeps the daily budget a cap. Under option 2 a
  broker who wins more draws than their share pays more than their share.
- **No default.**

#### Q4. What the per-lead amounts mean when charging is daily

> **OPEN QUESTION. Blocks M6 and the last step of routing in M7.**

- **The SRS ties money to each lead in four places:**
  - §3.4 steps 2 to 4: the lead's USD price × the sign-up-day rate gives the
    EGP charge, which is deducted from the wallet.
  - §4.3 step 4: "deduct the fee" when the lead is delivered.
  - §6.2: every lead stores a USD charge and the sign-up-day rate.
  - §4.4: Finance can override "lead prices" for premium campaigns and
    organic imports (FR-8.12, FR-9.7).
- **Unclear**, now that billing charges actual spend daily:
  1. Is anything deducted at the moment a lead is delivered?
  2. What are the USD charge and the rate stored on a lead? For example, the
     broker's charge for that day divided by the leads they received that
     day, shown for information only.
  3. What does a price override change?
  4. What is charged for organic leads, which have no ad spend?
- **No default.**

#### Q5. Project X tax and fee rates

> **OPEN QUESTION. Blocks approving sales in MX.**

- **The SRS says:** net payout = gross commission − (withholding taxes + bank
  collection fees) (§5.2). It gives no rates.
- **Ask** (best answered by the accountant): the withholding tax rate or
  rates; the bank collection fee, and whether it is a percentage or a fixed
  amount; and whether any other deduction applies.
- **No default.** The rates will be dated settings in the admin, and no sale
  can be approved until they are entered.

### 5.2 Defaults to confirm: the build goes ahead with these

If the business agrees with a default, "yes" is a complete answer.

#### Q6. How a new brokerage gets its account (M1) — decided 2026-11-14

- **The SRS says:** nothing about sign-up.
- **Decided:** both. Staff can still open an office in the admin, and a
  brokerage can sign up itself; it confirms its email and a Platform Admin
  approves it before the portal opens (see M1, "Self sign-up").

#### Q7. What an owner can let a team member do (M1, M3)

- **The SRS says:** a team member can't change campaign budgets or top up the
  wallet "unless the Broker Owner grants explicit permission" (§2.1). The
  §2.2 matrix marks campaigns as "Conditional" for the team, but wallet
  top-up as not allowed.
- **Default:** the owner can grant the campaign permission only, as in the
  matrix. Team members without it can see the office's campaigns but not
  change them.
- **Alternative:** the owner can also grant wallet top-up, as §2.1 reads.

#### Q8. Where the cost-per-lead and lead estimates come from (M3)

- **The SRS says:** the builder shows "live CPL and daily lead estimates"
  (FR-4.6, FR-4.7). It names no source.
- **Default:** a table that staff keep in the admin, with a low and a high
  cost per lead for each project, falling back to area and unit type, then
  area. The builder shows a range and calls it an estimate. Later the
  figures can come from the platform's own history.
- **Needed from the business:** the first set of numbers.

#### Q9. The wallet coverage rule (M3, M6)

- **The SRS says:** the builder checks coverage before submission (§3.1).
  Campaigns pause when the wallet is "below the 24-hour minimum threshold"
  (§3.1) and "at zero coverage" (FR-6.13). A warning fires below three days
  (FR-8.11). It gives no formula, and nothing on how EGP and USD balances
  combine.
- **Default:**
  - Days of coverage = wallet value ÷ the most the office can be charged in
    a day.
  - Wallet value = USD balance + EGP balance at the latest rate. This is for
    the estimate only; no money is converted.
  - The most the office can be charged in a day comes from the daily budgets
    of its Live and AI Building campaigns, per the answer to Q2. A lifetime
    budget counts as what is left ÷ the days left.
  - A campaign can be submitted, and paused campaigns resume, with at least
    one day of coverage. The warning shows below three days. Campaigns pause
    below one day.
  - "Zero coverage" and "24-hour minimum" are treated as the same rule.
- **Alternative:** require three days of coverage to submit, so no campaign
  starts with the warning already showing.

#### Q10. How many creatives a campaign can have (M3, M7)

- **The SRS says:** a pool has one creative (§6.2), but the brief lists
  "creatives" (§4.1).
- **Default:** one creative for a dark-page campaign, so that it can pool. A
  connected-page campaign, which never pools, can have several.

#### Q11. How a broker's own page is connected (M3)

- **The SRS says:** a campaign can run under the broker's connected Instagram
  or Facebook page (FR-4.3, FR-4.4). It doesn't say how the page is
  connected.
- **Default:** the owner types the page handle and staff verify it during
  review. Connecting the page by signing in with Meta comes with the Meta
  integration.

#### Q12. When a campaign becomes Live (M4)

- **The SRS says:** Live means "approved and delivering" (§3.1). The brief
  tells the agent to create the campaign paused, then activate it and report
  the ad set ID (§4.1).
- **Default:** approval alone doesn't make a campaign Live. It becomes Live
  when the media buyer records the Meta ad set ID. Until then the broker
  sees AI Building. The four-hour SLA covers the decision only.

#### Q13. Review queue details (M4, M8)

- **(a) Broker media uploads.** The SRS says they need Content Manager
  approval (FR-5.8, FR-5.9) and enter the single review queue (FR-6.1), while
  the §2.2 matrix gives task approval to Platform Admin, Media Buying Lead
  and Media Buyer. **Default:** media-upload tasks are decided by the people
  who manage the creative library (Content Manager and Platform Admin).
  Campaign tasks are decided by the matrix roles.
- **(b) Escalation.** The SRS says tasks "near breach" escalate to the Media
  Buying Lead (FR-6.7). **Default:** when one hour is left.
- **(c) Silver "priority AI launch queueing"** (§3.5). **Default:** tasks from
  Silver and higher offices are listed ahead of Bronze tasks. The four-hour
  limit stays the same for everyone.

#### Q14. Who in an office sees which leads (M5)

- **The SRS says:** a team member "works assigned leads" (§2.1), but a lead
  is stored against the office only (§6.2).
- **Default:** leads are assigned to a person. The owner sees every lead of
  the office and assigns them. A team member sees only their own. The office
  can switch on automatic assignment in turn, so that a new lead doesn't
  wait for the owner.
- **Alternative:** every member sees every lead of the office.

#### Q15. Organic lead batches (M5)

- **The SRS says:** media buyers upload organic lead batches (§2.1). It
  doesn't say who receives them.
- **Default:** the media buyer picks the campaign or pool when uploading. A
  batch for a pool goes through the same weighted draw, lead by lead. What
  organic leads cost is part of Q4.

#### Q16. Spend above the budget (M6)

- **Background:** Meta treats an ad set's daily budget as an average over the
  week. Its published rule lets one day run up to 75% above the daily
  budget, with the week limited to seven times the daily budget
  ([summary](https://www.jonloomer.com/qvt/increased-budget-flexibility/);
  check Meta's "About daily budgets" help page for the current rule). Spend
  can also run above the brokers' budgets when a pool member pauses and the
  ad set budget is lowered late.
- **The SRS says:** total charged = actual spend × 1.26 (FR-8.7). The daily
  budget is a cap (section 3). On such a day both can't hold.
- **Default:** a broker is never charged above their cap. The platform bears
  the excess, and the internal finance page shows it.
- **Alternative:** the excess is charged on later days of the same week when
  spend is under the cap.

#### Q17. Which wallet pays when an office holds both currencies (M6)

- **The SRS says:** EGP and USD are kept separately and never converted on
  deposit (FR-8.1). It doesn't say which is charged first.
- **Default:** EGP first, then USD for the rest. An office can switch the
  order.

#### Q18. The official exchange rate (M6)

- **The SRS says:** charges use "the official USD/EGP rate" of the day (§3.4,
  FR-8.13), and Finance manages daily rates (§2.1).
- **Default:** the Finance Manager enters one rate a day, and the system
  does not fetch it. A day's charge waits until that day's rate is entered,
  and Finance gets an alert when a rate is missing.
- **Needed from the business:** which published rate counts as official (for
  example the Central Bank of Egypt's), and whether it is the buy, sell or
  mid rate.

#### Q19. Refunds (M6)

- **The SRS says:** the Finance Manager handles refunds (§2.1). Nothing
  else.
- **Default:** Finance can credit the wallet to reverse a charge, or record
  a withdrawal when money is returned to the broker by bank transfer outside
  the system. Both need a reason.

#### Q20. Notifications (M4 onward)

- **The SRS says:** alerts and escalations exist (FR-8.11, FR-6.7), but it
  names no channel.
- **Default:** a notice inside the app plus an email, for a new lead, a
  campaign that needs changes, low balance and paused campaigns, SLA
  escalation and payout maturity. SMS and WhatsApp are out of scope until
  asked for.

#### Q21. Pooling details (M7)

- **(a) Category campaigns.** The SRS defines a pool by creative, project
  and audience (FR-7.1), and a category campaign (FR-4.1) has no single
  project. **Default:** a category campaign on the dark page is not pooled
  and runs on its own ad set. **Alternative:** pool category campaigns that
  share area, unit type, audience and creative.
- **(b) Lifetime budgets.** The SRS draws by daily budget (FR-7.8).
  **Default:** a lifetime budget counts as the budget ÷ the scheduled days.
- **(c) The example brief.** The §4.1 example is a connected-page campaign
  that names a pool, while FR-7.2 says connected pages are never pooled.
  **Default:** follow FR-7.2; a brief for a connected page names no pool.

#### Q22. Feedback Score details (M8)

- **(a) Speed.** The SRS gives 25% for the average time to the first status
  update (FR-11.2), with no scale. **Default:** a lead scores 100 if it is
  first updated within one hour, falling in a straight line to 0 at 72
  hours. Speed is the average of those scores.
- **(b) Period.** The SRS gives none. **Default:** leads delivered in the
  last 90 days, as for tiers.
- **(c) Depth.** The SRS counts leads moved past Contacted into Serious,
  Site Visit or Not Interested. **Default:** a lead that reached Closed Won
  counts too.

#### Q23. Spend Tier details (M8)

- **(a) What counts as spend.** The SRS says "90-day spend (USD)".
  **Default:** what the wallet was charged in the last 90 days (displayed
  media cost plus service fee), less refunds. Charges paid in EGP count at
  their USD amount.
- **(b) When a tier change applies.** **Default:** tiers are recalculated
  every night, and the new tier, up or down, applies from the next day. This
  decides which service fee a Gold office pays.
- **(c) Below the score gate.** The SRS requires a score of 70 for Silver
  and above. **Default:** an office under 70 is Bronze, whatever it spends.

#### Q24. Disabled ad accounts (M9)

- **The SRS says:** when Meta disables an account, the affected campaigns
  are flagged and relaunched on a healthy one (FR-6.12).
- **Default:** staff mark the account disabled by hand at first; an
  automatic check through Meta can follow. The broker sees no change of
  state during the relaunch, because account health must not reach clients
  (§2.2). Their leads stop until the relaunch is done.

#### Q25. Developer metrics (M10)

- **The SRS says:** active brokers, live campaigns and leads by channel,
  without broker identities (§2.1).
- **Default:** "channel" means how the lead came in: platform page, broker's
  own page or organic upload. Figures cover the last 30 days. A count of
  brokers below three shows as "fewer than 3", so that one broker can't be
  picked out.

#### Q26. Who registers and who validates a sale (MX)

- **The SRS says:** the broker registers the sale and "admins" verify it
  (§5.1). The Broker Owner controls Project X contracts (§2.1).
- **Default:** the Broker Owner registers. Platform Admin and Finance
  Manager validate and approve. Both are new permissions, so other roles can
  be given them later.

#### Q27. The check collection voucher (MX)

- **The SRS says:** a voucher is generated at maturity (§5.1). It gives no
  content.
- **Default:** a printable page in Arabic and English with the office, sale
  reference, project, sale value, gross commission, deductions, net payout
  and maturity date.
- **Needed from the business:** an existing voucher, or its required
  wording, if there is one.

#### Q28. What an owner can do with their team (M1)

- **The SRS says:** the Broker Owner controls the organization and its team
  invitations (§2.1). It doesn't say whom an owner may invite or manage.
- **Default:**
  - An owner invites people as Broker Team Members. Other owners are added
    by LeadsHub staff in the admin.
  - An owner can deactivate, reactivate and give campaign access to team
    members. They can't change their own account or another owner's (anyone
    who can manage the team).
  - An invited person signs in with their email address. The invitation link
    works once, for 3 days (Django's default), and the owner can send a new
    one at any time.
  - A deactivated person is signed out on their next page load or live
    action, and their campaign access is kept for when they are reactivated.

#### Q29. The creative library (M2)

- **The SRS says:** brokers get ready-made assets (FR-5.2), tagged by
  developer, project, area, unit type, language, aspect ratio and offer end
  date (FR-5.3). Expired assets are hidden from the builder (FR-5.7).
- **Default:**
  - Everyone in a broker office can browse the library and open or download
    its files, not only people who can run campaigns.
  - An offer runs through its end date (Cairo time) and is hidden from the
    next day. An asset with no end date never expires.
  - An asset tagged with a project takes the project's area and developer.
    An asset with no project belongs to its whole area.
  - Files can be images, videos or zip bundles (carousels, landing pages), up
    to 100 MB. HTML and SVG files are refused, because they could run scripts
    on the site.

### 5.3 To start now: long lead times

These are not questions, but they take weeks outside the team's control.

- **Meta access (for M7).** Meta's Lead Ads guides say that reading leads
  needs App Review, including the `leads_retrieval` and `pages_manage_ads`
  permissions, and a verified business is usually required too. Start
  verification and review early, and check the current requirements in
  Meta's developer documentation
  ([webhooks guide](https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/lead-ads/quickstart/webhooks-integration),
  [retrieving leads](https://developers.secure.facebook.com/documentation/ads-commerce/marketing-api/guides/lead-ads/retrieving)).
- **Fawry merchant account (for M6).**
- **Card processor (for M6).** Ask whether it can charge and settle in USD in
  Egypt. FR-8.1 depends on it. If it can't, USD top-ups start as bank wire
  only.
- **The accountant (for MX).** See Q5.
- **Benchmark costs per lead (for M3).** See Q8.

Until Meta access arrives, M7 runs against a stand-in. Leads downloaded from
Meta as a file can be loaded through the M5 upload and go through the same
draw, which allows a pilot without the 60-second delivery.

## 6. Infrastructure, added when first needed

| What | When | Why |
|---|---|---|
| Email backend | M1 (done) | Invitations and notices |
| File storage, with private files behind a permission check | M2 (done) | Creatives, broker uploads and sale contracts |
| Job runner and scheduler | M4 | SLA escalation first; later expiry, coverage, the daily charge, tiers and payout maturity |
| Production deployment | before real brokers | The Docker setup is for testing only |

- **Job runner:** Redis with Celery is the proposal, because Redis is needed
  for the channel layer anyway. Decide at M4.
- **djust built-ins to check before writing helpers** (AGENTS.md, "New
  helpers"): `WizardMixin`, `UploadMixin`, `push_to_view` and the tenant
  managers all ship in the installed 1.2.8.

## 7. Out of scope until the SRS says more

- **E-invoices.** Named in the Finance Manager role (§2.1), with no
  requirement behind it.
- **Google Ads.** Named once in §1.
- **Platinum benefits.** Custom creative production and benchmark reports
  (§3.5) have no detail.
- **Lead export for brokers**, and rules for how long lead contact details
  are kept.
- **SMS and WhatsApp notifications.**
- **Reading spend and account health from Meta automatically.** Staff enter
  both at first.

## 8. Keeping this file current

- When a milestone ships, mark it in the section 2 table.
- When the business answers a question, write the answer into `SRS.md`,
  update the milestone and delete the question here.
- A new open point found while building goes into section 5 with the next
  number.
