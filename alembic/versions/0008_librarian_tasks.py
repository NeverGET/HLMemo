"""Librarian tasks (PHASE2-4-ROADMAP W2b + W2c; D-062, D-067, D-069).

Revision ID: 0008_librarian_tasks
Revises: 0007_import (D-069: the chain is 0006_librarian → 0007_import → 0008_librarian_tasks)

Adds, never rewrites (CC-1):
  * ``version_signals`` — the W2b placement projection (``version_id`` PK). The librarian writes a
    row per reviewed version: its suggestion for every field the CLIENT LEFT UNSET (importance,
    stability), a topic hint and up to 5 suggested tags. A client-set importance is copied with
    ``importance_src = 'client'`` and is never replaced. Content is not re-versioned, so
    ``expected_version_id`` stays stable. ``usage_count``/``last_scored_at``/``score`` are the W3b
    scoring columns (unused until then). A projection of ``librarian`` events (replay rebuilds it);
  * ``librarian_batches`` — owner-approval batches (§4b): ONE ``open`` batch per project collects
    up to 25 open proposals, then becomes ``ready`` (full) and the next one opens; an approval
    decision closes it (``decided``); the ``apply_batch`` job marks it ``applied``. A projection
    of ``librarian``/``answer`` events;
  * ``librarian_questions`` (D-062 shape) gains the W2c lifecycle: status ``answered`` (custom
    answer, re-plan queued), ``applied`` (accepted and applied), ``expired`` (30 days),
    ``authority_lost`` (apply-time recheck failed); the ``answer`` jsonb (decision, note, by) and
    ``expires_at`` (created_at + 30 days, set by the writer).
"""

from __future__ import annotations

from alembic import op

revision = "0008_librarian_tasks"
down_revision = "0007_import"  # D-069
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "3s"

UPGRADE = r"""
CREATE TABLE IF NOT EXISTS version_signals (
  version_id          bigint PRIMARY KEY REFERENCES memory_versions,
  importance          smallint CHECK (importance BETWEEN 1 AND 10),
  importance_src      text CHECK (importance_src IN ('client','librarian')),
  stability_suggested text CHECK (stability_suggested IN ('stable','volatile')),
  topic_hint          text CHECK (length(topic_hint) <= 80),
  topic_logical_id    bigint,
  tags_add            text[] NOT NULL DEFAULT '{}',
  usage_count         integer NOT NULL DEFAULT 0 CHECK (usage_count >= 0),
  last_scored_at      timestamptz,
  score               real,
  recorded_at         timestamptz NOT NULL,
  source_event_id     bigint NOT NULL REFERENCES events
);

CREATE TABLE IF NOT EXISTS librarian_batches (
  batch_id        uuid PRIMARY KEY,
  project_id      bigint NOT NULL REFERENCES projects,
  status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open','ready','decided','applied')),
  created_at      timestamptz NOT NULL,
  source_event_id bigint NOT NULL REFERENCES events,
  decided_at      timestamptz,
  decided_by      bigint REFERENCES devices,
  applied_at      timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS librarian_batches_one_open ON librarian_batches (project_id)
  WHERE status = 'open';

ALTER TABLE librarian_questions DROP CONSTRAINT IF EXISTS librarian_questions_status_check;
ALTER TABLE librarian_questions ADD CONSTRAINT librarian_questions_status_check CHECK (status IN (
  'open','approved','rejected','superseded','answered','applied','expired','authority_lost'));
ALTER TABLE librarian_questions ADD COLUMN IF NOT EXISTS answer jsonb;
ALTER TABLE librarian_questions ADD COLUMN IF NOT EXISTS expires_at timestamptz;
CREATE INDEX IF NOT EXISTS librarian_questions_expiry ON librarian_questions (expires_at)
  WHERE status = 'open';
"""

DOWNGRADE = r"""
DROP INDEX IF EXISTS librarian_questions_expiry;
ALTER TABLE librarian_questions DROP COLUMN IF EXISTS expires_at;
ALTER TABLE librarian_questions DROP COLUMN IF EXISTS answer;
ALTER TABLE librarian_questions DROP CONSTRAINT IF EXISTS librarian_questions_status_check;
ALTER TABLE librarian_questions ADD CONSTRAINT librarian_questions_status_check CHECK (status IN (
  'open','approved','rejected','superseded'));
DROP TABLE IF EXISTS librarian_batches;
DROP TABLE IF EXISTS version_signals;
"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    bind.exec_driver_sql(UPGRADE)


def downgrade() -> None:
    # Refuses (CHECK violation) while questions carry a W2c status: those rows are projections of
    # authoritative events, and replaying the events needs this schema.
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    bind.exec_driver_sql(DOWNGRADE)
