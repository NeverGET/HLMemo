"""W2d independent validation of the deterministic constants (Sol 41 #6): the public bench v2 T9
pack (``bench/v2/tasks/t9_risk_check.json``: 30 lessons and 25 tasks, authored separately from the
W2d fixture), mapped to lessons and run through the deterministic stage of ``memory.risk_check``.

Mapping: each library entry becomes one ``lesson`` item in project ``t9-main`` (title = its first
sentence, body = its text); a case with ``gold.warn`` is a positive (catch = a gold lesson among the
warnings), ``warn:false`` a negative (any warning = false warn) — the bench's own definitions.
Gate: catch ≥ 0.70 with the calibrated ``VEC_MAX_DIST``/``TAU`` (G-R2's bar). The grid around them
is printed for the report (``-s``).
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core import risk_service as rs
from hlmemo.core.read_service import default_read_deps
from hlmemo.core.write_service import default_deps, write
from hlmemo.worker.main import drain
from tests.integration._risk_fixtures import rates, score_case

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "bench" / "v2" / "tasks" / "t9_risk_check.json"
PROJECT = "t9-main"
TAG = "t9:"


def _title(text: str) -> str:
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return first if len(first) <= 120 else first[:119].rsplit(" ", 1)[0] + "…"


async def _seed(connect, embedder) -> tuple[AuthContext, int, dict[int, str], list[dict[str, Any]]]:  # noqa: ANN001
    from tests.conftest import TRUNCATE_SQL

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    async with await connect() as conn:
        await conn.execute(TRUNCATE_SQL)
        cur = await conn.execute(
            "INSERT INTO projects (slug, name) VALUES (%s, 'T9') RETURNING project_id", (PROJECT,)
        )
        (pid,) = await cur.fetchone()
        cur = await conn.execute(
            "INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,"
            " approved_by_device_id) VALUES ('t9-dev', 'personal', 'fp-t9', 'h-t9', 'trusted', now(), 1)"
            " RETURNING device_id"
        )
        (did,) = await cur.fetchone()
        await conn.execute(
            "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
            " VALUES (%s, %s, 'write', 1)",
            (did, pid),
        )
        await conn.commit()
        ctx = AuthContext(did, "personal", False, 1, {pid: Role.WRITE}, "pytest-t9/0")
        items = [
            {"kind": "lesson", "title": _title(le["text"]), "body": le["text"], "tags": [TAG + le["id"]]}
            for le in pack["library"]
        ]
        ack = await write(
            conn,
            ctx,
            {"project": PROJECT, "request_id": str(uuid.uuid4()), "client": "pytest-t9/0", "items": items},
            deps=default_deps(),
        )
        await conn.commit()
    await drain(connect, embedder)
    lesson_of = {v.logical_id: pack["library"][v.index]["id"] for v in ack.versions}
    return ctx, pid, lesson_of, pack["cases"]


@pytest.fixture(autouse=True)
async def _clean_tables():
    yield


async def test_t9_independent_validation(connect) -> None:  # noqa: ANN001
    deps = default_read_deps()
    ctx, pid, lesson_of, cases = await _seed(connect, deps.embedder)
    per_case = []
    async with await connect() as conn:
        for case in cases:
            cands, _ = await rs.candidates(conn, ctx, pid, case["task"], deps)
            await conn.commit()
            per_case.append(({**case, "gold": case["gold"]["lesson_ids"]}, cands))

    def evaluate(vec_max: float, tau: float) -> dict[str, float]:
        scored = []
        for case, cands in per_case:
            hits = sorted(
                ((rs.det_score(c.ranks, c.vector_dist, vec_max), lesson_of[c.logical_id]) for c in cands),
                reverse=True,
            )
            hits = [h for h in hits if h[0] >= tau][: rs.MAX_WARNINGS]
            scored.append(score_case(case, bool(hits), [h[1] for h in hits]))
        return rates(scored)

    recall = sum(
        bool(set(c["gold"]) & {lesson_of[x.logical_id] for x in k}) for c, k in per_case if c["gold"]
    ) / sum(1 for c, _ in per_case if c["gold"])
    at = evaluate(rs.VEC_MAX_DIST, rs.TAU)
    print(f"\nT9 (independent, {len(cases)} cases): candidate recall@{rs.TOP_K}={recall:.3f}")
    print(f"T9 at VEC_MAX_DIST={rs.VEC_MAX_DIST} TAU={rs.TAU}: {at}")
    for vm in (0.14, 0.15, 0.16, 0.17, 0.18):
        row = " ".join(
            f"{tau:.4f}:{r['catch']:.2f}/{r['false_warn']:.2f}"
            for tau in (0.030, 0.0325, 0.040, 0.045, 0.047, 0.048)
            for r in [evaluate(vm, tau)]
        )
        print(f"  vec_max={vm:.2f} (tau:catch/false_warn) {row}")
    assert at["catch"] >= 0.70, at
