#!/usr/bin/env bash
# Smoke: register hlm with codex (0.155.1; the registration names the env var HLM_DEVICE_TOKEN),
# run `codex exec` one-shot calling memory.write then memory.query, and verify output + server log.
#   HLM_DEVICE_TOKEN=hlm_... tests/smoke/codex.sh
CLI=codex
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

require_env
check_version
server_up
register_mcp

MARKER="$(smoke_marker)"
REQ="$(new_uuid)"
SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OUT="$(mktemp -t hlm-smoke-codex.XXXXXX)"
trap 'rm -f "$OUT"' EXIT

log "headless one-shot (marker $MARKER)"
# Spec launch form `codex exec -C "$ROOT" "$P"`; HLM_DEVICE_TOKEN is exported by require_env.
run_with_timeout "$SMOKE_TIMEOUT_S" \
  codex exec -C "$REPO_ROOT" --skip-git-repo-check ${CODEX_EXTRA_ARGS:-} "$(smoke_prompt "$MARKER" "$REQ")" \
  >"$OUT" 2>&1 || { cat "$OUT" >&2; fail "codex exited non-zero"; }

verify_output "$OUT" "$MARKER"
verify_server_log "$SINCE"
log "PASS"
