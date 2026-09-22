# Production operations

This directory implements DP1 (D-032). No cloud resource is needed to validate it. Real configuration
belongs outside this checkout (D-018); the public templates contain placeholders. Commands below
are operator instructions, not evidence that a VPS has been provisioned. Never use the development
Compose project `hlmemo` for these scripts. Requirements: Docker Engine + Compose **2.24+**, Bash,
Python 3, curl; Terraform **1.6+** for provisioning; AWS CLI only for optional uploads.

## Configuration and local gate

Copy `deploy/.env.prod.example` to a private file with mode 0600. Replace the four `CHANGE_ME_*`
values with independent `openssl rand -hex 32` outputs, using the **same database password** in
`POSTGRES_PASSWORD` and `HLM_DB_DSN`. Hex avoids URI/Compose escaping pitfalls. Set a real domain
and `HLM_TLS_MODE=acme` in production. Do not paste tokens into commands, Git, Terraform, tickets or
logs. Env files are Compose dotenv syntax, not executable scripts. A service env file is passed only
to DB/app containers; Caddy receives only domain/TLS settings. Docker administrators can inspect
container environment, so Docker membership is privileged.

For local validation, use a private file such as `/private/tmp/hlmemo-local.env` and set:

```dotenv
HLM_DOMAIN=localhost
HLM_TLS_MODE=internal
HLM_IMAGE=hlmemo:bake-astra
BAKE_PROJECT=bake-astra
BAKE_HTTP_PORT=18080
BAKE_HTTPS_PORT=18443
BAKE_BIND_IP=127.0.0.1
```

The rest of the template, including generated credentials, is still required.

```sh
export HLM_ENV_FILE=/private/tmp/hlmemo-local.env
make deploy-up DEPLOY_ENV="$HLM_ENV_FILE"
curl -sk https://localhost:18443/ready
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost:18443/anything-else
bash deploy/scripts/smoke_tls.sh
bash deploy/scripts/smoke_mcp.sh
HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_backup_restore.sh
bash deploy/scripts/stack.sh down -v   # disposable LOCAL bake data only
```

`-k` is only for this local internal CA test. The probes skip verification only for internal TLS on
loopback; for other custom CAs set `HLM_CA_FILE=/path/to/ca.pem`. Caddy stores its CA in its `caddy_data`
volume. It is not installed into the Mac trust store. HTTP redirect targets normal port 443;
with remapped local ports use the explicit HTTPS URL. A raw Compose invocation needs **both**
`HLM_ENV_FILE=/absolute/file` and `--env-file /absolute/file`: the first selects service env injection,
the second provides Compose interpolation. The scripts handle both. `config -q` can render without
a real service env file; starting a DB without credentials fails closed.

`api`, `db` and `worker` have no published host ports. Resource ceilings are DB 4 GiB/2 CPUs,
API 3 GiB/2 CPUs, worker 4 GiB/3 CPUs, migration 1 GiB/1 CPU, Caddy 256 MiB/0.5 CPU; these are limits,
not reservations. Migration is intentionally one-shot, `restart: no`, and must exit zero.
Logs rotate at 5 × 10 MiB per container. Worker health checks observe successful poll-loop log
heartbeats; missing heartbeats for 180 seconds fail health. Docker does not automatically restart
an unhealthy but running process: alert on unhealthy state and aging job backlog. Caddy's container
probe checks its local admin listener; the external `/ready` probe verifies end-to-end service.

## Provisioning (operator-controlled, incurs cost)

Confirm D-005 server choice, price and location before provisioning. No `terraform apply` is part
of the tooling tests. Keep `HCLOUD_TOKEN` in your shell's secret manager, never tfvars. Put only the
SSH **public** key and restricted administrator CIDRs into a private tfvars file outside Git.
Terraform state contains infrastructure information; use an encrypted private backend with locking
for a shared deployment, or keep local state encrypted and backed up. `.terraform/`, state and
real tfvars are ignored; the provider dependency lock is public and should be retained.

```sh
terraform -chdir=deploy/terraform/hetzner init -backend=false
terraform -chdir=deploy/terraform/hetzner validate
terraform fmt -check -recursive deploy/terraform
# After explicit spending/provisioning approval:
terraform -chdir=deploy/terraform/hetzner plan -var-file=/secure/path/host.tfvars
terraform -chdir=deploy/terraform/hetzner apply -var-file=/secure/path/host.tfvars
terraform -chdir=deploy/terraform/hetzner output
```

Required private tfvars fields: `ssh_public_key` (OpenSSH public key), `admin_cidrs` (list such as
`["203.0.113.10/32"]`, replace documentation address). Defaults: `server_type="cx43"`, `location="fsn1"`,
`image="ubuntu-24.04"`, `deploy_user="hlmdeploy"`. Firewall ingress permits TCP 22 only from those
CIDRs and TCP 80/443 globally over IPv4/IPv6. No other inbound rules; outbound remains allowed.
Host snapshots default off and do not replace logical dumps. Cloud-init installs the official
Docker repository, Engine and Compose plugin, key-only SSH, fail2ban and unattended upgrades.
Automatic reboots are disabled; schedule maintenance for pending kernel/security reboots.

