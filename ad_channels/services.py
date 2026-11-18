"""Launching ready campaigns on Meta and managing them there (US-META-02).

SRS §4.1, §4.2, FR-6.10, FR-7.6, FR-5.14. Every function takes the person
acting and needs ``accounts.launch_meta_campaigns`` (plus the back office).

The build runs while the media buyer waits (the design's approach 2). Meta
objects can't be rolled back with a database transaction, so each Meta ID
is saved in its own short transaction the moment Meta returns it. Pressing
Build again skips every step whose ID is saved; ``discard_build`` deletes
what was made on Meta.
"""

import datetime
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from campaigns import services as campaign_services
from reviews import selectors as review_selectors
from reviews import services as review_services

from .meta_ads import sync
from .meta_ads.client import LeadFormInfo, MetaError, PageInfo
from .models import MetaAdAccount, MetaBusinessManager, MetaLaunch

BACK_OFFICE = "accounts.access_back_office"
LAUNCH = "accounts.launch_meta_campaigns"

# Creative types the first version builds (design §8.5).
_IMAGE = "image"
_REEL = "reel"
BUILDABLE_TYPES = (_IMAGE, _REEL)

# A reel is usable once Meta has processed it: wait at most a minute.
VIDEO_WAIT_SECONDS = 60
VIDEO_POLL_SECONDS = 5

COUNTRY = "EG"


class LaunchError(Exception):
    """A launch can't go ahead or Meta refused it. The message is safe to show staff."""


@dataclass
class BuildOptions:
    accounts: list[MetaAdAccount]
    pages: list[PageInfo] = field(default_factory=list)
    forms: list[LeadFormInfo] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _sleep(seconds):
    """Tests replace this so a reel test doesn't wait."""
    time.sleep(seconds)


def _require(actor):
    if not (actor.has_perm(BACK_OFFICE) and actor.has_perm(LAUNCH)):
        raise PermissionDenied


def budget_cents(amount: Decimal) -> int:
    """USD to Meta's minor units, rounding half up (Review Focus 4)."""
    return int(Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)


def _client(account: MetaAdAccount):
    return sync.client_for(account.business_manager.get_token())


def _ready_task(campaign_id):
    task = review_selectors.ready_task_for(campaign_id)
    if task is None:
        raise LaunchError(_("This campaign isn't ready to launch on Meta."))
    return task


def _available_account(ad_account_id):
    account = (
        MetaAdAccount.objects.available_for_launch()
        .select_related("business_manager")
        .filter(pk=ad_account_id)
        .first()
    )
    if account is None:
        raise LaunchError(_("That ad account can't take a launch right now. Choose another one."))
    return account


# Choosing where to build


def build_options(*, actor, campaign_id, ad_account_id=None, page_id=""):
    """What the build form offers: accounts, then the account's pages, then the page's forms."""
    _require(actor)
    _ready_task(campaign_id)
    options = BuildOptions(
        accounts=list(MetaAdAccount.objects.available_for_launch().select_related("business_manager"))
    )
    if not options.accounts:
        options.problems.append(_("No ad account is ready for launch. A Platform Admin enables them."))
        return options
    account = next((a for a in options.accounts if a.pk == ad_account_id), None)
    if account is None:
        return options
    meta = _client(account)
    try:
        options.pages = meta.list_pages(account.business_manager.meta_business_id)
        if not options.pages:
            options.problems.append(
                _("This Business Manager can't use any Facebook page. Share the page with it in Meta first.")
            )
        elif page_id and any(p.id == page_id for p in options.pages):
            options.forms = meta.list_lead_forms(page_id)
            if not options.forms:
                options.problems.append(
                    _("This page has no active lead form. Create one in Meta, then choose the page again.")
                )
    except MetaError as error:
        _note_token(account, error)
        options.problems.append(str(error))
    return options


# Building


