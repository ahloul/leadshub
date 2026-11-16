"""Admin screens for Business Managers and ad accounts (US-META-01).

Access follows model permissions (AGENTS.md, permission step 6):

- Platform Admin: connect Business Managers with Facebook Login for
  Business (no typed tokens), list, rename and delete them, and run Sync,
  Recheck token, Deactivate and the launch switches. Ad accounts are
  created, listed, updated and deleted here. Ad accounts get in only
  two ways: added by hand here, or by Sync. Nothing runs on a schedule.
- Media Buying Lead: view only. Django's admin renders the forms read-only
  and hides every action (``permissions=["change"]``) and delete.
- Deleting a Business Manager deletes its ad accounts. Once campaigns exist
  they reference ad accounts with ``PROTECT``, so anything a campaign uses
  can't be deleted (criterion 19).
"""

import secrets
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils import timezone, translation
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.decorators.debug import sensitive_variables
from unfold.admin import ModelAdmin
from unfold.decorators import action, display

from .meta_ads import sync as meta_sync
from .models import MetaAdAccount, MetaBusinessManager

_TOKEN_LABELS = {
    MetaBusinessManager.TokenStatus.VALID: "success",
    MetaBusinessManager.TokenStatus.INVALID: "danger",
    MetaBusinessManager.TokenStatus.UNCHECKED: "warning",
}


class MetaBusinessManagerForm(forms.ModelForm):
    """Only the name is edited here. The business ID and token come from
    Facebook Login for Business (connect_view), so nobody types a token."""

    class Meta:
        model = MetaBusinessManager
        fields = ["name"]


# Session key for one Facebook Login round trip: the CSRF state, the
# Business Manager being reconnected (or None) and the admin's language.
CONNECT_SESSION_KEY = "meta_connect"


