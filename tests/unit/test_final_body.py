"""Final D1/D2/RSS regressions: incremental admission, patient reads, bounded buffers."""

from __future__ import annotations

import asyncio
import json
import threading
import tracemalloc
from types import SimpleNamespace

import pytest

from hlmemo.config import get_settings
from hlmemo.server import middleware as middleware_module
from hlmemo.server.middleware import AuthMiddleware


@pytest.fixture(autouse=True)
def _clean_tables():
    """Transport-only tests must not touch a database."""
    yield


def _scope(settings, *, client="192.0.2.1", length=None):
    return {
        "type": "http",
        "method": "GET",
        "path": "/ready",
        "headers": [] if length is None else [(b"content-length", str(length).encode())],
        "client": (client, 1234),
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
    }


async def _request(middleware, scope, receive):
    messages = []

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)
    return messages


async def _drain(scope, receive, send):
    size = 0
    while True:
        message = await receive()
        assert message["type"] == "http.request"
        assert len(message["body"]) <= 64 * 1024
        size += len(message["body"])
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(size).encode()})


class _Clock:
    """Advance upload time without wall-clock sleeps or changing the event loop clock."""

    def __init__(self, loop):
        self.loop = loop
        self.now = 100.0
        self.deadline = None

    def __getattr__(self, name):
        return getattr(self.loop, name)

    def time(self):
        return self.now

    def timeout_at(self, deadline):
        self.deadline = deadline
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def reschedule(self, deadline):
        self.deadline = deadline

    def advance(self, delay):
        self.now += delay
        if self.now > self.deadline:
            raise TimeoutError


async def _timed_request(monkeypatch, settings, chunks):
    clock = _Clock(asyncio.get_running_loop())
    monkeypatch.setattr(middleware_module.asyncio, "get_running_loop", lambda: clock)
    monkeypatch.setattr(middleware_module.asyncio, "timeout_at", clock.timeout_at)
    iterator = iter(chunks)
    reads = 0

    async def receive():
        nonlocal reads
        reads += 1
        delay, data, more = next(iterator)
        clock.advance(delay)
        return {"type": "http.request", "body": data, "more_body": more}

    middleware = AuthMiddleware(_drain)
    messages = await _request(middleware, _scope(settings), receive)
    assert middleware.body_budget.used == 0
    return messages, reads


def _retryable_timeout(messages):
    assert messages[0]["status"] == 408
    assert json.loads(messages[1]["body"])["retryable"] is True


async def test_declared_lengths_do_not_exhaust_budget_before_bytes_arrive():
    settings = get_settings()
    middleware = AuthMiddleware(_drain)
    admitted = asyncio.Event()
    blocked = asyncio.Event()
    waiting = 0

    def trickler():
        first = True

        async def receive():
            nonlocal first, waiting
            if first:
                first = False
                return {"type": "http.request", "body": b"x", "more_body": True}
            waiting += 1
            if waiting == 70:
                admitted.set()
            await blocked.wait()
            return {"type": "http.disconnect"}

        return receive

    tasks = [
        asyncio.create_task(
            _request(middleware, _scope(settings, client=f"192.0.2.{i}", length=4 * 1024 * 1024), trickler())
        )
        for i in range(1, 71)
    ]
    try:
        await asyncio.wait_for(admitted.wait(), 2)
        assert middleware.body_budget.used == 70
        remaining = 38_400_000

        async def legitimate_receive():
            nonlocal remaining
            size = min(remaining, 64 * 1024)
            remaining -= size
            return {"type": "http.request", "body": b"w" * size, "more_body": bool(remaining)}

        messages = await _request(
            middleware, _scope(settings, client="198.51.100.1", length=remaining), legitimate_receive
        )
        assert messages[0]["status"] == 200
        assert messages[1]["body"] == b"38400000"
        assert middleware.body_budget.used == 70
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert middleware.body_budget.used == 0
    assert middleware.body_budget.clients == {}


@pytest.mark.parametrize("initial", [[], [(0, b"x", True)], [(0, b"", True)]])
async def test_idle_timeout_applies_before_rate_grace(monkeypatch, initial):
    messages, _ = await _timed_request(monkeypatch, get_settings(), [*initial, (30.1, b"x", False)])
    _retryable_timeout(messages)


async def test_empty_messages_do_not_extend_idle_timeout(monkeypatch):
    messages, reads = await _timed_request(
        monkeypatch, get_settings(), [(10, b"", True), (10, b"", True), (10.1, b"", True)]
    )
    _retryable_timeout(messages)
    assert reads == 3


async def test_pause_beyond_rate_lead_uses_idle_allowance(monkeypatch):
    settings = get_settings()
    chunks = [(8, b"x" * (64 * 1024), True)] * 4
    # At 32s the client has no rate lead. A 20s pause remains within the 30s idle
    # allowance; the following chunk restores its average above the 8KiB/s floor.
    chunks.append((20, b"x" * (256 * 1024), False))
    messages, reads = await _timed_request(monkeypatch, settings, chunks)
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b"524288"
    assert reads == 5


async def test_floor_is_checked_after_grace_on_chunk_arrival(monkeypatch):
    chunks = [(20, b"x" * (64 * 1024), True)] * 4
    messages, reads = await _timed_request(monkeypatch, get_settings(), chunks)
    _retryable_timeout(messages)
    assert reads == 4


