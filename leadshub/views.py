"""Live pages: one home per audience (SRS §2.1) and the office team page.

Every page names the capabilities it needs in ``permission_required``, often
a list: its audience's entry capability and its own. Django's
``PermissionRequiredMixin`` checks them on the HTTP request, before
``TenantMixin`` so other audiences get 403, not 404. djust checks the same
list on the WebSocket mount and, with ``LIVEVIEW_CONFIG["reauth_on_event"]``,
on every event. (djust 1.2.8's ``PermissionRequiredLiveViewMixin`` accepts
only one permission over HTTP.) Handlers that change data also carry
``@permission_required``. See accounts/roles.py.

Views stay thin: business rules live in ``accounts.services`` and reads in
``accounts.selectors``.
"""

from django.contrib import admin
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.utils.translation import gettext as _
from djust import LiveView
# Handler decorators are written live.permission_required(...): inside a view
# class the permission_required class attribute hides a bare decorator name,
# and djust's S009 check doesn't recognise an alias.
from djust import decorators as live
from djust.auth import LoginRequiredLiveViewMixin
from djust.forms import FormMixin
from djust.tenants import TenantMixin

from accounts import selectors, services
from creatives import selectors as creative_selectors
from accounts.forms import InviteMemberForm
from accounts.roles import CAPABILITIES

from .live import PageLanguageMixin


class DashboardView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
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
        context["office"] = selectors.office(self._office_id)
        context["members"] = selectors.office_members(self._office_id).filter(is_active=True)
        context["can_manage_team"] = self.request.user.has_perm(services.MANAGE_TEAM)
        return context


# Sending email can be abused, so these handlers are rate limited (djust's
# abuse control): a burst of 10, then one every 2 seconds.
_EMAIL_RATE = {"rate": 0.5, "burst": 10}


class TeamView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    TenantMixin,
    PageLanguageMixin,
    FormMixin,
    LiveView,
):
    """The office's people (SRS §2.1, §2.2 "Conditional"; ROADMAP M1).

    An owner invites team members, deactivates and reactivates them, and gives
    or takes back campaign access. The rules are in ``accounts.services``,
    which refuses people outside the owner's office.
    """

    template_name = "leadshub/team.html"
    permission_required = ["accounts.access_client_portal", services.MANAGE_TEAM]
    tenant_required = True
    form_class = InviteMemberForm

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._office_id = self.tenant.id
        # Feedback for the latest action on the member list.
        self.notice = ""
        self.notice_is_error = False
        self._refresh()

    def _refresh(self):
        self._members = selectors.office_members(self._office_id)

    def get_context_data(self, **kwargs):
        self.members = self._members
        context = super().get_context_data(**kwargs)
        context["can_give_campaigns"] = self.request.user.has_perm(services.RUN_CAMPAIGNS)
        return context

    def _done(self, message, *, error=False):
        """Show ``message`` above the member list, in place of older feedback."""
        self.notice = message
        self.notice_is_error = error
        self.success_message = ""
        self._refresh()

    @live.permission_required(services.MANAGE_TEAM)
    @live.rate_limit(**_EMAIL_RATE)
    @live.event_handler()
    def submit_form(self, **kwargs):
        """Invite: FormMixin validates, then ``form_valid`` sends the invitation."""
        super().submit_form(**kwargs)

    def form_valid(self, form):
        member = services.invite_member(
            actor=self.request.user, request=self.request, **form.cleaned_data
        )
        self.reset_form()
        self.success_message = _("Invitation sent to %(email)s.") % {"email": member.email}
        self.notice = ""
        self._refresh()

    def form_invalid(self, form):
        # Otherwise the last invitation's confirmation stays under the errors.
        self.success_message = ""

    @live.permission_required(services.MANAGE_TEAM)
    @live.rate_limit(**_EMAIL_RATE)
    @live.event_handler()
    def resend_invitation(self, member_id: int = 0, **kwargs):
        try:
            member = services.resend_invitation(
                actor=self.request.user, member_id=member_id, request=self.request
            )
        except ValidationError as error:
            self._done(" ".join(error.messages), error=True)
            return
        self._done(_("A new invitation was sent to %(email)s.") % {"email": member.email})

    @live.permission_required(services.MANAGE_TEAM)
    @live.event_handler()
    def deactivate_member(self, member_id: int = 0, **kwargs):
        member = services.set_member_active(
            actor=self.request.user, member_id=member_id, active=False
        )
        self._done(_("%(name)s can no longer sign in.") % {"name": services.display_name(member)})

    @live.permission_required(services.MANAGE_TEAM)
    @live.event_handler()
    def reactivate_member(self, member_id: int = 0, **kwargs):
        member = services.set_member_active(
            actor=self.request.user, member_id=member_id, active=True
        )
        self._done(_("%(name)s can sign in again.") % {"name": services.display_name(member)})

    # Giving campaign access also needs it: an owner gives only what they have.
    @live.permission_required([services.MANAGE_TEAM, services.RUN_CAMPAIGNS])
    @live.event_handler()
    def allow_campaigns(self, member_id: int = 0, **kwargs):
        member = services.set_campaign_access(
            actor=self.request.user, member_id=member_id, allowed=True
        )
        self._done(
            _("%(name)s can now create and run campaigns.")
            % {"name": services.display_name(member)}
        )

    @live.permission_required(services.MANAGE_TEAM)
    @live.event_handler()
    def revoke_campaigns(self, member_id: int = 0, **kwargs):
        member = services.set_campaign_access(
            actor=self.request.user, member_id=member_id, allowed=False
        )
        self._done(
            _("%(name)s can no longer create or run campaigns.")
            % {"name": services.display_name(member)}
        )


