# D-038 deploy verification — 2026-09-22

All six requested items fixed without commit. Main checkout only; no src/** or other worktree
edits. Existing/unrelated docs changes preserved. Native Fable coarchitect consultation and
independent review recorded in [consults/d038-state.md](consults/d038-state.md); user scope
forbids docs/consults and .fable writes for this task.

## Changes

- N1: remote-deploy.sh:156 freezes the actual previous API/worker image under its commit tag,
  refuses a conflicting immutable tag, builds/reuses a separate SHA tag, and atomically
  publishes HLM_IMAGE only after internal readiness. release_env.py preserves dotenv contents
  without executing them. Env publication failure still triggers recovery. restore.sh and
  stack.sh therefore use the previous image/old Alembic after a failed release.
  Existing /etc/hlmemo directories need deploy-user ownership for atomic file replacement;
  cloud-init default and RUNBOOK instructions corrected, preflight refuses otherwise.
- N2: deploy.sh fetches and extracts the runner from the exact remote target SHA. A missing
  runner refuses before checkout/build/backup/restore. Bootstrap releases its lock before
  handing off to older runners; initial clones have clean checkout and no adopted baseline.
- N3: runner PID and live-process heartbeat recorded; observer detects dead/zombie PID without
  status. Default overall observation timeout 1800s also bounds stalled SSH. Required remote
  commands are checked individually; setsid launch failure exits promptly.
- N4: explicit guard rejects any Compose model change between previous and target before
  build/backup/stop. Thus the rendered rollback model equals the previous release model.
  RUNBOOK documents intentional staged model migrations; no unsafe bypass flag.
- Same-second daily names have a random suffix, including distinct remote object keys;
  retention still keeps newest per 7 days / 4 weeks, with deterministic same-second ties.
- Ruff debt removed from all tests/deploy files; assertions retained and 15 tests added.

## Actual gate commands and exact last nonempty lines

Only compose project bake-astra, host bindings 127.0.0.1:18080/18443, synthetic env files.
Runtime logs/env: /private/tmp/hlmemo-d038-gates/. No cloud resources or real secrets.
`HLM_ENV_FILE=/private/tmp/hlmemo-d038-gates/prod.env` for runtime commands.
Build/start: `bash deploy/scripts/stack.sh up -d --build --wait --wait-timeout 300`, exit0.
Image hlmemo:bake-astra SHA256 fd38557d84cf45a86cb8bef80d0a59923829429f50ed823c344eb1c4a4f1a177;
main source HEAD 085625c4ec9c177aa9aed4c46af58de5a3c43d9c, cached Docker layers.

| Gate / command | Exact last nonempty line |
|---|---|
| G-D1 stack.sh config -q (with split env paths) | `(no output; exit 0)` |
| G-D2 smoke_tls.sh | `PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking` |
| G-D3 smoke_mcp.sh (second run) | `PASS G-D3: HTTPS initialize + tools/list returned all five memory tools; project deploy-smoke reused; device revoked` |
| G-D4 HLM_ALLOW_DESTRUCTIVE_DRILL=1 drill_backup_restore.sh | `PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore` |
| G-D5 terraform -chdir=deploy/terraform/hetzner validate -no-color | `Success! The configuration is valid.` |
| G-D5 terraform fmt -check -recursive deploy/terraform | `(no output; exit 0)` |
| G-D6 shellcheck deploy/backup/*.sh deploy/scripts/*.sh | `(no output; exit 0)` |
| G-D7 gitleaks dir deploy --no-banner | `11:30PM INF no leaks found` |
| D13 HLM_ALLOW_DESTRUCTIVE_DRILL=1 drill_deploy_backup.sh | `D13 PASS: failed S3 upload; live stack unchanged; local pre-upgrade dump valid; stale mkdir lock ignored` |
| uv run ruff check tests/deploy | `All checks passed!` |
| uv run ruff format --check tests/deploy | `7 files already formatted` |

Ruff ran with isolated UV_CACHE_DIR=/private/tmp/hlmemo-d038-uv. Local process tests require
sandbox escalation because macOS sandbox denies ps (otherwise falsely reports runner gone).
Terraform provider handshake also required escalation; no plan/apply was performed.

`python3 -m unittest discover -s tests/deploy -v` (log /private/tmp/hlmemo-d038-tests.log):
```text
Ran 47 tests in 80.385s

OK
```

Fake-SSH end-to-end evidence (/private/tmp/hlmemo-d038-fake-e2e.log):
```text
Deployment ready: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb (localhost)
PASS failed B after build -> restore.sh: running=sha256:old-image; alembic=sha256:old-image (release A)
```
Actual deploy/runner/backup/restore shell code runs; SSH transport, Git/container runtime and
public curl are doubles. The migration and pg_dump children explicitly drain stdin and assert EOF.
Failure-to-restore regression separately covers dump, migration and health failures after build.

## New tests (15)

- `test_backup_lock.py::test_same_second_daily_backups_have_distinct_upload_keys_and_keep_latest`
- `test_backup_retention.py::test_same_second_rotation_keeps_current_dump_over_random_suffix_order`
- `test_deploy_recovery.py::test_compose_change_refused_before_build_backup_or_stop`
- `test_deploy_recovery.py::test_conflicting_previous_release_tag_fails_without_retag_or_build`
- `test_deploy_recovery.py::test_existing_release_image_is_reused_without_rebuild`
- `test_deploy_recovery.py::test_failed_build_release_then_restore_uses_previous_image_and_alembic`
- `test_deploy_recovery.py::test_image_publication_failure_recovers_previous_release`
- `test_deploy_recovery.py::test_success_publishes_immutable_image`
- `test_deploy_transport.py::test_each_required_detachment_command_is_checked`
- `test_deploy_transport.py::test_observer_timeout_bounds_live_runner_and_stalled_ssh`
- `test_deploy_transport.py::test_pre_runner_ref_fails_before_checkout_or_deploy_mutation`
- `test_deploy_transport.py::test_real_git_initial_clone_runs_legacy_runner_with_its_own_lock`
- `test_deploy_transport.py::test_runner_is_extracted_from_resolved_target_ref`
- `test_deploy_transport.py::test_setsid_launch_failure_does_not_poll_forever`
- `test_deploy_transport.py::test_sigkill_runner_is_detected_without_final_status`

## Independent verification and cleanup

Uninvolved gate/review worker ran 15 selected N1/N4/transport/cloud tests:
`Ran 15 tests in 22.374s` / `OK`. Additional fault injection verified atomic rename failure
leaves original env unchanged, removes temporary file and preserves mode/dollar literals.
N4 probe found zero Docker calls, no target checkout, no env mutation or image state on refusal.
No blocking review finding. Root added publication-failure recovery and immutable-tag conflict/
reuse regressions and included them in the final 47-test run.

`bash deploy/scripts/stack.sh down -v` exit0. Queries for bake-astra containers and volumes
returned no output. No operation targeted project hlmemo or host ports 8765/5432.

Unverified without real infrastructure: actual VPS/SSH daemon disconnect, genuine OOM,
public DNS/ACME/IPv6, systemd timer, real S3 credentials/network. SIGKILL detection uses real
local processes with fake SSH. Uncatchable termination still cannot execute recovery traps;
observer reports this explicitly. Intentional Compose model changes require a staged manual
migration. Internal rollback restores the pre-upgrade dump and can discard later writes.
