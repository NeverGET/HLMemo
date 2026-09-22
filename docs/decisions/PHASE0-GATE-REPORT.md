# Phase 0 gate report

Status: **INTERIM (safe-pause 2026-09-22 ~13:00 UTC)** — G7 and G8 verified live in this session; G1/G2/G5/G6 were
re-launched but the run was stopped by the owner's safe-pause request before it finished (partial output only),
so they are marked PENDING here with their last recorded values and the exact resume command.

- Date: 2026-09-22 (12:34–12:58 UTC)
- Hardware: Apple M3 Pro (12 cores, 36 GB), macOS 26.6.2 (Darwin 25.6.0 arm64), Docker 29.8.0 / Compose v5.5.1,
  Python 3.12 via `uv run --frozen`, images `hlmemo:dev` (python:3.12.14-slim-bookworm) + `pgvector/pgvector:0.8.6-pg17`
- Commit under test: `799b682` + the uncommitted working-tree changes listed in "Changes made in this session"
- Stack: `docker compose up -d --build --wait db migrate api worker` — api on **http://127.0.0.1:8765** (MCP at `/mcp`,
  `GET /health` → 200), db on 127.0.0.1:5432. Embedding model **bind-mounted** (`./models:/app/models:ro`,
  `HLM_MODELS_DIR=/app/models`), not baked — Phase 0 choice, Dockerfile still says "not baked yet".
- Secrets: `HLM_ADMIN_TOKEN` lives only in `.hlm-dev.env` (gitignored, mode 0600, loaded via compose `env_file`).

## Gate table

| Gate | Status | Measured value | Command | Date | Hardware |
|---|---|---|---|---|---|
| G1 boot | **PENDING** (re-run stopped at safe-pause; last recorded: PASS) | api healthy in <30 s (`--wait` total 25.8 s incl. build); `test_api_boots_and_health_ok` last passed in commit c1a7849 run; restart-preserves-events test not yet written (test_g1_boot.py docstring) | `HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_test uv run --frozen pytest -q tests/integration/test_g1_boot.py` | 2026-09-22 | M3 Pro |
| G2 budget | **PENDING** (re-run stopped; last recorded: PASS w/ D-026 caveat) | 1000/1000 ≤ budget; 58/1000 `memory.raw` hit E_BUDGET_TOO_SMALL (D-026 TODO) | `HLM_TEST_DSN=...hlm_test uv run --frozen pytest -q tests/integration/test_g2_budget.py tests/integration/test_g2_wire.py` | 2026-09-22 (prior run) | M3 Pro |
| G3 recall | **PENDING** (not re-run this session; last recorded: PASS) | mean Recall@5 = 0.930 ≥ 0.90 (100 queries, 11 574 chunks, db `hlm_retr`) | `HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr uv run --frozen pytest -q tests/integration/test_g3_recall.py` | 2026-09-22 (commit c1a7849) | M3 Pro |
| G4 latency | **PENDING** (not re-run; last recorded: PASS) | p50 250 ms, **p95 370.6 ms** ≤ 500, p99 552 ms, 3 callers, 300 queries (HARDWARE.md) | `HLM_TEST_DSN=...hlm_retr uv run --frozen pytest -q tests/integration/test_g4_latency.py` | 2026-09-22 12:26 UTC | M3 Pro, pg 17.11, pgvector 0.8.6 exact scan |
| G5 isolation | **PENDING** (re-run stopped; last recorded: PASS) | 0 leaks / randomized cross-project probes; status gate 401/403 | `HLM_TEST_DSN=...hlm_test uv run --frozen pytest -q tests/integration/test_g5_auth.py tests/integration/test_g5_integrity.py` | 2026-09-22 (commit 478473b) | M3 Pro |
| G6 durability | **PENDING** (re-run stopped; last recorded: PASS) | duplicate request_id → 1 effect; revision conflict explicit; replay rebuild equal | `HLM_TEST_DSN=...hlm_test uv run --frozen pytest -q tests/integration/test_g6_write.py` | 2026-09-22 (commit cf44967) | M3 Pro |
| G7 clients | **PASS** (claude 2.1.278, codex 0.155.1, agy 1.2.8 — see gap on agy 1.1.4) | each CLI: initialize → tools/list → memory.write → memory.query → memory.drilldown → memory.raw, all `outcome=ok` in api log; pytest 3 passed in 132 s | `HLM_DEVICE_TOKEN=hlm_… HLM_PROJECT=g7-smoke uv run --frozen pytest -q -s tests/integration/test_g7_clients.py` (= `make test-g7`) | 2026-09-22 12:46–12:48 UTC | M3 Pro, compose stack |
| G8 secrets | **PASS** | gitleaks: 28 commits, 15.87 MB scanned, **no leaks found**; `.env`, `.hlm-dev.env`, `models/` untracked and gitignored; pytest 3 passed | `uv run --frozen pytest -q tests/integration/test_g8_secrets.py` (= `make test-g8`) | 2026-09-22 12:46 UTC | M3 Pro, gitleaks 8.30.1 |

