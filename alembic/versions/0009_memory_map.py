"""Memory Map L2 summary cache (D-136: the research librarian's "summarising" layer, report §D L2).

Revision ID: 0009_memory_map
Revises: 0008_librarian_tasks

Adds ONE table, never rewrites (CC-1): ``memory_map_summaries`` caches a short LLM summary (1–3
sentences) per source file or topic cluster of a project, keyed by ``(project_id, source_key)`` and
stamped with the digest of the exact version ids it was written from (``member_ids``).

* It is a rebuildable CACHE, not a projection of events: nothing in ``events`` records it, replay
  (``db/replay.py``) neither truncates nor rebuilds it, and deleting every row only makes the
  librarian regenerate the summaries (the Memory Map works without them).
* Only the librarian process writes it (``librarian/tasks/map_summary.py``); ``memory.ask`` only
  reads it, and shows a summary to a caller only when every member version is in that caller's
  view (``core/memory_map.py``).
"""

from __future__ import annotations

from alembic import op

revision = "0009_memory_map"
down_revision = "0008_librarian_tasks"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "3s"

UPGRADE = r"""
CREATE TABLE IF NOT EXISTS memory_map_summaries (
  project_id     bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
  source_key     text NOT NULL CHECK (length(source_key) BETWEEN 1 AND 600),
  digest         text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
  member_ids     bigint[] NOT NULL,
  summary        text CHECK (summary IS NULL OR length(summary) <= 800),
  status         text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','failed')),
  failures       smallint NOT NULL DEFAULT 0 CHECK (failures >= 0),
  profile        text,
  prompt_version text,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, source_key)
);
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS memory_map_summaries;
"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    bind.exec_driver_sql(UPGRADE)


def downgrade() -> None:
    # A cache: dropping it loses nothing that events or replay would need.
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    bind.exec_driver_sql(DOWNGRADE)
