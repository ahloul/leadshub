"""Shared building blocks for the project's live views."""

import functools
import inspect

from django.urls import reverse
from django.utils import translation
from djust.decorators import is_event_handler
from djust.frameworks import TailwindAdapter


class PageLanguageMixin:
    """Render live updates and run event handlers in the page's language (/en/ or /ar/).

    Workaround for a djust 1.2.8 gap: Django's LocaleMiddleware only runs for
    HTTP requests, so work done over the WebSocket used the default language.
    An Arabic page switched back to English after the live mount, and text made
    by event handlers (form errors, messages, emails) came out in English.
    Covered by ``leadshub.tests.PageLanguageMixinTests``.

    The language comes from the page URL's prefix at mount time and is
    activated around every render and every event handler, including the
    handlers views inherit (FormMixin's ``submit_form`` and ``validate_field``).
    It also gives each view a ``login_url`` in the page's language, as a
    plain string (see ``_login_url_in_page_language``). Every live view must
    include it and call ``super().mount()``.

    Remove it once djust activates the page language on the live path itself.
    Check after each djust upgrade: drop the mixin from DashboardView, open
    /ar/ in a browser, and see whether the live region stays Arabic after the
    WebSocket mounts. Then check that an invalid form on /ar/ shows Arabic
    errors.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # djust looks handlers up by name on the view instance, so replacing
        # them on the class here covers the WebSocket, the HTTP fallback and
        # LiveViewTestClient alike. djust has no per-event hook for this.
        for name in dir(cls):
            handler = inspect.getattr_static(cls, name, None)
            if is_event_handler(handler) and not getattr(handler, "_in_page_language", False):
                setattr(cls, name, _in_page_language(handler))
        # Set on each view class: djust's LoginRequiredLiveViewMixin comes
        # first in the bases and declares login_url = None.
        if "login_url" not in cls.__dict__:
            cls.login_url = property(_login_url_in_page_language)

    def mount(self, request, **kwargs):
        self._page_language = (
            translation.get_language_from_path(request.path)
            or translation.get_language_from_request(request)
        )
        return super().mount(request, **kwargs)

    def render(self, *args, **kwargs):
        with translation.override(self._language_for_render()):
            return super().render(*args, **kwargs)

    def render_with_diff(self, *args, **kwargs):
        with translation.override(self._language_for_render()):
            return super().render_with_diff(*args, **kwargs)

    # The WebSocket mount calls this before render_with_diff; it serializes
    # models (e.g. get_FOO_display choice labels), so it needs the language too.
    def _sync_state_to_rust(self, *args, **kwargs):
        with translation.override(self._language_for_render()):
            return super()._sync_state_to_rust(*args, **kwargs)

    def _language_for_render(self):
        return getattr(self, "_page_language", None) or translation.get_language()


def _login_url_in_page_language(view):
    """The sign-in page in the page's language, as a plain string.

    djust sends a person whose session ended a ``navigate`` frame to
    ``login_url``, falling back to ``settings.LOGIN_URL``. That setting is
    ``reverse_lazy("login")``, which djust 1.2.8 can't put in a frame: the
    live mount failed with a server error. With this, the frame is sent and
    names the page's own language.

    djust 1.2.8's WebSocket client doesn't act on ``navigate`` frames yet, so
    the browser shows "reconnecting" until the person reloads (ROADMAP M1,
    known issues). Covered by ``TeamPageOverTheSocketTests``.
    """
    language = getattr(view, "_page_language", None)
    request = getattr(view, "request", None)
    if language is None and request is not None:
        language = translation.get_language_from_path(request.path)
    with translation.override(language or translation.get_language()):
        return reverse("login")


def _in_page_language(handler):
    """``handler``, run with the view's page language active.

    ``functools.wraps`` keeps djust's decorator metadata (``@event_handler``,
    ``@permission_required``, ``@rate_limit``) and the signature it reads to
    coerce parameters.
    """
    if inspect.iscoroutinefunction(handler):

        @functools.wraps(handler)
        async def run(self, *args, **kwargs):
            with translation.override(self._language_for_render()):
                return await handler(self, *args, **kwargs)

    else:

        @functools.wraps(handler)
        def run(self, *args, **kwargs):
            with translation.override(self._language_for_render()):
                return handler(self, *args, **kwargs)

    run._in_page_language = True
    return run


class LiveFormAdapter(TailwindAdapter):
    """How ``{% live_form %}`` draws fields: the project's classes, for RTL and dark mode.

    The field, label and error classes come from ``LIVEVIEW_CONFIG["leadshub"]``
    (component classes in static/css/input.css). djust 1.2.8 hard-codes the
    required-field marker and the help-text classes in its adapters, without
    dark-mode colours, so this subclass sets those two. Registered as the
    "leadshub" framework in ``leadshub.apps``.
    """

    required_marker = ' <span class="field-required" aria-hidden="true">*</span>'
    help_text_class = "field-help"
