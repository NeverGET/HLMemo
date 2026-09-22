# D-035 deploy verification — 2026-09-22

Repo `/Users/cemalkurt/Projects/HLMemo`, branch `main`. No commit, cloud resources, real secrets,
or application source changes. Initial `docs/consults/13-codex-prompt-d035.md` left untouched (concurrent work later tracked it);
new untracked `docs/consults/14-codex-prompt-d037.md` also untouched. Final observed HEAD: `5c14485`.
All edits are under `deploy/**` and `tests/deploy/**`; Makefile needed no change.
Fable co-architect/review/checkpoint evidence: [consults/d035-state.md](consults/d035-state.md).

## Implementation and evidence

1. `deploy.sh` uploads the complete `remote-deploy.sh` file, launches with `nohup setsid` and
   stdin `/dev/null`, all output redirected into a private log. Noninteractive Compose children
   also explicitly receive `/dev/null`; pg_restore receives only its intended dump stream.
   Real shell control flow with fake SSH/Docker reaches readiness on first install and upgrade.
   Both fake pg_dump and fake migration consume stdin and require EOF.
2. Restore runs current migration before restarting writers. A stateful old-schema fake refuses
   writer startup unless the migration moved it to head; migration failure keeps writers stopped.
   G-D4 also executes the real migration command during Docker restore.
3. Compose dotenv parsing tolerates absent/null service environment. Real Compose tests use both
   empty and comment-only backup.env and verify the default directory without creating it.
4. Readiness checks API loopback and an unpublished Caddy loopback proxy at 127.0.0.1:8081/ready.
   Only internal failures recover the old database. External-only HTTPS failure returns nonzero,
   prints DNS/AAAA/firewall/ACME guidance and retains the internally healthy release/current marker.
   Tests inject API, Caddy and external failures independently. Actual Caddy loopback returned
   status=ready with db/migration/models checks healthy on bake-astra.
5. Client streams log increments and reports atomic remote exit status. Whole client/SSH process
   group SIGKILL during migration and an SSH observer exit255 both leave the remote run completing.
   EXIT cleanup removes rollback secrets; early setup failures also publish status. Uncatchable
   SIGKILL/power loss cannot execute traps: next deploy sweeps stale snapshots only under its lock.
6. Rollback config is rendered after target checkout using its split env layout; previous running
   image IDs are pinned, and a missing baseline container refuses before stopping writers. Legacy
   common.sh test deliberately fails if sourced before checkout. Dollar round-trip remains tested.
7. Backup defaults are /var/backups/hlmemo in common.sh, example and systemd. Canonical path guards
   reject the repo itself/descendants, symlinked destinations/tiers and restore safety overrides.
8. Rejected cloud-init SSH drop-in is removed, verified with a real temporary file. RUNBOOK gives
   exact Engine27+ IPv6 network/proxy settings and source-address verification (not applied here).
   Successful deployments prune pre-upgrade snapshots (default5); failure/external failure does not.
   Recovered failures preserve both previous markers; successful internal cutovers promote them.

## Commands and exact last nonempty lines

Fresh synthetic env files: `/private/tmp/hlmemo-d035/{prod,app,api,db,backup}.env` (0600).
Project `bake-astra`; image `hlmemo:bake-astra`; ports `127.0.0.1:18080/18443`; internal TLS.
Runtime commands below used `HLM_ENV_FILE=/private/tmp/hlmemo-d035/prod.env`.
Build/start: `bash deploy/scripts/stack.sh up -d --build --wait --wait-timeout 300`, exit0.
No operation targeted project `hlmemo` or its host ports 8765/5432.

| Gate / command | Exact last nonempty line |
|---|---|
| G-D1 `bash deploy/scripts/stack.sh config -q` | `(no output; exit 0)` |
| G-D2 `bash deploy/scripts/smoke_tls.sh` | `PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking` |
| G-D3 `bash deploy/scripts/smoke_mcp.sh` (second run) | `PASS G-D3: HTTPS initialize + tools/list returned all five memory tools; project deploy-smoke reused; device revoked` |
| G-D4 `HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_backup_restore.sh` | `PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore` |
| G-D5 `terraform -chdir=deploy/terraform/hetzner validate -no-color` | `Success! The configuration is valid.` |
| G-D5 `terraform fmt -check -recursive deploy/terraform` | `(no output; exit 0)` |
| G-D6 `shellcheck deploy/backup/*.sh deploy/scripts/*.sh` | `(no output; exit 0)` |
| G-D7 `gitleaks dir deploy --no-banner` | `11:10PM INF no leaks found` |
| D13 `HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_deploy_backup.sh` | `D13 PASS: failed S3 upload; live stack unchanged; local pre-upgrade dump valid; stale mkdir lock ignored` |
| Fake SSH deploy end-to-end, stdin-reading pg_dump + migrate | `Deployment ready: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb (localhost)` |

