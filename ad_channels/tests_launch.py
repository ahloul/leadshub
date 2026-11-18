"""US-META-02: launching ready campaigns on Meta (FR-6.10, FR-7.6, FR-5.14).

Meta is never called: ``meta_ads.sync.client_for`` is replaced by a fake.
"""

import datetime
import json
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from facebook_business.exceptions import FacebookRequestError

from accounts.roles import Role
from accounts.test_utils import make_office, make_user
from campaigns import services as campaign_services
from campaigns.models import AccountStatus, Campaign, Channel, ConnectedAccount, Destination, Platform, Status, TargetKind
from catalog.models import AudiencePreset
from creatives.models import AssetType
from creatives.test_utils import TemporaryUploadsMixin, make_creative, make_project
from reviews import selectors as review_selectors
from reviews import services as review_services
from reviews.tests import ReviewSetup

from . import services
from .meta_ads.client import LaunchState, LeadFormInfo, MetaClient, MetaError, PageInfo
from .test_utils import FakeMetaLauncher
from .models import MetaAdAccount, MetaBusinessManager, MetaLaunch

TOKEN = "EAAG-system-user-token-1234"


def make_account(meta_account_id="act_1001", *, bm=None, enabled=True):
    if bm is None:
        bm = MetaBusinessManager(
            name="BM One", meta_business_id="111", token_status=MetaBusinessManager.TokenStatus.VALID
        )
        bm.set_token(TOKEN)
        bm.save()
    return MetaAdAccount.objects.create(
        business_manager=bm, meta_account_id=meta_account_id, name="Account 1001",
        enabled_for_launch=enabled,
    )


@override_settings(META_TOKEN_KEYS=[Fernet.generate_key().decode()])
class LaunchSetup(ReviewSetup):
    """ReviewSetup's campaign, approved and ready, plus a healthy ad account."""

    def setUp(self):
        super().setUp()
        review_services.approve(actor=self.buyer, task_id=self.task.pk)
        self.refresh()
        self.account = make_account()

    def launch(self, **fields):
        fields.setdefault("status", MetaLaunch.Status.BUILDING)
        return MetaLaunch.objects.create(
            campaign=self.campaign, ad_account=self.account, page_id="p1", page_name="Page",
            lead_form_id="f1", lead_form_name="Form", budget_kind=self.campaign.budget_kind,
            budget_usd=self.campaign.budget_usd, launched_by=self.buyer, **fields,
        )


class MetaLaunchModelTests(LaunchSetup):
    def test_one_launch_per_campaign(self):
        self.launch()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.launch()

    def test_ad_account_in_use_cannot_be_deleted(self):
        self.launch()
        with self.assertRaises(ProtectedError):
            self.account.delete()

    def test_stale_build_after_five_minutes(self):
        launch = self.launch()
        now = timezone.now()
        self.assertFalse(launch.is_stale_build(now + datetime.timedelta(minutes=4)))
        self.assertTrue(launch.is_stale_build(now + datetime.timedelta(minutes=6)))
        launch.status = MetaLaunch.Status.BUILD_FAILED
        self.assertFalse(launch.is_stale_build(now + datetime.timedelta(minutes=6)))


class ReadyForMetaTests(LaunchSetup):
    def test_lists_approved_meta_campaigns_only(self):
        google = self.sent_campaign(channel=Channel.GOOGLE, destination=Destination.LANDING_PAGE)
        google_task = google.review_tasks.get()
        review_services.approve(actor=self.buyer, task_id=google_task.pk)
        self.sent_campaign()  # open, not approved
        ready = list(review_selectors.ready_for_meta())
        self.assertEqual([t.campaign_id for t in ready], [self.campaign.pk])

    def test_deployed_and_launched_campaigns_leave_the_list(self):
        launch = self.launch(status=MetaLaunch.Status.BUILD_FAILED)
        self.assertEqual(review_selectors.ready_for_meta().count(), 1)  # failed builds stay ready
        launch.status = MetaLaunch.Status.ACTIVE
        launch.save()
        self.assertEqual(review_selectors.ready_for_meta().count(), 0)

    def test_ready_task_for(self):
        self.assertEqual(review_selectors.ready_task_for(self.campaign.pk), self.task)
        self.assertIsNone(review_selectors.ready_task_for(self.campaign.pk + 999))

    def test_record_deployment_needs_an_approved_task(self):
        open_campaign = self.sent_campaign()
        task = open_campaign.review_tasks.get()
        with self.assertRaises(ValidationError):
            review_services.record_deployment(task_id=task.pk, actor=self.buyer)

    def test_record_deployment_ignores_assignment(self):
        self.assertEqual(self.task.assigned_to, self.buyer)  # approving took it
        review_services.record_deployment(task_id=self.task.pk, actor=self.buyer2)
        self.refresh()
        self.assertEqual(self.task.deployed_by, self.buyer2)
        self.assertFalse(self.task.awaiting_deployment)


class _Response:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Graph:
    """Stands in for ``FacebookAdsApi.call``: records calls, answers from ``replies``."""

    def __init__(self, replies=None, error=None):
        self.calls = []
        self.replies = replies or {}
        self.error = error

    def __call__(self, method, path, params=None, headers=None, files=None, **kwargs):
        key = (method, "/".join(map(str, path)))
        self.calls.append({"method": method, "path": key[1], "params": params or {}, "files": files or {}})
        if self.error:
            raise self.error
        reply = self.replies.get(key, {"id": "new1"})
        return _Response(reply.pop(0) if isinstance(reply, list) else reply)


