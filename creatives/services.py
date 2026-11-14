"""Creative library rules (SRS §3.2)."""

from django.db.models import Q

from .models import Creative, Status


def may_open_file(user, name):
    """Whether ``user`` may open the creative file ``name`` (leadshub.files).

    Staff who can view creatives (Content Managers, Platform Admins) may open
    any. Brokers may open only the files of assets they can use: published,
    and the offer still on (FR-5.7).
    """
    creative = Creative.objects.filter(Q(file=name) | Q(preview=name)).first()
    if creative is None:
        return False
    if user.has_perm("creatives.view_creative"):
        return True
    return (
        user.has_perm("accounts.access_client_portal")
        and Creative.objects.usable().filter(pk=creative.pk).exists()
    )


def set_status(creatives, status):
    """Publish, unpublish or archive ``creatives`` (a queryset). Returns how many changed."""
    return creatives.exclude(status=status).update(status=Status(status))
