"""Reads of an office's campaigns and connected accounts. Always scoped to one office (SRS §7.2)."""

import re

from django.db.models import Count, Q

from catalog import selectors as catalog_selectors
from creatives import selectors as creative_selectors

from .models import (  # noqa: F401 (the choices, for views and forms)
    PLATFORM_CHANNEL,
    AccountStatus,
    Campaign,
    Channel,
    ConnectedAccount,
    Goal,
    Platform,
    Status,
)


def office_campaigns(
    office_id, *, status="", channel="", goal="", developer_id=None, project_id=None, search=""
):
    """The office's campaigns, newest first, narrowed by the list's filters.

    ``search`` matches the campaign code (LH-C-0051, or just 51) and the names
    of its project, developer, area and unit type, in English or Arabic.
    """
    campaigns = (
        Campaign.objects.filter(office_id=office_id)
        .select_related("project", "developer", "area", "unit_type", "connected_account")
        .annotate(creative_count=Count("creatives"))
    )
    if status:
        campaigns = campaigns.filter(status=status)
    if channel:
        campaigns = campaigns.filter(channel=channel)
    if goal:
        campaigns = campaigns.filter(goal=goal)
    if developer_id:
        campaigns = campaigns.filter(developer_id=developer_id)
    if project_id:
        campaigns = campaigns.filter(project_id=project_id)
    if search:
        campaigns = campaigns.filter(_matching(search))
    return campaigns.order_by("-created_at", "-pk")


_SEARCHED_NAMES = ("project", "developer", "area", "unit_type")
_CODE = re.compile(r"(?:LH-?C-?)?0*(\d{1,9})", re.IGNORECASE)


def _matching(text):
    match = Q()
    for relation in _SEARCHED_NAMES:
        match |= Q(**{f"{relation}__name_en__icontains": text})
        match |= Q(**{f"{relation}__name_ar__icontains": text})
    code = _CODE.fullmatch(text.replace(" ", ""))
    if code:
        match |= Q(pk=int(code.group(1)))
    return match


def office_campaign_developers(office_id):
    """The developers the office's campaigns name, for the list's filter."""
    ids = Campaign.objects.filter(office_id=office_id, developer__isnull=False).values("developer_id")
    return catalog_selectors.developers(ids=ids, active_only=False)


def office_campaign_projects(office_id, *, developer_id=None):
    """The projects the office's campaigns advertise, optionally only ``developer_id``'s."""
    ids = Campaign.objects.filter(office_id=office_id, project__isnull=False).values("project_id")
    return catalog_selectors.projects(ids=ids, developer_id=developer_id, active_only=False)


def office_campaign(office_id, campaign_id):
    """One of the office's campaigns, or None (another office's, or none at all)."""
    return (
        Campaign.objects.filter(office_id=office_id, pk=campaign_id)
        .select_related(
            "project__developer", "project__area", "developer", "area", "unit_type", "audience",
            "connected_account",
        )
        .first()
    )


def choosable_creatives(campaign, today=None):
    """Library assets this campaign's target may use (FR-4.2, FR-5.7)."""
    return creative_selectors.for_campaign(
        project_id=campaign.project_id,
        area_id=campaign.area_id,
        developer_id=campaign.developer_id,
        unit_type_id=campaign.unit_type_id,
        today=today,
    )


def choosable_audiences(campaign):
    """Audience presets for the campaign's area, or for any area (FR-4.5)."""
    return catalog_selectors.audience_presets(area_id=campaign.area_id)


def chosen_creatives(campaign):
    return campaign.creatives.select_related("project", "area").order_by("pk")


def office_accounts(office_id, *, channel=None, usable_only=False):
    """The office's own pages and ad accounts, optionally only those for ``channel``."""
    accounts = ConnectedAccount.objects.filter(office_id=office_id)
    if channel:
        platforms = [p for p, c in PLATFORM_CHANNEL.items() if c == channel]
        accounts = accounts.filter(platform__in=platforms)
    if usable_only:
        accounts = accounts.exclude(status=AccountStatus.DISCONNECTED)
    return accounts.annotate(campaign_count=Count("campaigns")).order_by("platform", "handle")
