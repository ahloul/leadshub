"""URL configuration for leadshub.

Pages live under a language prefix (/en/, /ar/). Switching language posts to
Django's ``set_language`` view, which keeps the user on the same page.
"""

from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts.views import LoginView, home

from .views import BackOfficeView, DashboardView, DeveloperHomeView

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
]

urlpatterns += i18n_patterns(
    # Each page requires a role capability; see leadshub/views.py.
    path("", home, name="home"),
    path("portal/", DashboardView.as_view(), name="dashboard"),
    path("back-office/", BackOfficeView.as_view(), name="back_office"),
    path("developer/", DeveloperHomeView.as_view(), name="developer_home"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
)
