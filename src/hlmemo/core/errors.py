"""Tool-level errors (PHASE0-SPEC §3): ``{code, message, retryable, details}``.

``ToolError`` is the single exception type the write service raises; the server maps it to
an ``isError`` tool result (or an HTTP status for non-tool routes).
"""

from __future__ import annotations

from typing import Any

RETRYABLE: frozenset[str] = frozenset({"E_DEVICE_PENDING", "E_UNAVAILABLE"})


class ToolError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = code in RETRYABLE
        self.details = details

    def as_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ToolError({self.code!r}, {self.message!r}, {self.details!r})"


def invalid_arg(message: str, **details: Any) -> ToolError:
    return ToolError("E_INVALID_ARG", message, **details)


__all__ = ["ToolError", "invalid_arg", "RETRYABLE"]
