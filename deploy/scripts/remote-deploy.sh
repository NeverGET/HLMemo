#!/usr/bin/env bash
# Extracted from the fetched release and run detached; never run through bash -s.
# All non-interactive children deliberately receive EOF.
# shellcheck disable=SC2217
set -Eeuo pipefail
# Static compatibility marker read by the observer before this file is executed.
HLM_RUNNER_PROTOCOL=3
export HLM_RUNNER_PROTOCOL
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
test -w "$(dirname "$HLM_ENV_FILE")" || { echo 'Env directory must be writable by the deploy user for atomic release publication (see RUNBOOK).' >&2; exit 1; }
mkdir -p "$parent_dir"
if [[ ${HLM_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
  exec 9>"$parent_dir/.deploy.lock"
  flock -n 9 </dev/null || { echo 'Another deployment is running' >&2; exit 1; }
fi
# A previous uncatchable SIGKILL/reboot can leave a private snapshot; only the
# exclusive deployment owner may sweep it. Normal exits always remove it.
rm -f -- "$parent_dir"/.rollback-compose.* </dev/null
new_checkout=${HLM_DEPLOY_NEW_CHECKOUT:-0}
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
# release-state.json (atomic, Sol 36 M1) wins over the derived legacy marker files.
state_current=
if [[ -f $parent_dir/release-state.json ]]; then
  state_current=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("current_ref") or "")' "$parent_dir/release-state.json" </dev/null)
fi
if [[ -n $state_current || -f $parent_dir/current-ref ]]; then
  previous=${state_current:-$(cat "$parent_dir/current-ref")}
  [[ $previous =~ ^([a-f0-9]{40}|[a-f0-9]{64})$ ]] || { echo 'Invalid last successful deployment ref' >&2; exit 1; }
  [[ $(git </dev/null rev-parse --verify "$previous^{commit}") == "$previous" ]] || { echo 'Last successful deployment commit is unavailable' >&2; exit 1; }
elif [[ $new_checkout == 0 && ! -e $parent_dir/.deploy-managed ]]; then
  # Adopt an existing checkout once. Subsequent failed attempts must never turn
  # their checked-out (but unverified) commit into the rollback baseline.
  previous=$(git </dev/null rev-parse --verify 'HEAD^{commit}')
  printf '%s\n' "$previous" > "$parent_dir/current-ref"
fi
touch "$parent_dir/.deploy-managed"
if [[ -n ${HLM_DEPLOY_PREPARED_REVISION:-} ]]; then
  revision=$HLM_DEPLOY_PREPARED_REVISION
  [[ $revision == "$ref" ]] || { echo 'Prepared runner/ref mismatch' >&2; exit 1; }
else
  git fetch --prune origin "$ref" </dev/null
  revision=$(git </dev/null rev-parse --verify 'FETCH_HEAD^{commit}')
fi
# The runner's umask 077 checks files out 0600, but Compose bind-mounts some of
# them into non-root containers (worker runs as uid 10001). Checkout never
# rewrites unchanged files, so fix the mode after every checkout.
checkout_release() {
  git checkout --detach "$1" </dev/null || return
  local mounted
  for mounted in deploy/Caddyfile deploy/scripts/worker_entrypoint.py deploy/scripts/worker_health.py; do
    [[ ! -e $mounted ]] || chmod go+r "$mounted"
  done
}
writers_stopped=0
migration_started=0
pre_upgrade_dump=
# W0a (D-061): "<env file>|<backup>" pairs written by migrate_env_w0 in THIS run.
env_w0_restore=()
# Remove the retired HLM_ADMIN_TOKEN / HLM_REGISTRATION_SECRET from the host env files (device 1
# becomes disabled, spec §2 (ii)). Idempotent: an already-migrated file is left untouched and gets
# no new backup. Each changed file is first copied to <file>.pre-w0-<UTC stamp> (0600), recorded at
# once in release-state.json retired_backups (deleted by --accept-release). Only key
# names are logged, never values. Runs before cutover; a failed deployment restores the backups
# because the previous release's code would otherwise start with open registration.
migrate_env_w0() {
  local file backup stamp result
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  for file in "$HLM_ENV_FILE" "${HLM_APP_ENV_FILE:-}" "${HLM_API_ENV_FILE:-}"; do
    [[ -n $file && -f $file ]] || continue
    backup=$file.pre-w0-$stamp
    result=0
    python3 - "$file" "$backup" <<'PYENV' || result=$?
import os, re, shutil, sys
path, backup = sys.argv[1:]
retired = ("HLM_ADMIN_TOKEN", "HLM_REGISTRATION_SECRET")
pattern = re.compile(r"^\s*(?:export\s+)?(" + "|".join(retired) + r")\s*=")
with open(path) as stream:
    lines = stream.readlines()
removed = sorted({m.group(1) for m in map(pattern.match, lines) if m})
name = os.path.basename(path)
if not removed:
    print(f"migrate_env_w0: {name}: already migrated (no retired keys)")
    sys.exit(0)
shutil.copy2(path, backup)
os.chmod(backup, 0o600)
tmp = path + ".w0-tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.writelines(line for line in lines if not pattern.match(line))
shutil.copymode(path, tmp)
os.replace(tmp, path)
print(f"migrate_env_w0: {name}: removed {','.join(removed)} (backup {os.path.basename(backup)})")
sys.exit(10)
PYENV
    # Record the backup (it holds the retired secrets) durably BEFORE anything else happens, so
    # --accept-release deletes it even when this deployment fails and is auto-recovered.
    if [[ -e $backup ]]; then
      python3 deploy/scripts/release_state.py add-retired "$parent_dir" "$backup" </dev/null || return 1
    fi
    case $result in
      0) ;;
      10) env_w0_restore+=("$file|$backup") ;;
      *) echo "migrate_env_w0: failed on $(basename "$file")" >&2; return 1 ;;
    esac
  done
  printf 'migrate_env_w0: done (%s file(s) changed)\n' "${#env_w0_restore[@]}"
}
restore_env_w0() {
  local pair
  for pair in "${env_w0_restore[@]}"; do
    # The restored file now holds the same content, so the backup is redundant: remove it
    # (it stays recorded in release-state.json; accept tolerates an already-missing path).
    cp -p -- "${pair#*|}" "${pair%%|*}" </dev/null && rm -f -- "${pair#*|}" </dev/null &&
      printf 'migrate_env_w0: restored %s from its backup\n' "$(basename "${pair%%|*}")" >&2
  done
}
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
  restore_env_w0
  if [[ $writers_stopped == 1 && -n $previous && -s $rollback_config ]]; then
    echo 'Deployment failed; recovering the previous stack.' >&2
    recovery_ok=1
    dc stop caddy api worker librarian </dev/null >&2 || recovery_ok=0
    # W2a: a previous model without the librarian must not keep this release's librarian
    # container around (it would run new code against the restored, older schema).
    if [[ " ${rollback_services[*]} " != *" librarian "* ]]; then
      dc rm -f librarian </dev/null >&2 || recovery_ok=0
    fi
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
    checkout_release "$previous" >&2 || recovery_ok=0
    # Never let this shell's new-release image selection leak into recovery: select the
    # previous release explicitly and prove the rendered rollback model runs its pinned image.
    export HLM_IMAGE=${previous_image:-} HLM_IMAGE_REVISION=$previous
    if [[ $recovery_ok == 1 ]]; then
      rendered_image=$(rollback config --format json </dev/null | python3 -c 'import json,sys; s=json.load(sys.stdin)["services"]; i={s[n]["image"] for n in ("api","worker","librarian") if n in s}; print(s["api"]["image"] if len(i) == 1 else "MISMATCH")') || rendered_image=
      if [[ -n ${previous_id:-} && $rendered_image == "$previous_id" ]]; then
        printf 'Rollback image verified: %s (%s)\n' "$rendered_image" "$HLM_IMAGE" >&2
      else
        printf 'Rollback model selects %s, expected previous image %s; not starting it.\n' "${rendered_image:-<unrendered>}" "${previous_id:-<unknown>}" >&2
        recovery_ok=0
      fi
    fi
    if [[ $recovery_ok == 1 ]] && rollback up -d --no-deps --wait --wait-timeout 300 "${rollback_services[@]}" </dev/null >&2; then
      echo "Previous stack restored: $previous" >&2
    else
      echo "Automatic recovery failed; recovery ref=$previous dump=$pre_upgrade_dump (see $run_dir/log)." >&2
    fi
  elif [[ -n $previous ]]; then
    checkout_release "$previous" >&2 || true
    echo 'Deployment failed before writers stopped; previous stack remains running.' >&2
  else
    echo 'Initial deployment failed; no previous stack exists to recover.' >&2
  fi
  exit "$status"
}
# Services the rollback model runs (W2a: the librarian only when the previous model defines it).
rollback_services=(db api worker caddy)
trap deployment_failed ERR
trap 'deployment_failed 130' INT
trap 'deployment_failed 143' TERM
# A Compose model change needs the operator's explicit acknowledgement naming the exact new
# model (deploy.sh --accept-compose-change=<sha256>). Everything is validated here, before
# build, backup or writer shutdown; recovery then runs the PREVIOUS model (rendered below).
compose_changed=0
compose_hash=$(git show "$revision:deploy/compose.prod.yaml" </dev/null | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')
if [[ -n $previous ]] && ! git diff --quiet "$previous" "$revision" -- deploy/compose.prod.yaml </dev/null; then
  compose_changed=1
  if [[ -z ${HLM_ACCEPT_COMPOSE_SHA256:-} ]]; then
    echo "Compose model changed between releases; refusing automatic deployment before build/stop. Re-run deploy.sh --accept-compose-change=$compose_hash after reviewing the change (RUNBOOK \"Compose model changes\")." >&2
    false
  fi
fi
if [[ -n ${HLM_ACCEPT_COMPOSE_SHA256:-} ]]; then
  [[ $HLM_ACCEPT_COMPOSE_SHA256 == "$compose_hash" ]] || {
    echo "Accepted compose sha256 $HLM_ACCEPT_COMPOSE_SHA256 does not match $revision's compose.prod.yaml ($compose_hash); refusing before build/stop." >&2
    false
  }
  echo "Compose model change accepted: sha256 $compose_hash (changed=$compose_changed)"
fi
checkout_release "$revision"
test -f deploy/compose.prod.yaml || { echo 'Requested ref has no production compose file' >&2; false; }

# shellcheck source=deploy/scripts/common.sh
source deploy/scripts/common.sh
dc config -q </dev/null
# The guard above proves this is also the previous release's Compose model.
# Resolve it with current split env files, then pin the running images.
rollback_config=$(mktemp "$parent_dir/.rollback-compose.XXXXXX")
chmod 600 "$rollback_config"
if [[ -n $previous ]]; then
  if [[ $compose_changed == 1 ]]; then
    # Recovery must run the previous release's own Compose model, rendered with the current
    # (not yet migrated) env files, next to this checkout so relative paths resolve alike.
    previous_model=$(mktemp "$PWD/deploy/.compose-previous.XXXXXX")
    git show "$previous:deploy/compose.prod.yaml" </dev/null > "$previous_model"
    docker compose -p "$COMPOSE_PROJECT" -f "$previous_model" --env-file "$HLM_ENV_FILE" config --format json </dev/null > "$rollback_config"
    rm -f -- "$previous_model"
  else
    dc config --format json </dev/null > "$rollback_config"
  fi
  python3 - "$rollback_config" <<'PYCONFIG'
import json, subprocess, sys
path = sys.argv[1]
with open(path) as stream:
    config = json.load(stream)
project = config["name"]
# W2a: the librarian is captured only when the previous model defines it (the first W2a
# deployment runs over a model without it); where defined, exactly one container must exist.
for service in [s for s in ("db", "api", "worker", "librarian", "caddy") if s in config["services"]]:
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
  rollback_list=$(python3 -c 'import json,sys; s=json.load(open(sys.argv[1]))["services"]; print(" ".join(n for n in ("db","api","worker","librarian","caddy") if n in s))' "$rollback_config" </dev/null)
  read -ra rollback_services <<< "$rollback_list"
fi
# Use a separate immutable tag for each release, shared by migrate/api/worker.
# The persistent env stays on the baseline until the internal cutover succeeds.
configured_image=$(env_value HLM_IMAGE)
configured_image=${configured_image:-hlmemo:prod}
image_repository=${configured_image%%@*}
if [[ ${image_repository##*/} == *:* ]]; then image_repository=${image_repository%:*}; fi
if [[ -n $previous ]]; then
  previous_image="$image_repository:$previous"
  previous_id=$(python3 - "$rollback_config" <<'PYIMAGE'
import json, sys
with open(sys.argv[1]) as stream:
    services = json.load(stream)["services"]
image = services["api"]["image"]
if any(services[n]["image"] != image for n in ("worker", "librarian") if n in services):
    sys.exit("Cannot adopt baseline: api, worker and librarian run different images")
print(image)
PYIMAGE
)
  if existing_id=$(docker image inspect --format '{{.Id}}' "$previous_image" 2>/dev/null </dev/null); then
    [[ $existing_id == "$previous_id" ]] || { echo 'Previous release tag differs from running image; refusing to retag immutable release' >&2; false; }
  else
    docker image tag "$previous_id" "$previous_image" </dev/null
  fi
  python3 deploy/scripts/release_env.py "$HLM_ENV_FILE" "$previous_image" </dev/null
fi
export HLM_IMAGE="$image_repository:$revision"
export HLM_IMAGE_REVISION="$revision"
verify_release_image() {
  local actual_revision
  actual_revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$HLM_IMAGE" </dev/null) || return 1
  [[ $actual_revision == "$revision" ]] || {
    printf 'Release image revision mismatch: %s expected %s, found %s; refusing deployment.\n' "$HLM_IMAGE" "$revision" "${actual_revision:-<missing>}" >&2
    return 1
  }
}
# Use Compose's dotenv parser; never source a secrets file as executable shell.
domain=$(env_value HLM_DOMAIN)
[[ $domain =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { echo 'HLM_DOMAIN must be a DNS hostname' >&2; exit 1; }
dc pull db caddy </dev/null
if ! docker image inspect "$HLM_IMAGE" >/dev/null 2>&1 </dev/null; then
  # The librarian (W2a) runs the same ${HLM_IMAGE} tag as api/worker/migrate: no separate build.
  dc build --pull api worker migrate </dev/null
fi
# Existence alone is never evidence that a release tag contains the right code.
verify_release_image
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
# W0a (D-061): the new release refuses to start with the retired secrets' routes open; the old
# release keeps running on its already-loaded environment until the stop below.
migrate_env_w0
# Mark first so even a partially failed stop restarts the previous stack.
writers_stopped=1
dc stop caddy api worker librarian </dev/null
# Final, quiesced snapshot (Sol 34 #3): no write can commit between it and the migration, so a
# recovery restores everything the old release acknowledged. It becomes the rollback dump; the
# live snapshot above proved backups work before any downtime. We already hold the operation lock.
if [[ -n $pre_upgrade_dump ]]; then
  live_dump=$pre_upgrade_dump
  pre_upgrade_dump=$(HLM_OPERATION_LOCK_HELD=1 bash deploy/backup/backup.sh --pre-upgrade "${previous:-$revision}" </dev/null)
  [[ -f $pre_upgrade_dump ]] || { echo 'Final pre-upgrade dump missing' >&2; false; }
  # The quiesced dump supersedes the live one (a strict superset of acknowledged writes).
  rm -f -- "$live_dump" </dev/null
  printf 'Quiesced pre-upgrade dump: %s\n' "$pre_upgrade_dump"
fi
dc up -d --wait --wait-timeout 180 db </dev/null
verify_release_image
migration_started=1
dc run --rm --no-deps migrate </dev/null
verify_release_image
dc up -d --no-deps --wait --wait-timeout 300 db api worker librarian caddy </dev/null
# Test application readiness and Caddy routing without public DNS/ACME. Only
# failures before this boundary may restore the snapshot automatically.
dc exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=8).read()" </dev/null
dc exec -T caddy wget -q -O /dev/null http://127.0.0.1:8081/ready </dev/null
# W2a librarian: fresh heartbeat (it idles without provider calls while HLM_LIBRARIAN_ENABLED=false).
dc exec -T librarian python -m hlmemo.librarian.health 120 </dev/null
# W0a route table on the API's own loopback listener (the route filter applies to any listener).
# The checker mints a 10-minute ci device with hlmemo.ops inside the container and revokes it
# through the public self-revoke route. A failure here restores the previous stack.
dc exec -T api python - --routes --base http://127.0.0.1:8765 --mint-ops < deploy/scripts/check_edge.py
# Publishing the image is part of cutover: if it fails, restore the baseline
# rather than leaving a healthy new checkout with an old image selection.
python3 deploy/scripts/release_env.py "$HLM_ENV_FILE" "$HLM_IMAGE" </dev/null
trap - ERR
trap 'exit 130' INT
trap 'exit 143' TERM
# Once internally healthy, preserve accepted writes even on marker/disk or public
# network errors. Publish rollback markers only for this successful cutover.
# One atomic document (tmp + fsync + rename) holds the whole rollback tuple: previous ref, its
# quiesced dump, its image + verified ID and this run's env-file backups (retired secrets). The
# legacy current-ref/previous-ref/previous-dump files are derived from it for compatibility.
state_args=(--current "$revision")
if [[ -n $previous ]]; then
  state_args+=(--previous "$previous" --previous-dump "$pre_upgrade_dump" --previous-image "$previous_image" --previous-image-id "$previous_id")
  for pair in "${env_w0_restore[@]}"; do
    state_args+=(--env-backup "${pair%%|*}=${pair#*|}")
  done
fi
python3 deploy/scripts/release_state.py publish "$parent_dir" "${state_args[@]}" </dev/null
if ! curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 \
  --connect-timeout 10 --max-time 20 "https://$domain/ready" </dev/null; then
  echo 'External HTTPS readiness failed; internally healthy new stack left running (no database rollback). Check DNS A/AAAA, firewall, ACME and Caddy logs; retry the public /ready probe.' >&2
  exit 1
fi
printf '\n'
# W0a public route table through Caddy (RG-routes). Like the readiness probe above, a failure
# leaves the internally healthy stack running and fails the deployment without a DB rollback.
routes_token=$(dc exec -T api python -m hlmemo.ops device mint --name "deploy-routes-${revision:0:12}-$RANDOM" \
  --class ci --expires 10m </dev/null)
if ! HLM_ROUTES_TOKEN=$routes_token python3 deploy/scripts/check_edge.py --routes --base "https://$domain" </dev/null; then
  unset routes_token
  echo 'Public route verification failed (see RESULT routes above); new stack left running. Check the Caddyfile @rest matcher and HLM_REGISTRATION_MODE/HLM_ADMIN_HTTP.' >&2
  exit 1
fi
unset routes_token
# Cutover review (Sol 34): every device that still holds a token, with status, expiry and grants
# (never tokens). RUNBOOK: rotate g7-*, revoke stale gates-*/deploy-* with hlm_ops.sh.
echo 'Device inventory after cutover (python -m hlmemo.ops device list):'
dc exec -T api python -m hlmemo.ops device list </dev/null || echo 'WARNING: device inventory unavailable; run hlm_ops.sh device list' >&2
# Release the operation lock before the pruning helper acquires it again.
exec 8>&-
if ! bash deploy/backup/backup.sh --prune-pre-upgrade </dev/null; then
  echo 'WARNING: deployment healthy, but pre-upgrade retention failed; run backup.sh --prune-pre-upgrade.' >&2
fi
printf 'Deployment ready: %s (%s)\n' "$revision" "$domain"