class MetaClientWriteTests(SimpleTestCase):
    """The Graph calls the launch makes (US-META-02). Meta is never called."""

    def meta_for(self, **graph):
        meta = MetaClient(TOKEN)
        self.graph = _Graph(**graph)
        meta._api.call = self.graph
        return meta

    def test_create_campaign_sends_housing_and_egypt_paused(self):
        meta = self.meta_for(replies={("POST", "act_1/campaigns"): {"id": "c1"}})
        self.assertEqual(meta.create_campaign("act_1", "LH-C-0001"), "c1")
        params = self.graph.calls[0]["params"]
        self.assertEqual(params["objective"], "OUTCOME_LEADS")
        self.assertEqual(params["special_ad_categories"], ["HOUSING"])
        self.assertEqual(params["special_ad_category_country"], ["EG"])
        self.assertEqual(params["status"], "PAUSED")
        self.assertEqual(params["name"], "LH-C-0001")

    def test_create_adset_never_sends_age_or_gender(self):
        meta = self.meta_for(replies={("POST", "act_1/adsets"): {"id": "s1"}})
        start = timezone.now()
        adset = meta.create_adset(
            "act_1", name="LH-C-0001", campaign_id="c1", page_id="p1", budget_kind="daily",
            budget_cents=3000, start=start, end=None,
            targeting={"geo_locations": {"countries": ["EG"]}, "age_min": 25, "genders": [1], "zips": ["x"]},
        )
        self.assertEqual(adset, "s1")
        params = self.graph.calls[0]["params"]
        for banned in ("age_min", "age_max", "genders", "zips"):
            self.assertNotIn(banned, params["targeting"])
        self.assertEqual(params["daily_budget"], 3000)
        self.assertNotIn("lifetime_budget", params)
        self.assertEqual(params["optimization_goal"], "LEAD_GENERATION")
        self.assertEqual(params["destination_type"], "ON_AD")
        self.assertEqual(params["promoted_object"], {"page_id": "p1"})
        self.assertEqual(params["status"], "PAUSED")

    def test_lifetime_budget_sends_lifetime_and_end(self):
        meta = self.meta_for()
        end = timezone.now() + datetime.timedelta(days=10)
        meta.create_adset("act_1", name="n", campaign_id="c1", page_id="p1", budget_kind="lifetime",
                          budget_cents=50000, start=timezone.now(), end=end, targeting={})
        params = self.graph.calls[0]["params"]
        self.assertEqual(params["lifetime_budget"], 50000)
        self.assertNotIn("daily_budget", params)
        self.assertIn("end_time", params)

    def test_errors_come_out_as_meta_error_without_the_token(self):
        body = {"error": {"message": f"Invalid token {TOKEN}", "code": 190, "type": "OAuthException"}}
        error = FacebookRequestError("boom", {"method": "POST", "path": "/x"}, 400, {}, json.dumps(body))
        meta = self.meta_for(error=error)
        with self.assertRaises(MetaError) as caught:
            meta.create_campaign("act_1", "n")
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertTrue(caught.exception.token_rejected)

    def test_list_pages_merges_owned_and_client_pages(self):
        meta = self.meta_for(replies={
            ("GET", "111/owned_pages"): {"data": [
                {"id": "p1", "name": "Dark page", "instagram_business_account": {"id": "ig1"}},
            ]},
            ("GET", "111/client_pages"): {"data": [{"id": "p1", "name": "Dark page"}, {"id": "p2", "name": "Nour"}]},
        })
        pages = meta.list_pages("111")
        self.assertEqual([(p.id, p.name, p.instagram_actor_id) for p in pages],
                         [("p1", "Dark page", "ig1"), ("p2", "Nour", "")])

    def test_list_lead_forms_uses_the_page_token_and_keeps_active_forms(self):
        meta = self.meta_for(replies={
            ("GET", "p1"): {"access_token": "PAGE-TOKEN"},
            ("GET", "p1/leadgen_forms"): {"data": [
                {"id": "f1", "name": "Buyers", "status": "ACTIVE"},
                {"id": "f2", "name": "Old", "status": "ARCHIVED"},
            ]},
        })
        forms = meta.list_lead_forms("p1")
        self.assertEqual([(f.id, f.name) for f in forms], [("f1", "Buyers")])
        self.assertEqual(self.graph.calls[1]["params"]["access_token"], "PAGE-TOKEN")

    def test_lead_form_errors_never_show_the_page_token(self):
        body = {"error": {"message": "Bad token PAGE-TOKEN", "code": 190, "type": "OAuthException"}}
        error = FacebookRequestError("boom", {"method": "GET", "path": "/x"}, 400, {}, json.dumps(body))
        meta = self.meta_for(replies={("GET", "p1"): {"access_token": "PAGE-TOKEN"}})
        calls = self.graph

        def second_call_fails(method, path, **kwargs):
            if "leadgen_forms" in path:
                raise error
            return calls(method, path, **kwargs)

        meta._api.call = second_call_fails
        with self.assertRaises(MetaError) as caught:
            meta.list_lead_forms("p1")
        self.assertNotIn("PAGE-TOKEN", str(caught.exception))

    def test_search_interest_needs_an_exact_name(self):
        meta = self.meta_for(replies={("GET", "search"): [
            {"data": [{"id": "600", "name": "Real estate"}]},
            {"data": [{"id": "700", "name": "Compound interest"}]},
        ]})
        self.assertEqual(meta.search_interest("real estate"), "600")
        self.assertIsNone(meta.search_interest("Compounds"))

    def test_find_by_name_filters_by_exact_name(self):
        meta = self.meta_for(replies={("GET", "act_1/campaigns"): {"data": [{"id": "c9", "name": "LH-C-0001"}]}})
        self.assertEqual(meta.find_by_name("act_1", "campaigns", "LH-C-0001"), "c9")
        filtering = self.graph.calls[0]["params"]["filtering"]
        self.assertEqual(filtering[0], {"field": "name", "operator": "EQUAL", "value": "LH-C-0001"})

    def test_upload_image_returns_the_hash(self):
        meta = self.meta_for(replies={("POST", "act_1/adimages"): {"images": {"ad.png": {"hash": "h1"}}}})
        self.assertEqual(meta.upload_image("act_1", "ad.png", b"png"), "h1")

    def test_upload_video_sends_the_file(self):
        meta = self.meta_for(replies={("POST", "act_1/advideos"): {"id": "v1"}})
        self.assertEqual(meta.upload_video("act_1", "reel.mp4", b"mp4"), "v1")
        self.assertIn("source", self.graph.calls[0]["files"])

    def test_video_ready(self):
        meta = self.meta_for(replies={("GET", "v1"): [{"status": {"video_status": "processing"}},
                                                    {"status": {"video_status": "ready"}}]})
        self.assertFalse(meta.video_ready("v1"))
        self.assertTrue(meta.video_ready("v1"))

    def test_ad_creative_links_the_lead_form(self):
        meta = self.meta_for(replies={("POST", "act_1/adcreatives"): {"id": "cr1"}})
        meta.create_ad_creative("act_1", name="n", page_id="p1", instagram_actor_id="ig1", lead_form_id="f1",
                                message="5% DP", image_hash="h1")
        spec = self.graph.calls[0]["params"]["object_story_spec"]
        self.assertEqual(spec["page_id"], "p1")
        self.assertEqual(spec["instagram_user_id"], "ig1")
        cta = spec["link_data"]["call_to_action"]
        self.assertEqual(cta["value"]["lead_gen_form_id"], "f1")

    def test_video_creative_uses_video_data(self):
        meta = self.meta_for()
        meta.create_ad_creative("act_1", name="n", page_id="p1", instagram_actor_id="", lead_form_id="f1",
                                message="m", video_id="v1", image_hash="thumb")
        spec = self.graph.calls[0]["params"]["object_story_spec"]
        self.assertEqual(spec["video_data"]["video_id"], "v1")
        self.assertNotIn("instagram_user_id", spec)

    def test_status_budget_delete_and_read(self):
        meta = self.meta_for(replies={
            ("GET", "c1"): {"effective_status": "PAUSED"},
            ("GET", "s1"): {"effective_status": "ACTIVE", "daily_budget": "3000", "end_time": None},
        })
        meta.set_status("c1", "ACTIVE")
        meta.update_adset("s1", budget_kind="daily", budget_cents=2500, end=None)
        meta.delete_object("ad1")
        state = meta.read_launch("c1", "s1")
        self.assertEqual([c["method"] for c in self.graph.calls[:3]], ["POST", "POST", "DELETE"])
        self.assertEqual(self.graph.calls[0]["params"], {"status": "ACTIVE"})
        self.assertEqual(self.graph.calls[1]["params"]["daily_budget"], 2500)
        self.assertEqual((state.campaign_status, state.adset_status, state.budget_cents, state.end),
                         ("PAUSED", "ACTIVE", 3000, None))

    def test_ad_account_currency(self):
        meta = self.meta_for(replies={("GET", "act_1"): {"currency": "EGP"}})
        self.assertEqual(meta.ad_account_currency("act_1"), "EGP")

    def test_create_ad_is_paused(self):
        meta = self.meta_for(replies={("POST", "act_1/ads"): {"id": "ad1"}})
        self.assertEqual(meta.create_ad("act_1", name="n", adset_id="s1", creative_id="cr1"), "ad1")
        params = self.graph.calls[0]["params"]
        self.assertEqual(params["creative"], {"creative_id": "cr1"})
        self.assertEqual(params["status"], "PAUSED")


