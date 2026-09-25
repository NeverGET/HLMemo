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
# D-108/D-111 #7: llm.env is part of the release state. The previous release's own llm.env
# (previous_llm_env, snapshotted by remote-deploy.sh) renders its model and is restored atomically
# before the previous image starts; the newer llm.env is copied once per attempt (rollback_llm_env)
# and put back before the current release restarts after a failed step.
# D-116 (review 75): accept refuses during an unfinished rollback and always checks running ==
# current_ref; the newer env is copied only when the disk env is what the current api AND librarian
# run; the FIRST safety dump and the destructive phase are journalled before the database changes,
# so a retry reuses that dump; secret-bearing copies are journalled and cleaned idempotently.
# accept: verifies the running release, deletes every recorded env backup (retired secrets) and, for
# a W0+ current release, sweeps any unrecorded *.pre-w0-* next to the env files.
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
# A killed (SIGKILL) earlier run leaves its private rendered model and helper copies behind; the model
# holds env values (llm.env included, D-111). Only the lock owner sweeps them, like remote-deploy.sh.
rm -rf -- "$parent_dir"/.rollback-compose.* "$parent_dir"/.rollback-helpers.*
cd "$app_dir"
[[ -f $state_file ]] || refuse "no $state_file (only releases deployed by a W0a-or-later runner can be rolled back this way)"
current=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["current_ref"])' "$state_file")
# Helpers always come from the CURRENT release's git objects, never from the working tree (which
# an interrupted rollback may have left at the previous commit), into a private temp dir.
helpers=$(mktemp -d "$parent_dir/.rollback-helpers.XXXXXX")
for helper in release_state.py release_env.py llm_env_release.py; do
  git show "$current:deploy/scripts/$helper" > "$helpers/$helper"
done
state() { python3 "$helpers/release_state.py" get "$parent_dir" "$1"; }
# shellcheck source=deploy/scripts/common.sh
source deploy/scripts/common.sh
# D-116 #9: finish any journalled deletion of secret-bearing copies under this lock first.
python3 "$helpers/release_state.py" cleanup "$parent_dir"
# D-116 #3: an unfinished llm.env switch first (install_llm_env.sh re-run under the deploy lock).
[[ -z $(state env_switch) ]] ||
  refuse 'an llm.env switch is unfinished (install_llm_env.sh was interrupted): re-run install_llm_env.sh first'

running_revision() {
  local id
  id=$(docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" --filter label=com.docker.compose.service=api | head -1)
  [[ -n $id ]] || return 0
  docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$id"
}
running=$(running_revision)
in_progress=$(state rollback_in_progress)
mismatch="the running api is ${running:-<none>}, but release-state.json says $current. Do not guess: inspect 'docker ps', /opt/hlmemo/.deploy-runs/*/log and release-state.json; re-run or finish the deployment first (deploy.sh REF), then retry."
[[ -z $(state deploy_attempt) ]] ||
  refuse "an unfinished deployment is recorded (deploy_attempt): re-run deploy.sh with its ref first; it completes or recovers it"
if [[ -z $in_progress && $running != "$current" ]]; then
  refuse "$mismatch"
fi

case $mode in
  accept)
    # D-116 #5: never while a rollback is unfinished, and ALWAYS only for the release that runs.
    [[ -z $in_progress ]] ||
      refuse "an unfinished rollback to $in_progress is recorded: finish it (deploy.sh --rollback) before accepting anything"
    [[ $running == "$current" ]] || refuse "$mismatch"
    # Defensive sweep of UNRECORDED *.pre-w0-* backups next to the env files, only when the
    # current release is W0+ (D-065 one-way door): pre-W0 code would still need those secrets.
    sweep=()
    if git cat-file -e "$current:alembic/versions/0005_w0_access.py" 2>/dev/null &&
      git cat-file -e "$current:src/hlmemo/ops/__init__.py" 2>/dev/null; then
      declare -A seen=()
      for file in "$HLM_ENV_FILE" "${HLM_APP_ENV_FILE:-}" "${HLM_API_ENV_FILE:-}"; do
        [[ -n $file ]] || continue
        dir=$(dirname "$file")
        [[ -n ${seen[$dir]:-} ]] || { seen[$dir]=1; sweep+=(--sweep-dir "$dir"); }
      done
    else
      echo "Current release $current predates W0a: unrecorded *.pre-w0-* backups are left in place." >&2
    fi
    python3 "$helpers/release_state.py" accept "$parent_dir" "${sweep[@]}"
    echo "Release $current accepted; recorded env backups deleted."
    exit 0 ;;
  rollback) ;;
  *) echo "unknown mode $mode" >&2; exit 64 ;;
esac

previous=$(state previous_ref)
dump=$(state previous_dump)
image=$(state previous_image)
image_id=$(state previous_image_id)
previous_llm_env=$(state previous_llm_env)
llm_env_file=${HLM_LLM_ENV_FILE:-$(dirname "$HLM_ENV_FILE")/llm.env}
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
# D-111 #7: Compose inlines env_file contents into the rendered model, so the previous model is
# rendered with the previous release's OWN llm.env (else it would carry the newer env, whose
# profiles the previous code may not know: D-108).
render_llm_env=$llm_env_file
case $previous_llm_env in
  '')
    echo "WARNING: release-state.json records no llm.env for $previous (deployed by an older runner): llm.env is left as it is. If $previous cannot load it, reinstall its own llm.env first (D-108)." >&2 ;;
  absent) render_llm_env=$helpers/no-llm.env ;;  # the previous release ran without one
  *)
    [[ -f $previous_llm_env ]] || refuse "the llm.env snapshot of $previous ($previous_llm_env) is missing"
    render_llm_env=$previous_llm_env ;;
