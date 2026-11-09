"""The roles and permissions matrix (SRS §2.1, §2.2), in one place.

Each role is a Django ``Group`` with a ``RoleProfile`` (bilingual name,
audience). ``sync_role_groups()`` runs after every ``migrate`` and gives each
essential role the permissions in ``ROLE_PERMISSIONS`` when it is first created.
After that the database is the source of truth: a Platform Admin can add and
remove permissions on any role in the admin.

Views check the capabilities with Django's native permission API, e.g.
``permission_required = "accounts.run_campaigns"`` or
``user.has_perm("accounts.manage_wallet")``.

Every page declares the capability it needs (``permission_required``), and
each audience has its own entry capability:

- internal staff: ``access_back_office`` (back office and Django admin)
- broker offices: ``access_client_portal``
- developers: ``access_developer_view``

A user may hold any number of roles, all from one audience (``role_errors``),
so internal staff never reach client pages and clients never reach the back
office.

Roles are dynamic: the essential roles below are created from code (their
names and audiences are fixed; their permissions can be changed in the
admin), and a Platform Admin can add custom roles in the admin (``RoleProfile``),
choosing an audience and permissions allowed for it
(``AUDIENCE_PERMISSIONS``). A custom role gets its audience's entry
capability automatically.

A role can build on another (``ROLE_INCLUDES``). A Media Buyer is promoted to
team leader by adding the Media Buying Lead role, which has every Media Buyer
capability plus the lead's own.

"Conditional" in the matrix (Broker Team Member → create & run campaigns)
means the Broker Owner grants ``accounts.run_campaigns`` to that user
directly (``user.user_permissions``); the team role does not include it.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

# Capability codename → label. Declared on ``accounts.Capability``.
CAPABILITIES = [
    ("view_internal_financials", _("View actual ad spend and media margin")),
    ("review_campaign_tasks", _("Approve or reject campaign tasks")),
    ("manage_master_data", _("Manage master data and ad accounts")),
    ("manage_fx_and_pricing", _("Manage FX rates and price overrides")),
    ("run_campaigns", _("Create and run campaigns")),
    ("manage_wallet", _("Manage wallet and top up funds")),
    ("work_leads", _("Access and work assigned leads")),
    ("view_project_metrics", _("View aggregate project metrics")),
    ("manage_creative_library", _("Manage the creative library")),
    ("manage_team", _("Invite and manage office team members")),
    ("assign_campaign_tasks", _("Assign campaign tasks to media buyers")),
    # Entry to each audience's pages.
    ("access_back_office", _("Open the internal back office")),
    ("access_client_portal", _("Open the broker client portal")),
    ("access_developer_view", _("Open the developer read-only view")),
]


class Role(models.TextChoices):
    """Values are the Django group names."""

    PLATFORM_ADMIN = "Platform Admin", _("Platform Admin")
    MEDIA_BUYING_LEAD = "Media Buying Lead", _("Media Buying Lead")
    MEDIA_BUYER = "Media Buyer", _("Media Buyer")
    CONTENT_MANAGER = "Content Manager", _("Content Manager")
    FINANCE_MANAGER = "Finance Manager", _("Finance Manager")
    BROKER_OWNER = "Broker Owner", _("Broker Owner")
    BROKER_TEAM_MEMBER = "Broker Team Member", _("Broker Team Member")
    DEVELOPER_VIEW = "Developer View", _("Developer Read-Only View")


class Audience(models.TextChoices):
    """Who a role is for. Each audience has its own pages and entry capability."""

    INTERNAL = "internal", _("Internal staff")
    CLIENT = "client", _("Broker office")
    DEVELOPER = "developer", _("Developer")


# The capability every role of an audience gets, which opens its pages.
AUDIENCE_ENTRY = {
    Audience.INTERNAL: "accounts.access_back_office",
    Audience.CLIENT: "accounts.access_client_portal",
    Audience.DEVELOPER: "accounts.access_developer_view",
}

INTERNAL_ROLES = frozenset(
    {
        Role.PLATFORM_ADMIN,
        Role.MEDIA_BUYING_LEAD,
        Role.MEDIA_BUYER,
        Role.CONTENT_MANAGER,
        Role.FINANCE_MANAGER,
    }
)
CLIENT_ROLES = frozenset({Role.BROKER_OWNER, Role.BROKER_TEAM_MEMBER})
DEVELOPER_ROLES = frozenset({Role.DEVELOPER_VIEW})

SYSTEM_ROLE_AUDIENCE = {
    **{role: Audience.INTERNAL for role in INTERNAL_ROLES},
    **{role: Audience.CLIENT for role in CLIENT_ROLES},
    **{role: Audience.DEVELOPER for role in DEVELOPER_ROLES},
}

_CATALOG_ADMIN = [
    f"catalog.{action}_{model}"
    for model in ("developer", "project", "area", "unittype")
    for action in ("view", "add", "change", "delete")
]

# Creating roles and giving them to people, in the admin (SRS §2.1 Platform Admin).
_ROLE_ADMIN = [
    "accounts.view_user",
    "accounts.add_user",
    "accounts.change_user",
    *[f"accounts.{action}_roleprofile" for action in ("view", "add", "change", "delete")],
    *[f"accounts.{action}_membership" for action in ("view", "add", "change", "delete")],
    "accounts.view_brokeroffice",
]

# What a role of each audience may be given. Client and developer roles can
# never get internal capabilities such as actual ad spend (SRS §2.2).
AUDIENCE_PERMISSIONS = {
    Audience.INTERNAL: frozenset(
        {
            "accounts.view_internal_financials",
            "accounts.review_campaign_tasks",
            "accounts.assign_campaign_tasks",
            "accounts.manage_master_data",
            "accounts.manage_fx_and_pricing",
            "accounts.run_campaigns",
            "accounts.manage_wallet",
            "accounts.view_project_metrics",
            "accounts.manage_creative_library",
            *_CATALOG_ADMIN,
            *_ROLE_ADMIN,
        }
    ),
    Audience.CLIENT: frozenset(
        {
            "accounts.run_campaigns",
            "accounts.manage_wallet",
            "accounts.work_leads",
            "accounts.manage_team",
        }
    ),
    Audience.DEVELOPER: frozenset({"accounts.view_project_metrics"}),
}

# The essential roles. Their permissions are fixed here; custom roles are
# made in the admin (accounts.RoleProfile).
ROLE_PERMISSIONS = {
    Role.PLATFORM_ADMIN: [
        "accounts.view_internal_financials",
        "accounts.review_campaign_tasks",
        "accounts.manage_master_data",
        "accounts.manage_fx_and_pricing",
        "accounts.run_campaigns",
        "accounts.manage_wallet",
        "accounts.view_project_metrics",
        "accounts.manage_creative_library",
        "accounts.assign_campaign_tasks",
        *_CATALOG_ADMIN,
        *_ROLE_ADMIN,
    ],
    # Plus everything a Media Buyer has (ROLE_INCLUDES).
    Role.MEDIA_BUYING_LEAD: [
        "accounts.manage_master_data",
        "accounts.assign_campaign_tasks",
        *_CATALOG_ADMIN,
    ],
    Role.MEDIA_BUYER: [
        "accounts.view_internal_financials",
        "accounts.review_campaign_tasks",
        "accounts.view_project_metrics",
    ],
    Role.CONTENT_MANAGER: [
        "accounts.manage_creative_library",
    ],
    Role.FINANCE_MANAGER: [
        "accounts.view_internal_financials",
        "accounts.manage_fx_and_pricing",
        "accounts.manage_wallet",
        "accounts.view_project_metrics",
    ],
    Role.BROKER_OWNER: [
        "accounts.run_campaigns",
        "accounts.manage_wallet",
        "accounts.work_leads",
        "accounts.manage_team",
    ],
    Role.BROKER_TEAM_MEMBER: [
        "accounts.work_leads",
    ],
    Role.DEVELOPER_VIEW: [
        "accounts.view_project_metrics",
    ],
}


# A role on the left also has every capability of the roles on the right.
ROLE_INCLUDES = {
    Role.MEDIA_BUYING_LEAD: [Role.MEDIA_BUYER],
}


def role_permissions(role):
    """All permission names of an essential role: its own, its included roles', and its entry."""
    role = Role(role)
    names = set(ROLE_PERMISSIONS[role])
    for included in ROLE_INCLUDES.get(role, ()):
        names |= role_permissions(included)
    names.add(AUDIENCE_ENTRY[SYSTEM_ROLE_AUDIENCE[role]])
    return names


def _audience_of(role):
    """The audience of an essential ``Role`` (or its name) or of a role ``Group``."""
    if isinstance(role, str):
        return SYSTEM_ROLE_AUDIENCE[Role(role)]
    profile = getattr(role, "role_profile", None)
    return profile.audience if profile is not None else None


def role_errors(roles, *, developer=None):
    """Why these roles can't be given to one user (empty list if they can).

    ``roles`` are essential ``Role`` values or role ``Group`` objects. A user
    may hold any number of roles, all from one audience.
    """
    audiences = {_audience_of(role) for role in roles} - {None}
    errors = []
    if len(audiences) > 1:
        errors.append(
            _("A user's roles must all be internal, all broker, or all developer roles.")
        )
    if Audience.DEVELOPER in audiences and developer is None:
        errors.append(_("Choose the developer this read-only account belongs to."))
    if developer is not None and Audience.DEVELOPER not in audiences:
        errors.append(_("Only developer accounts are linked to a developer."))
    return errors


def permissions_for(names):
    """``Permission`` objects for ``app_label.codename`` names."""
    from django.contrib.auth.models import Permission
    from django.db.models import Q

    query = Q(pk__in=[])
    for name in names:
        app_label, codename = name.split(".")
        query |= Q(content_type__app_label=app_label, codename=codename)
    return Permission.objects.filter(query)


