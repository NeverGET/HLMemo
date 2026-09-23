"""Final D1/D2/RSS regressions: incremental admission, patient reads, bounded buffers."""

from __future__ import annotations

import asyncio
import gc
import json
import threading
import tracemalloc
import weakref
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from hlmemo.config import get_settings
from hlmemo.server import middleware as middleware_module
from hlmemo.server.middleware import AuthMiddleware


@pytest.fixture(autouse=True)
def _clean_tables():
    """Transport-only tests must not touch a database."""
    yield


@pytest.fixture(autouse=True)
def _trusted_transport(monkeypatch):
    """Stub authoritative auth only; the real admission gate still inspects a trusted row.

    Real SELECT/lease/revocation behavior is covered by test_prebody_auth integration tests.
    """

    async def resolve(*args, **kwargs):
        return None, {"status": "trusted"}

    monkeypatch.setattr(middleware_module, "resolve", resolve)


class _Pool:
    @asynccontextmanager
    async def connection(self, **kwargs):
        yield self

    async def execute(self, *args):
        return self

    async def fetchone(self):
        return 2, "trusted", 1, False  # device_id, status, token_generation, expired (W0a)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _scope(settings, *, client="192.0.2.1", length=None):
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(b"authorization", b"Bearer trusted-test-device")]
        + ([] if length is None else [(b"content-length", str(length).encode())]),
        "client": (client, 1234),
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings, pool=_Pool())),
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
        self.now = self.started = loop.time()
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
        target = self.now + delay
        if target > self.deadline:
            self.now = self.deadline
            raise TimeoutError
        self.now = target


async def _timed_request(monkeypatch, settings, chunks, *, length=None):
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
    messages = await _request(middleware, _scope(settings, length=length), receive)
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
async def test_idle_timeout_applies_from_first_receive(monkeypatch, initial):
    messages, _ = await _timed_request(monkeypatch, get_settings(), [*initial, (30.1, b"x", False)])
    _retryable_timeout(messages)


async def test_empty_messages_do_not_extend_idle_timeout(monkeypatch):
    messages, reads = await _timed_request(
        monkeypatch, get_settings(), [(10, b"", True), (10, b"", True), (10.1, b"", True)]
    )
    _retryable_timeout(messages)
    assert reads == 3


async def test_25_second_pause_mid_upload_survives(monkeypatch):
    settings = get_settings()
    chunks = [(8, b"x" * (64 * 1024), True)] * 4
    # At 32s, the 30s base allowance permits a 25s pause at the floor rate.
    chunks.append((25, b"x" * (256 * 1024), False))
    messages, reads = await _timed_request(monkeypatch, settings, chunks)
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b"524288"
    assert reads == 5


async def test_floor_applies_below_old_grace_before_next_chunk(monkeypatch):
    chunks = [(20, b"x" * (64 * 1024), True)] * 4
    messages, reads = await _timed_request(monkeypatch, get_settings(), chunks)
    _retryable_timeout(messages)
    assert reads == 2


