1. **POSTGRES DDL**

Half-open intervals; infinity ends. Project-scoped foreign keys.

```sql
CREATE EXTENSION vector;
CREATE EXTENSION pg_trgm;
CREATE EXTENSION btree_gist;

CREATE TABLE projects(
 project_id uuid PRIMARY KEY, name text NOT NULL UNIQUE,
 policy jsonb NOT NULL DEFAULT '{}');

CREATE TABLE events(
 project_id uuid REFERENCES projects, event_id uuid,
 seq bigint GENERATED ALWAYS AS IDENTITY,
 request_id uuid NOT NULL, request_hash bytea NOT NULL,
 kind text NOT NULL, session_id uuid, payload jsonb NOT NULL,
 result jsonb NOT NULL, recorded_at timestamptz NOT NULL,
 PRIMARY KEY(project_id,event_id), UNIQUE(project_id,request_id),
 UNIQUE(project_id,seq));
CREATE UNIQUE INDEX one_close ON events(project_id,session_id)
 WHERE kind='close';

CREATE TABLE memory_versions(
 project_id uuid, version_id uuid, logical_id uuid NOT NULL,
 revision integer NOT NULL CHECK(revision>0),
 event_id uuid NOT NULL, layer smallint NOT NULL CHECK(layer BETWEEN 1 AND 4),
 body text NOT NULL, archived boolean NOT NULL DEFAULT false,
 valid_from timestamptz NOT NULL, valid_to timestamptz NOT NULL DEFAULT 'infinity',
 recorded_at timestamptz NOT NULL, superseded_at timestamptz NOT NULL DEFAULT 'infinity',
 PRIMARY KEY(project_id,version_id),
 FOREIGN KEY(project_id,event_id) REFERENCES events,
 UNIQUE(project_id,logical_id,revision,valid_from),
 CHECK(valid_from<valid_to AND recorded_at<superseded_at),
 EXCLUDE USING gist(project_id WITH =,logical_id WITH =,
 tstzrange(valid_from,valid_to,'[)') WITH &&,
 tstzrange(recorded_at,superseded_at,'[)') WITH &&));

CREATE TABLE chunks(
 project_id uuid, chunk_id uuid, version_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal>=0),
 start_offset integer NOT NULL, end_offset integer NOT NULL,
 body text NOT NULL, lex text NOT NULL,
 PRIMARY KEY(project_id,chunk_id), UNIQUE(project_id,version_id,ordinal),
 FOREIGN KEY(project_id,version_id) REFERENCES memory_versions,
 CHECK(start_offset>=0 AND end_offset>start_offset));

CREATE TABLE embeddings(
 project_id uuid, chunk_id uuid, model text, revision text, preprocessing text,
 embedding vector NOT NULL,
 PRIMARY KEY(project_id,chunk_id,model,revision,preprocessing),
 FOREIGN KEY(project_id,chunk_id) REFERENCES chunks);

CREATE TABLE links(
 project_id uuid, link_id uuid, source_id uuid NOT NULL,
 target_id uuid NOT NULL, event_id uuid NOT NULL, kind text NOT NULL,
 valid_from timestamptz NOT NULL, valid_to timestamptz NOT NULL DEFAULT 'infinity',
 recorded_at timestamptz NOT NULL, superseded_at timestamptz NOT NULL DEFAULT 'infinity',
 PRIMARY KEY(project_id,link_id),
 FOREIGN KEY(project_id,source_id) REFERENCES memory_versions,
 FOREIGN KEY(project_id,target_id) REFERENCES memory_versions,
 FOREIGN KEY(project_id,event_id) REFERENCES events,
 UNIQUE(project_id,source_id,target_id,kind,valid_from,recorded_at),
 CHECK(valid_from<valid_to AND recorded_at<superseded_at));

CREATE TABLE jobs(
 project_id uuid, job_id uuid, event_id uuid NOT NULL,
 dedupe_key text NOT NULL, kind text NOT NULL, payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN('queued','running','done','failed')),
 attempts integer NOT NULL DEFAULT 0, lease_token uuid,
 run_after timestamptz NOT NULL DEFAULT now(), lease_until timestamptz,
 PRIMARY KEY(project_id,job_id), UNIQUE(project_id,dedupe_key),
 FOREIGN KEY(project_id,event_id) REFERENCES events);

CREATE INDEX chunks_lex ON chunks USING gin(lex gin_trgm_ops);
CREATE INDEX links_reverse ON links(project_id,target_id);
CREATE INDEX jobs_ready ON jobs(run_after) WHERE state='queued';
CREATE INDEX jobs_expired ON jobs(lease_until) WHERE state='running';

-- Optional migration; substitute locked profile values.
CREATE INDEX embeddings_hnsw ON embeddings USING hnsw
 ((embedding::vector(384)) vector_cosine_ops)
 WITH(m=16,ef_construction=64)
 WHERE model='PROFILE_MODEL' AND revision='PROFILE_REVISION'
 AND preprocessing='PROFILE_PREPROCESSING';
```

