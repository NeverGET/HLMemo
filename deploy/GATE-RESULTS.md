# DP1 local gate results — 2026-09-22

Worktree: `/Users/cemalkurt/Projects/HLMemo-bake/astra-r2`, branch `bake/r2-astra`.
Docker Desktop: 4 CPUs/~8 GB; intended server limits sized for 8 CPUs/16 GB. No cloud resources,
Terraform apply, SSH production deployment, commit, source edits or development-stack mutations.
Test env had generated disposable credentials and `localhost/internal`, project `bake-astra`,
ports `18080/18443`, loopback bind and image `hlmemo:bake-astra`; never committed.

All G-D1..G-D7 passed. Commands below ran from the worktree root. Last nonempty output lines shown;
for quiet tools, success means exit code zero with no output.

| Gate | Exact command | Last output / result |
|---|---|---|
| G-D1 | `docker compose -f deploy/compose.prod.yaml --env-file deploy/.env.prod.example config -q` | No output; exit 0 |
| G-D2 | `HLM_ENV_FILE=/private/tmp/hlmemo-bake-astra.env bash deploy/scripts/smoke_tls.sh` | `PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking` |
| G-D3 | `HLM_ENV_FILE=/private/tmp/hlmemo-bake-astra.env bash deploy/scripts/smoke_mcp.sh` | `PASS G-D3: HTTPS initialize + tools/list returned all five memory tools using an approved device` |
| G-D4 | `HLM_ENV_FILE=/private/tmp/hlmemo-bake-astra.env HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_backup_restore.sh` | `PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore` |
| G-D5 | `terraform -chdir=deploy/terraform/hetzner init -backend=false && terraform -chdir=deploy/terraform/hetzner validate && terraform fmt -check -recursive deploy/terraform` | `Success! The configuration is valid.` (fmt quiet, exit 0) |
| G-D6 | `shellcheck deploy/**/*.sh` | No output; exit 0 |
| G-D7 | `gitleaks dir deploy --no-banner` | `INF no leaks found` (timestamp prefix varies) |

Build/start commands:

```sh
HLM_ENV_FILE=/private/tmp/hlmemo-bake-astra.env docker compose -p bake-astra -f deploy/compose.prod.yaml --env-file /private/tmp/hlmemo-bake-astra.env build api
HLM_ENV_FILE=/private/tmp/hlmemo-bake-astra.env docker compose -p bake-astra -f deploy/compose.prod.yaml --env-file /private/tmp/hlmemo-bake-astra.env up -d --wait --wait-timeout 300
```

The build reused validated model-download layers. Actual readiness executed model hash checks and
inference. G-D2 inspected actual Docker port bindings and internal DB network, all long-running
service health states and exact-path negative controls. Raw curls separately returned ready=200,
unknown=404, and DB inspect returned `db bindings={}`. G-D3 registers/approves/revokes a real device;
it checks exactly five tools. G-D4 proved public table count zero between wipe and restore and
compared the identical payload with the restored bearer via TLS. An earlier attempt was interrupted
by a concurrent script edit; the stable final version was rerun to the PASS recorded above.

Additional checks: authenticated >2 MB MCP request returned HTTP 413; public ACME Caddy config passed
adapt/validate without certificate issuance; `git diff --check` clean. Backup producer tested 84
synthetic snapshots across 42 days: seven distinct daily and four weekly snapshots retained.
Independent file review and SSH-deploy mock scenarios are recorded in `consults/production-tooling-review.md`.

Not verified: real VPS/cloud-init boot, actual SSH release deployment, public ACME issuance, remote
three-CLI round trips, S3 upload credentials/network, systemd timer execution on Linux, sustained
load/resource sizing. The application currently emits stateless MCP JSON; proxy streaming settings
were reviewed, not tested against a separate long-lived SSE-emitting upstream. No claim of those
remote milestones is made.

Cleanup command (local bake volumes only):

```sh
HLM_ENV_FILE=/private/tmp/hlmemo-bake-astra.env docker compose -p bake-astra -f deploy/compose.prod.yaml --env-file /private/tmp/hlmemo-bake-astra.env down -v
```

Cleanup completed: no `bake-astra` containers or volumes remained. The three existing `hlmemo`
development services were still running with their original 8765/5432 bindings (read-only check).
