Implement production deploy tooling for HLMemo in `deploy/`.

You are working in your OWN git worktree (path given below) on your OWN branch, created from `main`. HLMemo is a self-hosted memory backend (Postgres 17 + pgvector, a Starlette/MCP API on :8765, an embedding worker) that currently runs locally via `compose.yaml`. The next milestone (DECISIONS D-032) is a public VPS deployment on Hetzner Cloud (proposed CX43, Falkenstein/Nuremberg, D-005). Read first: README.md, docs/USAGE.md, compose.yaml, Dockerfile, src/hlmemo/server/app.py (routes: /health, /ready, /mcp, /devices/*, /admin/*), src/hlmemo/config.py, docs/decisions/DECISIONS.md (D-004, D-005, D-017, D-018, D-023, D-030, D-032), docs/decisions/PHASE0-GATE-REPORT.md.

DELIVERABLES (all under `deploy/`, plus a Makefile target group `deploy-*`):
1. `deploy/compose.prod.yaml`: db (NOT published to the host at all), migrate (one-shot `alembic upgrade phase0@head`), api (not published directly), worker, and `caddy` as the only public entrypoint (80/443). restart: unless-stopped, healthchecks, log rotation, sane resource limits for a 16 GB / 8 vCPU host. Secrets come only from an env file that is NOT committed.
2. `deploy/Caddyfile`: automatic HTTPS for `{$HLM_DOMAIN}`; reverse proxy to api:8765 for exactly /mcp, /health, /ready, /devices/*, /admin/*; everything else 404. Must not break streamable-HTTP/SSE (no response buffering on /mcp). Security headers, request body size limit. A switch for local testing with Caddy's internal CA (e.g. `HLM_TLS_MODE=internal`) so the whole stack can be exercised on this Mac without a real domain.
3. `deploy/.env.prod.example`: every variable the prod stack needs, documented, no real values.
4. `deploy/backup/`: `backup.sh` (pg_dump custom format, timestamped, retention 7 daily + 4 weekly, optional upload to S3-compatible object storage when S3 vars are set), `restore.sh` (restore a given dump into the running stack safely), and a way to schedule the backup on the server (systemd timer or cron).
5. `deploy/terraform/hetzner/`: hcloud provider; server (type/location/image as variables, default cx43 / fsn1 / ubuntu-24.04), SSH key, firewall (22 only from `var.admin_cidrs`, 80/443 from anywhere, everything else closed), cloud-init that installs Docker Engine + compose plugin, creates a non-root deploy user, enables unattended-upgrades and fail2ban. Outputs: server IPv4/IPv6. `terraform validate` must pass. Never commit state or tfvars with real values.
6. `deploy/scripts/deploy.sh`: deploy a given git ref to the server over SSH (idempotent; pulls/builds, runs migrations, `up -d --wait`, then checks https://$HLM_DOMAIN/ready); no secrets in the script.
7. `deploy/RUNBOOK.md`: provisioning, first deploy, admin-token bootstrap, registering a client device and the three CLIs against the remote URL (`hlm` commands from docs/USAGE.md), backup/restore, upgrade, rollback, and what to do if TLS issuance fails (include the sslip.io fallback: `hlm.<ip-with-dashes>.sslip.io`).

CONSTRAINTS
- Do NOT touch src/**, tests/** outside `tests/deploy/`, compose.yaml, or the running dev stack (compose project `hlmemo`, ports 8765/5432). Use compose project name `$BAKE_PROJECT` and host ports `$BAKE_HTTP_PORT` / `$BAKE_HTTPS_PORT` (given below) for any local run, so you never collide with other stacks on this machine.
- Do not create any cloud resources and do not run `terraform apply`. No real secrets anywhere in git.
- Do not git commit; leave your work as uncommitted changes in your worktree.
- The Docker image downloads the embedding model at build time (network needed; ~5 min first build).

GATES — the judge will run these identically on every submission; make them pass:
G-D1 `docker compose -f deploy/compose.prod.yaml --env-file deploy/.env.prod.example config -q` exits 0.
G-D2 Local TLS smoke: with `HLM_TLS_MODE=internal`, `HLM_DOMAIN=localhost` and the bake ports, the prod stack comes up healthy; `curl -sk https://localhost:$BAKE_HTTPS_PORT/ready` → 200; `curl -sk https://localhost:$BAKE_HTTPS_PORT/anything-else` → 404; Postgres is NOT reachable from the host on any port.
G-D3 MCP over TLS: an MCP `initialize` + `tools/list` against `https://localhost:$BAKE_HTTPS_PORT/mcp` with a valid device bearer returns the five memory tools (write a script `deploy/scripts/smoke_mcp.sh` or `tests/deploy/test_prod_stack.py` that does device bootstrap with the admin token and this check, so the judge can run it).
G-D4 Backup/restore drill: write data via the API, run backup.sh, wipe the database, run restore.sh, and the same data is readable again (scripted: `deploy/scripts/drill_backup_restore.sh`).
G-D5 `terraform -chdir=deploy/terraform/hetzner init -backend=false && terraform -chdir=deploy/terraform/hetzner validate` exit 0, and `terraform fmt -check -recursive deploy/terraform` exit 0.
G-D6 `shellcheck deploy/**/*.sh` clean.
G-D7 `gitleaks dir deploy --no-banner` finds nothing.

Tear down your local prod stack when done (`docker compose -p $BAKE_PROJECT ... down -v`).

FINAL MESSAGE (max ~400 words): files created; your own result for each gate G-D1..G-D7 with the exact command and its last output line; known limitations; anything you could not verify.
