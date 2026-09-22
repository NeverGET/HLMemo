#!/usr/bin/env bash
# Uploaded as a file and run detached; never execute this runner through bash -s.
# All non-interactive children deliberately receive EOF.
# shellcheck disable=SC2217
set -Eeuo pipefail
umask 077
run_dir=$5
rollback_config=
finish() {
  result=$?
  # Only the owning shell may publish completion or remove its snapshot.
  (( BASH_SUBSHELL == 0 )) || exit "$result"
  trap - EXIT ERR INT TERM
  set +e
  rm -f -- "${rollback_config:-}" </dev/null || true
  printf '%s\n' "$result" > "$run_dir/status.tmp"
  mv -f "$run_dir/status.tmp" "$run_dir/status" </dev/null
  exit "$result"
}
trap finish EXIT
exec </dev/null
trap 'exit 130' INT
trap 'exit 143' TERM
ref=$1
repository=$2
app_dir=$3
export HLM_ENV_FILE=$4
parent_dir=$(dirname "$app_dir")
test -r "$HLM_ENV_FILE" || { echo "Missing readable env file: $HLM_ENV_FILE" >&2; exit 1; }
mkdir -p "$parent_dir"
exec 9>"$parent_dir/.deploy.lock"
flock -n 9 </dev/null || { echo 'Another deployment is running' >&2; exit 1; }
# A previous uncatchable SIGKILL/reboot can leave a private snapshot; only the
# exclusive deployment owner may sweep it. Normal exits always remove it.
rm -f -- "$parent_dir"/.rollback-compose.* </dev/null
new_checkout=0
if [[ ! -d $app_dir/.git ]]; then
  git clone --no-checkout -- "$repository" "$app_dir" </dev/null
  new_checkout=1
fi
cd "$app_dir"
[[ $(git </dev/null remote get-url origin) == "$repository" ]] || { echo 'Origin differs from requested repository' >&2; exit 1; }
previous=
if [[ $new_checkout == 0 ]]; then
  [[ -z $(git </dev/null status --porcelain --untracked-files=no) ]] || { echo 'Refusing to replace tracked local changes' >&2; exit 1; }
fi
if [[ -f $parent_dir/current-ref ]]; then
  previous=$(cat "$parent_dir/current-ref")
  [[ $previous =~ ^([a-f0-9]{40}|[a-f0-9]{64})$ ]] || { echo 'Invalid last successful deployment ref' >&2; exit 1; }
  [[ $(git </dev/null rev-parse --verify "$previous^{commit}") == "$previous" ]] || { echo 'Last successful deployment commit is unavailable' >&2; exit 1; }
elif [[ $new_checkout == 0 && ! -e $parent_dir/.deploy-managed ]]; then
  # Adopt an existing checkout once. Subsequent failed attempts must never turn
  # their checked-out (but unverified) commit into the rollback baseline.
  previous=$(git </dev/null rev-parse --verify 'HEAD^{commit}')
  printf '%s\n' "$previous" > "$parent_dir/current-ref"
fi
touch "$parent_dir/.deploy-managed"
git fetch --prune origin "$ref" </dev/null
revision=$(git </dev/null rev-parse --verify 'FETCH_HEAD^{commit}')
writers_stopped=0
migration_started=0
pre_upgrade_dump=
rollback() {
  docker compose -p "$COMPOSE_PROJECT" -f "$rollback_config" "$@"
}
deployment_failed() {
  status=${1:-$?}
  (( BASH_SUBSHELL == 0 )) || exit "$status"
  trap - ERR
  trap 'exit 130' INT
  trap 'exit 143' TERM
  set +e
  if [[ $writers_stopped == 1 && -n $previous && -s $rollback_config ]]; then
    echo 'Deployment failed; recovering the previous stack.' >&2
    recovery_ok=1
    dc stop caddy api worker </dev/null >&2 || recovery_ok=0
    if [[ $migration_started == 0 ]]; then
      # No schema mutation happened: even a failed cleanup stop must not prevent
      # restarting the old services that were already stopped successfully.
      recovery_ok=1
    else
      if [[ $recovery_ok == 1 && -f $pre_upgrade_dump ]]; then
        rollback up -d --no-deps --wait --wait-timeout 180 db </dev/null >&2 || recovery_ok=0
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
    git checkout --detach "$previous" </dev/null >&2 || recovery_ok=0
    if [[ $recovery_ok == 1 ]] && rollback up -d --no-deps --wait --wait-timeout 300 db api worker caddy </dev/null >&2; then
      echo "Previous stack restored: $previous" >&2
    else
      echo "Automatic recovery failed; recovery ref=$previous dump=$pre_upgrade_dump (see $run_dir/log)." >&2
    fi
  elif [[ -n $previous ]]; then
    git checkout --detach "$previous" </dev/null >&2 || true
    echo 'Deployment failed before writers stopped; previous stack remains running.' >&2
  else
    echo 'Initial deployment failed; no previous stack exists to recover.' >&2
  fi
  exit "$status"
}
trap deployment_failed ERR
trap 'deployment_failed 130' INT
trap 'deployment_failed 143' TERM
git checkout --detach "$revision" </dev/null
test -f deploy/compose.prod.yaml || { echo 'Requested ref has no production compose file' >&2; false; }

