# fix/body OOM follow-up — verification checkpoint

Updated: 2026-09-23. Owner: root implementer. No commit or deploy performed.

Scope: only src/, tests/, deploy/compose.prod.yaml, deploy/RUNBOOK.md,
docs/decisions/PHASE0-SPEC.md. Checkpoint lives here to respect that allowlist.

Implementation: one lifespan-created API embedder, injected into all MCP reads and
readiness; ORT CPU arena disabled, configured intra-op threads default 2 for API
and worker. Body-read specification synchronized with the existing implementation.

Design consultation (native worker oom_smoke): explicit ReadDeps injection preserves
cursor-secret identity and avoids a hidden lazy fallback. Reviewed all three MCP
read paths and inference tokenizer usage; no configuration mutation during inference.
Independent final reviewer spec_sync found no blocking defect; the reported Handler
signature mismatch was fixed with ReadHandler Protocol and a narrowed cast. Sizing,
YAML merge, tmpfs ownership, arithmetic and allowlisted scope independently reviewed.
This is native review, not an Opus ratification.

Acceptance evidence (local ignored *.log files alongside this checkpoint):
- oom-targeted.log: 35 passed in 3.17s (readiness/lifecycle).
- oom-r1.log: 17 passed in 8.48s. Required HLM_TRUSTED_PROXY_IPS=172.18.0.0/16,
  PYTHONPATH=src:., HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_r1judge.
- oom-full.log: 479 passed in 103.92s (0:01:43), normal suite run once on hlm_body.
  Command: pytest tests/unit tests/fixtures tests/integration -q -p no:cacheprovider;
  explicit ignores: test_g2_budget.py, test_g3_recall.py, test_g4_latency.py,
  test_g7_clients.py, test_worker_restart.py under tests/integration. Cached fixture
  gates and external CLI/restart harnesses are separate from the normal suite.
  Includes two real lifespan/MCP constructor-count cases and four ORT/worker cases.
- oom-g34.log: [G3] overall: Recall@5 = 0.930 (93/100).
  [G4] 300 queries / 3 callers: p50=182.5 ms p95=274.0 ms p99=457.0 ms
  mean=197.7 ms max=503.7 ms wall=19.9s (15.1 q/s).
  4 passed in 45.64s. Run once by independent worker on hlm_retr, with
  PGOPTIONS='-c default_transaction_read_only=on'. HARDWARE.md restored byte-identically;
  sha256=965febca3ff3ad786b64b28f3bce1e3730617eac1ceada487169329c4da88873.
- oom-build.log: final runtime image built from this worktree as hlmemo:oom-check;
  pinned local models mounted read-only (BAKE_MODELS=0).
- oom-smoke.log: 1536 MiB / one CPU / no swap / 320 MiB spool tmpfs;
  /ready + 20 memory.query + /ready; OOMKilled=false, RestartCount=0,
  docker_stats_peak_MiB=956.2, cgroup_peak_MiB=1536.0, stats_samples=5, errors=[].
  All oom-check containers removed; label-filtered docker ps -a returned no rows.
- oom-deploy-unittest.log: python3 -m unittest discover -s tests/deploy: Ran 62 tests
  in 91.089s, OK (exit 0 after allowing isolated process-group tests).
- Ruff check src tests: All checks passed! Format --check: 125 files already formatted.
- Example env invocation of docker compose -f deploy/compose.prod.yaml
  --env-file deploy/.env.prod.example config -q: exit 0. git diff --check: clean.

Sizing: API 2560m; DB 2g; worker 1536m; migrate 768m; Caddy 256m.
(1536 + 320) * 1.25 = 2320 MiB; additionally reserve the separate 64 MiB /tmp:
(1536 + 320 + 64) * 1.25 = 2400 MiB, rounded to 2560 MiB.
Steady ceilings 6400 MiB = 6.25 GiB; including migration 7168 MiB = 7 GiB.
Decimal 8 GB headroom: 1.20 GiB steady / 0.45 GiB combined. Tmpfs is already included.
Stats excludes inactive file cache; cgroup peak includes cold-start/cache pressure
under the limit, not an unconstrained peak. This smoke is not a concurrent load test.

Root-cause evidence: pre-fix readiness constructed a throwaway Embedder and the first
query constructed another through default_read_deps. The closing B6 log recorded two
OOM/137 restarts. Constructor regression and real limited smoke now pass. The separate
contributions of duplicate construction versus ORT arena retention were not A/B isolated.

Pitfalls resolved: early Docker proxy connection resets need readiness retry; Docker
29.8 streaming stats adds ANSI codes despite piped stdout, so the smoke uses finite
--no-stream polling. Initial deploy unittest run was blocked by sandbox process/session
restrictions (Operation not permitted); rerun with process permission passed all 62 tests.

Complete: all requested implementation and acceptance checks finished. No commit performed.
Constraints: never control project hlmemo or host ports 8765/5432. HLM_TEST_DSN always
explicit; only hlm_body, R1 hlm_r1judge, and read-only G3/G4 hlm_retr were used.
