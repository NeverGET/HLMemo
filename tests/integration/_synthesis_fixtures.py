"""W2e synthesis fixture world (τ_s calibration, CI replay, G-LIVE-D): HLMemo's own PUBLIC docs.

The corpus is ``docs/`` of the pinned commit ``CORPUS_COMMIT`` (``git archive``; ``docs/private``
is not tracked and is refused defensively), imported into project ``syn-docs`` through the same
code as ``hlm import markdown`` (an in-process caller instead of the MCP transport, like
``eval/import_recall/run_gi4.py``), with a fixed import clock so undated items get a stable
``valid_from``. The embed outbox is drained once with the pinned e5 model. The tables are
truncated first (``RESTART IDENTITY``), so version ids and clues are stable across runs.

The questions (``tests/fixtures/synthesis/questions.json``, sha256-pinned in ``SHA256SUMS``) were
written by the W2e implementer from these pinned files: each has verbatim ``keys`` (any one of them
answers it), the source ``file`` and a ``split`` (``cal`` tunes τ_s and the prompt; ``test`` is
held out). They are NOT the corpus-B hold-out set (never opened).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from hlmemo.auth.context import AuthContext, Role
from hlmemo.cli.mcp_client import ToolCallError
from hlmemo.core.budget import Meter
from hlmemo.core.embedder import Embedder
from hlmemo.core.errors import ToolError
from hlmemo.core.export_service import export
from hlmemo.core.read_service import ReadDeps, query
from hlmemo.core.write_service import write
from hlmemo.importers.cli import import_async, parse_source
from hlmemo.ops import service as ops
from hlmemo.server.tools.handlers import as_result_dict
from hlmemo.worker.main import drain

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "synthesis"
CORPUS_COMMIT = "ad7ccc2"  # main at the W2e fork (2026-09-24); docs/ only
PROJECT = "syn-docs"
SECTION_CHARS = 8000  # the G-I4 import granularity (D-072)
IMPORT_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
#: undated imported items get the import time as valid_from (D-072); the fixture pins them to this
#: instant so the prompts (whose excerpts carry a date) and therefore the cassette keys are stable
CORPUS_DATE = datetime(2026, 9, 23, 0, 0, tzinfo=UTC)
CLIENT = "pytest-w2e/0"

_WS = re.compile(r"\s+")


def norm(text: str) -> str:
    """Key matching (``eval/realdata/run_eval.py``): NFKC + casefold + collapsed whitespace."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def keys_in(text: str, keys: list[str]) -> list[str]:
    t = norm(text)
    return [k for k in keys if norm(k) and norm(k) in t]


def _pinned(name: str) -> Any:
    """A fixture file whose sha256 matches ``SHA256SUMS`` (an edit is a gate change: re-record the
    cassettes, re-run the calibration and G-LIVE-D, and update the pin in the same commit)."""
    raw = (FIXTURE_DIR / name).read_bytes()
    pins = dict(
        reversed(line.split()) for line in (FIXTURE_DIR / "SHA256SUMS").read_text().splitlines() if line
    )
    digest = hashlib.sha256(raw).hexdigest()
    assert pins.get(name) == digest, f"{name}: sha256 {digest} != pinned {pins.get(name)}"
    return json.loads(raw)


def load_env_file() -> None:
    """Live runs: ``HLM_W2E_ENV_FILE=<path to .env>`` supplies OPENROUTER_API_KEY (values are never
    printed or copied; variables already set win)."""
    path = os.environ.get("HLM_W2E_ENV_FILE")
    if not path:
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_questions() -> list[dict[str, Any]]:
    return _pinned("questions.json")["questions"]


def extract_docs(commit: str, dest: Path) -> Path:
    """``docs/`` of ``commit`` under ``dest`` (never the working tree: the pin is the corpus)."""
    tar = dest / "docs.tar"
    subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", "-o", str(tar), commit, "docs"], check=True
    )
    with tarfile.open(tar) as tf:
        names = tf.getnames()
        assert not [n for n in names if n.startswith("docs/private")], "docs/private must never be imported"
        tf.extractall(dest, filter="data")
    return dest


@dataclass(slots=True)
class SynWorld:
    ctx: AuthContext
    project_id: int
    device_id: int
    items: int
    chunks: int


async def seed_corpus(
    connect: Any, deps: ReadDeps, embedder: Embedder, *, commit: str = CORPUS_COMMIT
) -> SynWorld:
    """Truncate, create ``syn-docs`` + a trusted personal device with write on it, import the pinned
    docs and embed them (about two minutes)."""
    from tests.conftest import TRUNCATE_SQL

    async with await connect() as conn:
        await conn.execute(TRUNCATE_SQL)
        await conn.commit()
        (started,) = await (await conn.execute("SELECT now()")).fetchone()
        await conn.commit()
        await ops.project_create(conn, PROJECT, "W2e synthesis corpus (HLMemo public docs)")
        cur = await conn.execute(
            "INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,"
            " approved_by_device_id)"
            " VALUES ('syn-reader', 'personal', 'fp-syn-reader', 'h-syn-reader', 'trusted', now(), 1)"
            " RETURNING device_id"
        )
        (did,) = await cur.fetchone()
        (pid,) = await (
            await conn.execute("SELECT project_id FROM projects WHERE slug = %s", (PROJECT,))
        ).fetchone()
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
        client=CLIENT,
    )

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

    with tempfile.TemporaryDirectory(prefix="w2e-docs-") as tmp:
        tree = extract_docs(commit, Path(tmp))
        parsed = parse_source(
            "markdown", [tree / "docs"], base=tree, now=IMPORT_NOW, section_chars=SECTION_CHARS
        )
        rep = await import_async(
            call, source="markdown", parsed=parsed, project=PROJECT, dry_run=False, meter=Meter()
        )
    assert not rep["writes"]["failed"], rep["writes"]["failed"][:3]
    await drain(connect, embedder)
    async with await connect() as conn:
        await conn.execute(
            "UPDATE memory_versions SET valid_from = %s WHERE project_id = %s AND valid_from >= %s",
            (CORPUS_DATE, pid, started),
        )
        await conn.commit()
        items, chunks = await (
            await conn.execute(
                "SELECT count(DISTINCT mv.version_id), count(c.chunk_id) FROM memory_versions mv"
                " JOIN chunks c ON c.version_id = mv.version_id WHERE mv.project_id = %s",
                (pid,),
            )
        ).fetchone()
        await conn.commit()
    return SynWorld(ctx=ctx, project_id=pid, device_id=did, items=int(items), chunks=int(chunks))


async def direct_connect(dsn: str) -> psycopg.AsyncConnection:
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=False)
    await conn.execute("SET TIME ZONE 'UTC'")
    return conn


__all__ = [
    "CORPUS_COMMIT",
    "CORPUS_DATE",
    "FIXTURE_DIR",
    "PROJECT",
    "SynWorld",
    "extract_docs",
    "keys_in",
    "load_env_file",
    "load_questions",
    "norm",
    "seed_corpus",
]
