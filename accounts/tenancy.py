"""Tenant resolution: the signed-in user's broker office (SRS §7.2).

Configured as djust's custom tenant resolver (``DJUST_CONFIG`` in settings).
djust then binds the office for HTTP requests (``TenantMiddleware``) and for
the live WebSocket path, and ``TenantManager`` querysets return nothing when
no office is bound.
"""

from djust.tenants import TenantInfo

from .models import Membership


def resolve_office(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    membership = Membership.objects.select_related("office").filter(user=user).first()
    if membership is None:
        return None
    office = membership.office
    return TenantInfo(tenant_id=str(office.pk), name=office.company_name, raw=office)
