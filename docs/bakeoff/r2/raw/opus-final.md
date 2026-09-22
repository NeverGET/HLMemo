# HLMemo production runbook (D-032 DP1)

One Hetzner Cloud VM (D-005: CX43, 8 vCPU / 16 GB, fsn1/nbg1), Docker Compose, Caddy as the only
public entrypoint with automatic HTTPS.

```
internet ──80/443 (tcp) + 443/udp──▶ caddy ──edge net──▶ api:8765 ──backend net (internal)──▶ db:5432
                                                          worker ──backend + egress
```

| file | purpose |
|---|---|
| `deploy/compose.prod.yaml` | db (no published port, internal network), migrate (one-shot `alembic upgrade phase0@head`), api, worker, caddy |
| `deploy/Caddyfile` | TLS for `$HLM_DOMAIN`; proxies only `/mcp /health /ready /devices/* /admin/*`, everything else 404; unbuffered (`flush_interval -1`) for streamable HTTP/SSE |
| `deploy/.env.prod.example` | every variable, documented. The real file is `/opt/hlmemo/env/.env.prod` on the server (0600) |
| `deploy/backup/` | `backup.sh`, `restore.sh`, `hlmemo-backup.{service,timer}` |
| `deploy/terraform/hetzner/` | server + SSH key + firewall + cloud-init |
| `deploy/scripts/` | `deploy.sh` (SSH deploy of a git ref), `smoke_tls.sh`, `smoke_mcp.sh`, `drill_backup_restore.sh`, `local_up.sh`/`local_down.sh` |

Real values (tfvars, env file, domain, IPs) never enter this public repo; they belong in the private
ops repo `NeverGET/hlmemo-ops` (D-018). `deploy/.gitignore` blocks the usual files; the pre-commit
hook runs gitleaks.

Tools on the operator machine: `terraform >= 1.6`, `ssh`, `git`, `curl`, `jq`, `openssl`, and `uv`
for the `hlm` CLI.

---

## 0. Rehearse locally first (D-004)

Everything below can be exercised on a laptop with Caddy's internal CA, in a separate compose project:

```sh
make deploy-local-up   BAKE_PROJECT=hlm-localprod BAKE_HTTP_PORT=18080 BAKE_HTTPS_PORT=18443
make deploy-smoke      BAKE_PROJECT=hlm-localprod BAKE_HTTP_PORT=18080 BAKE_HTTPS_PORT=18443   # TLS edge + MCP
make deploy-drill      BAKE_PROJECT=hlm-localprod BAKE_HTTP_PORT=18080 BAKE_HTTPS_PORT=18443   # backup/restore
make deploy-local-down BAKE_PROJECT=hlm-localprod BAKE_HTTP_PORT=18080 BAKE_HTTPS_PORT=18443
```

`local_up.sh` writes `deploy/.env.local` (gitignored, random secrets, `HLM_DOMAIN=localhost`,
`HLM_TLS_MODE=internal`). `make deploy-gates` runs G-D1..G-D7 in one go.

## 1. Provision the server (Terraform)

```sh
export HCLOUD_TOKEN=...                      # Hetzner Console -> project -> Security -> API tokens (read/write)
cd deploy/terraform/hetzner
cp terraform.tfvars.example terraform.tfvars # gitignored; set ssh_public_key and admin_cidrs (your IP/32)
terraform init
terraform plan -out plan.tfplan              # review: 1 server, 1 firewall, 1 ssh key
terraform apply plan.tfplan
terraform output                             # ipv4_address, ipv6_address, sslip_domain, ssh
```

Firewall: 22/tcp only from `admin_cidrs`; 80/tcp, 443/tcp, 443/udp and ICMP from anywhere; all other
inbound dropped. Cloud-init installs Docker Engine + compose plugin from Docker's apt repo, creates
the `deploy` user (SSH key only, docker group, passwordless sudo), hardens sshd (no root, no
passwords, `AllowUsers deploy`), enables unattended-upgrades (auto-reboot 04:30 UTC if
`auto_reboot`) and fail2ban (sshd jail), and creates `/opt/hlmemo/{src,env}` and
`/var/backups/hlmemo`.

Wait until cloud-init is done (2-4 min):

```sh
ssh deploy@<ipv4> 'cloud-init status --wait && docker compose version'
```