class BuildSetup(LaunchSetup):
    """A ready campaign, a healthy USD account and a fake Meta behind ``sync.client_for``."""

    def setUp(self):
        super().setUp()
        self.meta = FakeMetaLauncher()
        patcher = mock.patch("ad_channels.meta_ads.sync.client_for", self.meta)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = mock.patch("ad_channels.services._sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def build(self, actor=None, **kwargs):
        kwargs.setdefault("ad_account_id", self.account.pk)
        kwargs.setdefault("page_id", "p1")
        kwargs.setdefault("lead_form_id", "f1")
        return services.build(actor=actor or self.buyer, campaign_id=self.campaign.pk, **kwargs)

    def set_campaign(self, **fields):
        Campaign.objects.filter(pk=self.campaign.pk).update(**fields)
        self.campaign.refresh_from_db()

    def stored(self):
        return MetaLaunch.objects.get(campaign=self.campaign)


class BuildTests(BuildSetup):
    def test_build_creates_everything_paused_then_activates(self):
        launch = self.build()
        order = [name for name, _ in self.meta.calls if name.startswith(("create_", "set_status", "upload_"))]
        self.assertEqual(order, ["create_campaign", "create_adset", "upload_image", "create_ad_creative",
                                 "create_ad", "set_status", "set_status", "set_status"])
        activated = [c["object_id"] for c in self.meta.called("set_status")]
        ad_id = launch.ads[0]["ad_id"]
        self.assertEqual(activated, [ad_id, launch.meta_adset_id, launch.meta_campaign_id])
        self.assertTrue(all(c["status"] == "ACTIVE" for c in self.meta.called("set_status")))
        self.assertEqual(launch.status, MetaLaunch.Status.ACTIVE)
        self.assertEqual((launch.page_name, launch.instagram_actor_id, launch.lead_form_name),
                         ("LeadsHub Homes", "ig1", "Buyers form"))
        self.refresh()
        self.assertEqual(self.campaign.status, Status.LIVE)
        self.assertEqual(self.campaign.external_id, launch.meta_adset_id)
        self.assertEqual(self.campaign.platform_data["ad_account_id"], "act_1001")
        self.assertEqual(self.task.deployed_by, self.buyer)
        self.assertEqual(self.meta.called("create_campaign")[0]["name"], self.campaign.code)
        self.assertEqual(self.meta.called("create_adset")[0]["name"], self.campaign.code)

    def test_lead_can_build_a_task_assigned_to_someone_else(self):
        self.build(actor=self.lead)
        self.assertEqual(self.stored().launched_by, self.lead)

    def test_role_without_capability_is_refused(self):
        for user in (self.content, self.finance, self.owner):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                self.build(actor=user)
        self.assertEqual(self.meta.calls, [])

    def test_campaign_no_longer_ready_is_refused(self):
        review_services.mark_deployed(actor=self.buyer, task_id=self.task.pk, network_id="120210000000001",
                                      ad_account="act_1234567890")
        with self.assertRaisesMessage(services.LaunchError, "isn't ready"):
            self.build()

    def test_google_campaign_is_refused(self):
        self.set_campaign(channel=Channel.GOOGLE)
        with self.assertRaises(services.LaunchError):
            self.build()

    def test_second_build_while_building_is_refused(self):
        self.launch(status=MetaLaunch.Status.BUILDING)
        with self.assertRaisesMessage(services.LaunchError, "already being built"):
            self.build()

    def test_stale_building_can_resume(self):
        launch = self.launch(status=MetaLaunch.Status.BUILDING)
        MetaLaunch.objects.filter(pk=launch.pk).update(updated_at=timezone.now() - datetime.timedelta(minutes=6))
        self.assertEqual(self.build().status, MetaLaunch.Status.ACTIVE)

    def test_account_no_longer_available_is_refused(self):
        MetaAdAccount.objects.filter(pk=self.account.pk).update(enabled_for_launch=False)
        with self.assertRaisesMessage(services.LaunchError, "ad account"):
            self.build()
        self.assertFalse(MetaLaunch.objects.exists())

    def test_non_usd_account_is_refused(self):
        self.meta.currency = "EGP"
        with self.assertRaisesMessage(services.LaunchError, "EGP"):
            self.build()
        self.assertEqual(self.meta.called("create_campaign"), [])

    def test_unknown_page_or_form_is_refused(self):
        with self.assertRaisesMessage(services.LaunchError, "page"):
            self.build(page_id="p-other")
        with self.assertRaisesMessage(services.LaunchError, "form"):
            self.build(lead_form_id="f-other")
        self.assertEqual(self.meta.called("create_campaign"), [])

    def test_expired_creative_is_refused_by_name(self):
        self.creative.offer_expires_on = timezone.localdate() - datetime.timedelta(days=1)
        self.creative.save()
        with self.assertRaisesMessage(services.LaunchError, "O West launch"):
            self.build()

    def test_carousel_creative_is_refused(self):
        self.creative.asset_type = AssetType.CAROUSEL
        self.creative.save()
        with self.assertRaisesMessage(services.LaunchError, "O West launch"):
            self.build()

    def test_past_start_starts_now(self):
        self.set_campaign(starts_on=timezone.localdate() - datetime.timedelta(days=3))
        before = timezone.now()
        self.build()
        self.assertGreaterEqual(self.meta.called("create_adset")[0]["start"], before)

    def test_future_start_starts_that_morning_in_cairo(self):
        self.build()
        start = timezone.localtime(self.meta.called("create_adset")[0]["start"])
        self.assertEqual((start.date(), start.hour), (self.campaign.starts_on, 0))

    def test_end_date_passed_is_refused(self):
        today = timezone.localdate()
        self.set_campaign(starts_on=today - datetime.timedelta(days=5), ends_on=today)
        with self.assertRaisesMessage(services.LaunchError, "end date"):
            self.build()

    def test_lifetime_budget_without_end_is_refused(self):
        self.set_campaign(budget_kind="lifetime", ends_on=None)
        with self.assertRaisesMessage(services.LaunchError, "end date"):
            self.build()

    def test_failure_mid_build_resumes_without_duplicates(self):
        self.meta.fail_on["create_ad"] = MetaError("Temporary problem (Meta error 2)")
        with self.assertRaisesMessage(services.LaunchError, "Temporary problem"):
            self.build()
        failed = self.stored()
        self.assertEqual(failed.status, MetaLaunch.Status.BUILD_FAILED)
        self.assertIn("Temporary problem", failed.last_error)
        self.assertTrue(failed.meta_campaign_id and failed.meta_adset_id)
        self.assertEqual(review_selectors.ready_for_meta().count(), 1)  # still ready to resume

        launch = self.build()
        self.assertEqual(launch.status, MetaLaunch.Status.ACTIVE)
        self.assertEqual(launch.last_error, "")
        for method in ("create_campaign", "create_adset", "upload_image", "create_ad_creative"):
            self.assertEqual(len(self.meta.called(method)), 1, method)

    def test_resume_keeps_the_account_once_meta_objects_exist(self):
        self.meta.fail_on["create_adset"] = MetaError("boom")
        with self.assertRaises(services.LaunchError):
            self.build()
        other = make_account("act_2002", bm=self.account.business_manager)
        with self.assertRaisesMessage(services.LaunchError, "act_1001"):
            self.build(ad_account_id=other.pk)

    def test_lost_response_reuses_existing_campaign_by_name(self):
        self.meta.existing[("campaigns", self.campaign.code)] = "c99"
        self.meta.existing[("adsets", self.campaign.code)] = "s99"
        launch = self.build()
        self.assertEqual((launch.meta_campaign_id, launch.meta_adset_id), ("c99", "s99"))
        self.assertEqual(self.meta.called("create_campaign"), [])
        self.assertEqual(self.meta.called("create_adset"), [])

    def test_token_rejected_marks_business_manager_invalid(self):
        self.meta.fail_on["create_campaign"] = MetaError("Error validating access token (190)", token_rejected=True)
        with self.assertRaises(services.LaunchError):
            self.build()
        bm = MetaBusinessManager.objects.get()
        self.assertEqual(bm.token_status, MetaBusinessManager.TokenStatus.INVALID)
        self.assertEqual(self.stored().status, MetaLaunch.Status.BUILD_FAILED)

    def test_reel_waits_for_processing(self):
        self.creative.asset_type = AssetType.REEL
        self.creative.save()
        launch = self.build()
        self.assertTrue(launch.ads[0]["video_id"])
        self.assertEqual(self.meta.called("create_ad_creative")[0]["video_id"], launch.ads[0]["video_id"])

    def test_reel_still_processing_fails_resumably(self):
        self.creative.asset_type = AssetType.REEL
        self.creative.save()
        self.meta.video_ready_answer = False
        with self.assertRaisesMessage(services.LaunchError, "still processing"):
            self.build()
        self.assertLessEqual(sum(call.args[0] for call in self.sleep.call_args_list), 60)
        self.meta.video_ready_answer = True
        self.build()
        self.assertEqual(len(self.meta.called("upload_video")), 1)

    def test_missing_creative_file_fails_resumably(self):
        self.creative.file.storage.delete(self.creative.file.name)
        with self.assertRaisesMessage(services.LaunchError, "O West launch"):
            self.build()
        self.assertEqual(self.stored().status, MetaLaunch.Status.BUILD_FAILED)

    def test_no_interest_matches_builds_country_only(self):
        self.meta.interests = {}
        launch = self.build()
        targeting = self.meta.called("create_adset")[0]["targeting"]
        self.assertEqual(targeting, {"geo_locations": {"countries": ["EG"]}})
        self.assertEqual(launch.skipped_interests, ["Real estate", "Compounds"])

    def test_matched_interests_are_sent(self):
        self.build()
        targeting = self.meta.called("create_adset")[0]["targeting"]
        self.assertEqual(targeting["flexible_spec"], [{"interests": [{"id": "600"}, {"id": "700"}]}])
        for banned in ("age_min", "age_max", "genders", "zips"):
            self.assertNotIn(banned, targeting)

    def test_budget_cents_round_half_up(self):
        self.assertEqual(services.budget_cents(Decimal("30.005")), 3001)
        self.assertEqual(services.budget_cents(Decimal("30")), 3000)
        self.build()
        self.assertEqual(self.meta.called("create_adset")[0]["budget_cents"], 3000)

    def test_discard_deletes_on_meta_and_returns_campaign_to_ready(self):
        self.meta.fail_on["create_ad"] = MetaError("boom")
        with self.assertRaises(services.LaunchError):
            self.build()
        campaign_id = self.stored().meta_campaign_id
        services.discard_build(actor=self.buyer, campaign_id=self.campaign.pk)
        self.assertEqual(self.meta.called("delete_object"), [{"object_id": campaign_id}])
        self.assertFalse(MetaLaunch.objects.exists())
        self.assertEqual(review_selectors.ready_for_meta().count(), 1)

    def test_build_during_discard_is_refused(self):
        self.meta.fail_on["create_ad"] = MetaError("boom")
        with self.assertRaises(services.LaunchError):
            self.build()
        seen = []

        def build_meanwhile(object_id):
            try:
                self.build()
            except services.LaunchError as error:
                seen.append(str(error))

        with mock.patch.object(self.meta, "delete_object", side_effect=build_meanwhile):
            services.discard_build(actor=self.buyer, campaign_id=self.campaign.pk)
        self.assertEqual(len(seen), 1)
        self.assertIn("already being built", seen[0])

    def test_final_step_failure_pauses_on_meta(self):
        with mock.patch("reviews.services.record_deployment", side_effect=ValidationError("deployed meanwhile")):
            with self.assertRaises(services.LaunchError):
                self.build()
        launch = self.stored()
        self.assertEqual(launch.status, MetaLaunch.Status.BUILD_FAILED)
        self.assertEqual(self.meta.called("set_status")[-1],
                         {"object_id": launch.meta_campaign_id, "status": "PAUSED"})
        self.refresh()
        self.assertEqual(self.campaign.status, Status.AI_BUILDING)

    def test_discard_failure_keeps_the_record_with_the_error(self):
        self.meta.fail_on["create_ad"] = MetaError("boom")
        with self.assertRaises(services.LaunchError):
            self.build()
        self.meta.fail_on["delete_object"] = MetaError("Can't delete now")
        with self.assertRaisesMessage(services.LaunchError, "Can't delete now"):
            services.discard_build(actor=self.buyer, campaign_id=self.campaign.pk)
        self.assertIn("Can't delete now", self.stored().last_error)

    def test_discard_refuses_an_active_launch_and_other_roles(self):
        self.build()
        with self.assertRaises(services.LaunchError):
            services.discard_build(actor=self.buyer, campaign_id=self.campaign.pk)
        with self.assertRaises(PermissionDenied):
            services.discard_build(actor=self.content, campaign_id=self.campaign.pk)

    def test_build_options_lists_only_available_accounts(self):
        make_account("act_3003", bm=self.account.business_manager, enabled=False)
        options = services.build_options(actor=self.buyer, campaign_id=self.campaign.pk)
        self.assertEqual(options.accounts, [self.account])
        self.assertEqual(options.pages, [])
        options = services.build_options(actor=self.buyer, campaign_id=self.campaign.pk,
                                         ad_account_id=self.account.pk, page_id="p1")
        self.assertEqual([p.id for p in options.pages], ["p1"])
        self.assertEqual([f.id for f in options.forms], ["f1"])
        self.assertEqual(options.problems, [])

    def test_page_without_forms_is_a_problem(self):
        self.meta.forms = {}
        options = services.build_options(actor=self.buyer, campaign_id=self.campaign.pk,
                                         ad_account_id=self.account.pk, page_id="p1")
        self.assertTrue(any("lead form" in str(p) for p in options.problems))

    def test_no_pages_and_meta_errors_are_problems(self):
        self.meta.pages = {}
        options = services.build_options(actor=self.buyer, campaign_id=self.campaign.pk,
                                         ad_account_id=self.account.pk)
        self.assertTrue(any("page" in str(p) for p in options.problems))
        self.meta.fail_on["list_pages"] = MetaError("Meta is down")
        options = services.build_options(actor=self.buyer, campaign_id=self.campaign.pk,
                                         ad_account_id=self.account.pk)
        self.assertIn("Meta is down", " ".join(map(str, options.problems)))


class ManageLaunchTests(BuildSetup):
    def setUp(self):
        super().setUp()
        self.launched = self.build()
        self.meta.calls.clear()

    def act(self, name, actor=None, **kwargs):
        return getattr(services, name)(actor=actor or self.buyer, campaign_id=self.campaign.pk, **kwargs)

    def test_pause_and_resume_keep_the_campaign_live(self):
        launch = self.act("pause")
        self.assertEqual(launch.status, MetaLaunch.Status.PAUSED)
        self.assertEqual(self.meta.called("set_status"),
                         [{"object_id": launch.meta_campaign_id, "status": "PAUSED"}])
        self.refresh()
        self.assertEqual(self.campaign.status, Status.LIVE)
        self.act("pause")  # already paused: no call
        self.assertEqual(len(self.meta.called("set_status")), 1)
        self.assertEqual(self.act("resume").status, MetaLaunch.Status.ACTIVE)
        self.assertEqual(self.meta.called("set_status")[-1]["status"], "ACTIVE")

    def test_budget_above_broker_figure_is_refused(self):
        with self.assertRaisesMessage(services.LaunchError, "30"):
            self.act("change_budget", budget_usd=Decimal("30.01"), ends_on=None)
        with self.assertRaises(services.LaunchError):
            self.act("change_budget", budget_usd=Decimal("0"), ends_on=None)
        self.assertEqual(self.meta.called("update_adset"), [])

    def test_budget_change_sent_in_cents_with_end(self):
        ends_on = self.campaign.starts_on + datetime.timedelta(days=10)
        launch = self.act("change_budget", budget_usd=Decimal("25.50"), ends_on=ends_on)
        call = self.meta.called("update_adset")[0]
        self.assertEqual((call["adset_id"], call["budget_kind"], call["budget_cents"]),
                         (launch.meta_adset_id, "daily", 2550))
        self.assertEqual(timezone.localtime(call["end"]).date(), ends_on + datetime.timedelta(days=1))
        launch.refresh_from_db()
        self.assertEqual((launch.budget_usd, launch.ends_on), (Decimal("25.50"), ends_on))

    def test_end_date_must_be_in_the_future_and_within_the_broker_end(self):
        with self.assertRaisesMessage(services.LaunchError, "end date"):
            self.act("change_budget", budget_usd=Decimal("30"), ends_on=timezone.localdate())
        broker_end = self.campaign.starts_on + datetime.timedelta(days=10)
        self.set_campaign(ends_on=broker_end)
        with self.assertRaisesMessage(services.LaunchError, "end date"):
            self.act("change_budget", budget_usd=Decimal("30"), ends_on=broker_end + datetime.timedelta(days=1))

    def test_end_date_cannot_be_removed_when_the_broker_set_one(self):
        self.set_campaign(ends_on=self.campaign.starts_on + datetime.timedelta(days=10))
        with self.assertRaisesMessage(services.LaunchError, "end date"):
            self.act("change_budget", budget_usd=Decimal("20"), ends_on=None)
        self.assertEqual(self.meta.called("update_adset"), [])

    def test_refresh_never_undoes_a_stop_made_meanwhile(self):
        def stop_meanwhile(campaign_id, adset_id):
            services.stop(actor=self.lead, campaign_id=self.campaign.pk)
            return LaunchState(campaign_status="ACTIVE", adset_status="ACTIVE", budget_cents=3000, end=None)

        with mock.patch.object(self.meta, "read_launch", side_effect=stop_meanwhile):
            self.act("refresh")
        self.assertEqual(MetaLaunch.objects.get().status, MetaLaunch.Status.ARCHIVED)

    def test_pause_never_undoes_a_stop_made_meanwhile(self):
        real_set_status = self.meta.set_status

        def stop_meanwhile(object_id, status):
            real_set_status(object_id, status)
            if status == "PAUSED":
                services.stop(actor=self.lead, campaign_id=self.campaign.pk)

        with mock.patch.object(self.meta, "set_status", side_effect=stop_meanwhile):
            with self.assertRaises(services.LaunchError):
                self.act("pause")
        self.assertEqual(MetaLaunch.objects.get().status, MetaLaunch.Status.ARCHIVED)

    def test_stop_archives_and_ends_but_keeps_the_ad_set_id(self):
        launch = self.act("stop")
        self.assertEqual(launch.status, MetaLaunch.Status.ARCHIVED)
        self.assertEqual(self.meta.called("set_status"),
                         [{"object_id": launch.meta_campaign_id, "status": "ARCHIVED"}])
        self.refresh()
        self.assertEqual(self.campaign.status, Status.ENDED)
        self.assertEqual(self.campaign.external_id, launch.meta_adset_id)
        self.assertTrue(MetaLaunch.objects.get().meta_adset_id)

    def test_stop_twice_is_a_no_op(self):
        self.act("stop")
        self.act("stop")
        self.assertEqual(len(self.meta.called("set_status")), 1)

    def test_stopped_launch_cannot_pause_or_change(self):
        self.act("stop")
        for name, kwargs in (("pause", {}), ("resume", {}),
                             ("change_budget", {"budget_usd": Decimal("10"), "ends_on": None})):
            with self.subTest(name), self.assertRaises(services.LaunchError):
                self.act(name, **kwargs)

    def test_refresh_shows_changes_made_in_ads_manager(self):
        self.meta.state = LaunchState(campaign_status="PAUSED", adset_status="ACTIVE", budget_cents=2000,
                                      end=None)
        launch = self.act("refresh")
        self.assertEqual(launch.status, MetaLaunch.Status.PAUSED)
        self.assertEqual(launch.meta_status, "PAUSED")
        self.assertEqual(launch.budget_usd, Decimal("20.00"))
        self.assertIsNotNone(launch.meta_status_checked_at)

    def test_refresh_shows_a_campaign_meta_deleted(self):
        self.meta.state = LaunchState(campaign_status="DELETED", adset_status="DELETED", budget_cents=3000,
                                      end=None)
        launch = self.act("refresh")
        self.assertEqual(launch.meta_status, "DELETED")
        self.assertEqual(launch.status, MetaLaunch.Status.ACTIVE)  # no automatic change (M9)

    def test_meta_error_is_shown_and_kept(self):
        self.meta.fail_on["set_status"] = MetaError("Rate limited (Meta error 17)")
        with self.assertRaisesMessage(services.LaunchError, "Rate limited"):
            self.act("pause")
        launch = MetaLaunch.objects.get()
        self.assertEqual(launch.status, MetaLaunch.Status.ACTIVE)
        self.assertIn("Rate limited", launch.last_error)

    def test_each_action_refuses_a_role_without_the_capability(self):
        for name, kwargs in (("pause", {}), ("resume", {}), ("stop", {}), ("refresh", {}),
                             ("change_budget", {"budget_usd": Decimal("10"), "ends_on": None})):
            with self.subTest(name), self.assertRaises(PermissionDenied):
                self.act(name, actor=self.content, **kwargs)
        self.assertEqual(self.meta.calls, [])

    def test_actions_on_a_campaign_not_launched_raise_launch_error(self):
        other = self.sent_campaign()
        for name in ("pause", "resume", "stop", "refresh"):
            with self.subTest(name), self.assertRaises(services.LaunchError):
                getattr(services, name)(actor=self.buyer, campaign_id=other.pk)


LIST_URL = "/en/back-office/meta/campaigns/"


class ListPageTests(BuildSetup):
    def get(self, user, url=LIST_URL, status=200):
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status, url)
        return response.content.decode()

    def test_ready_tab_lists_ready_campaigns_with_office_and_budget(self):
        html = self.get(self.buyer)
        self.assertIn(self.campaign.code, html)
        self.assertIn("Nour Realty", html)
        self.assertIn("30", html)
        self.assertIn(f"/en/back-office/meta/campaigns/{self.campaign.pk}/", html)

    def test_launched_tab_filters_by_status(self):
        launch = self.build()
        ready = self.get(self.buyer)
        self.assertNotIn(f'data-key="ready-{self.campaign.pk}"', ready)
        launched = self.get(self.buyer, f"{LIST_URL}?tab=launched")
        self.assertIn(f'data-key="launch-{launch.pk}"', launched)
        paused = self.get(self.buyer, f"{LIST_URL}?tab=launched&status=paused")
        self.assertNotIn(f'data-key="launch-{launch.pk}"', paused)
        # Unknown values fall back to all statuses and the first page.
        anything = self.get(self.buyer, f"{LIST_URL}?tab=launched&status=x&page=zz")
        self.assertIn(f'data-key="launch-{launch.pk}"', anything)

    def test_no_token_or_business_manager_id_in_page(self):
        self.build()
        for url in (LIST_URL, f"{LIST_URL}?tab=launched"):
            html = self.get(self.lead, url)
            self.assertNotIn(TOKEN, html)
            self.assertNotIn(self.account.business_manager.token_ciphertext, html)

    def test_others_are_refused(self):
        for user in (self.content, self.finance, self.owner, self.admin):
            with self.subTest(user=user.username):
                self.get(user, status=403)

    def test_ready_tab_query_count_stays_flat(self):
        for _ in range(5):
            task = self.sent_campaign().review_tasks.get()
            review_services.approve(actor=self.buyer, task_id=task.pk)
        self.client.force_login(self.buyer)
        self.client.get(LIST_URL)  # warm caches (session, permissions)
        with CaptureQueriesContext(connection) as few:
            self.client.get(LIST_URL)
        for _ in range(10):
            task = self.sent_campaign().review_tasks.get()
            review_services.approve(actor=self.buyer, task_id=task.pk)
        with CaptureQueriesContext(connection) as many:
            self.client.get(LIST_URL)
        self.assertEqual(len(few), len(many))


