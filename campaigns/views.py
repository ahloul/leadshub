"""Campaign pages and the office's Pages page, for brokers (SRS §3.1; ROADMAP M3).

- ``CampaignListView`` /portal/campaigns/: the office's campaigns, with the
  status filter and page in the URL.
- ``CampaignDetailView`` /portal/campaigns/<id>/: one campaign, with Run and
  Discard while it can change.
- The builder, one live page per step, so the step is in the URL and each
  step saves the draft: /portal/campaigns/new/ (type) for a new campaign,
  then /portal/campaigns/<id>/<step>/. Its layout follows the product
  prototype: type, target (with channel and destination), creative,
  audience (with the optimization goal), budget and run.
- ``ConnectedAccountsView`` /portal/pages/: the office's own Instagram,
  Facebook and Google accounts (FR-4.4).

Guards (accounts/roles.py, AGENTS.md "Permission setup"): every page needs
the client portal and an office (``TenantMixin``). Team members without
``run_campaigns`` can look at campaigns but not change them (ROADMAP Q7), so
the builder pages and every campaign handler also need ``run_campaigns``.
The Pages page needs ``manage_connected_accounts``. The rules themselves,
including the office check, are in ``campaigns.services``.

djust's ``WizardMixin`` isn't used: it keeps the answers in the live view
rather than in a saved draft, and the step isn't in the URL.
"""

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils.translation import gettext as _
from djust import LiveView
# Handler decorators are written live.permission_required(...): inside a view
# class the permission_required class attribute hides a bare decorator name.
from djust import decorators as live
from djust.auth import LoginRequiredLiveViewMixin
from djust.forms import FormMixin
from djust.tenants import TenantMixin

from leadshub.live import PageLanguageMixin

from . import integrations, selectors, services
from .forms import ACCOUNT_PREFIX, AccountForm, AudienceForm, BudgetForm, TargetForm, TypeForm, identity_of
from .models import (
    CHANNEL_NAMES,
    GOAL_NAMES,
    DESTINATION_CHANNELS,
    BudgetKind,
    Channel,
    Destination,
    Goal,
    Platform,
    Step,
    TargetKind,
)

VIEW = services.VIEW_CAMPAIGNS
RUN = services.RUN_CAMPAIGNS
MANAGE_ACCOUNTS = services.MANAGE_ACCOUNTS


def _param(params, key):
    """One query parameter as text, from request.GET or djust's handle_params dict."""
    value = params.get(key, "")
    if isinstance(value, (list, tuple)):
        value = value[-1] if value else ""
    return str(value).strip()


def _positive_int(text, default=0):
    return int(text) if text.isdigit() and int(text) > 0 else default


def _messages(error):
    return " ".join(error.messages)


def _text(value):
    """A form value as text, for comparing with choice values (ids arrive as ints or strings)."""
    if value is None or value is False:
        return ""
    return str(value)


def _checked(value):
    return value is True or str(value).lower() in ("true", "on", "1")


def step_url(campaign_id, step):
    if not campaign_id:
        return reverse("campaign_new")
    return reverse(f"campaign_{Step(step).value}", args=[campaign_id])


def step_links(campaign, current):
    """The builder's step list: label, number, link, done and current flags."""
    count = campaign.creatives.count() if campaign is not None else None
    links = []
    for number, step in enumerate(Step, start=1):
        links.append(
            {
                "number": number,
                "label": str(step.label),
                "url": step_url(campaign.pk, step) if campaign is not None else "",
                "done": campaign is not None and campaign.step_done(step, creative_count=count),
                "current": step == current,
            }
        )
    return links


def summary_rows(campaign):
    """What's chosen so far, for the summary beside the builder and on the campaign page."""
    if campaign is None:
        return []
    creatives = campaign.creatives.count()
    budget = ""
    if campaign.budget_usd:
        budget = f"{campaign.get_budget_kind_display()}: {campaign.budget_usd:,.0f} USD"
    appears = campaign.get_destination_display()
    if campaign.destination == Destination.OWN_ACCOUNT and campaign.connected_account_id:
        appears = str(campaign.connected_account)
    rows = [
        (_("Type"), campaign.get_target_kind_display()),
        (_("Target"), campaign.target_label if campaign.step_done(Step.TARGET) else ""),
        (_("Channel"), campaign.get_channel_display()),
        (_("Ads appear on"), appears),
        (_("Creatives"), str(creatives) if creatives else ""),
        (_("Audience"), campaign.audience.display_name if campaign.audience_id else ""),
        (_("Optimization goal"), campaign.get_goal_display()),
        (_("Budget"), budget),
    ]
    return [{"label": label, "value": value} for label, value in rows]


class CampaignListView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    TenantMixin,
    PageLanguageMixin,
    LiveView,
):
    """The office's campaigns, newest first, laid out like the product prototype's list.

    The search, the filters and the page are in the URL
    (?q=&status=&channel=&goal=&developer=&project=&page=), so a filtered list
    can be bookmarked and the back button works. Values that aren't valid are
    ignored.
    """

    template_name = "campaigns/list.html"
    permission_required = VIEW
    tenant_required = True
    page_size = 20
    search_length = 100

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self._apply(request.GET)

    def handle_params(self, params, uri):
        self._apply(params)

    def _apply(self, params):
        self.q = _param(params, "q")[: self.search_length]
        status = _param(params, "status")
        self.status = status if status in selectors.Status.values else ""
        channel = _param(params, "channel")
        self.channel = channel if channel in Channel.values else ""
        goal = _param(params, "goal")
        self.goal = goal if goal in Goal.values else ""
        self.developer = _positive_int(_param(params, "developer"))
        self.project = _positive_int(_param(params, "project"))
        self.page = _positive_int(_param(params, "page"), default=1)
        campaigns = services.campaigns_for(
            self.request.user,
            status=self.status,
            channel=self.channel,
            goal=self.goal,
            developer_id=self.developer or None,
            project_id=self.project or None,
            search=self.q,
        )
        self._page = Paginator(campaigns, self.page_size).get_page(self.page)
        self.page = self._page.number

    def _params(self, **changes):
        params = {
            "q": self.q,
            "status": self.status,
            "channel": self.channel,
            "goal": self.goal,
            "developer": self.developer or "",
            "project": self.project or "",
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
        self.campaigns = self._page.object_list
        self.developers, self.projects = services.campaign_filter_choices(
            self.request.user, developer_id=self.developer or None
        )
        context = super().get_context_data(**kwargs)
        page = self._page
        context.update(
            {
                # Built here, not in mount(), so the labels follow the page language.
                "status_choices": selectors.Status.choices,
                "channel_choices": [(value, str(CHANNEL_NAMES[value])) for value in Channel],
                "goal_choices": [(value, str(GOAL_NAMES[value])) for value in Goal],
                "can_run": self.request.user.has_perm(RUN),
                "filtered": bool(
                    self.q or self.status or self.channel or self.goal or self.developer or self.project
                ),
                "total": page.paginator.count,
                "page_count": page.paginator.num_pages,
                "previous_query": self._page_query(page.previous_page_number())
                if page.has_previous() else "",
                "next_query": self._page_query(page.next_page_number()) if page.has_next() else "",
            }
        )
        return context

    _FILTERS = ("status", "channel", "goal", "developer", "project")

    @live.permission_required(VIEW)
    @live.event_handler()
    def set_filter(self, field: str = "", value: str = "", **kwargs):
        """A filter changed: the first page of the new list, and the URL follows."""
        if field not in self._FILTERS:
            return
        params = self._params(**{field: value, "page": ""})
        if field == "developer":
            params.pop("project", None)  # the project list depends on the developer
        self._apply(params)
        self.live_patch(params=self._params())

    @live.permission_required(VIEW)
    @live.event_handler()
    def search(self, value: str = "", **kwargs):
        """The search box changed (debounced in the template)."""
        self._apply(self._params(q=str(value).strip(), page=""))
        self.live_patch(params=self._params())

    # Clearing goes through a handler, not a dj-patch link: djust 1.2.8's
    # dj-patch only adds parameters to the URL, while live_patch sets them
    # exactly (AGENTS.md, "URL state").
    @live.permission_required(VIEW)
    @live.event_handler()
    def clear_filters(self, **kwargs):
        self._apply({})
        self.live_patch(params={})


class CampaignDetailView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    TenantMixin,
    PageLanguageMixin,
    LiveView,
):
    """One campaign: what was chosen, what's missing, and Run (SRS §3.1)."""

    template_name = "campaigns/detail.html"
    permission_required = VIEW
    tenant_required = True

    def mount(self, request, pk=0, **kwargs):
        super().mount(request, **kwargs)
        self.campaign_id = int(pk)
        self.notice = ""
        self.notice_is_error = False
        self._load()

    def _load(self):
        # Another office's campaign raises PermissionDenied (403).
        self._campaign = services.campaign_for(self.request.user, self.campaign_id)

    def get_context_data(self, **kwargs):
        campaign = self._campaign
        self.campaign = campaign
        self.creatives = selectors.chosen_creatives(campaign)
        context = super().get_context_data(**kwargs)
        can_change = campaign.is_editable and self.request.user.has_perm(RUN)
        context.update(
            {
                "summary": summary_rows(campaign),
                "can_change": can_change,
                "can_discard": can_change and campaign.status == selectors.Status.DRAFT,
                "run_errors": [str(e) for e in services.run_errors(campaign)] if can_change else [],
                "steps": step_links(campaign, current=None) if can_change else [],
                "change_reasons": campaign.change_reasons
                if campaign.status == selectors.Status.NEEDS_CHANGES
                else "",
                "account_unverified": bool(
                    campaign.connected_account_id
                    and campaign.connected_account.status == selectors.AccountStatus.UNVERIFIED
                ),
            }
        )
        return context

    @live.permission_required([VIEW, RUN])
    @live.event_handler()
    def run_campaign(self, **kwargs):
        try:
            campaign = services.run_campaign(actor=self.request.user, campaign_id=self.campaign_id)
        except ValidationError as error:
            self.notice, self.notice_is_error = _messages(error), True
        else:
            self.notice, self.notice_is_error = (
                _("%(code)s is in the review queue. Our team usually reviews it within 4 hours.")
                % {"code": campaign.code},
                False,
            )
        self._load()

    @live.permission_required([VIEW, RUN])
    @live.event_handler()
    def discard_draft(self, **kwargs):
        try:
            services.discard_draft(actor=self.request.user, campaign_id=self.campaign_id)
        except ValidationError as error:
            self.notice, self.notice_is_error = _messages(error), True
            self._load()
            return
        self.live_redirect(reverse("campaigns"))