State stays local (`terraform.tfstate`, gitignored). Keep it in the private ops repo's backend, not
here. `delete_protection`/`rebuild_protection` are on by default; `terraform destroy` needs
`protect = false` first. Recreating the server changes its IP (update DNS / sslip domain).

## 2. Choose the domain

- Own domain: create `A` (and `AAAA`) records `hlm.example.org -> <ipv4>/<ipv6>`, TTL 300. Wait until
  `dig +short hlm.example.org` returns the server IP **before** the first deploy (Caddy requests the
  certificate on start).
- No domain: `hlm.<ipv4-with-dashes>.sslip.io` (Terraform output `sslip_domain`), e.g.
  `hlm.203-0-113-7.sslip.io`. sslip.io resolves it to the embedded IP; Let's Encrypt issues for it.

## 3. Server env file (once)

On your machine, fill a copy of the template and upload it — never commit it:

```sh
cp deploy/.env.prod.example /tmp/hlm.env && chmod 600 /tmp/hlm.env
# generate:
openssl rand -hex 32                                                    # POSTGRES_PASSWORD
openssl rand -hex 32                                                    # HLM_CURSOR_SECRET
printf 'hlm_%s\n' "$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')" # HLM_ADMIN_TOKEN
openssl rand -base64 24 | tr '+/' '-_' | tr -d '='                      # HLM_REGISTRATION_SECRET
# edit /tmp/hlm.env: HLM_DOMAIN, the four secrets, HLM_PROFILE + its API key, optional HLM_S3_*
scp /tmp/hlm.env deploy@<ipv4>:/opt/hlmemo/env/.env.prod
ssh deploy@<ipv4> chmod 600 /opt/hlmemo/env/.env.prod
shred -u /tmp/hlm.env 2>/dev/null || rm -P /tmp/hlm.env
```

Store the admin token and registration secret in your password manager; they are needed below.
`POSTGRES_PASSWORD` is only applied when the database volume is first created (see §11 to change it).

## 4. First deploy

```sh
deploy/scripts/deploy.sh --host deploy@<ipv4> main        # or: make deploy REF=main HOST=deploy@<ipv4>
```

