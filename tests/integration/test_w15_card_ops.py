"""Carried items of W1.5 (D-069): the D-015 skeleton project card and the librarian fields of
``python -m hlmemo.ops status``."""

from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace

import pytest

from hlmemo.cli.mcp_client import ToolCallError
from hlmemo.core.skeleton_card import (
    SKELETON_TAG,
    backfill_skeleton_cards,
    skeleton_body,
    skeleton_request_id,
)
from hlmemo.db.replay import rebuild_projections
from hlmemo.ops import service as ops
from tests.integration._import_fixtures import Caller, make_device, make_project, rows, scalar
from tests.integration._mcp_fixtures import create_project, running_app
from tests.integration._write_fixtures import dump_projections

pytestmark = pytest.mark.integration


async def test_ops_project_create_writes_the_skeleton_card(connect) -> None:
    pid, card_lid = await make_project(connect, "carded")
    (vid, kind, tags, pinned, stability, scope, body, event_id) = (
        await rows(
            connect,
            "SELECT version_id, kind, tags, pinned, stability, device_scope, body, source_event_id"
            " FROM memory_versions WHERE logical_id = %s",
            (card_lid,),
        )
    )[0]
    assert (kind, tags, pinned, stability, scope) == ("project_card", [SKELETON_TAG], True, "stable", "all")
    assert body == skeleton_body("carded", "Carded")
    ev = (
        await rows(
            connect,
            "SELECT kind, device_id, client, request_id::text, project_id FROM events WHERE event_id = %s",
            (event_id,),
        )
    )[0]
    assert ev[:2] == ("write", 1) and ev[2].startswith("hlm-ops/") and ev[4] == pid
    assert ev[3] == skeleton_request_id("carded", card_lid)
    kinds = [r[0] for r in await rows(connect, "SELECT kind FROM events ORDER BY event_id")]
    assert kinds == ["project_created", "grant_added", "write"]  # one transaction, in order
    assert await scalar(connect, "SELECT count(*) FROM jobs WHERE kind = 'embed'") == 1

    # memory.query shows the card; call_the_day must name the skeleton as the expected head
    call = Caller(connect, await make_device(connect, "writer", {pid: "write"}))
    res = await call("memory.query", {"project": "carded", "query": "anything", "token_budget": 2000})
    assert res["card"]["clue"] == f"v{vid}" and "Skeleton card (D-015)" in res["card"]["text"]
    close = {
        "project": "carded",
        "request_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "notes": "first session",
        "card_update": {"body": "# Carded\n\nThe real card."},
    }
    with pytest.raises(ToolCallError) as exc:
        await call("memory.call_the_day", close)
    assert exc.value.code == "E_VERSION_CONFLICT" and exc.value.details["current_version_id"] == vid
    close["card_update"]["expected_version_id"] = vid
    await call("memory.call_the_day", close)
    # the skeleton keeps its past valid-time segment; the new card is current from the close on
    res = await call("memory.query", {"project": "carded", "query": "anything", "token_budget": 2000})
    assert res["card"]["text"].startswith("# Carded\n\nThe real card.") and res["card"]["clue"] != f"v{vid}"

    async with await connect() as conn:
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before


async def test_admin_http_project_create_writes_the_skeleton_card(db_dsn, connect) -> None:
    async with running_app(db_dsn) as client:
        pid = await create_project(client, "via-http")
    card_lid = await scalar(connect, "SELECT card_logical_id FROM projects WHERE project_id = %s", (pid,))
    got = await rows(
        connect,
        "SELECT mv.kind, e.device_id, e.kind FROM memory_versions mv JOIN events e"
        " ON e.event_id = mv.source_event_id WHERE mv.logical_id = %s",
        (card_lid,),
    )
    assert got == [("project_card", 1, "write")]


async def test_backfill_writes_one_card_per_user_project(db_dsn, connect) -> None:
    async with await connect() as conn:
        await conn.execute(
            "INSERT INTO projects (slug, name, policy, archived_at) VALUES"
            " ('legacy', 'Legacy', '{}', NULL),"
            " ('sleepy', 'Sleepy', '{}', now()),"
            " ('hlm-global', 'Global experience (reserved)', '{\"reserved\": true}', NULL)"
        )
        await conn.commit()
    await make_project(connect, "already")  # has its card
    assert await backfill_skeleton_cards(db_dsn) == 1
    carded = await rows(
        connect,
        "SELECT p.slug FROM projects p WHERE EXISTS (SELECT 1 FROM memory_versions mv"
        " WHERE mv.logical_id = p.card_logical_id) ORDER BY 1",
    )
    assert carded == [("already",), ("legacy",)]
    assert await backfill_skeleton_cards(db_dsn) == 0  # idempotent
    clients = await rows(connect, "SELECT client FROM events WHERE kind = 'write' ORDER BY event_id")
    assert [c[0].split("/")[0] for c in clients] == ["hlm-ops", "hlm-migrate"]


async def test_ops_status_reports_the_librarian_heartbeat_fields(connect, tmp_path) -> None:
    fields = {"ready", "in_flight", "oldest_ready_age_s", "failed_24h", "spend_today_usd", "spend_hour_usd"}
    settings = SimpleNamespace(librarian_role="observer", librarian_heartbeat_file=tmp_path / "missing.json")
    async with await connect() as conn:
        st = await ops.status(conn)
        lib = await ops.librarian_status(conn, settings)
        assert set(st["librarian"]) >= fields | {"reserved_usd", "role", "breaker_state", "breaker_source"}
        # no call in the window: closed, as the librarian's heartbeat says (e2e #10: was "idle")
        assert (lib["role"], lib["breaker_state"], lib["breaker_source"]) == ("observer", "closed", "ledger")
        assert lib["llm_calls_15m"] == 0
        await conn.execute(
            "INSERT INTO llm_calls (call_id, task, profile, model_id, prompt_version, schema_version, mode,"
            " outcome) VALUES (gen_random_uuid(), 'pair_check', 'p', 'm', 'v1', 'v1', 'live', 'breaker_open')"
        )
        await conn.execute(
            "INSERT INTO jobs (kind, dedupe_key, payload) VALUES ('librarian_write', 'lw:1', '{}')"
        )
        lib = await ops.librarian_status(conn, settings)
        assert lib["breaker_state"] == "open" and lib["ready"] == 1 and lib["llm_calls_15m"] == 1
        hb = tmp_path / "hb.json"
        hb.write_text(json.dumps({"ts": time.time(), "breaker_state": "degraded", "enabled": True}))
        lib = await ops.librarian_status(
            conn, SimpleNamespace(librarian_role="observer", librarian_heartbeat_file=hb)
        )
        assert (lib["breaker_state"], lib["breaker_source"]) == ("degraded", "heartbeat")
        hb.write_text(json.dumps({"ts": time.time() - 3600, "breaker_state": "closed"}))  # stale → ledger
        lib = await ops.librarian_status(
            conn, SimpleNamespace(librarian_role="observer", librarian_heartbeat_file=hb)
        )
        assert (lib["breaker_state"], lib["breaker_source"]) == ("open", "ledger")
        await conn.rollback()
