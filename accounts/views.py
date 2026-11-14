from django.contrib.auth import login, password_validation
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render, resolve_url
from django.utils.http import urlsafe_base64_decode
from django.views import View
from django.views.generic import FormView, TemplateView

from . import selectors, services
from .forms import InvitationPasswordForm, LoginForm, RegistrationForm
from .models import OfficeStatus, User
from .tokens import email_confirmation_token

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


class AcceptInvitationView(auth_views.PasswordResetConfirmView):
    """The link in an invitation email: the person sets a password and is signed in.

    Django's password-reset confirmation (accounts.services.send_invitation
    makes the token). The link stops working once the password is set or after
    ``PASSWORD_RESET_TIMEOUT``. A deactivated account's link doesn't work.
    """

    template_name = "registration/accept_invitation.html"
    form_class = InvitationPasswordForm
    post_reset_login = True
    # Two backends are configured (axes, then Django's); sign in with Django's.
    post_reset_login_backend = "django.contrib.auth.backends.ModelBackend"

    def get_user(self, uidb64):
        user = super().get_user(uidb64)
        return user if user is not None and user.is_active else None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("validlink"):
            context["sign_in_name"] = self.user.get_username()
            context["password_rules"] = password_validation.password_validators_help_texts()
        return context

    def get_success_url(self):
        return landing_url(self.request.user) or resolve_url("home")


@login_required
def home(request):
    """``/``: sends the user to their audience's home page."""
    url = landing_url(request.user)
    if url is None:
        # A brokerage that signed up itself and isn't approved yet.
        if selectors.registration_of(request.user) is not None:
            return redirect("registration_status")
        # Signed in but no role yet: nothing to show.
        raise PermissionDenied
    return redirect(url)


# Two backends are configured (axes, then Django's); sign in with Django's.
_MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


class RegisterView(FormView):
    """A brokerage signs itself up (ROADMAP Q6). Public.

    The new account is signed in straight away, but has no role: it sees only
    the status page until staff approve the office.
    """

    template_name = "registration/register.html"
    form_class = RegistrationForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["password_rules"] = password_validation.password_validators_help_texts()
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        user = services.register_office(
            company_name=data["company_name"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data["email"],
            phone_number=data["phone_number"],
            password=data["password1"],
            request=self.request,
        )
        login(self.request, user, backend=_MODEL_BACKEND)
        return redirect("registration_status")


class RegistrationStatusView(LoginRequiredMixin, TemplateView):
    """Where a self-signed-up brokerage stands: confirm email, in review, or turned down.

    Only for the applicant. Once the office is approved it sends them to the
    portal. Posting asks for a new confirmation email.
    """

    template_name = "registration/registration_status.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.registration = selectors.registration_of(request.user)
        if self.registration is None or self.registration.office.status == OfficeStatus.ACTIVE:
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self.registration.office.status == OfficeStatus.PENDING:
            try:
                services.send_email_confirmation(request.user, request=request)
            except ValidationError:
                pass  # already confirmed; the page shows that
            else:
                return redirect(f"{request.path}?sent=1")
        return redirect(request.path)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            office_name=self.registration.office.company_name,
            email=self.request.user.email,
            rejected=self.registration.office.status == OfficeStatus.REJECTED,
            email_confirmed=self.registration.email_confirmed_at is not None,
            sent=self.request.GET.get("sent") == "1",
        )
        return context


class ConfirmEmailView(View):
    """The link in the sign-up confirmation email. Public; the token guards it."""

    template_name = "registration/confirm_email.html"

    def get(self, request, uidb64, token):
        try:
            user = User.objects.get(pk=urlsafe_base64_decode(uidb64).decode())
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None
        if (
            user is not None
            and user.is_active
            and selectors.registration_of(user) is not None
            and email_confirmation_token.check_token(user, token)
        ):
            services.confirm_email(user, request=request)
            return render(request, self.template_name, {"confirmed": True})
        return render(request, self.template_name, {"confirmed": False})
