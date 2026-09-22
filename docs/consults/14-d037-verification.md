# D-037 implementation verification — fix/d037

Owner request: all 15 source fixes in isolated fix-d037 worktree; no commit; no changes to
`deploy/`, `Makefile`, or `tests/deploy/`. G3/G4 only once at end; restore HARDWARE.md.

Environment: `HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models`; PostgreSQL on localhost:5432.
Created only `hlm_d037` through the `postgres` maintenance database. Normal pytest always specifies
`HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_d037`. R1 reproduction and final retrieval
runs use only the two exceptions explicitly authorized in the task.

Focused verification (actual DB runs):
- Read pagination + existing isolation: `12 passed in 1.70s`.
- Middleware/lifetime: `31 passed in 4.28s`, including 10/10 revoke successes with 64 waiting
  requests and both normal slots occupied; admin-revoke and self-revoke alternate.
- Write/replay/scope: `59 passed in 5.18s`.
- App/readiness/pins/transport: `25 passed in 3.10s`; real SDK accepts 50 × 64,000 supplementary
  Unicode bodies both as UTF-8 and surrogate-pair JSON escapes (approximately 13 MB / 38 MB).

Acceptance A1 (root, sequential reproduction suite):

```sh
PYTHONPATH=. HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_r1judge uv run --frozen pytest docs/bakeoff/r1/repro -q -p no:cacheprovider
```

```text
17 passed in 5.28s
```

Acceptance A2 (separate native worker, twice):

```sh
HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_d037 uv run --frozen pytest tests/unit tests/fixtures tests/integration -q -p no:cacheprovider --ignore=tests/integration/test_g3_recall.py --ignore=tests/integration/test_g4_latency.py --ignore=tests/integration/test_g2_budget.py --ignore=tests/integration/test_g7_clients.py --ignore=tests/integration/test_worker_restart.py
```

```text
409 passed in 49.64s
409 passed in 48.61s
```

Both complete logs: `/tmp/d037-full-1.log`, `/tmp/d037-full-2.log`. Root inspected final summaries.

A3 root check and separate worker final recheck:

```sh
uv run ruff check src tests/unit tests/integration tests/fixtures
uv run ruff format --check src tests/unit tests/integration tests/fixtures
```

```text
All checks passed!
100 files already formatted
```

A4 final single G3/G4 run (root):

```sh
PGOPTIONS='-c default_transaction_read_only=on' HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr uv run --frozen pytest tests/integration/test_g3_recall.py tests/integration/test_g4_latency.py -q -s
```

```text
[G3] overall: Recall@5 = 0.930 (93/100)
[G4] 300 queries / 3 callers: p50=189.8 ms p95=276.2 ms p99=468.7 ms mean=203.5 ms max=507.8 ms wall=20.4s (14.7 q/s)
4 passed in 45.87s
```

TR .971; DE .909; EN .909; identifiers 25/25. Before running, a read-only `_state`
check confirmed the cached retrieval fixture and 11,574 embeddings. PGOPTIONS enforced
read-only transactions for the actual gate. HARDWARE.md was backed up and restored byte-for-byte;
measured copy is `/tmp/hlm-d037-HARDWARE-measured.md`, full log `/tmp/hlm-d037-g3-g4.log`.
This was the only G3/G4 invocation in the task.

Native independent reviews: read worker reviewed root API/DB/SDK; middleware worker reviewed
write/replay; write worker reviewed middleware. Exchanges and scoped findings are in sibling
14-d037-{app,read,write,middleware}.md files. This is not an Opus adversarial review; D-036's
external Opus gate remains for the orchestrator before merge.

Contract notes: §3 keeps 50 items and 64,000-character bodies. SDK's supported
`max_request_body_size` and the app now share 64 MiB; the explicit complete-wire cap includes
metadata/whitespace. Raw edge overlap and raw/drilldown link paging are documented in §1.1,
§3 and §4.4. Project cards require `device_scope="all"`. Append-only D-037 records these decisions.

Final scope verification: `git diff --check` clean; no diff in deploy/, Makefile, tests/deploy/, or HARDWARE.md. HEAD remains 5c1448560fe61806c85af40530ebd184bf97b8e4; no commit, push, deploy, or main-checkout edits.
