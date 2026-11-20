# B. Rust ecosystem map for HLMemo's Python dependencies

Checked 2026-11-20 against crates.io (API `https://crates.io/api/v1/crates/<name>`), PyPI, GitHub releases and
vendor docs. "Latest" means the newest version on crates.io with its publish date; downloads are all-time.
HLMemo usage was read from `src/hlmemo` (read-only).

## Summary table

| Python dep (HLMemo use) | Rust candidate | Latest (date) | Maintenance signal | Fit | Gap we write |
|---|---|---|---|---|---|
| **mcp 2.2**: low-level `Server`, stateless streamable HTTP, JSON responses, verbatim tool schemas, own error envelope; CLI client | **rmcp** (official) | 3.11.0 (2026-11-17) | `modelcontextprotocol/rust-sdk`, Tier 1 SDK; 52.7M downloads; ~weekly releases | Clean | Request-scoped DB transaction handed to tools; canonical JSON bytes (see notes) |
| **psycopg 3.3 + pool**: raw SQL in ~63 files, `%(name)s` params, advisory locks, `FOR SHARE` | **sqlx** (or tokio-postgres + deadpool-postgres) | sqlx 0.9.1 (2026-11-18); tokio-postgres 0.7.18 (2026-06-12); deadpool-postgres 0.14.2 (2026-08-26) | sqlx 195M downloads; rust-postgres 91M | Clean | Rewrite every named placeholder to `$n`; dict rows → typed structs |
| **pgvector 0.5** (vectors mostly passed as text, `<=>`, HNSW) | **pgvector** (pgvector org) | 0.4.2 (2026-05-22) | Same author as the extension; 23M downloads | Clean | None |
| **alembic + sqlalchemy**: 11 revisions, raw SQL via `exec_driver_sql`, a separate `hnsw` branch run `CONCURRENTLY` | **sqlx migrate** or **refinery** | sqlx-cli 0.9.1 (2026-11-18); refinery 0.10.0 (2026-10-02) | Both active | Partial | Baseline the 11 revisions; the branch label and lock-timeout glue |
| **onnxruntime 1.30**: CPU EP, `intra_op_num_threads`, no arena, e5-small, mean pool + L2 | **ort** (pyke) | 2.0.0-rc.15 (2026-10-19), wraps ORT 1.31 | Community, not Microsoft; 27M downloads; still pre-1.0 | Clean (RC) | ~100 lines: batching, padding, pooling, hash lock |
| **tokenizers 0.23** (`tokenizer.json`, truncation, no padding) | **tokenizers** (HF; Python is a binding of this crate) | 0.23.2 (2026-09-03); 1.0.0-rc.5 (2026-11-20) | Hugging Face; 49M downloads | Clean, same code | None |
| **tiktoken 0.14** (o200k_base budget meter, vocab cache handling) | **tiktoken-rs** (alt: bpe-openai from GitHub) | 0.12.1 (2026-09-24); bpe-openai 0.3.2 (2026-10-08) | Community; 26M downloads | Clean | Parity test only; vocab is embedded |
| **pydantic 2 + pydantic-settings** (config, `Field(gt=0)`, env) | **serde** + **config** (or figment) + **garde**/**validator** | serde 1.0.229; config 0.15.27 (2026-09-30); figment 0.10.19 (2024-05-17); garde 0.23.0 | serde/config active; figment no release since 2024 | Partial | Hand-written validation messages; env-prefix rules |
| **typer** (`hlm` CLI) | **clap** (derive) | 4.6.9 (2026-11-20) | 1.36B downloads | Clean | None |
| **httpx** (OpenRouter, Gemini, Anthropic; retries, timeouts, `MockTransport` in tests) | **reqwest** + **reqwest-middleware/-retry** or **backon** | reqwest 0.13.5 (2026-09-08); reqwest-retry 0.9.1; backon 1.6.0 | Very widely used | Clean | Test transport: wiremock or a tower mock |
| LLM provider SDKs (HLMemo uses none; raw httpx) | plain **reqwest + serde** types; optional async-openai / genai | async-openai 0.43.0 (2026-11-01); genai 0.7.5 (2026-11-14) | No official Rust SDK from Anthropic, OpenAI or Google | n/a | Same as today: our own request/response types |
| **keyring 25** (client secret store, macOS + Linux) | **keyring** 4 / **keyring-core** + per-platform store crates | keyring 4.2.0 (2026-08-29); keyring-core 1.0.1 (2026-10-13) | Active; 42M downloads | Clean | Choose stores per OS |
| **starlette + uvicorn** (routes `/health` `/ready` `/devices` `/admin` `/mcp`, middleware) | **axum** + **tower-http** (on hyper/tokio) | axum 0.8.9 (2026-04-14); tower-http 0.7.1; hyper 1.12.0 | tokio-rs; 609M downloads | Clean | None beyond the port itself |
| **jsonschema** (Draft 2020-12 validation of LLM output, `iter_errors`) | **jsonschema** (Stranger6667) | 0.61.2 (2026-11-12) | Active; 127M downloads | Clean | Error ordering/messages differ |
| Partial port: hot paths only | **PyO3** + **maturin** (+ pyo3-async-runtimes) | pyo3 0.30.1 (2026-10-14); maturin 1.16.0 (2026-10-29); pyo3-async-runtimes 0.30.0 | 312M downloads | Clean | Build/wheel pipeline in Docker |

Source for every version, date and download count in the table: `https://crates.io/crates/<crate name>`. Python side: https://pypi.org/project/mcp/ (latest 2.3.0, 2026-10-02), https://pypi.org/project/onnxruntime/
(latest 1.32.0, 2026-11-13).

## Notes

### MCP: rmcp

- **Official and Tier 1.** The MCP docs list the Rust SDK as Tier 1, the same tier as Python
  (https://modelcontextprotocol.io/docs/sdk). Release cadence is high: 3.5.1 through 3.9.0 shipped between
  2026-10-05 and 2026-11-05 (https://github.com/modelcontextprotocol/rust-sdk/releases), and 3.11.0 followed on
  2026-11-17.
- **Spec version.** rmcp serves the 2026-07-28 revision statelessly by itself: no `Mcp-Session-Id`, no
  standalone GET stream. `legacy_session_mode` only governs clients below 2026-07-28. It also implements
  Multi-Round-Trip Requests (https://github.com/modelcontextprotocol/rust-sdk). The Python SDK v2 also ships
  the stateless transport (https://newsletter.victordibia.com/p/mcp-july-2026-update-stateless-core). There is
  no spec gap against `mcp` 2.2.
- **Transport and auth.** `StreamableHttpService` is a tower service that is nested into an axum router, with
  `with_json_response(true)`
  (https://eseospace.com/blog/build-mcp-server-rust). Bearer auth is ordinary axum middleware. The tool
  handler reads the HTTP `request::Parts` and its extensions from `RequestContext.extensions`
  (https://www.shuttle.dev/blog/2025/08/13/sse-mcp-server-with-oauth-in-rust). This matches today's pattern of
  passing `conn`/`auth` through `scope["state"]`.
- **Low-level control.** HLMemo bypasses the Python decorator API so that schemas go out verbatim and errors use
  its own envelope. In rmcp the equivalent is a hand-written `ServerHandler::list_tools` / `call_tool` that
  returns our own `Tool` JSON instead of `#[tool]` macros with schemars. This works, but it is the less
  documented route.
- **Gaps we write.**
  - *The per-request transaction.* Today the middleware opens the transaction and the tool runs inside it. In
    Rust the open transaction has to travel through request extensions, for example an
    `Arc<Mutex<Transaction>>` or a connection checked out from the pool. This is a design task, not a library
    gap.
  - *Byte-exact canonical JSON.* `budget.used` counts the exact bytes on the wire. `serde_json` maps are sorted
    and do not escape non-ASCII, which matches `sort_keys=True, ensure_ascii=False`. Float formatting differs,
    though: Python writes `1e-05` and `1e+16`, while ryu writes `1e-5` and `1e16`. Integers and plain decimals
    match. We need a custom float formatter or a golden-byte test over real responses.
- **Security parity.** The unbounded-request-body DoS class hit rmcp too (CVE-2026-63128, fixed 2026-09-16). The
  Python fix landed in 1.29.1 / after 2.0.1
  (https://dev.to/razashariff/the-first-of-its-kind-how-one-unbounded-request-body-became-a-denial-of-service-class-across-every-38).
  In Rust we keep an explicit `tower_http::limit::RequestBodyLimitLayer`, as `max_request_body_size` does today.

### Embeddings: ort vs fastembed-rs vs candle

- **ort** is a safe wrapper over the same ONNX Runtime C library that the Python wheel wraps. Its crate
  description reads "wrapper for ONNX Runtime 1.31" (https://crates.io/crates/ort), and it is still
  `2.0.0-rc.15`. That release fixed "over 30 instances of undefined behavior" and memory leaks
  (https://github.com/pykeio/ort/releases). It is community-run, not a Microsoft product, and rc.14 made the
  `Environment` explicit (a breaking change between RCs)
  (https://aitamer.news/posts/ort-2-0-rc-14-explicit-environment).
  - Multiversioning (`api-NN` features) lets one `ort` build use older ORT minors
    (https://github.com/pykeio/ort/releases/tag/v2.0.0-rc.12).
  - `load-dynamic` + `ORT_DYLIB_PATH` loads a chosen `libonnxruntime` at runtime
    (https://ort.pyke.io/setup/linking).
- **Parity with onnxruntime-python.** Bit-for-bit output is plausible only with the same ORT version, the same
  CPU kernels (ISA dispatch), the same thread count and the same graph-optimisation level. Pin the official
  Microsoft 1.30 C build with `load-dynamic`.
  - **Tokenizer ids:** identical, because the Python `tokenizers` package is a binding of the same Rust crate.
  - **Pooling and L2:** our own code. Summation order is visible at the last ulp, so expect a tolerance of
    about 1e-6 rather than equality.
  - **Practical rule:** measure on a fixed corpus before switching; the gate is cosine ≥ 0.99999 and identical
    top-k on the retrieval golden set. Existing stored vectors stay valid only if this holds. Otherwise
    re-embed, which is cheap at e5-small scale.
  - Cross-language ORT differences have been reported when pre-processing differed
    (https://github.com/microsoft/onnxruntime/issues/13614).
- **fastembed-rs** 7.7.1 (2026-11-16) wraps ort + tokenizers and lists `MultilingualE5Small`
  (https://docs.rs/fastembed/latest/fastembed/enum.EmbeddingModel.html). It does not cover HLMemo's
  token-budgeted batching, `models.lock` hash check, `enable_cpu_mem_arena=False` or memory sampling, so it saves
  little. Use ort directly; the Python embedder core is about 100 lines.
- **candle** 0.11.1 (2026-10-31) is pure Rust (BERT-family models in candle-transformers), but it uses its own
  kernels, so vectors match only within tolerance and stored embeddings would likely need a re-embed. One vendor
  measured the ONNX path as clearly faster than candle on the same MiniLM weights
  (https://manticoresearch.com/blog/onnx-embeddings-speedup). The same post notes that ORT's C `Run()` is
  thread-safe, so one session can be shared.

### tiktoken o200k_base

**tiktoken-rs** 0.12.1 bundles `o200k_base.tiktoken` in the crate; we checked the published crate, which
contains `assets/o200k_base.tiktoken`. Its `CoreBPE::encode` "mirrors upstream tiktoken"
(https://github.com/zurawiki/tiktoken-rs). Both use the same BPE algorithm and regex, so counts should be exact.
Prove it with a parity test over the brief/budget fixtures. The whole `brief/vocab.py` cache-and-warm logic
(the macOS `$TMPDIR` purge workaround) disappears, because no download is needed. GitHub's **bpe-openai** is a
faster alternative (https://github.com/github/rust-gems/tree/main/crates/bpe-openai).

### Postgres, pgvector and migrations

- **sqlx** fits best: raw SQL like today, a built-in pool, migrations, and optional compile-time-checked queries
  (which need a DB or offline metadata at build time). **tokio-postgres + deadpool-postgres** is the
  lower-level alternative and is closer to psycopg's style. **diesel** 2.4.0 / diesel-async is ORM-first and a
  poor match for hand-written SQL. **pgvector-rust** supports all three
  (https://github.com/pgvector/pgvector-rust).
- **Gap: placeholders.** Neither sqlx nor tokio-postgres supports psycopg's `%(name)s` named parameters, so every
  query is rewritten to `$1…$n`. This is mechanical but touches every query file, and positional mistakes there
  are silent. Advisory locks, `FOR SHARE` and `SET LOCAL` are plain SQL and port unchanged.
- **Taking over the 11 alembic revisions.**
  - The revisions are already raw SQL strings with thin Python glue: `lock_timeout` handling and an
    `autocommit_block` for `CREATE INDEX CONCURRENTLY`.
  - Plan: freeze the schema at `0011` as one baseline SQL migration, then mark it as applied on prod without
    running it. sqlx's `Migrator` can skip pending migrations up to a version and record them as applied
    (https://docs.rs/sqlx/latest/sqlx/migrate/struct.Migrator.html). refinery has `Target::Fake` /
    `FakeVersion` for the same job (https://docs.rs/refinery/latest/refinery/enum.Target.html).
  - Afterwards drop `alembic_version`.
  - The deferred `hnsw` branch has no equivalent in a linear tool: it becomes a separate no-transaction script
    or a second migrator table.
  - This step is a one-way door on prod data and needs the dual review.

### LLM providers

- **Anthropic:** official SDKs exist for Python, TypeScript, C#, Go, Java, PHP and Ruby, but not Rust
  (https://platform.claude.com/docs/en/cli-sdks-libraries/overview).
- **Google:** the GenAI SDK covers Python, JS/TS, Go and Java (with C# listed), but not Rust
  (https://ai.google.dev/gemini-api/docs/libraries).
- **OpenAI:** no official Rust SDK; there is an open request for one
  (https://github.com/openai/openai-go/issues/628).
- HLMemo already calls all three providers with raw httpx, so nothing is lost. We port our own request and
  response structs onto reqwest + serde, with retries via reqwest-retry or backon. async-openai can cover
  OpenRouter's OpenAI-compatible API if wanted.

### Other dependencies

- **clap** replaces typer one-to-one.
- **axum + tower-http** replace starlette + uvicorn, and Rust needs no separate ASGI server.
- **jsonschema** supports `iter_errors` with instance paths (https://docs.rs/jsonschema/latest/jsonschema/).
  Error text differs from Python's, which matters only if prompts feed validator messages back to the model.
- **keyring** v4 moved the API into `keyring-core` and each credential store into its own crate; applications
  should depend on the core plus the stores they need
  (https://github.com/open-source-cooperative/keyring-rs).
- **Config:** prefer `config` over `figment`, which has had no release since 2024-05. Validation constraints
  like `gt=0` become garde/validator attributes or hand-written checks. pydantic's error messages and coercion
  rules are not reproduced.

### Partial port with PyO3/maturin

PyO3 0.30 and maturin 1.16 are mature, and PyO3 defaults to free-threading-safe modules
(https://pyo3.rs/v0.30.1/free-threading). Async Rust can be bridged with pyo3-async-runtimes. That said, the
obvious hot paths are already native code behind Python: ONNX Runtime, HF tokenizers and tiktoken are all
C++/Rust inside their wheels. A PyO3 module therefore buys little unless C-performance.md finds Python-side
pooling or batching, JSON metering or retrieval merging dominating. A mixed build would also add a Rust
toolchain to the Docker image and the release path.

## Bottom line

- **Map cleanly (12):** rmcp, sqlx/tokio-postgres, pgvector, ort, tokenizers, tiktoken-rs, clap, reqwest,
  axum/tower-http, jsonschema, keyring, serde.
- **Partial (3):**
  - alembic → sqlx/refinery: a baseline plus the hnsw branch.
  - pydantic-settings → config + garde: validation by hand.
  - MCP low-level mode: our request-scoped transaction and canonical JSON bytes.
- **We write ourselves:** the provider clients (as today), the e5 batching, pooling and hash lock, a
  JSON float formatter matching Python, the `%(name)s` → `$n` rewrite of every query, and the baseline cut-over.
- **Biggest risk:** silent behavioural drift, not a missing crate. The likely sources are embedding vectors
  that differ in the last bits (ort RC, ORT version, kernels), changed `budget.used` bytes from float formatting,
  and positional-parameter mistakes in about 60 query files. Each one needs a golden parity test against the
  Python system before cut-over. The most fragile dependency is `ort` still being an RC with breaking changes
  between candidates.
