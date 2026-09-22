#!/usr/bin/env bash
# Smoke: register hlm with claude (2.1.278), run a headless one-shot that calls memory.write then
# memory.query, and check the output + server log for both tool names.
#   HLM_DEVICE_TOKEN=hlm_... HLM_SERVER_URL=http://127.0.0.1:8765/mcp tests/smoke/claude.sh
CLI=claude
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

require_env
check_version
server_up
register_mcp

MARKER="$(smoke_marker)"
REQ="$(new_uuid)"
SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OUT="$(mktemp -t hlm-smoke-claude.XXXXXX)"
trap 'rm -f "$OUT"' EXIT

log "headless one-shot (marker $MARKER)"
# Spec launch form `claude -p "$P"`; MCP tools must be pre-allowed in -p mode.
run_with_timeout "$SMOKE_TIMEOUT_S" \
  claude -p "$(smoke_prompt "$MARKER" "$REQ")" \
    --allowedTools "mcp__hlm__memory.write,mcp__hlm__memory.query,mcp__hlm__memory.drilldown,mcp__hlm__memory.raw" \
    ${CLAUDE_EXTRA_ARGS:-} \
  >"$OUT" 2>&1 || { cat "$OUT" >&2; fail "claude exited non-zero"; }

verify_output "$OUT" "$MARKER"
verify_server_log "$SINCE"
log "PASS"
