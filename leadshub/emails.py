"""Emails in the LeadsHub layout: HTML with a plain-text copy, sent after commit.

Every email is three templates under one name, for example
``accounts/email/confirm_email``:

- ``<name>_subject.txt``: one line;
- ``<name>_body.txt``: the plain-text copy, extending ``email/base.txt``;
- ``<name>_body.html``: the HTML, extending ``email/base.html`` (the brand
  header, the card and the footer), with the parts in ``templates/email/``:
  ``button.html``, ``link_fallback.html`` and ``details.html``.

They are rendered with Django's template engine, not djust's: djust's adds
attributes to tags when ``DEBUG`` is on. Write in the reader's language by
passing ``language``; the layout turns right-to-left for Arabic. Values in the
context are escaped in the HTML, so names people type are safe to pass.

Why not ``send_mail(html_message=...)``: it can't wait for the transaction to
commit, render in another language or add the layout's own context, which every
caller would otherwise repeat.
"""

from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import translation


def render_email(name, context, *, language=None):
    """Return ``(subject, text, html)`` for the email ``name`` in ``language``."""
    with translation.override(language or translation.get_language()):
        rtl = translation.get_language_bidi()
        context = {
            **context,
            # Email clients ignore logical CSS (text-align: start), so the
            # layout is told which side the text starts on.
            "direction": "rtl" if rtl else "ltr",
            "start": "right" if rtl else "left",
            "end": "left" if rtl else "right",
        }
        subject = render_to_string(f"{name}_subject.txt", context, using="django")
        text = render_to_string(f"{name}_body.txt", context, using="django")
        html = render_to_string(f"{name}_body.html", context, using="django")
    return " ".join(subject.split()), text.strip() + "\n", html


def send_after_commit(name, context, recipients, *, language=None):
    """Email ``recipients`` the email ``name`` once the transaction commits.

    A mail server failure is logged (``robust``) and doesn't break the page;
    callers offer a way to send the email again.
    """
    subject, text, html = render_email(name, context, language=language)
    recipients = list(recipients)

    # A named function, not functools.partial: Django logs a robust callback's
    # failure by its __qualname__, which a partial doesn't have.
    def send_email():
        message = EmailMultiAlternatives(subject, text, None, recipients)
        message.attach_alternative(html, "text/html")
        message.send()

    transaction.on_commit(send_email, robust=True)
