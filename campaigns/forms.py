"""The builder's step forms and the add-account form (SRS §3.1). Saving goes through ``campaigns.services``.

Each form validates one step; the view hands ``cleaned_data`` to the service,
which checks the same rules again with the person acting (forged requests).
The creative step has no form: brokers pick cards, saved one click at a time.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from catalog import selectors as catalog_selectors

from . import selectors, services
from .models import (
    DESTINATION_CHANNELS,
    BudgetKind,
    Campaign,
    ConnectedAccount,
    Destination,
)

ACCOUNT_PREFIX = "account-"


class TypeForm(forms.ModelForm):
    """Step 1 (FR-4.1): a specific project, or a general campaign across projects."""

    class Meta:
        model = Campaign
        fields = ["target_kind"]
        widgets = {"target_kind": forms.RadioSelect}


class TargetForm(forms.ModelForm):
    """Step 2 (FR-4.1, FR-4.3, FR-4.4): what to advertise, the channel, and whose page.

    ``identity`` folds the destination and the office's account into one
    choice: ``dark_page``, ``landing_page`` or ``account-<id>``.
    """

    identity = forms.ChoiceField(label=_("Where the ads appear"))

    class Meta:
        model = Campaign
        fields = [
            "developer", "project", "area", "unit_type", "channel",
            "accept_area_leads", "accept_developer_leads",
        ]
        widgets = {"channel": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["developer"].queryset = catalog_selectors.developers()
        self.fields["developer"].empty_label = _("Any developer")
        self.fields["area"].queryset = catalog_selectors.areas()
        self.fields["unit_type"].queryset = catalog_selectors.unit_types()
        # The project list follows the chosen developer.
        developer = self.data.get("developer") if self.is_bound else self.instance.developer_id
        developer = str(developer or "")
        self.fields["project"].queryset = catalog_selectors.projects(
            developer_id=int(developer) if developer.isdigit() else None
        )
        self.accounts = list(selectors.office_accounts(self.instance.office_id, usable_only=True))
        self.fields["identity"].choices = [
            (Destination.DARK_PAGE.value, Destination.DARK_PAGE.label),
            (Destination.LANDING_PAGE.value, Destination.LANDING_PAGE.label),
            *[(f"{ACCOUNT_PREFIX}{a.pk}", str(a)) for a in self.accounts],
        ]
        if not self.is_bound:
            self.initial["identity"] = identity_of(self.instance)

    def clean(self):
        cleaned = super().clean()
        identity = cleaned.get("identity", "")
        account = None
        if identity.startswith(ACCOUNT_PREFIX):
            account = next(
                (a for a in self.accounts if f"{ACCOUNT_PREFIX}{a.pk}" == identity), None
            )
            cleaned["destination"] = Destination.OWN_ACCOUNT
        else:
            cleaned["destination"] = identity or Destination.DARK_PAGE
        cleaned["connected_account"] = account
        channel = cleaned.get("channel")
        if channel and channel not in DESTINATION_CHANNELS[Destination(cleaned["destination"])]:
            self.add_error("identity", _("The platform dark page runs on Meta only."))
        if account is not None and channel and account.channel != channel:
            self.add_error("identity", _("This account is for the other channel."))
        cleaned.pop("identity", None)
        return cleaned

    def _post_clean(self):
        # The service checks the target and saves it; validating the half-
        # built campaign here would only repeat its messages.
        pass


def identity_of(campaign):
    if campaign.destination == Destination.OWN_ACCOUNT and campaign.connected_account_id:
        return f"{ACCOUNT_PREFIX}{campaign.connected_account_id}"
    return campaign.destination


class AudienceForm(forms.ModelForm):
    """Step 4 (FR-4.5): a plain-language preset, and what to optimize for. Never age or gender (FR-5.14)."""

    class Meta:
        model = Campaign
        fields = ["audience", "goal"]
        widgets = {"audience": forms.RadioSelect, "goal": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["audience"]
        field.required = True
        field.empty_label = None
        field.queryset = selectors.choosable_audiences(self.instance)


class BudgetForm(forms.ModelForm):
    """Step 5 (FR-4.6): daily or total budget in USD, and the dates."""

    class Meta:
        model = Campaign
        fields = ["budget_kind", "budget_usd", "starts_on", "ends_on"]
        widgets = {
            "budget_kind": forms.RadioSelect,
            "budget_usd": forms.NumberInput(attrs={"min": "1", "step": "1", "dir": "ltr", "inputmode": "decimal"}),
            "starts_on": forms.DateInput(attrs={"type": "date", "dir": "ltr"}, format="%Y-%m-%d"),
            "ends_on": forms.DateInput(attrs={"type": "date", "dir": "ltr"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["budget_usd"].required = True
        self.fields["starts_on"].required = True

    def clean(self):
        cleaned = super().clean()
        errors = services.budget_errors(
            budget_kind=cleaned.get("budget_kind") or BudgetKind.DAILY,
            budget_usd=cleaned.get("budget_usd"),
            starts_on=cleaned.get("starts_on"),
            ends_on=cleaned.get("ends_on"),
        )
        for field, message in errors.items():
            if field not in self.errors:
                self.add_error(field, message)
        return cleaned

    def _post_clean(self):
        # The service saves the campaign and checks the dates again; the
        # model's own validation would only repeat these messages.
        pass


class AccountForm(forms.ModelForm):
    """Add one of the office's own accounts by hand (until the Meta and Google sign-in exists)."""

    class Meta:
        model = ConnectedAccount
        fields = ["platform", "handle"]
        widgets = {
            "handle": forms.TextInput(
                attrs={"dir": "ltr", "autocomplete": "off", "placeholder": "@nour.realty"}
            ),
        }

    def _post_clean(self):
        # services.add_account normalizes the handle, then validates it.
        pass