class _StepPage(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    TenantMixin,
    PageLanguageMixin,
    LiveView,
):
    """What every builder step shares: the guards, loading the draft, the step list, the summary."""

    abstract = True  # djust's checks skip it; each step sets template_name
    permission_required = [VIEW, RUN]
    tenant_required = True
    step = Step.TYPE

    def mount(self, request, pk=0, **kwargs):
        self.campaign_id = int(pk or 0)
        self.locked_message = ""
        self.notice = ""
        self._campaign = self._load(request.user) if self.campaign_id else None
        # FormMixin (in the form steps) binds the form to this instance.
        self._model_instance = self._campaign if not self.locked_message else None
        super().mount(request, **kwargs)

    def _load(self, user):
        try:
            return services.campaign_to_change(user, self.campaign_id)
        except ValidationError as error:
            # Sent to review or live: shown, not editable.
            self.locked_message = _messages(error)
            return services.campaign_for(user, self.campaign_id)

    def _reload(self):
        self._campaign = services.campaign_for(self.request.user, self.campaign_id)

    def next_url(self):
        steps = list(Step)
        index = steps.index(self.step)
        if index + 1 < len(steps):
            return step_url(self.campaign_id, steps[index + 1])
        return reverse("campaign", args=[self.campaign_id])

    def previous_url(self):
        steps = list(Step)
        index = steps.index(self.step)
        return step_url(self.campaign_id, steps[index - 1]) if index else ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        campaign = self._campaign
        context.update(
            {
                "steps": step_links(campaign, current=self.step),
                "step_number": list(Step).index(self.step) + 1,
                "step_label": str(self.step.label),
                "campaign_code": campaign.code if campaign else "",
                "campaign_url": reverse("campaign", args=[campaign.pk]) if campaign else "",
                "previous_url": self.previous_url() if campaign else "",
                "last_step": self.step == list(Step)[-1],
                "summary": summary_rows(campaign),
                "target_missing": bool(campaign and not campaign.step_done(Step.TARGET)),
                "target_url": step_url(self.campaign_id, Step.TARGET) if campaign else "",
            }
        )
        return context


