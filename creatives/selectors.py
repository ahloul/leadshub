"""Reads of the creative library."""

from django.db.models import Q

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


def for_campaign(*, project_id=None, area_id=None, developer_id=None, unit_type_id=None, today=None):
    """Creatives a campaign with this target may use (FR-4.2, FR-5.7).

    A specific campaign (``project_id``): the project's own assets and the
    area-wide assets of its area. A general campaign: every asset matching
    its area, developer and unit type, where each one given narrows the list
    (an asset with no unit type suits any). Always usable assets only:
    published, offer still on.
    """
    creatives = Creative.objects.usable(today).select_related("project", "area")
    if project_id:
        if not area_id:
            return Creative.objects.none()
        creatives = creatives.filter(
            Q(project_id=project_id) | Q(project__isnull=True, area_id=area_id)
        )
    else:
        if not (area_id or developer_id or unit_type_id):
            return Creative.objects.none()
        if area_id:
            creatives = creatives.filter(area_id=area_id)
        if developer_id:
            creatives = creatives.filter(developer_id=developer_id)
        if unit_type_id:
            creatives = creatives.filter(Q(unit_type_id=unit_type_id) | Q(unit_type__isnull=True))
    return creatives.order_by("-created_at", "-pk")