def page_url(campaign_id, language="en"):
    return f"/{language}/back-office/meta/campaigns/{campaign_id}/"


class DetailPageTests(BuildSetup):
    def page(self, user=None, campaign=None):
        from reviews.tests import live

        from .views import MetaCampaignView

        campaign = campaign or self.campaign
        return live(MetaCampaignView, user or self.buyer, page_url(campaign.pk), pk=campaign.pk)

    def choose(self, client):
        client.send_event("choose_account", value=str(self.account.pk))
        client.send_event("choose_page", value="p1")
        client.send_event("choose_form", value="f1")

    def test_ready_campaign_shows_the_build_form(self):
        self.client.force_login(self.buyer)
        html = self.client.get(page_url(self.campaign.pk)).content.decode()
        self.assertIn(self.campaign.code, html)
        self.assertIn("Nour Realty", html)
        self.assertIn("O West launch", html)
        self.assertIn('dj-change="choose_account"', html)
        self.assertIn("act_1001", html)
        self.assertNotIn(TOKEN, html)

    def test_build_from_the_page_makes_the_campaign_live(self):
        client = self.page()
        self.choose(client)
        client.assert_state(ad_account_id=self.account.pk, page_id="p1", lead_form_id="f1")
        client.send_event("build_on_meta")
        client.assert_state(building=True)
        client.render_async()
        client.assert_state(building=False, error="")
        self.refresh()
        self.assertEqual(self.campaign.status, Status.LIVE)
        html = client.render()
        self.assertIn(self.stored().meta_adset_id, html)
        self.assertIn('dj-click="pause"', html)

    def test_failed_build_shows_the_error_and_a_build_again_button(self):
        self.meta.fail_on["create_ad"] = MetaError("Ad account has no payment method")
        client = self.page()
        self.choose(client)
        client.send_event("build_on_meta")
        client.render_async()
        self.assertIn("no payment method", client.get_state()["error"])
        html = client.render()
        self.assertIn('dj-click="build_on_meta"', html)
        self.assertIn('dj-click="discard"', html)

    def test_manage_handlers_change_meta(self):
        launch = self.build()
        client = self.page()
        client.send_event("pause")
        self.assertEqual(MetaLaunch.objects.get().status, MetaLaunch.Status.PAUSED)
        client.send_event("resume")
        client.send_event("save_budget", budget_usd="99", ends_on="")
        self.assertIn("30", client.get_state()["error"])
        client.send_event("save_budget", budget_usd="20", ends_on="")
        self.assertEqual(MetaLaunch.objects.get().budget_usd, Decimal("20"))
        client.send_event("save_budget", budget_usd="abc", ends_on="2026-02-30")
        self.assertTrue(client.get_state()["error"])
        client.send_event("refresh")
        client.send_event("stop")
        self.assertEqual(MetaLaunch.objects.get().status, MetaLaunch.Status.ARCHIVED)
        self.assertEqual(launch.pk, MetaLaunch.objects.get().pk)

    def test_each_handler_refuses_a_role_without_the_capability(self):
        client = self.page(user=self.content)
        for name, params in (("choose_account", {"value": "1"}), ("choose_page", {"value": "p1"}),
                             ("choose_form", {"value": "f1"}), ("build_on_meta", {}), ("discard", {}),
                             ("pause", {}), ("resume", {}), ("stop", {}), ("refresh", {}),
                             ("save_budget", {"budget_usd": "1", "ends_on": ""})):
            with self.subTest(name):
                self.assertEqual(client.send_event(name, **params)["error"], "Permission denied")
        self.assertEqual([c for c in self.meta.calls if not c[0].startswith("list_")], [])

    def test_unknown_or_unready_campaign_is_404(self):
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(page_url(self.campaign.pk + 999)).status_code, 404)
        open_campaign = self.sent_campaign()  # not approved yet
        self.assertEqual(self.client.get(page_url(open_campaign.pk)).status_code, 404)

    def test_others_are_refused(self):
        for user in (self.content, self.owner, self.admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(page_url(self.campaign.pk)).status_code, 403)


