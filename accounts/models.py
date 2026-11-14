"""Users, broker offices (tenants) and office membership (SRS §2, §6.2)."""

import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from .roles import CAPABILITIES, Audience, Role


class User(AbstractUser):
    email = models.EmailField(_("email address"), unique=True)
    # Set only for Developer Read-Only View accounts: the developer whose
    # aggregate project metrics they may see.
    developer = models.ForeignKey(
        "catalog.Developer",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="viewer_accounts",
        verbose_name=_("developer"),
    )

    # Never sent to the browser by djust (password/is_staff/is_superuser are
    # excluded by djust already).
    djust_exclude_fields = ("last_login",)

    def has_role(self, role):
        return self.groups.filter(name=Role(role).value).exists()

    def role_labels(self):
        """Names of this user's roles in the active language, for display."""
        groups = self.groups.filter(role_profile__isnull=False).select_related("role_profile")
        return sorted(g.role_profile.display_name for g in groups)

    @property
    def is_internal_staff(self):
        return self.groups.filter(role_profile__audience=Audience.INTERNAL).exists()


class SpendTier(models.TextChoices):
    BRONZE = "bronze", _("Bronze")
    SILVER = "silver", _("Silver")
    GOLD = "gold", _("Gold")
    PLATINUM = "platinum", _("Platinum")


class OfficeStatus(models.TextChoices):
    """Where an office stands. Offices opened by staff start active; a
    brokerage that signs up itself waits for staff review (ROADMAP Q6)."""

    PENDING = "pending", _("Pending review")
    ACTIVE = "active", _("Active")
    REJECTED = "rejected", _("Rejected")


class BrokerOffice(models.Model):
    """A brokerage organization: the tenant every client-side record belongs to."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_name = models.CharField(_("company name"), max_length=255)
    wallet_balance_egp = models.DecimalField(
        _("wallet balance (EGP)"), max_digits=12, decimal_places=2, default=0
    )
    wallet_balance_usd = models.DecimalField(
        _("wallet balance (USD)"), max_digits=12, decimal_places=2, default=0
    )
    spend_tier = models.CharField(
        _("spend tier"), max_length=16, choices=SpendTier.choices, default=SpendTier.BRONZE
    )
    feedback_score = models.PositiveSmallIntegerField(
        _("feedback score"), default=0, validators=[MaxValueValidator(100)]
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=OfficeStatus.choices,
        default=OfficeStatus.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("broker office")
        verbose_name_plural = _("broker offices")
        ordering = ["company_name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(feedback_score__lte=100), name="feedback_score_max_100"
            ),
        ]

    def __str__(self):
        return self.company_name


class Membership(models.Model):
    """Links a broker user to their office. One office per user.

    The user's role inside the office (owner or team member) is their group;
    see ``accounts.roles``.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="membership",
        verbose_name=_("user"),
    )
    office = models.ForeignKey(
        BrokerOffice,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name=_("office"),
    )
    joined_at = models.DateTimeField(_("joined at"), auto_now_add=True)

    class Meta:
        verbose_name = _("membership")
        verbose_name_plural = _("memberships")

    def __str__(self):
        return f"{self.user} @ {self.office}"


# Digits with an optional leading +, as accounts.forms.clean_phone_number
# leaves them: 01012345678 or +201012345678.
phone_number_validator = RegexValidator(
    r"^\+?[0-9]{8,15}$",
    _("Enter a phone number of 8 to 15 digits, optionally starting with +."),
    code="invalid_phone_number",
)


class OfficeRegistration(models.Model):
    """A brokerage's own sign-up, kept for staff review (ROADMAP Q6).

    The applicant becomes the office's Broker Owner when staff approve it
    (``accounts.services.approve_registrations``); until then they have no
    role and see only the registration status page.
    """

    office = models.OneToOneField(
        BrokerOffice,
        on_delete=models.CASCADE,
        related_name="registration",
        verbose_name=_("office"),
    )
    applicant = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="office_registration",
        verbose_name=_("applicant"),
    )
    phone_number = models.CharField(
        _("mobile number"), max_length=16, validators=[phone_number_validator]
    )
    # The emails about this sign-up go out in the language it was made in.
    language = models.CharField(_("language"), max_length=8, choices=settings.LANGUAGES)
    email_confirmed_at = models.DateTimeField(_("email confirmed at"), null=True, blank=True)
    created_at = models.DateTimeField(_("signed up at"), auto_now_add=True)
    reviewed_at = models.DateTimeField(_("reviewed at"), null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("reviewed by"),
    )

    class Meta:
        verbose_name = _("sign-up")
        verbose_name_plural = _("sign-ups")
        ordering = ["-created_at"]

    def __str__(self):
        return str(self.office)


class RoleProfile(models.Model):
    """A role: a Django group with a bilingual name and an audience.

    The essential roles (``is_system``) are created from ``accounts.roles`` on
    every migrate and can't be edited. Custom roles are made in the admin; their
    group is kept in step by ``accounts.admin.RoleForm``.
    """

    group = models.OneToOneField(
        Group, on_delete=models.CASCADE, related_name="role_profile", verbose_name=_("group")
    )
    name_en = models.CharField(_("name (English)"), max_length=150, unique=True)
    name_ar = models.CharField(_("name (Arabic)"), max_length=150, blank=True)
    audience = models.CharField(_("audience"), max_length=16, choices=Audience.choices)
    is_system = models.BooleanField(_("essential role"), default=False, editable=False)

    class Meta:
        verbose_name = _("role")
        verbose_name_plural = _("roles")
        ordering = ["-is_system", "name_en"]

    @property
    def display_name(self):
        if self.name_ar and (get_language() or "").startswith("ar"):
            return self.name_ar
        return self.name_en

    def __str__(self):
        return self.display_name


class Capability(models.Model):
    """Holds the role capabilities as Django permissions. Has no table."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = CAPABILITIES
