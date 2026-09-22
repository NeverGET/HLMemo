#!/usr/bin/env bash
# Usage: deploy.sh hlmdeploy@SERVER GIT_REF [REPOSITORY_URL]
# Secrets are installed separately at HLM_REMOTE_ENV; never passed over argv.
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

# %q encodes each argument for the Ubuntu deployment user's bash login shell.
printf -v remote_command 'bash -s -- %q %q %q %q' "$ref" "$repository" "$remote_dir" "$remote_env"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -- "$host" "$remote_command" <<'REMOTE'
set -Eeuo pipefail
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
# Capture the running stack before checkout/build. Pin image IDs: builds may replace
# the production tag, and rollback must never run migrations from either revision.
rollback_config=$(mktemp "$parent_dir/.rollback-compose.XXXXXX")
chmod 600 "$rollback_config"
trap 'rm -f "$rollback_config"' EXIT
if [[ -n $previous ]]; then
  # shellcheck source=deploy/scripts/common.sh
  source deploy/scripts/common.sh
  dc config --format json > "$rollback_config"
  python3 - "$rollback_config" <<'PYCONFIG'
import json, subprocess, sys
path = sys.argv[1]
with open(path) as stream:
    config = json.load(stream)
project = config["name"]
for service in ("db", "api", "worker", "caddy"):
    ids = subprocess.check_output(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}", "--filter", f"label=com.docker.compose.service={service}"], text=True).split()
    if ids:
        config["services"][service]["image"] = subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}", ids[0]], text=True).strip()
    config["services"][service].pop("build", None)
# Compose config already escapes dollars for a safe round-trip. Preserve that
# representation verbatim; escaping again would corrupt credentials/healthchecks.
with open(path, "w") as stream:
    json.dump(config, stream)
PYCONFIG
fi
writers_stopped=0
migration_started=0
pre_upgrade_dump=
rollback() {
  docker compose -p "$COMPOSE_PROJECT" -f "$rollback_config" "$@"
}
deployment_failed() {
  status=${1:-$?}
  trap - ERR INT TERM
  set +e
  if [[ $writers_stopped == 1 && -n $previous && -s $rollback_config ]]; then
    echo 'Deployment failed; recovering the previous stack.' >&2
    recovery_ok=1
    dc stop caddy api worker >&2 || recovery_ok=0
    if [[ $migration_started == 0 ]]; then
      # No schema mutation happened: even a failed cleanup stop must not prevent
      # restarting the old services that were already stopped successfully.
      recovery_ok=1
    else
      if [[ $recovery_ok == 1 && -f $pre_upgrade_dump ]]; then
        rollback up -d --no-deps --wait --wait-timeout 180 db >&2 || recovery_ok=0
        # shellcheck disable=SC2016
        rollback exec -T db sh -eu -c '
          case "$POSTGRES_DB" in postgres|template0|template1|"") exit 64;; esac
          dropdb --username="$POSTGRES_USER" --if-exists --force -- "$POSTGRES_DB"
          createdb --username="$POSTGRES_USER" --owner="$POSTGRES_USER" --template=template0 -- "$POSTGRES_DB"
          pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction
        ' < "$pre_upgrade_dump" >&2 || recovery_ok=0
      else
        echo 'Cannot safely restore: writers did not stop or recovery dump unavailable.' >&2
        recovery_ok=0
      fi
    fi
    git checkout --detach "$previous" >&2 || recovery_ok=0
    if [[ $recovery_ok == 1 ]] && rollback up -d --no-deps --wait --wait-timeout 300 db api worker caddy >&2; then
      echo "Previous stack restored: $previous" >&2
    else
      echo "Automatic recovery failed; use previous-ref and previous-dump markers in $parent_dir." >&2
    fi
  elif [[ -n $previous ]]; then
    git checkout --detach "$previous" >&2 || true
    echo 'Deployment failed before writers stopped; previous stack remains running.' >&2
  else
    echo 'Initial deployment failed; no previous stack exists to recover.' >&2
  fi
  exit "$status"
}
trap deployment_failed ERR
trap 'deployment_failed 130' INT
trap 'deployment_failed 143' TERM
git checkout --detach "$revision"
test -f deploy/compose.prod.yaml || { echo 'Requested ref has no production compose file' >&2; false; }

# shellcheck source=deploy/scripts/common.sh
source deploy/scripts/common.sh
dc config -q
# Use Compose's dotenv parser; never source a secrets file as executable shell.
domain=$(env_value HLM_DOMAIN)
[[ $domain =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { echo 'HLM_DOMAIN must be a DNS hostname' >&2; exit 1; }
dc pull db caddy
dc build --pull api worker migrate
# Compilation/model download and the snapshot both happen while writers are live.
if [[ -n $(dc ps -q --status running db) ]]; then
  pre_upgrade_dump=$(bash deploy/backup/backup.sh --pre-upgrade "${previous:-$revision}")
fi
if [[ -n $previous ]]; then
  [[ -f $pre_upgrade_dump ]] || { echo 'Refusing upgrade without a recovery dump' >&2; false; }
  printf '%s\n' "$previous" > "$parent_dir/previous-ref"
  printf '%s\n' "$pre_upgrade_dump" > "$parent_dir/previous-dump"
fi
# Hold the operation fd across downtime/recovery; a timer or manual restore cannot
# race migrations. Lock contention before stop leaves all services running.
if [[ -n $pre_upgrade_dump ]]; then
  exec 8>"$(dirname "$(dirname "$pre_upgrade_dump")")/.operation.flock"
  flock -n 8 || { echo 'Another backup/restore started; aborting before stop' >&2; false; }
fi
# Mark first so even a partially failed stop restarts the previous stack.
writers_stopped=1
dc stop caddy api worker
dc up -d --wait --wait-timeout 180 db
migration_started=1
dc run --rm --no-deps migrate
dc up -d --wait --wait-timeout 300
# Verify public DNS + real certificate validation, not only container health.
curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 \
  --connect-timeout 10 --max-time 20 "https://$domain/ready"
printf '\n'
printf '%s\n' "$revision" > "$parent_dir/current-ref"
trap - ERR INT TERM
printf 'Deployment ready: %s (%s)\n' "$revision" "$domain"
REMOTE
