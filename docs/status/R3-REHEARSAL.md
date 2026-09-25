# R3 VM rehearsal — checklist and results (D-099 criterion 2, as amended by D-116/D-123)

Status: **PHASE 2 resumed 17:00Z (see RESUME below).** Target: **R3 = main `805f4cd2955b83c258678cf901a32a605ea88f9c`** (pushed), i.e. J (D-096) + F (D-098) + the
env-aware release tooling (D-111/D-119, merged as-is per D-123). **The query rewrite is SHELVED (D-116): rewrite and per-source cap
stay OFF/absent**; slice 2 renditions absent. Baseline: R2 `6902f91e7a79aab3ff4791ae443d7371b7cca5d7`.
VM: lima `hlm-2604` (vz, aarch64, Ubuntu 26.04, 2 vCPU / 8 GiB, 7913 MiB visible), state dir `deploy/.local/127.0.0.1-2223`,
URL `https://localhost:19443` (Caddy internal CA). **Production (153.92.1.166) and every Hostinger VPS are never touched.**
Evidence: `docs/bakeoff/rehearsal-r3/NN-*.log`; harness `docs/bakeoff/rehearsal-r3/scripts/`. Raw outputs, the isolated hlm config
(tokens) and the R2 source archive live in the scratchpad `$R3` and are never committed. Commits stay on local main (not pushed).

Stop rule (D-099): any criterion below that misses → STOP, record the numbers here, report to the owner; no prod deploy.

## What R3 changes vs R2 (verified with git on 805f4cd)
- No alembic change (head `0008_librarian_tasks`), `deploy/compose.prod.yaml` unchanged (sha256 `99fecbf4…`), Dockerfile,
  pyproject/uv.lock/models.lock and `deploy/scripts/deploy.sh` (the workstation bootstrap) unchanged → code-only for the database and
  the Compose model: no migration, no `--accept-compose-change`.
