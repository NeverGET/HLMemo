# Production operations

This directory implements DP1 (D-032). No cloud resource is needed to validate it. Real configuration
belongs outside this checkout (D-018); the public templates contain placeholders. Commands below
are operator instructions, not evidence that a VPS has been provisioned. Never use the development
Compose project `hlmemo` for these scripts. Requirements: Docker Engine + Compose **2.24+**, Bash,
Python 3, curl, `flock` (Ubuntu util-linux; macOS `brew install flock`); Terraform **1.6+** only for optional Hetzner provisioning; AWS CLI only for optional uploads.

## Provision any Ubuntu 24.04 VPS

The launch target (D-042) is **2 vCPU / 8 GB RAM**, with local e5-small in both API and worker
(e.g. Hostinger KVM 2 or OVH VPS-2). Use any provider's fresh Ubuntu 24.04 image. This script
creates no cloud resources; obtain the host separately and verify its SSH host-key fingerprint
through the provider console. Keep that console and the original SSH session available until
another deploy-user login succeeds. The script supports SSH port 22 and requires root/sudo.

```sh
# Operator workstation: public key only; --dry-run makes no changes.
bash deploy/bootstrap.sh --ssh-key ~/.ssh/id_ed25519.pub --dry-run
scp -o StrictHostKeyChecking=yes deploy/bootstrap.sh ~/.ssh/id_ed25519.pub root@SERVER:/tmp/
ssh -o StrictHostKeyChecking=yes root@SERVER \
  'bash /tmp/bootstrap.sh --ssh-key /tmp/id_ed25519.pub'
# Optional restricted SSH ingress: append --admin-cidr YOUR_ADMIN_IP/32 (or IPv6 /128).
# The CIDR must include this SSH session's actual source. Existing unrelated UFW rules remain.

# A NEW session explicitly proves deploy-user key authentication before hardening:
ssh -o StrictHostKeyChecking=yes -o PasswordAuthentication=no \
  -o KbdInteractiveAuthentication=no hlmdeploy@SERVER \
  'sudo --preserve-env=SSH_CONNECTION,SSH_USER_AUTH bash /tmp/bootstrap.sh --finalize-ssh'
# Keep that session open; verify another login and services before disconnecting the original.
ssh -o StrictHostKeyChecking=yes hlmdeploy@SERVER \
  'docker compose version && systemctl is-active docker fail2ban unattended-upgrades'
```

