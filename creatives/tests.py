import datetime
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.roles import Role
from accounts.test_utils import make_office, make_user

from . import selectors
from .demo import solid_png
from .models import AspectRatio, AssetType, Creative, Language, Status
from .test_utils import TemporaryUploadsMixin, make_creative, make_project


class CreativeModelTests(TemporaryUploadsMixin, TestCase):
    """FR-5.3 metadata and the rules Content Managers get help with."""

    def creative(self, **fields):
        fields.setdefault("title", "Launch")
        fields.setdefault("asset_type", AssetType.IMAGE)
        fields.setdefault("language", Language.ARABIC)
        fields.setdefault("aspect_ratio", AspectRatio.SQUARE)
        return Creative(**fields)

    def test_a_project_fills_in_its_area_and_developer(self):
        project = make_project()
        creative = self.creative(project=project)
        creative.clean()
        self.assertEqual(creative.area, project.area)
        self.assertEqual(creative.developer, project.developer)

    def test_area_and_developer_must_match_the_project(self):
        project = make_project()
        other = make_project("O West", area="6th of October", developer="Orascom")
        creative = self.creative(project=project, area=other.area, developer=other.developer)
        with self.assertRaises(ValidationError) as caught:
            creative.clean()
        self.assertEqual(set(caught.exception.message_dict), {"area", "developer"})

    def test_an_area_wide_asset_needs_an_area(self):
        with self.assertRaises(ValidationError) as caught:
            self.creative().clean()
        self.assertIn("area", caught.exception.message_dict)
        creative = self.creative(area=make_project().area)
        creative.clean()  # no project is fine

    def test_aspect_ratio_rules(self):
        area = make_project().area
        with self.assertRaises(ValidationError):
            self.creative(area=area, aspect_ratio="").clean()
        with self.assertRaises(ValidationError):
            self.creative(area=area, asset_type=AssetType.REEL, aspect_ratio=AspectRatio.SQUARE).clean()
        self.creative(area=area, asset_type=AssetType.LANDING_PAGE, aspect_ratio="").clean()
        self.creative(area=area, asset_type=AssetType.REEL, aspect_ratio=AspectRatio.PORTRAIT).clean()

    def test_files_that_could_run_scripts_are_refused(self):
        field = Creative._meta.get_field("file")
        for name in ("page.html", "logo.svg", "script.js"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                field.run_validators(SimpleUploadedFile(name, b"<script>alert(1)</script>"))
        field.run_validators(SimpleUploadedFile("ad.mp4", b"video"))

    def test_large_files_are_refused(self):
        big = SimpleUploadedFile("ad.png", b"x")
        big.size = 101 * 1024 * 1024
        with self.assertRaises(ValidationError):
            Creative._meta.get_field("file").run_validators(big)

    def test_code_and_upload_path(self):
        creative = make_creative()
        self.assertEqual(creative.code, f"CR-{creative.pk:04d}")
        self.assertRegex(creative.file.name, r"^creatives/[0-9a-f]{32}/ad\.png$")
        self.assertEqual(creative.file.url, f"/files/{creative.file.name}")


class UsableCreativesTests(TemporaryUploadsMixin, TestCase):
    """FR-5.7: brokers get published assets whose offer hasn't ended."""

    def test_only_published_assets(self):
        published = make_creative("Published")
        make_creative("Draft", status=Status.DRAFT)
        make_creative("Archived", status=Status.ARCHIVED)
        self.assertEqual(list(Creative.objects.usable()), [published])

    def test_both_sides_of_the_expiry_date(self):
        ends = datetime.date(2026, 12, 31)
        creative = make_creative(offer_expires_on=ends)
        self.assertIn(creative, Creative.objects.usable(today=ends))  # the last day
        self.assertNotIn(creative, Creative.objects.usable(today=ends + datetime.timedelta(days=1)))

    def test_no_end_date_never_expires(self):
        creative = make_creative(offer_expires_on=None)
        self.assertIn(creative, Creative.objects.usable(today=datetime.date(2099, 1, 1)))


class LibrarySelectorTests(TemporaryUploadsMixin, TestCase):
    def setUp(self):
        self.phnc = make_project()
        self.o_west = make_project("O West", area="6th of October", developer="Orascom")
        self.image = make_creative("Image", project=self.phnc)
        self.arabic = make_creative("Arabic", project=self.o_west, language=Language.ARABIC)
        self.reel = make_creative(
            "Reel", project=self.phnc, asset_type=AssetType.REEL, aspect_ratio=AspectRatio.PORTRAIT,
            filename="reel.mp4", content=b"video",
        )

    def test_filters(self):
        self.assertEqual(set(selectors.library()), {self.image, self.arabic, self.reel})
        self.assertEqual(set(selectors.library(area_id=self.o_west.area_id)), {self.arabic})
        self.assertEqual(set(selectors.library(project_id=self.phnc.pk)), {self.image, self.reel})
        self.assertEqual(set(selectors.library(asset_type=AssetType.REEL)), {self.reel})
        self.assertEqual(set(selectors.library(language=Language.ARABIC)), {self.arabic})

    def test_one_query_for_the_list(self):
        with self.assertNumQueries(1):
            for creative in selectors.library():
                str(creative.project), str(creative.area), str(creative.developer)

    def test_filter_choices_only_list_places_with_assets(self):
        empty = make_project("Empty", area="Sheikh Zayed", developer="SODIC")
        self.assertEqual(set(selectors.library_areas()), {self.phnc.area, self.o_west.area})
        self.assertNotIn(empty, selectors.library_projects())
        self.assertEqual(list(selectors.library_projects(area_id=self.o_west.area_id)), [self.o_west])


class FileAccessTests(TemporaryUploadsMixin, TestCase):
    """Private files: who may open a creative's file (ROADMAP M2)."""

    def setUp(self):
        self.creative = make_creative()
        self.url = self.creative.file.url
        self.broker = make_user("owner", Role.BROKER_OWNER, office=make_office())

    def open_as(self, user, url=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(url or self.url)

    def test_signed_out_people_are_sent_to_sign_in(self):
        response = self.open_as(None)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_a_broker_opens_a_usable_asset_in_the_browser(self):
        response = self.open_as(self.broker)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertEqual(response["Content-Security-Policy"], "sandbox")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(b"".join(response.streaming_content), self.creative.file.open().read())

    def test_brokers_cannot_open_drafts_or_expired_offers(self):
        for status, ends in ((Status.DRAFT, None), (Status.PUBLISHED, timezone.localdate() - datetime.timedelta(days=1))):
            with self.subTest(status=status, ends=ends):
                Creative.objects.filter(pk=self.creative.pk).update(status=status, offer_expires_on=ends)
                self.assertEqual(self.open_as(self.broker).status_code, 404)

    def test_content_managers_open_any_asset(self):
        Creative.objects.filter(pk=self.creative.pk).update(status=Status.DRAFT)
        self.assertEqual(self.open_as(make_user("content", Role.CONTENT_MANAGER)).status_code, 200)

    def test_other_audiences_are_refused(self):
        from catalog.models import Developer

        viewer = make_user("dev", Role.DEVELOPER_VIEW, developer=Developer.objects.create(name_en="X"))
        self.assertEqual(self.open_as(viewer).status_code, 404)
        self.assertEqual(self.open_as(make_user("buyer", Role.MEDIA_BUYER)).status_code, 404)

    def test_zip_files_are_downloaded(self):
        bundle = make_creative("Bundle", filename="carousel.zip", content=b"PK zip")
        response = self.open_as(self.broker, bundle.file.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))

    def test_unknown_or_outside_names_are_not_found(self):
        self.client.force_login(self.broker)
        for name in ("creatives/none/ad.png", "creatives/../../settings.py", "other/x.png"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(f"/files/{name}").status_code, 404)


class CreativeAdminTests(TemporaryUploadsMixin, TestCase):
    """The library is kept by Content Managers and Platform Admins (SRS §2.1)."""

    URL = "/en/admin/creatives/creative/"

    def setUp(self):
        self.project = make_project()
        self.content = make_user("content", Role.CONTENT_MANAGER, first_name="Laila")

    def test_who_can_open_the_library_admin(self):
        for role, status in ((Role.CONTENT_MANAGER, 200), (Role.PLATFORM_ADMIN, 200),
                             (Role.MEDIA_BUYER, 403), (Role.FINANCE_MANAGER, 403)):
            with self.subTest(role=role):
                self.client.force_login(make_user(f"u-{role.name}", role))
                self.assertEqual(self.client.get(self.URL).status_code, status)

    def test_content_manager_uploads_a_creative(self):
        self.client.force_login(self.content)
        response = self.client.post(f"{self.URL}add/", {
            "title": "Launch", "offer": "5% down", "asset_type": AssetType.IMAGE,
            "language": Language.ENGLISH, "aspect_ratio": AspectRatio.SQUARE,
            "project": self.project.pk, "status": Status.PUBLISHED,
            "file": SimpleUploadedFile("launch.png", solid_png(4, 4, (0, 0, 0)), content_type="image/png"),
        })
        self.assertEqual(response.status_code, 302, getattr(response, "context", None)
                         and response.context["adminform"].form.errors)
        creative = Creative.objects.get(title="Launch")
        self.assertEqual(creative.area, self.project.area)  # filled in from the project
        self.assertEqual(creative.created_by, self.content)

    def test_expired_offers_are_flagged(self):
        make_creative("Winter flyer K7", offer_expires_on=timezone.localdate() - datetime.timedelta(days=2))
        make_creative("Spring flyer Q4", offer_expires_on=timezone.localdate() + datetime.timedelta(days=2))
        self.client.force_login(self.content)
        page = self.client.get(f"{self.URL}?offer=expired")
        self.assertContains(page, "Winter flyer K7")
        self.assertNotContains(page, "Spring flyer Q4")
        self.assertContains(self.client.get(self.URL), "Expired ")

    def test_publish_action(self):
        draft = make_creative("Draft", status=Status.DRAFT)
        self.client.force_login(self.content)
        response = self.client.post(self.URL, {
            "action": "publish", "_selected_action": [draft.pk], "index": "0",
        }, follow=True)
        self.assertContains(response, "Published 1 creative.")
        draft.refresh_from_db()
        self.assertEqual(draft.status, Status.PUBLISHED)


class StorageTests(TestCase):
    def test_s3_files_go_through_the_private_view(self):
        from leadshub.files import PrivateS3Storage

        storage = PrivateS3Storage(
            bucket_name="leadshub", endpoint_url="http://localhost:9000",
            access_key="key", secret_key="secret",
        )
        self.assertEqual(storage.url("creatives/abc/ad.png"), "/files/creatives/abc/ad.png")


class EnsureStorageBucketTests(TestCase):
    def run_command(self):
        out = StringIO()
        call_command("ensure_storage_bucket", "--wait=0", stdout=out)
        return out.getvalue()

    def test_nothing_to_do_on_disk(self):
        from django.test import override_settings

        # Pinned: a developer's .env may set AWS_STORAGE_BUCKET_NAME.
        disk = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with override_settings(STORAGES=disk):
            self.assertIn("stored on disk", self.run_command())

    def test_creates_a_missing_bucket(self):
        from botocore.exceptions import ClientError
        from django.test import override_settings

        s3 = {
            "default": {
                "BACKEND": "leadshub.files.PrivateS3Storage",
                "OPTIONS": {"bucket_name": "leadshub", "endpoint_url": "http://localhost:9000",
                            "access_key": "key", "secret_key": "secret"},
            },
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        from leadshub.files import PrivateS3Storage

        client = mock.Mock()
        client.head_bucket.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadBucket")
        with override_settings(STORAGES=s3), mock.patch.object(
            PrivateS3Storage, "connection", new_callable=mock.PropertyMock,
            return_value=mock.Mock(meta=mock.Mock(client=client)),
        ):
            self.assertIn("Created bucket leadshub", self.run_command())
        client.create_bucket.assert_called_once_with(Bucket="leadshub")
