"""Campaign admin screens for staff (ROADMAP M3).

Connected accounts: brokers add their own Instagram, Facebook and Google
accounts by hand until the Meta and Google sign-in exists, and staff who
manage ad accounts check them here (SRS §2.2; ROADMAP Q11).
"""

from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from unfold.admin import ModelAdmin

from .models import AccountStatus, ConnectedAccount


@admin.register(ConnectedAccount)
class ConnectedAccountAdmin(ModelAdmin):
    list_display = ["handle", "platform", "office", "status", "created_at"]
    list_filter = ["status", "platform"]
    search_fields = ["handle", "office__company_name"]
    list_select_related = ["office"]
    readonly_fields = ["office", "platform", "handle", "added_by", "verified_at", "created_at"]
    fields = ["office", "platform", "handle", "status", "external_id", "added_by", "verified_at", "created_at"]
    actions = ["mark_verified"]

    def has_add_permission(self, request):
        return False  # brokers add them on their Pages page

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description=_("Mark as verified"), permissions=["change"])
    def mark_verified(self, request, queryset):
        changed = queryset.exclude(status=AccountStatus.VERIFIED).update(
            status=AccountStatus.VERIFIED, verified_at=timezone.now()
        )
        self.message_user(
            request,
            ngettext("%(count)d account marked as verified.", "%(count)d accounts marked as verified.", changed)
            % {"count": changed},
        )

    def save_model(self, request, obj, form, change):
        if "status" in form.changed_data and obj.status == AccountStatus.VERIFIED:
            obj.verified_at = timezone.now()
        super().save_model(request, obj, form, change)
