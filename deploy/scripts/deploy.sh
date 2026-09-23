#!/usr/bin/env bash
# Usage: deploy.sh [--accept-compose-change=SHA256] hlmdeploy@SERVER GIT_REF [REPOSITORY_URL]
# Secrets stay on the server; every child receives EOF, never script input.
# --accept-compose-change: explicit operator acknowledgement of a release whose
# deploy/compose.prod.yaml differs from the running one. The value must be the sha256 of the
# requested ref's compose file (`git show REF:deploy/compose.prod.yaml | shasum -a 256`); the
# runner re-checks it before build, backup or writer shutdown and refuses on any mismatch.
# shellcheck disable=SC2217
set -Eeuo pipefail

# --rollback / --accept-release USER@HOST: the current release's deploy/scripts/rollback.sh runs
# detached (same lock, log, heartbeat and status as a deployment); see RUNBOOK "Rollback".
mode=deploy
if [[ ${1:-} == --rollback || ${1:-} == --accept-release ]]; then
  mode=${1#--}
  mode=${mode%-release}
  shift
  [[ $# == 1 ]] || { echo "Usage: deploy.sh --rollback|--accept-release USER@HOST" >&2; exit 64; }
  set -- "$1" rollback-runner
fi
accept_compose=
if [[ ${1:-} == --accept-compose-change=* ]]; then
  accept_compose=${1#--accept-compose-change=}
  shift
  [[ $accept_compose =~ ^[a-f0-9]{64}$ ]] || { echo '--accept-compose-change needs a sha256 hex digest' >&2; exit 64; }
fi
if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo 'Usage: deploy.sh [--accept-compose-change=SHA256] USER@HOST GIT_REF [REPOSITORY_URL]' >&2
  exit 64
fi
host=$1
ref=$2
repository=${3:-https://github.com/NeverGET/HLMemo.git}
remote_dir=${HLM_REMOTE_DIR:-/opt/hlmemo/app}
remote_env=${HLM_REMOTE_ENV:-/etc/hlmemo/prod.env}
timeout=${HLM_DEPLOY_TIMEOUT_SECONDS:-1800}
poll=${HLM_DEPLOY_POLL_SECONDS:-1}
[[ $host =~ ^[a-zA-Z0-9][a-zA-Z0-9@._:-]*$ ]] || { echo 'Invalid SSH host (use raw IPv6 without brackets)' >&2; exit 64; }
[[ $ref =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]*$ ]] || { echo 'Invalid git ref' >&2; exit 64; }
[[ $remote_dir =~ ^/[a-zA-Z0-9_./-]+$ && $remote_env =~ ^/[a-zA-Z0-9_./-]+$ ]] || {
  echo 'Remote paths must be absolute and contain no shell metacharacters' >&2; exit 64;
}
[[ $repository != -* && $repository != *$'\n'* ]] || { echo 'Invalid repository URL' >&2; exit 64; }
[[ $timeout =~ ^[1-9][0-9]*$ && $poll =~ ^[0-9]+([.][0-9]+)?$ ]] || {
  echo 'HLM_DEPLOY_TIMEOUT_SECONDS must be a positive integer; poll interval must be nonnegative' >&2; exit 64;
}
command -v python3 >/dev/null || { echo 'Deployment observer requires local python3' >&2; exit 1; }
deadline=$((SECONDS + timeout))
run_dir='(not created yet)'
observer_timeout() {
  printf '\n' >&2
  echo "Deployment observation timed out after ${timeout}s; remote work may still be running. Inspect $run_dir/log, $run_dir/pid, $run_dir/heartbeat and $run_dir/status on $host; do not launch a duplicate deploy." >&2
  exit 124
}
# Bound even a stalled SSH command by the remaining overall observation budget.
ssh_bounded() {
  local remaining=$((deadline - SECONDS)) result=0
  (( remaining > 0 )) || return 124
  python3 -c 'import subprocess, sys
try:
    sys.exit(subprocess.run(sys.argv[2:], timeout=int(sys.argv[1]), check=False).returncode)
except subprocess.TimeoutExpired:
    sys.exit(124)
' "$remaining" ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
    -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -- "$host" "$1" </dev/null || result=$?
  return "$result"
}
printf -v command 'umask 077; mkdir -p %q/.deploy-runs; mktemp -d %q/.deploy-runs/run.XXXXXXXX' "$(dirname "$remote_dir")" "$(dirname "$remote_dir")"
result=0
run_dir=$(ssh_bounded "$command") || result=$?
(( result != 124 )) || observer_timeout
(( result == 0 )) || exit "$result"
[[ $run_dir =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'Invalid remote run directory' >&2; exit 1; }
printf 'Remote deployment log: %s:%s/log (final exit code: %s/status)\n' "$host" "$run_dir" "$run_dir" >&2

# This bootstrap only owns transport, locking and ref resolution. Deployment
# behavior comes exclusively from the fetched immutable commit, never local files.
bootstrap=$(cat <<'BOOTSTRAP'
set -Eeuo pipefail
umask 077
ref=$1 repository=$2 app_dir=$3 remote_env=$4 run_dir=$5
# Protocol 3 runners that know it read the acknowledgement from the environment.
export HLM_ACCEPT_COMPOSE_SHA256=${6:-}
mode=${7:-deploy}
printf '%s\n' "$$" > "$run_dir/pid"
finish() {
  result=$?
  trap - EXIT
  printf '%s\n' "$result" > "$run_dir/status.tmp"
  mv -f "$run_dir/status.tmp" "$run_dir/status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# This monitor does not inherit the deployment lock. It exits when the runner
# disappears (including an unreaped zombie after SIGKILL).
bash -c '
  while kill -0 "$1" 2>/dev/null; do
    state=$(ps -o stat= -p "$1") || break
    [[ $state != *Z* ]] || break
    date -u +%Y-%m-%dT%H:%M:%SZ > "$2/heartbeat"
    sleep 1
  done
' heartbeat "$$" "$run_dir" 9>&- </dev/null >/dev/null 2>&1 &
parent_dir=$(dirname "$app_dir")
mkdir -p "$parent_dir"
exec 9>"$parent_dir/.deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
export HLM_DEPLOY_LOCK_HELD=1 HLM_DEPLOY_NEW_CHECKOUT=0
if [[ $mode != deploy ]]; then
  cd "$app_dir"  # no clone, fetch or origin change: only the deployed checkout matters
  # Rollback/acceptance logic comes from the CURRENT release (it knows its own state file).
  current=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("current_ref") or "")' "$parent_dir/release-state.json" 2>/dev/null || true)
  [[ -n $current ]] || { echo 'No release-state.json: this host was not deployed by a runner that supports --rollback' >&2; exit 1; }
  git show "$current:deploy/scripts/rollback.sh" > "$run_dir/deploy.sh" || { echo "Current release $current has no deploy/scripts/rollback.sh" >&2; exit 1; }
  exec bash "$run_dir/deploy.sh" "$mode" "$app_dir" "$remote_env" "$run_dir"
fi
if [[ ! -d $app_dir/.git ]]; then
  git clone --no-checkout -- "$repository" "$app_dir"
  HLM_DEPLOY_NEW_CHECKOUT=1
fi
cd "$app_dir"
[[ $(git remote get-url origin) == "$repository" ]] || { echo 'Origin differs from requested repository' >&2; exit 1; }
if [[ $HLM_DEPLOY_NEW_CHECKOUT == 0 ]]; then
  [[ -z $(git status --porcelain --untracked-files=no) ]] || { echo 'Refusing to replace tracked local changes' >&2; exit 1; }
fi
git fetch --prune origin "$ref"
HLM_DEPLOY_PREPARED_REVISION=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
export HLM_DEPLOY_PREPARED_REVISION
if ! git show "$HLM_DEPLOY_PREPARED_REVISION:deploy/scripts/remote-deploy.sh" > "$run_dir/deploy.sh"; then
  echo "Requested ref $ref ($HLM_DEPLOY_PREPARED_REVISION) predates the detached deployment runner or has no deploy/scripts/remote-deploy.sh; refusing before checkout, build, backup or restore. Select a ref containing the deployment tooling." >&2
  exit 1
fi
[[ -s $run_dir/deploy.sh ]] || { echo 'Requested ref contains an empty deployment runner' >&2; exit 1; }
# Read the marker as data, never source an unverified runner. Older runners can
# overwrite the currently selected immutable image tag with the wrong revision.
if ! grep -qx 'HLM_RUNNER_PROTOCOL=3' "$run_dir/deploy.sh"; then
  echo "Requested ref $ref ($HLM_DEPLOY_PREPARED_REVISION) has an unsupported deployment runner protocol; protocol 3 is required. Refusing before checkout, build, backup or restore." >&2
  exit 1
fi
# Give a new clone a clean worktree without adopting it as a baseline.
if [[ $HLM_DEPLOY_NEW_CHECKOUT == 1 ]]; then
  git checkout --detach "$HLM_DEPLOY_PREPARED_REVISION"
  touch "$parent_dir/.deploy-managed"
fi
# Protocol 3 inherits the exclusive lock across the exec handoff.
exec bash "$run_dir/deploy.sh" "$HLM_DEPLOY_PREPARED_REVISION" "$repository" "$app_dir" "$remote_env" "$run_dir"
BOOTSTRAP
)
printf -v launch 'nohup setsid bash -c %q deploy-bootstrap %q %q %q %q %q %q %q </dev/null >%q/log 2>&1 &' \
  "$bootstrap" "$ref" "$repository" "$remote_dir" "$remote_env" "$run_dir" "$accept_compose" "$mode" "$run_dir"
# shellcheck disable=SC2016
printf -v command 'umask 077; for required in nohup setsid bash git flock ps grep; do command -v "$required" >/dev/null || { echo "Missing required remote command: $required" >&2; exit 1; }; done; : >%q/log; %s launcher=$!; printf "%%s\n" "$launcher" >%q/launcher.pid; attempts=0; while ! test -s %q/pid && ! test -f %q/status; do attempts=$((attempts + 1)); if ! kill -0 "$launcher" 2>/dev/null || test "$attempts" -ge 50; then echo "Detached deployment runner failed to start; inspect the remote log" >&2; exit 1; fi; sleep 0.1; done' \
  "$run_dir" "$launch" "$run_dir" "$run_dir" "$run_dir"
result=0
ssh_bounded "$command" || result=$?
(( result != 124 )) || observer_timeout
(( result == 0 )) || exit "$result"
export LC_ALL=C
offset=1
while :; do
  (( SECONDS < deadline )) || observer_timeout
  # shellcheck disable=SC2016
  printf -v command 'if test -f %q/status; then tail -c +%s %q/log; printf "\nHLM_DEPLOY_STATUS="; cat %q/status; else tail -c +%s %q/log; pid=$(cat %q/pid); state=$(ps -o stat= -p "$pid" 2>/dev/null) || state=; if ! kill -0 "$pid" 2>/dev/null || test -z "$state" || test "${state#*Z}" != "$state"; then if test -f %q/status; then printf "\nHLM_DEPLOY_STATUS="; cat %q/status; else printf "\nHLM_DEPLOY_RUNNER_GONE=%%s" "$pid"; fi; fi; fi' \
    "$run_dir" "$offset" "$run_dir" "$run_dir" "$offset" "$run_dir" "$run_dir" "$run_dir" "$run_dir"
  result=0
  output=$(ssh_bounded "$command") || result=$?
  (( result != 124 )) || observer_timeout
  if (( result != 0 )); then
    printf '\n' >&2
    echo "SSH observation interrupted; deployment continues. Inspect $run_dir/log and $run_dir/status on $host." >&2
    exit 255
  fi
  if [[ $output == *$'\nHLM_DEPLOY_STATUS='* ]]; then
    status=${output##*$'\nHLM_DEPLOY_STATUS='}
    output=${output%$'\nHLM_DEPLOY_STATUS='*}
    [[ -z $output ]] || printf '%s\n' "$output"
    [[ $status =~ ^[0-9]+$ && $status -le 255 ]] || { echo 'Invalid remote status' >&2; exit 1; }
    exit "$status"
  fi
  if [[ $output == *$'\nHLM_DEPLOY_RUNNER_GONE='* ]]; then
    pid=${output##*$'\nHLM_DEPLOY_RUNNER_GONE='}
    output=${output%$'\nHLM_DEPLOY_RUNNER_GONE='*}
    [[ -z $output ]] || printf '%s\n' "$output"
    printf '\n' >&2
    echo "Deployment runner PID $pid is gone without final status (SIGKILL/OOM or launcher failure). Inspect $run_dir/log and $run_dir/heartbeat on $host before recovery." >&2
    exit 1
  fi
  # Command substitution strips trailing newlines; re-fetch them on the next poll.
  if [[ -n $output ]]; then
    printf '%s' "$output"
    offset=$((offset + ${#output}))
  fi
  python3 -c 'import sys, time; time.sleep(min(float(sys.argv[1]), max(0, int(sys.argv[2]))))' \
    "$poll" "$((deadline - SECONDS))" </dev/null
done