- **Release tooling changed** (runs on the server from the fetched commit): `remote-deploy.sh` (+281/−), `rollback.sh` (+95),
  `release_state.py` (+190: journals `deploy_attempt`, `env_switch`, `pending_cleanup`, `previous_llm_env`), new `llm_env_release.py`
  (env snapshot/restore/fingerprint/provenance), `check_librarian.py` (+277: `RELEASE_MANIFESTS["r3"]`, `--release r3`,
  `--llm-env-file`, the D-108 interim), `install_llm_env.sh` (+114: one journalled step under the deploy lock, recreates
  librarian+api and runs `evaluate --release r3`; preserves the operator's caps and key, D-121), `llm.env.example`
  (D-094 mapping, `HLM_ENV_RELEASE=r3`, caps MONTH=10 / DAY=2 / HOUR=1), RUNBOOK "R3 release", 9 new profiles.
- llm.env is part of the release state: the R3 deploy snapshots the env the running R2 uses (`llm.env.release-<ref>`, provenance-
  checked against the api AND librarian containers) and `--rollback` restores it before the R2 image starts. **No manual R2-env
  reinstall** before a rollback (the provenance check would refuse a disk env the containers do not run).

## Budget (coordinator): ≤ $3 real provider spend for the WHOLE rehearsal (the OpenRouter balance is shared with production)
PHASE 1 spent $0.5492 → $2.45 left. The VM guard is now the R3 template's production caps (MONTH=10, DAY=2, HOUR=1, guard on); no
rehearsal-only cap edit is needed. Host live gates carry their own guards: G-LIVE-B `--max-usd 1.0`, G-LIVE-D `HLM_GLIVE_D_MAX_USD=0.5`;
the host G-LIVE-C reference runs only if the VM run fails or is ambiguous. Tally after every paid step; **STOP at a cumulative $2.70.**

| Step | Real $ | Cumulative $ |
|---|---|---|
| PHASE 1 R2 backlog (import 247 + seeds, 345 jobs) | 0.5492 | 0.5492 |
| §3 remote gates + §4 G-LIVE-C on the VM (VM ledger 13:41→14:00Z; G-LIVE-C real $0.1745) | 0.1804 | 0.7296 |

## D-099 criterion 2 (amended by D-116: rewrite OFF) — results

| # | Item | Bound | Measured | Result | Evidence |
|---|---|---|---|---|---|
| 1 | gate-release G3 (final SHA, flags off) | Recall@5 ≥ 0.90 | 0.980 (98/100) | PASS | §5.2, 17 |
| 2 | gate-release G4 | warm p95 ≤ 500 ms, 3 callers | p95 251.0 ms (host load 2.5–3.1) | PASS | §5.2, 17 |
| 2b | gate-release G-L3 (local, LLM stalled/503) | p95 ≤ 500 ms, both bodies | 358.1 / 417.1 ms | PASS | §5.2, 17 |
| 3 | G-L3 on the 2 vCPU VM, librarian ON (observer, concurrency 3), rewrite OFF, quiet host | query p95 ≤ 500 ms during 100 writes, 100/100 acked, neutral AND identifier | | | §6 |
| 4 | Remote gates: the 8 base (health-ready, unknown-path-404, tls-issuer, postgres-closed, probe, routes, hlm-cli, wan-latency) + risk-check + librarian + backup-restore drill | all PASS | 11/11 on R3 (WAN p95 45 ms, librarian job 10 s, restore 16 s) | PASS | §3, 13 |
| 4b | Cutover checks: interim (R3 image + R2 env) and `--release r3` after the env switch | RESULT librarian PASS | interim PASS (release=r2-env); r3 PASS (release=r3 manifest=r3) | PASS | §1, §2, 11, 12 |
| 5 | G-LIVE-C through the D-094 wiring on the VM, primary forced down | catch ≥ 0.85, false-warn ≤ 0.10 every rep; judged only `ok_fallback` | catch min .975, false-warn max .075; 297 ok_fallback / 23 timeout / 0 primary | PASS | §4, 14 |
| 6 | G-LIVE-B as in R2, on the D-094 mapping | run_w2b verdict PASS per config | | | §5 |
| 7 | G-LIVE-D as in R2 (luna, openrouter) | synthesis − fast path ≥ +0.03 every rep | | | §5 |
| 8 | Observer = 0 librarian mutations | links/versions by librarian 0, close delta 0, 0 decided/applied | | | §7 |
| 9a | Drill (a) script rollback R3 → R2 (env restored automatically), R2 healthy, roll forward | rc 0 both ways, gates PASS | | | §8 |
| 9b | Drill (b) VM snapshot restore (pre-deploy disk clone, the Hostinger-snapshot analog, D-123) → R2 healthy → back to R3 | R2 healthy, gates PASS | | | §9 |

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
- [x] Found and fixed on restart: the VM clock was **46 min slow** (the host slept about 47 min; chrony only slews, "2763 s slow"), which would skew every host/VM time window (sampler vs gl3 timestamps, llm_calls windows). §0.2 now guards it.

### PHASE 1 findings and how the final scope resolved them
1. R2 code cannot load the D-094 llm.env (`profile 'openrouter-glm53-flash' not found`) → became **D-108** (Order B: R3 image with the
   R2 env first, then the R3 env) and D-111's automatic env restore on rollback. The manual "reinstall the R2 env before a rollback"
   of the first plan is now WRONG and not done (the provenance check refuses a disk env the containers do not run).
2. `HLM_QUERY_REWRITE` missing from the template → moot: the rewrite is shelved (D-116); the R3 manifest requires it absent/false.
3. SHA on origin → done (805f4cd pushed by the orchestrator).
4. R3's librarian verifier becomes glm53-flash (relate_verify chain `[luna, glm53-flash]`) → G-LIVE-B runs on the D-094 mapping.
5. Host load / VM clock (host sleep) → quiet-host rule and `caffeinate` + clock check (§0).
6. NEW for PHASE 2 (read of install_llm_env.sh): the install PRESERVES the installed caps (D-121). The R2 template's caps are
   HOUR=3 / DAY=10 / **MONTH=60**, and the R3 manifest requires month ≤ 10, so an R3 install over an untouched R2 env fails its own
   `--release r3` check and leaves the `env_switch` journal open (deploy/rollback refuse until a fixed re-run). **Production
   precondition:** the owner's D-121 edit (MONTH=10, DAY=2, HOUR=1) must be in `/etc/hlmemo/llm.env` before the R3 env install (or use
   `--reset-operator-values` with the prod key file). The rehearsal mirrors the owner's edit in §0.4.

## Variables (run every block under **bash**, not zsh)

