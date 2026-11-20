"""Campaign builder rules and the office's own accounts (SRS §3.1, FR-4.1 to FR-4.7; ROADMAP M3).

Every function takes the person acting and checks what they may do, so a
caller can't skip the rules:

- Seeing campaigns takes the client portal; anything that changes one takes
  ``run_campaigns`` (SRS §2.2: owners, and team members the owner allows,
  ROADMAP Q7). Adding the office's pages and ad accounts takes
  ``manage_connected_accounts`` (SRS §2.1: the owner controls them).
- A campaign or account outside the actor's office raises
  ``PermissionDenied`` (SRS §7.2).
- Only Draft and Needs Changes campaigns change (``Campaign.is_editable``).
- Choices are checked again here, not only in the forms: an expired or
  unpublished creative, an audience for another area, or another office's
  account is refused even when the request is forged.

Not here yet, waiting on ROADMAP Q2 (what the daily budget caps): the wallet
coverage check before Run, and the cost-per-lead and lead estimates.
Launching on Meta or Google happens after review (ROADMAP M4), through
``campaigns.integrations``.

Review (``reviews``, ROADMAP M4): Run opens a review task; the queue then
calls ``send_back`` or ``mark_live``, after checking the reviewer itself.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from accounts import selectors as account_selectors
from catalog import selectors as catalog_selectors
from leadshub.emails import send_after_commit

from . import selectors
from .models import (
    AccountStatus,
    Campaign,
    ConnectedAccount,
    Destination,
    Platform,
    Status,
    Step,
    TargetKind,
)

VIEW_CAMPAIGNS = "accounts.access_client_portal"
RUN_CAMPAIGNS = account_selectors.RUN_CAMPAIGNS
MANAGE_ACCOUNTS = "accounts.manage_connected_accounts"


def _office_id(actor, perm):
    office_id = account_selectors.office_id_of(actor)
    if office_id is None or not actor.has_perm(perm):
        raise PermissionDenied
    return office_id


def campaigns_for(actor, **filters):
    """The actor's office's campaigns (team members without ``run_campaigns`` see them too).

    ``filters`` are those of ``selectors.office_campaigns``.
    """
    return selectors.office_campaigns(_office_id(actor, VIEW_CAMPAIGNS), **filters)


def campaign_filter_choices(actor, *, developer_id=None):
    """The developers and projects the office's campaigns name, for the list's filters."""
    office_id = _office_id(actor, VIEW_CAMPAIGNS)
    return (
        selectors.office_campaign_developers(office_id),
        selectors.office_campaign_projects(office_id, developer_id=developer_id),
    )


def campaign_for(actor, campaign_id):
    """One campaign of the actor's office, to look at."""
    campaign = selectors.office_campaign(_office_id(actor, VIEW_CAMPAIGNS), campaign_id)
    if campaign is None:
        raise PermissionDenied
    return campaign


def campaign_to_change(actor, campaign_id, *, lock=False):
    """One campaign of the actor's office that they may change now.

    ``lock`` takes a row lock until the transaction ends, for writes.
    """
    office_id = _office_id(actor, RUN_CAMPAIGNS)
    campaigns = Campaign.objects.filter(office_id=office_id, pk=campaign_id)
    if lock:
        campaigns = campaigns.select_for_update(of=("self",))
    campaign = campaigns.first()
    if campaign is None:
        raise PermissionDenied
    if not campaign.is_editable:
        raise ValidationError(
            _("This campaign can't be changed while it is %(status)s.")
            % {"status": campaign.get_status_display()},
            code="locked",
        )
    return campaign


def _drop_what_no_longer_fits(campaign):
    """After the target changes: creatives and an audience that don't fit any more."""
    fitting = selectors.choosable_creatives(campaign).values("pk")
    campaign.creatives.remove(*campaign.creatives.exclude(pk__in=fitting))
    if campaign.is_pooled and campaign.creatives.count() > 1:
        campaign.creatives.clear()  # the dark page takes one (ROADMAP Q10)
    if campaign.audience_id and not selectors.choosable_audiences(campaign).filter(
        pk=campaign.audience_id
    ).exists():
        campaign.audience = None
        campaign.save(update_fields=["audience", "updated_at"])


