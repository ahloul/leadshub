"""Reads of the creative library."""

from catalog import selectors as catalog_selectors

from .models import AssetType, Creative, Language  # noqa: F401 (the choices, for views)


def library(*, area_id=None, project_id=None, asset_type="", language="", today=None):
    """Creatives a broker may browse, newest first, with the library's filters applied (FR-5.7)."""
    creatives = Creative.objects.usable(today).select_related(
        "developer", "project", "area", "unit_type"
    )
    if area_id:
        creatives = creatives.filter(area_id=area_id)
    if project_id:
        creatives = creatives.filter(project_id=project_id)
    if asset_type:
        creatives = creatives.filter(asset_type=asset_type)
    if language:
        creatives = creatives.filter(language=language)
    return creatives.order_by("-created_at", "-pk")


def library_areas(today=None):
    """Areas that have assets a broker can use, for the library's filter."""
    return catalog_selectors.areas(ids=Creative.objects.usable(today).values("area_id"))


def library_projects(*, area_id=None, today=None):
    """Projects that have assets a broker can use, in ``area_id`` if given."""
    usable = Creative.objects.usable(today).exclude(project=None)
    return catalog_selectors.projects(ids=usable.values("project_id"), area_id=area_id)
