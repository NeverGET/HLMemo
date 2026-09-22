You are the implementer on HLMemo again (round 10). In round 9 you adversarially reviewed your own round-8 work and found three defects. Fix them now. Work in /Users/cemalkurt/Projects/HLMemo.

SCOPE — you own ONLY these files this round:
- src/hlmemo/server/middleware.py
- src/hlmemo/server/app.py
- src/hlmemo/core/write_service.py
- src/hlmemo/db/replay.py (only if the hash versioning requires it)
- tests/integration/test_g6_commit_ack.py, tests/integration/test_g1_boot.py, tests/integration/test_g6_write.py (extend)
- a new test file if you need one
A concurrent worker owns src/hlmemo/worker/main.py, compose.yaml, Dockerfile, Makefile and tests/integration/test_worker_*.py — DO NOT touch those.

FIXES (your own round-9 findings, docs/consults/09-codex-final-review.md):

N1 — SSE withholding [middleware.py:118]. The buffer-until-commit logic must apply to FINITE responses only. For a streaming/indefinite response (the authenticated `GET /mcp` with `Accept: text/event-stream` and no `MCP-Protocol-Version` reaches persistent SSE), commit the transaction BEFORE the first body chunk is sent and then stream through without buffering. Decide and document how you detect "finite vs streaming" (content-length / response start headers / first-chunk timeout is NOT acceptable — use the response start message and its content-type/transfer semantics). Add a regression test that the SSE path delivers response headers promptly and that a finite POST still commits before acknowledgement.

N2 — hash-algorithm upgrade breaks retries [write_service.py:567]. Version the idempotency hash: record the algorithm version with the event (a new column is acceptable only if you add an Alembic migration `0003_hash_version` on the `phase0` branch; prefer storing it inside the existing event payload/resolved structure if that avoids DDL). On a retry, compare using the version recorded on the stored event, so a request written before the upgrade still replays instead of raising E_REQUEST_ID_CONFLICT. Test both directions: a stored old-style event replays; a genuinely different payload still conflicts.

N3 — readiness cache race [app.py:142]. Guard the cold-start readiness cache with an asyncio.Lock (or equivalent) so concurrent `GET /ready` calls cannot clear a half-populated cache or load the model twice. Test with concurrent cold calls asserting a single load and no false 503.

HARD RULES
- NEVER run pytest without HLM_TEST_DSN. Use `export HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_verify`. Never point it at `hlm` or `hlm_retr`.
- `hlm_retr` holds 11,574 cached embeddings (12 min to rebuild). Do not drop or truncate it.
- Alembic: `alembic upgrade phase0@head`.
- Do not git commit, stash, checkout or reset. Do not touch .env, .hlm-dev.env, hlm.toml, models/.
- Keep every contract in docs/decisions/PHASE0-SPEC.md. If a fix needs a contract change, stop and report it instead of making it.

VERIFY and report the exact summary line of each:
a. `uv run ruff check src tests` and `uv run ruff format --check src tests`
b. `HLM_TEST_DSN=...hlm_verify uv run --frozen pytest tests/unit tests/fixtures -q`
c. `HLM_TEST_DSN=...hlm_verify uv run --frozen pytest tests/integration -q --ignore=tests/integration/test_g3_recall.py --ignore=tests/integration/test_g4_latency.py --ignore=tests/integration/test_g2_budget.py --ignore=tests/integration/test_g7_clients.py --ignore=tests/integration/test_worker_fencing.py`
d. `HLM_TEST_DSN=...hlm_retr uv run --frozen pytest tests/integration/test_g3_recall.py tests/integration/test_g4_latency.py -q -s` — report G3 Recall@5 and G4 p95; they must stay >= 0.90 and <= 500 ms.

OUTPUT (final message, max ~450 words):
## Verification (a-d exact summary lines + G3/G4 numbers)
## N1 / N2 / N3: fixed — file:line, what changed, which test proves it
## How I distinguish finite vs streaming responses (3 lines)
## Anything still broken or any contract question (max 3)
