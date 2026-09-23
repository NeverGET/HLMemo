"""D-037: progress deadlines, bounded registration buckets and trusted forwarding."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from hlmemo.config import get_settings
from hlmemo.server.middleware import AuthMiddleware, RateLimiter, trusted_client_ip


def test_rate_limiter_evicts_expired_and_bounds_live_keys(monkeypatch):
    now = 100.0
    monkeypatch.setattr("hlmemo.server.middleware.time.monotonic", lambda: now)
    limiter = RateLimiter(2, window_s=60, max_keys=32)
    assert limiter.allow("first")
    assert limiter.allow("first")
    assert not limiter.allow("first")
    for index in range(1000):
        assert limiter.allow(str(index))
    assert len(limiter._hits) == 32
    now += 60
    assert limiter.allow("fresh")
    assert list(limiter._hits) == ["fresh"]


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("172.18.0.5", "203.0.113.66", "203.0.113.66"),
        ("172.18.0.5", "198.51.100.7", "198.51.100.7"),
        ("203.0.113.66", "198.51.100.7", "203.0.113.66"),
        ("172.18.0.5", "192.0.2.8, 203.0.113.66, 172.18.0.9", "203.0.113.66"),
        ("172.18.0.5", "spoofed", "172.18.0.5"),
        ("172.18.0.5", "", "172.18.0.5"),
        ("::1", "2001:db8::4", "2001:db8::4"),
    ],
)
def test_forwarding_requires_trusted_peer_and_peels_only_trusted_hops(peer, forwarded, expected):
    scope = {"client": (peer, 1234), "headers": [(b"x-forwarded-for", forwarded.encode())]}
    assert trusted_client_ip(scope, "127.0.0.1/32,::1/128,172.18.0.0/16") == expected


async def _body_request(settings, chunks, *, path="/ready", headers=()):
    messages = []
    routed = []
    iterator = iter(chunks)
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))
    scope = {
        "type": "http",
        "method": "POST" if path == "/devices/register" else "GET",
        "path": path,
        "headers": list(headers),
        "app": app,
    }

    async def receive():
        delay, data, more = next(iterator)
        await asyncio.sleep(delay)
        return {"type": "http.request", "body": data, "more_body": more}

    async def route(scope, receive, send):
        routed.append((await receive())["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        messages.append(message)

    await AuthMiddleware(route)(scope, receive, send)
    return messages, routed


async def test_slow_body_progress_extends_idle_deadline():
    settings = get_settings(request_body_timeout_s=0.15)
    chunks = [(0.04, b"x" * 4096, True)] * 9 + [(0.04, b"end", False)]
    messages, routed = await _body_request(settings, chunks)
    assert messages[0]["status"] == 200
    assert routed == [b"x" * (4096 * 9) + b"end"]


@pytest.mark.parametrize("empty_progress", [False, True])
async def test_stalled_body_is_retryable_without_pool_checkout(empty_progress):
    settings = get_settings(request_body_timeout_s=0.08)
    chunks = [(0.02, b"", True)] * 20 if empty_progress else [(1, b"x", False)]
    messages, routed = await _body_request(settings, chunks)
    assert messages[0]["status"] == 408
    assert json.loads(messages[1]["body"])["retryable"] is True
    assert not routed


@pytest.mark.parametrize("declared", [False, True])
async def test_register_has_fixed_small_body_cap_before_pool_checkout(declared):
    settings = get_settings(request_max_body_bytes=64 * 1024 * 1024)
    headers = [(b"content-length", b"16385")] if declared else []
    messages, routed = await _body_request(
        settings,
        [(0, b"x" * (16 * 1024 + 1), False)],
        path="/devices/register",
        headers=headers,
    )
    assert messages[0]["status"] == 413
    assert not routed
