# Phase 0 gate report

Status: **G7 + G8 + O2 + S4b verified live on 2026-09-22 (round 10).** G1/G3/G4/G6 carry their last
measured values and are labelled as such — they were not personally re-run in this session.

- Date of this round: 2026-09-22, 18:00–18:30 UTC (round 8/9 code changes in the tree)
- Hardware: Apple M3 Pro (12 cores, 36 GB), macOS 26.6.2 (Darwin 25.6.0 arm64), Docker 29.8.0 /
  Compose v5.5.1, Python 3.12 via `uv run --frozen`, images `hlmemo:dev`
  (python:3.12.14-slim-bookworm) + `pgvector/pgvector:0.8.6-pg17`
- Commit under test: `be237f1` plus the working-tree changes listed under "Changes in this round"
- Stack: `docker compose up -d --wait db migrate api worker` — api on **http://127.0.0.1:8765**
  (`/health` 200, `/ready` 200), db on **127.0.0.1:5432** (loopback only, see S4b).
  Embedding model **baked into the image** (`HLM_BAKE_MODELS=1`, no host bind mount); `/ready`
  hashes the files against `models.lock` before the api is healthy.
- Secrets: `HLM_ADMIN_TOKEN` lives only in `.hlm-dev.env` (gitignored, 0600, compose `env_file`).

## Gate table

| Gate | Status | Measured value | Command | Date | Hardware |
|---|---|---|---|---|---|
| G1 boot | **last measured 2026-09-22 (PASS)** — not re-run this round | api healthy in <30 s (`--wait` total 25.8 s incl. build); `test_g1_boot.py` owned by a concurrent process this round, deliberately not executed | `HLM_TEST_DSN=…/hlm_o2 uv run --frozen pytest -q tests/integration/test_g1_boot.py` | 2026-09-22 (round 7) | M3 Pro |
| G2 budget | **PASS (re-run 2026-09-22 18:15–18:27 UTC)** | all budget + wire assertions green inside the combined `69 passed in 726.76s` run; the 58/1000 `memory.raw` → `E_BUDGET_TOO_SMALL` split (D-026, still open) is the last round's instrumented figure, not re-instrumented here | `HLM_TEST_DSN=…/hlm_o2 uv run --frozen pytest -q tests/integration/test_g2_budget.py tests/integration/test_g2_wire.py` | 2026-09-22 | M3 Pro |
| G3 recall | **last measured 2026-09-22 (PASS)** — not re-run (needs `hlm_retr`, which is read-only for this session) | mean Recall@5 = **0.930** ≥ 0.90 (100 queries, 11 574 chunks, db `hlm_retr`) | `HLM_TEST_DSN=…/hlm_retr uv run --frozen pytest -q tests/integration/test_g3_recall.py` | 2026-09-22 (commit c1a7849) | M3 Pro |
| G4 latency | **last measured 2026-09-22 12:26 UTC (PASS)** — not re-run (needs `hlm_retr`) | p50 250 ms, **p95 370.6 ms** ≤ 500, p99 552 ms, 3 callers, 300 queries | `HLM_TEST_DSN=…/hlm_retr uv run --frozen pytest -q tests/integration/test_g4_latency.py` | 2026-09-22 12:26 UTC | M3 Pro, pg 17.11, pgvector 0.8.6 exact scan |
| G5 isolation | **PASS (re-run 2026-09-22 18:15–18:27 UTC)** | 0 leaks across auth / integrity / read-leak suites inside the combined `69 passed in 726.76s` run; status gate 401/403 | `HLM_TEST_DSN=…/hlm_o2 uv run --frozen pytest -q tests/integration/test_g5_auth.py test_g5_integrity.py test_g5_read_leaks.py` | 2026-09-22 | M3 Pro |
| G6 durability | **PARTIAL re-run 2026-09-22 18:15–18:27 UTC** — `test_g6_replay.py` + `test_commit_durability.py` + `test_worker_fencing.py` green; `test_g6_write.py` / `test_g6_commit_ack.py` owned by a concurrent process, **last measured 2026-09-22** | duplicate `request_id` → 1 effect; revision conflict explicit; replay rebuild equal | `HLM_TEST_DSN=…/hlm_o2 uv run --frozen pytest -q tests/integration/test_g6_write.py` | 2026-09-22 (commit cf44967) | M3 Pro |
| G7 clients | **PASS (re-run 2026-09-22 18:12–18:15 UTC)** | claude 2.1.280, codex 0.155.1, agy 1.2.8 — each: `tools/list` → `memory.write` → `memory.query` → `memory.drilldown` → `memory.raw`, all `outcome=ok` in the api log. `SMOKE_STRICT_VERSIONS=1`. **`3 passed in 134.14s`** | `HLM_DEVICE_TOKEN=hlm_… HLM_PROJECT=g7-smoke SMOKE_STRICT_VERSIONS=1 uv run --frozen pytest -q -s tests/integration/test_g7_clients.py` (= `make test-g7`) | 2026-09-22 18:12–18:15 UTC | M3 Pro, compose stack |
| G8 secrets | **PASS (re-run 2026-09-22 18:25 UTC)** | gitleaks 8.30.1: no leaks found; `.env`, `.hlm-dev.env`, `models/` untracked and gitignored. **`3 passed in 12.04s`** | `uv run --frozen pytest -q tests/integration/test_g8_secrets.py` (= `make test-g8`) | 2026-09-22 18:25 UTC | M3 Pro, gitleaks 8.30.1 |
| O2 worker crash/restart | **PASS (new, 2026-09-22 18:08–18:11 UTC)** | `test_worker_sigkill_after_vector_inserts_restarts_and_reclaims`: **`1 passed in 181.19s (0:03:01)`** — see "O2 evidence" | `HLM_O2_RESTART=1 uv run --frozen pytest -q -s tests/integration/test_worker_restart.py` (= `make test-o2`) | 2026-09-22 18:08–18:11 UTC | M3 Pro, isolated compose project `hlmemo-o2` |
| S4b db bind | **PASS (2026-09-22 ~18:00 UTC)** | `db` recreated; published port is now `127.0.0.1:5432->5432/tcp`; not reachable on the LAN interface — see "S4b verification" | `docker compose up -d --force-recreate --wait db` | 2026-09-22 | M3 Pro |

