# R3 VM rehearsal — checklist and results (D-099 criterion 2)

Status: **PHASE 1 done (VM at R2 state, harness ready). PHASE 2 waits for the final R3 SHA.**
Target: R3 = main + pivot-s1-r3 (final SHA: `<SHA>`, filled in at PHASE 2). Baseline: R2 `6902f91e7a79aab3ff4791ae443d7371b7cca5d7`.
VM: lima `hlm-2604` (vz, aarch64, Ubuntu 26.04, 2 vCPU / 8 GiB, 7913 MiB visible), state dir
`deploy/.local/127.0.0.1-2223`, URL `https://localhost:19443` (Caddy internal CA). **Production (153.92.1.166) is never touched.**
Evidence logs: `docs/bakeoff/rehearsal-r3/` (NN-*.log); harness: `docs/bakeoff/rehearsal-r3/scripts/`.
Raw outputs, the isolated hlm config (tokens) and the R2/R3 source archives live in the scratchpad `$R3` and are never committed.

Stop rule (D-099): any criterion below that misses → STOP, record the numbers here, report to the owner; no prod deploy.

## Budget (coordinator, 2026-09-25 07:0xZ): ≤ $3 real provider spend for the WHOLE rehearsal
The OpenRouter balance (~$19.8) is shared with PRODUCTION. Spent in PHASE 1: $0.5492 (the R2 backlog) → **$2.45 left for PHASE 2**.
PHASE 2 estimate ≈ $1.0: G-L3's 200 writes as librarian jobs ~$0.3–0.5 (the whole R2 rehearsal: $0.64); G-LIVE-C on the VM 4 × 80 ~$0.18 (D-098: $0.175); G-LIVE-B 3 configs ~$0.1–0.3; G-LIVE-D ~$0.12 (R2: $0.115); gates and rewrites: cents.
Guards: (a) VM: after EVERY `install_llm_env.sh` (§2.1, §8.1, §8.4) and before containers are (re)created: `$VSSH "sed -i 's/^HLM_LLM_BUDGET_DAY_USD=.*/HLM_LLM_BUDGET_DAY_USD=2.2/' /etc/hlmemo/llm.env"`. This is a rehearsal-only hand edit, as R2's DAY=2; the UTC-day window already holds the $0.549. §4 runs before §5, so a tripped cap cannot turn G-LIVE-C into retrieval-only. (b) Host runs: G-LIVE-B `--max-usd 1.0`, G-LIVE-D `HLM_GLIVE_D_MAX_USD=0.5`. The host G-LIVE-C reference (§7.3) runs only if §4 fails or is ambiguous (`HLM_GLIVE_C_MAX_USD=0.6`). (c) After every paid step, add its real cost to the tally: VM = `SELECT round(sum(cost_usd),4) FROM llm_calls WHERE created_at > '<step start>'`; host = the run's SUMMARY cost. **STOP and report at a cumulative $2.70.**

| Step | Real $ | Cumulative $ |
|---|---|---|
| PHASE 1 R2 backlog (import 247 + seeds, 345 jobs) | 0.5492 | 0.5492 |

## D-099 criterion 2 — results

| # | Item | Bound | Measured | Result | Evidence |
|---|---|---|---|---|---|
| 1 | gate-release G3 (final SHA, rewrite flag on) | Recall@5 ≥ 0.90 | | | §7.2 |
| 2 | gate-release G4 | warm p95 ≤ 500 ms (3 callers) | | | §7.2 |
| 3 | G-L3 on the 2 vCPU VM, librarian ON (observer) + rewrite ON, quiet host | query p95 ≤ 500 ms during 100 writes, 100/100 acked (neutral AND identifier) | | | §5 |
| 4 | Remote gates (8 base + risk-check + librarian + backup-restore drill) | all PASS | | | §3, §8 |
| 5 | G-LIVE-C through the production fallback wiring, primary forced down (on the VM) | catch ≥ 0.85 and false-warn ≤ 0.10 every rep; all judged = `ok_fallback` | | | §4 (+ host reference §7.3) |
| 6 | G-LIVE-B as in R2 (D-094 mapping) | run_w2b.py verdict PASS per config | | | §7.4 |
| 7 | G-LIVE-D as in R2 | Δ vs fast path ≥ +0.03 every rep, per profile | | | §7.5 |
| 8 | Observer = 0 librarian mutations | links/versions by librarian = 0, close delta 0, 0 decided/applied | | | §6 |
| 9 | Rollback to R2 (W0a image rollback) works, then roll forward | rc 0 both ways, R2 serves, gates PASS | | | §8 |
| — | Code-only since R2 | no alembic/compose/deploy.sh/remote-deploy/rollback/Dockerfile diff | | | §0.1 |

