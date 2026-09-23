#!/usr/bin/env bash
# First deployment of a purchased Ubuntu 24.04 or 26.04 VPS in ONE command (RUNBOOK "Two-command deploy").
# Operator workstation only. Idempotent: re-running converges (bootstrap re-run, same secrets,
# redeploy of the same pushed SHA). Secrets are generated locally, uploaded over SSH stdin and
# never printed; the local copy lives in deploy/.local/<host>/ (gitignored, 0700/0600).
# Remote commands are single-quoted on purpose: they expand on the server.
# shellcheck disable=SC2016
set -Eeuo pipefail
# Empty "${array[@]}" under set -u needs bash >= 4.4 (macOS /bin/bash is 3.2: brew install bash).
((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4))) || { echo "${0##*/}: bash >= 4.4 required" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: first_deploy.sh --host IP --domain FQDN|sslip [options]

  --host IP             server address (root SSH with --ssh-key must work on a fresh host)
  --domain FQDN|sslip   public hostname; "sslip" derives hlm.<ip-with-dashes>.sslip.io
  --acme-email MAIL     recorded in prod.env (HLM_ACME_EMAIL); not yet consumed by the Caddyfile
  --tls acme|internal   default acme; internal is REHEARSAL-ONLY (loopback host)
  --ssh-key PATH        private key (default ~/.ssh/hlmemo_deploy_ed25519; PATH.pub is installed)
  --admin-cidr CIDR     restrict SSH ingress (must include this workstation's source address)
  --host-fingerprint FP expected SSH host key (SHA256:...) from the provider console;
                        without it the first contact is trust-on-first-use and FP is printed
  --deploy-user USER    default hlmdeploy
  --repo URL            default: origin converted to https (server needs no Git credentials)
  --secrets-only        generate/refresh the local secret set and exit (no SSH)
  --dry-run             local checks and the plan only; no SSH, no files written
REHEARSAL-ONLY (local VM; refused for non-loopback hosts):
  --ssh-port N          SSH port forwarded to the guest's port 22
  --public-port N       host port forwarded to the guest's 443 (printed URL only)
  --domain localhost    together with --tls internal
  --repo git://127.0.0.1[:PORT]/PATH  unpushed tooling served through an SSH reverse tunnel;
                        HEAD must then be on the "main" of the checkout's own origin
EOF
}

die() { printf 'first_deploy: %s\n' "$*" >&2; exit 1; }
usage_die() { printf 'first_deploy: %s\n' "$*" >&2; usage >&2; exit 64; }
step() { printf '\n== [%4ss] %s\n' "$SECONDS" "$*"; }

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
host='' domain='' acme_email='' tls=acme ssh_key=~/.ssh/hlmemo_deploy_ed25519 admin_cidr=''
host_fp='' deploy_user=hlmdeploy repo='' ssh_port=22 public_port=443 dry_run=0 secrets_only=0
while (($#)); do
  case $1 in
    --host|--domain|--acme-email|--tls|--ssh-key|--admin-cidr|--host-fingerprint|--deploy-user|--repo|--ssh-port|--public-port)
      (($# >= 2)) || usage_die "$1 needs a value"
      case $1 in
        --host) host=$2 ;; --domain) domain=$2 ;; --acme-email) acme_email=$2 ;;
        --tls) tls=$2 ;; --ssh-key) ssh_key=$2 ;; --admin-cidr) admin_cidr=$2 ;;
        --host-fingerprint) host_fp=$2 ;; --deploy-user) deploy_user=$2 ;; --repo) repo=$2 ;;
        --ssh-port) ssh_port=$2 ;; --public-port) public_port=$2 ;;
      esac
      shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --secrets-only) secrets_only=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage_die "unknown argument: $1" ;;
  esac
done