**Never run the integration suite against the `hlm` database** the api uses, nor against `hlm_retr`
(11 574 cached embeddings, ~12 min to rebuild): `tests/conftest.py` has an autouse fixture that
truncates every table and deletes every device except id 1. This round used `hlm_o2`.

## O2 evidence — worker crash / restart (codex final review 09 §4)

Test: `tests/integration/test_worker_restart.py::test_worker_sigkill_after_vector_inserts_restarts_and_reclaims`
(opt-in: `HLM_O2_RESTART=1`, or `make test-o2`). Result: **`1 passed in 181.19s (0:03:01)`**.

Setup, exactly as specified: isolated Compose project **`hlmemo-o2`** with its own `pgdata` volume
on a free loopback port (never the dev stack, never `HLM_TEST_DSN`), **real baked models**
(`HLM_BAKE_MODELS=1`), **one** worker under `restart: unless-stopped`, warmed for 10 s by embedding
two seed versions with the real E5 model (which also produces the reference vectors).

What the run proved, in order:

1. **Barrier reached inside the fenced transaction, after both inserts.** A persisted, one-shot
   barrier (`hlm_test_barrier`, claimed on an independent autocommit connection) fires immediately
   before `_mark_done`. Log: `TEST BARRIER 'o2-before-mark-done' claimed by pid 1: holding job 3
   before _mark_done`. `hit_pid = 1` proves the worker is PID 1.
2. **The window is real.** An independent connection saw job 3 `status='running'`, `attempts=1`,
   `lease_token` set, and **zero** committed embeddings for the revision's chunks.
3. **PID-1 self-destruct, no manual restart.** `TEST BARRIER … released, action=sigkill`.
   Measured kernel behaviour: `kill(1, SIGKILL)` from inside the container is *silently dropped* —
   a PID namespace's init is `SIGNAL_UNKILLABLE` and ignores every default-action signal sent from
   within its own namespace, self-sent included (verified separately: the process just kept
   running). `worker.main._self_destruct` therefore attempts the real `SIGKILL` first and then
   forces a fault, which `force_sig_info_to_task` handles by clearing `SIGNAL_UNKILLABLE` — the
   process dies immediately (exit 139), no cleanup, transaction abandoned.
4. **Docker restarted it on its own.** `RestartCount` incremented (asserted `>` the pre-crash
   value); the container came back at 18:09:14 and logged `worker: polling db:5432/hlm`.
   No `docker restart` / `docker start` was issued at any point.
5. **The job was reclaimed and completed within `LEASE_SECONDS + 60`** (180 s; the lease alone is
   120 s): `status='done'`, **`attempts = 2`**, `lease_token IS NULL`, `lease_until IS NULL`,
   `done_at` set, `last_error IS NULL`.
6. **Exactly one embedding per (chunk, model, model_revision, preproc_version)** for the revision —
   two chunks, one row each.
