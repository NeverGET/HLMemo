You are the co-architect AND now the implementer on HLMemo (we have used you as reviewer in rounds 1-7; your review docs/consults/07-codex-code-review.md is what we are fixing). Three Claude agents applied fixes for that review but were killed by a rate limit BEFORE running the test suite. The edits are on disk, unverified. Your job: finish and verify them. Work in /Users/cemalkurt/Projects/HLMemo.

CONTEXT
- Authoritative contract: docs/decisions/PHASE0-SPEC.md. Binding decisions: docs/decisions/DECISIONS.md (D-001..D-027). Your review: docs/consults/07-codex-code-review.md.
- Uncommitted work implements D-027 items: S1/S1b/C4 (read path: src/hlmemo/core/read_service.py, src/hlmemo/db/read_queries.py, new tests/integration/test_g5_read_leaks.py), C2/C3/S2/C5 (write path: src/hlmemo/core/write_service.py, src/hlmemo/db/replay.py, tests/integration/test_g6_write.py, new tests/integration/test_g6_replay.py), C1/C6/S4/O1/O2/O3 (server+infra: src/hlmemo/server/middleware.py, src/hlmemo/server/app.py, src/hlmemo/worker/main.py, compose.yaml, Dockerfile, Makefile). S3 (preflight) is already committed and green.
- The infra agent self-reported three defects in its own unfinished code: (a) its `default_model_dir()` override is redundant (the function already honors HLM_MODELS_DIR), (b) Starlette `State` stores attributes in `_state`, not `__dict__`, (c) a ruff line-length violation.
- KNOWN FAILING TEST (the only one I ran): tests/integration/test_g5_read_leaks.py::test_payload_item_links_filtered_by_endpoint_authz fails with `ToolError: items[0].links: unknown target` from write_service.py:490. Diagnosis: the write agent's S2 fix now correctly refuses to create a link to a target the writing device cannot see, so the read agent's fixture (written before S2 landed) is invalid. Fix the TEST setup (e.g. create the link while the writer is authorized, then narrow scope / read as a different device), not the S2 behaviour.

HARD RULES
- NEVER run pytest without HLM_TEST_DSN: the conftest truncates whatever DB it points at. Use `export HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_verify` (already created, safe to truncate). NEVER point it at `hlm` or `hlm_retr` for the normal suite.
- `hlm_retr` holds 11,574 cached embeddings for the G3/G4 gates (12 minutes to rebuild). Do not drop or truncate it. To run G3/G4: `HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr uv run --frozen pytest tests/integration/test_g3_recall.py tests/integration/test_g4_latency.py -q -s`.
- Postgres is already running via `docker compose up -d db`. Do not bring up api/worker unless a test needs them.
- Alembic: always `alembic upgrade phase0@head` (two heads exist by design).
- Do not git commit and do not git stash/checkout/reset: the orchestrator commits. Do not touch .env, .hlm-dev.env, hlm.toml, models/.
- Keep every existing contract in PHASE0-SPEC.md. If a fix requires a contract change, do NOT make it silently: stop and report it as a required decision.

TASK
1. Read the uncommitted diff (`git diff`, plus the two untracked test files) and the spec sections it touches.
2. Finish the work: fix the three self-reported infra defects, fix the failing test's setup, and resolve any other cross-agent inconsistency you find (the three agents could not see each other's work — expect interface drift, e.g. write_service's new optional `raw` parameter needs the MCP handler in src/hlmemo/server/tools/handlers.py to pass verbatim arguments for C5 to be real; that file was owned by nobody in this wave, so you may edit it).
3. Verify, in this order, and report the exact summary line of each:
   a. `uv run ruff check src tests` and `uv run ruff format --check src tests`
   b. `HLM_TEST_DSN=...hlm_verify uv run --frozen pytest tests/unit tests/fixtures -q`
   c. `HLM_TEST_DSN=...hlm_verify uv run --frozen pytest tests/integration -q --ignore=tests/integration/test_g3_recall.py --ignore=tests/integration/test_g4_latency.py --ignore=tests/integration/test_g2_budget.py --ignore=tests/integration/test_g7_clients.py`
   d. `HLM_TEST_DSN=...hlm_retr uv run --frozen pytest tests/integration/test_g3_recall.py tests/integration/test_g4_latency.py tests/integration/test_g2_budget.py -q -s` (G3 Recall@5 must stay >= 0.90 and G4 p95 <= 500 ms; report both numbers)
   e. `docker compose config -q` and `docker compose build api worker`
4. For EACH D-027 item (S1, S1b, C4, C2, C3, S2, C5, C1, C6, S4, O1, O2, O3) state: VERIFIED (which test proves it) / WEAK (implemented but no test proves it) / NOT DONE.

OUTPUT (write it as your final message, max ~700 words):
## Verification results (a-e with exact summary lines, plus G3/G4 numbers)
## D-027 item status table (item | status | proving test or gap)
## What I changed to finish the work (file:line, one line each)
## Remaining defects or contract questions for the orchestrator (max 5)
