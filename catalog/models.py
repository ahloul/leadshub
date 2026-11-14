"""Shared master data: developers, projects, areas and unit types (SRS §1.1, FR-4.1).

Every entry has an English and an Arabic name. Templates use ``display_name``
(or ``str()``), which picks the one for the active language.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _


class BilingualNamed(models.Model):
    name_en = models.CharField(_("name (English)"), max_length=200)
    name_ar = models.CharField(_("name (Arabic)"), max_length=200, blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        abstract = True
        ordering = ["name_en"]

    @property
    def display_name(self):
        if self.name_ar and (get_language() or "").startswith("ar"):
            return self.name_ar
        return self.name_en

    def __str__(self):
        return self.display_name


class Developer(BilingualNamed):
    class Meta(BilingualNamed.Meta):
        verbose_name = _("developer")
        verbose_name_plural = _("developers")
        constraints = [models.UniqueConstraint(fields=["name_en"], name="unique_developer_name")]


class Area(BilingualNamed):
    class Meta(BilingualNamed.Meta):
        verbose_name = _("area")
        verbose_name_plural = _("areas")
        constraints = [models.UniqueConstraint(fields=["name_en"], name="unique_area_name")]


class UnitType(BilingualNamed):
    class Meta(BilingualNamed.Meta):
        verbose_name = _("unit type")
        verbose_name_plural = _("unit types")
        constraints = [models.UniqueConstraint(fields=["name_en"], name="unique_unit_type_name")]


class Project(BilingualNamed):
    developer = models.ForeignKey(
        Developer, on_delete=models.PROTECT, related_name="projects", verbose_name=_("developer")
    )
    area = models.ForeignKey(
        Area, on_delete=models.PROTECT, related_name="projects", verbose_name=_("area")
    )
    unit_types = models.ManyToManyField(
        UnitType, blank=True, related_name="projects", verbose_name=_("unit types")
    )
    # Fixed broker commission rate used by Project X (SRS §5.1), e.g. 3.50 = 3.5%.
    commission_rate_percent = models.DecimalField(
        _("commission rate (%)"),
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta(BilingualNamed.Meta):
        verbose_name = _("project")
        verbose_name_plural = _("projects")
        constraints = [
            models.UniqueConstraint(fields=["developer", "name_en"], name="unique_project_per_developer"),
            models.CheckConstraint(
                condition=models.Q(commission_rate_percent__gte=0, commission_rate_percent__lte=100),
                name="project_commission_rate_0_100",
            ),
        ]


class AudiencePreset(BilingualNamed):
    """A plain-language audience brokers pick in the campaign builder (FR-4.5).

    Brokers see only the name, such as "New Cairo compound buyers, higher
    income". The Meta targeting behind it is internal and goes into the
    campaign brief (SRS §4.1). There are no age or gender fields: Meta's
    housing category doesn't allow those limits (FR-5.14).
    """

    area = models.ForeignKey(
        Area,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audience_presets",
        verbose_name=_("area"),
        help_text=_("Leave empty if it suits any area."),
    )
    meta_interests = models.TextField(
        _("Meta interests"), blank=True, help_text=_("One interest per line.")
    )
    lookalike_source = models.CharField(
        _("lookalike source"),
        max_length=100,
        blank=True,
        help_text=_("For example: closed_won_1pct."),
    )
    geo_targeting = models.CharField(
        _("location targeting"),
        max_length=200,
        blank=True,
        help_text=_("For example: 6th of October + 15 km."),
    )

    class Meta(BilingualNamed.Meta):
        verbose_name = _("audience preset")
        verbose_name_plural = _("audience presets")
        constraints = [
            models.UniqueConstraint(fields=["name_en"], name="unique_audience_preset_name")
        ]

    @property
    def interests(self):
        return [line.strip() for line in self.meta_interests.splitlines() if line.strip()]
