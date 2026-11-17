"""One menu for the whole site: the app's sidebar and the admin's (ROADMAP M1).

Internal staff move between live pages (the back office, the review queue)
and admin screens; both sidebars draw ``MENU``, so the menu stays the same
wherever they are. The live pages come first, always shown; each titled
section below folds, open when it holds the current page:

- the app's sidebar: ``context_processors.navigation`` → ``base.html``;
- the admin's (Unfold): ``UNFOLD["SIDEBAR"]["navigation"]`` names
  ``unfold_navigation``, called on each admin page.

Each person sees only the items whose capabilities they hold, the same ones
the page itself requires (hiding is for convenience; the pages enforce it).
A section with nothing left to show is left out. Icons are Material Symbols
names: the admin draws them with its icon font, the app as inline SVG from
``leadshub.icons`` (no font, so no icon names showing as text).
"""

from django.urls import reverse_lazy
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

BACK_OFFICE = "accounts.access_back_office"
PORTAL = "accounts.access_client_portal"
DEVELOPER = "accounts.access_developer_view"


def _page(label, icon, url_name, *perms):
    """A live page: opened with dj-navigate in the app."""
    return {"label": label, "icon": icon, "url": reverse_lazy(url_name), "perms": perms, "live": True}


def _admin(label, icon, url_name, perm, *, query="", badge=None):
    """An admin screen. The admin opens only to internal staff, so it needs the back office too."""
    url = reverse_lazy(url_name)
    if query:
        url = format_lazy("{}?{}", url, query)
    return {"label": label, "icon": icon, "url": url, "perms": (BACK_OFFICE, perm), "live": False,
            "badge": badge}


# (section title or None, items). The first section has the live pages, so
# it needs no title.
MENU = [
    (None, [
        _page(_("Back office"), "space_dashboard", "back_office", BACK_OFFICE),
        _page(_("Review queue"), "fact_check", "review_queue", BACK_OFFICE, "accounts.review_campaign_tasks"),
        _page(_("Dashboard"), "space_dashboard", "dashboard", PORTAL),
        _page(_("Campaigns"), "campaign", "campaigns", PORTAL),
        _page(_("Creative library"), "photo_library", "library", PORTAL),
        _page(_("Team"), "group", "team", PORTAL, "accounts.manage_team"),
        _page(_("Pages and accounts"), "link", "connected_accounts", PORTAL, "accounts.manage_connected_accounts"),
        _page(_("Developer view"), "domain", "developer_home", DEVELOPER),
    ]),
    (_("People and access"), [
        _admin(_("Users"), "person", "admin:accounts_user_changelist", "accounts.view_user"),
        _admin(_("Roles"), "admin_panel_settings", "admin:accounts_roleprofile_changelist",
               "accounts.view_roleprofile"),
        _admin(_("Broker offices"), "apartment", "admin:accounts_brokeroffice_changelist",
               "accounts.view_brokeroffice"),
        _admin(_("Sign-ups to review"), "how_to_reg", "admin:accounts_brokeroffice_changelist",
               "accounts.change_brokeroffice", query="status__exact=pending",
               badge="accounts.admin.pending_signups_badge"),
    ]),
    (_("Master data"), [
        _admin(_("Developers"), "domain", "admin:catalog_developer_changelist", "catalog.view_developer"),
        _admin(_("Projects"), "holiday_village", "admin:catalog_project_changelist", "catalog.view_project"),
        _admin(_("Areas"), "map", "admin:catalog_area_changelist", "catalog.view_area"),
        _admin(_("Unit types"), "category", "admin:catalog_unittype_changelist", "catalog.view_unittype"),
        _admin(_("Audience presets"), "groups", "admin:catalog_audiencepreset_changelist",
               "catalog.view_audiencepreset"),
    ]),
    (_("Creative library"), [
        _admin(_("Creatives"), "photo_library", "admin:creatives_creative_changelist", "creatives.view_creative"),
    ]),
    (_("Campaigns"), [
        _admin(_("Brokers' pages and accounts"), "link", "admin:campaigns_connectedaccount_changelist",
               "campaigns.view_connectedaccount"),
    ]),
    # django-q2 (leadshub/jobs.py, leadshub/admin.py).
    (_("Background jobs"), [
        _admin(_("Schedules"), "schedule", "admin:django_q_schedule_changelist", "django_q.view_schedule"),
        _admin(_("Failed tasks"), "error", "admin:django_q_failure_changelist", "django_q.view_failure"),
        _admin(_("Finished tasks"), "task_alt", "admin:django_q_success_changelist", "django_q.view_success"),
        _admin(_("Waiting tasks"), "pending", "admin:django_q_ormq_changelist", "django_q.view_ormq"),
    ]),
    # US-META-01: Platform Admin edits, Media Buying Lead reads.
    (_("Meta ads"), [
        _admin(_("Business Managers"), "business", "admin:ad_channels_metabusinessmanager_changelist",
               "ad_channels.view_metabusinessmanager"),
        _admin(_("Ad accounts"), "account_balance", "admin:ad_channels_metaadaccount_changelist",
               "ad_channels.view_metaadaccount"),
    ]),
]


def menu_for(user):
    """``[(title, items)]`` with only what ``user`` may open; empty sections left out."""
    if user is None or not user.is_authenticated:
        return []
    sections = []
    for title, items in MENU:
        allowed = [item for item in items if user.has_perms(item["perms"])]
        if allowed:
            sections.append((title, allowed))
    return sections


def unfold_navigation(request):
    """The menu in Unfold's sidebar format (``UNFOLD["SIDEBAR"]["navigation"]``)."""
    groups = []
    for title, items in menu_for(request.user):
        entries = []
        for item in items:
            entry = {"title": item["label"], "icon": item["icon"], "link": item["url"]}
            if item.get("badge"):
                entry["badge"] = item["badge"]
            entries.append(entry)
        # Titled sections fold, as before; Unfold opens the one holding the current page.
        groups.append({"title": title or "", "collapsible": bool(title), "items": entries})
    return groups
