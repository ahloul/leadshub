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

from campaigns import views as campaign_views
from reviews import views as review_views

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
    # Campaigns (campaigns/views.py): the list, one campaign, and the builder,
    # one page per step so the step is in the URL (ROADMAP M3).
    path("portal/campaigns/", campaign_views.CampaignListView.as_view(), name="campaigns"),
    path("portal/campaigns/new/", campaign_views.TypeStepView.as_view(), name="campaign_new"),
    path("portal/campaigns/<int:pk>/", campaign_views.CampaignDetailView.as_view(), name="campaign"),
    path("portal/campaigns/<int:pk>/type/", campaign_views.TypeStepView.as_view(), name="campaign_type"),
    path("portal/campaigns/<int:pk>/target/", campaign_views.TargetStepView.as_view(), name="campaign_target"),
    path("portal/campaigns/<int:pk>/creative/", campaign_views.CreativeStepView.as_view(),
         name="campaign_creative"),
    path("portal/campaigns/<int:pk>/audience/", campaign_views.AudienceStepView.as_view(),
         name="campaign_audience"),
    path("portal/campaigns/<int:pk>/budget/", campaign_views.BudgetStepView.as_view(), name="campaign_budget"),
    # The office's own Instagram, Facebook and Google accounts (FR-4.4).
    path("portal/pages/", campaign_views.ConnectedAccountsView.as_view(), name="connected_accounts"),
    path("back-office/", BackOfficeView.as_view(), name="back_office"),
    # The review queue for media buyers and their lead (reviews/views.py, ROADMAP M4).
    path("back-office/queue/", review_views.ReviewQueueView.as_view(), name="review_queue"),
    path("back-office/queue/<int:pk>/", review_views.ReviewTaskView.as_view(), name="review_task"),
    path("back-office/queue/<int:pk>/brief.json", review_views.BriefDownloadView.as_view(),
         name="review_brief"),
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
