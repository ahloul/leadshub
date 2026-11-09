from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Area, Developer, Project, UnitType


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
