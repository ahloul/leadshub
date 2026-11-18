# Meta and Google integration: where it plugs in

This is for the developer connecting LeadsHub to the Meta Marketing API
(Facebook and Instagram) and Google Ads. The campaign builder, the office's
own accounts and the hooks are built in `campaigns/`.

**Meta is connected in `ad_channels/`** (US-META-01, US-META-02):
Business Managers come in through Facebook Login for Business, and the
media buying team launches approved campaigns at
`/back-office/meta/campaigns/` (`ad_channels.services.build`, then pause,
`change_budget`, `stop`, `refresh`). The Graph calls are on
`ad_channels.meta_ads.client.MetaClient`; tests replace
`ad_channels.meta_ads.sync.client_for` with
`ad_channels.test_utils.FakeMetaLauncher`. Each Meta ID is saved in
`MetaLaunch` as soon as Meta returns it, so a failed build resumes. The
`AdPlatformClient` hooks below are still unused for Meta; Google has no
calls yet.

## What exists today

| Piece | Where | State now |
|---|---|---|
| Channel per campaign: `meta` or `google` | `Campaign.channel` | Chosen by the broker in the builder (Target step) |
| Where the ads appear: platform dark page, platform landing page, or the office's own account | `Campaign.destination`, `Campaign.connected_account` | Chosen in the builder. The dark page is Meta only and is the only pooled destination (FR-7.1) |
| The office's own Instagram accounts, Facebook pages and Google Ads accounts | `ConnectedAccount` (`platform`, `handle`, `status`, `external_id`) | Typed by the owner on `/portal/pages/`, status `unverified`; staff mark them `verified` in the admin |
| Optimization goal | `Campaign.goal`: `lowest_cpl`, `more_leads`, `better_quality` | Chosen in the builder (Audience step) |
| Lead sharing options | `Campaign.accept_area_leads`, `accept_developer_leads` | Chosen in the builder; for lead routing (ROADMAP M7) |
| Targeting | `campaign.audience` (`catalog.AudiencePreset`): `meta_interests`, `lookalike_source`, `geo_targeting` | Kept by staff in the admin. **Never send age or gender (FR-5.14).** |
| The network's ids | `Campaign.external_id`, `Campaign.platform_data` (JSON), `ConnectedAccount.external_id` | Empty, for you to fill |
| One client per channel | `campaigns/integrations/` | Every channel uses `ManualClient`, which makes no calls |

## The contract: `AdPlatformClient`

`campaigns/integrations/base.py` defines what LeadsHub calls. Write one
subclass per network, for example `campaigns/integrations/meta.py` with
`MetaClient` and `google.py` with `GoogleClient`, then name them in settings:

```python
AD_PLATFORM_CLIENTS = {
    "meta": "campaigns.integrations.meta.MetaClient",
    "google": "campaigns.integrations.google.GoogleClient",
}
```

The rest of the app only calls `integrations.client_for(channel)` and these
methods:

| Method | When it's called | What it should do |
|---|---|---|
| `connect_url(office_id=, request=)` | Rendering `/portal/pages/` | Return the OAuth start URL. When it returns a URL, the page's "Connect with Meta/Google" button links to it (today it shows "soon"). |
| `verify_account(account)` | Checking an account added by hand | Return True or False, or None if it can't tell |
| `launch(campaign)` | After review approves a campaign (ROADMAP M4) | Create it on the network; return the network's campaign id |
| `pause(campaign)`, `resume(campaign)`, `end(campaign)` | Wallet jobs and schedule end (ROADMAP M6) | Change delivery on the network |

Rules:

- Raise `integrations.base.IntegrationError` with a message staff can act on.
- Don't change `Campaign.status` in a client. The caller moves the campaign
  through its life cycle (`campaign.go_live()`, `pause_for_low_balance()`,
  `resume()`, `end()`), which refuses changes the SRS doesn't allow.
- Keep secrets (app secret, OAuth tokens) out of the code and out of the
  page. Read the app credentials from the environment (Fly secrets in
  production), and store tokens encrypted. Adding the storage for tokens is
  part of this work: no field holds them today.

## What you add

1. **OAuth callback views** for Meta and Google. Guard them like the Pages
   page: `access_client_portal` and `manage_connected_accounts`, with the
   office from the signed-in user. On success, create or update the office's
   `ConnectedAccount` rows with `external_id` and status `verified`.
   Follow the permission checklist in AGENTS.md for each new URL.
2. **`MetaClient` and `GoogleClient`** implementing the methods above.
3. **The calls from the review queue** (ROADMAP M4, built in `reviews/`).
   Today a media buyer approves a task (`reviews.services.approve`, which
   writes the brief), launches by hand from the brief, then records the ad
   set ID and ad account with "Mark deployed"
   (`reviews.services.mark_deployed` → `campaigns.services.mark_live`,
   which saves `external_id` and makes the campaign Live). To launch
   automatically, call `client_for(campaign.channel).launch(campaign)` from
   that flow and pass its result to `campaigns.services.mark_live`; keep
   "Mark deployed" for the manual client.
4. **The lead webhook** (ROADMAP M7, SRS §6.1), which maps Meta's
   `adset_id` to a pool or campaign.

## Testing

Tests must never call Meta or Google. Point `AD_PLATFORM_CLIENTS` at a fake
client with `override_settings`, as `campaigns.tests.FakeMetaClient` does,
and mock HTTP in the client's own tests.
