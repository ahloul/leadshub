"""Campaign builder, connected accounts and integration seam tests (SRS §3.1, FR-4.1 to FR-4.7; ROADMAP M3)."""

import datetime
import re
from decimal import Decimal
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone, translation
from djust.testing import LiveViewTestClient

from accounts.roles import Role
from accounts.test_utils import make_office, make_user
from catalog.models import AudiencePreset, UnitType
from creatives.models import Status as CreativeStatus
from creatives.test_utils import TemporaryUploadsMixin, make_creative, make_project

from . import integrations, services
from .integrations.base import AdPlatformClient
from .models import (
    TRANSITIONS,
    AccountStatus,
    BudgetKind,
    Campaign,
    Channel,
    ConnectedAccount,
    Destination,
    Goal,
    InvalidTransition,
    Platform,
    Status,
    Step,
    TargetKind,
)

TODAY = timezone.localdate


def make_campaign(office, *, project=None, **fields):
    """A specific (project) campaign, with the project's developer and area."""
    project = project or make_project()
    fields.setdefault("target_kind", TargetKind.PROJECT)
    return Campaign.objects.create(
        office=office, project=project, developer=project.developer, area=project.area, **fields
    )


def make_area_wide_creative(title, *, area, **fields):
    """A published image creative for a whole area (no project)."""
    from django.core.files.base import ContentFile

    from creatives.demo import solid_png
    from creatives.models import AspectRatio, AssetType, Creative, Language

    creative = Creative(
        title=title, area=area, asset_type=AssetType.IMAGE, language=Language.ENGLISH,
        aspect_ratio=AspectRatio.SQUARE, status=CreativeStatus.PUBLISHED, **fields,
    )
    creative.file.save("area.png", ContentFile(solid_png(8, 8, (16, 185, 129))), save=False)
    creative.save()
    return creative


class CampaignSetup(TemporaryUploadsMixin, TestCase):
    """An office with an owner and a team member, a rival office, and library data."""

    def setUp(self):
        self.enterContext(translation.override("en"))
        self.office = make_office("Nour Realty")
        self.owner = make_user("owner", Role.BROKER_OWNER, office=self.office)
        self.member = make_user("sara", Role.BROKER_TEAM_MEMBER, office=self.office)
        self.rival_office = make_office("Cairo Homes")
        self.rival = make_user("rival", Role.BROKER_OWNER, office=self.rival_office)

        self.project = make_project()  # Palm Hills New Cairo, New Cairo
        self.other_project = make_project("O West", area="6th of October", developer="Orascom")
        self.creative = make_creative("Launch offer A1", project=self.project)
        self.second_creative = make_creative("Launch offer A2", project=self.project)
        self.area_wide = make_area_wide_creative("New Cairo living", area=self.project.area)
        self.elsewhere = make_creative("O West offer", project=self.other_project)
        self.expired = make_creative(
            "Ended offer", project=self.project, offer_expires_on=TODAY() - datetime.timedelta(days=1)
        )
        self.draft_creative = make_creative(
            "Unpublished", project=self.project, status=CreativeStatus.DRAFT
        )
        self.audience = AudiencePreset.objects.create(
            name_en="New Cairo compound buyers", name_ar="مشترو كمبوندات القاهرة الجديدة",
            description_en="People looking at compounds in New Cairo.", area=self.project.area,
        )
        self.any_area_audience = AudiencePreset.objects.create(name_en="Investors")
        self.other_audience = AudiencePreset.objects.create(
            name_en="October families", area=self.other_project.area
        )
        self.instagram = ConnectedAccount.objects.create(
            office=self.office, platform=Platform.INSTAGRAM, handle="@nour.realty"
        )
        self.google_ads = ConnectedAccount.objects.create(
            office=self.office, platform=Platform.GOOGLE, handle="123-456-7890"
        )
        self.rival_instagram = ConnectedAccount.objects.create(
            office=self.rival_office, platform=Platform.INSTAGRAM, handle="@cairo.homes"
        )

    def complete_campaign(self, office=None, **fields):
        """A Draft with every step done."""
        campaign = make_campaign(
            office or self.office,
            project=self.project,
            audience=self.audience,
            budget_kind=BudgetKind.DAILY,
            budget_usd=Decimal("30"),
            starts_on=TODAY(),
            **fields,
        )
        campaign.creatives.add(self.creative)
        return campaign

    def save_target(self, campaign, actor=None, **fields):
        fields.setdefault("channel", Channel.META)
        fields.setdefault("destination", Destination.DARK_PAGE)
        return services.save_target(actor=actor or self.owner, campaign_id=campaign.pk, **fields)


class CampaignModelTests(CampaignSetup):
    def test_code_and_target_label(self):
        campaign = make_campaign(self.office, project=self.project)
        self.assertEqual(campaign.code, f"LH-C-{campaign.pk:04d}")
        self.assertEqual(campaign.target_label, "Palm Hills New Cairo")

        commercial = UnitType.objects.create(name_en="Commercial", name_ar="تجاري")
        general = Campaign.objects.create(
            office=self.office, target_kind=TargetKind.CATEGORY, area=self.project.area,
            unit_type=commercial,
        )
        self.assertEqual(general.target_label, "Commercial · New Cairo")
        general.unit_type, general.area = None, None
        self.assertEqual(general.target_label, "No target yet")

    def test_every_allowed_change_works_and_every_other_is_refused(self):
        """SRS §3.1: the six states change only along TRANSITIONS."""
        for change, (sources, target) in TRANSITIONS.items():
            for start in Status:
                with self.subTest(change=change, start=start):
                    campaign = make_campaign(self.office, project=self.project, status=start)
                    method = getattr(campaign, change)
                    args = ("Offer ended",) if change == "request_changes" else ()
                    if start in sources:
                        method(*args)
                        campaign.refresh_from_db()
                        self.assertEqual(campaign.status, target)
                    else:
                        with self.assertRaises(InvalidTransition):
                            method(*args)
                        campaign.refresh_from_db()
                        self.assertEqual(campaign.status, start)

    def test_a_stale_copy_cannot_move_a_campaign_twice(self):
        campaign = make_campaign(self.office, project=self.project)
        stale = Campaign.objects.get(pk=campaign.pk)
        campaign.submit()
        campaign.go_live()
        with self.assertRaises(InvalidTransition):
            stale.submit()  # still says Draft, but the row is Live
        self.assertEqual(Campaign.objects.get(pk=campaign.pk).status, Status.LIVE)

    def test_submitting_and_rejecting_record_when_and_why(self):
        campaign = make_campaign(self.office, project=self.project)
        campaign.submit()
        self.assertIsNotNone(Campaign.objects.get(pk=campaign.pk).submitted_at)
        campaign.request_changes("The offer has ended.")
        self.assertEqual(Campaign.objects.get(pk=campaign.pk).change_reasons, "The offer has ended.")

    def test_only_draft_and_needs_changes_are_editable(self):
        editable = {s for s in Status if Campaign(status=s).is_editable}
        self.assertEqual(editable, {Status.DRAFT, Status.NEEDS_CHANGES})

    def test_target_rules(self):
        campaign = Campaign(office=self.office, target_kind=TargetKind.PROJECT)
        self.assertEqual(campaign.target_errors(), ["Choose a project."])
        campaign.target_kind = TargetKind.CATEGORY
        campaign.area = self.project.area
        self.assertEqual(
            campaign.target_errors(), ["Choose at least two of area, developer and unit type."]
        )
        campaign.developer = self.project.developer
        self.assertEqual(campaign.target_errors(), [])
        # The dark page is a Meta page.
        campaign.channel = Channel.GOOGLE
        self.assertEqual(campaign.target_errors(), ["The platform dark page runs on Meta only."])
        campaign.destination = Destination.LANDING_PAGE
        self.assertEqual(campaign.target_errors(), [])
        # One's own account must exist, be usable and be for the channel.
        campaign.destination = Destination.OWN_ACCOUNT
        self.assertTrue(campaign.target_errors())
        campaign.connected_account = self.instagram
        self.assertTrue(campaign.target_errors())  # Instagram on Google
        campaign.connected_account = self.google_ads
        self.assertEqual(campaign.target_errors(), [])
        self.google_ads.status = AccountStatus.DISCONNECTED
        self.assertTrue(campaign.target_errors())

    def test_steps(self):
        campaign = make_campaign(self.office, project=self.project)
        self.assertEqual(campaign.missing_steps(), [Step.CREATIVE, Step.AUDIENCE, Step.BUDGET])
        # The dark page takes exactly one creative (ROADMAP Q10).
        campaign.creatives.add(self.creative, self.second_creative)
        self.assertFalse(campaign.step_done(Step.CREATIVE))
        campaign.destination = Destination.LANDING_PAGE
        self.assertTrue(campaign.step_done(Step.CREATIVE))
        # A total budget needs an end date.
        campaign.budget_usd, campaign.starts_on = Decimal("500"), TODAY()
        campaign.budget_kind = BudgetKind.LIFETIME
        self.assertFalse(campaign.step_done(Step.BUDGET))
        campaign.ends_on = TODAY()
        self.assertTrue(campaign.step_done(Step.BUDGET))

    def test_database_keeps_budget_and_dates_sane(self):
        for fields in (
            {"budget_usd": Decimal("0.50")},
            {"starts_on": TODAY(), "ends_on": TODAY() - datetime.timedelta(days=1)},
        ):
            with self.subTest(fields=fields), self.assertRaises(IntegrityError), transaction.atomic():
                make_campaign(self.office, project=self.project, **fields)

    def test_no_age_or_gender_fields(self):
        """FR-5.14: Meta's housing category allows no age or gender limits."""
        for model in (Campaign, AudiencePreset):
            for field in model._meta.get_fields():
                with self.subTest(model=model.__name__, field=field.name):
                    self.assertNotRegex(field.name, r"(^|_)(age|ages|gender|sex)(_|$)")

    def test_account_handles_are_checked_per_platform(self):
        for platform, handle, valid in (
            (Platform.INSTAGRAM, "@nour.realty", True),
            (Platform.FACEBOOK, "@NourRealty", True),
            (Platform.INSTAGRAM, "nour realty", False),
            (Platform.GOOGLE, "123-456-7890", True),
            (Platform.GOOGLE, "@nour.realty", False),
        ):
            with self.subTest(platform=platform, handle=handle):
                account = ConnectedAccount(office=self.office, platform=platform, handle=handle)
                options = {"validate_unique": False, "validate_constraints": False}
                if valid:
                    account.full_clean(**options)
                else:
                    with self.assertRaises(ValidationError):
                        account.full_clean(**options)


