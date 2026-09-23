#!/usr/bin/env bash
# End-to-end gates against a deployed HLMemo, run from the operator workstation
# (RUNBOOK "Two-command deploy"). Prints PASS/FAIL per gate; exits non-zero if any gate fails.
# W0a (D-061): no admin token. Every gate device is minted over SSH with hlm_ops.sh (tokens
# travel on stdout into `hlm device login --token-stdin` or a 0600 state file, never argv) and
# revoked at the end. RG-routes verifies the public route table (check_edge.py --routes).
# Remote commands are single-quoted on purpose: they expand on the server.
# shellcheck disable=SC2016
set -Euo pipefail
# Empty "${array[@]}" under set -u needs bash >= 4.4 (macOS /bin/bash is 3.2: brew install bash).
((BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4))) || { echo "${0##*/}: bash >= 4.4 required" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: remote_gates.sh --url https://FQDN[:PORT] [--state DIR] [options]

  --state DIR           deploy/.local/<host>/ state (ssh_config, deploy.conf). Default: the state
                        whose deploy.conf URL matches --url (written by first_deploy.sh).
                        No admin token is used or needed (D-061); SSH reaches hlmemo.ops.
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
url='' state_dir='' insecure=0 tls='' ssh_config='' latency_n=20 p95_max=3000 drill=1 g7=0
while (($#)); do
  case $1 in
    --url|--state|--tls|--ssh-config|--latency-n|--latency-p95-ms)
      (($# >= 2)) || { echo "remote_gates: $1 needs a value" >&2; exit 64; }
      case $1 in
        --url) url=$2 ;; --state) state_dir=$2 ;; --tls) tls=$2 ;;
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
if [[ -z $state_dir ]]; then
  for conf in "$REPO_ROOT"/deploy/.local/*/deploy.conf; do
    [[ -f $conf ]] && grep -qxF "URL=$url" "$conf" && state_dir=$(dirname "$conf") && break
  done
  [[ -n $state_dir ]] || { echo "remote_gates: no deploy/.local/*/deploy.conf with URL=$url; pass --state" >&2; exit 64; }
fi
[[ -d $state_dir ]] || die "state directory not found: $state_dir"
state_dir=$(cd "$state_dir" && pwd)
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

# Never inherit an old admin token or registration secret from the operator's shell (G-W0-9).
unset HLM_ADMIN_TOKEN HLM_REGISTRATION_SECRET
uvrun() { uv run --quiet --frozen --project "$REPO_ROOT" "$@"; }
rssh() { /usr/bin/ssh -F "$ssh_config" hlm-deploy "$@"; }
# Operator path (D-061): python -m hlmemo.ops in the api container over SSH.
ops() { HLM_OPS_SSH_CONFIG=$ssh_config bash "$REPO_ROOT/deploy/scripts/hlm_ops.sh" --state "$state_dir" "$@"; }
minted_id() { # minted_id META_FILE -> device id from hlmemo.ops mint/rotate stderr metadata
  python3 -c 'import json,sys
for line in open(sys.argv[1]):
    try:
        d = json.loads(line)
    except ValueError:
        continue
    if isinstance(d, dict) and "minted" in d:
        print(d["minted"]["id"])' "$1" 2>/dev/null
}
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
probe_project=gates-probe
probe_device="gates-probe-${stamp,,}"
probe() { uvrun python "$REPO_ROOT/docs/bakeoff/r2/judge/probe.py" --base "$url" --state "$probe_state" --project "$probe_project" "${probe_insecure[@]}" "$@"; }
probe_insecure=()
((insecure)) && probe_insecure=(--insecure)
marker_a="gates-a-$stamp-$RANDOM"
rc=1
if ((have_ssh)) && ops project create "$probe_project" --name 'Remote gates probe' --exists-ok >/dev/null &&
  probe_token=$(ops device mint --name "$probe_device" --class ci --grant "$probe_project:write" --expires 30m 2>"$work/probe.meta"); then
  # The token goes from the ops pipe into a 0600 state file through python's stdin, never argv.
  printf '%s' "$probe_token" | python3 -c 'import json,os,sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"base": sys.argv[2], "device_id": int(sys.argv[3]), "device_token": sys.stdin.read().strip(), "project": sys.argv[4]}, f)' \
    "$probe_state" "$url" "$(minted_id "$work/probe.meta")" "$probe_project" && rc=0
  unset probe_token
fi
if ((rc != 0)); then
  out='probe device could not be minted over SSH (hlm_ops.sh)'
else
  out=$(probe --mode smoke 2>&1); rc=$?
fi
printf '%s\n' "$out" | sed 's/^/    /'
if ((rc == 0)); then
  out=$(probe --mode write-marker --marker "$marker_a" 2>&1 && probe --mode read-marker --marker "$marker_a" 2>&1); rc=$?
  printf '%s\n' "$out" | sed 's/^/    /'
  if ((rc == 0)); then gate probe PASS "smoke + write/read marker $marker_a"; else gate probe FAIL 'marker round trip failed'; fi
else
  gate probe FAIL 'smoke failed'
fi

# ------------------------------------------------------------------ RG-routes (W0a, D-061)
routes_tls=()
if ((insecure)); then
  if [[ -n $ca_file ]]; then routes_tls=(--cafile "$ca_file"); else routes_tls=(--insecure); fi
fi
if ((have_ssh)) && routes_token=$(ops device mint --name "gates-routes-${stamp,,}" --class ci --expires 10m 2>/dev/null) &&
  out=$(HLM_ROUTES_TOKEN=$routes_token python3 "$REPO_ROOT/deploy/scripts/check_edge.py" --routes --base "$url" "${routes_tls[@]}" 2>&1); then
  printf '%s\n' "$out" | sed 's/^/    /'
  gate routes PASS "$(tail -1 <<< "$out")"
else
  printf '%s\n' "${out:-route check did not run (SSH/mint failed)}" | sed 's/^/    /'
  gate routes FAIL "$(tail -1 <<< "${out:-mint over SSH failed}")"
fi
unset routes_token out

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
  ((cli_ok)) && { ops project create gates-cli --name 'Remote gates' --exists-ok >/dev/null || cli_ok=0; }
  # Public self-registration must be closed, with the operator hint (exit 77).
  if ((cli_ok)); then
    printf '    $ hlm device register --name %s (expect refusal)\n' "$cli_device-reg"
    reg_out=$(hlm device register --name "$cli_device-reg" --class ci 2>&1); reg_rc=$?
    printf '%s\n' "$reg_out" | sed 's/^/      /'
    [[ $reg_rc == 77 && $reg_out == *'registration is closed'* ]] || cli_ok=0
  fi
  if ((cli_ok)); then
    printf '    $ hlm_ops.sh device mint --name %s ... | hlm device login --token-stdin\n' "$cli_device"
    ops device mint --name "$cli_device" --class ci --grant gates-cli:write --expires 30m 2>/dev/null |
      hlm device login --name "$cli_device" --token-stdin 2>&1 | sed 's/^/      /'
    ((PIPESTATUS[0] == 0 && PIPESTATUS[1] == 0)) || cli_ok=0
  fi
  ((cli_ok)) && { run_cli query 'remote gates context' || cli_ok=0; }
  ((cli_ok)) && { run_cli close --notes "remote gates $stamp against $url" --decision "remote gates $stamp ran" || cli_ok=0; }
  ((cli_ok)) && { run_cli device whoami | grep -q 'gates-cli:write' || cli_ok=0; }
  ((cli_ok)) && { run_cli device revoke --self || cli_ok=0; }
  ((cli_ok)) && { ! hlm device whoami >/dev/null 2>&1 || cli_ok=0; }
  cli_detail="init/register-refused/ops-mint+login/query/close/whoami/revoke --self as $cli_device"
fi
if ((cli_ok)); then gate hlm-cli PASS "$cli_detail"; else gate hlm-cli FAIL "${cli_detail:-see log}"; fi

# ------------------------------------------------------------------ G7 WAN latency
lat=$(env "${tls_env[@]}" INSECURE="$insecure" uv run --quiet --frozen --project "$REPO_ROOT" python - "$url" "$probe_state" "$latency_n" <<'PY' 2>&1
import json, os, statistics, sys, time, uuid
import httpx
url, state, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
st = json.load(open(state))
token, project = st["device_token"], st["project"]
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
                "arguments": {"project": project, "query": f"latency probe {uuid.uuid4().hex[:6]}", "token_budget": 1024}}}
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
if ((have_ssh)); then
  for device in "$probe_device" "$cli_device"; do
    # Already revoked (cli: --self above) is fine; anything else is reported.
    ops device list --json 2>/dev/null | python3 -c 'import json,sys; d={x["name"]: x["status"] for x in json.load(sys.stdin)["devices"]}; sys.exit(0 if d.get(sys.argv[1]) == "trusted" else 1)' "$device" &&
      { ops device revoke "$device" >/dev/null 2>&1 || echo "note: could not revoke $device"; }
  done
fi

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
  ops project create gates-g7 --name 'G7 real CLIs' --exists-ok >/dev/null || g7_ok=0
  # Minted once, then rotated on every run: the new token replaces the stored one and
  # `hlm mcp add` below re-registers it with each CLI (D-061).
  if ((g7_ok)); then
    if ops device rotate "$g7_device" 2>/dev/null | g7hlm device login --name "$g7_device" --token-stdin; then
      echo "G7 device $g7_device rotated"
    elif ops device mint --name "$g7_device" --class personal --grant gates-g7:write 2>/dev/null |
      g7hlm device login --name "$g7_device" --token-stdin; then
      echo "G7 device $g7_device minted"
    else
      g7_ok=0
    fi
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
