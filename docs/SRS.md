# Software Requirements Specification — LeadsHub & Project X

This is the source of requirements for the platform. Requirement IDs (`FR-x.y`)
are referenced from code, tests and commits. Change this file through review,
like code.

## 1. Document overview and architecture

This SRS defines the functional, data, operational and non-functional
requirements for the dual-system real estate marketing and financial platform
made up of **LeadsHub** and **Project X**.

Small and mid-sized Egyptian brokerages are often priced out of digital
advertising on Meta and Google because large developers and enterprise
brokerages set high minimum bids.

- **LeadsHub** is a real estate marketing automation and lead generation
  platform. It pools broker advertising budgets, automates housing campaign
  creation, routes incoming leads in real time, and tracks conversions in an
  integrated CRM.
- **Project X** is a broker commission tracking module. It manages closed
  transactions, validates sale contracts, calculates net commission payouts and
  schedules check distributions.

### 1.1 Architectural layers

1. **Client Portal.** Brokers build 5-step campaigns, choose Pooled Dark Page or
   Connected Brand Page execution, manage EGP/USD wallet balances, track sales
   in a built-in CRM, and view Feedback Scores and Spend Tiers.
2. **Project X Transaction Module.** Sale registrations, contract proofs, fixed
   commission calculations and 30-day payout countdowns.
3. **Shared Authentication & Master Data.** Single sign-on and multi-tenant
   isolation, on a shared catalog of developers, projects, geographic areas and
   unit types.
4. **Internal Back Office & Ad Operations.** The 4-hour SLA task review queue,
   JSON campaign brief generator, real-time weighted-draw lead routing,
   confidential financial calculation layer, and multi-Business Manager ad
   account resilience.

### 1.2 Objectives

- **Budget pooling efficiency.** Merge identical campaign parameters (creative,
  project, audience) into one Meta ad set (`adset_id`) to lower Cost Per Lead.
- **Deterministic, auditable lead distribution.** Deliver leads to broker CRMs
  in under 60 seconds via a budget-weighted draw.
- **Confidential financial abstraction.** Keep internal ad execution costs
  (actual spend, media margins) separate from client-facing ledgers (displayed
  media cost, service fees).
- **Broker lifecycle unification.** One login to acquire leads in LeadsHub and
  claim commissions in Project X.

## 2. Roles, permissions and security

Role-based access control with multi-tenancy and data masking. Internal staff
operations stay fully separated from client-facing interfaces.

### 2.1 Roles

Internal back office:

- **Platform Admin.** Platform configuration, role assignment, master
  developer/project data, ad account mappings, fee rates, financial overrides.
- **Media Buying Lead.** Oversees review queues and SLA timers, assigns tasks to
  media buyers, manages ad account allocation across Business Managers.
- **Media Buyer.** Reviews, approves or rejects campaign briefs, deploys ads via
  AI execution agents, uploads organic lead batches, inspects custom creatives.
- **Content Manager.** Curates the creative library, tags assets with
  developer/project metadata, enforces offer expiry, manages publishing status.
- **Finance Manager.** Wallet top-ups, refunds, daily FX rates, tax-compliant
  e-invoices, price overrides.

External client and developer:

- **Broker Owner.** Controls the organization (tenant): wallet funds,
  campaigns, connected social pages, team invitations, Project X contracts.
- **Broker Team Member.** Works assigned leads in the CRM (statuses, notes).
  Cannot change campaign budgets or top up the wallet unless the Broker Owner
  grants explicit permission.
- **Developer Read-Only View.** Aggregate project metrics (active brokers, live
  campaigns, leads by channel) without broker identities, campaign strategies
  or customer details.

### 2.2 Permissions and confidentiality matrix

Actual ad spend, the 16% internal media margin and platform account health must
never reach external client APIs, interfaces or exports.

