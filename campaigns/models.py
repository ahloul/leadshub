"""Broker campaigns, their life cycle, and the office's own ad accounts (SRS §3.1; ROADMAP M3).

A broker builds a campaign in five steps (``Step``): type, target, creative,
audience, budget and run. The draft is saved at every step, so a half-built
campaign waits as a Draft. "Run" sends it to the back-office review queue
(AI Building).

The six §3.1 states form a state machine (``TRANSITIONS``). Each change is a
model method, such as ``submit()``, and any change not listed is refused with
``InvalidTransition``. The change is a conditional UPDATE, so two people
acting at once can't both move the same campaign.

Only Draft and Needs Changes campaigns can be edited (``is_editable``). Edits
to a Live campaign go through review, which comes with ROADMAP M4.

Ads run on Meta (Facebook and Instagram) or Google (``Channel``). They appear
under the platform's own page, on a platform landing page, or under one of
the office's own accounts (``ConnectedAccount``). Talking to Meta and Google
is not built yet: ``campaigns.integrations`` is where it plugs in, and the
``external_id`` and ``platform_data`` fields are for what those platforms
send back (docs/INTEGRATIONS.md).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Status(models.TextChoices):
    DRAFT = "draft", _("Draft")
    AI_BUILDING = "ai_building", _("AI Building")
    LIVE = "live", _("Live")
    NEEDS_CHANGES = "needs_changes", _("Needs Changes")
    PAUSED_LOW_BALANCE = "paused_low_balance", _("Paused (Low Balance)")
    ENDED = "ended", _("Ended")


# Change name → (the states it starts from, the state it leads to). §3.1:
# Run sends a draft (or a corrected campaign) to review; review approves it
# (Live) or rejects it with reasons (Needs Changes, ROADMAP M4); a Live
# campaign pauses when the wallet runs low and resumes when it's topped up
# (M6); it ends when its schedule is complete.
TRANSITIONS = {
    "submit": ({Status.DRAFT, Status.NEEDS_CHANGES}, Status.AI_BUILDING),
    "request_changes": ({Status.AI_BUILDING}, Status.NEEDS_CHANGES),
    "go_live": ({Status.AI_BUILDING}, Status.LIVE),
    "pause_for_low_balance": ({Status.LIVE}, Status.PAUSED_LOW_BALANCE),
    "resume": ({Status.PAUSED_LOW_BALANCE}, Status.LIVE),
    "end": ({Status.LIVE, Status.PAUSED_LOW_BALANCE}, Status.ENDED),
}

EDITABLE = frozenset({Status.DRAFT, Status.NEEDS_CHANGES})


class InvalidTransition(ValidationError):
    """A state change the §3.1 life cycle doesn't allow from the current state."""


class TargetKind(models.TextChoices):
    """FR-4.1: one project, or a category across projects."""

    PROJECT = "project", _("Specific campaign")
    CATEGORY = "category", _("General campaign")


class Channel(models.TextChoices):
    """The ad network the campaign runs on (phase 1: Meta and Google)."""

    META = "meta", _("Meta: Facebook and Instagram")
    GOOGLE = "google", _("Google: Search and YouTube")


# The network's short name, for tags in lists.
CHANNEL_NAMES = {Channel.META: _("Meta"), Channel.GOOGLE: _("Google")}


class Destination(models.TextChoices):
    """FR-4.3, FR-4.4: whose page or account the ads appear under."""

    DARK_PAGE = "dark_page", _("Platform dark page")
    LANDING_PAGE = "landing_page", _("Platform landing page")
    OWN_ACCOUNT = "own_account", _("Your own page or account")


# Where each destination can run. The dark page is a Meta page, so it
# exists only there; pooling (FR-7.1) happens on Meta ad sets.
DESTINATION_CHANNELS = {
    Destination.DARK_PAGE: {Channel.META},
    Destination.LANDING_PAGE: {Channel.META, Channel.GOOGLE},
    Destination.OWN_ACCOUNT: {Channel.META, Channel.GOOGLE},
}


class Goal(models.TextChoices):
    """What the campaign is optimized for (the prototype's "Optimization goal")."""

    LOWEST_CPL = "lowest_cpl", _("Lowest cost per lead")
    MORE_LEADS = "more_leads", _("More leads")
    BETTER_QUALITY = "better_quality", _("Better quality")