def _claim(task, account, page, form, actor, now):
    """Lock the campaign's launch row and mark it building, or refuse."""
    campaign = task.campaign
    with transaction.atomic():
        launch = MetaLaunch.objects.select_for_update().filter(campaign=campaign).first()
        if launch is not None:
            if launch.status == MetaLaunch.Status.BUILDING and not launch.is_stale_build(now):
                raise LaunchError(_("This campaign is already being built on Meta. Wait a moment."))
            if launch.meta_campaign_id and launch.ad_account_id != account.pk:
                raise LaunchError(
                    _("This build already started on %(account)s. Build again there, or discard it first.")
                    % {"account": launch.ad_account.meta_account_id}
                )
        else:
            launch = MetaLaunch(campaign=campaign, launched_by=actor)
        launch.ad_account = account
        launch.page_id, launch.page_name, launch.instagram_actor_id = page.id, page.name, page.instagram_actor_id
        launch.lead_form_id, launch.lead_form_name = form.id, form.name
        launch.budget_kind = campaign.budget_kind
        launch.budget_usd = campaign.budget_usd
        launch.ends_on = campaign.ends_on
        launch.status = MetaLaunch.Status.BUILDING
        launch.last_error = ""
        try:
            with transaction.atomic():
                launch.save()
        except IntegrityError:
            raise LaunchError(_("This campaign is already being built on Meta. Wait a moment.")) from None
    return launch


def _save(launch, *fields):
    """Keep what Meta returned, in its own commit, before the next call."""
    launch.save(update_fields=[*fields, "updated_at"])


def _campaign_errors(campaign, creatives, today):
    if campaign.channel != "meta":
        return [_("Only Meta campaigns launch here.")]
    errors = []
    if campaign.ends_on is not None and campaign.ends_on <= today:
        errors.append(_("The end date has passed. Ask the broker for a new end date."))
    if campaign.budget_kind == "lifetime" and campaign.ends_on is None:
        errors.append(_("A total budget needs an end date."))
    if not creatives:
        errors.append(_("The campaign has no creatives."))
    usable = set(campaign.creatives.usable(today).values_list("pk", flat=True))
    for creative in creatives:
        if creative.pk not in usable:
            errors.append(_("%(title)s is no longer published or its offer ended.") % {"title": creative.title})
        elif creative.asset_type not in BUILDABLE_TYPES:
            errors.append(
                _("%(title)s is a %(type)s; only images and reels launch on Meta for now.")
                % {"title": creative.title, "type": creative.get_asset_type_display()}
            )
    return errors


def _start(campaign, now):
    """The later of the campaign's first morning (Cairo time) and now."""
    morning = timezone.make_aware(datetime.datetime.combine(campaign.starts_on, datetime.time.min))
    return max(morning, now)


def _end(campaign):
    """Midnight after the last day, Cairo time, or None to run until stopped."""
    if campaign.ends_on is None:
        return None
    return timezone.make_aware(
        datetime.datetime.combine(campaign.ends_on + datetime.timedelta(days=1), datetime.time.min)
    )


def _targeting(meta, campaign, launch):
    """Egypt, plus the preset's interests Meta knows by exact name. Never age or gender (FR-5.14)."""
    targeting = {"geo_locations": {"countries": [COUNTRY]}}
    names = campaign.audience.interests if campaign.audience_id else []
    found, skipped = [], []
    for name in names:
        interest_id = meta.search_interest(name)
        (found if interest_id else skipped).append(interest_id or name)
    if found:
        targeting["flexible_spec"] = [{"interests": [{"id": i} for i in found]}]
    launch.skipped_interests = skipped
    return targeting


def _read_file(creative):
    try:
        with creative.file.open("rb") as handle:
            return handle.read()
    except OSError:
        raise LaunchError(
            _("The file of %(title)s can't be read. Ask a Content Manager to upload it again.")
            % {"title": creative.title}
        ) from None


def _wait_for_video(meta, video_id, title):
    waited = 0
    while not meta.video_ready(video_id):
        if waited >= VIDEO_WAIT_SECONDS:
            raise LaunchError(
                _("Meta is still processing the video of %(title)s. Build again in a minute.") % {"title": title}
            )
        _sleep(VIDEO_POLL_SECONDS)
        waited += VIDEO_POLL_SECONDS


