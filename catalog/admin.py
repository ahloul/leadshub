from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from .models import Area, AudiencePreset, Developer, Project, UnitType


class BilingualNamedAdmin(ModelAdmin):
    list_display = ["name_en", "name_ar", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name_en", "name_ar"]


admin.site.register(Developer, BilingualNamedAdmin)
admin.site.register(Area, BilingualNamedAdmin)
admin.site.register(UnitType, BilingualNamedAdmin)


@admin.register(Project)
class ProjectAdmin(BilingualNamedAdmin):
    list_display = ["name_en", "name_ar", "developer", "area", "commission_rate_percent", "is_active"]
    list_filter = ["is_active", "developer", "area"]
    list_select_related = ["developer", "area"]
    autocomplete_fields = ["developer", "area"]
    filter_horizontal = ["unit_types"]


@admin.register(AudiencePreset)
class AudiencePresetAdmin(BilingualNamedAdmin):
    """Audiences brokers pick by name; the targeting below stays internal (FR-4.5, FR-5.14)."""

    list_display = ["name_en", "name_ar", "area", "is_active"]
    list_filter = ["is_active", "area"]
    list_select_related = ["area"]
    autocomplete_fields = ["area"]
    fieldsets = (
        (None, {"fields": ["name_en", "name_ar", "area", "is_active"]}),
        (_("Meta targeting (internal)"), {"fields": ["meta_interests", "lookalike_source", "geo_targeting"]}),
    )
