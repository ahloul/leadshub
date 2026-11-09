from django.test import RequestFactory, TestCase
from django.utils import translation
from djust import LiveView
from djust.testing import LiveViewTestClient

from accounts.roles import Role
from accounts.test_utils import make_office, make_user

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

        from .views import BackOfficeView, DashboardView, DeveloperHomeView

        views = {
            "/en/back-office/": BackOfficeView,
            "/en/portal/": DashboardView,
            "/en/developer/": DeveloperHomeView,
        }
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

    PUBLIC = {"login", "logout", "set_language"}

    def test_every_live_page_declares_a_permission(self):
        from django.urls import URLPattern, URLResolver, get_resolver

        def walk(patterns, namespace=None):
            for p in patterns:
                if isinstance(p, URLResolver):
                    yield from walk(p.url_patterns, p.namespace or namespace)
                elif isinstance(p, URLPattern):
                    yield namespace, p

        unguarded = []
        for namespace, pattern in walk(get_resolver().url_patterns):
            if namespace == "admin":
                continue  # gated by the admin site; see AdminAccessTests
            view_class = getattr(pattern.callback, "view_class", None)
            if view_class is not None and issubclass(view_class, LiveView):
                if not view_class.permission_required:
                    unguarded.append(pattern.name)
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