7. **Vectors correct.** The copyable chunk (identical `text_norm` on the superseded version) got
   the predecessor vector **byte-for-byte** (§1.1 (5), no re-inference). The novel chunk was really
   inferred and matches a reference embedding of the same text (max abs delta < 1e-5, 384 dims),
   and differs from the copied vector.
8. **No duplicate job** for the revision version, and still exactly **one** worker container.

### Heartbeat

`worker/main.heartbeat` emits at most one line per `HEARTBEAT_SECONDS` (10 s):

```
worker heartbeat: last_done_job=2 last_done_at=2026-09-22T18:09:01.322216+00:00 ready_jobs=0 \
  oldest_ready_age_s=None in_flight_jobs=0 oldest_in_flight_age_s=None jobs_done_this_process=2
```

Codex asked for last committed job id + timestamp, ready-job count and oldest-ready age. Those
alone did **not** make the O2 stall visible: after the crash the job is `running` under a *live*
lease, so `ready_jobs` stays 0 for the full 120 s and the queue reads as idle. The heartbeat
therefore also carries `in_flight_jobs` / `oldest_in_flight_age_s`, and the live run shows the
stall exactly as it should:

```
18:09:14  … ready_jobs=0 … in_flight_jobs=1 oldest_in_flight_age_s=1.761   jobs_done_this_process=0
18:10:14  … ready_jobs=0 … in_flight_jobs=1 oldest_in_flight_age_s=62.151  jobs_done_this_process=0
18:11:04  … ready_jobs=0 … in_flight_jobs=1 oldest_in_flight_age_s=112.485 jobs_done_this_process=0
```

