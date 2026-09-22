#!/usr/bin/env bash
# Run on the target via SSH. See RUNBOOK.md for the two-phase SSH handover.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bootstrap.sh --ssh-key PATH [--deploy-user USER] [--admin-cidr CIDR] [--dry-run]
       bootstrap.sh --finalize-ssh [--deploy-user USER] [--dry-run]

Prepare an Ubuntu 24.04 host as root. PATH is one operator OpenSSH public key.
Default deploy user: hlmdeploy. Default SSH ingress: TCP 22 from anywhere.
--admin-cidr restricts the managed SSH rule; must include the current SSH source.
--finalize-ssh disables root/password login only from a verified public-key SSH
session as the deploy user; sudo must preserve SSH_CONNECTION and SSH_USER_AUTH.
--prepare-only installs/configures files but skips services/firewall activation
(for disposable non-systemd container testing; NOT a ready deployment host).
--dry-run prints actions and makes no changes; works on the operator workstation.
EOF
}

die() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }
deploy_user=hlmdeploy
ssh_key=
admin_cidr=
dry_run=0
prepare_only=0
finalize=0
while (($#)); do
  case "$1" in
    --deploy-user|--ssh-key|--admin-cidr)
      (($# >= 2)) || die "$1 needs a value"
      case "$1" in
        --deploy-user) deploy_user=$2 ;;
        --ssh-key) ssh_key=$2 ;;
        --admin-cidr) admin_cidr=$2 ;;
      esac
      shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --prepare-only) prepare_only=1; shift ;;
    --finalize-ssh) finalize=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ $deploy_user =~ ^[a-z_][a-z0-9_-]{0,30}$ && $deploy_user != root ]] || die 'invalid non-root deploy user'
((!finalize || !prepare_only)) || die '--finalize-ssh cannot use --prepare-only'
if ((!finalize)); then
  [[ -n $ssh_key && -f $ssh_key ]] || die '--ssh-key must name a public key file'
  [[ $(awk 'NF {n++} END {print n+0}' "$ssh_key") == 1 ]] || die 'provide exactly one public key'
  grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com) [A-Za-z0-9+/=]+( |$)' "$ssh_key" || die 'invalid OpenSSH public key'
fi

validate_cidr() {
  [[ -z $admin_cidr ]] && return 0
  python3 - "$admin_cidr" "${SSH_CONNECTION:-}" <<'PY'
import ipaddress
import sys

try:
    network = ipaddress.ip_network(sys.argv[1], strict=True)
    if sys.argv[2]:
        source, _, _, port = sys.argv[2].split()
        if port != "22":
            raise ValueError("bootstrap supports SSH on port 22 only")
        if ipaddress.ip_address(source) not in network:
            raise ValueError("admin CIDR excludes the current SSH source")
except ValueError as exc:
    sys.exit(f"bootstrap: {exc}")
PY
}

if ((dry_run)); then
  command -v python3 >/dev/null && validate_cidr
  if ((finalize)); then
    printf '%s\n' 'VERIFY: active deploy-user public-key SSH session; sshd syntax/effective access.' \
      'WRITE: root/password SSH disabled; validate and reload ssh.service, roll back on failure.'
  else
    printf '%s\n' 'VERIFY: Ubuntu 24.04, root, SSH port 22 and operator public key.' \
      'INSTALL: ca-certificates curl git python3 sudo openssh-server ufw fail2ban unattended-upgrades.' \
      'INSTALL: Docker official signed apt repository; docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin (keep installed versions on re-run).'
    printf 'CONFIGURE: user %s, docker/sudo groups, passwordless sudo, append operator public key.\n' "$deploy_user"
    printf 'CREATE: /opt/hlmemo /var/backups/hlmemo /etc/hlmemo; owner %s, mode 0750.\n' "$deploy_user"
    printf 'CONFIGURE: ufw TCP 22 from %s; TCP 80/443 and UDP 443; preserve unrelated rules.\n' "${admin_cidr:-anywhere}"
    printf '%s\n' 'CONFIGURE: fail2ban sshd/systemd, unattended upgrades without automatic reboot.' \
      'CONFIGURE: public-key SSH and ExposeAuthInfo; preserve current login until --finalize-ssh.'
    if ((prepare_only)); then
      printf '%s\n' 'SKIP: systemd services, active firewall and SSH reload (--prepare-only).'
    else
      printf '%s\n' 'ACTIVATE: ufw; enable/start docker, fail2ban, unattended-upgrades; validate/reload SSH.'
    fi
  fi
  exit 0
fi

((EUID == 0)) || die 'run on the target as root (sudo)'
# shellcheck disable=SC1091
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || die 'Ubuntu 24.04 is required'
if [[ -n ${SSH_CONNECTION:-} ]]; then
  [[ ${SSH_CONNECTION##* } == 22 ]] || die 'bootstrap supports SSH on port 22 only'
fi
if [[ -n $admin_cidr && -z ${SSH_CONNECTION:-} ]] && ((!prepare_only)); then
  die '--admin-cidr needs SSH_CONNECTION; preserve it through sudo to verify operator access'
fi
if ((!prepare_only)); then
  [[ -d /run/systemd/system ]] || die 'systemd is required (use --prepare-only only for container tests)'
fi

configure_sshd() {
  local target=/etc/ssh/sshd_config.d/00-hlmemo.conf saved effective root_effective connection
  connection=${SSH_CONNECTION:-127.0.0.1}
  saved=$(mktemp)
  if [[ -f $target ]]; then cp "$target" "$saved"; else rm "$saved"; fi
  install -d -m 0755 /etc/ssh/sshd_config.d /run/sshd
  printf '%s\n' 'PubkeyAuthentication yes' 'ExposeAuthInfo yes' > "$target"
  if ((finalize)); then
    printf '%s\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin no' >> "$target"
  elif [[ -f $saved ]] && grep -qx 'PermitRootLogin no' "$saved"; then
    # A preparation re-run must never undo a completed handover.
    printf '%s\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin no' >> "$target"
  fi
  chmod 0644 "$target"
  if /usr/sbin/sshd -t &&
    effective=$(/usr/sbin/sshd -T -C "user=$deploy_user,host=localhost,addr=${connection%% *}") &&
    root_effective=$(/usr/sbin/sshd -T -C "user=root,host=localhost,addr=${connection%% *}"); then
    if ! grep -qx 'pubkeyauthentication yes' <<< "$effective" ||
       ! grep -qx 'exposeauthinfo yes' <<< "$effective"; then
      printf '%s\n' 'bootstrap: existing SSH rules prevent public-key handover' >&2
    elif ((!finalize)) || { grep -qx 'passwordauthentication no' <<< "$effective" &&
      grep -qx 'kbdinteractiveauthentication no' <<< "$effective" &&
      grep -Eq '^authenticationmethods (any|publickey)$' <<< "$effective" &&
      grep -qx 'permitrootlogin no' <<< "$root_effective"; }; then
      if ((prepare_only)) || systemctl try-reload-or-restart ssh.service; then
        rm -f "$saved"
        return 0
      fi
    fi
  fi
  if [[ -f $saved ]]; then mv "$saved" "$target"; else rm -f "$target"; fi
  if ((!prepare_only)); then systemctl try-reload-or-restart ssh.service || true; fi
  die 'SSH change rejected; restored previous drop-in; retain your current session'
}

if ((finalize)); then
  [[ ${SUDO_USER:-} == "$deploy_user" && -n ${SSH_CONNECTION:-} ]] || die 'finalize through sudo in a new SSH session as the deploy user'
  [[ -n ${SSH_USER_AUTH:-} && -f $SSH_USER_AUTH ]] || die 'new SSH session must expose SSH_USER_AUTH (prepared by bootstrap)'
  grep -Eq '^publickey ' "$SSH_USER_AUTH" || die 'finalize requires a successful public-key SSH login'
  configure_sshd
  printf '%s\n' 'SSH handover complete: root/password login disabled. Keep this session open and verify another login.'
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-upgrade ca-certificates curl git python3 sudo openssh-server ufw fail2ban unattended-upgrades
validate_cidr
ssh-keygen -lf "$ssh_key" >/dev/null || die 'invalid SSH public key'
install -d -m 0755 /etc/apt/keyrings
curl --fail --silent --show-error --location https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable\n' \
  "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y --no-upgrade docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
if ! id "$deploy_user" >/dev/null 2>&1; then
  useradd --create-home --user-group --shell /bin/bash "$deploy_user"
fi
usermod -aG docker,sudo "$deploy_user"
deploy_home=$(getent passwd "$deploy_user" | cut -d: -f6)
deploy_group=$(id -gn "$deploy_user")
[[ $deploy_home == /* && $deploy_home != / && $deploy_home != /root ]] || die 'unsafe deploy home'
install -d -m 0700 -o "$deploy_user" -g "$deploy_group" "$deploy_home/.ssh"
authorized_keys=$deploy_home/.ssh/authorized_keys
touch "$authorized_keys"
key=$(cat "$ssh_key")
grep -qxF "$key" "$authorized_keys" || printf '\n%s\n' "$key" >> "$authorized_keys"
chown "$deploy_user:$deploy_group" "$authorized_keys"
chmod 0600 "$authorized_keys"
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$deploy_user" > /etc/sudoers.d/90-hlmemo-deploy
chmod 0440 /etc/sudoers.d/90-hlmemo-deploy
visudo -cf /etc/sudoers.d/90-hlmemo-deploy
install -d -m 0750 -o "$deploy_user" -g "$deploy_group" /opt/hlmemo /var/backups/hlmemo /etc/hlmemo
printf '%s\n' 'APT::Periodic::Update-Package-Lists "1";' 'APT::Periodic::Unattended-Upgrade "1";' > /etc/apt/apt.conf.d/20auto-upgrades
printf '%s\n' 'Unattended-Upgrade::Automatic-Reboot "false";' > /etc/apt/apt.conf.d/52hlmemo-unattended
printf '%s\n' '[sshd]' 'enabled = true' 'backend = systemd' 'maxretry = 5' 'bantime = 1h' > /etc/fail2ban/jail.d/hlmemo.local

# Add the new SSH allowance before removing our previous one. Never reset UFW.
ufw default deny incoming
ufw default allow outgoing
ufw allow from "${admin_cidr:-any}" to any port 22 proto tcp comment hlmemo-ssh
previous_cidr_file=/etc/hlmemo/bootstrap-ssh-cidr
if [[ -f $previous_cidr_file ]]; then
  previous_cidr=$(cat "$previous_cidr_file")
  if [[ $previous_cidr != "${admin_cidr:-any}" ]]; then
    ufw --force delete allow from "$previous_cidr" to any port 22 proto tcp comment hlmemo-ssh
  fi
fi
printf '%s\n' "${admin_cidr:-any}" > "$previous_cidr_file"
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
configure_sshd
if ((prepare_only)); then
  printf '%s\n' 'PREPARED ONLY: services/firewall activation untested; host is not deployment-ready.'
else
  ufw --force enable
  systemctl enable --now docker fail2ban unattended-upgrades
  systemctl is-active docker fail2ban unattended-upgrades
fi
docker compose version
printf '%s\n' 'Preparation complete. Verify a new deploy-user public-key SSH login, then run --finalize-ssh.'
