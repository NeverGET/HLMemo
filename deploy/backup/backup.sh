#!/usr/bin/env bash
# Snapshot-consistent backup. stdout is the completed dump path; deployment snapshots
# are independent of calendar rotation and survive until an explicit prune command.
# shellcheck disable=SC2016
set -euo pipefail
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/scripts/common.sh
source "$SCRIPT_DIR/../scripts/common.sh"
mode=daily
revision=
case ${1:-} in
    "") ;;
    --pre-upgrade)
        [[ $# == 2 && $2 =~ ^[a-f0-9]{7,64}$ ]] || { echo 'Usage: backup.sh --pre-upgrade GIT_SHA' >&2; exit 64; }
        mode=pre-upgrade; revision=$2 ;;
    --prune-pre-upgrade) [[ $# == 1 ]] || exit 64; mode=prune ;;
    *) echo 'Usage: backup.sh [--pre-upgrade GIT_SHA | --prune-pre-upgrade]' >&2; exit 64 ;;
esac
BACKUP_DIR=$(backup_dir)
for tier in daily weekly pre-upgrade; do
    backup_path "$BACKUP_DIR/$tier" >/dev/null
done
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly" "$BACKUP_DIR/pre-upgrade" </dev/null
BACKUP_DIR=$(cd "$BACKUP_DIR" && pwd)
# Advisory fd locks are released by the kernel, including after SIGKILL/reboot.
# A different filename also ignores legacy stale .operation.lock directories.
# The deploy runner already holds this lock on an inherited fd 8 for its final quiesced dump.
if [[ ${HLM_OPERATION_LOCK_HELD:-} != 1 ]] || ! { true >&8; } 2>/dev/null; then
    exec 8>"$BACKUP_DIR/.operation.flock"
fi
flock -n 8 </dev/null || { echo 'Another backup/restore is active; stack remains running.' >&2; exit 1; }
if [[ $mode == prune ]]; then
    keep=${HLM_PRE_UPGRADE_KEEP:-$(backup_value HLM_PRE_UPGRADE_KEEP)}
    python3 "$SCRIPT_DIR/retention.py" --prune-pre-upgrade "$BACKUP_DIR" --keep "${keep:-5}" </dev/null
    exit
fi
temporary=$(mktemp "$BACKUP_DIR/.dump.XXXXXX" </dev/null)
trap 'rm -f "$temporary" </dev/null' EXIT
stamp=$(date -u +%Y-%m-%dT%H%M%SZ </dev/null)
if [[ $mode == pre-upgrade ]]; then
    # The random suffix also preserves two deploy attempts within the same second.
    dump="$BACKUP_DIR/pre-upgrade/$revision-$stamp-${temporary##*.}.dump"
else
    dump="$BACKUP_DIR/daily/hlmemo-$stamp-${temporary##*.}.dump"
fi
dc exec -T db sh -eu -c 'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl' </dev/null > "$temporary"
dc exec -T db pg_restore --list < "$temporary" > /dev/null
# Keep every non-interactive child isolated from an invoking script stream.
# shellcheck disable=SC2217
mv "$temporary" "$dump" </dev/null
if [[ $mode == pre-upgrade ]]; then
    if ! backup_env | python3 "$SCRIPT_DIR/upload.py" "$dump"; then
        echo "WARNING: pre-upgrade S3 upload failed; local dump retained: $dump" >&2
    fi
    echo "Pre-upgrade backup completed: $dump (retained until explicit prune)" >&2
else
    weekly=$(python3 "$SCRIPT_DIR/retention.py" "$dump" "$BACKUP_DIR" </dev/null)
    backup_env | python3 "$SCRIPT_DIR/upload.py" "$dump" "$weekly"
    echo "Backup completed: $dump (7 daily / 4 weekly retention)" >&2
fi
printf '%s\n' "$dump"