esac
previous_model=$(mktemp "$PWD/deploy/.compose-previous.XXXXXX")
git show "$previous:deploy/compose.prod.yaml" > "$previous_model"
model=$(mktemp "$parent_dir/.rollback-compose.XXXXXX")
HLM_LLM_ENV_FILE=$render_llm_env docker compose -p "$COMPOSE_PROJECT" -f "$previous_model" --env-file "$HLM_ENV_FILE" \
  config --format json > "$model"
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
# D-111 #7: a copy of the NEWER llm.env, taken once per attempt (a re-run finds the older one
# already restored and must keep the recorded copy).
newer_llm_env=$(state rollback_llm_env)
if [[ -n $previous_llm_env && -z $newer_llm_env ]]; then
  # D-116 #1: the disk env must be what the current api AND librarian run, or its copy is no
  # "newer env" a failed step could put back: refuse before anything stops.
  running_fps=()
  for service in api librarian; do
    id=$(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" --filter "label=com.docker.compose.service=$service" | head -1)
    [[ -n $id ]] || continue
    running_fps+=("$service=$(docker inspect --format '{{json .Config.Env}}' "$id" | python3 "$helpers/llm_env_release.py" fingerprint -)")
  done
  python3 "$helpers/llm_env_release.py" provenance "$llm_env_file" "${running_fps[@]}" ||
    refuse "the llm.env on disk is not the env the running $current was created with (above): finish the env switch (install_llm_env.sh) first"
  newer_llm_env=$(python3 "$helpers/llm_env_release.py" snapshot "$llm_env_file" "$current")
  [[ $newer_llm_env == absent ]] || python3 "$helpers/release_state.py" record-pending "$parent_dir" "$newer_llm_env"
fi
python3 "$helpers/release_state.py" begin-rollback "$parent_dir" "$previous" ${newer_llm_env:+--llm-env "$newer_llm_env"}
newer_llm_env=$(state rollback_llm_env)
# D-116 #4: the FIRST safety dump of this attempt and whether its destructive phase began.
safety=$(state rollback_safety)
db_replaced=0
[[ $(state rollback_destructive) != True ]] || db_replaced=1
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
  if [[ -n ${newer_llm_env:-} ]]; then  # D-111 #7: the newer llm.env back BEFORE the current release starts
    python3 "$helpers/llm_env_release.py" restore "$newer_llm_env" "$llm_env_file" >&2 || ok=0
  fi
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
    # D-116 #9: the attempt ends; its newer-env copy is journalled and deleted in that one step
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
# D-116 #4: taken ONCE per attempt and journalled before any database change; a retry reuses it
# and never re-snapshots a half-restored database.
if [[ -z $safety ]]; then
  safety=$(bash deploy/backup/backup.sh)
  python3 "$helpers/release_state.py" rollback-mark "$parent_dir" --safety "$safety"
  printf 'Saved the current database before replacing it: %s\n' "$safety"
else
  [[ -f $safety ]] || { echo "The recorded safety dump $safety is missing." >&2; false; }
  printf 'Re-using the safety dump of the interrupted attempt: %s\n' "$safety"
fi
git checkout --detach "$previous"
for mounted in deploy/Caddyfile deploy/scripts/worker_entrypoint.py deploy/scripts/worker_health.py; do
  [[ ! -e $mounted ]] || chmod go+r "$mounted"
done
python3 "$helpers/release_env.py" "$HLM_ENV_FILE" "$image"
# D-111 #7: the previous release's own llm.env, atomically, BEFORE its image starts (D-108)
if [[ -n $previous_llm_env ]]; then
  python3 "$helpers/llm_env_release.py" restore "$previous_llm_env" "$llm_env_file"
fi
# D-116 #4: the destructive phase is journalled BEFORE the database changes
python3 "$helpers/release_state.py" rollback-mark "$parent_dir" --destructive
db_replaced=1
rb up -d --no-deps --wait --wait-timeout 180 db
rb exec -T db sh -eu -c '
  case "$POSTGRES_DB" in postgres|template0|template1|"") exit 64;; esac
  dropdb --username="$POSTGRES_USER" --if-exists --force -- "$POSTGRES_DB"
  createdb --username="$POSTGRES_USER" --owner="$POSTGRES_USER" --template=template0 -- "$POSTGRES_DB"
  pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction
' < "$dump"
rb up -d --no-deps --wait --wait-timeout 300 "${previous_services[@]}"
rb exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=8).read()"
trap - ERR
# One atomic step: consume the pair, clear the attempt, derive (and prune) the legacy markers, and
# journal both spent llm.env copies (they hold the provider key), deleted idempotently (D-116 #9).
python3 "$helpers/release_state.py" rolled-back "$parent_dir"
printf 'Rollback complete: %s is running again (image %s). Saved database of %s: %s\n' "$previous" "$image_id" "$current" "$safety"