Resume command for all PENDING gates (with the stack up, `make up`):

```sh
docker compose exec db createdb -U hlm hlm_test 2>/dev/null || true
HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_test uv run --frozen pytest -q -p no:cacheprovider \
  tests/integration/test_g1_boot.py tests/integration/test_g2_budget.py tests/integration/test_g2_wire.py \
  tests/integration/test_g5_auth.py tests/integration/test_g5_integrity.py tests/integration/test_g6_write.py tests/integration/test_g7_server.py
HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr uv run --frozen pytest -q -p no:cacheprovider \
  tests/integration/test_g3_recall.py tests/integration/test_g4_latency.py
```

**Never run the integration suite against the `hlm` database** the api uses: `tests/conftest.py` has an autouse
fixture that truncates every table and deletes every device except id 1 (this wiped the onboarding once in this
session, see "Known gaps"). Always set `HLM_TEST_DSN` to a separate database.

## Onboarding transcript (only the `hlm` CLI, tokens redacted)

```
$ hlm init --project hlmemo --device-name mac-dev
wrote /Users/cemalkurt/Projects/HLMemo/hlm.toml
$ hlm --json device register --name mac-dev --class personal
{"device":{...,"id":2,"name":"mac-dev","class":"personal","status":"pending","token_generation":1},"stored":"keyring"}
$ hlm --admin project create hlmemo --name "HLMemo"        # HLM_ADMIN_TOKEN from .hlm-dev.env in the shell
created project id=1 slug=hlmemo name=HLMemo
$ hlm --admin project create g7-smoke --name "G7 smoke"
created project id=2 slug=g7-smoke name=G7 smoke
$ hlm --admin device approve mac-dev --class personal --notes "G7 verification Mac" --grant hlmemo:write --grant g7-smoke:write
approved 2  mac-dev  personal  trusted  grants=[{'project': 'hlmemo', 'role': 'write'}, {'project': 'g7-smoke', 'role': 'write'}]
$ hlm device whoami
2  mac-dev  personal  trusted ; grants: g7-smoke:write, hlmemo:write ; scope: ['all', 'class:personal', 'device:2']
$ hlm doctor
config      server=http://127.0.0.1:8765/mcp project=hlmemo device=mac-dev
config      preflight budget=3000 timeout_s=5.0 on_failure=block
credentials present for mac-dev
server      OK http://127.0.0.1:8765/health -> ok
device      id=2 name=mac-dev status=trusted class=personal
admin       bound (device 1 accepts HLM_ADMIN_TOKEN)
db          skipped (HLM_DB_DSN not set)
cli         claude  2.1.278      OK
cli         codex   0.155.1      OK
cli         agy     1.2.8        DRIFT (pinned 1.1.4)      # was "1.1.4 OK" at 12:36 UTC; agy self-updated at 12:37 UTC
[exit 0]
$ hlm mcp add claude   -> claude: registered MCP server 'hlm' -> http://127.0.0.1:8765/mcp
$ hlm mcp add codex    -> codex: registered MCP server 'hlm' -> http://127.0.0.1:8765/mcp (export HLM_DEVICE_TOKEN ...)
$ hlm mcp add agy      -> agy: updated /Users/cemalkurt/.gemini/config/mcp_config.json (mcpServers.hlm)
```

(First onboarding at 12:36 UTC produced device id 4 / project ids 3,4 on top of leftovers from an earlier test run;
it was wiped by the conftest truncation at 12:41 and redone identically at 12:43 — ids above are the current ones.)

MCP registration verified, user configs intact (backups diffed):

- `claude mcp get hlm`: scope **local** (`~/.claude.json` → `projects["…/HLMemo"].mcpServers.hlm`, type http,
  `Authorization: Bearer hlm_…`), Status ✔ Connected; global `mcpServers` unchanged.
