#!/usr/bin/env bash
# Detached rollback / release acceptance runner (Sol 36 H, D-061). Started by
#   deploy.sh --rollback USER@HOST        or   deploy.sh --accept-release USER@HOST
# from the CURRENT release's checkout (extracted with git show, like remote-deploy.sh); never
# run through bash -s. Args: MODE(rollback|accept) APP_DIR ENV_FILE RUN_DIR.
#
# rollback: reads release-state.json (never the legacy marker files) and restores, as ONE unit,
# the previous ref, its quiesced pre-upgrade dump, its image (verified by ID) and the env-file
# backups taken for that cutover (the retired admin token / registration secret). Everything is
# validated before anything stops. A pre-W0 previous release (its compose model does not pin
# HLM_REGISTRATION_MODE: closed) is REFUSED unless the restored env gives its API a non-empty
# HLM_REGISTRATION_SECRET: old code without one opens public registration.
# accept: deletes the recorded env backups and marks the release accepted; afterwards a rollback
# to a pre-W0 release is refused (its secrets are gone).
# shellcheck disable=SC2016,SC2217
set -Eeuo pipefail
umask 077
mode=$1 app_dir=$2 run_dir=$4
export HLM_ENV_FILE=$3
model=
finish() {
  result=$?
  (( BASH_SUBSHELL == 0 )) || exit "$result"
  trap - EXIT ERR INT TERM
  set +e
  rm -f -- "${model:-}" "${previous_model:-}" </dev/null || true
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
# The previous checkout may predate these helpers: run the current release's copies.
cp deploy/scripts/release_state.py deploy/scripts/release_env.py "$run_dir/"
state() { python3 "$run_dir/release_state.py" get "$parent_dir" "$1"; }
case $mode in
  accept)
    python3 "$run_dir/release_state.py" accept "$parent_dir"
    echo "Release $(state current_ref) accepted; env backups deleted. Rollback to a pre-W0 release is no longer possible."
    exit 0 ;;
  rollback) ;;
  *) echo "unknown mode $mode" >&2; exit 64 ;;
esac

# shellcheck source=deploy/scripts/common.sh
source deploy/scripts/common.sh
current=$(state current_ref)
previous=$(state previous_ref)
dump=$(state previous_dump)
image=$(state previous_image)
image_id=$(state previous_image_id)
[[ -n $previous ]] || refuse 'release-state.json records no previous release (already rolled back or initial deployment)'
[[ $previous =~ ^([a-f0-9]{40}|[a-f0-9]{64})$ && $(git rev-parse --verify "$previous^{commit}") == "$previous" ]] ||
  refuse "previous commit $previous is unavailable"
[[ -f $dump ]] || refuse "previous dump $dump is missing"
[[ -n $image && $(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null) == "$image_id" ]] ||
  refuse "previous image $image does not resolve to the recorded ID $image_id"
# Env backups of THIS cutover: FILE=BACKUP. Render with the backups in place of the live files.
declare -A restore=()
while IFS='=' read -r file backup; do
  [[ -n $file ]] || continue
  [[ -f $backup ]] || refuse "env backup $backup (for $(basename "$file")) is missing; keep backups until deploy.sh --accept-release"
  restore[$file]=$backup
done < <(state env_backups)
pick() { if [[ -n ${restore[$1]:-} ]]; then printf '%s' "${restore[$1]}"; else printf '%s' "$1"; fi; }
pre_w0=1
git show "$previous:deploy/compose.prod.yaml" | grep -Eq '^[[:space:]]+HLM_REGISTRATION_MODE:[[:space:]]*closed' && pre_w0=0
if ((pre_w0)) && ((${#restore[@]} == 0)); then
  refuse "previous release $previous predates W0a and no env backups are recorded (accepted or deleted); it would start with open registration"
fi
previous_model=$(mktemp "$PWD/deploy/.compose-previous.XXXXXX")
git show "$previous:deploy/compose.prod.yaml" > "$previous_model"
model=$(mktemp "$parent_dir/.rollback-compose.XXXXXX")
HLM_APP_ENV_FILE=$(pick "$HLM_APP_ENV_FILE") HLM_API_ENV_FILE=$(pick "$HLM_API_ENV_FILE") \
  docker compose -p "$COMPOSE_PROJECT" -f "$previous_model" --env-file "$(pick "$HLM_ENV_FILE")" config --format json > "$model"
rm -f -- "$previous_model"
# Pin the previous image ID for every application service and check registration safety.
python3 - "$model" "$image_id" "$pre_w0" <<'PYMODEL'
import json, sys
path, image_id, pre_w0 = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
with open(path) as stream:
    config = json.load(stream)
for name, service in config["services"].items():
    if name in ("api", "worker", "migrate"):
        service["image"] = image_id
        service.pop("build", None)
env = config["services"]["api"].get("environment") or {}
closed = str(env.get("HLM_REGISTRATION_MODE", "")).lower() == "closed"
if pre_w0 and not closed and not str(env.get("HLM_REGISTRATION_SECRET") or "").strip():
    sys.exit("REFUSE: the restored env gives the previous API no HLM_REGISTRATION_SECRET; it would open public registration")
with open(path, "w") as stream:
    json.dump(config, stream)
PYMODEL
rb() { docker compose -p "$COMPOSE_PROJECT" -f "$model" "$@"; }
export HLM_IMAGE=$image HLM_IMAGE_REVISION=$previous
rendered=$(rb config --format json | python3 -c 'import json,sys; s=json.load(sys.stdin)["services"]; print(s["api"]["image"] if s["api"]["image"] == s["worker"]["image"] else "MISMATCH")')
[[ $rendered == "$image_id" ]] || refuse "rendered rollback model runs $rendered, expected $image_id"
printf 'Rollback validated: %s -> %s (image %s = %s, dump %s, %s env backup(s))\n' \
  "$current" "$previous" "$image" "$image_id" "$dump" "${#restore[@]}"

# ---- from here on the service is interrupted; failures leave it stopped with guidance.
trap 'echo "Rollback FAILED after writers stopped; state unchanged ($state_file). Inspect $run_dir/log, then re-run deploy.sh --rollback." >&2' ERR
dc stop caddy api worker
safety=$(bash deploy/backup/backup.sh)
printf 'Saved the current database before replacing it: %s\n' "$safety"
for file in "${!restore[@]}"; do
  cp -p -- "${restore[$file]}" "$file"
  printf 'restored %s from %s\n' "$(basename "$file")" "$(basename "${restore[$file]}")"
done
git checkout --detach "$previous"
for mounted in deploy/Caddyfile deploy/scripts/worker_entrypoint.py deploy/scripts/worker_health.py; do
  [[ ! -e $mounted ]] || chmod go+r "$mounted"
done
python3 "$run_dir/release_env.py" "$HLM_ENV_FILE" "$image"
rb up -d --no-deps --wait --wait-timeout 180 db
rb exec -T db sh -eu -c '
  case "$POSTGRES_DB" in postgres|template0|template1|"") exit 64;; esac
  dropdb --username="$POSTGRES_USER" --if-exists --force -- "$POSTGRES_DB"
  createdb --username="$POSTGRES_USER" --owner="$POSTGRES_USER" --template=template0 -- "$POSTGRES_DB"
  pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction
' < "$dump"
rb up -d --no-deps --wait --wait-timeout 300 db api worker caddy
rb exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=8).read()"
python3 "$run_dir/release_state.py" rolled-back "$parent_dir"
printf 'Rollback complete: %s is running again (image %s); %s saved the rolled-back database.\n' "$previous" "$image_id" "$safety"
