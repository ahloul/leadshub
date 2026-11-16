"""Platform Admins see the background jobs in the admin (django-q2; ROADMAP section 6).

``accounts.roles`` gives these to new databases (``sync_role_groups`` after
migrate). This grants them where the Platform Admin role already exists.
Django creates new permissions only after migrate finishes, so this creates
django_q's first.
"""

from django.db import migrations

NAMES = [
    "view_schedule", "view_success", "view_failure", "change_failure", "delete_failure", "view_ormq",
]


def _permissions(apps):
    from django.contrib.auth.management import create_permissions

    app_config = apps.get_app_config("django_q")
    original = getattr(app_config, "models_module", None)
    app_config.models_module = original or True
    try:
        create_permissions(app_config, apps=apps, verbosity=0)
    finally:
        app_config.models_module = original
    Permission = apps.get_model("auth", "Permission")
    return Permission.objects.filter(content_type__app_label="django_q", codename__in=NAMES)


def grant(apps, schema_editor):
    group = apps.get_model("auth", "Group").objects.filter(name="Platform Admin").first()
    if group is not None:
        group.permissions.add(*_permissions(apps))


def revoke(apps, schema_editor):
    group = apps.get_model("auth", "Group").objects.filter(name="Platform Admin").first()
    if group is not None:
        Permission = apps.get_model("auth", "Permission")
        group.permissions.remove(
            *Permission.objects.filter(content_type__app_label="django_q", codename__in=NAMES)
        )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_alter_capability_options"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(grant, revoke)]