- `codex mcp list`: `hlm  http://127.0.0.1:8765/mcp  HLM_DEVICE_TOKEN  enabled  Bearer token`; `serena` and
  `notebooklm` untouched. Note: `codex mcp add` rewrote `~/.codex/config.toml` (section order, `30` → `30.0`
  timeouts) — semantically identical, done by codex itself.
- `~/.gemini/config/mcp_config.json`: 10 pre-existing servers byte-identical, `hlm` added
  (`{"serverUrl": ".../mcp", "headers": {"Authorization": "Bearer hlm_…"}}`), file mode 0600.

## G7 evidence per CLI (api log lines are `docker compose logs api`, device=2 is mac-dev)

Smoke prompt (tests/smoke/_lib.sh) asks for write → query → drilldown(`v<version_id>`) → raw(`version_id`) and the
verbatim lines `CALLED memory.<tool>` + a marker; the script checks CLI stdout and the api log (`outcome=ok`).
"initialize" is not logged by the MCP SDK at INFO; it is evidenced by the per-session `tools/list` that follows it.

**claude 2.1.278** — `claude -p "<prompt>" --allowedTools mcp__hlm__memory.write,…query,…drilldown,…raw`
- attempts: #1 exit 69 (`hlm mcp add claude` failed: "MCP server hlm already exists in local config" → fixed in
  mcp_register.py), #2 exit 1 (401: db had been truncated by conftest, re-onboarded), #3 **exit 0, 25 s** (write+query);
  pytest run **exit 0, 32 s** (all four tools)
- api log 12:46:41–12:46:51: `tools/list client=claude-code/2.1.278 (sdk-cli)` ×5, then
  `tools/call memory.write … outcome=ok ms=7`, `memory.query … ok ms=25`, `memory.drilldown … ok ms=7`, `memory.raw … ok ms=8`
- stdout: all four `CALLED memory.*` lines + marker `hlm-smoke-claude-20260922T124629Z`

**codex 0.155.1** — `codex exec -C <root> --skip-git-repo-check "<prompt>"`, token via `HLM_DEVICE_TOKEN` env
- attempts: #1 **exit 0, 57 s** (write+query); pytest run **exit 0, 70 s** (all four tools)
- api log 12:47:2x–12:47:51: `tools/list client=codex-mcp-client/0.155.1`, `memory.write … ok ms=8`,
  `memory.query … ok ms=31`, `memory.drilldown … ok ms=6`, `memory.raw … ok ms=3`
- stdout: all four `CALLED memory.*` lines + marker `hlm-smoke-codex-20260922T12473xZ`

**agy** — `agy --add-dir <root> --print "<prompt>"`, config `~/.gemini/config/mcp_config.json`
- **1.2.8** (the binary self-updated from 1.1.4 at 12:37 UTC, see gaps): attempt #1 **exit 0, 30 s** (write+query);
  pytest run **exit 0, 31 s** (all four tools)
- api log 12:48:18–12:48:27: `tools/list client=Go-http-client/1.1` ×5, `memory.write … ok ms=8`,
  `memory.query … ok ms=30`, `memory.drilldown … ok ms=5`, `memory.raw … ok ms=5`
- **1.1.4** (pinned; run from a copy of the pre-update binary `~/.local/bin/agy.1790080655803198000.old`,
  `SMOKE_STRICT_VERSIONS=1`): attempt #1 **FAIL, exit 1, 306 s** — 1.1.4 answered "I have launched the subagent to
  run the smoke test…" then `Error: timeout waiting for response`; **no** `tools/call` reached the api. Not retried
  (safe-pause). Open: G7 for the *pinned* agy version is unproven; either re-pin `tests/smoke/VERSIONS` to 1.2.8 or
  retry 1.1.4 with `AGY_EXTRA_ARGS` that disable sub-agent delegation.

**Preflight (D-014) real check** — `hlm --project g7-smoke claude --headless --task "what do we know about g7-smoke? …"`
exit 0 in 8 s; api log `12:44:21 tools/call memory.query device=2 client=hlm-cli outcome=ok ms=35` **before** the
CLI started; claude's reply line 1 was `<hlmemo-preflight project="g7-smoke">` (the injected block was seen), hits=0 at that time.

## G8 evidence

```
$ gitleaks git --no-banner --redact .
INF 28 commits scanned. / scanned ~15865981 bytes (15.87 MB) in 15.4s / no leaks found
$ uv run --frozen pytest -q tests/integration/test_g8_secrets.py     -> 3 passed (gitleaks_clean, env_untracked, secret_paths_gitignored)
$ git ls-files | grep -E '\.env$|hlm-dev|^models/'                   -> (nothing; only .env.example is tracked)
```