# ---------------------------------------------------------------- validation (no side effects)
[[ -n $host ]] || usage_die '--host is required'
is_ipv4() { [[ $1 =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] &&
  ((BASH_REMATCH[1] <= 255 && BASH_REMATCH[2] <= 255 && BASH_REMATCH[3] <= 255 && BASH_REMATCH[4] <= 255)); }
[[ $host =~ ^[A-Za-z0-9][A-Za-z0-9.:-]*$ ]] || usage_die 'invalid --host'
loopback=0
[[ $host == 127.0.0.1 || $host == localhost || $host == ::1 ]] && loopback=1
[[ $tls == acme || $tls == internal ]] || usage_die '--tls must be acme or internal'
[[ $ssh_port =~ ^[0-9]{1,5}$ && $public_port =~ ^[0-9]{1,5}$ ]] || usage_die 'ports must be numeric'
if ((!loopback)); then
  [[ $tls == acme ]] || usage_die '--tls internal is rehearsal-only (loopback host); public hosts need acme'
  ((ssh_port == 22)) || usage_die '--ssh-port is rehearsal-only; bootstrap supports SSH on port 22'
  ((public_port == 443)) || usage_die '--public-port is rehearsal-only'
fi
case $domain in
  '') usage_die '--domain is required' ;;
  sslip)
    is_ipv4 "$host" || usage_die '--domain sslip needs an IPv4 --host'
    domain="hlm.${host//./-}.sslip.io" ;;
  localhost)
    ((loopback)) && [[ $tls == internal ]] || usage_die '--domain localhost is rehearsal-only (loopback host + --tls internal)' ;;
  *) [[ $domain =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$ ]] || usage_die "invalid --domain: $domain" ;;
