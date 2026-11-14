"""Roles that keep the creative library and the audience presets (ROADMAP M2).

``accounts.roles`` gives these to new databases (``sync_role_groups`` after
migrate). This grants them where the roles already exist; their other
permissions are left as they are.

The two models are new in this release, and Django creates a model's
permissions only after migrate finishes, so this creates them first.
"""

from django.db import migrations

_ACTIONS = ("view", "add", "change", "delete")
_CREATIVES = [("creatives", f"{action}_creative") for action in _ACTIONS]
_PRESETS = [("catalog", f"{action}_audiencepreset") for action in _ACTIONS]

GRANTS = {
    "Platform Admin": _CREATIVES + _PRESETS,
    "Media Buying Lead": _PRESETS,
    "Content Manager": _CREATIVES,
}


def _create_permissions(apps):
    from django.contrib.auth.management import create_permissions

    for label in ("creatives", "catalog"):
        app_config = apps.get_app_config(label)
        # A migration's app configs have no models module, and
        # create_permissions skips such apps; put back whatever was there.
        original = getattr(app_config, "models_module", None)
        app_config.models_module = original or True
        try:
            create_permissions(app_config, apps=apps, verbosity=0)
        finally:
            app_config.models_module = original


def _permissions(apps, names):
    from django.db.models import Q

    Permission = apps.get_model("auth", "Permission")
    query = Q(pk__in=[])
    for app_label, codename in names:
        query |= Q(content_type__app_label=app_label, codename=codename)
    return Permission.objects.filter(query)


def grant(apps, schema_editor):
    _create_permissions(apps)
    Group = apps.get_model("auth", "Group")
    for role, names in GRANTS.items():
        group = Group.objects.filter(name=role).first()
        # On a new database the roles are created after migrate, with these.
        if group is not None:
            group.permissions.add(*_permissions(apps, names))


def revoke(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for role, names in GRANTS.items():
        group = Group.objects.filter(name=role).first()
        if group is not None:
            group.permissions.remove(*_permissions(apps, names))


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_platform_admin_opens_offices"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("catalog", "0002_audiencepreset"),
        ("creatives", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
