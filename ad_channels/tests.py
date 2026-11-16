"""US-META-01: Meta Business Managers and ad accounts (FR-6.10, FR-6.11).

Meta is never called: ``meta_ads.sync.client_for`` is replaced by ``FakeMeta``.
"""

import json
from urllib.parse import parse_qs, urlparse
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.admin.models import LogEntry
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from facebook_business.exceptions import FacebookRequestError

from accounts.roles import Role
from accounts.test_utils import make_user

from .meta_ads.client import OWNED, SHARED, AdAccountInfo, MetaError, _TranslateErrors
from .models import MetaAdAccount, MetaBusinessManager
from .meta_ads import client as meta_client
from .meta_ads.sync import ConnectError, SyncError, connect_business, recheck_token, sync_business_manager

TOKEN = "EAAB-secret-system-user-token-1234"
NEW_TOKEN = "EAAB-another-secret-token-9876"
BM_URL = "/en/admin/ad_channels/metabusinessmanager/"
ACCOUNT_URL = "/en/admin/ad_channels/metaadaccount/"
REJECTED = MetaError("Error validating access token (Meta error 190)", token_rejected=True)


def account(id_="act_1", *, name="Account 1", status=1, ownership=OWNED):
    return AdAccountInfo(
        id=id_, name=name, status=status, currency="EGP", timezone_name="Africa/Cairo", ownership=ownership
    )


class FakeMeta:
    """Stands in for ``meta_ads.sync.client_for`` and the ``MetaClient`` it returns."""

    def __init__(self):
        self.accounts = {}  # business ID -> [AdAccountInfo]
        self.check_error = None
        self.list_error = None
        self.tokens = []
        self.business = ("111", "BM One")
        self.business_error = None
        self.exchanged = []
        self.exchange_error = None

    def __call__(self, token):
        self.tokens.append(token)
        return self

    def check_access(self, business_id):
        if self.check_error:
            raise self.check_error

    def business_for_token(self):
        if self.business_error:
            raise self.business_error
        return self.business

    def exchange_code(self, code, redirect_uri):
        """Stands in for ``meta_ads.sync.exchange_code``."""
        self.exchanged.append((code, redirect_uri))
        if self.exchange_error:
            raise self.exchange_error
        return TOKEN

    def list_ad_accounts(self, business_id):
        if self.list_error:
            raise self.list_error
        return list(self.accounts.get(business_id, []))


def make_bm(name="BM One", business_id="111", *, token=TOKEN, status=MetaBusinessManager.TokenStatus.VALID, **fields):
    bm = MetaBusinessManager(name=name, meta_business_id=business_id, token_status=status, **fields)
    bm.set_token(token)
    bm.save()
    return bm


@override_settings(META_TOKEN_KEYS=[Fernet.generate_key().decode()])
class MetaTestCase(TestCase):
    def setUp(self):
        self.meta = FakeMeta()
        for target, fake in (
            ("ad_channels.meta_ads.sync.client_for", self.meta),
            ("ad_channels.meta_ads.sync.exchange_code", self.meta.exchange_code),
        ):
            patcher = mock.patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)


class TokenStorageTests(MetaTestCase):
    """Criteria 4, 5."""

    def test_token_is_encrypted_at_rest(self):
        bm = make_bm()
        with connection.cursor() as cursor:
            cursor.execute("SELECT token_ciphertext FROM ad_channels_metabusinessmanager WHERE id = %s", [bm.pk])
            stored = cursor.fetchone()[0]
        self.assertNotIn(TOKEN, stored)
        self.assertEqual(MetaBusinessManager.objects.get(pk=bm.pk).get_token(), TOKEN)

    def test_only_a_masked_value_is_shown(self):
        self.assertEqual(make_bm().masked_token, "••••1234")

    def test_old_keys_still_decrypt_after_rotation(self):
        bm = make_bm()
        with override_settings(META_TOKEN_KEYS=[Fernet.generate_key().decode(), *self._keys()]):
            self.assertEqual(MetaBusinessManager.objects.get(pk=bm.pk).get_token(), TOKEN)

    def _keys(self):
        from django.conf import settings

        return settings.META_TOKEN_KEYS


