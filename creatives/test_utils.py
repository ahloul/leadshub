"""Helpers for tests that upload files or need creatives."""

import shutil
import tempfile

from django.core.files.base import ContentFile
from django.test import override_settings

from catalog.models import Area, Developer, Project

from .demo import solid_png
from .models import AspectRatio, AssetType, Creative, Language, Status


class TemporaryUploadsMixin:
    """Keep the test class's uploads in a temporary folder on disk, removed afterwards."""

    @classmethod
    def setUpClass(cls):
        cls._uploads_dir = tempfile.mkdtemp(prefix="leadshub-test-uploads-")
        cls._uploads_override = override_settings(
            STORAGES={
                "default": {
                    "BACKEND": "leadshub.files.PrivateFileSystemStorage",
                    "OPTIONS": {"location": cls._uploads_dir},
                },
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            }
        )
        cls._uploads_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._uploads_override.disable()
        shutil.rmtree(cls._uploads_dir, ignore_errors=True)


def make_project(name="Palm Hills New Cairo", area="New Cairo", developer="Palm Hills"):
    area, _ = Area.objects.get_or_create(name_en=area)
    developer, _ = Developer.objects.get_or_create(name_en=developer)
    project, _ = Project.objects.get_or_create(
        name_en=name, developer=developer, defaults={"area": area}
    )
    return project


def make_creative(title="Launch offer", *, project=None, filename="ad.png", content=None, **fields):
    """A published image creative for ``project`` (one is made if not given)."""
    project = project or make_project()
    fields.setdefault("asset_type", AssetType.IMAGE)
    fields.setdefault("language", Language.ENGLISH)
    fields.setdefault("aspect_ratio", AspectRatio.SQUARE)
    fields.setdefault("status", Status.PUBLISHED)
    creative = Creative(
        title=title, project=project, developer=project.developer, area=project.area, **fields
    )
    creative.file.save(filename, ContentFile(content or solid_png(8, 8, (79, 70, 229))), save=False)
    creative.save()
    return creative
