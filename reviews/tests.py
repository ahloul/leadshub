"""The review queue, its 4-hour SLA, escalation and briefs (SRS §4.1, FR-6.1, FR-6.2, FR-6.7; ROADMAP M4)."""

import datetime
import json
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone, translation
from djust.testing import LiveViewTestClient

from accounts.roles import Role
from accounts.test_utils import make_office, make_user
from campaigns import services as campaign_services
from campaigns.models import BudgetKind, Campaign, Channel, ConnectedAccount, Destination, Platform, Status
from campaigns.models import TargetKind
from catalog.models import AudiencePreset
from creatives.test_utils import TemporaryUploadsMixin, make_creative, make_project

from . import briefs, selectors, services
from .models import NEAR_BREACH, SLA, Brief, ImmutableBrief, Kind, ReviewTask, TaskStatus, format_countdown

HOUR = datetime.timedelta(hours=1)
MINUTE = datetime.timedelta(minutes=1)


class _PageRequests(RequestFactory):
    def __init__(self, path, **defaults):
        super().__init__(**defaults)
        self.page_path = path

    def get(self, path, *args, **kwargs):
        return super().get(self.page_path, *args, **kwargs)


def live(view_class, user, path, **params):
    client = LiveViewTestClient(view_class, user=user, request_factory=_PageRequests(path))
    return client.mount(**params)


class ReviewSetup(TemporaryUploadsMixin, TestCase):
    """An office with a campaign sent to review, the internal roles, and a rival office."""

    def setUp(self):
        self.enterContext(translation.override("en"))
        self.office = make_office("Nour Realty", wallet_balance_usd=Decimal("3180"), spend_tier="gold")
        self.owner = make_user("owner", Role.BROKER_OWNER, office=self.office, email="owner@nour.test")
        self.member = make_user("sara", Role.BROKER_TEAM_MEMBER, office=self.office, email="sara@nour.test")
        self.rival_office = make_office("Cairo Homes")
        self.rival = make_user("rival", Role.BROKER_OWNER, office=self.rival_office, email="r@cairo.test")

        self.buyer = make_user("buyer", Role.MEDIA_BUYER, email="buyer@lh.test", first_name="Mona")
        self.buyer2 = make_user("buyer2", Role.MEDIA_BUYER, email="buyer2@lh.test")
        self.lead = make_user("lead", Role.MEDIA_BUYER, Role.MEDIA_BUYING_LEAD, email="lead@lh.test")
        self.admin = make_user("platform", Role.PLATFORM_ADMIN, email="admin@lh.test")
        self.content = make_user("content", Role.CONTENT_MANAGER, email="content@lh.test")
        self.finance = make_user("finance", Role.FINANCE_MANAGER, email="finance@lh.test")

        self.project = make_project("O West", area="6th of October", developer="Orascom")
        self.creative = make_creative("O West launch", project=self.project, offer="5% DP · 10-yr plan")
        self.audience = AudiencePreset.objects.create(
            name_en="October buyers", area=self.project.area,
            meta_interests="Real estate\nCompounds\n", lookalike_source="closed_won_1pct",
            geo_targeting="6th of October + 15km",
        )
        self.campaign = self.sent_campaign()
        self.task = ReviewTask.objects.get(campaign=self.campaign)

    def ready_campaign(self, office=None, **fields):
        office = office or self.office
        fields.setdefault("destination", Destination.DARK_PAGE)
        campaign = Campaign.objects.create(
            office=office, target_kind=TargetKind.PROJECT, project=self.project,
            developer=self.project.developer, area=self.project.area, audience=self.audience,
            budget_kind=BudgetKind.DAILY, budget_usd=Decimal("30"),
            starts_on=timezone.localdate() + datetime.timedelta(days=1), **fields,
        )
        campaign.creatives.add(self.creative)
        return campaign

    def sent_campaign(self, office=None, **fields):
        """A complete campaign sent to review the way a broker sends it (Run)."""
        campaign = self.ready_campaign(office, **fields)
        owner = self.owner if office is None else self.rival
        return campaign_services.run_campaign(actor=owner, campaign_id=campaign.pk)

    def refresh(self):
        self.task.refresh_from_db()
        self.campaign.refresh_from_db()


