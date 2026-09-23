"""G3 fixture world for the read-path gates (G2 service-level, G3 recall, G4 latency).

Loads ``tests/fixtures/g3/items.jsonl`` (2,000 items in ``fx-main`` + 400 decoys in ``fx-other``)
through ``write_service.write`` in batches of 50 from a trusted personal loader device holding
``write`` on both projects, then drains the embed outbox ONCE with the real pinned ONNX model
(~11.6k chunks on CPU: 10-15 min). The load is cached across pytest sessions: when the database
already holds exactly the expected rows (versions, chunks == embeddings, no open jobs) it is
reused as-is, so point ``HLM_TEST_DSN`` at a dedicated database (e.g. ``hlm_retr``) and keep it.

Reader identities:
* ``ctx_reader`` — trusted personal device with ``read`` on ``fx-main`` only (the gate caller);
* ``ctx_loader`` — the loader device (``write`` on both projects);
* ``ctx_other`` — trusted work device with ``read`` on ``fx-other`` only.

The conftest's autouse ``_clean_tables`` truncates every table between tests; the modules that
use this world import the no-op override below (``from ... import _clean_tables``) so the world
survives the module. The ``logical_key`` of every item is kept as a ``lk:<key>`` tag (tags are
not part of the lexical/vector index) so gold keys map back to ``logical_id``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.embedder import Embedder, default_model_dir
from hlmemo.core.read_service import ReadDeps, default_read_deps
from hlmemo.core.write_service import default_deps, write
from hlmemo.worker.main import drain

log = logging.getLogger("tests.read_fixtures")

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "g3"
MAIN = "fx-main"
OTHER = "fx-other"
LOADER = "fx-loader"
READER = "fx-reader"
OTHER_DEV = "fx-other-reader"
BATCH = 50
KEY_TAG = "lk:"


def load_items() -> list[dict[str, Any]]:
    with (FIXTURE_DIR / "items.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_queries() -> list[dict[str, Any]]:
    with (FIXTURE_DIR / "queries.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@dataclass(slots=True)
class RetrWorld:
    main_id: int
    other_id: int
    loader_id: int
    reader_id: int
    other_dev_id: int
    ctx_loader: AuthContext
    ctx_reader: AuthContext
    ctx_other: AuthContext
    key_to_logical: dict[str, int]  # logical_key -> logical_id
    logical_to_key: dict[int, str]
    version_to_logical: dict[int, int]  # head version_id -> logical_id
    logical_to_version: dict[int, int]
    n_chunks: int
    load_seconds: float | None = None
    drain_seconds: float | None = None

    def logical_of_clue(self, clue: str) -> int:
        vid = int(clue[1:].split(".", 1)[0])
        return self.version_to_logical[vid]

    def project_of_logical(self, lid: int) -> str:
        return self.logical_to_key[lid].split(":", 1)[0]


# --------------------------------------------------------------------------- state checks
async def _state(conn: psycopg.AsyncConnection, n_items: int) -> dict[str, Any] | None:
    """Return the world's ids if the database holds exactly the expected fixture, else ``None``."""
    cur = await conn.execute("SELECT slug, project_id FROM projects WHERE slug = ANY(%s)", ([MAIN, OTHER],))
    projects = dict(await cur.fetchall())
    if set(projects) != {MAIN, OTHER}:
        return None
    cur = await conn.execute(
        "SELECT name, device_id FROM devices WHERE name = ANY(%s) AND status = 'trusted'",
        ([LOADER, READER, OTHER_DEV],),
    )
    devices = dict(await cur.fetchall())
    if set(devices) != {LOADER, READER, OTHER_DEV}:
        return None
    cur = await conn.execute(
        """
        SELECT (SELECT count(*) FROM memory_versions WHERE kind <> 'project_card'),
               (SELECT count(*) FROM memory_versions WHERE superseded_at <> 'infinity'),
               (SELECT count(*) FROM chunks),
               (SELECT count(*) FROM embeddings),
               (SELECT count(*) FROM jobs WHERE status <> 'done')
        """
    )
    n_versions, n_superseded, n_chunks, n_emb, n_open = await cur.fetchone()
    if n_versions != n_items or n_superseded != 0 or n_chunks == 0 or n_chunks != n_emb or n_open != 0:
        return None
    return {"projects": projects, "devices": devices, "n_chunks": n_chunks}


