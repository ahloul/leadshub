from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.utils import translation

from .models import Membership, RoleProfile, User
from .roles import Role, role_errors, role_permissions, sync_role_groups
from .tenancy import resolve_office
from .test_utils import make_office, make_user


class RoleMatrixTests(TestCase):
    """SRS §2.2 permissions matrix."""

    def perms(self, role):
        group = Group.objects.get(name=role)
        return {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all()}

    def test_every_role_group_exists_with_exactly_its_permissions(self):
        for role in Role:
            with self.subTest(role=role):
                self.assertEqual(self.perms(role), role_permissions(role))
                profile = Group.objects.get(name=role).role_profile
                self.assertTrue(profile.is_system)

    def test_essential_roles_only_use_permissions_allowed_for_their_audience(self):
        from .roles import AUDIENCE_ENTRY, AUDIENCE_PERMISSIONS, SYSTEM_ROLE_AUDIENCE

        for role in Role:
            with self.subTest(role=role):
                audience = SYSTEM_ROLE_AUDIENCE[role]
                own = role_permissions(role) - {AUDIENCE_ENTRY[audience]}
                self.assertLessEqual(own, AUDIENCE_PERMISSIONS[audience])

    def test_essential_roles_have_arabic_names(self):
        profile = Group.objects.get(name=Role.MEDIA_BUYER).role_profile
        self.assertEqual(profile.name_ar, "مشتري الوسائط")
        with translation.override("ar"):
            self.assertEqual(profile.display_name, "مشتري الوسائط")

    def test_matrix_rows(self):
        allowed = {
            "accounts.view_internal_financials": {
                Role.PLATFORM_ADMIN, Role.MEDIA_BUYING_LEAD, Role.MEDIA_BUYER, Role.FINANCE_MANAGER,
            },
            "accounts.review_campaign_tasks": {
                Role.PLATFORM_ADMIN, Role.MEDIA_BUYING_LEAD, Role.MEDIA_BUYER,
            },
            "accounts.manage_master_data": {Role.PLATFORM_ADMIN, Role.MEDIA_BUYING_LEAD},
            "accounts.manage_fx_and_pricing": {Role.PLATFORM_ADMIN, Role.FINANCE_MANAGER},
            "accounts.run_campaigns": {Role.PLATFORM_ADMIN, Role.BROKER_OWNER},
            "accounts.manage_wallet": {Role.PLATFORM_ADMIN, Role.FINANCE_MANAGER, Role.BROKER_OWNER},
            "accounts.work_leads": {Role.BROKER_OWNER, Role.BROKER_TEAM_MEMBER},
            "accounts.view_project_metrics": {
                Role.PLATFORM_ADMIN, Role.MEDIA_BUYING_LEAD, Role.MEDIA_BUYER,
                Role.FINANCE_MANAGER, Role.DEVELOPER_VIEW,
            },
        }
        for perm, roles in allowed.items():
            for role in Role:
                with self.subTest(perm=perm, role=role):
                    self.assertEqual(perm in self.perms(role), role in roles)

    def test_internal_financials_never_reach_client_roles(self):
        for role in (Role.BROKER_OWNER, Role.BROKER_TEAM_MEMBER, Role.DEVELOPER_VIEW):
            user = make_user(f"u-{role.name}", role)
            self.assertFalse(user.has_perm("accounts.view_internal_financials"))

    def test_team_member_can_run_campaigns_only_when_granted(self):
        member = make_user("team", Role.BROKER_TEAM_MEMBER)
        self.assertFalse(member.has_perm("accounts.run_campaigns"))

        member.user_permissions.add(
            Permission.objects.get(content_type__app_label="accounts", codename="run_campaigns")
        )
        member = User.objects.get(pk=member.pk)  # fresh permission cache
        self.assertTrue(member.has_perm("accounts.run_campaigns"))
        self.assertFalse(member.has_perm("accounts.manage_wallet"))

    def test_migrate_keeps_permissions_edited_in_the_admin(self):
        group = Group.objects.get(name=Role.BROKER_TEAM_MEMBER)
        group.permissions.add(_perm("accounts.manage_team"))
        group.permissions.remove(_perm("accounts.work_leads"))
        sync_role_groups()
        self.assertEqual(
            self.perms(Role.BROKER_TEAM_MEMBER),
            {"accounts.manage_team", "accounts.access_client_portal"},
        )

    def test_migrate_restores_what_a_role_must_keep(self):
        group = Group.objects.get(name=Role.PLATFORM_ADMIN)
        group.permissions.remove(
            _perm("accounts.access_back_office"), _perm("accounts.change_roleprofile")
        )
        sync_role_groups()
        self.assertIn("accounts.access_back_office", self.perms(Role.PLATFORM_ADMIN))
        self.assertIn("accounts.change_roleprofile", self.perms(Role.PLATFORM_ADMIN))

    def test_migrate_recreates_a_missing_role_with_defaults(self):
        Group.objects.filter(name=Role.CONTENT_MANAGER).delete()
        sync_role_groups()
        self.assertEqual(self.perms(Role.CONTENT_MANAGER), role_permissions(Role.CONTENT_MANAGER))

    def test_reset_command_restores_defaults(self):
        from io import StringIO

        from django.core.management import CommandError, call_command

        group = Group.objects.get(name=Role.MEDIA_BUYER)
        group.permissions.remove(_perm("accounts.review_campaign_tasks"))
        Group.objects.get(name=Role.FINANCE_MANAGER).permissions.add(_perm("accounts.run_campaigns"))

        call_command("reset_role_permissions", "Media Buyer", stdout=StringIO())
        self.assertEqual(self.perms(Role.MEDIA_BUYER), role_permissions(Role.MEDIA_BUYER))
        self.assertIn("accounts.run_campaigns", self.perms(Role.FINANCE_MANAGER))  # untouched

        call_command("reset_role_permissions", stdout=StringIO())
        self.assertEqual(self.perms(Role.FINANCE_MANAGER), role_permissions(Role.FINANCE_MANAGER))
        with self.assertRaises(CommandError):
            call_command("reset_role_permissions", "Nobody", stdout=StringIO())

    def test_role_helpers(self):
        buyer = make_user("buyer", Role.MEDIA_BUYER)
        owner = make_user("owner", Role.BROKER_OWNER)
        self.assertTrue(buyer.has_role(Role.MEDIA_BUYER))
        self.assertTrue(buyer.is_internal_staff)
        self.assertFalse(owner.is_internal_staff)


class AudienceTests(TestCase):
    """Internal, broker and developer users each have their own entry capability."""

    ENTRY = {
        "accounts.access_back_office": {
            Role.PLATFORM_ADMIN, Role.MEDIA_BUYING_LEAD, Role.MEDIA_BUYER,
            Role.CONTENT_MANAGER, Role.FINANCE_MANAGER,
        },
        "accounts.access_client_portal": {Role.BROKER_OWNER, Role.BROKER_TEAM_MEMBER},
        "accounts.access_developer_view": {Role.DEVELOPER_VIEW},
    }

    def test_every_role_opens_exactly_one_audience(self):
        for role in Role:
            with self.subTest(role=role):
                entries = {perm for perm, roles in self.ENTRY.items() if role in roles}
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries & role_permissions(role), entries)
                self.assertEqual(set(self.ENTRY) & role_permissions(role), entries)

    def test_every_internal_role_has_capabilities_beyond_entry(self):
        for role in self.ENTRY["accounts.access_back_office"]:
            with self.subTest(role=role):
                self.assertGreater(len(role_permissions(role)), 1)

    def test_mixed_audiences_are_refused(self):
        self.assertTrue(role_errors([Role.MEDIA_BUYER, Role.BROKER_OWNER]))
        self.assertTrue(role_errors([Role.FINANCE_MANAGER, Role.DEVELOPER_VIEW], developer=object()))
        # Several roles from one audience are fine.
        self.assertEqual(role_errors([Role.BROKER_OWNER, Role.BROKER_TEAM_MEMBER]), [])
        self.assertEqual(role_errors([Role.MEDIA_BUYER, Role.MEDIA_BUYING_LEAD]), [])
        self.assertEqual(role_errors([]), [])

    def test_developer_view_needs_its_developer(self):
        self.assertTrue(role_errors([Role.DEVELOPER_VIEW]))
        self.assertEqual(role_errors([Role.DEVELOPER_VIEW], developer=object()), [])
        self.assertTrue(role_errors([Role.MEDIA_BUYER], developer=object()))


