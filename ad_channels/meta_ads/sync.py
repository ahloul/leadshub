"""Connecting, token checks and ad account sync for Business Managers (US-META-01).

Called only from the admin: the Facebook Login callback, saving a Business
Manager, and the Sync and Recheck token actions. Nothing runs on a schedule; the Platform Admin either
adds ad accounts by hand or presses Sync.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils.translation import gettext as _
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from ..models import MetaAdAccount, MetaBusinessManager
from . import client as _client
from .client import OWNED, MetaClient, MetaError


class SyncError(Exception):
    """Sync didn't run or failed. The message is safe to show."""


class ConnectError(Exception):
    """Connecting a Business Manager failed. The message is safe to show."""


@dataclass(frozen=True)
class SyncResult:
    created: int
    updated: int
    removed: int


def client_for(token: str) -> MetaClient:
    """The Meta client for a token. Tests replace this with a fake."""
    return MetaClient(token)


@sensitive_variables("code")
def exchange_code(code: str, redirect_uri: str) -> str:
    """The system-user token for a login code. Tests replace this with a fake."""
    return _client.exchange_code(code, redirect_uri)


@sensitive_variables("code", "token")
def connect_business(
    code: str,
    redirect_uri: str,
    *,
    expected: MetaBusinessManager | None = None,
    may_replace=lambda bm: True,
) -> tuple[MetaBusinessManager, bool]:
    """Save the Business Manager a Facebook Login for Business code grants (FR-6.10).

    Creates it, or replaces the token of the one with the same Meta business
    ID. ``expected`` is the Business Manager being reconnected: the login
    must be for that same business. ``may_replace(bm)`` says whether the
    caller may replace an existing Business Manager's token (the admin's
    change permission), so adding can't be used to overwrite one. Every Meta
    call happens before any write, so a failure saves nothing.
    """
    try:
        token = exchange_code(code, redirect_uri)
        meta = client_for(token)
        business_id, name = meta.business_for_token()
        if expected is not None and business_id != expected.meta_business_id:
            raise ConnectError(
                _("You picked a different business than %(bm)s. Nothing was changed.") % {"bm": expected}
            )
        meta.check_access(business_id)
    except MetaError as error:
        if not error.token_rejected:  # Network, outage or rate limit: not a refusal.
            raise ConnectError(str(error)) from None
        raise ConnectError(_("Meta refused the connection: %(reason)s") % {"reason": error}) from None

    with transaction.atomic():
        bm = MetaBusinessManager.objects.select_for_update().filter(meta_business_id=business_id).first()
        created = bm is None
        if not created and not may_replace(bm):
            raise ConnectError(
                _("%(bm)s is already connected, and you may not change it. Nothing was changed.") % {"bm": bm}
            )
        if created:
            bm = MetaBusinessManager(name=name, meta_business_id=business_id)
        bm.set_token(token)
        bm.token_status = MetaBusinessManager.TokenStatus.VALID
        bm.token_error = ""
        bm.token_checked_at = timezone.now()
        bm.save()
    return bm, created


@sensitive_variables("token")
def check_token(business_id: str, token: str) -> str | None:
    """Meta's reason the token can't run this business, or None if it can (criterion 2)."""
    try:
        client_for(token).check_access(business_id)
    except MetaError as error:
        return str(error)
    return None


def recheck_token(bm: MetaBusinessManager) -> None:
    """Recheck the stored token and record the result (criteria 5, 17).

    A passing failure (network, rate limit) is recorded but keeps the
    current status, so a Meta outage doesn't pull every account from launch.
    """
    try:
        client_for(bm.get_token()).check_access(bm.meta_business_id)
    except MetaError as error:
        if error.token_rejected:
            bm.token_status = MetaBusinessManager.TokenStatus.INVALID
        bm.token_error = str(error)
    else:
        bm.token_status = MetaBusinessManager.TokenStatus.VALID
        bm.token_error = ""
    bm.token_checked_at = timezone.now()
    bm.save(update_fields=["token_status", "token_error", "token_checked_at", "updated_at"])


def sync_business_manager(bm: MetaBusinessManager) -> SyncResult:
    """Copy this Business Manager's ad accounts from Meta (criteria 8 to 13)."""
    if not bm.is_active:
        raise SyncError("Business Manager is deactivated.")

    # Every Meta call happens before any write, so a failure leaves the
    # ad accounts exactly as they were (criterion 13).
    try:
        accounts = client_for(bm.get_token()).list_ad_accounts(bm.meta_business_id)
    except MetaError as error:
        now = timezone.now()
        bm.last_sync_error = str(error)
        bm.last_sync_failed_at = now
        fields = ["last_sync_error", "last_sync_failed_at", "updated_at"]
        if error.token_rejected:
            bm.token_status = MetaBusinessManager.TokenStatus.INVALID
            bm.token_error = str(error)
            bm.token_checked_at = now
            fields += ["token_status", "token_error", "token_checked_at"]
        bm.save(update_fields=fields)
        raise SyncError(str(error)) from None

    now = timezone.now()
    created = updated = 0
    with transaction.atomic():
        existing = {
            account.meta_account_id: account
            for account in MetaAdAccount.objects.select_for_update().filter(
                meta_account_id__in=[info.id for info in accounts]
            )
        }
        for info in accounts:
            account = existing.get(info.id)
            if account is None:
                # New accounts start off for launch (criterion 10).
                account = MetaAdAccount(meta_account_id=info.id, enabled_for_launch=False)
                created += 1
            elif account.business_manager_id != bm.pk and not _moves_here(account, info):
                continue  # Another Business Manager keeps it.
            else:
                updated += 1
            # Meta-owned fields only; enabled_for_launch is left alone (criterion 11).
            account.business_manager = bm
            account.name = info.name
            account.account_status = info.status
            account.is_removed = False
            account.last_synced_at = now
            account.save()

        # Gone from Meta: kept, but no longer offered for launch (criterion 12).
        removed = (
            bm.ad_accounts.filter(is_removed=False)
            .exclude(meta_account_id__in=[info.id for info in accounts])
            .update(is_removed=True, updated_at=now)
        )

        bm.last_synced_at = now
        bm.last_sync_error = ""
        bm.save(update_fields=["last_synced_at", "last_sync_error", "updated_at"])

    return SyncResult(created=created, updated=updated, removed=removed)


def _moves_here(account: MetaAdAccount, info) -> bool:
    """Whether an account already saved under another Business Manager moves to this one.

    The same ad account can be owned by one of our Business Managers and
    shared with another. Each account is stored once (criterion 9), under
    the business that owns it, or wherever it is still returned.
    """
    if account.is_removed:
        return True
    # Only the owner takes it over; a business it's merely shared with doesn't.
    return info.ownership == OWNED
