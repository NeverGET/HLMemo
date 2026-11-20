# A. Inventory of the Python codebase for a Rust port

Workstream A of `PLAN.md`. Read-only survey of main 4b6345d (2026-11-20). Method: an AST pass over every package
(imports, async/await, pydantic/dataclass classes, regex/unicode calls, in-function imports, SQL strings with two or
more SQL markers), comment/docstring/blank counts, then reading the hot and deterministic code by hand.
"Eff." = lines minus docstrings, comments and blanks. "SQL" = share of a package's lines inside SQL strings;
`exec` = `.execute(` call sites.

## 1. Per package

| Package | Runs where | Lines / eff. | SQL (exec) | What it does |
|---|---|---|---|---|
| librarian | server (librarian process + API process for `ask`/risk judge) | 16,406 / 12,029 | 2% (101) | LLM worker: leases jobs, calls providers over HTTP, guards, privacy/redaction, budget ledger, prompts, cassettes, research answer verification |
| core | server (API) | 11,816 / 8,672 | <1% (21) | Read/write/research/risk/synthesis services, retrieval + RRF, budget meter, normalize, chunker, embedder, memory map |
| cli | client + operator | 4,981 / 3,902 | 0% (5) | `hlm` (typer): init/doctor/device/query/import/migrate/curate/review/links, MCP client, keyring credentials |
| db | server | 3,539 / 2,698 | 18% (126) | All query modules (read/write/librarian/auth/risk/import), pool, projection replay from events |
| server | server | 3,203 / 2,379 | <1% (8) | Starlette app, MCP streamable-HTTP endpoint (lowlevel `Server`), auth middleware, device registration, tool schemas |
| importers | client/operator | 3,056 / 2,302 | 0% | Markdown/automemory/serena/lessons importers, plan/classify, export format, git provenance |
| ops | operator on the server (`python -m hlmemo.ops`) | 3,012 / 2,403 | 4% (43) | Token mint/rotate, grants, librarian ops, link backfill, shadow relate pipeline |
| bench | offline/operator | 2,877 / 2,304 | 0% | LLM model bench, scorers, leaderboard, Decimal cost reports |
| migrate | client (project chats) + operator | 2,057 / 1,455 | 0% | Migration kit: spec (TOML), lint, redact, seal, batches, round-trip checks |
| curate | operator (Cemal's machine) | 1,997 / 1,631 | 3% | Resumable curation pipeline driving agent CLIs as workers, jsonschema-gated outputs |
| capture | client hook | 1,568 / 1,266 | 0% | SessionEnd/PreCompact hook: transcript reduce, scrub, `claude -p` summarise, relay `call_the_day` |
| brief | client hook (every session start) | 1,512 / 1,045 | 0% | SessionStart hook: protocol digest + memory brief, watchdog, local cache, tiktoken vocab |
| worker | server (embed worker) | 874 / 652 | 10% (15) | Outbox embed worker, `FOR UPDATE SKIP LOCKED` leases, fenced writes |
| auth | server | 332 / 197 | ~3% (2) | Bearer -> AuthContext, token hashing, HMAC cursors, error envelope |
| (config.py) | everywhere | 541 | 0% | pydantic-settings: profile file < `hlm.toml` < `HLM_*` env, nested `__` keys, validators |

SQL is a minority everywhere except db/worker: queries are composed from f-string fragments (`db/read_queries.py:33-52`
`_authz`/`_temporal`), so they move to sqlx/tokio-postgres as text, but the row-to-struct mapping (25 dataclasses in db)
and psycopg specifics (Jsonb, pgvector, `'infinity'` timestamps mapped to `None`, int arrays) must be rewritten.

### Python reliance, libraries, difficulty, estimate

Estimate = person-days for an experienced Rust developer with a coding agent, including porting that package's tests.

| Package | Python-specific reliance | Libraries | Rating | Days |
|---|---|---|---|---|
| librarian | 87 `re.compile` (22 with lookaround, need `fancy-regex`), 18 `casefold`, 10 `unicodedata.normalize`, char-index maps (`tasks/research.py:225`, `risk_judge.py:192`), 159 dict literals, `Decimal` money (`provider.py:86`), 202 async defs, `create_task`/shield cancellation, 41 in-function imports, 43 prompt/schema files | httpx, psycopg(+pool), jsonschema, pydantic, numpy | high | 40 |
| core | `normalize`/`extract_terms` (NFKC+casefold+NFD, Unicode `\w`), code-point offsets, 46 regexes (lookbehind in `retrieval.py:247`), pydantic request models with custom error codes (`write_models.py:39`), canonical JSON hashing, `lru_cache`, thread-pool offload | tokenizers, onnxruntime, numpy, tiktoken, pydantic, psycopg | high | 30 |
| server | ASGI middleware that buffers the MCP response until COMMIT (`middleware.py:1-30`), `asyncio.shield` (`app.py:361`), `app.state` getattr (41), `importlib` lazy load (`tools/handlers.py:46`) | mcp 2.2, starlette, uvicorn, pydantic, alembic (head check) | high | 12 |
| cli | typer (52 in-function imports for fast start), keyring, subprocess (git, ioreg, claude) | typer, mcp client, httpx, keyring, psycopg | medium | 12 |
| db | dataclass rows, psycopg adapters, `replay.py` event re-application | psycopg, pgvector | medium | 8 |
| importers | 39 regexes (frontmatter), NFC/casefold, git subprocess, golden JSON plans | none heavy | medium | 8 |
| ops | dict-heavy reports (107 literals), SQL | psycopg, httpx | medium | 7 |
| migrate | 20 regexes (7 lookaround), tomllib, `__dict__` dumps (`markers.py:111`) | none | medium | 6 |
| bench | Decimal, sha256 keys, subprocess git | httpx, typer, psycopg | low (or keep in Python) | 6 |
| curate | subprocess agent CLIs in a thread pool (`workers.py:371`), jsonschema | jsonschema | low-medium (or keep) | 5 |
| capture | 16 regexes (scrub), `fcntl` lock, detached `Popen`, `claude -p` | none | low-medium | 5 |
| brief | watchdog thread (`hook.py:522`), tiktoken vocab cache management (`vocab.py`), `-P` import hygiene | tiktoken | medium | 5 |
| worker | lease loop, `to_thread` embedding (`main.py:424`) | psycopg, onnxruntime | low | 3 |
| auth | HMAC cursors, constant-time compare | psycopg | low | 2 |
| cross-cutting | config.py (pydantic-settings precedence), test harness (Postgres fixtures, cassette replay), differential-test harness Python vs Rust, Dockerfile/compose/`python -m` call sites in deploy | | | 20 |
| **Total** | | | | **~169** (range 120-220) |

## 2. CPU-hot and latency-critical paths

| Path | Where | Note |
|---|---|---|
| Query embedding (ONNX e5-small, 384-d, mean pool + L2) | `core/embedder.py:233` `_infer`, `:247` `embed_texts`; called **inline on the event loop** at `core/read_service.py:261` | Blocks the API loop per `memory.query`; Rust would use `spawn_blocking`. |
| Batch passage embedding | `worker/main.py:424` | Dominant worker CPU. Same ONNX model, so speed-up is mostly Python glue, not inference. |
| E5 tokenization + chunking (400/40 tokens) | `core/chunker.py:67`, `core/write_service.py:116-125` (offloaded above 2,048 chars) | |
| normalize / extract_terms | `core/normalize.py:26`, `:39`; per chunk on write, per query on read | |
| o200k metering of canonical JSON | `core/budget.py:74`, `:99-152` (`count`, `truncate`, `token_offsets`, `pack`) | Every response is serialised and counted, often repeatedly while packing. |
| RRF fusion + dedupe | `core/retrieval.py:163` `rrf_fuse`, `:215`; `core/research_service.py:281`; `librarian/candidates.py:134` | Small lists (tens), cheap. |
| Term highlighting / previews | `core/retrieval.py:254` (per-char normalise map), `:287`, `:354`, `:413`, `:456` | |
| Term DF cache | `core/term_stats.py:118` `StatsCache` (single-flight, SWR) | |
| Secret guard on every write | `core/secret_guard.py:105` | Regex over every string of the raw arguments. |
| Memory map render per `ask` | `core/memory_map.py:357` `build_map` | Python loops over all current items of a project. |
| Answer verification | `librarian/tasks/research.py:225-760` (quote location, literal checks, polarity) | Offloaded with `to_thread` at `research_service.py:1483`, `:1973`. |
| Redaction of every prompt | `librarian/redact.py:99-108` | |

LLM latency (seconds) dwarfs all of these for `ask`/librarian; the read path (`query`) is where Rust can show.

## 3. Behaviour a port must reproduce bit for bit

Stored or wire-visible values. Getting these wrong breaks prod data, idempotent retries or the test cassettes:

1. **`normalize`** (`core/normalize.py:26`), stored as `chunks.text_norm` (`db/write_queries.py:396`) and used for
   tsvector matching; `NORMALIZER_VERSION = 1` (`core/__init__.py:11`). Python 3.12 `unicodedata` is Unicode 15.0.
   Python `re` `\w` (isalnum + `_`) differs from Rust `regex` `\w` (adds marks and connector punctuation). Python
   `casefold` is full folding, which Rust `to_lowercase` is not. Pinned by `tests/unit/test_normalize.py:61-77`.
   SQL has its own fold, `hlm_title_norm` (`alembic/versions/0004_title_norm_fold.py:25`).
2. **Chunk boundaries and offsets**: `chunks.char_start/char_end` are **code-point** offsets. The Python `tokenizers`
   binding returns char offsets; the Rust crate returns byte offsets, so they need converting. Pinned by
   `tests/unit/test_chunker.py`.
3. **Token counts**: o200k over `json.dumps(sort_keys, ensure_ascii=False, separators=(",",":"))`
   (`core/budget.py:74`). Python float repr (`1e-05`) differs from serde/ryu (`1e-5`), and scores are rounded with
   `round()` (`retrieval.py:400`). These are tested by budget equality (`test_budget.py`, `test_d055_retrieval.py`
   pack tests) and by `tools/list` ≤ 3,000 tokens (`test_gsurf_tools_list.py:12`).
4. **`payload_sha256`** = sha256(canonical JSON of the raw arguments) (`core/write_service.py:191`). This is the
   idempotency key stored in prod `events`, so a retry that crosses the cutover must hash the same.
5. **Cassette keys** = sha256(model ‖ prompt version ‖ schema version ‖ canonical(redacted messages) ‖
   canonical(params)) (`librarian/cassette.py:33`). Every prompt byte, the redaction output and the JSON shaping
   must match, or the 859 recorded lines in `tests/cassettes/*` miss and have to be re-recorded with paid live calls.
6. **IDs and formats**: `hlm_` tokens and their sha256 (`auth/tokens.py:26`); HMAC cursors (`auth/cursors.py:37-69`);
   `⟦REDACTED:type:sha8⟧` (`librarian/redact.py:85`); uuid5 request ids (`importers/plan.py:95`,
   `capture/write.py:31`); clue handles `vN`.
7. **RRF ordering**: weights, `K_RRF`, and the tie-break (score, scope specificity, lexical rank, chunk_id) in
   `retrieval.py:205`. Pinned by `test_d055_retrieval.py` (e.g. order `[12, 21]`).
8. **Projection replay** from `events.payload.resolved` (`db/replay.py`). A Rust replayer must rebuild the same
   projections from Python-written events (`test_g6_replay.py`).
9. **Four separate secret regex sets** (`core/secret_guard.py`, `importers/common.py`, `capture/scrub.py`,
   `librarian/redact.py`, plus `migrate/redact.py`) with lookaround. Security-critical; pinned by `test_secret_rules`,
   `test_r41b_secret_rules`, `test_r41c_secret_names`, `test_gl2_redaction`.
10. **Importer plans**: `tests/fixtures/import/golden/*.json` (exact JSON).

## 4. Tests

- 249 files, 71.9k lines: unit 99 files / 26.8k, integration 115 / 35.3k (95 marked `integration`, live Postgres via
  `HLM_TEST_DSN`, alembic upgrade once per session, truncation between tests, `tests/conftest.py:1-80`), deploy 25 / 7.9k.
- LLM: strict cassette replay by default (`HLM_LLM_MODE=replay`, `conftest.py:39`). 30 test files touch cassettes;
  the G-L gates use in-process stubs.
- Golden/frozen data: G3 recall fixture (`tests/fixtures/g3`, SHA256SUMS, 100 TR/DE/EN queries over 2,000 items,
  gate ≥ 0.90), import golden JSON, bench saved outputs, provider error fixtures.
- Reuse: 91 integration files import `hlmemo.core/db/librarian` directly (in-process), so they cannot run against
  a Rust binary. About 38 go through the wire (MCP/HTTP), and those could be re-pointed at a Rust server to serve
  as a black-box conformance suite. Embedding tests use tolerances (`allclose 1e-4/1e-5`), not exact vectors.

## 5. Shared with non-Python parts

- `alembic/versions` (11 files, 1,396 lines): mostly raw SQL plus two plpgsql functions (`hlm_title_norm`). The
  server checks the alembic head at startup (`server/app.py:116`), so a port either keeps alembic as the migrator or
  takes over its `alembic_version` table.
- `deploy/` + `tools/` (7.8k lines): about 20 call sites of `python -m hlmemo.ops`, plus `python -m
  hlmemo.librarian.{worker,health}`, the `hlm ...` CLI (50× `hlm migrate`) and Python helpers (`check_librarian.py`
  1,130 lines, `release_state.py`, `load_smoke.py`). Changing the CLI surface or module paths breaks the runbook
  and the remote gates.
- `integrations/claude/skills` (3 skills, 1.8k lines) script `hlm` commands and the hook command
  `python -P -m hlmemo.brief.hook`, which is registered in users' `~/.claude/settings.json`.
- `src/hlmemo/librarian/prompts` (43 `.md`/`.schema.json` files), `brief/protocol_digest.txt` (a byte copy of the
  protocol doc), `profiles/*.toml`, `models.lock`, and `curate/briefs/*.md` are data files any port embeds unchanged.

## 6. The five hardest parts

1. **Answer-verification and judgement text logic** (`librarian/tasks/research.py`, 3,675 lines, 56 regexes;
   `guards.py`, `risk_judge.py`). Unicode folding with index maps, lookaround regexes and heuristics tuned on
   real data. A port has to match outputs, not only the spec, and any drift changes measured LLM quality.
2. **The MCP transport and commit-before-ack middleware** (`server/middleware.py`, `mcp_server.py`). It wraps the
   Python SDK's streamable-HTTP ASGI app, buffers responses until COMMIT and shields commits from cancellation.
   In Rust, dropping a future cancels it at any `.await`, so these semantics have to be rebuilt deliberately
   around the Rust MCP SDK.
3. **Byte-exact determinism** (section 3): normalize/`\w`/casefold, code-point offsets, canonical JSON plus o200k
   counts, and cassette keys. Each needs a differential test against Python before cutover.
4. **The provider layer** (`librarian/provider.py`, 1,484 lines): Decimal spend reservation, breakers, fallback
   chains, unbilled-error policies, usage normalisation across vendors, and cassette record/replay.
5. **The test suite**: 72k lines, mostly in-process. The port must rewrite most of it or replace it with a wire-level
   conformance suite plus differential fixtures, and it is the largest single cost after the librarian.
