# Production tooling consultation and independent review — 2026-09-22

Scope: DP1 only, user-authorized `deploy/` + Makefile; no cloud resources, commit or changes to the
development stack. This record is under `deploy/consults/` rather than `docs/consults/` to preserve
the task's explicit file scope. Consultation used a native independent Astra worker with session
model/reasoning inherited, not a duplicate external CLI session.

## Design brief and decisions

Review requested: Caddy exact routing/TLS/SSE, env-only secrets, worker health without source edits,
migration/restore lifecycle, deployment rollback, network boundaries, Terraform security defaults.

- Keep DB on an internal network with no port bindings; retain outbound connectivity for Caddy and
  provider-agnostic app/worker configuration.
- Use explicit ACME/internal Caddy snippets; preserve `/mcp` path and `flush_interval -1`.
- Readiness uses the application's real DB/migration/model checks. Observe existing worker heartbeat
  logs through a deploy-owned launcher, since source has no durable heartbeat endpoint/file.
- Include cursor secret and forwarded-header trust configuration; keep API private and document the
  container trust boundary. Secrets enter services through the selected private env file.
- Restore offline with a pre-restore safety dump; require explicit destructive local drill opt-in.
- Deploy immutable resolved commits, build before downtime, back up before migration, never downgrade
  schema automatically. Record last successful revision separately from current checkout.

## Findings and resolution

1. Timer initially disagreed with cloud-init paths/user. Corrected to `hlmdeploy`, `/opt/hlmemo/app`,
   `/etc/hlmemo/prod.env` and `/var/backups/hlmemo`.
2. A failed B build after healthy A, then retrying B, could lose rollback A. Use validated `current-ref`
   as baseline and persist previous-ref before checkout/build. Producer's mocked failed-build/retry,
   repeated-deploy and invalid-reference checks passed. No actual SSH deployment was performed.
3. Destructive drill checked DOCKER_HOST before DOCKER_CONTEXT; corrected to Docker's actual precedence.
4. Removed fallback to committed placeholder credentials: raw config can render without `.env.prod`,
   but an unconfigured DB cannot start. Scripts require an existing explicit env file.
5. Backup/restore share an operation lock; restore and deployment failure paths stop writers even
   after partial startup. Portable mktemp templates use trailing X characters.

Final independent reread found no remaining material issue in these changes or runbook command
syntax. Reviewer only inspected files; root performed live local gates. See `../GATE-RESULTS.md`.