# shellcheck source=deploy/scripts/common.sh
source deploy/scripts/common.sh
dc config -q </dev/null
# Use the target's split env layout even when upgrading a pre-D-034 checkout.
# Running image IDs preserve old code; reject incomplete baselines before stop.
# Capture the running image IDs before build. Pin image IDs: builds may replace
# the production tag, and rollback must never run migrations from either revision.
rollback_config=$(mktemp "$parent_dir/.rollback-compose.XXXXXX")
chmod 600 "$rollback_config"
if [[ -n $previous ]]; then
  dc config --format json </dev/null > "$rollback_config"
  python3 - "$rollback_config" <<'PYCONFIG'
import json, subprocess, sys
path = sys.argv[1]
with open(path) as stream:
    config = json.load(stream)
project = config["name"]
for service in ("db", "api", "worker", "caddy"):
    ids = subprocess.check_output(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}", "--filter", f"label=com.docker.compose.service={service}"], text=True, stdin=subprocess.DEVNULL).split()
    if len(ids) != 1:
        sys.exit(f"Cannot capture rollback: expected one existing {service} container")
    if ids:
        config["services"][service]["image"] = subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}", ids[0]], text=True, stdin=subprocess.DEVNULL).strip()
    config["services"][service].pop("build", None)
# Compose config already escapes dollars for a safe round-trip. Preserve that
# representation verbatim; escaping again would corrupt credentials/healthchecks.
with open(path, "w") as stream:
    json.dump(config, stream)
PYCONFIG
fi
# Use Compose's dotenv parser; never source a secrets file as executable shell.
domain=$(env_value HLM_DOMAIN)
[[ $domain =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { echo 'HLM_DOMAIN must be a DNS hostname' >&2; exit 1; }
dc pull db caddy </dev/null
dc build --pull api worker migrate </dev/null
# Compilation/model download and the snapshot both happen while writers are live.
if [[ -n $(dc ps </dev/null -q --status running db) ]]; then
  pre_upgrade_dump=$(bash deploy/backup/backup.sh --pre-upgrade "${previous:-$revision}" </dev/null)
fi
if [[ -n $previous ]]; then
  [[ -f $pre_upgrade_dump ]] || { echo 'Refusing upgrade without a recovery dump' >&2; false; }
fi
# Hold the operation fd across downtime/recovery; a timer or manual restore cannot
# race migrations. Lock contention before stop leaves all services running.
if [[ -n $pre_upgrade_dump ]]; then
  exec 8>"$(dirname "$(dirname "$pre_upgrade_dump")")/.operation.flock"
  flock -n 8 </dev/null || { echo 'Another backup/restore started; aborting before stop' >&2; false; }
fi
# Mark first so even a partially failed stop restarts the previous stack.
writers_stopped=1
dc stop caddy api worker </dev/null
dc up -d --wait --wait-timeout 180 db </dev/null
migration_started=1
dc run --rm --no-deps migrate </dev/null
dc up -d --no-deps --wait --wait-timeout 300 db api worker caddy </dev/null
# Test application readiness and Caddy routing without public DNS/ACME. Only
# failures before this boundary may restore the snapshot automatically.
dc exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=8).read()" </dev/null
dc exec -T caddy wget -q -O /dev/null http://127.0.0.1:8081/ready </dev/null
trap - ERR
trap 'exit 130' INT
trap 'exit 143' TERM
# Once internally healthy, preserve accepted writes even on marker/disk or public
# network errors. Publish rollback markers only for this successful cutover.
if [[ -n $previous ]]; then
  printf '%s\n' "$previous" > "$parent_dir/previous-ref.tmp"
  printf '%s\n' "$pre_upgrade_dump" > "$parent_dir/previous-dump.tmp"
  mv "$parent_dir/previous-ref.tmp" "$parent_dir/previous-ref" </dev/null
  mv "$parent_dir/previous-dump.tmp" "$parent_dir/previous-dump" </dev/null
fi
printf '%s\n' "$revision" > "$parent_dir/current-ref.tmp"
mv "$parent_dir/current-ref.tmp" "$parent_dir/current-ref" </dev/null
if ! curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 \
  --connect-timeout 10 --max-time 20 "https://$domain/ready" </dev/null; then
  echo 'External HTTPS readiness failed; internally healthy new stack left running (no database rollback). Check DNS A/AAAA, firewall, ACME and Caddy logs; retry the public /ready probe.' >&2
  exit 1
fi
printf '\n'
# Release the operation lock before the pruning helper acquires it again.
exec 8>&-
if ! bash deploy/backup/backup.sh --prune-pre-upgrade </dev/null; then
  echo 'WARNING: deployment healthy, but pre-upgrade retention failed; run backup.sh --prune-pre-upgrade.' >&2
fi
printf 'Deployment ready: %s (%s)\n' "$revision" "$domain"
