# Final deploy round — D-041 / D-042 — 2026-09-23

Deployment changes implemented, **not a Phase 0 closeout**: required real-client-IP TLS gate
fails against current main application source. No commit, cloud resources, real secrets, src edits,
or HLMemo-bake worktree operations. Source HEAD: ab9f8f5b2f7dba3346d67d3c0891d16be8305535.
Existing unrelated untracked docs preserved. Native coarchitect and independent review exchange:
[consults/final-deploy-state.md](consults/final-deploy-state.md).

## Changes and validation

1. D3: compose.prod.yaml pins frontend 172.30.39.0/24; api.env.example sets matching
   HLM_TRUSTED_PROXY_IPS and removes FORWARDED_ALLOW_IPS. API alias api-frontend exists only
   on frontend; every Caddy upstream uses it, avoiding shared-outbound DNS ambiguity.
   Compose isolation regression passes. Runtime source prerequisite below remains blocking.
2. Caddyfile: /mcp 64 MiB, REST 64KiB. Actual TLS authenticated 5,000,000-byte POST passed Caddy
   and received an upstream rejection; details below distinguish the old app and SDK caps.
   This is explicitly not application acceptance. Reproducible check: scripts/smoke_edge.sh.
3. D4: fetched runner requires exact protocol 3 before checkout/build; lock retained across exec.
   Every release build gets org.opencontainers.image.revision=<target-sha>; existing/missing/wrong
   labels fail closed; label rechecked before migration and application startup. Tests include
   absent/protocol2 runners, cached/new missing/wrong labels and label changes mid-deploy.
   Actual Docker build with HLM_IMAGE_REVISION=$(git rev-parse HEAD) inspected label:
   ab9f8f5b2f7dba3346d67d3c0891d16be8305535. Legacy rollback remains pinned by captured image ID.
4. Ruff debt removed across deploy; all Python files in deploy+tests/deploy clean/formatted.
5. Observer timeout, SSH interruption and runner-gone diagnostics start on a fresh line;
   timeout regression asserts the newline.
6. bootstrap.sh provides provider-independent Ubuntu 24.04 preparation and two-phase SSH
   hardening, idempotent public key/user/directories/UFW/Docker apt setup, --dry-run and test-only
   --prepare-only. RUNBOOK begins with this path; Terraform/Hetzner is optional.
   Nine bootstrap unit tests and real container evidence below.
7. Host sizing: DB 2 GiB, API 1.5 GiB, worker 1.5 GiB, Caddy 256 MiB; steady 5.25 GiB, migration 768 MiB
   gives 6 GiB simultaneous ceilings. DB 512 MB shared_buffers/4MB work_mem/128MB maintenance_work_mem/
   max_connections50. Actual SHOW output matches. After TLS/MCP/restore drills, Docker stats:
   DB 65.79 MiB, API 453.2 MiB, worker 838.1 MiB, Caddy 16.93 MiB. This is smoke-load evidence,
   not a production capacity benchmark. RUNBOOK documents per-operation work_mem and OS headroom.

## Gates: commands and exact last nonempty lines

Only project bake-astra, loopback 127.0.0.1:18080/18443. Synthetic split env files:
/private/tmp/hlmemo-final-gates/{prod,app,api,db,backup}.env.
Runtime commands used HLM_ENV_FILE=/private/tmp/hlmemo-final-gates/prod.env.
Build/start: stack.sh up -d --build --wait --wait-timeout 300 (exit0).

| Gate / command | Exact last nonempty line |
|---|---|
| G-D1 `bash deploy/scripts/stack.sh config -q` | `(no output; exit 0)` |
| G-D2 `bash deploy/scripts/smoke_tls.sh` | `PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking` |
| G-D3 `bash deploy/scripts/smoke_mcp.sh` (second run) | `PASS G-D3: HTTPS initialize + tools/list returned all five memory tools; project deploy-smoke reused; device revoked` |
| G-D4 `HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_backup_restore.sh` | `PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore` |
| G-D5 `terraform -chdir=deploy/terraform/hetzner validate -no-color` | `Success! The configuration is valid.` |
| G-D5 `terraform fmt -check -recursive deploy/terraform` | `(no output; exit 0)` |
| G-D6 `shellcheck deploy/**/*.sh deploy/bootstrap.sh` | `(no output; exit 0)` |
| G-D7 `gitleaks dir deploy --no-banner` | `12:25AM INF no leaks found` |
| D13 `HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_deploy_backup.sh` | `D13 PASS: failed S3 upload; live stack unchanged; local pre-upgrade dump valid; stale mkdir lock ignored` |
| `uv run ruff check deploy tests/deploy` | `All checks passed!` |
| `uv run ruff format --check deploy tests/deploy` | `25 files already formatted` |

UV_CACHE_DIR=/private/tmp/hlmemo-final-uv used to keep cache writable. Docker/runtime tests and
Terraform provider validation ran with approved shell access. No plan/apply. `git diff --check` clean.

