from django.contrib import admin
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from unfold.admin import ModelAdmin
from unfold.decorators import display

from . import services
from .models import Creative, Status


class OfferFilter(admin.SimpleListFilter):
    """Expired assets are flagged for Content Managers (FR-5.5)."""

    title = _("offer")
    parameter_name = "offer"

    def lookups(self, request, model_admin):
        return [
            ("expired", _("Expired")),
            ("running", _("Running")),
            ("open", _("No end date")),
        ]

    def queryset(self, request, queryset):
        today = timezone.localdate()
        if self.value() == "expired":
            return queryset.filter(offer_expires_on__lt=today)
        if self.value() == "running":
            return queryset.filter(offer_expires_on__gte=today)
        if self.value() == "open":
            return queryset.filter(offer_expires_on__isnull=True)
        return queryset


@admin.register(Creative)
class CreativeAdmin(ModelAdmin):
    """The creative library, kept by Content Managers (SRS §2.1, §3.2)."""

    list_display = [
        "code", "title", "asset_type", "language", "area", "project", "status_label", "offer_label",
    ]
    list_display_links = ["code", "title"]
    list_filter = ["status", OfferFilter, "asset_type", "language", "area", "developer"]
    list_select_related = ["area", "project"]
    search_fields = ["title", "offer", "project__name_en", "project__name_ar"]
    autocomplete_fields = ["developer", "project", "area", "unit_type"]
    readonly_fields = ["code", "created_by", "created_at", "updated_at"]
    actions = ["publish", "unpublish", "archive"]
    fieldsets = (
        (None, {"fields": ["title", "offer", "asset_type", "language", "aspect_ratio"]}),
        (_("Where it applies"), {"fields": ["project", "developer", "area", "unit_type"]}),
        (_("Files"), {"fields": ["file", "preview"]}),
        (_("Publishing"), {"fields": ["status", "offer_expires_on"]}),
        (_("Record"), {"fields": ["code", "created_by", "created_at", "updated_at"]}),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @display(
        description=_("status"),
        ordering="status",
        label={Status.PUBLISHED: "success", Status.DRAFT: "info", Status.ARCHIVED: "warning"},
    )
    def status_label(self, obj):
        return obj.status, obj.get_status_display()

    @display(
        description=_("offer"),
        ordering="offer_expires_on",
        label={"expired": "danger", "running": "success", "open": "info"},
    )
    def offer_label(self, obj):
        if obj.offer_expires_on is None:
            return "open", _("No end date")
        date = date_format(obj.offer_expires_on, "SHORT_DATE_FORMAT")
        if obj.expired:
            return "expired", _("Expired %(date)s") % {"date": date}
        return "running", _("Until %(date)s") % {"date": date}

    def _set_status(self, request, queryset, status, message):
        changed = services.set_status(queryset, status)
        self.message_user(request, message(changed) % {"count": changed})

    @admin.action(description=_("Publish"), permissions=["change"])
    def publish(self, request, queryset):
        self._set_status(request, queryset, Status.PUBLISHED, lambda n: ngettext(
            "Published %(count)d creative.", "Published %(count)d creatives.", n))

    @admin.action(description=_("Back to draft"), permissions=["change"])
    def unpublish(self, request, queryset):
        self._set_status(request, queryset, Status.DRAFT, lambda n: ngettext(
            "Moved %(count)d creative back to draft.", "Moved %(count)d creatives back to draft.", n))

    @admin.action(description=_("Archive"), permissions=["change"])
    def archive(self, request, queryset):
        self._set_status(request, queryset, Status.ARCHIVED, lambda n: ngettext(
            "Archived %(count)d creative.", "Archived %(count)d creatives.", n))
