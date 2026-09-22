#!/usr/bin/env bash
# Usage: deploy.sh hlmdeploy@SERVER GIT_REF [REPOSITORY_URL]
# Secrets are installed separately at HLM_REMOTE_ENV; never passed over argv.
# All non-interactive children deliberately receive EOF.
# shellcheck disable=SC2217
set -Eeuo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo 'Usage: deploy.sh USER@HOST GIT_REF [REPOSITORY_URL]' >&2
  exit 64
fi
host=$1
ref=$2
repository=${3:-https://github.com/NeverGET/HLMemo.git}
remote_dir=${HLM_REMOTE_DIR:-/opt/hlmemo/app}
remote_env=${HLM_REMOTE_ENV:-/etc/hlmemo/prod.env}
[[ $host =~ ^[a-zA-Z0-9][a-zA-Z0-9@._:-]*$ ]] || { echo 'Invalid SSH host (use raw IPv6 without brackets)' >&2; exit 64; }
[[ $ref =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]*$ ]] || { echo 'Invalid git ref' >&2; exit 64; }
[[ $remote_dir =~ ^/[a-zA-Z0-9_./-]+$ && $remote_env =~ ^/[a-zA-Z0-9_./-]+$ ]] || {
  echo 'Remote paths must be absolute and contain no shell metacharacters' >&2; exit 64;
}
[[ $repository != -* && $repository != *$'\n'* ]] || { echo 'Invalid repository URL' >&2; exit 64; }

# Upload a complete file before launching. SSH is only transport/observation, never
# the runner's script input or output; disconnecting cannot signal its children.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
printf -v command 'umask 077; mkdir -p %q/.deploy-runs; mktemp -d %q/.deploy-runs/run.XXXXXXXX' "$(dirname "$remote_dir")" "$(dirname "$remote_dir")"
run_dir=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -- "$host" "$command" </dev/null)
[[ $run_dir =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'Invalid remote run directory' >&2; exit 1; }
printf 'Remote deployment log: %s:%s/log (final exit code: %s/status)\n' "$host" "$run_dir" "$run_dir" >&2
printf -v command 'umask 077; cat > %q/deploy.sh' "$run_dir"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -- "$host" "$command" < "$script_dir/remote-deploy.sh"
printf -v command 'umask 077; command -v nohup setsid bash >/dev/null || exit 1; : >%q/log; nohup setsid bash %q/deploy.sh %q %q %q %q %q </dev/null >%q/log 2>&1 &' \
  "$run_dir" "$run_dir" "$ref" "$repository" "$remote_dir" "$remote_env" "$run_dir" "$run_dir"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -- "$host" "$command" </dev/null
# Fetch bounded increments. A failed observer exits with guidance; the remote job
# keeps running, and its status can be read later without launching a second job.
export LC_ALL=C
offset=1
while :; do
  printf -v command 'if test -f %q/status; then tail -c +%s %q/log; printf "\\nHLM_DEPLOY_STATUS="; cat %q/status; else tail -c +%s %q/log; fi' \
    "$run_dir" "$offset" "$run_dir" "$run_dir" "$offset" "$run_dir"
  if ! output=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -- "$host" "$command" </dev/null); then
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
  # Command substitution strips trailing newlines; re-fetch them on the next poll.
  if [[ -n $output ]]; then
    printf '%s' "$output"
    offset=$((offset + ${#output}))
  fi
  sleep "${HLM_DEPLOY_POLL_SECONDS:-1}" </dev/null
done
