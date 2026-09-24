"""`python -m hlmemo.ops librarian …` (W2b/W2c): audit, questions list, approve-batch, role set.

The approval records one ``answer`` event per question (device 1, the owner's name in ``client``)
and closes the batch (``decided``); the ``apply_batch`` job applies it only in ``assistant``.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from hlmemo import __version__
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._read_fixtures import embedder  # noqa: F401 - fixture by import
from tests.integration._w2b_fixtures import Oracle, embed, write_items
from tests.integration._write_fixtures import MAIN, World, count, item, seed_world

pytestmark = pytest.mark.integration

D_OLD = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D_NEW = datetime(2026, 6, 1, tzinfo=UTC).isoformat()
OLD = ("Worker threads", "The embed worker runs with 2 intra-op threads.")
NEW = ("Worker threads raised", "The embed worker now runs with 4 intra-op threads.")


def _ops(db_dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "HLM_DB_DSN": db_dsn, "HLM_API_PORT": "9"}
    return subprocess.run(
        [sys.executable, "-m", "hlmemo.ops", "librarian", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def test_ops_librarian_audit_approve_and_role(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    await write_items(connect, world.ctx_a, MAIN, [item(*OLD, valid_from=D_OLD)])
    await write_items(connect, world.ctx_a, MAIN, [item(*NEW, valid_from=D_NEW)])
    await embed(connect, embedder)
    oracle = Oracle(relations={(NEW[0], OLD[0]): ("contradicts", "new", "high")})
    llm = ScriptedLLM(default=oracle)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn), provider, connect).drain()

    proc = _ops(db_dsn, "audit", "--project", MAIN, "--json")
    assert proc.returncode == 0, proc.stderr
    audit = json.loads(proc.stdout)
    (prop,) = audit["proposals"]
    assert prop["kind"] == "contradiction" and prop["status"] == "open" and prop["tier"] == "action"
    assert [s["title"] for s in prop["subjects"]] == [NEW[0], OLD[0]]
    assert [a["op"] for a in prop["actions"]] == ["link_insert", "link_insert", "version_close"]
    assert prop["verification"]["agreed"] is True and prop["label"] is None
    (batch,) = audit["batches"]
    assert batch["status"] == "open" and batch["open"] == 1
    proc = _ops(db_dsn, "questions", "list", "--project", MAIN, "--json")
    assert [q["question_id"] for q in json.loads(proc.stdout)["questions"]] == [prop["question_id"]]

    # role: assistant needs the operator's decision event; unknown roles are usage errors
    assert _ops(db_dsn, "role", "set", "assistant", "--decision", "D-test").returncode == 0
    assert _ops(db_dsn, "role", "set", "boss", "--decision", "D-x").returncode == 2
    # approve the batch as the owner
    proc = _ops(db_dsn, "approve-batch", batch["batch_id"], "--owner", "cemals-mb")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["accepted"] == 1
    assert _ops(db_dsn, "approve-batch", batch["batch_id"]).returncode == 1  # nothing open any more
    async with await connect() as conn:
        cur = await conn.execute("SELECT client FROM events WHERE kind = 'answer'")
        assert [r[0] for r in await cur.fetchall()] == [f"hlm-ops/{__version__} (owner:cemals-mb)"]
        cur = await conn.execute("SELECT status FROM librarian_batches")
        assert await cur.fetchall() == [("decided",)]
    await make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        assert await count(conn, "links") == 2
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("applied",)]
        cur = await conn.execute("SELECT status FROM librarian_batches")
        assert await cur.fetchall() == [("applied",)]
    proc = _ops(db_dsn, "audit", "--project", MAIN)
    assert proc.returncode == 0 and "applied" in proc.stdout
    assert _ops(db_dsn, "audit", "--project", "no-such").returncode == 1
    assert json.loads(_ops(db_dsn, "expire").stdout) == {"expired": 0}


async def test_ops_librarian_backfill_history(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    from hlmemo.core.write_service import default_deps

    for i in range(3):  # history written while the librarian was disabled: no jobs
        await write_items(connect, world.ctx_a, MAIN, [item(f"H{i}", f"history {i}")], deps=default_deps())
    await write_items(connect, world.ctx_a, MAIN, [item("New", "reviewed at write time")])
    async with await connect() as conn:
        assert await count(conn, "jobs", "kind = 'librarian_write'") == 1
    proc = _ops(db_dsn, "backfill", "--project", MAIN)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"enqueued_events": 3, "jobs": 3, "project": MAIN, "source_events": 3}
    assert json.loads(_ops(db_dsn, "backfill", "--project", MAIN).stdout)["jobs"] == 0  # idempotent
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT priority, payload->>'trigger', payload->'capabilities'->>'trigger_device_id' FROM jobs"
            " WHERE kind = 'librarian_write' AND payload->>'trigger' = 'backfill'"
        )
        assert await cur.fetchall() == [(6, "backfill", str(world.dev_a))] * 3