## PHASE 1 (2026-09-25, done)

- [x] VM found: `limactl list` → `hlm-2604` (R1 + R2 rehearsals) and `hlm-rehearsal` (Phase 0). `hlm-2604` started 00:45Z.
- [x] Host key changed on boot (as in R2); `ssh-keyscan` = the guest's `/etc/ssh/ssh_host_ed25519_key.pub` read through lima's own channel (`SHA256:VjThmXZL…`) → re-pinned in the state dir (old file kept as `known_hosts.bak-*`).
- [x] VM state found: current_ref `3535bcc` (the R2 *candidate* of the R2 rehearsal), no llm.env (key removed after R2), data r1-seed/r2-seed/r2-load/r2-import.
- [x] R2-production llm.env installed with **R2's own** script and template (`git archive 6902f91` → `$R3/r2src`; key from `./.env` over ssh stdin): enabled, observer, luna, fallback openrouter, prod caps (3/10/60 USD).
- [x] **R2 baseline deployed: `deploy.sh hlm-deploy 6902f91…`** (code-only from 3535bcc, same compose `99fecbf4…`, no `--accept-compose-change`) → rc 0, routes PASS, `RESULT librarian PASS`, "Deployment ready: 6902f91…". So the rollback target recorded at the R3 cutover is exactly production's R2. (Baseline only; nothing of R3 is on the VM.)
- [x] Realistic data, the R2-rehearsal way: `hlm import markdown` of 51 tracked HLMemo docs (docs/decisions, docs/status, first 41 docs/consults; 585 kB) with the **R2 CLI** into test project `r3-base` → 247 new, 0 failed (137 s of librarian backlog at start).
- [x] Seeded on R2 (so R3 reads R2-written data): G-L3 project `r3-load` (4 × 40 items, as R2 08a); G-LIVE-C world (projects rk-main/rk-shell/rk-secret + hlm-global, devices rk-loader id 34 / rk-reader id 35, the pinned 50-entry risk library via MCP); devices r3-base-mac 32, r3-load-mac 33 (tokens only in `$R3/cfg`, 0600).
- [x] Harness written and smoke-tested on R2: `scripts/libcheck.sh` (the runner's librarian check on demand: PASS on R2), `observer.sql`, `gl3_vm_r3.py`, `glive_c_vm.py`, `preseed.py` (+ R2 helpers sampler/analyze/poller/downtime/vmsql/snap/backlog).
- [x] Host: own template DB `hlm_r3reh_world` with the G3 world pre-loaded (main code; the loader is identical at pivot-s1-r3), for gate-release clones.
- [x] The R2 librarian (concurrency 1) drained the whole import/seed backlog by 01:56Z: 345 librarian_write jobs done, 0 failed; 1,066 provider calls, all `ok` (place 343 + relate 668 on luna, relate_verify 55 on openrouter), **$0.5492**. The librarian was NOT stopped: nothing was left to spend. Drained R2 state: 1,060 signal_upsert, 0 links/versions by the librarian, 0 of 45 batches decided/applied, versions superseded/closed 0 (00-phase1.log).
- [x] **R2-state snapshot** (07:04Z, VM stopped): APFS clones `~/.lima/_snapshots/hlm-2604-r2state.{disk,vz-efi}`. Restarted: host key re-pinned the same way (`SHA256:kFVL8y3o…`), `/ready` 200 after ~3 s, `libcheck.sh` PASS (R2 reboot check).
- [x] Found and fixed on restart: the VM clock was **46 min slow** (the host slept about 47 min; chrony only slews, "2763 s slow"), which would skew every host/VM time window (sampler vs gl3 timestamps, llm_calls windows). §0.8 now guards it.

### PHASE 1 findings that shape this procedure
1. **R2 code cannot load the D-094 llm.env.** In the R2 image, `profile_chain` fails with `LlmConfigError: profile 'openrouter-glm53-flash' not found` and the risk judge chain is empty (checked in a throwaway container on the VM with the D-094 profile lines; evidence 00-phase1.log). The R2 librarian builds its provider at startup, so it would crash-loop. Consequences:
   - a `deploy.sh --rollback` to R2 with the D-094 llm.env still installed fails its `up --wait` and aborts back to R3 → **the R2 llm.env must be reinstalled before the rollback** (§8.1);
   - the in-deploy automatic recovery of a failed R3 cutover starts the R2 images with whatever llm.env is on the host → **Order B**: deploy R3 while the R2 llm.env is still installed (R3 accepts it: fallback `openrouter`), and switch to the D-094 llm.env only after the cutover succeeded (§1 → §2). Recommended for production too (RUNBOOK's R2 order "install llm.env first" is unsafe for R3).
2. **`HLM_QUERY_REWRITE` has no line in `deploy/llm.env.example`** (pivot-s1-r3 97344eb), and `install_llm_env.sh` rewrites llm.env from the template, so a later install silently turns the rewrite off. Recommended before the final SHA: add `HLM_QUERY_REWRITE=true` to the template. If the SHA's template lacks it, §2.2 appends it on the host (before the END marker) and production must do the same.
3. **The SHA must be fetchable from origin** (`deploy.sh` makes the server fetch it from GitHub; local main is 76 commits ahead of origin/main). Before PHASE 2: push main, or push the SHA only as the temporary branch `r3-rehearsal` after a gitleaks scan of `origin/main..SHA` (the R2 precedent).
4. **R3 changes the librarian verifier.** `relate_verify` has no per-task override, so its chain is `[luna, glm53-flash]` and the cross verifier becomes glm53-flash (R2: deepseek via `openrouter`). §7.4 therefore runs G-LIVE-B "as in R2" on the D-094 mapping (luna / glm53-flash / chain luna+glm53-flash), not only on R2's configs.
5. Host load during PHASE 1 was ~4.6–5.0 (12 cores; other agents' jobs). G-L3 and G4 wait for a quiet window (§5.0).

## Variables (run every block under **bash**, not zsh: zsh does not word-split, which broke a command in PHASE 1)

```bash
REPO=/Users/cemalkurt/Projects/HLMemo; cd "$REPO"
S=$REPO/deploy/.local/127.0.0.1-2223          # VM state dir (the ONLY host this checklist targets)
R3=/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r3
EV=$REPO/docs/bakeoff/rehearsal-r3; SC=$EV/scripts
URL=https://localhost:19443
SHA=<final full 40-char SHA>
export HLM_CONFIG_DIR=$R3/cfg PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring SSL_CERT_FILE=$S/caddy-internal-root.crt
OPS="bash deploy/scripts/hlm_ops.sh --state $S"
VSSH="ssh -F $S/ssh_config hlm-deploy"
PG=postgresql://hlm:hlm@127.0.0.1:5432
quiet() { date -u +%FT%TZ; uptime; ps -Ao pcpu,pid,comm -r | head -8; }
```
Sanity before anything: `grep -q 127.0.0.1 $S/ssh_config && ! grep -q 153.92 $S/ssh_config`.

## PHASE 2

### §0 Preconditions (no VM change)
- [ ] 0.1 SHA and scope: `git cat-file -e $SHA^{commit}`; `git log -1 --format='%H %P %s' $SHA`; it contains the final pivot-s1-r3 head and main (`git merge-base --is-ancestor <pivot head> $SHA && git merge-base --is-ancestor main-at-merge $SHA`).
  Code-only check (must print nothing): `git diff --stat 6902f91 $SHA -- alembic deploy/compose.prod.yaml deploy/scripts/deploy.sh deploy/scripts/remote-deploy.sh deploy/scripts/rollback.sh deploy/scripts/common.sh deploy/scripts/stack.sh deploy/backup deploy/Caddyfile Dockerfile pyproject.toml uv.lock models.lock`;
  `git show $SHA:deploy/compose.prod.yaml | shasum -a 256` = `99fecbf403cf6403…`; newest `alembic/versions/0008_librarian_tasks.py`. → 01-preconditions.log
  PHASE-1 check on pivot-s1-r3 97344eb: empty diff, compose 99fecbf4…, head 0008 (only RUNBOOK, llm.env.example, check_librarian.py, install_llm_env.sh and profiles/ changed under deploy/ + profiles/).
- [ ] 0.2 On origin: `git fetch origin && git branch -r --contains $SHA` non-empty (else STOP → finding 3).
- [ ] 0.3 Template at SHA: `git show $SHA:deploy/llm.env.example | grep -E '^HLM_(PROFILE|FALLBACK_PROFILE|QUERY_REWRITE|RETRIEVAL_SOURCE_CAP|RENDITIONS|LIBRARIAN_CONCURRENCY)'` — expect the D-094 lines; note whether `HLM_QUERY_REWRITE=true` is present (finding 2); `HLM_RETRIEVAL_SOURCE_CAP` absent or false; no `HLM_RENDITIONS`.
- [ ] 0.4 R3 sources: `rm -rf $R3/r3src && mkdir -p $R3/r3src && git archive $SHA | tar -x -C $R3/r3src && (cd $R3/r3src && uv sync --frozen)`.
- [ ] 0.5 VM baseline (R2): `$VSSH cat /opt/hlmemo/release-state.json` (current_ref 6902f91), `$SC/libcheck.sh` PASS, `$OPS status` (librarian `ready=0` = drained), `$SC/vmsql.sh < $SC/snap.sql`; `BASE_EVENT=$(printf '\\pset tuples_only on\n\\pset format unaligned\nSELECT max(event_id) FROM events;\n' | $SC/vmsql.sh)`; project item counts:
  `printf "SELECT p.slug, count(*) FROM memory_versions v JOIN projects p ON p.project_id = v.project_id GROUP BY 1 ORDER BY 1;\n" | $SC/vmsql.sh` → 10-baseline-r2.log.
- [ ] 0.6 `scp`-free: `$VSSH 'cat > /tmp/r3-backlog.sql' < $SC/backlog.sql` (sampler input).
- [x] 0.7 Restorable R2-state snapshot: DONE in PHASE 1 (drained R2, 07:04Z) as APFS clones `~/.lima/_snapshots/hlm-2604-r2state.{disk,vz-efi}` (lima vz has no `limactl snapshot`). Restore only if the VM is broken beyond the rollback drill: `limactl stop hlm-2604 && cp -c ~/.lima/_snapshots/hlm-2604-r2state.disk ~/.lima/hlm-2604/disk && cp -c ~/.lima/_snapshots/hlm-2604-r2state.vz-efi ~/.lima/hlm-2604/vz-efi && limactl start hlm-2604`, then re-pin (keyscan = guest key file via `limactl shell`), `/ready`, `libcheck.sh`.
- [ ] 0.8 Clock and sleep guard: `caffeinate -dimsu & CAF=$!` for the whole of PHASE 2 (kill at §9). Check `date -u` on host vs `$VSSH 'date -u; chronyc tracking | grep "System time"'`: offset ≤ 2 s. Else `$VSSH 'sudo chronyc makestep'`, or restart the VM (re-pin) before any timed step.

### §1 Upgrade R2 → R3, Order B (the R2 llm.env stays in place during the cutover)
- [ ] 1.1 Downtime poller: `python3 $SC/poller.py $R3/poll-cutover.txt $SSL_CERT_FILE & POLL=$!`
- [ ] 1.2 `PATH="$S/bin:$PATH" bash deploy/scripts/deploy.sh hlm-deploy "$SHA"` (NO `--accept-compose-change`; the runner refuses if the model changed) → expect rc 0, "Deployment ready: $SHA", routes PASS, `RESULT librarian PASS … fallback=openrouter`. → 11-cutover-r3.log
- [ ] 1.3 `kill $POLL; python3 $SC/downtime.py $R3/poll-cutover.txt` → 11b-downtime.log (R2 cutover: 12.0 s).
- [ ] 1.4 `$VSSH 'cat /opt/hlmemo/release-state.json; docker ps --format "{{.Names}} {{.Image}} {{.Status}}"'` → current_ref=$SHA, previous_ref=6902f91…, previous_dump + previous_image_id recorded; alembic still 0008 (snap.sql).

### §2 Switch to the R3 production llm.env (D-094 mapping + rewrite ON)
- [ ] 2.1 `bash $R3/r3src/deploy/scripts/install_llm_env.sh --state $S --key-file $REPO/.env` (the SHA's template; key over ssh stdin; the R2 file is kept as `llm.env.bak-*`).
- [ ] 2.2 Only if 0.3 found no `HLM_QUERY_REWRITE` line: `$VSSH "sed -i '/^# END llm.env (install_llm_env.sh)$/i HLM_QUERY_REWRITE=true' /etc/hlmemo/llm.env && grep -c '^HLM_QUERY_REWRITE=true$' /etc/hlmemo/llm.env"` (= 1). Record as a required production step.
- [ ] 2.3 `$VSSH 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh up -d --no-deps --wait librarian api'`
- [ ] 2.4 `$SC/libcheck.sh` → `per-task fallbacks: query_rewrite=openrouter risk_judge=openrouter-qwen38-27b-fast synthesis=openrouter`, `fallback=openrouter-glm53-flash`, all profiles key set + reachable, `RESULT librarian PASS`;
  `$VSSH 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api python -c "from hlmemo.config import get_settings as g; s=g(); print(s.query_rewrite, s.retrieval_source_cap, s.librarian_role, s.librarian_concurrency)"'` → `True False observer 3`; `$OPS status` (chains per task). → 12-llm-env-d094.log

### §3 Remote gates on R3 (D-094 wiring active)
- [ ] 3.1 `bash deploy/scripts/remote_gates.sh --url $URL --state $S --insecure --librarian` (drill ON: rehearsal VM) → the 8 base gates (health-ready, unknown-path-404, tls-issuer, postgres-closed, probe, routes, hlm-cli, wan-latency) + risk-check (`judged=true`) + librarian (observer, 0 links/closes/applied) + backup-restore: **all PASS**. → 13-remote-gates-r3.log

### §4 G-LIVE-C on the VM, primary forced unavailable in the api (D-098 method, deployed)
- [ ] 4.1 (0.3 must show that the SHA's llm.env template sets no `HLM_LLM_BASE_URL`, else it would override api.env.) Force the primary down for the api only: `$VSSH 'cp -p /etc/hlmemo/api.env /etc/hlmemo/api.env.r3glc && echo HLM_LLM_BASE_URL=http://127.0.0.1:9/v1 >> /etc/hlmemo/api.env && cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh up -d --no-deps --wait api'`;
  `$SC/libcheck.sh` must show the api primary `openrouter-gpt6-luna http://127.0.0.1:9/v1` (unreachable, reported only) and risk_judge fallback qwen38-27b-fast. `GLC_EVENT` = max event_id; `GLC_T0=$(date -u +%FT%TZ)`.
- [ ] 4.2 `(cd $R3/r3src && uv run --frozen python $SC/glive_c_vm.py run --repo $R3/r3src --reader rk-reader --world $R3/rk-world.json --reps 4 --out $R3/glive-c-vm)` → `RESULT G-LIVE-C-VM PASS|FAIL` (catch min, false-warn max, statuses, p50/p95). → 14-glive-c-vm.log
- [ ] 4.3 Ledger: `printf "SELECT profile, outcome, count(*), round(sum(cost_usd),4) usd, percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms) p50 FROM llm_calls WHERE task='risk_judge' AND created_at > '$GLC_T0' GROUP BY 1,2 ORDER BY 1,2;\n" | $SC/vmsql.sh` → only luna (http/connect errors) and qwen38-27b-fast rows.
- [ ] 4.4 Restore: `$VSSH 'mv -f /etc/hlmemo/api.env.r3glc /etc/hlmemo/api.env && ! grep -q HLM_LLM_BASE_URL /etc/hlmemo/api.env && cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh up -d --no-deps --wait api'`; `$SC/libcheck.sh` PASS with the real primary URL.

### §5 G-L3 on the 2 vCPU VM (librarian ON observer + rewrite ON; quiet host)
- [ ] 5.0 Quiet host: `quiet` must show load1 ≤ 3.0 and no other heavy job (pytest/uv/eval/codex/docker build > 100 % CPU) besides the VM; otherwise wait (re-check every 60 s) and, after 30 min, ask the orchestrator to pause other jobs. Never overlap with §7 or other gates. Record `quiet` at start and end of each run.
- [ ] 5.1 Backlog drained: `$OPS status` → worker and librarian `ready=0`.
- [ ] 5.2 `$SC/sampler.sh $R3/sampler-gl3.log & SAMP=$!`
- [ ] 5.3 Neutral: `(cd $R3/r3src && uv run --frozen python $SC/gl3_vm_r3.py --device r3-load-mac --project r3-load --body neutral --run gl3n --prime)` → JSON: `q_p95` ≤ 500, `writes_acked` 100/100, errors 0; `prime.statuses` shows `applied` for the non-English queries and `query_rewrite_during` counts `applied` (the cache-hit path is under test). → 15-gl3-vm.log
- [ ] 5.4 Wait until `$OPS status` shows librarian/worker `ready=0` again (record drain time; R2: embed 96 s, librarian 537 s at concurrency 1; R3 runs 3 jobs at once).
- [ ] 5.5 Identifier: same command with `--body identifier --run gl3i --offset 100` → same bounds.
- [ ] 5.6 `kill $SAMP; python3 $SC/analyze.py $R3/sampler-gl3.log <t_start> <t_end>` per run (CPU busy, load, memory peak per service vs limits, backlog peaks) → 15b-gl3-resources.log. R2 reference: p95 268 (neutral) / 375 (identifier) ms.

### §6 Observer = 0 librarian mutations (after §3–§5 drained)
- [ ] 6.1 `{ echo "\\set since $BASE_EVENT"; cat $SC/observer.sql; } | $SC/vmsql.sh` → mutation ops only `signal_upsert`/`<none>`, `links_by_librarian` 0, `versions_by_librarian` 0, versions superseded/closed = the §0.5 values, links closed 0, `batches_decided_or_applied` 0, no `applied` question.
- [ ] 6.2 `for p in r3-base r3-load rk-main gates-probe; do $OPS librarian audit --project $p --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sys.argv[1], d.get("by_status") or {}, "applied batches", sum(1 for b in d.get("batches",[]) if b.get("applied_at")))' $p; done` → applied 0 everywhere. → 16-observer.log

### §7 Host gates on the final SHA (sequential; never during §5)
- [ ] 7.1 DBs (own only): `psql $PG/postgres -c 'CREATE DATABASE hlm_r3reh_gate TEMPLATE hlm_r3reh_world' -c 'CREATE DATABASE hlm_r3reh_live'`.
- [ ] 7.2 gate-release (quiet host, `quiet` before/after): `(cd $R3/r3src && HLM_MODELS_DIR=$REPO/models HLM_TEST_DSN=$PG/hlm_r3reh_gate HLM_QUERY_REWRITE=true make gate-release)` → G3 ≥ 0.90, G4 p95 ≤ 500 ms, local G-L3 (rewrite flag on, its LLM refused) p95 ≤ 500 ms both bodies. Then `DROP DATABASE hlm_r3reh_gate`. → 17-gate-release.log
- [ ] 7.3 (Only if §4 FAILs or is ambiguous: budget) G-LIVE-C host reference, the D-098 procedure on the final code, separating a deploy/wiring problem from a model one: `(cd $R3/r3src && HLM_GLIVE_C_MAX_USD=0.6 HLM_GLIVE_C_WIRING=1 HLM_GLIVE_C_KEY_FILE=$REPO/.env HLM_GLIVE_C_OUT=$R3/glive-c-host HLM_MODELS_DIR=$REPO/models HLM_TEST_DSN=$PG/hlm_r3reh_live uv run --frozen pytest -q -s -p no:cacheprovider tests/integration/test_wf_glive_c_wiring.py)` → PASS (D-098: catch 1.000, false-warn max .075). → 18-glive-c-host.log
- [ ] 7.4 G-LIVE-B as in R2, on the D-094 mapping (finding 4): `(cd $R3/r3src && uv run --frozen python eval/live/run_w2b.py --profile openrouter-gpt6-luna --fallback openrouter-glm53-flash --chain openrouter-gpt6-luna+openrouter-glm53-flash --reps 3 --max-usd 1.0 --env-file $REPO/.env --out $R3/glive-b)` → Verdict PASS for luna, glm53-flash and the chain (placement ≥ .90, contradiction exact ≥ .90, false supersede ≤ .02, class bars). R2: luna / openrouter / chain luna+openrouter all PASS. → 19-glive-b.log
- [ ] 7.5 G-LIVE-D as in R2 (profiles luna + openrouter = D-094 synthesis primary/fallback): `(cd $R3/r3src && HLM_GLIVE_D_MAX_USD=0.5 HLM_GLIVE_D=1 HLM_W2E_ENV_FILE=$REPO/.env HLM_GLIVE_D_OUT=$R3/glive-d HLM_MODELS_DIR=$REPO/models HLM_TEST_DSN=$PG/hlm_r3reh_live uv run --frozen pytest -q -s -p no:cacheprovider tests/integration/test_w2e_glive_d.py)` → PASS per profile (R2: luna +0.161 min, openrouter +0.194 min). → 20-glive-d.log

### §8 Rollback drill R3 → R2 (W0a image rollback), then roll forward
All R3 measurements are recorded first: the rollback restores the pre-R3 quiesced dump, so every R3-era write is discarded (by design, RUNBOOK).
- [ ] 8.1 Put the R2 llm.env back FIRST (finding 1): `bash $R3/r2src/deploy/scripts/install_llm_env.sh --state $S --key-file $REPO/.env` (no container recreate needed; the rollback recreates them).
- [ ] 8.2 `python3 $SC/poller.py $R3/poll-rollback.txt $SSL_CERT_FILE & POLL=$!`; `PATH="$S/bin:$PATH" bash deploy/scripts/deploy.sh --rollback hlm-deploy` → "Rollback validated: $SHA -> 6902f91…", "Rollback complete: 6902f91… is running again"; `kill $POLL; python3 $SC/downtime.py $R3/poll-rollback.txt`. → 21-rollback.log
- [ ] 8.3 R2 serves: `$VSSH cat /opt/hlmemo/release-state.json` (current_ref 6902f91, pair consumed); api image label 6902f91; `$SC/libcheck.sh` PASS (R2's own check, fallback openrouter); project counts = §0.5 (the pre-upgrade dump); `bash deploy/scripts/remote_gates.sh --url $URL --state $S --insecure --librarian --no-drill` all PASS. → 21b-r2-after-rollback.log
- [ ] 8.4 Roll forward, Order B: `PATH="$S/bin:$PATH" bash deploy/scripts/deploy.sh hlm-deploy "$SHA"` (image reused by label) → rc 0; then §2.1–2.4 again (D-094 + rewrite; libcheck PASS). → 22-roll-forward.log
- [ ] 8.5 `bash deploy/scripts/remote_gates.sh --url $URL --state $S --insecure --librarian --no-drill` all PASS; release-state current=$SHA, previous=6902f91 (a valid rollback pair again, like production will have). → 22b-gates-after-roll-forward.log

### §9 End state and cleanup
- [ ] 9.1 Final `$OPS status`, `snap.sql`, spend (`spend_today_usd`), memory table (`docker stats --no-stream`). → 23-final.log
- [ ] 9.2 VM stays at R3 ($SHA, D-094 llm.env) unless the orchestrator says otherwise; the provider key is removed afterwards like R2 (`install_llm_env.sh --state $S --remove`, `stack.sh up -d --no-deps librarian api`, positive-controlled grep that no file or container env on the VM holds it) and the VM is stopped (`limactl stop hlm-2604`), unless the orchestrator wants it kept running for the prod comparison.
- [ ] 9.3 Host: `DROP DATABASE hlm_r3reh_live, hlm_r3reh_world` (own DBs only); shred `$R3/cfg/credentials.toml`.
- [ ] 9.4 Secret gate before commit: `gitleaks dir docs/bakeoff/rehearsal-r3 docs/status/R3-REHEARSAL.md` clean (logs abbreviate 40-hex image tags to 12, as in R2); commit this file + `docs/bakeoff/rehearsal-r3/` on main.