`python3 -m unittest discover -s tests/deploy -v`:
```text
Ran 62 tests in 93.685s

OK
```
Log: /private/tmp/hlmemo-final-tests.log. An initial new sizing test incorrectly assumed Compose
JSON memory values were integers; fixed to parse numeric strings, then full suite rerun green.
Fake-SSH actual runner/backup/migration children drain stdin, asserting EOF; initial+upgrade both:
```text
Deployment ready: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb (localhost)
Deployment ready: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb (localhost)
```
Log: /private/tmp/hlmemo-final-fake-ssh.log. SSH/Git/Docker/curl are test doubles for these cases.

## Required edge check: one PASS, one FAIL (source dependency)

`HLM_ENV_FILE=... bash deploy/scripts/smoke_edge.sh`, exit1:
```text
PASS edge 5 MB: Caddy forwarded body; legacy SDK returned 413 (API cap permits 5 MB)
INFO:     172.30.39.3:41614 - "GET /ready?edge-real-ip-a5c2a8f6df704dd6bb57e66a9ebda971 HTTP/1.1" 200 OK
FAIL TLS client IP: expected 172.21.0.3; API must implement HLM_TRUSTED_PROXY_IPS (D-039)
```
Initially the old main outer app rejected the declared length with JSON413 E_INVALID_ARG.
For stronger proxy evidence, ONLY the temporary test app.env then set
HLM_REQUEST_MAX_BODY_BYTES=67108864. No source/production example changed. This lets the outer
middleware consume the full 5,000,000-byte body before SDK dispatch. The separate old SDK bound
still returns plain `Request body too large`/413. The probe verifies this request's unique API
access-log marker and effective app cap, so this proves Caddy forwarding, not application success.
The local test image with the real SHA OCI label was used for this final run.

A real TLS client in the worker container makes its source IP independently known and avoids
Docker Desktop host-NAT ambiguity; sends spoofed XFF 198.51.100.77 to test stripping as well.
Main's config currently has no trusted-proxy setting/parser; app starts Uvicorn with default
loopback-only proxy trust. Deploy env cannot add this source capability. Source task must integrate
D-039/D-041, then rebuild and rerun smoke_edge.sh without the temporary body override. Do not
reinstate wildcard trust to fake a PASS. Actual main SDK contract also remains a source prerequisite.
Log: /private/tmp/hlmemo-final-edge-full.log (earlier /private/tmp/hlmemo-final-edge.log).
No src or other worktree changed.

## Real Ubuntu bootstrap evidence

Throwaway ubuntu:24.04 container bake-astra-bootstrap, no published ports; removed afterwards.
Official apt packages installed (Docker Engine 29.8.1, Compose v5.5.1). Preparation ran twice;
verified deploy docker/sudo groups, directories 0750/ownership, single authorized key, persisted
UFW 22/80/443 TCP + 443 UDP rules, fail2ban-client -t, sshd -t. Real intra-container sshd proved new
public-key SSH authentication/SSH_USER_AUTH, finalization, second login after disabling root/
password/kbdinteractive. Rerun after hardening retained settings; managed CIDR replacement passed;
simulated reload failure restored byte-identical configuration and subsequent actual login passed.
Dry-run safety tests confirm no mutation commands executed.

Exact evidence markers:
```text
BOOTSTRAP_CONTAINER_CHECKS_OK (systemctl mocked; firewall inactive)
BOOTSTRAP_RERUN_AND_ROLLBACK_OK
```
Missing preserved SSH_CONNECTION with --admin-cidr also rejected before provisioning.
Logs: /private/tmp/hlmemo-bootstrap-test/{prepare,rerun,verify,verify-rerun,cidr-guard}.log.
Reproduction drivers: /private/tmp/hlmemo-bootstrap-test/{bootstrap.sh,verify.sh,verify-rerun.sh}.
A separate systemd feasibility attempt used jrei/systemd-ubuntu:24.04. No ARM64 manifest was
available; AMD64 emulation was used, unprivileged, without host mounts/ports. PID1 exited255:
`Failed to mount tmpfs ... /run ... Operation not permitted`,
`Failed to mount cgroup ... /sys/fs/cgroup/systemd ... Operation not permitted`,
`Failed to mount API filesystems. Exiting PID1`.
Evidence: /private/tmp/hlmemo-bootstrap-test/systemd-attempt.log; container removed.
Thus systemd could not start under the tested isolated Docker permissions; privileged mode or
host cgroup mounts were not attempted. In the successful non-systemd Ubuntu bootstrap test, systemctl reload was a stub
sending real sshd HUP. No privileged/cgroup access was used. Docker daemon startup, active kernel
firewall enforcement, systemd service activation, running fail2ban/unattended services and real
VPS boot remain unexercised. Public DNS/ACME/IPv6 and real S3 also require real infrastructure.

## Independent review and cleanup

Uninvolved reviewer inspected final bootstrap/protocol/labels/network/sizing and independently
ran 9 bootstrap tests: PASS; no new High or deploy-blocking Medium. It confirmed the source blocker.
Its sandboxed recovery attempt could not observe detached processes (ps restriction), so is not
counted as an independent PASS; the approved-shell full suite above is the runtime evidence.

Cleanup: stack.sh down -v completed for bake-astra. Final container and volume label queries
returned no output; see /private/tmp/hlmemo-final-cleanup.log. Bootstrap container independently removed.
