import re

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from unfold.widgets import UnfoldAdminEmailInputWidget, UnfoldAdminTextInputWidget

from .models import BrokerOffice, OfficeRegistration, User


class LoginForm(AuthenticationForm):
    """Django's login form with the project's input styling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"


def clean_sign_in_email(email):
    """``email``, normalized, if it can be a new person's sign-in name.

    Invited people sign in with their email address in lower case, so it must
    pass the username rules and must not belong to anyone yet, in any case.
    """
    email = User.objects.normalize_email(email)
    username = email.lower()
    try:
        for validator in User._meta.get_field("username").validators:
            validator(username)
    except ValidationError:
        raise ValidationError(
            _(
                "This address can't be a sign-in name. Use one of up to 150 characters "
                "with only letters, digits and @ . + - _"
            ),
            code="invalid_sign_in_name",
        ) from None
    if User.objects.filter(Q(email__iexact=email) | Q(username__iexact=username)).exists():
        raise ValidationError(
            _("This email address already has a LeadsHub account."), code="taken"
        )
    return email


class InviteMemberForm(forms.ModelForm):
    """Who to invite to the office (SRS §2.1). Their email address becomes their sign-in name."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        # Someone else's details: don't offer the owner's own saved ones.
        for name in self.fields:
            self.fields[name].widget.attrs["autocomplete"] = "off"
        self.fields["email"].widget.attrs["dir"] = "ltr"

    def clean_email(self):
        return clean_sign_in_email(self.cleaned_data["email"])


class NewOfficeForm(forms.ModelForm):
    """A new broker office and the owner to invite, in the admin (ROADMAP M1)."""

    owner_first_name = User._meta.get_field("first_name").formfield(
        label=_("Owner's first name"), required=True, widget=UnfoldAdminTextInputWidget
    )
    owner_last_name = User._meta.get_field("last_name").formfield(
        label=_("Owner's last name"), widget=UnfoldAdminTextInputWidget
    )
    owner_email = User._meta.get_field("email").formfield(
        label=_("Owner's email address"),
        help_text=_(
            "The owner gets an email with a link to set their password. "
            "They sign in with this address."
        ),
        widget=UnfoldAdminEmailInputWidget,
    )

    class Meta:
        model = BrokerOffice
        fields = ["company_name"]

    def clean_owner_email(self):
        return clean_sign_in_email(self.cleaned_data["owner_email"])


class InvitationPasswordForm(SetPasswordForm):
    """Django's set-password form with the project's input styling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"


def clean_phone_number(value):
    """``value`` without spaces, dashes, dots or brackets, if it is a valid number."""
    number = re.sub(r"[\s\-.()]", "", value or "")
    for validator in OfficeRegistration._meta.get_field("phone_number").validators:
        validator(number)
    return number


class RegistrationForm(forms.Form):
    """A brokerage signs itself up (ROADMAP Q6); staff review it before it opens."""

    company_name = BrokerOffice._meta.get_field("company_name").formfield(
        label=_("Company name"), widget=forms.TextInput(attrs={"autocomplete": "organization"})
    )
    first_name = User._meta.get_field("first_name").formfield(
        label=_("Your first name"),
        required=True,
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = User._meta.get_field("last_name").formfield(
        label=_("Your last name"), widget=forms.TextInput(attrs={"autocomplete": "family-name"})
    )
    email = User._meta.get_field("email").formfield(
        label=_("Work email"),
        required=True,
        help_text=_("You sign in with this address. We send a link to confirm it."),
        widget=forms.EmailInput(attrs={"autocomplete": "email", "dir": "ltr"}),
    )
    phone_number = forms.CharField(
        label=_("Mobile number"),
        max_length=32,
        help_text=_("For example 01012345678."),
        widget=forms.TextInput(
            attrs={"type": "tel", "autocomplete": "tel", "inputmode": "tel", "dir": "ltr"}
        ),
    )
    password1 = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label=_("Password again"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"

    def clean_email(self):
        return clean_sign_in_email(self.cleaned_data["email"])

    def clean_phone_number(self):
        return clean_phone_number(self.cleaned_data["phone_number"])

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("password1"), cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error(
                "password2",
                ValidationError(_("The two passwords don't match."), code="password_mismatch"),
            )
        elif password1:
            # Checked against the person's details too (not too similar to them).
            email = cleaned.get("email") or ""
            user = User(
                username=email.lower(),
                email=email,
                first_name=cleaned.get("first_name") or "",
                last_name=cleaned.get("last_name") or "",
            )
            try:
                password_validation.validate_password(password1, user)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned
