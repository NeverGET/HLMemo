#!/usr/bin/env bash
# Restore a pg_dump -Fc dump into the NEW database hlm_al_ceiling for the active-librarian ceiling
# experiment (eval/active/restore.md). Never deploy/backup/restore.sh; never another database,
# stack or volume.
#
# Default: a dedicated compose project `hlmal` running ONLY the repo's compose.yaml `db` service
# on 127.0.0.1:${HLM_AL_DB_PORT:-55432} (volume hlmal_pgdata). The compose file pins `name: hlmemo`;
# `-p hlmal` (and COMPOSE_PROJECT_NAME) override it, and the script verifies the resolved name.
#
# Alternative: HLM_AL_ADMIN_DSN=<dsn of an existing Postgres with pgvector, any database there> uses
# that server with the host's pg_restore/psql (they must be >= the dump's version, 17).
#
# Usage: eval/active/restore.sh <dump file> [--replace]
#   --replace  drop and recreate hlm_al_ceiling (only that database) when it already exists
set -euo pipefail

DB=hlm_al_ceiling
PROJECT=hlmal
PORT="${HLM_AL_DB_PORT:-55432}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
COMPOSE_FILE="$ROOT/compose.yaml"

dump="${1:-}"
replace=0
[[ "${2:-}" == "--replace" ]] && replace=1
if [[ -z "$dump" || ! -f "$dump" ]]; then
  echo "usage: $0 <dump file (pg_dump -Fc)> [--replace]" >&2
  exit 2
fi

count_sql() {
  cat <<'SQL'
SELECT 'alembic_version=' || (SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version);
SELECT c.relname || '=' || (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'r' AND n.nspname = 'public'
 ORDER BY c.relname;
SQL
}

if [[ -n "${HLM_AL_ADMIN_DSN:-}" ]]; then
  # ---------------------------------------------------------------- explicit server (host tools)
  ver="$(pg_restore --version | grep -Eo '[0-9]+' | head -1)"
  if (( ver < 17 )); then
    echo "host pg_restore is version $ver; the dump needs >= 17 (use the default hlmal compose path)" >&2
    exit 2
  fi
  admin="$HLM_AL_ADMIN_DSN"
  exists="$(psql "$admin" -tAX -c "SELECT 1 FROM pg_database WHERE datname = '$DB'")"
  if [[ "$exists" == "1" ]]; then
    if (( replace == 0 )); then echo "database $DB exists; pass --replace to recreate it" >&2; exit 3; fi
    psql "$admin" -v ON_ERROR_STOP=1 -qX -c "DROP DATABASE $DB WITH (FORCE)"
  fi
  psql "$admin" -v ON_ERROR_STOP=1 -qX -c "CREATE DATABASE $DB"
  target="$(python3 - "$admin" "$DB" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit
u = urlsplit(sys.argv[1])
print(urlunsplit((u.scheme, u.netloc, "/" + sys.argv[2], u.query, u.fragment)))
PY
)"
  pg_restore --dbname="$target" --no-owner --no-acl --exit-on-error --single-transaction "$dump"
  count_sql | psql "$target" -tAX -v ON_ERROR_STOP=1
  echo "restored into $DB (explicit server)"
  exit 0
fi

# ------------------------------------------------------------------ default: compose project hlmal
if ! curl -s --max-time 5 --unix-socket "$HOME/.docker/run/docker.sock" http://localhost/_ping | grep -q OK; then
  echo "docker is not up (the _ping did not return OK)" >&2
  exit 4
fi
export COMPOSE_PROJECT_NAME="$PROJECT"
export HLM_DB_PORT="$PORT"
dc() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }

resolved="$(dc config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
if [[ "$resolved" != "$PROJECT" ]]; then
  echo "compose resolved the project name to '$resolved', not '$PROJECT': refusing" >&2
  exit 5
fi

dc up -d --no-deps --wait db
dc exec -T db pg_restore --list < "$dump" > /dev/null   # the dump is readable by the server's pg_restore

exists="$(dc exec -T db psql -U hlm -d hlm -tAX -c "SELECT 1 FROM pg_database WHERE datname = '$DB'")"
if [[ "$exists" == "1" ]]; then
  if (( replace == 0 )); then echo "database $DB exists; pass --replace to recreate it" >&2; exit 3; fi
  dc exec -T db psql -U hlm -d hlm -v ON_ERROR_STOP=1 -qX -c "DROP DATABASE $DB WITH (FORCE)"
fi
dc exec -T db psql -U hlm -d hlm -v ON_ERROR_STOP=1 -qX -c "CREATE DATABASE $DB"
dc exec -T db pg_restore --username=hlm --dbname="$DB" --no-owner --no-acl --exit-on-error --single-transaction < "$dump"
count_sql | dc exec -T db psql -U hlm -d "$DB" -tAX -v ON_ERROR_STOP=1
echo "restored into $DB (compose project $PROJECT, 127.0.0.1:$PORT)"
echo "HLM_AL_DSN=postgresql://hlm:hlm@127.0.0.1:$PORT/$DB"
