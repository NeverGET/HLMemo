<!-- Source: Claude Plan agent, 2026-09-22, written independently of codex round 3 -->
# HLMemo Phase-0 Implementation Spec (2026-09-22)

Honors D-001..D-019. Naming: valid time = `valid_from/valid_to`; system time = `recorded_at/superseded_at`. All ids are `bigint` (clue token cost), except client-supplied `request_id` (text).

## 1. Postgres DDL (Alembic `0001_phase0`)

```sql
CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE projects (
  project_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug         text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,63}$'),
  name         text NOT NULL,
  token_sha256 text NOT NULL UNIQUE,          -- one bearer token per project (ASSUMPTION: admin token in env HLM_ADMIN_TOKEN)
  policy       jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  archived_at  timestamptz);

CREATE TABLE events (                          -- authoritative, append-only (D-010)
  event_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id     bigint NOT NULL REFERENCES projects,
  request_id     text   NOT NULL,
  kind           text   NOT NULL CHECK (kind IN ('write','call_the_day','access','archive','restore','project_created')),
  schema_version smallint NOT NULL DEFAULT 1,
  payload        jsonb  NOT NULL,
  payload_sha256 text   NOT NULL,
  actor          text   NOT NULL,              -- e.g. "claude-code/2.1.4"
  occurred_at    timestamptz NOT NULL,         -- client claim
  received_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
  result         jsonb,                        -- stored reply for idempotent replay
  UNIQUE (project_id, request_id));
CREATE INDEX events_project_received ON events (project_id, received_at);

CREATE TABLE memory_versions (                 -- bi-temporal projection
  version_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  logical_id     bigint NOT NULL,              -- from sequence logical_id_seq on first version
  project_id     bigint NOT NULL REFERENCES projects,
  kind           text NOT NULL CHECK (kind IN ('fact','episode','lesson','experience','project_card','session_note','doc_chunk')),
  status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','tombstone')),
  title          text NOT NULL, body text NOT NULL, tags text[] NOT NULL DEFAULT '{}',
  pinned         boolean NOT NULL DEFAULT false,
  stability      text NOT NULL DEFAULT 'volatile' CHECK (stability IN ('stable','volatile')),
  importance     smallint CHECK (importance BETWEEN 1 AND 10),
  token_count    integer NOT NULL,             -- o200k_base
  valid_from     timestamptz NOT NULL, valid_to timestamptz CHECK (valid_to IS NULL OR valid_to > valid_from),
  recorded_at    timestamptz NOT NULL DEFAULT clock_timestamp(), superseded_at timestamptz,
  source_event_id bigint NOT NULL REFERENCES events,
  supersedes_version_id bigint REFERENCES memory_versions,
  last_access_at timestamptz);                 -- retention signal only (D-012, Phase 3)
CREATE UNIQUE INDEX mv_current ON memory_versions (logical_id) WHERE superseded_at IS NULL;  -- concurrency
CREATE UNIQUE INDEX mv_one_card ON memory_versions (project_id) WHERE kind='project_card' AND superseded_at IS NULL;
CREATE INDEX mv_project_kind ON memory_versions (project_id, kind, status) WHERE superseded_at IS NULL;
CREATE INDEX mv_temporal ON memory_versions (project_id, valid_from, valid_to, recorded_at, superseded_at);

CREATE TABLE chunks (                          -- immutable spans
  chunk_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  version_id  bigint NOT NULL REFERENCES memory_versions ON DELETE RESTRICT,
  project_id  bigint NOT NULL REFERENCES projects,           -- denormalized for scoping
  ordinal     smallint NOT NULL,
  char_start  integer NOT NULL, char_end integer NOT NULL,
  text        text NOT NULL, text_norm text NOT NULL,         -- app-side normalize() (see 3.1)
  e5_tokens   smallint NOT NULL,
  tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple', text_norm)) STORED,
  UNIQUE (version_id, ordinal));
CREATE INDEX chunks_tsv_gin  ON chunks USING gin (tsv);
CREATE INDEX chunks_trgm_gin ON chunks USING gin (text_norm gin_trgm_ops);
CREATE INDEX chunks_project  ON chunks (project_id, version_id);

CREATE TABLE embeddings (
  chunk_id       bigint NOT NULL REFERENCES chunks,
  project_id     bigint NOT NULL REFERENCES projects,
  model          text NOT NULL,                -- 'intfloat/multilingual-e5-small'
  model_revision text NOT NULL,                -- pinned HF commit
  preproc_version smallint NOT NULL DEFAULT 1,
  vec            vector(384) NOT NULL,         -- L2-normalized
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, model, model_revision));
CREATE INDEX emb_project ON embeddings (project_id, model, model_revision);
-- Phase 0 = EXACT scan (D-008): 10k x 384-d < 10 ms. No ANN index. Prepared migration 0002_hnsw
-- (apply at >200k rows): USING hnsw (vec vector_cosine_ops) WITH (m=16, ef_construction=128); SET hnsw.ef_search=100.

CREATE TABLE links (                           -- bi-temporal edges (D-006/7)
  link_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id bigint NOT NULL REFERENCES projects,
  src_logical_id bigint NOT NULL, dst_logical_id bigint NOT NULL,
  rel text NOT NULL CHECK (rel IN ('relates_to','contradicts','supersedes','derived_from','depends_on','applies_to_project')),
  props jsonb NOT NULL DEFAULT '{}',
  valid_from timestamptz NOT NULL, valid_to timestamptz,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), superseded_at timestamptz,
  source_event_id bigint NOT NULL REFERENCES events);
CREATE UNIQUE INDEX links_current ON links (project_id, src_logical_id, dst_logical_id, rel) WHERE superseded_at IS NULL;
CREATE INDEX links_dst ON links (project_id, dst_logical_id) WHERE superseded_at IS NULL;

CREATE TABLE jobs (                            -- transactional outbox
  job_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('embed','reembed','archive_cycle')),
  dedupe_key text NOT NULL UNIQUE,             -- 'embed:<version_id>:<model>@<rev>'
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  attempts smallint NOT NULL DEFAULT 0, run_after timestamptz NOT NULL DEFAULT now(),
  locked_by text, locked_at timestamptz, last_error text,
  created_at timestamptz NOT NULL DEFAULT now(), done_at timestamptz);
CREATE INDEX jobs_pick ON jobs (run_after) WHERE status='queued';
```

