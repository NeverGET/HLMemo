"""Librarian foundation (PHASE2-4-ROADMAP W2a, CC-1/CC-2/CC-3/CC-5; D-062 proposed).

Revision ID: 0006_librarian
Revises: 0004_title_norm_fold (TEMPORARY, see ``down_revision``; branch `phase0`)

Adds, never rewrites (CC-1):
  * ``jobs.kind`` += librarian_write, topic_summary, consolidate, ingest_extract, import_postprocess,
    pack_review, experience_review; ``jobs.priority smallint DEFAULT 5`` and the ready index the
    shared lease query orders by ``(priority, run_after, job_id)``;
  * ``events.kind`` += librarian, question, answer, import, ingest, consolidation, pack_import,
    device_minted (CC-2);
  * ``devices.is_system`` and the reserved system device ``librarian`` (class ``server``, trusted,
    ``token_sha256 = 'reserved:librarian'``: no bearer can ever hash to it, CC-3; not admin);
  * ``llm_calls`` (ledger, no content), ``llm_budget`` (hour/day/month windows) and
    ``llm_reservations`` (atomic worst-case reservations, swept as spent after ``expires_at``);
  * ``librarian_questions`` (CC-3 ``question(P)``: proposals as rows, a projection of events);
  * the reserved projects ``hlm-librarian`` (librarian working memory) and ``hlm-global``
    (cross-project experience). Neither gets any grant here: ``hlm-global`` grants are issued
    explicitly by ops (D-058); the librarian writes ``hlm-librarian`` through its capability only.

Executed as one driver-level batch (``exec_driver_sql``) like 0001: ``'reserved:librarian'`` would
otherwise be parsed as a ``:librarian`` bind parameter.
"""

from __future__ import annotations

from alembic import op

revision = "0006_librarian"
# ORCHESTRATOR: relink to 0005_w0_access at merge
down_revision = "0004_title_norm_fold"
branch_labels = None
depends_on = None

