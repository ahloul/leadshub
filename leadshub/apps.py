from django.apps import AppConfig


class LeadshubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "leadshub"

    def ready(self):
        from djust.frameworks import register_adapter

        from .live import LiveFormAdapter

        # LIVEVIEW_CONFIG["css_framework"] = "leadshub" (settings).
        register_adapter("leadshub", LiveFormAdapter())

        # The scheduled jobs in leadshub/jobs.py, written after every migrate.
        # On django_q's signal: Django sends post_migrate only for apps with
        # models, and this one has none.
        from django.apps import apps
        from django.db.models.signals import post_migrate

        from .jobs import sync_schedules

        post_migrate.connect(
            sync_schedules, sender=apps.get_app_config("django_q"), dispatch_uid="leadshub.sync_schedules"
        )
