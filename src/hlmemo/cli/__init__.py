"""`hlm` — the HLMemo wrapper CLI (PHASE0-SPEC §5).

Modules:
  hlm.py            Typer app (entry point `hlm`)
  client_config.py  [client]/[preflight] resolution: flags > env > hlm.toml > defaults; exit codes
  credentials.py    device token in the OS keychain (`keyring`) or ~/.config/hlm/credentials.toml (0600)
  http_client.py    httpx client for /health, /devices/*, /admin/*
  mcp_client.py     streamable-HTTP MCP client for memory.query / memory.call_the_day
  preflight.py      query text, memory.query with one retry, first-prompt construction
  launch.py         argv per CLI + os.execvpe
  mcp_register.py   `hlm mcp add claude|codex|agy`
"""
