"""Reads of the master catalog for other apps (they don't import its models)."""

from django.db.models import Q

from .models import Area, AudiencePreset, Developer, Project, UnitType


def areas(*, ids=None):
    """Active areas, optionally only ``ids`` (a list or a queryset of ids)."""
    found = Area.objects.filter(is_active=True)
    if ids is not None:
        found = found.filter(pk__in=ids)
    return found.order_by("name_en")


def developers(*, ids=None, active_only=True):
    """Active developers, for the campaign builder (FR-4.1), optionally only ``ids``.

    ``active_only=False`` keeps retired ones, for filtering records that name them.
    """
    found = Developer.objects.filter(is_active=True) if active_only else Developer.objects.all()
    if ids is not None:
        found = found.filter(pk__in=ids)
    return found.order_by("name_en")


def projects(*, ids=None, area_id=None, developer_id=None, active_only=True):
    """Active projects, optionally only ``ids``, those in ``area_id`` or by ``developer_id``.

    ``active_only=False`` keeps retired ones, for filtering records that name them.
    """
    found = Project.objects.filter(is_active=True) if active_only else Project.objects.all()
    found = found.select_related("developer", "area")
    if ids is not None:
        found = found.filter(pk__in=ids)
    if area_id:
        found = found.filter(area_id=area_id)
    if developer_id:
        found = found.filter(developer_id=developer_id)
    return found.order_by("name_en")


def unit_types():
    """Active unit types, for a category campaign (FR-4.1)."""
    return UnitType.objects.filter(is_active=True).order_by("name_en")


def audience_presets(*, area_id=None):
    """Active audience presets for an area (or any area), for brokers to pick (FR-4.5).

    Brokers see only the name: templates must not render the Meta targeting.
    """
    found = AudiencePreset.objects.filter(is_active=True)
    if area_id:
        found = found.filter(Q(area_id=area_id) | Q(area__isnull=True))
    return found.order_by("name_en")
