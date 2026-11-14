"""Review queue rules (SRS §4.1, FR-6.1, FR-6.2, FR-6.7; ROADMAP M4).

Every function takes the person acting and refuses what they may not do:

- Deciding, taking a task and recording a deployment need
  ``review_campaign_tasks`` (Media Buyer, Media Buying Lead, Platform Admin).
- Assigning needs ``assign_campaign_tasks`` (the lead and Platform Admin).
- A buyer decides tasks that are theirs or nobody's (deciding one that is
  nobody's makes it theirs). Someone who can assign tasks may decide any.
  ROADMAP Q13 (CONFIRM) lists this default.

Leads are told by email when a task comes within an hour of breaching
(``escalate_near_breach``, a django-q2 schedule every minute: ``leadshub/jobs.py``).
"""

import logging
import re

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from accounts import selectors as account_selectors
from campaigns import services as campaign_services
from leadshub.emails import send_after_commit

from . import briefs
from .models import NEAR_BREACH, SLA, Brief, Kind, ReviewTask, TaskStatus

logger = logging.getLogger(__name__)

BACK_OFFICE = "accounts.access_back_office"
REVIEW = "accounts.review_campaign_tasks"
ASSIGN = "accounts.assign_campaign_tasks"
REASONS_LENGTH = 2000

# A Meta ad set ID or a Google Ads campaign ID: digits only.
_NETWORK_ID = re.compile(r"\d{5,30}")
# A Meta ad account (act_1234…) or a Google Ads customer ID (123-456-7890).
_AD_ACCOUNT = re.compile(r"(act_)?\d[\d-]{3,38}")


def _require(actor, perm):
    if not (actor.has_perm(BACK_OFFICE) and actor.has_perm(perm)):
        raise PermissionDenied


def _locked(task_id):
    task = ReviewTask.objects.select_for_update(of=("self",)).select_related("campaign").filter(
        pk=task_id
    ).first()
    if task is None:
        raise ValidationError(_("This task no longer exists."), code="missing")
    return task


def _open(task_id):
    task = _locked(task_id)
    if not task.is_open:
        raise ValidationError(_("This task was already decided."), code="decided")
    return task


def may_decide(actor, task):
    """Whether ``actor`` may decide (or record the deployment of) ``task``."""
    if not (actor.has_perm(BACK_OFFICE) and actor.has_perm(REVIEW)):
        return False
    return task.assigned_to_id in (None, actor.pk) or actor.has_perm(ASSIGN)


def _require_decider(actor, task):
    _require(actor, REVIEW)
    if not may_decide(actor, task):
        raise PermissionDenied


# Opening (from campaigns.services.run_campaign)


def open_campaign_task(campaign, now=None):
    """Run sent ``campaign`` to review: open its task, due in four hours (FR-6.1, FR-6.7)."""
    now = now or timezone.now()
    kind = (
        Kind.CAMPAIGN_RESENT
        if ReviewTask.objects.filter(campaign_id=campaign.pk).exists()
        else Kind.NEW_CAMPAIGN
    )
    try:
        with transaction.atomic():
            return ReviewTask.objects.create(
                campaign_id=campaign.pk, kind=kind, created_at=now, due_at=now + SLA
            )
    except IntegrityError:
        # One open task per campaign; Run can't reach here twice, so keep the first.
        return ReviewTask.objects.get(campaign_id=campaign.pk, status=TaskStatus.OPEN)


# Assigning


@transaction.atomic
def assign(*, actor, task_id, assignee_id):
    """The lead gives the task to a media buyer, or takes it off them (``assignee_id`` None)."""
    _require(actor, ASSIGN)
    task = _open(task_id)
    assignee = None
    if assignee_id:
        assignee = account_selectors.staff_with(REVIEW).filter(pk=assignee_id).first()
        if assignee is None:
            raise ValidationError(_("Choose someone who can review tasks."), code="assignee")
    task.assigned_to = assignee
    task.assigned_at = timezone.now() if assignee else None
    task.save(update_fields=["assigned_to", "assigned_at"])
    return task


@transaction.atomic
def take(*, actor, task_id):
    """A media buyer takes a task nobody has yet."""
    _require(actor, REVIEW)
    task = _open(task_id)
    if task.assigned_to_id not in (None, actor.pk):
        raise ValidationError(_("Someone else already has this task."), code="taken")
    if task.assigned_to_id is None:
        task.assigned_to = actor
        task.assigned_at = timezone.now()
        task.save(update_fields=["assigned_to", "assigned_at"])
    return task


# Deciding


def _decide(task, actor, status, now):
    task.status = status
    task.decided_by = actor
    task.decided_at = now
    fields = ["status", "decided_by", "decided_at"]
    if task.assigned_to_id is None:
        task.assigned_to = actor
        task.assigned_at = now
        fields += ["assigned_to", "assigned_at"]
    return fields