class OpeningTests(ReviewSetup):
    def test_run_opens_a_task_due_in_four_hours(self):
        self.assertEqual(self.task.kind, Kind.NEW_CAMPAIGN)
        self.assertEqual(self.task.status, TaskStatus.OPEN)
        self.assertEqual(self.task.due_at - self.task.created_at, SLA)
        self.assertEqual(SLA, datetime.timedelta(hours=4))
        self.assertRegex(self.task.code, r"^TSK-\d{4}$")
        self.assertEqual(self.campaign.status, Status.AI_BUILDING)

    def test_sending_again_opens_a_new_task(self):
        services.reject(actor=self.buyer, task_id=self.task.pk, reasons="Use a brighter image.")
        campaign_services.run_campaign(actor=self.owner, campaign_id=self.campaign.pk)
        tasks = list(ReviewTask.objects.filter(campaign=self.campaign).order_by("pk"))
        self.assertEqual([t.status for t in tasks], [TaskStatus.REJECTED, TaskStatus.OPEN])
        self.assertEqual(tasks[1].kind, Kind.CAMPAIGN_RESENT)

    def test_only_one_open_task_per_campaign(self):
        again = services.open_campaign_task(self.campaign)
        self.assertEqual(again.pk, self.task.pk)
        self.assertEqual(ReviewTask.objects.filter(campaign=self.campaign).count(), 1)


class SlaTests(ReviewSetup):
    """FR-6.7 with a controlled clock."""

    def test_countdown_near_breach_and_breach(self):
        opened = self.task.created_at
        self.assertEqual(self.task.time_left(opened), SLA)
        self.assertFalse(self.task.is_near_breach(opened + 2 * HOUR))
        self.assertTrue(self.task.is_near_breach(opened + 3 * HOUR))
        self.assertFalse(self.task.is_breached(opened + 3 * HOUR))
        self.assertTrue(self.task.is_breached(opened + SLA))
        self.assertEqual(NEAR_BREACH, HOUR)

    def test_countdown_text(self):
        self.assertEqual(format_countdown(datetime.timedelta(hours=2, minutes=41, seconds=7)), "2:41:07")
        self.assertEqual(format_countdown(-datetime.timedelta(minutes=12, seconds=5)), "-0:12:05")

    def test_counts_follow_the_clock(self):
        late = self.sent_campaign()
        ReviewTask.objects.filter(campaign=late).update(due_at=timezone.now() - MINUTE)
        now = self.task.created_at + 3 * HOUR + 30 * MINUTE
        counts = selectors.counts(user=self.buyer, now=now)
        self.assertEqual(counts["open"], 2)
        self.assertEqual(counts["breached"], 1)
        self.assertEqual(counts["near_breach"], 1)
        near = selectors.open_tasks(show=selectors.Show.NEAR_BREACH, now=self.task.created_at)
        self.assertEqual([t.campaign_id for t in near], [late.pk])

    def test_median_decision_time(self):
        opened = self.task.created_at
        services.approve(actor=self.buyer, task_id=self.task.pk, now=opened + 30 * MINUTE)
        slow = ReviewTask.objects.get(campaign=self.sent_campaign())
        services.reject(actor=self.buyer, task_id=slow.pk, reasons="No.", now=slow.created_at + 5 * HOUR)
        stats = selectors.decision_stats(now=slow.created_at + 6 * HOUR)
        self.assertEqual(stats["decided"], 2)
        self.assertEqual(stats["median_minutes"], round((30 + 300) / 2))
        self.assertEqual(stats["within_sla_percent"], 50)
        slow.refresh_from_db()
        self.assertFalse(slow.met_sla)

    def test_silver_and_higher_offices_are_listed_first(self):
        """ROADMAP Q13(c), CONFIRM."""
        bronze = make_office("Bronze Brokers")
        make_user("bronze", Role.BROKER_OWNER, office=bronze)
        bronze_campaign = self.ready_campaign(bronze)
        bronze_campaign.submit()
        bronze_task = services.open_campaign_task(bronze_campaign, now=self.task.created_at - HOUR)
        order = [t.pk for t in selectors.open_tasks()]
        self.assertEqual(order, [self.task.pk, bronze_task.pk])  # gold first, though due later


