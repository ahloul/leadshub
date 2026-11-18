"""Meta Business Managers and their ad accounts (US-META-01, FR-6.10, FR-6.11).

Campaigns launch only on platform-owned ad accounts spread across several
Business Managers. ``MetaAdAccount.objects.available_for_launch()`` is the one
place that decides which accounts may take a new launch; the campaign story
that picks an account builds on it.

Ad accounts get in two ways, both by the Platform Admin: added by hand in
the admin, or by pressing Sync on their Business Manager. Sync matches by
``meta_account_id``, so a hand-added account is picked up, not duplicated.

Field ownership (criterion 11):

- Meta-owned, overwritten by every sync: ``name``, ``account_status``,
  ``last_synced_at``, ``is_removed``. ``account_status`` and ``is_removed``
  are not shown in the admin; they only keep disabled or vanished accounts
  out of launch (FR-6.12).
- Admin-owned, never touched by sync: ``enabled_for_launch``.

Both are plain CRUD for the Platform Admin. Deleting a Business Manager
deletes its ad accounts. Future campaign models must reference ad accounts
with ``on_delete=models.PROTECT``, so nothing a campaign uses can be deleted
(criterion 19); deactivate a Business Manager to stop launches but keep it.
"""

import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import crypto


class MetaBusinessManager(models.Model):
    """A platform-owned Meta Business Manager and its system-user token."""

    class TokenStatus(models.TextChoices):
        UNCHECKED = "unchecked", _("Unchecked")
        VALID = "valid", _("Valid")
        INVALID = "invalid", _("Invalid")

    name = models.CharField(_("name"), max_length=255)
    meta_business_id = models.CharField(
        _("Meta business ID"),
        max_length=32,
        unique=True,
        help_text=_("The numeric ID in Business Settings → Business info."),
    )

    # Fernet ciphertext. Write-only from the UI: never rendered (criteria 4, 5, 7).
    token_ciphertext = models.TextField(editable=False)
    # Last four characters, so admins can tell tokens apart (criterion 5).
    token_hint = models.CharField(max_length=4, editable=False)
    token_status = models.CharField(
        _("token status"),
        max_length=16,
        choices=TokenStatus.choices,
        default=TokenStatus.UNCHECKED,
        editable=False,
    )
    token_checked_at = models.DateTimeField(_("token last checked"), null=True, editable=False)
    # Meta's reason when the last check failed. Never contains the token.
    token_error = models.TextField(_("token problem"), blank=True, editable=False)

    # Deactivated: every ad account leaves launch, records stay (criterion 18).
    is_active = models.BooleanField(_("active"), default=True, editable=False)

    last_synced_at = models.DateTimeField(_("last synced"), null=True, editable=False)
    last_sync_failed_at = models.DateTimeField(_("last sync failure"), null=True, editable=False)
    last_sync_error = models.TextField(_("last sync error"), blank=True, editable=False)

    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("Business Manager")
        verbose_name_plural = _("Business Managers")

    def __str__(self) -> str:
        return self.name

    def set_token(self, token: str) -> None:
        self.token_ciphertext = crypto.encrypt(token)
        self.token_hint = token[-4:]

    def get_token(self) -> str:
        return crypto.decrypt(self.token_ciphertext)

    @property
    def masked_token(self) -> str:
        return f"••••{self.token_hint}" if self.token_hint else ""

    @property
    def token_is_valid(self) -> bool:
        return self.token_status == self.TokenStatus.VALID


class MetaAdAccountQuerySet(models.QuerySet):
    def available_for_launch(self):
        """Accounts a new campaign may launch on right now (criteria 10, 12, 15, 17, 18)."""
        return self.filter(
            enabled_for_launch=True,
            is_removed=False,
            account_status=MetaAdAccount.Status.ACTIVE,
            business_manager__is_active=True,
            business_manager__token_status=MetaBusinessManager.TokenStatus.VALID,
        )


