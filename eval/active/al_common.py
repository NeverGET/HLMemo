"""Shared paths, config, IO and guards of the active-librarian ceiling harness (PLAN §3).

Tracked files (this directory) carry HLMemo product content only: code, task prompts, the rubric
template and the arm configuration. Everything derived from real data (packets, model outputs,
labels, keys, the pre-registration and run logs) lives under the PRIVATE directory, by default the
gitignored ``docs/private/active-librarian/`` (D-220); ``ensure_private`` refuses any other target.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PRIVATE_ENV = "HLM_AL_PRIVATE_DIR"
DSN_ENV = "HLM_AL_DSN"
ANY_DB_ENV = "HLM_AL_ALLOW_ANY_DB"
CEILING_DB = "hlm_al_ceiling"
DEFAULT_DSN = f"postgresql://hlm:hlm@127.0.0.1:55432/{CEILING_DB}"
CONFIG_PATH = HERE / "config.json"
PROMPT_DIR = HERE / "prompts"
READER_TEMPLATE = HERE / "READER-INSTRUCTIONS.md"
EXPERIMENTS = ("E0", "E1", "E2", "E3")
ARM_EXPERIMENTS = ("E1", "E2", "E3")
#: experiment -> (prompt file, schema file) under prompts/
PROMPTS = {
    "E1": ("e1_distill.md", "e1.schema.json"),
    "E2": ("e2_card.md", "e2.schema.json"),
    "E3": ("e3_lessons.md", "e3.schema.json"),
}


class HarnessError(RuntimeError):
    """A refusal of the harness (missing pre-registration, wrong database, spend cap, …)."""


# --------------------------------------------------------------------------- paths
def private_dir() -> Path:
    raw = os.environ.get(PRIVATE_ENV)
    return (Path(raw).expanduser() if raw else ROOT / "docs" / "private" / "active-librarian").resolve()


def _inside(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _git_ignored(path: Path) -> bool:
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(ROOT), "check-ignore", "-q", str(path)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0


def ensure_private(path: Path | str) -> Path:
    """``path`` resolved, refusing anything outside the private directory, and a private directory
    inside this repository that git would track (it must be gitignored)."""
    base = private_dir()
    p = Path(path).expanduser().resolve()
    if not _inside(p, base):
        raise HarnessError(f"refusing to write outside the private directory: {p}")
    if _inside(base, ROOT.resolve()) and not _git_ignored(base):
        raise HarnessError(f"the private directory {base} is inside the repository but not gitignored")
    return p


def pdir(*parts: str) -> Path:
    """A directory under the private root (created, mode 0700)."""
    d = ensure_private(private_dir().joinpath(*parts))
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def packets_dir(exp: str) -> Path:
    return pdir("packets", exp)


def key_dir() -> Path:
    """Grading keys live OUTSIDE the packets and grading directories (readers never get them)."""
    return pdir("grading-key")


# --------------------------------------------------------------------------- IO
def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def write_text(path: Path | str, text: str) -> Path:
    p = ensure_private(path)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def write_bytes(path: Path | str, data: bytes) -> Path:
    p = ensure_private(path)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return p


def write_json(path: Path | str, obj: Any) -> Path:
    return write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def append_jsonl(path: Path | str, obj: Any) -> None:
    p = ensure_private(path)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def read_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path: Path | str) -> list[Any]:
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- config
def load_config(path: Path | None = None) -> dict[str, Any]:
    return read_json(path or CONFIG_PATH)


def prompt_files(exp: str) -> tuple[Path, Path]:
    md, schema = PROMPTS[exp]
    return PROMPT_DIR / md, PROMPT_DIR / schema


# --------------------------------------------------------------------------- database guard
_DBNAME = re.compile(r"(?:^|[\s?&])dbname=([^\s&]+)")


def dsn_dbname(dsn: str) -> str | None:
    m = _DBNAME.search(dsn)
    if m:
        return m.group(1)
    tail = dsn.split("://", 1)[-1]
    if "/" not in tail:
        return None
    return tail.split("/", 1)[1].split("?", 1)[0] or None


def ceiling_dsn(dsn: str | None = None) -> str:
    """The DSN of the restored ceiling database; refuses any other database name (never prod) unless
    ``HLM_AL_ALLOW_ANY_DB=1`` (tests against a scratch database)."""
    dsn = dsn or os.environ.get(DSN_ENV) or DEFAULT_DSN
    if dsn_dbname(dsn) != CEILING_DB and os.environ.get(ANY_DB_ENV) != "1":
        raise HarnessError(f"the harness reads only the database {CEILING_DB!r} (set {DSN_ENV})")
    return dsn


async def connect_ro(dsn: str | None = None) -> Any:
    """A read-only connection to the ceiling database (every transaction READ ONLY)."""
    from psycopg import AsyncConnection

    conn = await AsyncConnection.connect(ceiling_dsn(dsn), autocommit=True)
    await conn.execute("SET default_transaction_read_only = on")
    await conn.execute("SET TIME ZONE 'UTC'")
    return conn


# --------------------------------------------------------------------------- env
def load_env_file(path: Path) -> None:
    """KEY=VALUE lines; the existing environment wins; nothing is printed."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


__all__ = [
    "ARM_EXPERIMENTS",
    "CEILING_DB",
    "CONFIG_PATH",
    "DEFAULT_DSN",
    "EXPERIMENTS",
    "HarnessError",
    "PROMPTS",
    "PROMPT_DIR",
    "READER_TEMPLATE",
    "ROOT",
    "append_jsonl",
    "canonical",
    "ceiling_dsn",
    "connect_ro",
    "dsn_dbname",
    "ensure_private",
    "key_dir",
    "load_config",
    "load_env_file",
    "packets_dir",
    "pdir",
    "private_dir",
    "prompt_files",
    "read_json",
    "read_jsonl",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
    "write_bytes",
    "write_json",
    "write_text",
]