async def test_default_total_timeout_accepts_38mb_at_floor(monkeypatch):
    settings = get_settings()
    size = 38_400_000
    floor = settings.request_body_min_rate_bytes_s
    assert floor == 8192
    assert settings.request_body_rate_grace_bytes == 256 * 1024
    assert settings.request_body_timeout_s == 30
    assert settings.request_body_total_timeout_s == 9000
    assert size / floor < settings.request_body_total_timeout_s
    chunks = [(8, b"x" * (64 * 1024), True)] * (size // (64 * 1024))
    remainder = size % (64 * 1024)
    chunks.append((remainder / floor, b"x" * remainder, False))
    messages, _ = await _timed_request(monkeypatch, settings, chunks)
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b"38400000"


async def test_total_timeout_still_bounds_continuous_progress(monkeypatch):
    settings = get_settings(request_body_timeout_s=30, request_body_total_timeout_s=25)
    messages, _ = await _timed_request(
        monkeypatch, settings, [(10, b"x", True), (10, b"x", True), (10, b"x", False)]
    )
    _retryable_timeout(messages)


async def test_declared_oversize_rejected_without_receive():
    settings = get_settings()

    async def receive():
        pytest.fail("oversized declared length must be rejected before reading")

    middleware = AuthMiddleware(_drain)
    messages = await _request(
        middleware, _scope(settings, length=settings.request_max_body_bytes + 1), receive
    )
    assert messages[0]["status"] == 413
    assert middleware.body_budget.used == 0


async def test_spooled_trickling_clients_have_bounded_tracemalloc_peak():
    settings = get_settings()
    assert settings.request_body_spool_threshold_bytes == 1024 * 1024
    middleware = AuthMiddleware(_drain)
    blocked = asyncio.Event()
    admitted = asyncio.Event()
    waiting = 0
    clients = 12
    body_size = 4 * 1024 * 1024

    def receiver():
        received = 0

        async def receive():
            nonlocal received, waiting
            if received < body_size:
                await asyncio.sleep(0)
                received += 4096
                return {"type": "http.request", "body": b"x" * 4096, "more_body": True}
            waiting += 1
            if waiting == clients:
                admitted.set()
            await blocked.wait()
            return {"type": "http.disconnect"}

        return receive

    tracemalloc.start()
    tasks = [
        asyncio.create_task(_request(middleware, _scope(settings, client=f"192.0.2.{i}"), receiver()))
        for i in range(clients)
    ]
    try:
        await asyncio.wait_for(admitted.wait(), 15)
        assert middleware.body_budget.used == clients * body_size
        _, peak = tracemalloc.get_traced_memory()
        print(f"12 trickling clients, 48 MiB received: tracemalloc peak {peak / 1024**2:.2f} MiB")
        # Buffers holding all 48MiB fail this bound. Per-client 1MiB spooling plus
        # one rollover copy and transport/task overhead have ample headroom.
        assert peak < 20 * 1024 * 1024, f"trickler allocation peak: {peak / 1024**2:.2f} MiB"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tracemalloc.stop()
    assert middleware.body_budget.used == 0
    assert middleware.body_budget.clients == {}


async def test_cancel_closes_rolled_spool_and_releases_budget(monkeypatch):
    settings = get_settings(request_body_spool_threshold_bytes=1024)
    original_spool = middleware_module.SpooledTemporaryFile
    spools = []

    def capture_spool(*args, **kwargs):
        spool = original_spool(*args, **kwargs)
        spools.append(spool)
        return spool

    monkeypatch.setattr(middleware_module, "SpooledTemporaryFile", capture_spool)
    waiting = asyncio.Event()
    block = asyncio.Event()
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b"x" * 2048, "more_body": True}
        waiting.set()
        await block.wait()
        return {"type": "http.disconnect"}

    middleware = AuthMiddleware(_drain)
    task = asyncio.create_task(_request(middleware, _scope(settings), receive))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        assert len(spools) == 1
        assert spools[0]._rolled
        assert middleware.body_budget.used == 2048
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert spools[0].closed
    assert middleware.body_budget.used == 0
    assert middleware.body_budget.clients == {}


@pytest.mark.parametrize("worker_fails", [False, True])
async def test_body_io_cancellation_waits_for_worker_and_preserves_cancelled_error(worker_fails):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()

    def operation():
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5), "test did not release disk worker"
            if worker_fails:
                raise OSError("disk write failed after cancellation")
            return 123
        finally:
            finished.set()

    task = asyncio.create_task(middleware_module._body_io(operation))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "cancellation must not close a spool while its worker still owns it"
        assert not finished.is_set()
        # Repeated cancellation must not bypass the worker lifetime guarantee.
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert finished.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_single_large_chunk_rolls_before_copying_into_memory():
    settings = get_settings()
    middleware = AuthMiddleware(_drain)
    chunk = b"x" * 38_400_000

    async def receive():
        return {"type": "http.request", "body": chunk, "more_body": False}

    # The transport has already allocated its chunk. Measure additional middleware
    # allocations: rolling only AFTER writing would copy the entire 38.4 MB here.
    tracemalloc.start()
    try:
        messages = await _request(middleware, _scope(settings, length=len(chunk)), receive)
        _, peak = tracemalloc.get_traced_memory()
        print(f"38.4 MB single chunk: additional tracemalloc peak {peak / 1024**2:.2f} MiB")
        assert messages[0]["status"] == 200
        assert messages[1]["body"] == b"38400000"
        assert peak < 2 * 1024 * 1024
    finally:
        tracemalloc.stop()
    assert middleware.body_budget.used == 0
    assert middleware.body_budget.clients == {}