async def _wipe(conn: psycopg.AsyncConnection) -> None:
    from tests.conftest import TRUNCATE_SQL

    await conn.execute(TRUNCATE_SQL)
    await conn.commit()


async def _seed_identities(conn: psycopg.AsyncConnection) -> tuple[dict[str, int], dict[str, int]]:
    cur = await conn.execute(
        "INSERT INTO projects (slug, name) VALUES (%s, 'Fixture main'), (%s, 'Fixture other')"
        " RETURNING slug, project_id",
        (MAIN, OTHER),
    )
    projects = dict(await cur.fetchall())
    cur = await conn.execute(
        """
        INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,
                             approved_by_device_id)
        VALUES (%s, 'personal', 'fp-fx-loader', 'h-fx-loader', 'trusted', now(), 1),
               (%s, 'personal', 'fp-fx-reader', 'h-fx-reader', 'trusted', now(), 1),
               (%s, 'work', 'fp-fx-other', 'h-fx-other', 'trusted', now(), 1)
        RETURNING name, device_id
        """,
        (LOADER, READER, OTHER_DEV),
    )
    devices = dict(await cur.fetchall())
    await conn.execute(
        """
        INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)
        VALUES (%s, %s, 'write', 1), (%s, %s, 'write', 1), (%s, %s, 'read', 1), (%s, %s, 'read', 1)
        """,
        (
            devices[LOADER],
            projects[MAIN],
            devices[LOADER],
            projects[OTHER],
            devices[READER],
            projects[MAIN],
            devices[OTHER_DEV],
            projects[OTHER],
        ),
    )
    await conn.commit()
    return projects, devices


def _contexts(
    projects: dict[str, int], devices: dict[str, int]
) -> tuple[AuthContext, AuthContext, AuthContext]:
    loader = AuthContext(
        device_id=devices[LOADER],
        device_class="personal",
        is_admin=False,
        token_generation=1,
        grants={projects[MAIN]: Role.WRITE, projects[OTHER]: Role.WRITE},
        client="pytest-fixture/0",
    )
    reader = AuthContext(
        device_id=devices[READER],
        device_class="personal",
        is_admin=False,
        token_generation=1,
        grants={projects[MAIN]: Role.READ},
        client="pytest/0",
    )
    other = AuthContext(
        device_id=devices[OTHER_DEV],
        device_class="work",
        is_admin=False,
        token_generation=1,
        grants={projects[OTHER]: Role.READ},
        client="pytest/0",
    )
    return loader, reader, other


def _write_item(it: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": it["kind"],
        "title": it["title"],
        "body": it["body"],
        "tags": [*it.get("tags", []), KEY_TAG + it["logical_key"]],
        "stability": it.get("stability", "volatile"),
        "importance": it.get("importance"),
        "valid_from": it["valid_from"],
    }


async def _load_items(conn: psycopg.AsyncConnection, ctx: AuthContext, items: list[dict[str, Any]]) -> float:
    deps = default_deps()
    t0 = time.perf_counter()
    by_project: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_project.setdefault(it["project"], []).append(it)
    for project, its in by_project.items():
        for start in range(0, len(its), BATCH):
            batch = its[start : start + BATCH]
            await write(
                conn,
                ctx,
                {
                    "project": project,
                    "request_id": str(uuid.uuid4()),
                    "client": "pytest-fixture/0",
                    "occurred_at": max(b["valid_from"] for b in batch),
                    "items": [_write_item(b) for b in batch],
                    "token_budget": 32000,
                },
                deps=deps,
            )
            await conn.commit()
    return time.perf_counter() - t0


