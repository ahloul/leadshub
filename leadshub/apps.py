from django.apps import AppConfig


class LeadshubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "leadshub"

    def ready(self):
        from djust.frameworks import register_adapter

        from .live import LiveFormAdapter

        # LIVEVIEW_CONFIG["css_framework"] = "leadshub" (settings).
        register_adapter("leadshub", LiveFormAdapter())
