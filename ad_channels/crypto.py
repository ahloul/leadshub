"""Encrypts Meta system-user tokens at rest (US-META-01 criterion 4).

Fernet (AES-128-CBC + HMAC-SHA256) from ``cryptography``. Keys come from
``settings.META_TOKEN_KEYS``: the first one encrypts, every one decrypts
(``MultiFernet``), so keys can be rotated without a data migration.

Why not a package such as django-fernet-fields: they are unmaintained, and
the token is read in exactly one place (``BusinessManager.get_token``), so a
plain text column plus these two functions is all we need.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.views.decorators.debug import sensitive_variables
from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class TokenDecryptError(Exception):
    """The stored token can't be decrypted with any configured key."""


def _fernet() -> MultiFernet:
    keys = getattr(settings, "META_TOKEN_KEYS", None) or []
    if not keys:
        raise ImproperlyConfigured("META_TOKEN_KEYS is not set (see .env.example).")
    return MultiFernet([Fernet(key) for key in keys])


@sensitive_variables("plaintext")
def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


@sensitive_variables("plaintext")
def decrypt(ciphertext: str) -> str:
    try:
        plaintext = _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Never include the ciphertext or key in the message.
        raise TokenDecryptError("Stored token can't be decrypted; check META_TOKEN_KEYS.") from None
    return plaintext
