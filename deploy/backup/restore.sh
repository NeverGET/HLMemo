#!/usr/bin/env bash
# Offline restore, with a pre-restore dump and writers left stopped on any failure.
# PostgreSQL variables below expand inside the container, never in the host shell.
# shellcheck disable=SC2016
set -euo pipefail
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/scripts/common.sh
source "$SCRIPT_DIR/../scripts/common.sh"
if [[ $# -ne 2 || $2 != --yes || ! -f $1 ]]; then
    echo "Usage: $0 DUMP --yes (replaces this stack's database after an offline safety backup)" >&2
    exit 64
fi
dump=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
dc exec -T db pg_restore --list < "$dump" > /dev/null
# Refuse administrative databases before stopping services or modifying data.
dc exec -T db sh -eu -c 'case "$POSTGRES_DB" in postgres|template0|template1|"") exit 64;; esac' </dev/null
BACKUP_DIR=$(backup_dir)
safety_dir=$(backup_path "${HLM_RESTORE_SAFETY_DIR:-$BACKUP_DIR/pre-restore}")
mkdir -p "$BACKUP_DIR" </dev/null
exec 8>"$BACKUP_DIR/.operation.flock"
flock -n 8 </dev/null || { echo 'Another backup/restore is active.' >&2; exit 1; }
writers_stopped=0
cleanup() {
    status=$?
    if [[ $status -ne 0 && $writers_stopped == 1 ]]; then
        dc stop caddy api worker librarian </dev/null >&2 || true
        echo 'Restore failed; writers remain stopped. Use the printed safety dump to recover.' >&2
    fi
    exit "$status"
}
trap cleanup EXIT
# Mark before stop so even a partial stop failure is handled fail-closed.
writers_stopped=1
dc stop caddy api worker librarian </dev/null
echo "Writers stopped; creating the pre-restore safety dump." >&2
mkdir -p "$safety_dir" </dev/null
safety=$(mktemp "$safety_dir/hlmemo-$(date -u +%Y-%m-%dT%H%M%SZ </dev/null).dump.XXXXXX" </dev/null)
dc exec -T db sh -eu -c 'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl' </dev/null > "$safety"
dc exec -T db pg_restore --list < "$safety" > /dev/null
echo "Safety dump: $safety; on failure writers remain stopped. Restore it with this script." >&2
dc exec -T db sh -eu -c '
    dropdb --username="$POSTGRES_USER" --if-exists --force -- "$POSTGRES_DB"
    createdb --username="$POSTGRES_USER" --owner="$POSTGRES_USER" --template=template0 -- "$POSTGRES_DB"
' </dev/null
dc exec -T db sh -eu -c 'pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction' < "$dump"
# Restore can move the schema backwards. Upgrade with the checked-out release before
# any writer restarts; a migration failure deliberately leaves all writers stopped.
dc run --rm --no-deps migrate </dev/null
dc up -d --no-deps --wait --wait-timeout 180 db api worker librarian caddy </dev/null
echo "Restore completed; stack healthy. Safety dump retained: $safety"