| Permission / data access | Platform Admin | Media Buying Lead | Media Buyer | Finance Manager | Broker Owner | Broker Team | Developer View |
|---|---|---|---|---|---|---|---|
| View actual ad spend & media margin | Allowed | Allowed | Allowed | Allowed | — | — | — |
| Approve / reject campaign tasks | Allowed | Allowed | Allowed | — | — | — | — |
| Manage master data & ad accounts | Allowed | Allowed | — | — | — | — | — |
| Manage FX rates & price overrides | Allowed | — | — | Allowed | — | — | — |
| Create & run campaigns | Allowed | — | — | — | Allowed | Conditional | — |
| Manage wallet & top-up funds | Allowed | — | — | Allowed | Allowed | — | — |
| Access & work assigned leads | — | — | — | — | Allowed | Allowed | — |
| View aggregate project metrics | Allowed | Allowed | Allowed | Allowed | — | — | Allowed |

### 2.3 Access rules (added during build)

- **One audience per user.** Internal back office, broker client portal and
  developer view are separate areas. A user's roles all belong to one of them,
  and each page is open only to its audience. Other users get "access denied".
- **Multiple and custom roles.** A user can hold several roles from their
  audience. A Platform Admin can add and remove permissions on any role. The
  essential roles in §2.1 start with the §2.2 matrix as defaults; they keep
  their names and can't be deleted. A Platform Admin can also create custom
  roles with an English and Arabic name, an audience and a set of
  permissions. Client and developer roles can never be given internal
  permissions such as actual ad spend. Platform Admin always keeps the
  permissions to manage roles and users.
- **Every internal user has a role.** Admin access comes from an internal role,
  not from a staff flag.
- **Team leaders.** A Media Buyer is promoted to team leader by adding the
  Media Buying Lead role. The lead keeps every Media Buyer permission and can
  also assign campaign tasks to media buyers and manage master data.
- **Revocation.** Removing a role takes effect on the next page load or live
  action.

## 3. LeadsHub Client Portal

### 3.1 Campaign builder (§4)

A 5-step wizard that captures parameters and checks wallet coverage before
queue submission.

1. **Target selection (FR-4.1).** A single project (e.g. Palm Hills Badya) or a
   general category across projects in an area (e.g. commercial units in New
   Cairo). Drawn from the master catalog (about 50 developers, 150+ projects,
   12 areas, 6 unit types).
2. **Destination and identity (FR-4.3, FR-4.4).**
   - *Platform Dark Page (pooled).* Runs under platform-managed channels and
     allows budget pooling (FR-7.1): the budget joins an existing Meta ad set
     with identical creative, project and audience.
   - *Connected Instagram/Facebook page (private).* Runs under the broker's own
     handle (e.g. @nour.realty). Never pooled; all leads go to the owner
     (FR-7.2).
3. **Creative selection (FR-4.2).** Approved library assets filtered by project
   and area: 9:16 reels, carousels, static images, landing pages.
