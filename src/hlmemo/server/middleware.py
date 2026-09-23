"""Status-gate / authorization middleware (PHASE0-SPEC §2).

Pure ASGI middleware that runs BEFORE routing and before any MCP session manager:

  * `GET /health` needs no bearer; with one it resolves the device in any status so a pending
    device can poll for approval.
  * `POST /devices/register` needs no bearer (it is how a device obtains one); a bearer that is
    present is still gated like everywhere else.
  * Every other path (including `/mcp` and unknown routes) requires a `trusted` device:
    unknown / revoked token -> 401 `E_AUTH`; pending -> 403 `E_DEVICE_PENDING`.

Before buffering a protected body, a short, unlocked bearer lookup admits only trusted
devices. That connection is released before receiving any bytes. The middleware then owns a
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

Route filter (W0a, D-061). Before any other work — before the body budget, the bearer check,
a database lookup or a single `receive()` — closed routes answer 404 `E_NOT_FOUND`:
`POST /devices/register` when `registration_mode=closed`, and every admin route (`/admin/*`,
`/devices/approve`, `/devices/grant`, `/devices/list`) when `admin_http=disabled`. The filter
applies to any listener (Caddy's `@rest` matcher is only defense in depth). An expired device
(`devices.expires_at <= now()`) is treated exactly like a revoked one by the pre-body gate and by
the in-transaction resolve.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict, deque
from functools import lru_cache
from ipaddress import ip_address, ip_network
from tempfile import SpooledTemporaryFile
from typing import Any

from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hlmemo.auth.errors import HlmError
from hlmemo.auth.resolve import resolve
from hlmemo.auth.tokens import constant_time_equal, hash_token, parse_bearer
from hlmemo.config import get_settings
from hlmemo.db import auth_queries as q
from hlmemo.server.errors import ERROR_TYPES, error_response

log = logging.getLogger("hlmemo.server.auth")

HEALTH = ("GET", "/health")
READY = ("GET", "/ready")
REGISTER = ("POST", "/devices/register")
NO_BEARER_OK = (HEALTH, READY)
# W0a route filter (D-061): paths closed when admin_http=disabled, regardless of method.
ADMIN_HTTP_PATHS = frozenset({"/devices/approve", "/devices/grant", "/devices/list"})
NOT_FOUND = HlmError("E_NOT_FOUND", "not found")


def normalized_path(path: str) -> str:
    """Collapse repeated slashes and drop a trailing one, so `//admin/x/` cannot dodge the filter."""
    parts = [p for p in path.split("/") if p]
    return "/" + "/".join(parts)


def route_closed(path: str, settings: Any) -> bool:
    """True if this path must answer 404 before any body byte is read (W0a, D-061)."""
    norm = normalized_path(path)
    if norm == "/devices/register" and getattr(settings, "registration_mode", "closed") == "closed":
        return True
    if getattr(settings, "admin_http", "disabled") != "enabled":
        return norm in ADMIN_HTTP_PATHS or norm == "/admin" or norm.startswith("/admin/")
    return False


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
    if not config.strip():
        return ()
    values = [value.strip() for value in config.split(",")]
    if any(not value or "/" not in value for value in values):
        raise ValueError("trusted_proxy_ips must be a comma-separated CIDR list")
    return tuple(ip_network(value, strict=False) for value in values)


def trusted_client_ip(scope: Scope, trusted_proxy_ips: str) -> str:
    """Use XFF only behind an explicitly trusted peer; peel trusted hops right-to-left."""
    peer = scope.get("client")
    if not peer:
        return "unknown"
    host = peer[0]
    try:
        networks = _proxy_networks(trusted_proxy_ips)
    except ValueError:
        return host

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


def _body_client_key(scope: Scope, trusted_proxy_ips: str) -> str:
    client = trusted_client_ip(scope, trusted_proxy_ips)
    try:
        address = ip_address(client)
    except ValueError:
        return client
    if address.version == 6:
        return str(ip_network(f"{address}/64", strict=False))
    return str(address)


class _BodyReadError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class BodyBudget:
    """Event-loop-local accounting; no await separates admission from reservation."""

    def __init__(self, global_limit: int, client_limit: int) -> None:
        self.global_limit = global_limit
        self.client_limit = client_limit
        self.used = 0
        self.clients: dict[str, int] = {}

    def reserve(self, client: str, size: int) -> bool:
        if not size:
            return True
        held = self.clients.get(client, 0)
        if self.used + size > self.global_limit or held + size > self.client_limit:
            return False
        self.used += size
        self.clients[client] = held + size
        return True

    def release(self, client: str, size: int) -> None:
        if not size:
            return
        self.used -= size
        remaining = self.clients[client] - size
        if remaining:
            self.clients[client] = remaining
        else:
            del self.clients[client]


class BodyReservation:
    def __init__(self, budget: BodyBudget, client: str) -> None:
        self.budget = budget
        self.client = client
        self.size = 0

    def grow(self, size: int) -> bool:
        if not self.budget.reserve(self.client, size):
            return False
        self.size += size
        return True

    def release(self, size: int) -> None:
        self.budget.release(self.client, size)
        self.size -= size


class AuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.body_budget: BodyBudget | None = None
        self.body_readers: dict[str, int] = {}
        self.admin_body_readers: dict[str, int] = {}

    async def commit_request(self, conn: Any) -> None:
        """The outer ``COMMIT`` of the request transaction (seam for the G6 commit-failure test)."""
        await conn.commit()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = getattr(scope["app"].state, "settings", None) or get_settings()
        if route_closed(scope["path"], settings):
            # Never awaits receive(): no body byte, no budget, no DB, no device row, no event.
            await error_response(NOT_FOUND)(scope, receive, send)
            return
        if self.body_budget is None:
            self.body_budget = BodyBudget(
                settings.request_body_global_budget_bytes, settings.request_body_client_budget_bytes
            )
        reservation = BodyReservation(self.body_budget, _body_client_key(scope, settings.trusted_proxy_ips))
        try:
            # No allocation is based on untrusted Content-Length. Large uploads roll
            # to disk; the byte budget also bounds disk use until the request ends.
            with SpooledTemporaryFile(
                max_size=settings.request_body_spool_threshold_bytes, dir=settings.request_spool_dir
            ) as body:
                await self._request(scope, receive, send, reservation, body)
        finally:
            # Keep accounting through authentication, route execution and slow responses.
            # Cancellation and every early return also release the entire reservation.
            reservation.release(reservation.size)

    async def _request(
        self, scope: Scope, receive: Receive, send: Send, reservation: BodyReservation, body: Any
    ) -> None:
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
        admin_token = settings.admin_token
        is_admin_token = (
            bearer is not None
            and settings.admin_enabled
            and admin_token is not None
            and constant_time_equal(bearer, admin_token.get_secret_value())
        )
        use_admin_pool = is_admin_token and (is_revoke or route == READY or route[1].startswith("/admin/"))
        # Slots cover both the short auth lease and body read; byte reservations
        # also cover downstream handling. Unknown bearers never reach reserved capacity.
        # Reserved admin admission is independently bounded: a normal-pool gate
        # flood from the same proxy/client must not obstruct the reserved DB path.
        body_readers = self.admin_body_readers if use_admin_pool else self.body_readers
        key = reservation.client
        readers = body_readers.get(key, 0)
        if readers >= settings.request_body_client_concurrency:
            await self._body_error(scope, receive, send, 429, "too many concurrent request body reads")
            return
        body_readers[key] = readers + 1
        failure = None
        gate_error = None
        try:
            try:
                trusted = False
                if not is_register and route not in NO_BEARER_OK and not use_admin_pool:
                    await self._pre_body_gate(scope["app"].state.pool, bearer, settings)
                    trusted = True
                body_cap = min(
                    settings.request_max_body_bytes,
                    16 * 1024 if is_register else (settings.request_max_body_bytes if trusted else 64 * 1024),
                )
                result = await self._read_body(
                    receive, headers, settings, body_cap, reservation, body, trusted=trusted
                )
            finally:
                remaining_readers = body_readers[key] - 1
                if remaining_readers:
                    body_readers[key] = remaining_readers
                else:
                    del body_readers[key]
        except HlmError as exc:
            gate_error = exc
        except (PoolTimeout, DatabaseError) as exc:
            log.debug("pre-body authentication unavailable: %s", type(exc).__name__)
            gate_error = HlmError("E_UNAVAILABLE", "pre-body authentication temporarily unavailable")
        except (_BodyReadError, TimeoutError, OSError) as exc:
            # A slow error consumer must not retain either storage or admission capacity.
            body.close()
            reservation.release(reservation.size)
            if isinstance(exc, _BodyReadError):
                status, message = exc.status, str(exc)
            elif isinstance(exc, TimeoutError):
                status, message = 408, "request body read timed out"
            else:
                status, message = 503, "request body storage unavailable"
            failure = (status, message)
        # Leave the exception scope before sending: its traceback owns the last chunk.
        if gate_error is not None:
            body.close()
            await error_response(gate_error)(scope, receive, send)
            return
        if failure is not None:
            await self._body_error(scope, receive, send, *failure)
            return
        if result is None:
            return
        size, on_disk = result

        body.seek(0)
        original_receive = receive
        body_delivered = False
        remaining = size

        async def replay_receive() -> Message:
            nonlocal body_delivered, remaining
            if not body_delivered:
                chunk = await _body_io(body.read, 64 * 1024) if on_disk else body.read(64 * 1024)
                remaining -= len(chunk)
                body_delivered = remaining == 0
                return {"type": "http.request", "body": chunk, "more_body": not body_delivered}
            return await original_receive()

        receive = replay_receive
        if bearer is None and route in NO_BEARER_OK:
            await self.app(scope, receive, send)
            return
        pool = scope["app"].state.admin_pool if use_admin_pool else scope["app"].state.pool
        buffered: list[Message] = []
        sent_any = False
        streaming = False
        commit_error: Exception | None = None
        send_lock = asyncio.Lock()
        conn = None
        # Self-revocation authenticates in the ordinary pool. Its longer, bounded
        # acquisition wait survives ordinary queue timeouts without granting unknown
        # bearers any reserved capacity.
        lease = (
            pool.connection(
                timeout=settings.request_db_timeout_s
                + settings.pool_timeout_s
                + 2 * settings.db_lock_timeout_ms / 1000
            )
            if is_revoke and not use_admin_pool
            else pool.connection()
        )
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
                    exclusive_auth = False
                    if is_revoke:
                        # Inspect only the caller's own bearer identity, then let
                        # resolve recheck it under the lock. Both route spellings
                        # need exclusive auth for self-revoke to avoid lock upgrade
                        # deadlocks; cross-device callers must never queue that lock.
                        identity = await conn.execute(
                            "SELECT device_id FROM devices WHERE token_sha256 = %s AND status = 'trusted'"
                            " AND (expires_at IS NULL OR expires_at > now())",
                            (hash_token(bearer),),
                        )
                        caller = await identity.fetchone()
                        if caller is not None:
                            if route[1] == "/devices/revoke":
                                if not reservation.grow(size):
                                    raise HlmError("E_UNAVAILABLE", "request body budget exhausted")
                                try:
                                    payload = await _body_io(body.read) if on_disk else body.read()
                                    target = _revoke_target(route[1], payload)
                                    del payload
                                finally:
                                    reservation.release(size)
                                    body.seek(0)
                            else:
                                target = _revoke_target(route[1], b"")
                            exclusive_auth = target is not None and int(caller[0]) == target
                    ctx, row = await resolve(
                        conn,
                        bearer,
                        client=client,
                        allow_pending=is_health,
                        allow_revoked=is_health,
                        exclusive=exclusive_auth,
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
    async def _pre_body_gate(pool: Any, bearer: str, settings: Any) -> None:
        """Admission hint only: no locks/grants/context; authoritative resolve stays in-tx.

        SET LOCAL is reverted when this short lease commits/rolls back. Exactly one
        SELECT reads the bearer state, with no connection held over client I/O.
        """

        async def lookup():
            async with pool.connection(timeout=min(settings.pool_timeout_s, 0.25)) as conn:
                await conn.execute("SET LOCAL statement_timeout = '250ms'")
                cur = await conn.execute(
                    "SELECT device_id, status, token_generation,"
                    " (expires_at IS NOT NULL AND expires_at <= now()) AS expired"
                    " FROM devices WHERE token_sha256 = %s",
                    (hash_token(bearer),),
                )
                return await cur.fetchone()

        # A disconnect must not interrupt pool return and leak its admission slot.
        task = asyncio.create_task(lookup())
        row = await _finish_shielded(task)
        if row is None or row[1] == "revoked" or row[3]:
            # Expired == revoked (W0a, D-061).
            raise HlmError("E_AUTH", "unknown, revoked or expired token")
        if row[1] == "pending":
            raise HlmError("E_DEVICE_PENDING", "device awaiting approval", {"device_id": int(row[0])})
        if row[1] != "trusted":
            raise HlmError("E_AUTH", "device is not trusted")

    @staticmethod
    async def _read_body(
        receive: Receive,
        headers: Headers,
        settings: Any,
        body_cap: int,
        reservation: BodyReservation,
        body: Any,
        *,
        trusted: bool = False,
    ) -> tuple[int, bool] | None:
        # No untrusted network input owns a database connection or device lock.
        length = headers.get("content-length")
        max_body_bytes = body_cap
        if length is not None:
            try:
                max_body_bytes = int(length)
            except ValueError:
                raise _BodyReadError(400, "invalid content-length") from None
            if max_body_bytes < 0:
                raise _BodyReadError(400, "invalid content-length")
            if max_body_bytes > body_cap:
                raise _BodyReadError(413, "request body too large")
        loop = asyncio.get_running_loop()
        started = loop.time()
        base_deadline = started + settings.request_body_base_s
        rate = settings.request_body_min_rate_bytes_s
        total_deadline = base_deadline + (max_body_bytes / rate if trusted else 0)
        idle_deadline = started + settings.request_body_timeout_s
        next_deadline = min(total_deadline, base_deadline, idle_deadline)
        size = 0
        on_disk = False
        async with asyncio.timeout_at(next_deadline) as body_deadline:
            while True:
                message = await receive()
                # A receive callable may complete without yielding; do not let a
                # late chunk buy more time before the timeout callback can run.
                if loop.time() > next_deadline:
                    raise _BodyReadError(408, "request body read timed out")
                if message["type"] == "http.disconnect":
                    return None
                chunk = message.get("body", b"")
                if size + len(chunk) > body_cap:
                    raise _BodyReadError(413, "request body too large")
                if not reservation.grow(len(chunk)):
                    raise _BodyReadError(503, "request body budget exhausted")
                if chunk:
                    idle_deadline = loop.time() + settings.request_body_timeout_s
                size += len(chunk)
                next_deadline = min(
                    total_deadline, base_deadline + (size / rate if trusted else 0), idle_deadline
                )
                body_deadline.reschedule(next_deadline)
                # Roll before writing, avoiding a large intermediate BytesIO copy.
                if not on_disk and size > settings.request_body_spool_threshold_bytes:
                    await _body_io(body.rollover)
                    on_disk = True
                if on_disk:
                    await _body_io(body.write, chunk)
                else:
                    body.write(chunk)
                if loop.time() > next_deadline:
                    raise _BodyReadError(408, "request body read timed out")
                more_body = message.get("more_body", False)
                del message, chunk
                if not more_body:
                    return size, on_disk

    @staticmethod
    async def _body_error(scope: Scope, receive: Receive, send: Send, status: int, message: str) -> None:
        code = (
            "E_RATE_LIMITED"
            if status == 429
            else ("E_UNAVAILABLE" if status in (408, 503) else "E_INVALID_ARG")
        )
        response = error_response(HlmError(code, message))
        response.status_code = status
        await response(scope, receive, send)


async def _body_io(operation: Any, *args: Any) -> Any:
    """Keep disk I/O off the event loop; finish it before cancellation closes the spool."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    return await _finish_shielded(task)


async def _finish_shielded(task: asyncio.Task) -> Any:
    """Finish owned work/cleanup under repeated cancellation, then propagate cancellation."""
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except Exception:
            break  # Re-raise the I/O failure below, unless cancellation already won.
    if cancelled is not None:
        # Retrieve any I/O failure, but preserve the caller's cancellation/deadline.
        if not task.cancelled():
            task.exception()
        raise cancelled
    return task.result()


def _revoke_target(path: str, body: bytes) -> int | None:
    """Identify a self-revoke lock key; routing remains authoritative for validation."""
    try:
        if path == "/devices/revoke":
            payload = json.loads(body)
            value = payload.get("id") if isinstance(payload, dict) else None
        else:
            value = path.split("/")[3]
        return int(value) if value is not None else None
    except (ValueError, TypeError, OverflowError, IndexError):
        return None


async def _rollback_quietly(conn: Any) -> None:
    try:
        await conn.rollback()
    except Exception:  # noqa: BLE001 - the connection may already be gone
        log.debug("rollback after failed request failed", exc_info=True)
