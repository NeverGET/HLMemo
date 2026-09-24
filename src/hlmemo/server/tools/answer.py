"""``memory.answer`` (PHASE2-4-ROADMAP W2c): answer one librarian question (``librarian/questions.py``).

Registered with one line in ``server/tools/__init__.py`` (``TOOLS``); the handler takes the request
transaction connection and the server-derived ``AuthContext`` like every write tool.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext
from hlmemo.librarian import questions
from hlmemo.server.tools.schemas import DEFS

NAME = questions.TOOL
DESCRIPTION = (
    "Answer a librarian question listed under librarian.notices in memory.query: accept applies the "
    "proposed action (rechecked; a changed subject makes it superseded), reject discards it, custom "
    "records your note and asks the librarian to re-plan. Idempotent per request_id."
)
INPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": {k: DEFS[k] for k in ("Slug", "Uuid", "Budget")},
    "type": "object",
    "properties": {
        "project": {"$ref": "#/$defs/Slug"},
        "request_id": {"$ref": "#/$defs/Uuid"},
        "question_id": {"$ref": "#/$defs/Uuid"},
        "decision": {"enum": ["accept", "reject", "custom"]},
        "note": {"type": "string", "maxLength": questions.NOTE_MAX},
        "token_budget": {"$ref": "#/$defs/Budget", "default": 2000},
    },
    "required": ["project", "request_id", "question_id", "decision"],
    "additionalProperties": False,
}


async def memory_answer(conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any]) -> dict[str, Any]:
    return await questions.answer(conn, ctx, args, raw=args)


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "memory_answer"]
