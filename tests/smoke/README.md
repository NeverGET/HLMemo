# Smoke tests — real coding CLIs against a running HLMemo stack

These scripts are the D-014 end-to-end check: register the MCP server with each pinned CLI, run one
headless turn that must call `memory.write` and then `memory.query`, and verify both tool names in
the CLI output and in the server log. They are **not** run by pytest and need a live stack.

Pinned versions: `tests/smoke/VERSIONS` (`claude 2.1.278`, `codex 0.155.1`, `agy 1.1.4`).
`hlm doctor` reports drift against the same file.

## Prerequisites

1. Stack up: `make up` (db → migrate → api, worker), `HLM_ADMIN_TOKEN` set for the api container.
2. A project and a trusted device with `write` on it (see `docs/USAGE.md` quickstart):
   ```sh
   hlm --admin project create hlmemo --name "HLMemo"
   hlm device register --name smoke --class ci
   hlm --admin device approve smoke --class ci --grant hlmemo:write
   ```
3. Export the device token for the scripts (they never read the keychain):
   `export HLM_DEVICE_TOKEN=hlm_...` (printed by `hlm device register --json`, or `hlm --json device register | jq -r .token`).
4. The CLI under test on `PATH` and logged in.

## Run

```sh
export HLM_DEVICE_TOKEN=hlm_...            # trusted device, write on $HLM_PROJECT
export HLM_SERVER_URL=http://127.0.0.1:8765/mcp
export HLM_PROJECT=hlmemo
tests/smoke/claude.sh
tests/smoke/codex.sh
tests/smoke/agy.sh
```

Knobs: `HLM_SERVER_LOG=<file>` (api log file; default `docker compose logs api --since <start>`),
`SMOKE_TIMEOUT_S` (default 300), `SMOKE_STRICT_VERSIONS=1` (version drift fails instead of warning),
`CLAUDE_EXTRA_ARGS` / `CODEX_EXTRA_ARGS` / `AGY_EXTRA_ARGS` (extra flags, e.g. approval mode),
`HLM_BIN` (default `uv run --frozen hlm`).

## What each script checks (self-checking, exit 1 on any failure)

| step | check |
|---|---|
| version | `<cli> --version` vs `VERSIONS` (warn, or fail with `SMOKE_STRICT_VERSIONS=1`) |
| server | `GET /health` with the bearer → device must be `trusted` |
| register | `hlm mcp add <cli>` succeeds (claude/codex: CLI command; agy: JSON merge, verified by parsing the file) |
| one-shot | headless run with a prompt that calls `memory.write` then `memory.query` and prints `CALLED memory.write`, `CALLED memory.query`, the query JSON and a unique marker |
| output | both tool names and the marker appear in the CLI output |
| server log | both tool names appear in the api log since the run started (skipped with a WARN if no log source) |

Headless forms used are the spec ones: `claude -p`, `codex exec -C <root>`, `agy --add-dir <root> --print`.
`claude -p` needs the MCP tools pre-allowed (`--allowedTools mcp__hlm__memory.write,mcp__hlm__memory.query`);
codex gets the token through the `HLM_DEVICE_TOKEN` env var its registration names.
