"""Status-gate / authorization middleware (PHASE0-SPEC §2).

Pure ASGI middleware that runs BEFORE routing and before any MCP session manager:

  * `GET /health` needs no bearer; with one it resolves the device in any status so a pending
    device can poll for approval.
  * `POST /devices/register` needs no bearer (it is how a device obtains one); a bearer that is
    present is still gated like everywhere else.
  * Every other path (including `/mcp` and unknown routes) requires a `trusted` device:
    unknown / revoked token -> 401 `E_AUTH`; pending -> 403 `E_DEVICE_PENDING`.

The middleware owns the request transaction: it takes a pooled connection, resolves the device
under `FOR SHARE`, exposes `request.state.conn` / `request.state.auth` / `request.state.device`
to the route, commits when the route finished (or rolls back on an `HlmError`, which it maps to
the JSON envelope), and only then refreshes `last_seen_at` (≤ once per 60 s) outside the request
transaction so that it never deadlocks with concurrent share holders.

Commit before acknowledgement (codex review C1). Tool handlers and routes run inside savepoints of
the request transaction; nothing they produce is durable until the outer ``COMMIT`` here. The
response the route produced is therefore *buffered* and only released to the client after that
commit succeeded — a client that sees a success envelope holds a durable write. If the commit
fails, the buffered response is discarded and the client receives the ``E_UNAVAILABLE`` (503,
retryable) envelope instead. The configured MCP transport uses finite JSON responses; all body
chunks are buffered, including responses split across several ASGI messages.

`GET /health` (liveness) and `GET /ready` (readiness) need no bearer; with one they are gated
like every other route except that `/health` also resolves pending/revoked devices (§2 poll).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hlmemo.auth.errors import HlmError
from hlmemo.auth.resolve import resolve
from hlmemo.auth.tokens import parse_bearer
from hlmemo.db import auth_queries as q
from hlmemo.server.errors import ERROR_TYPES, error_response

log = logging.getLogger("hlmemo.server.auth")

HEALTH = ("GET", "/health")
READY = ("GET", "/ready")
REGISTER = ("POST", "/devices/register")
NO_BEARER_OK = (HEALTH, READY)
COMMIT_FAILED = HlmError(
    "E_UNAVAILABLE", "request commit could not be confirmed; retry with the same request_id", {}
)


class RateLimiter:
    """Fixed-window per-key counter (register: 5/min/IP). In-memory, per process."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        dq = self._hits.setdefault(key, deque())
        while dq and now - dq[0] > self.window_s:
            dq.popleft()
        if len(dq) >= self.limit:
            return False
        dq.append(now)
        return True


class AuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def commit_request(self, conn: Any) -> None:
        """The outer ``COMMIT`` of the request transaction (seam for the G6 commit-failure test)."""
        await conn.commit()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state: dict[str, Any] = scope.setdefault("state", {})
        state.setdefault("auth", None)
        state.setdefault("device", None)
        state.setdefault("conn", None)
        headers = Headers(scope=scope)
        bearer = parse_bearer(headers.get("authorization"))
        client = headers.get("x-hlm-client") or headers.get("user-agent") or "unknown/0"
        route = (scope["method"], scope["path"].rstrip("/") or "/")
        is_health = route == HEALTH
        is_register = route == REGISTER

        if bearer is None and route in NO_BEARER_OK:
            await self.app(scope, receive, send)
            return
        if bearer is None and not is_register:
            await error_response(HlmError("E_AUTH", "missing bearer token"))(scope, receive, send)
            return

        pool = scope["app"].state.pool
        buffered: list[Message] = []  # response messages held back until the outer commit
        sent_any = False  # at least one message reached the client

        async def flush() -> None:
            nonlocal sent_any
            while buffered:
                sent_any = True
                await send(buffered.pop(0))

        async def send_wrapper(message: Message) -> None:
            buffered.append(message)

        async with pool.connection() as conn:
            ctx = None
            if bearer is not None:
                try:
                    ctx, row = await resolve(
                        conn, bearer, client=client, allow_pending=is_health, allow_revoked=is_health
                    )
                except HlmError as err:
                    await conn.rollback()
                    await error_response(err)(scope, receive, send)
                    return
                state["auth"] = ctx
                state["device"] = row
            state["conn"] = conn
            try:
                await self.app(scope, receive, send_wrapper)
            except ERROR_TYPES as err:
                await conn.rollback()
                if sent_any:
                    raise
                buffered.clear()
                await error_response(err)(scope, receive, send)
                return
            except BaseException:
                await _rollback_quietly(conn)
                raise  # Starlette's ServerErrorMiddleware answers 500; nothing buffered was sent
            try:
                await self.commit_request(conn)
            except Exception as exc:
                await self._commit_failed(conn, exc, sent_any, scope, receive, send)
                return
            await flush()
            if ctx is not None and state["device"] is not None and state["device"]["status"] == "trusted":
                try:
                    await q.touch_last_seen(conn, ctx.device_id)
                    await conn.commit()
                except Exception:  # best effort, never fails the request
                    log.debug("last_seen_at refresh failed", exc_info=True)
                    await conn.rollback()

    async def _commit_failed(
        self, conn: Any, exc: BaseException | None, sent_any: bool, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Discard success when the outer commit fails or its outcome cannot be confirmed."""
        await _rollback_quietly(conn)
        log.error("request %s %s: commit failed, response discarded: %s", scope["method"], scope["path"], exc)
        if sent_any:  # a stream already flushed bytes; the client sees the broken response
            raise RuntimeError("commit failed after response bytes were sent") from exc
        await error_response(COMMIT_FAILED)(scope, receive, send)


async def _rollback_quietly(conn: Any) -> None:
    try:
        await conn.rollback()
    except Exception:  # noqa: BLE001 - the connection may already be gone
        log.debug("rollback after failed request failed", exc_info=True)
