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
import logging
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
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

logger = logging.getLogger(__name__)


class LaunchError(Exception):
    """A launch can't go ahead or Meta refused it. The message is safe to show staff."""


@dataclass
class BuildOptions:
    accounts: list[MetaAdAccount]
    pages: list[PageInfo] = field(default_factory=list)
    forms: list[LeadFormInfo] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


MAX_INTERESTS = 25
# MetaLaunch.budget_usd holds 10 digits with 2 decimals: refuse anything larger
# before Decimal arithmetic can overflow (review I-1).
MAX_BUDGET_USD = Decimal("99999999.99")
# Dates further out than this are typos; Meta can't take them (review I-2).
HORIZON_DAYS = 5 * 365


@dataclass(frozen=True)
class LaunchSettings:
    """How the campaign runs on Meta: the media buyer's values inside the broker's approval.

    Saved on ``MetaLaunch``; the broker's ``Campaign`` and ``Brief`` are never
    rewritten (SRS §2.2, FR-6.1; launch settings spec). Anything beyond these
    limits goes back to the broker through reject → Needs Changes.
    """

    budget_usd: Decimal
    starts_on: datetime.date
    ends_on: datetime.date | None
    interests: tuple[str, ...]
    creative_ids: tuple[int, ...]


def _brokers_interests(campaign):
    return tuple(campaign.audience.interests) if campaign.audience_id else ()


def default_settings(campaign, launch=None) -> LaunchSettings:
    """The launch's saved settings, or the broker's values for a new (or older) launch."""
    if launch is not None and launch.starts_on is not None:
        return LaunchSettings(
            budget_usd=launch.budget_usd, starts_on=launch.starts_on, ends_on=launch.ends_on,
            interests=tuple(launch.interests), creative_ids=tuple(launch.creative_ids),
        )
    return LaunchSettings(
        budget_usd=campaign.budget_usd, starts_on=campaign.starts_on, ends_on=campaign.ends_on,
        interests=_brokers_interests(campaign),
        creative_ids=tuple(campaign.creatives.order_by("pk").values_list("pk", flat=True)),
    )


def _normalized(settings: LaunchSettings) -> LaunchSettings:
    """Budget to the cent; interests stripped, blanks and repeats (any case) dropped."""
    budget = Decimal(settings.budget_usd)
    if not budget.is_finite() or abs(budget) > MAX_BUDGET_USD:
        raise LaunchError(_("Enter the budget in USD, for example 30."))
    seen, interests = set(), []
    for name in settings.interests:
        name = str(name).strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            interests.append(name)
    return LaunchSettings(
        budget_usd=budget.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        starts_on=settings.starts_on, ends_on=settings.ends_on,
        interests=tuple(interests), creative_ids=tuple(int(pk) for pk in settings.creative_ids),
    )


def settings_errors(campaign, settings: LaunchSettings, today) -> list[str]:
    """What breaks the broker's approval: budget, dates, interests, creatives (launch settings spec)."""
    errors = []
    budget = settings.budget_usd
    if not budget.is_finite() or budget < 1:
        errors.append(_("The budget must be at least 1 USD."))
    elif budget > campaign.budget_usd:
        errors.append(_("The budget can't go above the broker's %(amount)s USD.") % {"amount": campaign.budget_usd})
    if settings.starts_on < campaign.starts_on:
        errors.append(_("The start can't be before the broker's start, %(date)s.")
                      % {"date": campaign.starts_on.isoformat()})
    end = settings.ends_on
    if end is None:
        if campaign.ends_on is not None:
            errors.append(_("Keep a last day: the broker's campaign ends on %(date)s.")
                          % {"date": campaign.ends_on.isoformat()})
        elif campaign.budget_kind == "lifetime":
            errors.append(_("A total budget needs a last day."))
    else:
        if campaign.ends_on is not None and end > campaign.ends_on:
            errors.append(_("The last day can't be after the broker's last day, %(date)s.")
                          % {"date": campaign.ends_on.isoformat()})
        if end <= today:
            errors.append(_("The last day has passed. Choose a later one."))
        if settings.starts_on > end:
            errors.append(_("The start must be on or before the last day."))
    horizon = today + datetime.timedelta(days=HORIZON_DAYS)
    if settings.starts_on > horizon or (end is not None and end > horizon):
        errors.append(_("Choose dates within the next 5 years."))
    if len(settings.interests) > MAX_INTERESTS:
        errors.append(_("Use at most %(count)d interests.") % {"count": MAX_INTERESTS})
    if not settings.creative_ids:
        errors.append(_("Choose at least one creative."))
    else:
        on_campaign = set(campaign.creatives.values_list("pk", flat=True))
        if len(set(settings.creative_ids)) != len(settings.creative_ids) or not set(settings.creative_ids) <= on_campaign:
            errors.append(_("Choose only creatives on this campaign, each once."))
    return errors


def _sleep(seconds):
    """Tests replace this so a reel test doesn't wait."""
    time.sleep(seconds)


def _require(actor):
    if not (actor.has_perm(BACK_OFFICE) and actor.has_perm(LAUNCH)):
        raise PermissionDenied


