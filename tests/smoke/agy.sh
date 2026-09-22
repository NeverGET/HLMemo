#!/usr/bin/env bash
# Smoke: register hlm with agy (1.1.4; JSON merge into ~/.gemini/config/mcp_config.json — agy has no
# `mcp` subcommand), run `agy --print` one-shot calling memory.write then memory.query, verify.
#   HLM_DEVICE_TOKEN=hlm_... tests/smoke/agy.sh
CLI=agy
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

require_env
check_version
server_up
register_mcp

CFG="${HLM_AGY_MCP_CONFIG:-$HOME/.gemini/config/mcp_config.json}"
python3 - "$CFG" "$HLM_SERVER_URL" <<'PY' || fail "agy mcp_config.json does not carry the hlm entry"
import json, sys
doc = json.load(open(sys.argv[1]))
entry = doc["mcpServers"]["hlm"]
url = sys.argv[2].rstrip("/")
url = url if url.endswith("/mcp") else url + "/mcp"
assert entry["serverUrl"] == url, entry
assert entry["headers"]["Authorization"].startswith("Bearer hlm_"), "missing bearer"
PY
log "mcp_config.json carries mcpServers.hlm"

MARKER="$(smoke_marker)"
REQ="$(new_uuid)"
SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OUT="$(mktemp -t hlm-smoke-agy.XXXXXX)"
trap 'rm -f "$OUT"' EXIT

log "headless one-shot (marker $MARKER)"
# Spec launch form `agy --add-dir "$ROOT" --print "$P"` (default --print-timeout 5m).
run_with_timeout "$SMOKE_TIMEOUT_S" \
  agy --add-dir "$REPO_ROOT" ${AGY_EXTRA_ARGS:-} --print "$(smoke_prompt "$MARKER" "$REQ")" \
  >"$OUT" 2>&1 || { cat "$OUT" >&2; fail "agy exited non-zero"; }

verify_output "$OUT" "$MARKER"
verify_server_log "$SINCE"
log "PASS"
