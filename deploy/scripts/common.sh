#!/usr/bin/env bash
# Shared by deploy scripts; env files are parsed by Compose, never executed as shell code.
set -euo pipefail
DEPLOY_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
HLM_ENV_FILE=${HLM_ENV_FILE:-"$DEPLOY_DIR/.env.prod"}
if [[ ! -f "$HLM_ENV_FILE" ]]; then
  printf 'Missing env file: %s (copy deploy/.env.prod.example and configure it)\n' "$HLM_ENV_FILE" >&2
  exit 1
fi
HLM_ENV_FILE=$(cd -- "$(dirname -- "$HLM_ENV_FILE")" && pwd)/$(basename -- "$HLM_ENV_FILE")
export HLM_ENV_FILE
_env_dir=$(dirname "$HLM_ENV_FILE")
HLM_APP_ENV_FILE=${HLM_APP_ENV_FILE:-$_env_dir/app.env}
HLM_API_ENV_FILE=${HLM_API_ENV_FILE:-$_env_dir/api.env}
HLM_DB_ENV_FILE=${HLM_DB_ENV_FILE:-$_env_dir/db.env}
HLM_BACKUP_ENV_FILE=${HLM_BACKUP_ENV_FILE:-$_env_dir/backup.env}
for _name in HLM_APP_ENV_FILE HLM_API_ENV_FILE HLM_DB_ENV_FILE HLM_BACKUP_ENV_FILE; do
  _path=${!_name}
  [[ -f $_path ]] || { printf 'Missing service env file: %s\n' "$_path" >&2; exit 1; }
  printf -v "$_name" '%s/%s' "$(cd -- "$(dirname -- "$_path")" && pwd)" "$(basename -- "$_path")"
done
export HLM_APP_ENV_FILE HLM_API_ENV_FILE HLM_DB_ENV_FILE HLM_BACKUP_ENV_FILE
# W2a librarian provider key/settings: OPTIONAL (absent in R1; compose marks it required: false).
# Lives next to the other secrets, never inside the checkout.
HLM_LLM_ENV_FILE=${HLM_LLM_ENV_FILE:-$_env_dir/llm.env}
if [[ -d $(dirname -- "$HLM_LLM_ENV_FILE") ]]; then
  printf -v HLM_LLM_ENV_FILE '%s/%s' "$(cd -- "$(dirname -- "$HLM_LLM_ENV_FILE")" && pwd)" "$(basename -- "$HLM_LLM_ENV_FILE")"
fi
export HLM_LLM_ENV_FILE
# Resolve project from the env file as well as explicit overrides without eval/source.
_file_project=$(docker compose -f "$DEPLOY_DIR/compose.prod.yaml" --env-file "$HLM_ENV_FILE" config --environment </dev/null | python3 -c 'import sys; d=dict(line.rstrip("\n").partition("=")[::2] for line in sys.stdin if "=" in line); print(d.get("BAKE_PROJECT") or d.get("HLM_COMPOSE_PROJECT") or "hlmemo-prod")')
COMPOSE_PROJECT=${BAKE_PROJECT:-${HLM_COMPOSE_PROJECT:-$_file_project}}
if [[ "$COMPOSE_PROJECT" == hlmemo || ! "$COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  printf 'Refusing reserved/invalid Compose project: %s\n' "$COMPOSE_PROJECT" >&2
  exit 1
fi
export COMPOSE_PROJECT

dc() {
  docker compose -p "$COMPOSE_PROJECT" -f "$DEPLOY_DIR/compose.prod.yaml" --env-file "$HLM_ENV_FILE" "$@"
}

env_value() {
  dc config --environment </dev/null | python3 -c 'import sys; d=dict(line.rstrip("\n").partition("=")[::2] for line in sys.stdin if "=" in line); print(d.get(sys.argv[1], ""))' "$1"
}

# A separate minimal Compose model parses backup.env with the same dotenv semantics.
# Extract ONLY its service environment, never the host/production interpolation environment.
backup_env() {
  printf 'services:\n  settings:\n    image: unused\n    env_file:\n      - "%s"\n' "$HLM_BACKUP_ENV_FILE" |
    docker compose -p hlmemo-backup-config --env-file /dev/null -f - config --format json |
    python3 -c 'import json,sys; d=json.load(sys.stdin)["services"]["settings"].get("environment") or {}; [print(k+"="+str(v).replace("$$", "$")) for k,v in d.items() if v is not None]'
}

backup_value() {
  backup_env | python3 -c 'import sys; d=dict(line.rstrip("\n").partition("=")[::2] for line in sys.stdin if "=" in line); print(d.get(sys.argv[1], ""))' "$1"
}

# Resolve symlinks before creating directories: backups must survive checkout replacement.
# The same guard applies to per-tier directories and an explicit restore safety override.
backup_path() {
  python3 - "$DEPLOY_DIR/.." "$1" <<'PYTHON'
from pathlib import Path
import sys
repository, destination = (Path(value).resolve() for value in sys.argv[1:])
if destination == repository or repository in destination.parents:
    print(f"Refusing backup directory inside repository: {destination}", file=sys.stderr)
    raise SystemExit(64)
print(destination)
PYTHON
}

backup_dir() {
  local directory
  directory=${HLM_BACKUP_DIR:-$(backup_value HLM_BACKUP_DIR)}
  backup_path "${directory:-/var/backups/hlmemo}"
}
