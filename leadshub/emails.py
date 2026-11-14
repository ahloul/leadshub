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
commit, render in another language, add the layout's own context or go
through the task queue, which every caller would otherwise repeat.
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

    The email is rendered now, in ``language``, and sent by the task queue
    (``deliver``, leadshub/jobs.py), so the page never waits for the mail
    server. A failed send is tried again by the queue, then kept under Failed
    tasks in the admin.
    """
    subject, text, html = render_email(name, context, language=language)
    recipients = list(recipients)
    if not recipients:
        return

    # A named function: Django logs a robust callback's failure by its
    # __qualname__. Robust, so a queue failure never breaks the page.
    def queue_email():
        from .jobs import enqueue

        enqueue("leadshub.emails.deliver", subject, text, html, recipients)

    transaction.on_commit(queue_email, robust=True)


def deliver(subject, text, html, recipients):
    """Send one rendered email (a queued task). Raises on failure, so the queue retries it."""
    message = EmailMultiAlternatives(subject, text, None, recipients)
    message.attach_alternative(html, "text/html")
    message.send()
