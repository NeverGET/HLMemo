#!/usr/bin/env bash
# Local scratch stack for a migration rehearsal: its own database, the API on loopback, one device with `write` on the
# slug. No production access and no LLM calls (the librarian is off by default).
#
#   bash tools/migrate/local_stack.sh up <slug>      drop + create hlm_mig_<slug>, migrate, create the project,
#                                                    mint the device mig-<slug>, start the API
#   bash tools/migrate/local_stack.sh env <slug>     print the client exports (the token is read from a 0600 file,
#                                                    never printed)
#   bash tools/migrate/local_stack.sh status <slug>  is the API up, which database
#   bash tools/migrate/local_stack.sh down <slug>    stop the API (the database stays; `up` recreates it)
#
# Environment:
#   HLM_MIG_PG     Postgres base DSN without a database (default: the compose dev database on 127.0.0.1:5432)
#   HLM_MIG_PORT   API port (default 8799)
#   HLM_MIG_STATE  where the token, pid and logs live (default: <repo>/docs/private/migration/<slug>/local, gitignored)
#   HLM_MIG_PYTHON the interpreter (default: <repo>/.venv/bin/python)
# Every process runs in a neutral cwd: a repository root may hold an hlm.toml that points at production.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CMD=${1:?usage: local_stack.sh up|env|status|down <slug>}
SLUG=${2:?slug}
[[ $SLUG =~ ^[a-z0-9][a-z0-9-]{1,63}$ ]] || { echo "bad slug: $SLUG" >&2; exit 2; }
DB=hlm_mig_${SLUG//-/_}
PORT=${HLM_MIG_PORT:-8799}
DEV=mig-$SLUG
STATE=${HLM_MIG_STATE:-$REPO/docs/private/migration/$SLUG/local}
PG=${HLM_MIG_PG:-postgresql://hlm:hlm@127.0.0.1:5432}
PY=${HLM_MIG_PYTHON:-$REPO/.venv/bin/python}
export PYTHONPATH="$REPO/src"

mkdir -p "$STATE" && chmod 700 "$STATE"
cd "${TMPDIR:-/tmp}"

up() {
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $PORT is busy (an old scratch API?): run 'down' or set HLM_MIG_PORT" >&2; exit 1
  fi
  PGOPTIONS="-c client_min_messages=warning" psql "$PG/postgres" -v ON_ERROR_STOP=1 -qc "DROP DATABASE IF EXISTS $DB" \
    -c "CREATE DATABASE $DB" >/dev/null
  HLM_DB_DSN="$PG/$DB" HLM_MODELS_DIR="${HLM_MODELS_DIR:-$REPO/models}" "$PY" -m alembic -c "$REPO/alembic.ini" \
    upgrade main@head >"$STATE/alembic.log" 2>&1 || { tail -20 "$STATE/alembic.log" >&2; exit 1; }
  HLM_DB_DSN="$PG/$DB" "$PY" -m hlmemo.ops project create "$SLUG" --name "$SLUG (local rehearsal)" --exists-ok >/dev/null
  ( umask 077; HLM_DB_DSN="$PG/$DB" "$PY" -m hlmemo.ops device mint --name "$DEV" --class other --grant "$SLUG:write" \
      --notes "local migration rehearsal" >"$STATE/token" 2>"$STATE/mint.log" )
  HLM_DB_DSN="$PG/$DB" HLM_MODELS_DIR="${HLM_MODELS_DIR:-$REPO/models}" HLM_PROFILES_DIR="$REPO/profiles" \
  HLM_API_HOST=127.0.0.1 HLM_API_PORT="$PORT" HLM_REGISTRATION_MODE=closed HLM_ADMIN_HTTP=disabled \
    nohup "$PY" -m hlmemo.server.app >"$STATE/api.log" 2>&1 &
  echo $! >"$STATE/api.pid"
  for _ in $(seq 1 60); do
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "up: db=$DB api=http://127.0.0.1:$PORT/mcp device=$DEV (write on $SLUG); log $STATE/api.log"; return 0
    fi
    sleep 1
  done
  echo "the API did not start; see $STATE/api.log" >&2; exit 1
}

case $CMD in
  up) up ;;
  env)
    # eval "$(bash tools/migrate/local_stack.sh env <slug>)"
    echo "export HLM_SERVER_URL=http://127.0.0.1:$PORT/mcp HLM_DEVICE_NAME=$DEV HLM_CONFIG=/dev/null"
    echo "export HLM_DEVICE_TOKEN=\"\$(cat '$STATE/token')\""
    echo "cd \"\${TMPDIR:-/tmp}\"" ;;
  status)
    if [[ -f $STATE/api.pid ]] && kill -0 "$(cat "$STATE/api.pid")" 2>/dev/null; then
      echo "api up (pid $(cat "$STATE/api.pid"), port $PORT, db $DB)"
    else echo "api down (db $DB may still exist)"; fi ;;
  down)
    if [[ -f $STATE/api.pid ]]; then kill "$(cat "$STATE/api.pid")" 2>/dev/null || true; rm -f "$STATE/api.pid"; fi
    echo "api stopped (db $DB kept)" ;;
  *) echo "usage: local_stack.sh up|env|status|down <slug>" >&2; exit 2 ;;
esac