async def test_derived_total_cap_accepts_38mb_at_floor(monkeypatch):
    settings = get_settings()
    size = 38_400_000
    floor = settings.request_body_min_rate_bytes_s
    assert floor == 8192
    assert settings.request_body_base_s == 30
    assert settings.request_body_timeout_s == 30
    assert settings.request_body_base_s + size / floor == 4717.5
    chunks = [(8, b"x" * (64 * 1024), True)] * (size // (64 * 1024))
    remainder = size % (64 * 1024)
    chunks.append((remainder / floor, b"x" * remainder, False))
    messages, _ = await _timed_request(monkeypatch, settings, chunks, length=size)
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b"38400000"


async def test_total_cap_is_derived_from_declared_length(monkeypatch):
    settings = get_settings()
    clock = _Clock(asyncio.get_running_loop())
    monkeypatch.setattr(middleware_module.asyncio, "get_running_loop", lambda: clock)
    monkeypatch.setattr(middleware_module.asyncio, "timeout_at", clock.timeout_at)
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            clock.advance(0)
            return {"type": "http.request", "body": b"x" * 8192, "more_body": True}
        if calls == 2:
            clock.advance(29)
            # Even an inconsistent ASGI sender exceeding its declaration cannot
            # extend the declared-size cap with another chunk's rate allowance.
            return {"type": "http.request", "body": b"x", "more_body": True}
        assert clock.deadline == clock.started + 31
        clock.advance(3)
        pytest.fail("the derived 31s total cap must interrupt the pending receive")

    middleware = AuthMiddleware(_drain)
    messages = await _request(middleware, _scope(settings, length=8192), receive)
    _retryable_timeout(messages)
    assert clock.now == clock.started + 31
    assert middleware.body_budget.used == 0


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


async def test_under_grace_trickle_times_out_and_releases_before_error_send(monkeypatch):
    settings = get_settings()
    clock = _Clock(asyncio.get_running_loop())
    monkeypatch.setattr(middleware_module.asyncio, "get_running_loop", lambda: clock)
    monkeypatch.setattr(middleware_module.asyncio, "timeout_at", clock.timeout_at)
    initial_size = 255 * 1024
    chunks = iter([(0, b"x" * initial_size, True)] + [(29, b"x", True)] * 3)
    middleware = AuthMiddleware(_drain)
    response_started = asyncio.Event()
    release_send = asyncio.Event()
    messages = []

    async def receive():
        delay, data, more = next(chunks)
        clock.advance(delay)
        return {"type": "http.request", "body": data, "more_body": more}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.start":
            assert middleware.body_budget.used == 0
            assert middleware.body_budget.clients == {}
            assert middleware.body_readers == {}
            response_started.set()
            await release_send.wait()

    task = asyncio.create_task(middleware(_scope(settings), receive, send))
    try:
        await asyncio.wait_for(response_started.wait(), 2)
        # Two delivered trickle bytes earn only 244 microseconds beyond 61.875s.
        assert clock.now - clock.started <= settings.request_body_base_s + (initial_size + 2) / 8192
        assert clock.now - clock.started < 62
        assert not task.done()
        release_send.set()
        await task
        _retryable_timeout(messages)
    finally:
        release_send.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    ("peer", "forwarded"),
    [
        ("192.0.2.1", None),
        ("2001:db8:1234:1::1", None),
        ("172.18.0.2", "198.51.100.7"),
    ],
)
async def test_seventeenth_body_read_gets_429_before_reservation(peer, forwarded):
    settings = get_settings(trusted_proxy_ips="172.18.0.0/16")
    assert settings.request_body_client_concurrency == 16
    middleware = AuthMiddleware(_drain)
    admitted = asyncio.Event()
    waiting = 0
    block = asyncio.Event()

    def scope_for(index):
        client = f"2001:db8:1234:1::{index + 1}" if ":" in peer else peer
        scope = _scope(settings, client=client)
        if forwarded:
            scope["headers"].append((b"x-forwarded-for", forwarded.encode()))
        return scope

    async def receive():
        nonlocal waiting
        waiting += 1
        if waiting == 16:
            admitted.set()
        await block.wait()
        return {"type": "http.disconnect"}

    async def rejected_receive():
        pytest.fail("client concurrency admission must precede receive and reservation")

    tasks = [asyncio.create_task(_request(middleware, scope_for(i), receive)) for i in range(16)]
    try:
        await asyncio.wait_for(admitted.wait(), 2)
        assert middleware.body_budget.used == 0
        messages = await _request(middleware, scope_for(16), rejected_receive)
        assert messages[0]["status"] == 429
        assert json.loads(messages[1]["body"])["retryable"] is True
        assert middleware.body_budget.used == 0
        assert sum(middleware.body_readers.values()) == 16
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert middleware.body_readers == {}
    assert middleware.body_budget.used == 0
    # Cancellation returns every slot, so the same client can immediately upload.
    messages = await _request(middleware, scope_for(0), _empty_receive)
    assert messages[0]["status"] == 200