@admin.register(MetaBusinessManager)
class MetaBusinessManagerAdmin(ModelAdmin):
    form = MetaBusinessManagerForm
    # criterion 20
    list_display = [
        "name",
        "meta_business_id",
        "token_badge",
        "account_counts",
        "last_synced_at",
        "sync_badge",
        "is_active",
    ]
    list_filter = ["is_active", "token_status"]
    search_fields = ["name", "meta_business_id"]
    readonly_fields = [
        "meta_business_id",
        "masked_token",
        "token_status",
        "token_checked_at",
        "token_error",
        "is_active",
        "last_synced_at",
        "last_sync_failed_at",
        "last_sync_error",
    ]
    actions = ["sync_accounts", "recheck_tokens", "deactivate", "reactivate", "delete_selected"]
    # Business Managers are added with "Connect with Facebook", not the Add form.
    show_add_link = False
    actions_list = ["connect_with_facebook"]
    actions_detail = ["reconnect_with_facebook"]
    # A "Save and sync" button on the edit page (POST, like Save).
    actions_submit_line = ["save_and_sync"]

    # Facebook Login for Business (US-META-01, FR-6.10). The admin is sent to
    # Meta's dialog and back; the server trades the code for a system-user
    # token, so nobody copies a token.

    def get_urls(self):
        view = self.admin_site.admin_view
        info = self.opts.app_label, self.opts.model_name
        return [
            path("connect/", view(self.connect_view), name="%s_%s_connect" % info),
            path("connect/callback/", view(self.callback_view), name="%s_%s_connect_callback" % info),
            *super().get_urls(),
        ]

    @staticmethod
    def login_configured() -> bool:
        return bool(settings.META_APP_ID and settings.META_LOGIN_CONFIG_ID)

    def _url(self, name, *args, lang=None):
        info = self.opts.app_label, self.opts.model_name
        with translation.override(lang or translation.get_language()):
            return reverse(f"admin:{info[0]}_{info[1]}_{name}", args=args)

    def _callback_uri(self, request):
        # Meta matches redirect_uri exactly, so it is the same whatever the
        # admin's language: register only the /en/ one in the Meta app.
        if settings.META_LOGIN_REDIRECT_URI:  # Behind a proxy the request looks like http.
            return settings.META_LOGIN_REDIRECT_URI
        return request.build_absolute_uri(self._url("connect_callback", lang="en"))

    def _target(self, request, pk):
        """The Business Manager being reconnected (or None), with the permission checked."""
        if pk is None:
            if not self.has_add_permission(request):
                raise PermissionDenied
            return None
        try:
            bm = get_object_or_404(MetaBusinessManager, pk=int(pk))
        except (TypeError, ValueError):
            raise Http404 from None
        if not self.has_change_permission(request, bm):
            raise PermissionDenied
        return bm

    def connect_view(self, request):
        bm = self._target(request, request.GET.get("bm"))
        back = self._url("change", bm.pk) if bm else self._url("changelist")
        if not self.login_configured():
            self.message_user(
                request,
                _("Facebook Login isn't set up: set META_APP_ID and META_LOGIN_CONFIG_ID."),
                messages.ERROR,
            )
            return HttpResponseRedirect(back)
        state = secrets.token_urlsafe(32)
        request.session[CONNECT_SESSION_KEY] = {
            "state": state,
            "bm": bm.pk if bm else None,
            "lang": translation.get_language(),
        }
        query = urlencode(
            {
                "client_id": settings.META_APP_ID,
                "redirect_uri": self._callback_uri(request),
                "state": state,
                "config_id": settings.META_LOGIN_CONFIG_ID,
                "response_type": "code",
                "override_default_response_type": "true",
            }
        )
        return HttpResponseRedirect(f"https://www.facebook.com/{settings.META_API_VERSION}/dialog/oauth?{query}")

    @method_decorator(sensitive_variables("code"))
    def callback_view(self, request):
        pending = request.session.pop(CONNECT_SESSION_KEY, None)  # Single use.
        state = request.GET.get("state", "")
        if not pending or not state or not secrets.compare_digest(state, pending["state"]):
            raise PermissionDenied
        # The callback always arrives on /en/ (one registered URL), so answer
        # in the language the admin started in.
        with translation.override(pending.get("lang") or translation.get_language()):
            return self._finish_connect(request, pending)

    @method_decorator(sensitive_variables("code"))
    def _finish_connect(self, request, pending):
        bm = self._target(request, pending["bm"])
        lang = translation.get_language()
        back = self._url("change", bm.pk, lang=lang) if bm else self._url("changelist", lang=lang)
        code = request.GET.get("code", "")
        if request.GET.get("error") or not code:
            # str() now: lazy text would be rendered later, outside the language override.
            self.message_user(request, str(_("Facebook login was cancelled. Nothing was changed.")), messages.WARNING)
            return HttpResponseRedirect(back)
        try:
            connected, created = meta_sync.connect_business(
                code,
                self._callback_uri(request),
                expected=bm,
                may_replace=lambda existing: self.has_change_permission(request, existing),
            )
        except meta_sync.ConnectError as error:
            self.message_user(request, str(error), messages.ERROR)
            return HttpResponseRedirect(back)
        if created:
            self.log_addition(request, connected, [{"added": {}}])
        else:
            self.log_change(request, connected, [{"changed": {"fields": ["token"]}}])
        self.message_user(request, _("Connected %(bm)s.") % {"bm": connected}, messages.SUCCESS)
        return HttpResponseRedirect(self._url("change", connected.pk, lang=lang))

    def add_view(self, request, form_url="", extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied
        return HttpResponseRedirect(self._url("connect"))

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        # "Save and add another" would lead to the add page, which is now Facebook Login.
        extra_context = {**(extra_context or {}), "show_save_and_add_another": False}
        return super().changeform_view(request, object_id, form_url, extra_context)

    def has_connect_permission(self, request):
        return self.has_add_permission(request) and self.login_configured()

    def has_reconnect_permission(self, request, object_id):
        bm = self.get_object(request, object_id)
        return bm is not None and self.has_change_permission(request, bm) and self.login_configured()

    @action(description=_("Connect with Facebook"), permissions=["connect"], icon="add")
    def connect_with_facebook(self, request):
        return HttpResponseRedirect(self._url("connect"))

    @action(description=_("Reconnect with Facebook"), permissions=["reconnect"], icon="sync_lock")
    def reconnect_with_facebook(self, request, object_id):
        bm = self.get_object(request, object_id)
        if bm is None:
            raise Http404
        return HttpResponseRedirect(f"{self._url('connect')}?{urlencode({'bm': bm.pk})}")

    def get_fieldsets(self, request, obj=None):
        return [
            (None, {"fields": ["name", "meta_business_id"]}),
            (_("Token"), {"fields": ["masked_token", "token_status", "token_checked_at", "token_error"]}),
            (_("Sync"), {"fields": ["is_active", "last_synced_at", "last_sync_failed_at", "last_sync_error"]}),
        ]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                accounts_total=Count("ad_accounts", filter=Q(ad_accounts__is_removed=False)),
                accounts_enabled=Count(
                    "ad_accounts",
                    filter=Q(ad_accounts__is_removed=False, ad_accounts__enabled_for_launch=True),
                ),
            )
        )

    @display(description=_("token"))
    def masked_token(self, obj):
        return obj.masked_token

    @display(description=_("token status"), label=_TOKEN_LABELS)
    def token_badge(self, obj):
        return obj.token_status, obj.get_token_status_display()

    @display(description=_("ad accounts (enabled / total)"))
    def account_counts(self, obj):
        return f"{obj.accounts_enabled} / {obj.accounts_total}"

    @display(description=_("last sync"), label={"ok": "success", "failed": "danger", "never": "info"})
    def sync_badge(self, obj):
        # Failed more recently than it last succeeded (criterion 13).
        if obj.last_sync_failed_at and (not obj.last_synced_at or obj.last_sync_failed_at > obj.last_synced_at):
            return "failed", _("Failed")
        if obj.last_synced_at:
            return "ok", _("OK")
        return "never", _("Never synced")

    @admin.action(description=_("Sync ad accounts from Meta"), permissions=["change"])
    def sync_accounts(self, request, queryset):
        for bm in queryset:
            self._sync(request, bm)

    @action(description=_("Save and sync"), permissions=["change"])
    def save_and_sync(self, request, obj):
        self._sync(request, obj)

    def _sync(self, request, bm):
        """Sync one Business Manager and report the result as a message."""
        try:
            result = meta_sync.sync_business_manager(bm)
        except meta_sync.SyncError as error:
            self.message_user(
                request,
                _("%(bm)s: sync failed: %(reason)s") % {"bm": bm, "reason": error},
                messages.ERROR,
            )
        else:
            self.message_user(
                request,
                _("%(bm)s: %(new)d new, %(updated)d updated, %(removed)d removed.")
                % {"bm": bm, "new": result.created, "updated": result.updated, "removed": result.removed},
                messages.SUCCESS,
            )

    @admin.action(description=_("Recheck token with Meta"), permissions=["change"])
    def recheck_tokens(self, request, queryset):
        for bm in queryset:
            meta_sync.recheck_token(bm)
            level = messages.SUCCESS if bm.token_is_valid else messages.WARNING
            text = _("%(bm)s: token is %(status)s.") % {"bm": bm, "status": bm.get_token_status_display()}
            if bm.token_error:
                text = f"{text} {bm.token_error}"
            self.message_user(request, text, level)

    @admin.action(description=_("Deactivate (stop all launches)"), permissions=["change"])
    def deactivate(self, request, queryset):
        count = queryset.update(is_active=False, updated_at=timezone.now())
        self.message_user(
            request,
            ngettext(
                "%(count)d Business Manager deactivated; its ad accounts are no longer offered for launch.",
                "%(count)d Business Managers deactivated; their ad accounts are no longer offered for launch.",
                count,
            )
            % {"count": count},
        )

    @admin.action(description=_("Reactivate"), permissions=["change"])
    def reactivate(self, request, queryset):
        count = queryset.update(is_active=True, updated_at=timezone.now())
        self.message_user(
            request,
            ngettext("%(count)d Business Manager reactivated.", "%(count)d Business Managers reactivated.", count)
            % {"count": count},
        )


