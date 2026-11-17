from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AdChannelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # Not "channels": that import name belongs to Django Channels (WebSockets).
    name = "ad_channels"
    verbose_name = _("Meta ads")
