#!/usr/bin/env bash
# Operator wrapper (W0a, D-052/D-061): run `python -m hlmemo.ops <args>` inside the production api
# container over SSH. Operator workstation only; there is no admin HTTP route to call instead.
#
#   deploy/scripts/hlm_ops.sh [--state DIR] <ops args...>
#   deploy/scripts/hlm_ops.sh --state deploy/.local/HOST device mint --name my-mac --class personal \
#     --grant my-project:write \
#     | hlm device login --name my-mac --token-stdin --server https://FQDN/mcp
#
# The SSH target is `hlm-deploy` from <state>/ssh_config (written by first_deploy.sh). <state> is
# --state, else $HLM_OPS_STATE, else the only deploy/.local/*/ directory holding a deploy.conf.
# Every argument is quoted with printf %q for the remote bash; ssh gets -n and </dev/null, so it
# can never swallow the caller's stdin (D-035/D-059 lesson). A minted token travels only on
# stdout; it is never an argument, and this script never echoes or logs it.
set -Eeuo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
state=${HLM_OPS_STATE:-}
if [[ ${1:-} == --state ]]; then
  (($# >= 2)) || { echo 'hlm_ops: --state needs a directory' >&2; exit 64; }
  state=$2
  shift 2
fi
(($# >= 1)) || { echo 'usage: hlm_ops.sh [--state DIR] <device|project|status> ...' >&2; exit 64; }
if [[ -z $state ]]; then
  candidates=()
  for conf in "$REPO_ROOT"/deploy/.local/*/deploy.conf; do
    [[ -f $conf ]] && candidates+=("$(dirname "$conf")")
  done
  ((${#candidates[@]} == 1)) || {
    echo "hlm_ops: found ${#candidates[@]} deploy/.local/*/deploy.conf; pass --state DIR or set HLM_OPS_STATE" >&2
    exit 64
  }
  state=${candidates[0]}
fi
ssh_config=${HLM_OPS_SSH_CONFIG:-$state/ssh_config}
[[ -f $ssh_config ]] || { echo "hlm_ops: no SSH config at $ssh_config (run first_deploy.sh first)" >&2; exit 64; }
remote_dir=${HLM_REMOTE_DIR:-/opt/hlmemo/app}
remote_env=${HLM_REMOTE_ENV:-/etc/hlmemo/prod.env}
[[ $remote_dir =~ ^/[a-zA-Z0-9_./-]+$ && $remote_env =~ ^/[a-zA-Z0-9_./-]+$ ]] || {
  echo 'hlm_ops: remote paths must be absolute and contain no shell metacharacters' >&2; exit 64;
}

quoted=''
for arg in "$@"; do
  printf -v q '%q' "$arg"
  quoted+=" $q"
done
# stack.sh resolves the Compose project and split env files exactly like deploy.sh does.
printf -v command 'cd %q && HLM_ENV_FILE=%q exec bash deploy/scripts/stack.sh exec -T api python -m hlmemo.ops%s' \
  "$remote_dir" "$remote_env" "$quoted"
exec ssh -n -F "$ssh_config" hlm-deploy "$command" </dev/null