class _FormStepPage(FormMixin, _StepPage):
    """A step with a form. ``save(data)`` hands the cleaned data to the service."""

    abstract = True

    def save(self, data):
        raise NotImplementedError

    @live.permission_required(RUN)
    @live.event_handler()
    def submit_form(self, **kwargs):
        if self.locked_message:
            return
        self._action = kwargs.pop("_target", "") or ""
        super().submit_form(**kwargs)

    def form_valid(self, form):
        try:
            campaign = self.save(form.cleaned_data)
        except ValidationError as error:
            self.error_message = _messages(error)
            return
        self.campaign_id = campaign.pk
        self.live_redirect(self.next_url())

    def form_invalid(self, form):
        self.error_message = ""

    def cards(self, field, choices, **extra):
        """Choice cards for ``field``: [{name, value, label, selected, ...}], selected from the live form."""
        current = _text(self.form_data.get(field))
        return [
            {"name": field, "value": str(value), "label": str(label), "selected": str(value) == current,
             **extra.get(str(value), {})}
            for value, label in choices
        ]


class TypeStepView(_FormStepPage):
    """Step 1 (FR-4.1): a specific project or a general campaign. Without an id it starts a draft."""

    template_name = "campaigns/step_type.html"
    form_class = TypeForm
    step = Step.TYPE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["kind_cards"] = self.cards(
            "target_kind",
            TargetKind.choices,
            **{
                TargetKind.PROJECT.value: {
                    "description": _("Advertise one project from one developer. You choose the developer and the project.")
                },
                TargetKind.CATEGORY.value: {
                    "description": _(
                        "Advertise a category across projects. You choose any two of area, developer and unit type."
                    )
                },
            },
        )
        return context

    def save(self, data):
        if self.campaign_id:
            return services.save_type(
                actor=self.request.user, campaign_id=self.campaign_id, target_kind=data["target_kind"]
            )
        return services.start_campaign(actor=self.request.user, target_kind=data["target_kind"])


class TargetStepView(_FormStepPage):
    """Step 2 (FR-4.1, FR-4.3, FR-4.4): what to advertise, the channel, and whose page."""

    template_name = "campaigns/step_target.html"
    form_class = TargetForm
    step = Step.TARGET

    def mount(self, request, pk=0, **kwargs):
        super().mount(request, pk=pk, **kwargs)
        if self._campaign is not None and not self.locked_message:
            # Not a model field, so FormMixin can't fill it from the campaign.
            self.form_data["identity"] = identity_of(self._campaign)

    def _project(self):
        from catalog import selectors as catalog_selectors

        project_id = _text(self.form_data.get("project"))
        if not project_id.isdigit():
            return None
        return catalog_selectors.projects(ids=[int(project_id)]).first()

    def _chips(self, field, items, any_label):
        current = _text(self.form_data.get(field))
        chips = [{"name": field, "value": "", "label": any_label, "selected": current == ""}]
        chips += [
            {"name": field, "value": str(item.pk), "label": item.display_name, "selected": str(item.pk) == current}
            for item in items
        ]
        return chips

    def _identity_cards(self, channel):
        current = _text(self.form_data.get("identity"))
        cards = []
        for destination, label, description, badge in (
            (Destination.DARK_PAGE, Destination.DARK_PAGE.label,
             _("Runs under the platform. Shares one campaign with others on the same creative, "
               "for a better cost per lead."), _("Recommended · pooled")),
            (Destination.LANDING_PAGE, Destination.LANDING_PAGE.label,
             _("A ready page from the library. A hidden campaign ID routes each lead to you."), ""),
        ):
            if channel in DESTINATION_CHANNELS[destination]:
                cards.append({"name": "identity", "value": destination.value, "label": str(label),
                              "description": str(description), "badge": str(badge),
                              "selected": current == destination.value})
        for account in selectors.office_accounts(self._campaign.office_id, channel=channel, usable_only=True):
            value = f"{ACCOUNT_PREFIX}{account.pk}"
            cards.append({
                "name": "identity", "value": value,
                "label": _("Your %(platform)s %(handle)s") % {
                    "platform": account.get_platform_display(), "handle": account.isolated_handle},
                "description": _("The ads carry your identity. Never pooled: every lead comes straight to you."),
                "warning": str(account.get_status_display()) if account.status == selectors.AccountStatus.UNVERIFIED else "",
                "selected": current == value,
            })
        return cards

    def get_context_data(self, **kwargs):
        from catalog import selectors as catalog_selectors

        context = super().get_context_data(**kwargs)
        if self._campaign is None or self.locked_message:
            return context
        channel = _text(self.form_data.get("channel")) or Channel.META.value
        context.update(
            {
                "specific": self._campaign.target_kind == TargetKind.PROJECT,
                "project": self._project(),
                "area_chips": self._chips("area", catalog_selectors.areas(), _("Any area")),
                "unit_type_chips": self._chips("unit_type", catalog_selectors.unit_types(), _("Any unit type")),
                "channel_cards": self.cards(
                    "channel",
                    Channel.choices,
                    **{
                        Channel.META.value: {"description": _("Facebook and Instagram feeds, Stories and Reels.")},
                        Channel.GOOGLE.value: {"description": _("Google Search and YouTube.")},
                    },
                ),
                "identity_cards": self._identity_cards(channel),
                "own_platforms": ", ".join(
                    str(p.label) for p in Platform if selectors.PLATFORM_CHANNEL[p] == channel
                ),
                "area_legend": _("Area"),
                "unit_type_legend": _("Unit type"),
                "can_add_accounts": self.request.user.has_perm(MANAGE_ACCOUNTS),
                "lead_options": [
                    {"name": "accept_area_leads", "checked": _checked(self.form_data.get("accept_area_leads")),
                     "label": _("Also send me leads from other projects in the same area")},
                    {"name": "accept_developer_leads",
                     "checked": _checked(self.form_data.get("accept_developer_leads")),
                     "label": _("Also send me leads from other projects by the same developer")},
                ],
            }
        )
        return context

    def save(self, data):
        return services.save_target(actor=self.request.user, campaign_id=self.campaign_id, **data)


