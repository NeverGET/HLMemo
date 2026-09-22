#!/usr/bin/env bash
# Judge check (BRIEF G-D2 "Postgres is NOT reachable from the host on any port").
#
#   db_exposure.sh PROJECT [HTTP_PORT HTTPS_PORT]
#
# Exits non-zero if
#   * the compose project has no containers (nothing to judge), or
#   * ANY container of PROJECT publishes container port 5432 or host port 5432, or runs with
#     network_mode=host, or
#   * a Postgres server answers (SSLRequest handshake -> 'S'/'N') on 127.0.0.1 or the LAN IP
#     (`ipconfig getifaddr en0`) on any host port published by PROJECT other than HTTP_PORT /
#     HTTPS_PORT (when given, those two are skipped: they belong to Caddy).
# Uses only `docker compose ps --format json`, `docker port`, `docker inspect` and python3 (for the
# JSON parse and the raw TCP handshake). Does not trust any contestant script.
set -uo pipefail

PROJECT="${1:?usage: db_exposure.sh PROJECT [HTTP_PORT HTTPS_PORT]}"
HTTP_PORT="${2:-}"
HTTPS_PORT="${3:-}"
fail=0

say() { printf '%s\n' "$*"; }

ids="$(docker compose -p "$PROJECT" ps -a -q 2>/dev/null)"
if [[ -z "$ids" ]]; then
  say "FAIL db-exposure no containers found for compose project '$PROJECT'"
  exit 2
fi

# 1) static: published ports from compose ps JSON (one object per line, or one array)
published="$(docker compose -p "$PROJECT" ps -a --format json | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
rows = []
if raw.startswith("["):
    rows = json.loads(raw)
else:
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
for r in rows:
    for p in r.get("Publishers") or []:
        if int(p.get("PublishedPort") or 0) > 0:
            print(r.get("Service"), p.get("URL") or "-", p.get("PublishedPort"), p.get("TargetPort"), p.get("Protocol"))
')"
say "INFO published ports (service url host_port container_port proto):"
if [[ -n "$published" ]]; then
  while IFS= read -r line; do say "INFO   $line"; done <<<"$published"
else
  say "INFO   (none)"
fi

host_ports=()
while read -r svc url hport cport proto; do
  [[ -z "${svc:-}" ]] && continue
  if [[ "$cport" == "5432" || "$hport" == "5432" ]]; then
    say "FAIL db-exposure service=$svc publishes $url:$hport->$cport/$proto"
    fail=1
  fi
  if [[ "$proto" == "tcp" && "$hport" != "$HTTP_PORT" && "$hport" != "$HTTPS_PORT" ]]; then
    host_ports+=("$hport")
  fi
done <<<"$published"

# 2) cross-check with `docker port` and host networking via `docker inspect`
for id in $ids; do
  name="$(docker inspect -f '{{.Name}}' "$id" | sed 's#^/##')"
  netmode="$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$id")"
  if [[ "$netmode" == "host" ]]; then
    say "FAIL db-exposure container=$name uses network_mode=host"
    fail=1
  fi
  if docker port "$id" 5432/tcp >/dev/null 2>&1 && [[ -n "$(docker port "$id" 5432/tcp 2>/dev/null)" ]]; then
    say "FAIL db-exposure container=$name docker port 5432/tcp -> $(docker port "$id" 5432/tcp | tr '\n' ' ')"
    fail=1
  fi
done

# 3) dynamic: does Postgres answer on any other published host port?
lan_ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
addrs=(127.0.0.1)
[[ -n "$lan_ip" ]] && addrs+=("$lan_ip")
uniq_ports=()
while IFS= read -r p; do uniq_ports+=("$p"); done < <(printf '%s\n' "${host_ports[@]:-}" | sed '/^$/d' | sort -un)
for addr in "${addrs[@]}"; do
  for port in "${uniq_ports[@]:-}"; do
    [[ -z "$port" ]] && continue
    verdict="$(python3 - "$addr" "$port" <<'PY'
import socket, struct, sys
addr, port = sys.argv[1], int(sys.argv[2])
try:
    s = socket.create_connection((addr, port), timeout=3)
except OSError as e:
    print(f"closed {type(e).__name__}")
    sys.exit(0)
try:
    s.settimeout(3)
    s.sendall(struct.pack("!ii", 8, 80877103))  # SSLRequest
    b = s.recv(1)
    print("postgres" if b in (b"S", b"N") else f"other first_byte={b!r}")
except OSError as e:
    print(f"other {type(e).__name__}")
finally:
    s.close()
PY
)"
    if [[ "$verdict" == postgres* ]]; then
      say "FAIL db-exposure postgres answers on $addr:$port"
      fail=1
    else
      say "INFO probe $addr:$port -> $verdict"
    fi
  done
done

if [[ $fail -eq 0 ]]; then
  say "PASS db-exposure project=$PROJECT no 5432 publish, no postgres on published ports (${addrs[*]})"
fi
exit "$fail"
