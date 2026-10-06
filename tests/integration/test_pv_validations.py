"""PV-1..PV-5 (protocol §5.2) against a real database: refusals write nothing and never echo a
secret; the legitimate side (imports with ``source``, the librarian's own write) still passes; and
replay rebuilds historic events that today's checks would refuse, byte for byte."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core import lesson_service, write_models, write_service
from hlmemo.core.errors import ToolError
from hlmemo.core.write_service import call_the_day, default_deps, write
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.reserved import ensure_reserved_rows
from tests.integration._write_fixtures import (
    MAIN,
    World,
    count,
    dump_projections,
    item,
    seed_world,
    write_req,
)
from tests.unit.test_pv_validations import FAKE, JWT_EXAMPLES

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:
    async with await connect() as conn:
        w = await seed_world(conn)
        await ensure_reserved_rows(conn)
        await conn.commit()
        return w


async def _counts(conn) -> tuple[int, int]:
    return await count(conn, "events"), await count(conn, "memory_versions")


def _close_req(**kw: Any) -> dict[str, Any]:
    return {
        "project": MAIN,
        "request_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "notes": "Session notes.",
        **kw,
    }


def _source(path: str) -> dict[str, Any]:
    return {"system": "markdown", "path": path, "sha256": "0" * 64}


# --------------------------------------------------------------------------- PV-1
@pytest.mark.parametrize("rule", sorted(FAKE))
@pytest.mark.parametrize("field", ["title", "body", "tags"])
async def test_memory_write_refuses_each_rule_in_each_field(
    connect, world, deps, rule: str, field: str
) -> None:
    secret = FAKE[rule]
    it = item("Plain title", "Plain body.")
    if field == "tags":
        it["tags"] = ["ok", f"k {secret}"]  # the whole value: a cut PEM block is no longer a key
    else:
        it[field] = f"see {secret} here"
    async with await connect() as conn:
        before = await _counts(conn)
        with pytest.raises(ToolError) as exc:
            await write(conn, world.ctx_a, write_req(MAIN, [item("Clean", "Clean body."), it]), deps=deps)
        await conn.rollback()
        assert await _counts(conn) == before
    err = exc.value
    assert err.code == "E_INVALID_ARG"
    path = "items[1].tags[1]" if field == "tags" else f"items[1].{field}"
    assert err.details == {"index": 1, "field": path, "reason": "secret_pattern", "rule": rule}
    assert secret not in json.dumps(err.as_error())


@pytest.mark.parametrize(
    "patch",
    [
        {"notes": "token " + FAKE["github-token"]},
        {"decisions": ["use " + FAKE["sk-api-key"]]},
        {"lessons": [{"title": "t", "body": "key " + FAKE["aws-access-key"]}]},
        {"card_update": {"body": "card " + FAKE["google-api-key"]}},
    ],
)
async def test_call_the_day_refuses_secrets_in_derived_items(
    connect, world, deps, patch: dict[str, Any]
) -> None:
    async with await connect() as conn:
        before = await _counts(conn)
        with pytest.raises(ToolError) as exc:
            await call_the_day(conn, world.ctx_a, _close_req(**patch), deps=deps)
        await conn.rollback()
        assert await _counts(conn) == before
    assert exc.value.code == "E_INVALID_ARG" and exc.value.details["reason"] == "secret_pattern"
    assert all(v not in json.dumps(exc.value.as_error()) for v in FAKE.values())


@pytest.mark.parametrize("part", ["mistake", "fix", "context"])
async def test_register_lesson_refuses_secrets(connect, world, deps, part: str) -> None:
    args = {
        "project": MAIN,
        "request_id": str(uuid.uuid4()),
        "mistake": "Title line\nWhen: it went wrong.",
        "fix": "Do: this. Avoid: that.",
        "context": "Evidence: none.",
    }
    args[part] = args[part] + " " + FAKE["slack-token"]
    async with await connect() as conn:
        with pytest.raises(ToolError) as exc:
            await lesson_service.register_lesson(conn, world.ctx_a, args, deps=deps)
        await conn.rollback()
    assert exc.value.details["reason"] == "secret_pattern" and exc.value.details["rule"] == "slack-token"
    assert FAKE["slack-token"] not in json.dumps(exc.value.as_error())


async def test_clean_text_and_an_import_with_source_pass(connect, world, deps) -> None:
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Key location",
                        "The OpenRouter key lives in .env (OPENROUTER_API_KEY); never paste it.",
                    ),
                    item("Imported doc", "Use sk-learn.", source=_source("docs/a.md")),
                ],
            ),
            deps=deps,
        )
        await conn.commit()
    assert len(res.versions) == 2


# --------------------------------------------------------------------------- PV-2
async def test_raw_supersedes_link_without_source_is_refused(connect, world, deps) -> None:
    async with await connect() as conn:
        old = (
            await write(conn, world.ctx_a, write_req(MAIN, [item("Old", "Old state.")]), deps=deps)
        ).versions[0]
        await conn.commit()
        before = await _counts(conn)
        for links, n_items in (
            ([{"rel": "supersedes", "target": old.logical_id}], 1),
            ([{"rel": "supersedes", "target": "$1"}], 2),
        ):
            items = [item("New", "New state.", links=links)] + ([item("Other", "x")] if n_items == 2 else [])
            with pytest.raises(ToolError) as exc:
                await write(conn, world.ctx_a, write_req(MAIN, items), deps=deps)
            await conn.rollback()
            assert exc.value.code == "E_INVALID_ARG"
            assert exc.value.details == {"index": 0, "reason": "supersedes_needs_updates"}
            assert "updates" in exc.value.message
        assert await _counts(conn) == before


async def test_supersedes_with_source_and_other_relations_pass(connect, world, deps) -> None:
    async with await connect() as conn:
        old = (
            await write(conn, world.ctx_a, write_req(MAIN, [item("Old", "Old state.")]), deps=deps)
        ).versions[0]
        await conn.commit()
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Imported new",
                        "New state.",
                        source=_source("docs/new.md"),
                        links=[{"rel": "supersedes", "target": old.logical_id}],
                    ),
                    item("Related", "See also.", links=[{"rel": "relates_to", "target": old.logical_id}]),
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        assert len(res.versions) == 2
        assert await count(conn, "links", "rel = 'supersedes'") == 1


# --------------------------------------------------------------------------- PV-4
async def test_a_granted_device_is_still_refused_over_mcp_and_the_librarian_writes(
    connect, world, deps
) -> None:
    from hlmemo.librarian.memory import write_rule
    from hlmemo.librarian.reserved import MEMORY_PROJECT, reserved_ids
    from hlmemo.server.tools import handlers, risk

    async with await connect() as conn:
        ids = await reserved_ids(conn)
        ctx = AuthContext(
            device_id=world.dev_a,
            device_class="personal",
            is_admin=False,
            token_generation=1,
            grants={world.main_id: Role.WRITE, ids.memory_project_id: Role.WRITE},
            client="pytest/0",
        )
        before = await _counts(conn)
        calls = [
            handlers.memory_write(conn, ctx, write_req(MEMORY_PROJECT, [item("x", "y")])),
            handlers.memory_write(
                conn, ctx, write_req(MAIN, [item("x", "y", project_ids=[MAIN, MEMORY_PROJECT])])
            ),
            handlers.memory_call_the_day(conn, ctx, _close_req(project=MEMORY_PROJECT)),
            risk.memory_register_lesson(
                conn,
                ctx,
                {"project": MEMORY_PROJECT, "request_id": str(uuid.uuid4()), "mistake": "m", "fix": "f"},
            ),
        ]
        for call in calls:
            with pytest.raises(ToolError) as exc:
                await call
            assert exc.value.code == "E_FORBIDDEN_PROJECT"
            assert exc.value.details == {"project": MEMORY_PROJECT, "reason": "reserved_project"}
        await conn.rollback()
        assert await _counts(conn) == before
        vid = await write_rule(
            conn,
            title="Rule",
            text="Prefer dated evidence over memory claims.",
            clue_refs=[],
            dedupe="pv4",
            deps=deps,
        )
        await conn.commit()
        assert vid > 0


# --------------------------------------------------------------------------- PV-5 through register_lesson
async def test_register_lesson_refuses_a_conflicting_status(connect, world, deps) -> None:
    args = {
        "project": MAIN,
        "request_id": str(uuid.uuid4()),
        "mistake": "Title\nWhen: x.",
        "fix": "Do: y.",
        "tags": ["py@3.12", "active", "historical"],
    }
    async with await connect() as conn:
        with pytest.raises(ToolError) as exc:
            await lesson_service.register_lesson(conn, world.ctx_a, args, deps=deps)
        await conn.rollback()
        args["tags"] = ["py@3.12", "resolved", "historical"]
        res = await lesson_service.register_lesson(conn, world.ctx_a, args, deps=deps)
        await conn.commit()
    assert exc.value.details["reason"] == "lesson_status_conflict"
    assert res["clue"].startswith("v")


# --------------------------------------------------------------------------- replay
async def test_replay_rebuilds_historic_events_that_today_would_be_refused(
    connect, world, deps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Events written before PV-2/PV-3 (simulated by switching the checks off) replay byte-identically:
    ``db/replay.py`` never calls the write service, so no validation runs on replay."""
    async with await connect() as conn:
        old = (
            await write(conn, world.ctx_a, write_req(MAIN, [item("Old", "Old state.")]), deps=deps)
        ).versions[0]
        await conn.commit()
        historic = write_req(
            MAIN,
            [
                item("New", "New state.", links=[{"rel": "supersedes", "target": old.logical_id}]),
                item("   ", "A blank title, as an old client could send it."),
            ],
        )
        with pytest.raises(ToolError):
            await write(conn, world.ctx_a, historic, deps=deps)
        await conn.rollback()
        monkeypatch.setattr(write_service, "_check_supersedes_links", lambda i, it: None)
        monkeypatch.setattr(write_models, "_reject_blank", lambda v: v)
        await write(conn, world.ctx_a, historic, deps=deps)
        await conn.commit()
        monkeypatch.undo()
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before
        assert await count(conn, "links", "rel = 'supersedes'") == 1