@override_settings(META_TOKEN_KEYS=[Fernet.generate_key().decode()])
class MetaPagesOverTheSocketTests(TemporaryUploadsMixin, TransactionTestCase):
    """The Meta campaign page through djust's real WebSocket consumer (mount auth, re-check per event)."""

    def setUp(self):
        office = make_office("Nour Realty")
        owner = make_user("owner", Role.BROKER_OWNER, office=office)
        self.buyer = make_user("buyer", Role.MEDIA_BUYER)
        self.content = make_user("content", Role.CONTENT_MANAGER)
        project = make_project("O West", area="6th of October", developer="Orascom")
        creative = make_creative("O West launch", project=project)
        campaign = Campaign.objects.create(
            office=office, target_kind=TargetKind.PROJECT, project=project, developer=project.developer,
            area=project.area, audience=AudiencePreset.objects.create(name_en="October buyers"),
            budget_usd=Decimal("30"), starts_on=timezone.localdate() + datetime.timedelta(days=1),
        )
        campaign.creatives.add(creative)
        campaign_services.run_campaign(actor=owner, campaign_id=campaign.pk)
        review_services.approve(actor=self.buyer, task_id=campaign.review_tasks.get().pk)
        self.campaign = campaign
        make_account()
        self.meta = FakeMetaLauncher()
        patcher = mock.patch("ad_channels.meta_ads.sync.client_for", self.meta)
        patcher.start()
        self.addCleanup(patcher.stop)

    def socket_for(self, user):
        from channels.testing import WebsocketCommunicator
        from django.test import Client

        from leadshub.asgi import application

        web = Client()
        web.force_login(user)
        cookie = f"sessionid={web.cookies['sessionid'].value}".encode()
        headers = [(b"cookie", cookie), (b"origin", b"http://testserver"), (b"host", b"testserver")]
        return WebsocketCommunicator(application, "/ws/live/", headers=headers)

    async def open(self, user):
        from asgiref.sync import sync_to_async

        socket = await sync_to_async(self.socket_for)(user)
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        self.assertEqual((await socket.receive_json_from())["type"], "connect")
        await socket.send_json_to({"type": "mount", "view": "ad_channels.views.MetaCampaignView",
                                   "url": page_url(self.campaign.pk)})
        return socket, await socket.receive_json_from(timeout=5)

    async def test_a_role_without_the_capability_is_refused_at_mount(self):
        socket, reply = await self.open(self.content)
        self.assertEqual(reply, {"type": "error", "error": "Permission denied"})
        await socket.disconnect()

    async def test_capability_removed_mid_session_refuses_next_event(self):
        from asgiref.sync import sync_to_async
        from django.contrib.auth.models import Group

        socket, reply = await self.open(self.buyer)
        self.assertEqual(reply["type"], "mount")
        self.assertIn(self.campaign.code, reply["html"])

        @sync_to_async
        def revoke():
            Group.objects.get(name=Role.MEDIA_BUYER).user_set.remove(self.buyer)

        await revoke()
        await socket.send_json_to({"type": "event", "event": "choose_account", "params": {"value": "1"}})
        reply = await socket.receive_json_from(timeout=5)
        # djust re-checks the page's permissions on every event and sends the
        # page away (as for a deactivated user, leadshub.tests); the handler,
        # which would load the account's pages from Meta, never runs.
        self.assertEqual(reply.get("type"), "navigate")
        self.assertEqual(self.meta.calls, [])
        await socket.disconnect()