esac
[[ -z $acme_email || $acme_email =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || usage_die 'invalid --acme-email'
[[ $deploy_user =~ ^[a-z_][a-z0-9_-]{0,30}$ && $deploy_user != root ]] || usage_die 'invalid --deploy-user'
[[ -z $host_fp || $host_fp =~ ^SHA256:[A-Za-z0-9+/]{43}=?$ ]] || usage_die '--host-fingerprint must look like SHA256:...'
if [[ -n $repo && $repo == git://* ]]; then
  # REHEARSAL-ONLY: a git daemon on this workstation reached through `ssh -R` from the guest.
  ((loopback)) || usage_die '--repo git:// is rehearsal-only (loopback host); public hosts fetch https'
  [[ $repo =~ ^git://127\.0\.0\.1(:[0-9]{1,5})?/[A-Za-z0-9._/-]+$ ]] || usage_die 'rehearsal --repo must be git://127.0.0.1[:PORT]/PATH'
fi
ssh_key=${ssh_key/#\~/$HOME}
url="https://$domain"
((public_port == 443)) || url="https://$domain:$public_port"

# One state directory per SSH endpoint: rehearsal VMs share 127.0.0.1 and differ by port.
state_name=$host
((ssh_port == 22)) || state_name=$host-$ssh_port
state=${HLM_LOCAL_STATE_DIR:-$REPO_ROOT/deploy/.local}/$state_name
secrets=$state/secrets

# ---------------------------------------------------------------- local secrets
# RUNBOOK procedure: four independent `openssl rand -hex 32` values, the DB password shared by
# db.env and app.env's DSN. Existing files are never regenerated (a new DB password would
# lock out the existing volume). Values only ever pass through bash variables and printf.
template() { # template NAME -> content of deploy/NAME at the deployed SHA (or working tree)
  if [[ -n ${sha:-} ]] && git -C "$REPO_ROOT" cat-file -e "$sha:deploy/$1" 2>/dev/null; then
    git -C "$REPO_ROOT" show "$sha:deploy/$1"
  else
    cat "$REPO_ROOT/deploy/$1"
  fi
}
write_private() { # write_private PATH CONTENT
  local tmp
  tmp=$(mktemp "$1.XXXXXX")
  chmod 0600 "$tmp"
  printf '%s\n' "$2" > "$tmp"
  mv -f "$tmp" "$1"
}
set_key() { # set_key CONTENT KEY VALUE -> CONTENT with KEY=VALUE (replaced or appended)
  local out='' line found=0
  while IFS= read -r line || [[ -n $line ]]; do
    if [[ $line == "$2="* ]]; then line="$2=$3"; found=1; fi
    out+=$line$'\n'
  done <<< "$1"
  ((found)) || out+="$2=$3"$'\n'
  printf '%s' "${out%$'\n'}"
}
generate_secrets() {
  umask 077
  mkdir -p "$secrets"
  chmod 0700 "$state" "$secrets"
  local db admin registration cursor content
  if [[ ! -f $secrets/db.env || ! -f $secrets/app.env || ! -f $secrets/api.env ]]; then
    if [[ -e $secrets/db.env || -e $secrets/app.env || -e $secrets/api.env ]]; then
      die "partial secret set in $secrets; restore the missing file or move the directory aside"
    fi
    db=$(openssl rand -hex 32); admin=$(openssl rand -hex 32)
    registration=$(openssl rand -hex 32); cursor=$(openssl rand -hex 32)
    content=$(template db.env.example); write_private "$secrets/db.env" "${content//CHANGE_ME_DATABASE/$db}"
    content=$(template app.env.example); write_private "$secrets/app.env" "${content//CHANGE_ME_DATABASE/$db}"
    content=$(template api.env.example)
    content=${content//CHANGE_ME_ADMIN/$admin}
    content=${content//CHANGE_ME_REGISTRATION/$registration}
    write_private "$secrets/api.env" "${content//CHANGE_ME_CURSOR/$cursor}"
    write_private "$state/admin.token" "$admin"
    write_private "$state/registration.secret" "$registration"
    unset db admin registration cursor
    echo "generated new secrets in $secrets (values not shown)"
  else
    echo "reusing existing secrets in $secrets"
  fi
  grep -q CHANGE_ME "$secrets"/{db,app,api}.env && die "unreplaced CHANGE_ME placeholder in $secrets"
  [[ -f $secrets/backup.env ]] || write_private "$secrets/backup.env" "$(template backup.env.example)"
  [[ -f $state/admin.token && -f $state/registration.secret ]] || die "missing $state/admin.token or registration.secret"
  content=$(template .env.prod.example)
  [[ ! -f $secrets/prod.env ]] || content=$(<"$secrets/prod.env")
  content=$(set_key "$content" HLM_DOMAIN "$domain")
  content=$(set_key "$content" HLM_TLS_MODE "$tls")
  [[ -z $acme_email ]] || content=$(set_key "$content" HLM_ACME_EMAIL "$acme_email")
  write_private "$secrets/prod.env" "$content"
  chmod 0600 "$secrets"/*.env "$state/admin.token" "$state/registration.secret"
}

# ---------------------------------------------------------------- git: deploy only pushed code
default_repo() {
  local origin
  origin=$(git -C "$REPO_ROOT" remote get-url origin)
  case $origin in
    git@github.com:*) origin="https://github.com/${origin#git@github.com:}" ;;
    ssh://git@github.com/*) origin="https://github.com/${origin#ssh://git@github.com/}" ;;
  esac
  [[ $origin == *.git ]] || origin="$origin.git"
  printf '%s\n' "$origin"
}
if ! git -C "$REPO_ROOT" fetch --quiet origin main; then
  ((dry_run || secrets_only)) || die 'git fetch origin main failed'
  echo 'WARNING: git fetch origin main failed; using the cached origin/main' >&2
fi
sha=$(git -C "$REPO_ROOT" rev-parse HEAD)
pushed=1
git -C "$REPO_ROOT" merge-base --is-ancestor HEAD origin/main || pushed=0
repo=${repo:-$(default_repo)}
if [[ $repo != git://* ]]; then
  [[ $repo =~ ^https://[A-Za-z0-9._/:@-]+$ ]] || usage_die "repository must be an https URL without credentials: $repo"
  [[ $repo != *@* ]] || usage_die 'repository URL must not embed credentials'
fi

if ((secrets_only)); then
  generate_secrets
  exit 0
fi

if ((dry_run)); then
  cat <<EOF
DRY RUN (no SSH, nothing written)
  host=$host ssh_port=$ssh_port deploy_user=$deploy_user tls=$tls
  domain=$domain url=$url
  release=$sha repository=$repo
  state=$state (0700; secrets/ + admin.token + registration.secret, mode 0600)
PLAN:
  1. pin the SSH host key in $state/known_hosts (${host_fp:-trust-on-first-use, fingerprint printed})
  2. preflight: Ubuntu 24.04 (noble) or 26.04 (resolute), >= 7 GiB RAM, >= 2 vCPU (abort otherwise)
  3. bootstrap.sh@$sha as root (prepare), then --finalize-ssh as $deploy_user; re-runs use sudo
  4. generate secrets locally (RUNBOOK procedure), upload to /etc/hlmemo ($deploy_user, 0600)
  5. deploy.sh $deploy_user@host $sha $repo
  6. print URL, admin token path and next steps
EOF
  ((pushed)) || echo "BLOCKER: HEAD $sha is not on origin/main; push it first (a real run refuses)."
  [[ -f $ssh_key && -f $ssh_key.pub ]] || echo "BLOCKER: missing $ssh_key or $ssh_key.pub"
  if ((loopback)); then echo 'REHEARSAL MODE: loopback host; rehearsal-only affordances enabled.'; fi
  exit 0
fi

((pushed)) || die "HEAD $sha is not pushed to origin/main; push first (the server fetches from $repo)"
[[ $(git -C "$REPO_ROOT" rev-parse origin/main) == "$sha" ]] ||
  echo "NOTE: deploying HEAD $sha, which is behind origin/main $(git -C "$REPO_ROOT" rev-parse --short origin/main)"
[[ -f $ssh_key && -f $ssh_key.pub ]] || die "missing SSH key pair $ssh_key(.pub)"
pubkey=$(<"$ssh_key.pub")
[[ $pubkey =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521))\ [A-Za-z0-9+/=]+(\ [A-Za-z0-9._@+-]*)?$ ]] ||
  die "$ssh_key.pub is not a single OpenSSH public key"
for tool in ssh ssh-keyscan ssh-keygen openssl git python3; do
  command -v "$tool" >/dev/null || die "missing local command: $tool"
done

umask 077
mkdir -p "$state/logs" "$state/bin"
chmod 0700 "$state" "$state/logs"
log=$state/logs/first_deploy-$(date -u +%Y%m%dT%H%M%SZ).log
exec > >(tee -a "$log") 2>&1
echo "first_deploy: host=$host domain=$domain tls=$tls release=$sha log=$log"
((loopback)) && echo 'REHEARSAL MODE: loopback host; rehearsal-only affordances (--ssh-port/--public-port/internal TLS) active.'

# ---------------------------------------------------------------- SSH plumbing
# Every SSH call (including deploy.sh's) goes through one private config: pinned known_hosts,
# this key only, strict host-key checking. deploy.sh finds the wrappers first on PATH.
cat > "$state/ssh_config" <<EOF
Host hlm-root hlm-deploy
  HostName $host
  Port $ssh_port
  IdentityFile $ssh_key
  IdentitiesOnly yes
  UserKnownHostsFile $state/known_hosts
  StrictHostKeyChecking yes
  BatchMode yes
  ConnectTimeout 15
  ServerAliveInterval 15
Host hlm-root
  User root
Host hlm-deploy
  User $deploy_user
EOF
for tool in ssh scp; do
  printf '#!/bin/sh\nexec /usr/bin/%s -F %q "$@"\n' "$tool" "$state/ssh_config" > "$state/bin/$tool"
  chmod 0700 "$state/bin/$tool"
done
rssh() { /usr/bin/ssh -F "$state/ssh_config" "$@"; }

step 'SSH host key'
if [[ ! -s $state/known_hosts ]]; then
  scan=$(ssh-keyscan -p "$ssh_port" -t ed25519 "$host" 2>/dev/null) || true
  [[ -n $scan ]] || die "no ed25519 host key from $host:$ssh_port (host down or SSH blocked?)"
  fp=$(ssh-keygen -lf - <<< "$scan" | awk '{print $2}')
  if [[ -n $host_fp ]]; then
    [[ $fp == "$host_fp" ]] || die "host key $fp does not match --host-fingerprint $host_fp; NOT trusting it"
    echo "host key verified: $fp"
  else
    echo "TRUST-ON-FIRST-USE: pinned host key $fp; compare it with the provider console now."
  fi
  printf '%s\n' "$scan" > "$state/known_hosts"
else
  echo "using pinned $state/known_hosts"
fi

step 'Access detection'
# Every refused login counts toward the fail2ban sshd jail (maxretry 5 per 10 min; on 26.04 one
# refused root login alone logs 3 counted lines). Probe only the account expected to work: root
# on a fresh host, the deploy user once this workstation recorded the SSH handover.
root_ok=0 deploy_ok=0
if [[ -f $state/ssh-handover-done ]]; then
  rssh hlm-deploy true </dev/null 2>/dev/null && deploy_ok=1
  ((deploy_ok)) || { rssh hlm-root true </dev/null 2>/dev/null && root_ok=1; }
else
  rssh hlm-root true </dev/null 2>/dev/null && root_ok=1
  ((root_ok)) || { rssh hlm-deploy true </dev/null 2>/dev/null && deploy_ok=1; }
fi
root_state='not probed' deploy_state='not probed'
((root_ok)) && root_state=yes
((deploy_ok)) && deploy_state=yes
((root_ok || deploy_ok)) || { root_state=no; deploy_state=no; }
echo "root login: $root_state; $deploy_user login: $deploy_state"
((root_ok || deploy_ok)) || die "cannot log in as root or $deploy_user with $ssh_key (check the key given to the provider)"
admin_target=hlm-root
((deploy_ok)) && admin_target=hlm-deploy

step 'Preflight'
facts=$(rssh "$admin_target" '. /etc/os-release; printf "%s %s %s %s %s\n" "$ID" "$VERSION_ID" "${VERSION_CODENAME:-${UBUNTU_CODENAME:-none}}" "$(nproc)" "$(awk "/^MemTotal:/ {print \$2}" /proc/meminfo)"; printf "sudo=%s coreutils=%s\n" "$(readlink -f "$(command -v sudo)")" "$(ls --version 2>/dev/null | head -n 1)"' </dev/null)
read -r os_id os_version os_codename cpus mem_kb <<< "${facts%%$'\n'*}"
echo "os=$os_id $os_version ($os_codename) vcpu=$cpus mem=$((mem_kb / 1024)) MiB; ${facts#*$'\n'}"
# Same supported set as bootstrap.sh (which re-checks on the host with the same pairs).
case $os_id:$os_version:$os_codename in
  ubuntu:24.04:noble|ubuntu:26.04:resolute) ;;
  *) die "host runs $os_id $os_version ($os_codename); Ubuntu 24.04 or 26.04 is required. Reinstall the VPS image." ;;
esac
((cpus >= 2)) || die "host has $cpus vCPU; at least 2 are required (D-042 target 2 vCPU / 8 GB)"
((mem_kb >= 7 * 1024 * 1024)) || die "host has $((mem_kb / 1024)) MiB RAM; at least 7 GiB is required (D-042 target 8 GB)"

step 'Bootstrap'
bootstrap_src=$(git -C "$REPO_ROOT" show "$sha:deploy/bootstrap.sh")
cidr_args=''
[[ -z $admin_cidr ]] || printf -v cidr_args ' --admin-cidr %q' "$admin_cidr"
run_bootstrap() { # run_bootstrap TARGET SUDO_PREFIX prepare|finalize
  local target=$1 sudo_prefix=$2 args cmd
  if [[ $3 == prepare ]]; then args='--ssh-key "$d/operator.pub"'$cidr_args; else args=--finalize-ssh; fi
  printf -v cmd 'set -e; d=$(mktemp -d); trap '\''rm -rf "$d"'\'' EXIT; cat > "$d/bootstrap.sh"; printf "%%s\\n" %q > "$d/operator.pub"; %s bash "$d/bootstrap.sh" --deploy-user %q %s' \
    "$pubkey" "$sudo_prefix" "$deploy_user" "$args"
  rssh "$target" "$cmd" <<< "$bootstrap_src"
}
# Root still accepting logins means the handover is not finalized (PermitRootLogin no refuses it).
finalized=0
if ((deploy_ok)); then
  rssh hlm-deploy 'sudo grep -qx "PermitRootLogin no" /etc/ssh/sshd_config.d/00-hlmemo.conf' </dev/null 2>/dev/null && finalized=1
fi
t0=$SECONDS
if ((root_ok && !finalized)); then
  run_bootstrap hlm-root '' prepare
else
  run_bootstrap hlm-deploy 'sudo --preserve-env=SSH_CONNECTION' prepare
fi
echo "bootstrap prepare: $((SECONDS - t0)) s"
rssh hlm-deploy 'docker compose version >/dev/null && id -nG | grep -qw docker' </dev/null ||
  die "a new $deploy_user login failed after preparation; keep the provider console open and inspect"
if ((!finalized)); then
  run_bootstrap hlm-deploy 'sudo --preserve-env=SSH_CONNECTION,SSH_USER_AUTH' finalize
  rssh hlm-deploy 'systemctl is-active docker fail2ban unattended-upgrades >/dev/null' </dev/null ||
    die 'services inactive after finalize'
  # Prove the refusal without a refused login (which would feed fail2ban): the effective sshd
  # configuration for root from this workstation's address, read inside a new deploy session.
  rssh hlm-deploy 'a=${SSH_CONNECTION%% *}; t=$(sudo /usr/sbin/sshd -T -C "user=root,host=localhost,addr=$a") &&
    grep -qx "permitrootlogin no" <<< "$t" && grep -qx "passwordauthentication no" <<< "$t" &&
    grep -qx "kbdinteractiveauthentication no" <<< "$t"' </dev/null ||
    die 'effective sshd config still allows root or password login after --finalize-ssh'
  echo 'root and password SSH login disabled (effective sshd config); deploy-user key login verified in a new session'
else
  echo 'SSH handover already finalized (root login disabled); kept'
fi
: > "$state/ssh-handover-done"

step 'Secrets'
generate_secrets
install_remote() { # install_remote NAME: upload over stdin; never overwrite a differing secret
  local name=$1 cmd
  printf -v cmd 'set -eu; umask 077; t=$(mktemp); trap '\''rm -f "$t"'\'' EXIT; cat > "$t"; f=/etc/hlmemo/%s
if [ ! -e "$f" ]; then install -m 0600 "$t" "$f"; echo "installed $f"
elif [ %q = prod.env ]; then
  for k in HLM_DOMAIN HLM_TLS_MODE; do
    [ "$(grep "^$k=" "$t")" = "$(grep "^$k=" "$f")" ] || { echo "$f has a different $k; edit it deliberately (see RUNBOOK)" >&2; exit 3; }
  done; echo "kept $f (domain/TLS match; HLM_IMAGE is managed by deploy.sh)"
elif cmp -s "$t" "$f"; then echo "unchanged $f"
else echo "$f differs from the local copy; refusing to replace a live secret (rotate deliberately)" >&2; exit 3; fi
chmod 0600 "$f"' "$name" "$name"
  rssh hlm-deploy "$cmd" < "$secrets/$name"
}
for name in db.env app.env api.env backup.env prod.env; do install_remote "$name"; done
rssh hlm-deploy 'stat -c "%n %U:%G %a" /etc/hlmemo /etc/hlmemo/*.env' </dev/null

cat > "$state/deploy.conf" <<EOF
# Non-secret facts written by first_deploy.sh; read by remote_gates.sh.
HOST=$host
SSH_PORT=$ssh_port
DEPLOY_USER=$deploy_user
DOMAIN=$domain
TLS=$tls
URL=$url
RELEASE=$sha
REPOSITORY=$repo
EOF

step "Deploy $sha"
t0=$SECONDS
deploy_rc=0
PATH="$state/bin:$PATH" bash "$REPO_ROOT/deploy/scripts/deploy.sh" hlm-deploy "$sha" "$repo" || deploy_rc=$?
echo "deploy.sh exit=$deploy_rc after $((SECONDS - t0)) s"
current=$(rssh hlm-deploy 'cat /opt/hlmemo/current-ref 2>/dev/null || true' </dev/null)
if ((deploy_rc != 0)); then
  [[ $current == "$sha" ]] || die "deployment failed before cutover (current-ref=${current:-none}); see the remote log above"
  if [[ $tls == internal ]]; then
    # REHEARSAL-ONLY: remote-deploy.sh verifies https://DOMAIN/ready from the host with system
    # CAs; Caddy's internal CA is unknown there. Trust it on the rehearsal guest only, then re-check.
    step 'REHEARSAL-ONLY: trust Caddy internal CA on the guest'
    rssh hlm-deploy 'docker exec "$(docker ps -q --filter label=com.docker.compose.project=hlmemo-prod --filter label=com.docker.compose.service=caddy)" cat /data/caddy/pki/authorities/local/root.crt' </dev/null > "$state/caddy-internal-root.crt"
    rssh hlm-deploy 'sudo tee /usr/local/share/ca-certificates/hlmemo-caddy-internal-rehearsal.crt >/dev/null && sudo chmod 0644 /usr/local/share/ca-certificates/hlmemo-caddy-internal-rehearsal.crt && sudo update-ca-certificates >/dev/null' < "$state/caddy-internal-root.crt"
    printf -v cmd 'curl --fail --silent --show-error --max-time 20 https://%s/ready >/dev/null && echo "guest strict-TLS /ready OK"' "$domain"
    rssh hlm-deploy "$cmd" </dev/null || die 'internal-TLS readiness still failing'
  else
    die "internally healthy release $sha is running, but public https://$domain/ready failed (DNS A record, provider firewall 80/443, ACME). Fix it and re-run this command."
  fi
fi
if [[ $tls == internal && ! -s $state/caddy-internal-root.crt ]]; then
  rssh hlm-deploy 'docker exec "$(docker ps -q --filter label=com.docker.compose.project=hlmemo-prod --filter label=com.docker.compose.service=caddy)" cat /data/caddy/pki/authorities/local/root.crt' </dev/null > "$state/caddy-internal-root.crt"
fi
[[ $(rssh hlm-deploy 'cat /opt/hlmemo/current-ref' </dev/null) == "$sha" ]] || die 'current-ref does not match the deployed SHA'

step 'Done'
insecure=''
[[ $tls == internal ]] && insecure=' --insecure'
cat <<EOF
URL:              $url   (MCP endpoint: $url/mcp)
Release:          $sha
Admin token:      $state/admin.token (0600; value not shown)
Registration:     $state/registration.secret
Server secrets:   /etc/hlmemo/*.env on the host; local copy $secrets/ (keep a backup in your secret manager)
SSH:              ssh -F $state/ssh_config hlm-deploy   (root login is disabled)
Log:              $log
Total:            ${SECONDS}s
Next steps:
  1. bash deploy/scripts/remote_gates.sh --url $url$insecure --admin-token-file $state/admin.token
  2. Enable the daily backup timer and off-host copies (RUNBOOK "Backups and restore").
  3. Connect this Mac: hlm init --server $url/mcp ...; hlm device register; hlm --admin device approve ...
EOF
[[ -z $acme_email ]] || echo "NOTE: HLM_ACME_EMAIL is recorded in prod.env but the current Caddyfile does not consume it yet (Caddy registers ACME without an email)."
