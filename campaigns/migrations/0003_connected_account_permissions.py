"""Who manages connected accounts (ROADMAP M3; SRS §2.1, §2.2).

``accounts.roles`` gives these to new databases (``sync_role_groups`` after
migrate). This grants them where the roles already exist; their other
permissions are left as they are:

- Broker Owner: ``manage_connected_accounts`` (the office's own pages).
- Platform Admin and Media Buying Lead: view and change connected accounts
  in the admin, to verify them ("manage ad accounts").

Django creates new permissions only after migrate finishes, so this creates
them first.
"""

from django.db import migrations

_ADMIN = [("campaigns", "view_connectedaccount"), ("campaigns", "change_connectedaccount")]

GRANTS = {
    "Broker Owner": [("accounts", "manage_connected_accounts")],
    "Platform Admin": _ADMIN,
    "Media Buying Lead": _ADMIN,
}


def _create_permissions(apps):
    from django.contrib.auth.management import create_permissions

    for label in ("accounts", "campaigns"):
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
        ("accounts", "0007_alter_capability_options"),
        ("campaigns", "0002_channels_and_connected_accounts"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
