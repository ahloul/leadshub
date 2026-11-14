"""Demo creatives for manage.py seed_demo. Never run against production data."""

import datetime
import struct
import zlib

from django.core.files.base import ContentFile
from django.utils import timezone

from .models import AspectRatio, AssetType, Creative, Language, Status


def solid_png(width, height, rgb):
    """A PNG of one colour, without an imaging library."""

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def seed_library(projects):
    """Create the demo creatives once: two brokers can see, one expired, one draft."""
    today = timezone.localdate()
    demo = [
        ("Palm Hills New Cairo launch offer", projects["palm_hills_new_cairo"], Language.ENGLISH,
         AspectRatio.SQUARE, "5% down payment, 8-year plan", today + datetime.timedelta(days=30),
         Status.PUBLISHED, (79, 70, 229)),
        ("شقق أو ويست", projects["o_west"], Language.ARABIC, AspectRatio.VERTICAL,
         "مقدم 5% وتقسيط على 10 سنوات", None, Status.PUBLISHED, (5, 150, 105)),
        ("Summer offer (ended)", projects["palm_hills_new_cairo"], Language.ENGLISH,
         AspectRatio.SQUARE, "Summer prices", today - datetime.timedelta(days=5),
         Status.PUBLISHED, (217, 119, 6)),
        ("New brochure (draft)", projects["o_west"], Language.ENGLISH, AspectRatio.LANDSCAPE,
         "", None, Status.DRAFT, (100, 116, 139)),
    ]
    for title, project, language, aspect, offer, ends, status, colour in demo:
        if Creative.objects.filter(title=title).exists():
            continue
        creative = Creative(
            title=title, project=project, developer=project.developer, area=project.area,
            asset_type=AssetType.IMAGE, language=language, aspect_ratio=aspect, offer=offer,
            offer_expires_on=ends, status=status,
        )
        creative.file.save("demo.png", ContentFile(solid_png(480, 270, colour)), save=False)
        creative.save()