@override_settings(META_APP_ID="1", META_LOGIN_CONFIG_ID="cfg-9")
class BusinessManagerPageTests(MetaTestCase):
    """Business Managers come only from Facebook Login; nobody types a token (US-META-01)."""

    def setUp(self):
        super().setUp()
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        self.bm = make_bm()
        self.change_url = f"{BM_URL}{self.bm.pk}/change/"

    def test_add_url_redirects_to_connect(self):
        response = self.client.get(f"{BM_URL}add/")
        self.assertRedirects(response, f"{BM_URL}connect/", fetch_redirect_response=False)

    def test_changelist_shows_connect_button_not_add(self):
        page = self.client.get(BM_URL)
        self.assertContains(page, "Connect with Facebook")
        self.assertContains(page, f"{BM_URL}connect_with_facebook/")
        self.assertNotContains(page, f'href="{BM_URL}add/"')
        response = self.client.get(f"{BM_URL}connect_with_facebook/")
        self.assertRedirects(response, f"{BM_URL}connect/", fetch_redirect_response=False)

    def test_change_page_shows_reconnect_and_no_token_section(self):
        page = self.client.get(self.change_url)
        self.assertContains(page, "Reconnect with Facebook")
        self.assertNotContains(page, 'name="token"')
        self.assertNotContains(page, "••••1234")
        self.assertNotContains(page, "Token problem")
        self.assertNotContains(page, "Token last checked")

    def test_no_save_and_add_another_button(self):
        self.assertNotContains(self.client.get(self.change_url), 'name="_addanother"')

    def test_reconnect_button_starts_the_login_for_this_bm(self):
        page = self.client.get(self.change_url)
        self.assertContains(page, f"{BM_URL}{self.bm.pk}/reconnect_with_facebook/")
        response = self.client.get(f"{BM_URL}{self.bm.pk}/reconnect_with_facebook/")
        self.assertRedirects(response, f"{BM_URL}connect/?bm={self.bm.pk}", fetch_redirect_response=False)

    def test_business_id_is_read_only(self):
        self.client.post(self.change_url, {"name": "BM One", "meta_business_id": "222"})
        self.bm.refresh_from_db()
        self.assertEqual(self.bm.meta_business_id, "111")

    def test_name_edit_saves_without_calling_meta(self):
        self.assertEqual(self.client.post(self.change_url, {"name": "Renamed"}).status_code, 302)
        self.bm.refresh_from_db()
        self.assertEqual((self.bm.name, self.bm.get_token()), ("Renamed", TOKEN))
        self.assertEqual(self.meta.tokens, [])

    def test_media_buying_lead_sees_no_buttons(self):
        self.client.force_login(make_user("lead", Role.MEDIA_BUYING_LEAD))
        self.assertNotContains(self.client.get(BM_URL), "Connect with Facebook")
        self.assertNotContains(self.client.get(self.change_url), "Reconnect with Facebook")
        self.assertEqual(self.client.get(f"{BM_URL}{self.bm.pk}/reconnect_with_facebook/").status_code, 403)

    @override_settings(META_LOGIN_CONFIG_ID=None)
    def test_buttons_shown_and_explain_when_not_configured(self):
        self.assertContains(self.client.get(BM_URL), "Connect with Facebook")
        self.assertContains(self.client.get(self.change_url), "Reconnect with Facebook")
        response = self.client.get(f"{BM_URL}connect_with_facebook/", follow=True)
        self.assertContains(response, "META_LOGIN_CONFIG_ID")
        self.assertNotIn("facebook.com", response.redirect_chain[-1][0])

    def test_token_never_appears_in_pages_or_history(self):
        for url in (BM_URL, self.change_url, f"{self.change_url}../history/"):
            with self.subTest(url=url):
                response = self.client.get(url, follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, TOKEN)
                self.assertNotContains(response, self.bm.token_ciphertext)
        for entry in LogEntry.objects.all():
            self.assertNotIn(TOKEN, entry.change_message)


class SyncTests(MetaTestCase):
    """Criteria 8 to 13."""

    def setUp(self):
        super().setUp()
        self.bm = make_bm()
        self.meta.accounts["111"] = [account("act_1"), account("act_2", name="Account 2")]

    def test_new_accounts_are_saved_off_for_launch(self):
        result = sync_business_manager(self.bm)
        self.assertEqual((result.created, result.updated, result.removed), (2, 0, 0))
        self.assertFalse(MetaAdAccount.objects.filter(enabled_for_launch=True).exists())
        self.bm.refresh_from_db()
        self.assertIsNotNone(self.bm.last_synced_at)

    def test_running_twice_creates_no_duplicates(self):
        sync_business_manager(self.bm)
        result = sync_business_manager(self.bm)
        self.assertEqual(MetaAdAccount.objects.count(), 2)
        self.assertEqual((result.created, result.updated), (0, 2))

    def test_only_meta_owned_fields_change(self):
        sync_business_manager(self.bm)
        MetaAdAccount.objects.filter(meta_account_id="act_1").update(enabled_for_launch=True)
        self.meta.accounts["111"] = [account("act_1", name="Renamed", status=2), account("act_2")]
        sync_business_manager(self.bm)
        acc = MetaAdAccount.objects.get(meta_account_id="act_1")
        self.assertEqual((acc.name, acc.account_status), ("Renamed", 2))
        self.assertTrue(acc.enabled_for_launch)

    def test_account_missing_from_meta_is_marked_removed_not_deleted(self):
        sync_business_manager(self.bm)
        MetaAdAccount.objects.update(enabled_for_launch=True)
        self.meta.accounts["111"] = [account("act_1")]
        self.assertEqual(sync_business_manager(self.bm).removed, 1)
        gone = MetaAdAccount.objects.get(meta_account_id="act_2")
        self.assertTrue(gone.is_removed)
        self.assertTrue(gone.enabled_for_launch)  # Admin's choice is kept...
        self.assertNotIn(gone, MetaAdAccount.objects.available_for_launch())  # ...but it can't launch.

    def test_account_that_comes_back_is_restored(self):
        sync_business_manager(self.bm)
        self.meta.accounts["111"] = [account("act_1")]
        sync_business_manager(self.bm)
        self.meta.accounts["111"] = [account("act_1"), account("act_2")]
        sync_business_manager(self.bm)
        self.assertFalse(MetaAdAccount.objects.get(meta_account_id="act_2").is_removed)

    def test_failed_sync_records_the_error_and_leaves_accounts_unchanged(self):
        sync_business_manager(self.bm)
        before = list(MetaAdAccount.objects.values())
        self.meta.list_error = MetaError("Couldn't reach Meta; try again later.")
        with self.assertRaises(SyncError):
            sync_business_manager(self.bm)
        self.assertEqual(list(MetaAdAccount.objects.values()), before)
        self.bm.refresh_from_db()
        self.assertEqual(self.bm.last_sync_error, "Couldn't reach Meta; try again later.")
        self.assertIsNotNone(self.bm.last_sync_failed_at)
        self.assertEqual(self.bm.token_status, MetaBusinessManager.TokenStatus.VALID)

    def test_sync_rejected_for_the_token_marks_it_invalid(self):
        self.meta.list_error = REJECTED
        with self.assertRaises(SyncError):
            sync_business_manager(self.bm)
        self.bm.refresh_from_db()
        self.assertEqual(self.bm.token_status, MetaBusinessManager.TokenStatus.INVALID)

    def test_deactivated_business_manager_is_not_synced(self):
        self.bm.is_active = False
        self.bm.save()
        with self.assertRaises(SyncError):
            sync_business_manager(self.bm)
        self.assertEqual(self.meta.tokens, [])

    def test_shared_account_moves_to_the_business_that_owns_it(self):
        other = make_bm("BM Two", "222")
        self.meta.accounts["222"] = [account("act_1", ownership=SHARED)]
        sync_business_manager(other)
        sync_business_manager(self.bm)  # Owns act_1.
        self.assertEqual(MetaAdAccount.objects.get(meta_account_id="act_1").business_manager, self.bm)
        sync_business_manager(other)  # Still only shared there: stays with the owner.
        acc = MetaAdAccount.objects.get(meta_account_id="act_1")
        self.assertEqual((acc.business_manager, acc.ownership), (self.bm, OWNED))
        self.assertEqual(MetaAdAccount.objects.count(), 2)


