"""``hlm.questions``: the read behind ``hlm review`` (``librarian/questions.py::review_list``).

A client-protocol tool like ``hlm.export``: dispatched by ``tools/call`` but never advertised on
``tools/list``, so the agent tool surface (CC-4, G-SURF) does not grow. Unlike ``hlm.export`` it
is OWNER-ONLY (``ToolSpec.owner_only``): an agent's MCP call with its device bearer alone is refused
(``E_FORBIDDEN``); the owner's CLI also sends the owner client token (``HLM_OWNER_TOKEN``,
``server/mcp_server.require_owner_client``). Read-only (``read`` on the project; the notices'
visibility rule per question); answering stays ``memory.answer``. Paging is a keyset ``cursor``.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext
from hlmemo.core.write_models import SLUG_RE
from hlmemo.librarian import questions

NAME = questions.REVIEW_TOOL
DESCRIPTION = (
    "hlm CLI only (hlm review; owner client token required): the project's open librarian questions, "
    "oldest first, with the subjects' titles, body heads, the proposal's quotes, reason and actions; "
    "pending counts per kind; keyset paging by cursor."
)
INPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "project": {"type": "string", "pattern": SLUG_RE},
        "kind": {"type": "string", "pattern": "^[a-z_]{1,32}$"},
        "question_ids": {
            "type": "array",
            "items": {"type": "string", "format": "uuid"},
            "minItems": 1,
            "maxItems": questions.REVIEW_LIMIT_MAX,
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": questions.REVIEW_LIMIT_MAX, "default": 10},
        # keyset: a question's ``cursor`` / the listing's ``next_cursor``, for the same project+kind
        "cursor": {"type": "string", "minLength": 1, "maxLength": questions.REVIEW_CURSOR_MAX},
        "token_budget": {
            "type": "integer",
            "minimum": 256,
            "maximum": 32000,
            "default": questions.REVIEW_DEFAULT_BUDGET,
        },
    },
    "required": ["project"],
    "additionalProperties": False,
}


async def hlm_questions(conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any]) -> dict[str, Any]:
    return await questions.review_list(conn, ctx, args)


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "hlm_questions"]