D-008 keeps exact search active. Optional HNSW uses `ef_search=100`; variable-dimension storage permits configured model changes. [pgvector](https://github.com/pgvector/pgvector)

Choose GIN `pg_trgm`: language-independent partial-word/identifier matching for TR/DE/EN; embeddings supply semantics. [PostgreSQL](https://www.postgresql.org/docs/current/pgtrgm.html)

Writes lock the project, check canonical SHA-256 request hash before maximum logical revision, then atomically append event/projections/jobs. Hash excludes budget. Corrections close recorded intervals and insert replacement plus surviving valid-time fragments, rebuilding chunks/links. Replay reuses event IDs/monotonic timestamps. Events/chunks are immutable; version/link updates only close recorded intervals.

2. **TOOL CONTRACTS**

JSON Schema 2020-12; enforce formats. Emitted schemas include root `type:"object"` and these `$defs`. Mapping: `query:QI→Q`, `drilldown:DI→D`, `raw:RI→Raw`, `write:WI→Ack`, `call_the_day:CI→Ack`. Outputs use `oneOf` success/Error; errors set `isError:true`.

```json
{"$defs":{
"U":{"type":"string","format":"uuid"},
"T":{"type":"string","format":"date-time"},
"S":{"type":"string"},
"I":{"type":"integer","minimum":0},
"B":{"type":"integer","minimum":0,"maximum":8000},
"M":{"type":"object","properties":{"version_id":{"$ref":"#/$defs/U"},"logical_id":{"$ref":"#/$defs/U"},"expected_revision":{"$ref":"#/$defs/I"},"kind":{"enum":["note","summary","project_card","lesson"]},"body":{"type":"string","minLength":1,"maxLength":65536},"valid_from":{"$ref":"#/$defs/T"},"valid_to":{"anyOf":[{"$ref":"#/$defs/T"},{"type":"null"}]},"sources":{"type":"array","items":{"$ref":"#/$defs/U"},"maxItems":64,"uniqueItems":true}},"required":["version_id","logical_id","expected_revision","kind","body","valid_from","valid_to","sources"],"additionalProperties":false},
"Clue":{"type":"object","properties":{"clue_id":{"$ref":"#/$defs/U"},"version_id":{"$ref":"#/$defs/U"},"logical_id":{"$ref":"#/$defs/U"},"layer":{"type":"integer","minimum":1,"maximum":4},"preview":{"$ref":"#/$defs/S"},"stale":{"type":"boolean"}},"required":["clue_id","version_id","logical_id","layer","preview","stale"],"additionalProperties":false},
"Detail":{"type":"object","properties":{"clue_id":{"$ref":"#/$defs/U"},"version_id":{"$ref":"#/$defs/U"},"event_id":{"$ref":"#/$defs/U"},"text":{"$ref":"#/$defs/S"},"source_clues":{"type":"array","items":{"$ref":"#/$defs/U"}},"valid_from":{"$ref":"#/$defs/T"},"valid_to":{"anyOf":[{"$ref":"#/$defs/T"},{"type":"null"}]},"recorded_at":{"$ref":"#/$defs/T"},"superseded_at":{"anyOf":[{"$ref":"#/$defs/T"},{"type":"null"}]}},"required":["clue_id","version_id","event_id","text","source_clues","valid_from","valid_to","recorded_at","superseded_at"],"additionalProperties":false},
"Error":{"type":"object","properties":{"code":{"enum":["INVALID_ARGUMENT","FORBIDDEN","NOT_FOUND","CONFLICT","IDEMPOTENCY_CONFLICT","SESSION_CLOSED","INVALID_CURSOR","BUDGET_TOO_SMALL","UNAVAILABLE"]},"minimum_budget":{"anyOf":[{"$ref":"#/$defs/I"},{"type":"null"}]},"retryable":{"type":"boolean"}},"required":["code","minimum_budget","retryable"],"additionalProperties":false},
"QI":{"type":"object","properties":{"project_id":{"$ref":"#/$defs/U"},"token_budget":{"$ref":"#/$defs/B"},"query":{"type":"string","minLength":1,"maxLength":4096},"valid_at":{"$ref":"#/$defs/T"},"known_at":{"$ref":"#/$defs/T"},"include_archived":{"type":"boolean","default":false},"cursor":{"$ref":"#/$defs/S"}},"required":["project_id","token_budget","query"],"additionalProperties":false},
"DI":{"type":"object","properties":{"project_id":{"$ref":"#/$defs/U"},"token_budget":{"$ref":"#/$defs/B"},"clue_ids":{"type":"array","items":{"$ref":"#/$defs/U"},"minItems":1,"maxItems":20,"uniqueItems":true},"cursor":{"$ref":"#/$defs/S"}},"required":["project_id","token_budget","clue_ids"],"additionalProperties":false},
"RI":{"type":"object","properties":{"project_id":{"$ref":"#/$defs/U"},"token_budget":{"$ref":"#/$defs/B"},"event_id":{"$ref":"#/$defs/U"},"offset":{"type":"integer","minimum":0,"default":0}},"required":["project_id","token_budget","event_id"],"additionalProperties":false},
"WI":{"type":"object","properties":{"project_id":{"$ref":"#/$defs/U"},"token_budget":{"$ref":"#/$defs/B"},"request_id":{"$ref":"#/$defs/U"},"change":{"$ref":"#/$defs/M"}},"required":["project_id","token_budget","request_id","change"],"additionalProperties":false},
"CI":{"type":"object","properties":{"project_id":{"$ref":"#/$defs/U"},"token_budget":{"$ref":"#/$defs/B"},"request_id":{"$ref":"#/$defs/U"},"session_id":{"$ref":"#/$defs/U"},"notes":{"type":"array","items":{"allOf":[{"$ref":"#/$defs/M"},{"properties":{"kind":{"const":"note"}}}]},"maxItems":16},"card":{"allOf":[{"$ref":"#/$defs/M"},{"properties":{"kind":{"const":"project_card"}}}]}},"required":["project_id","token_budget","request_id","session_id","notes"],"additionalProperties":false},
"Q":{"type":"object","properties":{"card":{"anyOf":[{"$ref":"#/$defs/Clue"},{"type":"null"}]},"items":{"type":"array","items":{"$ref":"#/$defs/Clue"}},"next_cursor":{"anyOf":[{"$ref":"#/$defs/S"},{"type":"null"}]},"message":{"enum":["ok","no matching evidence"]},"indexing_pending":{"type":"boolean"}},"required":["card","items","next_cursor","message","indexing_pending"],"additionalProperties":false},
"D":{"type":"object","properties":{"items":{"type":"array","items":{"$ref":"#/$defs/Detail"}},"next_cursor":{"anyOf":[{"$ref":"#/$defs/S"},{"type":"null"}]}},"required":["items","next_cursor"],"additionalProperties":false},
"Raw":{"type":"object","properties":{"event_id":{"$ref":"#/$defs/U"},"offset":{"$ref":"#/$defs/I"},"text":{"$ref":"#/$defs/S"},"next_offset":{"anyOf":[{"$ref":"#/$defs/I"},{"type":"null"}]}},"required":["event_id","offset","text","next_offset"],"additionalProperties":false},
"Ack":{"type":"object","properties":{"event_id":{"$ref":"#/$defs/U"},"applied":{"type":"array","items":{"type":"object","properties":{"logical_id":{"$ref":"#/$defs/U"},"version_id":{"$ref":"#/$defs/U"},"revision":{"type":"integer","minimum":1}},"required":["logical_id","version_id","revision"],"additionalProperties":false}}},"required":["event_id","applied"],"additionalProperties":false}
}}
```

Query times default to request-time; null interval ends mean infinity. Revision zero creates. Kinds map to layers 1/2/3/4. Close commits notes/card together; sources may reference batch version IDs. Project creation emits a skeleton card: logical ID `uuid5(project_id,"project_card")`, maximum 512 tokens.

Drilldown returns requested chunks and one-hop source clues. Raw pages canonical event payload by Unicode-character offset. Explicit IDs remain historically readable after authorization. Error codes identify validation, authorization, lookup, revision/idempotency/session conflicts, budget and dependency failures.

Return structuredContent plus identical JSON text. Count the complete serialized MCP result using pinned `cl100k_base`, compact separators, sorted keys, UTF-8, including duplication/escaping. Minimum budget 128; reject before mutation if acknowledgement cannot fit. Pack complete items; raw packs character prefixes. No item fitting means BUDGET_TOO_SMALL with minimum_budget; rejected-budget errors are exempt. Never equate budget exhaustion with “no matching evidence”. [MCP](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)

3. **RETRIEVAL ALGORITHM**

Authorize bearer/project before every lookup. Filter project, archive flag, `valid_from≤valid_at<valid_to`, `recorded_at≤known_at<superseded_at`. Lexical: NFKC/casefold, threshold .1, indexed `query <% lex`, top 100 by word_similarity. Vector: matching model/revision/preprocessing, normalized `query:` embedding, exact cosine top 100 over a materialized filtered relation.

`score=1/(60+lexical_rank)+1/(60+vector_rank)`; ranks start at one, missing terms contribute zero. Keep maximum-score chunk per logical_id; order score descending, layer preference 3/2/1/4, UUID ascending. Clue IDs are chunk UUIDs; previews contain first 64 tokens. Pack current card preview, then ranked clues. Staleness compares source versions at requested times. Signed cursors reference ten-minute cached rankings; expiration/restart invalidates them. Chunk at 400 embedding tokens, overlap 32, prefix `passage:`. Lexical projections commit immediately; embeddings remain asynchronous.

4. **`hlm` WRAPPER**

`hlm init|doctor|bench`; `hlm {claude,codex,agy} --project UUID --prompt TEXT [--budget 1500] [--timeout 5] [--headless] [-- CLI_ARGS]`. Init creates project/card and registers MCP. Precedence: flags > environment > nearest hlm.toml > defaults.

```toml
profile="openrouter"
fallback_profile="openrouter-luna"
HLM_SERVER_URL="http://127.0.0.1:8787/mcp"
HLM_HOSTING_TARGET="compose"
HLM_EMBED_MODEL="intfloat/multilingual-e5-small"
HLM_EMBED_REVISION="models.lock:e5"
HLM_DB_DSN="${HLM_DB_DSN}"
[profiles.openrouter]
HLM_LLM_BASE_URL="https://openrouter.ai/api/v1"
HLM_LLM_MODEL="deepseek/deepseek-v4.1-flash"
HLM_LLM_API_KEY="${HLM_LLM_API_KEY}"
HLM_LLM_REASONING='{"enabled":false}'
reasoning_field="reasoning"
response_format={type="json_object"}
temperature=0
[profiles.openrouter-luna]
extends="openrouter"
HLM_LLM_MODEL="openai/gpt-5.6-luna"
```

Also ship openai/mistral-eu/alibaba-eu/local-vllm profiles with profile-defined request mappings. Expand environment references; prelock model/tokenizer/ONNX hashes. Phase-0 librarian disabled.

After successful authenticated query, P is labeled evidence-as-data + bounded JSON + original task. Launch using argv, cwd=ROOT:

```sh
claude "$P"                                      # headless: claude -p "$P"
codex -C "$ROOT" "$P"                            # headless: codex exec -C "$ROOT" "$P"
agy --add-dir "$ROOT" --prompt-interactive "$P"    # headless: agy --add-dir "$ROOT" --print "$P"
```

Reject prompt/resume overrides. Any preflight failure exits 69 without spawning. Guarantee covers initial tasks. Verified local pins: Claude 2.1.278, Codex 0.155.1, agy 1.1.4.

5. **PYTHON PACKAGE LAYOUT**

```text
src/hlmemo/
  server/{app,auth,tools,write,retrieve,budget}.py
  worker/{main,outbox,embed}.py
  wrapper/{cli,config,adapters}.py
  db/{repository,replay}.py
migrations/0001.sql
profiles/*.toml
models.lock
bench/
tests/{unit,integration,gates,fixtures}/
compose.yaml
Dockerfile
pyproject.toml
uv.lock
```

Modules respectively own transport/health, authorization, schemas, transactions, retrieval, packing; worker lifecycle, leased jobs, ONNX; arguments, configuration, CLI invocation; SQL access, replay. Fence completions by lease_token.

Compose: db=`pgvector/pgvector:0.8.2-pg17`; migrate/api/worker/test=`hlmemo:0.0.1`, built from `python:3.12.10-slim-bookworm`. Persist pgdata; bake model artifacts. API/worker wait for migrations.

6. **TEST PLAN**

Session-scoped Compose; isolated project UUIDs; real pinned ONNX. Freeze seed 0, UUID5, UTC timestamps and human-authored paraphrase dictionaries: 100 gold chunks plus 9,900 distractors, including cards; 30 TR/30 DE/30 EN/10 identifier queries. Include cross-language pairs, confusable entities, backdates and supersessions. Derive gold from fact IDs; commit fixture checksums.

| Gate | Concrete pytest tests |
|---|---|
| G1 | `test_health_within_60s`, `test_restart_preserves_acknowledged` |
| G2 | `test_serialized_budget_1000`, `test_undersized_budget_no_write` |
| G3 | `test_recall5_ge_090` |
| G4 | `test_warm_p95_le_500ms_three_callers` |
| G5 | `test_cross_project_1000_zero_leaks` |
| G6 | `test_idempotent_replay`, `test_revision_race`, `test_crash_after_commit`, `test_backdated_split`, `test_rebuild`, `test_pg_dump_restore` |
| G7 | `test_cli_write_query_drilldown_raw`, `test_preflight_300_per_cli`, `test_outage_no_launch` |
| G8 | `test_gitleaks_tree`, `test_env_untracked` |

G4 includes query embedding and records hardware. G7 uses real pinned clients, correlated traces and 300/300 initial-task successes. Independently reviewed; runtime gates unrun.

7. **TWO CUTS, IN ORDER**

1. Automatic MCP registration; retain manual configuration.
2. Interactive launch adapters; retain headless wrappers for all three CLIs.
