# Yang-gumi release: 1.3.0
from __future__ import annotations

import hashlib
import hmac


SHARE_COOKIE_NAME = "yanggumi_share_session"
_COOKIE_PURPOSE = b"Yang-gumi read-only share cookie v1"


def session_cookie_value(token: str) -> str:
    """Return a non-reversible cookie value tied to the current share token."""
    return hmac.new(token.encode("utf-8"), _COOKIE_PURPOSE, hashlib.sha256).hexdigest()


def valid_session_cookie(value: str | None, token: str) -> bool:
    if not value or not token:
        return False
    return hmac.compare_digest(str(value), session_cookie_value(token))
