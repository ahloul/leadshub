"""Where Meta and Google plug in (docs/INTEGRATIONS.md).

LeadsHub talks to each ad network through one client per channel, a subclass
of ``base.AdPlatformClient``. ``client_for(channel)`` returns the client
named in ``settings.AD_PLATFORM_CLIENTS``; until the real ones exist, every
channel gets ``manual.ManualClient``, which does nothing and lets staff do
the work by hand.

The rest of the app calls only ``client_for()`` and the base class's
methods, so the integration replaces no code outside this package.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from ..models import Channel

DEFAULT_CLIENT = "campaigns.integrations.manual.ManualClient"


def client_for(channel):
    """The client for ``channel`` ("meta" or "google")."""
    channel = Channel(channel)
    path = getattr(settings, "AD_PLATFORM_CLIENTS", {}).get(channel.value, DEFAULT_CLIENT)
    return import_string(path)(channel)