```bash
REPO=/Users/cemalkurt/Projects/HLMemo; cd "$REPO"            # local main == 805f4cd (the release tooling runs from here)
S=$REPO/deploy/.local/127.0.0.1-2223                          # VM state dir (the ONLY host this checklist targets)
R3=/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r3
EV=$REPO/docs/bakeoff/rehearsal-r3; SC=$EV/scripts
URL=https://localhost:19443
SHA=805f4cd2955b83c258678cf901a32a605ea88f9c
export HLM_CONFIG_DIR=$R3/cfg PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring SSL_CERT_FILE=$S/caddy-internal-root.crt
OPS="bash deploy/scripts/hlm_ops.sh --state $S"; VSSH="ssh -F $S/ssh_config hlm-deploy"
PG=postgresql://hlm:hlm@127.0.0.1:5432
quiet() { date -u +%FT%TZ; uptime; ps -Ao pcpu,pid,comm -r | head -8; }
repin() { g=$(limactl shell hlm-2604 sudo cat /etc/ssh/ssh_host_ed25519_key.pub | tr -d '\r' | awk '{print $1, $2}')
          k=$(ssh-keyscan -p 2223 -t ed25519 127.0.0.1 2>/dev/null | grep -v '^#' | awk '{print $2, $3}')
          [ -n "$g" ] && [ "$g" = "$k" ] && printf '[127.0.0.1]:2223 %s\n' "$g" > $S/known_hosts && ssh-keygen -lf $S/known_hosts; }
```
Sanity first: `grep -q 127.0.0.1 $S/ssh_config && ! grep -q 153.92 $S/ssh_config`.

## PHASE 2

### §0 Preconditions
- [x] 0.1 `git rev-parse HEAD` = $SHA; `git branch -r --contains $SHA` = origin/main. Code-only check (empty):
  `git diff --stat 6902f91 $SHA -- alembic deploy/compose.prod.yaml Dockerfile pyproject.toml uv.lock models.lock deploy/scripts/deploy.sh`;
  the tooling diff (`git diff --stat 6902f91 $SHA -- deploy`) recorded. → 01-preconditions.log
- [x] 0.2 `caffeinate -dimsu & CAF=$!`; clock: host `date -u` vs `$VSSH date -u` ≤ 2 s (else `sudo chronyc makestep` or restart + repin).
- [x] 0.3 VM = R2 6902f91, drained (`$OPS status`: librarian ready=0), `$SC/libcheck.sh` PASS. No `hlmemo-backup.timer` exists on the
  VM (only dpkg-db-backup): the prod step "pause the backup timer" has no VM analog (noted).
- [x] 0.4 Mirror the owner's D-121 edit on the R2 env (finding 6): `$VSSH "sed -i 's/^HLM_LLM_BUDGET_HOUR_USD=.*/HLM_LLM_BUDGET_HOUR_USD=1/; s/^HLM_LLM_BUDGET_DAY_USD=.*/HLM_LLM_BUDGET_DAY_USD=2/; s/^HLM_LLM_BUDGET_MONTH_USD=.*/HLM_LLM_BUDGET_MONTH_USD=10/' /etc/hlmemo/llm.env"`,
  then "restart" like the owner: `$VSSH 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh up -d --no-deps --wait librarian api'`; `$SC/libcheck.sh` PASS.
- [x] 0.5 Baseline: `$SC/vmsql.sh < $SC/snap.sql`, project counts, `BASE_EVENT=$(printf '\\pset tuples_only on\n\\pset format unaligned\nSELECT max(event_id) FROM events;\n' | $SC/vmsql.sh)`,
  `{ echo "\\set since 0"; cat $SC/observer.sql; } | $SC/vmsql.sh`. → 02-baseline-r2.log
- [x] 0.6 **Pre-deploy snapshot** (the prod Hostinger snapshot analog, D-123): `limactl stop hlm-2604 && cp -c ~/.lima/hlm-2604/disk ~/.lima/_snapshots/hlm-2604-predeploy.disk && cp -c ~/.lima/hlm-2604/vz-efi ~/.lima/_snapshots/hlm-2604-predeploy.vz-efi && limactl start hlm-2604 && repin`;
  `/ready` 200, `$SC/libcheck.sh` PASS, clock check; then remove the superseded PHASE-1 clones `hlm-2604-r2state.*`. → 03-predeploy-snapshot.log

### §1 R3 image with the R2 env (D-108 steps 1–2)
- [x] 1.1 `python3 $SC/poller.py $R3/poll-cutover.txt $SSL_CERT_FILE & POLL=$!`
- [x] 1.2 `PATH="$S/bin:$PATH" bash deploy/scripts/deploy.sh hlm-deploy "$SHA"` (no `--accept-compose-change`) → rc 0,
  `RESULT librarian PASS llm.env=present release=r2-env (D-108 interim …)`, routes PASS, "Deployment ready". → 11-cutover-r3.log