`python3 -m unittest discover -s tests/deploy -v`:
```text
Ran 32 tests in 47.561s

OK
```
Separate stdin regression (`-p test_deploy_recovery.py -k stdin_reading`):
```text
Ran 1 test in 3.476s

OK
```
Fake SSH E2E invokes actual deploy.sh and uploaded runner; only SSH transport, Git/container runtime
and public curl are doubles. It is not a real VPS or real SSH-daemon deployment.

## New tests (14)

- `tests/deploy/test_backup_lock.py`: `test_empty_and_comment_only_backup_env_are_safe`
- `tests/deploy/test_deploy_recovery.py`: `test_early_setup_failure_still_publishes_final_status`
- `tests/deploy/test_deploy_recovery.py`: `test_external_failure_keeps_new_stack_and_never_restores_database`
- `tests/deploy/test_deploy_recovery.py`: `test_killing_client_process_group_does_not_stop_remote_migration`
- `tests/deploy/test_deploy_recovery.py`: `test_missing_rollback_container_fails_before_stopping_writers`
- `tests/deploy/test_deploy_recovery.py`: `test_rollback_config_render_follows_target_checkout`
- `tests/deploy/test_deploy_recovery.py`: `test_ssh_observer_failure_reports_guidance_and_remote_completes`
- `tests/deploy/test_deploy_recovery.py`: `test_stdin_reading_children_reach_deployment_ready`
- `tests/deploy/test_deploy_recovery.py`: `test_success_prunes_pre_upgrade_dumps_but_failure_does_not`
- `tests/deploy/test_restore.py`: `test_backup_directory_rejects_repository_and_symlink_destinations`
- `tests/deploy/test_restore.py`: `test_backup_directory_rejects_symlinked_tier`
- `tests/deploy/test_restore.py`: `test_restore_migration_failure_leaves_writers_stopped`
- `tests/deploy/test_restore.py`: `test_restore_safety_override_rejects_repository_before_stopping_writers`
- `tests/deploy/test_restore.py`: `test_restored_older_schema_migrates_before_starting_writers`

Existing cloud-init test now verifies actual invalid drop-in removal. Existing recovery test additionally
injects both internal readiness failures, verifies dump-before-restart and unchanged rollback markers.

## Independent review, corrections and limits

Native co-architect separately ran four key transport/readiness tests: `Ran 4 tests in 10.484s` / `OK`.
It found no blocking defect; explicit redirects and a stale comment were corrected. Final review also
closed observer log-creation race and restricted status/cleanup to the owning shell, not substitutions.
Official Docker Engine27 and port-publishing references support the documented IPv6 settings.

First full regression run exposed an assertion counting new no-deps startup as recovery; discriminator
was corrected. D13's first attempt hit a macOS /var alias mismatch after canonicalization; drill now
canonicalizes its temp path and passes. Terraform's provider handshake was blocked in sandbox;
read-only validate outside sandbox passed, no plan/apply/cloud call.

Real VM boot/ssh.socket reactivation, public DNS/AAAA/ACME, Linux IPv6 original source addresses,
real SSH-daemon disconnect behavior, systemd timer firing and S3 credentials/network remain untested.
Automatic internal rollback still discards writes after its pre-upgrade snapshot (documented);
external-only failures no longer do. Uncatchable SIGKILL/power loss cannot run EXIT handlers.

Cleanup: `bash deploy/scripts/stack.sh down -v` completed, exit0. Both
`docker ps -aq --filter label=com.docker.compose.project=bake-astra` and
`docker volume ls -q --filter label=com.docker.compose.project=bake-astra` produced no output.
Logs remain outside Git in `/private/tmp/hlmemo-d035/`. `git diff --check` quiet, exit0.
