"""Device tokens (PHASE0-SPEC §2): `hlm_<43 base64url chars>` (32 random bytes), stored as sha256 only.

A token is generated once at registration, returned once, and from then on exists only as
`sha256(token)` in `devices.token_sha256`. It is never logged and never compared other than by hash.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

TOKEN_PREFIX = "hlm_"
TOKEN_RE = re.compile(r"^hlm_[A-Za-z0-9_-]{43}$")
ADMIN_PLACEHOLDER_HASH = "reserved:admin"  # can never equal a sha256 hex digest (§2)


def generate_token() -> str:
    """`hlm_` + 32 random bytes, base64url without padding (43 chars)."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """sha256 hex digest of the raw bearer string (this is what `devices.token_sha256` stores)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def looks_like_token(value: str) -> bool:
    return bool(TOKEN_RE.match(value))


def parse_bearer(authorization: str | None) -> str | None:
    """`Authorization: Bearer <token>` -> `<token>`; None when absent or malformed."""
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    value = value.strip()
    return value or None
