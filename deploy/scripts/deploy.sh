#!/usr/bin/env bash
# Usage: deploy.sh hlmdeploy@SERVER GIT_REF [REPOSITORY_URL]
# Secrets are installed separately at HLM_REMOTE_ENV; never passed over argv.
set -euo pipefail

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

# %q encodes each argument for the Ubuntu deployment user's bash login shell.
printf -v remote_command 'bash -s -- %q %q %q %q' "$ref" "$repository" "$remote_dir" "$remote_env"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -- "$host" "$remote_command" <<'REMOTE'
set -euo pipefail
ref=$1
repository=$2
app_dir=$3
export HLM_ENV_FILE=$4
parent_dir=$(dirname "$app_dir")
test -r "$HLM_ENV_FILE" || { echo "Missing readable env file: $HLM_ENV_FILE" >&2; exit 1; }
mkdir -p "$parent_dir"
exec 9>"$parent_dir/.deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
new_checkout=0
if [[ ! -d $app_dir/.git ]]; then
  git clone --no-checkout -- "$repository" "$app_dir"
  new_checkout=1
fi
cd "$app_dir"
[[ $(git remote get-url origin) == "$repository" ]] || { echo 'Origin differs from requested repository' >&2; exit 1; }
previous=
if [[ $new_checkout == 0 ]]; then
  [[ -z $(git status --porcelain --untracked-files=no) ]] || { echo 'Refusing to replace tracked local changes' >&2; exit 1; }
fi
if [[ -f $parent_dir/current-ref ]]; then
  previous=$(cat "$parent_dir/current-ref")
  [[ $previous =~ ^([a-f0-9]{40}|[a-f0-9]{64})$ ]] || { echo 'Invalid last successful deployment ref' >&2; exit 1; }
  [[ $(git rev-parse --verify "$previous^{commit}") == "$previous" ]] || { echo 'Last successful deployment commit is unavailable' >&2; exit 1; }
elif [[ $new_checkout == 0 && ! -e $parent_dir/.deploy-managed ]]; then
  # Adopt an existing checkout once. Subsequent failed attempts must never turn
  # their checked-out (but unverified) commit into the rollback baseline.
  previous=$(git rev-parse --verify 'HEAD^{commit}')
  printf '%s\n' "$previous" > "$parent_dir/current-ref"
fi
touch "$parent_dir/.deploy-managed"
git fetch --prune origin "$ref"
revision=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
if [[ -n $previous && $previous != "$revision" ]]; then
  printf '%s\n' "$previous" > "$parent_dir/previous-ref"
fi
git checkout --detach "$revision"
test -f deploy/compose.prod.yaml || { echo 'Requested ref has no production compose file' >&2; exit 1; }

# shellcheck source=deploy/scripts/common.sh
source deploy/scripts/common.sh
dc config -q
# Use Compose's dotenv parser; never source a secrets file as executable shell.
domain=$(env_value HLM_DOMAIN)
[[ $domain =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { echo 'HLM_DOMAIN must be a DNS hostname' >&2; exit 1; }
dc pull db caddy
dc build --pull api worker migrate
# Compilation/model download happens while the old application is still live.
# Failures after this point deliberately leave writers stopped: migrations are
# not assumed reversible. Use RUNBOOK.md to choose an appropriate recovery.
deployment_failed() {
  status=$?
  trap - ERR
  dc stop caddy api worker >&2 || true
  echo "Deployment failed; writers stopped. Inspect logs and recover with the matching ref/dump." >&2
  exit "$status"
}
trap deployment_failed ERR
dc stop caddy api worker
if [[ -n $(dc ps -q --status running db) ]]; then
  HLM_BACKUP_DIR=${HLM_BACKUP_DIR:-/var/backups/hlmemo} bash deploy/backup/backup.sh
fi
dc up -d --wait --wait-timeout 180 db
dc run --rm --no-deps migrate
dc up -d --wait --wait-timeout 300
# Verify public DNS + real certificate validation, not only container health.
curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 \
  --connect-timeout 10 --max-time 20 "https://$domain/ready"
printf '\n'
printf '%s\n' "$revision" > "$parent_dir/current-ref"
trap - ERR
printf 'Deployment ready: %s (%s)\n' "$revision" "$domain"
REMOTE
