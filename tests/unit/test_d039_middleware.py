"""D-039 body admission bounds memory before authentication and through replay."""

from __future__ import annotations

import asyncio
import json

import pytest
from tests.unit.test_final_body import _scope, _trusted_transport  # noqa: F401 - pytest fixture

from hlmemo.config import get_settings
from hlmemo.server.middleware import AuthMiddleware


@pytest.fixture(autouse=True)
def _clean_tables():
    """These transport-only tests do not need the global database cleanup fixture."""
    yield


async def _ok(scope, receive, send):
    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _request(middleware, scope, receive):
    messages = []

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)
    return messages


@pytest.mark.parametrize("declared", [False, True])
async def test_tricklers_cannot_exceed_global_or_client_budget(declared):
    settings = get_settings(
        request_body_global_budget_bytes=1024,
        request_body_client_budget_bytes=512,
        request_body_timeout_s=3,
    )
    middleware = AuthMiddleware(_ok)
    blocked = asyncio.Event()
    started = 0
    admitted = asyncio.Event()

    def receiver():
        first = True

        async def receive():
            nonlocal first, started
            if first:
                first = False
                return {"type": "http.request", "body": b"x" * 400, "more_body": True}
            started += 1
            if started == 2:
                admitted.set()
            await blocked.wait()
            return {"type": "http.disconnect"}

        return receive

    tasks = [
        asyncio.create_task(
            _request(
                middleware,
                _scope(settings, client=f"192.0.2.{i}", length=400 if declared else None),
                receiver(),
            )
        )
        for i in (1, 2)
    ]
    try:
        await asyncio.wait_for(admitted.wait(), 1)
        assert middleware.body_budget.used == 800
        # First attacker shares an already-full client bucket; others hit the global cap.
        for i in (1, *range(3, 30)):
            response = await asyncio.wait_for(
                _request(
                    middleware,
                    _scope(settings, client=f"192.0.2.{i}", length=400 if declared else None),
                    receiver(),
                ),
                0.1,
            )
            assert response[0]["status"] == 503
            assert json.loads(response[1]["body"])["retryable"] is True
            assert middleware.body_budget.used == 800
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert middleware.body_budget.used == 0
    assert middleware.body_budget.clients == {}


@pytest.mark.parametrize("end", ["normal", "cancel", "error"])
async def test_completed_body_stays_accounted_until_route_releases_it(end):
    settings = get_settings(request_body_global_budget_bytes=1024, request_body_client_budget_bytes=1024)
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def route(scope, receive, send):
        data = await receive()
        assert len(data["body"]) == 400
        entered.set()
        await finish.wait()
        if end == "error":
            raise RuntimeError("route failed")

    async def receive():
        return {"type": "http.request", "body": b"x" * 400}

    middleware = AuthMiddleware(route)
    task = asyncio.create_task(_request(middleware, _scope(settings), receive))
    await asyncio.wait_for(entered.wait(), 1)
    assert middleware.body_budget.used == 400
    if end == "cancel":
        task.cancel()
    else:
        finish.set()
    result = await asyncio.gather(task, return_exceptions=True)
    if end == "error":
        assert isinstance(result[0], RuntimeError)
    assert middleware.body_budget.used == 0
    assert middleware.body_budget.clients == {}


async def test_minimum_average_rate_expires_despite_inactivity_progress():
    settings = get_settings(
        request_body_timeout_s=1,
        request_body_base_s=0.01,
        request_body_min_rate_bytes_s=1024,
    )
    reads = 0

    async def receive():
        nonlocal reads
        reads += 1
        if reads == 1:
            return {"type": "http.request", "body": b"x" * 64, "more_body": True}
        await asyncio.sleep(0.02)
        return {"type": "http.request", "body": b"x", "more_body": True}

    middleware = AuthMiddleware(_ok)
    response = await asyncio.wait_for(_request(middleware, _scope(settings), receive), 0.3)
    assert response[0]["status"] == 408
    assert json.loads(response[1]["body"])["retryable"] is True
    assert 2 <= reads < 10
    assert middleware.body_budget.used == 0


async def test_normal_link_accepts_38mb_body_with_default_budgets():
    settings = get_settings()
    remaining = 38_400_000
    received = 0

    async def receive():
        nonlocal remaining
        await asyncio.sleep(0.001)
        size = min(remaining, 1024 * 1024)
        remaining -= size
        return {"type": "http.request", "body": b"x" * size, "more_body": bool(remaining)}

    async def route(scope, receive, send):
        nonlocal received
        while True:
            message = await receive()
            received += len(message["body"])
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = AuthMiddleware(route)
    response = await _request(middleware, _scope(settings, length=remaining), receive)
    assert response[0]["status"] == 200
    assert received == 38_400_000
    assert middleware.body_budget.used == 0
