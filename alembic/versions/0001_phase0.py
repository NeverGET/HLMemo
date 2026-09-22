"""Phase-0 schema (PHASE0-SPEC.md §1, verbatim DDL).

Revision ID: 0001_phase0
Revises: None (base of branch `phase0`)

Nine tables: devices, projects, device_project_grants, events, memory_versions, chunks,
embeddings, links, jobs; extensions vector / pg_trgm / btree_gist; sequence logical_id_seq;
device 1 (`admin`, class `server`, is_admin, placeholder hash) reserved by the bootstrap
INSERT ... OVERRIDING SYSTEM VALUE + setval (D-023, §2).

The SQL below is byte-identical to the spec block and is executed as ONE driver-level
statement batch (`exec_driver_sql`, no bind-parameter parsing) — `op.execute()` would
route the text through SQLAlchemy `text()`, which treats `'reserved:admin'` as a `:admin`
bind parameter.
"""
from __future__ import annotations

from alembic import op

revision = "0001_phase0"
down_revision = None
branch_labels = ("phase0",)
depends_on = None

DDL = r"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE SEQUENCE logical_id_seq AS bigint;

-- 1. devices (D-023). One hashed bearer token per device. device_id 1 is
--    RESERVED for the admin device by this migration (bootstrap insert below);
--    its hash is bound from HLM_ADMIN_TOKEN at API start (§2). Every event
--    therefore has a device_id from the first migration on.
CREATE TABLE devices (
  device_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id               text NOT NULL DEFAULT 'owner',
  name                  text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9-]{1,63}$'),
  class                 text NOT NULL DEFAULT 'other'
                        CHECK (class IN ('personal','work','server','ci','other')),
  fingerprint           text NOT NULL,
  os                    text,
  status                text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','trusted','revoked')),
  is_admin              boolean NOT NULL DEFAULT false,
  token_sha256          text NOT NULL,
  token_generation      integer NOT NULL DEFAULT 1,   -- bumped on revoke/rotation; cursors are bound to it (§2, §3)
  notes                 text,
  registered_at         timestamptz NOT NULL DEFAULT now(),
  approved_at           timestamptz,
  approved_by_device_id bigint REFERENCES devices,
  revoked_at            timestamptz,
  last_seen_at          timestamptz,
  UNIQUE (user_id, name),
  UNIQUE (token_sha256),
  UNIQUE (fingerprint),
  CHECK (NOT is_admin OR device_id = 1)                -- only the reserved device may be admin
);
CREATE INDEX devices_status ON devices (status) WHERE status <> 'revoked';

-- Bootstrap (D-023, §2): reserve device 1 atomically inside migration 0001, before any
-- registration can run. The placeholder hash can never equal sha256(<token>), so the
-- device is unusable until the API binds HLM_ADMIN_TOKEN to it at start-up.
INSERT INTO devices (device_id, user_id, name, class, fingerprint, os, status, is_admin,
                     token_sha256, notes, approved_at, approved_by_device_id)
OVERRIDING SYSTEM VALUE
VALUES (1, 'owner', 'admin', 'server', 'reserved:admin', NULL, 'trusted', true,
        'reserved:admin', 'reserved by migration 0001; hash bound from HLM_ADMIN_TOKEN at API start',
        now(), 1);
SELECT setval(pg_get_serial_sequence('devices', 'device_id'), 1);

-- 2. projects
CREATE TABLE projects (
  project_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug         text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,63}$'),
  name         text NOT NULL,
  card_logical_id bigint NOT NULL UNIQUE DEFAULT nextval('logical_id_seq'),
  policy       jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  archived_at  timestamptz
);

-- 3. device_project_grants (D-023): DEVICE x PROJECT authorization matrix.
CREATE TABLE device_project_grants (
  device_id            bigint NOT NULL REFERENCES devices,
  project_id           bigint NOT NULL REFERENCES projects,
  role                 text NOT NULL CHECK (role IN ('read','write','admin')),
  granted_at           timestamptz NOT NULL DEFAULT now(),
  granted_by_device_id bigint NOT NULL REFERENCES devices,
  revoked_at           timestamptz,
  PRIMARY KEY (device_id, project_id)
);
CREATE INDEX dpg_project ON device_project_grants (project_id) WHERE revoked_at IS NULL;

