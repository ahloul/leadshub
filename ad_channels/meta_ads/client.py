"""The Meta Graph API calls this app makes (US-META-01). No database access.

``MetaClient`` wraps the ``facebook-business`` SDK for one system-user token.
Every SDK or network failure comes out as ``MetaError``, whose message is
Meta's reason with the token scrubbed out, so it is safe to store and show
(criterion 7).
"""

import base64
from dataclasses import dataclass
from datetime import datetime
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
]


class MetaError(Exception):
    """A Meta call failed. ``str(error)`` never contains the token."""

    def __init__(self, message: str, *, token_rejected: bool = False):
        super().__init__(message)
        # True when Meta refused the token itself rather than failing for a
        # passing reason (network, rate limit, outage).
        self.token_rejected = token_rejected


@dataclass(frozen=True)
class PageInfo:
    id: str
    name: str
    instagram_actor_id: str  # "" when no Instagram account is linked
    owned: bool = True  # False: another business (a broker) shared it with the platform's Business Manager


@dataclass(frozen=True)
class LeadFormInfo:
    id: str
    name: str


@dataclass(frozen=True)
class LaunchState:
    """What Meta says about a launched campaign now (US-META-02, Refresh)."""

    campaign_status: str
    adset_status: str
    budget_cents: int
    end: datetime | None


# Never sent, whatever the caller passes (FR-5.14, HOUSING special category).
_BANNED_TARGETING = ("age_min", "age_max", "genders", "zips")


