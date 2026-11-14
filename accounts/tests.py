from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.utils import translation

from creatives.test_utils import TemporaryUploadsMixin

from .models import Membership, RoleProfile, User
from .roles import INTERNAL_ROLES, Role, role_errors, role_permissions, sync_role_groups
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


class SeedDemoCommandTests(TemporaryUploadsMixin, TestCase):
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

        # One brokerage that signed itself up, waiting for review.
        from .models import BrokerOffice, OfficeStatus

        applicant = User.objects.get(username="applicant")
        self.assertFalse(applicant.groups.exists())
        self.assertEqual(applicant.office_registration.office.status, OfficeStatus.PENDING)
        self.assertIsNotNone(applicant.office_registration.email_confirmed_at)
        self.assertEqual(BrokerOffice.objects.filter(status=OfficeStatus.PENDING).count(), 1)

    def test_creates_a_small_library_once(self):
        from io import StringIO

        from django.core.management import call_command

        from catalog.models import AudiencePreset
        from creatives.models import Creative

        call_command("seed_demo", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())  # idempotent
        self.assertEqual(Creative.objects.count(), 4)
        self.assertEqual(Creative.objects.usable().count(), 2)  # one ended, one draft
        self.assertEqual(AudiencePreset.objects.count(), 1)
        self.assertTrue(all(c.file.storage.exists(c.file.name) for c in Creative.objects.all()))


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


def _invite(owner, email="hana@example.com", **fields):
    """Invite through the service, running the after-commit email; returns the new user."""
    from . import services

    request = RequestFactory().get("/")
    return services.invite_member(actor=owner, email=email, request=request, **fields)


def _link_path(message):
    """The invitation link's path in an email."""
    import re

    return re.search(r"https?://[^/\s]+(/\S+)", message.body).group(1)


class LibraryPermissionTests(TestCase):
    """Who keeps the creative library and the audience presets (ROADMAP M2)."""

    def test_library_and_preset_permissions_by_role(self):
        creatives = {f"creatives.{a}_creative" for a in ("view", "add", "change", "delete")}
        presets = {f"catalog.{a}_audiencepreset" for a in ("view", "add", "change", "delete")}
        for role in Role:
            with self.subTest(role=role):
                perms = role_permissions(role)
                self.assertEqual(
                    creatives <= perms, role in (Role.PLATFORM_ADMIN, Role.CONTENT_MANAGER)
                )
                self.assertEqual(
                    presets <= perms, role in (Role.PLATFORM_ADMIN, Role.MEDIA_BUYING_LEAD)
                )
                self.assertFalse((creatives | presets) & perms and role not in INTERNAL_ROLES)

    def test_existing_databases_get_the_permissions(self):
        """Migration 0005 grants them where the roles already existed."""
        import importlib

        from django.apps import apps

        migration = importlib.import_module("accounts.migrations.0005_library_permissions")
        content = Group.objects.get(name=Role.CONTENT_MANAGER)
        content.permissions.remove(_perm("creatives.change_creative"))
        lead = Group.objects.get(name=Role.MEDIA_BUYING_LEAD)
        lead.permissions.remove(_perm("catalog.view_audiencepreset"))
        migration.grant(apps, None)
        self.assertTrue(content.permissions.filter(codename="change_creative").exists())
        self.assertTrue(lead.permissions.filter(codename="view_audiencepreset").exists())