class LaunchAvailabilityTests(MetaTestCase):
    """Criteria 14, 15, 17, 18."""

    def setUp(self):
        super().setUp()
        self.bm = make_bm()
        self.acc = MetaAdAccount.objects.create(
            business_manager=self.bm,
            meta_account_id="act_1",
            name="ACC-01",
            currency="EGP",
            timezone_name="Africa/Cairo",
            account_status=MetaAdAccount.Status.ACTIVE,
            ownership=OWNED,
            last_synced_at=timezone.now(),
        )

    def enable(self):
        self.acc.enabled_for_launch = True
        self.acc.full_clean()
        self.acc.save()

    def test_healthy_account_can_be_enabled_and_is_available(self):
        self.enable()
        self.assertIn(self.acc, MetaAdAccount.objects.available_for_launch())

    def test_inactive_account_cannot_be_enabled(self):
        self.acc.account_status = MetaAdAccount.Status.DISABLED
        self.acc.save()
        with self.assertRaisesMessage(ValidationError, "Meta status is not active"):
            self.enable()

    def test_account_cannot_be_enabled_while_the_token_is_not_valid(self):
        for status in (MetaBusinessManager.TokenStatus.INVALID, MetaBusinessManager.TokenStatus.UNCHECKED):
            with self.subTest(status=status):
                MetaBusinessManager.objects.filter(pk=self.bm.pk).update(token_status=status)
                self.acc.refresh_from_db()
                with self.assertRaisesMessage(ValidationError, "token is not valid"):
                    self.enable()

    def test_enabled_account_leaves_launch_when_it_goes_bad(self):
        self.enable()
        cases = {
            "token invalid": lambda: MetaBusinessManager.objects.update(token_status=MetaBusinessManager.TokenStatus.INVALID),
            "deactivated": lambda: MetaBusinessManager.objects.update(is_active=False),
            "Meta disabled it": lambda: MetaAdAccount.objects.update(account_status=MetaAdAccount.Status.DISABLED),
        }
        for label, break_it in cases.items():
            with self.subTest(label), self._restored():
                break_it()
                self.assertFalse(MetaAdAccount.objects.available_for_launch().exists())
                self.assertTrue(MetaAdAccount.objects.get().launch_blockers())

    def _restored(self):
        """Each case starts from the healthy account and puts it back afterwards."""
        test = self

        class _Restore:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                MetaBusinessManager.objects.update(token_status=MetaBusinessManager.TokenStatus.VALID, is_active=True)
                MetaAdAccount.objects.update(account_status=MetaAdAccount.Status.ACTIVE)
                test.assertTrue(MetaAdAccount.objects.available_for_launch().exists())

        return _Restore()

    def test_enable_action_skips_blocked_accounts_with_a_reason(self):
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        MetaAdAccount.objects.update(account_status=MetaAdAccount.Status.DISABLED)
        response = self.client.post(
            ACCOUNT_URL, {"action": "enable_for_launch", "_selected_action": [self.acc.pk]}, follow=True
        )
        self.assertContains(response, "Meta status is not active")
        self.assertFalse(MetaAdAccount.objects.get().enabled_for_launch)

    def test_enable_action_enables_healthy_accounts(self):
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        self.client.post(ACCOUNT_URL, {"action": "enable_for_launch", "_selected_action": [self.acc.pk]})
        self.assertTrue(MetaAdAccount.objects.get().enabled_for_launch)

    def test_deactivate_action_keeps_the_records(self):
        self.enable()
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        self.client.post(BM_URL, {"action": "deactivate", "_selected_action": [self.bm.pk]})
        self.assertFalse(MetaBusinessManager.objects.get().is_active)
        self.assertTrue(MetaAdAccount.objects.get().enabled_for_launch)
        self.assertFalse(MetaAdAccount.objects.available_for_launch().exists())



