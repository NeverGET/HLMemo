#!/usr/bin/env bash
# D13: run the exact live pre-upgrade backup entrypoint with a failing S3 client.
set -euo pipefail
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=deploy/scripts/common.sh
source "$SCRIPT_DIR/common.sh"
[[ $COMPOSE_PROJECT == bake-astra ]] || { echo 'D13 drill requires bake-astra' >&2; exit 64; }
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
mkdir -p "$temporary/bin" "$temporary/backups/.operation.lock"
cat > "$temporary/bin/aws" <<'AWS'
#!/usr/bin/env bash
echo 'D13 injected S3 upload failure' >&2
exit 42
AWS
chmod 700 "$temporary/bin/aws"
printf 'S3_BUCKET=local-d13-failure-drill\nS3_PREFIX=hlmemo\n' > "$temporary/backup.env"
# Compare service IDs and start times to prove the dump/upload neither stopped nor
# recreated production writers. A stale legacy mkdir lock must also be harmless.
services=(api worker caddy)
before=()
for service in "${services[@]}"; do
  id=$(dc ps -q --status running "$service")
  [[ -n $id ]] || { echo "$service must be running before the drill" >&2; exit 1; }
  before+=("$(docker inspect --format '{{.Id}} {{.State.StartedAt}} {{.State.Running}}' "$id")")
done
revision=$(git -C "$DEPLOY_DIR/.." rev-parse HEAD)
dump=$(PATH="$temporary/bin:$PATH" HLM_BACKUP_DIR="$temporary/backups" HLM_BACKUP_ENV_FILE="$temporary/backup.env" \
  bash "$DEPLOY_DIR/backup/backup.sh" --pre-upgrade "$revision" 2> "$temporary/backup.log")
cat "$temporary/backup.log"
grep -q 'D13 injected S3 upload failure' "$temporary/backup.log"
grep -q 'WARNING: pre-upgrade S3 upload failed' "$temporary/backup.log"
[[ -s $dump && $dump == "$temporary/backups/pre-upgrade/"* ]]
dc exec -T db pg_restore --list < "$dump" > /dev/null
for index in "${!services[@]}"; do
  id=$(dc ps -q --status running "${services[$index]}")
  [[ -n $id && $(docker inspect --format '{{.Id}} {{.State.StartedAt}} {{.State.Running}}' "$id") == "${before[$index]}" ]]
done
bash "$SCRIPT_DIR/smoke_tls.sh"
echo 'D13 PASS: failed S3 upload; live stack unchanged; local pre-upgrade dump valid; stale mkdir lock ignored'