# Short goal names for badges in lists, as the product prototype shows them.
GOAL_NAMES = {
    Goal.LOWEST_CPL: _("Lowest CPL"),
    Goal.MORE_LEADS: _("More leads"),
    Goal.BETTER_QUALITY: _("Better quality"),
}


class BudgetKind(models.TextChoices):
    DAILY = "daily", _("Daily budget")
    LIFETIME = "lifetime", _("Total budget")


class Step(models.TextChoices):
    """The builder's steps, in order (SRS §3.1). Values are the URL segments."""

    TYPE = "type", _("Type")
    TARGET = "target", _("Target")
    CREATIVE = "creative", _("Creative")
    AUDIENCE = "audience", _("Audience")
    BUDGET = "budget", _("Budget and run")


# The office's own pages and ad accounts


class Platform(models.TextChoices):
    INSTAGRAM = "instagram", _("Instagram account")
    FACEBOOK = "facebook", _("Facebook page")
    GOOGLE = "google", _("Google Ads account")


# Which accounts can carry a campaign on each channel.
PLATFORM_CHANNEL = {
    Platform.INSTAGRAM: Channel.META,
    Platform.FACEBOOK: Channel.META,
    Platform.GOOGLE: Channel.GOOGLE,
}


class AccountStatus(models.TextChoices):
    """Added by hand they wait for a check; the Meta and Google sign-in will confirm them."""

    UNVERIFIED = "unverified", _("Not verified yet")
    VERIFIED = "verified", _("Verified")
    DISCONNECTED = "disconnected", _("Disconnected")


# A handle as it appears on Instagram or Facebook: letters, digits, dots and
# underscores (ROADMAP Q11: typed by the owner, checked by staff).
SOCIAL_HANDLE = RegexValidator(
    r"^@[A-Za-z0-9._]{1,50}$",
    _("Enter the handle as it appears on Instagram or Facebook, for example @nour.realty."),
)
# A Google Ads customer ID: ten digits, usually written 123-456-7890.
GOOGLE_ADS_ID = RegexValidator(
    r"^\d{3}-\d{3}-\d{4}$", _("Enter the Google Ads customer ID, for example 123-456-7890.")
)


