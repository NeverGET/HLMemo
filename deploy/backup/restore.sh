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
dc exec -T db sh -eu -c 'case "$POSTGRES_DB" in postgres|template0|template1|"") exit 64;; esac'
BACKUP_DIR=${HLM_BACKUP_DIR:-$(env_value HLM_BACKUP_DIR)}
BACKUP_DIR=${BACKUP_DIR:-$DEPLOY_DIR/backup/data}
mkdir -p "$BACKUP_DIR"
if ! mkdir "$BACKUP_DIR/.operation.lock" 2>/dev/null; then
    echo "Another backup/restore is active (or remove stale $BACKUP_DIR/.operation.lock after checking)." >&2
    exit 1
fi
writers_stopped=0
cleanup() {
    status=$?
    if [[ $status -ne 0 && $writers_stopped == 1 ]]; then
        dc stop caddy api worker >&2 || true
        echo 'Restore failed; writers remain stopped. Use the printed safety dump to recover.' >&2
    fi
    rmdir "$BACKUP_DIR/.operation.lock"
    exit "$status"
}
trap cleanup EXIT
# Mark before stop so even a partial stop failure is handled fail-closed.
writers_stopped=1
dc stop caddy api worker
echo "Writers stopped; creating the pre-restore safety dump." >&2
safety_dir=${HLM_RESTORE_SAFETY_DIR:-$BACKUP_DIR/pre-restore}
mkdir -p "$safety_dir"
safety=$(mktemp "$safety_dir/hlmemo-$(date -u +%Y-%m-%dT%H%M%SZ).dump.XXXXXX")
dc exec -T db sh -eu -c 'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$safety"
dc exec -T db pg_restore --list < "$safety" > /dev/null
echo "Safety dump: $safety; on failure writers remain stopped. Restore it with this script." >&2
dc exec -T db sh -eu -c '
    dropdb --username="$POSTGRES_USER" --if-exists --force -- "$POSTGRES_DB"
    createdb --username="$POSTGRES_USER" --owner="$POSTGRES_USER" --template=template0 -- "$POSTGRES_DB"
'
dc exec -T db sh -eu -c 'pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --no-owner --no-acl --exit-on-error --single-transaction' < "$dump"
dc up -d --wait --wait-timeout 180
echo "Restore completed; stack healthy. Safety dump retained: $safety"
