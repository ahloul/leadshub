"""URL configuration for leadshub.

Pages live under a language prefix (/en/, /ar/). Switching language posts to
Django's ``set_language`` view, which keeps the user on the same page.
"""

from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts.views import (
    AcceptInvitationView,
    ConfirmEmailView,
    LoginView,
    RegisterView,
    RegistrationStatusView,
    home,
)

from .files import PrivateFileView
from .views import BackOfficeView, DashboardView, DeveloperHomeView, LibraryView, TeamView

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    # Private uploads; the app that owns each file decides who may open it.
    path("files/<path:name>", PrivateFileView.as_view(), name="private_file"),
]

urlpatterns += i18n_patterns(
    # Each page requires a role capability; see leadshub/views.py.
    path("", home, name="home"),
    path("portal/", DashboardView.as_view(), name="dashboard"),
    path("portal/team/", TeamView.as_view(), name="team"),
    path("portal/library/", LibraryView.as_view(), name="library"),
    path("back-office/", BackOfficeView.as_view(), name="back_office"),
    path("developer/", DeveloperHomeView.as_view(), name="developer_home"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Public, protected by the token in the link (accounts.services.send_invitation).
    path("invitation/<uidb64>/<token>/", AcceptInvitationView.as_view(), name="accept_invitation"),
    # A brokerage signs itself up; staff approve it (accounts.services).
    path("register/", RegisterView.as_view(), name="register"),
    path("register/status/", RegistrationStatusView.as_view(), name="registration_status"),
    # Public, protected by the token in the link (accounts.services.send_email_confirmation).
    path("register/confirm/<uidb64>/<token>/", ConfirmEmailView.as_view(), name="confirm_email"),
    path("admin/", admin.site.urls),
)
