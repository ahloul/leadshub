"""The brief a media buyer hands to the AI execution agent (SRS §4.1, FR-6.2, FR-7.6).

``build(task, now)`` returns a dict with every key of the §4.1 example. Names
are in English, the agent's working language. A few values the example shows
come with later milestones and are written as ``null`` until then:

- ``routing.pool_eligible``: pools arrive with ROADMAP M7.
- ``client.wallet_balance_usd`` is the office's USD balance today; the wallet
  itself arrives with M6.

ROADMAP Q2 (OPEN): the budget is the broker's figure as entered. What the
daily budget caps may change what is written here.

The ``meta`` block is the example's. A Google campaign gets a ``google`` block
of the same shape instead (the SRS gives no Google example).
"""

from django.utils import timezone

# How each destination is named in the brief. The example shows
# "client_instagram" for a broker's own Instagram account.
_DESTINATIONS = {
    "dark_page": "platform_dark_page",
    "landing_page": "platform_landing_page",
}

_NOTES = {
    "meta": (
        "Open each creative via open_path. Create Meta campaign PAUSED, apply HOUSING+EG, "
        "set AD SET name to leadshub_campaign_id, then activate. "
        "Report back Meta adset_id as routing key."
    ),
    "google": (
        "Open each creative via open_path. Create the Google Ads campaign PAUSED, "
        "set the campaign name to leadshub_campaign_id, then activate. "
        "Report back the Google Ads campaign ID as routing key."
    ),
}


def _name(item):
    return item.name_en if item is not None else None


def _lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _money(value):
    if value is None:
        return None
    return int(value) if value == int(value) else float(value)


def _destination(campaign):
    if campaign.destination == "own_account":
        account = campaign.connected_account
        return f"client_{account.platform}" if account is not None else "client_account"
    return _DESTINATIONS[campaign.destination]


def build(task, now=None):
    campaign = task.campaign
    office = campaign.office
    project = campaign.project
    audience = campaign.audience
    account = campaign.connected_account if campaign.destination == "own_account" else None
    now = timezone.localtime(now or timezone.now())

    brief = {
        "brief_id": f"BRIEF-{task.code}",
        "generated_at": now.isoformat(timespec="seconds"),
        "leadshub_campaign_id": campaign.code,
        "client": {
            "name": office.company_name,
            "wallet_balance_usd": _money(office.wallet_balance_usd),
            "tier": office.spend_tier.title(),
        },
        "campaign": {
            "type": "specific" if campaign.target_kind == "project" else "general",
            "channel": campaign.channel,
            "developer": _name(campaign.developer),
            "project": _name(project),
            "area": _name(campaign.area),
            "unit_type": _name(campaign.unit_type),
            "destination": _destination(campaign),
            "identity": account.handle if account is not None else None,
            "optimization_goal": campaign.goal,
            "budget": {"mode": campaign.budget_kind, "amount_usd": _money(campaign.budget_usd)},
            "schedule": {
                "start": campaign.starts_on.isoformat() if campaign.starts_on else None,
                "end": campaign.ends_on.isoformat() if campaign.ends_on else None,
            },
            "accept_area_leads": campaign.accept_area_leads,
            "accept_developer_leads": campaign.accept_developer_leads,
        },
        "audience": {
            "name": _name(audience),
            "meta_interests": _lines(audience.meta_interests) if audience else [],
            "lookalike_source": (audience.lookalike_source or None) if audience else None,
            "geo_radius": (audience.geo_targeting or None) if audience else None,
            # FR-5.14: Meta's housing category allows no age or gender limits.
            "no_age_gender_limits": True,
        },
        "creatives": [
            {
                "id": creative.code,
                "type": creative.asset_type,
                "language": creative.language,
                "aspect": creative.aspect_ratio or None,
                "offer": creative.offer or creative.title,
                "open_path": creative.file_url,
                "download_path": creative.file_url,
            }
            for creative in campaign.creatives.order_by("pk")
        ],
        "routing": {
            "pool_eligible": None,
            "mode": "weighted_by_budget" if campaign.destination == "dark_page" else "direct",
        },
        "notes_for_agent": _NOTES[campaign.channel],
    }
    if campaign.channel == "meta":
        brief["meta"] = {
            "objective": "OUTCOME_LEADS",
            "special_ad_categories": ["HOUSING"],
            "special_ad_category_country": ["EG"],
            "status_on_create": "PAUSED",
            "set_adset_name_to": campaign.code,
            "routing_key": "adset_id",
        }
    else:
        brief["google"] = {
            "status_on_create": "PAUSED",
            "set_campaign_name_to": campaign.code,
            "routing_key": "campaign_id",
        }
    return brief


# PostgreSQL stores JSON with its keys re-sorted, so a stored brief is put
# back in the SRS example's order before it is shown or downloaded.
_ORDER = [
    "brief_id", "generated_at", "leadshub_campaign_id", "client", "campaign", "meta", "google",
    "audience", "creatives", "routing", "notes_for_agent",
    "name", "wallet_balance_usd", "tier",
    "id", "type", "channel", "developer", "project", "area", "unit_type", "destination", "identity",
    "optimization_goal", "budget", "schedule", "accept_area_leads", "accept_developer_leads",
    "mode", "amount_usd", "start", "end",
    "objective", "special_ad_categories", "special_ad_category_country", "status_on_create",
    "set_adset_name_to", "set_campaign_name_to", "routing_key",
    "meta_interests", "lookalike_source", "geo_radius", "no_age_gender_limits",
    "language", "aspect", "offer", "open_path", "download_path", "pool_eligible",
]
_RANK = {key: number for number, key in enumerate(_ORDER)}


def ordered(value):
    """``value`` with every object's keys in the brief's reading order (unknown keys last)."""
    if isinstance(value, dict):
        keys = sorted(value, key=lambda key: (_RANK.get(key, len(_ORDER)), key))
        return {key: ordered(value[key]) for key in keys}
    if isinstance(value, list):
        return [ordered(item) for item in value]
    return value