class CampaignServiceTests(CampaignSetup):
    def test_owner_starts_a_draft_in_their_office(self):
        campaign = services.start_campaign(actor=self.owner, target_kind=TargetKind.PROJECT)
        self.assertEqual(campaign.office, self.office)
        self.assertEqual(campaign.status, Status.DRAFT)
        self.assertEqual(campaign.created_by, self.owner)
        self.assertEqual(campaign.missing_steps()[0], Step.TARGET)

    def test_a_project_brings_its_developer_and_area(self):
        campaign = services.start_campaign(actor=self.owner, target_kind=TargetKind.PROJECT)
        self.save_target(campaign, project=self.project, area=self.other_project.area,
                         unit_type=UnitType.objects.create(name_en="Villa"))
        campaign.refresh_from_db()
        self.assertEqual(campaign.developer, self.project.developer)
        self.assertEqual(campaign.area, self.project.area)
        self.assertIsNone(campaign.unit_type)

    def test_a_general_campaign_needs_two_of_area_developer_and_unit_type(self):
        campaign = services.start_campaign(actor=self.owner, target_kind=TargetKind.CATEGORY)
        with self.assertRaises(ValidationError):
            self.save_target(campaign, area=self.project.area)
        commercial = UnitType.objects.create(name_en="Commercial")
        self.save_target(campaign, area=self.project.area, unit_type=commercial, project=self.project)
        campaign.refresh_from_db()
        self.assertIsNone(campaign.project)  # a general campaign has none
        self.assertEqual(campaign.target_label, "Commercial · New Cairo")

    def test_switching_type_keeps_what_still_applies(self):
        campaign = self.complete_campaign()
        services.save_type(actor=self.owner, campaign_id=campaign.pk, target_kind=TargetKind.CATEGORY)
        campaign.refresh_from_db()
        self.assertIsNone(campaign.project)
        # Developer and area are two filters: still a complete general target.
        self.assertTrue(campaign.step_done(Step.TARGET))
        self.assertEqual(list(campaign.creatives.all()), [self.creative])
        services.save_type(actor=self.owner, campaign_id=campaign.pk, target_kind=TargetKind.PROJECT)
        campaign.refresh_from_db()
        self.assertFalse(campaign.step_done(Step.TARGET))

    def test_inactive_catalog_entries_are_refused(self):
        self.other_project.is_active = False
        self.other_project.save()
        campaign = services.start_campaign(actor=self.owner, target_kind=TargetKind.PROJECT)
        with self.assertRaises(ValidationError):
            self.save_target(campaign, project=self.other_project)

    def test_channels_and_destinations(self):
        campaign = make_campaign(self.office, project=self.project)
        with self.assertRaisesMessage(ValidationError, "runs on Meta only"):
            self.save_target(campaign, project=self.project, channel=Channel.GOOGLE)
        self.save_target(campaign, project=self.project, channel=Channel.GOOGLE,
                         destination=Destination.OWN_ACCOUNT, connected_account=self.google_ads)
        campaign.refresh_from_db()
        self.assertEqual(campaign.connected_account, self.google_ads)
        with self.assertRaises(ValidationError):  # Instagram can't carry a Google campaign
            self.save_target(campaign, project=self.project, channel=Channel.GOOGLE,
                             destination=Destination.OWN_ACCOUNT, connected_account=self.instagram)
        # Choosing a platform page forgets the account.
        self.save_target(campaign, project=self.project, destination=Destination.LANDING_PAGE,
                         connected_account=self.instagram)
        campaign.refresh_from_db()
        self.assertIsNone(campaign.connected_account)

    def test_another_offices_account_is_refused(self):
        campaign = make_campaign(self.office, project=self.project)
        with self.assertRaises(PermissionDenied):
            self.save_target(campaign, project=self.project, destination=Destination.OWN_ACCOUNT,
                             connected_account=self.rival_instagram)
        self.assertIsNone(Campaign.objects.get(pk=campaign.pk).connected_account)

    def test_lead_options_are_saved(self):
        campaign = make_campaign(self.office, project=self.project)
        self.save_target(campaign, project=self.project, accept_area_leads=True)
        campaign.refresh_from_db()
        self.assertTrue(campaign.accept_area_leads)
        self.assertFalse(campaign.accept_developer_leads)

    def test_who_may_build(self):
        """SRS §2.2: owners; team members only once the owner allows it (ROADMAP Q7)."""
        staff = make_user("platform", Role.PLATFORM_ADMIN)  # has run_campaigns, no office
        for actor in (self.member, staff):
            with self.subTest(actor=actor.username), self.assertRaises(PermissionDenied):
                services.start_campaign(actor=actor, target_kind=TargetKind.PROJECT)
        from accounts import services as account_services

        account_services.set_campaign_access(actor=self.owner, member_id=self.member.pk, allowed=True)
        member = type(self.member).objects.get(pk=self.member.pk)
        campaign = services.start_campaign(actor=member, target_kind=TargetKind.PROJECT)
        self.assertEqual(campaign.office, self.office)

    def test_team_members_see_their_offices_campaigns(self):
        mine = make_campaign(self.office, project=self.project)
        make_campaign(self.rival_office, project=self.project)
        self.assertEqual(list(services.campaigns_for(self.member)), [mine])
        self.assertEqual(services.campaign_for(self.member, mine.pk), mine)

    def test_another_offices_campaign_is_refused_everywhere(self):
        theirs = self.complete_campaign(office=self.rival_office)
        calls = {
            "campaign_for": lambda: services.campaign_for(self.owner, theirs.pk),
            "save_type": lambda: services.save_type(
                actor=self.owner, campaign_id=theirs.pk, target_kind=TargetKind.CATEGORY
            ),
            "save_target": lambda: self.save_target(theirs, project=self.other_project),
            "choose_creative": lambda: services.choose_creative(
                actor=self.owner, campaign_id=theirs.pk, creative_id=self.second_creative.pk
            ),
            "remove_creative": lambda: services.remove_creative(
                actor=self.owner, campaign_id=theirs.pk, creative_id=self.creative.pk
            ),
            "save_audience": lambda: services.save_audience(
                actor=self.owner, campaign_id=theirs.pk, audience=self.any_area_audience,
                goal=Goal.MORE_LEADS,
            ),
            "save_budget": lambda: services.save_budget(
                actor=self.owner, campaign_id=theirs.pk, budget_kind=BudgetKind.DAILY,
                budget_usd=Decimal("99"), starts_on=TODAY(),
            ),
            "run_campaign": lambda: services.run_campaign(actor=self.owner, campaign_id=theirs.pk),
            "discard_draft": lambda: services.discard_draft(actor=self.owner, campaign_id=theirs.pk),
        }
        for name, call in calls.items():
            with self.subTest(call=name), self.assertRaises(PermissionDenied):
                call()
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Status.DRAFT)
        self.assertEqual(theirs.project, self.project)
        self.assertEqual(list(theirs.creatives.all()), [self.creative])
        self.assertEqual(theirs.budget_usd, Decimal("30"))

    def test_a_campaign_in_review_cannot_change(self):
        campaign = self.complete_campaign()
        campaign.submit()
        with self.assertRaisesMessage(ValidationError, "can't be changed while it is AI Building"):
            services.save_audience(
                actor=self.owner, campaign_id=campaign.pk, audience=self.any_area_audience,
                goal=Goal.LOWEST_CPL,
            )
        with self.assertRaises(ValidationError):
            services.run_campaign(actor=self.owner, campaign_id=campaign.pk)

    def test_only_usable_creatives_that_fit_the_target_can_be_chosen(self):
        """FR-5.7: also when the request is forged."""
        campaign = make_campaign(self.office, project=self.project)
        for creative in (self.expired, self.draft_creative, self.elsewhere):
            with self.subTest(creative=creative.title), self.assertRaises(ValidationError):
                services.choose_creative(
                    actor=self.owner, campaign_id=campaign.pk, creative_id=creative.pk
                )
        with self.assertRaises(ValidationError):
            services.choose_creative(actor=self.owner, campaign_id=campaign.pk, creative_id=0)
        self.assertFalse(campaign.creatives.exists())
        # The project's own assets and its area's area-wide ones fit.
        services.choose_creative(
            actor=self.owner, campaign_id=campaign.pk, creative_id=self.area_wide.pk
        )
        self.assertEqual(list(campaign.creatives.all()), [self.area_wide])

    def test_a_general_campaign_uses_every_matching_creative(self):
        campaign = Campaign.objects.create(
            office=self.office, target_kind=TargetKind.CATEGORY, area=self.project.area,
            developer=self.project.developer, destination=Destination.LANDING_PAGE,
        )
        services.choose_creative(actor=self.owner, campaign_id=campaign.pk, creative_id=self.creative.pk)
        with self.assertRaises(ValidationError):  # another area and developer
            services.choose_creative(
                actor=self.owner, campaign_id=campaign.pk, creative_id=self.elsewhere.pk
            )
        self.assertEqual(list(campaign.creatives.all()), [self.creative])

    def test_the_dark_page_takes_one_creative_and_other_destinations_several(self):
        campaign = make_campaign(self.office, project=self.project)
        for creative in (self.creative, self.second_creative):
            services.choose_creative(actor=self.owner, campaign_id=campaign.pk, creative_id=creative.pk)
        self.assertEqual(list(campaign.creatives.all()), [self.second_creative])  # replaced

        self.save_target(campaign, project=self.project, destination=Destination.OWN_ACCOUNT,
                         connected_account=self.instagram)
        services.choose_creative(actor=self.owner, campaign_id=campaign.pk, creative_id=self.creative.pk)
        self.assertEqual(campaign.creatives.count(), 2)

        # Back to the dark page: two creatives can't pool, so they're cleared.
        self.save_target(campaign, project=self.project, destination=Destination.DARK_PAGE)
        self.assertFalse(campaign.creatives.exists())

    def test_changing_the_target_drops_what_no_longer_fits(self):
        campaign = self.complete_campaign(destination=Destination.LANDING_PAGE)
        campaign.creatives.add(self.area_wide)
        self.save_target(campaign, project=self.other_project, destination=Destination.LANDING_PAGE)
        campaign.refresh_from_db()
        self.assertEqual(campaign.area, self.other_project.area)
        self.assertFalse(campaign.creatives.exists())
        self.assertIsNone(campaign.audience)  # New Cairo buyers don't fit October

    def test_audience_must_fit_the_area_and_the_goal_is_saved(self):
        campaign = make_campaign(self.office, project=self.project)
        with self.assertRaises(ValidationError):
            services.save_audience(
                actor=self.owner, campaign_id=campaign.pk, audience=self.other_audience,
                goal=Goal.LOWEST_CPL,
            )
        services.save_audience(
            actor=self.owner, campaign_id=campaign.pk, audience=self.any_area_audience,
            goal=Goal.BETTER_QUALITY,
        )
        campaign.refresh_from_db()
        self.assertEqual(campaign.goal, Goal.BETTER_QUALITY)

    def test_budget_rules(self):
        today = TODAY()
        yesterday = today - datetime.timedelta(days=1)
        self.assertEqual(
            set(services.budget_errors(budget_kind="daily", budget_usd=Decimal("0"),
                                       starts_on=yesterday, ends_on=None)),
            {"budget_usd", "starts_on"},
        )
        self.assertEqual(
            set(services.budget_errors(budget_kind="lifetime", budget_usd=Decimal("500"),
                                       starts_on=today, ends_on=None)),
            {"ends_on"},
        )
        self.assertEqual(
            set(services.budget_errors(budget_kind="daily", budget_usd=Decimal("30"),
                                       starts_on=today, ends_on=yesterday)),
            {"ends_on"},
        )
        self.assertEqual(
            services.budget_errors(budget_kind="daily", budget_usd=Decimal("30"),
                                   starts_on=today, ends_on=None),
            {},
        )

    def test_run_sends_a_complete_campaign_to_review(self):
        campaign = self.complete_campaign()
        services.run_campaign(actor=self.owner, campaign_id=campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.AI_BUILDING)
        self.assertIsNotNone(campaign.submitted_at)

    def test_run_refuses_an_incomplete_campaign(self):
        campaign = make_campaign(self.office, project=self.project)
        with self.assertRaises(ValidationError) as caught:
            services.run_campaign(actor=self.owner, campaign_id=campaign.pk)
        self.assertEqual(len(caught.exception.messages), 3)  # creative, audience, budget
        self.assertEqual(Campaign.objects.get(pk=campaign.pk).status, Status.DRAFT)

    def test_run_checks_choices_again(self):
        """A creative whose offer ended after it was chosen, a start date now past, a removed account."""
        campaign = self.complete_campaign(destination=Destination.OWN_ACCOUNT, connected_account=self.instagram)
        self.creative.offer_expires_on = TODAY() - datetime.timedelta(days=1)
        self.creative.save()
        Campaign.objects.filter(pk=campaign.pk).update(starts_on=TODAY() - datetime.timedelta(days=2))
        ConnectedAccount.objects.filter(pk=self.instagram.pk).update(status=AccountStatus.DISCONNECTED)
        with self.assertRaises(ValidationError) as caught:
            services.run_campaign(actor=self.owner, campaign_id=campaign.pk)
        text = " ".join(caught.exception.messages)
        self.assertIn("can no longer be used", text)
        self.assertIn("The start date has passed", text)
        self.assertIn("Complete the Target step", text)

    def test_a_campaign_sent_back_can_be_corrected_and_run_again(self):
        campaign = self.complete_campaign()
        campaign.submit()
        campaign.request_changes("Choose another creative.")
        services.choose_creative(
            actor=self.owner, campaign_id=campaign.pk, creative_id=self.second_creative.pk
        )
        services.run_campaign(actor=self.owner, campaign_id=campaign.pk)
        self.assertEqual(Campaign.objects.get(pk=campaign.pk).status, Status.AI_BUILDING)

    def test_only_a_draft_can_be_discarded(self):
        draft = make_campaign(self.office, project=self.project)
        services.discard_draft(actor=self.owner, campaign_id=draft.pk)
        self.assertFalse(Campaign.objects.filter(pk=draft.pk).exists())

        sent_back = self.complete_campaign()
        sent_back.submit()
        sent_back.request_changes("Low budget")
        with self.assertRaises(ValidationError):
            services.discard_draft(actor=self.owner, campaign_id=sent_back.pk)
        self.assertTrue(Campaign.objects.filter(pk=sent_back.pk).exists())


