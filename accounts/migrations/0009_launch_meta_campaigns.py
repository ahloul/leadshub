"""The Meta launch capability (US-META-02), granted to existing media buying roles.

New databases get it from ``ROLE_PERMISSIONS`` (``sync_role_groups`` after
migrate). Release note: Media Buyers and Media Buying Leads can now launch
ready campaigns on Meta and manage them there.
"""

from django.db import migrations

ROLES = ["Media Buyer", "Media Buying Lead"]


def _permission(apps):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    content_type, _ = ContentType.objects.get_or_create(app_label="accounts", model="capability")
    permission, _ = Permission.objects.get_or_create(
        content_type=content_type,
        codename="launch_meta_campaigns",
        defaults={"name": "Launch and manage campaigns on Meta"},
    )
    return permission


def grant(apps, schema_editor):
    groups = apps.get_model("auth", "Group").objects.filter(name__in=ROLES)
    if groups:
        permission = _permission(apps)
        for group in groups:
            group.permissions.add(permission)


def revoke(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    for group in apps.get_model("auth", "Group").objects.filter(name__in=ROLES):
        group.permissions.remove(
            *Permission.objects.filter(content_type__app_label="accounts", codename="launch_meta_campaigns")
        )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_background_job_permissions'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='capability',
            options={'default_permissions': (), 'managed': False, 'permissions': [('view_internal_financials', 'View actual ad spend and media margin'), ('review_campaign_tasks', 'Approve or reject campaign tasks'), ('manage_master_data', 'Manage master data and ad accounts'), ('manage_fx_and_pricing', 'Manage FX rates and price overrides'), ('run_campaigns', 'Create and run campaigns'), ('manage_wallet', 'Manage wallet and top up funds'), ('work_leads', 'Access and work assigned leads'), ('view_project_metrics', 'View aggregate project metrics'), ('manage_creative_library', 'Manage the creative library'), ('manage_team', 'Invite and manage office team members'), ('assign_campaign_tasks', 'Assign campaign tasks to media buyers'), ('manage_connected_accounts', "Connect the office's own pages and ad accounts"), ('launch_meta_campaigns', 'Launch and manage campaigns on Meta'), ('access_back_office', 'Open the internal back office'), ('access_client_portal', 'Open the broker client portal'), ('access_developer_view', 'Open the developer read-only view')]},
        ),
        migrations.RunPython(grant, revoke),
    ]