For an image with an existing sudo user, copy to that user's `/tmp` paths and run preparation
with `sudo --preserve-env=SSH_CONNECTION bash /tmp/bootstrap.sh ...`. Use `--deploy-user NAME`
in both phases to change the default `hlmdeploy`. Preparation installs Docker Engine and Compose
from [Docker's signed apt repository](https://docs.docker.com/engine/install/ubuntu/), fail2ban,
unattended upgrades (no automatic reboot), and UFW TCP 22/80/443 plus UDP 443. It creates
`/opt/hlmemo`, `/var/backups/hlmemo`, `/etc/hlmemo` owned by the deploy user, mode 0750.
Docker-group and passwordless-sudo membership grant administrative privilege.

Preparation preserves existing root/password access. `--finalize-ssh` requires an authenticated
public-key SSH session as the deploy user (OpenSSH `ExposeAuthInfo`), validates effective sshd
configuration, then disables root/password login; a configuration/reload failure restores the
prior drop-in. Re-running preparation appends no duplicate key, retains completed hardening,
and keeps installed Docker versions. After `--finalize-ssh` root login is disabled, so re-run it as
the deploy user: `sudo --preserve-env=SSH_CONNECTION bash /tmp/bootstrap.sh --ssh-key /tmp/KEY.pub`
(copy both files to `/tmp` again first; `/tmp` does not survive a reboot). Patch/upgrade Docker deliberately in a maintenance window.
`--prepare-only` skips service/firewall activation for non-systemd container tests; it does **not**
produce a ready VPS. Docker publishes ports outside ordinary UFW filtering, so never publish
API/DB ports; Compose publishes only Caddy. See the optional Terraform path below for managed
provider firewall rules, then continue with configuration and first deployment.

## Configuration and local gate

Keep these private files together with mode 0600; each has exactly one example in `deploy/`:

| Private file | Example | Consumer |
|---|---|---|
| `prod.env` | `.env.prod.example` | Compose interpolation (domain, images, local ports) |
| `app.env` | `app.env.example` | API, worker and migration: database DSN/provider settings |
| `api.env` | `api.env.example` | API only: admin token, registration and cursor secrets |
| `db.env` | `db.env.example` | DB only: PostgreSQL variables |
| `backup.env` | `backup.env.example` | Host backup/upload only: S3 credentials and retention |

Replace the four `CHANGE_ME_*` values with independent `openssl rand -hex 32` outputs;
use the **same database password** in `db.env` and `app.env`'s DSN. Set a real domain and
`HLM_TLS_MODE=acme` in `prod.env`. Keep secrets out of `prod.env` and keep backup credentials out
of every service file. Env files use Compose dotenv syntax, never shell `source`.
Docker administrators can inspect container environments; Docker membership is privileged.
`HLM_ENV_FILE` selects `prod.env`; scripts resolve the other four files alongside it. Explicit
`HLM_APP_ENV_FILE`, `HLM_API_ENV_FILE`, `HLM_DB_ENV_FILE`, `HLM_BACKUP_ENV_FILE` overrides select
other absolute paths. All five files must exist, even when S3 upload is disabled; `backup.env`
may be empty or contain only comments. The backup directory defaults to `/var/backups/hlmemo`.
An explicit directory inside the repository (including a symlink resolving there) is rejected.

For local validation, use a private file directory such as `/private/tmp/hlmemo-local/`; copy all five templates there and set in `prod.env` and set:

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
export HLM_ENV_FILE=/private/tmp/hlmemo-local/prod.env
make deploy-up DEPLOY_ENV="$HLM_ENV_FILE"
curl -sk https://localhost:18443/ready
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost:18443/anything-else
bash deploy/scripts/smoke_tls.sh
bash deploy/scripts/smoke_mcp.sh
bash deploy/scripts/smoke_mcp.sh # same deploy-smoke project; device revoked after each run
bash deploy/scripts/smoke_edge.sh # 5 MB through Caddy + actual TLS client IP in API log
HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_backup_restore.sh
HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_deploy_backup.sh
bash deploy/scripts/stack.sh down -v   # disposable LOCAL bake data only
```

`-k` is only for this local internal CA test. The probes skip verification only for internal TLS on
loopback; for other custom CAs set `HLM_CA_FILE=/path/to/ca.pem`. Caddy stores its CA in its `caddy_data`
volume. It is not installed into the Mac trust store. HTTP redirect targets normal port 443;
with remapped local ports use the explicit HTTPS URL. A raw Compose invocation selects the service files explicitly:

```sh
HLM_APP_ENV_FILE="$PWD/deploy/app.env.example" \
HLM_API_ENV_FILE="$PWD/deploy/api.env.example" \
HLM_DB_ENV_FILE="$PWD/deploy/db.env.example" \
docker compose -f deploy/compose.prod.yaml --env-file deploy/.env.prod.example config -q
```

That command is the example-only G-D1; replace paths with private files for actual operation.
Scripts supply these paths automatically. Missing service files fail closed.

`api`, `db` and `worker` have no published host ports. Limits for the **2 vCPU / 8 GB** host:

| Service | Memory ceiling | CPU ceiling |
|---|---:|---:|
| PostgreSQL | 2 GiB | 1 |
| API (local e5-small, including tmpfs) | 2.5 GiB (2560 MiB) | 1 |
| Worker (local e5-small) | 1.5 GiB | 1 |
| Migration (one-shot) | 768 MiB | 1 |
| Caddy | 256 MiB | 0.5 |

Steady ceilings total **6.25 GiB** (`2 + 2.5 + 1.5 + 0.25`); adding the one-shot migration's
0.75 GiB gives a conservative **7 GiB** combined ceiling. An 8 GiB host retains 1.75 GiB
steady / 1 GiB combined headroom; a decimal 8 GB host retains about **1.20 GiB steady /
0.45 GiB combined**. Migration completes before API/worker startup. Limits are not reservations;
CPU limits share the two physical vCPUs. PostgreSQL uses `shared_buffers=512MB`, `work_mem=4MB`,
`maintenance_work_mem=128MB`, `max_connections=50`, and 512 MiB shared memory. `work_mem` applies
per sort/hash operation, not once per connection; avoid increasing concurrency without measuring.
The example API/worker pools each max at 8 connections. Monitor container RSS, OOM events and
query latency on the actual VPS; these limits are capacity planning, not a production load test.
Migration is intentionally one-shot, `restart: no`, and must exit zero.

API sizing evidence (2026-09-23, local Docker arm64, one CPU, 1536 MiB cgroup with swap disabled):
the isolated `oom-check` smoke built this worktree's runtime image, mounted pinned models
read-only, passed `/ready` and 20 real MCP `memory.query` calls, and finished with
`OOMKilled=false`, `RestartCount=0`. **Docker stats sampled peak: 956.2 MiB** (5 samples);
**cgroup `memory.peak`: 1536 MiB**. Docker stats excludes inactive file cache and can miss short
spikes, so sizing uses the larger cgroup high-water mark, which includes startup/cache pressure
under the test limit. This is not an unconstrained peak or a maximum-concurrency load test.

The API uses one ONNX session shared by readiness and every query. Missing model files leave
the API running with `/ready` returning 503 `not_ready` and the missing file list; after the
files are restored, the next readiness probe initializes the shared session once. Both API and
worker disable the CPU memory arena and use `HLM_EMBED_INTRA_OP_NUM_THREADS=2` by default.
`ORT_DISABLE_TELEMETRY=1` (runtime image `ENV`, Compose app environment, embedder module) is set before ONNX Runtime initializes: on macOS its native
telemetry HTTP callback was observed locking a destroyed mutex during interpreter exit.
The API owns a single native executor, drains/cancels the readiness task on shutdown,
joins the executor and releases its session/tokenizer before the event loop closes.
The worker drains active inference before releasing its native session as well.

Worker limits are configurable: `HLM_WORKER_BATCH_CHUNKS=32` bounds each SQL page;
`HLM_EMBED_MAX_BATCH_TOKENS=1024` bounds **padded** tokens (`texts × longest text`,
including prefixes/special tokens), not just the number of texts. It permits two
full-length 512-token texts per inference. `HLM_WORKER_MAX_JOBS_PER_BATCH=1` avoids
leasing jobs that wait behind a large job. Pages are inserted into one uncommitted
transaction per version; the final lease fence commits every page together or rolls
them all back. Texts and vectors are released before fetching the next page. Writes
already create one job per version; a 50-item write therefore creates 50 bounded jobs.
`HLM_WORKER_MEMORY_PROFILE=1` enables RSS/tracemalloc stage logs for diagnosis (off
by default; profiling adds overhead). Increasing batch limits requires repeating the
1536 MiB worker test; the earlier API-query smoke does not validate worker sizing.
In production Compose, set the three batch limits in `prod.env` (Compose
interpolation); set the optional profiling flag in the worker's app environment.

Run the contract-max worker test separately from tests that reset `hlm_exit`:

```sh
HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_exit \
uv run --frozen python tests/deploy/worker_oom_smoke.py \
  --image hlmemo:worker-oom --build --worker-log tests/auth-worker-memory.log
```

The harness creates only Compose project `oom-worker`, binds an ephemeral loopback
port, writes 50 × 64,000 emoji characters (38.4 MB escaped JSON), checks all returned
versions' embeddings, reports memory/OOM/restarts, and verifies API SIGTERM exit 0.
It removes its own containers/network and preserves database rows and the log. The worker runs
with production defaults; add `--profile` to enable `HLM_WORKER_MEMORY_PROFILE` stage logs.

`HLM_REQUEST_SPOOL_DIR=/var/spool/hlmemo` is backed by a **320 MiB tmpfs** owned by UID/GID 10001;
`/tmp` retains its separate 64 MiB tmpfs. Both tmpfs allocations count against the API cgroup.
Required budget: `(1536 + 320) × 1.25 = 2320 MiB`; also reserving all of `/tmp` gives
`(1536 + 320 + 64) × 1.25 = 2400 MiB`, rounded up to **2560 MiB**. Do not add these tmpfs
ceilings again to the host table: they are already included in the API limit.

Repeat the isolated memory smoke against an already migrated disposable `hlm_body` database
(never run concurrently with tests using that DB):

```sh
HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_body \
python3 tests/deploy/oom_smoke.py --image hlmemo:oom-check --build \
  --models-dir "$HLM_MODELS_DIR"
```

Set `HLM_MODELS_DIR` to the local directory containing the pinned model first. Omit
`--models-dir` to bake models instead. The script binds a fresh test admin token, adds a uniquely
named test project, publishes only an ephemeral loopback port, reports stats and inspect results,
and removes its `oom-check` API container in `finally`; it does not alter an existing stack.

Protected requests authenticate before body receipt using one unlocked device SELECT with a
250 ms statement timeout and at most 250 ms normal-pool wait. The connection is returned before
reading bytes; the authoritative transaction resolves the bearer again after receipt. Unknown
or revoked tokens return 401, pending devices 403, and pool pressure retryable 503. Only
gate-verified trusted devices get 64 MiB and rate-based upload time; registration stays at
16 KiB, and public routes/reserved-admin bypass requests at 64 KiB with a fixed base deadline.
The 16 per-client slots cover authentication and body receipt. Admin-token requests on reserved
routes retain separate pool capacity and 16 separate body slots per client, sharing byte budgets;
normal auth waiters cannot obstruct that path even from the same IP. Caddy upstream transport uses `keepalive 4s`, below the
API's 5-second idle timeout, to prevent reuse of stale connections for POST requests.

Logs rotate at 5 × 10 MiB per container. Worker health checks observe successful poll-loop log
heartbeats; missing heartbeats for 180 seconds fail health. Docker does not automatically restart
an unhealthy but running process: alert on unhealthy state and aging job backlog. Caddy's container
probe checks its local admin listener; the external `/ready` probe verifies end-to-end service.

## Alternative: Terraform provisioning on Hetzner (incurs cost)

Terraform remains optional. Confirm server size (at least the D-042 8 GB target), price and location before provisioning. No `terraform apply` is part
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
CIDRs, TCP 80/443 and UDP 443 (HTTP/3) globally over IPv4/IPv6. No other inbound rules; outbound remains allowed.
Host snapshots default off and do not replace logical dumps. Cloud-init installs the official
Docker repository, Engine and Compose plugin, key-only SSH, fail2ban and unattended upgrades.
Automatic reboots are disabled; schedule maintenance for pending kernel/security reboots.
If SSH configuration validation fails, provisioning removes its `00-hlmemo.conf` drop-in before
continuing, so a later socket activation cannot load that rejected file. Investigate the warning
through the provider console; cloud-init's `ssh_pwauth: false` remains in effect.

Verify the SSH host key fingerprint through the provider console before adding it to known_hosts;
the deploy script requires `StrictHostKeyChecking=yes`. Then:

```sh
ssh hlmdeploy@SERVER 'sudo cloud-init status --wait && docker compose version'
ssh hlmdeploy@SERVER 'sudo systemctl is-active docker fail2ban unattended-upgrades'
```

Create DNS A → output IPv4 and AAAA → output IPv6 after verifying routing, external IPv6 HTTPS reachability (`curl -6 https://YOUR_DOMAIN/ready`), and the source-IP check below. Production port declarations omit the host IP so Docker can publish both families. Local tests bind only 127.0.0.1. UDP 443 exposes Caddy HTTP/3. No DNS provider is
hard-coded. Cloud-init creates `/etc/hlmemo`, `/opt/hlmemo` and `/var/backups/hlmemo`. Docker group
and passwordless sudo membership make `hlmdeploy` a privileged operator despite being non-root.

### IPv6 client addresses and request limits

The shipped Compose bridges are IPv4-only. Docker's default userland proxy can translate incoming
IPv6 connections to IPv4, making Caddy see the bridge gateway; registration limits and the 16 concurrent auth/body slots then share one
bucket across IPv6 clients. Slow bodies can therefore deny other IPv6 clients admission. Publishing `::` alone does not fix this. Before advertising AAAA,
configure native IPv6 on the production Linux host. This procedure requires Docker Engine 27+
and a maintenance window; it has not been validated against a live VPS. See Docker's
[port publishing behavior](https://docs.docker.com/engine/network/port-publishing/),
[IPv6 networking](https://docs.docker.com/engine/daemon/ipv6/) and
[Engine 27 network defaults](https://docs.docker.com/engine/release-notes/27/).

Merge these exact keys into `/etc/docker/daemon.json`, preserving unrelated settings:

```json
{
  "ip6tables": true,
  "userland-proxy": false,
  "default-network-opts": {
    "bridge": { "com.docker.network.enable_ipv6": "true" }
  }
}
```

This daemon default gives **new** user-defined bridges native IPv6 with automatically allocated
ULA subnets. It also applies to other new bridge networks on this host. The equivalent Compose
network setting is `enable_ipv6: true` on both `frontend` and `outbound`; daemon configuration
keeps the deployment checkout unchanged across upgrades. Enabling only the default `docker0`
bridge with `"ipv6": true` does not configure these Compose networks.

For an existing production stack, back up first, stop containers and remove their old networks
without deleting volumes, then restart Docker and recreate the stack:

```sh
cd /opt/hlmemo/app
export HLM_ENV_FILE=/etc/hlmemo/prod.env
bash deploy/backup/backup.sh
sudo dockerd --validate --config-file=/etc/docker/daemon.json
bash deploy/scripts/stack.sh down       # NEVER add -v on a production host
sudo systemctl restart docker
bash deploy/scripts/stack.sh up -d --wait
docker network inspect hlmemo-prod_frontend hlmemo-prod_outbound --format '{{.Name}} IPv6={{.EnableIPv6}}'
```

Adjust network names if `HLM_COMPOSE_PROJECT` differs. Both must report `IPv6=true`. Verify host
IPv6 routing and Docker's IPv6 firewall rules, then issue `curl -6 https://YOUR_DOMAIN/ready`
from two external IPv6 clients. With host `tcpdump` installed, inspect addresses **inside Caddy's
network namespace**, not merely the host's public interface:

```sh
container=$(bash deploy/scripts/stack.sh ps -q caddy)
pid=$(docker inspect --format '{{.State.Pid}}' "$container")
sudo nsenter -t "$pid" -n tcpdump -n -i any 'ip6 and (tcp dst port 443 or udp dst port 443)'
```

The two observed sources must match the clients' distinct public IPv6 addresses. Caddy forwards
these to the API; do not trust client-supplied forwarding headers as a workaround. Until this is
verified, withhold AAAA and treat IPv6 registration and auth/body admission as shared buckets.

## First deploy and admin bootstrap

Install all five completed private env files, with `HLM_BACKUP_DIR=/var/backups/hlmemo` in `backup.env`:

```sh
scp /secure/path/{prod,app,api,db,backup}.env hlmdeploy@SERVER:/opt/hlmemo/
ssh hlmdeploy@SERVER 'for file in prod app api db backup; do sudo install -o hlmdeploy -g hlmdeploy -m 0600 "/opt/hlmemo/$file.env" "/etc/hlmemo/$file.env" && rm "/opt/hlmemo/$file.env"; done'
bash deploy/scripts/deploy.sh hlmdeploy@SERVER RELEASE_REF
```

The deploy user must own the env directory as well as `prod.env`: successful releases replace
that file atomically. Cloud-init configures this for new hosts. On an existing host created by
older tooling, run `sudo chown hlmdeploy:hlmdeploy /etc/hlmemo` and
`sudo chmod 0750 /etc/hlmemo` once (substitute your configured deploy user).

`RELEASE_REF` must include the deploy tooling in the remote repository. An optional third argument
selects another repository URL; private repositories need read credentials installed separately on
the server. Do not embed credentials into the URL. Defaults: `/opt/hlmemo/app`, `/etc/hlmemo/prod.env`;
override with `HLM_REMOTE_DIR` / `HLM_REMOTE_ENV`. The script fetches the requested ref, resolves an
immutable commit, builds including model assets, takes a snapshot-consistent pre-upgrade dump while
the DB and writers are live, then stops writers and runs `alembic upgrade phase0@head`, starts with
`up -d --wait`, verifies API readiness and local Caddy routing, then checks public HTTPS readiness
with certificate validation. The runner itself is extracted on the server with `git show` from
that same fetched commit. A ref without `deploy/scripts/remote-deploy.sh`, or without the exact supported
`HLM_RUNNER_PROTOCOL=3` marker, is rejected before checkout, build, backup or restore. Older and
unknown protocols are refused; the bootstrap retains its deployment lock across runner handoff. A first uncached model build can take several minutes.
Application images use `repository:<commit-sha>`; each release build carries `org.opencontainers.image.revision=<target-sha>`.
An existing image is reused only if that label matches the target. The label is checked again
before migration and application startup. Missing/mismatched labels abort instead of overwriting
an immutable tag; investigate/remove a known corrupt unused target image explicitly before retrying.
Legacy unlabeled running images remain recoverable by captured image ID, but cannot be reused as
new deployment targets. Local unversioned test builds carry `local`; set `HLM_IMAGE_REVISION`
to the source SHA for deliberate release builds. Before an upgrade, the actual running baseline is frozen under its previous
SHA (API and worker must agree). `prod.env` retains this previous `HLM_IMAGE` until internal
readiness succeeds, when it is atomically switched to the new image. Failed builds/upgrades
therefore leave later `restore.sh` and `stack.sh up` on the previous release, including Alembic.
Do not override `HLM_IMAGE` in your shell when restoring, or rebuild/retag a published SHA.
Repeated deployment of the same ref is supported. This is a single-node maintenance-window deploy,
not zero downtime. Internal failures after writers stop trigger recovery of the previous pinned images and, if migration began,
the recorded pre-upgrade database snapshot. Inspect recovery output and verify readiness. A failed
first deployment has no previous stack to restore. An external-only readiness failure leaves the
new, internally healthy stack running and returns nonzero: fix DNS, A/AAAA routing, firewall or
ACME issuance before retrying the public check. It does not restore an older database over new writes.

The remote script runs from a file, detached from SSH, with stdin closed and output redirected to
`/opt/hlmemo/.deploy-runs/<run-id>/log`. The client prints the run directory and follows that log;
its disconnect does not cancel a migration or recovery. The remote `status` file is written after
completion and contains its exit code. With a custom `HLM_REMOTE_DIR`, `.deploy-runs` is in that
checkout's parent directory. Reconnect to inspect the reported run rather than launching another
deployment while the first is active:

```sh
ssh hlmdeploy@SERVER 'tail -n 100 /opt/hlmemo/.deploy-runs/RUN_ID/log'
ssh hlmdeploy@SERVER 'cat /opt/hlmemo/.deploy-runs/RUN_ID/status'
ssh hlmdeploy@SERVER 'cat /opt/hlmemo/.deploy-runs/RUN_ID/pid /opt/hlmemo/.deploy-runs/RUN_ID/heartbeat'
```

A missing `status` means no completion has been recorded. The observer detects a missing/dead
runner PID (including SIGKILL/OOM), reports the log/heartbeat paths and exits nonzero.
`heartbeat` records a timestamp every second while the runner process is alive; it is a liveness
signal, not proof that a migration is progressing. Observation has an overall 30-minute deadline,
including SSH calls; set `HLM_DEPLOY_TIMEOUT_SECONDS` to another positive integer if needed.
Timeout/disconnect does not kill remote work. Investigate the PID and status before retrying.
The workstation observer requires Python 3; remote preflight checks `nohup`, `setsid`, `bash`,
`git`, `flock` and `ps` individually before launching work.
Keep run directories private (0700). Secret-bearing rollback Compose files are removed
on script exit; an SSH disconnect cannot interrupt this cleanup. A host power loss or SIGKILL
cannot execute shell traps: inspect/remove stale `.rollback-compose.*` files during host recovery.

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
Rotating admin token needs editing `api.env` and recreating API; device tokens are rotated by
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
atomically publishes it. Daily names include a random suffix, so same-second runs have different
filenames and remote object keys. Local retention keeps the latest snapshot on each of 7 distinct UTC days
and the latest snapshot in each of 4 distinct ISO weeks. Missed days cannot be reconstructed.
For S3-compatible upload install the AWS CLI, set `S3_BUCKET`, `S3_PREFIX`, `S3_ENDPOINT_URL` and
AWS credential/region fields only in `backup.env`. Use least-privilege bucket access and server-side
encryption/versioning. Upload failure exits nonzero while retaining the local dump; monitor the
service. Deploy-time uploads warn without failing deployment; the local pre-upgrade dump survives.
Configure bucket lifecycle independently: local retention does not delete remote objects.

```sh
# Restore ONLY trusted dumps, during an announced maintenance window:
bash deploy/backup/restore.sh /var/backups/hlmemo/daily/SELECTED.dump --yes
curl --fail https://YOUR_DOMAIN/ready
bash deploy/scripts/smoke_mcp.sh
```

Restore validates the dump before stopping Caddy/API/worker, saves and validates a safety dump,
recreates the configured database and restores in one transaction, then runs the selected checkout's
migration before starting API/worker/Caddy. Older dumps are therefore upgraded to that code's schema.
Migration must succeed before writers restart. All writers stay stopped on
failure; use the reported safety dump to recover. Safety dumps are not auto-pruned: remove them
only after confirming recovery. Do not restore a dump with untrusted SQL. Backup and restore share
an fd-based `flock` lock (`.operation.flock`), released automatically on process exit or reboot.
A leftover lock file is harmless; never unlink it during an operation. Legacy `.operation.lock`
directories are ignored. Preserve a copy off-host; server loss destroys local volumes and dumps.
Caddy certificate/account volumes and the private env files are separate from database backups;
back them up encrypted (or reissue certificates with attention to CA rate limits).

The destructive `drill_backup_restore.sh` additionally requires a `bake-*` project, local Unix Docker
socket, loopback HTTPS and `HLM_ALLOW_DESTRUCTIVE_DRILL=1`. It writes a unique payload through MCP,
backs up, drops/recreates the database, proves zero public tables, restores and reads the identical
payload through MCP with the restored device bearer. It is intended for disposable local stacks.

## Upgrade and rollback

### Staged Compose upgrade

Automatic deployment requires `deploy/compose.prod.yaml` to be byte-identical between the previous
and requested release. A change to commands, healthchecks, mounts or any other Compose model field
is rejected **before build, backup or writer shutdown**. There is no bypass flag: automatic
recovery must never run previous images with a different release's Compose model. The supported
guard intentionally also rejects formatting-only changes.

For an intentional Compose change, stage and validate the target in a disposable local stack
first. Schedule a maintenance window, retain the previous checkout, rendered Compose model,
immutable images and verified dump, and stop writers with the previous model. Switch checkout
and configuration together, build a new immutable image, migrate and validate internal readiness
before publishing its image/ref. On failure restore the previous checkout/model and dump together.
Treat PostgreSQL major-version or volume-layout changes as a separate migration. This manual
procedure needs a release-specific plan; `deploy.sh` does not automate it.

### Application releases

Before an upgrade, verify recent off-host backup and disk headroom. Deploy a reviewed immutable
release ref using `deploy.sh`. `/opt/hlmemo/current-ref` names the last successful deployment;
`previous-ref` and `previous-dump` record the exact rollback pair. Every deploy snapshot lives at
`$HLM_BACKUP_DIR/pre-upgrade/<previous-sha>-<UTC-stamp>-<unique>.dump`, outside daily/weekly rotation.
Two deployments on the same day create separate snapshots. After a successful deployment,
retention keeps the last `HLM_PRE_UPGRADE_KEEP` snapshots (default 5, set in `backup.env`),
including the recorded rollback dump. Failed/recovered deployments preserve the prior
`previous-ref`/`previous-dump` pair and do not prune. Manual pruning remains available:

```sh
# Inspect rollback markers and preserve any required older snapshots first.
bash deploy/backup/backup.sh --prune-pre-upgrade
```

Daily backup rotation does not touch pre-upgrade snapshots. Before manual pruning, verify the
recorded rollback dump is among those kept. Keep old images and refs too. Do not change major PostgreSQL versions by simply
changing the image; plan a dump/restore or pg_upgrade.

For a manual rollback, copy the matching markers before changing the checkout. On the server:

```sh
cd /opt/hlmemo/app
export HLM_ENV_FILE=/etc/hlmemo/prod.env
previous=$(cat /opt/hlmemo/previous-ref)
dump=$(cat /opt/hlmemo/previous-dump)
test -f "$dump"
helper=$(mktemp /opt/hlmemo/release-env.XXXXXX.py)
cp deploy/scripts/release_env.py "$helper"
bash deploy/scripts/stack.sh stop caddy api worker
# Save the failed/new database before replacing it:
bash deploy/backup/backup.sh
git checkout --detach "$previous"
# Select the retained image from the same repository, never rebuild a release tag.
old_image="hlmemo:$previous" # substitute your configured image repository
docker image inspect "$old_image" >/dev/null
python3 "$helper" "$HLM_ENV_FILE" "$old_image"
rm "$helper"
bash deploy/backup/restore.sh "$dump" --yes
curl --fail https://YOUR_DOMAIN/ready
bash deploy/scripts/smoke_mcp.sh
printf '%s\n' "$previous" > /opt/hlmemo/current-ref
```

Use a ref supporting the split env layout; for older tooling retain its compatible private config.
Restore migrates to the selected checkout's head before starting its services with `--no-deps`;
select the matching old code **before** restoring for a rollback. Automatic deployment recovery
pins the old running image IDs and skips
migration entirely. Restoring the pre-upgrade dump discards writes after its snapshot, including
writes between the live snapshot and writer shutdown. The in-progress dump is captured before downtime;
the rollback markers are promoted only after successful internal deployment checks.
Do not run blind `alembic downgrade`. This package has no point-in-time recovery/WAL archive.

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
It caps `/mcp` bodies at **64 MiB** (D-037 transport bound), and `/devices/*`, `/admin/*`
and health routes at **64 KiB**, sets security headers, and flushes MCP responses immediately. Existing
Phase 0 MCP responds with stateless JSON; Caddy also preserves streaming/SSE without buffering.
The frontend network is pinned to `172.30.39.0/24`; `api.env` sets
`HLM_TRUSTED_PROXY_IPS=172.30.39.0/24`. Caddy resolves `api-frontend`, an alias registered only on
that network, so its upstream cannot accidentally use the shared outbound bridge.
[Caddy sets X-Forwarded-For by default and ignores untrusted incoming forwarding headers](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#headers).
Keep API unpublished and do not attach untrusted containers to frontend. Multiple copies on one
Docker host cannot share this pinned subnet; adjust both subnet and API trust together if needed.
The application release must implement `HLM_TRUSTED_PROXY_IPS` (D-039); configuration alone cannot
add proxy trust to older code. Validate a TLS request's API client-IP log against the actual client
before enabling public registration. On native-IPv6 frontend networks, pin/trust their IPv6 subnet
as well, or retain IPv4 upstream routing; an automatically chosen IPv6 ULA is not trusted by this
IPv4-only setting. Registration uses a separate secret
and device approval; admin routes still require app authorization. Provider/model choices remain in
profiles/env configuration; current baked Phase 0 embedding assets are pinned by `models.lock` and
cannot be replaced simply by changing a model-name environment variable.

References: [Caddy TLS](https://caddyserver.com/docs/caddyfile/directives/tls),
[Caddy streaming proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy),
[Compose services](https://docs.docker.com/reference/compose-file/services/),
[Docker on Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[Hetzner provider](https://registry.terraform.io/providers/hetznercloud/hcloud/latest/docs/resources/server).