# --------------------------------------------------------------------------- review 111 (round 1)
@pytest.mark.parametrize(
    "extra",
    [
        lambda target, s: {
            "updates": [{"item": target, "old_span": "no such span", "mode": "revise", "replacement": s}]
        },
        lambda target, s: {"source": {"system": "markdown", "path": f"docs/{s}.md", "sha256": "0" * 64}},
        lambda target, s: {"describes": [f"src/{s}.py"]},
    ],
    ids=["updates.replacement", "source.path", "describes"],
)
async def test_review_secret_outside_title_body_tags_is_refused_and_never_stored(
    connect, world, deps, extra
) -> None:
    """Astra #1 / Sol #1: a secret in a field the server does not index used to be stored verbatim in
    the carrier's event (the update itself was rejected). Now the whole write is refused."""
    s = FAKE["sk-api-key"]
    async with await connect() as conn:
        target = (
            await write(conn, world.ctx_a, write_req(MAIN, [item("Old", "Old text here.")]), deps=deps)
        ).versions[0]
        await conn.commit()
        before = await _counts(conn)
        carrier = item("Carrier", "Clean body.", **extra(f"v{target.version_id}", s))
        with pytest.raises(ToolError) as exc:
            await write(conn, world.ctx_a, write_req(MAIN, [carrier]), deps=deps)
        await conn.rollback()
        assert await _counts(conn) == before
        cur = await conn.execute("SELECT count(*) FROM events WHERE payload::text LIKE %s", (f"%{s}%",))
        assert (await cur.fetchone())[0] == 0
    assert exc.value.details["reason"] == "secret_pattern" and s not in json.dumps(exc.value.as_error())


