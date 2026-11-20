"""Reads for the Meta campaigns pages (US-META-02)."""

from django.core.paginator import Paginator
from django.db.models import Count

from reviews import selectors as review_selectors

from .models import MetaLaunch

PER_PAGE = 25


def _page(items, number):
    return Paginator(items, PER_PAGE).get_page(number)


def ready(page=1):
    """Approved Meta campaigns waiting to be built, oldest approval first, with any failed build."""
    tasks = review_selectors.ready_for_meta().select_related("campaign__meta_launch")
    page = _page(tasks, page)
    _count_creatives([task.campaign for task in page.object_list])
    return page


def _count_creatives(campaigns):
    """Set ``creative_count`` on each campaign in one query, so ``builder_url`` needs none per row."""
    if not campaigns:
        return
    through = campaigns[0].creatives.through
    counts = dict(
        through.objects.filter(campaign_id__in=[c.pk for c in campaigns])
        .values("campaign_id").annotate(n=Count("pk")).values_list("campaign_id", "n")
    )
    for campaign in campaigns:
        campaign.creative_count = counts.get(campaign.pk, 0)


def launched(status="", page=1):
    """Campaigns built on Meta, newest first, optionally of one status."""
    launches = MetaLaunch.objects.select_related(
        "campaign__office", "campaign__project", "campaign__developer", "campaign__area",
        "campaign__unit_type", "ad_account",
    ).exclude(status__in=(MetaLaunch.Status.BUILDING, MetaLaunch.Status.BUILD_FAILED))
    if status in MetaLaunch.Status.values:
        launches = launches.filter(status=status)
    page = _page(launches, page)
    _count_creatives([launch.campaign for launch in page.object_list])
    return page


def page_campaign(campaign_id):
    """(campaign, ready task or None, launch or None) for the Meta campaign page, or None.

    A campaign shows when it is ready to build (failed builds included) or
    has been launched. Anything else isn't the media buying team's page.
    """
    launch = MetaLaunch.objects.select_related(
        "ad_account", "campaign__office", "campaign__project", "campaign__developer", "campaign__area",
        "campaign__unit_type", "campaign__audience",
    ).filter(campaign_id=campaign_id).first()
    task = review_selectors.ready_task_for(campaign_id)
    if task is not None:
        return task.campaign, task, launch
    if launch is not None:
        # Any launch stays reachable, a failed one too, so it can be discarded.
        return launch.campaign, None, launch
    return None
