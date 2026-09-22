"""Signed cursors (PHASE0-SPEC §2/§3): HMAC-SHA256 over `{device_id, token_generation, payload}`.

A cursor presented by another device, or after the issuing device's `token_generation` changed
(revoke / rotation / admin restart), fails verification -> `E_INVALID_CURSOR`. The payload
(`{version_id, ordinal, ...}`) is opaque to callers of this module.

Format: `base64url(canonical JSON {"d","g","p"}) . base64url(hmac)` — both parts unpadded.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Any

from hlmemo.auth.context import AuthContext
from hlmemo.auth.errors import HlmError

CURSOR_SECRET_ENV = "HLM_CURSOR_SECRET"


def load_cursor_secret() -> bytes:
    """`HLM_CURSOR_SECRET` from the environment, else a fresh per-process secret.

    Cursors are short-lived pagination tokens; with a per-process secret they simply do not
    survive an API restart (admin cursors never do anyway, §2 generation bump).
    """
    raw = os.environ.get(CURSOR_SECRET_ENV, "").strip()
    return raw.encode("utf-8") if raw else secrets.token_bytes(32)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _mac(secret: bytes, body: bytes) -> bytes:
    return hmac.new(secret, body, hashlib.sha256).digest()


def sign_cursor(secret: bytes, ctx: AuthContext, payload: dict[str, Any]) -> str:
    """Issue a cursor bound to `ctx.device_id` and `ctx.token_generation`."""
    body = _canonical({"d": ctx.device_id, "g": ctx.token_generation, "p": payload})
    return f"{_b64e(body)}.{_b64e(_mac(secret, body))}"


def verify_cursor(secret: bytes, cursor: object, ctx: AuthContext) -> dict[str, Any]:
    """Verify signature + binding for the calling device; return the payload or raise E_INVALID_CURSOR."""
    if not isinstance(cursor, str) or cursor.count(".") != 1:
        raise HlmError("E_INVALID_CURSOR", "malformed cursor")
    body_b64, mac_b64 = cursor.split(".", 1)
    try:
        body = _b64d(body_b64)
        mac = _b64d(mac_b64)
    except (ValueError, TypeError) as exc:
        raise HlmError("E_INVALID_CURSOR", "malformed cursor") from exc
    if not hmac.compare_digest(mac, _mac(secret, body)):
        raise HlmError("E_INVALID_CURSOR", "bad cursor signature")
    try:
        doc = json.loads(body)
    except ValueError as exc:
        raise HlmError("E_INVALID_CURSOR", "malformed cursor") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("p"), dict):
        raise HlmError("E_INVALID_CURSOR", "malformed cursor")
    if doc.get("d") != ctx.device_id or doc.get("g") != ctx.token_generation:
        # another device, or the issuing device's generation moved (revoke / rotation / restart)
        raise HlmError("E_INVALID_CURSOR", "cursor not valid for this device")
    return doc["p"]
