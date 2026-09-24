"""Librarian job priorities at the W2b trigger (the W2b x W1.5 x W2d integration; roadmap W2b
"imports priority 6", W2d "cross-project check at priority 2", D-073).

A write batch's server-side ``librarian_priority`` (``memory.register_lesson`` = 2, an import = 6)
is the priority of the ``librarian_write:<event_id>`` job enqueued in the same transaction; an
ordinary write keeps the trigger default 3. The job descriptors, priority included, are recorded
in the event's ``payload.resolved.librarian_jobs`` next to ``resolved.librarian_priority``, so a
projection rebuild re-creates identical rows. A W1.5 ``close`` enqueues nothing (its version is
never current, so there is nothing to review).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from hlmemo.core import lesson_service as ls
from hlmemo.core.budget import Meter
from hlmemo.core.write_service import write
from hlmemo.db.replay import rebuild_projections
from hlmemo.importers.cli import import_async, parse_source
from hlmemo.librarian.trigger import PRIORITY
from tests.integration._import_fixtures import Caller, copy_fixture, make_device, make_project, rows
from tests.integration._w2b_fixtures import dump_w2b, review_deps
from tests.integration._write_fixtures import MAIN, item, seed_world

pytestmark = pytest.mark.integration

IMPORT_CLIENT = "hlm-import/1"

#: per write event: its client, resolved.librarian_priority, the librarian_write jobs it enqueued
#: (dedupe key, priority, job id) and the descriptors it recorded (dedupe key, priority, job id)
EVENT_JOBS_SQL = """
SELECT e.event_id, e.client, (e.payload->'resolved'->>'librarian_priority')::int,
       COALESCE((SELECT array_agg(ARRAY[j.dedupe_key, j.priority::text, j.job_id::text] ORDER BY j.job_id)
                   FROM jobs j WHERE j.source_event_id = e.event_id AND j.kind = 'librarian_write'),
                '{}'),
       COALESCE((SELECT array_agg(ARRAY[d->>'dedupe_key', d->>'priority', d->>'job_id'])
                   FROM jsonb_array_elements(e.payload->'resolved'->'librarian_jobs') d),
                '{}'),
       e.payload->'resolved'->'items'->0->>'valid_to'
  FROM events e
 WHERE e.kind = 'write'
 ORDER BY e.event_id
"""


async def _event_jobs(connect) -> list[dict[str, Any]]:  # noqa: ANN001
    out = []
    for event_id, client, prio, jobs, recorded, valid_to in await rows(connect, EVENT_JOBS_SQL):
        out.append(
            {
                "event_id": event_id,
                "client": client,
                "librarian_priority": prio,
                "jobs": [tuple(j) for j in jobs],
                "recorded": [tuple(d) for d in recorded],
                "closed": valid_to not in (None, "infinity"),
            }
        )
    return out


async def _replay_identical(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        before = await dump_w2b(conn)
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_w2b(conn)
    for table in before:
        assert after[table] == before[table], table


def _one_job(ev: dict[str, Any], priority: int) -> None:
    key = f"librarian_write:{ev['event_id']}"
    assert len(ev["jobs"]) == 1, ev
    (dedupe_key, prio, job_id) = ev["jobs"][0]
    assert (dedupe_key, int(prio)) == (key, priority), ev
    assert ev["recorded"] == [(key, str(priority), job_id)], ev  # replay re-creates this exact row


async def test_register_lesson_job_is_priority_2_and_a_plain_write_3(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        world = await seed_world(conn)
        await conn.commit()
    deps = review_deps()  # HLM_LIBRARIAN_ENABLED: the write path enqueues
    plain = {
        "project": MAIN,
        "request_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "items": [item("Deploy stdin", "Every stdin-reading command in deploy.sh gets </dev/null.")],
    }
    lesson = {
        "project": MAIN,
        "request_id": str(uuid.uuid4()),
        "mistake": "Piped deploy.sh into `ssh host bash -s`; `docker compose exec -T` ate the script.",
        "fix": "Give every stdin-reading command `</dev/null`.",
    }
    async with await connect() as conn:
        await write(conn, world.ctx_a, plain, deps=deps)
        await ls.register_lesson(conn, world.ctx_a, lesson, deps=deps)
        await conn.commit()
    ev_plain, ev_lesson = await _event_jobs(connect)
    assert ev_plain["librarian_priority"] is None
    _one_job(ev_plain, PRIORITY["write"])
    assert PRIORITY["write"] == 3
    assert ev_lesson["librarian_priority"] == ls.PRIORITY == 2
    _one_job(ev_lesson, 2)
    await _replay_identical(connect)
    assert await _event_jobs(connect) == [ev_plain, ev_lesson]


async def test_import_writes_enqueue_one_priority_6_job_each(connect, tmp_path: Path) -> None:  # noqa: ANN001
    root = copy_fixture(tmp_path)
    pid, _card = await make_project(connect, "fx")
    call = Caller(connect, await make_device(connect, "importer", {pid: "write"}), write_deps=review_deps())
    meter = Meter()

    async def run() -> dict[str, Any]:
        parsed = parse_source("markdown", [root / "docs"], base=root, tz=UTC)
        return await import_async(
            call, source="markdown", parsed=parsed, project="fx", dry_run=False, meter=meter
        )

    first = await run()
    assert first["writes"]["written"] == 9 and not first["writes"]["failed"]
    imports = [ev for ev in await _event_jobs(connect) if ev["client"] == IMPORT_CLIENT]
    assert len(imports) == 9
    for ev in imports:
        assert ev["librarian_priority"] == 6
        _one_job(ev, 6)

    # a removed source is closed: the close is an import write (priority 6 recorded) but it
    # enqueues no review (its version already ended; the privacy gate would call it stale)
    (root / "docs" / "other" / "plain.md").unlink()
    rep = await run()
    assert rep["writes"]["closed"] == 1 and not rep["writes"]["failed"]
    after = [ev for ev in await _event_jobs(connect) if ev["client"] == IMPORT_CLIENT]
    (close,) = after[9:]
    assert close["closed"] and close["librarian_priority"] == 6
    assert close["jobs"] == [] and close["recorded"] == []

    # a plain write to the imported project keeps the default priority
    await call(
        "memory.write",
        {
            "project": "fx",
            "request_id": str(uuid.uuid4()),
            "client": "pytest/0",
            "items": [item("t", "plain")],
        },
    )
    (plain,) = [ev for ev in await _event_jobs(connect) if ev["client"] == "pytest/0"]
    _one_job(plain, 3)

    snapshot = await _event_jobs(connect)
    await _replay_identical(connect)
    assert await _event_jobs(connect) == snapshot
