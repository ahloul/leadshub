"""Demo master data for manage.py seed_demo. Never run against production data."""

from .models import Area, AudiencePreset, Developer, Project, UnitType


def seed_catalog():
    """Create the demo catalog once; returns the projects by short name."""
    new_cairo, _ = Area.objects.get_or_create(name_en="New Cairo", defaults={"name_ar": "القاهرة الجديدة"})
    october, _ = Area.objects.get_or_create(
        name_en="6th of October", defaults={"name_ar": "السادس من أكتوبر"}
    )
    palm_hills, _ = Developer.objects.get_or_create(name_en="Palm Hills", defaults={"name_ar": "بالم هيلز"})
    orascom, _ = Developer.objects.get_or_create(name_en="Orascom", defaults={"name_ar": "أوراسكوم"})
    apartment, _ = UnitType.objects.get_or_create(name_en="Apartment", defaults={"name_ar": "شقة"})
    UnitType.objects.get_or_create(name_en="Villa", defaults={"name_ar": "فيلا"})
    phnc, _ = Project.objects.get_or_create(
        developer=palm_hills,
        name_en="Palm Hills New Cairo",
        defaults={"name_ar": "بالم هيلز القاهرة الجديدة", "area": new_cairo, "commission_rate_percent": 3.5},
    )
    o_west, _ = Project.objects.get_or_create(
        developer=orascom,
        name_en="O West",
        defaults={"name_ar": "أو ويست", "area": october, "commission_rate_percent": 3},
    )
    for project in (phnc, o_west):
        project.unit_types.add(apartment)
    AudiencePreset.objects.get_or_create(
        name_en="New Cairo compound buyers, higher income",
        defaults={
            "name_ar": "مشترو الكمبوندات في القاهرة الجديدة، دخل أعلى",
            "area": new_cairo,
            "meta_interests": "Real estate\nCompounds\nLuxury",
            "lookalike_source": "closed_won_1pct",
            "geo_targeting": "New Cairo + 10 km",
        },
    )
    return {"palm_hills_new_cairo": phnc, "o_west": o_west}