# Permissions a role must keep whatever is edited in the admin, so nobody
# locks the platform out of managing roles and users.
REQUIRED_PERMISSIONS = {
    Role.PLATFORM_ADMIN: frozenset(
        {
            "accounts.view_user",
            "accounts.change_user",
            "accounts.view_roleprofile",
            "accounts.change_roleprofile",
        }
    ),
}


def sync_role_groups(**kwargs):
    """Create any missing essential role, with its default permissions.

    Connected to ``post_migrate``, so it also runs when the test database is
    created. The database is the source of truth: an existing role keeps the
    permissions set in the admin and only gets back what it must always have
    (its audience entry and ``REQUIRED_PERMISSIONS``). Custom roles are left
    alone. ``manage.py reset_role_permissions`` restores the defaults.
    """
    from django.contrib.auth.models import Group
    from django.utils import translation

    from .models import RoleProfile

    for role in Role:
        names = role_permissions(role)
        defaults = list(permissions_for(names))
        if len(defaults) != len(names):
            # Not all created yet (partial migrate); the next migrate completes it.
            return
        group, created = Group.objects.get_or_create(name=role.value)
        if created:
            group.permissions.set(defaults)
        else:
            must_keep = {AUDIENCE_ENTRY[SYSTEM_ROLE_AUDIENCE[role]]}
            must_keep |= REQUIRED_PERMISSIONS.get(role, frozenset())
            group.permissions.add(*permissions_for(must_keep))
        with translation.override("en"):
            name_en = str(role.label)
        with translation.override("ar"):
            name_ar = str(role.label)
        RoleProfile.objects.update_or_create(
            group=group,
            defaults={
                "name_en": name_en,
                "name_ar": name_ar,
                "audience": SYSTEM_ROLE_AUDIENCE[role],
                "is_system": True,
            },
        )


def reset_role_permissions(roles=None):
    """Put essential roles (all, or the given ones) back to the defaults above."""
    from django.contrib.auth.models import Group

    for role in roles or list(Role):
        role = Role(role)
        Group.objects.get(name=role.value).permissions.set(
            permissions_for(role_permissions(role))
        )