def _build_ad(meta, launch, campaign, creative):
    """Upload, creative, ad: one creative's entry in ``launch.ads``, saved after each step."""
    entry = next((e for e in launch.ads if e["creative_pk"] == creative.pk), None)
    if entry is None:
        entry = {"creative_pk": creative.pk, "image_hash": "", "video_id": "", "creative_id": "", "ad_id": ""}
        launch.ads = [*launch.ads, entry]
    account_id = launch.ad_account.meta_account_id
    name = f"{campaign.code} · {creative.pk}"
    if creative.asset_type == _REEL:
        if not entry["video_id"]:
            entry["video_id"] = meta.upload_video(account_id, creative.file.name.rsplit("/", 1)[-1],
                                                  _read_file(creative))
            _save(launch, "ads")
        if not entry["creative_id"]:
            _wait_for_video(meta, entry["video_id"], creative.title)
    elif not entry["image_hash"]:
        entry["image_hash"] = meta.upload_image(account_id, creative.file.name.rsplit("/", 1)[-1],
                                                _read_file(creative))
        _save(launch, "ads")
    if not entry["creative_id"]:
        entry["creative_id"] = meta.create_ad_creative(
            account_id, name=name, page_id=launch.page_id, instagram_actor_id=launch.instagram_actor_id,
            lead_form_id=launch.lead_form_id, message=creative.offer or creative.title,
            image_hash=entry["image_hash"], video_id=entry["video_id"],
        )
        _save(launch, "ads")
    if not entry["ad_id"]:
        entry["ad_id"] = meta.create_ad(account_id, name=name, adset_id=launch.meta_adset_id,
                                        creative_id=entry["creative_id"])
        _save(launch, "ads")
    return entry


def _note_token(account, error):
    """Meta refused the token itself: stop offering its accounts, as sync does."""
    if error.token_rejected:
        MetaBusinessManager.objects.filter(pk=account.business_manager_id).update(
            token_status=MetaBusinessManager.TokenStatus.INVALID,
            token_error=str(error),
            token_checked_at=timezone.now(),
        )


def build(*, actor, campaign_id, ad_account_id, page_id, lead_form_id, now=None):
    """Build the ready campaign on Meta, paused, then switch it on; it goes Live (FR-6.10, FR-7.6).

    Resumes a failed or crashed build. Raises ``LaunchError`` with a message
    staff can act on, ``PermissionDenied`` without the capability.
    """
    _require(actor)
    now = now or timezone.now()
    today = timezone.localdate(now)
    task = _ready_task(campaign_id)
    campaign = task.campaign
    creatives = list(campaign.creatives.order_by("pk"))
    if errors := _campaign_errors(campaign, creatives, today):
        raise LaunchError(" ".join(str(e) for e in errors))
    account = _available_account(ad_account_id)
    meta = _client(account)
    try:
        currency = meta.ad_account_currency(account.meta_account_id)
        if currency != "USD":
            raise LaunchError(
                _("%(account)s bills in %(currency)s. Campaign budgets are in USD: choose a USD account.")
                % {"account": account.meta_account_id, "currency": currency}
            )
        page = next((p for p in meta.list_pages(account.business_manager.meta_business_id) if p.id == page_id),
                    None)
        if page is None:
            raise LaunchError(_("This Business Manager can't use that Facebook page. Choose the page again."))
        form = next((f for f in meta.list_lead_forms(page.id) if f.id == lead_form_id), None)
        if form is None:
            raise LaunchError(_("That lead form isn't active on the page. Choose the form again."))
    except MetaError as error:
        _note_token(account, error)
        raise LaunchError(str(error)) from None

    launch = _claim(task, account, page, form, actor, now)
    try:
        _run_build(meta, launch, campaign, creatives, now)
    except (MetaError, LaunchError) as error:
        if isinstance(error, MetaError):
            _note_token(account, error)
        launch.status = MetaLaunch.Status.BUILD_FAILED
        launch.last_error = str(error)
        _save(launch, "status", "last_error")
        raise LaunchError(str(error)) from None

    try:
        with transaction.atomic():
            campaign_services.mark_live(campaign.pk, adset_id=launch.meta_adset_id,
                                        ad_account_id=account.meta_account_id)
            review_services.record_deployment(task_id=task.pk, actor=actor, now=now)
            launch.status = MetaLaunch.Status.ACTIVE
            _save(launch, "status")
    except Exception as error:
        # It is switched on at Meta but not Live here: switch it off again so
        # nothing delivers that LeadsHub doesn't know about.
        message = " ".join(getattr(error, "messages", [str(error)]))
        try:
            meta.set_status(launch.meta_campaign_id, "PAUSED")
        except MetaError as meta_error:
            message = f"{message} {meta_error}"
        launch.status = MetaLaunch.Status.BUILD_FAILED
        launch.last_error = _("Built on Meta and paused again, because LeadsHub couldn't record it: %(reason)s") % {
            "reason": message
        }
        _save(launch, "status", "last_error")
        raise LaunchError(launch.last_error) from None
    return launch