-- 4. events: authoritative, append-only (D-010). project_id is NULL for
--    device-level events; NULLS NOT DISTINCT keeps request_id unique there too.
--    request_id is unique per (project, device): the same UUID sent by another
--    device is a different request (§3). payload = {"request","resolved"} (§1.1).
CREATE TABLE events (
  event_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id     bigint REFERENCES projects,
  device_id      bigint NOT NULL REFERENCES devices,
  client         text NOT NULL,                 -- e.g. 'claude-code/2.1.278', 'hlm/0.0.1'
  request_id     uuid NOT NULL,
  session_id     uuid,
  kind           text NOT NULL CHECK (kind IN (
                   'write','call_the_day','access','archive','restore','project_created',
                   'device_registered','device_approved','device_revoked',
                   'grant_added','grant_revoked')),
  schema_version smallint NOT NULL DEFAULT 1,
  projection_version smallint NOT NULL DEFAULT 1, -- chunker/normalizer/meter contract that derived the projections (§1.1)
  payload        jsonb NOT NULL,                -- {"request":<verbatim args>,"resolved":<server-resolved defaults+ids>} (§1.1)
  payload_sha256 text NOT NULL,                 -- sha256(canonical JSON of payload.request) — idempotency key
  occurred_at    timestamptz NOT NULL,          -- client claim
  received_at    timestamptz NOT NULL DEFAULT clock_timestamp(),  -- audit only; projections use payload.resolved.recorded_at
  result         jsonb,                         -- stored reply for idempotent replay (re-authorised on replay, §3)
  UNIQUE NULLS NOT DISTINCT (project_id, device_id, request_id)
);
CREATE INDEX events_project_received ON events (project_id, received_at);
CREATE INDEX events_device_received  ON events (device_id, received_at);
CREATE UNIQUE INDEX events_one_close ON events (project_id, session_id) WHERE kind = 'call_the_day';

-- 5. memory_versions: bi-temporal projection, dual-axis scope (D-023). Rows are
--    immutable except for closing superseded_at. A logical_id may have several
--    CURRENT rows as long as their valid intervals do not overlap (§1.1).
CREATE TABLE memory_versions (
  version_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  logical_id     bigint NOT NULL,
  project_id     bigint NOT NULL REFERENCES projects,           -- home project
  project_ids    bigint[] NOT NULL,                             -- home + cross-project targets
  device_scope   text NOT NULL DEFAULT 'all',
  kind           text NOT NULL CHECK (kind IN
                   ('fact','episode','lesson','experience','project_card','session_note','doc_chunk')),
  status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','tombstone')),
  title          text NOT NULL CHECK (length(title) <= 200),
  body           text NOT NULL,
  tags           text[] NOT NULL DEFAULT '{}',
  pinned         boolean NOT NULL DEFAULT false,
  stability      text NOT NULL DEFAULT 'volatile' CHECK (stability IN ('stable','volatile')),
  importance     smallint CHECK (importance BETWEEN 1 AND 10),
  token_count    integer NOT NULL,                              -- o200k_base
  valid_from     timestamptz NOT NULL,
  valid_to       timestamptz NOT NULL DEFAULT 'infinity',
  recorded_at    timestamptz NOT NULL,                          -- = payload.resolved.recorded_at of source_event; never a fresh clock
  superseded_at  timestamptz NOT NULL DEFAULT 'infinity',
  source_event_id bigint NOT NULL REFERENCES events,
  supersedes_version_id bigint REFERENCES memory_versions,      -- the row this one replaces or was split from (§1.1)
  last_access_at timestamptz,                                   -- retention signal only (D-012)
  CHECK (valid_from < valid_to AND recorded_at < superseded_at),
  CHECK (project_id = ANY (project_ids)),
  CHECK (device_scope = 'all'
         OR device_scope ~ '^class:(personal|work|server|ci|other)$'
         OR device_scope ~ '^device:[0-9]+$'),
  -- one version of a logical item per (valid_at, known_at) point; several current,
  -- non-overlapping valid-time segments are allowed (backdated corrections, §1.1).
  EXCLUDE USING gist (
    logical_id WITH =,
    tstzrange(valid_from, valid_to, '[)') WITH &&,
    tstzrange(recorded_at, superseded_at, '[)') WITH &&),
  -- one project card per project per (valid_at, known_at) point (replaces the old
  -- mv_one_card unique index, which forbade valid-time segments of the card).
  EXCLUDE USING gist (
    project_id WITH =,
    tstzrange(valid_from, valid_to, '[)') WITH &&,
    tstzrange(recorded_at, superseded_at, '[)') WITH &&) WHERE (kind = 'project_card')
);
CREATE INDEX mv_logical_current  ON memory_versions (logical_id, version_id) WHERE superseded_at = 'infinity';  -- head() lookup, §1.1
CREATE INDEX mv_project_kind     ON memory_versions (project_id, kind, status) WHERE superseded_at = 'infinity';
CREATE INDEX mv_project_ids_gin  ON memory_versions USING gin (project_ids);
CREATE INDEX mv_temporal         ON memory_versions (valid_from, valid_to, recorded_at, superseded_at);

-- 6. chunks: immutable spans of exactly one version — never rewritten; a new version
--    gets its own chunk rows. project_ids/device_scope are denormalised copies used
--    ONLY as a candidate prefilter; memory_versions is the authoritative scope (§4).
CREATE TABLE chunks (
  chunk_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  version_id   bigint NOT NULL REFERENCES memory_versions ON DELETE RESTRICT,
  project_ids  bigint[] NOT NULL,
  device_scope text NOT NULL,
  ordinal      smallint NOT NULL CHECK (ordinal >= 0),
  char_start   integer NOT NULL CHECK (char_start >= 0),
  char_end     integer NOT NULL,
  text         text NOT NULL,
  text_norm    text NOT NULL,                                   -- app-side normalize() (§4.2)
  e5_tokens    smallint NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', text_norm)) STORED,
  UNIQUE (version_id, ordinal),
  CHECK (char_end > char_start)
);
CREATE INDEX chunks_tsv_gin     ON chunks USING gin (tsv);
CREATE INDEX chunks_trgm_gin    ON chunks USING gin (text_norm gin_trgm_ops);
CREATE INDEX chunks_project_gin ON chunks USING gin (project_ids);
CREATE INDEX chunks_version     ON chunks (version_id, ordinal);