class OfficeOnboardingTests(TestCase):
    """A Platform Admin opens a broker office and invites its owner (ROADMAP M1, Q6)."""

    ADD_URL = "/en/admin/accounts/brokeroffice/add/"

    def setUp(self):
        # Requests to /ar/ pages leave Arabic active in the thread; start in
        # English and put back whatever was active before.
        self.enterContext(translation.override("en"))
        self.client.force_login(make_user("platform", Role.PLATFORM_ADMIN, first_name="Omar"))

    def open_office(self, **data):
        fields = {
            "company_name": "Delta Brokers", "owner_first_name": "Mona",
            "owner_last_name": "Said", "owner_email": "mona@delta.test",
        }
        fields.update(data)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.ADD_URL, fields)

    def test_add_form_asks_for_the_owner(self):
        page = self.client.get(self.ADD_URL)
        self.assertContains(page, 'name="owner_email"')
        self.assertContains(page, 'name="owner_first_name"')
        self.assertNotContains(page, 'name="wallet_balance_egp"')
        self.assertNotContains(page, "memberships-TOTAL_FORMS")

    def test_opening_an_office_invites_its_owner(self):
        from django.core import mail

        from .models import BrokerOffice

        response = self.open_office()
        self.assertEqual(response.status_code, 302)
        office = BrokerOffice.objects.get(company_name="Delta Brokers")
        owner = User.objects.get(email="mona@delta.test")
        self.assertEqual(owner.membership.office, office)
        self.assertTrue(owner.has_role(Role.BROKER_OWNER))
        self.assertFalse(owner.has_usable_password())
        self.assertEqual((owner.first_name, owner.last_name), ("Mona", "Said"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Delta Brokers", mail.outbox[0].subject)
        self.assertIn("Omar invited you", mail.outbox[0].body)
        self.assertTrue(_link_path(mail.outbox[0]).startswith("/en/invitation/"))

    def test_owner_address_must_be_new(self):
        make_user("mona", email="mona@delta.test")
        response = self.open_office(owner_email="MONA@delta.test")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This email address already has a LeadsHub account.")
        from .models import BrokerOffice

        self.assertFalse(BrokerOffice.objects.filter(company_name="Delta Brokers").exists())

    def test_balances_tier_and_score_are_read_only(self):
        office = make_office("Nour Realty", wallet_balance_usd=3180)
        url = f"/en/admin/accounts/brokeroffice/{office.pk}/change/"
        page = self.client.get(url)
        self.assertNotContains(page, 'name="wallet_balance_usd"')
        self.assertNotContains(page, 'name="feedback_score"')
        self.client.post(url, {
            "company_name": "Nour Realty", "wallet_balance_usd": "999999", "feedback_score": "100",
            "memberships-TOTAL_FORMS": "0", "memberships-INITIAL_FORMS": "0",
        })
        office.refresh_from_db()
        self.assertEqual(office.wallet_balance_usd, 3180)
        self.assertEqual(office.feedback_score, 0)

    def test_other_internal_roles_cannot_open_offices(self):
        self.client.force_login(make_user("lead", Role.MEDIA_BUYING_LEAD))
        self.assertEqual(self.client.get(self.ADD_URL).status_code, 403)

    def test_resending_from_the_user_list(self):
        from django.core import mail

        self.open_office()
        mona = User.objects.get(email="mona@delta.test")
        accepted = make_user("sara", Role.BROKER_TEAM_MEMBER, office=mona.membership.office)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/en/admin/accounts/user/", {
                "action": "send_new_invitation",
                "_selected_action": [mona.pk, accepted.pk],
                "index": "0",
            }, follow=True)
        self.assertContains(response, "Sent 1 invitation.")
        self.assertEqual([m.to for m in mail.outbox], [["mona@delta.test"], ["mona@delta.test"]])

    def test_existing_databases_get_the_permissions(self):
        """Migration 0004 grants them where the role already existed."""
        import importlib

        from django.apps import apps

        migration = importlib.import_module("accounts.migrations.0004_platform_admin_opens_offices")
        group = Group.objects.get(name=Role.PLATFORM_ADMIN)
        group.permissions.remove(_perm("accounts.add_brokeroffice"), _perm("accounts.change_brokeroffice"))
        migration.grant(apps, None)
        names = {p.codename for p in group.permissions.all()}
        self.assertLessEqual({"add_brokeroffice", "change_brokeroffice"}, names)