class TeamLeaderTests(TestCase):
    """A Media Buyer becomes team leader by adding the Media Buying Lead role."""

    def test_lead_has_every_media_buyer_capability(self):
        self.assertLessEqual(
            role_permissions(Role.MEDIA_BUYER), role_permissions(Role.MEDIA_BUYING_LEAD)
        )

    def test_promoting_a_media_buyer(self):
        buyer = make_user("buyer", Role.MEDIA_BUYER)
        self.assertFalse(buyer.has_perm("accounts.assign_campaign_tasks"))
        self.assertFalse(buyer.has_perm("accounts.manage_master_data"))

        buyer.groups.add(Group.objects.get(name=Role.MEDIA_BUYING_LEAD))
        leader = User.objects.get(pk=buyer.pk)  # fresh permission cache
        self.assertTrue(leader.has_perm("accounts.assign_campaign_tasks"))
        self.assertTrue(leader.has_perm("accounts.manage_master_data"))
        self.assertTrue(leader.has_perm("accounts.review_campaign_tasks"))
        self.assertFalse(leader.has_perm("accounts.manage_wallet"))

    def test_only_leads_and_admins_assign_tasks(self):
        allowed = {r for r in Role if "accounts.assign_campaign_tasks" in role_permissions(r)}
        self.assertEqual(allowed, {Role.MEDIA_BUYING_LEAD, Role.PLATFORM_ADMIN})


class AdminAccessTests(TestCase):
    """The admin opens by back-office role, not by the is_staff flag."""

    def test_internal_role_opens_admin_without_is_staff(self):
        self.client.force_login(make_user("buyer", Role.MEDIA_BUYER))
        self.assertEqual(self.client.get("/en/admin/").status_code, 200)

    def test_broker_with_is_staff_is_refused(self):
        owner = make_user("owner", Role.BROKER_OWNER, office=make_office(), is_staff=True)
        self.client.force_login(owner)
        response = self.client.get("/en/admin/", follow=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/en/admin/catalog/project/", follow=True).status_code, 403)

    def test_anonymous_goes_to_the_app_login(self):
        response = self.client.get("/en/admin/", follow=True)
        self.assertEqual(response.redirect_chain[-1][0], "/en/login/?next=/en/admin/")

    def test_admin_sections_follow_model_permissions(self):
        self.client.force_login(make_user("finance", Role.FINANCE_MANAGER))
        self.assertEqual(self.client.get("/en/admin/catalog/project/").status_code, 403)
        self.client.force_login(make_user("lead", Role.MEDIA_BUYING_LEAD))
        self.assertEqual(self.client.get("/en/admin/catalog/project/").status_code, 200)

    def test_user_form_refuses_mixed_roles(self):
        from .admin import RoleCheckedUserChangeForm

        user = make_user("mixed")

        def form_for(*roles):
            groups = Group.objects.filter(name__in=roles)
            return RoleCheckedUserChangeForm(
                instance=user,
                data={
                    "username": "mixed", "email": "mixed@example.com", "is_active": "on",
                    "groups": [g.pk for g in groups], "date_joined": "2026-01-01 00:00:00",
                },
            )

        form = form_for(Role.MEDIA_BUYER, Role.BROKER_OWNER)
        self.assertFalse(form.is_valid())
        self.assertIn("must all be internal", " ".join(form.non_field_errors()))
        self.assertTrue(form_for(Role.MEDIA_BUYER, Role.MEDIA_BUYING_LEAD).is_valid())

    def test_bare_groups_are_not_editable_in_admin(self):
        """Roles are edited as RoleProfiles; a bare group would have no audience."""
        admin_user = User.objects.create_superuser("root", "root@example.com", "pass-12345")
        self.client.force_login(admin_user)
        self.assertEqual(self.client.get("/en/admin/auth/group/add/").status_code, 404)


def _perm(name):
    app_label, codename = name.split(".")
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)