class ArabicPageTests(BuildSetup):
    def test_arabic_page_shows_arabic_refusal(self):
        from reviews.tests import live

        from .views import MetaCampaignView

        self.client.force_login(self.buyer)
        html = self.client.get(page_url(self.campaign.pk, "ar")).content.decode()
        self.assertIn('dir="rtl"', html)
        self.assertIn("ابنِ على ميتا", html)
        self.assertIn("حملات ميتا", self.client.get("/ar/back-office/meta/campaigns/").content.decode())

        self.meta.currency = "EGP"
        client = live(MetaCampaignView, self.buyer, page_url(self.campaign.pk, "ar"), pk=self.campaign.pk)
        client.send_event("choose_account", value=str(self.account.pk))
        client.send_event("choose_page", value="p1")
        client.send_event("choose_form", value="f1")
        client.send_event("build_on_meta")
        client.render_async()
        error = client.get_state()["error"]
        self.assertIn("EGP", error)
        self.assertIn("بالدولار", error)


class FinalReviewFixTests(BuildSetup):
    """Fixes from the whole-branch review (US-META-02)."""

    def test_second_build_cannot_pause_a_campaign_that_just_went_live(self):
        real_forms = self.meta.list_lead_forms
        finished = []

        def other_buyer_finishes(page_id):
            if not finished:
                finished.append(True)
                services.build(actor=self.lead, campaign_id=self.campaign.pk, ad_account_id=self.account.pk,
                               page_id="p1", lead_form_id="f1")
            return real_forms(page_id)

        with mock.patch.object(self.meta, "list_lead_forms", side_effect=other_buyer_finishes):
            with self.assertRaises(services.LaunchError):
                self.build()
        self.assertEqual(self.stored().status, MetaLaunch.Status.ACTIVE)
        self.refresh()
        self.assertEqual(self.campaign.status, Status.LIVE)
        self.assertNotIn("PAUSED", [c["status"] for c in self.meta.called("set_status")])

    def test_manual_mark_deployed_is_refused_once_a_meta_build_exists(self):
        self.meta.fail_on["create_ad"] = MetaError("boom")
        with self.assertRaises(services.LaunchError):
            self.build()
        with self.assertRaises(ValidationError):
            review_services.mark_deployed(actor=self.buyer, task_id=self.task.pk, network_id="120210000000001",
                                          ad_account="act_1234567890")

    def test_review_page_hides_mark_deployed_once_a_meta_build_exists(self):
        self.client.force_login(self.buyer)
        url = f"/en/back-office/queue/{self.task.pk}/"
        self.assertIn('dj-submit="mark_deployed"', self.client.get(url).content.decode())
        self.meta.fail_on["create_ad"] = MetaError("boom")
        with self.assertRaises(services.LaunchError):
            self.build()
        self.assertNotIn('dj-submit="mark_deployed"', self.client.get(url).content.decode())

    def test_a_failed_build_stays_reachable_after_its_task_was_deployed(self):
        self.meta.fail_on["create_ad"] = MetaError("boom")
        with self.assertRaises(services.LaunchError):
            self.build()
        type(self.task).objects.filter(pk=self.task.pk).update(deployed_at=timezone.now())
        self.client.force_login(self.buyer)
        html = self.client.get(page_url(self.campaign.pk)).content.decode()
        self.assertIn('dj-click="discard"', html)

    def own_page_campaign(self, platform, external_id):
        account = ConnectedAccount.objects.create(
            office=self.office, platform=platform, handle="@nour.realty", status=AccountStatus.VERIFIED,
            external_id=external_id)
        self.set_campaign(destination=Destination.OWN_ACCOUNT, connected_account=account)

    def test_own_account_needs_the_brokers_own_page(self):
        self.meta.pages = {"111": [PageInfo(id="p1", name="LeadsHub Homes", instagram_actor_id="ig1", owned=True),
                                   PageInfo(id="p9", name="Nour", instagram_actor_id="ig9", owned=False)]}
        self.meta.forms["p9"] = [LeadFormInfo(id="f9", name="Nour form")]
        self.own_page_campaign(Platform.FACEBOOK, "p9")
        with self.assertRaisesMessage(services.LaunchError, "broker"):
            self.build()
        options = services.build_options(actor=self.buyer, campaign_id=self.campaign.pk,
                                         ad_account_id=self.account.pk)
        self.assertEqual([p.id for p in options.pages], ["p9"])
        self.assertEqual(self.build(page_id="p9", lead_form_id="f9").page_id, "p9")

    def test_own_instagram_matches_the_pages_instagram_account(self):
        self.meta.pages = {"111": [PageInfo(id="p9", name="Nour", instagram_actor_id="ig9", owned=False)]}
        self.meta.forms["p9"] = [LeadFormInfo(id="f9", name="Nour form")]
        self.own_page_campaign(Platform.INSTAGRAM, "ig9")
        self.assertEqual(self.build(page_id="p9", lead_form_id="f9").page_id, "p9")

    def test_unverified_own_account_is_refused(self):
        account = ConnectedAccount.objects.create(office=self.office, platform=Platform.FACEBOOK,
                                                  handle="@nour.realty")
        self.set_campaign(destination=Destination.OWN_ACCOUNT, connected_account=account)
        with self.assertRaisesMessage(services.LaunchError, "verified"):
            self.build()

    def test_dark_page_needs_a_platform_owned_page(self):
        self.meta.pages = {"111": [PageInfo(id="p9", name="Nour", instagram_actor_id="", owned=False)]}
        self.meta.forms["p9"] = [LeadFormInfo(id="f9", name="Nour form")]
        with self.assertRaisesMessage(services.LaunchError, "page"):
            self.build(page_id="p9", lead_form_id="f9")

    def test_landing_page_destination_is_refused_for_now(self):
        self.set_campaign(destination=Destination.LANDING_PAGE)
        with self.assertRaisesMessage(services.LaunchError, "landing page"):
            self.build()

    def test_reel_sends_its_thumbnail(self):
        self.creative.asset_type = AssetType.REEL
        self.creative.save()
        self.build()
        call = self.meta.called("create_ad_creative")[0]
        self.assertEqual(call["image_url"], "https://thumb.test/v.jpg")

    def test_unexpected_error_marks_the_build_failed(self):
        self.meta.fail_on["create_ad"] = KeyError("id")
        with self.assertRaises(services.LaunchError):
            self.build()
        launch = self.stored()
        self.assertEqual(launch.status, MetaLaunch.Status.BUILD_FAILED)
        self.assertTrue(launch.last_error)

    def test_creative_without_a_file_fails_resumably_by_name(self):
        type(self.creative).objects.filter(pk=self.creative.pk).update(file="")
        with self.assertRaisesMessage(services.LaunchError, "O West launch"):
            self.build()
        self.assertEqual(self.stored().status, MetaLaunch.Status.BUILD_FAILED)


