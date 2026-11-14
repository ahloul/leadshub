"""Private uploads: where they are stored and who may open them.

Every upload (creatives now; Project X contracts later) is private. Files are
never served from a public URL: a file field's ``.url`` points at
``PrivateFileView``, which asks the app that owns the file whether the
signed-in person may open it, then streams it.

Storage is chosen in settings (``STORAGES["default"]``): S3 in production, an
S3-compatible server locally (RustFS in docker-compose.yml), or ``MEDIA_ROOT``
on disk when no bucket is configured (tests, quick local runs).
"""

import mimetypes
import posixpath

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import FileSystemStorage, default_storage
from django.http import FileResponse, Http404
from django.urls import reverse
from django.views import View
from storages.backends.s3 import S3Storage

# Shown in the browser; everything else is downloaded. Uploads are limited to
# these and zip files (no HTML or SVG, which could run scripts on this site).
INLINE_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4", "video/webm", "video/quicktime"}
)


class PrivateFilesMixin:
    """A storage whose file URLs go through ``PrivateFileView``."""

    def url(self, name, *args, **kwargs):
        return reverse("private_file", kwargs={"name": name})


class PrivateFileSystemStorage(PrivateFilesMixin, FileSystemStorage):
    """Uploads in ``MEDIA_ROOT``, opened through ``PrivateFileView``."""


class PrivateS3Storage(PrivateFilesMixin, S3Storage):
    """Uploads in an S3 bucket, opened through ``PrivateFileView``.

    The bucket stays private: the app streams files after its own permission
    check, so no signed links are handed out. That also works when the app
    reaches the bucket on a host the browser can't (``storage`` inside
    docker-compose).
    """


def _access_rules():
    """The first folder of a file's name, and the rule of the app that owns it.

    Each rule is ``rule(user, name) -> bool``.
    """
    from creatives import services as creative_services

    return {"creatives": creative_services.may_open_file}


class PrivateFileView(LoginRequiredMixin, View):
    """Streams an upload once its owning app agrees that this person may open it.

    Anyone else gets 404, so the file's existence isn't revealed.
    """

    def get(self, request, name):
        rule = _access_rules().get(name.split("/", 1)[0])
        if rule is None or not rule(request.user, name) or not default_storage.exists(name):
            raise Http404
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        response = FileResponse(
            default_storage.open(name, "rb"),
            as_attachment=content_type not in INLINE_TYPES,
            filename=posixpath.basename(name),
            content_type=content_type,
        )
        response["Cache-Control"] = "private, max-age=300"
        # Belt and braces: even if a browser rendered the file as a page, no
        # script in it could run with this site's permissions.
        response["Content-Security-Policy"] = "sandbox"
        return response