UPGRADE = r"""
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN (
  'embed','reembed','archive_cycle',
  'librarian_write','topic_summary','consolidate','ingest_extract','import_postprocess',
  'pack_review','experience_review'));
ALTER TABLE jobs ADD COLUMN priority smallint NOT NULL DEFAULT 5;
CREATE INDEX jobs_ready_prio ON jobs (priority, run_after, job_id) WHERE status = 'queued';

ALTER TABLE events DROP CONSTRAINT events_kind_check;
ALTER TABLE events ADD CONSTRAINT events_kind_check CHECK (kind IN (
  'write','call_the_day','access','archive','restore','project_created',
  'device_registered','device_approved','device_revoked','grant_added','grant_revoked',
  'librarian','question','answer','import','ingest','consolidation','pack_import','device_minted'));

ALTER TABLE devices ADD COLUMN is_system boolean NOT NULL DEFAULT false;
INSERT INTO devices (user_id, name, class, fingerprint, os, status, is_admin, is_system,
                     token_sha256, notes, approved_at, approved_by_device_id)
VALUES ('owner', 'librarian', 'server', 'reserved:librarian', NULL, 'trusted', false, true,
        'reserved:librarian', 'reserved by migration 0006: librarian system actor, no usable bearer (CC-3)',
        now(), 1)
ON CONFLICT DO NOTHING;

INSERT INTO projects (slug, name, policy) VALUES
  ('hlm-librarian', 'Librarian working memory (reserved)', '{"reserved": true, "librarian": "off"}'),
  ('hlm-global', 'Global experience (reserved)', '{"reserved": true, "librarian": "off"}')
ON CONFLICT (slug) DO NOTHING;

-- CC-3 question(P): librarian proposals as rows (a projection of `librarian`/`answer` events;
-- replay rebuilds it). `proposal` is redacted JSON; decisions come from `answer` events.
CREATE TABLE librarian_questions (
  question_id         uuid PRIMARY KEY,
  job_key             text NOT NULL,          -- the proposing job's dedupe key (stable across replay)
  batch_id            uuid NOT NULL,
  project_id          bigint NOT NULL REFERENCES projects,
  project_ids         bigint[] NOT NULL,      -- every project the proposal touches
  kind                text NOT NULL CHECK (kind IN ('contradiction','widen_scope','merge','promote',
                                                    'card_refresh','quarantine','pack_accept','link')),
  subject_clues       text[] NOT NULL,
  subject_version_ids bigint[] NOT NULL,      -- the versions the model assessed (stale check at apply)
  proposal            jsonb NOT NULL,
  status              text NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open','approved','rejected','superseded')),
  created_at          timestamptz NOT NULL,
  decided_at          timestamptz,
  decided_by          bigint REFERENCES devices,
  source_event_id     bigint NOT NULL REFERENCES events
);
CREATE INDEX librarian_questions_batch ON librarian_questions (batch_id, status);
CREATE INDEX librarian_questions_open ON librarian_questions (project_id, created_at) WHERE status = 'open';

CREATE TABLE llm_calls (
  call_id             uuid PRIMARY KEY,
  job_id              bigint,                 -- no FK: jobs is a projection truncated by replay
  lineage             uuid,                   -- loop lineage: re-enqueued jobs inherit it (call ceiling)
  task                text NOT NULL,
  profile             text NOT NULL,
  model_id            text NOT NULL,
  prompt_version      text NOT NULL,
  schema_version      text NOT NULL,
  mode                text NOT NULL CHECK (mode IN ('live','record','replay')),
  request_sha256      text,
  response_sha256     text,
  input_tokens        integer,
  cached_input_tokens integer,
  output_tokens       integer,
  reserved_usd        numeric(14,8) NOT NULL DEFAULT 0,
  cost_usd            numeric(14,8) NOT NULL DEFAULT 0,
  latency_ms          integer,
  outcome             text NOT NULL CHECK (outcome IN ('ok','schema_retry_ok','schema_fail','http_error',
                                                       'timeout','budget_deferred','breaker_open')),
  created_at          timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX llm_calls_job ON llm_calls (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX llm_calls_lineage ON llm_calls (lineage) WHERE lineage IS NOT NULL;
CREATE INDEX llm_calls_created ON llm_calls (created_at);

-- Loop-lineage call ceiling (Sol 37 #7): one counter row per lineage, claimed atomically
-- (INSERT … ON CONFLICT DO UPDATE … WHERE calls < cap) before every network attempt.
CREATE TABLE llm_lineage_calls (
  lineage    uuid PRIMARY KEY,
  calls      integer NOT NULL CHECK (calls >= 0),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE llm_budget (
  period_kind  text NOT NULL CHECK (period_kind IN ('hour','day','month')),
  period_start timestamptz NOT NULL,
  cap_usd      numeric(14,8) NOT NULL CHECK (cap_usd >= 0),
  reserved_usd numeric(14,8) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
  spent_usd    numeric(14,8) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
  PRIMARY KEY (period_kind, period_start)
);

CREATE TABLE llm_reservations (
  call_id     uuid PRIMARY KEY,
  job_id      bigint,
  hour_start  timestamptz NOT NULL,
  day_start   timestamptz NOT NULL,
  month_start timestamptz NOT NULL,
  worst_usd   numeric(14,8) NOT NULL CHECK (worst_usd >= 0),
  created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  expires_at  timestamptz NOT NULL
);
CREATE INDEX llm_reservations_expiry ON llm_reservations (expires_at);
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS librarian_questions;
DROP TABLE IF EXISTS llm_lineage_calls;
DROP TABLE IF EXISTS llm_reservations;
DROP TABLE IF EXISTS llm_budget;
DROP TABLE IF EXISTS llm_calls;
DELETE FROM projects p WHERE p.slug IN ('hlm-librarian', 'hlm-global')
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.project_id = p.project_id)
  AND NOT EXISTS (SELECT 1 FROM memory_versions v WHERE v.project_id = p.project_id)
  AND NOT EXISTS (SELECT 1 FROM device_project_grants g WHERE g.project_id = p.project_id);
DELETE FROM devices d WHERE d.is_system AND d.name = 'librarian'
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.device_id = d.device_id);
ALTER TABLE devices DROP COLUMN is_system;
ALTER TABLE events DROP CONSTRAINT events_kind_check;
ALTER TABLE events ADD CONSTRAINT events_kind_check CHECK (kind IN (
  'write','call_the_day','access','archive','restore','project_created',
  'device_registered','device_approved','device_revoked','grant_added','grant_revoked'));
DROP INDEX IF EXISTS jobs_ready_prio;
ALTER TABLE jobs DROP COLUMN priority;
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN ('embed','reembed','archive_cycle'));
"""


