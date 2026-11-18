"""US-META-02: launching ready campaigns on Meta (FR-6.10, FR-7.6, FR-5.14).

Meta is never called: ``meta_ads.sync.client_for`` is replaced by a fake.
"""

import datetime

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import override_settings
from django.utils import timezone

from campaigns.models import Channel, Destination
from reviews import selectors as review_selectors
from reviews import services as review_services
from reviews.tests import ReviewSetup

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