class LaunchReadyFilter(admin.SimpleListFilter):
    """Accounts new campaigns can use, and the ones flagged (criterion 17)."""

    title = _("launch")
    parameter_name = "launch"

    def lookups(self, request, model_admin):
        return [("available", _("Available for launch")), ("flagged", _("Enabled but blocked"))]

    def queryset(self, request, queryset):
        available = MetaAdAccount.objects.available_for_launch()
        if self.value() == "available":
            return queryset.filter(pk__in=available)
        if self.value() == "flagged":
            return queryset.filter(enabled_for_launch=True).exclude(pk__in=available)
        return queryset


_META_FIELDS = ["business_manager", "meta_account_id", "name", "account_status", "ownership", "currency", "timezone_name"]


@admin.register(MetaAdAccount)
class MetaAdAccountAdmin(ModelAdmin):
    # criterion 21
    list_display = [
        "name",
        "meta_account_id",
        "business_manager",
        "status_badge",
        "enabled_for_launch",
        "launch_badge",
        "currency",
        "timezone_name",
        "last_synced_at",
    ]
    list_filter = ["business_manager", "account_status", LaunchReadyFilter, "enabled_for_launch", "is_removed"]
    list_select_related = ["business_manager"]
    search_fields = ["name", "meta_account_id"]
    actions = ["enable_for_launch", "disable_for_launch"]

    def get_fields(self, request, obj=None):
        if obj is None:
            return [*_META_FIELDS, "enabled_for_launch"]
        return [*_META_FIELDS, "enabled_for_launch", "is_removed", "last_synced_at", "launch_badge"]

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        # Once synced, Meta owns these and the next sync would overwrite any
        # edit (criterion 11). A hand-added account stays editable until then.
        meta = _META_FIELDS if obj.last_synced_at else []
        return [*meta, "is_removed", "last_synced_at", "launch_badge"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "business_manager":
            kwargs["queryset"] = MetaBusinessManager.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # Every status needs a colour: Unfold shows the raw number for an unmapped one.
    @display(
        description=_("Meta status"),
        label={
            status: "success" if status in (MetaAdAccount.Status.ACTIVE, MetaAdAccount.Status.ANY_ACTIVE) else "danger"
            for status in MetaAdAccount.Status
        },
    )
    def status_badge(self, obj):
        return obj.account_status, obj.get_account_status_display()

    @display(description=_("launch"), label={"ready": "success", "blocked": "danger", "off": "info"})
    def launch_badge(self, obj):
        if problems := obj.launch_blockers():
            return "blocked", ", ".join(str(p) for p in problems)
        if obj.enabled_for_launch:
            return "ready", _("Available")
        return "off", _("Not enabled")

    @admin.action(description=_("Enable for launch"), permissions=["change"])
    def enable_for_launch(self, request, queryset):
        enabled = 0
        for account in queryset.select_related("business_manager").filter(enabled_for_launch=False):
            account.enabled_for_launch = True
            try:
                account.clean()  # The launch rule only (criterion 15); Meta's data isn't re-validated.
            except ValidationError as error:
                self.message_user(
                    request, f"{account}: {' '.join(error.message_dict['enabled_for_launch'])}", messages.ERROR
                )
                continue
            account.save(update_fields=["enabled_for_launch", "updated_at"])
            enabled += 1
        if enabled:
            self.message_user(
                request,
                ngettext("%(count)d ad account enabled.", "%(count)d ad accounts enabled.", enabled)
                % {"count": enabled},
                messages.SUCCESS,
            )

    @admin.action(description=_("Disable for launch"), permissions=["change"])
    def disable_for_launch(self, request, queryset):
        count = queryset.update(enabled_for_launch=False, updated_at=timezone.now())
        self.message_user(
            request,
            ngettext("%(count)d ad account disabled.", "%(count)d ad accounts disabled.", count) % {"count": count},
        )