def meta_minor_units(budget_usd: Decimal, fx_rate: Decimal) -> int:
    """A USD budget in the ad account's minor units: cents, or piastres for EGP.

    Broker budgets are USD; an EGP account gets USD × the launch's locked rate
    (launch settings spec). Rounds half up once, on the final amount.
    """
    amount = Decimal(budget_usd) * Decimal(fx_rate) * 100
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _account_rate(currency: str) -> Decimal:
    """How many units of the account's currency one USD buys, or refuse the account.

    EGP uses ``settings.META_EGP_PER_USD``, a stand-in until M6's Finance-kept
    daily rate (ROADMAP Q1–Q4).
    """
    if currency == "USD":
        return Decimal("1")
    if currency == "EGP":
        if not settings.META_EGP_PER_USD:
            raise LaunchError(_("EGP ad accounts need the USD to EGP rate. Set META_EGP_PER_USD first."))
        return Decimal(settings.META_EGP_PER_USD)
    raise LaunchError(
        _("This ad account bills in %(currency)s. Only USD and EGP ad accounts launch.") % {"currency": currency}
    )


def _usd(minor_units, fx_rate) -> Decimal:
    """Meta's minor units back to USD at the launch's rate, to the cent."""
    return (Decimal(minor_units) / 100 / Decimal(fx_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


def _destination_errors(campaign):
    """What the campaign's destination (FR-4.3, FR-4.4) rules out before any Meta call."""
    if campaign.destination == "landing_page":
        return [_("Campaigns for the platform landing page don't launch here yet.")]
    if campaign.destination == "own_account":
        account = campaign.connected_account
        if account is None or account.status != "verified" or not account.external_id:
            return [_("The broker's own page isn't verified yet. Verify it before launching.")]
    return []


def _page_fits(campaign, page):
    """Dark page: a page the platform owns. Own account: exactly the broker's verified page (tenancy)."""
    if campaign.destination == "own_account":
        account = campaign.connected_account
        if account.platform == "instagram":
            return page.instagram_actor_id == account.external_id
        return page.id == account.external_id
    return page.owned


def build_options(*, actor, campaign_id, ad_account_id=None, page_id=""):
    """What the build form offers: accounts, then the account's pages, then the page's forms."""
    _require(actor)
    campaign = _ready_task(campaign_id).campaign
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
    if errors := _destination_errors(campaign):
        options.problems.extend(errors)
        return options
    try:
        options.pages = [p for p in meta.list_pages(account.business_manager.meta_business_id)
                         if _page_fits(campaign, p)]
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


def _claim(task, account, page, form, actor, now, settings, currency, fx_rate):
    """Lock the campaign's launch row and mark it building, or refuse."""
    campaign = task.campaign
    with transaction.atomic():
        launch = MetaLaunch.objects.select_for_update().filter(campaign=campaign).first()
        # Checked again under the lock: another build may have finished while
        # this one read Meta (review finding 1).
        if review_selectors.ready_task_for(campaign.pk) is None:
            raise LaunchError(_("This campaign isn't ready to launch on Meta."))
        if launch is not None:
            if launch.status == MetaLaunch.Status.BUILDING and not launch.is_stale_build(now):
                raise LaunchError(_("This campaign is already being built on Meta. Wait a moment."))
            if launch.status not in (MetaLaunch.Status.BUILDING, MetaLaunch.Status.BUILD_FAILED):
                raise LaunchError(_("This campaign isn't ready to launch on Meta."))
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
        if launch.meta_adset_id:
            # The ad set already carries the saved budget, dates and interests:
            # changing them now would be silently ignored (Review Focus 1).
            if settings != default_settings(campaign, launch):
                raise LaunchError(
                    _("The ad set is already on Meta with the saved settings. Discard the build to change them.")
                )
        else:
            launch.currency, launch.fx_rate = currency, fx_rate
        launch.budget_kind = campaign.budget_kind
        launch.budget_usd = settings.budget_usd
        launch.starts_on, launch.ends_on = settings.starts_on, settings.ends_on
        launch.interests, launch.creative_ids = list(settings.interests), list(settings.creative_ids)
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
    errors = []  # Dates and the creative choice are checked in settings_errors.
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


def _start(launch, now):
    """The later of the launch's first morning (Cairo time) and now."""
    morning = timezone.make_aware(datetime.datetime.combine(launch.starts_on, datetime.time.min))
    return max(morning, now)


def _end(launch):
    """Midnight after the launch's last day, Cairo time, or None to run until stopped."""
    if launch.ends_on is None:
        return None
    return timezone.make_aware(
        datetime.datetime.combine(launch.ends_on + datetime.timedelta(days=1), datetime.time.min)
    )


def _targeting(meta, launch):
    """Egypt, plus the launch's interests Meta knows by exact name. Never age or gender (FR-5.14)."""
    targeting = {"geo_locations": {"countries": [COUNTRY]}}
    names = launch.interests
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
    except _STORAGE_ERRORS:
        raise LaunchError(
            _("The file of %(title)s can't be read. Ask a Content Manager to upload it again.")
            % {"title": creative.title}
        ) from None


def _storage_errors():
    """Missing or unreadable files: disk (OSError), no file (ValueError), S3 (botocore)."""
    errors = [OSError, ValueError]
    try:
        from botocore.exceptions import BotoCoreError, ClientError

        errors += [BotoCoreError, ClientError]
    except ImportError:  # Disk storage only.
        pass
    return tuple(errors)


_STORAGE_ERRORS = _storage_errors()


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
        entry = {"creative_pk": creative.pk, "image_hash": "", "video_id": "", "image_url": "",
                 "creative_id": "", "ad_id": ""}
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
            if not entry.get("image_url"):
                entry["image_url"] = meta.video_thumbnail(entry["video_id"])
                _save(launch, "ads")
    elif not entry["image_hash"]:
        entry["image_hash"] = meta.upload_image(account_id, creative.file.name.rsplit("/", 1)[-1],
                                                _read_file(creative))
        _save(launch, "ads")
    if not entry["creative_id"]:
        entry["creative_id"] = meta.create_ad_creative(
            account_id, name=name, page_id=launch.page_id, instagram_actor_id=launch.instagram_actor_id,
            lead_form_id=launch.lead_form_id, message=creative.offer or creative.title,
            image_hash=entry["image_hash"], video_id=entry["video_id"], image_url=entry.get("image_url", ""),
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


def build(*, actor, campaign_id, ad_account_id, page_id, lead_form_id, settings=None, now=None):
    """Build the ready campaign on Meta, paused, then switch it on; it goes Live (FR-6.10, FR-7.6).

    Resumes a failed or crashed build. Raises ``LaunchError`` with a message
    staff can act on, ``PermissionDenied`` without the capability.
    """
    _require(actor)
    now = now or timezone.now()
    today = timezone.localdate(now)
    task = _ready_task(campaign_id)
    campaign = task.campaign
    if settings is None:
        settings = default_settings(campaign, MetaLaunch.objects.filter(campaign=campaign).first())
    settings = _normalized(settings)
    if errors := settings_errors(campaign, settings, today):
        raise LaunchError(" ".join(str(e) for e in errors))
    by_pk = {c.pk: c for c in campaign.creatives.all()}
    creatives = [by_pk[pk] for pk in settings.creative_ids]
    if errors := _campaign_errors(campaign, creatives, today) or _destination_errors(campaign):
        raise LaunchError(" ".join(str(e) for e in errors))
    account = _available_account(ad_account_id)
    meta = _client(account)
    try:
        currency = meta.ad_account_currency(account.meta_account_id)
        fx_rate = _account_rate(currency)
        page = next((p for p in meta.list_pages(account.business_manager.meta_business_id) if p.id == page_id),
                    None)
        if page is None:
            raise LaunchError(_("This Business Manager can't use that Facebook page. Choose the page again."))
        if not _page_fits(campaign, page):
            if campaign.destination == "own_account":
                raise LaunchError(_("This campaign runs on the broker's own page. Choose that page."))
            raise LaunchError(_("Dark page campaigns run on a page the platform owns. Choose one of those."))
        form = next((f for f in meta.list_lead_forms(page.id) if f.id == lead_form_id), None)
        if form is None:
            raise LaunchError(_("That lead form isn't active on the page. Choose the form again."))
    except MetaError as error:
        _note_token(account, error)
        raise LaunchError(str(error)) from None

    launch = _claim(task, account, page, form, actor, now, settings, currency, fx_rate)
    try:
        _run_build(meta, launch, campaign, creatives, now)
    except Exception as error:
        if isinstance(error, MetaError):
            _note_token(account, error)
        if isinstance(error, (MetaError, LaunchError)):
            message = str(error)
        else:
            # Never leave it "building": say so, and keep the details in the log.
            logger.exception("Meta build of campaign %s failed unexpectedly", campaign.pk)
            message = _("Something went wrong while building. Build again; if it keeps failing, tell a developer.")
        launch.status = MetaLaunch.Status.BUILD_FAILED
        launch.last_error = message
        _save(launch, "status", "last_error")
        raise LaunchError(message) from None

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
            budget_kind=launch.budget_kind, budget_cents=meta_minor_units(launch.budget_usd, launch.fx_rate),
            start=_start(launch, now), end=_end(launch), targeting=_targeting(meta, launch),
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
    if ends_on is not None and launch.starts_on is not None and ends_on < launch.starts_on:
        raise LaunchError(_("The end date can't be before the start, %(date)s.")
                          % {"date": launch.starts_on.isoformat()})
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
                                                    budget_cents=meta_minor_units(budget_usd, launch.fx_rate), end=end))
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
            launch.budget_usd = _usd(state.budget_cents, launch.fx_rate)
        if state.end is not None:
            launch.ends_on = (timezone.localtime(state.end) - datetime.timedelta(microseconds=1)).date()
        launch.meta_status_checked_at = now or timezone.now()
        _save(launch, *fields)
    return launch
