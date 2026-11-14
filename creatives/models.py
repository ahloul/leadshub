"""The creative library: ready-made ads brokers use in campaigns (SRS §3.2, FR-5.2 to FR-5.7).

Content Managers upload assets in the admin and tag them with catalog data
(FR-5.3). Brokers see only what ``Creative.objects.usable()`` returns:
published assets whose offer hasn't ended (FR-5.7).
"""

import mimetypes
import posixpath
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.deconstruct import deconstructible
from django.utils.text import get_valid_filename
from django.utils.translation import gettext_lazy as _

# No HTML or SVG: they could run scripts on this site (leadshub.files).
FILE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif", "mp4", "mov", "webm", "zip"]
PREVIEW_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]
MEGABYTE = 1024 * 1024


class AssetType(models.TextChoices):
    REEL = "reel", _("9:16 reel")
    CAROUSEL = "carousel", _("Carousel")
    IMAGE = "image", _("Static image")
    LANDING_PAGE = "landing_page", _("Landing page")


class Language(models.TextChoices):
    ARABIC = "ar", _("Arabic")
    ENGLISH = "en", _("English")


class AspectRatio(models.TextChoices):
    PORTRAIT = "9:16", "9:16"
    VERTICAL = "4:5", "4:5"
    SQUARE = "1:1", "1:1"
    LANDSCAPE = "16:9", "16:9"


class Status(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PUBLISHED = "published", _("Published")
    ARCHIVED = "archived", _("Archived")


def upload_to(instance, filename):
    """``creatives/<random folder>/<original name>``: unique, and a download keeps the name."""
    return f"creatives/{uuid.uuid4().hex}/{get_valid_filename(posixpath.basename(filename))}"


@deconstructible
class MaxFileSize:
    def __init__(self, megabytes):
        self.megabytes = megabytes

    def __call__(self, file):
        if file.size > self.megabytes * MEGABYTE:
            raise ValidationError(
                _("The file is larger than %(size)d MB."),
                params={"size": self.megabytes},
                code="file_too_large",
            )

    def __eq__(self, other):
        return isinstance(other, MaxFileSize) and other.megabytes == self.megabytes


class CreativeQuerySet(models.QuerySet):
    def unexpired(self, today=None):
        """Offers that run through ``today`` (Cairo time) or have no end date."""
        today = today or timezone.localdate()
        return self.filter(Q(offer_expires_on__isnull=True) | Q(offer_expires_on__gte=today))

    def usable(self, today=None):
        """What brokers may see and choose (FR-5.7): published, and the offer still on.

        The library page and the campaign builder both start from this.
        """
        return self.filter(status=Status.PUBLISHED).unexpired(today)


class Creative(models.Model):
    title = models.CharField(_("title"), max_length=200)
    offer = models.CharField(
        _("offer"),
        max_length=255,
        blank=True,
        help_text=_('As the ad states it, for example "5% down payment, 10-year plan".'),
    )
    asset_type = models.CharField(_("type"), max_length=20, choices=AssetType.choices)
    language = models.CharField(_("language"), max_length=2, choices=Language.choices)
    aspect_ratio = models.CharField(
        _("aspect ratio"),
        max_length=5,
        choices=AspectRatio.choices,
        blank=True,
        help_text=_("Leave empty for a landing page."),
    )
    developer = models.ForeignKey(
        "catalog.Developer",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="creatives",
        verbose_name=_("developer"),
    )
    project = models.ForeignKey(
        "catalog.Project",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="creatives",
        verbose_name=_("project"),
        help_text=_("Leave empty for an area-wide asset."),
    )
    area = models.ForeignKey(
        "catalog.Area",
        on_delete=models.PROTECT,
        blank=True,
        related_name="creatives",
        verbose_name=_("area"),
        help_text=_("Filled in from the project when one is chosen."),
    )
    unit_type = models.ForeignKey(
        "catalog.UnitType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="creatives",
        verbose_name=_("unit type"),
    )
    offer_expires_on = models.DateField(
        _("offer ends on"),
        null=True,
        blank=True,
        help_text=_("The last day of the offer. From the next day, brokers no longer see the asset."),
    )
    status = models.CharField(
        _("status"), max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    file = models.FileField(
        _("file"),
        upload_to=upload_to,
        max_length=255,
        validators=[FileExtensionValidator(FILE_EXTENSIONS), MaxFileSize(100)],
        help_text=_("Image, video or zip, up to 100 MB."),
    )
    preview = models.FileField(
        _("preview image"),
        upload_to=upload_to,
        max_length=255,
        blank=True,
        validators=[FileExtensionValidator(PREVIEW_EXTENSIONS), MaxFileSize(5)],
        help_text=_("Shown in the library. Not needed when the file is an image."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    objects = CreativeQuerySet.as_manager()

    class Meta:
        verbose_name = _("creative")
        verbose_name_plural = _("creatives")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "offer_expires_on"], name="creative_usable_idx")]

    def __str__(self):
        return f"{self.code} {self.title}"

    @property
    def code(self):
        """The reference used in briefs and support, e.g. CR-0042 (SRS §4.1)."""
        return f"CR-{self.pk:04d}" if self.pk else "CR-…"

    @property
    def expired(self):
        return self.offer_expires_on is not None and self.offer_expires_on < timezone.localdate()

    @property
    def is_video(self):
        return (mimetypes.guess_type(self.file.name)[0] or "").startswith("video/")

    @property
    def image_url(self):
        """What the library shows: the preview, or the file itself when it's an image."""
        if self.preview:
            return self.preview.url
        if (mimetypes.guess_type(self.file.name)[0] or "").startswith("image/"):
            return self.file.url
        return ""

    @property
    def file_url(self):
        return self.file.url if self.file else ""

    @property
    def opens_in_browser(self):
        """Images and videos open in the browser; zip files are downloaded (leadshub.files)."""
        kind = (mimetypes.guess_type(self.file.name)[0] or "").split("/")[0]
        return kind in ("image", "video")

    def clean(self):
        errors = {}
        if self.project_id:
            project = self.project
            if not self.area_id:
                self.area = project.area
            elif self.area_id != project.area_id:
                errors["area"] = _("This project is in %(area)s.") % {"area": project.area}
            if not self.developer_id:
                self.developer = project.developer
            elif self.developer_id != project.developer_id:
                errors["developer"] = _("This project is by %(developer)s.") % {
                    "developer": project.developer
                }
        if not self.area_id and "area" not in errors:
            errors["area"] = _("Choose an area, or a project.")
        if self.asset_type and self.asset_type != AssetType.LANDING_PAGE and not self.aspect_ratio:
            errors["aspect_ratio"] = _("Choose the aspect ratio.")
        if self.asset_type == AssetType.REEL and self.aspect_ratio not in ("", AspectRatio.PORTRAIT):
            errors["aspect_ratio"] = _("A reel is 9:16.")
        if errors:
            raise ValidationError(errors)