-- 7. embeddings: dimension-free storage (D-017: model is config); Phase 0 = exact scan (D-008).
CREATE TABLE embeddings (
  chunk_id        bigint NOT NULL REFERENCES chunks,
  model           text NOT NULL,                                -- 'intfloat/multilingual-e5-small'
  model_revision  text NOT NULL,                                -- pinned HF commit (models.lock)
  preproc_version smallint NOT NULL DEFAULT 1,
  dims            smallint NOT NULL,
  vec             vector NOT NULL,                              -- L2-normalised
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, model, model_revision, preproc_version)
);
CREATE INDEX emb_model ON embeddings (model, model_revision, preproc_version);

-- 8. links: bi-temporal edges (D-006/D-007), dual-axis scoped (D-023). Edges are
--    immutable rows: a change = supersede (close superseded_at) + insert, never UPDATE.
--    dst_version_id pins the immutable target version (D-015 card provenance, §1.1).
CREATE TABLE links (
  link_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id     bigint NOT NULL REFERENCES projects,
  project_ids    bigint[] NOT NULL,
  device_scope   text NOT NULL DEFAULT 'all',
  src_logical_id bigint NOT NULL,
  dst_logical_id bigint NOT NULL,
  dst_version_id bigint NULL REFERENCES memory_versions,        -- immutable target version; required for derived_from
  rel            text NOT NULL CHECK (rel IN
                   ('relates_to','contradicts','supersedes','derived_from','depends_on')),
  props          jsonb NOT NULL DEFAULT '{}',
  valid_from     timestamptz NOT NULL,
  valid_to       timestamptz NOT NULL DEFAULT 'infinity',
  recorded_at    timestamptz NOT NULL,                          -- = payload.resolved.recorded_at of source_event
  superseded_at  timestamptz NOT NULL DEFAULT 'infinity',
  source_event_id bigint NOT NULL REFERENCES events,
  supersedes_link_id bigint REFERENCES links,
  CHECK (valid_from < valid_to AND recorded_at < superseded_at),
  CHECK (project_id = ANY (project_ids)),
  CHECK (rel <> 'derived_from' OR dst_version_id IS NOT NULL),
  CHECK (device_scope = 'all'
         OR device_scope ~ '^class:(personal|work|server|ci|other)$'
         OR device_scope ~ '^device:[0-9]+$'),
  -- one edge (src, dst, rel) per (valid_at, known_at) point; temporal segments allowed
  -- (replaces the old links_current unique index).
  EXCLUDE USING gist (
    src_logical_id WITH =,
    dst_logical_id WITH =,
    rel WITH =,
    tstzrange(valid_from, valid_to, '[)') WITH &&,
    tstzrange(recorded_at, superseded_at, '[)') WITH &&)
);
CREATE INDEX links_src            ON links (src_logical_id) WHERE superseded_at = 'infinity';
CREATE INDEX links_dst            ON links (dst_logical_id) WHERE superseded_at = 'infinity';
CREATE INDEX links_dst_version    ON links (dst_version_id) WHERE superseded_at = 'infinity';
CREATE INDEX links_project_gin    ON links USING gin (project_ids);

-- 9. jobs: transactional outbox with leases.
CREATE TABLE jobs (
  job_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind        text NOT NULL CHECK (kind IN ('embed','reembed','archive_cycle')),
  dedupe_key  text NOT NULL UNIQUE,                            -- 'embed:<version_id>:<model>@<rev>'
  source_event_id bigint REFERENCES events,
  payload     jsonb NOT NULL,
  status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  attempts    smallint NOT NULL DEFAULT 0,
  run_after   timestamptz NOT NULL DEFAULT now(),
  lease_token uuid,
  lease_until timestamptz,
  last_error  text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  done_at     timestamptz
);
CREATE INDEX jobs_ready   ON jobs (run_after)   WHERE status = 'queued';
CREATE INDEX jobs_expired ON jobs (lease_until) WHERE status = 'running';
"""

DROP = """
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS links;
DROP TABLE IF EXISTS embeddings;
DROP TABLE IF EXISTS chunks;
DROP TABLE IF EXISTS memory_versions;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS device_project_grants;
DROP TABLE IF EXISTS projects;
DROP TABLE IF EXISTS devices;
DROP SEQUENCE IF EXISTS logical_id_seq;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(DDL)


def downgrade() -> None:
    # Extensions are left installed (shared, cheap, harmless).
    op.get_bind().exec_driver_sql(DROP)
