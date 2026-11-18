"""US-META-02: launching ready campaigns on Meta (FR-6.10, FR-7.6, FR-5.14).

Meta is never called: ``meta_ads.sync.client_for`` is replaced by a fake.
"""

import datetime
import json

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from facebook_business.exceptions import FacebookRequestError

from campaigns.models import Channel, Destination
from reviews import selectors as review_selectors
from reviews import services as review_services
from reviews.tests import ReviewSetup

from .meta_ads.client import MetaClient, MetaError
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
