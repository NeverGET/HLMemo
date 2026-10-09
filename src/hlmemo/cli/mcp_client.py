"""Streamable-HTTP MCP client for `memory.query` / `memory.call_the_day` (PHASE0-SPEC §3, §5).

Wire format (D-024 (6)): every tool result is one `TextContent` carrying canonical JSON; errors are
`isError:true` results whose text is `{code, message, retryable, details}`. Both are decoded here into a
dict or a `ToolCallError`. Transport failures (timeouts, connection refused, HTTP 401/403 from the status
gate) become `ToolCallError` with `E_UNAVAILABLE` / `E_AUTH` / `E_DEVICE_PENDING`. A cancellation
(``asyncio.CancelledError``, e.g. from a caller's ``asyncio.timeout``) is never classified: it propagates
unchanged, so the caller's timeout still sees its own cancellation and reports a timeout.

`MemoryClient(url, token)` talks to a server; `MemoryClient.in_memory(server)` binds an `MCPServer`
instance in-process (unit tests), no network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any

from mcp import Client

TOOL_QUERY = "memory.query"
TOOL_CALL_THE_DAY = "memory.call_the_day"
CLIENT_NAME = "hlm-cli"
_STATUS_RE = re.compile(r"\b(401|403|404|429|5\d\d)\b")
#: Client-protocol tools the server dispatches but never lists (W1.5, Sol 40 #1: the agent tool
#: surface stays small). The SDK validates every result against the listed output schema; for an
#: unlisted tool it re-lists the tools and warns "not listed by server" on EVERY call (e2e
#: 2026-09-24 #10). There is no schema to validate against, so these are not validated.
UNLISTED_TOOLS = frozenset({"hlm.export", "hlm.questions"})


def _skip_unlisted_validation(client: Client) -> None:
    """Make an entered ``Client`` skip output validation for ``UNLISTED_TOOLS`` (no warning and no
    extra ``tools/list`` round trip per call); listed tools are validated as before."""
    session = client.session
    validate = session.validate_tool_result

    async def validate_listed(name: str, result: Any) -> None:
        if name not in UNLISTED_TOOLS:
            await validate(name, result)

    session.validate_tool_result = validate_listed  # type: ignore[method-assign]


class ToolCallError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        transport: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        #: no answer came from the server (a timeout, a refused connection, TLS, a proxy error): the
        #: transport failure's exception type. None when the server (or its auth gate) sent the error.
        self.transport = transport

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class _BearerHttpTransport(AbstractAsyncContextManager):
    """`mcp.client.Transport` over streamable HTTP with a fixed Authorization header and timeout."""

    def __init__(
        self, url: str, token: str | None, timeout_s: float, extra_headers: dict[str, str] | None = None
    ) -> None:
        self.url, self.token, self.timeout_s = url, token, timeout_s
        self.extra_headers = dict(extra_headers or {})
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self):
        import httpx2
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        headers = {**self.extra_headers, "User-Agent": CLIENT_NAME}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        http = create_mcp_http_client(
            headers=headers, timeout=httpx2.Timeout(self.timeout_s, read=max(self.timeout_s, 30.0))
        )
        self._stack = AsyncExitStack()
        await self._stack.enter_async_context(http)
        return await self._stack.enter_async_context(streamable_http_client(self.url, http_client=http))

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None


def _flatten(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        out: list[BaseException] = []
        for e in exc.exceptions:
            out.extend(_flatten(e))
        return out
    return [exc]


def _classify(exc: BaseException) -> ToolCallError:
    """Map transport/HTTP failures to spec codes (best effort; the gate returns the JSON envelope)."""
    leaves = _flatten(exc)
    for leaf in leaves:
        if _is_timeout(
            leaf
        ):  # a timeout carries no response; its text may hold numbers ("500 ms", review 124)
            continue
        resp = getattr(leaf, "response", None)
        status = getattr(resp, "status_code", None)
        body: Any = None
        if resp is not None:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = None
        if isinstance(body, dict) and "code" in body:
            return ToolCallError(
                str(body["code"]),
                str(body.get("message") or body["code"]),
                retryable=bool(body.get("retryable", False)),
                details=body.get("details") or {},
            )
        if status is None:
            m = _STATUS_RE.search(str(leaf))
            status = int(m.group(1)) if m else None
        if status == 401:
            return ToolCallError("E_AUTH", "server rejected the device token (401)")
        if status == 403:
            return ToolCallError("E_DEVICE_PENDING", "device not trusted yet (403)", retryable=True)
        if (
            isinstance(status, int) and status >= 500
        ):  # the server answered: not a network failure (review 123)
            return ToolCallError(
                "E_UNAVAILABLE",
                f"memory server error (HTTP {status})",
                retryable=True,
                details={"http": status},
            )
    if any(_is_timeout(leaf) for leaf in leaves):
        return ToolCallError(
            "E_UNAVAILABLE", "timeout waiting for the memory server", retryable=True, transport="TimeoutError"
        )
    text = "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in leaves)[:400]
    return ToolCallError(
        "E_UNAVAILABLE",
        f"memory server unreachable: {text}",
        retryable=True,
        transport=type(leaves[0]).__name__,
    )


def _is_timeout(exc: BaseException) -> bool:
    """A builtin timeout, or an HTTP client's own (httpx ``ConnectTimeout``, ``ReadTimeout``, ...: subclasses
    of its ``TimeoutException``, not of the builtin ``TimeoutError``; review 123)."""
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return True
    return any(c.__name__ == "TimeoutException" for c in type(exc).__mro__)


def decode_result(result: Any) -> dict[str, Any]:
    """CallToolResult -> dict (success) or ToolCallError (isError)."""
    texts = [getattr(c, "text", None) for c in (result.content or [])]
    text = next((t for t in texts if isinstance(t, str)), "")
    parsed: Any = None
    if text:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
    if getattr(result, "is_error", False):
        if isinstance(parsed, dict) and "code" in parsed:
            raise ToolCallError(
                str(parsed["code"]),
                str(parsed.get("message") or parsed["code"]),
                retryable=bool(parsed.get("retryable", False)),
                details=parsed.get("details") or {},
            )
        raise ToolCallError("E_UNAVAILABLE", text or "tool call failed", retryable=True)
    if isinstance(parsed, dict):
        return parsed
    raise ToolCallError("E_UNAVAILABLE", f"tool returned non-JSON text: {text[:120]!r}")


class MemoryClient:
    def __init__(
        self,
        url: str,
        token: str | None,
        *,
        timeout_s: float = 5.0,
        target: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout_s = timeout_s
        self._target = target  # MCPServer / Transport injected for tests
        #: sent on every request of this client (``hlm review``: the owner client token header)
        self.extra_headers = dict(extra_headers or {})

    @classmethod
    def in_memory(cls, server: Any, *, timeout_s: float = 5.0) -> MemoryClient:
        return cls("memory://", None, timeout_s=timeout_s, target=server)

    def _client(self) -> Client:
        target = (
            self._target
            if self._target is not None
            else _BearerHttpTransport(self.url, self.token, self.timeout_s, self.extra_headers)
        )
        return Client(target, read_timeout_seconds=self.timeout_s)

    async def call_async(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client() as c:
                _skip_unlisted_validation(c)
                result = await c.call_tool(tool, arguments, read_timeout_seconds=self.timeout_s)
        except ToolCallError:
            raise
        except BaseException as exc:  # noqa: BLE001 - anyio groups, httpx2 errors, TimeoutError
            if isinstance(exc, KeyboardInterrupt | asyncio.CancelledError):
                raise
            raise _classify(exc) from exc
        return decode_result(result)

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self.call_async(tool, arguments))

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]]:
        """One MCP session for many calls (``hlm import``/``hlm export``); yields ``call(tool, args)``.

        Tool errors raise ``ToolCallError``; transport failures are classified like ``call_async``.
        A cancellation (the caller's timeout) propagates unchanged from a call and from the session.
        The unlisted client tool ``hlm.export`` is not output-validated (``UNLISTED_TOOLS``).
        """
        try:
            async with self._client() as c:
                _skip_unlisted_validation(c)

                async def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
                    try:
                        result = await c.call_tool(tool, arguments, read_timeout_seconds=self.timeout_s)
                    except ToolCallError:
                        raise
                    except BaseException as exc:  # noqa: BLE001 - classified below
                        if isinstance(exc, KeyboardInterrupt | asyncio.CancelledError):
                            raise
                        raise _classify(exc) from exc
                    return decode_result(result)

                yield call
        except ToolCallError:
            raise
        except BaseException as exc:  # noqa: BLE001 - session setup / teardown failures
            if isinstance(exc, KeyboardInterrupt | GeneratorExit | asyncio.CancelledError):
                raise
            raise _classify(exc) from exc

    # ------------------------------------------------------------------ tools

    def query(
        self,
        project: str,
        query: str,
        token_budget: int,
        *,
        kinds: list[str] | None = None,
        valid_at: str | None = None,
        known_at: str | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"project": project, "query": query, "token_budget": token_budget}
        if kinds:
            args["kinds"] = kinds
        if valid_at:
            args["valid_at"] = valid_at
        if known_at:
            args["known_at"] = known_at
        if include_archived:
            args["include_archived"] = True
        return self.call(TOOL_QUERY, args)

    def call_the_day(
        self,
        project: str,
        *,
        request_id: str,
        session_id: str,
        notes: str,
        decisions: list[str] | None = None,
        lessons: list[dict[str, Any]] | None = None,
        card_update: dict[str, Any] | None = None,
        expected_versions: list[dict[str, Any]] | None = None,
        token_budget: int | None = None,
        client: str = CLIENT_NAME,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "project": project,
            "request_id": request_id,
            "session_id": session_id,
            "client": client,
            "notes": notes,
        }
        if decisions:
            args["decisions"] = decisions
        if lessons:
            args["lessons"] = lessons
        if card_update:
            args["card_update"] = card_update
        if expected_versions:
            args["expected_versions"] = expected_versions
        if token_budget is not None:
            args["token_budget"] = token_budget
        return self.call(TOOL_CALL_THE_DAY, args)