class ConnectedAccountServiceTests(CampaignSetup):
    """The office's own pages and ad accounts (FR-4.4, ROADMAP Q11)."""

    def test_owner_adds_accounts_written_any_reasonable_way(self):
        facebook = services.add_account(actor=self.owner, platform=Platform.FACEBOOK, handle=" NourRealty ")
        self.assertEqual(facebook.handle, "@NourRealty")
        self.assertEqual(facebook.status, AccountStatus.UNVERIFIED)
        self.assertEqual(facebook.added_by, self.owner)
        google = services.add_account(actor=self.owner, platform=Platform.GOOGLE, handle="987 654 3210")
        self.assertEqual(google.handle, "987-654-3210")

    def test_bad_or_repeated_handles_are_refused(self):
        for platform, handle in ((Platform.INSTAGRAM, "nour realty!"), (Platform.GOOGLE, "12345"),
                                 (Platform.INSTAGRAM, "@"), (Platform.INSTAGRAM, "@Nour.Realty")):
            with self.subTest(handle=handle), self.assertRaises(ValidationError):
                services.add_account(actor=self.owner, platform=platform, handle=handle)

    def test_only_people_who_manage_accounts(self):
        for actor in (self.member, make_user("platform", Role.PLATFORM_ADMIN)):
            with self.subTest(actor=actor.username), self.assertRaises(PermissionDenied):
                services.add_account(actor=actor, platform=Platform.INSTAGRAM, handle="@x")
        self.assertEqual(list(services.accounts_for(self.owner)), [self.google_ads, self.instagram])

    def test_removing(self):
        unused = services.add_account(actor=self.owner, platform=Platform.FACEBOOK, handle="@spare")
        services.remove_account(actor=self.owner, account_id=unused.pk)
        self.assertFalse(ConnectedAccount.objects.filter(pk=unused.pk).exists())

        # Used by a draft: kept for that campaign's record, marked disconnected.
        make_campaign(self.office, project=self.project, destination=Destination.OWN_ACCOUNT,
                      connected_account=self.instagram)
        services.remove_account(actor=self.owner, account_id=self.instagram.pk)
        self.instagram.refresh_from_db()
        self.assertEqual(self.instagram.status, AccountStatus.DISCONNECTED)
        # Adding it back brings it back, waiting for a check again.
        again = services.add_account(actor=self.owner, platform=Platform.INSTAGRAM, handle="nour.realty")
        self.assertEqual(again.pk, self.instagram.pk)
        self.assertEqual(again.status, AccountStatus.UNVERIFIED)

    def test_an_account_in_use_by_a_running_campaign_stays(self):
        campaign = self.complete_campaign(destination=Destination.OWN_ACCOUNT, connected_account=self.instagram)
        campaign.submit()
        with self.assertRaises(ValidationError):
            services.remove_account(actor=self.owner, account_id=self.instagram.pk)
        self.assertEqual(ConnectedAccount.objects.get(pk=self.instagram.pk).status, AccountStatus.UNVERIFIED)

    def test_another_offices_account_is_refused(self):
        with self.assertRaises(PermissionDenied):
            services.remove_account(actor=self.owner, account_id=self.rival_instagram.pk)
        self.assertTrue(ConnectedAccount.objects.filter(pk=self.rival_instagram.pk).exists())


