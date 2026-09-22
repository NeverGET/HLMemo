#!/usr/bin/env bash
# Neutral judge harness for the R2 deploy bake-off (docs/bakeoff/r2/BRIEF.md).
#
#   run_gates.sh WORKTREE PROJECT HTTP_PORT HTTPS_PORT
#
# Runs, against the contestant worktree WORKTREE (absolute path):
#   G-D1  compose config -q with the example env file(s) (per-service *.env.example since D-035)
#   G-D5  terraform init -backend=false / validate / fmt -check -recursive
#   G-D6  shellcheck deploy/**/*.sh (globstar on, i.e. recursive)
#   G-D7  gitleaks dir deploy --no-banner
#   G-D2  prod stack up in local TLS mode (HLM_TLS_MODE=internal, HLM_DOMAIN=localhost) under
#         compose project PROJECT; curl /ready == 200, /anything-else == 404; db_exposure.sh
#   G-D3  judge's own probe.py smoke over TLS (device bootstrap + MCP initialize + tools/list);
#         optionally also the contestant's smoke via JUDGE_CONTESTANT_SMOKE_CMD
#   G-D4  MANUAL — instructions are printed (contestant interfaces differ)
#
# Environment knobs (all optional):
#   JUDGE_UP_CMD / JUDGE_DOWN_CMD  shell commands run with `bash -c` inside WORKTREE, taken from the
#       contestant's deploy/RUNBOOK.md. They can use $JUDGE_ENV_FILE, $JUDGE_PROJECT,
#       $JUDGE_HTTP_PORT, $JUDGE_HTTPS_PORT (exported). Defaults:
#         docker compose -p PROJECT -f deploy/compose.prod.yaml --env-file $JUDGE_ENV_FILE up -d --wait
#         docker compose -p PROJECT -f deploy/compose.prod.yaml --env-file $JUDGE_ENV_FILE down -v
#   JUDGE_ENV_FILE         use this env file as-is instead of generating one from .env.prod.example
#   JUDGE_ENV_EXTRA        path of extra KEY=VALUE lines appended to the generated env file
#   JUDGE_CONTESTANT_SMOKE_CMD  contestant's own G-D3 script invocation (run inside WORKTREE)
#   JUDGE_SKIP_STACK=1     only run the static gates (G-D1, G-D5, G-D6, G-D7)
#   JUDGE_KEEP_UP=1        do not tear the stack down at the end (e.g. to run G-D4 by hand)
#
# Every command and its exit code go to docs/bakeoff/r2/judge/logs/<PROJECT>-<timestamp>.log.
# The generated env (with a random admin token) is logs/<PROJECT>-<timestamp>.secret.env (0600,
# gitignored). The admin token is never printed.
set -uo pipefail
shopt -s globstar nullglob

