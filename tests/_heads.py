"""The ``main@head`` alembic revision, for tests that assert the migrated head (it moves with every
new revision on the chain: 0006 → 0007_import → 0008_librarian_tasks …)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@cache
def main_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    (rev,) = script.get_revisions("main@head")
    return rev.revision