class FakeMetaClient(AdPlatformClient):
    def connect_url(self, *, office_id, request):
        return f"https://meta.example/oauth?state={office_id}"


class IntegrationSeamTests(TestCase):
    """campaigns.integrations: where the Meta and Google clients plug in (docs/INTEGRATIONS.md)."""

    def test_every_channel_is_manual_until_configured(self):
        from .integrations.manual import ManualClient

        for channel in Channel:
            with self.subTest(channel=channel):
                client = integrations.client_for(channel)
                self.assertIsInstance(client, ManualClient)
                self.assertEqual(client.channel, channel)
                self.assertIsNone(client.connect_url(office_id="x", request=None))
                self.assertIsNone(client.verify_account(None))
                self.assertEqual(client.launch(None), "")
        with self.assertRaises(ValueError):
            integrations.client_for("tiktok")

    @override_settings(AD_PLATFORM_CLIENTS={"meta": "campaigns.tests.FakeMetaClient"})
    def test_a_configured_client_is_used(self):
        self.assertIsInstance(integrations.client_for("meta"), FakeMetaClient)
        self.assertNotIsInstance(integrations.client_for("google"), FakeMetaClient)


class _PageRequests(RequestFactory):
    """Requests for one page URL (the live mount reads its language from it)."""

    def __init__(self, path, **defaults):
        super().__init__(**defaults)
        self.page_path = path

    def get(self, path, *args, **kwargs):
        return super().get(self.page_path, *args, **kwargs)


def live(view_class, user, path, **params):
    """A mounted live page with the office bound, as djust's live mount binds it.

    LiveViewTestClient runs no auth checks and skips the tenant step, so
    refusals here come from the handlers' own guards and the services.
    """

    class WithOffice(view_class):
        def mount(self, request, **kwargs):
            self._ensure_tenant(request)
            return super().mount(request, **kwargs)

    WithOffice.__name__ = view_class.__name__
    client = LiveViewTestClient(WithOffice, user=user, request_factory=_PageRequests(path))
    return client.mount(**params)


def redirect_path(client):
    return [n["path"] for n in client.view_instance._pending_navigation if n["type"] == "live_redirect"]


