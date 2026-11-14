"""The review queue for media buyers and their lead (SRS §4.1; ROADMAP M4).

- ``ReviewQueueView`` /back-office/queue/: open tasks, most urgent first, with
  live countdowns to the 4-hour limit (FR-6.7), the filters (?show=mine,
  unassigned, near_breach), the open and breach counts and the median
  decision time. Below them, approved campaigns still waiting for launch.
- ``ReviewTaskView`` /back-office/queue/<id>/: everything the broker chose,
  with the internal targeting, then assign (the lead), take, approve or ask
  for changes; once approved, the brief and "Mark deployed".
- ``BriefDownloadView`` /back-office/queue/<id>/brief.json: the brief as a file.

Guards (AGENTS.md, "Permission setup"): the pages need the back office and
``review_campaign_tasks``; assigning needs ``assign_campaign_tasks``. The
rules, including who may decide which task, are in ``reviews.services``.
"""

import json

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from djust import LiveView
from djust import decorators as live
from djust.auth import LoginRequiredLiveViewMixin

from leadshub.live import PageLanguageMixin

from . import briefs, selectors, services
from .models import Kind, TaskStatus

BACK_OFFICE = services.BACK_OFFICE
REVIEW = services.REVIEW
ASSIGN = services.ASSIGN


def _param(params, key):
    value = params.get(key, "")
    if isinstance(value, (list, tuple)):
        value = value[-1] if value else ""
    return str(value).strip()


def _messages(error):
    if hasattr(error, "error_dict"):
        return {field: " ".join(errors[0].messages) for field, errors in error.error_dict.items()}
    return " ".join(error.messages)


class ReviewQueueView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    PageLanguageMixin,
    LiveView,
):
    template_name = "reviews/queue.html"
    permission_required = [BACK_OFFICE, REVIEW]
    # Refresh the list and counts while the page is open (djust's server tick).
    tick_interval = 15000

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._apply(request.GET)

    def handle_params(self, params, uri):
        self._apply(params)

    def handle_tick(self):
        """Nothing to change: the next render reads the queue again."""

    def _apply(self, params):
        show = _param(params, "show")
        self.show = show if show in selectors.Show.VALUES else selectors.Show.ALL

    def get_context_data(self, **kwargs):
        user = self.request.user
        now = timezone.now()
        can_assign = user.has_perm(ASSIGN)
        self.tasks = list(selectors.open_tasks(show=self.show, user=user, now=now)[:200])
        self.waiting = list(
            selectors.awaiting_deployment(user=None if can_assign else user)[:50]
        )
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "counts": selectors.counts(user=user, now=now),
                "stats": selectors.decision_stats(now=now),
                "can_assign": can_assign,
                "filters": [
                    (selectors.Show.ALL, _("All open")),
                    (selectors.Show.MINE, _("Mine")),
                    (selectors.Show.UNASSIGNED, _("Unassigned")),
                    (selectors.Show.NEAR_BREACH, _("Near breach")),
                ],
            }
        )
        return context

    @live.permission_required(REVIEW)
    @live.event_handler()
    def set_show(self, value: str = "", **kwargs):
        self._apply({"show": value})
        self.live_patch(params={"show": self.show} if self.show else {})


class ReviewTaskView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    PageLanguageMixin,
    LiveView,
):
    template_name = "reviews/task.html"
    permission_required = [BACK_OFFICE, REVIEW]

    def mount(self, request, pk=0, **kwargs):
        super().mount(request, **kwargs)
        self.task_id = int(pk)
        self.notice = ""
        self.error = ""
        self.field_errors = {}
        self.reasons = ""
        self.network_id = ""
        self.ad_account = ""
        self._load()

    def _load(self):
        task = selectors.task(self.task_id)
        if task is None:
            raise Http404
        self._task = task

    def get_context_data(self, **kwargs):
        user = self.request.user
        task = self._task
        campaign = task.campaign
        self.task = task
        self.campaign = campaign
        self.creatives = list(campaign.creatives.select_related("project", "area").order_by("pk"))
        self.earlier = list(selectors.earlier_tasks(task))
        brief = selectors.brief_of(task)
        can_assign = user.has_perm(ASSIGN)
        may_decide = services.may_decide(user, task)
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "brief_id": brief.brief_id if brief else "",
                "brief_json": json.dumps(briefs.ordered(brief.content), indent=2, ensure_ascii=False)
                if brief else "",
                "brief_url": reverse("review_brief", args=[task.pk]) if brief else "",
                "can_assign": can_assign and task.is_open,
                "reviewers": list(selectors.reviewers()) if can_assign and task.is_open else [],
                "can_take": task.is_open and task.assigned_to_id is None,
                "can_decide": task.is_open and may_decide,
                "assigned_elsewhere": task.is_open and not may_decide,
                "can_deploy": task.awaiting_deployment and may_decide,
                "is_meta": campaign.channel == "meta",
                "is_resent": task.kind == Kind.CAMPAIGN_RESENT,
                "rejected": task.status == TaskStatus.REJECTED,
                "audience": campaign.audience,
                "account": campaign.connected_account if campaign.destination == "own_account" else None,
            }
        )
        return context

    def _run(self, action, success, **kwargs):
        self.notice, self.error, self.field_errors = "", "", {}
        try:
            action(actor=self.request.user, task_id=self.task_id, **kwargs)
        except ValidationError as error:
            messages = _messages(error)
            if isinstance(messages, dict):
                self.field_errors = messages
            else:
                self.error = messages
            return False
        except PermissionDenied:
            self.error = _("This task is assigned to someone else. Ask the lead to reassign it.")
            return False
        finally:
            self._load()
        self.notice = success
        return True

    @live.permission_required(ASSIGN)
    @live.event_handler()
    def assign(self, value: str = "", **kwargs):
        assignee = int(value) if str(value).isdigit() else None
        self._run(services.assign, _("Assignment saved."), assignee_id=assignee)

    @live.permission_required(REVIEW)
    @live.event_handler()
    def take(self, **kwargs):
        self._run(services.take, _("The task is yours. Decide it before the time runs out."))

    @live.permission_required(REVIEW)
    @live.event_handler()
    def approve(self, **kwargs):
        self._run(services.approve, _("Approved. The brief is ready; launch the campaign and mark it deployed."))

    @live.permission_required(REVIEW)
    @live.event_handler()
    def reject(self, reasons: str = "", **kwargs):
        self.reasons = str(reasons)[: services.REASONS_LENGTH + 100]
        if self._run(services.reject, _("Sent back to the broker with your reasons."), reasons=self.reasons):
            self.reasons = ""

    @live.permission_required(REVIEW)
    @live.event_handler()
    def mark_deployed(self, network_id: str = "", ad_account: str = "", **kwargs):
        self.network_id, self.ad_account = str(network_id)[:40], str(ad_account)[:40]
        self._run(
            services.mark_deployed,
            _("Recorded. The campaign is Live."),
            network_id=self.network_id,
            ad_account=self.ad_account,
        )


class BriefDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """The brief as a JSON file, for the AI execution agent (FR-6.2)."""

    # Signed out: to sign-in. Signed in without the capability: 403.
    permission_required = [BACK_OFFICE, REVIEW]

    def get(self, request, pk):
        task = selectors.task(pk)
        brief = selectors.brief_of(task) if task else None
        if brief is None:
            raise Http404
        response = JsonResponse(
            briefs.ordered(brief.content), json_dumps_params={"indent": 2, "ensure_ascii": False}
        )
        response["Content-Disposition"] = f'attachment; filename="{brief.brief_id}.json"'
        return response
