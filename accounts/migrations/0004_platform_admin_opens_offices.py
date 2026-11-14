"""Platform Admins open broker offices in the admin (ROADMAP M1, SRS §2.1).

``accounts.roles`` gives these permissions to the Platform Admin role on new
databases (``sync_role_groups`` after migrate). This grants them on databases
created before; the role's other permissions are left as they are.
"""

from django.db import migrations

ROLE = "Platform Admin"
CODENAMES = ["add_brokeroffice", "change_brokeroffice"]


def _role_and_permissions(apps):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    group = Group.objects.filter(name=ROLE).first()
    perms = Permission.objects.filter(content_type__app_label="accounts", codename__in=CODENAMES)
    return group, perms


def grant(apps, schema_editor):
    group, perms = _role_and_permissions(apps)
    # On a new database the role doesn't exist yet: it is created after
    # migrate, already with these permissions.
    if group is not None:
        group.permissions.add(*perms)


def revoke(apps, schema_editor):
    group, perms = _role_and_permissions(apps)
    if group is not None:
        group.permissions.remove(*perms)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_role_profiles"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
