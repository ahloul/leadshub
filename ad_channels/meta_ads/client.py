"""The Meta Graph API calls this app makes (US-META-01). No database access.

``MetaClient`` wraps the ``facebook-business`` SDK for one system-user token.
Every SDK or network failure comes out as ``MetaError``, whose message is
Meta's reason with the token scrubbed out, so it is safe to store and show
(criterion 7).
"""

from dataclasses import dataclass
from itertools import islice

import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.business import Business
from facebook_business.adobjects.user import User
from facebook_business.api import FacebookAdsApi
from facebook_business.exceptions import FacebookError, FacebookRequestError
from facebook_business.session import FacebookSession

# Phase 1 launches through the API (SRS §4.1), so the token must be able to
# create ads, not only read them (criterion 2).
REQUIRED_PERMISSIONS = ("ads_management", "business_management")

# Graph error codes that mean the token itself is bad (expired, revoked,
# password change, app removed) or lacks a permission.
_AUTH_ERROR_CODES = {102, 190, 10, 200}

OWNED = "owned"
SHARED = "shared"

_AD_ACCOUNT_FIELDS = [
    AdAccount.Field.id,
    AdAccount.Field.name,
    AdAccount.Field.account_status,
    AdAccount.Field.currency,
    AdAccount.Field.timezone_name,
]


class MetaError(Exception):
    """A Meta call failed. ``str(error)`` never contains the token."""

    def __init__(self, message: str, *, token_rejected: bool = False):
        super().__init__(message)
        # True when Meta refused the token itself rather than failing for a
        # passing reason (network, rate limit, outage).
        self.token_rejected = token_rejected


@dataclass(frozen=True)
class AdAccountInfo:
    id: str  # "act_<number>"
    name: str
    status: int  # Meta account_status, 1 = active
    currency: str
    timezone_name: str
    ownership: str  # OWNED or SHARED


class MetaClient:
    @sensitive_variables("token")
    def __init__(self, token: str):
        self._token = token
        session = FacebookSession(
            app_id=settings.META_APP_ID,
            app_secret=settings.META_APP_SECRET,
            access_token=token,
        )
        # Its own instance: never the SDK's global default, so tokens of
        # different Business Managers can't leak into each other's calls.
        self._api = FacebookAdsApi(session, api_version=settings.META_API_VERSION)

    def __repr__(self) -> str:
        return "<MetaClient>"

    def check_access(self, business_id: str) -> None:
        """Raise ``MetaError`` unless the token can run ads for this business (criterion 2).

        Checks, in order: the token is valid and has ``REQUIRED_PERMISSIONS``,
        it can open the business, and it can read the business's ad accounts.
        """
        with self._errors():
            granted = {
                p["permission"]
                for p in User(fbid="me", api=self._api).get_permissions()
                if p.get("status") == "granted"
            }
        if missing := [p for p in REQUIRED_PERMISSIONS if p not in granted]:
            raise MetaError(
                f"Token is missing permissions: {', '.join(missing)}.", token_rejected=True
            )
        with self._errors():
            business = Business(fbid=business_id, api=self._api)
            business.api_get(fields=[Business.Field.id])
            list(islice(business.get_owned_ad_accounts(fields=[AdAccount.Field.id], params={"limit": 1}), 1))

    def list_ad_accounts(self, business_id: str) -> list[AdAccountInfo]:
        """Owned accounts, then accounts other businesses shared with this one."""
        business = Business(fbid=business_id, api=self._api)
        found: dict[str, AdAccountInfo] = {}
        with self._errors():
            for ownership, edge in (
                (OWNED, business.get_owned_ad_accounts),
                (SHARED, business.get_client_ad_accounts),
            ):
                # The cursor follows Meta's pagination as it is iterated.
                for acc in edge(fields=_AD_ACCOUNT_FIELDS, params={"limit": 100}):
                    found.setdefault(
                        acc[AdAccount.Field.id],
                        AdAccountInfo(
                            id=acc[AdAccount.Field.id],
                            name=acc.get(AdAccount.Field.name, ""),
                            status=int(acc.get(AdAccount.Field.account_status, 0)),
                            currency=acc.get(AdAccount.Field.currency, ""),
                            timezone_name=acc.get(AdAccount.Field.timezone_name, ""),
                            ownership=ownership,
                        ),
                    )
        return list(found.values())

    def business_for_token(self) -> tuple[str, str]:
        """The business a system-user token from Facebook Login for Business belongs to: (id, name)."""
        with self._errors():
            me = User(fbid="me", api=self._api).api_get(fields=["client_business_id"])
        business_id = me.get("client_business_id")
        if not business_id:
            raise MetaError(
                "This login didn't return a business. Check that the Facebook Login for Business "
                "configuration uses a system-user token.",
                token_rejected=True,
            )
        with self._errors():
            business = Business(fbid=business_id, api=self._api).api_get(fields=[Business.Field.name])
        return str(business_id), business.get(Business.Field.name, "") or str(business_id)

    def _errors(self):
        return _TranslateErrors(self._token)


class _TranslateErrors:
    """Turns SDK and network exceptions into ``MetaError`` with the token scrubbed."""

    def __init__(self, token: str):
        self._token = token

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            return False
        if isinstance(exc, FacebookRequestError):
            code = exc.api_error_code()
            message = exc.api_error_message() or "Meta refused the request."
            text = f"{message} (Meta error {code})" if code else message
            rejected = code in _AUTH_ERROR_CODES or exc.api_error_type() == "OAuthException"
        elif isinstance(exc, requests.RequestException):
            text, rejected = "Couldn't reach Meta; try again later.", False
        elif isinstance(exc, FacebookError):
            text, rejected = "The Meta SDK reported an error.", False
        else:
            return False  # A bug in our code: let it surface.
        # `from None`: the original exception can carry request URLs and params.
        raise MetaError(scrub(text, self._token), token_rejected=rejected) from None


def scrub(text: str, token: str) -> str:
    return text.replace(token, "[token]") if token else text


@sensitive_variables()  # All locals: the code, the app secret and the token.
def exchange_code(code: str, redirect_uri: str) -> str:
    """Trade a Facebook Login for Business code for its system-user token (US-META-01).

    The code, the app secret and the token never appear in the ``MetaError``
    text, because Meta can echo request parameters in its messages.
    """
    url = f"https://graph.facebook.com/{settings.META_API_VERSION}/oauth/access_token"
    params = {
        "client_id": settings.META_APP_ID,
        "client_secret": settings.META_APP_SECRET,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    secrets_ = [code, settings.META_APP_SECRET or ""]
    try:
        # POST, so the app secret isn't in a URL that proxies or logs record.
        response = requests.post(url, data=params, timeout=10)
        body = response.json()
    except (requests.RequestException, ValueError):
        raise MetaError("Couldn't reach Meta; try again later.") from None
    token = body.get("access_token") if isinstance(body, dict) else None
    if response.ok and token:
        return token
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        error = {}
    message = error.get("message") or "Meta refused the login code."
    text = f"{message} (Meta error {error['code']})" if error.get("code") else message
    for secret in secrets_:
        text = scrub(text, secret)
    raise MetaError(text, token_rejected=True)
