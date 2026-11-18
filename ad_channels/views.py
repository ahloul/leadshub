"""The media buying team's Meta campaigns pages (US-META-02; SRS §4.1, FR-6.10).

- ``MetaCampaignListView`` /back-office/meta/campaigns/: campaigns ready to
  launch (``?tab=ready``) and the ones built on Meta (``?tab=launched``,
  ``?status=``), paginated (``?page=``).

Guards (AGENTS.md, "Permission setup"): the back office and
``launch_meta_campaigns``. The rules are in ``ad_channels.services``.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.utils import translation
from django.utils.translation import gettext as _
from djust import LiveView
from djust import decorators as live
from djust.auth import LoginRequiredLiveViewMixin

from leadshub.live import PageLanguageMixin

from . import selectors, services
from .models import MetaLaunch

BACK_OFFICE = services.BACK_OFFICE
LAUNCH = services.LAUNCH

READY = "ready"
LAUNCHED = "launched"


def _param(params, key):
    value = params.get(key, "")
    if isinstance(value, (list, tuple)):
        value = value[-1] if value else ""
    return str(value).strip()


def _page_number(value):
    return int(value) if value.isdigit() and int(value) > 0 else 1


class MetaCampaignListView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    PageLanguageMixin,
    LiveView,
):
    template_name = "ad_channels/list.html"
    permission_required = [BACK_OFFICE, LAUNCH]

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._apply(request.GET)

    def handle_params(self, params, uri):
        self._apply(params)

    def _apply(self, params):
        tab = _param(params, "tab")
        self.tab = tab if tab in (READY, LAUNCHED) else READY
        status = _param(params, "status")
        self.status = status if status in MetaLaunch.Status.values else ""
        self.page_number = _page_number(_param(params, "page"))

    def get_context_data(self, **kwargs):
        if self.tab == LAUNCHED:
            page = selectors.launched(self.status, self.page_number)
        else:
            page = selectors.ready(self.page_number)
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "rows": list(page.object_list),
                "page_obj": page,
                "tabs": [(READY, _("Ready to launch")), (LAUNCHED, _("On Meta"))],
                "statuses": [("", _("All")), *[(v, l) for v, l in MetaLaunch.Status.choices
                                                 if v not in ("building", "build_failed")]],
            }
        )
        return context


class MetaCampaignView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    PageLanguageMixin,
    LiveView,
):
    """/back-office/meta/campaigns/<campaign_id>/: build a ready campaign, or manage its launch.

    Choosing an account loads its Business Manager's pages from Meta, and
    choosing a page loads its lead forms; nothing else calls Meta on render.
    Build runs after the click (``start_async``) so the page shows
    "Building on Meta…" while it waits.
    """

    template_name = "ad_channels/campaign.html"
    permission_required = [BACK_OFFICE, LAUNCH]

    def mount(self, request, pk=0, **kwargs):
        super().mount(request, **kwargs)
        self.campaign_id = int(pk)
        self.ad_account_id = None
        self.page_id = ""
        self.lead_form_id = ""
        self.building = False
        self.error = ""
        self.notice = ""
        self._load()
        launch = self._launch
        if launch is not None and not self._launched:
            # A failed build: offer the same choices again for one-click resume.
            self.ad_account_id, self.page_id, self.lead_form_id = (
                launch.ad_account_id, launch.page_id, launch.lead_form_id
            )
        self._options = None
        if not self._launched:
            self._load_options()

    def _load(self):
        found = selectors.page_campaign(self.campaign_id)
        if found is None:
            raise Http404
        self._campaign, self._task, self._launch = found
        self._launched = self._launch is not None and self._launch.status not in (
            MetaLaunch.Status.BUILDING, MetaLaunch.Status.BUILD_FAILED,
        )

    def _load_options(self):
        try:
            self._options = services.build_options(
                actor=self.request.user, campaign_id=self.campaign_id,
                ad_account_id=self.ad_account_id, page_id=self.page_id,
            )
        except services.LaunchError as error:
            self._options = None
            self.error = str(error)
        except PermissionDenied:
            self._options = None  # The page guard refuses this person anyway.

    def get_context_data(self, **kwargs):
        campaign = self._campaign
        options = self._options
        launch = self._launch
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "campaign": campaign,
                "creatives": list(campaign.creatives.order_by("pk")),
                "audience": campaign.audience,
                "launch": launch,
                "launched": self._launched,
                "failed": launch is not None and launch.status == MetaLaunch.Status.BUILD_FAILED,
                "accounts": [(a.pk, f"{a.name} ({a.meta_account_id})") for a in options.accounts] if options else [],
                "pages": [(p.id, p.name) for p in options.pages] if options else [],
                "forms": [(f.id, f.name) for f in options.forms] if options else [],
                "problems": [str(p) for p in options.problems] if options else [],
                "can_build": bool(self.ad_account_id and self.page_id and self.lead_form_id) and not self.building,
                "ads": launch.ads if launch else [],
            }
        )
        return context

    # Choosing where to build

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def choose_account(self, value: str = "", **kwargs):
        self.ad_account_id = int(value) if str(value).isdigit() else None
        self.page_id, self.lead_form_id, self.error = "", "", ""
        self._load_options()

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def choose_page(self, value: str = "", **kwargs):
        self.page_id, self.lead_form_id, self.error = str(value)[:64], "", ""
        self._load_options()

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def choose_form(self, value: str = "", **kwargs):
        self.lead_form_id = str(value)[:64]

    # Building

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def build_on_meta(self, **kwargs):
        if self.building:
            return
        self.building, self.error, self.notice = True, "", ""
        self.start_async(self._build, name="build")

    def _build(self):
        """Runs after the click; the page re-renders when it returns."""
        with translation.override(self._language_for_render()):
            try:
                services.build(
                    actor=self.request.user, campaign_id=self.campaign_id, ad_account_id=self.ad_account_id,
                    page_id=self.page_id, lead_form_id=self.lead_form_id,
                )
                self.notice = _("Built on Meta and switched on. The campaign is Live.")
            except services.LaunchError as error:
                self.error = str(error)
            except PermissionDenied:
                self.error = _("You can no longer launch campaigns on Meta.")
            finally:
                self.building = False
                self._load()

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def discard(self, **kwargs):
        if self._run(services.discard_build, _("The build was deleted from Meta. Start again when ready.")):
            self._load_options()

    # Managing

    def _run(self, action, success, **kwargs):
        self.error, self.notice = "", ""
        try:
            action(actor=self.request.user, campaign_id=self.campaign_id, **kwargs)
        except services.LaunchError as error:
            self.error = str(error)
            return False
        finally:
            self._load()
        self.notice = success
        return True

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def pause(self, **kwargs):
        self._run(services.pause, _("Paused on Meta. The broker still sees Live."))

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def resume(self, **kwargs):
        self._run(services.resume, _("Running on Meta again."))

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def save_budget(self, budget_usd: str = "", ends_on: str = "", **kwargs):
        try:
            amount = Decimal(str(budget_usd).strip())
            end = datetime.date.fromisoformat(ends_on) if str(ends_on).strip() else None
        except (InvalidOperation, ValueError):
            self.error, self.notice = _("Enter the budget in USD and the end date as a date."), ""
            return
        if not amount.is_finite():
            self.error, self.notice = _("Enter the budget in USD and the end date as a date."), ""
            return
        self._run(services.change_budget, _("Budget saved on Meta."), budget_usd=amount, ends_on=end)

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def stop(self, **kwargs):
        self._run(services.stop, _("Stopped on Meta. The campaign has ended."))

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def refresh(self, **kwargs):
        self._run(services.refresh, _("Up to date with Meta."))
