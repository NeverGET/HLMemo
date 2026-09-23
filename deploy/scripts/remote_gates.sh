#!/usr/bin/env bash
# End-to-end gates against a deployed HLMemo, run from the operator workstation
# (RUNBOOK "Two-command deploy"). Prints PASS/FAIL per gate; exits non-zero if any gate fails.
# Tokens come from files and reach children only through the environment; never printed.
# Remote commands are single-quoted on purpose: they expand on the server.
# shellcheck disable=SC2016
set -Euo pipefail
# Empty "${array[@]}" under set -u needs bash >= 4.4 (macOS /bin/bash is 3.2: brew install bash).
((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4))) || { echo "${0##*/}: bash >= 4.4 required" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: remote_gates.sh --url https://FQDN[:PORT] [--admin-token-file PATH] [options]

  --admin-token-file P  admin token (0600). Default: the deploy/.local/<host>/ state whose
                        deploy.conf URL matches --url (written by first_deploy.sh)
  --insecure            server uses Caddy's internal CA (rehearsal); TLS is then verified
                        against that CA (fetched over SSH), never skipped for the hlm CLI
  --tls acme|internal   expected issuer (default: deploy.conf TLS, else acme unless --insecure)
  --ssh-config P        default: <state>/ssh_config (host alias hlm-deploy)
  --latency-n N         memory.query samples for the WAN latency gate (default 20)
  --latency-p95-ms MS   p95 ceiling for the latency gate (default 3000)
  --no-drill            skip the backup/restore drill (it restores over live data: only for
                        a fresh deployment or a maintenance window)
  --g7                  ALSO register the real claude/codex/agy CLIs against --url (edits
                        ~/.claude.json, ~/.codex/config.toml, ~/.gemini/config/mcp_config.json
                        after backing them up to deploy/.local/backups/) and run a headless
                        write->query per CLI. Owner-only; prints the restore command.
EOF
}

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
url='' token_file='' insecure=0 tls='' ssh_config='' latency_n=20 p95_max=3000 drill=1 g7=0
while (($#)); do
  case $1 in
    --url|--admin-token-file|--tls|--ssh-config|--latency-n|--latency-p95-ms)
      (($# >= 2)) || { echo "remote_gates: $1 needs a value" >&2; exit 64; }
      case $1 in
        --url) url=$2 ;; --admin-token-file) token_file=$2 ;; --tls) tls=$2 ;;
        --ssh-config) ssh_config=$2 ;; --latency-n) latency_n=$2 ;; --latency-p95-ms) p95_max=$2 ;;
      esac
      shift 2 ;;
    --insecure) insecure=1; shift ;;
    --no-drill) drill=0; shift ;;
    --g7) g7=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "remote_gates: unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done
die() { printf 'remote_gates: %s\n' "$*" >&2; exit 1; }
[[ $url =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?$ ]] || { echo 'remote_gates: --url must be https://HOST[:PORT]' >&2; usage >&2; exit 64; }
url=${url%/}
[[ $latency_n =~ ^[1-9][0-9]*$ && $p95_max =~ ^[1-9][0-9]*$ ]] || { echo 'remote_gates: numeric --latency-* values required' >&2; exit 64; }
if [[ -z $token_file ]]; then
  for conf in "$REPO_ROOT"/deploy/.local/*/deploy.conf; do
    [[ -f $conf ]] && grep -qxF "URL=$url" "$conf" && token_file=$(dirname "$conf")/admin.token && break
  done
  [[ -n $token_file ]] || { echo "remote_gates: no deploy/.local/*/deploy.conf with URL=$url; pass --admin-token-file" >&2; exit 64; }
fi
[[ -f $token_file ]] || die "admin token file not found: $token_file"
mode=$(stat -f %Lp "$token_file" 2>/dev/null || stat -c %a "$token_file")
[[ $mode == 600 || $mode == 400 ]] || die "$token_file must be mode 0600 (is $mode)"
state_dir=$(cd "$(dirname "$token_file")" && pwd)
conf_value() { [[ -f $state_dir/deploy.conf ]] && sed -n "s/^$1=//p" "$state_dir/deploy.conf" | head -1; }
[[ -n $tls ]] || tls=$(conf_value TLS || true)
[[ -n $tls ]] || { if ((insecure)); then tls=internal; else tls=acme; fi; }
[[ $tls == acme || $tls == internal ]] || die '--tls must be acme or internal'
[[ -n $ssh_config ]] || ssh_config=$state_dir/ssh_config
have_ssh=0
[[ -f $ssh_config ]] && have_ssh=1
hostport=${url#https://}
host=${hostport%%:*}
port=443
[[ $hostport == *:* ]] && port=${hostport##*:}

umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$state_dir/logs"
log=$state_dir/logs/remote_gates-$stamp.log
work=$(mktemp -d "$state_dir/gates-work.XXXXXX")
trap 'rm -rf "$work"' EXIT
exec > >(tee -a "$log") 2>&1

HLM_ADMIN_TOKEN=$(<"$token_file")
export HLM_ADMIN_TOKEN
[[ -f $state_dir/registration.secret ]] && { HLM_REGISTRATION_SECRET=$(<"$state_dir/registration.secret"); export HLM_REGISTRATION_SECRET; }
uvrun() { uv run --quiet --frozen --project "$REPO_ROOT" "$@"; }
rssh() { /usr/bin/ssh -F "$ssh_config" hlm-deploy "$@"; }
curl_tls=()
((insecure)) && curl_tls=(-k)

names=() results=() details=()
gate() { # gate NAME PASS|FAIL DETAIL
  names+=("$1"); results+=("$2"); details+=("$3")
  printf '%s %-22s %s\n' "$2" "$1" "$3"
}
started=$SECONDS
echo "remote_gates: url=$url tls=$tls insecure=$insecure ssh=$([[ $have_ssh == 1 ]] && echo "$ssh_config" || echo none) log=$log"

# ------------------------------------------------------------------ G1/G2 edge
code() { curl "${curl_tls[@]}" -s -o /dev/null -w '%{http_code}' --max-time 20 "$url$1"; }
h=$(code /health) r=$(code /ready)
if [[ $h == 200 && $r == 200 ]]; then gate health-ready PASS "/health=$h /ready=$r"; else gate health-ready FAIL "/health=$h /ready=$r"; fi
n=$(code /definitely-not-a-route)
if [[ $n == 404 ]]; then gate unknown-path-404 PASS "status=$n"; else gate unknown-path-404 FAIL "status=$n"; fi

# ------------------------------------------------------------------ G3 TLS issuer
issuer=$(openssl s_client -connect "$host:$port" -servername "$host" </dev/null 2>/dev/null |
  openssl x509 -noout -issuer 2>/dev/null | sed 's/^issuer= *//')
if [[ -z $issuer ]]; then
  gate tls-issuer FAIL 'no certificate presented'
elif [[ $tls == acme ]]; then
  if [[ $issuer == *"Caddy Local Authority"* ]]; then
    gate tls-issuer FAIL "Caddy internal CA while ACME expected: $issuer"
  elif curl -s -o /dev/null --max-time 20 "$url/ready"; then
    gate tls-issuer PASS "publicly trusted: $issuer"
  else
    gate tls-issuer FAIL "not trusted by system CAs: $issuer"
  fi
else
  if [[ $issuer == *"Caddy Local Authority"* ]]; then gate tls-issuer PASS "internal CA as requested (rehearsal): $issuer"
  else gate tls-issuer FAIL "expected Caddy internal CA: $issuer"; fi
fi

# ------------------------------------------------------------------ G4 Postgres closed
if [[ $host == localhost || $host == 127.0.0.1 ]]; then
  # REHEARSAL-ONLY: a loopback URL means a port-forwarded VM; this workstation's own 5432 says
  # nothing about the guest. Probe the guest's primary address instead (from inside the guest).
  if ((have_ssh)); then
    external=$(rssh 'ip=$(ip -4 route get 1.1.1.1 | sed -n "s/.* src \([0-9.]*\).*/\1/p"); if timeout 4 bash -c "</dev/tcp/$ip/5432" 2>/dev/null; then echo "OPEN at guest $ip"; else echo "closed at guest $ip (rehearsal: probed inside the VM)"; fi' </dev/null 2>&1)
  else
    external='not probed (loopback URL, no SSH)'
  fi
else
  external=$(python3 - "$host" <<'PY'
import socket, sys
try:
    socket.create_connection((sys.argv[1], 5432), timeout=4).close()
    print("OPEN")
except OSError as exc:
    print(f"closed ({type(exc).__name__})")
PY
)
fi
inside='ssh unavailable'
if ((have_ssh)); then
  # .Ports lists the image's EXPOSEd "5432/tcp" even when unpublished; only "->" is a host binding.
  inside=$(rssh 'l=$(ss -Hltn "sport = :5432" | wc -l); p=$(docker ps --filter label=com.docker.compose.service=db --format "{{.Ports}}" | tr "," "\n" | grep -e "->" | tr -d " " | paste -sd, -); echo "host-listeners=$l db-published=${p:-none}"' </dev/null 2>&1)
fi
if [[ $external == closed* && ( $have_ssh == 0 || $inside == 'host-listeners=0 db-published=none' ) ]]; then
  gate postgres-closed PASS "tcp $host:5432 $external; $inside"
else
  gate postgres-closed FAIL "tcp $host:5432 $external; $inside"
fi

# ------------------------------------------------------------------ CA for strict TLS clients
ca_file=''
if ((insecure)); then
  ca_file=$state_dir/caddy-internal-root.crt
  if ((have_ssh)); then
    rssh 'docker exec "$(docker ps -q --filter label=com.docker.compose.service=caddy)" cat /data/caddy/pki/authorities/local/root.crt' </dev/null > "$work/ca.crt" 2>/dev/null &&
      [[ -s $work/ca.crt ]] && cp "$work/ca.crt" "$ca_file"
  fi
  [[ -s $ca_file ]] || ca_file=''
fi
tls_env=()
[[ -n $ca_file ]] && tls_env=(SSL_CERT_FILE="$ca_file")

# ------------------------------------------------------------------ G5 neutral probe
probe_state=$work/probe.json
probe() { uvrun python "$REPO_ROOT/docs/bakeoff/r2/judge/probe.py" --base "$url" --state "$probe_state" --project gates "${probe_insecure[@]}" "$@"; }
probe_insecure=()
((insecure)) && probe_insecure=(--insecure)
marker_a="gates-a-$stamp-$RANDOM"
out=$(probe --mode smoke 2>&1); rc=$?
printf '%s\n' "$out" | sed 's/^/    /'
if ((rc == 0)); then
  out=$(probe --mode write-marker --marker "$marker_a" 2>&1 && probe --mode read-marker --marker "$marker_a" 2>&1); rc=$?
  printf '%s\n' "$out" | sed 's/^/    /'
  if ((rc == 0)); then gate probe PASS "smoke + write/read marker $marker_a"; else gate probe FAIL 'marker round trip failed'; fi
else
  gate probe FAIL 'smoke failed'
fi

# ------------------------------------------------------------------ G6 real hlm CLI
cli_dir=$work/hlm-cli
# The fail backend (not null: null silently discards the token) forces credentials.toml in the
# isolated config dir, so the operator's keychain is never touched.
mkdir -p "$cli_dir/cfg"
cli_device="gates-cli-$RANDOM"
hlm() { (cd "$cli_dir" && env HLM_CONFIG_DIR="$cli_dir/cfg" PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring "${tls_env[@]}" \
  uv run --quiet --frozen --project "$REPO_ROOT" hlm "$@"); }
cli_ok=1
if ((insecure)) && [[ -z $ca_file ]]; then
  cli_ok=0; cli_detail='no Caddy CA available for strict TLS (need SSH)'
else
  run_cli() { printf '    $ hlm %s\n' "$*"; local o; o=$(hlm "$@" 2>&1); local c=$?; printf '%s\n' "$o" | sed 's/^/      /'; return $c; }
  run_cli init --server "$url/mcp" --project gates-cli --device-name "$cli_device" || cli_ok=0
  if ((cli_ok)); then
    hlm --admin project list 2>/dev/null | grep -qw gates-cli || run_cli --admin project create gates-cli --name 'Remote gates' || cli_ok=0
  fi
  ((cli_ok)) && { run_cli device register --name "$cli_device" --class ci || cli_ok=0; }
  ((cli_ok)) && { run_cli --admin device approve "$cli_device" --class ci --grant gates-cli:write || cli_ok=0; }
  ((cli_ok)) && { run_cli query 'remote gates context' || cli_ok=0; }
  ((cli_ok)) && { run_cli close --notes "remote gates $stamp against $url" --decision "remote gates $stamp ran" || cli_ok=0; }
  ((cli_ok)) && { run_cli device whoami | grep -q 'gates-cli:write' || cli_ok=0; }
  cli_detail="init/register/approve/query/close/whoami as $cli_device"
fi
if ((cli_ok)); then gate hlm-cli PASS "$cli_detail"; else gate hlm-cli FAIL "${cli_detail:-see log}"; fi

# ------------------------------------------------------------------ G7 WAN latency
lat=$(env "${tls_env[@]}" INSECURE="$insecure" uv run --quiet --frozen --project "$REPO_ROOT" python - "$url" "$probe_state" "$latency_n" <<'PY' 2>&1
import json, os, statistics, sys, time, uuid
import httpx
url, state, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
token = json.load(open(state))["device_token"]
verify = False if (os.environ.get("INSECURE") == "1" and not os.environ.get("SSL_CERT_FILE")) else True
h = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
with httpx.Client(base_url=url, verify=verify, timeout=30) as c:
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "remote-gates", "version": "1"}}})
    r.raise_for_status()
    h["MCP-Protocol-Version"] = r.json()["result"]["protocolVersion"]
    samples, errors = [], 0
    for i in range(n):
        body = {"jsonrpc": "2.0", "id": i + 1, "method": "tools/call", "params": {"name": "memory.query",
                "arguments": {"project": "gates", "query": f"latency probe {uuid.uuid4().hex[:6]}", "token_budget": 1024}}}
        t = time.perf_counter()
        r = c.post("/mcp", headers=h, json=body)
        samples.append((time.perf_counter() - t) * 1000)
        if r.status_code != 200 or r.json().get("result", {}).get("isError"):
            errors += 1
q = statistics.quantiles(samples, n=100, method="inclusive")
print(f"n={n} errors={errors} p50={statistics.median(samples):.0f}ms p95={q[94]:.0f}ms max={max(samples):.0f}ms")
PY
)
if [[ $lat =~ errors=0\ p50=([0-9]+)ms\ p95=([0-9]+)ms ]] && ((BASH_REMATCH[2] <= p95_max)); then
  gate wan-latency PASS "$lat (ceiling p95<=${p95_max}ms)"
else
  gate wan-latency FAIL "${lat//$'\n'/ } (ceiling p95<=${p95_max}ms)"
fi

# ------------------------------------------------------------------ G8 backup/restore drill
if ((!drill)); then
  gate backup-restore SKIP '--no-drill'
elif ((!have_ssh)); then
  gate backup-restore FAIL "no SSH config at $ssh_config"
else
  drill_ok=1 drill_detail=''
  env_prefix='cd /opt/hlmemo/app && export HLM_ENV_FILE=/etc/hlmemo/prod.env &&'
  dump=$(rssh "$env_prefix bash deploy/backup/backup.sh" </dev/null 2>"$work/backup.err" | tail -1)
  sed 's/^/    /' "$work/backup.err"
  [[ $dump =~ ^/[A-Za-z0-9_./-]+\.dump$ ]] || { drill_ok=0; drill_detail="backup failed ($dump)"; }
  marker_b="gates-b-$stamp-$RANDOM"
  if ((drill_ok)); then
    out=$(probe --mode write-marker --marker "$marker_b" 2>&1 && probe --mode read-marker --marker "$marker_b" 2>&1) ||
      { drill_ok=0; drill_detail='post-backup marker write failed'; }
    printf '%s\n' "$out" | sed 's/^/    /'
  fi
  if ((drill_ok)); then
    printf -v cmd '%s bash deploy/backup/restore.sh %q --yes' "$env_prefix" "$dump"
    t0=$SECONDS
    rssh "$cmd" </dev/null 2>&1 | tail -3 | sed 's/^/    /'
    ((PIPESTATUS[0] == 0)) || { drill_ok=0; drill_detail='restore.sh failed'; }
    restore_s=$((SECONDS - t0))
  fi
  if ((drill_ok)); then
    for _ in $(seq 30); do [[ $(code /ready) == 200 ]] && break; sleep 2; done
    out=$(probe --mode read-marker --marker "$marker_a" 2>&1) || { drill_ok=0; drill_detail="pre-backup marker $marker_a lost"; }
    printf '%s\n' "$out" | sed 's/^/    /'
  fi
  if ((drill_ok)); then
    if out=$(probe --mode read-marker --marker "$marker_b" --wait 6 2>&1); then
      drill_ok=0; drill_detail="post-backup marker $marker_b survived the restore"
    fi
    printf '%s\n' "$out" | sed 's/^/    (expected absent) /'
  fi
  if ((drill_ok)); then
    gate backup-restore PASS "backup $(basename "$dump") -> restore ${restore_s}s; marker A survives, post-backup marker B gone"
  else
    gate backup-restore FAIL "$drill_detail"
  fi
fi

# ------------------------------------------------------------------ cleanup: revoke gate devices
if [[ -f $probe_state ]]; then
  probe_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["device_id"])' "$probe_state" 2>/dev/null || true)
  [[ -z $probe_id ]] || hlm --admin device revoke "$probe_id" >/dev/null 2>&1 || echo "note: could not revoke probe device $probe_id"
fi
[[ -f $cli_dir/hlm.toml ]] && { hlm --admin device revoke "$cli_device" >/dev/null 2>&1 || echo "note: could not revoke $cli_device"; }

# ------------------------------------------------------------------ optional G7: real CLIs
if ((g7)); then
  g7_dir=$state_dir/g7
  backup=$REPO_ROOT/deploy/.local/backups/g7-$stamp
  mkdir -p "$backup" "$g7_dir/cfg"
  restore_cmd=''
  for f in "$HOME/.claude.json" "$HOME/.codex/config.toml" "$HOME/.gemini/config/mcp_config.json"; do
    rel=${f#"$HOME"/}
    if [[ -f $f ]]; then
      mkdir -p "$backup/$(dirname "$rel")"; cp -p "$f" "$backup/$rel"
      restore_cmd+="cp -p '$backup/$rel' '$f'; "
    else
      restore_cmd+="rm -f '$f'; "
    fi
  done
  echo "G7 backups: $backup"
  g7hlm() { (cd "$g7_dir" && env HLM_CONFIG_DIR="$g7_dir/cfg" "${tls_env[@]}" uv run --quiet --frozen --project "$REPO_ROOT" hlm "$@"); }
  g7_device="g7-$(hostname -s | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '-')"
  g7_ok=1
  [[ -f $g7_dir/hlm.toml ]] || g7hlm init --server "$url/mcp" --project gates-g7 --device-name "$g7_device" || g7_ok=0
  g7hlm --admin project list 2>/dev/null | grep -qw gates-g7 || g7hlm --admin project create gates-g7 --name 'G7 real CLIs' || g7_ok=0
  if ! g7hlm device whoami >/dev/null 2>&1; then
    g7hlm device register --name "$g7_device" --class personal && g7hlm --admin device approve "$g7_device" --class personal --grant gates-g7:write || g7_ok=0
  fi
  for cli in claude codex agy; do
    if ((g7_ok)); then
      scope=(); [[ $cli == claude ]] && scope=(--scope user)
      marker="g7-$cli-$stamp-$RANDOM"
      if g7hlm mcp add "$cli" "${scope[@]}" &&
        g7hlm "$cli" --headless --task "Use the hlm MCP server: call memory.write with project gates-g7 and one item {kind: fact, title: $marker, body: 'G7 marker $marker'}; then call memory.query for $marker and print the clue you found." &&
        g7hlm query "$marker" | grep -q "$marker"; then
        gate "g7-$cli" PASS "mcp add + headless write->query $marker"
      else
        gate "g7-$cli" FAIL "see log ($marker)"
      fi
    else
      gate "g7-$cli" FAIL 'g7 device/project setup failed'
    fi
  done
  echo "G7 RESTORE (undo CLI registration): $restore_cmd"
fi

# ------------------------------------------------------------------ summary
echo
printf '%-22s %-5s %s\n' GATE RESULT DETAIL
failed=0
for i in "${!names[@]}"; do
  printf '%-22s %-5s %s\n' "${names[$i]}" "${results[$i]}" "${details[$i]}"
  [[ ${results[$i]} == FAIL ]] && failed=1
done
echo "total ${SECONDS}s (gates ${started:+$((SECONDS - started))}s); log: $log"
((failed)) && { echo 'RESULT FAIL'; exit 1; }
echo 'RESULT PASS'
