from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover
    Fernet = None


def get_email_fernet() -> Fernet:
    if Fernet is None:
        raise ImproperlyConfigured(
            "Missing dependency: install 'cryptography' to use email encryption."
        )
    key = getattr(settings, "EMAIL_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise ImproperlyConfigured("EMAIL_TOKEN_ENCRYPTION_KEY must be configured.")
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:  # pragma: no cover
        raise ImproperlyConfigured("EMAIL_TOKEN_ENCRYPTION_KEY is invalid.") from exc


def encrypt_text(value: str) -> str:
    if not value:
        return ""
    return get_email_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: str) -> str:
    if not value:
        return ""
    return get_email_fernet().decrypt(value.encode("utf-8")).decode("utf-8")

