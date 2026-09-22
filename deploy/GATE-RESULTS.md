# D-034 six-defect verification — 2026-09-22

Repo `/Users/cemalkurt/Projects/HLMemo`, branch `main`. Only deploy tooling, Makefile deploy
comments and tests/deploy changed. No commit, cloud resources or Terraform apply. Existing unrelated
untracked docs/consults/11-codex-prompt-d034.md preserved. Native independent review recorded in
[consults/d034-review.md](consults/d034-review.md).

Private generated files (not in Git): `/private/tmp/hlmemo-d034/{prod,app,api,db,backup}.env`.
`prod.env` selected localhost/internal, bake-astra, hlmemo:bake-astra, 127.0.0.1:18080/18443.
Build/start used `HLM_ENV_FILE=/private/tmp/hlmemo-d034/prod.env bash deploy/scripts/stack.sh
up -d --build --wait --wait-timeout 300`. All services healthy, one-shot migration exited zero.
No operations against project hlmemo or ports 8765/5432.

## Final gate commands and exact last nonempty lines

G-D1 (quiet, exit 0):
```sh
HLM_APP_ENV_FILE="$PWD/deploy/app.env.example" \
HLM_API_ENV_FILE="$PWD/deploy/api.env.example" \
HLM_DB_ENV_FILE="$PWD/deploy/db.env.example" \
docker compose -f deploy/compose.prod.yaml --env-file deploy/.env.prod.example config -q
```

For G-D2..4/D13: `HLM_ENV_FILE=/private/tmp/hlmemo-d034/prod.env` exported.

| Gate / command | Exact last nonempty output |
|---|---|
| G-D2 `bash deploy/scripts/smoke_tls.sh` | `PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking` |
| G-D3 `bash deploy/scripts/smoke_mcp.sh` (twice) | `PASS G-D3: HTTPS initialize + tools/list returned all five memory tools; project deploy-smoke reused; device revoked` (both final runs) |
| G-D4 `HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_backup_restore.sh` | `PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore` |
| D13 `HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_deploy_backup.sh` | `D13 PASS: failed S3 upload; live stack unchanged; local pre-upgrade dump valid; stale mkdir lock ignored` |
| G-D5 `terraform -chdir=deploy/terraform/hetzner validate -no-color` | `Success! The configuration is valid.` |
| G-D5 `terraform fmt -check -recursive deploy/terraform` | No output; exit 0 |
| G-D6 `shellcheck deploy/backup/*.sh deploy/scripts/*.sh` | No output; exit 0 |
| G-D7 `gitleaks dir deploy --no-banner` | `10:45PM INF no leaks found` |
| Regression `python3 -m unittest discover -s tests/deploy -v` | `Ran 18 tests in 13.925s` / `OK` |
| Rendered cloud-init, installed in disposable Ubuntu24.04 container: `cloud-init schema --config-file /tmp/cloud-init.yaml` | `Valid schema /tmp/cloud-init.yaml` |

Initial G-D3 first run ended `project deploy-smoke created; device revoked`; second ended
`project deploy-smoke reused; device revoked`. Final two reruns both reused it. Read-only SQL
`SELECT count(*) FROM projects WHERE slug = 'deploy-smoke'` returned exactly `1`.

D13 executed real pg_dump and pg_restore --list with a failing local aws stub (exit42), retaining
valid local pre-upgrade dump. Compared API/worker/Caddy container IDs and StartedAt before/after:
unchanged and running. Legacy stale mkdir lock existed throughout. Temporary drill data removed.
Recovery regression executes actual deploy script with transport/runtime doubles: upload, dump,
partial/persistent stop, migration/health failures and TERM after stop/during migration; captures
old images, restores matching dump if needed, restarts with no migration. Actual Compose roundtrip
regression preserves already-escaped dollar literals in recovery config.

D11 fake dumps: two same-day pre-upgrade files both survive; daily latest-per-day still keeps
7 days, weekly still 4 weeks. Explicit prune defaults to5, honors override, rejects zero.
Kernel flock regression rejects concurrent holder, then reacquires after SIGKILL with file intact.
Env tests use actual Compose: backup credentials reach no service, API-only secrets absent from
DB/worker/migrate, DB only PostgreSQL keys. Backup parser excludes host credentials and preserves
literal dollars. Production has no host_ip, local mode loopback, TCP80/443 and UDP443 present.

## Limits and cleanup

Real VM boot/socket-activated SSH, public IPv6 listeners/AAAA reachability, public ACME issuance,
SSH production rollout, live S3 credentials/network and systemd timer firing were not exercised.
Cloud-init schema plus injected SSH-command failures were tested locally. Automatic rollback
restores the pre-upgrade snapshot, discarding subsequent writes; see RUNBOOK rollback section.

Cleanup uses `HLM_ENV_FILE=/private/tmp/hlmemo-d034/prod.env bash deploy/scripts/stack.sh down -v`.
Cleanup completed: `docker ps -aq --filter label=com.docker.compose.project=bake-astra` and
`docker volume ls -q --filter label=com.docker.compose.project=bake-astra` both produced no output.
Final gitleaks scan: `10:45PM INF no leaks found`. `git diff --check` quiet, exit0.