Verify the SSH host key fingerprint through the provider console before adding it to known_hosts;
the deploy script requires `StrictHostKeyChecking=yes`. Then:

```sh
ssh hlmdeploy@SERVER 'sudo cloud-init status --wait && docker compose version'
ssh hlmdeploy@SERVER 'sudo systemctl is-active docker fail2ban unattended-upgrades'
```

Create DNS A → output IPv4 and only publish AAAA after verifying IPv6 routing. No DNS provider is
hard-coded. Cloud-init creates `/etc/hlmemo`, `/opt/hlmemo` and `/var/backups/hlmemo`. Docker group
and passwordless sudo membership make `hlmdeploy` a privileged operator despite being non-root.

## First deploy and admin bootstrap

Install the completed private env file, with `HLM_BACKUP_DIR=/var/backups/hlmemo`:

```sh
scp /secure/path/prod.env hlmdeploy@SERVER:/opt/hlmemo/prod.env.pending
ssh hlmdeploy@SERVER 'sudo install -o hlmdeploy -g hlmdeploy -m 0600 /opt/hlmemo/prod.env.pending /etc/hlmemo/prod.env && rm /opt/hlmemo/prod.env.pending'
bash deploy/scripts/deploy.sh hlmdeploy@SERVER RELEASE_REF
```

`RELEASE_REF` must include the deploy tooling in the remote repository. An optional third argument
selects another repository URL; private repositories need read credentials installed separately on
the server. Do not embed credentials into the URL. Defaults: `/opt/hlmemo/app`, `/etc/hlmemo/prod.env`;
override with `HLM_REMOTE_DIR` / `HLM_REMOTE_ENV`. The script fetches the requested ref, resolves an
immutable commit, builds including model assets, stops writers, takes a pre-upgrade dump when a DB
is running, runs `alembic upgrade phase0@head`, starts with `up -d --wait`, then checks public HTTPS
readiness with certificate validation. A first uncached model build can take several minutes.
Repeated deployment of the same ref is supported. This is a single-node maintenance-window deploy,
not zero downtime. Failure after downtime begins requires inspection before resuming traffic.

At every API startup, `HLM_ADMIN_TOKEN` binds reserved device 1; there is no separate SQL bootstrap.
Use the **same** token on your workstation, loaded from a secret manager into `HLM_ADMIN_TOKEN`.
Load `HLM_REGISTRATION_SECRET` similarly for registration. Never use admin token for everyday clients.

```sh
uv sync --frozen
export HLM_SERVER_URL=https://memory.example.org/mcp  # replace domain
uv run hlm init --server "$HLM_SERVER_URL" --project my-project --device-name my-mac
uv run hlm --admin project create my-project --name 'My project'
uv run hlm device register --name my-mac --class personal
uv run hlm --admin device approve my-mac --class personal --grant my-project:write
uv run hlm device whoami
uv run hlm mcp add claude
uv run hlm mcp add codex
uv run hlm mcp add agy
uv run hlm query 'project context'
uv run hlm claude
uv run hlm codex --task 'inspect the project'
uv run hlm agy --headless --task 'summarise decisions'
unset HLM_ADMIN_TOKEN HLM_REGISTRATION_SECRET
```

`--wait` can be used on registration if approval happens in another terminal. The device token is
stored in the OS keychain, falling back to a 0600 credentials file; never put it in `hlm.toml`.
Codex's MCP entry stores the env-var name, so launch with `hlm codex` to inject `HLM_DEVICE_TOKEN`.
Claude and agy adapters store the bearer in their user configurations; protect those files.
See [the exact supported CLI commands](../docs/USAGE.md). `hlm doctor` also checks local DB/model
settings and may report those local checks absent on a remote-only workstation; `/ready` is the
server's authoritative DB/model readiness check. Use per-project grants and revoke lost devices.
Rotating admin token needs editing the env file and recreating API; device tokens are rotated by
revoke/register/approve and re-running `hlm mcp add`.

## Backups and restore

On the server:

```sh
cd /opt/hlmemo/app
export HLM_ENV_FILE=/etc/hlmemo/prod.env
bash deploy/backup/backup.sh
sudo install -m 0644 deploy/backup/hlmemo-backup.service deploy/backup/hlmemo-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hlmemo-backup.timer
sudo systemctl start hlmemo-backup.service
systemctl list-timers hlmemo-backup.timer
journalctl -u hlmemo-backup.service --since today
```

