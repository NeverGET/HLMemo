You are the implementer on HLMemo (repo /Users/cemalkurt/Projects/HLMemo, branch main). Your production deploy tooling under `deploy/` won a blinded head-to-head and was merged (docs/bakeoff/r2/RESULT.md, DECISIONS D-034). An independent adjudicator confirmed six defects in it by experiment. Fix all six. Do not git commit.

DEFECTS (adjudicated; see docs/bakeoff/r2/blinded-findings.md rows targeting subA for the full original claims, and D-034):
1. D11 (Medium) retention.py keeps only the latest dump per UTC day, so a same-day second deploy deletes the earlier pre-upgrade dump, and the weekly slot gets overwritten — but RUNBOOK's rollback relies on "the matching pre-upgrade dump". Fix: deploy-time (pre-upgrade) dumps go to their own directory (e.g. `$BACKUP_DIR/pre-upgrade/<git-sha>-<stamp>.dump`), are excluded from daily/weekly rotation, kept until explicitly pruned (keep the last N=5 by default, configurable), and the path is recorded next to the previous-ref marker so rollback finds the exact dump. Update RUNBOOK rollback steps.
2. D13 (Medium) deploy.sh stops caddy/api/worker BEFORE the backup, so any backup failure (S3 upload failing or aws CLI missing, a stale `.operation.lock` after SIGKILL/reboot, a concurrent timer run) leaves production down via the ERR trap. Fix: take the pre-upgrade dump while the stack is live (pg_dump is snapshot-consistent) BEFORE stopping anything; make the deploy-time S3 upload non-fatal (warn, keep the local dump); replace the mkdir lock with `flock` on an fd so it cannot go stale; and on any failure after writers were stopped, the trap must restart the previous stack.
3. D01 (Medium, plausible) cloud-init on Ubuntu 24.04: `sshd -t` and `systemctl reload ssh` run under `set -e` before `install -d` creates /opt/hlmemo, /var/backups/hlmemo, /etc/hlmemo; with socket-activated ssh `/run/sshd` may be missing and reload fails on an inactive unit. Fix: create directories/users first; `mkdir -p /run/sshd` before `sshd -t`; use `systemctl try-reload-or-restart ssh.service`; make the ssh-hardening step unable to abort the rest of provisioning. If `cloud-init` is installable locally, run `cloud-init schema --config-file` on the rendered template.
4. D09 (Low) compose publishes 80/443 with an explicit `0.0.0.0` host IP, so there is no IPv6 listener although Terraform enables IPv6 and opens ::/0. Fix: omit the host IP in production so Docker binds both families (keep the loopback bind for local test mode), and publish 443/udp for HTTP/3 if Caddy serves it. Document AAAA in the RUNBOOK.
5. D05 (Low) one env file is injected into every container, so S3 backup credentials, the admin token and the registration secret reach api/worker/migrate/db. Fix: split into per-service env files (e.g. `app.env`, `db.env`, `backup.env`); backup/upload reads only `backup.env`; the db container gets only its Postgres vars; keep a single documented example per file; update deploy.sh, restore.sh, RUNBOOK and the local test path.
6. D02 (Low) smoke_mcp.sh creates a new `deploy-<uuid>` project per run and there is no delete API. Fix: use a fixed `deploy-smoke` project slug (tolerate "already exists"), and reuse or revoke the smoke device deterministically.

CONSTRAINTS
- Edit only `deploy/**`, the Makefile `deploy-*` targets, and tests under `tests/deploy/` (create if needed). Do not touch src/**, compose.yaml, Dockerfile, or other tests. A separate worker is concurrently editing src/** in another worktree.
- Local runs: compose project `bake-astra`, ports 18080/18443 (never touch project `hlmemo` or ports 8765/5432). Tear down with `down -v` when done.
- No cloud resources, no `terraform apply`, no real secrets in git.

GATES (all must pass at the end; report exact last lines):
G-D1 `docker compose -f deploy/compose.prod.yaml <your env-file args for the examples> config -q`
G-D2 local TLS smoke (your smoke_tls.sh) — /ready 200, unknown 404, DB unreachable from host
G-D3 MCP over TLS (your smoke_mcp.sh) — five tools; run it TWICE and show that no second project is created
G-D4 your backup/restore drill passes; plus a new drill for D13: simulate a failing S3 upload during deploy-time backup and show the stack stays up (or is restored) and the local pre-upgrade dump exists
G-D5 terraform validate + fmt -check
G-D6 shellcheck deploy/**/*.sh
G-D7 gitleaks dir deploy
Plus a retention test for D11 (fake dump files: two same-day pre-upgrade dumps both survive; daily/weekly rotation unchanged).

FINAL MESSAGE (max ~400 words): per defect "fixed: file:line — what"; gate results with exact last lines; anything you could not verify.