Lexical choice: `tsvector('simple')` GIN over app-normalized `text_norm`, with prefix-matching tsqueries, plus a pg_trgm GIN as secondary for identifier terms. Justification: no per-row stemmer covers TR/DE/EN without language detection; Turkish (suffixal) and German (inflection/compounds) are recovered by `term:*` prefix matching after deterministic casefold/diacritic folding; trigram catches snake_case/paths/env keys and compound substrings. Both GIN; ranking stays SQL-deterministic.

## 2. Tool contracts (MCP streamable HTTP, Bearer per project)

Common: `project` (string slug, required) must match the token's project, else `E_FORBIDDEN_PROJECT` (unknown project also 403, no enumeration). Budget meter: `tiktoken.get_encoding("o200k_base")` over `json.dumps(resp, ensure_ascii=False, separators=(",",":"), sort_keys=True)`. Rule: `256 ≤ token_budget ≤ 32000`; `<256` → `E_BUDGET_TOO_SMALL{min:256}`; `>32000` → `E_BUDGET_TOO_LARGE`. Every response carries `budget:{limit,used,tokenizer}` and is guaranteed `used ≤ limit`. Errors are MCP tool errors with JSON `{code,message,details}`. Codes: `E_AUTH`(401), `E_FORBIDDEN_PROJECT`, `E_INVALID_ARG`, `E_NOT_FOUND`, `E_BUDGET_TOO_SMALL/LARGE`, `E_REQUEST_ID_CONFLICT`, `E_VERSION_CONFLICT{current_version_id}`, `E_TEMPORAL`, `E_CARD_TOO_LARGE`, `E_UNAVAILABLE`.

Clue = `v<version_id>` (whole item) or `v<version_id>.<ordinal>` (chunk).

**memory.query** in: `{project, query:string(1..2000), token_budget:int, valid_at?:iso, known_at?:iso, include_archived?:bool=false, kinds?:[kind]}`. out: `{project, as_of:{valid_at,known_at}, card:{clue,text,stale,truncated}|null, hits:[{clue,kind,title,preview,score,valid_from,tags}], omitted:int, evidence:"matched"|"none", budget}`. `evidence:"none"` is the only negative wording (D-014).