usage() { echo "usage: run_gates.sh WORKTREE PROJECT HTTP_PORT HTTPS_PORT" >&2; exit 64; }
[[ $# -eq 4 ]] || usage
WORKTREE="$1"; PROJECT="$2"; HTTP_PORT="$3"; HTTPS_PORT="$4"
[[ "$WORKTREE" == /* && -d "$WORKTREE" ]] || { echo "WORKTREE must be an existing absolute path" >&2; exit 64; }
[[ "$HTTP_PORT" =~ ^[0-9]+$ && "$HTTPS_PORT" =~ ^[0-9]+$ ]] || usage
case "$PROJECT" in hlmemo|"") echo "refusing to touch compose project '$PROJECT' (dev stack)" >&2; exit 64;; esac
for p in "$HTTP_PORT" "$HTTPS_PORT"; do
  case "$p" in 8765|5432) echo "refusing port $p (dev stack)" >&2; exit 64;; esac
done

JUDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$JUDGE_DIR/../../../.." && pwd)"
LOG_DIR="$JUDGE_DIR/logs"
mkdir -p "$LOG_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/$PROJECT-$TS.log"
STATE="$LOG_DIR/$PROJECT-$TS.probe-state.secret.json"
: >"$LOG"

declare -A RESULT
for g in G-D1 G-D2 G-D3 G-D4 G-D5 G-D6 G-D7; do RESULT[$g]="FAIL"; done
RESULT[G-D4]="MANUAL"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }

# run LABEL DIR CMD...   -> runs CMD in DIR, logs command + output + exit code, returns the rc
run() {
  local label="$1" dir="$2"; shift 2
  local rc
  log "[$label] \$ (cd $dir && $*)"
  ( cd "$dir" && "$@" ) >>"$LOG" 2>&1
  rc=$?
  log "[$label] exit=$rc"
  return "$rc"
}

# run_sh LABEL DIR 'shell string'
run_sh() {
  local label="$1" dir="$2" cmd="$3"
  local rc
  log "[$label] \$ (cd $dir && bash -c '$cmd')"
  ( cd "$dir" && bash -c "$cmd" ) >>"$LOG" 2>&1
  rc=$?
  log "[$label] exit=$rc"
  return "$rc"
}

log "judge run: worktree=$WORKTREE project=$PROJECT http=$HTTP_PORT https=$HTTPS_PORT"
log "worktree HEAD: $(git -C "$WORKTREE" rev-parse --short HEAD 2>/dev/null || echo '?') branch: $(git -C "$WORKTREE" branch --show-current 2>/dev/null || echo '?')"
git -C "$WORKTREE" status --short >>"$LOG" 2>&1

# ------------------------------------------------------------------ static gates

# G-D1
# D-035 split secrets into per-service env files; render G-D1 against the shipped examples when present.
GD1_ENV=()
for svc in APP API DB BACKUP; do
  lower=$(printf '%s' "$svc" | tr '[:upper:]' '[:lower:]')
  if [[ -f "$WORKTREE/deploy/$lower.env.example" ]]; then GD1_ENV+=("HLM_${svc}_ENV_FILE=$WORKTREE/deploy/$lower.env.example"); fi
done
if run G-D1 "$WORKTREE" env "${GD1_ENV[@]}" docker compose -f deploy/compose.prod.yaml --env-file deploy/.env.prod.example config -q; then
  RESULT[G-D1]=PASS
fi

# G-D5
g5=0
run G-D5 "$WORKTREE" terraform -chdir=deploy/terraform/hetzner init -backend=false -input=false -no-color || g5=1
run G-D5 "$WORKTREE" terraform -chdir=deploy/terraform/hetzner validate -no-color || g5=1
run G-D5 "$WORKTREE" terraform fmt -check -recursive deploy/terraform || g5=1
[[ $g5 -eq 0 ]] && RESULT[G-D5]=PASS

# G-D6 (globstar: deploy/**/*.sh is recursive and includes deploy/*.sh)
sh_files=()
pushd "$WORKTREE" >/dev/null || exit 1
sh_files=(deploy/**/*.sh)
popd >/dev/null || exit 1
log "[G-D6] files: ${sh_files[*]:-(none)}"
if [[ ${#sh_files[@]} -eq 0 ]]; then
  log "[G-D6] no .sh files under deploy/ -> FAIL"
elif run G-D6 "$WORKTREE" shellcheck "${sh_files[@]}"; then
  RESULT[G-D6]=PASS
fi

# G-D7
if run G-D7 "$WORKTREE" gitleaks dir deploy --no-banner; then
  RESULT[G-D7]=PASS
fi

# ------------------------------------------------------------------ stack gates

print_table() {
  {
    echo
    echo "==================== $PROJECT  ($TS) ===================="
    printf '%-6s %s\n' GATE RESULT
    for g in G-D1 G-D2 G-D3 G-D4 G-D5 G-D6 G-D7; do printf '%-6s %s\n' "$g" "${RESULT[$g]}"; done
    echo "log: $LOG"
  } | tee -a "$LOG"
}

if [[ "${JUDGE_SKIP_STACK:-0}" == "1" ]]; then
  RESULT[G-D2]="SKIPPED"; RESULT[G-D3]="SKIPPED"
  print_table
  exit 0
fi

# generated env for local TLS mode
if [[ -n "${JUDGE_ENV_FILE:-}" ]]; then
  ENV_FILE="$JUDGE_ENV_FILE"
  log "using provided env file $ENV_FILE"
  ADMIN_TOKEN="$(sed -n 's/^HLM_ADMIN_TOKEN=//p' "$ENV_FILE" | tail -n1 | tr -d "\"'")"
else
  ENV_FILE="$LOG_DIR/$PROJECT-$TS.secret.env"
  ADMIN_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
  (
    umask 077
    # Keep every example line; fill EMPTY password/secret values (not S3/AWS/LLM keys) with random
    # hex so `${VAR:?}` interpolations work; then force the judge overrides (last one wins).
    python3 - "$WORKTREE/deploy/.env.prod.example" <<'PY'
import re, secrets, sys
skip = re.compile(r"S3|AWS|LLM|OPENAI|OPENROUTER|ANTHROPIC|API_KEY|HCLOUD|SSH", re.I)
fill = re.compile(r"PASSWORD|SECRET", re.I)
try:
    lines = open(sys.argv[1]).read().splitlines()
except OSError:
    lines = []
for line in lines:
    m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
    if m and fill.search(m.group(1)) and not skip.search(m.group(1)) and m.group(2).strip().strip("\"'") == "":
        print(f"{m.group(1)}={secrets.token_hex(24)}")
    else:
        print(line)
PY
    echo ""
    echo "# ---- judge overrides ($TS) ----"
    echo "HLM_TLS_MODE=internal"
    echo "HLM_DOMAIN=localhost"
    echo "HLM_ADMIN_TOKEN=$ADMIN_TOKEN"
    echo "COMPOSE_PROJECT_NAME=$PROJECT"
    echo "BAKE_PROJECT=$PROJECT"
    echo "BAKE_HTTP_PORT=$HTTP_PORT"
    echo "BAKE_HTTPS_PORT=$HTTPS_PORT"
    echo "HLM_HTTP_PORT=$HTTP_PORT"
    echo "HLM_HTTPS_PORT=$HTTPS_PORT"
    echo "HTTP_PORT=$HTTP_PORT"
    echo "HTTPS_PORT=$HTTPS_PORT"
    if [[ -n "${JUDGE_ENV_EXTRA:-}" ]]; then
      echo "# ---- JUDGE_ENV_EXTRA ----"
      cat "$JUDGE_ENV_EXTRA"
    fi
  ) >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "generated env file $ENV_FILE (keys: $(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$ENV_FILE" | sort -u | tr '\n' ' '))"
fi
REG_SECRET="$(sed -n 's/^HLM_REGISTRATION_SECRET=//p' "$ENV_FILE" | tail -n1 | tr -d "\"'")"

export JUDGE_ENV_FILE="$ENV_FILE" JUDGE_PROJECT="$PROJECT" JUDGE_HTTP_PORT="$HTTP_PORT" JUDGE_HTTPS_PORT="$HTTPS_PORT"
export BAKE_PROJECT="$PROJECT" BAKE_HTTP_PORT="$HTTP_PORT" BAKE_HTTPS_PORT="$HTTPS_PORT"
export HLM_TLS_MODE=internal HLM_DOMAIN=localhost

UP_CMD="${JUDGE_UP_CMD:-docker compose -p \"\$JUDGE_PROJECT\" -f deploy/compose.prod.yaml --env-file \"\$JUDGE_ENV_FILE\" up -d --wait}"
DOWN_CMD="${JUDGE_DOWN_CMD:-docker compose -p \"\$JUDGE_PROJECT\" -f deploy/compose.prod.yaml --env-file \"\$JUDGE_ENV_FILE\" down -v}"

teardown() {
  if [[ "${JUDGE_KEEP_UP:-0}" == "1" ]]; then
    log "JUDGE_KEEP_UP=1: leaving project $PROJECT running; tear down with: (cd $WORKTREE && JUDGE_ENV_FILE=$ENV_FILE JUDGE_PROJECT=$PROJECT bash -c '$DOWN_CMD')"
  else
    run_sh teardown "$WORKTREE" "$DOWN_CMD" || true
  fi
}

g2=0
if ! run_sh G-D2-up "$WORKTREE" "$UP_CMD"; then
  g2=1
  log "[G-D2] stack did not come up healthy"
  run_sh G-D2-diag "$WORKTREE" "docker compose -p \"\$JUDGE_PROJECT\" ps -a; docker compose -p \"\$JUDGE_PROJECT\" logs --no-color --tail 80" || true
fi
run_sh G-D2-ps "$WORKTREE" "docker compose -p \"\$JUDGE_PROJECT\" ps -a" || true

# readiness can lag `up --wait` slightly behind Caddy's cert issuance; poll up to 120 s
code=000
for _ in $(seq 1 60); do
  code="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost:$HTTPS_PORT/ready" || true)"
  [[ "$code" == "200" ]] && break
  sleep 2
done
log "[G-D2] \$ curl -sk https://localhost:$HTTPS_PORT/ready -> $code"
[[ "$code" == "200" ]] || g2=1
code404="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost:$HTTPS_PORT/anything-else" || true)"
log "[G-D2] \$ curl -sk https://localhost:$HTTPS_PORT/anything-else -> $code404"
[[ "$code404" == "404" ]] || g2=1
codehttp="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$HTTP_PORT/ready" || true)"
log "[G-D2] (info) \$ curl -s http://localhost:$HTTP_PORT/ready -> $codehttp (redirect expected, not gated)"
run G-D2-db "$JUDGE_DIR" bash "$JUDGE_DIR/db_exposure.sh" "$PROJECT" "$HTTP_PORT" "$HTTPS_PORT" || g2=1
[[ $g2 -eq 0 ]] && RESULT[G-D2]=PASS

# G-D3: judge probe (authoritative) + optional contestant smoke
g3=0
log "[G-D3] \$ HLM_ADMIN_TOKEN=<redacted> uv run --frozen python $JUDGE_DIR/probe.py --base https://localhost:$HTTPS_PORT --insecure --mode smoke --project judge --state $STATE"
( cd "$REPO_ROOT" && HLM_ADMIN_TOKEN="$ADMIN_TOKEN" HLM_REGISTRATION_SECRET="$REG_SECRET" \
    uv run --frozen python "$JUDGE_DIR/probe.py" --base "https://localhost:$HTTPS_PORT" --insecure \
    --mode smoke --project judge --state "$STATE" ) 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
log "[G-D3] probe exit=$rc"
[[ $rc -eq 0 ]] || g3=1
if [[ -n "${JUDGE_CONTESTANT_SMOKE_CMD:-}" ]]; then
  HLM_ADMIN_TOKEN="$ADMIN_TOKEN" run_sh G-D3-contestant "$WORKTREE" "$JUDGE_CONTESTANT_SMOKE_CMD" || g3=1
else
  log "[G-D3] JUDGE_CONTESTANT_SMOKE_CMD not set; contestant smoke not run (judge probe is authoritative)"
fi
[[ $g3 -eq 0 ]] && RESULT[G-D3]=PASS

