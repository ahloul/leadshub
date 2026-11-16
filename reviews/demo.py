"""Review tasks for the demo campaigns (manage.py seed_demo). Never run against production data."""

import datetime

from django.utils import timezone

from . import briefs
from .models import SLA, Brief, Kind, ReviewTask, TaskStatus


def seed_reviews(campaigns, buyer):
    """Give each sent campaign without a task the review history its status implies.

    AI Building: an open task, nobody's yet. Needs Changes: a task the buyer
    rejected with the campaign's reasons. Live, Paused or Ended: an approved,
    deployed task with its brief.
    """
    now = timezone.now()
    for campaign in campaigns.exclude(status="draft").filter(review_tasks__isnull=True):
        if campaign.status == "ai_building":
            opened = now - datetime.timedelta(minutes=40)
            ReviewTask.objects.create(
                campaign=campaign, kind=Kind.NEW_CAMPAIGN, created_at=opened, due_at=opened + SLA
            )
            continue
        opened = (campaign.submitted_at or now) - datetime.timedelta(hours=3)
        decided = opened + datetime.timedelta(minutes=95)
        if campaign.status == "needs_changes":
            ReviewTask.objects.create(
                campaign=campaign, kind=Kind.NEW_CAMPAIGN, status=TaskStatus.REJECTED,
                created_at=opened, due_at=opened + SLA, assigned_to=buyer, assigned_at=opened,
                decided_by=buyer, decided_at=decided, reasons=campaign.change_reasons,
            )
            continue
        task = ReviewTask.objects.create(
            campaign=campaign, kind=Kind.NEW_CAMPAIGN, status=TaskStatus.APPROVED,
            created_at=opened, due_at=opened + SLA, assigned_to=buyer, assigned_at=opened,
            decided_by=buyer, decided_at=decided, deployed_by=buyer,
            deployed_at=decided + datetime.timedelta(minutes=20),
        )
        Brief.objects.create(task=task, content=briefs.build(task, decided), generated_at=decided)