# Step 1: type (FR-4.1)


def start_campaign(*, actor, target_kind):
    """A new Draft for the actor's office, from step 1."""
    office_id = _office_id(actor, RUN_CAMPAIGNS)
    return Campaign.objects.create(
        office_id=office_id, created_by=actor, target_kind=TargetKind(target_kind)
    )


@transaction.atomic
def save_type(*, actor, campaign_id, target_kind):
    """Switch between a specific and a general campaign.

    Going general keeps the project's developer and area as two of the
    general campaign's filters; going specific needs a project again.
    """
    campaign = campaign_to_change(actor, campaign_id, lock=True)
    target_kind = TargetKind(target_kind)
    if target_kind != campaign.target_kind:
        campaign.target_kind = target_kind
        campaign.project = None
        campaign.save(update_fields=["target_kind", "project", "updated_at"])
        _drop_what_no_longer_fits(campaign)
    return campaign


# Step 2: target, channel and destination (FR-4.1, FR-4.3, FR-4.4)


def _active(queryset, item, message):
    if item is not None and not queryset.filter(pk=item.pk).exists():
        raise ValidationError(message, code="inactive")
    return item


@transaction.atomic
def save_target(
    *,
    actor,
    campaign_id,
    channel,
    destination,
    project=None,
    developer=None,
    area=None,
    unit_type=None,
    connected_account=None,
    accept_area_leads=False,
    accept_developer_leads=False,
):
    """What to advertise, on which channel, under whose page. Drops choices that no longer fit."""
    campaign = campaign_to_change(actor, campaign_id, lock=True)
    _active(catalog_selectors.projects(), project, _("Choose a project from the list."))
    _active(catalog_selectors.developers(), developer, _("Choose a developer from the list."))
    _active(catalog_selectors.areas(), area, _("Choose an area from the list."))
    _active(catalog_selectors.unit_types(), unit_type, _("Choose a unit type from the list."))
    if campaign.target_kind == TargetKind.PROJECT:
        # A project brings its own developer and area.
        developer = project.developer if project else None
        area = project.area if project else None
        unit_type = None
    else:
        project = None
    if destination != Destination.OWN_ACCOUNT:
        connected_account = None
    elif connected_account is not None and connected_account.office_id != campaign.office_id:
        raise PermissionDenied
    campaign.project, campaign.developer, campaign.area, campaign.unit_type = (
        project, developer, area, unit_type,
    )
    campaign.channel = channel
    campaign.destination = destination
    campaign.connected_account = connected_account
    campaign.accept_area_leads = bool(accept_area_leads)
    campaign.accept_developer_leads = bool(accept_developer_leads)
    errors = campaign.target_errors()
    if errors:
        raise ValidationError(errors, code="target")
    campaign.save(
        update_fields=[
            "project", "developer", "area", "unit_type", "channel", "destination",
            "connected_account", "accept_area_leads", "accept_developer_leads", "updated_at",
        ]
    )
    _drop_what_no_longer_fits(campaign)
    return campaign


# Step 3: creatives (FR-4.2, FR-5.7)


@transaction.atomic
def choose_creative(*, actor, campaign_id, creative_id):
    """Add a creative; on the dark page it replaces the one chosen before."""
    campaign = campaign_to_change(actor, campaign_id, lock=True)
    creative = selectors.choosable_creatives(campaign).filter(pk=creative_id).first()
    if creative is None:
        raise ValidationError(
            _("This creative can't be used for this campaign. Its offer may have ended."),
            code="creative",
        )
    if campaign.is_pooled:
        campaign.creatives.set([creative])
    else:
        campaign.creatives.add(creative)
    campaign.save(update_fields=["updated_at"])
    return campaign


@transaction.atomic
def remove_creative(*, actor, campaign_id, creative_id):
    campaign = campaign_to_change(actor, campaign_id, lock=True)
    campaign.creatives.remove(*campaign.creatives.filter(pk=creative_id))
    campaign.save(update_fields=["updated_at"])
    return campaign


# Step 4: audience and goal (FR-4.5)