class EscalationTests(ReviewSetup):
    """Near breach goes to the people who assign tasks, once (FR-6.7, ROADMAP Q13(b), Q20)."""

    def escalate(self, now):
        with self.captureOnCommitCallbacks(execute=True):
            return services.escalate_near_breach(now=now)

    def test_escalates_once_when_an_hour_is_left(self):
        opened = self.task.created_at
        self.assertEqual(self.escalate(opened + 2 * HOUR), 0)
        self.assertEqual(len(mail.outbox), 0)

        self.assertEqual(self.escalate(opened + 3 * HOUR + MINUTE), 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(set(message.to), {"lead@lh.test", "admin@lh.test"})  # assign_campaign_tasks
        self.assertIn(self.task.code, message.body)
        self.assertIn("Nour Realty", message.body)
        self.assertIn(f"{settings.SITE_BASE_URL}/en/back-office/queue/?show=near_breach", message.body)
        self.assertIn(self.task.code, message.alternatives[0][0])

        self.assertEqual(self.escalate(opened + 3 * HOUR + 2 * MINUTE), 0)  # not twice
        self.assertEqual(len(mail.outbox), 1)
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.escalated_at)

    def test_a_decided_task_is_not_escalated(self):
        services.approve(actor=self.buyer, task_id=self.task.pk)
        self.assertEqual(self.escalate(self.task.created_at + 5 * HOUR), 0)

    def test_escalation_is_scheduled_every_minute(self):
        from django_q.models import Schedule

        from leadshub.jobs import PREFIX, SCHEDULES

        self.assertEqual(
            SCHEDULES["review SLA escalation"]["func"], "reviews.services.escalate_near_breach"
        )
        # Written by post_migrate when the test database was made.
        schedule = Schedule.objects.get(name=PREFIX + "review SLA escalation")
        self.assertEqual((schedule.func, schedule.schedule_type, schedule.minutes),
                         ("reviews.services.escalate_near_breach", Schedule.MINUTES, 1))
        self.assertEqual(schedule.repeats, -1)

    def test_the_scheduler_calls_it_without_arguments(self):
        """django-q2 runs a schedule's function with no arguments."""
        ReviewTask.objects.filter(pk=self.task.pk).update(due_at=timezone.now() + 30 * MINUTE)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.escalate_near_breach(), 1)
        self.assertEqual(len(mail.outbox), 1)


class AssignmentTests(ReviewSetup):
    def test_the_lead_assigns_and_reassigns(self):
        services.assign(actor=self.lead, task_id=self.task.pk, assignee_id=self.buyer.pk)
        self.refresh()
        self.assertEqual(self.task.assigned_to, self.buyer)
        services.assign(actor=self.lead, task_id=self.task.pk, assignee_id=None)
        self.refresh()
        self.assertIsNone(self.task.assigned_to)

    def test_only_people_who_review_can_be_assigned(self):
        for person in (self.content, self.finance, self.owner):
            with self.subTest(person=person.username), self.assertRaises(ValidationError):
                services.assign(actor=self.lead, task_id=self.task.pk, assignee_id=person.pk)

    def test_assigning_needs_its_capability(self):
        for actor in (self.buyer, self.content, self.owner):
            with self.subTest(actor=actor.username), self.assertRaises(PermissionDenied):
                services.assign(actor=actor, task_id=self.task.pk, assignee_id=self.buyer.pk)

    def test_a_buyer_takes_a_free_task_but_not_someone_elses(self):
        services.take(actor=self.buyer, task_id=self.task.pk)
        self.refresh()
        self.assertEqual(self.task.assigned_to, self.buyer)
        with self.assertRaises(ValidationError):
            services.take(actor=self.buyer2, task_id=self.task.pk)


