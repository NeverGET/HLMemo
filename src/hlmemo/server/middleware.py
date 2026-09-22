"""Status-gate / authorization middleware (PHASE0-SPEC §2).

Pure ASGI middleware that runs BEFORE routing and before any MCP session manager:

  * `GET /health` needs no bearer; with one it resolves the device in any status so a pending
    device can poll for approval.
  * `POST /devices/register` needs no bearer (it is how a device obtains one); a bearer that is
    present is still gated like everywhere else.
  * Every other path (including `/mcp` and unknown routes) requires a `trusted` device:
    unknown / revoked token -> 401 `E_AUTH`; pending -> 403 `E_DEVICE_PENDING`.

The middleware bounds and buffers the body before acquiring a connection. It then owns a
time-bounded request transaction: it takes a pooled connection, resolves the device
under `FOR SHARE`, exposes `request.state.conn` / `request.state.auth` / `request.state.device`
to the route, commits when the route finished (or rolls back on an `HlmError`, which it maps to
the JSON envelope), and only then refreshes `last_seen_at` (≤ once per 60 s) outside the request
transaction so that it never deadlocks with concurrent share holders.

Commit before acknowledgement (codex review C1). Tool handlers and routes run inside savepoints of
the request transaction; nothing they produce is durable until the outer ``COMMIT`` here. The
response the route produced is therefore *buffered* and only released to the client after that
commit succeeded — a client that sees a success envelope holds a durable write. If the commit
fails, the buffered response is discarded and the client receives the ``E_UNAVAILABLE`` (503,
retryable) envelope instead. Finite JSON responses remain buffered even when chunked. At
``http.response.start``, a ``text/event-stream`` media type declares an event stream: commit
and return the connection before forwarding its headers, then pass its messages through.
Transfer-Encoding/chunk count
and absence of Content-Length do not imply an indefinite stream (ASGI owns HTTP framing).
This covers the persistent MCP GET channel; tool POST acknowledgements use finite JSON.

`GET /health` (liveness) and `GET /ready` (readiness) need no bearer; with one they are gated
like every other route except that `/health` also resolves pending/revoked devices (§2 poll).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, deque
from functools import lru_cache
from ipaddress import ip_address, ip_network
from typing import Any

from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hlmemo.auth.errors import HlmError
from hlmemo.auth.resolve import resolve
from hlmemo.auth.tokens import constant_time_equal, parse_bearer
from hlmemo.config import get_settings
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
    """Bounded sliding-window per-IP counters, ordered by most recent accepted hit."""

    def __init__(self, limit: int, window_s: float = 60.0, max_keys: int = 4096) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        while self._hits:
            oldest = next(iter(self._hits.values()))
            if oldest and now - oldest[-1] < self.window_s:
                break
            self._hits.popitem(last=False)
        if key not in self._hits and len(self._hits) >= self.max_keys:
            self._hits.popitem(last=False)
        dq = self._hits.setdefault(key, deque())
        while dq and now - dq[0] >= self.window_s:
            dq.popleft()
        if len(dq) >= self.limit:
            return False
        dq.append(now)
        self._hits.move_to_end(key)
        return True


@lru_cache(maxsize=32)
def _proxy_networks(config: str) -> tuple:
    return tuple(ip_network(value.strip(), strict=False) for value in config.split(",") if value.strip())


def trusted_client_ip(scope: Scope, trusted_proxy_ips: str) -> str:
    """Use XFF only behind an explicitly trusted peer; peel trusted hops right-to-left."""
    peer = scope.get("client")
    if not peer:
        return "unknown"
    host = peer[0]
    networks = _proxy_networks(trusted_proxy_ips)

    def trusted(value: str) -> bool:
        address = ip_address(value)
        return any(address in network for network in networks)

    try:
        if not trusted(host):
            return host
        forwarded = Headers(scope=scope).get("x-forwarded-for")
        if not forwarded:
            return host
        hops = [str(ip_address(value.strip())) for value in forwarded.split(",")]
        for hop in reversed(hops):
            if not trusted(hop):
                return hop
        return hops[0]
    except ValueError:
        # Malformed or non-IP peers/forwarding headers never become attacker-chosen buckets.
        return host


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
        is_revoke = route[0] == "POST" and (
            route[1] == "/devices/revoke"
            or (route[1].startswith("/admin/devices/") and route[1].endswith("/revoke"))
        )

        if bearer is None and not is_register and route not in NO_BEARER_OK:
            await error_response(HlmError("E_AUTH", "missing bearer token"))(scope, receive, send)
            return

        settings = getattr(scope["app"].state, "settings", None) or get_settings()
        body_cap = (
            min(settings.request_max_body_bytes, 16 * 1024)
            if is_register
            else settings.request_max_body_bytes
        )
        # Untrusted network input must never own a database connection or device lock.
        try:
            loop = asyncio.get_running_loop()
            total_deadline = loop.time() + settings.request_body_total_timeout_s
            async with asyncio.timeout_at(
                min(total_deadline, loop.time() + settings.request_body_timeout_s)
            ) as body_deadline:
                body = bytearray()
                length = headers.get("content-length")
                if length is not None:
                    try:
                        declared = int(length)
                    except ValueError:
                        declared = -1
                    if declared < 0:
                        await error_response(HlmError("E_INVALID_ARG", "invalid content-length"))(
                            scope, receive, send
                        )
                        return
                    if declared > body_cap:
                        await self._body_error(scope, receive, send, 413, "request body too large")
                        return
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > body_cap:
                        await self._body_error(scope, receive, send, 413, "request body too large")
                        return
                    body.extend(chunk)
                    if chunk:
                        body_deadline.reschedule(
                            min(total_deadline, loop.time() + settings.request_body_timeout_s)
                        )
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await self._body_error(scope, receive, send, 408, "request body read timed out")
            return

        original_receive = receive
        body_delivered = False

        async def replay_receive() -> Message:
            nonlocal body_delivered
            if not body_delivered:
                body_delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await original_receive()

        receive = replay_receive
        if bearer is None and route in NO_BEARER_OK:
            await self.app(scope, receive, send)
            return
        admin_token = settings.admin_token
        admin_route = route[1].startswith("/admin/") and bearer is not None and admin_token is not None
        use_admin_pool = (
            is_revoke
            or route == READY
            or (admin_route and constant_time_equal(bearer, admin_token.get_secret_value()))
        )
        pool = scope["app"].state.admin_pool if use_admin_pool else scope["app"].state.pool
        buffered: list[Message] = []
        sent_any = False
        streaming = False
        commit_error: Exception | None = None
        send_lock = asyncio.Lock()
        conn = None
        lease = pool.connection()
        ctx = None
        dispatched = False

        async def release(*, discard: bool = False) -> None:
            nonlocal conn
            if conn is not None:
                owned = conn
                # MCP workers live in the session manager's task group. On cancellation
                # their savepoint cleanup may outlive this HTTP task: never pool a
                # connection they could still use, or let a late handler fall back to
                # opening a fresh transaction with this failed request's credentials.
                state["conn"] = owned if discard else None

                async def cleanup() -> None:
                    if discard and hasattr(owned, "close"):
                        await owned.close()
                    else:
                        await _rollback_quietly(owned)
                    await lease.__aexit__(None, None, None)

                # A disconnect/deadline may arrive during pool reset or its queue lock.
                # Retain ownership and finish cleanup even under repeated cancellation.
                task = asyncio.create_task(cleanup())
                cancelled = None
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError as exc:
                        cancelled = exc
                task.result()
                conn = None
                if cancelled is not None:
                    raise cancelled

        async def finish() -> None:
            # Release BEFORE touching the network, including a slow response consumer.
            await self.commit_request(conn)
            if ctx is not None and state["device"]["status"] == "trusted":
                try:
                    await q.touch_last_seen(conn, ctx.device_id)
                    await conn.commit()
                except Exception:
                    log.debug("last_seen_at refresh failed", exc_info=True)
                    await _rollback_quietly(conn)
            await release()
            deadline.reschedule(None)

        async def send_wrapper(message: Message) -> None:
            nonlocal streaming, sent_any, commit_error
            async with send_lock:
                if commit_error is not None:
                    raise commit_error
                if message["type"] == "http.response.start":
                    content_type = Headers(raw=message.get("headers", [])).get("content-type", "")
                    if content_type.partition(";")[0].strip().lower() == "text/event-stream":
                        try:
                            await finish()
                        except Exception as exc:
                            commit_error = exc
                            raise
                        streaming = True
                if streaming:
                    sent_any = True
                    await send(message)
                else:
                    buffered.append(message)

        db_timeout = settings.request_db_timeout_s
        if is_revoke:
            # A queued revocation must be able to outwait ordinary requests' share
            # locks, including pool acquisition and a margin for transaction cleanup.
            db_timeout += settings.pool_timeout_s + 2 * settings.db_lock_timeout_ms / 1000
        try:
            async with asyncio.timeout(db_timeout) as deadline:
                conn = await lease.__aenter__()
                if route[1] == "/mcp" and hasattr(conn, "execute"):
                    remaining_ms = max(1, int((deadline.when() - asyncio.get_running_loop().time()) * 1000))
                    await conn.execute(
                        "SELECT set_config('hlmemo.request_db_timeout_ms', %s, true)", (str(remaining_ms),)
                    )
                if is_revoke:
                    wait_ms = int(settings.request_db_timeout_s * 1000) + settings.db_lock_timeout_ms
                    for name in ("lock_timeout", "statement_timeout"):
                        await conn.execute("SELECT set_config(%s, %s, true)", (name, f"{wait_ms}ms"))
                    if conn.info.server_version >= 170000:
                        await conn.execute(
                            "SELECT set_config('transaction_timeout', %s, true)",
                            (f"{int(db_timeout * 1000) + 1000}ms",),
                        )
                if bearer is not None:
                    ctx, row = await resolve(
                        conn,
                        bearer,
                        client=client,
                        allow_pending=is_health,
                        allow_revoked=is_health,
                        exclusive=is_revoke,
                    )
                    state["auth"] = ctx
                    state["device"] = row
                state["conn"] = conn
                dispatched = True
                await self.app(scope, receive, send_wrapper)
                # The transport may swallow a send failure; never retry a failed commit.
                if commit_error is not None:
                    raise commit_error
                if not streaming:
                    try:
                        await finish()
                    except Exception as exc:
                        commit_error = exc
                        raise
        except BaseException as err:
            if conn is not None:
                # Ordinary authentication denials have no detached transport worker;
                # rolling back is sufficient and avoids reconnect churn from bad tokens.
                await release(
                    discard=dispatched
                    and route[1] == "/mcp"
                    and isinstance(err, (asyncio.CancelledError, TimeoutError))
                )
            if sent_any:
                raise
            if commit_error is not None:
                log.error("request commit failed; response discarded: %s", commit_error)
                await error_response(COMMIT_FAILED)(scope, receive, send)
            elif isinstance(err, ERROR_TYPES):
                await error_response(err)(scope, receive, send)
            elif isinstance(err, (TimeoutError, PoolTimeout, DatabaseError)):
                await error_response(HlmError("E_UNAVAILABLE", "request database work timed out or failed"))(
                    scope, receive, send
                )
            else:
                raise
            return
        finally:
            if conn is not None:
                await release(discard=dispatched and route[1] == "/mcp")
        # Finite responses are committed and detached from the pool before their first byte.
        for message in buffered:
            await send(message)

    @staticmethod
    async def _body_error(scope: Scope, receive: Receive, send: Send, status: int, message: str) -> None:
        response = error_response(HlmError("E_UNAVAILABLE" if status == 408 else "E_INVALID_ARG", message))
        response.status_code = status
        await response(scope, receive, send)


async def _rollback_quietly(conn: Any) -> None:
    try:
        await conn.rollback()
    except Exception:  # noqa: BLE001 - the connection may already be gone
        log.debug("rollback after failed request failed", exc_info=True)