class InvitationTests(TestCase):
    """An invited person sets a password from the emailed link and lands in the portal."""

    PASSWORD = "Quiet-river-1984"

    def setUp(self):
        # Requests to /ar/ pages leave Arabic active in the thread; start in
        # English and put back whatever was active before.
        self.enterContext(translation.override("en"))
        self.office = make_office("Nour Realty")
        self.owner = make_user("owner", Role.BROKER_OWNER, office=self.office, first_name="Nour")

    def invite(self, **fields):
        from django.core import mail

        with self.captureOnCommitCallbacks(execute=True):
            user = _invite(self.owner, **fields)
        return user, mail.outbox[-1]

    def accept(self, path, password=None):
        password = password or self.PASSWORD
        form_page = self.client.get(path, follow=True)
        set_password_url = form_page.redirect_chain[-1][0] if form_page.redirect_chain else path
        return form_page, self.client.post(
            set_password_url, {"new_password1": password, "new_password2": password}
        )

    def test_member_sets_a_password_and_lands_in_the_portal(self):
        hana, message = self.invite(first_name="Hana")
        self.assertIn("Your sign-in name is hana@example.com.", message.body)
        form_page, response = self.accept(_link_path(message))
        self.assertContains(form_page, '<span dir="ltr">hana@example.com</span>')
        self.assertRedirects(response, "/en/portal/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/en/portal/").status_code, 200)
        self.assertTrue(_fresh_user(hana).check_password(self.PASSWORD))

    def test_member_signs_in_with_the_email_address(self):
        _hana, message = self.invite()
        self.accept(_link_path(message))
        self.client.logout()
        response = self.client.post(
            "/en/login/", {"username": "hana@example.com", "password": self.PASSWORD}
        )
        self.assertRedirects(response, "/en/portal/", fetch_redirect_response=False)

    def test_no_sign_in_before_accepting(self):
        self.invite()
        response = self.client.post("/en/login/", {"username": "hana@example.com", "password": ""})
        self.assertEqual(response.status_code, 200)

    def test_the_link_works_once(self):
        _hana, message = self.invite()
        self.accept(_link_path(message))
        self.client.logout()
        page = self.client.get(_link_path(message), follow=True)
        self.assertContains(page, "This link no longer works")
        self.assertNotContains(page, 'name="new_password1"')

    def test_a_deactivated_persons_link_does_not_work(self):
        hana, message = self.invite()
        hana.is_active = False
        hana.save(update_fields=["is_active"])
        page = self.client.get(_link_path(message), follow=True)
        self.assertContains(page, "This link no longer works")

    def test_weak_passwords_are_refused(self):
        hana, message = self.invite()
        _form_page, response = self.accept(_link_path(message), password="12345678")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="mt-1 text-sm text-red-600 dark:text-red-400"')
        self.assertFalse(_fresh_user(hana).has_usable_password())

    def test_arabic_invitation(self):
        with translation.override("ar"):
            _hana, message = self.invite(first_name="Hana")
        path = _link_path(message)
        self.assertTrue(path.startswith("/ar/invitation/"))
        self.assertIn("مرحبًا Hana،", message.body)
        form_page, _response = self.accept(path)
        self.assertContains(form_page, 'dir="rtl"')
        self.assertContains(form_page, "عيّن كلمة المرور")

    def test_mail_failure_keeps_the_account(self):
        from unittest import mock

        with mock.patch("leadshub.emails.EmailMultiAlternatives.send", side_effect=OSError("mail server down")):
            with self.assertLogs("django.test", level="ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    hana = _invite(self.owner)
        self.assertTrue(User.objects.filter(pk=hana.pk).exists())


def _fresh_user(user):
    return User.objects.get(pk=user.pk)


class TeamServiceTests(TestCase):
    """accounts.services refuses what the team rules don't allow, whoever calls it."""

    def setUp(self):
        # Requests to /ar/ pages leave Arabic active in the thread; start in
        # English and put back whatever was active before.
        self.enterContext(translation.override("en"))
        self.office = make_office("Nour Realty")
        self.owner = make_user("owner", Role.BROKER_OWNER, office=self.office)
        self.member = make_user("sara", Role.BROKER_TEAM_MEMBER, office=self.office)

    def test_only_team_managers_invite(self):
        from django.core.exceptions import PermissionDenied

        for actor in (self.member, make_user("buyer", Role.MEDIA_BUYER)):
            with self.subTest(actor=actor.username), self.assertRaises(PermissionDenied):
                _invite(actor)
        self.assertFalse(User.objects.filter(email="hana@example.com").exists())

    def test_people_outside_the_office_are_refused(self):
        from django.core.exceptions import PermissionDenied

        from . import services

        rival = make_user("rival", Role.BROKER_OWNER, office=make_office("Cairo Homes"))
        with self.assertRaises(PermissionDenied):
            services.set_member_active(actor=rival, member_id=self.member.pk, active=False)
        with self.assertRaises(PermissionDenied):
            services.set_campaign_access(actor=rival, member_id=self.member.pk, allowed=True)
        self.assertTrue(_fresh_user(self.member).is_active)

    def test_opening_an_office_needs_the_admin_permission(self):
        from django.core.exceptions import PermissionDenied

        from . import services
        from .models import BrokerOffice

        with self.assertRaises(PermissionDenied):
            services.open_office(
                office=BrokerOffice(company_name="Delta"), owner_email="mona@delta.test",
                opened_by=make_user("buyer", Role.MEDIA_BUYER), request=RequestFactory().get("/"),
            )
        self.assertFalse(BrokerOffice.objects.filter(company_name="Delta").exists())


class SignInEmailTests(TestCase):
    """The address an invitation uses must be able to become a sign-in name."""

    def setUp(self):
        self.enterContext(translation.override("en"))

    def form(self, email):
        from .forms import InviteMemberForm

        return InviteMemberForm(data={"first_name": "Hana", "last_name": "", "email": email})

    def test_valid_address(self):
        form = self.form("Hana.F+team@Example.COM")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "Hana.F+team@example.com")

    def test_taken_in_any_case(self):
        make_user("hana", email="hana@example.com")
        self.assertIn("already has", self.form("HANA@example.com").errors["email"][0])

    def test_taken_as_someone_elses_sign_in_name(self):
        make_user("hana@example.com", email="other@example.com")
        self.assertFalse(self.form("hana@example.com").is_valid())

    def test_characters_a_sign_in_name_cannot_have(self):
        self.assertIn("can't be a sign-in name", self.form("o'neil@example.com").errors["email"][0])

    def test_first_name_is_required(self):
        from .forms import InviteMemberForm

        form = InviteMemberForm(data={"first_name": "", "email": "hana@example.com"})
        self.assertIn("first_name", form.errors)


