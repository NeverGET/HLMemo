# Opus 5.5 adversarial review of the D-035 deploy fixes (2026-09-22)

## Status of prior findings

| # | Status | Evidence / attack attempted |
|---|---|---|
| 1 stdin swallowing | **FIXED** | The runner is uploaded as a file (deploy.sh:31-35) and started with `nohup setsid bash FILE </dev/null >log 2>&1`. It then runs `exec </dev/null` (remote-deploy.sh:21). Neither the script's stdin nor the ssh channel is reachable, so even a child that reads stdin gets EOF. The only intentional stdin feeds are the dump into `pg_restore` (:90) and the python heredoc (:127). I found no remaining path. |
| 5 detach, SIGPIPE, traps | **FIXED** | `setsid`, `nohup` and file stdio mean an ssh disconnect sends no HUP or PIPE. `finish` (:9-19) writes `status` atomically on every trapped exit. On bash 5 the EXIT trap also runs on an untrapped TERM (I checked). Residual: see N3. |
| Races between deploys | **FIXED** | `flock -n` on `.deploy.lock` (:31-32). A second run fails fast and records status 1. The timer backup and restore.sh are excluded by the `.operation.flock` pre-check (:160-162) before writers stop. |
| Secret temp files | **FIXED** | `.rollback-compose.*` is removed in `finish` on EXIT, ERR, INT and TERM. Stale copies from a SIGKILL are swept after the lock is taken (:35). The backup `.dump.*` temp is also removed on TERM. |
| 2 restore at schema head | **PARTIAL** | restore.sh:49 now runs migrate before starting services. But migrate runs from the `hlmemo:prod` image, not from the checkout. See N1. |
| 3 empty backup.env | **FIXED** | common.sh:45 `.get("environment") or {}`. |
| 4 internal vs external | **FIXED** | Internal readiness failures are all under the ERR trap: the `--wait` up (:170), `/ready` inside the API container (:173), and Caddy's loopback :8081 route (:174). `trap - ERR` (:175) is set before the markers and the public curl, so an external-only failure exits 1 with no DB rollback. Caddy's healthcheck uses the admin API and `/ready` checks only local state, so ACME or DNS problems cannot fail the internal checks. |
| 6 rollback env from old checkout | **FIXED** (trade-off) | Config is now rendered from the target compose file, with the old image IDs pinned (:123-144). This introduces N4. |
| 7 backup dir | **FIXED** | backup.env.example and the service now set `/var/backups/hlmemo`, and `backup_path` refuses any directory inside the repository. |
| Marker overwrite residual | **FIXED** | Markers are written only after the internal checks pass (:180-187). |
| D01 residual | **FIXED** | A rejected drop-in is now removed (tftpl). |

## New defects

| # | file:line | Trigger → observed vs expected | Sev | Conf |
|---|---|---|---|---|
| N1 | remote-deploy.sh:150 plus restore.sh:49-50 | 1. A deploy fails anywhere after `dc build` (dump failure, lock contention at :162, or any recovered failure). The build has already retagged `hlmemo:prod` to the failed release, while the old containers keep running from pinned IDs and the checkout goes back to `previous`. 2. Later, the operator runs `restore.sh` (or any `stack.sh up`). It runs the **failed release's** alembic, then recreates api and worker from `hlmemo:prod`: failed code on the old checkout's bind mounts and Caddyfile. Expected: the schema and code of the checkout / current-ref. Fix: retag, or rebuild, the rollback image on recovery, or pin by digest in `.env`. | Med-High | High |
| N2 | deploy.sh:32 (runner taken from the local working tree) vs :116-117, :153 (common.sh and backup.sh taken from the target ref) | The runner is not part of the reviewed ref. Deploying any pre-D-035 ref, including the current `main` 5c14485 or a "redeploy the previous release" rollback, gives a Caddyfile with no `:8081` site. :174 then fails after the migration, the pre-upgrade dump is restored, and the deploy always fails. More generally, runner and tooling versions can drift apart silently. Fix: run `deploy/scripts/remote-deploy.sh` from `FETCH_HEAD` (`git show`), or refuse a mismatched runner hash. | Med | High |
| N3 | deploy.sh:33, :40-59 | If the runner dies without writing `status` (OOM or SIGKILL during a large build, or setsid failing to start), the observer polls over a new SSH connection every second, forever. There is no PID, heartbeat or timeout. `command -v nohup setsid bash` returns 0 when any one of the three exists (I checked in bash and sh), so the preflight guard never fires. The RUNBOOK says "investigate the running process", but no PID is recorded. | Low-Med | High |
| N4 | remote-deploy.sh:126-139 | The rollback runs the old image under the new release's compose model: its healthchecks, commands, env, and mounts like `worker_entrypoint.py`. A release that changes a healthcheck or command makes the automatic recovery `--wait` fail exactly when it is needed ("Automatic recovery failed"). This is not triggered by this diff, because compose.prod.yaml is unchanged. | Low | Med |

Minor: `.deploy-runs/*` (logs) are never pruned. Checks run: shellcheck and `bash -n` are clean on all deploy and backup scripts. I did not run the pytest suite, because it includes a real-compose roundtrip test.

## Verdict
**Nearly.** The blocking stdin defect and items 3–7 are fixed, and neither the detached run nor the readiness split let an input trigger their original failures. N1 and N2 are real. Fix at least N2, by deploying the runner from the ref being deployed, and N1, by resetting `hlmemo:prod` on failure, before the first real VPS deploy. For the first deploy on an empty host, N1 and N4 cannot fire, and N2 cannot fire if the target ref contains this diff.