class AdvantageAudienceTests(SimpleTestCase):
    def test_adset_turns_advantage_audience_off(self):
        meta = MetaClient(TOKEN)
        graph = _Graph()
        meta._api.call = graph
        meta.create_adset("act_1", name="n", campaign_id="c1", page_id="p1", budget_kind="daily",
                          budget_cents=3000, start=timezone.now(), end=None, targeting={})
        self.assertEqual(graph.calls[0]["params"]["targeting"]["targeting_automation"], {"advantage_audience": 0})

    def test_video_creative_carries_the_thumbnail(self):
        meta = MetaClient(TOKEN)
        graph = _Graph()
        meta._api.call = graph
        meta.create_ad_creative("act_1", name="n", page_id="p1", instagram_actor_id="", lead_form_id="f1",
                                message="m", video_id="v1", image_url="https://thumb.test/v.jpg")
        self.assertEqual(graph.calls[0]["params"]["object_story_spec"]["video_data"]["image_url"],
                         "https://thumb.test/v.jpg")

    def test_video_thumbnail(self):
        meta = MetaClient(TOKEN)
        meta._api.call = _Graph(replies={("GET", "v1"): {"picture": "https://thumb.test/v.jpg"}})
        self.assertEqual(meta.video_thumbnail("v1"), "https://thumb.test/v.jpg")
