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
# Resolve project from the env file as well as explicit overrides without eval/source.
_file_project=$(docker compose -f "$DEPLOY_DIR/compose.prod.yaml" --env-file "$HLM_ENV_FILE" config --environment | python3 -c 'import sys; d=dict(line.rstrip("\n").partition("=")[::2] for line in sys.stdin if "=" in line); print(d.get("BAKE_PROJECT") or d.get("HLM_COMPOSE_PROJECT") or "hlmemo-prod")')
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
  dc config --environment | python3 -c 'import sys; d=dict(line.rstrip("\n").partition("=")[::2] for line in sys.stdin if "=" in line); print(d.get(sys.argv[1], ""))' "$1"
}
