from django.test import RequestFactory, TestCase, TransactionTestCase
from django.utils import translation
from djust import LiveView
from djust.testing import LiveViewTestClient

from accounts.roles import Role
from accounts.test_utils import make_office, make_user
from creatives.test_utils import TemporaryUploadsMixin

from .live import PageLanguageMixin


class DashboardTests(TestCase):
    def setUp(self):
        self.office = make_office("Nour Realty", feedback_score=82)
        self.owner = make_user("owner", Role.BROKER_OWNER, office=self.office, first_name="Nour")
        other = make_office("Cairo Homes")
        make_user("rival", Role.BROKER_OWNER, office=other, email="rival@cairohomes.test")

    def test_anonymous_users_are_sent_to_login(self):
        response = self.client.get("/en/portal/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_member_sees_only_their_own_office(self):
        self.client.force_login(self.owner)
        response = self.client.get("/en/portal/")
        self.assertContains(response, "Nour Realty")
        self.assertContains(response, "owner@example.com")
        self.assertNotContains(response, "Cairo Homes")
        self.assertNotContains(response, "rival@cairohomes.test")

    def test_internal_staff_are_refused(self):
        self.client.force_login(make_user("buyer", Role.MEDIA_BUYER))
        self.assertEqual(self.client.get("/en/portal/").status_code, 403)

    def test_broker_not_yet_in_an_office_gets_404(self):
        self.client.force_login(make_user("loner", Role.BROKER_OWNER))
        self.assertEqual(self.client.get("/en/portal/").status_code, 404)

    def test_no_password_hash_in_page(self):
        self.client.force_login(self.owner)
        html = self.client.get("/en/portal/").content.decode()
        self.assertNotIn(self.owner.password, html)
        self.assertNotIn("pbkdf2", html)


class LanguageAndLayoutTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user("owner", Role.BROKER_OWNER, office=make_office()))

    def test_english_is_left_to_right(self):
        html = self.client.get("/en/portal/").content.decode()
        self.assertIn('lang="en" dir="ltr"', html)
        self.assertIn("Your office at a glance.", html)

    def test_arabic_is_right_to_left_and_translated(self):
        html = self.client.get("/ar/portal/").content.decode()
        self.assertIn('lang="ar" dir="rtl"', html)
        self.assertIn("نظرة سريعة على مكتبك.", html)

    def test_money_is_grouped_the_same_in_both_languages(self):
        office = make_office("Big Office", wallet_balance_egp=250000)
        self.client.force_login(make_user("big", Role.BROKER_OWNER, office=office))
        for lang in ("en", "ar"):
            with self.subTest(lang=lang):
                self.assertContains(self.client.get(f"/{lang}/portal/"), "EGP 250,000.00")

    def test_switching_language_keeps_the_page(self):
        response = self.client.post("/i18n/setlang/", {"language": "ar", "next": "/en/portal/"})
        self.assertRedirects(response, "/ar/portal/", fetch_redirect_response=False)

    def test_root_redirects_to_a_language(self):
        self.client.logout()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/en/")

    def test_page_is_responsive_and_themed(self):
        html = self.client.get("/en/portal/").content.decode()
        self.assertIn('name="viewport"', html)
        self.assertIn("css/output.css", html)
        self.assertIn("data-theme-toggle", html)