- [x] 1.3 `kill $POLL; python3 $SC/downtime.py $R3/poll-cutover.txt` (R2 cutover: 12.0 s). → 11b-downtime.log
- [x] 1.4 `$VSSH 'python3 -m json.tool /opt/hlmemo/release-state.json; ls -la /etc/hlmemo'`: current_ref=$SHA, previous_ref=6902f91,
  `previous_llm_env` = `llm.env.release-6902f91…` (0600), no open journal; `$SC/libcheck.sh --llm-env-file /etc/hlmemo/llm.env` PASS (interim).

### §2 Install the R3 env under the deploy lock (D-108 steps 3–4)
- [x] 2.1 `bash deploy/scripts/install_llm_env.sh --state $S` (local main = $SHA; the installed key and the §0.4 caps are kept) →
  "kept the operator's …", backup, "recreating librarian and api … (deploy lock held)", `RESULT librarian PASS … release=r3`,
  "switch complete". → 12-env-r3.log
- [x] 2.2 Independent re-check: `$SC/libcheck.sh --llm-env-file /etc/hlmemo/llm.env --release r3` PASS (per-task fallbacks
  risk_judge=openrouter-qwen38-27b-fast, synthesis=openrouter, query_rewrite=openrouter; fallback glm53-flash);
  `$VSSH 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api python -c "from hlmemo.config import get_settings as g; s=g(); print(s.query_rewrite, s.retrieval_source_cap, s.librarian_role, s.librarian_concurrency, s.llm_budget_hour_usd, s.llm_budget_day_usd, s.llm_budget_month_usd)"'`
  → `False False observer 3 1 2 10`; release-state has no `env_switch`; `$OPS status` chains.

### §3 Remote gates on R3 (R3 env active)
- [x] 3.1 `bash deploy/scripts/remote_gates.sh --url $URL --state $S --insecure --librarian` (drill ON: rehearsal VM) → the 8 base
  gates + risk-check (`judged=true`) + librarian (observer, 0 links/closes/applied) + backup-restore: all PASS. → 13-remote-gates-r3.log

### §4 G-LIVE-C on the VM, primary forced unavailable in the api (D-098 method, deployed)
- [x] 4.1 `$VSSH 'cp -p /etc/hlmemo/api.env /etc/hlmemo/api.env.r3glc && echo HLM_LLM_BASE_URL=http://127.0.0.1:9/v1 >> /etc/hlmemo/api.env && cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh up -d --no-deps --wait api'`
  (api.env is api-only and not part of the llm.env fingerprint); `GLC_T0` = VM `now()`.
- [x] 4.2 `uv run --frozen python $SC/glive_c_vm.py run --repo $REPO --reader rk-reader --world $R3/rk-world.json --reps 4 --out $R3/glive-c-vm`
  → `RESULT G-LIVE-C-VM`. → 14-glive-c-vm.log
- [x] 4.3 Ledger since GLC_T0 by profile/outcome (only luna errors + qwen38-27b-fast); spend added to the tally.
- [x] 4.4 Restore: `$VSSH 'mv -f /etc/hlmemo/api.env.r3glc /etc/hlmemo/api.env && ! grep -q HLM_LLM_BASE_URL /etc/hlmemo/api.env && cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh up -d --no-deps --wait api'`;
  `$SC/libcheck.sh --llm-env-file /etc/hlmemo/llm.env --release r3` PASS.

### §5 Host gates on the final SHA (sequential; quiet host; never during §6)
- [x] 5.1 `quiet` → load1 < 3 and no other heavy job (else wait; report after 30 min). DB (own):
  `psql $PG/postgres -c 'CREATE DATABASE hlm_r3reh_gate TEMPLATE hlm_r3reh_world'`.
- [x] 5.2 `HLM_TEST_DSN=$PG/hlm_r3reh_gate make gate-release` (flags off: the R3 config) → G3 ≥ 0.90, G4 p95 ≤ 500 ms, local G-L3 p95 ≤ 500 ms
  both bodies; `quiet` again; `DROP DATABASE hlm_r3reh_gate`; `git checkout -- HARDWARE.md` if the G4 test rewrote it. → 17-gate-release.log