class DeleteTests(MetaTestCase):
    """Create, list, update and delete: the delete part."""

    def setUp(self):
        super().setUp()
        self.bm = make_bm()
        self.meta.accounts["111"] = [account("act_1"), account("act_2")]
        sync_business_manager(self.bm)

    def test_platform_admin_deletes_an_ad_account(self):
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        acc = MetaAdAccount.objects.get(meta_account_id="act_1")
        response = self.client.post(f"{ACCOUNT_URL}{acc.pk}/delete/", {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(MetaAdAccount.objects.values_list("meta_account_id", flat=True)), ["act_2"])

    def test_deleting_a_business_manager_deletes_its_ad_accounts(self):
        other = make_bm("BM Two", "222")
        MetaAdAccount.objects.create(
            business_manager=other, meta_account_id="act_9", name="Other", currency="EGP", timezone_name="Africa/Cairo"
        )
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        page = self.client.get(f"{BM_URL}{self.bm.pk}/delete/")
        self.assertContains(page, "act_1")  # The confirmation lists what goes with it.
        self.assertNotContains(page, TOKEN)
        self.client.post(f"{BM_URL}{self.bm.pk}/delete/", {"post": "yes"})
        self.assertEqual(list(MetaBusinessManager.objects.all()), [other])
        self.assertEqual(list(MetaAdAccount.objects.values_list("meta_account_id", flat=True)), ["act_9"])

    def test_bulk_delete_from_the_list(self):
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        ids = list(MetaAdAccount.objects.values_list("pk", flat=True))
        self.client.post(ACCOUNT_URL, {"action": "delete_selected", "_selected_action": ids, "post": "yes"})
        self.assertFalse(MetaAdAccount.objects.exists())

    def test_media_buying_lead_cannot_delete(self):
        self.client.force_login(make_user("lead", Role.MEDIA_BUYING_LEAD))
        acc = MetaAdAccount.objects.first()
        self.assertEqual(self.client.post(f"{BM_URL}{self.bm.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertEqual(self.client.post(f"{ACCOUNT_URL}{acc.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.client.post(ACCOUNT_URL, {"action": "delete_selected", "_selected_action": [acc.pk], "post": "yes"})
        self.assertEqual((MetaBusinessManager.objects.count(), MetaAdAccount.objects.count()), (1, 2))


class RecheckTokenTests(MetaTestCase):
    """Criterion 17: the Recheck token action flags a token Meta no longer accepts."""

    def setUp(self):
        super().setUp()
        self.bm = make_bm()

    def test_revoked_token_is_flagged(self):
        self.meta.check_error = REJECTED
        recheck_token(self.bm)
        self.bm.refresh_from_db()
        self.assertEqual(self.bm.token_status, MetaBusinessManager.TokenStatus.INVALID)
        self.assertIn("Meta error 190", self.bm.token_error)

    def test_meta_outage_keeps_the_current_status(self):
        self.meta.check_error = MetaError("Couldn't reach Meta; try again later.")
        recheck_token(self.bm)
        self.bm.refresh_from_db()
        self.assertEqual(self.bm.token_status, MetaBusinessManager.TokenStatus.VALID)
        self.assertEqual(self.bm.token_error, "Couldn't reach Meta; try again later.")

    def test_recheck_action_from_the_list(self):
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        self.meta.check_error = REJECTED
        response = self.client.post(BM_URL, {"action": "recheck_tokens", "_selected_action": [self.bm.pk]}, follow=True)
        self.assertContains(response, "token is Invalid")


class ManualAdAccountTests(MetaTestCase):
    """The Platform Admin adds an ad account by hand; Sync later picks it up."""

    def setUp(self):
        super().setUp()
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        self.bm = make_bm()

    def add(self, **fields):
        data = {
            "business_manager": self.bm.pk,
            "meta_account_id": "act_555",
            "name": "ACC-05",
            "account_status": MetaAdAccount.Status.ACTIVE,
            "ownership": OWNED,
            "currency": "egp",
            "timezone_name": "Africa/Cairo",
            **fields,
        }
        return self.client.post(f"{ACCOUNT_URL}add/", data)

    def test_added_by_hand_without_calling_meta(self):
        self.assertEqual(self.add().status_code, 302)
        acc = MetaAdAccount.objects.get()
        self.assertEqual((acc.meta_account_id, acc.currency), ("act_555", "EGP"))
        self.assertFalse(acc.enabled_for_launch)
        self.assertIsNone(acc.last_synced_at)
        self.assertEqual(self.meta.tokens, [])

    def test_bare_number_gets_the_act_prefix(self):
        self.add(meta_account_id="555")
        self.assertEqual(MetaAdAccount.objects.get().meta_account_id, "act_555")

    def test_malformed_id_and_duplicates_are_rejected(self):
        self.assertContains(self.add(meta_account_id="act_abc"), "numeric account ID")
        self.add()
        self.assertEqual(self.add(name="Again").status_code, 200)
        self.assertEqual(MetaAdAccount.objects.count(), 1)

    def test_enabling_on_add_follows_the_same_rule(self):
        MetaBusinessManager.objects.update(token_status=MetaBusinessManager.TokenStatus.INVALID)
        self.assertContains(self.add(enabled_for_launch="on"), "token is not valid")
        self.assertFalse(MetaAdAccount.objects.exists())

    def test_deactivated_business_managers_are_not_offered(self):
        MetaBusinessManager.objects.update(is_active=False)
        self.assertEqual(self.add().status_code, 200)
        self.assertFalse(MetaAdAccount.objects.exists())

    def test_sync_takes_over_a_hand_added_account_without_duplicating_it(self):
        self.add(enabled_for_launch="on")
        self.meta.accounts["111"] = [account("act_555", name="ACC-05 from Meta")]
        result = sync_business_manager(self.bm)
        self.assertEqual((result.created, result.updated), (0, 1))
        acc = MetaAdAccount.objects.get()
        self.assertEqual(acc.name, "ACC-05 from Meta")
        self.assertTrue(acc.enabled_for_launch)
        self.assertIsNotNone(acc.last_synced_at)

    def test_meta_fields_lock_once_synced(self):
        self.add()
        acc = MetaAdAccount.objects.get()
        self.assertContains(self.client.get(f"{ACCOUNT_URL}{acc.pk}/change/"), 'name="name"')
        self.meta.accounts["111"] = [account("act_555")]
        sync_business_manager(self.bm)
        self.assertNotContains(self.client.get(f"{ACCOUNT_URL}{acc.pk}/change/"), 'name="name"')


class AccessTests(MetaTestCase):
    """Criterion 22: Platform Admin edits; Media Buying Lead reads; others see nothing."""

    def setUp(self):
        super().setUp()
        self.bm = make_bm()
        self.meta.accounts["111"] = [account("act_1")]
        sync_business_manager(self.bm)
        self.meta.tokens.clear()

    def test_media_buying_lead_reads_but_cannot_change(self):
        self.client.force_login(make_user("lead", Role.MEDIA_BUYING_LEAD))
        acc = MetaAdAccount.objects.get()
        for url in (BM_URL, ACCOUNT_URL, f"{BM_URL}{self.bm.pk}/change/", f"{ACCOUNT_URL}{acc.pk}/change/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertNotContains(self.client.get(BM_URL), "sync_accounts")
        self.assertEqual(self.client.get(f"{BM_URL}add/").status_code, 403)
        self.assertEqual(self.client.get(f"{ACCOUNT_URL}add/").status_code, 403)
        self.assertEqual(
            self.client.post(f"{BM_URL}{self.bm.pk}/change/", {"name": "x"}).status_code,
            403,
        )
        self.client.post(BM_URL, {"action": "sync_accounts", "_selected_action": [self.bm.pk]})
        self.client.post(BM_URL, {"action": "deactivate", "_selected_action": [self.bm.pk]})
        self.client.post(ACCOUNT_URL, {"action": "enable_for_launch", "_selected_action": [acc.pk]})
        self.assertEqual(self.meta.tokens, [])
        self.assertTrue(MetaBusinessManager.objects.get().is_active)
        self.assertFalse(MetaAdAccount.objects.get().enabled_for_launch)

    def test_other_roles_cannot_open_the_screens(self):
        for role in (Role.MEDIA_BUYER, Role.CONTENT_MANAGER, Role.FINANCE_MANAGER):
            with self.subTest(role=role):
                self.client.force_login(make_user(f"u-{role.name}", role))
                self.assertEqual(self.client.get(BM_URL).status_code, 403)
                self.assertEqual(self.client.get(ACCOUNT_URL).status_code, 403)

    def test_no_sync_button_or_action_for_the_platform_admin(self):
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        self.assertNotContains(self.client.get(f"{BM_URL}{self.bm.pk}/change/"), "save_and_sync")
        page = self.client.get(BM_URL)
        self.assertNotContains(page, "sync_accounts")
        self.assertNotContains(page, "Last sync")
        self.meta.accounts["111"].append(account("act_2"))
        self.client.post(BM_URL, {"action": "sync_accounts", "_selected_action": [self.bm.pk]})
        self.assertEqual(MetaAdAccount.objects.count(), 1)

    def test_no_sync_section_on_the_edit_page(self):
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        page = self.client.get(f"{BM_URL}{self.bm.pk}/change/")
        self.assertNotContains(page, "Last sync error")
        self.assertNotContains(page, "Last sync failure")


class ListViewTests(MetaTestCase):
    """Criteria 20, 21."""

    def setUp(self):
        super().setUp()
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        self.one, self.two = make_bm(), make_bm("BM Two", "222")
        self.meta.accounts["111"] = [account("act_1"), account("act_2", status=2)]
        self.meta.accounts["222"] = [account("act_3")]
        for bm in (self.one, self.two):
            sync_business_manager(bm)
        MetaAdAccount.objects.filter(meta_account_id="act_1").update(enabled_for_launch=True)

    def test_business_manager_list_shows_counts_and_status(self):
        response = self.client.get(BM_URL)
        self.assertContains(response, "1 / 2")
        self.assertContains(response, "Valid")
        self.assertContains(response, "111")

    def test_ad_account_list_filters_by_business_manager_and_status(self):
        response = self.client.get(ACCOUNT_URL, {"business_manager__id__exact": self.two.pk})
        self.assertContains(response, "act_3")
        self.assertNotContains(response, "act_1")
        response = self.client.get(ACCOUNT_URL, {"account_status__exact": 2})
        self.assertContains(response, "act_2")
        self.assertContains(response, "Disabled")  # The label, not the raw Meta number.
        self.assertNotContains(response, "act_3")

    def test_flagged_filter_shows_enabled_accounts_that_cannot_launch(self):
        MetaBusinessManager.objects.filter(pk=self.one.pk).update(token_status=MetaBusinessManager.TokenStatus.INVALID)
        response = self.client.get(ACCOUNT_URL, {"launch": "flagged"})
        self.assertContains(response, "act_1")
        self.assertContains(response, "token is not valid")


class MetaErrorTests(TestCase):
    """Criterion 7: Meta's errors are stored and shown without the token."""

    def sdk_error(self, message, code):
        body = json.dumps({"error": {"message": message, "code": code, "type": "OAuthException"}})
        return FacebookRequestError("boom", {"method": "GET", "path": "/me"}, 400, {}, body)

    def test_token_is_scrubbed_and_auth_errors_are_flagged(self):
        with self.assertRaises(MetaError) as caught, _TranslateErrors(TOKEN):
            raise self.sdk_error(f"Invalid token {TOKEN}", 190)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIn("Meta error 190", str(caught.exception))
        self.assertTrue(caught.exception.token_rejected)
        self.assertIsNone(caught.exception.__cause__)


class ConnectBusinessTests(MetaTestCase):
    """Facebook Login for Business: a login code becomes a Business Manager (US-META-01, FR-6.10)."""

    def test_creates_bm_with_encrypted_valid_token(self):
        bm, created = connect_business("the-code", "https://x/cb/")
        self.assertTrue(created)
        self.assertEqual((bm.meta_business_id, bm.name), ("111", "BM One"))
        self.assertEqual(bm.get_token(), TOKEN)
        self.assertNotIn(TOKEN, bm.token_ciphertext)
        self.assertEqual(bm.token_status, MetaBusinessManager.TokenStatus.VALID)
        self.assertIsNotNone(bm.token_checked_at)
        self.assertEqual(self.meta.exchanged, [("the-code", "https://x/cb/")])

    def test_existing_business_updates_token_no_new_row(self):
        old = make_bm(name="Kept name", token=NEW_TOKEN, status=MetaBusinessManager.TokenStatus.INVALID, token_error="old")
        bm, created = connect_business("c", "u")
        self.assertFalse(created)
        self.assertEqual(MetaBusinessManager.objects.count(), 1)
        old.refresh_from_db()
        self.assertEqual((old.name, old.get_token(), old.token_status, old.token_error), ("Kept name", TOKEN, "valid", ""))

    def test_connect_existing_inactive_keeps_inactive(self):
        make_bm(is_active=False)
        bm, _ = connect_business("c", "u")
        bm.refresh_from_db()
        self.assertFalse(bm.is_active)

    def test_reconnect_to_different_business_refused(self):
        other = make_bm(name="Other", business_id="222", token=NEW_TOKEN)
        with self.assertRaisesMessage(ConnectError, "different business"):
            connect_business("c", "u", expected=other)
        other.refresh_from_db()
        self.assertEqual(other.get_token(), NEW_TOKEN)
        self.assertFalse(MetaBusinessManager.objects.filter(meta_business_id="111").exists())

    def test_reconnect_same_business_replaces_token(self):
        bm = make_bm(token=NEW_TOKEN)
        connect_business("c", "u", expected=bm)
        bm.refresh_from_db()
        self.assertEqual(bm.get_token(), TOKEN)

    def test_meta_errors_save_nothing(self):
        for attr in ("exchange_error", "business_error", "check_error"):
            with self.subTest(attr):
                setattr(self.meta, attr, REJECTED)
                with self.assertRaisesMessage(ConnectError, "Meta refused the connection"):
                    connect_business("c", "u")
                setattr(self.meta, attr, None)
        self.assertEqual(MetaBusinessManager.objects.count(), 0)


@override_settings(META_APP_ID="1", META_APP_SECRET="app-secret-xyz", META_API_VERSION="v25.0")
class ExchangeCodeTests(TestCase):
    """The code-for-token call and the business lookup, with HTTP mocked."""

    def response(self, status, body):
        r = mock.Mock(status_code=status, ok=status < 400)
        r.json.return_value = body
        return r

    def test_returns_access_token_and_sends_params(self):
        with mock.patch.object(meta_client.requests, "post", return_value=self.response(200, {"access_token": TOKEN})) as post:
            self.assertEqual(meta_client.exchange_code("the-code", "https://x/cb/"), TOKEN)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://graph.facebook.com/v25.0/oauth/access_token")
        self.assertNotIn("params", kwargs)  # The secret stays out of the URL and its logs.
        self.assertEqual(
            kwargs["data"],
            {"client_id": "1", "client_secret": "app-secret-xyz", "redirect_uri": "https://x/cb/", "code": "the-code"},
        )
        self.assertTrue(kwargs["timeout"])

    def test_graph_error_is_scrubbed(self):
        body = {"error": {"message": "Bad code the-code for secret app-secret-xyz", "code": 100}}
        with mock.patch.object(meta_client.requests, "post", return_value=self.response(400, body)):
            with self.assertRaises(MetaError) as caught:
                meta_client.exchange_code("the-code", "u")
        text = str(caught.exception)
        self.assertNotIn("the-code", text)
        self.assertNotIn("app-secret-xyz", text)
        self.assertIn("Meta error 100", text)
        self.assertIsNone(caught.exception.__cause__)

    def test_exchange_network_error_is_scrubbed(self):
        import requests

        with mock.patch.object(meta_client.requests, "post", side_effect=requests.ConnectionError("x?code=the-code")):
            with self.assertRaisesMessage(MetaError, "Couldn't reach Meta; try again later."):
                meta_client.exchange_code("the-code", "u")

    def test_unexpected_error_body_is_a_meta_error(self):
        with mock.patch.object(meta_client.requests, "post", return_value=self.response(400, {"error": "string"})):
            with self.assertRaisesMessage(MetaError, "Meta refused the login code."):
                meta_client.exchange_code("the-code", "u")

    def test_exchange_hides_all_locals_from_error_reports(self):
        with mock.patch.object(meta_client.requests, "post", return_value=self.response(200, {"access_token": TOKEN})):
            meta_client.exchange_code("the-code", "u")
        # Django sets this on the wrapper when it runs; "__ALL__" hides the app secret too.
        self.assertEqual(meta_client.exchange_code.sensitive_variables, "__ALL__")

    def test_business_for_token_reads_id_and_name(self):
        with mock.patch.object(meta_client.User, "api_get", return_value={"client_business_id": "111"}), \
                mock.patch.object(meta_client.Business, "api_get", return_value={"id": "111", "name": "BM One"}):
            self.assertEqual(meta_client.MetaClient(TOKEN).business_for_token(), ("111", "BM One"))

    def test_missing_client_business_id_refused(self):
        with mock.patch.object(meta_client.User, "api_get", return_value={"id": "me"}):
            with self.assertRaisesMessage(MetaError, "system-user token") as caught:
                meta_client.MetaClient(TOKEN).business_for_token()
        self.assertTrue(caught.exception.token_rejected)


@override_settings(META_APP_ID="1", META_APP_SECRET="app-secret-xyz", META_LOGIN_CONFIG_ID="cfg-9", META_API_VERSION="v25.0")
class ConnectViewTests(MetaTestCase):
    """The admin's Facebook Login for Business round trip (US-META-01, FR-6.10)."""

    CONNECT = BM_URL + "connect/"
    CALLBACK = BM_URL + "connect/callback/"

    def setUp(self):
        super().setUp()
        self.admin_user = make_user("platform", Role.PLATFORM_ADMIN)
        self.client.force_login(self.admin_user)

    def start(self, url=None):
        response = self.client.get(url or self.CONNECT)
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlparse(response["Location"]).query)

    def finish(self, **params):
        state = self.client.session["meta_connect"]["state"]
        return self.client.get(self.CALLBACK, {"state": state, "code": "the-code", **params})

    def test_connect_redirects_to_dialog_with_state(self):
        response = self.client.get(self.CONNECT)
        url = urlparse(response["Location"])
        q = parse_qs(url.query)
        self.assertEqual(url.netloc + url.path, "www.facebook.com/v25.0/dialog/oauth")
        self.assertEqual(q["client_id"], ["1"])
        self.assertEqual(q["config_id"], ["cfg-9"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["override_default_response_type"], ["true"])
        self.assertEqual(q["state"], [self.client.session["meta_connect"]["state"]])
        self.assertTrue(q["redirect_uri"][0].endswith("/en/admin/ad_channels/metabusinessmanager/connect/callback/"))
        self.assertNotIn("app-secret-xyz", response["Location"])

    def test_redirect_uri_is_language_independent(self):
        en = self.start()["redirect_uri"]
        ar = self.start(self.CONNECT.replace("/en/", "/ar/"))["redirect_uri"]
        self.assertEqual(en, ar)
        self.assertIn("/en/admin/", en[0])

    def test_callback_creates_and_opens_change_page(self):
        q = self.start()
        response = self.finish()
        bm = MetaBusinessManager.objects.get(meta_business_id="111")
        self.assertRedirects(response, f"{BM_URL}{bm.pk}/change/", fetch_redirect_response=False)
        self.assertEqual(self.meta.exchanged, [("the-code", q["redirect_uri"][0])])
        self.assertContains(self.client.get(response["Location"]), "Connected")

    def test_connect_loads_every_ad_account_of_the_business(self):
        self.meta.accounts["111"] = [account("act_1"), account("act_2", ownership=SHARED)]
        self.start()
        response = self.finish()
        bm = MetaBusinessManager.objects.get(meta_business_id="111")
        self.assertEqual(
            sorted(bm.ad_accounts.values_list("meta_account_id", flat=True)), ["act_1", "act_2"]
        )
        self.assertFalse(bm.ad_accounts.filter(enabled_for_launch=True).exists())
        self.assertContains(self.client.get(response["Location"]), "2 ad accounts loaded")

    def test_reconnect_loads_new_ad_accounts(self):
        bm = make_bm(token=NEW_TOKEN)
        self.meta.accounts["111"] = [account("act_1"), account("act_9")]
        self.start(f"{self.CONNECT}?bm={bm.pk}")
        self.finish()
        self.assertEqual(bm.ad_accounts.count(), 2)

    def test_ad_account_load_failure_keeps_the_connection_and_says_so(self):
        self.meta.list_error = MetaError("Couldn't reach Meta; try again later.")
        self.start()
        response = self.finish()
        bm = MetaBusinessManager.objects.get(meta_business_id="111")
        self.assertEqual(bm.token_status, MetaBusinessManager.TokenStatus.VALID)
        self.assertEqual(bm.ad_accounts.count(), 0)
        page = self.client.get(response["Location"])
        self.assertContains(page, "ad accounts couldn")
        self.assertContains(page, "Reconnect")

    def test_callback_returns_to_the_language_it_started_in(self):
        self.start(self.CONNECT.replace("/en/", "/ar/"))
        response = self.finish()
        self.assertTrue(response["Location"].startswith("/ar/admin/"))

    def test_wrong_or_missing_state_refused_without_calling_meta(self):
        self.assertEqual(self.client.get(self.CALLBACK, {"code": "c", "state": "x"}).status_code, 403)
        self.start()
        self.assertEqual(self.client.get(self.CALLBACK, {"code": "c", "state": "wrong"}).status_code, 403)
        self.assertEqual(self.client.get(self.CALLBACK, {"code": "c"}).status_code, 403)
        self.assertEqual(self.meta.exchanged, [])
        self.assertEqual(MetaBusinessManager.objects.count(), 0)

    def test_state_is_single_use(self):
        self.start()
        state = self.client.session["meta_connect"]["state"]
        self.client.get(self.CALLBACK, {"state": state, "code": "the-code"})
        again = self.client.get(self.CALLBACK, {"state": state, "code": "the-code"})
        self.assertEqual(again.status_code, 403)
        self.assertEqual(len(self.meta.exchanged), 1)

    def test_cancelled_login_saves_nothing(self):
        self.start()
        response = self.finish(code="", error="access_denied", error_description="Permissions error")
        self.assertRedirects(response, BM_URL, fetch_redirect_response=False)
        self.assertContains(self.client.get(BM_URL), "cancelled")
        self.assertEqual(self.meta.exchanged, [])
        self.assertEqual(MetaBusinessManager.objects.count(), 0)

    def test_connect_error_shown_and_nothing_saved(self):
        self.meta.check_error = REJECTED
        self.start()
        response = self.finish()
        self.assertRedirects(response, BM_URL, fetch_redirect_response=False)
        self.assertContains(self.client.get(BM_URL), "Meta refused the connection")
        self.assertEqual(MetaBusinessManager.objects.count(), 0)

    def test_reconnect_same_business_replaces_token(self):
        bm = make_bm(token=NEW_TOKEN, status=MetaBusinessManager.TokenStatus.INVALID)
        self.start(f"{self.CONNECT}?bm={bm.pk}")
        response = self.finish()
        self.assertRedirects(response, f"{BM_URL}{bm.pk}/change/", fetch_redirect_response=False)
        bm.refresh_from_db()
        self.assertEqual((bm.get_token(), bm.token_status), (TOKEN, "valid"))

    def test_reconnect_to_other_business_refused(self):
        bm = make_bm(name="Other", business_id="222", token=NEW_TOKEN)
        self.start(f"{self.CONNECT}?bm={bm.pk}")
        response = self.finish()
        self.assertRedirects(response, f"{BM_URL}{bm.pk}/change/", fetch_redirect_response=False)
        bm.refresh_from_db()
        self.assertEqual(bm.get_token(), NEW_TOKEN)
        self.assertEqual(MetaBusinessManager.objects.count(), 1)

    def test_add_only_user_cannot_replace_an_existing_token(self):
        from django.contrib.auth.models import Permission

        bm = make_bm(token=NEW_TOKEN)
        user = make_user("adder", Role.MEDIA_BUYING_LEAD)  # Views Business Managers, can't change them.
        user.user_permissions.add(Permission.objects.get(codename="add_metabusinessmanager"))
        self.client.force_login(user)
        self.start()
        response = self.finish()
        self.assertRedirects(response, BM_URL, fetch_redirect_response=False)
        self.assertContains(self.client.get(BM_URL), "already connected")
        bm.refresh_from_db()
        self.assertEqual(bm.get_token(), NEW_TOKEN)

    @override_settings(META_LOGIN_REDIRECT_URI="https://app.example.com/en/admin/ad_channels/metabusinessmanager/connect/callback/")
    def test_redirect_uri_setting_wins_behind_a_proxy(self):
        uri = "https://app.example.com/en/admin/ad_channels/metabusinessmanager/connect/callback/"
        self.assertEqual(self.start()["redirect_uri"], [uri])
        self.finish()
        self.assertEqual(self.meta.exchanged[0][1], uri)

    def test_arabic_admin_gets_arabic_messages(self):
        self.start(self.CONNECT.replace("/en/", "/ar/"))
        response = self.finish()
        self.assertContains(self.client.get(response["Location"]), "تم ربط")

    def test_arabic_admin_gets_arabic_cancel_message(self):
        self.start(self.CONNECT.replace("/en/", "/ar/"))
        response = self.finish(code="", error="access_denied")
        self.assertContains(self.client.get(response["Location"]), "أُلغي تسجيل الدخول")

    def test_network_failure_is_not_called_a_refusal(self):
        self.meta.exchange_error = MetaError("Couldn't reach Meta; try again later.")
        self.start()
        page = self.client.get(self.finish()["Location"])
        self.assertContains(page, "Couldn&#x27;t reach Meta")
        self.assertNotContains(page, "Meta refused the connection")
        self.assertEqual(MetaBusinessManager.objects.count(), 0)

    def test_reconnect_unknown_bm_is_404(self):
        self.assertEqual(self.client.get(f"{self.CONNECT}?bm=999").status_code, 404)
        self.assertEqual(self.client.get(f"{self.CONNECT}?bm=abc").status_code, 404)

    def test_media_buying_lead_refused(self):
        bm = make_bm()
        self.client.force_login(make_user("lead", Role.MEDIA_BUYING_LEAD))
        self.assertEqual(self.client.get(self.CONNECT).status_code, 403)
        self.assertEqual(self.client.get(f"{self.CONNECT}?bm={bm.pk}").status_code, 403)
        self.assertEqual(self.client.get(self.CALLBACK, {"code": "c", "state": "x"}).status_code, 403)
        self.assertEqual(self.meta.exchanged, [])

    def test_anonymous_goes_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.CONNECT).status_code, 302)
        self.assertNotIn("facebook.com", self.client.get(self.CONNECT)["Location"])

    @override_settings(META_LOGIN_CONFIG_ID=None)
    def test_not_configured_shows_error(self):
        response = self.client.get(self.CONNECT)
        self.assertRedirects(response, BM_URL, fetch_redirect_response=False)
        self.assertContains(self.client.get(BM_URL), "META_LOGIN_CONFIG_ID")
        self.assertNotIn("meta_connect", self.client.session)

    def test_no_secret_in_any_response(self):
        self.meta.check_error = MetaError("bad")
        self.start()
        failed = self.finish()
        self.meta.check_error = None
        self.start()
        ok = self.finish()
        for response in (failed, ok):
            page = self.client.get(response["Location"]).content.decode()
            for secret in (TOKEN, "the-code", "app-secret-xyz"):
                self.assertNotIn(secret, page)
                self.assertNotIn(secret, response["Location"])