The timer runs daily; backup uses `pg_dump --format=custom`, validates its table of contents and
atomically publishes it. Local retention keeps the latest snapshot on each of 7 distinct UTC days
and the latest snapshot in each of 4 distinct ISO weeks. Missed days cannot be reconstructed.
For S3-compatible upload install the AWS CLI, set `S3_BUCKET`, `S3_PREFIX`, `S3_ENDPOINT_URL` and
AWS credential/region fields in the env file. Use least-privilege bucket access and server-side
encryption/versioning. Upload failure exits nonzero while retaining the local dump; monitor the
service. Configure bucket lifecycle independently: local retention does not delete remote objects.

```sh
# Restore ONLY trusted dumps, during an announced maintenance window:
bash deploy/backup/restore.sh /var/backups/hlmemo/daily/SELECTED.dump --yes
curl --fail https://YOUR_DOMAIN/ready
bash deploy/scripts/smoke_mcp.sh
```

Restore validates the dump before stopping Caddy/API/worker, saves and validates a safety dump,
recreates the configured database and restores in one transaction. All writers stay stopped on
failure; use the reported safety dump to recover. Safety dumps are not auto-pruned: remove them
only after confirming recovery. Do not restore a dump with untrusted SQL. Backup and restore share
an operation lock; after a killed process, confirm no operation is running before removing a stale
`.operation.lock` directory. Preserve a copy off-host; server loss destroys local volumes and dumps.
Caddy certificate/account volumes and the private env file are separate from database backups;
back them up encrypted (or reissue certificates with attention to CA rate limits).

The destructive `drill_backup_restore.sh` additionally requires a `bake-*` project, local Unix Docker
socket, loopback HTTPS and `HLM_ALLOW_DESTRUCTIVE_DRILL=1`. It writes a unique payload through MCP,
backs up, drops/recreates the database, proves zero public tables, restores and reads the identical
payload through MCP with the restored device bearer. It is intended for disposable local stacks.

## Upgrade and rollback

Before an upgrade, verify recent off-host backup and disk headroom. Deploy a reviewed immutable
release ref using the same `deploy.sh` command. Logs and last successful refs live under `/opt/hlmemo`
(`current-ref`, `previous-ref`). Inspect `stack.sh ps` and `stack.sh logs --tail 100` on failures. Do not
change major PostgreSQL versions by simply changing the image; plan a dump/restore or pg_upgrade.
Keep the old image/ref and pre-migration dump until validation succeeds.

If old code supports the current schema, redeploy the recorded last-good commit. Otherwise stop
traffic, check out the old ref, build its image, restore its matching **pre-upgrade** dump using that
ref's restore script, then start/check readiness. Restoring the old dump discards writes after that
snapshot; make a new safety backup first. Do not run blind `alembic downgrade` or automatically
rollback after a migration failure. A restored older schema must be run with matching old code;
`restore.sh` starts that checkout's services and migration head. There is no automatic point-in-time
recovery/WAL archive in this package.

## TLS, routing and operational diagnosis

Use `stack.sh logs --tail 100 caddy`, public DNS lookups and external TCP 80/443 checks. Check A/AAAA
point to this server, restrictive CAA records, provider firewall, and another listener on 80/443.
ACME requires outbound network and public challenge reachability. Remove an incorrect AAAA record.
Preserve `caddy_data` across upgrades to retain ACME accounts/certificates. Repeatedly deleting it
and retrying issuance can exhaust CA rate limits; fix the cause and respect retry delays.

Without a domain, set `HLM_DOMAIN=hlm.<ip-with-dashes>.sslip.io`, e.g. for documentation IP
`203.0.113.7`, `hlm.203-0-113-7.sslip.io` (replace with real public IPv4). Verify that DNS resolves
correctly, keep `HLM_TLS_MODE=acme`, recreate Caddy, then use that URL in `hlm init`. sslip.io DNS and
public CA issuance remain external dependencies; a paid domain may be needed if rate limits or
DNS policy block issuance. `internal` is a local test switch, not a public trusted-TLS fallback.

The proxy forwards only `/mcp`, `/health`, `/ready`, `/devices/*`, `/admin/*`; all other paths are 404.
It limits bodies to 2 MB, sets security headers, and flushes MCP responses immediately. Existing
Phase 0 MCP responds with stateless JSON; Caddy also preserves streaming/SSE without buffering.
`FORWARDED_ALLOW_IPS=*` is safe only while API is unpublished and all containers on its networks are
trusted; do not attach untrusted containers or expose its port. Registration uses a separate secret
and device approval; admin routes still require app authorization. Provider/model choices remain in
profiles/env configuration; current baked Phase 0 embedding assets are pinned by `models.lock` and
cannot be replaced simply by changing a model-name environment variable.

References: [Caddy TLS](https://caddyserver.com/docs/caddyfile/directives/tls),
[Caddy streaming proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy),
[Compose services](https://docs.docker.com/reference/compose-file/services/),
[Docker on Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[Hetzner provider](https://registry.terraform.io/providers/hetznercloud/hcloud/latest/docs/resources/server).
