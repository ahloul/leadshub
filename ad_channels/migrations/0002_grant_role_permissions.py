"""Gives existing roles the Business Manager and ad account permissions (US-META-01 #22).

New databases get them from ``ROLE_PERMISSIONS`` when ``sync_role_groups``
creates the roles. This covers databases where the roles already exist.
Release note: Platform Admins can now manage Meta Business Managers and ad
accounts; Media Buying Leads can view them.
"""

from django.contrib.auth.management import create_permissions
from django.db import migrations

GRANTS = {
    "Platform Admin": [
        "view_metabusinessmanager",
        "add_metabusinessmanager",
        "change_metabusinessmanager",
        "view_metaadaccount",
        "add_metaadaccount",
        "change_metaadaccount",
        "delete_metabusinessmanager",
        "delete_metaadaccount",
    ],
    "Media Buying Lead": ["view_metabusinessmanager", "view_metaadaccount"],
}


def grant(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    groups = {g.name: g for g in Group.objects.filter(name__in=GRANTS)}
    if not groups:
        return  # New database: sync_role_groups seeds the roles after migrate.
    # Model permissions are normally created after migrate (post_migrate).
    app_config = apps.get_app_config("ad_channels")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None
    for name, codenames in GRANTS.items():
        if name in groups:
            groups[name].permissions.add(
                *Permission.objects.filter(content_type__app_label="ad_channels", codename__in=codenames)
            )


class Migration(migrations.Migration):
    dependencies = [
        ("ad_channels", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
