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

-- Small partial indexes for the librarian's event lookups (role decisions, proposal batches).
CREATE INDEX events_librarian_role ON events (project_id, event_id)
  WHERE kind = 'librarian' AND (payload->'request'->>'op') = 'set_role';
CREATE INDEX events_librarian_batch ON events ((payload->'resolved'->>'batch_id'))
  WHERE kind = 'librarian';
CREATE INDEX events_answer_batch ON events ((payload->'request'->>'batch_id'))
  WHERE kind = 'answer';

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

CREATE TABLE llm_calls (
  call_id             uuid PRIMARY KEY,
  job_id              bigint,                 -- no FK: jobs is a projection truncated by replay
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
CREATE INDEX llm_calls_created ON llm_calls (created_at);

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
DROP INDEX IF EXISTS events_answer_batch;
DROP INDEX IF EXISTS events_librarian_batch;
DROP INDEX IF EXISTS events_librarian_role;
ALTER TABLE events DROP CONSTRAINT events_kind_check;
ALTER TABLE events ADD CONSTRAINT events_kind_check CHECK (kind IN (
  'write','call_the_day','access','archive','restore','project_created',
  'device_registered','device_approved','device_revoked','grant_added','grant_revoked'));
DROP INDEX IF EXISTS jobs_ready_prio;
ALTER TABLE jobs DROP COLUMN priority;
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN ('embed','reembed','archive_cycle'));
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(UPGRADE)


def downgrade() -> None:
    # Refuses (CHECK violation) if librarian-era job/event kinds exist: a downgrade never drops
    # authoritative events. Reserved rows are removed only while nothing references them.
    op.get_bind().exec_driver_sql(DOWNGRADE)