class CampaignPageTests(CampaignSetup):
    def get(self, user, url, status=200):
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status, url)
        return response.content.decode()

    def test_list_shows_only_the_offices_campaigns(self):
        mine = make_campaign(self.office, project=self.project)
        theirs = make_campaign(self.rival_office, project=self.other_project)
        html = self.get(self.owner, "/en/portal/campaigns/")
        self.assertIn(mine.code, html)
        self.assertNotIn(theirs.code, html)
        self.assertIn('href="/en/portal/campaigns/new/"', html)
        self.assertIn('dj-view="campaigns.views.CampaignListView"', html)

    def test_status_filter_comes_from_the_url(self):
        from .views import CampaignListView

        draft = make_campaign(self.office, project=self.project)
        sent = self.complete_campaign()
        sent.submit()
        html = self.get(self.owner, "/en/portal/campaigns/?status=ai_building")
        self.assertIn(sent.code, html)
        self.assertNotIn(draft.code, html)
        html = self.get(self.owner, "/en/portal/campaigns/?status=nonsense&page=-1")
        self.assertIn(draft.code, html)  # ignored

        client = live(CampaignListView, self.owner, "/en/portal/campaigns/")
        client.send_event("set_filter", field="status", value="draft")
        client.assert_state(status="draft")
        client.assert_patch(params={"status": "draft"})
        client.send_event("set_filter", field="status", value="")
        self.assertEqual(client.view_instance._pending_navigation[-1]["params"], {})

    def test_team_members_look_but_do_not_build(self):
        """ROADMAP Q7: without run_campaigns, the list and a campaign, nothing more."""
        campaign = self.complete_campaign()
        html = self.get(self.member, "/en/portal/campaigns/")
        self.assertIn(campaign.code, html)
        self.assertNotIn("/en/portal/campaigns/new/", html)
        html = self.get(self.member, f"/en/portal/campaigns/{campaign.pk}/")
        self.assertNotIn('dj-click="run_campaign"', html)
        self.assertNotIn('dj-click="discard_draft"', html)
        for step in ("new/", f"{campaign.pk}/type/", f"{campaign.pk}/target/", f"{campaign.pk}/budget/"):
            with self.subTest(step=step):
                self.get(self.member, f"/en/portal/campaigns/{step}", status=403)

    def test_another_offices_campaign_pages_are_refused(self):
        theirs = self.complete_campaign(office=self.rival_office)
        for page in ("", *[f"{step.value}/" for step in Step]):
            with self.subTest(page=page):
                self.get(self.owner, f"/en/portal/campaigns/{theirs.pk}/{page}", status=403)

    def test_live_mount_refuses_another_offices_campaign(self):
        from .views import BudgetStepView, CampaignDetailView, TargetStepView

        theirs = self.complete_campaign(office=self.rival_office)
        for view_class in (CampaignDetailView, TargetStepView, BudgetStepView):
            with self.subTest(view=view_class.__name__), self.assertRaises(PermissionDenied):
                live(view_class, self.owner, f"/en/portal/campaigns/{theirs.pk}/", pk=theirs.pk)

    def test_building_a_campaign_step_by_step(self):
        """SRS §3.1, in the prototype's order: type, target, creative, audience, budget and run."""
        from . import views

        client = live(views.TypeStepView, self.owner, "/en/portal/campaigns/new/")
        client.send_event("submit_form", target_kind="project")
        campaign = Campaign.objects.get(office=self.office)
        path = f"/en/portal/campaigns/{campaign.pk}/"
        self.assertEqual(redirect_path(client), [f"{path}target/"])

        client = live(views.TargetStepView, self.owner, path, pk=campaign.pk)
        client.send_event(
            "submit_form", developer=str(self.project.developer_id), project=str(self.project.pk),
            channel="meta", identity=f"account-{self.instagram.pk}", accept_developer_leads="on",
        )
        self.assertEqual(redirect_path(client), [f"{path}creative/"])
        campaign.refresh_from_db()
        self.assertEqual(campaign.destination, Destination.OWN_ACCOUNT)
        self.assertEqual(campaign.connected_account, self.instagram)
        self.assertTrue(campaign.accept_developer_leads)

        client = live(views.CreativeStepView, self.owner, path, pk=campaign.pk)
        html = client.render()
        self.assertIn("Launch offer A1", html)
        self.assertIn("New Cairo living", html)
        for hidden in ("Ended offer", "Unpublished", "O West offer"):
            self.assertNotIn(hidden, html)
        client.send_event("continue_to_next")
        client.assert_state(notice="Choose at least one creative.")
        client.send_event("choose_creative", creative_id=self.creative.pk)
        client.send_event("choose_creative", creative_id=self.second_creative.pk)
        self.assertEqual(campaign.creatives.count(), 2)  # own page: several
        client.send_event("continue_to_next")
        self.assertEqual(redirect_path(client), [f"{path}audience/"])

        client = live(views.AudienceStepView, self.owner, path, pk=campaign.pk)
        self.assertIn("People looking at compounds in New Cairo.", client.render())
        client.send_event("submit_form", audience=str(self.audience.pk), goal="more_leads")
        self.assertEqual(redirect_path(client), [f"{path}budget/"])

        client = live(views.BudgetStepView, self.owner, path, pk=campaign.pk)
        client.send_event(
            "submit_form", budget_kind="daily", budget_usd="40",
            starts_on=TODAY().isoformat(), ends_on="", _target="run",
        )
        self.assertEqual(redirect_path(client), [path])
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.AI_BUILDING)
        self.assertEqual(campaign.goal, Goal.MORE_LEADS)

    def test_save_draft_keeps_it_a_draft(self):
        from . import views

        campaign = self.complete_campaign()
        path = f"/en/portal/campaigns/{campaign.pk}/"
        client = live(views.BudgetStepView, self.owner, path, pk=campaign.pk)
        client.send_event("submit_form", budget_kind="daily", budget_usd="55",
                          starts_on=TODAY().isoformat(), _target="save_draft")
        self.assertEqual(redirect_path(client), [path])
        campaign.refresh_from_db()
        self.assertEqual((campaign.status, campaign.budget_usd), (Status.DRAFT, Decimal("55")))

    def test_run_from_the_budget_step_reports_what_is_missing(self):
        from . import views

        campaign = make_campaign(self.office, project=self.project)
        client = live(views.BudgetStepView, self.owner, f"/en/portal/campaigns/{campaign.pk}/",
                      pk=campaign.pk)
        client.send_event("submit_form", budget_kind="daily", budget_usd="30",
                          starts_on=TODAY().isoformat(), _target="run")
        self.assertEqual(redirect_path(client), [])
        self.assertIn("Complete the Creative step.", client.view_instance.error_message)
        self.assertEqual(Campaign.objects.get(pk=campaign.pk).budget_usd, Decimal("30"))  # saved

    def test_target_choices_follow_the_channel(self):
        from . import views

        campaign = make_campaign(self.office, project=self.project)
        client = live(views.TargetStepView, self.owner, f"/en/portal/campaigns/{campaign.pk}/",
                      pk=campaign.pk)
        html = client.render()
        self.assertIn('value="dark_page"', html)
        self.assertIn(f'value="account-{self.instagram.pk}"', html)
        self.assertNotIn(f'value="account-{self.google_ads.pk}"', html)
        self.assertNotIn(f'value="account-{self.rival_instagram.pk}"', html)
        client.send_event("validate_field", field="channel", value="google")
        html = client.render()
        self.assertNotIn('value="dark_page"', html)
        self.assertIn(f'value="account-{self.google_ads.pk}"', html)
        self.assertNotIn(f'value="account-{self.instagram.pk}"', html)

    def test_project_list_follows_the_developer(self):
        """The project dropdown lists the chosen developer's projects (checked in a browser too)."""
        from .forms import TargetForm

        campaign = services.start_campaign(actor=self.owner, target_kind=TargetKind.PROJECT)
        form = TargetForm({"developer": str(self.other_project.developer_id)}, instance=campaign)
        self.assertEqual(list(form.fields["project"].queryset), [self.other_project])
        self.assertEqual(
            set(TargetForm(instance=campaign).fields["project"].queryset),
            {self.project, self.other_project},
        )

    def test_general_campaign_target(self):
        from . import views

        commercial = UnitType.objects.create(name_en="Commercial")
        campaign = services.start_campaign(actor=self.owner, target_kind=TargetKind.CATEGORY)
        path = f"/en/portal/campaigns/{campaign.pk}/"
        client = live(views.TargetStepView, self.owner, path, pk=campaign.pk)
        html = client.render()
        self.assertIn("Any area", html)
        self.assertIn('name="unit_type"', html)
        client.send_event("submit_form", area=str(self.project.area_id), unit_type=str(commercial.pk),
                          developer="", channel="google", identity="landing_page")
        self.assertEqual(redirect_path(client), [f"{path}creative/"])
        campaign.refresh_from_db()
        self.assertEqual(campaign.target_label, "Commercial · New Cairo")
        self.assertEqual((campaign.channel, campaign.destination), (Channel.GOOGLE, Destination.LANDING_PAGE))

    def test_form_errors_stay_on_the_step(self):
        from . import views

        campaign = make_campaign(self.office, project=self.project)
        path = f"/en/portal/campaigns/{campaign.pk}/"
        client = live(views.BudgetStepView, self.owner, path, pk=campaign.pk)
        client.send_event(
            "submit_form", budget_kind="lifetime", budget_usd="0",
            starts_on=(TODAY() - datetime.timedelta(days=1)).isoformat(), ends_on="",
        )
        self.assertEqual(redirect_path(client), [])
        self.assertEqual(set(client.view_instance.field_errors), {"budget_usd", "starts_on", "ends_on"})

        client = live(views.AudienceStepView, self.owner, path, pk=campaign.pk)
        client.send_event("submit_form", audience=str(self.other_audience.pk), goal="lowest_cpl")  # forged
        self.assertEqual(redirect_path(client), [])
        self.assertIsNone(Campaign.objects.get(pk=campaign.pk).audience)

        client = live(views.TargetStepView, self.owner, path, pk=campaign.pk)
        client.send_event("submit_form", project=str(self.project.pk), channel="google", identity="dark_page")
        self.assertEqual(redirect_path(client), [])
        self.assertIn("identity", client.view_instance.field_errors)
        client.send_event("submit_form", project=str(self.project.pk), channel="meta",
                          identity=f"account-{self.rival_instagram.pk}")  # forged
        self.assertEqual(redirect_path(client), [])
        self.assertEqual(Campaign.objects.get(pk=campaign.pk).destination, Destination.DARK_PAGE)

    def test_a_forged_creative_is_refused_on_the_page(self):
        from . import views

        campaign = make_campaign(self.office, project=self.project)
        client = live(views.CreativeStepView, self.owner, f"/en/portal/campaigns/{campaign.pk}/",
                      pk=campaign.pk)
        client.send_event("choose_creative", creative_id=self.expired.pk)
        self.assertIn("can't be used for this campaign", client.view_instance.notice)
        self.assertFalse(campaign.creatives.exists())

    def test_handlers_need_run_campaigns(self):
        """AGENTS.md "Permission setup" step 8: a person without the capability is refused.

        A team member can't even open the builder (its mount refuses them), so
        each page is opened by the owner and then acted on by the member, as
        when the owner's access is taken away while the page is open (§2.3).
        """
        from . import views

        campaign = self.complete_campaign()
        path = f"/en/portal/campaigns/{campaign.pk}/"
        cases = [
            (views.CampaignDetailView, "run_campaign", {}),
            (views.CampaignDetailView, "discard_draft", {}),
            (views.TypeStepView, "submit_form", {"target_kind": "category"}),
            (views.TargetStepView, "submit_form", {"project": str(self.other_project.pk),
                                                   "channel": "meta", "identity": "landing_page"}),
            (views.CreativeStepView, "choose_creative", {"creative_id": self.second_creative.pk}),
            (views.CreativeStepView, "remove_creative", {"creative_id": self.creative.pk}),
            (views.CreativeStepView, "continue_to_next", {}),
            (views.AudienceStepView, "submit_form", {"audience": str(self.any_area_audience.pk),
                                                     "goal": "more_leads"}),
            (views.BudgetStepView, "submit_form", {"budget_kind": "daily", "budget_usd": "99",
                                                   "starts_on": TODAY().isoformat(), "_target": "run"}),
        ]
        for view_class, event, params in cases:
            with self.subTest(view=view_class.__name__, event=event):
                client = live(view_class, self.owner, path, pk=campaign.pk)
                client.view_instance.request.user = self.member
                result = client.send_event(event, **params)
                self.assertEqual(result["error"], "Permission denied")
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Status.DRAFT)
        self.assertEqual(campaign.target_kind, TargetKind.PROJECT)
        self.assertEqual(campaign.project, self.project)
        self.assertEqual(list(campaign.creatives.all()), [self.creative])
        self.assertEqual(campaign.audience, self.audience)
        self.assertEqual(campaign.budget_usd, Decimal("30"))

    def test_builder_mount_refuses_a_team_member(self):
        from . import views

        campaign = self.complete_campaign()
        with self.assertRaises(PermissionDenied):
            live(views.BudgetStepView, self.member, f"/en/portal/campaigns/{campaign.pk}/",
                 pk=campaign.pk)

    def test_a_campaign_in_review_shows_as_locked(self):
        campaign = self.complete_campaign()
        campaign.submit()
        html = self.get(self.owner, f"/en/portal/campaigns/{campaign.pk}/budget/")
        self.assertIn("be changed while it is AI Building", html)
        self.assertNotIn('dj-submit="submit_form"', html)
        html = self.get(self.owner, f"/en/portal/campaigns/{campaign.pk}/")
        self.assertNotIn('dj-click="run_campaign"', html)
        self.assertIn("In the review queue", html)

    def test_discarding_a_draft_returns_to_the_list(self):
        from . import views

        campaign = make_campaign(self.office, project=self.project)
        client = live(views.CampaignDetailView, self.owner, f"/en/portal/campaigns/{campaign.pk}/",
                      pk=campaign.pk)
        client.send_event("discard_draft")
        self.assertFalse(Campaign.objects.filter(pk=campaign.pk).exists())
        self.assertEqual(redirect_path(client), ["/en/portal/campaigns/"])

    def test_campaign_page_summary(self):
        campaign = self.complete_campaign(destination=Destination.OWN_ACCOUNT, connected_account=self.instagram,
                                          goal=Goal.BETTER_QUALITY)
        html = self.get(self.owner, f"/en/portal/campaigns/{campaign.pk}/").replace("&#39;", "&#x27;")
        for text in ("Specific campaign", "Meta: Facebook and Instagram", "Instagram account \u2066@nour.realty\u2069",
                     "Better quality", "Daily budget: 30 USD", "verified yet. Our team checks it"):
            with self.subTest(text=text):
                self.assertIn(text, html)

    def test_change_reasons_show_while_changes_are_needed(self):
        campaign = self.complete_campaign()
        campaign.submit()
        campaign.request_changes("The offer has ended. Choose another creative.")
        html = self.get(self.owner, f"/en/portal/campaigns/{campaign.pk}/")
        self.assertIn("The offer has ended. Choose another creative.", html)
        self.assertIn('dj-click="run_campaign"', html)

    def test_no_page_mentions_age_or_gender(self):
        """FR-5.14, ROADMAP M3 "Done when": only the line saying audiences never limit them."""
        campaign = self.complete_campaign()
        pages = ["", "new/", f"{campaign.pk}/"] + [f"{campaign.pk}/{step.value}/" for step in Step]
        for language, words in (
            ("en", r"\b(age|ages|gender|male|female)\b"),
            ("ar", r"العمر|الأعمار|الجنس|ذكور|إناث"),
        ):
            for page in pages:
                with self.subTest(language=language, page=page):
                    html = self.get(self.owner, f"/{language}/portal/campaigns/{page}")
                    main = html[html.find("dj-root"):]
                    main = re.sub(r'<p class="policy-note[^"]*">.*?</p>', "", main, flags=re.S)
                    self.assertIsNone(re.search(words, main, re.IGNORECASE))

    def test_arabic_builder(self):
        campaign = self.complete_campaign()
        html = self.get(self.owner, f"/ar/portal/campaigns/{campaign.pk}/audience/")
        self.assertIn('dir="rtl"', html)
        self.assertIn("الخطوة 4 من 5", html)
        self.assertIn("مشترو كمبوندات القاهرة الجديدة", html)
        html = self.get(self.owner, "/ar/portal/campaigns/")
        self.assertIn("مسودة", html)  # Draft

    def test_selects_have_one_blank_choice(self):
        """leadshub.live.LiveFormAdapter: djust's blank and the model field's aren't both shown."""
        from leadshub.live import LiveFormAdapter

        from .forms import TargetForm

        campaign = make_campaign(self.office, project=self.project)
        html = self.get(self.owner, f"/en/portal/campaigns/{campaign.pk}/target/")
        project_select = html[html.find('name="project"'):]
        project_select = project_select[:project_select.find("</select>")]
        self.assertEqual(project_select.count('<option value=""'), 1)

        select = LiveFormAdapter()._render_select(TargetForm().fields["developer"], "developer", "", False, {})
        self.assertEqual(select.count('<option value=""'), 1)
        self.assertIn(">Any developer</option>", select)

    def test_navigation_links(self):
        html = self.get(self.member, "/en/portal/")
        self.assertIn('href="/en/portal/campaigns/" dj-navigate="/en/portal/campaigns/"', html)
        self.assertNotIn("/en/portal/pages/", html)
        html = self.get(self.owner, "/en/portal/")
        self.assertIn('href="/en/portal/pages/" dj-navigate="/en/portal/pages/"', html)