@transaction.atomic
def approve(*, actor, task_id, now=None):
    """Approve: close the task and write its brief (FR-6.2). The campaign stays AI Building (ROADMAP Q12)."""
    now = now or timezone.now()
    task = _open(task_id)
    _require_decider(actor, task)
    if task.campaign.status != "ai_building":
        raise ValidationError(_("The campaign is no longer waiting for review."), code="campaign")
    task.save(update_fields=_decide(task, actor, TaskStatus.APPROVED, now))
    Brief.objects.create(task=task, content=briefs.build(task, now), generated_at=now)
    return task


@transaction.atomic
def reject(*, actor, task_id, reasons, now=None):
    """Ask for changes: the campaign goes back to the broker as Needs Changes, with the reasons."""
    now = now or timezone.now()
    task = _open(task_id)
    _require_decider(actor, task)
    reasons = (reasons or "").strip()
    if not reasons:
        raise ValidationError(_("Tell the broker what to change."), code="reasons")
    if len(reasons) > REASONS_LENGTH:
        raise ValidationError(
            _("Keep the reasons under %(count)d characters.") % {"count": REASONS_LENGTH},
            code="reasons",
        )
    fields = _decide(task, actor, TaskStatus.REJECTED, now)
    task.reasons = reasons
    task.save(update_fields=[*fields, "reasons"])
    campaign_services.send_back(task.campaign_id, reasons)
    return task


@transaction.atomic
def mark_deployed(*, actor, task_id, network_id, ad_account, now=None):
    """The buyer launched the approved campaign: record its IDs, and it goes Live (ROADMAP Q12)."""
    task = _locked(task_id)
    _require_decider(actor, task)
    if not task.awaiting_deployment:
        raise ValidationError(_("Only an approved task waiting for launch can be marked deployed."),
                              code="state")
    network_id = (network_id or "").strip()
    ad_account = (ad_account or "").strip()
    errors = {}
    if not _NETWORK_ID.fullmatch(network_id):
        errors["network_id"] = _("Enter the ID the network gave, digits only.")
    if not _AD_ACCOUNT.fullmatch(ad_account):
        errors["ad_account"] = _("Enter the ad account, for example act_1234567890 or 123-456-7890.")
    if errors:
        raise ValidationError(errors)
    campaign_services.mark_live(task.campaign_id, adset_id=network_id, ad_account_id=ad_account)
    task.deployed_by = actor
    task.deployed_at = now or timezone.now()
    task.save(update_fields=["deployed_by", "deployed_at"])
    return task


# SLA escalation (FR-6.7; scheduled in leadshub.jobs)


def escalate_near_breach(now=None):
    """Tell the leads about open tasks with an hour or less left. Each task once; returns how many.

    ROADMAP Q13(b) and Q20 (CONFIRM): at one hour left, by email to everyone
    who can assign tasks (the Media Buying Lead by default).
    """
    now = now or timezone.now()
    due = ReviewTask.objects.filter(
        status=TaskStatus.OPEN, escalated_at__isnull=True, due_at__lte=now + NEAR_BREACH
    ).values_list("pk", flat=True)
    escalated = []
    for task_id in list(due):
        # The conditional update makes this safe if two runners overlap.
        if ReviewTask.objects.filter(pk=task_id, escalated_at__isnull=True).update(escalated_at=now):
            escalated.append(task_id)
    if not escalated:
        return 0
    tasks = list(
        ReviewTask.objects.filter(pk__in=escalated)
        .select_related("campaign__office", "assigned_to")
        .order_by("due_at")
    )
    recipients = list(
        account_selectors.staff_with(ASSIGN).exclude(email="").values_list("email", flat=True)
    )
    if not recipients:
        logger.warning("No one can be told that %d review tasks are near breach", len(tasks))
        return len(tasks)
    language = settings.LANGUAGE_CODE
    with translation.override(language):
        link = settings.SITE_BASE_URL + reverse("review_queue") + "?show=near_breach"
        rows = [_escalation_row(task, now) for task in tasks]
    send_after_commit(
        "reviews/email/near_breach",
        {"count": len(tasks), "rows": rows, "link": link},
        recipients,
        language=language,
    )
    return len(tasks)


def _escalation_row(task, now):
    left = task.due_at - now
    minutes = max(0, int(left.total_seconds() // 60))
    if left.total_seconds() <= 0:
        when = gettext_lazy("past due")
    else:
        when = _("%(minutes)d min left") % {"minutes": minutes}
    who = task.assigned_to.get_full_name() or task.assigned_to.get_username() if task.assigned_to else _("nobody")
    return (
        task.code,
        _("%(campaign)s for %(office)s · %(when)s · assigned to %(who)s")
        % {
            "campaign": task.campaign.code,
            "office": task.campaign.office.company_name,
            "when": when,
            "who": who,
        },
    )