**memory.drilldown** in: `{project, clue_ids:[string](1..20), token_budget, cursor?:string, valid_at?, known_at?}`. out: `{items:[{clue,kind,title,text,ordinal_range:[a,b],links:[{rel,clue}]}], next_cursor:string|null, budget}`. Chunk clue returns chunk ±1 neighbour; item clue returns body in order. Unknown/foreign clue → `E_NOT_FOUND`. Writes async `access` event (resets idleness, D-012).

**memory.raw** in: `{project, version_id:int, token_budget, cursor?}`. out: `{version_id, logical_id, kind, source_event:{event_id,request_id,actor,occurred_at}, payload_item:object, chunks:[{ordinal,char_start,char_end,text}], next_cursor, budget}`. Access event as above.

**memory.write** in: `{project, request_id:string(uuid), occurred_at?:iso, actor:string, items:[{kind, logical_id?:int, expected_version_id?:int, title:string(≤200), body:string(≤64000), tags?, pinned?, stability?, valid_from?:iso, valid_to?:iso, links?:[{rel, target_logical_id:int|"$<item_index>"}]}](1..50)}`. out: `{request_id, replayed:bool, versions:[{index,logical_id,version_id,chunk_count,embedding_status:"queued"|"done"}]}`. Rules: same `request_id`+same `payload_sha256` → stored `result`, `replayed:true`; same id, different hash → `E_REQUEST_ID_CONFLICT`. `logical_id` given → must supply `expected_version_id`; mismatch with current → `E_VERSION_CONFLICT`. `project_card` body > 512 tokens → `E_CARD_TOO_LARGE`. Whole batch is one transaction: event + versions + chunks + links + embed jobs. Lexically visible on commit; vector after worker.

**memory.call_the_day** in: `{project, request_id, session_id:string, actor, notes:string, decisions?:[string], lessons?:[{title,body,tags?}], card_update?:{body, expected_version_id}, expected_versions?:[{logical_id,version_id}]}`. out: same as write plus `session_note_clue`. Implementation: maps to one write batch (`session_note` + `lesson`s + optional card) via the same service; no LLM.

## 3. memory.query fast path

1. Auth → `project_id`; validate; `valid_at,known_at` default `now()` (server clock).
2. `normalize(s) = drop_combining_marks(NFD(NFKC(s).casefold())).replace('ı','i')` (deterministic, identical at index/query time; ß→ss, İ/ı→i, ü→u, ş→s).
3. Terms: regex `[\w][\w./-]*` on normalized query, drop len<2, max 24 terms. Identifier terms = contain `_ / .` or a digit.
4. Temporal predicate (every candidate SQL joins `memory_versions mv`): `mv.valid_from <= :valid_at AND (mv.valid_to IS NULL OR mv.valid_to > :valid_at) AND mv.recorded_at <= :known_at AND (mv.superseded_at IS NULL OR mv.superseded_at > :known_at) AND mv.project_id = :pid AND mv.status = 'active'` (`IN ('active','archived')` if include_archived) `AND mv.kind <> 'project_card'`.
5. Lexical list L (≤50): `tsquery = OR of "term:*"`, `ORDER BY ts_rank_cd(tsv, q, 32) DESC, chunk_id ASC`.
6. Trigram list T (≤20, only if identifier terms): `WHERE text_norm % :term`, `ORDER BY word_similarity(:term,text_norm) DESC, chunk_id`.
7. Vector list V (≤50): embed `"query: " + raw_query` (ONNX, in-process, mean-pool, L2-norm), exact scan `ORDER BY vec <=> :q ASC, chunk_id` scoped by `project_id` and current `model@revision`.
8. Fusion (RRF, k=60): `S(c) = Σ_s w_s / (60 + rank_s(c))`, `w_L=1.0, w_T=0.8, w_V=1.0`; absent from a list → no contribution. Tie-break: lexical rank, then lower `chunk_id`.
9. Dedupe by `logical_id`: keep best chunk; `S' = S × kw`, `kw(lesson)=1.2, kw(experience)=1.1, else 1.0`. No decay in Phase 0 (signals only).
10. Card: fetch current `project_card` separately (slot 0); `stale = any(derived_from targets superseded)`.
11. Clues: `v{version_id}.{ordinal}` for hits, `v{version_id}` for card.
12. Pack (measure after each append, never exceed): (a) envelope with empty lists; (b) card: full if ≤ max(512, 25%·budget) else truncate to that and `truncated:true`; (c) hits in `S'` order, `preview` = first 48 o200k tokens of chunk text; stop at first non-fit, `omitted` = rest; (d) if ≥96 tokens remain, extend top-3 previews to 120 tokens. `evidence="none"` when hits is empty.
13. No write on query (preview exposure does not count as access).