`deploy.sh` (idempotent): clones/fetches the repo on the server (`HLM_DEPLOY_REPO`, default this
checkout's `origin`; the ref must be pushed), checks out the commit, refuses if the env file is not
0600 or still has `CHANGE_ME`, takes a pre-deploy backup when a db is running, builds
`hlmemo:<commit12>` (the first build downloads the embedding model, ~5 min), pins `HLM_IMAGE_TAG` in
the env file, runs `up -d --wait` (db -> migrate -> api `/ready` -> worker -> caddy), installs the
backup timer, and finally polls `https://$HLM_DOMAIN/ready` from your machine until 200 (first
deploy: certificate issuance, usually < 1 min).

Verify from your machine:

```sh
HLM_ENV_FILE=/path/to/private/copy/of/.env.prod HLM_SKIP_DOCKER=1 deploy/scripts/smoke_tls.sh
HLM_ENV_FILE=/path/to/private/copy/of/.env.prod deploy/scripts/smoke_mcp.sh   # creates project deploy-smoke + a ci device
```

(`smoke_mcp.sh` leaves a trusted `smoke-*` device behind; revoke it afterwards:
`hlm --server https://$HLM_DOMAIN/mcp --admin device revoke <name>`.)

## 5. Admin token bootstrap

Device 1 (`admin (reserved)`) is bound to `HLM_ADMIN_TOKEN` at **every** api start (§2); it has no
stored token and is used with `hlm --admin` from a shell that has the token:

```sh
export HLM_SERVER_URL=https://hlm.example.org/mcp
read -rs HLM_ADMIN_TOKEN && export HLM_ADMIN_TOKEN    # paste from the password manager
uv run hlm --admin project list                        # proves the binding (also: `hlm doctor` -> "admin bound")
uv run hlm --admin project create hlmemo --name "HLMemo"
```

Rotate: put a new value in `/opt/hlmemo/env/.env.prod`, then
`ssh deploy@<ip> 'cd /opt/hlmemo/src && docker compose -p hlmemo-prod -f deploy/compose.prod.yaml --env-file /opt/hlmemo/env/.env.prod up -d --wait api'`
(the restart re-binds device 1 and invalidates the old token and its cursors).

## 6. Register a client device and the three CLIs

On each client machine (repo checkout with `uv sync --frozen`):

```sh
export HLM_SERVER_URL=https://hlm.example.org/mcp
export HLM_REGISTRATION_SECRET=...          # from the password manager; sent as X-HLM-Registration-Secret
uv run hlm init --server "$HLM_SERVER_URL" --project hlmemo --device-name mbp-personal --instructions
uv run hlm device register --name mbp-personal --class personal --wait   # polls until approved
```

In a second shell with the admin token (§5):

```sh
uv run hlm --server https://hlm.example.org/mcp --admin device approve mbp-personal --class personal --grant hlmemo:write
```

Back on the client, register the MCP server with each CLI (the token lives in the keychain under
`mbp-personal@https://hlm.example.org`):

```sh
uv run hlm mcp add claude      # claude mcp add --transport http hlm https://hlm.example.org/mcp --header "Authorization: Bearer ..."
uv run hlm mcp add codex       # codex mcp add hlm --url .../mcp --bearer-token-env-var HLM_DEVICE_TOKEN (use `hlm codex`)
uv run hlm mcp add agy         # merges serverUrl + header into ~/.gemini/config/mcp_config.json
uv run hlm doctor              # server /health, device trusted, CLI versions
uv run hlm claude --task "session start"   # preflight memory.query over TLS, then launches claude
```

If a client was registered against the local dev stack before, it needs a new registration for the
remote server (the keychain key includes the server URL). Remote G7: `HLM_SERVER_URL=https://.../mcp
HLM_DEVICE_TOKEN=... make smoke`.

## 7. Backups

- **Schedule:** `hlmemo-backup.timer` (installed by `deploy.sh`) runs `backup.sh` daily at 03:15 UTC
  as `deploy`. Check: `systemctl list-timers hlmemo-backup.timer`, `journalctl -u hlmemo-backup`.
  Manual install without sudo in deploy.sh:
  `sudo install -m644 deploy/backup/hlmemo-backup.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now hlmemo-backup.timer`
  (units assume the user `deploy`; edit `User=`/`Group=` if you changed `deploy_user`).
  Cron alternative: `15 3 * * * HLM_ENV_FILE=/opt/hlmemo/env/.env.prod /opt/hlmemo/src/deploy/backup/backup.sh >>/var/backups/hlmemo/backup.log 2>&1`.
- **What:** `pg_dump --format=custom` of database `hlm` via `docker compose exec` (no host port),
  verified with `pg_restore --list`, written atomically with a `.sha256` sidecar to
  `/var/backups/hlmemo/daily/`; promoted to `weekly/` when the newest weekly dump is > 6 days old.
  Retention 7 daily + 4 weekly (`HLM_BACKUP_KEEP_*`).
- **Off-site:** set `HLM_S3_BUCKET`, `HLM_S3_ENDPOINT` (Hetzner: `https://fsn1.your-objectstorage.com`),
  `HLM_S3_REGION`, `HLM_S3_ACCESS_KEY_ID`, `HLM_S3_SECRET_ACCESS_KEY`. The directory is mirrored with
  `aws s3 sync --delete` (pinned `amazon/aws-cli` image, credentials passed by env name). Turn on
  bucket versioning + a lifecycle rule for noncurrent versions so a mirrored deletion is recoverable.
- **Hetzner server backups** (`enable_backups`) are an additional crash-consistent disk image; the
  dumps are the primary, portable backup.

### Restore

```sh
ssh deploy@<ip>
cd /opt/hlmemo/src
export HLM_ENV_FILE=/opt/hlmemo/env/.env.prod
ls -1 /var/backups/hlmemo/daily/ /var/backups/hlmemo/weekly/
deploy/backup/restore.sh /var/backups/hlmemo/daily/hlmemo-<ts>.dump          # asks: type RESTORE
```

It verifies sha256 + `pg_restore --list`, stops api + worker, restores into a new database
`hlm_restore_<ts>` in one transaction (on failure: dropped, services restarted, live data untouched),
swaps it in (`hlm` -> `hlm_pre_restore_<ts>`), runs migrate and waits for `/ready`. The old database is
kept until you drop it (command printed) or pass `--drop-old`. From an off-site copy: download the dump
and its `.sha256` next to each other first (`aws s3 cp s3://<bucket>/hlmemo/daily/<file> .`).

New host (disaster recovery): provision (§1), env file (§3, **same** secrets), deploy (§4), then
restore the latest dump. Device tokens live in the database, so clients keep working once DNS points
at the new host.

Drill: `deploy/scripts/drill_backup_restore.sh` (write -> backup -> wipe -> restore -> compare). It
refuses to run against a stack with `HLM_TLS_MODE=acme` unless `HLM_DRILL_FORCE=1` — run it on a
scratch compose project, never on the live one.

## 8. Upgrade

```sh
git push origin main                                   # or a tag: v0.2.0
deploy/scripts/deploy.sh --host deploy@<ip> v0.2.0
```

Pre-deploy backup -> build -> `alembic upgrade phase0@head` -> rolling `up -d --wait`. Brief api
restart (~20-40 s of 502/503 at the edge). Postgres or Caddy image bumps are just commits that change
the pin in `compose.prod.yaml`; a Postgres **major** upgrade needs dump + restore into the new major.

## 9. Rollback

- **Code only (no schema change between the two refs):** `deploy/scripts/deploy.sh --host ... <previous-ref>`.
  The old image `hlmemo:<commit12>` is usually still cached, so it is fast. History:
  `/opt/hlmemo/deploy-history.log`.
- **Schema changed:** migrations are forward-only (`alembic downgrade` is not part of the tooling).
  Restore the pre-deploy dump taken by the failed deploy (newest file in `daily/` from before the
  deploy), then deploy the previous ref:
  `deploy/backup/restore.sh --yes <pre-deploy dump> && deploy/scripts/deploy.sh --host ... --no-backup <previous-ref>`.
  Writes made after the deploy are lost; say so to users.

## 10. TLS issuance fails

Symptoms: `deploy.sh` times out on `https://$HLM_DOMAIN/ready`, `curl -v https://$HLM_DOMAIN/health`
shows a TLS alert, or `docker compose ... logs caddy` shows `obtaining certificate` errors.

1. DNS: `dig +short $HLM_DOMAIN A` / `AAAA` must return **this** server. A stale AAAA record is a
   common cause (Let's Encrypt prefers IPv6). Fix DNS, then `docker compose ... restart caddy`.
2. Reachability: 80 and 443 open from the internet (`terraform output`, Hetzner firewall attached),
   nothing else bound to them: `sudo ss -ltnp '( sport = :80 or sport = :443 )'`.
3. Rate limits: Let's Encrypt allows 5 duplicate certificates per week. Never delete the `caddy_data`
   volume (it holds certificates and the ACME account). Caddy falls back to ZeroSSL automatically
   and retries with backoff; `logs caddy` shows which issuer failed and why.
4. **No/unsuitable domain -> sslip.io fallback:** set `HLM_DOMAIN=hlm.<ipv4-with-dashes>.sslip.io`
   (e.g. `hlm.203-0-113-7.sslip.io`, Terraform output `sslip_domain`) in the env file and
   `docker compose -p hlmemo-prod -f deploy/compose.prod.yaml --env-file /opt/hlmemo/env/.env.prod up -d caddy`.
   Clients must then use that URL (re-run `hlm mcp add ...` with the new `--server`).
5. Last resort (keeps the service usable while you fix DNS): `HLM_TLS_MODE=internal` serves Caddy's
   own CA. Clients must trust its root
   (`docker compose ... exec caddy cat /data/caddy/pki/authorities/local/root.crt`) — not suitable
   for the coding CLIs long term. Switch back to `acme` as soon as issuance works.

## 11. Operations

| task | command (on the server, in `/opt/hlmemo/src`, `C="docker compose -p hlmemo-prod -f deploy/compose.prod.yaml --env-file /opt/hlmemo/env/.env.prod"`) |
|---|---|
| status | `$C ps` |
| logs | `$C logs -f --tail 100 api worker caddy` (json-file, 5 x 10 MB per container) |
| worker health | `$C logs worker \| grep 'worker heartbeat'` (stalled = backlog ages while `last_done_job` stands still) |
| psql | `$C exec db psql -U hlm -d hlm` |
| restart api | `$C up -d --wait api` |
| change DB password | `$C exec db psql -U hlm -d postgres -c "ALTER ROLE hlm PASSWORD '<new>'"`, update `POSTGRES_PASSWORD` in the env file, `$C up -d --wait` |
| stop everything | `$C down` (volumes kept; `down -v` DESTROYS the database and certificates) |

Resource limits (CX43): db 4 CPU / 6 GB (shared_buffers 2 GB), api 3 / 3 GB, worker 3 / 3 GB, caddy 1 / 512 MB,
migrate 1 / 1 GB. Postgres is never published: it sits only on the `internal` backend network.
