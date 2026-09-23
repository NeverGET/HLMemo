"""Librarian working memory (W2a): reserved project ``hlm-librarian``, fresh-wake per job.

Each job loads the top ``HLM_LIBRARIAN_MEMORY_RULES`` (8) current ``librarian-rule`` facts within
``HLM_LIBRARIAN_MEMORY_TOKENS`` (1,500 o200k tokens), highest importance first, newest first on
ties. Rules are written only through the ``librarian_memory`` capability: a ``fact`` tagged
``librarian-rule`` in ``hlm-librarian`` whose body is rule text plus clue references — never a
copy of an item body. Writes go through the ordinary write service as the librarian system device
with an internal, non-persisted grant on that one project, so they are ordinary ``write`` events
(replayed like any other). Compaction is W3b (above 200 items or 40k tokens).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.budget import Meter
from hlmemo.core.errors import ToolError
from hlmemo.librarian.events import CLIENT, NS_LIBRARIAN
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.reserved import MEMORY_PROJECT, reserved_ids

RULE_TAG = "librarian-rule"
MAX_RULE_CHARS = 600
_CLUE = re.compile(r"^v[0-9]+(\.[0-9]+)?$")


def parse_rule(body: str) -> tuple[str, list[str]] | None:
    """``(rule text, clue refs)`` if ``body`` has exactly the rule shape ``write_rule`` produces
    (text ≤ MAX_RULE_CHARS, optional final ``Refs: v1, v2`` line of clues), else ``None``."""
    text, refs = body, []
    head, sep, tail = body.rpartition("\nRefs: ")
    if sep:
        text, refs = head, [r.strip() for r in tail.split(",")]
    if not text.strip() or len(text) > MAX_RULE_CHARS or any(not _CLUE.match(r) for r in refs):
        return None
    return text, refs


async def load_rules(
    conn: AsyncConnection,
    *,
    max_rules: int = 8,
    max_tokens: int = 1500,
    meter: Meter | None = None,
    redactor: Redactor | None = None,
) -> list[dict[str, Any]]:
    """Top rules for a prompt. Only rows the librarian system device wrote itself, only in the
    rule shape (rule text + clue references; anything longer — e.g. a pasted item body — is
    skipped), and the text passes the prompt redactor (Sol 37 #1)."""
    meter = meter or Meter()
    redactor = redactor or Redactor()
    ids = await reserved_ids(conn)
    cur = await conn.execute(
        """
        SELECT mv.version_id, mv.body FROM memory_versions mv
          JOIN events e ON e.event_id = mv.source_event_id
         WHERE mv.project_id = %s AND mv.kind = 'fact' AND mv.status = 'active' AND %s = ANY(mv.tags)
           AND mv.superseded_at = 'infinity' AND mv.valid_to = 'infinity'
           AND e.device_id = %s
         ORDER BY mv.importance DESC NULLS LAST, mv.version_id DESC
         LIMIT %s
        """,
        (ids.memory_project_id, RULE_TAG, ids.librarian_device_id, max_rules),
    )
    rules: list[dict[str, Any]] = []
    used = 0
    for vid, body in await cur.fetchall():
        parsed = parse_rule(body)
        if parsed is None:
            continue
        text, refs = parsed
        rule = {"clue": f"v{vid}", "rule": redactor.text(text), "refs": refs}
        cost = meter.count(rule)
        if used + cost > max_tokens:
            break
        used += cost
        rules.append(rule)
    return rules


def memory_ctx(librarian_device_id: int, memory_project_id: int) -> AuthContext:
    """Internal context: the system device with write on ``hlm-librarian`` ONLY (never persisted)."""
    return AuthContext(
        device_id=librarian_device_id,
        device_class="server",
        is_admin=False,
        token_generation=0,
        grants={memory_project_id: Role.WRITE},
        client=CLIENT,
    )


async def write_rule(
    conn: AsyncConnection,
    *,
    title: str,
    text: str,
    clue_refs: list[str],
    dedupe: str,
    importance: int = 5,
    deps: Any = None,
) -> int:
    """Write one ``librarian-rule`` fact (capability ``librarian_memory``); returns its version id."""
    from hlmemo.core.write_service import write

    text = Redactor().text(text)  # a rule never stores a secret (it is prompt input later)
    if len(text) > MAX_RULE_CHARS:
        raise ToolError("E_INVALID_ARG", f"a librarian rule is at most {MAX_RULE_CHARS} characters")
    if any(not _CLUE.match(c) for c in clue_refs):
        raise ToolError("E_INVALID_ARG", "clue references must be clues (v<version>[.<ordinal>])")
    ids = await reserved_ids(conn)
    body = text if not clue_refs else f"{text}\nRefs: {', '.join(clue_refs)}"
    req = {
        "project": MEMORY_PROJECT,
        "request_id": str(uuid.uuid5(NS_LIBRARIAN, f"rule:{dedupe}")),
        "client": CLIENT,
        "items": [
            {
                "kind": "fact",
                "title": title[:200],
                "body": body,
                "tags": [RULE_TAG],
                "importance": importance,
                "stability": "stable",
            }
        ],
    }
    res = await write(conn, memory_ctx(ids.librarian_device_id, ids.memory_project_id), req, deps=deps)
    return res.versions[0].version_id


__all__ = ["MAX_RULE_CHARS", "RULE_TAG", "load_rules", "memory_ctx", "parse_rule", "write_rule"]
