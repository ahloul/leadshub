"""Campaigns sent to review before the queue existed get their task (ROADMAP M4).

Each one is due four hours after it was sent (FR-6.7), so a campaign that
has waited longer shows as past the limit.
"""

import datetime

from django.db import migrations
from django.utils import timezone


def open_tasks(apps, schema_editor):
    Campaign = apps.get_model("campaigns", "Campaign")
    ReviewTask = apps.get_model("reviews", "ReviewTask")
    waiting = Campaign.objects.filter(status="ai_building").exclude(review_tasks__status="open")
    for campaign in waiting:
        opened = campaign.submitted_at or timezone.now()
        ReviewTask.objects.create(
            campaign=campaign,
            kind="new_campaign",
            created_at=opened,
            due_at=opened + datetime.timedelta(hours=4),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("reviews", "0001_initial"),
        ("campaigns", "0003_connected_account_permissions"),
    ]

    operations = [migrations.RunPython(open_tasks, migrations.RunPython.noop)]
