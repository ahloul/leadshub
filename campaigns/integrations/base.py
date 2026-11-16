"""What LeadsHub needs from an ad network (docs/INTEGRATIONS.md).

Subclass ``AdPlatformClient`` once per network (``MetaClient``,
``GoogleClient``) and name the class in ``settings.AD_PLATFORM_CLIENTS``.
Each method gets LeadsHub's own objects; the client keeps the network's ids
in ``external_id`` and anything else it needs in ``Campaign.platform_data``.

Rules for implementations:

- Never send age or gender targeting (FR-5.14). The audience preset's
  ``meta_interests``, ``lookalike_source`` and ``geo_targeting`` are the
  internal targeting.
- Raise ``IntegrationError`` with a message staff can act on; callers show
  it in the back office and leave the campaign's status unchanged.
- Don't change ``Campaign.status`` here. The callers move the campaign
  through its life cycle (``Campaign.go_live()`` and so on).
"""


class IntegrationError(Exception):
    """The ad network refused or failed. The message is shown to staff."""


class AdPlatformClient:
    """One ad network. Every method here is a no-op or "not available" by default."""

    def __init__(self, channel):
        self.channel = channel

    # Connecting an office's own accounts (the Pages page)

    def connect_url(self, *, office_id, request):
        """Where "Connect with Meta/Google" sends the owner (OAuth), or None if not available.

        The callback view the integration adds should create or update the
        office's ``ConnectedAccount`` rows with ``external_id`` set and the
        status ``verified``.
        """
        return None

    def verify_account(self, account):
        """Check an account typed by hand. True, False, or None when this client can't tell."""
        return None

    # A campaign's life on the network (called by the review queue and jobs,
    # ROADMAP M4 and M6)

    def launch(self, campaign):
        """Create the campaign on the network, after review approves it.

        Return the network's campaign id; the caller stores it in
        ``campaign.external_id`` and calls ``campaign.go_live()``. An empty
        string means it was set up by hand.
        """
        return ""

    def pause(self, campaign):
        """Stop delivery (the wallet ran low, or the broker paused it)."""

    def resume(self, campaign):
        """Start delivery again."""

    def end(self, campaign):
        """Stop for good (the schedule is complete)."""