## Changes made in this session (uncommitted, working tree)

- `compose.yaml`: `HLM_MODELS_DIR=/app/models`, `./models:/app/models:ro` on api/worker/test, `env_file: .hlm-dev.env`
  (required: false); `HLM_ADMIN_TOKEN`/`HLM_REGISTRATION_SECRET` removed from `environment` (a key there overrides
  `env_file` even when unset — the first boot ran with an empty admin token because of this).
- `.gitignore`: `.hlm-dev.env`. `Makefile`: `test-g7`, `test-g8`, `smoke` targets.
- `tests/smoke/_lib.sh`, `claude.sh`, `README.md`: prompt and checks extended to all four tools; log check now
  requires `tools/call <tool> … outcome=ok`.
- New: `tests/integration/test_g7_clients.py`, `tests/integration/test_g8_secrets.py` (both override the autouse
  truncation fixture so they never touch a database).
- **src edits (blocking):**
  1. `src/hlmemo/server/mcp_server.py` — the api logged nothing per tool call, so neither the smoke scripts'
     `verify_server_log` nor this report could show tool names. Added one INFO line per `tools/call`
     (`tool, device, client, outcome, ms`; never arguments) and one per `tools/list`.
  2. `src/hlmemo/cli/mcp_register.py` — `claude mcp add` (2.1.278) refuses to overwrite an existing entry, so a second
     `hlm mcp add claude` (and every smoke run) failed with exit 69. On "already exists" it now runs
     `claude mcp remove hlm --scope local` and re-adds (token rotation). Unit tests: 167 passed.
- `hlm.toml` written by `hlm init` (untracked, no secrets). `docs/consults/07-codex-code-review.md` appeared
  untracked during the session — not from this work.

## Known gaps

1. **G1–G6 not re-verified in this session** (run stopped at safe-pause). Values above are the last recorded ones.
2. **agy pinned version**: 1.1.4 auto-updated itself to 1.2.8 at the first invocation; G7 passed on 1.2.8, the 1.1.4
   retry timed out (see above). `hlm doctor` now reports DRIFT.
3. **Test/dev database collision**: `tests/conftest.py` truncates the db it is pointed at; the compose `db`
   fixture auto-detects the compose port and, without `HLM_TEST_DSN`, hits the live `hlm` database. Use
   `hlm_test` (created this session) — consider making conftest refuse `/hlm` outright.
4. Model files are bind-mounted (Phase 0); the Dockerfile still needs the bake layer for VPS deploy.
5. `docs/USAGE.md` quickstart says `export HLM_ADMIN_TOKEN=…; make up` — with the compose change the token must be in
   `.hlm-dev.env` (shell export is ignored). USAGE.md not edited (outside this task's allowed files).
6. `memory.raw` E_BUDGET_TOO_SMALL instead of cursor paging (D-026 TODO) still open for G2.
7. Nested Claude: `claude -p` refuses to run inside a Claude Code session unless `CLAUDECODE*` env vars are
   unset; `test_g7_clients.py` strips them, the bare smoke script does not.

## Stack state

At the end of verification (before safe-pause):

```
NAME              IMAGE                          COMMAND                  SERVICE   CREATED          STATUS                    PORTS
hlmemo-api-1      hlmemo:dev                     "python -m hlmemo.se…"   api       17 minutes ago   Up 17 minutes (healthy)   0.0.0.0:8765->8765/tcp, [::]:8765->8765/tcp
hlmemo-db-1       pgvector/pgvector:0.8.6-pg17   "docker-entrypoint.s…"   db        2 hours ago      Up 2 hours (healthy)      0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
hlmemo-worker-1   hlmemo:dev                     "python -m hlmemo.wo…"   worker    17 minutes ago   Up 17 minutes             8765/tcp
```

Safe-pause: `docker compose down` (volumes kept: `hlmemo_pgdata` holds databases `hlm` (onboarding: device 2
mac-dev trusted, projects hlmemo + g7-smoke), `hlm_test`, `hlm_retr` (G3/G4 fixture), `hlm_mcp`, `hlm_full`).
Resume with `make up` — `.hlm-dev.env` rebinds the same admin token, the mac-dev device token is in the macOS
keychain (`hlm doctor` should print all OK, agy DRIFT).

After `docker compose down` (2026-09-22T12:56:33Z):

```
NAME      IMAGE     COMMAND   SERVICE   CREATED   STATUS    PORTS
local     hlmemo_pgdata
```
