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
  ``currency``, ``timezone_name``, ``ownership``, ``last_synced_at``,
  ``is_removed``. Typed by the admin only when adding an account by hand.
- Admin-owned, never touched by sync: ``enabled_for_launch``.

Both are plain CRUD for the Platform Admin. Deleting a Business Manager
deletes its ad accounts. Future campaign models must reference ad accounts
with ``on_delete=models.PROTECT``, so nothing a campaign uses can be deleted
(criterion 19); deactivate a Business Manager to stop launches but keep it.
"""

from django.core.exceptions import ValidationError
from django.db import models
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

    class Ownership(models.TextChoices):
        OWNED = "owned", _("Owned")
        SHARED = "shared", _("Shared with us")

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
    ownership = models.CharField(
        _("ownership"), max_length=10, choices=Ownership.choices, default=Ownership.OWNED
    )
    currency = models.CharField(_("currency"), max_length=3, help_text=_("ISO code, e.g. EGP or USD."))
    timezone_name = models.CharField(
        _("time zone"), max_length=64, help_text=_("As in Ads Manager, e.g. Africa/Cairo.")
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
            problems.append(_("Meta status is not active"))
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
        if self.currency:
            self.currency = self.currency.upper()
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
