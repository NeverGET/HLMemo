# Production operations

This directory implements DP1 (D-032). No cloud resource is needed to validate it. Real configuration
belongs outside this checkout (D-018); the public templates contain placeholders. Commands below
are operator instructions, not evidence that a VPS has been provisioned. Never use the development
Compose project `hlmemo` for these scripts. Requirements: Docker Engine + Compose **2.24+**, Bash,
Python 3, curl, `flock` (Ubuntu util-linux; macOS `brew install flock`); Terraform **1.6+** only for optional Hetzner provisioning; AWS CLI only for optional uploads.

## Two-command deploy (operator workstation)

`deploy/scripts/first_deploy.sh` performs the whole manual path below (host-key pinning,
preflight, bootstrap prepare + `--finalize-ssh`, locally generated secrets uploaded to
`/etc/hlmemo`, `deploy.sh` of the pushed `HEAD`) and is idempotent; `remote_gates.sh` then proves
the deployment from outside. Secrets and logs stay in the gitignored `deploy/.local/<host>/`
(0700/0600); values are never printed.

```sh
bash deploy/scripts/first_deploy.sh --host SERVER_IP --domain FQDN --tls acme \
  [--host-fingerprint SHA256:...] [--admin-cidr YOUR_IP/32] [--dry-run]
bash deploy/scripts/remote_gates.sh --url https://FQDN   # no admin token (D-061)
```

The A record must already point at the host (ACME HTTP-01/TLS-ALPN on 80/443). Gates: `/health`
+ `/ready`, unknown path 404, certificate issuer, 5432 closed from outside, neutral probe with a
marker round trip (probe device minted over SSH), RG-routes (the W0a public route table with
anonymous, trusted, junk and admin-token-shaped bearers), the real `hlm` CLI in an isolated config
directory (registration refused, `hlm_ops.sh` mint piped into `hlm device login --token-stdin`,
`hlm device revoke --self`), WAN `memory.query` latency, and a backup/restore drill over SSH
(`--no-drill` on a deployment holding real data). Gate devices are minted with short expiries and
revoked at the end; no admin token exists or is read.
`--g7` additionally registers the operator's claude/codex/agy CLIs (backs up their configs first).
Rehearsal-only affordances (`--ssh-port`, `--public-port`, `--domain localhost --tls internal`,
`--repo git://127.0.0.1/...`) are refused for non-loopback hosts.

## Provision any Ubuntu 24.04 or 26.04 VPS

The launch target (D-042) is **2 vCPU / 8 GB RAM**, with local e5-small in both API and worker
(e.g. Hostinger KVM 2 or OVH VPS-2). Use any provider's fresh Ubuntu 24.04 (noble) or 26.04
(resolute) image; the Docker apt suite follows the host codename. This script
creates no cloud resources; obtain the host separately and verify its SSH host-key fingerprint
through the provider console. Keep that console and the original SSH session available until
another deploy-user login succeeds. The script supports SSH port 22 and requires root/sudo.
On 26.04, `sudo` is sudo-rs and coreutils are uutils: use only `sudo --preserve-env=LIST`
(sudo-rs ignores `-E`). OpenSSH is socket-activated on both releases (`ssh.socket` owns port 22);
bootstrap reloads `ssh.service` only when running and verifies port 22 is still served. The
fail2ban jail also matches `sshd-session` (OpenSSH >= 9.8 logs authentication failures there).

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
| `api.env` | `api.env.example` | API only: cursor secret, trusted proxy CIDR (no admin token, D-061); optional owner token for `hlm review` (docs/review/README.md) |
| `db.env` | `db.env.example` | DB only: PostgreSQL variables |
| `backup.env` | `backup.env.example` | Host backup/upload only: S3 credentials and retention |
| `llm.env` (optional) | `llm.env.example` | Librarian and API (risk judge, librarian enqueue): provider key, profile, role, spend guard. Absent in R1; R2 installs it with `install_llm_env.sh` |

Replace the `CHANGE_ME_*` values (database password, cursor secret) with independent
`openssl rand -hex 32` outputs; use the **same database password** in `db.env` and `app.env`'s DSN.
`HLM_DEPLOYMENT=production`, `HLM_REGISTRATION_MODE=closed` and `HLM_ADMIN_HTTP=disabled` are
pinned in `compose.prod.yaml` itself (D-061); the API refuses to start (`/ready` 503
"unsafe config") if they are ever loosened in production. Set a real domain and
`HLM_TLS_MODE=acme` in `prod.env`. Keep secrets out of `prod.env` and keep backup credentials out
of every service file. Env files use Compose dotenv syntax, never shell `source`.
Docker administrators can inspect container environments; Docker membership is privileged.
`HLM_ENV_FILE` selects `prod.env`; scripts resolve the other four files alongside it. Explicit
`HLM_APP_ENV_FILE`, `HLM_API_ENV_FILE`, `HLM_DB_ENV_FILE`, `HLM_BACKUP_ENV_FILE` overrides select
other absolute paths (`HLM_LLM_ENV_FILE` for the optional `llm.env`). All five files must exist, even when S3 upload is disabled; `backup.env`
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
| Librarian (W2a; idle in R1) | 512 MiB | 0.5 |
| Migration (one-shot) | 768 MiB | 1 |
| Caddy | 256 MiB | 0.5 |

Steady ceilings total **6.75 GiB** (`2 + 2.5 + 1.5 + 0.5 + 0.25`); adding the one-shot
migration's 0.75 GiB gives a conservative **7.5 GiB** combined ceiling. The R1 rehearsal VM
(nominal 8 GiB) showed only **~7.9 GiB** to the kernel, leaving ~1.15 GiB steady but only
**~0.4 GiB while the migration runs**; a decimal 8 GB host (7.45 GiB) has ~0.7 GiB steady and
**no** combined headroom. Headroom during a migration is thin: `deploy.sh` stops
caddy/api/worker/librarian before `migrate` (only db + migrate run then), so never run the
migration by hand next to a live stack on such a host. Migration completes before API/worker startup. Limits are not reservations;
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

