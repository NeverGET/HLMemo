#!/usr/bin/env python3
"""G-E-TEMP / G-E-W2b runner: the W2b librarian at ``assistant`` with APPROVE-ALL on a DISPOSABLE
eval database, then the real-data evaluation (``run_eval.py``) against it (PHASE2-4-ROADMAP W2b).

The hold-out questions never pass through this script's own logic: it only forwards the arguments
after ``--`` to ``run_eval.py`` (the orchestrator's eval runner opens them). Approval never looks at
the labels: every batch is accepted (the gate measures the librarian's unfiltered effect).

Steps (all on ``--dsn``, which must be a disposable copy of an imported corpus database):
  1. ``alembic upgrade main@head``; refuse protected databases (hlm, hlm_verify, hlm_retr, hlm_test);
  2. record the ``assistant`` role decision (ops actor) and backfill the W2b review of the project's
     history (``hlm.ops librarian backfill``: ``librarian_write:<event_id>`` for every write event);
  3. run the librarian worker in-process (role ``assistant``, the given profile + fallback, the
     spend guard capped at ``--max-usd``) until the queue is empty;
  4. approve every batch of the project (``hlm.ops librarian approve-batch`` semantics, the ops
     actor) and drain again: the ``apply_batch`` jobs apply links and bi-temporal closes;
  5. embed the survivor versions of the closes (the pinned E5 model, in-process);
  6. write ``<out-dir>/librarian_report.json`` (jobs, proposals by kind/relation, applied
     mutations, spend from ``llm_calls``) and run ``run_eval.py <args after --> --project P
     --out-dir <out-dir>/eval`` against the api serving that database (``--server`` in the
     forwarded arguments).

    uv run --frozen python eval/realdata/run_librarian_eval.py \\
        --dsn postgresql://hlm:hlm@127.0.0.1:5432/hlm_eval_temp --project corpus-a \\
        --env-file .env --max-usd 3 --out-dir eval/results/w2b-temp -- \\
        --questions docs/private/realdata-yt/questions.jsonl --server http://127.0.0.1:8765 --budgets 3000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
PROTECTED = frozenset({"hlm", "hlm_verify", "hlm_retr", "hlm_test"})


def load_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def refuse_protected(dsn: str) -> None:
    from psycopg.conninfo import conninfo_to_dict

    name = conninfo_to_dict(dsn).get("dbname")
    if name in PROTECTED:
        raise SystemExit(
            f"refusing to run the librarian eval on protected database {name!r}: use a disposable copy"
        )


async def _queued(conn: Any, project_id: int) -> tuple[int, float | None]:
    cur = await conn.execute(
        """
        SELECT count(*), EXTRACT(EPOCH FROM min(run_after) - now())::float8 FROM jobs
         WHERE kind = 'librarian_write' AND status IN ('queued', 'running')
           AND (payload->>'project_id')::bigint = %s
        """,
        (project_id,),
    )
    n, wait = await cur.fetchone()
    return int(n), wait


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from psycopg import AsyncConnection

    from hlmemo.config import get_settings
    from hlmemo.librarian.budget import DbBudget
    from hlmemo.librarian.provider import Provider
    from hlmemo.librarian.roles import latest_role_decision, record_batch_decision, record_role_decision
    from hlmemo.librarian.worker import LibrarianWorker, open_pool
    from hlmemo.ops.librarian import _ops_ctx, backfill

    settings = get_settings()

    async def connect() -> AsyncConnection:
        c = await AsyncConnection.connect(args.dsn, autocommit=False)
        await c.execute("SET TIME ZONE 'UTC'")
        await c.commit()
        return c

    report: dict[str, Any] = {"project": args.project, "profile": args.profile, "fallback": args.fallback}
    async with await connect() as conn:
        cur = await conn.execute("SELECT project_id FROM projects WHERE slug = %s", (args.project,))
        row = await cur.fetchone()
        if row is None:
            raise SystemExit(f"project {args.project!r} not found in the eval database")
        project_id = int(row[0])
        if await latest_role_decision(conn, None) != "assistant":
            await record_role_decision(
                conn,
                role="assistant",
                decided_by=_ops_ctx("eval"),
                decision="G-E-TEMP eval run (approve-all)",
            )
        report["backfill"] = await backfill(conn, args.project, device=args.device)
        await conn.commit()
    t0 = time.monotonic()
    async with open_pool(args.dsn) as pool:
        provider = Provider.from_settings(settings, conn=pool.connection)
        budget = provider.budget if isinstance(provider.budget, DbBudget) else None
        worker = LibrarianWorker(settings, provider=provider, connect=connect, budget=budget)

        async def drain_all(label: str) -> int:
            done = 0
            deadline = time.monotonic() + args.max_wait_s
            while time.monotonic() < deadline:
                n = await worker.drain()
                done += n
                async with await connect() as conn:
                    left, wait = await _queued(conn, project_id)
                    await conn.commit()
                if left == 0:
                    break
                if n == 0:
                    await asyncio.sleep(min(10.0, max(1.0, wait or 1.0)))
                if worker.paused:
                    print(f"[{label}] librarian paused ({worker.pause_reason}); waiting", flush=True)
            print(f"[{label}] {done} jobs processed", flush=True)
            return done

        try:
            report["review_jobs"] = await drain_all("review")
            async with await connect() as conn:  # approve-all: every batch with open questions
                cur = await conn.execute(
                    "SELECT DISTINCT batch_id::text FROM librarian_questions WHERE project_id = %s"
                    " AND status = 'open' ORDER BY 1",
                    (project_id,),
                )
                batches = [r[0] for r in await cur.fetchall()]
                approved = 0
                for b in batches:
                    summary = await record_batch_decision(
                        conn, batch_id=b, approver=_ops_ctx("eval-approve-all"), decision="accept"
                    )
                    approved += summary["accepted"]
                await conn.commit()
            report["approved_batches"], report["approved_questions"] = len(batches), approved
            report["apply_jobs"] = await drain_all("apply")
        finally:
            await provider.aclose()
    report["librarian_seconds"] = round(time.monotonic() - t0, 1)
    if not args.skip_embed:
        from hlmemo.core.embedder import Embedder, default_model_dir
        from hlmemo.worker.main import drain as embed_drain

        embedder = Embedder(default_model_dir())
        try:
            stats = await embed_drain(connect, embedder)
            report["embedded_jobs"] = stats.jobs_done
        finally:
            embedder.close()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT kind, status, proposal->>'relation', count(*) FROM librarian_questions"
            " WHERE project_id = %s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
            (project_id,),
        )
        report["questions"] = [list(r) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT rel, count(*) FROM links WHERE %s = ANY(project_ids) AND props->>'by' = 'librarian'"
            " GROUP BY 1 ORDER BY 1",
            (project_id,),
        )
        report["links"] = dict(await cur.fetchall())
        cur = await conn.execute(
            "SELECT count(*) FROM events WHERE kind IN ('librarian', 'answer')"
            " AND payload->'resolved'->'mutations' @> '[{\"op\": \"version_close\"}]'"
        )
        report["close_events"] = (await cur.fetchone())[0]
        cur = await conn.execute(
            "SELECT count(*), COALESCE(sum(cost_usd), 0)::float8, count(*) FILTER (WHERE outcome <> 'ok')"
            " FROM llm_calls"
        )
        n, cost, not_ok = await cur.fetchone()
        report["llm_calls"], report["spend_usd"], report["llm_calls_not_ok"] = (
            int(n),
            round(cost, 6),
            int(not_ok),
        )
        await conn.commit()
    return report


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, passthrough = argv[:i], argv[i + 1 :]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", required=True, help="a DISPOSABLE copy of the imported eval database")
    ap.add_argument("--project", required=True)
    ap.add_argument(
        "--device", help="device whose capabilities the backfilled jobs carry (default: each writer)"
    )
    ap.add_argument("--profile", default="openrouter-gpt6-luna")
    ap.add_argument("--fallback", default="openrouter")
    ap.add_argument("--env-file", default="", help="KEY=VALUE secrets (the repo .env), never echoed")
    ap.add_argument("--max-usd", type=float, default=3.0, help="runaway guard (hour/day/month caps)")
    ap.add_argument("--max-wait-s", type=float, default=3600.0, help="per drain phase")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--skip-embed", action="store_true")
    ap.add_argument("--skip-eval", action="store_true", help="librarian steps only")
    args = ap.parse_args(argv)
    refuse_protected(args.dsn)
    if args.env_file:
        load_env_file(Path(args.env_file))
    os.environ.update(
        HLM_DB_DSN=args.dsn,
        HLM_PROFILE=args.profile,
        HLM_FALLBACK_PROFILE=args.fallback,
        HLM_LIBRARIAN_ENABLED="true",
        HLM_LIBRARIAN_ROLE="assistant",
        HLM_LLM_MODE="live",
        HLM_LLM_BUDGET_HOUR_USD=str(args.max_usd),
        HLM_LLM_BUDGET_DAY_USD=str(args.max_usd),
        HLM_LLM_BUDGET_MONTH_USD=str(max(args.max_usd, 60.0)),
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "main@head"], cwd=ROOT, check=True, env=os.environ.copy()
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(run(args))
    (args.out_dir / "librarian_report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    if args.skip_eval:
        return 0
    cmd = [
        sys.executable,
        str(ROOT / "eval" / "realdata" / "run_eval.py"),
        *passthrough,
        "--project",
        args.project,
        "--out-dir",
        str(args.out_dir / "eval"),
    ]
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