- [ ] 5.3 G-LIVE-B (D-094 mapping): `uv run --frozen python eval/live/run_w2b.py --profile openrouter-gpt6-luna --fallback openrouter-glm53-flash --chain openrouter-gpt6-luna+openrouter-glm53-flash --reps 3 --max-usd 1.0 --env-file $REPO/.env --out $R3/glive-b`
  → Verdict PASS per config (R2: luna / openrouter / chain luna+openrouter PASS). → 19-glive-b.log
- [ ] 5.4 G-LIVE-D: `psql $PG/postgres -c 'CREATE DATABASE hlm_r3reh_live'`; `HLM_GLIVE_D=1 HLM_GLIVE_D_MAX_USD=0.5 HLM_W2E_ENV_FILE=$REPO/.env HLM_GLIVE_D_OUT=$R3/glive-d HLM_TEST_DSN=$PG/hlm_r3reh_live uv run --frozen pytest -q -s -p no:cacheprovider tests/integration/test_w2e_glive_d.py`
  → PASS per profile (R2: luna +0.161, openrouter +0.194 min Δ). → 20-glive-d.log
- [ ] 5.5 (only if §4 fails/ambiguous) host G-LIVE-C reference: `HLM_GLIVE_C_WIRING=1 HLM_GLIVE_C_MAX_USD=0.6 HLM_GLIVE_C_KEY_FILE=$REPO/.env HLM_GLIVE_C_OUT=$R3/glive-c-host HLM_TEST_DSN=$PG/hlm_r3reh_live uv run --frozen pytest -q -s tests/integration/test_wf_glive_c_wiring.py`.

### §6 G-L3 on the 2 vCPU VM (librarian ON observer, rewrite OFF, quiet host)
- [ ] 6.0 `quiet` (load1 < 3, nothing heavy), clock ≤ 2 s, `$OPS status`: worker/librarian ready=0 and `spend_hour_usd` ≤ 0.3 (HOUR cap 1:
  a tripped cap would pause the librarian and soften the test); `$VSSH 'cat > /tmp/r3-backlog.sql' < $SC/backlog.sql`.
- [ ] 6.1 `$SC/sampler.sh $R3/sampler-gl3.log & SAMP=$!`
- [ ] 6.2 Neutral: `uv run --frozen python $SC/gl3_vm_r3.py --device r3-load-mac --project r3-load --body neutral --run gl3n` → q_p95 ≤ 500, 100/100, 0 errors.
- [ ] 6.3 Drain (`$OPS status` ready=0; record time), `quiet`, then identifier: `… --body identifier --run gl3i --offset 100`.
- [ ] 6.4 `kill $SAMP`; `python3 $SC/analyze.py $R3/sampler-gl3.log <t_start> <t_end>` per run. R2 reference: p95 268 / 375 ms. → 15-gl3-vm.log

### §7 Observer = 0 librarian mutations (after §3–§6, drained)
- [ ] 7.1 `{ echo "\\set since $BASE_EVENT"; cat $SC/observer.sql; } | $SC/vmsql.sh` → only signal_upsert, links/versions by librarian 0,
  superseded/closed = baseline, 0 batches decided/applied, 0 applied questions; `$OPS librarian audit --project P --json` applied 0 for
  r3-base, r3-load, rk-main, gates-probe. → 16-observer.log

### §8 Drill (a): script rollback R3 → R2, then roll forward (all R3 measurements are recorded first: the rollback restores the pre-R3 dump)
- [x] 8.1 Poller; `PATH="$S/bin:$PATH" bash deploy/scripts/deploy.sh --rollback hlm-deploy` (NO manual env change) → "Rollback validated …",
  the env restore, "Rollback complete: 6902f91… is running again"; downtime. → 21-rollback.log
