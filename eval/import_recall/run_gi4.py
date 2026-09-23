#!/usr/bin/env python3
"""Gate G-I4 (W1.5, D-020 sampled recall): import HLMemo's docs at a pinned tree with ``hlm import``
and check that each question's evidence span is in the top-5 of ``memory.query``.

The questions (``questions.jsonl``) were written by the W1.5 implementer from the pinned files
themselves, one per sampled file (they are NOT the corpus-B hold-out set, which stays sealed).
Metric (W-E): a question is a hit when one of the first five hits' chunks overlaps the gold span
(``lines`` of ``file``) by >= 50 % of the span's characters. The item-level rate (the right file
section among the top-5 items) is reported next to it. Gate: >= 0.90.

    HLM_MODELS_DIR=... uv run python eval/import_recall/run_gi4.py --dsn postgresql://.../hlm_test_a

It uses its own disposable database (the protected ones are refused), migrates it, creates the
project through the ops path (D-015 skeleton card), imports ``docs/`` of ``--commit`` through the
same code as ``hlm import markdown`` (an in-process caller instead of the MCP transport), drains
the embedding outbox with the pinned e5 model, then queries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from hlmemo.auth.context import AuthContext, Role
from hlmemo.cli.mcp_client import ToolCallError
from hlmemo.core.budget import Meter
from hlmemo.core.embedder import Embedder, default_model_dir
from hlmemo.core.errors import ToolError
from hlmemo.core.export_service import export
from hlmemo.core.read_service import default_read_deps, query
from hlmemo.core.write_service import write
from hlmemo.importers.cli import import_async, parse_source
from hlmemo.importers.common import read_text
from hlmemo.ops import service as ops
from hlmemo.server.tools.handlers import as_result_dict
from hlmemo.worker.main import drain

ROOT = Path(__file__).resolve().parents[2]
PROTECTED = {"hlm", "hlm_verify", "hlm_retr", "hlm_test"}
K = 5
GATE = 0.90


def extract(commit: str, dest: Path) -> Path:
    tar = dest / "docs.tar"
    subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", "-o", str(tar), commit, "docs"], check=True
    )
    with tarfile.open(tar) as tf:
        tf.extractall(dest, filter="data")
    return dest


def span_chars(text: str, lines: list[int]) -> tuple[int, int]:
    starts = [0]
    for ln in text.split("\n"):
        starts.append(starts[-1] + len(ln) + 1)
    a, b = lines
    return starts[a - 1], starts[b] - 1  # [start of line a, end of line b)


def migrate(dsn: str) -> None:
    import os

    name = conninfo_to_dict(dsn).get("dbname")
    if name in PROTECTED:
        raise SystemExit(f"refusing protected database {name!r}")
    mig = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "main@head"],
        cwd=ROOT,
        env={**os.environ, "HLM_DB_DSN": dsn},
        capture_output=True,
        text=True,
    )
    if mig.returncode != 0:
        raise SystemExit(mig.stderr[-2000:])


def cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--dsn", required=True, help="a disposable database (never hlm/hlm_verify/hlm_retr/hlm_test)"
    )
    ap.add_argument("--commit", default="82200ae", help="pinned tree (corpus-B pin, 2026-09-23)")
    ap.add_argument("--questions", type=Path, default=Path(__file__).with_name("questions.jsonl"))
    ap.add_argument("--project", default="gi4-hlmemo")
    ap.add_argument("--budget", type=int, default=3000)
    ap.add_argument("--section-chars", type=int, default=8000, help="hlm import --section-chars")
    ap.add_argument("--out", type=Path, help="write the JSON report here")
    a = ap.parse_args(argv)
    migrate(a.dsn)
    a.question_rows = [json.loads(ln) for ln in a.questions.read_text().splitlines() if ln.strip()]
    with tempfile.TemporaryDirectory(prefix="gi4-") as tmp:
        tree = extract(a.commit, Path(tmp))
        out = asyncio.run(main(a, tree))
    text = json.dumps(out, indent=1, ensure_ascii=False)
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n")
    return 0 if out["pass"] else 1


async def main(a: argparse.Namespace, tree: Path) -> dict[str, Any]:
    async def connect() -> psycopg.AsyncConnection:
        conn = await psycopg.AsyncConnection.connect(a.dsn, autocommit=False)
        await conn.execute("SET TIME ZONE 'UTC'")
        return conn

    async with await connect() as conn:
        if await (await conn.execute("SELECT 1 FROM projects WHERE slug = %s", (a.project,))).fetchone():
            raise SystemExit(f"project {a.project!r} exists; use a fresh database or another --project")
        await ops.project_create(conn, a.project, "G-I4 HLMemo docs")
        cur = await conn.execute(
            "INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,"
            " approved_by_device_id)"
            " VALUES (%s, 'personal', %s, %s, 'trusted', now(), 1) RETURNING device_id",
            (f"{a.project}-importer"[:64], f"fp-{a.project}", f"h-{a.project}"),
        )
        (did,) = await cur.fetchone()
        pid = (
            await (
                await conn.execute("SELECT project_id FROM projects WHERE slug = %s", (a.project,))
            ).fetchone()
        )[0]
        await conn.execute(
            "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
            " VALUES (%s, %s, 'write', 1)",
            (did, pid),
        )
        await conn.commit()
    ctx = AuthContext(
        device_id=did,
        device_class="personal",
        is_admin=False,
        token_generation=1,
        grants={pid: Role.WRITE},
        client="gi4/1",
    )
    deps = default_read_deps()

    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        async with await connect() as conn:
            try:
                if tool == "memory.write":
                    res = as_result_dict(await write(conn, ctx, args, raw=args))
                elif tool == "hlm.export":
                    res = await export(conn, ctx, args, deps=deps)
                else:
                    res = await query(conn, ctx, args, deps=deps)
                await conn.commit()
            except ToolError as exc:
                raise ToolCallError(exc.code, exc.message, details=exc.details) from exc
        return res

    parsed = parse_source(
        "markdown", [tree / "docs"], base=tree, now=datetime.now(UTC), section_chars=a.section_chars
    )
    rep = await import_async(
        call, source="markdown", parsed=parsed, project=a.project, dry_run=False, meter=Meter()
    )
    if rep["writes"]["failed"]:
        raise SystemExit(f"import failures: {rep['writes']['failed'][:3]}")
    stats = await drain(connect, Embedder(default_model_dir()))
    texts = {f.relative_to(tree).as_posix(): read_text(f)[0] for f in (tree / "docs").rglob("*.md")}
    questions = a.question_rows
    results = []
    async with await connect() as conn:
        for q in questions:
            res = await call(
                "memory.query", {"project": a.project, "query": q["question"], "token_budget": a.budget}
            )
            # gold spans: the question's own span plus any verified same-fact span elsewhere ("alt")
            spans = []
            for s in [{"file": q["file"], "lines": q["lines"]}, *q.get("alt", [])]:
                gold_text = texts[s["file"]]
                assert gold_text is not None, s["file"]
                spans.append((s["file"], gold_text, *span_chars(gold_text, s["lines"])))
            hit_rank = drill_rank = item_rank = None
            for rank, h in enumerate(res["hits"][:K], 1):
                vid, _, ordinal = h["clue"][1:].partition(".")
                ords = int(ordinal or 0)
                row = await (
                    await conn.execute(
                        "SELECT mv.source->>'path', mv.body, c.char_start, c.char_end,"
                        " (SELECT min(n.char_start) FROM chunks n WHERE n.version_id = mv.version_id"
                        "   AND n.ordinal BETWEEN %s - 1 AND %s + 1),"
                        " (SELECT max(n.char_end) FROM chunks n WHERE n.version_id = mv.version_id"
                        "   AND n.ordinal BETWEEN %s - 1 AND %s + 1)"
                        " FROM memory_versions mv JOIN chunks c ON c.version_id = mv.version_id"
                        " WHERE mv.version_id = %s AND c.ordinal = %s",
                        (ords, ords, ords, ords, int(vid), ords),
                    )
                ).fetchone()
                if row is None or row[0] is None:
                    continue
                path, body, c0, c1, d0, d1 = row
                for file, gold_text, g0, g1 in spans:
                    if path.split("#", 1)[0] != file:
                        continue
                    base = gold_text.find(body)
                    if base < 0:
                        continue
                    need = 0.5 * (g1 - g0)
                    if not (base + len(body) <= g0 or base >= g1) and item_rank is None:
                        item_rank = rank  # the right section of the right file
                    if min(base + c1, g1) - max(base + c0, g0) >= need and hit_rank is None:
                        hit_rank = rank  # W-E: the returned chunk itself holds >= 50 % of the span
                    if min(base + d1, g1) - max(base + d0, g0) >= need and drill_rank is None:
                        drill_rank = rank  # one drilldown of the clue (chunk +-1) reaches the span
            results.append(
                {
                    "id": q["id"],
                    "file": q["file"],
                    "rank": hit_rank,
                    "drill_rank": drill_rank,
                    "item_rank": item_rank,
                }
            )
            await conn.rollback()
    hits = sum(1 for r in results if r["rank"] is not None)
    drills = sum(1 for r in results if r["drill_rank"] is not None)
    items = sum(1 for r in results if r["item_rank"] is not None)
    out = {
        "gate": "G-I4",
        "commit": a.commit,
        "questions": len(results),
        "files": len({r["file"] for r in results}),
        "alt_spans": sum(1 for q in questions if q.get("alt")),
        "evidence_recall_at_5": round(hits / len(results), 3),
        "drill_recall_at_5": round(drills / len(results), 3),
        "item_recall_at_5": round(items / len(results), 3),
        "threshold": GATE,
        "pass": hits / len(results) >= GATE,
        "import": {
            "counts": rep["counts"],
            "written": rep["writes"]["written"],
            "chunks_embedded": stats.chunks_embedded,
        },
        "misses": [r for r in results if r["rank"] is None],
        "results": results,
    }
    return out


if __name__ == "__main__":
    sys.exit(cli())