Idle is `ready_jobs=0 in_flight_jobs=0`; a stall is a backlog whose age grows while
`last_done_job` does not move. Covered by
`tests/integration/test_worker_heartbeat.py::test_heartbeat_reports_stalled_queue_then_last_committed_job`
(`1 passed in 0.72s`) for the content and the rate limit, and by the O2 test for the live container
(`in_flight_jobs=1` must appear in the restarted worker's log).

The barrier hook is **off by default**: `worker/main._test_barrier` returns on the first line unless
`HLM_WORKER_TEST_BARRIER` names a barrier row, and `compose.yaml` passes
`HLM_WORKER_TEST_BARRIER: "${HLM_WORKER_TEST_BARRIER:-}"` (empty ⇒ inert) to the worker only.
A missing `hlm_test_barrier` table is also a no-op.

## S4b verification — the dev `db` no longer publishes on all interfaces

The `127.0.0.1` bind added to `compose.yaml` in round 7 had never been applied to the *running*
container (it still showed `0.0.0.0:5432->5432/tcp`). Recreated in place, volume preserved:

```
$ docker compose up -d --force-recreate --wait db      # Container hlmemo-db-1 Recreated / Healthy
$ docker compose ps                                    # db  running  127.0.0.1:5432->5432/tcp
```

Data survived the recreate (`hlmemo_pgdata` untouched):

```
$ psql postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr -tAc 'select count(*) from embeddings'
11574          # identical to the pre-recreate count
```

Not reachable from a non-loopback interface (host IP `192.168.1.98` on `en0`):

```
$ nc -z -v -w2 $(ipconfig getifaddr en0) 5432
nc: connectx to 192.168.1.98 port 5432 (tcp) failed: Connection refused      [exit 1]
$ PGCONNECT_TIMEOUT=3 psql postgresql://hlm:hlm@192.168.1.98:5432/hlm -tAc 'select 1'
psql: error: connection to server at "192.168.1.98", port 5432 failed: Connection refused   [exit 2]
$ nc -z -v -w2 127.0.0.1 5432                                                 # control
Connection to 127.0.0.1 port 5432 [tcp/postgresql] succeeded!                 [exit 0]
```

## G7 evidence per CLI (round 10)

Pinned versions re-pinned to what is actually installed (`tests/smoke/VERSIONS`): **claude 2.1.280,
codex 0.155.1, agy 1.2.8**. Both claude (2.1.278 → 2.1.280) and agy (1.1.4 → 1.2.8) had self-updated
since the first pin. The run used `SMOKE_STRICT_VERSIONS=1`, so a drift would have **failed** the
gate rather than warned; `hlm doctor` now prints `OK` for all three.

The smoke prompt (`tests/smoke/_lib.sh`) asks for write → query → drilldown(`v<version_id>`) →
raw(`version_id`) plus verbatim `CALLED memory.<tool>` lines and a unique marker; each script checks
the CLI stdout *and* the api log (`outcome=ok`). `initialize` is not logged by the MCP SDK at INFO;
it is evidenced by the per-session `tools/list` that immediately follows it.

**claude 2.1.280** — `claude -p "<prompt>" --allowedTools mcp__hlm__memory.write,…query,…drilldown,…raw`
- tools called: `memory.write`, `memory.query`, `memory.drilldown`, `memory.raw` — all four in stdout
  with the marker `hlm-smoke-claude-20260922T181231Z`; outcome **PASS**, **exit 0, 36 s wall**
  (pytest case 35.70 s)
- api log: `18:12:31 tools/list client=claude-code/2.1.280 (sdk-cli)` (×5 across the session, the
  first one proving initialize+list), then `memory.write … outcome=ok ms=301`,
  `memory.query … ok ms=518`, `memory.drilldown … ok ms=9`, `memory.raw … ok ms=8`
- no retries needed

**codex 0.155.1** — `codex exec -C <root> --skip-git-repo-check "<prompt>"`, token via `HLM_DEVICE_TOKEN`
- tools called: all four, marker `hlm-smoke-codex-20260922T181304Z`; outcome **PASS**,
  **exit 0, 70 s wall** (pytest case 69.73 s)
- api log: `18:13:04 tools/list client=codex-mcp-client/0.155.1`, then `memory.write … ok ms=14`,
  `memory.query … ok ms=22`, `memory.drilldown … ok ms=9`, `memory.raw … ok ms=8`
- no retries needed

**agy 1.2.8** — `agy --add-dir <root> --print "<prompt>"`, config `~/.gemini/config/mcp_config.json`
- tools called: all four, marker `hlm-smoke-agy-20260922T181414Z`; outcome **PASS**,
  **exit 0, 29 s wall** (pytest case 28.70 s)
- api log: `18:14:15 tools/list client=Go-http-client/1.1` (×5), then `memory.write … ok ms=9`,
  `memory.query … ok ms=16`, `memory.drilldown … ok ms=8`, `memory.raw … ok ms=7`
- no retries needed; the 1.1.4 timeout recorded in round 7 no longer applies — the pin is now 1.2.8

Pytest summary: **`3 passed in 134.14s (0:02:14)`**.

### MCP configs — untouched beyond what `hlm mcp add` writes

All three user configs were backed up to the session scratchpad before the run and diffed after:

- `~/.codex/config.toml` — **byte-identical** to the pre-run backup
- `~/.gemini/config/mcp_config.json` — **byte-identical** (10 pre-existing servers + `hlm`)
- `~/.claude.json` — MCP content unchanged: the global `mcpServers` list is identical
  (chrome-devtools, context7, gemini-wrapper, morph-mcp, notebooklm-mcp, playwright,
  sequential-thinking, serena, tavily-remote-mcp), every project's `mcpServers` set is identical,
  and the HLMemo project still holds
  `hlm = {type: http, url: http://127.0.0.1:8765/mcp, headers.Authorization: Bearer hlm_…}`.
  The only textual diff is Claude Code's own `tengu_*` feature-flag cache, which it rewrites on
  every launch.

No config needed re-adding, so no restore from backup was necessary.

## G8 evidence

```
$ uv run --frozen pytest -q tests/integration/test_g8_secrets.py   -> 3 passed in 12.04s
  (gitleaks_clean, env_untracked, secret_paths_gitignored; gitleaks 8.30.1)
$ git ls-files | grep -E '\.env$|hlm-dev|^models/'                 -> (nothing; only .env.example tracked)
```

## Round-10 re-measured gates (G2 / G5 / replay / durability / fencing)

Re-run against `hlm_o2` in this round, deliberately excluding `test_g1_boot.py`,
`test_g6_write.py` and `test_g6_commit_ack.py`, which a concurrent process owned while this ran:

```sh
export HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_o2
uv run --frozen pytest -q -p no:cacheprovider \
  tests/integration/test_g2_budget.py tests/integration/test_g2_wire.py \
  tests/integration/test_g5_auth.py tests/integration/test_g5_integrity.py \
  tests/integration/test_g5_read_leaks.py tests/integration/test_g6_replay.py \
  tests/integration/test_commit_durability.py tests/integration/test_worker_fencing.py \
  tests/integration/test_worker_heartbeat.py tests/integration/test_g7_server.py
```

Result (2026-09-22 18:15:18 → 18:27:25 UTC): **`69 passed in 726.76s (0:12:06)`** — exit 0, no
failures, no skips. That covers G2 budget + wire, G5 auth / integrity / read-leaks, G6 replay
rebuild, commit-before-ack durability, worker lease fencing (D-027 C6), the new O2 heartbeat test,
and the G7 server-side MCP tests.

## Changes in this round (working tree)

- `src/hlmemo/worker/main.py` — O2 heartbeat (`heartbeat()`, `HEARTBEAT_SECONDS`,
  `DrainStats.last_done_job_id/last_done_at`, `in_flight` counters) called once per `run_forever`
  iteration; env-gated one-shot test barrier (`BARRIER_ENV`, `_test_barrier`, `_self_destruct`)
  invoked inside the fenced transaction immediately before `_mark_done`. Inert in production.
- `compose.yaml` — `worker` gets `HLM_WORKER_TEST_BARRIER: "${HLM_WORKER_TEST_BARRIER:-}"`
  (empty ⇒ off). The `db` loopback bind was already in the file; S4b applied it to the *running*
  container.
- `Makefile` — `test-o2` target (opt-in O2 crash test), added to `.PHONY`.
- `tests/integration/test_worker_restart.py` — new, the O2 test.
- `tests/integration/test_worker_heartbeat.py` — new, the heartbeat content/rate-limit test.
- `tests/smoke/VERSIONS` — re-pinned to claude 2.1.280 / codex 0.155.1 / agy 1.2.8.
- `docs/decisions/PHASE0-GATE-REPORT.md` — this file.

## Known gaps

1. **G1, G3, G4, G6 (write path) not re-measured this round.** G3/G4 need the `hlm_retr` fixture
   database, which was read-only for this session (11 574 cached embeddings, ~12 min to rebuild).
   G1/G6-write live in test files a concurrent process owned. Their rows above are labelled
   "last measured".
2. **`kill(1, SIGKILL)` cannot kill a container's PID 1 from inside.** Confirmed experimentally
   (the process survives and keeps running). O2 therefore attempts the SIGKILL and then forces an
   unignorable fault. The *observable* outcome the gate cares about — PID 1 dies abruptly, nothing
   is committed or cleaned up, Docker's restart policy brings it back — is identical, but anyone
   reading "self-SIGKILL" in the spec should know the literal call is a no-op there.
3. **A crashed worker's job is invisible for a full `LEASE_SECONDS`.** Nothing re-leases it earlier,
   so a crash costs up to 120 s of latency for that job. The heartbeat now *reports* the stall
   (`in_flight_jobs` with a growing age) but nothing acts on it. A shorter lease with renewal, or a
   liveness token per worker, would shrink the window — deliberately out of scope for Phase 0.
4. **The heartbeat is a log line only.** Writing it to a table would need an alembic migration,
   which was outside this task's file ownership. A scraper has to read `docker compose logs worker`.
5. **`memory.raw` returns `E_BUDGET_TOO_SMALL` instead of cursor paging** (D-026) — still open, and
   still the reason 58/1000 G2 cases take that branch.
6. **agy / claude self-update.** Both binaries updated themselves between rounds. `VERSIONS` is now
   re-pinned to the installed versions and `SMOKE_STRICT_VERSIONS=1` passes, but nothing stops the
   next self-update from re-opening the drift.
7. **Test/dev database collision.** `tests/conftest.py` truncates whatever `HLM_TEST_DSN` points at
   and falls back to the *live* compose `hlm` database when it is unset. Making conftest refuse a
   DSN ending in `/hlm` (and `/hlm_retr`) is still not done.
8. **Nested Claude.** `claude -p` refuses to run inside a Claude Code session unless the
   `CLAUDECODE*` env vars are unset; `test_g7_clients.py` strips them, the bare smoke script does not.
9. **`docs/USAGE.md` quickstart** still says `export HLM_ADMIN_TOKEN=…; make up`; with the compose
   `env_file` change the token must live in `.hlm-dev.env` (a shell export is ignored).

## Stack state

Left **up** at the end of this round (`docker compose ps`):

```
SERVICE   STATE     STATUS                    PORTS
api       running   Up (healthy)              0.0.0.0:8765->8765/tcp, [::]:8765->8765/tcp
db        running   Up (healthy)              127.0.0.1:5432->5432/tcp
worker    running   Up                        8765/tcp
```

The isolated O2 project (`hlmemo-o2`) tears itself down (`down -v`) at the end of its test and
leaves nothing behind. Databases in `hlmemo_pgdata`: `hlm` (onboarding: device 2 `mac-dev` trusted,
projects `hlmemo` + `g7-smoke`), `hlm_o2` (this round's test db), `hlm_test`, `hlm_verify`,
`hlm_retr` (G3/G4 fixture, 11 574 embeddings — do not truncate), `hlm_mcp`, `hlm_full`,
`hlm_fix_read`, `hlm_fix_srv`, `hlm_fix_write`.
