from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = _("Accounts")

    def ready(self):
        from .roles import sync_role_groups

        post_migrate.connect(sync_role_groups, dispatch_uid="accounts.sync_role_groups")

