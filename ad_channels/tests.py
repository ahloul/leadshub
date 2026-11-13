"""US-META-01: Meta Business Managers and ad accounts (FR-6.10, FR-6.11).

Meta is never called: ``meta_ads.sync.client_for`` is replaced by ``FakeMeta``.
"""

import json
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
from .meta_ads.sync import SyncError, recheck_token, sync_business_manager

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

    def __call__(self, token):
        self.tokens.append(token)
        return self

    def check_access(self, business_id):
        if self.check_error:
            raise self.check_error

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
        patcher = mock.patch("ad_channels.meta_ads.sync.client_for", self.meta)
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


class AddBusinessManagerTests(MetaTestCase):
    """Criteria 1, 2, 3, 6, 7."""

    def setUp(self):
        super().setUp()
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))

    def add(self, token=TOKEN, business_id="111"):
        return self.client.post(f"{BM_URL}add/", {"name": "BM One", "meta_business_id": business_id, "token": token})

    def test_valid_token_is_saved_as_valid(self):
        response = self.add()
        self.assertEqual(response.status_code, 302)
        bm = MetaBusinessManager.objects.get()
        self.assertEqual(bm.token_status, MetaBusinessManager.TokenStatus.VALID)
        self.assertIsNotNone(bm.token_checked_at)
        self.assertEqual(bm.get_token(), TOKEN)
        self.assertEqual(self.meta.tokens, [TOKEN])

    def test_rejected_token_is_not_saved_and_the_reason_is_shown(self):
        self.meta.check_error = MetaError("Token is missing permissions: ads_management.", token_rejected=True)
        response = self.add()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MetaBusinessManager.objects.exists())
        self.assertContains(response, "missing permissions: ads_management")
        self.assertNotContains(response, TOKEN)  # Not even echoed back into the field.

    def test_business_id_must_be_numeric(self):
        response = self.add(business_id="act_111")
        self.assertContains(response, "numeric Meta business ID")
        self.assertEqual(self.meta.tokens, [])

    def test_same_business_id_twice_is_rejected_without_calling_meta(self):
        make_bm(business_id="111")
        response = self.add(business_id="111")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MetaBusinessManager.objects.count(), 1)
        self.assertEqual(self.meta.tokens, [])

    def test_token_never_appears_in_pages_or_history(self):
        self.add()
        bm = MetaBusinessManager.objects.get()
        for url in (BM_URL, f"{BM_URL}{bm.pk}/change/", f"{BM_URL}{bm.pk}/history/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, TOKEN)
                self.assertNotContains(response, bm.token_ciphertext)
        self.assertContains(self.client.get(f"{BM_URL}{bm.pk}/change/"), "••••1234")
        for entry in LogEntry.objects.all():
            self.assertNotIn(TOKEN, entry.change_message)


class ReplaceTokenTests(MetaTestCase):
    """Criterion 6."""

    def setUp(self):
        super().setUp()
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        self.bm = make_bm()

    def change(self, token="", **fields):
        data = {"name": "BM One", "meta_business_id": "111", "token": token, **fields}
        return self.client.post(f"{BM_URL}{self.bm.pk}/change/", data)

    def test_blank_token_keeps_the_current_one_without_calling_meta(self):
        self.assertEqual(self.change(name="Renamed").status_code, 302)
        self.bm.refresh_from_db()
        self.assertEqual((self.bm.name, self.bm.get_token()), ("Renamed", TOKEN))
        self.assertEqual(self.meta.tokens, [])

    def test_new_valid_token_replaces_the_old_one(self):
        self.assertEqual(self.change(NEW_TOKEN).status_code, 302)
        self.bm.refresh_from_db()
        self.assertEqual(self.bm.get_token(), NEW_TOKEN)
        self.assertEqual(self.bm.masked_token, "••••9876")

    def test_rejected_new_token_keeps_the_old_one(self):
        self.meta.check_error = REJECTED
        response = self.change(NEW_TOKEN)
        self.assertContains(response, "Error validating access token")
        self.bm.refresh_from_db()
        self.assertEqual(self.bm.get_token(), TOKEN)

    def test_changing_the_business_id_rechecks_the_stored_token(self):
        self.meta.check_error = REJECTED
        self.change(meta_business_id="222")
        self.bm.refresh_from_db()
        self.assertEqual(self.bm.meta_business_id, "111")
        self.assertEqual(self.meta.tokens, [TOKEN])


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
            self.client.post(
                f"{BM_URL}{self.bm.pk}/change/", {"name": "x", "meta_business_id": "111", "token": NEW_TOKEN}
            ).status_code,
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

    def test_save_and_sync_button_on_the_edit_page(self):
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        page = self.client.get(f"{BM_URL}{self.bm.pk}/change/")
        self.assertContains(page, "ad_channels_metabusinessmanager_save_and_sync")
        self.meta.accounts["111"].append(account("act_2"))
        response = self.client.post(
            f"{BM_URL}{self.bm.pk}/change/",
            {"name": "Renamed", "meta_business_id": "111", "token": "", "ad_channels_metabusinessmanager_save_and_sync": ""},
            follow=True,
        )
        self.assertContains(response, "1 new, 1 updated, 0 removed")
        self.assertEqual(MetaBusinessManager.objects.get().name, "Renamed")
        self.assertEqual(MetaAdAccount.objects.count(), 2)

    def test_lead_does_not_see_the_save_and_sync_button(self):
        self.client.force_login(make_user("lead", Role.MEDIA_BUYING_LEAD))
        self.assertNotContains(self.client.get(f"{BM_URL}{self.bm.pk}/change/"), "save_and_sync")

    def test_platform_admin_can_sync_from_the_list(self):
        self.client.force_login(make_user("admin", Role.PLATFORM_ADMIN))
        self.meta.accounts["111"].append(account("act_2"))
        response = self.client.post(BM_URL, {"action": "sync_accounts", "_selected_action": [self.bm.pk]}, follow=True)
        self.assertContains(response, "1 new, 1 updated, 0 removed")
        self.assertEqual(MetaAdAccount.objects.count(), 2)


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

    def test_failed_sync_is_flagged_in_the_list(self):
        MetaBusinessManager.objects.filter(pk=self.one.pk).update(
            last_sync_failed_at=timezone.now() + timedelta(minutes=1)
        )
        self.assertContains(self.client.get(BM_URL), "Failed")

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