@transaction.atomic
def save_audience(*, actor, campaign_id, audience, goal):
    campaign = campaign_to_change(actor, campaign_id, lock=True)
    if audience is None or not selectors.choosable_audiences(campaign).filter(pk=audience.pk).exists():
        raise ValidationError(_("Choose one of the audiences listed."), code="audience")
    campaign.audience = audience
    campaign.goal = goal
    campaign.save(update_fields=["audience", "goal", "updated_at"])
    return campaign


# Step 5: budget and schedule (FR-4.6)


def budget_errors(*, budget_kind, budget_usd, starts_on, ends_on, today=None):
    """What's wrong with a budget and schedule: {field: message}. Shared with the form."""
    today = today or timezone.localdate()
    errors = {}
    if budget_usd is not None and budget_usd < 1:
        errors["budget_usd"] = _("The budget must be at least 1 USD.")
    if starts_on is not None and starts_on < today:
        errors["starts_on"] = _("The start date can't be in the past.")
    if budget_kind == "lifetime" and ends_on is None:
        errors["ends_on"] = _("A total budget needs an end date.")
    elif starts_on and ends_on and ends_on < starts_on:
        errors["ends_on"] = _("The end date must be on or after the start date.")
    return errors


@transaction.atomic
def save_budget(*, actor, campaign_id, budget_kind, budget_usd, starts_on, ends_on=None):
    campaign = campaign_to_change(actor, campaign_id, lock=True)
    errors = budget_errors(
        budget_kind=budget_kind, budget_usd=budget_usd, starts_on=starts_on, ends_on=ends_on
    )
    if errors:
        raise ValidationError(errors)
    campaign.budget_kind = budget_kind
    campaign.budget_usd = budget_usd
    campaign.starts_on = starts_on
    campaign.ends_on = ends_on
    campaign.save(update_fields=["budget_kind", "budget_usd", "starts_on", "ends_on", "updated_at"])
    return campaign


# Run and discard (SRS §3.1)


def run_errors(campaign, *, today=None):
    """Why ``campaign`` can't be sent to review yet (empty list if it can)."""
    errors = [
        _("Complete the %(step)s step.") % {"step": Step(step).label}
        for step in campaign.missing_steps()
    ]
    choosable = selectors.choosable_creatives(campaign, today).values("pk")
    if campaign.creatives.exclude(pk__in=choosable).exists():
        errors.append(
            _("A chosen creative can no longer be used (its offer may have ended). Choose another.")
        )
    if campaign.audience_id and not selectors.choosable_audiences(campaign).filter(
        pk=campaign.audience_id
    ).exists():
        errors.append(_("The chosen audience is no longer offered. Choose another."))
    if campaign.starts_on and campaign.starts_on < (today or timezone.localdate()):
        errors.append(_("The start date has passed. Choose a new one."))
    return errors


@transaction.atomic
def run_campaign(*, actor, campaign_id):
    """Run: send the campaign to the review queue (Draft or Needs Changes → AI Building).

    ROADMAP Q2 and Q9: the wallet coverage check goes here once the business
    says what the daily budget caps.
    """
    from reviews import services as review_services  # reviews builds on campaigns

    campaign = campaign_to_change(actor, campaign_id, lock=True)
    errors = run_errors(campaign)
    if errors:
        raise ValidationError(errors, code="incomplete")
    campaign.submit()
    review_services.open_campaign_task(campaign)
    return campaign


@transaction.atomic
def discard_draft(*, actor, campaign_id):
    """Delete a Draft. A campaign that was ever sent to review is kept."""
    campaign = campaign_to_change(actor, campaign_id, lock=True)
    if campaign.status != Status.DRAFT:
        raise ValidationError(_("Only a draft can be discarded."), code="not_draft")
    code = campaign.code
    campaign.delete()
    return code


# The office's own pages and ad accounts (FR-4.4, ROADMAP Q11)


def accounts_for(actor):
    return selectors.office_accounts(_office_id(actor, MANAGE_ACCOUNTS))


def normalize_handle(platform, handle):
    """``@handle`` for Instagram and Facebook; ``123-456-7890`` for a Google Ads customer ID."""
    handle = "".join(handle.split())
    if Platform(platform) == Platform.GOOGLE:
        digits = "".join(ch for ch in handle if ch.isdigit())
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else handle
    return "@" + handle.lstrip("@") if handle.strip("@") else ""