4. **Audience selection (FR-4.5).** Plain-language presets (e.g. "New Cairo
   compound buyers — higher income"). No age or gender restrictions, per Meta
   housing policy (FR-5.14).
5. **Budget, schedule and AI optimization (FR-4.6, FR-4.7).** Daily or lifetime
   budget with start/end dates, plus live CPL and daily lead estimates. "Run"
   moves the campaign to *AI Building* and into the back-office review queue.

Campaign states:

- **Draft.** Saved, not submitted.
- **AI Building.** In the review queue, awaiting media buyer approval.
- **Live.** Approved and delivering.
- **Needs Changes.** Rejected with reasons (e.g. expired offer, low budget).
- **Paused (Low Balance).** Wallet below the 24-hour minimum threshold.
- **Ended.** Schedule complete.

### 3.2 Creative library (§5)

Ready-made assets, so brokers don't pay for design or production (FR-5.2).

- **Metadata (FR-5.3).** Developer, project, area, unit type, language
  (Arabic/English), aspect ratio, offer expiry date.
- **Expiry (FR-5.5, FR-5.7).** Expired assets are flagged in admin and hidden
  from the campaign builder.
- **Private uploads (FR-5.8, FR-5.9).** Broker uploads need Content Manager
  approval before deployment.

### 3.3 CRM and lead pipeline (§9)

Leads route into the broker's CRM (FR-9.1). Pipeline stages:

1. **New** — awaiting first contact.
2. **Contacted** — reached by phone, WhatsApp or email.
3. **Serious** — qualified, clear buying intent.
4. **Site Visit** — viewing scheduled or done.
5. **Closed Won** — transaction finalized.
6. **Not Interested** — disqualified or unresponsive.

Repeat registrations by the same person, in one or several campaigns, are kept
as separate lead records (FR-7.11). Duplicate suppression is prohibited so
assignment stays fair across overlapping campaigns.

### 3.4 Wallet, dual-currency ledger and FX (§8)

Multi-currency top-ups without forced conversion (FR-8.1).

1. **Top-up.** Fawry Pay Code (minimum EGP 20,000), or card/wire in USD. Funds
   are kept in the currency paid.
2. **Lead event.** When a Meta Leadgen webhook creates a lead, the official
   USD/EGP rate for that sign-up day is captured.
3. **Charge.** The displayed USD lead price × the sign-up-day FX rate gives the
   EGP charge.
4. **Deduction and monitoring.** The fee is deducted from the wallet. An alert
   fires when remaining coverage drops below 3 days.

Rules:

- **As-is reservation (FR-8.1).** EGP payments (Fawry, local debit) are stored
  in EGP; USD payments (card, wire) in USD. No conversion on deposit.
- **Per-lead FX (FR-8.1, FR-8.13).** The deduction uses the sign-up-day rate,
  which is stored on the lead's ledger entry (FR-8.5).
- **Gateways (FR-8.2, FR-8.3, FR-8.4).** Fawry cash codes (min EGP 20,000),
  credit cards, and manual admin credits for enterprise checks.
- **Low balance (FR-8.11, FR-6.13).** Warn below 3 days of coverage; pause
  campaigns automatically at zero coverage.

### 3.5 Feedback Score and Spend Tiers (§11)

Feedback Score (0–100) dimensions (FR-11.2):

- **Coverage (30%).** Share of assigned leads contacted or updated within 72 h.
- **Speed (25%).** Average time from delivery to first CRM status update.
- **Depth (25%).** Share of leads moved past *Contacted* into *Serious*,
  *Site Visit* or *Not Interested*.
- **Completeness (20%).** Share of leads with disposition notes.

**Neutrality (FR-11.3).** *Not Interested* scores the same as *Serious*. The
score rewards logging, never outcomes.

Spend Tiers, from 90-day rolling spend and score (FR-11.7):

| Tier | 90-day spend (USD) | Score gate | Benefits |
|---|---|---|---|
| Bronze | $0 – $999 | None | Standard builder and library |
| Silver | $1,000 – $4,999 | ≥ 70 | Priority AI launch queueing |
| Gold | $5,000 – $14,999 | ≥ 70 | Service fee 10% → 8% |
| Platinum | $15,000+ | ≥ 70 | Custom creative production, benchmark reports |

**Routing neutrality (FR-11.11).** Scores, tiers and rewards never influence
lead routing. Routing depends only on budget share.

## 4. Internal Back Office and Ad Operations

### 4.1 Task review queue and briefs (§6)

All campaign creations, edits and media uploads enter one review queue
(FR-6.1).

- **SLA (FR-6.7).** Decide within 4 hours, 24/7. Track open tasks, SLA
  countdowns and decision medians. Tasks near breach escalate to the Media
  Buying Lead.
- **Briefs (FR-6.2, FR-7.6).** Approved tasks produce a JSON brief
  (`BRIEF-TSK-XXXX`) that media buyers hand to AI execution agents.

Example brief:

```json
{
  "brief_id": "BRIEF-TSK-5521",
  "generated_at": "2026-11-03T10:52:00+02:00",
  "leadshub_campaign_id": "LH-C-0051",
  "client": { "name": "Nour Realty", "wallet_balance_usd": 3180, "tier": "Gold" },
  "campaign": {
    "type": "specific",
    "developer": "Orascom",
    "project": "O West",
    "area": "6th of October",
    "destination": "client_instagram",
    "identity": "@nour.realty",
    "optimization_goal": "more_leads",
    "budget": { "mode": "daily", "amount_usd": 30 },
    "schedule": { "start": "2026-11-05", "end": "2026-12-05" }
  },
  "meta": {
    "objective": "OUTCOME_LEADS",
    "special_ad_categories": ["HOUSING"],
    "special_ad_category_country": ["EG"],
    "status_on_create": "PAUSED",
    "set_adset_name_to": "LH-C-0051",
    "routing_key": "adset_id"
  },
  "audience": {
    "name": "New Cairo compound buyers — higher income",
    "meta_interests": ["Real estate", "Compounds", "Luxury"],
    "lookalike_source": "closed_won_1pct",
    "geo_radius": "6th of October + 15km",
    "no_age_gender_limits": true
  },
  "creatives": [
    {
      "id": "CR-2290",
      "type": "carousel",
      "language": "ar",
      "aspect": "1:1",
      "offer": "O West apartment · 5% DP · 10-yr plan",
      "open_path": "/assets/cr/CR-2290/preview.html",
      "download_path": "/assets/cr/CR-2290/original.zip"
    }
  ],
  "routing": { "pool_eligible": "PL-0042", "mode": "weighted_by_budget" },
  "notes_for_agent": "Open each creative via open_path. Create Meta campaign PAUSED, apply HOUSING+EG, set AD SET name to leadshub_campaign_id, then activate. Report back Meta adset_id as routing key."
}
```

### 4.2 Ad account spreading and resilience (§6)

Campaigns run only on platform-owned ad accounts spread across several Meta
Business Managers (FR-6.10).

- **Spreading (FR-6.11).** At least 14 ad accounts across 3 Business Managers.
- **Health monitoring (FR-6.12).** If Meta disables an account (e.g. ACC-03),
  flag affected campaigns, halt their adset mappings and relaunch them on a
  healthy account (e.g. ACC-07).

### 4.3 Lead routing and pooling (§7)

Leads are matched to campaigns by Meta `adset_id` (FR-7.6).

**Pooling (FR-7.1, FR-7.3).** Dark Page campaigns with identical creative,
project and audience share one pool with a combined daily budget.

**Weighted draw (FR-7.8).** The probability that broker *i* gets a pooled lead
is their share of the pool's daily budget:

    P_i = B_i / Σ B_j = B_i / B_total

Example — pool PL-0042 on ad set 6001_7721, $90/day in total:

- Cairo Homes: $35 → 38.89%
- Nour Realty: $30 → 33.33%
- Delta Brokers: $25 → 27.78%

Every lead runs a fresh draw. The cryptographic seed, weights, drawn owner and
timestamp go to an immutable audit ledger (FR-7.9).

Delivery sequence:

1. Meta sends a Leadgen webhook with `value.ad_id`, `value.form_id`,
   `value.leadgen_id`, `value.created_time`.
2. `GET /{leadgen_id}` on the Graph API returns the `adset_id`.
3. Map `adset_id` to its pool, compute budget shares, run the weighted draw.
4. Insert the lead into the winner's CRM, deduct the fee, log the seed.

- **Delivery SLA (FR-7.12).** Under 60 seconds from the first webhook ping.
- **Unmapped leads (FR-7.6).** Leads from an unmapped ad set (e.g. 6009_8841)
  wait in a staging queue until an admin maps it. They are never dropped.

### 4.4 Financial architecture and overrides (§8)

Confidential calculation layer (FR-9.14). Fixed 16% media margin and 10%
service fee on actual spend (FR-8.7):

    Displayed Media Cost   = Actual Ad Spend × 1.16
    Service Fee            = Actual Ad Spend × 0.10
    Total Charged          = Actual Ad Spend × 1.26
    Platform Margin Kept   = Actual Ad Spend × 0.26

- **Price overrides (FR-8.12, FR-9.7).** Finance managers may override lead
  prices for premium campaigns or organic imports. Each override records the
  operator, timestamp, old price, new price and justification (FR-8.14).

## 5. Project X (broker commissions)

### 5.1 Transaction lifecycle

1. **Registration.** The broker picks developer and project from master data,
   enters the gross sale value and uploads proof of sale (signed contract or
   reservation).
2. **Validation.** Admins verify the documents. Valid deals take the project's
   fixed commission rate (e.g. 3.5%) from master data.
3. **Calculation.** Net payout = gross commission minus statutory taxes and
   collection fees.
4. **Disbursement.** Approval starts a 30-day countdown in the broker portal; at
   maturity a check collection voucher is generated.

### 5.2 Commission engine

    Gross Commission = Unit Sale Price × Project Commission Rate
    Net Payout       = Gross Commission − (Withholding Taxes + Bank Collection Fees)

## 6. Data and interfaces

### 6.1 Meta Marketing API (v25/v26)

**Leadgen webhook** — `POST /api/v1/webhooks/meta/leadgen`

```json
{
  "object": "page",
  "entry": [
    {
      "id": "109823471029",
      "time": 1762595520,
      "changes": [
        {
          "field": "leadgen",
          "value": {
            "ad_id": "238991_0012",
            "form_id": "771200_55",
            "leadgen_id": "900183_441",
            "created_time": 1762595518
          }
        }
      ]
    }
  ]
}
```

**Lead details** — `GET /v25.0/{leadgen_id}`

```json
{
  "id": "900183_441",
  "created_time": "2026-11-03T10:42:00+02:00",
  "ad_id": "238991_0012",
  "adset_id": "6001_7721",
  "campaign_id": "120330_7721",
  "form_id": "771200_55",
  "is_organic": false,
  "field_data": [
    { "name": "full_name", "values": ["Customer Name"] },
    { "name": "phone_number", "values": ["+201000000000"] },
    { "name": "preferred_unit_type", "values": ["Townhouse"] }
  ]
}
```

### 6.2 Data model

- **BrokerOffice (tenant).** Organization with EGP/USD wallet balances, spend
  tier and Feedback Score.
- **CampaignPool.** Shared campaign group for one project, audience and
  creative, with the combined daily budget.
- **MetaAdSetMap.** Maps Meta's `adset_id` to a CampaignPool.
- **LeadRecord.** Customer lead, CRM status, USD charge and sign-up-day FX rate.
- **RoutingDrawLog.** Immutable audit row per routing event: lead, drawn broker,
  weight, seed.

**BrokerOffice** — `id` UUID PK · `company_name` varchar(255) ·
`wallet_balance_egp` decimal(12,2) · `wallet_balance_usd` decimal(12,2) ·
`spend_tier` enum(Bronze, Silver, Gold, Platinum) · `feedback_score` smallint
0–100 · `created_at` timestamp

**CampaignPool** — `id` varchar(64) PK (e.g. PL-0042) · `project_id` FK ·
`audience_id` FK · `creative_id` FK · `total_daily_budget_usd` decimal(10,2) ·
`status` enum(Active, Paused, Terminated)

**LeadRecord** — `id` varchar(64) PK (e.g. LD-48213) · `meta_leadgen_id`
varchar(128) unique · `adset_id` varchar(128) indexed ·
`assigned_broker_office_id` FK · `full_name` varchar(255) · `phone_number`
varchar(64) · `crm_status` enum(New, Contacted, Serious, SiteVisit, ClosedWon,
NotInterested) · `charged_amount_usd` decimal(8,2) · `signup_day_fx_rate`
decimal(8,4) · `is_organic` bool · `created_at` timestamp

**RoutingDrawLog** — `id` bigint PK · `lead_id` FK · `adset_id` varchar(128) ·
`drawn_broker_office_id` FK · `drawn_weight` decimal(5,4) · `draw_seed`
varchar(128) · `executed_at` timestamp

## 7. Non-functional requirements

### 7.1 Performance

- **Lead routing.** Webhook → draw → lead record → broker CRM within 60 s
  (FR-7.12).
- **Review SLA.** 100% of campaign tasks decided within 4 hours (FR-6.7).

### 7.2 Security and isolation

- **Multi-tenant isolation.** Client portal queries are always filtered by
  `broker_office_id`.
- **Financial masking.** Client-facing APIs never include `actual_ad_spend`,
  `media_margin` or platform ad account identifiers (FR-9.14).

### 7.3 Availability

- **Ad account redundancy.** Active accounts across at least 3 Business
  Managers (FR-6.10).
- **Unmapped lead preservation.** Leads that fail adset mapping are staged,
  never dropped (FR-7.6).

### 7.4 User interface

- **Responsive.** Every page works on phone, tablet and laptop/desktop.
- **Bilingual.** English and Arabic, with full right-to-left layout for Arabic.
- **Light and dark themes.** Follows the device setting by default; users can
  switch and the choice is remembered.