def _param(params, key):
    """One query parameter as text, from request.GET or djust's handle_params dict."""
    value = params.get(key, "")
    if isinstance(value, (list, tuple)):
        value = value[-1] if value else ""
    return str(value).strip()


def _positive_int(text, default=0):
    return int(text) if text.isdigit() and int(text) > 0 else default


class LibraryView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    PageLanguageMixin,
    LiveView,
):
    """The creative library for brokers (SRS §3.2, FR-5.2, FR-5.7; ROADMAP M2).

    Read-only: published assets whose offer is still on. The filters are in the
    URL (?area=&project=&type=&language=&page=), so a filtered list can be
    bookmarked and the back button works.
    """

    template_name = "leadshub/library.html"
    permission_required = "accounts.access_client_portal"
    page_size = 24

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._apply(request.GET)

    def handle_params(self, params, uri):
        self._apply(params)

    def _apply(self, params):
        """Read the filters from URL parameters, ignoring values that aren't valid."""
        self.area = _positive_int(_param(params, "area"))
        self.project = _positive_int(_param(params, "project"))
        asset_type = _param(params, "type")
        self.asset_type = asset_type if asset_type in creative_selectors.AssetType.values else ""
        language = _param(params, "language")
        self.language = language if language in creative_selectors.Language.values else ""
        self.page = _positive_int(_param(params, "page"), default=1)
        self._refresh()

    def _refresh(self):
        creatives = creative_selectors.library(
            area_id=self.area or None,
            project_id=self.project or None,
            asset_type=self.asset_type,
            language=self.language,
        )
        self._page = Paginator(creatives, self.page_size).get_page(self.page)
        self.page = self._page.number

    def _params(self, **changes):
        params = {
            "area": self.area or "",
            "project": self.project or "",
            "type": self.asset_type,
            "language": self.language,
            "page": self.page if self.page > 1 else "",
        }
        params.update(changes)
        if str(params["page"]) == "1":
            params["page"] = ""  # the first page needs no parameter
        return {key: str(value) for key, value in params.items() if value}

    def _page_query(self, number):
        """The current filters and page ``number``, which is always stated (see clear_filters)."""
        from urllib.parse import urlencode

        return urlencode({**self._params(), "page": str(number)})

    def get_context_data(self, **kwargs):
        self.creatives = self._page.object_list
        self.areas = creative_selectors.library_areas()
        self.projects = creative_selectors.library_projects(area_id=self.area or None)
        context = super().get_context_data(**kwargs)
        page = self._page
        context.update(
            {
                "type_choices": creative_selectors.AssetType.choices,
                "language_choices": creative_selectors.Language.choices,
                "filtered": bool(self.area or self.project or self.asset_type or self.language),
                "page_count": page.paginator.num_pages,
                "previous_query": self._page_query(page.previous_page_number())
                if page.has_previous() else "",
                "next_query": self._page_query(page.next_page_number()) if page.has_next() else "",
            }
        )
        return context

    @live.permission_required("accounts.access_client_portal")
    @live.event_handler()
    def set_filter(self, field: str = "", value: str = "", **kwargs):
        """A filter changed: show the first page of the new list and update the URL."""
        names = {"area": "area", "project": "project", "type": "type", "language": "language"}
        if field not in names:
            return
        params = self._params(**{names[field]: value, "page": ""})
        if field == "area":
            params.pop("project", None)  # the project list depends on the area
        self._apply(params)
        self.live_patch(params=self._params())

    # Clearing filters goes through a handler, not a dj-patch link: djust
    # 1.2.8's dj-patch only adds parameters to the URL and can't remove one,
    # while live_patch sets them exactly. (Page links can use dj-patch: they
    # always state the page.)
    @live.permission_required("accounts.access_client_portal")
    @live.event_handler()
    def clear_filters(self, **kwargs):
        self._apply({})
        self.live_patch(params={})


class BackOfficeView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    PageLanguageMixin,
    LiveView,
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
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    PageLanguageMixin,
    LiveView,
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
