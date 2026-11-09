from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, Value, When
from django.utils.text import capfirst, format_lazy
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.widgets import UnfoldAdminCheckboxSelectMultipleWidget

from .models import BrokerOffice, Membership, RoleProfile, User
from .roles import (
    AUDIENCE_ENTRY,
    AUDIENCE_PERMISSIONS,
    CAPABILITIES,
    REQUIRED_PERMISSIONS,
    Role,
    permissions_for,
    role_errors,
)

_CAPABILITY_LABELS = dict(CAPABILITIES)
_ADMIN_ACTIONS = {
    "view": _("view"),
    "add": _("add"),
    "change": _("change"),
    "delete": _("delete"),
}


class MembershipInline(StackedInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["office"]


class RoleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, group):
        return group.role_profile.display_name


class RoleCheckedUserChangeForm(UserChangeForm):
    """Picks roles by name; refuses combinations that cross audiences (role_errors)."""

    groups = RoleChoiceField(
        queryset=Group.objects.filter(role_profile__isnull=False).select_related("role_profile"),
        required=False,
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        label=_("roles"),
        help_text=_("A user can have several roles, all internal, all broker, or all developer."),
    )

    def clean(self):
        cleaned = super().clean()
        groups = cleaned.get("groups")
        if groups is not None:
            errors = role_errors(list(groups), developer=cleaned.get("developer"))
            if errors:
                raise ValidationError(errors)
        return cleaned


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    form = RoleCheckedUserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    # is_staff is not used: admin access comes from the role
    # (accounts.admin_site), so it's left out to avoid a misleading switch.
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "email")}),
        (
            _("Roles and permissions"),
            {"fields": ("is_active", "groups", "developer", "is_superuser", "user_permissions")},
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + ((None, {"fields": ["email"]}),)
    list_display = ["username", "email", "first_name", "last_name", "role_names", "is_active"]
    list_filter = ["groups__role_profile__audience", "groups", "is_active"]
    autocomplete_fields = ["developer"]
    inlines = [MembershipInline]

    @admin.display(description=_("roles"))
    def role_names(self, obj):
        return ", ".join(obj.role_labels())

    def get_queryset(self, request):
        qs = super().get_queryset(request).prefetch_related("groups")
        if not request.user.is_superuser:
            # Only a superuser manages superusers.
            qs = qs.filter(is_superuser=False)
        return qs

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None or request.user.is_superuser:
            return fieldsets
        # Role assignment only: superuser status and one-off permissions stay
        # with superusers, so a Platform Admin can't grant themselves more.
        hidden = {"is_superuser", "user_permissions"}
        return [
            (name, {**opts, "fields": [f for f in opts["fields"] if f not in hidden]})
            for name, opts in fieldsets
        ]


class RoleForm(forms.ModelForm):
    """A role's names, audience and permissions; keeps its Django group in step."""

    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(),
        required=False,
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        label=_("Permissions"),
    )

    class Meta:
        model = RoleProfile
        fields = ["name_en", "name_ar", "audience", "permissions"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assignable = set().union(*AUDIENCE_PERMISSIONS.values())
        field = self.fields["permissions"]
        # Business capabilities first, in matrix order; then admin permissions.
        capability_order = Case(
            *[
                When(content_type__app_label="accounts", codename=code, then=Value(i))
                for i, (code, _label) in enumerate(CAPABILITIES)
            ],
            default=Value(len(CAPABILITIES)),
        )
        field.queryset = (
            permissions_for(assignable)
            .select_related("content_type")
            .order_by(capability_order, "content_type__app_label", "content_type__model", "codename")
        )
        field.label_from_instance = self._permission_label
        field.help_text = _(
            "Internal roles may use any of these. Broker roles: create and run campaigns, "
            "wallet, leads, team. Developer roles: aggregate project metrics."
        )
        if self.instance.pk:
            entries = set(AUDIENCE_ENTRY.values())
            self.initial["permissions"] = [
                p.pk for p in self.instance.group.permissions.all()
                if f"{p.content_type.app_label}.{p.codename}" not in entries
            ]

    @staticmethod
    def _permission_label(perm):
        if perm.content_type.app_label == "accounts" and perm.codename in _CAPABILITY_LABELS:
            return _CAPABILITY_LABELS[perm.codename]
        # Django stores "Can add user" in English; build it in the page language.
        action = perm.codename.split("_", 1)[0]
        model = perm.content_type.model_class()
        if action in _ADMIN_ACTIONS and model is not None:
            return format_lazy(
                "{}: {}", capfirst(model._meta.verbose_name_plural), _ADMIN_ACTIONS[action]
            )
        return perm.name

    def clean_name_en(self):
        name = self.cleaned_data["name_en"].strip()
        clash = Group.objects.filter(name=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.group_id)
        if clash.exists() or name in Role.values:
            raise ValidationError(_("A role with this name already exists."))
        return name

    def clean(self):
        cleaned = super().clean()
        audience = cleaned.get("audience") or self.instance.audience
        perms = cleaned.get("permissions") or []
        if audience:
            allowed = AUDIENCE_PERMISSIONS[audience]
            refused = [
                self._permission_label(p) for p in perms
                if f"{p.content_type.app_label}.{p.codename}" not in allowed
            ]
            if refused:
                raise ValidationError(
                    _("Not allowed for this audience: %(perms)s"),
                    params={"perms": ", ".join(str(r) for r in refused)},
                )
        if self.instance.is_system:
            required = REQUIRED_PERMISSIONS.get(Role(self.instance.group.name), frozenset())
            chosen = {f"{p.content_type.app_label}.{p.codename}" for p in perms}
            missing = permissions_for(required - chosen).select_related("content_type")
            if missing:
                raise ValidationError(
                    _("This role must keep: %(perms)s"),
                    params={"perms": ", ".join(str(self._permission_label(p)) for p in missing)},
                )
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        role = super().save(commit=False)
        if role.pk:
            # An essential role's group name is fixed (the code refers to it).
            if not role.is_system:
                role.group.name = role.name_en
                role.group.save()
        else:
            role.group = Group.objects.create(name=role.name_en)
        role.save()
        entry = permissions_for([AUDIENCE_ENTRY[role.audience]])
        role.group.permissions.set([*self.cleaned_data["permissions"], *entry])
        return role

    def save_m2m(self):  # permissions are saved on the group in save()
        pass


@admin.register(RoleProfile)
class RoleAdmin(ModelAdmin):
    """Any role's permissions can be edited. Essential roles keep their names and
    can't be deleted; custom roles can be added, renamed and removed."""

    form = RoleForm
    list_display = ["name_en", "name_ar", "audience", "is_system", "member_count"]
    list_filter = ["audience", "is_system"]
    search_fields = ["name_en", "name_ar"]

    @admin.display(description=_("members"))
    def member_count(self, obj):
        return obj.group.user_set.count()

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        if obj.is_system:
            # The code and seed data refer to essential roles by name.
            return ["name_en", "name_ar", "audience"]
        # Changing a used role's audience could break its members' role rules.
        return ["audience"]

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_system:
            return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        obj.group.delete()  # cascades to the profile

    def delete_queryset(self, request, queryset):
        Group.objects.filter(role_profile__in=queryset.filter(is_system=False)).delete()


# Roles are managed above; a bare group would have no audience.
admin.site.unregister(Group)


class OfficeMembershipInline(TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(BrokerOffice)
class BrokerOfficeAdmin(ModelAdmin):
    list_display = ["company_name", "spend_tier", "feedback_score", "created_at"]
    list_filter = ["spend_tier"]
    search_fields = ["company_name"]
    inlines = [OfficeMembershipInline]
