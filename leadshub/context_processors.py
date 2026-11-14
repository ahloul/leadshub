"""Sidebar navigation: the pages the signed-in person may open (ROADMAP M1).

The sidebar sits outside ``dj-root``, so it is rendered once over HTTP. djust
keeps ``aria-current`` on its ``dj-navigate`` links in step as the person moves
between live pages.
"""

from django.contrib import admin
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

# Outline icons (24x24 SVG path data, drawn with a 2px stroke).
ICONS = {
    "office": "M4 21V5h10v16M14 9h6v12M2 21h20M8 9h2M8 13h2M8 17h2",
    "dashboard": "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    "library": "M4 5h16v14H4zM4 15l4-4 5 5 3-3 4 4M15 9h.01",
    "team": "M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20a6 6 0 0 1 12 0M16 5.5a3 3 0 0 1 0 5.5M21 20a6 6 0 0 0-4-5.6",
    "developer": "M3 11l9-7 9 7M5 9.5V20h14V9.5M10 20v-5h4v5",
    "admin": "M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1M15 4v4M9 10v4M17 16v4",
}

# Live pages: (label, URL name, icon, capabilities needed). A link shows only
# to people who hold every capability, the same ones the page requires.
LIVE_PAGES = [
    (_("Back office"), "back_office", "office", ["accounts.access_back_office"]),
    (_("Dashboard"), "dashboard", "dashboard", ["accounts.access_client_portal"]),
    (_("Creative library"), "library", "library", ["accounts.access_client_portal"]),
    (_("Team"), "team", "team", ["accounts.access_client_portal", "accounts.manage_team"]),
    (_("Developer view"), "developer_home", "developer", ["accounts.access_developer_view"]),
]


def navigation(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_links": []}
    links = [
        {"label": label, "url": reverse(name), "icon": ICONS[icon], "live": True}
        for label, name, icon, perms in LIVE_PAGES
        if user.has_perms(perms)
    ]
    # The admin isn't a live page: an ordinary link. Shown, as on the back
    # office page, only to people with at least one admin section.
    if user.has_perm("accounts.access_back_office") and admin.site.get_app_list(request):
        links.append(
            {"label": _("Admin"), "url": reverse("admin:index"), "icon": ICONS["admin"], "live": False}
        )
    for link in links:
        link["current"] = link["url"] == request.path
    return {"nav_links": links}