## 4. `hlm` wrapper CLI (typer)

Commands: `hlm init` (writes `hlm.toml`), `hlm doctor` (server/db/model check), `hlm project create|list <slug>` (HTTP `/admin/projects`, admin token), `hlm mcp add claude|codex|agy` (registers server in the CLI), `hlm query "<q>" [--budget 3000]`, `hlm close --notes ... [--lesson ...]` (call_the_day), `hlm claude|codex|agy [CLI args...] [--task "..."] [--no-preflight] [--budget N]`.

`hlm.toml` (precedence env > toml > profile; discovery `./hlm.toml` → `~/.config/hlm/hlm.toml`):
```toml
[hlm]
profile = "openrouter"
HLM_DB_DSN = "postgresql://hlm:hlm@127.0.0.1:5432/hlm"
HLM_EMBED_MODEL = "intfloat/multilingual-e5-small@<pinned-rev>"   # ASSUMPTION: rev fixed at first build
HLM_LLM_BASE_URL = "https://openrouter.ai/api/v1"
HLM_LLM_MODEL = "deepseek/deepseek-v4.1-flash"
HLM_LLM_API_KEY = "env:OPENROUTER_API_KEY"
HLM_LLM_REASONING = "off"
[client]
server_url = "http://127.0.0.1:8765/mcp"
token = "env:HLM_API_TOKEN"
project = "hlmemo"
[preflight]
budget = 3000
timeout_s = 5
on_failure = "block"      # block | warn
[profiles.openrouter]   HLM_LLM_BASE_URL="https://openrouter.ai/api/v1" HLM_LLM_MODEL="deepseek/deepseek-v4.1-flash" HLM_LLM_REASONING="off" extra={response_format="json_object",temperature=0,provider={data_collection="deny"}}
[profiles.openai]       HLM_LLM_BASE_URL="https://api.openai.com/v1"  HLM_LLM_MODEL="gpt-5.6-luna" HLM_LLM_REASONING="off"
[profiles.local-vllm]   HLM_LLM_BASE_URL="http://127.0.0.1:8000/v1"   HLM_LLM_MODEL="<served-model>" HLM_LLM_API_KEY="none"
```
Also shipped: `mistral-eu`, `alibaba-eu` (D-017). Fallback = `openai` profile.

Preflight: query text = `--task` if given, else `"<git branch>: <last 3 commit subjects>"`; call `memory.query` (retry once); build prompt
`<hlmemo-preflight project=.. queried_at=..>{compact JSON}</hlmemo-preflight>\nReview this evidence before acting; use memory.drilldown(clue_ids) for detail. Task: <task|await user>`.
Invocation: `claude "<prompt>" [args]` (+`CLAUDE.md`); `codex "<prompt>" [args]` (+`AGENTS.md`); `agy -p "<prompt>"` headless / `agy "<prompt>"` interactive (ASSUMPTION: interactive positional supported; +`GEMINI.md`). `hlm mcp add` runs `claude mcp add --transport http hlm <url> --header "Authorization: Bearer $HLM_API_TOKEN"`, `codex mcp add hlm --url <url> --bearer-token-env-var HLM_API_TOKEN` (ASSUMPTION flag names), and edits agy `mcp_config.json`. Unreachable/failed preflight: `on_failure=block` (default, D-014) → stderr `HLMemo preflight failed: <reason>`, exit 3, CLI not launched; `warn` → launches with prompt `HLMemo unavailable; memory NOT consulted.`; `--no-preflight` is explicit and logged.

## 5. Package layout & compose

