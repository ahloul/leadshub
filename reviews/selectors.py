"""Reads of the review queue (SRS §4.1, FR-6.7)."""

import datetime
import statistics

from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from accounts import selectors as account_selectors

from .models import NEAR_BREACH, Brief, ReviewTask, TaskStatus

REVIEW = "accounts.review_campaign_tasks"


class Show:
    """The queue's filters (?show= in the URL)."""

    ALL = ""
    MINE = "mine"
    UNASSIGNED = "unassigned"
    NEAR_BREACH = "near_breach"
    VALUES = (ALL, MINE, UNASSIGNED, NEAR_BREACH)


def _with_campaign(tasks):
    return tasks.select_related(
        "campaign__office", "campaign__project", "campaign__developer", "campaign__area",
        "campaign__unit_type", "assigned_to",
    )


def open_tasks(*, show=Show.ALL, user=None, now=None):
    """Open tasks, most urgent first.

    ROADMAP Q13(c), CONFIRM: offices of Silver tier and higher are listed
    ahead of Bronze ones; the four-hour limit is the same for everyone.
    """
    now = now or timezone.now()
    tasks = _with_campaign(ReviewTask.objects.filter(status=TaskStatus.OPEN))
    if show == Show.MINE:
        tasks = tasks.filter(assigned_to=user)
    elif show == Show.UNASSIGNED:
        tasks = tasks.filter(assigned_to__isnull=True)
    elif show == Show.NEAR_BREACH:
        tasks = tasks.filter(due_at__lte=now + NEAR_BREACH)
    return tasks.annotate(
        tier_first=Case(When(campaign__office__spend_tier="bronze", then=Value(1)), default=Value(0),
                        output_field=IntegerField()),
    ).order_by("tier_first", "due_at", "pk")


def awaiting_deployment(*, user=None):
    """Approved tasks whose campaign isn't on the network yet (ROADMAP Q12)."""
    tasks = _with_campaign(
        ReviewTask.objects.filter(status=TaskStatus.APPROVED, deployed_at__isnull=True)
    )
    if user is not None:
        tasks = tasks.filter(assigned_to=user)
    return tasks.order_by("decided_at", "pk")


def counts(*, user=None, now=None):
    """The figures at the top of the queue."""
    now = now or timezone.now()
    open_ = ReviewTask.objects.filter(status=TaskStatus.OPEN)
    return {
        "open": open_.count(),
        "mine": open_.filter(assigned_to=user).count() if user is not None else 0,
        "unassigned": open_.filter(assigned_to__isnull=True).count(),
        "near_breach": open_.filter(due_at__gt=now, due_at__lte=now + NEAR_BREACH).count(),
        "breached": open_.filter(due_at__lte=now).count(),
    }


def decision_stats(*, days=30, now=None):
    """Median minutes from opening to decision, and the share decided within the SLA (FR-6.7)."""
    now = now or timezone.now()
    decided = ReviewTask.objects.filter(
        decided_at__isnull=False, decided_at__gte=now - datetime.timedelta(days=days)
    ).values_list("created_at", "decided_at", "due_at")
    rows = list(decided[:5000])
    if not rows:
        return {"decided": 0, "median_minutes": None, "within_sla_percent": None}
    minutes = [(decided_at - created).total_seconds() / 60 for created, decided_at, _due in rows]
    within = sum(1 for _created, decided_at, due in rows if decided_at <= due)
    return {
        "decided": len(rows),
        "median_minutes": round(statistics.median(minutes)),
        "within_sla_percent": round(100 * within / len(rows)),
    }


def task(task_id):
    """One task with its campaign and everything the review page shows, or None."""
    return (
        ReviewTask.objects.select_related(
            "campaign__office", "campaign__project__developer", "campaign__project__area",
            "campaign__developer", "campaign__area", "campaign__unit_type", "campaign__audience",
            "campaign__connected_account", "assigned_to", "decided_by", "deployed_by",
        )
        .filter(pk=task_id)
        .first()
    )


def earlier_tasks(task):
    """The campaign's earlier tasks, newest first, with their decisions."""
    return (
        ReviewTask.objects.filter(campaign_id=task.campaign_id)
        .exclude(pk=task.pk)
        .select_related("decided_by")
        .order_by("-created_at", "-pk")
    )


def brief_of(task):
    return Brief.objects.filter(task=task).first()


def reviewers():
    """Who a task can be assigned to: active internal staff who may decide tasks."""
    return account_selectors.staff_with(REVIEW)
