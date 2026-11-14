"""The task review queue and the briefs it writes (SRS §4.1, FR-6.1, FR-6.2, FR-6.7; ROADMAP M4).

A campaign sent to review (Run) opens a ``ReviewTask``. A media buyer decides
it within four hours, around the clock. Approving writes a ``Brief`` that the
buyer hands to the AI execution agent; it is never changed afterwards.
Rejecting sends the campaign back to the broker as Needs Changes, and sending
it again opens a new task.
"""

import datetime

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# FR-6.7: every task is decided within four hours of being opened, 24/7.
SLA = datetime.timedelta(hours=4)
# ROADMAP Q13(b), CONFIRM: "near breach" means one hour or less is left.
NEAR_BREACH = datetime.timedelta(hours=1)


def _local(moment):
    return timezone.localtime(moment).strftime("%Y-%m-%d %H:%M") if moment else ""


def format_countdown(delta):
    """``2:41:07`` left, or ``-0:12:05`` past the limit."""
    seconds = int(delta.total_seconds())
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    return f"{sign}{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class Kind(models.TextChoices):
    """What the task reviews. Edits to a Live campaign and broker media uploads join later (ROADMAP M4)."""

    NEW_CAMPAIGN = "new_campaign", _("New campaign")
    CAMPAIGN_RESENT = "campaign_resent", _("Campaign sent again")


class TaskStatus(models.TextChoices):
    OPEN = "open", _("Open")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Changes requested")


class ReviewTask(models.Model):
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.PROTECT,
        related_name="review_tasks",
        verbose_name=_("campaign"),
    )
    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    status = models.CharField(
        _("status"), max_length=10, choices=TaskStatus.choices, default=TaskStatus.OPEN
    )
    created_at = models.DateTimeField(_("opened at"), default=timezone.now)
    due_at = models.DateTimeField(_("due by"))
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_tasks",
        verbose_name=_("assigned to"),
    )
    assigned_at = models.DateTimeField(_("assigned at"), null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("decided by"),
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)
    reasons = models.TextField(_("changes asked for"), blank=True)
    # Set once, when the task first comes near breach and the leads are told.
    escalated_at = models.DateTimeField(_("escalated at"), null=True, blank=True)
    deployed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("deployed by"),
    )
    deployed_at = models.DateTimeField(_("deployed at"), null=True, blank=True)

    class Meta:
        ordering = ["due_at", "pk"]
        verbose_name = _("review task")
        verbose_name_plural = _("review tasks")
        indexes = [models.Index(fields=["status", "due_at"], name="review_task_queue_idx")]
        constraints = [
            # Run can't send the same campaign twice: it leaves AI Building
            # only through a decision, which closes the task.
            models.UniqueConstraint(
                fields=["campaign"],
                condition=models.Q(status="open"),
                name="one_open_review_task_per_campaign",
            ),
        ]

    def __str__(self):
        return self.code

    @property
    def code(self):
        """The reference staff quote, e.g. TSK-5521 (SRS §4.1)."""
        return f"TSK-{self.pk:04d}" if self.pk else "TSK-…"

    @property
    def is_open(self):
        return self.status == TaskStatus.OPEN

    @property
    def awaiting_deployment(self):
        """Approved, and the buyer hasn't recorded the ad set yet (ROADMAP Q12)."""
        return self.status == TaskStatus.APPROVED and self.deployed_at is None

    # For templates: djust renders from serialized values, so the times are
    # given as text in the site's time zone, and the countdown's start.

    @property
    def due_iso(self):
        """When the 4-hour limit runs out, for the browser's countdown (static/js/countdown.js)."""
        return self.due_at.isoformat()

    @property
    def countdown(self):
        """Time left (or how late) as h:mm:ss when the page is drawn; the browser keeps it running."""
        return format_countdown(self.time_left())

    @property
    def sla_state(self):
        """"breached", "near" or "ok" while open; empty once decided."""
        if not self.is_open:
            return ""
        if self.is_breached():
            return "breached"
        return "near" if self.is_near_breach() else "ok"

    @property
    def opened_text(self):
        return _local(self.created_at)

    @property
    def due_text(self):
        return _local(self.due_at)

    @property
    def decided_text(self):
        return _local(self.decided_at)

    @property
    def deployed_text(self):
        return _local(self.deployed_at)

    @property
    def decision_minutes(self):
        if self.decided_at is None:
            return None
        return round((self.decided_at - self.created_at).total_seconds() / 60)

    def time_left(self, now=None):
        return self.due_at - (now or timezone.now())

    def is_near_breach(self, now=None):
        return self.is_open and self.time_left(now) <= NEAR_BREACH

    def is_breached(self, now=None):
        return self.is_open and self.time_left(now) <= datetime.timedelta(0)

    @property
    def met_sla(self):
        """Whether the decision came within four hours (None while open)."""
        if self.decided_at is None:
            return None
        return self.decided_at <= self.due_at


class ImmutableBrief(Exception):
    """A brief is never changed after it is written (ROADMAP M4)."""


class Brief(models.Model):
    """The JSON a media buyer hands to the AI execution agent (FR-6.2, FR-7.6)."""

    task = models.OneToOneField(
        ReviewTask, on_delete=models.PROTECT, related_name="brief", verbose_name=_("task")
    )
    content = models.JSONField(_("content"), encoder=DjangoJSONEncoder)
    generated_at = models.DateTimeField(_("generated at"), default=timezone.now)

    class Meta:
        ordering = ["-generated_at"]
        verbose_name = _("brief")
        verbose_name_plural = _("briefs")

    def __str__(self):
        return self.brief_id

    @property
    def brief_id(self):
        return f"BRIEF-{self.task.code}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableBrief(f"{self.brief_id} was already written and can't change.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableBrief(f"{self.brief_id} is kept for good.")