- [x] 8.2 R2 healthy: release-state (current 6902f91, pair consumed), api label 6902f91, `/etc/hlmemo/llm.env` has no HLM_ENV_RELEASE
  marker and fallback `openrouter` (the R2 env with the §0.4 caps), `$SC/libcheck.sh` (R2's own check) PASS, project counts = §0.5,
  `remote_gates.sh --url $URL --state $S --insecure --librarian --no-drill` all PASS. → 21b-r2-after-rollback.log
- [ ] 8.3 Roll forward: `deploy.sh hlm-deploy "$SHA"` (interim PASS) → `install_llm_env.sh --state $S` (R3 PASS) → libcheck `--release r3` →
  `remote_gates.sh … --librarian --no-drill` all PASS; release-state current=$SHA previous=6902f91 with `previous_llm_env`. → 22-roll-forward.log

### §9 Drill (b): VM snapshot restore (Hostinger analog), then back to R3
- [ ] 9.1 `limactl stop hlm-2604 && cp -c ~/.lima/_snapshots/hlm-2604-predeploy.disk ~/.lima/hlm-2604/disk && cp -c ~/.lima/_snapshots/hlm-2604-predeploy.vz-efi ~/.lima/hlm-2604/vz-efi && limactl start hlm-2604 && repin`
  → `/ready` 200; release-state current 6902f91 (no R3 trace), R2 env (caps 10/2/1), `$SC/libcheck.sh` PASS, counts = §0.5,
  `remote_gates.sh … --librarian --no-drill` all PASS. Time from stop to healthy recorded. → 23-snapshot-restore.log
- [ ] 9.2 Back to R3 (final state): `deploy.sh hlm-deploy "$SHA"` (image rebuilt: the snapshot predates it) → `install_llm_env.sh --state $S`
  → libcheck `--release r3` → `remote_gates.sh … --librarian --no-drill` all PASS. → 24-final-r3.log

### §10 End state and cleanup
- [ ] 10.1 Final `$OPS status`, `snap.sql`, VM spend (`llm_calls` since PHASE 2 start), `docker stats --no-stream`; tally closed.
- [ ] 10.2 VM stays at R3 ($SHA, R3 env, dev key, caps 10/2/1), then `limactl stop hlm-2604` (restart = `limactl start` + repin).
  Snapshot `hlm-2604-predeploy.*` kept until the prod release is accepted.
- [ ] 10.3 `kill $CAF`; drop own DBs `hlm_r3reh_gate`/`hlm_r3reh_live`/`hlm_r3reh_world`; shred `$R3/cfg/credentials.toml`.
- [ ] 10.4 `gitleaks dir` over `docs/bakeoff/rehearsal-r3` and this file (image tags abbreviated to 12 hex); commit on main; NO push.

## PHASE 2 results (2026-09-25, SHA 805f4cd; times UTC)
- §0 (13:41–13:42Z): 01-preconditions.log: code-only vs R2 confirmed (no alembic/compose/Dockerfile/pyproject/uv.lock/models.lock/deploy.sh diff; compose 99fecbf4…, head 0008; `query_rewrite`/`retrieval_source_cap` absent from src/hlmemo/config.py). Clock host/VM within 0.4 s, caffeinate on. VM = R2 6902f91, drained. No hlmemo-backup.timer on the VM. Owner D-121 edit mirrored on the R2 env (3/10/60 → HOUR=1 DAY=2 MONTH=10) + recreate, R2 check PASS (02a). Baseline BASE_EVENT=1843; projects r3-base 248, r3-load 163, rk-main 46, rk-shell 6, rk-secret 2; links 0, closed/superseded 0 (02). **Pre-deploy snapshot** `~/.lima/_snapshots/hlm-2604-predeploy.{disk,vz-efi}` at 13:42:20Z; stop → /ready 200 in 13 s, host key re-pinned `SHA256:ssoa4SJm…`, R2 check PASS (03).
- §1 (13:43–13:44Z) R3 cutover with the R2 env: rc 0, "Deployment ready: 805f4cd…", `RESULT librarian PASS llm.env=present release=r2-env (D-108 interim …)`; **downtime 9.7 s** (R2 cutover 12.0 s); the image was built on the VM (~1 min, layer cache). release-state: current 805f4cd, previous 6902f91, `previous_llm_env=/etc/hlmemo/llm.env.release-6902f91…` (0600), previous_dump + image id recorded, no open journal (11, 11b, 11c).
- §2 (13:44:54Z) `install_llm_env.sh --state $S` under the deploy lock: backup of the R2 env, both services recreated, **`RESULT librarian PASS llm.env=present release=r3 manifest=r3`**, "switch complete", rc 0. Independent `libcheck.sh --llm-env-file … --release r3` PASS; per-task fallbacks risk_judge=qwen38-27b-fast, synthesis=openrouter, query_rewrite=openrouter; librarian fallback glm53-flash; api and librarian both: observer, concurrency 3, caps 1/2/10, no rewrite/cap field in the code; `env_switch` cleared, `pending_cleanup` [] (12).
- §3 (13:45Z) remote gates with `--librarian` and the drill: **11/11 PASS**: health-ready, unknown-path-404, tls-issuer, postgres-closed, probe, routes, hlm-cli, wan-latency (p50 16 / p95 45 ms), risk-check (judged=true, judge=ok), librarian (1 job in 10.0 s, observer, signal_upsert=1, links/closes 0, applied 0), backup-restore (restore 16 s) (13).
- §4 (13:46–14:00Z) G-LIVE-C on the VM, api primary forced to `http://127.0.0.1:9/v1` (api risk-judge chain printed with the dead URL): **PASS**. Catch per rep .975 / 1.000 / 1.000 / 1.000 (min .975), false-warn .075 / .050 / .075 / .025 (max .075); 297/320 `ok_fallback`, 23 `retrieval_only:timeout` (4 s cap), **0 answered by the primary**; risk_check p50 1773 / p95 4031 ms. Ledger: luna 7 http_error then 313 breaker_open (the breaker skipped the dead primary); qwen38-27b-fast 297 ok (p50 1627 / p95 3327 ms) + 23 timeout. Real cost $0.1745. api.env restored, primary URL back, R3 check PASS (14). (D-098 host reference: catch 1.000, false-warn max .075, 21 timeouts/320.)

### PAUSE 2026-09-25 ~14:12Z (coordinator request, at a safe point)
- **Done (all PASS):** §0 preconditions + pre-deploy snapshot; §1 R3 cutover with the R2 env (interim PASS, 9.7 s downtime); §2 R3 env install under the lock (`release=r3 manifest=r3` PASS); §3 remote gates 11/11 PASS; §4 G-LIVE-C on the VM PASS (catch min .975, false-warn max .075, 0 answered by the primary).
- **Interrupted, to redo:** §5.3 G-LIVE-B was killed mid-run: luna rep0 had finished (placement .984, contradiction exact .994, false supersede .000, positive recall/precision .972/.972, direction 1.000), and nothing was written to the result dir. Re-run §5.3 from the start. Its partial spend is only in the provider account, estimated < $0.03.
- **Not started:** §5.2 gate-release (the host was never quiet: another agent's integration suite used 2 cores for 20+ minutes, load 3.1–4.0), §5.4 G-LIVE-D, §6 VM G-L3, §7 observer, §8 drill (a) script rollback, §9 drill (b) snapshot restore, §10 cleanup.
- **VM state (left as is, running):** R3 805f4cd with the R3 env (`HLM_ENV_RELEASE=r3`, D-094 mapping, caps 1/2/10, dev key); api.env restored (no forced-down primary); `libcheck --release r3` PASS; release-state current 805f4cd / previous 6902f91 + `previous_llm_env` snapshot; no open journal; librarian idle (ready 0). Pre-deploy snapshot `~/.lima/_snapshots/hlm-2604-predeploy.*` kept. The own host DB `hlm_r3reh_world` is kept (G3 world template).
- **Background jobs:** all killed (G-LIVE-B, monitors, caffeinate). No deploy, rollback or install was running.
- **On resume:** start `caffeinate` again and check the clock (host sleep skews the VM clock; restart the VM + re-pin if it is off by more than 2 s). Then re-run §5.3 G-LIVE-B, §5.4 G-LIVE-D, §5.2 gate-release and §6 G-L3 (quiet host only), §7, §8, §9, §10.
- **Spend so far:** VM ledger $0.7296 (PHASE 1 $0.5492 + §3/§4 $0.1804) + G-LIVE-B partial (< $0.03, est.) ≈ **$0.76 of $3**.

### RESUME 2026-09-25 17:00Z
- 30-resume.log: caffeinate on. The VM clock was **9,703 s behind** (the Mac slept ~2.7 h; chrony still said 0.0005 s). Restarted the VM (R3 stays deployed), host key re-pinned (`SHA256:OpIj6nDT…`), clock in sync, `libcheck --release r3` PASS.
- §5.3 G-LIVE-B re-run from the start and §5.4 G-LIVE-D (own DB `hlm_r3reh_live`) started in the background at 17:01Z.
- §7 observer check #1 (16-observer-1.log, since BASE_EVENT 1843: R3 cutover, env switch, 11 gates, 320 risk_checks): librarian events 3, all `observer`; ops = 2 `signal_upsert` + 1 none; links_by_librarian 0, versions_by_librarian 0, versions superseded/closed 0/0, links 0, batches decided/applied 0/45; audit applied 0 for r3-base (224 open proposals), r3-load, rk-main (3 open), gates-probe → **PASS**. A second check follows G-L3 on the final state.
- **§8.1 drill (a) script rollback: PASS** (21-rollback.log). `deploy.sh --rollback hlm-deploy` with NO manual env change: "Rollback validated: 805f4cd… -> 6902f91…", **`llm.env: restored /etc/hlmemo/llm.env from llm.env.release-6902f91…`**, R2 stack started, "Rollback complete", both secret-bearing env copies deleted from `pending_cleanup`; rc 0; **downtime 11.3 s**. The current DB was saved to `/var/backups/hlmemo/daily/…` (the review-77 residual "daily rotation can delete the rollback safety dump" is visible in this path; nothing was rotated).
- **§8.2 R2 healthy: PASS** (21b): release-state current 6902f91, `rolled_back_from` 805f4cd, pair consumed; api image = R2's id; llm.env = the R2 env (no `HLM_ENV_RELEASE`, fallback `openrouter`, caps 1/2/10 as snapshotted); R2's own check PASS; data = the pre-upgrade dump (max event 1843; r3-base 248, r3-load 163, rk-main 46, rk-shell 6, rk-secret 2 = the §0.5 baseline); remote gates 10/10 PASS (drill skipped; risk-check judged=true).
- **§8.3 roll-forward, attempt 1 (17:03Z): failed SAFELY, environment issue.** `deploy.sh hlm-deploy 805f4cd…` recorded the R2 env for rollback, then `dc pull db caddy` could not resolve `pgvector/pgvector:0.8.6-pg17` (`dial tcp …:443: i/o timeout`) → "Deployment failed before writers stopped; previous stack remains running", rc 1; R2 kept serving, the env copy was queued in `pending_cleanup`; `install_llm_env.sh` then refused correctly ("the deployed release predates R3 … nothing changed", rc 3). Cause: **the owner's network currently has no IPv4 path to Docker Hub** (every registry-1.docker.io IPv4 address times out from the Mac as well; the Mac reaches it over IPv6, 401 in 0.5 s; the lima VM is IPv4-only; OpenRouter and GitHub are fine from the VM). The runner pulls db/caddy unconditionally and builds with `--pull`, so a deploy needs the registry even when every image is cached. Production (Vilnius VPS) has normal IPv4, but a Docker Hub outage would block a prod deploy or roll-forward the same (safe) way. The gate lines in 22-roll-forward.log therefore ran against **R2**, not R3.
- **§5.2 gate-release: PASS** (17-gate-release.log; 17:09–17:11Z, quiet host: load1 2.52 before, 3.09 after; nothing heavy running): `make gate-release` on 805f4cd, own clone `hlm_r3reh_gate` of `hlm_r3reh_world`, flags off (the R3 config): **G3 Recall@5 0.980** (98/100; TR 1.000, DE 0.939, EN 1.000, identifier-heavy 0.960); **G4 p95 251.0 ms** (p50 157.5, p99 443.4, 300 queries / 3 callers, M3 Pro); **local G-L3** (real api + librarian processes, stalled/503 provider, librarian concurrency 3) **p95 358.1 ms identifier / 417.1 ms neutral** during 100 writes (max 518 / 589 ms); 6 passed in 138 s. The clone was dropped and HARDWARE.md restored. D-098 reference: G3 0.980, G4 p95 274, G-L3 378/417.
- Evidence logs: `*.log` is gitignored (as for rehearsal-r2, which force-added its logs), so the earlier commits carried only the scripts. From 714951b on, the logs are force-added after a gitleaks scan.
- Waiting for the IPv4 path, then: roll-forward retry → drill (b) → back to R3 → G-L3 → observer #2. gate-release runs as soon as the host is quiet.

## Review-77 residuals (D-122/D-123) — watched during PHASE 2
| Residual | Hit? | Note |
|---|---|---|
| resumed deploy publishes a half-switched stack | | no deploy was killed/resumed in the drills |
| backup timer / restore race a rollback or recovery | | no hlmemo-backup.timer on the VM |
| daily rotation deletes the rollback safety dump | | |
| install and deploy/rollback journals deadlock | | |
| persistent image selection not reverted on recovery | | |
| provenance fingerprint ignores budget/guard fields, does not require both services | | |
| extra fallback overrides / unreadable env fail open | | |
| a preserved wrong key passes (probe does not authenticate) | | |