class ConnectedAccountsPageTests(CampaignSetup):
    """/portal/pages/: the office's own Instagram, Facebook and Google accounts."""

    def page(self, user=None):
        from .views import ConnectedAccountsView

        return live(ConnectedAccountsView, user or self.owner, "/en/portal/pages/")

    def test_owner_sees_only_their_accounts(self):
        self.client.force_login(self.owner)
        html = self.client.get("/en/portal/pages/").content.decode()
        self.assertIn("@nour.realty", html)
        self.assertIn("123-456-7890", html)
        self.assertNotIn("@cairo.homes", html)
        self.assertIn("Connect with Meta · soon", html)  # no real client yet

    def test_team_members_are_refused(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get("/en/portal/pages/").status_code, 403)

    def test_adding_and_removing(self):
        client = self.page()
        client.send_event("validate_field", field="platform", value="facebook")
        client.send_event("submit_form", platform="facebook", handle="NourRealty")
        account = ConnectedAccount.objects.get(office=self.office, platform=Platform.FACEBOOK)
        self.assertEqual(account.handle, "@NourRealty")
        self.assertIn("was added", client.view_instance.success_message)

        client.send_event("submit_form", platform="google", handle="123")
        self.assertIn("handle", client.view_instance.field_errors)

        client.send_event("remove_account", account_id=account.pk)
        self.assertFalse(ConnectedAccount.objects.filter(pk=account.pk).exists())
        self.assertFalse(client.send_event("remove_account", account_id=self.rival_instagram.pk)["success"])
        self.assertTrue(ConnectedAccount.objects.filter(pk=self.rival_instagram.pk).exists())

    def test_handlers_need_the_capability(self):
        client = self.page()
        client.view_instance.request.user = self.member
        for event, params in (("submit_form", {"platform": "instagram", "handle": "@x"}),
                              ("remove_account", {"account_id": self.instagram.pk})):
            with self.subTest(event=event):
                self.assertEqual(client.send_event(event, **params)["error"], "Permission denied")
        self.assertTrue(ConnectedAccount.objects.filter(pk=self.instagram.pk).exists())

    @override_settings(AD_PLATFORM_CLIENTS={"meta": "campaigns.tests.FakeMetaClient"})
    def test_connect_button_appears_with_a_real_client(self):
        self.client.force_login(self.owner)
        html = self.client.get("/en/portal/pages/").content.decode()
        self.assertIn(f'href="https://meta.example/oauth?state={self.office.pk}"', html)
        self.assertIn("Connect with Google · soon", html)


