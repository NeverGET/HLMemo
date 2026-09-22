"""HlmError: the single error type for auth/device/admin routes (PHASE0-SPEC §2, §3).

Codes and `retryable` flags are the spec's closed list; `HTTP_STATUS` is the §2 mapping for
non-tool routes. Tool results (MCP) serialise the same envelope with `isError: true` later.
"""

from __future__ import annotations

from typing import Any

RETRYABLE: dict[str, bool] = {
    "E_AUTH": False,
    "E_DEVICE_PENDING": True,
    "E_FORBIDDEN": False,
    "E_FORBIDDEN_PROJECT": False,
    "E_INVALID_ARG": False,
    "E_NOT_FOUND": False,
    "E_BUDGET_TOO_SMALL": False,
    "E_BUDGET_TOO_LARGE": False,
    "E_REQUEST_ID_CONFLICT": False,
    "E_VERSION_CONFLICT": False,
    "E_SESSION_CLOSED": False,
    "E_TEMPORAL": False,
    "E_CARD_TOO_LARGE": False,
    "E_INVALID_CURSOR": False,
    "E_UNAVAILABLE": True,
    # Not in the spec's list: the register endpoint's 5/min/IP limit (§2 onboarding step 1)
    # needs a code; 429 is the honest status. Flagged as an assumption.
    "E_RATE_LIMITED": True,
}

HTTP_STATUS: dict[str, int] = {
    "E_AUTH": 401,
    "E_DEVICE_PENDING": 403,
    "E_FORBIDDEN": 403,
    "E_FORBIDDEN_PROJECT": 403,
    "E_NOT_FOUND": 404,
    "E_INVALID_ARG": 400,
    "E_INVALID_CURSOR": 400,
    "E_BUDGET_TOO_SMALL": 400,
    "E_BUDGET_TOO_LARGE": 400,
    "E_TEMPORAL": 400,
    "E_CARD_TOO_LARGE": 400,
    "E_REQUEST_ID_CONFLICT": 409,
    "E_VERSION_CONFLICT": 409,
    "E_SESSION_CLOSED": 409,
    "E_UNAVAILABLE": 503,
    "E_RATE_LIMITED": 429,
}


class HlmError(Exception):
    """Error with a spec code; serialises to `{code, message, retryable, details}`."""

    def __init__(self, code: str, message: str = "", details: dict[str, Any] | None = None) -> None:
        if code not in RETRYABLE:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.details: dict[str, Any] = details or {}

    @property
    def retryable(self) -> bool:
        return RETRYABLE[self.code]

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 400)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }
