#!/usr/bin/env bash
# Detached rollback / release acceptance runner (D-065, Sol 36 H, Sol 39). Started by
#   deploy.sh --rollback USER@HOST        or   deploy.sh --accept-release USER@HOST
# from the CURRENT release (extracted with git show, like remote-deploy.sh); never run through
# bash -s. Args: MODE(rollback|accept) APP_DIR ENV_FILE RUN_DIR.
#
# D-065: W0a is a one-way door. A manual rollback is allowed ONLY between W0+ releases (their code
# is closed-by-default); any pre-W0 target (its tree lacks alembic/versions/0005_w0_access.py or
# src/hlmemo/ops) is refused unconditionally. Disaster recovery from a bad W0+ release rolls
# FORWARD: W0+ code plus a restore of the recorded dump (restore.sh migrates).
#
# rollback: reads release-state.json; verifies the RUNNING release equals its current_ref; restores
# the previous ref, its quiesced dump and its image (verified by ID) as one unit. The state records
# the attempt before anything stops, so an interrupted (killed) rollback can simply be re-run. If a
# step fails, the saved current database is restored and the current release restarted.
# accept: verifies the running release, deletes every recorded env backup (retired secrets).
# shellcheck disable=SC2016,SC2217
set -Eeuo pipefail
umask 077
mode=$1 app_dir=$2 run_dir=$4
export HLM_ENV_FILE=$3
model='' helpers=''
finish() {
  result=$?
  (( BASH_SUBSHELL == 0 )) || exit "$result"
  trap - EXIT ERR INT TERM
  set +e
  rm -rf -- "${model:-}" "${previous_model:-}" "${helpers:-}" </dev/null || true
  printf '%s\n' "$result" > "$run_dir/status.tmp"
  mv -f "$run_dir/status.tmp" "$run_dir/status" </dev/null
  exit "$result"
}
trap finish EXIT
exec </dev/null
trap 'exit 130' INT
trap 'exit 143' TERM
refuse() { printf 'Rollback refused (nothing was stopped): %s\n' "$*" >&2; exit 1; }
parent_dir=$(dirname "$app_dir")
state_file=$parent_dir/release-state.json
if [[ ${HLM_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
  exec 9>"$parent_dir/.deploy.lock"
  flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
fi
cd "$app_dir"
[[ -f $state_file ]] || refuse "no $state_file (only releases deployed by a W0a-or-later runner can be rolled back this way)"
current=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["current_ref"])' "$state_file")
# Helpers always come from the CURRENT release's git objects, never from the working tree (which
# an interrupted rollback may have left at the previous commit), into a private temp dir.
helpers=$(mktemp -d "$parent_dir/.rollback-helpers.XXXXXX")
for helper in release_state.py release_env.py; do
  git show "$current:deploy/scripts/$helper" > "$helpers/$helper"
done
state() { python3 "$helpers/release_state.py" get "$parent_dir" "$1"; }
# shellcheck source=deploy/scripts/common.sh
source deploy/scripts/common.sh

running_revision() {
  local id
  id=$(docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" --filter label=com.docker.compose.service=api | head -1)
  [[ -n $id ]] || return 0
  docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$id"
}
running=$(running_revision)
in_progress=$(state rollback_in_progress)
if [[ -z $in_progress && $running != "$current" ]]; then
  refuse "the running api is ${running:-<none>}, but release-state.json says $current. Do not guess: inspect 'docker ps', /opt/hlmemo/.deploy-runs/*/log and release-state.json; re-run or finish the deployment first (deploy.sh REF), then retry."
fi

case $mode in
  accept)
    python3 "$helpers/release_state.py" accept "$parent_dir"
    echo "Release $current accepted; recorded env backups deleted."
    exit 0 ;;
  rollback) ;;
  *) echo "unknown mode $mode" >&2; exit 64 ;;
esac

previous=$(state previous_ref)
dump=$(state previous_dump)
image=$(state previous_image)
image_id=$(state previous_image_id)
[[ -n $previous ]] || refuse 'release-state.json records no previous release (already rolled back or initial deployment)'
[[ -z $in_progress || $in_progress == "$previous" ]] || refuse "an unfinished rollback to $in_progress is recorded, not to $previous"
[[ -z $in_progress || $running == "$current" || $running == "$previous" || -z $running ]] ||
  refuse "unfinished rollback recorded, but the running api is $running"
[[ $previous =~ ^([a-f0-9]{40}|[a-f0-9]{64})$ && $(git rev-parse --verify "$previous^{commit}") == "$previous" ]] ||
  refuse "previous commit $previous is unavailable"
# D-065 one-way door: never start pre-W0 code (it opens registration without its old secret).
if ! git cat-file -e "$previous:alembic/versions/0005_w0_access.py" 2>/dev/null ||
  ! git cat-file -e "$previous:src/hlmemo/ops/__init__.py" 2>/dev/null; then
  refuse "previous release $previous predates W0a; W0a is a one-way door (D-065). Roll FORWARD instead: deploy a W0+ release and restore the recorded dump ($dump) with deploy/backup/restore.sh."
fi
[[ -f $dump ]] || refuse "previous dump $dump is missing"
[[ -n $image && $(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null) == "$image_id" ]] ||
  refuse "previous image $image does not resolve to the recorded ID $image_id"
previous_model=$(mktemp "$PWD/deploy/.compose-previous.XXXXXX")
git show "$previous:deploy/compose.prod.yaml" > "$previous_model"
model=$(mktemp "$parent_dir/.rollback-compose.XXXXXX")
docker compose -p "$COMPOSE_PROJECT" -f "$previous_model" --env-file "$HLM_ENV_FILE" config --format json > "$model"
rm -f -- "$previous_model"
python3 - "$model" "$image_id" <<'PYMODEL'
import json, sys
path, image_id = sys.argv[1], sys.argv[2]
with open(path) as stream:
    config = json.load(stream)
for name, service in config["services"].items():
    if name in ("api", "worker", "librarian", "migrate"):
        service["image"] = image_id
        service.pop("build", None)
with open(path, "w") as stream:
    json.dump(config, stream)
PYMODEL
rb() { docker compose -p "$COMPOSE_PROJECT" -f "$model" "$@"; }
# W2a: the librarian exists only in W2a+ models. Services each model runs, and a model-independent
# stop/remove of librarian containers (by Compose label) for a target model that lacks the service.
previous_services=(db api worker caddy)
if python3 -c 'import json,sys; sys.exit(0 if "librarian" in json.load(open(sys.argv[1]))["services"] else 1)' "$model"; then
  previous_services=(db api worker librarian caddy)
fi
current_services() {
  if dc config --services | grep -qx librarian; then echo db api worker librarian caddy; else echo db api worker caddy; fi
}
stop_librarian() { # stop_librarian [rm]
  local ids
  mapfile -t ids < <(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" --filter label=com.docker.compose.service=librarian)
  ((${#ids[@]})) || return 0
  docker stop "${ids[@]}" >&2
  [[ ${1:-} != rm ]] || docker rm -f "${ids[@]}" >&2
}
current_image=${image%:*}:$current
export HLM_IMAGE=$image HLM_IMAGE_REVISION=$previous
rendered=$(rb config --format json | python3 -c 'import json,sys; s=json.load(sys.stdin)["services"]; i={s[n]["image"] for n in ("api","worker","librarian") if n in s}; print(s["api"]["image"] if len(i) == 1 else "MISMATCH")')
[[ $rendered == "$image_id" ]] || refuse "rendered rollback model runs $rendered, expected $image_id"
printf 'Rollback validated: %s -> %s (image %s = %s, dump %s)\n' "$current" "$previous" "$image" "$image_id" "$dump"

# ---- the service is interrupted from here on. The attempt is recorded first (re-runnable).
python3 "$helpers/release_state.py" begin-rollback "$parent_dir" "$previous"
safety=
db_replaced=0
recover_current() {
  # A failed step (not a kill): put the CURRENT release back, with its own data.
  trap - ERR
  set +e
  echo "Rollback step failed; restoring the current release $current." >&2
  rb stop caddy api worker >&2
  stop_librarian >&2
  git checkout --detach "$current" >&2
  for mounted in deploy/Caddyfile deploy/scripts/worker_entrypoint.py deploy/scripts/worker_health.py; do
    [[ ! -e $mounted ]] || chmod go+r "$mounted"
  done
  python3 "$helpers/release_env.py" "$HLM_ENV_FILE" "$current_image" >&2
  export HLM_IMAGE=$current_image HLM_IMAGE_REVISION=$current
  ok=1
  if ((db_replaced)) && [[ -f $safety ]]; then
    dc up -d --no-deps --wait --wait-timeout 180 db >&2 || ok=0
    dc exec -T db sh -eu -c '
      case "$POSTGRES_DB" in postgres|template0|template1|"") exit 64;; esac
      dropdb --username="$POSTGRES_USER" --if-exists --force -- "$POSTGRES_DB"
      createdb --username="$POSTGRES_USER" --owner="$POSTGRES_USER" --template=template0 -- "$POSTGRES_DB"
      pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction
    ' < "$safety" >&2 || ok=0
  fi
  read -ra up_services <<< "$(current_services)"
  if ((ok)) && dc up -d --no-deps --wait --wait-timeout 300 "${up_services[@]}" >&2; then
    python3 "$helpers/release_state.py" end-rollback "$parent_dir" >&2
    echo "Current release $current restored${safety:+ (database from $safety)}; rollback aborted." >&2
  else
    echo "Automatic restore of $current FAILED; the recorded attempt lets 'deploy.sh --rollback' be re-run. Saved database: ${safety:-<none>}." >&2
  fi
  exit 1
}
trap recover_current ERR
dc stop caddy api worker
# The librarian is a writer too; removed outright when the previous model does not define it.
if [[ " ${previous_services[*]} " == *" librarian "* ]]; then stop_librarian; else stop_librarian rm; fi
# The current database, saved before it is replaced: used by recover_current on a failed step.
safety=$(bash deploy/backup/backup.sh)
printf 'Saved the current database before replacing it: %s\n' "$safety"
git checkout --detach "$previous"
for mounted in deploy/Caddyfile deploy/scripts/worker_entrypoint.py deploy/scripts/worker_health.py; do
  [[ ! -e $mounted ]] || chmod go+r "$mounted"
done
python3 "$helpers/release_env.py" "$HLM_ENV_FILE" "$image"
rb up -d --no-deps --wait --wait-timeout 180 db
db_replaced=1
rb exec -T db sh -eu -c '
  case "$POSTGRES_DB" in postgres|template0|template1|"") exit 64;; esac
  dropdb --username="$POSTGRES_USER" --if-exists --force -- "$POSTGRES_DB"
  createdb --username="$POSTGRES_USER" --owner="$POSTGRES_USER" --template=template0 -- "$POSTGRES_DB"
  pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction
' < "$dump"
rb up -d --no-deps --wait --wait-timeout 300 "${previous_services[@]}"
rb exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=8).read()"
trap - ERR
# One atomic step: consume the pair, clear the attempt, derive (and prune) the legacy markers.
python3 "$helpers/release_state.py" rolled-back "$parent_dir"
printf 'Rollback complete: %s is running again (image %s). Saved database of %s: %s\n' "$previous" "$image_id" "$current" "$safety"
