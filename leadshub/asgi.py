"""
ASGI config for leadshub project.

Wraps the HTTP handler with ``ASGIStaticFilesHandler`` so static files
(client.js, CSS, etc.) are served correctly under an ASGI server like
uvicorn without needing a separate static file server or extra deps.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "leadshub.settings")

# Initialize Django's app registry BEFORE importing anything that touches
# models / consumers (channels, djust.websocket). ``get_asgi_application()``
# calls ``django.setup()`` internally, so the imports below are safe.
django_asgi_app = get_asgi_application()

from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402
from django.urls import path  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from djust.websocket import LiveViewConsumer  # noqa: E402

# CSWSH defense: ``AllowedHostsOriginValidator`` rejects WebSocket
# handshakes whose Origin header is not in ``settings.ALLOWED_HOSTS``.
application = ProtocolTypeRouter(
    {
        "http": ASGIStaticFilesHandler(django_asgi_app),
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                URLRouter(
                    [
                        path("ws/live/", LiveViewConsumer.as_asgi()),
                    ]
                )
            )
        ),
    }
)