def add_account(*, actor, platform, handle):
    """Add one of the office's accounts by hand. It waits for a check until staff verify it."""
    office_id = _office_id(actor, MANAGE_ACCOUNTS)
    account = ConnectedAccount(
        office_id=office_id,
        platform=Platform(platform),
        handle=normalize_handle(platform, handle),
        added_by=actor,
    )
    # Uniqueness is checked just below, so a removed account can come back.
    account.full_clean(validate_unique=False, validate_constraints=False)
    if ConnectedAccount.objects.filter(
        office_id=office_id, platform=account.platform, handle__iexact=account.handle
    ).exclude(status=AccountStatus.DISCONNECTED).exists():
        raise ValidationError({"handle": _("This account is already on your list.")})
    existing = ConnectedAccount.objects.filter(
        office_id=office_id, platform=account.platform, handle__iexact=account.handle
    ).first()
    if existing is not None:  # added back after being removed
        existing.status = AccountStatus.UNVERIFIED
        existing.added_by = actor
        existing.save(update_fields=["status", "added_by", "updated_at"])
        return existing
    account.save()
    return account


@transaction.atomic
def remove_account(*, actor, account_id):
    """Take an account off the list. One that campaigns used is kept, marked disconnected."""
    office_id = _office_id(actor, MANAGE_ACCOUNTS)
    account = ConnectedAccount.objects.filter(office_id=office_id, pk=account_id).first()
    if account is None:
        raise PermissionDenied
    in_use = account.campaigns.filter(status__in=[Status.AI_BUILDING, Status.LIVE]).exists()
    if in_use:
        raise ValidationError(
            _("A campaign in review or live uses this account. Wait until it ends."), code="in_use"
        )
    try:
        with transaction.atomic():
            account.delete()
    except ProtectedError:
        account.status = AccountStatus.DISCONNECTED
        account.save(update_fields=["status", "updated_at"])
    return account


# After review (SRS §4.1; ROADMAP M4). ``reviews.services`` checks the
# reviewer before calling these.


@transaction.atomic
def send_back(campaign_id, reasons):
    """Review asked for changes: AI Building → Needs Changes, and the office is told (ROADMAP Q20)."""
    campaign = Campaign.objects.select_for_update().select_related("office").get(pk=campaign_id)
    campaign.request_changes(reasons)
    _tell_office_changes_needed(campaign)
    return campaign


@transaction.atomic
def mark_live(campaign_id, *, adset_id, ad_account_id):
    """The media buyer launched it: record the ad set and ad account, then AI Building → Live.

    ROADMAP Q12 (CONFIRM): a campaign is Live once its ad set ID is recorded.
    Lead routing (M7) matches leads to the campaign by ``external_id``.
    """
    campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
    campaign.external_id = adset_id
    campaign.platform_data = {
        **(campaign.platform_data or {}),
        "adset_id": adset_id,
        "ad_account_id": ad_account_id,
    }
    campaign.save(update_fields=["external_id", "platform_data", "updated_at"])
    campaign.go_live()
    return campaign


@transaction.atomic
def end_campaign(campaign_id):
    """The media buying team stopped it on Meta (US-META-02): Live or Paused → Ended."""
    campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
    campaign.end()
    return campaign


def _tell_office_changes_needed(campaign):
    """Email the office's people who can run campaigns, in the office's language."""
    recipients = list(
        account_selectors.office_people_with(campaign.office_id, RUN_CAMPAIGNS).values_list(
            "email", flat=True
        )
    )
    if not recipients:
        return
    language = account_selectors.office_language(campaign.office_id, settings.LANGUAGE_CODE)
    with translation.override(language):
        link = settings.SITE_BASE_URL + reverse("campaign", args=[campaign.pk])
        target = campaign.target_label
    context = {
        "code": campaign.code,
        "target": target,
        "reasons": campaign.change_reasons,
        "link": link,
        "details": [
            (gettext_lazy("Campaign"), f"{campaign.code} {target}"),
            (gettext_lazy("Office"), campaign.office.company_name),
        ],
    }
    send_after_commit("campaigns/email/changes_needed", context, recipients, language=language)