class ConnectedAccountAdminTests(CampaignSetup):
    def test_staff_who_manage_ad_accounts_verify_them(self):
        lead = make_user("lead", Role.MEDIA_BUYER, Role.MEDIA_BUYING_LEAD)
        self.client.force_login(lead)
        response = self.client.get("/en/admin/campaigns/connectedaccount/")
        self.assertContains(response, "@nour.realty")
        response = self.client.post(
            "/en/admin/campaigns/connectedaccount/",
            {"action": "mark_verified", "_selected_action": [self.instagram.pk]},
        )
        self.assertEqual(response.status_code, 302)
        self.instagram.refresh_from_db()
        self.assertEqual(self.instagram.status, AccountStatus.VERIFIED)
        self.assertIsNotNone(self.instagram.verified_at)

    def test_other_staff_and_brokers_cannot(self):
        for user in (make_user("content", Role.CONTENT_MANAGER), self.owner):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertIn(
                    self.client.get("/en/admin/campaigns/connectedaccount/").status_code, (302, 403)
                )


class CampaignPagesOverTheSocketTests(TemporaryUploadsMixin, TransactionTestCase):
    """The builder through djust's real WebSocket consumer.

    LiveViewTestClient skips the consumer, which checks the page's permissions
    at mount, binds the office, passes the URL's campaign id to mount() and
    re-checks the person on every event. A TransactionTestCase, because the
    consumer reads the database from other threads.
    """

    def setUp(self):
        self.office = make_office("Nour Realty")
        self.owner = make_user("owner", Role.BROKER_OWNER, office=self.office)
        self.member = make_user("sara", Role.BROKER_TEAM_MEMBER, office=self.office)
        self.rival = make_user("rival", Role.BROKER_OWNER, office=make_office("Cairo Homes"))
        self.project = make_project()
        self.campaign = make_campaign(self.office, project=self.project)

    def socket_for(self, user):
        from channels.testing import WebsocketCommunicator
        from django.test import Client

        from leadshub.asgi import application

        web = Client()
        web.force_login(user)
        cookie = f"sessionid={web.cookies['sessionid'].value}".encode()
        headers = [(b"cookie", cookie), (b"origin", b"http://testserver"), (b"host", b"testserver")]
        return WebsocketCommunicator(application, "/ws/live/", headers=headers)

    async def open(self, user, view, url):
        from asgiref.sync import sync_to_async

        socket = await sync_to_async(self.socket_for)(user)
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        self.assertEqual((await socket.receive_json_from())["type"], "connect")
        await socket.send_json_to({"type": "mount", "view": view, "url": url})
        return socket, await socket.receive_json_from(timeout=5)

    async def test_owner_saves_a_step_over_the_socket(self):
        url = f"/en/portal/campaigns/{self.campaign.pk}/target/"
        socket, reply = await self.open(self.owner, "campaigns.views.TargetStepView", url)
        self.assertEqual(reply["type"], "mount")
        self.assertIn('name="identity"', reply["html"])
        self.assertIn(self.campaign.code, reply["html"])
        await socket.send_json_to({
            "type": "event", "event": "submit_form",
            "params": {"developer": str(self.project.developer_id), "project": str(self.project.pk),
                       "channel": "meta", "identity": "landing_page"},
        })
        # A patch for the form, then the redirect to the next step.
        next_step = f"/en/portal/campaigns/{self.campaign.pk}/creative/"
        frames = []
        for _ in range(5):
            frames.append(await socket.receive_json_from(timeout=5))
            if next_step in str(frames[-1]):
                break
        self.assertIn(next_step, str(frames[-1]))
        campaign = await Campaign.objects.aget(pk=self.campaign.pk)
        self.assertEqual(campaign.destination, Destination.LANDING_PAGE)
        await socket.disconnect()

    async def test_an_arabic_page_mounts_with_its_campaign(self):
        """leadshub.live.PageLanguageMixin._url_kwargs: djust 1.2.8 lost the URL's pk on /ar/ pages."""
        url = f"/ar/portal/campaigns/{self.campaign.pk}/target/"
        socket, reply = await self.open(self.owner, "campaigns.views.TargetStepView", url)
        self.assertEqual(reply["type"], "mount")
        self.assertIn(self.campaign.code, reply["html"])
        self.assertIn('name="channel"', reply["html"])
        self.assertIn("الخطوة 2 من 5", reply["html"])
        await socket.disconnect()

    async def test_team_member_is_refused_at_mount(self):
        url = f"/en/portal/campaigns/{self.campaign.pk}/budget/"
        socket, reply = await self.open(self.member, "campaigns.views.BudgetStepView", url)
        self.assertEqual(reply, {"type": "error", "error": "Permission denied"})
        await socket.disconnect()

    async def test_another_offices_campaign_is_not_mounted(self):
        url = f"/en/portal/campaigns/{self.campaign.pk}/"
        socket, reply = await self.open(self.rival, "campaigns.views.CampaignDetailView", url)
        self.assertNotEqual(reply["type"], "mount")
        self.assertNotIn(self.campaign.code, str(reply))
        await socket.disconnect()


