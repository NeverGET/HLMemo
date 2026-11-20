# Moving HLMemo to Rust: what it would cost and what it would buy (2026-11-20)

Asked by Cemal on 2026-11-20. Evidence: A-inventory.md (the code, package by package), B-ecosystem.md (every
dependency's Rust counterpart, versions checked live), C-performance.md (where time goes, measured or recorded).
Baseline: main 4b6345d. Prod latency figures are from the recorded e2e run of 2026-09-24 and the brief log; the
numbers marked estimated in C-performance.md stay estimates here. Independent review: REVIEW.md (no HIGH finding;
its MEDIUM and LOW findings are applied below).

## The answer in five lines
1. **Effort (estimated from reading the code, not from a pilot):** about 170 person-days for an experienced Rust developer working with a coding agent (range 120–220),
   tests included. That is the server, the CLI and the client hooks; the offline tools could stay in Python.
2. **Everything can be ported; nothing is blocked.** Every dependency has a maintained Rust counterpart. The risk is
   not a missing library but silent drift in outputs that are gated or stored (normalized text, chunk offsets, token
   counts, hashes, embeddings).
3. **What we would write ourselves** is small: a JSON number formatter that prints like Python, the embedding
   pooling (~100 lines), the LLM provider clients (we already write these by hand today), the migration takeover,
   config validation.
4. **Performance gain is small where it matters:** a query would get an estimated 15–30 ms faster out of ~233 ms seen by
   the client, the session-start brief ~0.4 s out of 3.7 s (estimated), memory 0.1–0.2 GiB per process (out of
   1.2 GiB under load, 453 MiB idle). Writes,
   imports, `memory.ask` and the librarian would not get faster: they wait on SQL, the network and LLMs.
5. **Recommendation: do not port the server now.** Most of the same gain is available in Python for a few days of
   work (list below). A Rust **client** (the hooks and `hlm`) is the one part worth considering, for single-binary
   installs on guests' machines, not for speed.

## 1. Effort, by part

| Part | What it is | Lines | Difficulty | Person-days |
|---|---|---|---|---|
| librarian | LLM review, `memory.ask` research, guards, risk judge, provider layer | 16.4k | high | 40 |
| core | retrieval, write/read services, normalize, chunking, budgets | 11.8k | high | 30 |
| server | MCP transport, auth, middleware that commits before acknowledging | 3.2k | high | 12 |
| cli | `hlm` | 5.0k | medium | 12 |
| db, importers, ops | queries, Markdown import, operator commands | 9.6k | medium | 23 |
| migrate, capture, brief, worker, auth | kit, client hooks, queue | 6.3k | low–medium | 21 |
| bench, curate | offline tools | 4.9k | could stay Python | (11) |
| shared | config, test harness, a Python-vs-Rust comparison harness, deploy | — | — | 20 |
| **total** | | **57.8k** (43k without comments) | | **~170 (120–220)** |

Read the number as calendar months, not days: about eight months of one full-time engineer. With agents writing most
of the code, the bottleneck becomes review and the parity checks, not typing. The estimate is from reading the code
(A-inventory.md), not from a pilot port; a pilot of one package would make it firm.

**The tests do not come along.** 91 of the 115 integration test files call the Python services directly, so they
cannot test a Rust binary as they are. About 40 files touch MCP/HTTP (some of them also call services directly);
those are the start of a black-box conformance suite for a Rust server. The rest are rewritten, and the 859 recorded LLM answers (cassettes) stay usable only if the
Rust side computes the same cassette keys; otherwise re-recording costs paid LLM calls.

## 2. What ports cleanly, partly, or not at all

| Status | Items (Rust crate) |
|---|---|
| **Clean** | MCP server (rmcp, the official SDK); HTTP (axum, tower-http); Postgres (sqlx or tokio-postgres) and pgvector; HF tokenizers (the Python package is a binding of the same Rust crate, so ids are identical); o200k token counts (tiktoken-rs, vocabulary embedded); ONNX inference (ort); CLI (clap); HTTP client (reqwest); JSON Schema (jsonschema); keyring |
| **Partial** | migrations (freeze the 11 alembic revisions into one baseline and mark it applied; the separate HNSW branch becomes a script); settings validation (serde + config + garde, messages written by hand); regexes with lookaround (116 uses across src, 32 lines of them in the librarian, need fancy-regex; the rest use the standard crate) |
| **Not a library problem, a parity problem** | `normalize` output stored in `chunks.text_norm` (Python `\w` and `casefold` differ from Rust's); chunk offsets stored as character positions (Rust tokenizers give byte positions); token counts over canonical JSON (Python prints `1e-05`, Rust `1e-5`); `payload_sha256` (the idempotency key of every event); cassette keys; embeddings (bit-identical only with the same ONNX Runtime build and thread count, otherwise a cheap re-embed) |
| **Stays outside Rust anyway** | Postgres and its SQL (carried over as text); the shell scripts in deploy/ and tools/; the skills and protocol docs; the hook command registered in users' `~/.claude/settings.json` (a port must keep `python -P -m hlmemo.brief.hook` working or migrate every installed client) |

Riskiest dependencies: **ort** is still a release candidate (2.0.0-rc.15) with breaking changes between candidates,
and **rmcp** is at 3.x with about weekly releases, so its API moves too. Riskiest code: `librarian/tasks/research.py` (3,675 lines, 56 regexes, Unicode folding with maps back to
original positions, heuristics tuned on real data); any drift there changes measured answer quality.

## 3. What we would write ourselves

| Item | Size | Why |
|---|---|---|
| JSON number formatting identical to Python's | small | budgets are metered on exact JSON bytes |
| e5 batching, padding, mean pooling, L2, model hash lock | ~100 lines | ort gives raw outputs; fastembed-rs saves little |
| LLM provider clients (OpenRouter, Gemini, Anthropic) | as today | no official Rust SDK from any of the three; we already use raw HTTP |
| Spend reservations with exact decimals, breakers, fallback chains | medium | today's `provider.py` logic, re-implemented |
| Parameter rewrite to `$n` (18 files use `%(name)s`, 43 use `%s`) | mechanical, error-prone | a wrong position is a silent bug |
| Migration takeover from alembic | small, but a prod data step | one-way door: two reviewers and Cemal's OK |
| Python-vs-Rust comparison harness | medium | the only real guard against drift |

## 4. Performance and use: what would change

| Path | Today | Where the time goes | With Rust |
|---|---|---|---|
| `memory.query` (client view, e2e run 2026-09-24) | p50 ~233 ms, p95 335 ms | network 95–130 ms per round trip, SQL, ~10–30% Python CPU (result packing and previews, query embedding) | −15–30 ms |
| Same, under 3 concurrent callers (local) | p50 164 ms vs 56 ms alone | one 1-CPU process; the query embedding runs on the event loop and queues callers | better, but a Python fix gives most of it |
| `memory.write` | 250–300 ms | ~30 sequential SQL statements; Python CPU < 3% | ~nothing |
| Import (1,754 items) | ~10 min, ~342 ms/item | one awaited write per item | ~nothing; batching would help in any language |
| Embedding one chunk | 85.9 ms (321 tokens, M3) | 85.7 ms of it inside ONNX Runtime (native) | ~nothing |
| `memory.ask` | p95 31 s | LLM calls | nothing |
| Librarian queue | 1.8–5.2 jobs/min | LLM calls, spend caps | nothing |
| Session-start brief | p50 3.7 s, p95 6.5 s | ~25 server calls in ≥ 7 round trips; 0.45 s client start-up (0.2 s of it the `mcp` SDK import) | −0.4 s (estimated) |
| `hlm --help` | 0.35–0.4 s | interpreter and imports | ~0.01 s |
| Memory per server process | api 1.2 GiB under load (453 MiB idle), worker ~0.9 GiB | the 448 MB embedding model, loaded twice | −0.1–0.2 GiB each |

Measured vs estimated is marked in C-performance.md; the prod CPU is assumed 1.5–2.5× slower than the M3, and there is
no per-stage timing on prod. A `py-spy` or `pg_stat_statements` run on a local stack would settle the Python/SQL split.

**Use, beyond speed.** A Rust client would install as one file, with no Python, venv or pip on the user's machine.
That matters for guests (an onboarding today means a Python setup) more than for Cemal's two machines. On the server,
Rust would bring stricter types and fewer runtime surprises, at the price of slower iteration on the librarian, which
is the part that changes every week and is tuned by measurement.

## 5. Recommendation

1. **Not now for the server.** ~170 person-days for 15–30 ms per query and no gain where users wait longest
   (`ask`, the librarian, imports). The project's aim is LLM quality (CLAUDE.md), and the port would freeze that work
   for months and put every gated output at risk of drift.
2. **Take the Python quick wins first** (a few days, each measurable):
   - run the query embedding off the event loop (`core/read_service.py:261`, as the write path already does at
     `core/write_service.py:119`), and set ONNX threads to match the 1-CPU limit;
   - speed up the preview and `normalize` loop that dominates query packing (output must stay byte-identical: it is
     gated);
   - one server-side call for the session brief instead of ~25 (estimated −0.8 s, more than Rust's estimated −0.4 s);
   - a lighter client than the full `mcp` SDK in the hooks (−0.2 s);
   - batched writes for imports.
3. **Consider a Rust client later** (`capture`, `brief`, the client half of `hlm`: roughly 15–25 person-days) if guest
   onboarding grows; it is the part where Rust changes the experience.
4. **Revisit the server port** only on a measured trigger: many concurrent users, a CPU-bound path on prod, or a
   hosting constraint. If it ever happens, port package by package behind the comparison harness, starting with the
   read path, never as one big switch.

## 6. Alternatives and costs the numbers above leave out
- **Hybrid with PyO3/maturin** (Rust modules inside the Python server). Mature tooling, but the obvious hot paths
  (ONNX Runtime, tokenizers, tiktoken) already run as native code inside their Python wheels. The one Python-CPU
  hotspot left (previews and `normalize` in query packing) is small enough to fix in Python first; if a measured
  CPU bottleneck stays after that, a PyO3 module for that one function is the cheapest Rust step, at the cost of a
  native build in the Docker image and CI.
- **Two implementations during a gradual port.** Each package moved behind the comparison harness means both
  versions are maintained until cut-over. The librarian changed in 7 of the 17 commits to src in the last 30 days, so
  a port there chases a moving target; the read path and the client hooks change least.
- **Build and deploy.** A Rust server would ship a smaller image without a Python runtime, but release builds are
  slower and CI needs a Rust toolchain and a cache; not measured here.
- **Security.** Rust removes memory-safety bugs in our own code, but HLMemo's own code is not where those live today
  (the native parts are ONNX Runtime and Postgres in both cases). The threats in the decision log are auth, scoping,
  secrets and prompt-shaped data, which a language change does not address.
- **Skills and review.** Reviewing a large Rust codebase needs Rust fluency on the human side; with agents writing
  most of it, review capacity, not writing speed, sets the pace.

## Open checks before anyone relies on these numbers
- A pilot port of one module (e.g. `core/normalize` plus chunking) with the comparison harness would turn the
  effort estimate from reading into measurement.
- Embedding parity (cosine ≥ 0.99999 and identical top-k on the golden set) under ort with ONNX Runtime 1.30.
- Prod per-stage timing for `memory.query`.