@dataclass(frozen=True)
class AdAccountInfo:
    id: str  # "act_<number>"
    name: str
    status: int  # Meta account_status, 1 = active
    ownership: str  # OWNED or SHARED; decides which Business Manager keeps it, not stored


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

    # Launching and managing campaigns (US-META-02). Every call goes through
    # ``FacebookAdsApi.call`` with plain Graph paths and parameters.

    def _call(self, method: str, *path, params=None, files=None, secrets=()) -> dict:
        """``secrets``: other tokens in ``params`` that must be scrubbed from errors too."""
        with _TranslateErrors(self._token, *secrets):
            return self._api.call(method, tuple(path), params=params or {}, files=files).json()

    def _list(self, *path, params=None, secrets=()) -> list[dict]:
        """One page of an edge, up to 100 items: enough for pages, forms and name lookups."""
        return self._call("GET", *path, params={"limit": 100, **(params or {})}, secrets=secrets).get("data", [])

    def list_pages(self, business_id: str) -> list[PageInfo]:
        """Pages the Business Manager owns, then pages other businesses shared with it."""
        found: dict[str, PageInfo] = {}
        for edge in ("owned_pages", "client_pages"):
            for page in self._list(business_id, edge, params={"fields": "id,name,instagram_business_account"}):
                found.setdefault(page["id"], PageInfo(
                    id=page["id"], name=page.get("name", ""),
                    instagram_actor_id=(page.get("instagram_business_account") or {}).get("id", ""),
                    owned=edge == "owned_pages",
                ))
        return list(found.values())

    @sensitive_variables("page_token")
    def list_lead_forms(self, page_id: str) -> list[LeadFormInfo]:
        """The page's active instant forms. Reading them needs the page's own token, never kept."""
        page_token = self._call("GET", page_id, params={"fields": "access_token"}).get("access_token", "")
        forms = self._list(page_id, "leadgen_forms", params={"fields": "id,name,status", "access_token": page_token},
                           secrets=(page_token,))
        return [LeadFormInfo(id=f["id"], name=f.get("name", "")) for f in forms if f.get("status") == "ACTIVE"]

    def ad_account_currency(self, account_id: str) -> str:
        return self._call("GET", account_id, params={"fields": "currency"}).get("currency", "")

    def find_by_name(self, account_id: str, edge: str, name: str) -> str | None:
        """An existing campaign or ad set with exactly this name, so a lost reply isn't built twice."""
        items = self._list(account_id, edge, params={
            "fields": "id,name",
            "filtering": [{"field": "name", "operator": "EQUAL", "value": name}],
        })
        return next((item["id"] for item in items if item.get("name") == name), None)

    def create_campaign(self, account_id: str, name: str) -> str:
        return self._call("POST", account_id, "campaigns", params={
            "name": name,
            "objective": "OUTCOME_LEADS",
            "special_ad_categories": ["HOUSING"],
            "special_ad_category_country": ["EG"],
            "status": "PAUSED",
            # The budget is on the ad set, not shared across ad sets.
            "is_adset_budget_sharing_enabled": False,
        })["id"]

    def create_adset(self, account_id: str, *, name, campaign_id, page_id, budget_kind: str,
                     budget_cents: int, start: datetime, end: datetime | None, targeting: dict) -> str:
        params = {
            "name": name,
            "campaign_id": campaign_id,
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "LEAD_GENERATION",
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "destination_type": "ON_AD",
            "promoted_object": {"page_id": page_id},
            "targeting": {
                **{k: v for k, v in targeting.items() if k not in _BANNED_TARGETING},
                # Newer API versions require the choice; Advantage+ audience
                # would widen targeting beyond the preset.
                "targeting_automation": {"advantage_audience": 0},
            },
            "start_time": start.isoformat(),
            "status": "PAUSED",
            **_budget(budget_kind, budget_cents),
        }
        if end is not None:
            params["end_time"] = end.isoformat()
        return self._call("POST", account_id, "adsets", params=params)["id"]

    def search_interest(self, name: str) -> str | None:
        """Meta's interest ID for a name, only on an exact (case-insensitive) match."""
        for item in self._list("search", params={"type": "adinterest", "q": name}):
            if item.get("name", "").casefold() == name.casefold():
                return item["id"]
        return None

    def upload_image(self, account_id: str, filename: str, data: bytes) -> str:
        reply = self._call("POST", account_id, "adimages",
                           params={"bytes": base64.b64encode(data).decode(), "name": filename})
        return next(iter(reply["images"].values()))["hash"]

    def upload_video(self, account_id: str, filename: str, data: bytes) -> str:
        return self._call("POST", account_id, "advideos", params={"name": filename},
                          files={"source": (filename, data)})["id"]

    def video_ready(self, video_id: str) -> bool:
        status = self._call("GET", video_id, params={"fields": "status"}).get("status") or {}
        return status.get("video_status") == "ready"

    def video_thumbnail(self, video_id: str) -> str:
        """The processed video's thumbnail URL; video ads need one."""
        return self._call("GET", video_id, params={"fields": "picture"}).get("picture", "")

    def create_ad_creative(self, account_id: str, *, name, page_id, instagram_actor_id, lead_form_id,
                           message: str, image_hash: str = "", video_id: str = "", image_url: str = "") -> str:
        cta = {"type": "SIGN_UP", "value": {"lead_gen_form_id": lead_form_id}}
        spec = {"page_id": page_id}
        if instagram_actor_id:
            spec["instagram_user_id"] = instagram_actor_id
        if video_id:
            thumbnail = {"image_hash": image_hash} if image_hash else {"image_url": image_url} if image_url else {}
            spec["video_data"] = {"video_id": video_id, "message": message, "call_to_action": cta, **thumbnail}
        else:
            # Lead ads need a link; Meta's own placeholder for instant forms.
            spec["link_data"] = {"image_hash": image_hash, "message": message,
                                 "link": "http://fb.me/", "call_to_action": cta}
        return self._call("POST", account_id, "adcreatives",
                          params={"name": name, "object_story_spec": spec})["id"]

    def create_ad(self, account_id: str, *, name, adset_id, creative_id) -> str:
        return self._call("POST", account_id, "ads", params={
            "name": name, "adset_id": adset_id, "creative": {"creative_id": creative_id}, "status": "PAUSED",
        })["id"]

    def set_status(self, object_id: str, status: str) -> None:
        """ACTIVE, PAUSED or ARCHIVED, for a campaign, ad set or ad."""
        self._call("POST", object_id, params={"status": status})

    def update_adset(self, adset_id: str, *, budget_kind: str, budget_cents: int, end: datetime | None) -> None:
        params = _budget(budget_kind, budget_cents)
        params["end_time"] = end.isoformat() if end is not None else 0
        self._call("POST", adset_id, params=params)

    def delete_object(self, object_id: str) -> None:
        self._call("DELETE", object_id)

    def read_launch(self, campaign_id: str, adset_id: str) -> LaunchState:
        campaign = self._call("GET", campaign_id, params={"fields": "effective_status"})
        adset = self._call("GET", adset_id,
                           params={"fields": "effective_status,daily_budget,lifetime_budget,end_time"})
        end = adset.get("end_time")
        return LaunchState(
            campaign_status=campaign.get("effective_status", ""),
            adset_status=adset.get("effective_status", ""),
            budget_cents=int(adset.get("daily_budget") or adset.get("lifetime_budget") or 0),
            end=datetime.fromisoformat(end) if end else None,
        )

    def _errors(self):
        return _TranslateErrors(self._token)


class _TranslateErrors:
    """Turns SDK and network exceptions into ``MetaError`` with the token scrubbed."""

    def __init__(self, token: str, *others: str):
        self._token = token
        self._others = others

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
        for secret in (self._token, *self._others):
            text = scrub(text, secret)
        raise MetaError(text, token_rejected=rejected) from None


def _budget(kind: str, cents: int) -> dict:
    return {"lifetime_budget": cents} if kind == "lifetime" else {"daily_budget": cents}


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