class CampaignListTests(CampaignSetup):
    """The campaign list laid out like the product prototype: search, filters, table and cards."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        global CampaignListView
        from .views import CampaignListView

    def get(self, user, url):
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def setUp(self):
        super().setUp()
        self.palm = make_campaign(self.office, project=self.project, goal=Goal.MORE_LEADS)
        self.owest = make_campaign(
            self.office, project=self.other_project, channel=Channel.GOOGLE,
            destination=Destination.LANDING_PAGE, budget_usd=Decimal("1250"),
            budget_kind=BudgetKind.LIFETIME,
        )
        self.category = Campaign.objects.create(
            office=self.office, target_kind=TargetKind.CATEGORY, area=self.project.area,
            unit_type=UnitType.objects.create(name_en="Villas", name_ar="فيلات"),
        )
        self.theirs = make_campaign(self.rival_office, project=self.project)

    def codes(self, html):
        """The codes in the list itself (the search box may echo one)."""
        listed = html.split('aria-labelledby="campaigns-heading"', 1)[1]
        return set(re.findall(r"LH-C-\d{4}", listed))

    def test_search_matches_names_in_both_languages_and_the_code(self):
        self.project.name_ar = "بالم هيلز القاهرة الجديدة"
        self.project.save(update_fields=["name_ar"])
        cases = {
            "o west": {self.owest},
            "ORASCOM": {self.owest},  # developer
            "بالم": {self.palm},  # Arabic project name
            "villas": {self.category},  # unit type
            "new cairo": {self.palm, self.category},  # area
            self.owest.code: {self.owest},
            str(self.palm.pk): {self.palm},
            "nothing like it": set(),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                html = self.get(self.owner, "/en/portal/campaigns/?" + urlencode({"q": text}))
                self.assertEqual(self.codes(html), {c.code for c in expected})

    def test_another_offices_campaign_never_matches(self):
        html = self.get(self.owner, f"/en/portal/campaigns/?q={self.theirs.code}")
        self.assertEqual(self.codes(html), set())
        html = self.get(self.owner, f"/en/portal/campaigns/?developer={self.project.developer_id}")
        self.assertEqual(self.codes(html), {self.palm.code})

    def test_filters_come_from_the_url(self):
        cases = {
            "channel=google": {self.owest},
            "goal=more_leads": {self.palm},
            f"developer={self.other_project.developer_id}": {self.owest},
            f"project={self.project.pk}": {self.palm},
            "status=draft&channel=meta": {self.palm, self.category},
            "channel=tiktok&goal=fame&developer=x&project=-3": {self.palm, self.owest, self.category},
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                html = self.get(self.owner, f"/en/portal/campaigns/?{query}")
                self.assertEqual(self.codes(html), {c.code for c in expected})

    def test_filter_choices_are_the_offices_own(self):
        rival_project = make_project("Mivida", area="New Cairo", developer="Emaar Misr")
        make_campaign(self.rival_office, project=rival_project)
        client = live(CampaignListView, self.owner, "/en/portal/campaigns/")
        client.render()
        view = client.view_instance
        self.assertEqual(
            {d.name_en for d in view.developers}, {"Palm Hills", "Orascom"}
        )
        self.assertEqual({p.name_en for p in view.projects}, {self.project.name_en, "O West"})

        client.send_event("set_filter", field="project", value=str(self.project.pk))
        client.send_event("set_filter", field="developer", value=str(self.other_project.developer_id))
        # A new developer drops the project, whose list now follows the developer.
        client.assert_state(developer=self.other_project.developer_id, project=0)
        client.assert_patch(params={"developer": str(self.other_project.developer_id)})
        client.render()
        self.assertEqual([p.name_en for p in client.view_instance.projects], ["O West"])

    def test_search_and_clear_set_the_url_exactly(self):
        client = live(CampaignListView, self.owner, "/en/portal/campaigns/")
        client.send_event("set_filter", field="channel", value="google")
        client.send_event("search", value="  o west ")
        client.assert_state(q="o west", channel="google")
        client.assert_patch(params={"q": "o west", "channel": "google"})
        self.assertEqual([c.pk for c in client.view_instance._page.object_list], [self.owest.pk])

        client.send_event("set_filter", field="office", value="x")  # not a filter: ignored
        client.assert_state(channel="google")
        client.send_event("clear_filters")
        client.assert_state(q="", channel="")
        self.assertEqual(client.view_instance._pending_navigation[-1]["params"], {})

    def test_search_text_is_capped(self):
        client = live(CampaignListView, self.owner, "/en/portal/campaigns/")
        client.send_event("search", value="x" * 500)
        self.assertEqual(len(client.view_instance.q), CampaignListView.search_length)

    def test_rows_show_the_prototype_columns(self):
        html = self.get(self.owner, "/en/portal/campaigns/")
        for text in ("Channel", "Goal", "Budget", "Leads", "Interested", "No answer", "CPL"):
            self.assertIn(f">{text}</th>", html)
        self.assertIn(">Google</span>", html)
        self.assertIn(">Meta</span>", html)
        self.assertIn("More leads", html)
        self.assertIn('<span dir="ltr" class="font-medium tabular-nums">$1,250</span>', html)
        self.assertIn("in total", html)
        self.assertIn("Orascom · 6th of October", html)

    def test_each_row_offers_the_next_action(self):
        sent = self.complete_campaign()
        sent.submit()
        sent.request_changes("Use a brighter image.")
        empty = Campaign.objects.create(office=self.office, target_kind=TargetKind.CATEGORY)
        html = self.get(self.owner, "/en/portal/campaigns/")
        # A draft carries on where it stopped: these have their target but no creative yet.
        self.assertIn(f'href="/en/portal/campaigns/{self.palm.pk}/creative/" dj-navigate=', html)
        self.assertIn(f'href="/en/portal/campaigns/{self.category.pk}/creative/" dj-navigate=', html)
        self.assertIn(f'href="/en/portal/campaigns/{empty.pk}/target/" dj-navigate=', html)
        self.assertRegex(html, rf'href="/en/portal/campaigns/{sent.pk}/"[^>]*class="btn-primary[^"]*">Fix')

        # Without run_campaigns (ROADMAP Q7): Open only.
        html = self.get(self.member, "/en/portal/campaigns/")
        self.assertNotIn("/creative/", html)
        self.assertNotIn(">Fix<", html)
        self.assertNotIn(">Edit<", html)
        self.assertIn(">Open<", html)

    def test_builder_url_is_the_first_missing_step(self):
        campaign = self.complete_campaign()
        self.assertEqual(campaign.builder_url, f"/en/portal/campaigns/{campaign.pk}/")
        campaign.audience = None
        self.assertEqual(campaign.builder_url, f"/en/portal/campaigns/{campaign.pk}/audience/")

    def test_rows_cost_no_query_each(self):
        def queries():
            self.client.force_login(self.owner)
            from django.db import connection
            from django.test.utils import CaptureQueriesContext

            with CaptureQueriesContext(connection) as captured:
                self.client.get("/en/portal/campaigns/")
            return len(captured)

        before = queries()
        for _ in range(5):
            self.complete_campaign()
        self.assertEqual(queries(), before)

    def test_empty_states(self):
        Campaign.objects.filter(office=self.office).delete()
        html = self.get(self.owner, "/en/portal/campaigns/")
        self.assertIn("Build your first campaign", html)
        self.assertNotIn("Build your first campaign", self.get(self.member, "/en/portal/campaigns/"))
        html = self.get(self.owner, "/en/portal/campaigns/?q=zzz")
        self.assertIn("No campaigns match this search and these filters.", html)
        self.assertIn('dj-click="clear_filters"', html)

    def test_arabic_page(self):
        html = self.get(self.owner, "/ar/portal/campaigns/")
        self.assertIn('dir="rtl"', html)
        self.assertIn("بحث", html)
        self.assertIn(">ميتا</span>", html)


class ChannelsMigrationTests(TransactionTestCase):
    """campaigns 0002 moves existing campaigns onto the new fields.

    It must run on PostgreSQL with campaigns already in the table. When the
    rows were written in the migration's own transaction (as after a table
    rewrite), updating them queues the deferred foreign-key checks, and the
    ALTER TABLE that followed used to fail with "pending trigger events"
    (seen on the Docker database with demo campaigns). The tests write the
    rows and migrate in one transaction to take that path.
    """

    before = [("campaigns", "0001_initial")]
    after = [("campaigns", "0002_channels_and_connected_accounts")]

    def setUp(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        self.connection = connection
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.before)
        self.addCleanup(self._migrate_to_latest)

    def _make_campaigns(self):
        old = self.executor.loader.project_state(self.before).apps
        Office = old.get_model("accounts", "BrokerOffice")
        Developer = old.get_model("catalog", "Developer")
        Area = old.get_model("catalog", "Area")
        Project = old.get_model("catalog", "Project")
        Campaign = old.get_model("campaigns", "Campaign")
        office = Office.objects.create(company_name="Nour Realty")
        self.developer = Developer.objects.create(name_en="Palm Hills")
        project = Project.objects.create(
            name_en="Badya", developer=self.developer, area=Area.objects.create(name_en="October")
        )
        self.office_id = office.pk
        self.project_campaign = Campaign.objects.create(office=office, project=project).pk
        self.page_campaign = Campaign.objects.create(
            office=office, target_kind="category", destination="connected_page",
            connected_page_handle="nour.realty",
        ).pk
        # Check the inserts now, then defer again as a new transaction would,
        # so only the migration's own updates queue checks.
        with self.connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")

    def _migrate_to_latest(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(self.connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _migrate(self, targets):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(self.connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_existing_campaigns_keep_their_choices(self):
        with transaction.atomic():
            self._make_campaigns()
            new = self._migrate(self.after)
        Campaign = new.get_model("campaigns", "Campaign")

        project_campaign = Campaign.objects.get(pk=self.project_campaign)
        self.assertEqual(project_campaign.developer_id, self.developer.pk)
        page_campaign = Campaign.objects.get(pk=self.page_campaign)
        self.assertEqual(page_campaign.destination, "own_account")
        self.assertEqual(page_campaign.connected_account.handle, "@nour.realty")
        self.assertEqual(page_campaign.connected_account.platform, "instagram")
        self.assertEqual(page_campaign.connected_account.office_id, self.office_id)

    def test_it_reverses(self):
        with transaction.atomic():
            self._make_campaigns()
            self._migrate(self.after)
            old = self._migrate(self.before)
        page_campaign = old.get_model("campaigns", "Campaign").objects.get(pk=self.page_campaign)
        self.assertEqual(page_campaign.destination, "connected_page")
        self.assertEqual(page_campaign.connected_page_handle, "@nour.realty")