async def test_review_secret_shaped_client_is_refused(connect, world, deps) -> None:
    req = write_req(MAIN, [item("t", "b")])
    req["client"] = "agent/" + FAKE["slack-token"]
    async with await connect() as conn:
        with pytest.raises(ToolError) as exc:
            await write(conn, world.ctx_a, req, deps=deps)
        await conn.rollback()
    assert exc.value.details["field"] == "client"


@pytest.mark.parametrize("example", JWT_EXAMPLES)
async def test_review_jwt_documentation_example_is_written(connect, world, deps, example: str) -> None:
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(MAIN, [item("JWT format", f"JWT format example: {example}")]),
            deps=deps,
        )
        await conn.commit()
    assert len(res.versions) == 1


# --------------------------------------------------------------------------- review 112 (round 2)
async def test_review_secret_shaped_client_header_never_reaches_the_logs(db_dsn, caplog) -> None:
    """Astra/Sol round 2: the X-HLM-Client header is logged in a ``finally``; a secret-shaped one is
    redacted there, refused when register_lesson would store it, and absent from every log level."""
    import logging

    from tests.integration._mcp_fixtures import MCP_HEADERS, bearer, running_app, write_args, writer_on

    secret = "ghp_" + "Ab3Cd9Ef2Gh7Ij4Kl8Mn1Op6Qr5St0UvWx9Y"
    headers_extra = {"X-HLM-Client": secret}
    caplog.set_level(logging.DEBUG)
    async with running_app(db_dsn) as client:
        token = await writer_on(client, "pv-logs")

        async def call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            msg: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
            if params is not None:
                msg["params"] = params
            r = await client.post("/mcp", json=msg, headers={**MCP_HEADERS, **bearer(token), **headers_extra})
            assert r.status_code == 200, r.text
            return r.json()["result"]

        lesson = await call(
            "tools/call",
            {
                "name": "memory.register_lesson",
                "arguments": {
                    "project": "pv-logs",
                    "request_id": str(uuid.uuid4()),
                    "mistake": "Title line\nWhen: x.",
                    "fix": "Do: y.",
                },
            },
        )
        assert lesson.get("isError") is True
        envelope = json.loads(lesson["content"][0]["text"])
        assert envelope["details"]["reason"] == "secret_pattern" and envelope["details"]["field"] == "client"
        assert secret not in json.dumps(lesson)
        wrote = await call(
            "tools/call",
            {"name": "memory.write", "arguments": write_args("pv-logs", [item("t", "Clean body.")])},
        )
        assert not wrote.get("isError"), (
            wrote
        )  # memory.write stores its own `client` argument, not the header
        await call("tools/list")
    assert caplog.records, "the MCP path logs at least the per-call line"
    assert secret not in caplog.text
    assert all(secret not in str(r.args) and secret not in r.getMessage() for r in caplog.records)
    assert "<redacted:github-token>" in caplog.text
