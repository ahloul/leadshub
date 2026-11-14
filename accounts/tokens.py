"""The token in a sign-up's email confirmation link (ROADMAP Q6)."""

from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailConfirmationTokenGenerator(PasswordResetTokenGenerator):
    """Django's password-reset tokens, under their own salt.

    A token stops working once the email is confirmed or after
    ``PASSWORD_RESET_TIMEOUT``, and can't be used as a password-reset or
    invitation token (or the other way round).
    """

    key_salt = "accounts.tokens.EmailConfirmationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        registration = getattr(user, "office_registration", None)
        confirmed_at = registration.email_confirmed_at if registration else None
        confirmed = "" if confirmed_at is None else confirmed_at.replace(microsecond=0, tzinfo=None)
        return f"{user.pk}{user.email}{confirmed}{timestamp}"


email_confirmation_token = EmailConfirmationTokenGenerator()