# G-D4 instructions
{
  echo
  echo "---- G-D4 (backup/restore) — run by hand; stack must be up (re-run with JUDGE_KEEP_UP=1) ----"
  echo "  export HLM_ADMIN_TOKEN=\$(sed -n 's/^HLM_ADMIN_TOKEN=//p' $ENV_FILE)"
  echo "  P=\"uv run --frozen python $JUDGE_DIR/probe.py --base https://localhost:$HTTPS_PORT --insecure --state $STATE\""
  echo "  M=judge-drill-\$(date +%s)"
  echo "  (cd $REPO_ROOT && \$P --mode write-marker --marker \$M)          # 1. write via API"
  echo "  (cd $WORKTREE && <contestant backup.sh per RUNBOOK>)             # 2. backup"
  echo "  (cd $WORKTREE && <wipe DB per RUNBOOK, or down -v db volume + up>) # 3. wipe"
  echo "  (cd $REPO_ROOT && \$P --mode read-marker --marker \$M --wait 5)  # 3b. must FAIL now"
  echo "  (cd $WORKTREE && <contestant restore.sh per RUNBOOK>)            # 4. restore"
  echo "  (cd $REPO_ROOT && \$P --mode read-marker --marker \$M)          # 5. must PASS"
  echo "  Also run the contestant's own: (cd $WORKTREE && deploy/scripts/drill_backup_restore.sh ...)"
  echo "  Note: if the wipe drops the devices table, the stored device token is gone; the probe then"
  echo "  re-bootstraps a fresh device and step 5 still proves the data came back."
} | tee -a "$LOG"

teardown
print_table