class BrokerRegistrationTests(TestCase):
    """A brokerage signs itself up; staff approve it before it opens (ROADMAP Q6)."""

    PASSWORD = "Quiet-river-1984"

    def setUp(self):
        self.enterContext(translation.override("en"))
        self.reviewer = make_user("platform", Role.PLATFORM_ADMIN, email="platform@leadshub.test")

    def register(self, lang="en", **data):
        fields = {
            "company_name": "Delta Brokers", "first_name": "Mona", "last_name": "Said",
            "email": "Mona@Delta.test", "phone_number": "010 1234-5678",
            "password1": self.PASSWORD, "password2": self.PASSWORD,
        }
        fields.update(data)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(f"/{lang}/register/", fields)

    def applicant(self):
        return User.objects.get(email="Mona@delta.test")

    def confirm(self, lang="en"):
        from django.core import mail

        link = _link_path(mail.outbox[-1])
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.get(link)

    def review(self, action, *offices, actor=None):
        self.client.force_login(actor or self.reviewer)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post("/en/admin/accounts/brokeroffice/", {
                "action": action,
                "_selected_action": [office.pk for office in offices],
                "index": "0",
            }, follow=True)

    # --- Signing up ---------------------------------------------------------

    def test_the_page_is_public_and_linked_from_sign_in(self):
        self.assertContains(self.client.get("/en/login/"), 'href="/en/register/"')
        page = self.client.get("/en/register/")
        self.assertContains(page, "Register your brokerage")
        for name in ("company_name", "first_name", "email", "phone_number", "password1", "password2"):
            self.assertContains(page, f'name="{name}"')
        self.assertContains(page, 'type="tel"')
        self.assertContains(self.client.get("/ar/register/"), "سجّل شركتك")

    def test_signed_in_people_are_sent_home(self):
        self.client.force_login(make_user("owner", Role.BROKER_OWNER, office=make_office()))
        self.assertRedirects(self.client.get("/en/register/"), "/en/", fetch_redirect_response=False)

    def test_signing_up_creates_a_pending_office_and_signs_in_without_a_role(self):
        from django.core import mail

        from .models import OfficeStatus

        response = self.register()
        self.assertRedirects(response, "/en/register/status/", fetch_redirect_response=False)
        user = self.applicant()
        self.assertEqual(user.username, "mona@delta.test")
        self.assertTrue(user.check_password(self.PASSWORD))
        self.assertEqual(list(user.groups.all()), [])
        office = user.membership.office
        self.assertEqual((office.company_name, office.status), ("Delta Brokers", OfficeStatus.PENDING))
        registration = user.office_registration
        self.assertEqual(registration.office, office)
        self.assertEqual(registration.phone_number, "01012345678")
        self.assertEqual(registration.language, "en")
        self.assertIsNone(registration.email_confirmed_at)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        # Only the applicant is emailed until the address is confirmed.
        self.assertEqual([m.to for m in mail.outbox], [["Mona@delta.test"]])
        self.assertIn("Confirm your email address", mail.outbox[0].subject)
        self.assertTrue(_link_path(mail.outbox[0]).startswith("/en/register/confirm/"))
        # The HTML copy has the same link on its button.
        [(html, _)] = mail.outbox[0].alternatives
        self.assertIn(f'href="http://testserver{_link_path(mail.outbox[0])}"', html)
        self.assertIn("Confirm email address</a>", html)

    def test_invalid_details_are_refused(self):
        from .models import BrokerOffice

        make_user("taken", email="taken@delta.test")
        cases = {
            "email": ("TAKEN@delta.test", "This email address already has a LeadsHub account."),
            "phone_number": ("12-34", "Enter a phone number of 8 to 15 digits"),
            "password2": ("Other-river-1984", "The two passwords don&#x27;t match."),
            "password1": ("mona", "This password is too short."),
        }
        for field, (value, message) in cases.items():
            with self.subTest(field):
                data = {field: value}
                if field == "password1":
                    data["password2"] = value
                response = self.register(**data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, message)
        self.assertFalse(BrokerOffice.objects.exists())

    def test_password_may_not_resemble_the_person(self):
        response = self.register(password1="mona@delta.test1", password2="mona@delta.test1")
        self.assertContains(response, "The password is too similar to the")

    def test_arabic_sign_up_gets_arabic_emails_and_links(self):
        from django.core import mail

        self.register(lang="ar")
        self.assertEqual(self.applicant().office_registration.language, "ar")
        self.assertIn("تأكيد", mail.outbox[0].subject)
        self.assertTrue(_link_path(mail.outbox[0]).startswith("/ar/register/confirm/"))

    # --- Before approval ----------------------------------------------------

    def test_until_approved_the_applicant_sees_only_the_status_page(self):
        self.register()
        self.assertRedirects(self.client.get("/en/"), "/en/register/status/", fetch_redirect_response=False)
        page = self.client.get("/en/register/status/")
        self.assertContains(page, "Confirm your email address")
        self.assertContains(page, "Mona@delta.test")
        for url in ("/en/portal/", "/en/portal/library/", "/en/portal/team/", "/en/back-office/"):
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertNotContains(page, 'id="sidebar"')

    def test_sign_in_lands_on_the_status_page(self):
        self.register()
        self.client.logout()
        response = self.client.post("/en/login/", {"username": "mona@delta.test", "password": self.PASSWORD}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], "/en/register/status/")

    def test_status_page_is_only_for_applicants(self):
        self.assertRedirects(self.client.get("/en/register/status/"), "/en/login/?next=/en/register/status/")
        self.client.force_login(make_user("owner", Role.BROKER_OWNER, office=make_office()))
        self.assertRedirects(self.client.get("/en/register/status/"), "/en/", fetch_redirect_response=False)

    def test_applicant_can_ask_for_a_new_link(self):
        from django.core import mail

        self.register()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/en/register/status/")
        self.assertRedirects(response, "/en/register/status/?sent=1", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 2)
        self.assertContains(self.client.get("/en/register/status/?sent=1"), "We sent a new link.")

    # --- Confirming the email -----------------------------------------------

    def test_confirming_tells_the_reviewers_once(self):
        from django.core import mail

        make_user("buyer", Role.MEDIA_BUYER, email="buyer@leadshub.test")  # can't review
        self.register()
        link = _link_path(mail.outbox[0])
        page = self.confirm()
        self.assertContains(page, "Email address confirmed")
        self.assertIsNotNone(self.applicant().office_registration.email_confirmed_at)
        to_staff = mail.outbox[-1]
        self.assertEqual(to_staff.to, ["platform@leadshub.test"])
        self.assertIn("Delta Brokers", to_staff.subject)
        self.assertIn("01012345678", to_staff.body)
        office = self.applicant().membership.office
        self.assertIn(f"/en/admin/accounts/brokeroffice/{office.pk}/change/", to_staff.body)

        # The link works once; the status page moves on to "being reviewed".
        sent = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertContains(self.client.get(link), "This link no longer works")
        self.assertEqual(len(mail.outbox), sent)
        self.assertContains(self.client.get("/en/register/status/"), "Your registration is being reviewed")
        self.assertEqual(self.client.post("/en/register/status/").url, "/en/register/status/")

    def test_a_wrong_or_borrowed_token_does_not_confirm(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        self.register()
        user = self.applicant()
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        for token in ("abc-123", default_token_generator.make_token(user)):
            with self.subTest(token):
                self.assertContains(self.client.get(f"/en/register/confirm/{uid}/{token}/"), "This link no longer works")
        # A staff-made account has no sign-up to confirm.
        owner = make_user("owner", Role.BROKER_OWNER, office=make_office())
        from .tokens import email_confirmation_token

        owner_uid = urlsafe_base64_encode(force_bytes(owner.pk))
        page = self.client.get(f"/en/register/confirm/{owner_uid}/{email_confirmation_token.make_token(owner)}/")
        self.assertContains(page, "This link no longer works")
        self.assertIsNone(self.applicant().office_registration.email_confirmed_at)

    # --- Review in the admin ------------------------------------------------

    def test_approval_makes_the_applicant_the_owner(self):
        from django.core import mail

        from .models import OfficeStatus

        self.register(lang="ar")
        self.confirm()
        office = self.applicant().membership.office
        page = self.review("approve_signups", office)
        self.assertContains(page, "Approved 1 office")
        office.refresh_from_db()
        self.assertEqual(office.status, OfficeStatus.ACTIVE)
        user = self.applicant()
        self.assertTrue(user.has_role(Role.BROKER_OWNER))
        registration = user.office_registration
        self.assertEqual(registration.reviewed_by, self.reviewer)
        self.assertIsNotNone(registration.reviewed_at)
        approved = mail.outbox[-1]
        self.assertEqual(approved.to, ["Mona@delta.test"])
        self.assertIn("/ar/login/", approved.body)  # in the sign-up's language

        self.client.force_login(user)
        self.assertRedirects(self.client.get("/en/"), "/en/portal/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/en/portal/").status_code, 200)
        self.assertRedirects(self.client.get("/en/register/status/"), "/en/", fetch_redirect_response=False)

    def test_an_unconfirmed_email_is_not_approved(self):
        from .models import OfficeStatus

        self.register()
        office = self.applicant().membership.office
        page = self.review("approve_signups", office)
        self.assertContains(page, "Skipped 1 office")
        office.refresh_from_db()
        self.assertEqual(office.status, OfficeStatus.PENDING)
        self.assertFalse(self.applicant().groups.exists())

    def test_rejection_is_shown_to_the_applicant(self):
        from django.core import mail

        from .models import OfficeStatus

        self.register()
        self.confirm()
        office = self.applicant().membership.office
        self.assertContains(self.review("reject_signups", office), "Rejected 1 sign-up.")
        office.refresh_from_db()
        self.assertEqual(office.status, OfficeStatus.REJECTED)
        self.assertEqual(mail.outbox[-1].to, ["Mona@delta.test"])
        self.assertFalse(self.applicant().groups.exists())

        self.client.force_login(self.applicant())
        page = self.client.get("/en/register/status/")
        self.assertContains(page, "approve this registration")
        self.assertNotContains(page, "Send the link again")
        self.assertEqual(self.client.get("/en/portal/").status_code, 403)

    def test_unconfirmed_rejections_send_no_email(self):
        from django.core import mail

        self.register()
        sent = len(mail.outbox)
        self.review("reject_signups", self.applicant().membership.office)
        self.assertEqual(len(mail.outbox), sent)

    def test_staff_opened_offices_are_not_touched(self):
        from .models import OfficeStatus

        office = make_office("Nour Realty")
        self.assertEqual(office.status, OfficeStatus.ACTIVE)
        self.assertContains(self.review("reject_signups", office), "Skipped 1 office")
        office.refresh_from_db()
        self.assertEqual(office.status, OfficeStatus.ACTIVE)

    def test_only_reviewers_may_approve_or_reject(self):
        from django.core.exceptions import PermissionDenied

        from . import services
        from .models import BrokerOffice

        self.register()
        self.confirm()
        offices = BrokerOffice.objects.all()
        for role in (Role.MEDIA_BUYING_LEAD, Role.FINANCE_MANAGER, Role.BROKER_OWNER):
            actor = make_user(f"actor-{role}".replace(" ", "-").lower(), role)
            for review in (services.approve_registrations, services.reject_registrations):
                with self.subTest(role=role, review=review.__name__), self.assertRaises(PermissionDenied):
                    review(offices, actor=actor, request=RequestFactory().get("/"))
        self.client.force_login(make_user("lead", Role.MEDIA_BUYING_LEAD))
        self.assertEqual(self.client.get("/en/admin/accounts/brokeroffice/").status_code, 403)

    def test_admin_lists_sign_ups_with_a_badge(self):
        self.register()
        self.confirm()
        self.client.force_login(self.reviewer)
        page = self.client.get("/en/admin/accounts/brokeroffice/?status__exact=pending")
        self.assertContains(page, "Delta Brokers")
        self.assertContains(page, "Sign-ups to review")
        self.assertContains(page, "Approve selected sign-ups")
        office = self.applicant().membership.office
        detail = self.client.get(f"/en/admin/accounts/brokeroffice/{office.pk}/change/")
        self.assertContains(detail, "01012345678")
        self.assertNotContains(detail, 'name="status"')

    def test_editing_a_signed_up_office_keeps_its_status(self):
        from .models import OfficeStatus

        self.register()
        office = self.applicant().membership.office
        self.client.force_login(self.reviewer)
        url = f"/en/admin/accounts/brokeroffice/{office.pk}/change/"
        response = self.client.post(url, {
            "company_name": "Delta Brokers Group", "status": "active",
            "registration-TOTAL_FORMS": "1", "registration-INITIAL_FORMS": "1",
            "memberships-TOTAL_FORMS": "0", "memberships-INITIAL_FORMS": "0",
        })
        self.assertEqual(response.status_code, 302)
        office.refresh_from_db()
        self.assertEqual((office.company_name, office.status), ("Delta Brokers Group", OfficeStatus.PENDING))