# The role-decision lookup runs per librarian job. Measured on a 100k-event clone (2026-09-23):
# 17 ms parallel seq scan per project lookup without it (linear in events), 0.01 ms with it;
# the build took 19 ms. Built CONCURRENTLY (no write lock on events), like 0004's reindex.
ROLE_INDEX = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS events_librarian_role ON events (project_id, event_id)"
    " WHERE kind = 'librarian' AND (payload->'request'->>'op') = 'set_role'"
)
LEFTOVER = """
SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'events_librarian_role' AND NOT i.indisvalid
"""


# D-063/D-064: the chunk GIN indexes (trigram + lexical) and the title GIN index stop using the
# pending list: a write burst otherwise leaves unmerged entries that every candidate query scans
# linearly (measured 6.5 s statements, query p95 6.7 s during 100 writes).
#
# Lock discipline (neutral verifier, W2a): ALTER INDEX … SET (fastupdate) waits for an exclusive
# lock on the INDEX behind any running reader. It therefore runs FIRST, in its own autocommit
# statements, never inside the table-DDL transaction (which would hold AccessExclusive on events/
# jobs/devices while queued, stalling all traffic). Every step has a short lock_timeout: a busy
# database makes the migration FAIL FAST with nothing half-applied (a reloption is idempotent),
# and simply re-running `alembic upgrade` retries. The existing pending list is flushed last,
# outside every DDL transaction (gin_clean_pending_list).
GIN_NO_FASTUPDATE = ("chunks_trgm_gin", "chunks_tsv_gin", "mv_title_tsv")
LOCK_TIMEOUT = "3s"
RETRY_HINT = (
    "0006_librarian: lock_timeout ({t}) waiting for {what}; nothing was left half-applied. "
    "Retry `alembic upgrade` at a quieter moment (deploy stops writers first)."
)


def _set_fastupdate(enabled: bool) -> None:
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        bind.exec_driver_sql(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            for name in GIN_NO_FASTUPDATE:
                verb = "RESET (fastupdate)" if enabled else "SET (fastupdate = off)"
                try:
                    bind.exec_driver_sql(f"ALTER INDEX {name} {verb}")
                except Exception as exc:
                    raise RuntimeError(RETRY_HINT.format(t=LOCK_TIMEOUT, what=f"index {name}")) from exc
        finally:
            bind.exec_driver_sql("RESET lock_timeout")


def _flush_pending_lists() -> None:
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        for name in GIN_NO_FASTUPDATE:
            bind.exec_driver_sql(f"SELECT gin_clean_pending_list('{name}'::regclass)")


def upgrade() -> None:
    _set_fastupdate(enabled=False)  # D-063/D-064: first, alone, fail-fast
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")  # the table DDL, one tx
    try:
        bind.exec_driver_sql(UPGRADE)
    except Exception as exc:
        raise RuntimeError(RETRY_HINT.format(t=LOCK_TIMEOUT, what="the table DDL")) from exc
    _flush_pending_lists()  # commits the DDL first; no DDL transaction open during the flush
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        if bind.exec_driver_sql(LEFTOVER).fetchall():  # an interrupted concurrent build
            bind.exec_driver_sql("DROP INDEX CONCURRENTLY IF EXISTS events_librarian_role")
        bind.exec_driver_sql(ROLE_INDEX)


def downgrade() -> None:
    # Refuses (CHECK violation) if librarian-era job/event kinds exist: a downgrade never drops
    # authoritative events. Reserved rows are removed only while nothing references them.
    with op.get_context().autocommit_block():
        op.get_bind().exec_driver_sql("DROP INDEX CONCURRENTLY IF EXISTS events_librarian_role")
    _set_fastupdate(enabled=True)  # D-063, symmetric
    op.get_bind().exec_driver_sql(DOWNGRADE)
