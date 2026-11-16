"""Sidebar navigation: the pages the signed-in person may open (ROADMAP M1).

The menu itself is ``leadshub.navigation.MENU``, shared with the admin's
sidebar. The sidebar sits outside ``dj-root``, so it is rendered once over
HTTP. djust keeps ``aria-current`` on its ``dj-navigate`` links in step as
the person moves between live pages.
"""

from django.utils.module_loading import import_string

from .icons import PATHS, VIEWBOX
from .navigation import menu_for


def navigation(request):
    user = getattr(request, "user", None)
    sections = []
    for title, items in menu_for(user):
        links = []
        for item in items:
            url = str(item["url"])
            badge = import_string(item["badge"])(request) if item.get("badge") else None
            links.append(
                {
                    "label": item["label"],
                    "url": url,
                    "icon": PATHS[item["icon"]],
                    "live": item["live"],
                    "badge": badge,
                    "current": url == request.path,
                }
            )
        sections.append(
            {
                "title": title,
                "links": links,
                "open": any(link["current"] for link in links),
                # Shown on a folded section's title, e.g. sign-ups waiting.
                "badge": sum(link["badge"] or 0 for link in links) or None,
            }
        )
    return {
        "nav_icon_viewbox": VIEWBOX,
        "nav_chevron": PATHS["chevron_right"],
        "nav_sections": sections,
        "nav_links": [link for section in sections for link in section["links"]],
    }