Caddy must see each client's own address. The API keys its body admission on it (16 concurrent auth/body reads and a byte budget,
with IPv6 grouped per /64) and keys its registration limit on it too. Caddy publishes its ports from the dual-stack `edge` bridge
(`enable_ipv6: true`, a pinned IPv4 /24 and a ULA /64, `gw_priority` on Caddy's attachment). Docker then DNATs IPv6 clients
natively with ip6tables, and the client's source address reaches Caddy unchanged.

**Why this is needed.** On an IPv4-only bridge, Docker's userland proxy accepts IPv6 connections and relays them to Caddy from the bridge
gateway. Every IPv6 client then shares one bucket, and slow bodies from one client can deny the others admission.
Publishing `::` alone does not fix this.

Requirements and invariants:
- Docker Engine 28+ (for `gw_priority`) and Compose 2.33+. `ip6tables` is on by default since Engine 27; leave `userland-proxy` at its default.
  Do not add daemon-wide `default-network-opts`.
- **Only `edge` has IPv6.** If `frontend` had IPv6, Caddy could reach the API over IPv6 from outside `HLM_TRUSTED_PROXY_IPS`. The API would
  then treat Caddy as the client, and all clients would share one bucket again.
  Keep `gateway_mode_ipv6` at its default `nat`. In `routed` mode the host's IPv6 ports are not published at all.
- `outbound` stays on Caddy: `check_edge.py`'s TLS client-IP gate reaches Caddy from the worker over it.
- **Host.** Creating `edge` makes Docker set `net.ipv6.conf.{all,default}.forwarding=1`, and it does so again on every daemon start.
  Linux then ignores Router Advertisements on interfaces with `accept_ra=1`.
  The host's IPv6 default route must therefore be static (`accept_ra=0`, netplan `routes`), or the uplink must use `accept_ra=2`.
  Check this before the first deploy: `ip -6 route show default` must not say `proto ra`.
  UFW rules for the host are unaffected; Docker's FORWARD rules accept only its own bridges.
- Pick an `edge` IPv4 /24 that no other host network uses. The ULA comes from a random RFC 4193 global ID. Neither needs anything from the provider.

New hosts get `edge` on the first deploy. On an existing host, the first release that contains `edge` recreates only Caddy,
for a few seconds of downtime. Before AAAA is advertised, or after that release, run the source-IP check:

```sh
export HLM_ENV_FILE=/etc/hlmemo/prod.env
docker network inspect hlmemo-prod_edge hlmemo-prod_frontend --format '{{.Name}} IPv6={{.EnableIPv6}}'   # true, false (adjust names if HLM_COMPOSE_PROJECT differs)
cid=$(bash deploy/scripts/stack.sh ps -q caddy)
pid=$(docker inspect --format '{{.State.Pid}}' "$cid")
# Addresses only: SYN packets, bounded count, no payload or headers.
sudo nsenter -t "$pid" -n timeout 60 tcpdump -n -q -l -i any -c 2 'ip6 and tcp dst port 443 and ip6[53] & 2 != 0'
# Meanwhile, from two external IPv6 sources (e.g. two of one workstation's own global addresses):
curl -6 -sS -o /dev/null -w '%{http_code}\n' --interface ADDR_1 https://YOUR_DOMAIN/ready
curl -6 -sS -o /dev/null -w '%{http_code}\n' --interface ADDR_2 https://YOUR_DOMAIN/ready
```

The two `In IP6` sources must be ADDR_1 and ADDR_2. A `172.x.0.1` source means IPv6 is still relayed through the userland proxy.
Never trust client-supplied forwarding headers as a workaround.

**If IPv6 is your only admin path**, arm a dead-man's switch before the release. The switch is a root systemd transient timer that restores the
previous compose file, runs `stack.sh up -d --wait --no-deps caddy`, removes the `edge` network and restores the forwarding sysctls.
It fires unless it is cancelled after the checks above pass. Keep the provider's console as the fallback.
Rehearsed on Ubuntu 26.04 with Docker 29.8 and compose 5.5, the switch must meet these conditions:
- every docker/compose call is time-bounded, and the apply runs in its own unit with `TimeoutStartSec`, so a hung apply can never block the revert;
- the revert stops a running apply before it takes the shared lock;
- cancel and revert share that lock, and cancel refuses once a revert has started;
- the revert writes success only after it re-verifies compose, Caddy's networks, the removal of `edge`, the sysctls and `/ready`.
  A docker query that fails or times out counts as a failed check, never as "absent";
- one shared deadline, below the unit's own timeout, bounds the whole revert. Running out, or a stop signal, ends in an explicit failed state, never in a half-finished one.

Before cancelling, also prove the API's proxy trust: run the running API's own `trusted_client_ip()` against Caddy's live `frontend`
address and the effective `HLM_TRUSTED_PROXY_IPS`. `/ready` passes even when that CIDR is wrong.

**Production status:** \<date\>: `edge` live, two distinct client IPv6 addresses observed at Caddy (D-2xx).

**Production status:** live since 2026-10-03 (D-242). Three held connections from three distinct client IPv6 addresses
showed up in Caddy's socket table as three distinct peers. The gates pass 11/11.
The live checkout carries the change as a tracked edit until the next release: run
`git -C /opt/hlmemo/app checkout -- deploy/compose.prod.yaml` right before that deploy. NEVER deploy a release that lacks the
`edge` network (main 44d5f38 or later): Caddy would move back to the shared IPv6 bucket.


> **Librarian role promotion: DO NOT** run `ops librarian role set assistant|autonomous` while the project has
> `accepted_pending` proposals that were not independently verified (2026-10-03: 298 pending, of which only 2 are real contradictions).
> A promotion mass-applies every eligible pending batch in every project. Check first with
> `ops librarian audit --project <slug> --status accepted_pending --json`. See BACKLOG "Librarian pending-queue hazard".
> From the release with migration `0011_question_withdrawn` on, `role set` refuses such a promotion by itself, and
> `ops librarian withdraw` takes unverified proposals off the queue: see "Librarian pending queue: withdraw and the
> promotion guard" below.

## First deploy and admin bootstrap

Install all five completed private env files, with `HLM_BACKUP_DIR=/var/backups/hlmemo` in `backup.env`:

```sh
scp /secure/path/{prod,app,api,db,backup}.env hlmdeploy@SERVER:/opt/hlmemo/
ssh hlmdeploy@SERVER 'for file in prod app api db backup; do sudo install -o hlmdeploy -g hlmdeploy -m 0600 "/opt/hlmemo/$file.env" "/etc/hlmemo/$file.env" && rm "/opt/hlmemo/$file.env"; done'
bash deploy/scripts/deploy.sh hlmdeploy@SERVER "$(git rev-parse RELEASE_REF)"   # full SHA
```

**Always pass a full 40-character SHA** (`"$(git rev-parse REF)"`): the server fetches the ref by
name and `git fetch origin <short sha>` cannot resolve an abbreviated SHA (R1 rehearsal).
`deploy.sh` expands a 7-39 character hex ref to the full SHA from the local checkout before
sending it (and refuses one it cannot resolve), but the examples here spell the full SHA out.

The deploy user must own the env directory as well as `prod.env`: successful releases replace
that file atomically. Cloud-init configures this for new hosts. On an existing host created by
older tooling, run `sudo chown hlmdeploy:hlmdeploy /etc/hlmemo` and
`sudo chmod 0750 /etc/hlmemo` once (substitute your configured deploy user).

`RELEASE_REF` must include the deploy tooling in the remote repository. An optional third argument
selects another repository URL; private repositories need read credentials installed separately on
the server. Do not embed credentials into the URL. Defaults: `/opt/hlmemo/app`, `/etc/hlmemo/prod.env`;
override with `HLM_REMOTE_DIR` / `HLM_REMOTE_ENV`. The script fetches the requested ref, resolves an
immutable commit, builds including model assets, takes a snapshot-consistent pre-upgrade dump while
the DB and writers are live (and, after stopping writers, a final quiesced dump that becomes the
rollback dump), runs the idempotent `migrate_env_w0` step (removes the retired
`HLM_ADMIN_TOKEN` / `HLM_REGISTRATION_SECRET` from `prod.env`, `app.env` and `api.env` after a
0600 `<file>.pre-w0-<stamp>` backup; only key names are logged; a failed deployment restores the
backups), then stops writers and runs `alembic upgrade main@head`, starts with
`up -d --wait`, verifies API readiness, local Caddy routing and the W0a route table on the API's
loopback listener (`check_edge.py --routes --mint-ops`; failure restores the previous stack), then
checks public HTTPS readiness with certificate validation and the public route table through Caddy
(failure leaves the internally healthy stack running, like the readiness probe). The runner itself is extracted on the server with `git show` from
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

### Adding a device (operator + owner over SSH)

Production has no public registration and no admin HTTP routes (D-052, D-061): `POST
/devices/register`, `/devices/approve`, `/devices/grant`, `/devices/list` and every `/admin/*`
route answer 404 before a body byte is read, and device 1 is never bound. Every admin action is
`python -m hlmemo.ops` inside the api container, reached over SSH with
`deploy/scripts/hlm_ops.sh` (SSH config and host from `deploy/.local/<host>/`, arguments quoted
with `printf %q`, `ssh -n`). The token of a minted device travels only on stdout, straight into
`hlm device login --token-stdin`, which checks `/health` reports it `trusted` and stores it in the
keychain (else a 0600 credentials file). It never appears in argv, shell history or logs.

**Always call `hlm_ops.sh` with `--state deploy/.local/<host>`** (or `HLM_OPS_STATE`): without it
the wrapper guesses from `deploy/.local/*/deploy.conf` and refuses when rehearsal and production
state directories coexist; `--state` makes the target host explicit. SSH uses that directory's
pinned `known_hosts` with `StrictHostKeyChecking=yes`. **A changed SSH host key on production is a
real alarm, not a nuisance** (R1 rehearsal note): stop; do not delete the `known_hosts` entry or
re-pin from `ssh-keyscan`. Verify the new fingerprint through the provider console (rebuilt host,
or someone in the middle) before re-pinning. A legitimately recreated rehearsal VM changes its
key; production should not.

```sh
uv sync --frozen
export HLM_SERVER_URL=https://memory.example.org/mcp  # replace domain
OPS="bash deploy/scripts/hlm_ops.sh --state deploy/.local/SERVER_IP"
$OPS project create my-project --name 'My project' --exists-ok
uv run hlm init --server "$HLM_SERVER_URL" --project my-project --device-name my-mac
$OPS device mint --name my-mac --class personal --grant my-project:write \
  | uv run hlm device login --name my-mac --token-stdin
uv run hlm device whoami
uv run hlm mcp add claude
uv run hlm mcp add codex
uv run hlm mcp add agy
uv run hlm query 'project context'
uv run hlm claude
```

The owner can also paste a token: `uv run hlm device login --name my-mac` prompts without echo
(`getpass`). For a CI runner: `--class ci --grant slug:write --expires 30m`. Other operator
commands: `device list [--json]`, `device grant REF SLUG ROLE`, `device ungrant REF SLUG`,
`device rotate REF [--expires D | --no-expiry]` (prints the new token; pipe it into
`hlm device login` and re-run `hlm mcp add`), `device revoke REF` (the lost-laptop case, run from
any machine with the SSH key), `project list`, `status [--json]` (jobs ledger, worker progress,
devices by status, migration). A device revokes itself with `uv run hlm device revoke --self`
(the only public revoke: another device's id answers 404). `devices.expires_at` is enforced like a
revocation at the pre-body gate and in the request transaction; renewal is `device rotate --expires`.
`hlm device register` against production prints this procedure as a hint.
Codex's MCP entry stores the env-var name, so launch with `hlm codex` to inject `HLM_DEVICE_TOKEN`.
Claude and agy adapters store the bearer in their user configurations; protect those files.
See [the exact supported CLI commands](../docs/USAGE.md). `hlm doctor` also checks local DB/model
settings and may report those local checks absent on a remote-only workstation; `/ready` is the
server's authoritative DB/model readiness check. Publicly `/ready` answers only
`{"status": "ready"|"not_ready"}` (200/503); the per-check diagnostics are served to loopback peers
only (container healthcheck, `docker exec`) and printed by `hlm_ops.sh --state deploy/.local/<host> status` (D-061, Sol 34 #6).

Local development (`compose.yaml`) keeps the Phase-0 flow (`HLM_REGISTRATION_MODE=open`,
`HLM_ADMIN_HTTP=enabled`, `HLM_ADMIN_TOKEN` from `.hlm-dev.env`): see docs/USAGE.md.

## Upgrade and rollback

### Compose model changes (`--accept-compose-change`)

`deploy.sh` refuses a release whose `deploy/compose.prod.yaml` differs from the running one
**before build, backup or writer shutdown**, unless the operator acknowledges that exact model:

```sh
REF=$(git rev-parse <ref>)                                   # full SHA, never a short one
git show "$REF:deploy/compose.prod.yaml" | shasum -a 256    # review the diff first
bash deploy/scripts/deploy.sh --accept-compose-change=<that sha256> hlmdeploy@SERVER "$REF"
```

A missing or different hash is refused before anything stops. With the acknowledgement the normal
detached runner does, in order: validate the full new and previous refs and the previous image
(before any stop); render the **previous** release's own Compose model with the current env files
as the rollback model; take a live pre-upgrade dump (proves backups work); `migrate_env_w0`; stop
caddy/api/worker/librarian; take the final **quiesced** dump (no write can commit after it; it replaces the
live one and is the rollback dump); build; `alembic upgrade main@head`; `up --wait`; internal
readiness, Caddy loopback and the W0a route table; publish the image; publish the rollback tuple
atomically in `/opt/hlmemo/release-state.json` (previous ref, quiesced dump, image + ID, this
run's env backups; legacy `current-ref`/`previous-ref`/`previous-dump` are derived from it); public readiness + route table; print the device
inventory. On any internal failure it restores the quiesced dump if migration began, restores the
env-file backups (retired secrets included), selects the previous image explicitly
(`HLM_IMAGE=repository:<previous>`), verifies the rendered rollback model runs the pinned previous
image ID, and only then starts the previous stack; markers are not touched. Re-running the same
command is idempotent. PostgreSQL major-version or volume-layout changes remain separate migrations.

**W0a (D-061)** is such a release: `deploy.sh --accept-compose-change=<sha256 of R's compose.prod.yaml>
hlmdeploy@SERVER "$(git rev-parse R)"`, then from the workstation `remote_gates.sh --url https://FQDN --no-drill` and,
from the printed device inventory, rotate the g7 device (`remote_gates.sh ... --g7`, or
`hlm_ops.sh --state deploy/.local/<host> device rotate g7-<host> | hlm device login --name g7-<host> --token-stdin` followed by
`hlm mcp add claude|codex|agy`) and revoke stale pre-W0a devices (`gates-*`, `deploy-*`, `judge-*`)
with `hlm_ops.sh --state deploy/.local/<host> device revoke <name>`. Pre-W0a tokens have no expiry and keep working until then.
W0a is a one-way door (D-065): no manual rollback to the pre-W0 release; recovery rolls forward.
The `/etc/hlmemo/*.pre-w0-*` backups (retired secrets) are recorded in `release-state.json`
`retired_backups` the moment they are created (also by a deployment that then fails and is
auto-recovered; its restore removes the now-redundant copy) and deleted by
`deploy.sh --accept-release hlmdeploy@SERVER`, which for a W0+ current release also sweeps any
unrecorded `*.pre-w0-*` next to the env files; never delete them by hand.

**W2a/R1 (librarian service)** changes the model too (new `librarian` service): deploy it with
`--accept-compose-change=<sha256>` exactly like W0a. The runner stops/starts the librarian with the
other writers, checks its heartbeat (`python -m hlmemo.librarian.health`) after `up --wait`, and on
recovery to a model without it removes the new librarian container. See "Librarian (W2a)" below.
**R2** (librarian ON) changes it again (the `HLM_LIBRARIAN_ENABLED` pin removed, `llm.env` mounted
into the api): the full sequence is in "R2 release (librarian ON, observer)" below.

### Librarian (W2a service; switched by `llm.env` since R2)

The `librarian` service runs the release image (`python -m hlmemo.librarian.worker`, 512 MiB, 0.5
CPU, no port). It writes a heartbeat every 10 s (healthcheck: heartbeat younger than 120 s; the
heartbeat carries `enabled`, `role`, `breaker_state` and the spend fields). In **R1** the Compose
model pinned `HLM_LIBRARIAN_ENABLED: "false"`; since **R2** there is no pin: `llm.env`
(`/etc/hlmemo/llm.env`, optional, found next to `prod.env`; `HLM_LLM_ENV_FILE` overrides) decides,
and without it the default is off. Off (no `llm.env`, `HLM_LIBRARIAN_ENABLED=false` or
`HLM_LLM_MODE=off`), it leases no job, loads no provider and needs no key (`stack.sh logs
librarian`: `... idling`). The same `llm.env` is mounted into the **api**, because the
`memory.risk_check` judge (W2d) and the enqueue of `librarian_write` jobs run there; the key never
reaches db, worker, migrate or caddy (`tests/deploy/test_compose_isolation.py`). A role above
`observer` needs an owner decision event (D-062), else the librarian refuses to start.

### Librarian pending queue: withdraw and the promotion guard

Needs the release with migration `0011_question_withdrawn` (adds the terminal question status
`withdrawn`; backward compatible, no data change). Older code runs unchanged on that schema: it never
applies a withdrawn question. A schema downgrade refuses while any question is `withdrawn`; the
supported rollback stays the pre-upgrade dump (withdraws made after it are lost with it).

**`ops librarian withdraw`** moves `open`, `approved` and `accepted_pending` questions of ONE project to
`withdrawn`. Nothing in user memory changes. It writes ONE `librarian` event (operator device 1, client
`hlm-ops/<version> (owner:NAME)`, op `withdraw`; the request holds the ids and the redacted reason;
`resolved.question_status` holds one record per question, keeping the prior status and the owner's
prior answer). Replay rebuilds it exactly. A withdrawn question is never applied: not by a queued
`apply_batch` job, the sweeper, `release_pending`, a later promotion or `memory.answer`.

It is all or nothing. Under the apply path's locks (role-order lock shared, then the question rows), it
refuses with exit 1, writes nothing and lists the offending ids per reason (stderr JSON
`details.refused`) for any of these:
- `unknown`: the id does not exist;
- `other_project`: the question belongs to another project;
- `applied`: the question was already applied;
- `not_pending`: the question has another status (`rejected`, `withdrawn`, ...), listed per status;
- `running_apply`: its batch has a RUNNING `apply_batch` job; re-run once the job is done;
- `link_not_live` / `link_mismatch`: with `--resolved-by-link`, the link is not live or does not join
  the question's subjects. The link id is metadata only.

Malformed ids, an unreadable `--ids-file` or an empty `--reason` exit 2. `--ids-file -` reads stdin
(one id per line; blank lines and `#` comments are skipped).

Withdraw the D-244 queue (298 `accepted_pending` in `hlmemo`) from the operator workstation. Keep out
of the file every id the owner wants applied later (for example the 2 verified real contradictions).
The id file travels on the ssh stdin; `hlm_ops.sh` closes stdin, so these commands use plain ssh:

```sh
STATE=deploy/.local/153.92.1.166
OPS='cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api python -m hlmemo.ops librarian'
# 1. the pending set (read-only) and the ids to withdraw ($STATE/keep.txt: the ids to keep, one per line;
#    create it, empty when nothing is kept)
ssh -F "$STATE/ssh_config" hlm-deploy "$OPS audit --project hlmemo --status accepted_pending --json" </dev/null > "$STATE/pending.json"
python3 -c 'import json,sys; keep = set(open(sys.argv[2]).read().split()); ids = [p["question_id"] for p in json.load(open(sys.argv[1]))["proposals"]]; print("\n".join(i for i in ids if i not in keep))' \
  "$STATE/pending.json" "$STATE/keep.txt" > "$STATE/withdraw-ids.txt"
wc -l < "$STATE/withdraw-ids.txt"          # expected: 298 minus the kept ids
# 2. dry run: every check under the locks, nothing written. PASS: exit 0, withdrawn == the line count,
#    by_status == {"accepted_pending": N}
ssh -F "$STATE/ssh_config" hlm-deploy "$OPS withdraw --project hlmemo --ids-file - --reason 'D-244: verified; not applied as labeled (supersessions handled by curated links, the rest no conflict)' --dry-run --json" \
  < "$STATE/withdraw-ids.txt" > "$STATE/withdraw-preview.json"
python3 -c 'import json,sys; d = json.load(open(sys.argv[1])); n = sum(1 for x in open(sys.argv[2]) if x.strip()); ok = d["dry_run"] and d["withdrawn"] == n and set(d["by_status"]) == {"accepted_pending"}; print("preview", "PASS" if ok else "FAIL", d["withdrawn"], d["by_status"]); sys.exit(not ok)' \
  "$STATE/withdraw-preview.json" "$STATE/withdraw-ids.txt"
# 3. withdraw: ONE event; record its event_id
ssh -F "$STATE/ssh_config" hlm-deploy "$OPS withdraw --project hlmemo --ids-file - --reason 'D-244: verified; not applied as labeled (supersessions handled by curated links, the rest no conflict)' --owner OWNER_DEVICE_NAME --json" \
  < "$STATE/withdraw-ids.txt" > "$STATE/withdraw.json"
# 4. what a promotion would release now (records nothing): only the kept ids remain
ssh -F "$STATE/ssh_config" hlm-deploy "$OPS role set assistant --decision D-NNN --dry-run" </dev/null
```

A refused run (exit 1) wrote nothing: fix the file from `details.refused` and run the dry run again.
For one question that a curated link already implements, add `--resolved-by-link LINK_ID` (one link
per command; the link must join that question's subjects).

**Promotion guard.** `ops librarian role set assistant|autonomous` first counts, under the exclusive
role-order lock, the questions the decision would let the librarian apply. These are every unexpired
`approved` and `accepted_pending` question (never `widen_scope`) whose touched projects are all above
observer after the decision, counted per project. If there are any, the command exits 1, records
nothing and prints the counts with `withdraw or verify them first`. Only `--release-pending N`, where
N equals that exact total, records the promotion. A different N, 0 included, is refused, and the
accepted N is stored in the event (`request.release_pending`). `--dry-run` prints `would_release`
(total and per project), the `apply_batch` jobs the event would queue and the flag it `needs`, and
records nothing. A demotion (`observer`) releases nothing, so it needs no flag; a per-project
promotion is counted with the roles as they will stand after it.

### R2 release (librarian ON, observer)

R2 turns the librarian on in the **observer** role (D-058: every link, duplicate or contradiction
becomes a proposal; the only thing it applies is placement into `version_signals`) and enables the
`memory.risk_check` LLM judge in the api (D-066 primary `openrouter-gpt6-luna`; D-071: the
`openrouter` deepseek fallback never judges, so during a primary outage risk_check answers
`judged:false, judge:"retrieval_only"`). Its Compose model differs from R1 (pin removed, `llm.env`
also mounted into the api), so it ships with `--accept-compose-change`. Release-blocking before
this sequence: `make gate-release` (G3, G4, G-L3; G-L3 also on the 2 vCPU VM), `make gate-live`
(G-LIVE-A/B/C on gpt-6-luna and the fallback), the Sol review and the rehearsal on the VM.

Exact sequence, from the operator workstation at the repository root (`STATE` is the host's state
directory written by `first_deploy.sh`; production: `deploy/.local/153.92.1.166`):

```sh
STATE=deploy/.local/<host>
REF=$(git rev-parse <R2 release ref>)        # full 40-character SHA, already pushed to origin
# 1. llm.env on the host: HLM_LIBRARIAN_ENABLED=true, HLM_LIBRARIAN_ROLE=observer,
#    HLM_PROFILE=openrouter-gpt6-luna, the template's fallbacks (D-094: HLM_FALLBACK_PROFILE for
#    librarian jobs plus HLM_FALLBACK_PROFILE__<TASK> per task) and its spend caps.
#    OPENROUTER_API_KEY is read from ./.env (only that variable; --key-file FILE for another file)
#    and travels on ssh stdin: never argv, a log or the output. 0600, deploy user; idempotent.
bash deploy/scripts/install_llm_env.sh --state "$STATE"
# 2. review the model change, then deploy with its exact hash
git diff "$(ssh -F "$STATE/ssh_config" hlm-deploy cat /opt/hlmemo/current-ref </dev/null)" "$REF" -- deploy/compose.prod.yaml
SHA256=$(git show "$REF:deploy/compose.prod.yaml" | shasum -a 256 | cut -d' ' -f1)
PATH="$PWD/$STATE/bin:$PATH" bash deploy/scripts/deploy.sh --accept-compose-change="$SHA256" hlm-deploy "$REF"
# 3. gates from outside, with the observer check (no drill: production holds real data)
bash deploy/scripts/remote_gates.sh --url https://FQDN --state "$STATE" --no-drill --librarian
# 4. health and spend
bash deploy/scripts/hlm_ops.sh --state "$STATE" status
```

Installing `llm.env` first is safe on R1: the R1 model pins the librarian off and its api does not
mount the file, and no running container reads it before the R2 containers are created. A changed
`llm.env` is first copied to `llm.env.bak-<UTC stamp>` (0600; the newest 3 are kept); re-running
with the same key prints `unchanged`. `install_llm_env.sh` rewrites the whole file from the
template, so hand edits on the host are replaced (and kept in the backup) by the next install.

After cutover the deployment prints the **librarian check** (`deploy/scripts/check_librarian.py`,
after the device inventory): switch, role, heartbeat (`enabled`, `role`, `breaker_state`), spend,
the api's risk-judge chain, and per profile whether its key is set and whether its base URL answers.
With `llm.env` present it validates the R2 configuration strictly (Sol 48) and fails the deployment
(the new stack stays running, no database rollback) unless the api settings, the librarian settings
**and** the heartbeat all say `enabled=true` and role `observer`, both settings say
`HLM_LLM_MODE=live`, the heartbeat is fresh (Sol 49: at most 3 heartbeat intervals old, 30 s by
default; a missing or stale one is re-read for up to 45 s after cutover), the api's risk-judge
chain loads and is non-empty, and every profile key is set. The heartbeat is written between jobs,
so one job running longer than that window also fails the check: see `stack.sh logs librarian`. An unreachable provider is **reported only** (`UNREACHABLE ...; reported only`): jobs wait
with backoff and risk_check answers retrieval-only until it returns. Without `llm.env` (R1-style)
the R2 check passes only while everything idles; from R3 on a missing `llm.env` fails (below).

`remote_gates.sh` then adds two gates. `risk-check` (always): one registered lesson plus a task
that repeats its mistake; PASS when the tool returns a verdict, reporting `judged=true|false` and
the reason. `librarian` (with `--librarian`): a marker write, its `librarian_write` job awaited
(`--librarian-wait`, default 300 s), then the job's `librarian` event must be `role=observer` with
only `signal_upsert` mutations (zero links, zero closes/invalidations), the marker must have a
`version_signals` row, and `python -m hlmemo.ops librarian audit --project gates-probe --json` must
list none of its proposals (or their batches) as applied. On a release without the `librarian
audit` subcommand the gate is SKIPPED with that message.

**Switch it off quickly** (no redeploy): set the switch in `llm.env` and recreate the two services
that read it; `--no-deps` leaves db, worker, caddy and the one-shot `migrate` alone. The api restarts
(about a minute of 502s), the librarian idles, the api stops enqueueing librarian jobs and
risk_check answers retrieval-only; queued jobs wait. Back on: the same command with `true`.
While switched off this way, a **deployment** fails its post-cutover librarian check (llm.env present
but not enabled/live/observer; the new stack stays running). To deploy with the librarian off,
take the key off the host first (`install_llm_env.sh --remove`, below), and reinstall it afterwards.

```sh
ssh -F "$STATE/ssh_config" hlm-deploy 'sed -i "s/^HLM_LIBRARIAN_ENABLED=.*/HLM_LIBRARIAN_ENABLED=false/" /etc/hlmemo/llm.env &&
  cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh up -d --no-deps librarian api' </dev/null
```

An instant brake that leaves the api untouched is `stack.sh stop librarian` (jobs queue up; the api
still judges risk_check). To take the key off the host: `install_llm_env.sh --state "$STATE"
--remove` (deletes `llm.env` and its backups), then the same `up -d --no-deps librarian api`. A
rollback to R1 (`deploy.sh --rollback`) runs R1's model: librarian pinned off, api without the key;
the file on the host is ignored.

**Spend monitoring:** `hlm_ops.sh --state "$STATE" status` prints the `librarian` line: `ready`,
`in_flight`, `role`, `breaker` (`open` = provider outage, `budget` = a cap tripped, `closed`
otherwise, as in the librarian's own heartbeat; `(ledger, N calls/15m)` means inferred from the
llm_calls ledger because the api cannot see the librarian's heartbeat file, and `0 calls/15m`
means nothing was called in the window),
`failed_24h`, `spend_today_usd`, `spend_hour_usd`, `reserved_usd`; `status --json` has the same
fields under `librarian`. The caps in `llm.env` are a runaway guard with the D-058 development
defaults, not a budget: `HLM_LLM_BUDGET_HOUR_USD=3`, `HLM_LLM_BUDGET_DAY_USD=10`,
`HLM_LLM_BUDGET_MONTH_USD=60` (worst-case reservations at the profile's peak price) and
`HLM_LLM_JOB_CALL_CAP=20` provider calls per job lineage. A tripped cap pauses the librarian
(`breaker=budget`) and risk_check falls back to retrieval-only until the window rolls over. Check
the spend after the gates, daily during the Phase 5 migration, and against the provider's own
activity page; change a cap in `llm.env` and recreate `librarian api` as above.

### R3 release (D-108 order, D-111, D-116: no query rewrite)

R3 ships WITHOUT the query rewrite and the per-source cap (D-116). The post-cutover check validates
`llm.env` against the **release manifest** in `check_librarian.py` (`RELEASE_MANIFESTS`): for R3,
`HLM_QUERY_REWRITE` and `HLM_RETRIEVAL_SOURCE_CAP` absent or false in the api's and the librarian's
env, and the D-094 fallback mapping (`HLM_PROFILE`, `HLM_FALLBACK_PROFILE`,
`HLM_FALLBACK_PROFILE__SYNTHESIS`, `HLM_FALLBACK_PROFILE__RISK_JUDGE`) present.
Order B (D-108) stays: deploy the R3 ref while the R2 `llm.env` is still installed; its librarian
check passes as the **interim** (`RESULT librarian PASS llm.env=present release=r2-env (D-108
interim ...)`: the R2 checks and the manifest's "off" keys). Then install the R3 env with the R3
checkout's `install_llm_env.sh` (it writes the release marker `HLM_ENV_RELEASE=r3`), recreate
`librarian api` and run the check again with `--release r3` (the same collect/evaluate the runner
uses, `evaluate --llm-env present ... --release r3`): R3 mode fails unless both services run the R3
env and it satisfies the R3 manifest. An api that already runs the R3 env is checked in R3 mode by
every deployment; an R3 deployment without `llm.env` fails.
`llm.env` is part of the release state: the runner snapshots the env the previous release runs with
(`llm.env.release-<ref>`, 0600, `previous_llm_env` in `release-state.json`), and `--rollback`
renders the previous model with it and restores it atomically before the previous image starts
(a failed rollback step puts the newer env back first). Reinstalling the R2 env by hand before a
rollback is no longer needed, but harmless.

### R4 release (memory.ask with the Gemini writer; docs/decisions/R4-RELEASE-PLAN.md)

R4 = the R3 code plus `memory.ask` (the research librarian) with the Gemini 3.8 Flash **medium**
writer (D-198). `memory.ask` (api) and its Memory Map summaries (librarian) default **OFF** in the
code: an R3 `llm.env` on the R4 image never serves `memory.ask` and never spends on it (the R3
manifest keeps `HLM_RESEARCH_ENABLED` and `HLM_MAP_SUMMARY_ENABLED` absent or false). The order is
the R3 one (D-108 Order B): deploy the R4 ref with the R3 env still installed (checked against the R3
manifest), then switch the env with the R4 checkout's `install_llm_env.sh`. The only migration is
0009 (a create-only cache table) and the Compose model is unchanged (no `--accept-compose-change`).

**What `evaluate --release r4` checks** (`RELEASE_MANIFESTS["r4"]` in `check_librarian.py`, on top
of the R2/R3 checks):
- memory.ask ON, the map summaries OFF (D-192/D-195), `HLM_RESEARCH_ANSWER_MODE=prose`,
  `HLM_RESEARCH_ATTRIBUTION=llm`, `HLM_RESEARCH_RERANK=llm`, the D-094 mapping plus the research and
  map-summary fallbacks, exactly;
- `HLM_RESEARCH_WRITER_PROFILE` unset (the research primary `HLM_PROFILE`, luna, writes: the plan's
  §6.2(b)) or `google-gemini38-flash-medium` (the template) or `google-gemini38-flash-high`;
- `HLM_RESEARCH_MAX_USD` ≤ 0.12, `HLM_RESEARCH_MAX_TOKENS` ≤ 100000; the research, HTTP, writer and
  LLM timeouts, the detached hold and the prose limit run the same in api, librarian and the file;
- the spend guard ON with the owner's R4 caps (`_BUDGETS_R4`, D-198): HOUR ≤ 3, DAY ≤ 8, MONTH ≤ 60
  (R3's `_BUDGETS` stays MONTH ≤ 10, so an R3 env never passes with the R4 caps);
- every profile key set, the writer's `GEMINI_API_KEY` included (an unset writer needs none);
- **R-11:** the api runs WITHOUT the research tracer (`HLM_RESEARCH_TRACE_DIR` unset everywhere);
- **R-14:** `python -m hlmemo.ops probe-writer` in the **api** container (the api's own writer
  profile and key; one tiny request, no retry, no fallback) exits 0 with `ok=true` and the api's
  writer; only its `ok/profile/status/latency_ms` are printed (`status` may be `price_expired`).
  With the writer unset it probes the head of the research chain and must report it: `HLM_PROFILE`,
  or, when that profile's file lists `research` in `disabled_tasks`,
  `HLM_FALLBACK_PROFILE__RESEARCH` (else `HLM_FALLBACK_PROFILE`). Required either way;
- **R-5:** the writer profile's `price_valid_until` has not passed (a WARNING within 14 days): the
  2027-01-01 Gemini prices need a new profile commit with the new prices and date first. With the
  writer unset, the research primary's profile is checked only if it carries the field;
- **R-4:** the llm.env fingerprint (deploy/rollback provenance) covers all of the above keys and the
  caps, and both api and librarian must report.

**Exact sequence** (operator workstation, the repository root checked out at the `r4-rc` merge;
`STATE` is the production state directory; every remote command uses the host form
`cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh …`):

```sh
STATE=deploy/.local/153.92.1.166
REF=$(git rev-parse r4-rc)        # the FULL 40-character SHA, pushed to origin (the host fetches it)
R3=$(git rev-parse c98ec0a)       # the R3 release (behaviour-only rollback template)

# 0. Snapshot VM 2002259 ONLY (Hostinger VPS_createSnapshotV1), wait for it, record its id. Hostinger
#    keeps ONE snapshot per VM and a new one overwrites the old: take it now, before step 2, and never
#    again during or after the test (it would overwrite this rollback point). The other VPSs are
#    never touched. Pause the backup timer for the attended window (D-123; an admin session):
#    sudo systemctl stop hlmemo-backup.timer

# 1. The key file: a private directory, 0600, EVERY key the R4 env needs (--reset-operator-values
#    below takes every key from it): the OpenRouter prod key (D-120) and the Gemini key (the paid
#    Google project, the local .env). Values never on a command line, never printed.
umask 077
KEYDIR=$(mktemp -d)
KEYS=$KEYDIR/r4-keys.env
printf 'OPENROUTER_API_KEY=%s\n' "$(tr -d '\n' < "$STATE/openrouter-prod.key")" > "$KEYS"
grep '^GEMINI_API_KEY=' .env >> "$KEYS"
chmod 600 "$KEYS"
cut -d= -f1 "$KEYS"                                            # names only: both lines present
sed -n 's/^GEMINI_API_KEY=//p' "$KEYS" | tr -d '\n' | shasum -a 256 | cut -c1-12   # prefix to compare

# 2. Deploy the R4 ref with the R3 env still installed (no Compose change). Interim check: the
#    librarian check prints "RESULT librarian PASS llm.env=present release=r3 manifest=r3" (research
#    OFF under the R3 env), then "Deployment ready".
PATH="$PWD/$STATE/bin:$PATH" bash deploy/scripts/deploy.sh hlm-deploy "$REF"

# 3. The env switch, from this (R4) checkout, WITH --reset-operator-values: without it the installed
#    R3 caps 1/2/10 win over the template's 3/8/60 (and the installed keys over the key file's).
#    Under the deploy lock: journal, write llm.env, recreate librarian+api, evaluate --release r4
#    --writer-probe api, clear the journal. Pass: "writer probe (api container): exit=0 ok=True
#    profile=google-gemini38-flash-medium ..." and "RESULT librarian PASS llm.env=present release=r4
#    ... writer=google-gemini38-flash-medium". Interrupted: re-run the same command.
bash deploy/scripts/install_llm_env.sh --state "$STATE" --key-file "$KEYS" --reset-operator-values
ssh -F "$STATE/ssh_config" hlm-deploy \
  "sed -n 's/^GEMINI_API_KEY=//p' /etc/hlmemo/llm.env | tr -d '\n' | sha256sum | cut -c1-12" </dev/null
#    (the same prefix as step 1)
rm -f -- "$KEYS" && rmdir -- "$KEYDIR"     # a rollback install rebuilds it with the step-1 recipe

# 4. Gates from outside (the drill restores a fresh backup over the live data: run it BEFORE the
#    links apply, with no other client writing), the postgres-closed gate included; then status.
bash deploy/scripts/remote_gates.sh --url https://mcp.hlmemo.com --state "$STATE" --librarian
bash deploy/scripts/hlm_ops.sh --state "$STATE" status
bash deploy/scripts/hlm_ops.sh --state "$STATE" status --json | python3 -c 'import json,sys
r = json.load(sys.stdin)["research"]
print({k: r.get(k) for k in ("writer_used_24h", "writer_fallback_24h", "writer_outcomes_24h",
                             "writer_questions_24h", "writer_fallback_questions_24h")})'
#    (the 24 h numbers are operational: per call AND per question, one ledger lineage per ask; the
#    final test's fallback share comes from the answers' meta.flags.writer_fallback, plan §5.1)
#    Resume the backup timer (admin): sudo systemctl start hlmemo-backup.timer
```

The same check at any time (read-only apart from the probe's one tiny Google request, ~$0.0001),
and the probe alone:

```sh
ssh -F "$STATE/ssh_config" hlm-deploy 'cd /opt/hlmemo/app && export HLM_ENV_FILE=/etc/hlmemo/prod.env &&
  d=$(mktemp -d) &&
  bash deploy/scripts/stack.sh exec -T librarian python - collect --service librarian --probe --wait-heartbeat 45 \
    < deploy/scripts/check_librarian.py > "$d/l.json" &&
  bash deploy/scripts/stack.sh exec -T api python - collect --service api < deploy/scripts/check_librarian.py > "$d/a.json" &&
  python3 deploy/scripts/check_librarian.py evaluate --llm-env present --llm-env-file /etc/hlmemo/llm.env \
    --release r4 --writer-probe api --librarian "$d/l.json" --api "$d/a.json"; rc=$?; rm -rf "$d"; exit $rc' </dev/null
bash deploy/scripts/hlm_ops.sh --state "$STATE" probe-writer   # {"ok","profile","status","latency_ms"}; non-zero exit on failure
```

**Curated links** (prod data change, plan §4.6; `--proposals` must name a file INSIDE the api
container, and `docker compose cp` cannot write into its `/tmp` tmpfs, so the file is streamed in):

The preview is `--dry-run` (it runs every check under the locks, then rolls back; `applied` stays 0
in its output). A stale or foreign record anywhere in the file rejects the whole apply: exit 65,
`{"rejected": true, "stale": [...], "foreign": [...]}`, nothing written. `counts` prints
`<events>|<links>|<live backfill links of hlmemo>` (read-only).

```sh
counts() {
  ssh -F "$STATE/ssh_config" hlm-deploy 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T db sh -s' <<'SQL'
psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --tuples-only --no-align --command="SELECT (SELECT count(*) FROM events), (SELECT count(*) FROM links), (SELECT count(*) FROM links l JOIN projects p ON p.project_id = ANY (l.project_ids) WHERE p.slug = 'hlmemo' AND l.rel = 'supersedes' AND l.props->>'by' = 'backfill' AND l.superseded_at = 'infinity')"
SQL
}
P=docs/private/<the approved proposals file>.jsonl
APPROVED=<the approved count of the review>   # = the file's "proposed" records for hlmemo
ssh -F "$STATE/ssh_config" hlm-deploy "cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api sh -c 'umask 077 && cat > /tmp/r4-links.jsonl'" < "$P"
# preview (rolls back). PASS: exit 0, len(links) == $APPROVED, and the counts unchanged
before=$(counts)
ssh -F "$STATE/ssh_config" hlm-deploy 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api hlm links backfill --project hlmemo --apply --proposals /tmp/r4-links.jsonl --dry-run --json' </dev/null > "$STATE/r4-links-preview.json"
python3 -c 'import json,sys; d = json.load(open(sys.argv[1])); n = len(d["links"]); ok = d["preview"] and n == int(sys.argv[2]); print("preview", "PASS" if ok else "FAIL", n, "of", sys.argv[2]); sys.exit(not ok)' "$STATE/r4-links-preview.json" "$APPROVED"
test "$(counts)" = "$before" && echo "counts unchanged: $before"
# apply: ONE librarian event; record its id
ssh -F "$STATE/ssh_config" hlm-deploy 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api hlm links backfill --project hlmemo --apply --proposals /tmp/r4-links.jsonl' </dev/null
ssh -F "$STATE/ssh_config" hlm-deploy 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api rm -f /tmp/r4-links.jsonl' </dev/null
```

**Rollback** (plan §6; triggers in §6 of the plan). In order of reach:

1. **Links only.** Project-WIDE: it closes EVERY live `by=backfill` link of hlmemo in ONE
   `link_supersede` event, not only the links of one apply event. `--project` is required (without
   it the command exits 64 and the links stay live). Preview first (`counts` above). PASS: exit 0,
   len(links) == the live backfill link count (the third `counts` field), counts unchanged:
   ```sh
   before=$(counts)
   ssh -F "$STATE/ssh_config" hlm-deploy 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api hlm links backfill --project hlmemo --revert --dry-run --json' </dev/null > "$STATE/r4-revert-preview.json"
   python3 -c 'import json,sys; d = json.load(open(sys.argv[1])); n = len(d["links"]); ok = d["preview"] and n == int(sys.argv[2]); print("preview", "PASS" if ok else "FAIL", n, "of", sys.argv[2]); sys.exit(not ok)' "$STATE/r4-revert-preview.json" "${before##*|}"
   test "$(counts)" = "$before" && echo "counts unchanged: $before"
   ssh -F "$STATE/ssh_config" hlm-deploy 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api hlm links backfill --project hlmemo --revert' </dev/null
   ```
2. **Behaviour only** (the R4 code stays; the 0009 cache table stays and affects neither option).
   Rebuild the key file with the step-1 recipe first (every installer run needs `--key-file`).
   `--release-template REF|PATH`: **PATH**, an existing file, is used as it is; otherwise **REF** is
   a commit of THIS checkout and the template is `REF:deploy/llm.env.example` (`git show`). Its
   release marker (checked with `evaluate --release <its HLM_ENV_RELEASE>`), its default fallback,
   its writer and its **caps** are installed as that template says; the installed keys are kept
   (their values are not taken from the key file, but every key that template's profiles name must
   still be in it). Before anything is sent, the template's caps are checked against that release's
   manifest in this checkout (a violation sends nothing).
   - (a) **R3 env** (memory.ask OFF): the R3 template with its caps 1/2/10 (the R4 MONTH 60 would fail
     the R3 manifest's MONTH ≤ 10 and leave the env_switch journal open, R-3). Pass: "RESULT
     librarian PASS llm.env=present release=r3 manifest=r3" and "switch complete" (the journal is
     empty). The R3 template has no `GEMINI_API_KEY` line, so the key leaves `llm.env` (its previous
     copy stays in the newest `llm.env.bak-*`, 0600, until three later installs rotate it out).
     ```sh
     bash deploy/scripts/install_llm_env.sh --state "$STATE" --key-file "$KEYS" --release-template "$R3"
     ```
   - (b) **R4 env with the luna writer** (memory.ask stays ON; the r4 manifest accepts an unset
     writer; probe-writer then probes the head of the research chain, `HLM_PROFILE` (luna), and
     must report it; the price date applies only if that profile carries one). The installed `GEMINI_API_KEY` stays in
     the file (the template keeps its line):
     ```sh
     sed '/^HLM_RESEARCH_WRITER_PROFILE=/d' deploy/llm.env.example > "$KEYDIR/r4-luna.env.example"
     bash deploy/scripts/install_llm_env.sh --state "$STATE" --key-file "$KEYS" --release-template "$KEYDIR/r4-luna.env.example"
     ```
   Then delete the key file again. Back to the Gemini writer: step 3.
3. **Full** (R3 code, image, the R3 `llm.env` snapshot and the pre-R4 quiesced dump):
   ```sh
   PATH="$PWD/$STATE/bin:$PATH" bash deploy/scripts/deploy.sh --rollback hlm-deploy
   ```
   Every write after the deploy is lost, the curated links AND their events included; a later
   redeploy does not bring them back (a re-verified apply is needed). R-2: the rollback holds the
   backup/restore operation lock from before anything stops to its end (a running backup or restore
   refuses it: retry when it has finished); it saves the current database to
   `$HLM_BACKUP_DIR/rollback/hlmemo-rollback-<stamp>.dump`, outside every rotation (delete it by hand
   once the rollback is settled). If it prints **`FAIL CLOSED`**, the destructive phase began and
   that saved database is missing: the writers stay stopped and the journal stays open on purpose.
   Do not re-run blindly: restore a dump you trust with `deploy/backup/restore.sh DUMP --yes`
   (release-state.json `previous_dump`, or the saved database fetched back from S3) or go to 4.
4. **Last resort:** restore the step-0 Hostinger snapshot of VM 2002259 (~30 min; writes after it are
   lost).

**Spend.** The R4 caps are HOUR 3 / DAY 8 / MONTH 60 USD (the template; the owner's decision for the
Gemini test month, D-198) with the guard on; `HLM_RESEARCH_MAX_USD=0.12` bounds one question. The
**outer guard** is the OpenRouter prod key's own provider-side limit of **$50/month** (D-120): an
operator step in the OpenRouter dashboard, not a script. Before the deploy, confirm there that the
prod key's monthly limit is still $50 (reset monthly). Check `hlm_ops.sh status` (`spend_*`,
`writer_fallback_24h`) daily during the test, the ledger against the Google console, and the
writer's price date (R-5) before 2026-12-31.

**Review-77 residuals (D-122/D-123).** D-123 made the tooling hardening an R4 precondition. Status
after the R4 deploy work (detail and test names: `tests/deploy/RESIDUALS.md`):

| # | Finding | Status |
|---|---|---|
| 1 | A resumed deploy publishes a half-switched stack | accepted (attended deploy; read the re-run's output) |
| 2 | Backup timer / restore race a rollback or a recovery | closed for rollback (R-2: one operation lock, held to the end); accepted for a resumed deploy's recovery (timer paused in the window) |
| 3 | Rotation deletes the rollback safety dump; a retry closes the journal without restoring | closed (R-2: `rollback/` tier, journalled dumps protected, FAIL CLOSED recovery) |
| 4 | Install and deploy/rollback journals can deadlock | accepted: never run `install_llm_env.sh` while `python3 /opt/hlmemo/app/deploy/scripts/release_state.py get /opt/hlmemo deploy_attempt` (or `rollback_in_progress`) prints anything on the host |
| 5 | Persistent image selection not reverted on a resumed deploy's recovery | accepted: after any recovered deploy compare `HLM_IMAGE` in prod.env with the running api |
| 6 | Fingerprint ignores budget/guard fields, does not require both services | closed (R-4) |
| 7 | R3-REHEARSAL.md stale | not carried over: this section and the plan's §2.2 are the procedure |
| 8 | Extra fallback overrides / an unreadable env file fail open | accepted (read the printed per-task fallbacks) |
| 9 | Secret orphan windows (snapshot/tmp copies after a kill) | accepted (0600 in /etc/hlmemo; after an interrupted run delete `llm.env.*` strays release-state.json does not name) |
| 10 | A preserved wrong key passes (unauthenticated probe) | closed for the writing profile (R-14 probe-writer: the Gemini writer, or the research primary when the writer is unset); accepted for the other OpenRouter profiles (fallbacks, librarian tasks) |

**Convergence (D-116, review 75).** Every step is journalled in `release-state.json` first, so a
kill anywhere converges on a re-run of the same command:
- The snapshot is taken only when its provenance is proven: the non-secret fingerprint of the file
  on disk (release marker, D-094 mapping, rewrite/cap switches; R4 R-4: memory.ask's writer, answer
  settings, timeouts and prose limit, the caps and the guard switch) must equal what the api AND the
  librarian containers were created with, and both must report; otherwise deploy and rollback stop
  before anything changes ("finish the env switch"). A cap edited by hand in `llm.env` therefore
  needs `install_llm_env.sh` (it recreates both services) before the next deploy or rollback.
- `install_llm_env.sh` on a deployed host is ONE step under the deploy lock: journal
  (`env_switch`), write `llm.env`, recreate `librarian api` together, `evaluate --release <marker>`
  (the template's `HLM_ENV_RELEASE`: r3 or r4) against the file, clear the journal. If it is interrupted, deploy and rollback refuse until the
  same `install_llm_env.sh` command is re-run; the re-run finishes the step. It refuses on a
  checkout that predates R3 (Order B). It keeps the operator's hand-edited caps and key in the
  installed `llm.env` (D-121); `--reset-operator-values` replaces them with the template's caps and
  the `--key-file` key; `--release-template REF|PATH` (R4 R-3) installs that template's caps and
  keeps the installed keys.
- A deploy re-run of the published ref (same ref) only verifies: the rollback pair and its llm.env
  snapshot stay. A pair of a release to itself is never published.
- The deploy-attempt journal (`deploy_attempt`, with a 0600 copy of the rendered previous model)
  is written after the quiesced dump, before migration and the new stack. A re-run of the same ref
  after a kill verifies a running new stack and completes the publish with the recorded tuple, or
  recovers the previous stack with it. Deploying another ref, or a rollback, is refused meanwhile.
- A rollback journals its FIRST safety dump and the start of the destructive phase before the
  database changes; a retry reuses that dump. `--accept-release` refuses while a rollback is
  unfinished and always checks that the running api is the current release.
- R4 R-2: the rollback holds the backup/restore operation lock (`$HLM_BACKUP_DIR/.operation.flock`)
  to its end; its safety dump lives in `$HLM_BACKUP_DIR/rollback/`, outside every rotation, and
  rotation/pruning never delete a dump `release-state.json` references; after the destructive phase
  began, a missing safety dump stops the recovery FAIL CLOSED (journal open, writers stopped).
- Secret-bearing copies (`llm.env.release-*`, the attempt's model) are journalled in
  `pending_cleanup` in the same write that stops needing them and deleted by the next lock holder,
  idempotently.

**Spend of an R3 env (D-121).** The owner's production target is at most $10/month: the R3 template
(c98ec0a) sets `HLM_LLM_BUDGET_MONTH_USD=10`, `DAY=2`, `HOUR=1`, the guard on. The R3 manifest requires every cap
present, `HLM_LLM_BUDGET_DISABLED=false`, month at most 10 and day/hour at most month; the operator
may edit the caps and the key in `/etc/hlmemo/llm.env` by hand (then re-run `install_llm_env.sh`,
which keeps them and recreates both services).

### B3 release (D-118 write-time updates): forward-only data

B3 adds no migration and no table, but it writes NEW event content: a `memory.write` event can carry
`resolved.updates[].mutations` (span revisions, closes and `supersedes` links of the writer's own
updates), and a reversal is a `librarian` event with op `revert_write_update`. Only B3+ code knows
these records. That makes the data **forward-only** once the first update has been written:

- **The supported rollback** (`deploy.sh --rollback`) restores the quiesced pre-upgrade dump. It is
  safe, but EVERY write after the B3 deploy is lost (updates, reversals and ordinary memories alike),
  exactly like the R4 "Full" rollback above.
- **Never run pre-B3 code on a database that holds B3 events.** The old replay
  (`rebuild_projections`) skips a write's `resolved.updates`, so a rebuild silently drops every
  revision, close and `supersedes` link an update made (projections and events disagree), and it
  cannot apply a reversal's `version_reopen` at all (`unknown mutation`). Old read code also does not
  know the self-links and pinned links the updates leave behind. Rolling back the image while keeping
  the data is therefore NOT a recovery path.
- **Recovery after B3 rolls forward:** fix the defect on B3+ code, or undo one update with its
  compensating event (`python -m hlmemo.ops librarian revert-update EVENT --item I --update K
  --reason TEXT`), which keeps the history replayable.

Decide the release with this in mind: after the first update has been written, the only way back to
pre-B3 code is the pre-upgrade dump, at the price of every later write.

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

### W0a is a one-way door (D-065)

After a successful W0a cutover there is **no downgrade to a pre-W0 release**: that code ignores
`HLM_REGISTRATION_MODE` and would reopen public registration. `deploy.sh --rollback` refuses any
target whose tree lacks `alembic/versions/0005_w0_access.py` or `src/hlmemo/ops`, unconditionally.
(A deployment that fails *before* its cutover completes is still recovered automatically to the
previous stack with its pre-cutover env, secrets included.) **Disaster recovery from a bad W0+
release rolls FORWARD:** deploy a good W0+ release (`deploy.sh hlmdeploy@SERVER "$(git rev-parse REF)"`), then restore the recorded
dump (`release-state.json` `previous_dump`, or any pre-upgrade/daily dump) with
`bash deploy/backup/restore.sh DUMP --yes`; restore migrates the dump to the W0+ head.

### Rollback between W0+ releases (`deploy.sh --rollback`)

```sh
bash deploy/scripts/deploy.sh --rollback hlmdeploy@SERVER        # detached, same log/status/lock
bash deploy/scripts/deploy.sh --accept-release hlmdeploy@SERVER  # deletes the retired-secret backups
```

Both first verify that the **running** api's image revision label equals `release-state.json`
`current_ref`; on a mismatch they refuse (finish or re-run the interrupted deployment first).
`--rollback` copies its helpers from the current release's git objects into a private temp dir,
validates the previous commit (W0+), its quiesced dump and its image (by recorded ID) and renders
the previous Compose model pinned to that ID, all before stopping anything. It then records the
attempt in the state, stops writers, saves the current database (`backup.sh --rollback-safety`), checks out the
previous commit, publishes its image, restores its `llm.env` snapshot (D-111) and its dump and
starts it. If a step fails, the saved
database is restored and the current release restarted (the saved dump is used only for that; it
lives in `$HLM_BACKUP_DIR/rollback/`, outside every rotation, until deleted by hand; R4 R-2: after the
destructive phase began, a recovery that finds it missing stops FAIL CLOSED instead: writers stay
stopped, nothing restarts, the journal stays open). The whole run holds the backup/restore operation
lock, so the backup timer and `restore.sh` wait (and a rollback refuses while they run). If the runner is killed, the recorded attempt lets the same
`--rollback` command be re-run to completion. Success consumes the pair in one atomic state write
plus a `derive` that rewrites or deletes the legacy marker files. `--accept-release` deletes every
env backup ever recorded (`retired_backups`, including those of failed deployments) and, when the
current release is W0+ (D-065), any unrecorded `*.pre-w0-*` beside the env files; keep them until
then, never delete them by hand.

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
