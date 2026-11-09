"""Shared building blocks for the project's live views."""

from django.utils import translation


class PageLanguageMixin:
    """Render live updates in the page's language (/en/ or /ar/).

    Workaround for a djust 1.2.8 gap: Django's LocaleMiddleware only runs for
    HTTP requests, so renders over the WebSocket used the default language and
    an Arabic page switched back to English after the live mount.
    Covered by ``leadshub.tests.PageLanguageMixinTests``.

    The language comes from the page URL's prefix at mount time and is
    activated around every render. Every live view must include it and call
    ``super().mount()``.

    Remove it once djust activates the page language on the live path itself.
    Check after each djust upgrade: drop the mixin from DashboardView, open
    /ar/ in a browser, and see whether the live region stays Arabic after the
    WebSocket mounts.
    """

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