async def _maps(conn: psycopg.AsyncConnection) -> tuple[dict[str, int], dict[int, int]]:
    cur = await conn.execute(
        """
        SELECT mv.logical_id, mv.version_id, t.tag FROM memory_versions mv, unnest(mv.tags) AS t(tag)
        WHERE t.tag LIKE %s AND mv.superseded_at = 'infinity'
        """,
        (KEY_TAG + "%",),
    )
    key_to_logical: dict[str, int] = {}
    logical_to_version: dict[int, int] = {}
    for lid, vid, tag in await cur.fetchall():
        key_to_logical[tag[len(KEY_TAG) :]] = lid
        logical_to_version[lid] = max(vid, logical_to_version.get(lid, 0))
    return key_to_logical, logical_to_version


async def ensure_loaded(connect, embedder: Embedder) -> RetrWorld:  # noqa: ANN001
    items = load_items()
    load_s: float | None = None
    drain_s: float | None = None
    async with await connect() as conn:
        state = await _state(conn, len(items))
        if state is None:
            log.warning("G3 fixture not present in the test database: loading %s items (minutes)", len(items))
            await _wipe(conn)
            projects, devices = await _seed_identities(conn)
            loader, _, _ = _contexts(projects, devices)
            load_s = await _load_items(conn, loader, items)
            log.warning("G3 fixture written in %.1fs; draining embeddings", load_s)
        else:
            projects, devices = state["projects"], state["devices"]
    if load_s is not None:
        t0 = time.perf_counter()
        stats = await drain(connect, embedder)
        drain_s = time.perf_counter() - t0
        log.warning(
            "G3 fixture drained: %s jobs, %s chunks in %.0fs", stats.jobs_done, stats.chunks_embedded, drain_s
        )
        if stats.jobs_failed or stats.errors:
            raise RuntimeError(f"embedding drain had failures: {stats.errors[:5]}")
        async with await connect() as conn:
            state = await _state(conn, len(items))
            if state is None:
                raise RuntimeError("G3 fixture load finished but the database state does not match")
    async with await connect() as conn:
        key_to_logical, logical_to_version = await _maps(conn)
    loader, reader, other = _contexts(projects, devices)
    return RetrWorld(
        main_id=projects[MAIN],
        other_id=projects[OTHER],
        loader_id=devices[LOADER],
        reader_id=devices[READER],
        other_dev_id=devices[OTHER_DEV],
        ctx_loader=loader,
        ctx_reader=reader,
        ctx_other=other,
        key_to_logical=key_to_logical,
        logical_to_key={lid: key for key, lid in key_to_logical.items()},
        version_to_logical={vid: lid for lid, vid in logical_to_version.items()},
        logical_to_version=logical_to_version,
        n_chunks=state["n_chunks"],
        load_seconds=load_s,
        drain_seconds=drain_s,
    )


# --------------------------------------------------------------------------- pytest fixtures
@pytest.fixture
def _clean_tables() -> None:
    """Override of the conftest autouse truncation: the fixture world must survive the module."""
    yield


@pytest.fixture(scope="session")
def embedder() -> Embedder:
    model_dir = default_model_dir()
    if not (model_dir / "onnx" / "model.onnx").is_file():
        pytest.skip(f"E5 model not present at {model_dir}")
    instance = Embedder(model_dir)
    yield instance
    instance.close()


@pytest.fixture(scope="session")
def read_deps(embedder: Embedder) -> ReadDeps:
    deps = default_read_deps()
    deps._embedder = embedder
    yield deps
    deps._embedder = None


@pytest.fixture(scope="session")
async def retr_world(connect, embedder: Embedder) -> RetrWorld:  # noqa: ANN001
    return await ensure_loaded(connect, embedder)


@pytest.fixture
async def world(retr_world: RetrWorld, connect) -> RetrWorld:  # noqa: ANN001
    """Per-test guard: if another test truncated the world, reload it (cheap when intact)."""
    async with await connect() as conn:
        state = await _state(conn, len(retr_world.key_to_logical))
    if state is not None:
        return retr_world
    from hlmemo.core.embedder import Embedder as _E

    return await ensure_loaded(connect, _E(default_model_dir()))


__all__ = [
    "MAIN",
    "OTHER",
    "RetrWorld",
    "_clean_tables",
    "embedder",
    "ensure_loaded",
    "load_items",
    "load_queries",
    "read_deps",
    "retr_world",
    "world",
]
