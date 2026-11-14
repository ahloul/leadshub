"""Reads of the master catalog for other apps (they don't import its models)."""

from django.db.models import Q

from .models import Area, AudiencePreset, Project


def areas(*, ids=None):
    """Active areas, optionally only ``ids`` (a list or a queryset of ids)."""
    found = Area.objects.filter(is_active=True)
    if ids is not None:
        found = found.filter(pk__in=ids)
    return found.order_by("name_en")


def projects(*, ids=None, area_id=None):
    """Active projects, optionally only ``ids`` and those in ``area_id``."""
    found = Project.objects.filter(is_active=True).select_related("developer", "area")
    if ids is not None:
        found = found.filter(pk__in=ids)
    if area_id:
        found = found.filter(area_id=area_id)
    return found.order_by("name_en")


def audience_presets(*, area_id=None):
    """Active audience presets for an area (or any area), for brokers to pick (FR-4.5).

    Brokers see only the name: templates must not render the Meta targeting.
    """
    found = AudiencePreset.objects.filter(is_active=True)
    if area_id:
        found = found.filter(Q(area_id=area_id) | Q(area__isnull=True))
    return found.order_by("name_en")
