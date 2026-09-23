"""G-L5 — replay never calls an LLM (CC-5): with ``HLM_LLM_MODE=off`` and the network denied
(any Python socket connect or httpx send raises), rebuilding the projections from a history that
contains every librarian-era event kind gives identical projections."""

from __future__ import annotations

import socket

import httpx
import pytest

from hlmemo.core.write_service import default_deps
from hlmemo.db.replay import rebuild_projections
from tests.integration._librarian_fixtures import (
    add_new_kind_events,
    dump_full_jobs_and_questions,
    seed_reserved,
)
from tests.integration._write_fixtures import World, dump_projections, seed_world

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def test_gl5_replay_never_calls_llm(db_dsn, connect, world: World, deps, monkeypatch) -> None:  # noqa: ANN001
    added = await add_new_kind_events(connect, db_dsn, world, deps)
    assert added >= 10
    attempts: list[str] = []

    def deny(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        attempts.append(repr(args[:2]))
        raise OSError("network denied during replay (G-L5)")

    async def deny_send(self, request, **kwargs):  # noqa: ANN001, ANN003, ANN202
        attempts.append(str(request.url))
        raise httpx.ConnectError("network denied during replay (G-L5)")

    monkeypatch.setenv("HLM_LLM_MODE", "off")
    async with await connect() as conn:  # libpq's socket is native, not Python's: the DB still works
        before = {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)}
        monkeypatch.setattr(socket.socket, "connect", deny)
        monkeypatch.setattr(socket, "create_connection", deny)
        monkeypatch.setattr(httpx.AsyncClient, "send", deny_send)
        stats = await rebuild_projections(conn)
        await conn.commit()
        after = {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)}
    assert attempts == []
    assert after == before
    assert stats.links == len(before["links"]) and stats.links > 0