class ConnectedAccount(models.Model):
    """One of the office's own Instagram accounts, Facebook pages or Google Ads accounts.

    Owners add them on the Pages page (FR-4.4). Until the Meta and Google
    sign-in exists (``campaigns.integrations``), they're typed by hand and
    staff mark them verified in the admin. ``external_id`` is the platform's
    own id (page id, ad account id), set by the integration.
    """

    office = models.ForeignKey(
        "accounts.BrokerOffice",
        on_delete=models.CASCADE,
        related_name="connected_accounts",
        verbose_name=_("broker office"),
    )
    platform = models.CharField(_("platform"), max_length=10, choices=Platform.choices)
    handle = models.CharField(
        _("handle or account ID"),
        max_length=51,
        help_text=_("@handle for Instagram and Facebook, the customer ID for Google Ads."),
    )
    status = models.CharField(
        _("status"), max_length=12, choices=AccountStatus.choices, default=AccountStatus.UNVERIFIED
    )
    external_id = models.CharField(
        _("platform ID"), max_length=64, blank=True, help_text=_("Set by the Meta or Google connection.")
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("added by"),
    )
    verified_at = models.DateTimeField(_("verified at"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("connected account")
        verbose_name_plural = _("connected accounts")
        ordering = ["platform", "handle"]
        constraints = [
            models.UniqueConstraint(
                fields=["office", "platform", "handle"], name="unique_connected_account_per_office"
            )
        ]

    def __str__(self):
        return f"{self.get_platform_display()} {self.isolated_handle}"

    @property
    def isolated_handle(self):
        """The handle kept left to right inside Arabic text (Unicode isolate marks)."""
        return f"\u2066{self.handle}\u2069"

    @property
    def channel(self):
        return PLATFORM_CHANNEL[Platform(self.platform)]

    @property
    def usable(self):
        return self.status != AccountStatus.DISCONNECTED

    def clean(self):
        super().clean()
        validator = GOOGLE_ADS_ID if self.platform == Platform.GOOGLE else SOCIAL_HANDLE
        try:
            validator(self.handle)
        except ValidationError as error:
            raise ValidationError({"handle": error.messages}) from error


class Campaign(models.Model):
    office = models.ForeignKey(
        "accounts.BrokerOffice",
        on_delete=models.CASCADE,
        related_name="campaigns",
        verbose_name=_("broker office"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("created by"),
    )
    status = models.CharField(
        _("status"), max_length=20, choices=Status.choices, default=Status.DRAFT
    )

    # Step 1, type (FR-4.1).
    target_kind = models.CharField(
        _("campaign type"),
        max_length=10,
        choices=TargetKind.choices,
        default=TargetKind.PROJECT,
    )

    # Step 2, target (FR-4.1). A specific campaign has a project, which sets
    # its developer and area. A general campaign has any two or more of area,
    # developer and unit type; the rest is "any".
    project = models.ForeignKey(
        "catalog.Project",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="campaigns",
        verbose_name=_("project"),
    )
    developer = models.ForeignKey(
        "catalog.Developer",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="campaigns",
        verbose_name=_("developer"),
    )
    area = models.ForeignKey(
        "catalog.Area",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="campaigns",
        verbose_name=_("area"),
    )
    unit_type = models.ForeignKey(
        "catalog.UnitType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="campaigns",
        verbose_name=_("unit type"),
    )

    # Step 2, channel and destination (FR-4.3, FR-4.4).
    channel = models.CharField(
        _("channel"), max_length=10, choices=Channel.choices, default=Channel.META
    )
    destination = models.CharField(
        _("where the ads appear"),
        max_length=20,
        choices=Destination.choices,
        default=Destination.DARK_PAGE,
    )
    connected_account = models.ForeignKey(
        ConnectedAccount,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="campaigns",
        verbose_name=_("your page or account"),
    )
    # "Get more, better-quality leads": also take leads from other projects in
    # the same area, or by the same developer, when they're better. Used by
    # lead routing (ROADMAP M7).
    accept_area_leads = models.BooleanField(
        _("also take leads from other projects in the same area"), default=False
    )
    accept_developer_leads = models.BooleanField(
        _("also take leads from other projects by the same developer"), default=False
    )

    # Step 3, creatives (FR-4.2). One for the dark page, so the campaign can
    # join a pool; any number elsewhere (ROADMAP Q10).
    creatives = models.ManyToManyField(
        "creatives.Creative", blank=True, related_name="campaigns", verbose_name=_("creatives")
    )

    # Step 4, audience (FR-4.5) and what to optimize for. Never age or gender
    # (FR-5.14).
    audience = models.ForeignKey(
        "catalog.AudiencePreset",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="campaigns",
        verbose_name=_("audience"),
    )
    goal = models.CharField(
        _("optimization goal"), max_length=16, choices=Goal.choices, default=Goal.LOWEST_CPL
    )

    # Step 5, budget and schedule (FR-4.6). In US dollars, like the pools'
    # combined budget (SRS §6.2). What a daily budget caps is ROADMAP Q2.
    budget_kind = models.CharField(
        _("budget type"), max_length=10, choices=BudgetKind.choices, default=BudgetKind.DAILY
    )
    budget_usd = models.DecimalField(
        _("amount (USD)"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )
    starts_on = models.DateField(_("start date"), null=True, blank=True)
    ends_on = models.DateField(
        _("end date"),
        null=True,
        blank=True,
        help_text=_("Needed for a total budget. Leave empty to run a daily budget until you stop it."),
    )

    # Set by the review queue when it sends a campaign back (ROADMAP M4).
    change_reasons = models.TextField(_("changes asked for"), blank=True)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)

    # For the Meta and Google integration (campaigns.integrations): the
    # platform's campaign id, and whatever else it needs to keep.
    external_id = models.CharField(_("platform campaign ID"), max_length=64, blank=True)
    platform_data = models.JSONField(_("platform data"), default=dict, blank=True)

    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("campaign")
        verbose_name_plural = _("campaigns")
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["office", "status"], name="campaign_office_status_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(budget_usd__isnull=True) | models.Q(budget_usd__gte=1),
                name="campaign_budget_at_least_1",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_on__isnull=True)
                | models.Q(starts_on__isnull=True)
                | models.Q(ends_on__gte=models.F("starts_on")),
                name="campaign_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        return f"{self.code} {self.target_label}"

    @property
    def code(self):
        """The reference brokers and staff quote, e.g. LH-C-0051 (ROADMAP M3)."""
        return f"LH-C-{self.pk:04d}" if self.pk else "LH-C-…"

    @property
    def target_label(self):
        """What the campaign advertises, in the active language."""
        if self.target_kind == TargetKind.PROJECT:
            return self.project.display_name if self.project_id else str(_("No target yet"))
        parts = [
            item.display_name
            for item in (self.unit_type, self.developer, self.area)
            if item is not None
        ]
        return " · ".join(parts) if parts else str(_("No target yet"))

    @property
    def is_editable(self):
        return self.status in EDITABLE

    @property
    def channel_name(self):
        return str(CHANNEL_NAMES[Channel(self.channel)])

    @property
    def goal_name(self):
        return str(GOAL_NAMES[Goal(self.goal)])

    @property
    def budget_text(self):
        """The amount as the list shows it, e.g. $1,250 (empty until it's set)."""
        return f"${self.budget_usd:,.0f}" if self.budget_usd else ""

    @property
    def builder_url(self):
        """Where to carry on building: the first step still missing, or the campaign page.

        Uses the ``creative_count`` annotation of ``selectors.office_campaigns``
        when it's there, which saves a query per campaign.
        """
        from django.urls import reverse

        if not self.pk:
            return reverse("campaign_new")
        missing = self.missing_steps(creative_count=getattr(self, "creative_count", None))
        if missing:
            return reverse(f"campaign_{missing[0].value}", args=[self.pk])
        return reverse("campaign", args=[self.pk])

    @property
    def is_pooled(self):
        """Dark-page campaigns can join a pool; nothing else ever does (FR-7.1, FR-7.2)."""
        return self.destination == Destination.DARK_PAGE

    # Steps

    def target_errors(self):
        """What's missing or wrong in the target step, as messages (empty when complete)."""
        errors = []
        if self.target_kind == TargetKind.PROJECT:
            if not self.project_id:
                errors.append(_("Choose a project."))
        elif sum(bool(x) for x in (self.area_id, self.developer_id, self.unit_type_id)) < 2:
            errors.append(_("Choose at least two of area, developer and unit type."))
        if self.channel not in DESTINATION_CHANNELS[Destination(self.destination)]:
            errors.append(_("The platform dark page runs on Meta only."))
        if self.destination == Destination.OWN_ACCOUNT:
            account = self.connected_account
            if account is None or not account.usable or account.channel != self.channel:
                errors.append(_("Choose one of your pages or accounts for this channel."))
        return errors

    def step_done(self, step, *, creative_count=None):
        """Whether ``step`` has everything it needs. ``creative_count`` saves a query."""
        step = Step(step)
        if step == Step.TYPE:
            return bool(self.pk)
        if step == Step.TARGET:
            return not self.target_errors()
        if step == Step.CREATIVE:
            count = self.creatives.count() if creative_count is None else creative_count
            return count == 1 if self.is_pooled else count >= 1
        if step == Step.AUDIENCE:
            return bool(self.audience_id)
        return bool(
            self.budget_usd
            and self.starts_on
            and (self.budget_kind == BudgetKind.DAILY or self.ends_on)
        )

    def missing_steps(self, *, creative_count=None):
        """The steps still to complete, in builder order."""
        return [
            step for step in Step if not self.step_done(step, creative_count=creative_count)
        ]

    # Life cycle (SRS §3.1)

    def _move(self, change, **fields):
        sources, target = TRANSITIONS[change]
        now = timezone.now()
        moved = type(self).objects.filter(pk=self.pk, status__in=sources).update(
            status=target, updated_at=now, **fields
        )
        if not moved:
            current = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            raise InvalidTransition(
                _("A campaign that is %(status)s can't do that."),
                params={"status": Status(current or self.status).label},
                code="invalid_transition",
            )
        self.status = target
        self.updated_at = now
        for name, value in fields.items():
            setattr(self, name, value)

    def submit(self):
        """Run: Draft or Needs Changes → AI Building, into the review queue."""
        self._move("submit", submitted_at=timezone.now())

    def request_changes(self, reasons):
        """Review sends it back: AI Building → Needs Changes, with the reasons."""
        self._move("request_changes", change_reasons=reasons)

    def go_live(self):
        """Review approved and the ads deliver: AI Building → Live."""
        self._move("go_live")

    def pause_for_low_balance(self):
        """The wallet fell below the 24-hour threshold: Live → Paused (Low Balance)."""
        self._move("pause_for_low_balance")

    def resume(self):
        """The wallet covers it again: Paused (Low Balance) → Live."""
        self._move("resume")

    def end(self):
        """The schedule is complete: Live or Paused → Ended."""
        self._move("end")
