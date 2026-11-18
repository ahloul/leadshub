"""An in-memory Meta for launch tests (US-META-02). Never calls Meta.

Patch ``ad_channels.meta_ads.sync.client_for`` with an instance: calling it
with a token returns the fake itself, as ``client_for`` returns a client.
"""

import itertools

from .meta_ads.client import LaunchState, LeadFormInfo, PageInfo


class FakeMetaLauncher:
    def __init__(self):
        self.calls = []  # (method, kwargs)
        self.fail_on = {}  # method name -> MetaError, raised once
        self.fail_always = {}  # method name -> MetaError, raised every time
        self.currency = "USD"
        self.pages = {"111": [PageInfo(id="p1", name="LeadsHub Homes", instagram_actor_id="ig1")]}
        self.forms = {"p1": [LeadFormInfo(id="f1", name="Buyers form")]}
        self.interests = {"real estate": "600", "compounds": "700"}
        self.existing = {}  # (edge, name) -> id, for find_by_name
        self.video_ready_answer = True
        self.state = LaunchState(campaign_status="ACTIVE", adset_status="ACTIVE", budget_cents=3000, end=None)
        self.tokens = []
        self._ids = itertools.count(1)

    def __call__(self, token):
        self.tokens.append(token)
        return self

    def _record(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method in self.fail_always:
            raise self.fail_always[method]
        if method in self.fail_on:
            raise self.fail_on.pop(method)

    def called(self, method):
        return [kwargs for name, kwargs in self.calls if name == method]

    def _id(self, prefix):
        return f"{prefix}{next(self._ids)}"

    def list_pages(self, business_id):
        self._record("list_pages", business_id=business_id)
        return list(self.pages.get(business_id, []))

    def list_lead_forms(self, page_id):
        self._record("list_lead_forms", page_id=page_id)
        return list(self.forms.get(page_id, []))

    def ad_account_currency(self, account_id):
        self._record("ad_account_currency", account_id=account_id)
        return self.currency

    def find_by_name(self, account_id, edge, name):
        self._record("find_by_name", account_id=account_id, edge=edge, name=name)
        return self.existing.get((edge, name))

    def create_campaign(self, account_id, name):
        self._record("create_campaign", account_id=account_id, name=name)
        return self._id("c")

    def create_adset(self, account_id, **kwargs):
        self._record("create_adset", account_id=account_id, **kwargs)
        return self._id("s")

    def search_interest(self, name):
        self._record("search_interest", name=name)
        return self.interests.get(name.casefold())

    def upload_image(self, account_id, filename, data):
        self._record("upload_image", account_id=account_id, filename=filename)
        return self._id("img")

    def upload_video(self, account_id, filename, data):
        self._record("upload_video", account_id=account_id, filename=filename)
        return self._id("v")

    def video_ready(self, video_id):
        self._record("video_ready", video_id=video_id)
        return self.video_ready_answer

    def create_ad_creative(self, account_id, **kwargs):
        self._record("create_ad_creative", account_id=account_id, **kwargs)
        return self._id("cr")

    def create_ad(self, account_id, **kwargs):
        self._record("create_ad", account_id=account_id, **kwargs)
        return self._id("ad")

    def set_status(self, object_id, status):
        self._record("set_status", object_id=object_id, status=status)

    def update_adset(self, adset_id, **kwargs):
        self._record("update_adset", adset_id=adset_id, **kwargs)

    def delete_object(self, object_id):
        self._record("delete_object", object_id=object_id)

    def read_launch(self, campaign_id, adset_id):
        self._record("read_launch", campaign_id=campaign_id, adset_id=adset_id)
        return self.state