class CustomRoleTests(TestCase):
    """Roles are dynamic: essential roles are locked, new ones are made in the admin."""

    def setUp(self):
        self.platform = make_user("platform", Role.PLATFORM_ADMIN)
        self.client.force_login(self.platform)

    def add_role(self, **data):
        data.setdefault("name_ar", "")
        data["permissions"] = [_perm(n).pk for n in data.get("permissions", [])]
        return self.client.post("/en/admin/accounts/roleprofile/add/", data)

    def test_platform_admin_creates_an_internal_role(self):
        response = self.add_role(
            name_en="Ad Account Auditor", name_ar="مدقق الحسابات الإعلانية",
            audience="internal", permissions=["accounts.view_internal_financials"],
        )
        self.assertEqual(response.status_code, 302)
        role = RoleProfile.objects.get(name_en="Ad Account Auditor")
        self.assertFalse(role.is_system)
        self.assertEqual(role.group.name, "Ad Account Auditor")
        perms = {f"{p.content_type.app_label}.{p.codename}" for p in role.group.permissions.all()}
        # The audience entry is added automatically.
        self.assertEqual(
            perms, {"accounts.view_internal_financials", "accounts.access_back_office"}
        )

        auditor = make_user("auditor")
        auditor.groups.add(role.group)
        self.assertTrue(auditor.has_perm("accounts.access_back_office"))
        with translation.override("ar"):
            self.assertEqual(auditor.role_labels(), ["مدقق الحسابات الإعلانية"])

    def test_client_role_cannot_get_internal_capabilities(self):
        response = self.add_role(
            name_en="Senior Agent", audience="client",
            permissions=["accounts.work_leads", "accounts.view_internal_financials"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not allowed for this audience")
        self.assertFalse(RoleProfile.objects.filter(name_en="Senior Agent").exists())

    def test_role_form_lists_permissions_in_the_page_language(self):
        page = self.client.get("/ar/admin/accounts/roleprofile/add/").content.decode()
        self.assertIn("عرض الإنفاق الإعلاني الفعلي وهامش الوسائط", page)
        self.assertIn("المستخدمون: إضافة", page)
        self.assertNotIn("Can add user", page)
        english = self.client.get("/en/admin/accounts/roleprofile/add/").content.decode()
        # Business capabilities come before admin permissions.
        self.assertLess(english.index("View actual ad spend"), english.index("Users: add"))

    def test_names_must_be_unique(self):
        response = self.add_role(name_en="Media Buyer", audience="internal")
        self.assertContains(response, "already exists")

    def edit_role(self, group_name, permissions):
        role = RoleProfile.objects.get(group__name=group_name)
        return role, self.client.post(
            f"/en/admin/accounts/roleprofile/{role.pk}/change/",
            {"name_en": "Hacked", "permissions": [_perm(n).pk for n in permissions]},
        )

    def test_essential_role_permissions_can_be_edited(self):
        buyer = make_user("buyer", Role.MEDIA_BUYER)
        self.assertTrue(buyer.has_perm("accounts.review_campaign_tasks"))

        role, response = self.edit_role(
            Role.MEDIA_BUYER,
            ["accounts.view_internal_financials", "accounts.manage_creative_library"],
        )
        self.assertEqual(response.status_code, 302)
        role.refresh_from_db()
        self.assertEqual(role.name_en, "Media Buyer")  # name is read-only
        self.assertEqual(role.group.name, Role.MEDIA_BUYER)

        buyer = User.objects.get(pk=buyer.pk)  # fresh permission cache
        self.assertFalse(buyer.has_perm("accounts.review_campaign_tasks"))
        self.assertTrue(buyer.has_perm("accounts.manage_creative_library"))
        self.assertTrue(buyer.has_perm("accounts.access_back_office"))  # entry kept

    def test_edit_form_shows_current_permissions(self):
        role = RoleProfile.objects.get(group__name=Role.MEDIA_BUYER)
        form = self.client.get(f"/en/admin/accounts/roleprofile/{role.pk}/change/").context["adminform"].form
        chosen = {f"{p.content_type.app_label}.{p.codename}" for p in Permission.objects.filter(
            pk__in=form.initial["permissions"])}
        self.assertEqual(chosen, role_permissions(Role.MEDIA_BUYER) - {"accounts.access_back_office"})

    def test_broker_roles_still_cannot_get_internal_permissions(self):
        _role, response = self.edit_role(
            Role.BROKER_OWNER, ["accounts.work_leads", "accounts.view_internal_financials"]
        )
        self.assertContains(response, "Not allowed for this audience")
        owner = make_user("owner", Role.BROKER_OWNER, office=make_office())
        self.assertFalse(owner.has_perm("accounts.view_internal_financials"))

    def test_platform_admin_keeps_role_and_user_management(self):
        _role, response = self.edit_role(Role.PLATFORM_ADMIN, ["accounts.view_user"])
        self.assertContains(response, "This role must keep")
        self.assertTrue(
            Group.objects.get(name=Role.PLATFORM_ADMIN).permissions.filter(
                codename="change_roleprofile"
            ).exists()
        )

    def test_essential_roles_cannot_be_deleted(self):
        role = RoleProfile.objects.get(group__name=Role.MEDIA_BUYER)
        response = self.client.post(f"/en/admin/accounts/roleprofile/{role.pk}/delete/", {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(RoleProfile.objects.filter(pk=role.pk).exists())

    def test_custom_role_can_be_edited_and_deleted(self):
        self.add_role(name_en="Leads Coach", audience="client", permissions=["accounts.work_leads"])
        role = RoleProfile.objects.get(name_en="Leads Coach")
        url = f"/en/admin/accounts/roleprofile/{role.pk}/"
        response = self.client.post(f"{url}change/", {
            "name_en": "Lead Coach", "name_ar": "",
            "permissions": [_perm("accounts.work_leads").pk, _perm("accounts.manage_team").pk],
        })
        self.assertEqual(response.status_code, 302)
        role.refresh_from_db()
        self.assertEqual(role.group.name, "Lead Coach")
        self.assertTrue(role.group.permissions.filter(codename="manage_team").exists())
        self.assertEqual(role.audience, "client")  # read-only once created

        group_pk = role.group.pk
        self.client.post(f"{url}delete/", {"post": "yes"})
        self.assertFalse(Group.objects.filter(pk=group_pk).exists())

    def test_migrate_leaves_custom_roles_alone(self):
        self.add_role(name_en="Leads Coach", audience="client", permissions=["accounts.work_leads"])
        sync_role_groups()
        role = RoleProfile.objects.get(name_en="Leads Coach")
        self.assertTrue(role.group.permissions.filter(codename="work_leads").exists())
        self.assertFalse(role.is_system)

    def test_user_holds_essential_and_custom_roles_together(self):
        self.add_role(name_en="Leads Coach", audience="client", permissions=["accounts.manage_team"])
        member = make_user("sara", Role.BROKER_TEAM_MEMBER, office=make_office())
        member.groups.add(Group.objects.get(name="Leads Coach"))
        member = User.objects.get(pk=member.pk)
        self.assertTrue(member.has_perm("accounts.work_leads"))
        self.assertTrue(member.has_perm("accounts.manage_team"))
        self.assertEqual(member.role_labels(), ["Broker Team Member", "Leads Coach"])
        self.assertEqual(role_errors(list(member.groups.all())), [])

    def test_custom_roles_count_for_the_audience_rule(self):
        self.add_role(name_en="Leads Coach", audience="client", permissions=["accounts.work_leads"])
        coach = Group.objects.get(name="Leads Coach")
        buyer = Group.objects.get(name=Role.MEDIA_BUYER)
        self.assertTrue(role_errors([coach, buyer]))

    def test_other_internal_roles_cannot_manage_roles(self):
        self.client.force_login(make_user("buyer", Role.MEDIA_BUYER))
        self.assertEqual(self.client.get("/en/admin/accounts/roleprofile/add/").status_code, 403)
        self.assertEqual(self.client.get("/en/admin/accounts/user/").status_code, 403)


class RoleAssignmentTests(TestCase):
    """A Platform Admin assigns roles but can't hand out superuser powers."""

    def setUp(self):
        self.platform = make_user("platform", Role.PLATFORM_ADMIN)
        self.client.force_login(self.platform)

    def test_assigns_several_roles_in_the_user_form(self):
        user = make_user("karim", Role.MEDIA_BUYER)
        groups = Group.objects.filter(name__in=[Role.MEDIA_BUYER, Role.MEDIA_BUYING_LEAD])
        response = self.client.post(f"/en/admin/accounts/user/{user.pk}/change/", {
            "username": "karim", "email": "karim@example.com", "is_active": "on",
            "groups": [g.pk for g in groups], "date_joined_0": "2026-01-01",
            "date_joined_1": "00:00:00",
            "membership-TOTAL_FORMS": "0", "membership-INITIAL_FORMS": "0",
        })
        self.assertEqual(response.status_code, 302, getattr(response, "context", None)
                         and response.context["adminform"].form.errors)
        self.assertEqual(set(user.groups.values_list("name", flat=True)),
                         {Role.MEDIA_BUYER, Role.MEDIA_BUYING_LEAD})

    def test_cannot_make_anyone_superuser(self):
        user = make_user("karim", Role.MEDIA_BUYER)
        page = self.client.get(f"/en/admin/accounts/user/{user.pk}/change/")
        self.assertNotContains(page, 'name="is_superuser"')
        self.assertNotContains(page, 'name="user_permissions"')
        self.client.post(f"/en/admin/accounts/user/{user.pk}/change/", {
            "username": "karim", "email": "karim@example.com", "is_active": "on",
            "is_superuser": "on", "date_joined_0": "2026-01-01", "date_joined_1": "00:00:00",
            "membership-TOTAL_FORMS": "0", "membership-INITIAL_FORMS": "0",
        })
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)

    def test_superusers_are_hidden_from_platform_admins(self):
        root = User.objects.create_superuser("root", "root@example.com", "pass-12345")
        self.assertEqual(
            self.client.get(f"/en/admin/accounts/user/{root.pk}/change/").status_code, 302
        )
        self.assertNotContains(self.client.get("/en/admin/accounts/user/"), "root@example.com")

    def test_roles_are_listed_by_translated_name(self):
        user = make_user("karim", Role.MEDIA_BUYER)
        page = self.client.get(f"/ar/admin/accounts/user/{user.pk}/change/").content.decode()
        self.assertIn("مشتري الوسائط", page)


class TenancyTests(TestCase):
    def request_for(self, user):
        request = RequestFactory().get("/")
        request.user = user
        return request

    def test_member_resolves_to_their_office(self):
        office = make_office()
        user = make_user("owner", Role.BROKER_OWNER, office=office)
        tenant = resolve_office(self.request_for(user))
        self.assertEqual(tenant.id, str(office.pk))
        self.assertEqual(tenant.raw, office)

    def test_no_office_for_anonymous_or_non_members(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertIsNone(resolve_office(self.request_for(AnonymousUser())))
        staff = make_user("buyer", Role.MEDIA_BUYER)
        self.assertIsNone(resolve_office(self.request_for(staff)))


class ModelConstraintTests(TestCase):
    def test_user_belongs_to_one_office_only(self):
        user = make_user("owner", office=make_office("A"))
        with self.assertRaises(IntegrityError):
            Membership.objects.create(user=user, office=make_office("B"))

    def test_feedback_score_cannot_exceed_100(self):
        with self.assertRaises(IntegrityError):
            make_office(feedback_score=101)

    def test_email_is_unique(self):
        make_user("a", email="same@example.com")
        with self.assertRaises(IntegrityError):
            make_user("b", email="same@example.com")


class SeedDemoCommandTests(TestCase):
    def test_creates_demo_office_and_logins_once(self):
        from io import StringIO

        from django.core.management import call_command

        call_command("seed_demo", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())  # idempotent

        demo = User.objects.get(username="demo")
        self.assertTrue(demo.check_password("demo-pass-123"))
        self.assertTrue(demo.has_role(Role.BROKER_OWNER))
        self.assertEqual(demo.membership.office.company_name, "Nour Realty")
        self.assertTrue(User.objects.get(username="sara").has_role(Role.BROKER_TEAM_MEMBER))
        self.assertEqual(Membership.objects.filter(office=demo.membership.office).count(), 2)

        lead = User.objects.get(username="lead")
        self.assertTrue(lead.has_role(Role.MEDIA_BUYER))
        self.assertTrue(lead.has_perm("accounts.assign_campaign_tasks"))
        for username in ("platform", "lead", "buyer", "content", "finance"):
            with self.subTest(username=username):
                user = User.objects.get(username=username)
                self.assertTrue(user.has_perm("accounts.access_back_office"))
                self.assertFalse(hasattr(user, "membership"))
                self.assertEqual(role_errors([g.name for g in user.groups.all()]), [])


class AdminLookTests(TestCase):
    """The admin uses the Unfold (Tailwind) theme with a role-aware sidebar."""

    def sidebar_links(self, user, lang="en"):
        import re

        self.client.force_login(user)
        html = self.client.get(f"/{lang}/admin/").content.decode()
        sidebar = html[html.index('id="nav-sidebar"'):]
        return html, set(re.findall(r'href="(/[^"]+)"', sidebar))

    def test_admin_renders_with_the_theme(self):
        html, _links = self.sidebar_links(make_user("platform", Role.PLATFORM_ADMIN))
        self.assertIn("unfold", html)
        self.assertIn("LeadsHub", html)

    def test_sidebar_shows_only_what_the_role_may_open(self):
        _html, links = self.sidebar_links(make_user("platform", Role.PLATFORM_ADMIN))
        self.assertIn("/en/admin/accounts/user/", links)
        self.assertIn("/en/admin/accounts/roleprofile/", links)
        self.assertIn("/en/back-office/", links)

        _html, links = self.sidebar_links(make_user("buyer", Role.MEDIA_BUYER))
        self.assertIn("/en/back-office/", links)
        self.assertNotIn("/en/admin/accounts/user/", links)
        self.assertNotIn("/en/admin/catalog/project/", links)

        _html, links = self.sidebar_links(make_user("lead", Role.MEDIA_BUYING_LEAD))
        self.assertIn("/en/admin/catalog/project/", links)
        self.assertNotIn("/en/admin/accounts/roleprofile/", links)

    def test_admin_is_arabic_and_right_to_left(self):
        html, _links = self.sidebar_links(make_user("platform", Role.PLATFORM_ADMIN), "ar")
        self.assertIn('dir="rtl"', html)
        self.assertIn("الأشخاص والصلاحيات", html)  # our sidebar
        self.assertIn("ابحث في التطبيقات والنماذج", html)  # Unfold's own text

    def test_app_and_admin_share_the_theme_setting(self):
        self.client.force_login(make_user("buyer", Role.MEDIA_BUYER))
        html = self.client.get("/en/back-office/").content.decode()
        self.assertIn('localStorage.getItem("adminTheme")', html)
