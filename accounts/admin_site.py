"""The Django admin, opened by role instead of by ``is_staff`` (SRS §2.1).

Django's admin lets in any active user with ``is_staff``. Here only users whose
roles give ``accounts.access_back_office`` (internal staff and superusers) get
in, so a stray ``is_staff`` flag on a broker account opens nothing. What each
person sees inside still follows their model permissions.

Sign-in goes through the app's own login page (lockout, role landing), not the
admin's login form.

The look comes from django-unfold (Tailwind). Its default app config would
replace ``admin.site`` with a plain ``UnfoldAdminSite``, so settings install
``unfold.apps.BasicAppConfig`` (templates and static only) and this site
extends Unfold's.
"""

from django.contrib.admin.apps import AdminConfig
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from unfold.sites import UnfoldAdminSite

BACK_OFFICE = "accounts.access_back_office"


class LeadsHubAdminSite(UnfoldAdminSite):
    """Unfold's Tailwind admin (look and sidebar: ``UNFOLD`` in settings), gated by role."""

    def has_permission(self, request):
        user = request.user
        return user.is_active and user.has_perm(BACK_OFFICE)

    def login(self, request, extra_context=None):
        if request.user.is_authenticated:
            # Signed in without a back-office role: sending them to a login
            # page would just bounce back here.
            if not self.has_permission(request):
                raise PermissionDenied
            return super().login(request, extra_context)
        from django.contrib.auth.views import redirect_to_login

        next_url = request.GET.get("next") or reverse("admin:index", current_app=self.name)
        return redirect_to_login(next_url)


class LeadsHubAdminConfig(AdminConfig):
    """``django.contrib.admin`` using the site above (listed in INSTALLED_APPS)."""

    default_site = "accounts.admin_site.LeadsHubAdminSite"

