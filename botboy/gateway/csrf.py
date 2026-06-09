"""CSRF Token protection."""

import hmac
import secrets
import time
from typing import Optional

CSRF_TOKEN_LENGTH = 32
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"


def generate_csrf_token() -> str:
    """Generate a secure random CSRF token."""
    return secrets.token_urlsafe(CSRF_TOKEN_LENGTH)


def verify_csrf_token(header_token: Optional[str], cookie_token: Optional[str]) -> bool:
    """Verify that the CSRF token in the header matches the one in the cookie."""
    if not header_token or not cookie_token:
        return False
    if len(header_token) < 16 or len(cookie_token) < 16:
        return False
    return secrets.compare_digest(header_token, cookie_token)
