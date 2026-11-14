"""The default client: no network calls. Staff create and run the ads by hand."""

from .base import AdPlatformClient


class ManualClient(AdPlatformClient):
    """Used for every channel until ``settings.AD_PLATFORM_CLIENTS`` names a real client."""
