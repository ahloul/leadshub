"""Live pages, one home per audience (SRS §2.1).

Every page names the capability it needs in ``permission_required``. djust
checks it on the HTTP request (``PermissionRequiredLiveViewMixin.dispatch``,
which runs before ``TenantMixin`` so other audiences get 403, not 404), on
the WebSocket mount and, with ``LIVEVIEW_CONFIG["reauth_on_event"]``, on every
event. See accounts/roles.py.
"""

from django.contrib import admin
from djust import LiveView
from djust.auth import LoginRequiredLiveViewMixin, PermissionRequiredLiveViewMixin
from djust.tenants import TenantMixin

from accounts.models import BrokerOffice
from accounts.roles import CAPABILITIES

from .live import PageLanguageMixin


class DashboardView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredLiveViewMixin,
    TenantMixin,
    PageLanguageMixin,
    LiveView,
):
    """The signed-in broker's office overview (wallet, tier, score, team)."""

    template_name = "leadshub/dashboard.html"
    permission_required = "accounts.access_client_portal"
    # A broker account not yet added to an office gets 404.
    tenant_required = True

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._office_id = self.tenant.id

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        office = BrokerOffice.objects.get(pk=self._office_id)
        context["office"] = office
        context["members"] = office.memberships.select_related("user").order_by("joined_at")
        return context


class BackOfficeView(
    LoginRequiredLiveViewMixin, PermissionRequiredLiveViewMixin, PageLanguageMixin, LiveView
):
    """Internal staff home: the signed-in person's roles and what they may do."""

    template_name = "leadshub/back_office.html"
    permission_required = "accounts.access_back_office"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._user = request.user
        # Admin sections this person may open (their model permissions).
        self.can_open_admin = bool(admin.site.get_app_list(request))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Built here, not in mount(), so the labels follow the page language.
        context["role_labels"] = [str(label) for label in self._user.role_labels()]
        context["capability_labels"] = [
            str(label)
            for codename, label in CAPABILITIES
            if self._user.has_perm(f"accounts.{codename}")
        ]
        return context


class DeveloperHomeView(
    LoginRequiredLiveViewMixin, PermissionRequiredLiveViewMixin, PageLanguageMixin, LiveView
):
    """Developer read-only home. Shows only the developer's own name for now."""

    template_name = "leadshub/developer_home.html"
    permission_required = "accounts.access_developer_view"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._developer_id = request.user.developer_id

    def get_context_data(self, **kwargs):
        from catalog.models import Developer

        context = super().get_context_data(**kwargs)
        context["developer"] = Developer.objects.filter(pk=self._developer_id).first()
        return context