class CreativeStepView(_StepPage):
    """Step 3 (FR-4.2): cards from the library that fit the target, picked one click at a time."""

    template_name = "campaigns/step_creative.html"
    step = Step.CREATIVE

    def get_context_data(self, **kwargs):
        campaign = self._campaign
        chosen = list(selectors.chosen_creatives(campaign).values_list("pk", flat=True))
        choosable = selectors.choosable_creatives(campaign)
        self.creatives = choosable
        # Chosen earlier but no longer usable (offer ended, unpublished).
        self.unavailable = selectors.chosen_creatives(campaign).exclude(
            pk__in=choosable.values("pk")
        )
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "chosen_ids": chosen,
                "chosen_count": len(chosen),
                "pooled": campaign.is_pooled,
            }
        )
        return context

    def _change(self, action, creative_id):
        try:
            action(actor=self.request.user, campaign_id=self.campaign_id, creative_id=creative_id)
        except ValidationError as error:
            self.notice = _messages(error)
        else:
            self.notice = ""
        self._reload()

    @live.permission_required(RUN)
    @live.event_handler()
    def choose_creative(self, creative_id: int = 0, **kwargs):
        self._change(services.choose_creative, creative_id)

    @live.permission_required(RUN)
    @live.event_handler()
    def remove_creative(self, creative_id: int = 0, **kwargs):
        self._change(services.remove_creative, creative_id)

    @live.permission_required(RUN)
    @live.event_handler()
    def continue_to_next(self, **kwargs):
        self._reload()
        if self._campaign.step_done(Step.CREATIVE):
            self.live_redirect(self.next_url())
        elif self._campaign.is_pooled:
            self.notice = _("Choose one creative.")
        else:
            self.notice = _("Choose at least one creative.")