```
HLMemo/
  pyproject.toml  Dockerfile  docker-compose.yml  hlm.example.toml  profiles/*.toml
  alembic.ini  alembic/versions/0001_phase0.py  0002_hnsw.py(deferred)
  src/hlmemo/
    config.py          pydantic-settings: HLM_* env + hlm.toml + profiles
    db/pool.py         psycopg3 AsyncConnectionPool
    db/queries.py      all SQL (candidate lists, temporal predicate, outbox pick)
    core/normalize.py  normalize(), term split, identifier detection
    core/chunker.py    E5 tokenizer, 400-token chunks, 40 overlap, char offsets
    core/embedder.py   ONNX Runtime session for e5-small (fp32 onnx/model.onnx), prefixes
    core/budget.py     o200k_base meter + greedy packer
    core/retrieval.py  steps 3-9 of section 3
    core/clues.py      encode/decode clue ids
    core/write_service.py  one-tx events+versions+chunks+links+jobs, idempotency, conflicts
    core/temporal.py   bi-temporal predicates/validation
    server/app.py      MCP server (streamable HTTP) + /health + /admin/projects
    server/auth.py     bearer → project scope
    server/tools/{query,drilldown,raw,write,call_the_day}.py
    worker/main.py     outbox loop (FOR UPDATE SKIP LOCKED, backoff 1/2/4/8/16 s ×5)
    cli/{hlm.py,preflight.py,launch.py,mcp_register.py}
  tests/ (see 6)   .githooks/pre-commit   .gitleaks.toml
```
Compose: `db: pgvector/pgvector:0.8.1-pg17` (ASSUMPTION exact tag), `migrate` (one-shot `alembic upgrade head`), `api` and `worker` (both from `Dockerfile` on `python:3.12.7-slim-bookworm`, model files baked into image at pinned revision), healthchecks on `/health` and `pg_isready`. Pins (ASSUMPTION latest stable at 2026-09): `mcp==2.*`, `psycopg[binary,pool]==3.3.*`, `alembic==1.17.*`, `pgvector==0.4.*`, `onnxruntime==1.23.*`, `tokenizers==0.22.*`, `tiktoken==0.12.*`, `pydantic==2.12.*`, `typer==0.20.*`, `httpx==0.28.*`, `pytest==8.*`.

## 6. Test plan

Fixture (`tests/fixtures/gen_fixture.py`, `random.Random(20260922)`, frozen JSONL + SHA256 asserted by `test_fixture_frozen`): synthetic "project world" of 2,000 items × ~5 chunks = 10,000 chunks in project `fx-main` + 2,000 decoy chunks in `fx-other`. Templates per language (TR/DE/EN vocab lists) instantiate entities: service names (`svc-qx7`), env keys (`APP_DB_DSN`), paths, error codes (`E4193`), people, dates. 100 queries (34 TR/33 DE/33 EN; 25 identifier-heavy) are generated from a *different* template than the gold item with morphological variants (TR suffix table -ler/-de/-nin, DE plural/compound, EN inflection); gold = originating `logical_id`. Recall@5 = gold logical_id among top-5 dedup hits. ASSUMPTION: threshold tuned on this synthetic set; real-data regression added in Phase 1.

| Gate | pytest |
|---|---|
| G1 | `test_g1_boot::test_health_within_60s`, `::test_restart_preserves_acked_events` |
| G2 | `test_g2_budget::test_1000_random_budgets_never_overflow` (Hypothesis-free, seeded), `::test_budget_below_min_rejected`, `::test_drilldown_raw_budget` |
| G3 | `test_g3_recall::test_recall_at_5_ge_090`, `::test_per_language_recall_logged` |
| G4 | `test_g4_latency::test_warm_p95_le_500ms_3_callers` (300 queries, 3 threads, records `HARDWARE.md`) |
| G5 | `test_g5_isolation::test_1000_cross_project_probes_zero_leaks` (query/drilldown/raw with wrong token) |
| G6 | `test_g6_durability::test_duplicate_request_id_single_effect`, `::test_concurrent_revision_conflict`, `::test_kill_api_mid_batch_then_replay`, `::test_rebuild_projections_from_events_identical`, `::test_backdated_correction_valid_at_known_at`, `::test_pg_dump_restore_same_answers` |
| G7 | `tests/smoke/{claude,codex,agy}.sh` pinned versions (ASSUMPTION versions recorded in `tests/smoke/VERSIONS`), headless `-p`/`exec` run; `test_g7_clients::test_server_log_shows_write_query_drilldown_raw` |
| G8 | `test_g8_secrets::test_gitleaks_clean`, `::test_env_untracked`; pre-commit + CI |

## 7. Cuts if the 2-week budget is blown (in order)

1. Drop Antigravity (`agy`) from the wrapper and G7 (keep claude + codex; agy returns in Phase 1).
2. Drop the async worker: embed synchronously inside the write transaction (jobs table and outbox schema stay; compose loses one service; write latency +~50 ms/chunk).