def _run_build(meta, launch, campaign, creatives, now):
    account_id = launch.ad_account.meta_account_id
    name = campaign.code
    if not launch.meta_campaign_id:
        launch.meta_campaign_id = (meta.find_by_name(account_id, "campaigns", name)
                                   or meta.create_campaign(account_id, name))
        _save(launch, "meta_campaign_id")
    if not launch.meta_adset_id:
        launch.meta_adset_id = meta.find_by_name(account_id, "adsets", name) or meta.create_adset(
            account_id, name=name, campaign_id=launch.meta_campaign_id, page_id=launch.page_id,
            budget_kind=launch.budget_kind, budget_cents=budget_cents(launch.budget_usd),
            start=_start(campaign, now), end=_end(campaign), targeting=_targeting(meta, campaign, launch),
        )
        _save(launch, "meta_adset_id", "skipped_interests")
    entries = [_build_ad(meta, launch, campaign, creative) for creative in creatives]
    # Children first, so nothing delivers before the whole campaign is ready.
    for entry in entries:
        meta.set_status(entry["ad_id"], "ACTIVE")
    meta.set_status(launch.meta_adset_id, "ACTIVE")
    meta.set_status(launch.meta_campaign_id, "ACTIVE")


def discard_build(*, actor, campaign_id):
    """Delete a failed build from Meta so the campaign is ready again from scratch."""
    _require(actor)
    with transaction.atomic():
        launch = (
            MetaLaunch.objects.select_for_update(of=("self",))
            .select_related("ad_account__business_manager")
            .filter(campaign_id=campaign_id)
            .first()
        )
        if launch is None:
            return
        stuck = launch.status == MetaLaunch.Status.BUILD_FAILED or launch.is_stale_build()
        if not stuck:
            raise LaunchError(_("Only a build that failed can be discarded."))
        # Claim it: a build started meanwhile sees "already being built".
        launch.status = MetaLaunch.Status.BUILDING
        _save(launch, "status")
    if launch.meta_campaign_id:
        try:
            # Deleting the campaign deletes its ad set and ads on Meta.
            _client(launch.ad_account).delete_object(launch.meta_campaign_id)
        except MetaError as error:
            _note_token(launch.ad_account, error)
            launch.status = MetaLaunch.Status.BUILD_FAILED
            launch.last_error = str(error)
            _save(launch, "status", "last_error")
            raise LaunchError(str(error)) from None
    launch.delete()


# Managing a launched campaign (design §5.5)

_LIVE_ON_META = (MetaLaunch.Status.ACTIVE, MetaLaunch.Status.PAUSED)


def _launched(campaign_id, *, allow_stopped=False):
    statuses = (*_LIVE_ON_META, MetaLaunch.Status.ARCHIVED) if allow_stopped else _LIVE_ON_META
    launch = (
        MetaLaunch.objects.select_related("ad_account__business_manager", "campaign")
        .filter(campaign_id=campaign_id, status__in=statuses)
        .first()
    )
    if launch is None:
        raise LaunchError(_("This campaign isn't running on Meta."))
    return launch


def _on_meta(launch, call):
    """Run one Meta call for a launch; keep and show Meta's reason if it fails."""
    try:
        return call(_client(launch.ad_account))
    except MetaError as error:
        _note_token(launch.ad_account, error)
        launch.last_error = str(error)
        _save(launch, "last_error")
        raise LaunchError(str(error)) from None


def _locked_status(launch):
    """The launch's status now, with its row locked for the rest of the transaction."""
    return MetaLaunch.objects.select_for_update().values_list("status", flat=True).get(pk=launch.pk)


def _commit(launch, seen_status, *fields):
    """Save, unless someone changed the launch's status while Meta was answering."""
    with transaction.atomic():
        if _locked_status(launch) != seen_status:
            raise LaunchError(_("Someone changed this campaign meanwhile. Reload the page and try again."))
        _save(launch, *fields)


def _set_status(actor, campaign_id, meta_status, status):
    _require(actor)
    launch = _launched(campaign_id)
    seen = launch.status
    if seen != status:
        _on_meta(launch, lambda meta: meta.set_status(launch.meta_campaign_id, meta_status))
        launch.status = status
        launch.last_error = ""
        _commit(launch, seen, "status", "last_error")
    return launch