class DecisionTests(ReviewSetup):
    def test_approving_writes_the_brief_and_waits_for_launch(self):
        services.approve(actor=self.buyer, task_id=self.task.pk)
        self.refresh()
        self.assertEqual(self.task.status, TaskStatus.APPROVED)
        self.assertEqual(self.task.decided_by, self.buyer)
        self.assertEqual(self.task.assigned_to, self.buyer)  # deciding a free task takes it
        self.assertEqual(self.campaign.status, Status.AI_BUILDING)  # ROADMAP Q12
        self.assertTrue(self.task.awaiting_deployment)
        self.assertEqual(self.task.brief.brief_id, f"BRIEF-{self.task.code}")
        with self.assertRaises(ValidationError):
            services.approve(actor=self.buyer, task_id=self.task.pk)  # decided once

    def test_a_task_assigned_to_someone_else_is_refused_but_the_lead_may(self):
        services.assign(actor=self.lead, task_id=self.task.pk, assignee_id=self.buyer.pk)
        with self.assertRaises(PermissionDenied):
            services.approve(actor=self.buyer2, task_id=self.task.pk)
        with self.assertRaises(PermissionDenied):
            services.reject(actor=self.buyer2, task_id=self.task.pk, reasons="x")
        services.approve(actor=self.lead, task_id=self.task.pk)

    def test_deciding_needs_its_capability(self):
        for actor in (self.content, self.finance, self.owner, self.member):
            for action, extra in ((services.approve, {}), (services.reject, {"reasons": "x"}),
                                  (services.take, {})):
                with self.subTest(actor=actor.username, action=action.__name__), \
                        self.assertRaises(PermissionDenied):
                    action(actor=actor, task_id=self.task.pk, **extra)

    def test_rejecting_sends_the_reasons_to_the_office_only(self):
        with self.assertRaises(ValidationError):
            services.reject(actor=self.buyer, task_id=self.task.pk, reasons="   ")
        with self.captureOnCommitCallbacks(execute=True):
            services.reject(actor=self.buyer, task_id=self.task.pk, reasons="Use the 5% offer image.")
        self.refresh()
        self.assertEqual(self.task.status, TaskStatus.REJECTED)
        self.assertEqual(self.campaign.status, Status.NEEDS_CHANGES)
        self.assertEqual(self.campaign.change_reasons, "Use the 5% offer image.")
        self.assertFalse(hasattr(self.task, "brief") and Brief.objects.filter(task=self.task).exists())

        # Emailed to who can run the office's campaigns, nobody else.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner@nour.test"])
        self.assertIn(self.campaign.code, mail.outbox[0].subject)
        self.assertIn("Use the 5% offer image.", mail.outbox[0].body)
        self.assertIn(f"{settings.SITE_BASE_URL}/en/portal/campaigns/{self.campaign.pk}/", mail.outbox[0].body)

        # The broker sees them on the campaign page; another office can't open it.
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(f"/en/portal/campaigns/{self.campaign.pk}/"), "Use the 5% offer image.")
        self.client.force_login(self.rival)
        response = self.client.get(f"/en/portal/campaigns/{self.campaign.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("5% offer image", response.content.decode())

    def test_the_broker_can_edit_and_send_again(self):
        services.reject(actor=self.buyer, task_id=self.task.pk, reasons="Change the audience.")
        self.campaign.refresh_from_db()
        self.assertTrue(self.campaign.is_editable)


class DeploymentTests(ReviewSetup):
    def test_recording_the_ad_set_makes_the_campaign_live(self):
        with self.assertRaises(ValidationError):  # not approved yet
            services.mark_deployed(actor=self.buyer, task_id=self.task.pk, network_id="120210000000001",
                                   ad_account="act_1234567890")
        services.approve(actor=self.buyer, task_id=self.task.pk)
        with self.assertRaises(ValidationError) as caught:
            services.mark_deployed(actor=self.buyer, task_id=self.task.pk, network_id="12ab", ad_account="x")
        self.assertEqual(set(caught.exception.error_dict), {"network_id", "ad_account"})
        with self.assertRaises(PermissionDenied):
            services.mark_deployed(actor=self.buyer2, task_id=self.task.pk, network_id="120210000000001",
                                   ad_account="act_1234567890")

        services.mark_deployed(actor=self.buyer, task_id=self.task.pk, network_id="120210000000001",
                               ad_account="act_1234567890")
        self.refresh()
        self.assertEqual(self.campaign.status, Status.LIVE)
        self.assertEqual(self.campaign.external_id, "120210000000001")
        self.assertEqual(self.campaign.platform_data["ad_account_id"], "act_1234567890")
        self.assertEqual(self.task.deployed_by, self.buyer)
        self.assertFalse(self.task.awaiting_deployment)
        with self.assertRaises(ValidationError):  # once
            services.mark_deployed(actor=self.buyer, task_id=self.task.pk, network_id="120210000000002",
                                   ad_account="act_1234567890")


def _srs_example_brief():
    text = (Path(settings.BASE_DIR) / "docs" / "SRS.md").read_text()
    block = text.split("Example brief:", 1)[1]
    return json.loads(re.search(r"```json\n(.*?)\n```", block, re.S).group(1))


class BriefTests(ReviewSetup):
    def approve(self, task=None):
        task = task or self.task
        services.approve(actor=self.buyer, task_id=task.pk)
        return Brief.objects.get(task=task).content

    def assert_has_keys(self, example, brief, path="brief"):
        for key, value in example.items():
            with self.subTest(key=f"{path}.{key}"):
                self.assertIn(key, brief)
            if isinstance(value, dict) and isinstance(brief.get(key), dict):
                self.assert_has_keys(value, brief[key], f"{path}.{key}")
            if isinstance(value, list) and value and isinstance(value[0], dict):
                self.assertTrue(brief[key], f"{path}.{key} is empty")
                self.assert_has_keys(value[0], brief[key][0], f"{path}.{key}[0]")

    def test_every_key_of_the_srs_example(self):
        brief = self.approve()
        self.assert_has_keys(_srs_example_brief(), brief)

    def test_values(self):
        brief = self.approve()
        code = self.campaign.code
        self.assertEqual(brief["brief_id"], f"BRIEF-{self.task.code}")
        self.assertEqual(brief["leadshub_campaign_id"], code)
        self.assertEqual(brief["client"], {"name": "Nour Realty", "wallet_balance_usd": 3180, "tier": "Gold"})
        campaign = brief["campaign"]
        self.assertEqual(
            (campaign["type"], campaign["developer"], campaign["project"], campaign["area"]),
            ("specific", "Orascom", "O West", "6th of October"),
        )
        self.assertEqual(campaign["destination"], "platform_dark_page")
        self.assertIsNone(campaign["identity"])
        self.assertEqual(campaign["budget"], {"mode": "daily", "amount_usd": 30})
        self.assertEqual(brief["meta"]["set_adset_name_to"], code)
        self.assertEqual(brief["meta"]["special_ad_categories"], ["HOUSING"])
        self.assertEqual(brief["audience"]["meta_interests"], ["Real estate", "Compounds"])
        self.assertTrue(brief["audience"]["no_age_gender_limits"])
        self.assertNotIn("age", json.dumps(brief["audience"]).replace("no_age_gender_limits", ""))
        self.assertEqual(brief["creatives"][0]["id"], self.creative.code)
        self.assertEqual(brief["creatives"][0]["open_path"], self.creative.file.url)
        self.assertEqual(brief["routing"]["mode"], "weighted_by_budget")
        self.assertRegex(brief["generated_at"], r"\+02:00$|\+03:00$")  # Cairo time

    def test_own_instagram_and_google(self):
        account = ConnectedAccount.objects.create(office=self.office, platform=Platform.INSTAGRAM,
                                                  handle="@nour.realty")
        own = ReviewTask.objects.get(campaign=self.sent_campaign(
            destination=Destination.OWN_ACCOUNT, connected_account=account))
        brief = self.approve(own)
        self.assertEqual(brief["campaign"]["destination"], "client_instagram")
        self.assertEqual(brief["campaign"]["identity"], "@nour.realty")
        self.assertEqual(brief["routing"]["mode"], "direct")

        google = ReviewTask.objects.get(campaign=self.sent_campaign(
            channel=Channel.GOOGLE, destination=Destination.LANDING_PAGE))
        brief = self.approve(google)
        self.assertNotIn("meta", brief)
        self.assertEqual(brief["google"]["routing_key"], "campaign_id")
        self.assertEqual(brief["campaign"]["destination"], "platform_landing_page")

    def test_a_brief_never_changes(self):
        self.approve()
        brief = Brief.objects.get(task=self.task)
        brief.content = {}
        with self.assertRaises(ImmutableBrief):
            brief.save()
        with self.assertRaises(ImmutableBrief):
            brief.delete()

    def test_build_is_pure(self):
        self.assertEqual(briefs.build(self.task, timezone.now())["brief_id"], f"BRIEF-{self.task.code}")
        self.assertFalse(Brief.objects.exists())


class CreativeFilesTests(ReviewSetup):
    def test_reviewers_open_creative_files(self):
        from creatives.services import may_open_file

        name = self.creative.file.name
        self.assertTrue(may_open_file(self.buyer, name))
        self.assertTrue(may_open_file(self.lead, name))
        self.assertFalse(may_open_file(self.finance, name))


class PageTests(ReviewSetup):
    def get(self, user, url, status=200):
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status, url)
        return response.content.decode()

    def test_who_opens_the_queue(self):
        url = "/en/back-office/queue/"
        for user in (self.buyer, self.lead, self.admin):
            with self.subTest(user=user.username):
                self.assertIn(self.task.code, self.get(user, url))
        for user in (self.content, self.finance, self.owner):
            with self.subTest(user=user.username):
                self.get(user, url, status=403)
                self.get(user, f"/en/back-office/queue/{self.task.pk}/", status=403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_queue_filters(self):
        other = ReviewTask.objects.get(campaign=self.sent_campaign())
        services.assign(actor=self.lead, task_id=other.pk, assignee_id=self.buyer.pk)
        mine = self.get(self.buyer, "/en/back-office/queue/?show=mine")
        listed = mine.split('id="open-heading"', 1)[1]
        self.assertIn(other.code, listed)
        self.assertNotIn(self.task.code, listed)
        free = self.get(self.buyer, "/en/back-office/queue/?show=unassigned").split('id="open-heading"', 1)[1]
        self.assertIn(self.task.code, free)
        self.assertNotIn(other.code, free)
        self.assertIn(f'data-countdown="{self.task.due_iso}"', mine + free)

        client = live(_queue_view(), self.buyer, "/en/back-office/queue/")
        client.send_event("set_show", value="near_breach")
        client.assert_state(show="near_breach")
        client.send_event("set_show", value="nonsense")
        client.assert_state(show="")

    def test_task_page_shows_internal_targeting(self):
        html = self.get(self.buyer, f"/en/back-office/queue/{self.task.pk}/")
        self.assertIn("Compounds", html)
        self.assertIn("closed_won_1pct", html)
        self.assertIn('dj-click="approve"', html)
        self.assertIn('dj-submit="reject"', html)
        self.assertNotIn('dj-change="assign"', html)  # buyers don't assign
        self.assertIn('dj-change="assign"', self.get(self.lead, f"/en/back-office/queue/{self.task.pk}/"))
        # A broker never sees the internal targeting.
        broker = self.get(self.owner, f"/en/portal/campaigns/{self.campaign.pk}/")
        self.assertNotIn("closed_won_1pct", broker)

    def test_a_task_assigned_elsewhere_offers_no_decision(self):
        services.assign(actor=self.lead, task_id=self.task.pk, assignee_id=self.buyer.pk)
        html = self.get(self.buyer2, f"/en/back-office/queue/{self.task.pk}/")
        self.assertNotIn('dj-click="approve"', html)
        self.assertIn("assigned to someone else", html)

    def test_handlers_follow_the_rules(self):
        from .views import ReviewTaskView

        url = f"/en/back-office/queue/{self.task.pk}/"
        client = live(ReviewTaskView, self.buyer, url, pk=self.task.pk)
        # Assigning needs assign_campaign_tasks: djust refuses the event.
        self.assertEqual(client.send_event("assign", value=str(self.buyer2.pk))["error"], "Permission denied")

        client.send_event("reject", reasons="")
        self.assertEqual(client.get_state()["error"], "Tell the broker what to change.")
        client.send_event("approve")
        self.refresh()
        self.assertEqual(self.task.status, TaskStatus.APPROVED)

        client.send_event("mark_deployed", network_id="abc", ad_account="act_1")
        self.assertIn("network_id", client.get_state()["field_errors"])
        client.send_event("mark_deployed", network_id="120210000000001", ad_account="act_1234567890")
        self.refresh()
        self.assertEqual(self.campaign.status, Status.LIVE)

        lead = live(ReviewTaskView, self.lead, url, pk=self.sent_campaign_task().pk)
        lead.send_event("assign", value=str(self.buyer2.pk))
        self.assertEqual(lead.view_instance._task.assigned_to, self.buyer2)

    def sent_campaign_task(self):
        return ReviewTask.objects.get(campaign=self.sent_campaign())

    def test_brief_download(self):
        url = f"/en/back-office/queue/{self.task.pk}/brief.json"
        self.get(self.buyer, url, status=404)  # no brief before approval
        services.approve(actor=self.buyer, task_id=self.task.pk)
        self.client.force_login(self.buyer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'filename="BRIEF-{self.task.code}.json"', response["Content-Disposition"])
        self.assertEqual(response.json()["leadshub_campaign_id"], self.campaign.code)
        # In the SRS example's order, though PostgreSQL re-sorts stored keys.
        self.assertEqual(list(response.json())[:4], ["brief_id", "generated_at", "leadshub_campaign_id", "client"])
        self.get(self.owner, url, status=403)
        self.get(self.content, url, status=403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_arabic_pages(self):
        html = self.get(self.lead, "/ar/back-office/queue/")
        self.assertIn('dir="rtl"', html)
        self.assertIn("قائمة المراجعة", html)
        self.get(self.lead, f"/ar/back-office/queue/{self.task.pk}/")


def _queue_view():
    from .views import ReviewQueueView

    return ReviewQueueView


class DemoTests(ReviewSetup):
    def test_seed_gives_each_status_its_history(self):
        from .demo import seed_reviews

        live_campaign = self.ready_campaign()
        live_campaign.submit()
        live_campaign.go_live()
        seed_reviews(Campaign.objects.all(), self.buyer)
        seed_reviews(Campaign.objects.all(), self.buyer)  # once
        task = ReviewTask.objects.get(campaign=live_campaign)
        self.assertEqual(task.status, TaskStatus.APPROVED)
        self.assertIsNotNone(task.deployed_at)
        self.assertTrue(Brief.objects.filter(task=task).exists())
        self.assertEqual(ReviewTask.objects.filter(campaign=self.campaign).count(), 1)


@override_settings(SITE_BASE_URL="https://leadshub.example")
class SiteUrlTests(ReviewSetup):
    def test_email_links_use_the_site_address(self):
        with self.captureOnCommitCallbacks(execute=True):
            services.escalate_near_breach(now=self.task.due_at)
        self.assertIn("https://leadshub.example/en/back-office/queue/", mail.outbox[0].body)


class ReviewPagesOverTheSocketTests(TemporaryUploadsMixin, TransactionTestCase):
    """The task page through djust's real WebSocket consumer (mount auth, URL id, events)."""

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
        self.task = ReviewTask.objects.get(campaign=campaign)

    def socket_for(self, user):
        from channels.testing import WebsocketCommunicator
        from django.test import Client

        from leadshub.asgi import application

        web = Client()
        web.force_login(user)
        cookie = f"sessionid={web.cookies['sessionid'].value}".encode()
        headers = [(b"cookie", cookie), (b"origin", b"http://testserver"), (b"host", b"testserver")]
        return WebsocketCommunicator(application, "/ws/live/", headers=headers)

    async def open(self, user, url):
        from asgiref.sync import sync_to_async

        socket = await sync_to_async(self.socket_for)(user)
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        self.assertEqual((await socket.receive_json_from())["type"], "connect")
        await socket.send_json_to({"type": "mount", "view": "reviews.views.ReviewTaskView", "url": url})
        return socket, await socket.receive_json_from(timeout=5)

    async def test_buyer_approves_on_an_arabic_page(self):
        socket, reply = await self.open(self.buyer, f"/ar/back-office/queue/{self.task.pk}/")
        self.assertEqual(reply["type"], "mount")
        self.assertIn(self.task.code, reply["html"])
        self.assertIn("وافق واكتب الموجز", reply["html"])
        await socket.send_json_to({"type": "event", "event": "approve", "params": {}})
        await socket.receive_json_from(timeout=5)
        task = await ReviewTask.objects.aget(pk=self.task.pk)
        self.assertEqual(task.status, TaskStatus.APPROVED)
        await socket.disconnect()

    async def test_a_role_without_review_is_refused_at_mount(self):
        socket, reply = await self.open(self.content, f"/en/back-office/queue/{self.task.pk}/")
        self.assertEqual(reply, {"type": "error", "error": "Permission denied"})
        await socket.disconnect()
