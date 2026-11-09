from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, resolve_url

from .forms import LoginForm

# Each audience's home page, by its entry capability (accounts.roles.AUDIENCES).
_LANDINGS = (
    ("accounts.access_back_office", "back_office"),
    ("accounts.access_client_portal", "dashboard"),
    ("accounts.access_developer_view", "developer_home"),
)


def landing_url(user):
    """The home page for this user's roles, or None if they have no role."""
    for perm, url_name in _LANDINGS:
        if user.has_perm(perm):
            return resolve_url(url_name)
    return None


class LoginView(auth_views.LoginView):
    """Django's login view; after sign-in each kind of user lands on their own area."""

    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        return landing_url(self.request.user) or resolve_url("home")


@login_required
def home(request):
    """``/``: sends the user to their audience's home page."""
    url = landing_url(request.user)
    if url is None:
        # Signed in but no role yet: nothing to show.
        raise PermissionDenied
    return redirect(url)