class AudienceStepView(_FormStepPage):
    """Step 4 (FR-4.5): the audience, and what to optimize for."""

    template_name = "campaigns/step_audience.html"
    form_class = AudienceForm
    step = Step.AUDIENCE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self._campaign is None or self.locked_message:
            return context
        current = _text(self.form_data.get("audience"))
        context["audience_cards"] = [
            {"name": "audience", "value": str(preset.pk), "label": preset.display_name,
             "description": preset.display_description, "selected": str(preset.pk) == current}
            for preset in selectors.choosable_audiences(self._campaign)
        ]
        context["goal_cards"] = self.cards(
            "goal",
            Goal.choices,
            **{
                Goal.LOWEST_CPL.value: {"description": _("The most leads for the money: the cheapest cost per lead.")},
                Goal.MORE_LEADS.value: {"description": _("The most leads your budget can buy.")},
                Goal.BETTER_QUALITY.value: {
                    "description": _("Fewer, more serious leads: optimized for interested buyers.")
                },
            },
        )
        return context

    def save(self, data):
        return services.save_audience(actor=self.request.user, campaign_id=self.campaign_id, **data)


class BudgetStepView(_FormStepPage):
    """Step 5 (FR-4.6): the budget and dates, then Save draft or Run. Estimates (FR-4.7) wait for Q2."""

    template_name = "campaigns/step_budget.html"
    form_class = BudgetForm
    step = Step.BUDGET

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["budget_kinds"] = self.cards("budget_kind", BudgetKind.choices)
        context["is_total"] = _text(self.form_data.get("budget_kind")) == BudgetKind.LIFETIME.value
        return context

    def save(self, data):
        return services.save_budget(actor=self.request.user, campaign_id=self.campaign_id, **data)

    def form_valid(self, form):
        try:
            campaign = self.save(form.cleaned_data)
            if getattr(self, "_action", "") == "run":
                services.run_campaign(actor=self.request.user, campaign_id=campaign.pk)
        except ValidationError as error:
            self.error_message = _messages(error)
            self._reload()
            return
        self.live_redirect(reverse("campaign", args=[campaign.pk]))


class ConnectedAccountsView(
    LoginRequiredLiveViewMixin,
    PermissionRequiredMixin,
    TenantMixin,
    PageLanguageMixin,
    FormMixin,
    LiveView,
):
    """The office's own Instagram accounts, Facebook pages and Google Ads accounts (FR-4.4).

    Added by hand for now and checked by staff (ROADMAP Q11). The "Connect
    with Meta/Google" buttons light up when ``campaigns.integrations`` has a
    real client for that channel.
    """

    template_name = "campaigns/accounts.html"
    permission_required = [VIEW, MANAGE_ACCOUNTS]
    tenant_required = True
    form_class = AccountForm

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.notice = ""
        self.notice_is_error = False
        if not self.form_data.get("platform"):
            self.form_data["platform"] = Platform.INSTAGRAM.value

    def get_context_data(self, **kwargs):
        self.accounts = services.accounts_for(self.request.user)
        context = super().get_context_data(**kwargs)
        context["platform_cards"] = [
            {"name": "platform", "value": value, "label": str(label),
             "selected": value == _text(self.form_data.get("platform"))}
            for value, label in Platform.choices
        ]
        context["is_google"] = _text(self.form_data.get("platform")) == Platform.GOOGLE.value
        context["connect"] = [
            {"label": label, "url": integrations.client_for(channel).connect_url(
                office_id=self.tenant.id, request=self.request) or ""}
            for channel, label in (
                (Channel.META, _("Connect with Meta")), (Channel.GOOGLE, _("Connect with Google")),
            )
        ]
        return context

    @live.permission_required(MANAGE_ACCOUNTS)
    @live.event_handler()
    def submit_form(self, **kwargs):
        super().submit_form(**kwargs)

    def form_valid(self, form):
        try:
            account = services.add_account(actor=self.request.user, **form.cleaned_data)
        except ValidationError as error:
            self.field_errors = {"handle": error.messages}
            self.success_message = ""
            return
        platform = self.form_data.get("platform")
        self.reset_form()
        self.form_data["platform"] = platform
        self.notice, self.notice_is_error = "", False
        self.success_message = _("%(account)s was added. Our team checks it before your first campaign on it goes live.") % {
            "account": str(account)
        }

    def form_invalid(self, form):
        self.success_message = ""

    @live.permission_required(MANAGE_ACCOUNTS)
    @live.event_handler()
    def remove_account(self, account_id: int = 0, **kwargs):
        try:
            account = services.remove_account(actor=self.request.user, account_id=account_id)
        except ValidationError as error:
            self.notice, self.notice_is_error = _messages(error), True
            return
        self.success_message = ""
        self.notice, self.notice_is_error = _("%(account)s was removed.") % {"account": str(account)}, False