class LoginTests(TestCase):
    def test_login_form_has_labelled_inputs(self):
        response = self.client.get("/en/login/")
        self.assertContains(response, '<label for="id_username"')
        self.assertContains(response, 'name="username"')
        self.assertContains(response, '<label for="id_password"')
        self.assertContains(response, 'type="password"')

    def test_login_page_renders_in_both_languages(self):
        self.assertContains(self.client.get("/en/login/"), "Sign in to LeadsHub")
        self.assertContains(self.client.get("/ar/login/"), 'dir="rtl"')

    def test_wrong_password_shows_an_error(self):
        make_user("owner")
        response = self.client.post("/en/login/", {"username": "owner", "password": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')

    def test_correct_password_signs_in(self):
        make_user("owner", Role.BROKER_OWNER, office=make_office())
        response = self.client.post("/en/login/", {"username": "owner", "password": "pass-12345"})
        self.assertRedirects(response, "/en/portal/", fetch_redirect_response=False)

    def test_repeated_failures_lock_the_account(self):
        make_user("owner")
        for _ in range(5):
            self.client.post("/en/login/", {"username": "owner", "password": "nope"})
        response = self.client.post("/en/login/", {"username": "owner", "password": "pass-12345"})
        self.assertEqual(response.status_code, 429)


class LoginLandingTests(TestCase):
    """Each kind of user lands on their own area after sign-in."""

    def login(self, user, **extra):
        return self.client.post("/en/login/", {"username": user.username, "password": "pass-12345", **extra})

    def test_broker_lands_on_dashboard(self):
        user = make_user("owner", Role.BROKER_OWNER, office=make_office())
        self.assertRedirects(self.login(user), "/en/portal/", fetch_redirect_response=False)

    def test_internal_staff_land_on_back_office(self):
        user = make_user("buyer", Role.MEDIA_BUYER)
        self.assertRedirects(self.login(user), "/en/back-office/", fetch_redirect_response=False)

    def test_developer_viewer_lands_on_developer_view(self):
        from catalog.models import Developer

        dev = Developer.objects.create(name_en="Palm Hills", name_ar="بالم هيلز")
        user = make_user("dev", Role.DEVELOPER_VIEW, developer=dev)
        self.assertRedirects(self.login(user), "/en/developer/", fetch_redirect_response=False)

    def test_next_parameter_wins(self):
        user = make_user("lead", Role.MEDIA_BUYING_LEAD)
        response = self.login(user, next="/en/admin/catalog/project/")
        self.assertRedirects(response, "/en/admin/catalog/project/", fetch_redirect_response=False)

    def test_arabic_login_stays_in_arabic(self):
        response = self.client.get("/ar/")
        self.assertTrue(response["Location"].startswith("/ar/login/"))


class HeaderFormTests(TestCase):
    """Header forms on live pages must carry a CSRF token, or Django rejects them."""

    def setUp(self):
        self.client = self.client_class(enforce_csrf_checks=True)
        self.client.force_login(make_user("owner", Role.BROKER_OWNER, office=make_office()))

    def test_language_and_logout_forms_have_csrf_tokens(self):
        html = self.client.get("/en/portal/").content.decode()
        self.assertEqual(html.count('name="csrfmiddlewaretoken"'), 2)

    def test_language_switch_from_dashboard_succeeds(self):
        response = self.client.get("/en/portal/")
        token = response.cookies["csrftoken"].value
        response = self.client.post(
            "/i18n/setlang/", {"language": "ar", "next": "/en/portal/", "csrfmiddlewaretoken": token}
        )
        self.assertRedirects(response, "/ar/portal/", fetch_redirect_response=False)


class _ArabicPageRequests(RequestFactory):
    """Requests for an /ar/ page, as the live mount sees them."""

    def get(self, path, *args, **kwargs):
        return super().get("/ar/", *args, **kwargs)


def _language_probe_view():
    # Built per test so djust's view checks (V005) don't see a module-level view.
    class LanguageProbeView(PageLanguageMixin, LiveView):
        login_required = False
        template = '{% load i18n %}<div dj-root>{% translate "Team" %}</div>'

    return LanguageProbeView


class PageLanguageMixinTests(TestCase):
    """Live renders use the page's language, not the thread's (djust 1.2.8 gap)."""

    def test_live_render_uses_the_page_language(self):
        client = LiveViewTestClient(_language_probe_view(), request_factory=_ArabicPageRequests())
        # The WebSocket thread has the default language (English) active.
        with translation.override("en"):
            client.mount()
            html = client.render()
        self.assertIn("الفريق", html)
        self.assertNotIn("Team", html)

    def test_the_render_does_not_leak_the_language(self):
        client = LiveViewTestClient(_language_probe_view(), request_factory=_ArabicPageRequests())
        with translation.override("en"):
            client.mount()
            client.render()
            self.assertEqual(translation.get_language(), "en")

    def test_model_choice_labels_follow_the_page_language(self):
        office = make_office(spend_tier="gold")

        class TierProbeView(PageLanguageMixin, LiveView):
            login_required = False
            template = "<div dj-root>{{ office.get_spend_tier_display }}</div>"

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context["office"] = office
                return context

        client = LiveViewTestClient(TierProbeView, request_factory=_ArabicPageRequests())
        with translation.override("en"):
            client.mount()
            # Same call order as djust's WebSocket mount (runtime.dispatch_mount).
            # The WS-only failure itself was confirmed in a browser, not here.
            client.view_instance._sync_state_to_rust()
            html = client.render()
        self.assertIn("ذهبي", html)

    def test_event_handlers_run_in_the_page_language(self):
        from django.utils.translation import gettext
        from djust.decorators import event_handler

        class HandlerProbeView(PageLanguageMixin, LiveView):
            login_required = False
            template = "<div dj-root>{{ label }}</div>"

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.label = ""

            @event_handler()
            def name_it(self, **kwargs):
                self.label = gettext("Team")

        client = LiveViewTestClient(HandlerProbeView, request_factory=_ArabicPageRequests())
        with translation.override("en"):
            client.mount()
            self.assertTrue(client.send_event("name_it")["success"])
            self.assertEqual(translation.get_language(), "en")  # not leaked
        client.assert_state(label="الفريق")

    def test_form_errors_follow_the_page_language(self):
        from django import forms
        from djust.forms import FormMixin

        class NameForm(forms.Form):
            name = forms.CharField()

        class FormProbeView(PageLanguageMixin, FormMixin, LiveView):
            login_required = False
            template = "{% load live_tags %}<div dj-root>{% live_form view %}</div>"
            form_class = NameForm

        client = LiveViewTestClient(FormProbeView, request_factory=_ArabicPageRequests())
        with translation.override("en"):
            client.mount()
            client.send_event("submit_form", name="")
            # FormMixin keeps Django's lazy errors; they become text when the
            # page renders, in the page language. render_with_patches() is the
            # WebSocket render, which {% live_form %} needs (render() doesn't
            # register the view the tag falls back to).
            html, _patches, _version = client.render_with_patches()
        self.assertIn("هذا الحقل مطلوب.", html)
        self.assertNotIn("This field is required.", html)

    def test_handler_permissions_survive_the_language_wrapper(self):
        from djust.decorators import is_event_handler

        from .views import TeamView

        handler = TeamView.allow_campaigns
        self.assertTrue(is_event_handler(handler))
        self.assertEqual(
            handler._djust_decorators["permission_required"],
            ["accounts.manage_team", "accounts.run_campaigns"],
        )


def _user_for(role):
    """A user holding ``role``, set up the way that role needs (office, developer)."""
    from catalog.models import Developer

    extra = {}
    if role in (Role.BROKER_OWNER, Role.BROKER_TEAM_MEMBER):
        extra["office"] = make_office(f"Office {role.name}")
    if role == Role.DEVELOPER_VIEW:
        extra["developer"] = Developer.objects.create(name_en="Palm Hills", name_ar="بالم هيلز")
    return make_user(f"u-{role.name.lower()}", role, **extra)


# Who may open each page (SRS §2.1): one audience per page.
INTERNAL = {
    Role.PLATFORM_ADMIN, Role.MEDIA_BUYING_LEAD, Role.MEDIA_BUYER,
    Role.CONTENT_MANAGER, Role.FINANCE_MANAGER,
}
PAGE_AUDIENCES = {
    "/en/back-office/": INTERNAL,
    "/en/portal/": {Role.BROKER_OWNER, Role.BROKER_TEAM_MEMBER},
    "/en/portal/team/": {Role.BROKER_OWNER},
    "/en/portal/library/": {Role.BROKER_OWNER, Role.BROKER_TEAM_MEMBER},
    "/en/developer/": {Role.DEVELOPER_VIEW},
}


class PageAccessTests(TestCase):
    """Every role reaches its own audience's pages and is refused the others."""

    def test_role_by_page_matrix(self):
        for role in Role:
            user = _user_for(role)
            self.client.force_login(user)
            for url, allowed in PAGE_AUDIENCES.items():
                with self.subTest(role=role, url=url):
                    status = self.client.get(url).status_code
                    self.assertEqual(status, 200 if role in allowed else 403)

    def test_anonymous_users_are_sent_to_login_everywhere(self):
        for url in [*PAGE_AUDIENCES, "/en/"]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/en/login/"))

    def test_live_mount_enforces_the_same_rules(self):
        """The WebSocket mount authorizes with djust's check_view_auth, not dispatch()."""
        from django.core.exceptions import PermissionDenied
        from djust.auth.core import check_view_auth

        from .views import BackOfficeView, DashboardView, DeveloperHomeView, LibraryView, TeamView

        views = {
            "/en/back-office/": BackOfficeView,
            "/en/portal/": DashboardView,
            "/en/portal/team/": TeamView,
            "/en/portal/library/": LibraryView,
            "/en/developer/": DeveloperHomeView,
        }
        self.assertEqual(set(views), set(PAGE_AUDIENCES))
        for role in Role:
            request = RequestFactory().get("/")
            request.user = _user_for(role)
            for url, view_class in views.items():
                with self.subTest(role=role, view=view_class.__name__):
                    if role in PAGE_AUDIENCES[url]:
                        self.assertIsNone(check_view_auth(view_class(), request))
                    else:
                        with self.assertRaises(PermissionDenied):
                            check_view_auth(view_class(), request)

    def test_home_sends_each_audience_to_its_page(self):
        expected = {"/en/back-office/": Role.MEDIA_BUYER, "/en/portal/": Role.BROKER_TEAM_MEMBER,
                    "/en/developer/": Role.DEVELOPER_VIEW}
        for url, role in expected.items():
            with self.subTest(role=role):
                self.client.force_login(_user_for(role))
                self.assertRedirects(self.client.get("/en/"), url, fetch_redirect_response=False)

    def test_user_without_a_role_is_refused_with_a_translated_page(self):
        self.client.force_login(make_user("nobody"))
        response = self.client.get("/en/")
        self.assertContains(response, "Access denied", status_code=403)
        self.assertContains(self.client.get("/ar/"), "تم رفض الوصول", status_code=403)

    def test_removing_a_role_takes_effect_on_the_next_request(self):
        buyer = make_user("buyer", Role.MEDIA_BUYER)
        self.client.force_login(buyer)
        self.assertEqual(self.client.get("/en/back-office/").status_code, 200)
        buyer.groups.clear()
        self.assertEqual(self.client.get("/en/back-office/").status_code, 403)

    def test_live_events_recheck_permissions(self):
        from django.conf import settings

        self.assertTrue(settings.LIVEVIEW_CONFIG["reauth_on_event"])


class EveryPageIsGuardedTests(TestCase):
    """A new page can't ship without declaring who may open it."""

    # accept_invitation and confirm_email are opened from an email; the token
    # in the link guards them. register is the public brokerage sign-up.
    PUBLIC = {"login", "logout", "set_language", "accept_invitation", "register", "confirm_email"}

    def test_every_live_page_declares_a_permission(self):
        from django.urls import URLPattern, URLResolver, get_resolver

        def walk(patterns, namespace=None):
            for p in patterns:
                if isinstance(p, URLResolver):
                    yield from walk(p.url_patterns, p.namespace or namespace)
                elif isinstance(p, URLPattern):
                    yield namespace, p

        from django.contrib.auth.mixins import AccessMixin

        unguarded = []
        for namespace, pattern in walk(get_resolver().url_patterns):
            if namespace == "admin":
                continue  # gated by the admin site; see AdminAccessTests
            view_class = getattr(pattern.callback, "view_class", None)
            if view_class is not None and issubclass(view_class, LiveView):
                if not view_class.permission_required:
                    unguarded.append(pattern.name)
            elif view_class is not None and issubclass(view_class, AccessMixin):
                continue  # Django's access mixins, e.g. PrivateFileView (creatives.tests)
            elif pattern.name not in self.PUBLIC | {"home"}:
                unguarded.append(pattern.name)
        self.assertEqual(unguarded, [])


class BackOfficePageTests(TestCase):
    def test_shows_roles_and_capabilities_in_the_page_language(self):
        lead = make_user("lead", Role.MEDIA_BUYER, Role.MEDIA_BUYING_LEAD)
        self.client.force_login(lead)
        html = self.client.get("/en/back-office/").content.decode()
        self.assertIn("Media Buying Lead", html)
        self.assertIn("Assign campaign tasks to media buyers", html)
        self.assertIn("Approve or reject campaign tasks", html)  # inherited from Media Buyer
        self.assertNotIn("Manage wallet and top up funds", html)
        self.assertIn('href="/en/admin/"', html)  # lead manages master data
        arabic = self.client.get("/ar/back-office/").content.decode()
        self.assertIn('dir="rtl"', arabic)
        self.assertIn("إسناد مهام الحملات إلى مشتري الإعلانات", arabic)

    def test_admin_link_only_for_people_with_admin_sections(self):
        self.client.force_login(make_user("buyer", Role.MEDIA_BUYER))
        self.assertNotContains(self.client.get("/en/back-office/"), 'href="/en/admin/"')

    def test_live_render(self):
        from .views import BackOfficeView

        client = LiveViewTestClient(BackOfficeView, user=make_user("finance", Role.FINANCE_MANAGER))
        client.mount()
        html = client.render()
        self.assertIn("Finance Manager", html)
        self.assertIn("Manage FX rates and price overrides", html)


class _PageRequests(RequestFactory):
    """Requests for one page URL (the live mount reads its language from it)."""

    def __init__(self, path, **defaults):
        super().__init__(**defaults)
        self.page_path = path

    def get(self, path, *args, **kwargs):
        return super().get(self.page_path, *args, **kwargs)


def _team_client(user, language="en"):
    """A mounted TeamView, with the office bound the way djust's live mount binds it.

    djust's pre-mount sequence (run_pre_mount_auth) resolves the tenant before
    mount(); LiveViewTestClient skips that step, and its mount() runs no auth
    checks either, so refusals here come from the handlers themselves.
    """
    from .views import TeamView

    class TeamViewWithOffice(TeamView):
        def mount(self, request, **kwargs):
            self._ensure_tenant(request)
            return super().mount(request, **kwargs)

    factory = _PageRequests(f"/{language}/portal/team/")
    return LiveViewTestClient(TeamViewWithOffice, user=user, request_factory=factory).mount()


def _custom_client_role(name, *perm_names):
    """A broker role made in the admin, holding the portal entry and ``perm_names``."""
    from django.contrib.auth.models import Group

    from accounts.models import RoleProfile
    from accounts.roles import AUDIENCE_ENTRY, Audience, permissions_for

    group = Group.objects.create(name=name)
    RoleProfile.objects.create(group=group, name_en=name, audience=Audience.CLIENT)
    group.permissions.set(permissions_for([AUDIENCE_ENTRY[Audience.CLIENT], *perm_names]))
    return group


def _fresh(user):
    from accounts.models import User

    return User.objects.get(pk=user.pk)


class TeamPageTests(TestCase):
    """The office team page (SRS §2.1, §2.2 "Conditional"; ROADMAP M1)."""

    def setUp(self):
        # A request to an /ar/ page leaves Arabic active in the thread; start in
        # English and put back whatever was active before.
        self.enterContext(translation.override("en"))
        self.office = make_office("Nour Realty")
        self.owner = make_user("owner", Role.BROKER_OWNER, office=self.office, first_name="Nour")
        self.member = make_user(
            "sara", Role.BROKER_TEAM_MEMBER, office=self.office, first_name="Sara", last_name="Adel"
        )
        rival_office = make_office("Cairo Homes")
        self.rival = make_user(
            "rival", Role.BROKER_OWNER, office=rival_office, email="rival@cairohomes.test"
        )
        self.rival_member = make_user(
            "omar", Role.BROKER_TEAM_MEMBER, office=rival_office, email="omar@cairohomes.test"
        )

    # Rendering

    def test_lists_only_the_owners_office(self):
        self.client.force_login(self.owner)
        response = self.client.get("/en/portal/team/")
        self.assertContains(response, "sara@example.com")
        self.assertContains(response, 'dj-view="leadshub.views.TeamView"')
        # {% live_form %}, drawn with the project's classes (LIVEVIEW_CONFIG).
        self.assertContains(response, 'name="email" id="id_email" class="input" required="required"')
        self.assertContains(response, '<label for="id_first_name" class="field-label">')
        self.assertNotContains(response, "cairohomes.test")
        html = response.content.decode()
        self.assertNotIn(self.owner.password, html)
        self.assertNotIn("pbkdf2", html)

    def test_actions_show_only_for_people_the_owner_may_manage(self):
        self.client.force_login(self.owner)
        html = self.client.get("/en/portal/team/").content.decode()
        self.assertIn(f'dj-click="deactivate_member" data-member-id="{self.member.pk}"', html)
        self.assertIn(f'dj-click="allow_campaigns" data-member-id="{self.member.pk}"', html)
        self.assertNotIn(f'data-member-id="{self.owner.pk}"', html)

    def test_arabic_page(self):
        self.client.force_login(self.owner)
        html = self.client.get("/ar/portal/team/").content.decode()
        self.assertIn('dir="rtl"', html)
        self.assertIn("دعوة عضو إلى الفريق", html)
        self.assertIn('<span dir="ltr">sara@example.com</span>', html)

    # Inviting

    def test_owner_invites_a_team_member(self):
        from django.core import mail

        from accounts.models import User

        client = _team_client(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            result = client.send_event(
                "submit_form", first_name="Hana", last_name="Fawzy", email="Hana@Example.com"
            )
        self.assertTrue(result["success"], result["error"])
        hana = User.objects.get(email="Hana@example.com")
        self.assertEqual(hana.username, "hana@example.com")
        self.assertEqual(hana.membership.office, self.office)
        self.assertTrue(hana.has_role(Role.BROKER_TEAM_MEMBER))
        self.assertFalse(hana.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["Hana@example.com"])
        self.assertIn("Nour Realty", mail.outbox[0].subject)
        self.assertIn("http://testserver/en/invitation/", mail.outbox[0].body)
        state = client.get_state()
        self.assertEqual(state["success_message"], "Invitation sent to Hana@example.com.")
        self.assertEqual(state["form_data"]["email"], "")  # ready for the next one
        self.assertIn("Hana@example.com", client.render())

    def test_a_failed_invite_clears_the_last_confirmation(self):
        client = _team_client(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            client.send_event("submit_form", first_name="Hana", email="hana@example.com")
            client.send_event("submit_form", first_name="", email="omar@example.com")
        client.assert_state(success_message="")

    def test_invitation_waits_for_the_commit(self):
        from django.core import mail

        client = _team_client(self.owner)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            client.send_event("submit_form", first_name="Hana", email="hana@example.com")
            self.assertEqual(mail.outbox, [])
        self.assertEqual(len(callbacks), 1)

    def test_invite_refuses_an_address_that_has_an_account(self):
        from django.core import mail

        client = _team_client(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            client.send_event("submit_form", first_name="Omar", email="OMAR@cairohomes.test")
        self.assertEqual(
            client.get_state()["field_errors"]["email"],
            ["This email address already has a LeadsHub account."],
        )
        self.assertEqual(mail.outbox, [])

    def test_invite_errors_in_arabic(self):
        client = _team_client(self.owner, language="ar")
        with translation.override("en"):
            client.send_event("submit_form", first_name="", email="sara@example.com")
            html, _patches, _version = client.render_with_patches()
        self.assertIn("هذا الحقل مطلوب.", html)
        self.assertIn("لهذا البريد الإلكتروني حساب في LeadsHub بالفعل.", html)

    def test_new_invitation_for_someone_who_has_not_accepted(self):
        from django.core import mail

        from accounts import services

        with self.captureOnCommitCallbacks(execute=True):
            hana = services.invite_member(
                actor=self.owner, email="hana@example.com", first_name="Hana",
                request=RequestFactory().get("/"),
            )
        client = _team_client(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            result = client.send_event("resend_invitation", member_id=hana.pk)
        self.assertTrue(result["success"], result["error"])
        self.assertEqual(len(mail.outbox), 2)
        client.assert_state(notice="A new invitation was sent to hana@example.com.")

    def test_no_new_invitation_after_accepting(self):
        from django.core import mail

        client = _team_client(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            client.send_event("resend_invitation", member_id=self.member.pk)
        self.assertEqual(mail.outbox, [])
        client.assert_state(
            notice="Sara Adel has already accepted the invitation.", notice_is_error=True
        )

    # Deactivating

    def test_owner_deactivates_and_reactivates_a_member(self):
        client = _team_client(self.owner)
        self.assertTrue(client.send_event("deactivate_member", member_id=self.member.pk)["success"])
        self.assertFalse(_fresh(self.member).is_active)
        client.assert_state(notice="Sara Adel can no longer sign in.")

        self.assertTrue(client.send_event("reactivate_member", member_id=self.member.pk)["success"])
        self.assertTrue(_fresh(self.member).is_active)
        client.assert_state(notice="Sara Adel can sign in again.")

    def test_deactivated_member_is_signed_out_on_the_next_request(self):
        from accounts import services

        self.client.force_login(self.member)
        self.assertEqual(self.client.get("/en/portal/").status_code, 200)
        services.set_member_active(actor=self.owner, member_id=self.member.pk, active=False)
        response = self.client.get("/en/portal/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/en/login/"))

    def test_feedback_in_arabic(self):
        client = _team_client(self.owner, language="ar")
        with translation.override("en"):
            client.send_event("deactivate_member", member_id=self.member.pk)
        client.assert_state(notice="لم يعد بإمكان Sara Adel تسجيل الدخول.")

    # Campaign access (SRS §2.2, "Conditional")

    def test_owner_gives_and_takes_back_campaign_access(self):
        client = _team_client(self.owner)
        self.assertTrue(client.send_event("allow_campaigns", member_id=self.member.pk)["success"])
        # A fresh load of the user, as the next request or live action does (§2.3).
        self.assertTrue(_fresh(self.member).has_perm("accounts.run_campaigns"))
        self.assertFalse(_fresh(self.member).has_perm("accounts.manage_wallet"))
        self.assertIn(
            f'dj-click="revoke_campaigns" data-member-id="{self.member.pk}"', client.render()
        )

        self.assertTrue(client.send_event("revoke_campaigns", member_id=self.member.pk)["success"])
        self.assertFalse(_fresh(self.member).has_perm("accounts.run_campaigns"))

    def test_giving_campaign_access_needs_it(self):
        _custom_client_role("Office Manager", "accounts.manage_team")
        manager = make_user("manager", "Office Manager", office=self.office)
        client = _team_client(manager)
        result = client.send_event("allow_campaigns", member_id=self.member.pk)
        self.assertEqual(result["error"], "Permission denied")
        self.assertFalse(_fresh(self.member).has_perm("accounts.run_campaigns"))
        self.assertNotIn('dj-click="allow_campaigns"', client.render())

    # Refusals (AGENTS.md, "Permission setup", step 8)

    def test_team_member_is_refused_every_handler(self):
        client = _team_client(self.member)
        events = {
            "submit_form": {"first_name": "Hana", "email": "hana@example.com"},
            "resend_invitation": {"member_id": self.member.pk},
            "deactivate_member": {"member_id": self.owner.pk},
            "reactivate_member": {"member_id": self.owner.pk},
            "allow_campaigns": {"member_id": self.member.pk},
            "revoke_campaigns": {"member_id": self.member.pk},
        }
        for event, params in events.items():
            with self.subTest(event=event):
                result = client.send_event(event, **params)
                self.assertEqual(result["error"], "Permission denied")
        self.assertTrue(_fresh(self.owner).is_active)
        self.assertFalse(_fresh(self.member).has_perm("accounts.run_campaigns"))

    def test_another_offices_owner_is_refused(self):
        from django.core import mail

        client = _team_client(self.rival)
        for event in ("deactivate_member", "allow_campaigns", "resend_invitation"):
            with self.subTest(event=event), self.captureOnCommitCallbacks(execute=True):
                result = client.send_event(event, member_id=self.member.pk)
                self.assertFalse(result["success"])
        self.assertTrue(_fresh(self.member).is_active)
        self.assertFalse(_fresh(self.member).has_perm("accounts.run_campaigns"))
        self.assertEqual(mail.outbox, [])

    def test_owners_cannot_change_themselves_or_another_owner(self):
        co_owner = make_user("co", Role.BROKER_OWNER, office=self.office)
        client = _team_client(self.owner)
        for member in (self.owner, co_owner):
            with self.subTest(member=member.username):
                result = client.send_event("deactivate_member", member_id=member.pk)
                self.assertFalse(result["success"])
                self.assertTrue(_fresh(member).is_active)

    def test_a_deactivated_owner_stays_out_of_reach(self):
        co_owner = make_user("co", Role.BROKER_OWNER, office=self.office, is_active=False)
        client = _team_client(self.owner)
        self.assertFalse(client.send_event("reactivate_member", member_id=co_owner.pk)["success"])
        self.assertFalse(_fresh(co_owner).is_active)

    def test_unknown_member_is_refused(self):
        client = _team_client(self.owner)
        self.assertFalse(client.send_event("deactivate_member", member_id=0)["success"])


class NavigationTests(TestCase):
    """Sidebar links to the pages each person may open (ROADMAP M1)."""

    def nav(self, user, path):
        self.client.force_login(user)
        html = self.client.get(path).content.decode()
        start = html.find("<nav aria-label=")
        return html[start:html.find("</nav>", start)] if start != -1 else ""

    def test_owner_sees_dashboard_and_team(self):
        owner = make_user("owner", Role.BROKER_OWNER, office=make_office())
        nav = self.nav(owner, "/en/portal/")
        self.assertIn('href="/en/portal/" dj-navigate="/en/portal/" aria-current="page"', nav)
        self.assertIn('href="/en/portal/team/" dj-navigate="/en/portal/team/"', nav)
        self.assertNotIn("/en/back-office/", nav)

    def test_team_member_has_no_team_link(self):
        member = make_user("sara", Role.BROKER_TEAM_MEMBER, office=make_office())
        nav = self.nav(member, "/en/portal/")
        self.assertIn('href="/en/portal/"', nav)
        self.assertIn('href="/en/portal/library/" dj-navigate="/en/portal/library/"', nav)
        self.assertNotIn("/en/portal/team/", nav)

    def test_internal_staff_see_the_back_office_and_admin_when_they_have_sections(self):
        nav = self.nav(make_user("buyer", Role.MEDIA_BUYER), "/en/back-office/")
        self.assertIn('href="/en/back-office/" dj-navigate="/en/back-office/"', nav)
        self.assertNotIn("/en/admin/", nav)  # no admin sections for a Media Buyer
        self.assertNotIn("/en/portal/", nav)

        nav = self.nav(make_user("lead", Role.MEDIA_BUYING_LEAD), "/en/back-office/")
        self.assertIn('href="/en/admin/"', nav)
        self.assertNotIn('dj-navigate="/en/admin/"', nav)  # not a live page

    def test_developer_sees_the_developer_view(self):
        from catalog.models import Developer

        developer = Developer.objects.create(name_en="Palm Hills", name_ar="بالم هيلز")
        nav = self.nav(make_user("dev", Role.DEVELOPER_VIEW, developer=developer), "/en/developer/")
        self.assertIn('href="/en/developer/"', nav)
        self.assertNotIn("/en/portal/", nav)

    def test_labels_in_arabic(self):
        owner = make_user("owner", Role.BROKER_OWNER, office=make_office())
        nav = self.nav(owner, "/ar/portal/")
        self.assertIn(">لوحة التحكم</a>", nav)
        self.assertIn(">الفريق</a>", nav)

    def test_no_navigation_when_signed_out(self):
        html = self.client.get("/en/login/").content.decode()
        self.assertNotIn('<nav aria-label="Main"', html)
        self.assertNotIn('id="sidebar"', html)
        self.assertNotIn("data-sidebar-open", html)
        self.assertNotIn('class="lg:ps-64"', html)

    def test_links_sit_in_a_sidebar_opened_from_the_header_on_small_screens(self):
        from leadshub.context_processors import ICONS

        owner = make_user("owner", Role.BROKER_OWNER, office=make_office())
        self.client.force_login(owner)
        html = self.client.get("/en/portal/").content.decode()
        sidebar = html[html.find('<aside id="sidebar"'):html.find("</aside>")]
        self.assertIn('<nav aria-label="Main"', sidebar)
        self.assertIn(f'<path d="{ICONS["team"]}"/>', sidebar)
        self.assertIn('<div class="lg:ps-64">', html)
        self.assertIn('data-sidebar-open aria-controls="sidebar" aria-expanded="false"', html)
        self.assertIn('aria-label="Open menu"', html)
        self.assertIn('aria-label="Close menu"', sidebar)
        self.assertIn("js/sidebar.js", html)
        # The name and Sign out sit at the sidebar's foot, not in the header.
        self.assertIn(">Sign out</button>", sidebar)
        self.assertEqual(html.count(">Sign out</button>"), 1)

    def test_menu_buttons_in_arabic(self):
        owner = make_user("owner", Role.BROKER_OWNER, office=make_office())
        self.client.force_login(owner)
        html = self.client.get("/ar/portal/").content.decode()
        self.assertIn('aria-label="فتح القائمة"', html)
        self.assertIn('aria-label="إغلاق القائمة"', html)

    def test_language_switch_follows_live_navigation(self):
        owner = make_user("owner", Role.BROKER_OWNER, office=make_office())
        self.client.force_login(owner)
        html = self.client.get("/en/portal/").content.decode()
        self.assertIn("this.form.elements.next.value = location.pathname + location.search", html)


class DashboardTeamTests(TestCase):
    def setUp(self):
        self.office = make_office()
        self.owner = make_user("owner", Role.BROKER_OWNER, office=self.office)

    def test_lists_active_members_only(self):
        make_user("gone", Role.BROKER_TEAM_MEMBER, office=self.office, is_active=False)
        self.client.force_login(self.owner)
        response = self.client.get("/en/portal/")
        self.assertContains(response, "owner@example.com")
        self.assertNotContains(response, "gone@example.com")

    def test_team_link_for_team_managers_only(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/en/portal/"), "Manage team")
        member = make_user("sara", Role.BROKER_TEAM_MEMBER, office=self.office)
        self.client.force_login(member)
        self.assertNotContains(self.client.get("/en/portal/"), "Manage team")


class TeamPageOverTheSocketTests(TransactionTestCase):
    """The team page through djust's real WebSocket consumer (ROADMAP M1).

    LiveViewTestClient skips the consumer, which binds the office, checks the
    page's permissions at mount, re-checks the person on every event
    (reauth_on_event) and refuses handlers they may not call. A
    TransactionTestCase, because the consumer reads the database from other
    threads.
    """

    def setUp(self):
        office = make_office("Nour Realty")
        self.owner = make_user("owner", Role.BROKER_OWNER, office=office, first_name="Nour")
        self.member = make_user(
            "sara", Role.BROKER_TEAM_MEMBER, office=office, first_name="Sara", last_name="Adel"
        )

    def socket_for(self, user):
        """A WebSocket to the app, signed in as ``user`` with a real session."""
        from channels.testing import WebsocketCommunicator
        from django.test import Client

        from .asgi import application

        web = Client()
        web.force_login(user)
        cookie = f"sessionid={web.cookies['sessionid'].value}".encode()
        headers = [(b"cookie", cookie), (b"origin", b"http://testserver"), (b"host", b"testserver")]
        return WebsocketCommunicator(application, "/ws/live/", headers=headers)

    async def open_team_page(self, user, url="/en/portal/team/"):
        from asgiref.sync import sync_to_async

        socket = await sync_to_async(self.socket_for)(user)
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        self.assertEqual((await socket.receive_json_from())["type"], "connect")
        await socket.send_json_to({"type": "mount", "view": "leadshub.views.TeamView", "url": url})
        return socket, await socket.receive_json_from(timeout=5)

    async def send(self, socket, event, **params):
        await socket.send_json_to({"type": "event", "event": event, "params": params})
        return await socket.receive_json_from(timeout=5)

    async def test_owner_deactivates_a_member_on_the_arabic_page(self):
        import json

        from accounts.models import User

        socket, reply = await self.open_team_page(self.owner, "/ar/portal/team/")
        self.assertEqual(reply["type"], "mount")
        self.assertIn('name="email"', reply["html"])
        self.assertIn("دعوة عضو إلى الفريق", reply["html"])

        patch = await self.send(socket, "deactivate_member", member_id=self.member.pk)
        self.assertEqual(patch["type"], "patch")
        self.assertIn("لم يعد بإمكان Sara Adel تسجيل الدخول.", json.dumps(patch, ensure_ascii=False))
        self.assertFalse((await User.objects.aget(pk=self.member.pk)).is_active)
        await socket.disconnect()

    async def test_owner_invites_over_the_socket(self):
        from django.core import mail

        from accounts.models import User

        socket, _reply = await self.open_team_page(self.owner)
        patch = await self.send(
            socket, "submit_form", first_name="Hana", last_name="", email="hana@example.com"
        )
        self.assertEqual(patch["type"], "patch")
        self.assertTrue(await User.objects.filter(email="hana@example.com").aexists())
        self.assertEqual([m.to for m in mail.outbox], [["hana@example.com"]])
        self.assertIn("http://testserver/en/invitation/", mail.outbox[0].body)
        await socket.disconnect()

    async def test_a_deactivated_persons_next_action_is_refused(self):
        """SRS §2.3: the action is refused and the server points to the sign-in page.

        djust 1.2.8's WebSocket client doesn't follow the navigate frame yet
        (ROADMAP M1, known issues); this checks what the server sends.
        """
        from accounts.models import User

        socket, _reply = await self.open_team_page(self.owner, "/ar/portal/team/")
        # Staff deactivate the owner in the admin while the page is open.
        await User.objects.filter(pk=self.owner.pk).aupdate(is_active=False)
        answer = await self.send(socket, "deactivate_member", member_id=self.member.pk)
        self.assertEqual(answer, {"type": "navigate", "to": "/ar/login/"})
        self.assertTrue((await User.objects.aget(pk=self.member.pk)).is_active)

    async def test_a_signed_out_mount_points_to_sign_in(self):
        """Used to fail with a server error: settings.LOGIN_URL is lazy (leadshub.live)."""
        from channels.testing import WebsocketCommunicator

        from .asgi import application

        headers = [(b"origin", b"http://testserver"), (b"host", b"testserver")]
        socket = WebsocketCommunicator(application, "/ws/live/", headers=headers)
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        await socket.receive_json_from()
        await socket.send_json_to(
            {"type": "mount", "view": "leadshub.views.TeamView", "url": "/ar/portal/team/"}
        )
        self.assertEqual(
            await socket.receive_json_from(timeout=5), {"type": "navigate", "to": "/ar/login/"}
        )
        await socket.disconnect()

    async def test_team_member_is_refused_at_mount(self):
        socket, reply = await self.open_team_page(self.member)
        self.assertEqual(reply, {"type": "error", "error": "Permission denied"})
        self.assertEqual((await socket.receive_output(timeout=2))["code"], 4403)

    async def test_losing_the_capability_stops_the_next_event(self):
        """SRS §2.3: taking a role away takes effect on the next live action."""
        from asgiref.sync import sync_to_async

        from accounts.models import User

        socket, reply = await self.open_team_page(self.owner)
        self.assertEqual(reply["type"], "mount")
        await sync_to_async(self.owner.groups.clear)()
        await socket.send_json_to(
            {"type": "event", "event": "deactivate_member", "params": {"member_id": self.member.pk}}
        )
        # The event is refused and the socket closed; nothing is patched.
        output = await socket.receive_output(timeout=5)
        while output["type"] == "websocket.send":
            self.assertNotIn('"type": "patch"', output.get("text", ""))
            output = await socket.receive_output(timeout=5)
        self.assertEqual(output, {"type": "websocket.close", "code": 4403})
        self.assertTrue((await User.objects.aget(pk=self.member.pk)).is_active)



class LibraryPageTests(TemporaryUploadsMixin, TestCase):
    """The creative library brokers browse (SRS §3.2, FR-5.7; ROADMAP M2)."""

    def setUp(self):
        import datetime

        from django.utils import timezone

        from creatives.models import Language, Status
        from creatives.test_utils import make_creative, make_project

        self.enterContext(translation.override("en"))
        self.member = make_user("sara", Role.BROKER_TEAM_MEMBER, office=make_office())
        self.phnc = make_project()
        self.o_west = make_project("O West", area="6th of October", developer="Orascom")
        today = timezone.localdate()
        self.current = make_creative(
            "Launch offer A1", project=self.phnc, offer="5% down payment", offer_expires_on=today
        )
        self.arabic = make_creative("شقق أو ويست", project=self.o_west, language=Language.ARABIC)
        make_creative("Ended offer B2", project=self.phnc, offer_expires_on=today - datetime.timedelta(days=1))
        make_creative("Draft offer C3", project=self.phnc, status=Status.DRAFT)

    def page(self, query="", lang="en"):
        self.client.force_login(self.member)
        response = self.client.get(f"/{lang}/portal/library/{query}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def live(self):
        from .views import LibraryView

        factory = _PageRequests("/en/portal/library/")
        return LiveViewTestClient(LibraryView, user=self.member, request_factory=factory).mount()

    def test_brokers_see_only_assets_they_can_use(self):
        html = self.page()
        self.assertIn("Launch offer A1", html)  # ends today: still on
        self.assertIn("شقق أو ويست", html)
        self.assertNotIn("Ended offer B2", html)
        self.assertNotIn("Draft offer C3", html)
        self.assertIn(f'src="{self.current.file.url}"', html)
        self.assertIn('dj-view="leadshub.views.LibraryView"', html)

    def test_filters_come_from_the_url(self):
        html = self.page(f"?area={self.o_west.area_id}")
        self.assertIn("شقق أو ويست", html)
        self.assertNotIn("Launch offer A1", html)
        self.assertIn("No assets match these filters.", self.page("?type=reel"))
        html = self.page("?area=abc&type=nonsense&language=xx&page=-3")  # ignored
        self.assertIn("Launch offer A1", html)
        self.assertIn("شقق أو ويست", html)

    def test_changing_a_filter_updates_the_list_and_the_url(self):
        client = self.live()
        client.send_event("set_filter", field="area", value=str(self.o_west.area_id))
        client.assert_state(area=self.o_west.area_id)
        client.assert_patch(params={"area": str(self.o_west.area_id)})
        html = client.render()
        self.assertIn("شقق أو ويست", html)
        self.assertNotIn("Launch offer A1", html)

    def test_choosing_another_area_clears_the_project(self):
        client = self.live()
        client.send_event("set_filter", field="project", value=str(self.phnc.pk))
        client.send_event("set_filter", field="area", value=str(self.o_west.area_id))
        client.assert_state(project=0, area=self.o_west.area_id)

    def test_unknown_filters_change_nothing(self):
        client = self.live()
        client.send_event("set_filter", field="status", value="draft")
        self.assertNotIn("Draft offer C3", client.render())

    def test_pages(self):
        from unittest import mock

        from .views import LibraryView

        with mock.patch.object(LibraryView, "page_size", 1):
            first = self.page()
            self.assertIn('dj-patch="?page=2"', first)
            second = self.page("?language=en&page=1")
            self.assertNotIn("Next", second)  # one English asset
            second = self.page("?page=2")
            # dj-patch only overwrites parameters, so the way back states page=1.
            self.assertIn('dj-patch="?page=1"', second)
            self.assertIn("Page 2 of 2", second)

            client = self.live()
            client.view_instance.handle_params({"page": "2"}, "/en/portal/library/?page=2")
            client.assert_state(page=2)
            client.view_instance.handle_params({"page": "9"}, "/en/portal/library/?page=9")
            client.assert_state(page=2)  # past the end: the last page

    def test_clearing_filters(self):
        client = self.live()
        client.send_event("set_filter", field="type", value="reel")
        client.send_event("clear_filters")
        client.assert_state(asset_type="", area=0, project=0, language="", page=1)
        self.assertEqual(client.view_instance._pending_navigation[-1]["params"], {})

    def test_arabic_page(self):
        html = self.page(lang="ar")
        self.assertIn('dir="rtl"', html)
        self.assertIn("مكتبة المواد الإعلانية", html)
        self.assertIn("صورة ثابتة", html)  # the asset type, translated


class TestEmailTests(TestCase):
    """Email in tests stays in memory, even with Mailtrap or SMTP in .env."""

    def test_tests_never_send_through_the_configured_server(self):
        from django.conf import settings

        self.assertEqual(
            settings.EMAIL_BACKEND, "django.core.mail.backends.locmem.EmailBackend"
        )

    def test_sendtestemail_lands_in_the_outbox(self):
        from django.core import mail
        from django.core.management import call_command

        call_command("sendtestemail", "someone@example.com")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["someone@example.com"])


class EmailLayoutTests(TestCase):
    """Every email uses the LeadsHub layout (leadshub/emails.py), as HTML with a
    plain-text copy, in the reader's language."""

    CONTEXT = {
        "first_name": "Mona",
        "office_name": "Smith & Sons <script>",
        "link": "https://leadshub.test/en/register/confirm/abc/def/",
        "valid_days": 3,
    }

    def setUp(self):
        self.enterContext(translation.override("en"))

    def send(self, name="accounts/email/confirm_email", context=None, language=None):
        from django.core import mail

        from .emails import send_after_commit

        with self.captureOnCommitCallbacks(execute=True):
            send_after_commit(name, context or self.CONTEXT, ["mona@example.com"], language=language)
        return mail.outbox[-1]

    def html(self, message):
        [(html, mimetype)] = message.alternatives
        self.assertEqual(mimetype, "text/html")
        return html

    def test_html_and_text_share_the_layout(self):
        message = self.send()
        html = self.html(message)
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn('lang="en" dir="ltr"', html)
        self.assertIn(">LeadsHub</td>", html)  # the brand header
        self.assertIn(f'<a href="{self.CONTEXT["link"]}" target="_blank"', html)  # the button
        self.assertIn("Confirm email address</a>", html)
        self.assertIn("If the button doesn't work", html)
        self.assertIn("The LeadsHub team", html)
        self.assertIn("Please don't reply to it.", html)
        # The plain-text copy has the link and the same sign-off, unescaped.
        self.assertIn(self.CONTEXT["link"], message.body)
        self.assertIn("Smith & Sons <script>", message.body)
        self.assertTrue(message.body.rstrip().endswith("This email was sent automatically. Please don't reply to it."))

    def test_names_are_escaped_in_the_html_but_not_in_subjects(self):
        message = self.send(
            "accounts/email/new_signup",
            {**self.CONTEXT, "applicant_name": "Mona", "applicant_email": "m@x.test", "phone_number": "0101",
             "details": [("Applicant", "<b>Mona</b>")]},
        )
        html = self.html(message)
        self.assertNotIn("<script>", html)
        self.assertIn("Smith &amp; Sons &lt;script&gt;", html)
        self.assertIn("&lt;b&gt;Mona&lt;/b&gt;", html)
        self.assertEqual(message.subject, "New brokerage to review: Smith & Sons <script>")

    def test_arabic_emails_run_right_to_left(self):
        message = self.send(language="ar")
        html = self.html(message)
        self.assertIn('lang="ar" dir="rtl"', html)
        self.assertIn("text-align:right", html)
        self.assertIn("فريق LeadsHub", html)
        self.assertIn("فريق LeadsHub", message.body)
        self.assertEqual(translation.get_language(), "en")  # the caller's language is restored

    def test_nothing_is_sent_before_the_transaction_commits(self):
        from django.core import mail

        from .emails import send_after_commit

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            send_after_commit("accounts/email/confirm_email", self.CONTEXT, ["mona@example.com"])
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(mail.outbox, [])

    def test_every_account_email_renders_in_both_languages(self):
        from .emails import render_email

        context = {
            **self.CONTEXT,
            "inviter_name": "Omar",
            "sign_in_name": "mona@example.com",
            "applicant_name": "Mona Said",
            "applicant_email": "mona@example.com",
            "phone_number": "01012345678",
            "details": [("Sign-in name", "mona@example.com")],
        }
        for name in ("confirm_email", "invitation", "new_signup", "signup_approved", "signup_rejected"):
            for language in ("en", "ar"):
                with self.subTest(name=name, language=language):
                    subject, text, html = render_email(f"accounts/email/{name}", context, language=language)
                    self.assertTrue(subject)
                    self.assertNotIn("\n", subject)
                    self.assertIn("Smith & Sons", subject + text)
                    self.assertIn("Smith &amp; Sons", html)
                    self.assertIn(f'dir="{"rtl" if language == "ar" else "ltr"}"', html)
                    if name != "signup_rejected":  # the only email without a link
                        self.assertIn(f'href="{self.CONTEXT["link"]}"', html)
                        self.assertIn(self.CONTEXT["link"], text)


class DeploymentSettingsTests(TestCase):
    """Settings read what Fly sets: DATABASE_URL and Tigris' storage names."""

    def test_database_url_becomes_django_settings(self):
        from leadshub.settings import _database_from_url

        settings = _database_from_url(
            "postgres://app%40user:p%2Fss@db.flycast:5433/leadshub?sslmode=disable"
        )

        self.assertEqual(
            settings,
            {
                "NAME": "leadshub",
                "USER": "app@user",
                "PASSWORD": "p/ss",
                "HOST": "db.flycast",
                "PORT": "5433",
                "OPTIONS": {"sslmode": "disable"},
            },
        )

    def test_database_url_defaults_to_the_postgres_port(self):
        from leadshub.settings import _database_from_url

        self.assertEqual(_database_from_url("postgresql://u:p@host/db")["PORT"], "5432")

    def test_database_url_must_be_postgres(self):
        from leadshub.settings import _database_from_url

        with self.assertRaises(ValueError):
            _database_from_url("mysql://u:p@host/db")

    def test_first_env_takes_the_first_name_set(self):
        from unittest import mock

        from leadshub.settings import _first_env

        with mock.patch.dict("os.environ", {"AWS_STORAGE_BUCKET_NAME": "", "BUCKET_NAME": "tigris"}):
            self.assertEqual(_first_env("AWS_STORAGE_BUCKET_NAME", "BUCKET_NAME"), "tigris")
        with mock.patch.dict("os.environ", {"AWS_STORAGE_BUCKET_NAME": "own", "BUCKET_NAME": "tigris"}):
            self.assertEqual(_first_env("AWS_STORAGE_BUCKET_NAME", "BUCKET_NAME"), "own")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(_first_env("AWS_STORAGE_BUCKET_NAME", "BUCKET_NAME"))

    def test_production_refuses_to_start_without_a_secret_key(self):
        import subprocess
        import sys

        from django.conf import settings

        env = {"PATH": "/usr/bin:/bin", "DEBUG": "False", "SECRET_KEY": ""}
        result = subprocess.run(
            [sys.executable, "-c", "import leadshub.settings"],
            cwd=settings.BASE_DIR, env=env, capture_output=True, text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set SECRET_KEY", result.stderr)


class SharedStateSettingsTests(TestCase):
    """REDIS_URL moves djust's page state and the channel layer to Redis."""

    def settings_with(self, **env):
        import json
        import os
        import subprocess
        import sys

        from django.conf import settings

        script = (
            "import json, leadshub.settings as s; "
            "print(json.dumps({'layer': s.CHANNEL_LAYERS['default'], 'djust': s.DJUST_CONFIG}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=settings.BASE_DIR, env={**os.environ, "DEBUG": "True", **env},
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    def test_without_redis_both_stay_in_memory(self):
        found = self.settings_with(REDIS_URL="")

        self.assertEqual(found["layer"], {"BACKEND": "channels.layers.InMemoryChannelLayer"})
        self.assertNotIn("STATE_BACKEND", found["djust"])

    def test_with_redis_both_use_it(self):
        found = self.settings_with(REDIS_URL="redis://cache.internal:6379/0")

        self.assertEqual(
            found["layer"],
            {
                "BACKEND": "channels_redis.pubsub.RedisPubSubChannelLayer",
                "CONFIG": {"hosts": ["redis://cache.internal:6379/0"]},
            },
        )
        self.assertEqual(found["djust"]["STATE_BACKEND"], "redis")
        self.assertEqual(found["djust"]["REDIS_URL"], "redis://cache.internal:6379/0")


class PooledDatabaseSettingsTests(TestCase):
    """DB_TRANSACTION_POOLING turns off what a transaction pool can't carry."""

    def database_with(self, **env):
        import json
        import os
        import subprocess
        import sys

        from django.conf import settings

        script = "import json, leadshub.settings as s; print(json.dumps(s.DATABASES['default']))"
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=settings.BASE_DIR, env={**os.environ, "DEBUG": "True", **env},
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    def test_pooled_database_has_no_prepared_statements_or_server_cursors(self):
        found = self.database_with(
            DB_TRANSACTION_POOLING="True",
            DATABASE_URL="postgresql://u:p@pgbouncer.example.internal/leadshub",
        )

        self.assertTrue(found["DISABLE_SERVER_SIDE_CURSORS"])
        self.assertIsNone(found["OPTIONS"]["prepare_threshold"])
        self.assertEqual(found["HOST"], "pgbouncer.example.internal")

    def test_direct_database_keeps_the_defaults(self):
        found = self.database_with(DB_TRANSACTION_POOLING="")

        self.assertNotIn("DISABLE_SERVER_SIDE_CURSORS", found)
        self.assertNotIn("prepare_threshold", found.get("OPTIONS", {}))
