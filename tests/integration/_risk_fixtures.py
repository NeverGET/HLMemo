"""W2d risk-check fixture world (G-R1..G-R4, G-LIVE-C): ``tests/fixtures/risk/*.json`` seeded into
the test database through the production write paths.

Projects: ``rk-main`` (home), ``rk-shell`` (another granted project), ``rk-secret`` (NO grant for the
reader) and the reserved ``hlm-global`` (read grant; its policy is ``librarian: off`` by migration
0006, so its experiences never reach the LLM judge). Lessons are registered with
``memory.register_lesson`` (``core.lesson_service``); ``hlm-global`` experiences and distractor facts
use ``memory.write``. The embed outbox is drained once with the real pinned ONNX model.

Identities: ``ctx_loader`` (write everywhere) and ``ctx_reader`` (the caller of every gate: personal
device, write on rk-main, read on rk-shell and hlm-global, nothing on rk-secret).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.embedder import Embedder
from hlmemo.core.lesson_service import lesson_body, lesson_title, register_lesson
from hlmemo.core.write_service import default_deps, write
from hlmemo.librarian.reserved import GLOBAL_PROJECT, ensure_reserved_rows
from hlmemo.worker.main import drain

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "risk"
MAIN, SHELL, SECRET = "rk-main", "rk-shell", "rk-secret"
PROJECT_OF = {"main": MAIN, "main-work": MAIN, "shell": SHELL, "secret": SECRET, "global": GLOBAL_PROJECT}
LESSON_TAG = "rk:"


def load_env_file() -> None:
    """Live runs: ``HLM_W2D_ENV_FILE=<path to .env>`` supplies OPENROUTER_API_KEY (values are never
    printed or copied; variables already set win)."""
    path = os.environ.get("HLM_W2D_ENV_FILE")
    if not path:
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_library() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "library.json").read_text(encoding="utf-8"))


def load_cases() -> list[dict[str, Any]]:
    return json.loads((FIXTURE_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]


@dataclass(slots=True)
class RiskWorld:
    ctx_loader: AuthContext
    ctx_reader: AuthContext
    projects: dict[str, int]
    lesson_of_logical: dict[int, str] = field(default_factory=dict)  # logical_id -> "Lnn"
    logical_of_lesson: dict[str, int] = field(default_factory=dict)
    version_of_lesson: dict[str, int] = field(default_factory=dict)

    def lesson_of_clue(self, clue: str) -> str | None:
        vid = int(clue[1:].split(".", 1)[0])
        for lid, v in self.version_of_lesson.items():
            if v == vid:
                return lid
        return None


async def _identities(conn: psycopg.AsyncConnection) -> tuple[dict[str, int], dict[str, int]]:
    reserved = await ensure_reserved_rows(conn)
    cur = await conn.execute(
        "INSERT INTO projects (slug, name) VALUES (%s, 'Risk main'), (%s, 'Risk shell'), (%s, 'Risk secret')"
        " RETURNING slug, project_id",
        (MAIN, SHELL, SECRET),
    )
    projects = dict(await cur.fetchall())
    projects[GLOBAL_PROJECT] = reserved.global_project_id
    cur = await conn.execute(
        """
        INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,
                             approved_by_device_id)
        VALUES ('rk-loader', 'personal', 'fp-rk-loader', 'h-rk-loader', 'trusted', now(), 1),
               ('rk-reader', 'personal', 'fp-rk-reader', 'h-rk-reader', 'trusted', now(), 1)
        RETURNING name, device_id
        """
    )
    devices = dict(await cur.fetchall())
    grants = [
        (devices["rk-loader"], projects[MAIN], "write"),
        (devices["rk-loader"], projects[SHELL], "write"),
        (devices["rk-loader"], projects[SECRET], "write"),
        (devices["rk-loader"], projects[GLOBAL_PROJECT], "write"),
        (devices["rk-reader"], projects[MAIN], "write"),
        (devices["rk-reader"], projects[SHELL], "read"),
        (devices["rk-reader"], projects[GLOBAL_PROJECT], "read"),
    ]
    for did, pid, role in grants:
        await conn.execute(
            "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
            " VALUES (%s, %s, %s, 1)",
            (did, pid, role),
        )
    await conn.commit()
    return projects, devices


def _ctx(device_id: int, grants: dict[int, Role]) -> AuthContext:
    return AuthContext(
        device_id=device_id,
        device_class="personal",
        is_admin=False,
        token_generation=1,
        grants=grants,
        client="pytest-risk/0",
    )


async def seed_world(connect, embedder: Embedder) -> RiskWorld:  # noqa: ANN001
    """Truncate, seed the library and drain the embed outbox (a few seconds)."""
    from tests.conftest import TRUNCATE_SQL

    lib = load_library()
    async with await connect() as conn:
        await conn.execute(TRUNCATE_SQL)
        await conn.commit()
        projects, devices = await _identities(conn)
        loader = _ctx(devices["rk-loader"], {pid: Role.WRITE for pid in projects.values()})
        reader = _ctx(
            devices["rk-reader"],
            {projects[MAIN]: Role.WRITE, projects[SHELL]: Role.READ, projects[GLOBAL_PROJECT]: Role.READ},
        )
        world = RiskWorld(ctx_loader=loader, ctx_reader=reader, projects=projects)
        deps = default_deps()
        for les in lib["lessons"]:
            project = PROJECT_OF[les["project"]]
            scope = {"main-work": "class:work"}.get(les["project"], "all")
            if les.get("scope") == "self":
                scope = f"device:{reader.device_id}"
            tags = [*les.get("tags", []), LESSON_TAG + les["id"]]
            if les.get("kind", "lesson") == "lesson":
                args = {
                    "project": project,
                    "request_id": str(uuid.uuid4()),
                    "mistake": les["mistake"],
                    "fix": les["fix"],
                    "tags": tags,
                    "device_scope": scope,
                }
                if les.get("context"):
                    args["context"] = les["context"]
                res = await register_lesson(conn, loader, args, deps=deps)
                vid, lid = res["version_id"], res["logical_id"]
            else:
                ack = await write(
                    conn,
                    loader,
                    {
                        "project": project,
                        "request_id": str(uuid.uuid4()),
                        "client": "pytest-risk/0",
                        "items": [
                            {
                                "kind": les["kind"],
                                "title": lesson_title(les["mistake"]),
                                "body": lesson_body(les["mistake"], les["fix"], les.get("context")),
                                "tags": tags,
                                "device_scope": scope,
                                "stability": "stable",
                            }
                        ],
                    },
                    deps=deps,
                )
                vid, lid = ack.versions[0].version_id, ack.versions[0].logical_id
            await conn.commit()
            world.version_of_lesson[les["id"]] = vid
            world.logical_of_lesson[les["id"]] = lid
            world.lesson_of_logical[lid] = les["id"]
        for fact in lib["facts"]:
            await write(
                conn,
                loader,
                {
                    "project": PROJECT_OF[fact["project"]],
                    "request_id": str(uuid.uuid4()),
                    "client": "pytest-risk/0",
                    "items": [{"kind": "fact", "title": fact["title"], "body": fact["body"]}],
                },
                deps=deps,
            )
            await conn.commit()
    await drain(connect, embedder)
    return world


def score_case(case: dict[str, Any], warned: bool, lessons: list[str | None]) -> dict[str, Any]:
    """catch = warn with at least one gold lesson among the warnings; false_warn = any warning on a
    negative (the bench v2 T9 definitions)."""
    gold = set(case["gold"])
    if gold:
        return {"positive": True, "caught": warned and bool(gold & set(lessons))}
    return {"positive": False, "false_warn": warned}


def rates(scored: list[dict[str, Any]]) -> dict[str, float]:
    pos = [s for s in scored if s["positive"]]
    neg = [s for s in scored if not s["positive"]]
    return {
        "catch": sum(s["caught"] for s in pos) / max(1, len(pos)),
        "false_warn": sum(s["false_warn"] for s in neg) / max(1, len(neg)),
        "n_pos": len(pos),
        "n_neg": len(neg),
    }


__all__ = [
    "FIXTURE_DIR",
    "MAIN",
    "SECRET",
    "SHELL",
    "RiskWorld",
    "load_cases",
    "load_library",
    "rates",
    "score_case",
    "seed_world",
]
