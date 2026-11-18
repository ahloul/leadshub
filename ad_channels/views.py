"""The media buying team's Meta campaigns pages (US-META-02; SRS §4.1, FR-6.10).

- ``MetaCampaignListView`` /back-office/meta/campaigns/: campaigns ready to
  launch (``?tab=ready``) and the ones built on Meta (``?tab=launched``,
  ``?status=``), paginated (``?page=``).

Guards (AGENTS.md, "Permission setup"): the back office and
``launch_meta_campaigns``. The rules are in ``ad_channels.services``.
"""

from django.contrib.auth.mixins import PermissionRequiredMixin
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

    @live.permission_required(LAUNCH)
    @live.event_handler()
    def show(self, tab: str = "", status: str = "", **kwargs):
        """Switch tab or status filter. Sets the URL exactly, so filters can be cleared."""
        self._apply({"tab": tab, "status": status})
        params = {"tab": self.tab}
        if self.status:
            params["status"] = self.status
        self.live_patch(params=params)


class MetaCampaignView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    PageLanguageMixin,
    LiveView,
):
    """/back-office/meta/campaigns/<campaign_id>/: build a ready campaign, or manage its launch."""

    template_name = "ad_channels/campaign.html"
    permission_required = [BACK_OFFICE, LAUNCH]