class MetaAdAccount(models.Model):
    """A Meta ad account under one Business Manager: added by hand or by Sync."""

    class Status(models.IntegerChoices):
        # Meta's MetaAdAccount.account_status values.
        ACTIVE = 1, _("Active")
        DISABLED = 2, _("Disabled")
        UNSETTLED = 3, _("Unsettled")
        PENDING_RISK_REVIEW = 7, _("Pending risk review")
        PENDING_SETTLEMENT = 8, _("Pending settlement")
        IN_GRACE_PERIOD = 9, _("In grace period")
        PENDING_CLOSURE = 100, _("Pending closure")
        CLOSED = 101, _("Closed")
        ANY_ACTIVE = 201, _("Any active")
        ANY_CLOSED = 202, _("Any closed")

    business_manager = models.ForeignKey(
        MetaBusinessManager,
        on_delete=models.CASCADE,  # An ad account belongs to its Business Manager.
        related_name="ad_accounts",
        verbose_name=_("Business Manager"),
    )
    meta_account_id = models.CharField(
        _("Meta ad account ID"),
        max_length=64,
        unique=True,
        help_text=_('Starts with "act_", e.g. act_1234567890.'),
    )

    # Meta-owned: sync overwrites these (criterion 11).
    name = models.CharField(_("name"), max_length=255)
    account_status = models.PositiveSmallIntegerField(
        _("Meta status"), choices=Status.choices, default=Status.ACTIVE
    )
    # Empty for an account added by hand until its first sync.
    last_synced_at = models.DateTimeField(_("last synced"), null=True, blank=True, editable=False)
    # Meta stopped returning it: kept, never offered for launch (criterion 12).
    is_removed = models.BooleanField(_("removed from Meta"), default=False, editable=False)

    # Admin-owned (criterion 14). New accounts start off (criterion 10).
    enabled_for_launch = models.BooleanField(_("enabled for launch"), default=False)

    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    objects = MetaAdAccountQuerySet.as_manager()

    class Meta:
        ordering = ["business_manager__name", "name"]
        verbose_name = _("ad account")
        verbose_name_plural = _("ad accounts")

    def __str__(self) -> str:
        return f"{self.name} ({self.meta_account_id})"

    def launch_blockers(self) -> list[str]:
        """Why this account can't take a launch, apart from the admin switch (criteria 15, 17)."""
        problems = []
        if self.is_removed:
            problems.append(_("no longer returned by Meta"))
        if self.account_status != self.Status.ACTIVE:
            problems.append(_("disabled by Meta"))
        bm = self.business_manager
        if not bm.is_active:
            problems.append(_("Business Manager is deactivated"))
        if not bm.token_is_valid:
            problems.append(_("Business Manager token is not valid"))
        return problems

    def clean(self):
        if self.meta_account_id and not self.meta_account_id.startswith("act_"):
            self.meta_account_id = f"act_{self.meta_account_id}"
        if self.meta_account_id and not self.meta_account_id[4:].isdigit():
            raise ValidationError({"meta_account_id": _("Enter act_ followed by the numeric account ID.")})
        # Turning launch on needs a healthy account (criterion 15). Turning it
        # off is always allowed.
        if self.enabled_for_launch and not self._enabled_in_db():
            if problems := self.launch_blockers():
                raise ValidationError(
                    {
                        "enabled_for_launch": _("Can't enable for launch: %(reasons)s.")
                        % {"reasons": ", ".join(str(p) for p in problems)}
                    }
                )

    def _enabled_in_db(self) -> bool:
        if self.pk is None:
            return False
        return MetaAdAccount.objects.filter(pk=self.pk, enabled_for_launch=True).exists()


class MetaLaunch(models.Model):
    """One LeadsHub campaign built on Meta by the media buying team (US-META-02).

    Each Meta ID is saved the moment Meta returns it, so a build that fails
    halfway resumes from the last saved step instead of creating duplicates.
    ``meta_adset_id`` is the routing key lead delivery matches on (FR-7.6).
    """

    class Status(models.TextChoices):
        BUILDING = "building", _("Building")
        BUILD_FAILED = "build_failed", _("Build failed")
        ACTIVE = "active", _("Active")
        PAUSED = "paused", _("Paused")
        ARCHIVED = "archived", _("Stopped")

    # A build left "building" this long ago crashed; it may be resumed.
    STALE_AFTER = datetime.timedelta(minutes=5)

    campaign = models.OneToOneField(
        "campaigns.Campaign",
        on_delete=models.PROTECT,
        related_name="meta_launch",
        verbose_name=_("campaign"),
    )
    ad_account = models.ForeignKey(
        MetaAdAccount,
        on_delete=models.PROTECT,  # Nothing a campaign uses can be deleted.
        related_name="launches",
        verbose_name=_("ad account"),
    )
    page_id = models.CharField(_("Facebook page ID"), max_length=64)
    page_name = models.CharField(_("Facebook page"), max_length=255, blank=True)
    instagram_actor_id = models.CharField(_("Instagram account ID"), max_length=64, blank=True)
    lead_form_id = models.CharField(_("lead form ID"), max_length=64)
    lead_form_name = models.CharField(_("lead form"), max_length=255, blank=True)

    meta_campaign_id = models.CharField(_("Meta campaign ID"), max_length=64, blank=True)
    meta_adset_id = models.CharField(_("Meta ad set ID"), max_length=64, blank=True, db_index=True)
    # One entry per creative: {creative_pk, image_hash, video_id, creative_id, ad_id}.
    ads = models.JSONField(_("ads"), default=list, blank=True)
    # Interest names from the audience preset that Meta didn't recognise.
    skipped_interests = models.JSONField(_("interests not found on Meta"), default=list, blank=True)

    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.BUILDING, db_index=True
    )
    # Meta's reason, token scrubbed (MetaError).
    last_error = models.TextField(_("last error"), blank=True)

    # What is on Meta now.
    budget_kind = models.CharField(_("budget type"), max_length=10)
    budget_usd = models.DecimalField(_("budget (USD)"), max_digits=10, decimal_places=2)
    ends_on = models.DateField(_("end date"), null=True, blank=True)

    launched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
        verbose_name=_("launched by"),
    )
    launched_at = models.DateTimeField(_("launched at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    meta_status_checked_at = models.DateTimeField(_("last checked on Meta"), null=True, blank=True)

    class Meta:
        ordering = ["-launched_at"]
        verbose_name = _("Meta launch")
        verbose_name_plural = _("Meta launches")

    def __str__(self) -> str:
        return f"{self.campaign_id} on {self.ad_account_id} ({self.status})"

    def is_stale_build(self, now=None) -> bool:
        """A build that has said "building" for over five minutes crashed (Review Focus 1)."""
        now = now or timezone.now()
        return self.status == self.Status.BUILDING and self.updated_at < now - self.STALE_AFTER
