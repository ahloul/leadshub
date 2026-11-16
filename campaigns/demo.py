"""Demo campaigns for manage.py seed_demo (ROADMAP M3). Never run against production data."""

import datetime
from decimal import Decimal

from django.utils import timezone

from catalog.models import AudiencePreset
from creatives.models import Creative

from .models import (
    AccountStatus,
    BudgetKind,
    Campaign,
    Channel,
    ConnectedAccount,
    Destination,
    Goal,
    Platform,
    TargetKind,
)


def seed_campaigns(office, owner, projects):
    """Once per office: its Instagram account and a campaign in each status of the list."""
    ConnectedAccount.objects.get_or_create(
        office=office, platform=Platform.INSTAGRAM, handle="@nour.realty",
        defaults={"added_by": owner, "status": AccountStatus.UNVERIFIED},
    )
    if Campaign.objects.filter(office=office).exists():
        return
    phnc = projects["palm_hills_new_cairo"]
    today = timezone.localdate()

    o_west = projects["o_west"]
    Campaign.objects.create(
        office=office, created_by=owner, target_kind=TargetKind.PROJECT,
        project=o_west, area=o_west.area, developer=o_west.developer, goal=Goal.MORE_LEADS,
    )

    queued = Campaign.objects.create(
        office=office, created_by=owner, target_kind=TargetKind.PROJECT,
        project=phnc, area=phnc.area, developer=phnc.developer, destination=Destination.DARK_PAGE,
        audience=AudiencePreset.objects.filter(area=phnc.area).first(),
        budget_kind=BudgetKind.DAILY, budget_usd=Decimal("30"),
        starts_on=today + datetime.timedelta(days=1), ends_on=today + datetime.timedelta(days=31),
    )
    creative = Creative.objects.usable().filter(project=phnc).first()
    if creative is not None:
        queued.creatives.add(creative)
    if not queued.missing_steps():
        queued.submit()

    _seed_history(office, owner, projects, today)


def _seed_history(office, owner, projects, today):
    """Campaigns further along: live on Google, needing changes, paused and ended."""
    phnc, o_west = projects["palm_hills_new_cairo"], projects["o_west"]
    started = today - datetime.timedelta(days=20)

    def make(project=None, *, moves, creative_project=None, **fields):
        target = {"target_kind": TargetKind.PROJECT, "project": project,
                  "area": project.area, "developer": project.developer} if project else {}
        fields.setdefault("starts_on", started)
        campaign = Campaign.objects.create(
            office=office, created_by=owner, audience=AudiencePreset.objects.first(), **target, **fields,
        )
        creative = Creative.objects.usable().filter(project=creative_project or project).first()
        if creative is not None:
            campaign.creatives.add(creative)
        for move in moves:
            move(campaign)
        return campaign

    make(
        target_kind=TargetKind.CATEGORY, area=phnc.area, developer=phnc.developer,
        creative_project=phnc, channel=Channel.GOOGLE, destination=Destination.LANDING_PAGE,
        goal=Goal.BETTER_QUALITY, budget_usd=Decimal("55"),
        moves=[Campaign.submit, Campaign.go_live],
    )
    make(
        o_west, goal=Goal.LOWEST_CPL, budget_usd=Decimal("25"),
        moves=[Campaign.submit, lambda c: c.request_changes(
            "The headline quotes a price the developer hasn't confirmed. Use the launch offer instead."
        )],
    )
    make(
        phnc, goal=Goal.MORE_LEADS, budget_usd=Decimal("35"),
        moves=[Campaign.submit, Campaign.go_live, Campaign.pause_for_low_balance],
    )
    make(
        o_west, channel=Channel.GOOGLE, destination=Destination.LANDING_PAGE,
        budget_kind=BudgetKind.LIFETIME, budget_usd=Decimal("600"),
        starts_on=today - datetime.timedelta(days=60), ends_on=today - datetime.timedelta(days=30),
        moves=[Campaign.submit, Campaign.go_live, Campaign.end],
    )
