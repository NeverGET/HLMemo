#!/usr/bin/env bash
# Consistent PostgreSQL custom-format backup; stdout is the completed daily dump path.
# PostgreSQL variables below expand inside the container, never in the host shell.
# shellcheck disable=SC2016
set -euo pipefail
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/scripts/common.sh
source "$SCRIPT_DIR/../scripts/common.sh"
BACKUP_DIR=${HLM_BACKUP_DIR:-$(env_value HLM_BACKUP_DIR)}
BACKUP_DIR=${BACKUP_DIR:-$DEPLOY_DIR/backup/data}
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"
BACKUP_DIR=$(cd "$BACKUP_DIR" && pwd)
if ! mkdir "$BACKUP_DIR/.operation.lock" 2>/dev/null; then
    echo "Another backup/restore is active (or remove stale $BACKUP_DIR/.operation.lock after checking)." >&2
    exit 1
fi
temporary=$(mktemp "$BACKUP_DIR/.dump.XXXXXX")
cleanup() {
    rm -f "$temporary"
    rmdir "$BACKUP_DIR/.operation.lock"
}
trap cleanup EXIT
stamp=$(date -u +%Y-%m-%dT%H%M%SZ)
dump="$BACKUP_DIR/daily/hlmemo-$stamp.dump"
dc exec -T db sh -eu -c 'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$temporary"
dc exec -T db pg_restore --list < "$temporary" > /dev/null
mv "$temporary" "$dump"
# Retain the latest snapshot for each UTC day and each ISO week, then 7 days + 4 weeks.
# Copies (not hardlinks) prevent a replacement of one tier from mutating the other tier.
weekly=$(python3 "$SCRIPT_DIR/retention.py" "$dump" "$BACKUP_DIR")
dc config --environment | python3 "$SCRIPT_DIR/upload.py" "$dump" "$weekly"
echo "Backup completed: $dump (7 daily / 4 weekly retention)" >&2
printf '%s\n' "$dump"
