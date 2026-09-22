"""JSON error envelope + exception mapping for HTTP routes (PHASE0-SPEC §2 status table)."""

from __future__ import annotations

import logging
from typing import Any

from psycopg import errors as pgerrors
from pydantic import ValidationError
from starlette.responses import JSONResponse

from hlmemo.auth.errors import HTTP_STATUS, HlmError

log = logging.getLogger("hlmemo.server")

try:  # core.errors.ToolError is raised by the write service; map it to the same HTTP envelope
    from hlmemo.core.errors import ToolError
except ImportError:  # pragma: no cover - module lands with the write service

    class ToolError(Exception):  # type: ignore[no-redef]
        code = "E_UNAVAILABLE"
        message = ""
        retryable = True
        details: dict[str, Any] = {}


ERROR_TYPES: tuple[type[Exception], ...] = (HlmError, ToolError)


def error_response(err: Exception) -> JSONResponse:
    """`{code, message, retryable, details}` with the §2 HTTP status, for HlmError or ToolError."""
    code = getattr(err, "code", "E_UNAVAILABLE")
    body = {
        "code": code,
        "message": getattr(err, "message", str(err)),
        "retryable": bool(getattr(err, "retryable", False)),
        "details": getattr(err, "details", {}) or {},
    }
    return JSONResponse(body, status_code=HTTP_STATUS.get(code, 400))


def invalid_arg(message: str, **details: Any) -> HlmError:
    return HlmError("E_INVALID_ARG", message, details)


def from_validation_error(exc: ValidationError) -> HlmError:
    errs = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
    return HlmError("E_INVALID_ARG", "invalid request body", {"errors": errs})


def from_db_error(exc: pgerrors.Error) -> HlmError | None:
    """Map constraint violations that mean 'bad argument' to E_INVALID_ARG; None = not ours."""
    if isinstance(exc, pgerrors.UniqueViolation):
        constraint = getattr(exc.diag, "constraint_name", None) or "unique"
        return HlmError("E_INVALID_ARG", "value already in use", {"constraint": constraint})
    if isinstance(exc, pgerrors.CheckViolation):
        constraint = getattr(exc.diag, "constraint_name", None) or "check"
        return HlmError("E_INVALID_ARG", "value violates a constraint", {"constraint": constraint})
    return None
