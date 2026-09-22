#!/usr/bin/env bash
# Shared helpers for the hlm smoke scripts. Sourced, not executed.
#
# Inputs (environment):
#   HLM_DEVICE_TOKEN   required — token of a TRUSTED device with `write` on $HLM_PROJECT
#   HLM_SERVER_URL     default http://127.0.0.1:8765/mcp
#   HLM_PROJECT        default hlmemo
#   HLM_SERVER_LOG     optional path to the api log file; else `docker compose logs api` is used
#   SMOKE_TIMEOUT_S    default 300 — hard cap for the headless run
#   SMOKE_STRICT_VERSIONS=1 — fail (not warn) when the CLI version differs from tests/smoke/VERSIONS

set -euo pipefail

SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SMOKE_DIR/../.." && pwd)"
HLM_SERVER_URL="${HLM_SERVER_URL:-http://127.0.0.1:8765/mcp}"
HLM_PROJECT="${HLM_PROJECT:-hlmemo}"
SMOKE_TIMEOUT_S="${SMOKE_TIMEOUT_S:-300}"
HLM_BIN="${HLM_BIN:-uv run --frozen hlm}"

log()  { printf '[smoke %s] %s\n' "$CLI" "$*" >&2; }
fail() { log "FAIL: $*"; exit 1; }

require_env() {
  [[ -n "${HLM_DEVICE_TOKEN:-}" ]] || fail "HLM_DEVICE_TOKEN is not set (register + approve a device first, see README.md)"
  export HLM_DEVICE_TOKEN HLM_SERVER_URL HLM_PROJECT
  command -v "$CLI" >/dev/null 2>&1 || fail "$CLI not found on PATH"
}

pinned_version() {
  awk -v cli="$CLI" '$1 == cli { print $2 }' "$SMOKE_DIR/VERSIONS"
}

check_version() {
  local want have
  want="$(pinned_version)"
  have="$("$CLI" --version 2>&1 | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
  log "version: have=${have:-?} pinned=${want:-?}"
  if [[ -n "$want" && "$have" != "$want" ]]; then
    if [[ "${SMOKE_STRICT_VERSIONS:-0}" == "1" ]]; then
      fail "$CLI version $have differs from pinned $want"
    fi
    log "WARN: $CLI version $have differs from pinned $want (set SMOKE_STRICT_VERSIONS=1 to fail)"
  fi
}

server_up() {
  local base="${HLM_SERVER_URL%/mcp}"
  curl -fsS -m 5 -H "Authorization: Bearer $HLM_DEVICE_TOKEN" "$base/health" >/dev/null \
    || fail "server not reachable at $base/health"
  local status
  status="$(curl -fsS -m 5 -H "Authorization: Bearer $HLM_DEVICE_TOKEN" "$base/health" | sed -n 's/.*"status":"\([a-z]*\)".*"class".*/\1/p' | tail -n1)"
  log "server ok; device status=${status:-unknown}"
  [[ "$status" == "trusted" || -z "$status" ]] || fail "device is '$status', it must be trusted"
}

register_mcp() {
  log "registering MCP server via: $HLM_BIN mcp add $CLI"
  (cd "$REPO_ROOT" && $HLM_BIN --server "$HLM_SERVER_URL" mcp add "$CLI")
}

# run_with_timeout <seconds> <cmd...>   (portable: macOS has no coreutils `timeout`)
run_with_timeout() {
  local secs="$1"; shift
  perl -e 'alarm shift; exec @ARGV' "$secs" "$@"
}

smoke_marker() {
  printf 'hlm-smoke-%s-%s' "$CLI" "$(date -u +%Y%m%dT%H%M%SZ)"
}

smoke_prompt() {
  local marker="$1" req="$2"
  cat <<PROMPT
You are running a non-interactive smoke test of the MCP server named "hlm". Do exactly this, using only the MCP tools, then stop:
1. Call the tool memory.write with arguments: {"project":"$HLM_PROJECT","request_id":"$req","client":"smoke-$CLI","items":[{"kind":"fact","title":"$marker","body":"Smoke test write from $CLI at $marker.","tags":["smoke"]}]}
2. Call the tool memory.query with arguments: {"project":"$HLM_PROJECT","query":"$marker","token_budget":512}
3. Print, verbatim and on separate lines: "CALLED memory.write", "CALLED memory.query", the raw JSON result of step 2, and finally "MARKER $marker".
Do not edit any files. Do not run shell commands.
PROMPT
}

# verify_output <output-file> <marker>
verify_output() {
  local out="$1" marker="$2" ok=1
  grep -q 'memory.write' "$out" || { log "output lacks 'memory.write'"; ok=0; }
  grep -q 'memory.query' "$out" || { log "output lacks 'memory.query'"; ok=0; }
  grep -q "$marker" "$out"      || { log "output lacks the marker $marker"; ok=0; }
  [[ $ok == 1 ]] || { log "----- $CLI output -----"; cat "$out" >&2; fail "one-shot output did not show both tool calls"; }
  log "output: both tool names + marker present"
}

# verify_server_log <since-iso8601>
verify_server_log() {
  local since="$1" logtxt
  if [[ -n "${HLM_SERVER_LOG:-}" ]]; then
    logtxt="$(cat "$HLM_SERVER_LOG")"
  elif command -v docker >/dev/null 2>&1 && (cd "$REPO_ROOT" && docker compose ps -q api >/dev/null 2>&1); then
    logtxt="$(cd "$REPO_ROOT" && docker compose logs --no-color --since "$since" api 2>/dev/null || true)"
  else
    log "WARN: no server log source (set HLM_SERVER_LOG or run the compose stack); skipping log check"
    return 0
  fi
  local ok=1
  grep -q 'memory.write' <<<"$logtxt" || { log "server log lacks 'memory.write'"; ok=0; }
  grep -q 'memory.query' <<<"$logtxt" || { log "server log lacks 'memory.query'"; ok=0; }
  [[ $ok == 1 ]] || fail "server log did not record both tool calls since $since"
  log "server log: both tool names present"
}

new_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then uuidgen | tr 'A-Z' 'a-z'; else python3 -c 'import uuid;print(uuid.uuid4())'; fi
}