async def _empty_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def test_reserved_admin_body_slots_are_separately_bounded_and_released():
    settings = get_settings(admin_token="admin-test-token")
    middleware = AuthMiddleware(_drain)
    entered = asyncio.Event()
    blocked = asyncio.Event()
    waiting = 0

    def admin_scope():
        scope = _scope(settings)
        scope["path"] = "/admin/projects"
        scope["headers"] = [(b"authorization", b"Bearer admin-test-token")]
        return scope

    async def receive():
        nonlocal waiting
        waiting += 1
        if waiting == 16:
            entered.set()
        await blocked.wait()
        return {"type": "http.disconnect"}

    tasks = [asyncio.create_task(_request(middleware, admin_scope(), receive)) for _ in range(16)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        response = await _request(middleware, admin_scope(), _empty_receive)
        assert response[0]["status"] == 429
        assert sum(middleware.admin_body_readers.values()) == 16
        assert middleware.body_readers == {}
        # The reverse direction is isolated too; the normal bearer still uses its gate.
        response = await _request(middleware, _scope(settings), _empty_receive)
        assert response[0]["status"] == 200
        assert middleware.body_budget.used == 0
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert middleware.admin_body_readers == {}
    assert middleware.body_readers == {}


async def test_1028_under_grace_connections_from_two_clients_cannot_hold_budget(monkeypatch):
    settings = get_settings()
    middleware = AuthMiddleware(_drain)
    loop = asyncio.get_running_loop()
    active_clock = ContextVar("body_test_clock")
    monkeypatch.setattr(middleware_module.asyncio, "get_running_loop", lambda: active_clock.get())
    monkeypatch.setattr(
        middleware_module.asyncio, "timeout_at", lambda deadline: active_clock.get().timeout_at(deadline)
    )
    admitted = asyncio.Event()
    rejected = asyncio.Event()
    release = asyncio.Event()
    received = rejected_count = 0
    elapsed = []
    statuses = []

    async def request(index):
        nonlocal received, rejected_count
        clock = _Clock(loop)
        active_clock.set(clock)
        reads = 0

        async def receive():
            nonlocal reads, received
            reads += 1
            if reads == 1:
                received += 1
                if received == 32:
                    admitted.set()
                return {"type": "http.request", "body": b"x" * (255 * 1024), "more_body": True}
            await release.wait()
            clock.advance(29)
            return {"type": "http.request", "body": b"x", "more_body": True}

        messages = await _request(middleware, _scope(settings, client=f"192.0.2.{index % 2 + 1}"), receive)
        status = messages[0]["status"]
        statuses.append(status)
        if status == 429:
            rejected_count += 1
            if rejected_count == 996:
                rejected.set()
        else:
            _retryable_timeout(messages)
            elapsed.append(clock.now - clock.started)

    tasks = [asyncio.create_task(request(i)) for i in range(1028)]
    try:
        await asyncio.wait_for(admitted.wait(), 5)
        await asyncio.wait_for(rejected.wait(), 5)
        assert middleware.body_budget.used == 32 * 255 * 1024
        assert middleware.body_budget.used < settings.request_body_global_budget_bytes
        assert sum(middleware.body_readers.values()) == 32
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert statuses.count(429) == 996
    assert statuses.count(408) == 32
    assert max(elapsed) < 62
    assert middleware.body_budget.used == 0
    assert middleware.body_readers == {}


async def test_legitimate_38mb_write_at_64kib_per_second(monkeypatch):
    size = 38_400_000
    chunk_size = 64 * 1024
    chunks = [(1, b"x" * chunk_size, True)] * (size // chunk_size)
    remainder = size % chunk_size
    chunks.append((remainder / chunk_size, b"x" * remainder, False))
    messages, _ = await _timed_request(monkeypatch, get_settings(), chunks, length=size)
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b"38400000"


async def test_finished_body_releases_client_slot_while_handler_stays():
    settings = get_settings(request_body_client_concurrency=1)
    handler_entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(scope, receive, send):
        handler_entered.set()
        await release.wait()
        await _drain(scope, receive, send)

    middleware = AuthMiddleware(handler)
    first = asyncio.create_task(_request(middleware, _scope(settings), _empty_receive))
    second = None
    try:
        await asyncio.wait_for(handler_entered.wait(), 2)
        assert middleware.body_readers == {}
        second = asyncio.create_task(_request(middleware, _scope(settings), _empty_receive))
        await asyncio.sleep(0)
        release.set()
        messages = await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert [result[0]["status"] for result in messages] == [200, 200]
    finally:
        release.set()
        for task in (first, second):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)


async def test_spooled_body_uses_configured_directory(monkeypatch, tmp_path):
    settings = get_settings(request_spool_dir=str(tmp_path), request_body_spool_threshold_bytes=1)
    original_spool = middleware_module.SpooledTemporaryFile
    spools = []

    def capture_spool(*args, **kwargs):
        assert str(kwargs["dir"]) == str(tmp_path)
        spool = original_spool(*args, **kwargs)
        spools.append(spool)
        return spool

    monkeypatch.setattr(middleware_module, "SpooledTemporaryFile", capture_spool)

    async def receive():
        return {"type": "http.request", "body": b"spooled", "more_body": False}

    middleware = AuthMiddleware(_drain)
    messages = await _request(middleware, _scope(settings), receive)
    assert messages[0]["status"] == 200
    assert len(spools) == 1
    assert spools[0]._rolled
    assert spools[0].closed


async def test_ontime_chunk_extends_deadline_before_spool_io(monkeypatch):
    settings = get_settings(
        request_body_base_s=0.1,
        request_body_timeout_s=1,
        request_body_spool_threshold_bytes=1,
    )
    clock = _Clock(asyncio.get_running_loop())
    monkeypatch.setattr(middleware_module.asyncio, "get_running_loop", lambda: clock)
    monkeypatch.setattr(middleware_module.asyncio, "timeout_at", clock.timeout_at)
    operations = []

    async def delayed_io(operation, *args):
        operations.append(operation.__name__)
        # The on-time chunk already earned another second before rollover starts.
        # Leaving the initial base deadline armed would reject this operation.
        clock.advance(0.05)
        return operation(*args)

    monkeypatch.setattr(middleware_module, "_body_io", delayed_io)

    async def receive():
        clock.advance(0.075)
        return {"type": "http.request", "body": b"x" * 8192, "more_body": False}

    middleware = AuthMiddleware(_drain)
    messages = await _request(middleware, _scope(settings, length=8192), receive)
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b"8192"
    assert operations == ["rollover", "write", "read"]
    assert middleware.body_budget.used == 0


async def test_oversized_chunk_is_not_retained_while_error_send_blocks():
    class WeakChunk(bytearray):
        pass

    settings = get_settings(request_max_body_bytes=1)
    middleware = AuthMiddleware(_drain)
    chunk_ref = None
    response_started = asyncio.Event()
    release_send = asyncio.Event()
    messages = []

    async def receive():
        nonlocal chunk_ref
        chunk = WeakChunk(b"oversized")
        chunk_ref = weakref.ref(chunk)
        return {"type": "http.request", "body": chunk, "more_body": False}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.start":
            assert message["status"] == 413
            assert middleware.body_budget.used == 0
            assert middleware.body_readers == {}
            gc.collect()
            assert chunk_ref() is None, "the error traceback must not retain the rejected chunk"
            response_started.set()
            await release_send.wait()

    task = asyncio.create_task(middleware(_scope(settings), receive, send))
    try:
        await asyncio.wait_for(response_started.wait(), 2)
        assert not task.done()
        release_send.set()
        await task
        assert messages[0]["status"] == 413
    finally:
        release_send.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
