"""G-SURF (CC-4): the ``tools/list`` payload stays within 3,000 o200k tokens.

Measured on exactly what ``server/mcp_server.on_list_tools`` advertises (name, description,
inputSchema per tool; no outputSchema), serialized canonically like every HLMemo payload.
"""

from __future__ import annotations

from hlmemo.core.budget import Meter
from hlmemo.server.tools import TOOLS

G_SURF_MAX_TOKENS = 3000


def tools_list_tokens() -> int:
    payload = {
        "tools": [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in TOOLS
        ]
    }
    return Meter().count(payload)


def test_gsurf_tools_list_token_budget() -> None:
    n = tools_list_tokens()
    print(f"\nG-SURF tools/list: {len(TOOLS)} tools, {n} o200k tokens (limit {G_SURF_MAX_TOKENS})")
    assert n <= G_SURF_MAX_TOKENS