def pause(*, actor, campaign_id):
    """Pause delivery on Meta. The broker still sees Live (design §8.4)."""
    return _set_status(actor, campaign_id, "PAUSED", MetaLaunch.Status.PAUSED)


def resume(*, actor, campaign_id):
    return _set_status(actor, campaign_id, "ACTIVE", MetaLaunch.Status.ACTIVE)


def change_budget(*, actor, campaign_id, budget_usd, ends_on, today=None):
    """New budget (never above the broker's) and end date (never after the broker's) on the ad set."""
    _require(actor)
    launch = _launched(campaign_id)
    campaign = launch.campaign
    today = today or timezone.localdate()
    budget_usd = Decimal(budget_usd)
    if budget_usd <= 0:
        raise LaunchError(_("Enter a budget above zero."))
    if budget_usd > campaign.budget_usd:
        raise LaunchError(_("The budget can't go above the broker's %(amount)s USD.")
                          % {"amount": campaign.budget_usd})
    if ends_on is not None and ends_on <= today:
        raise LaunchError(_("Choose an end date after today."))
    if ends_on is not None and campaign.ends_on is not None and ends_on > campaign.ends_on:
        raise LaunchError(_("The end date can't be after the broker's end date, %(date)s.")
                          % {"date": campaign.ends_on.isoformat()})
    if ends_on is None and launch.budget_kind == "lifetime":
        raise LaunchError(_("A total budget needs an end date."))
    if ends_on is None and campaign.ends_on is not None:
        raise LaunchError(_("Keep an end date: the broker's campaign ends on %(date)s.")
                          % {"date": campaign.ends_on.isoformat()})
    end = None
    if ends_on is not None:
        end = timezone.make_aware(datetime.datetime.combine(ends_on + datetime.timedelta(days=1),
                                                            datetime.time.min))
    _on_meta(launch, lambda meta: meta.update_adset(launch.meta_adset_id, budget_kind=launch.budget_kind,
                                                    budget_cents=budget_cents(budget_usd), end=end))
    launch.budget_usd = budget_usd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    launch.ends_on = ends_on
    launch.last_error = ""
    _commit(launch, launch.status, "budget_usd", "ends_on", "last_error")
    return launch


def stop(*, actor, campaign_id):
    """Archive it on Meta and end the campaign. The ad set ID stays for late leads (M7)."""
    _require(actor)
    launch = _launched(campaign_id, allow_stopped=True)
    if launch.status == MetaLaunch.Status.ARCHIVED:
        return launch
    _on_meta(launch, lambda meta: meta.set_status(launch.meta_campaign_id, "ARCHIVED"))
    with transaction.atomic():
        if _locked_status(launch) == MetaLaunch.Status.ARCHIVED:
            launch.refresh_from_db()
            return launch  # Stopped meanwhile by someone else.
        launch.status = MetaLaunch.Status.ARCHIVED
        launch.last_error = ""
        _save(launch, "status", "last_error")
        if launch.campaign.status in ("live", "paused_low_balance"):
            campaign_services.end_campaign(launch.campaign_id)
    return launch


def refresh(*, actor, campaign_id, now=None):
    """Read what Meta says now, including changes made in Ads Manager (edge 18, 19)."""
    _require(actor)
    launch = _launched(campaign_id, allow_stopped=True)
    state = _on_meta(launch, lambda meta: meta.read_launch(launch.meta_campaign_id, launch.meta_adset_id))
    with transaction.atomic():
        # Read the status again under the lock: a stop or pause made while
        # Meta answered wins over what this refresh saw.
        launch.status = _locked_status(launch)
        fields = ["meta_status", "budget_usd", "ends_on", "meta_status_checked_at"]
        launch.meta_status = state.campaign_status
        if launch.status in _LIVE_ON_META and state.campaign_status in ("ACTIVE", "PAUSED"):
            launch.status = MetaLaunch.Status(state.campaign_status.lower())
            fields.append("status")
        if state.budget_cents:
            launch.budget_usd = Decimal(state.budget_cents) / 100
        if state.end is not None:
            launch.ends_on = (timezone.localtime(state.end) - datetime.timedelta(microseconds=1)).date()
        launch.meta_status_checked_at = now or timezone.now()
        _save(launch, *fields)
    return launch
