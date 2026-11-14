from django.db import IntegrityError
from django.test import TestCase
from django.utils import translation

from accounts.roles import Role
from accounts.test_utils import make_user

from .models import Area, Developer, Project


class CatalogModelTests(TestCase):
    def setUp(self):
        self.developer = Developer.objects.create(name_en="Orascom", name_ar="أوراسكوم")
        self.area = Area.objects.create(name_en="6th of October", name_ar="السادس من أكتوبر")

    def test_display_name_follows_active_language(self):
        with translation.override("ar"):
            self.assertEqual(str(self.developer), "أوراسكوم")
        with translation.override("en"):
            self.assertEqual(str(self.developer), "Orascom")

    def test_display_name_falls_back_to_english(self):
        dev = Developer.objects.create(name_en="Palm Hills")
        with translation.override("ar"):
            self.assertEqual(dev.display_name, "Palm Hills")

    def test_project_name_unique_per_developer(self):
        Project.objects.create(name_en="O West", developer=self.developer, area=self.area)
        with self.assertRaises(IntegrityError):
            Project.objects.create(name_en="O West", developer=self.developer, area=self.area)

    def test_commission_rate_must_be_a_percentage(self):
        with self.assertRaises(IntegrityError):
            Project.objects.create(
                name_en="Bad", developer=self.developer, area=self.area, commission_rate_percent=150
            )


class CatalogAdminAccessTests(TestCase):
    """Master data is managed by Platform Admin and Media Buying Lead only (SRS §2.2)."""

    url = "/en/admin/catalog/project/"

    def test_master_data_managers_can_open_projects(self):
        for role in (Role.PLATFORM_ADMIN, Role.MEDIA_BUYING_LEAD):
            with self.subTest(role=role):
                self.client.force_login(make_user(f"u-{role.name}", role, is_staff=True))
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_other_staff_are_refused(self):
        self.client.force_login(make_user("buyer", Role.MEDIA_BUYER, is_staff=True))
        self.assertEqual(self.client.get(self.url).status_code, 403)


class AudiencePresetTests(TestCase):
    """Plain-language audiences for the campaign builder (FR-4.5, FR-5.14)."""

    def test_no_age_or_gender_targeting(self):
        from .models import AudiencePreset

        names = {field.name for field in AudiencePreset._meta.get_fields()}
        self.assertFalse({name for name in names if "age" in name or "gender" in name or "sex" in name})

    def test_interests_one_per_line(self):
        from .models import AudiencePreset

        preset = AudiencePreset(name_en="Buyers", meta_interests="Real estate\n\n Luxury \n")
        self.assertEqual(preset.interests, ["Real estate", "Luxury"])

    def test_presets_for_an_area_include_any_area_ones(self):
        from . import selectors
        from .models import AudiencePreset

        new_cairo = Area.objects.create(name_en="New Cairo")
        october = Area.objects.create(name_en="6th of October")
        local = AudiencePreset.objects.create(name_en="New Cairo buyers", area=new_cairo)
        anywhere = AudiencePreset.objects.create(name_en="First-time buyers")
        AudiencePreset.objects.create(name_en="October buyers", area=october)
        AudiencePreset.objects.create(name_en="Old", area=new_cairo, is_active=False)
        self.assertEqual(list(selectors.audience_presets(area_id=new_cairo.pk)), [anywhere, local])

    def test_who_manages_presets(self):
        url = "/en/admin/catalog/audiencepreset/"
        for role, status in ((Role.PLATFORM_ADMIN, 200), (Role.MEDIA_BUYING_LEAD, 200),
                             (Role.CONTENT_MANAGER, 403), (Role.MEDIA_BUYER, 403)):
            with self.subTest(role=role):
                self.client.force_login(make_user(f"u-{role.name}", role))
                self.assertEqual(self.client.get(url).status_code, status)
