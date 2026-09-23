"""Shared helpers for the W2b/W2c gates (write_review, questions, memory.answer).

* ``Oracle``: a scripted OpenAI-compatible answer function (plugs into ``ScriptedLLM(default=…)``)
  that recognizes the task from the system prompt (``place`` / ``relate`` / ``relate_verify``) and
  answers from rule tables keyed by item TITLES, so tests read like the fixture they encode.
* ``review_deps``: write deps with the W2b trigger on (``librarian_enqueue``).
* ``embed``: drain the embed outbox with the real pinned E5 model (the librarian reads stored
  vectors; it never loads a model).
* ``dump_w2b``: every projection W2b/W2c adds (signals, batches, questions) plus the Phase-0
  projections and the full jobs table, for replay identity (G6 with the new kinds).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg

from hlmemo.core.write_service import default_deps, write

Rel = tuple[str, str, str, str]  # (relation, supersedes, confidence, verifier-current or "")


def review_deps() -> Any:
    return dataclasses.replace(default_deps(), librarian_enqueue=True)


def parse_input(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    system = body["messages"][0]["content"]
    user = body["messages"][1]["content"]
    raw = user.split("INPUT: ", 1)[1].split("\nRULES", 1)[0]
    if 'JOB "placement"' in system:
        task = "place"
    elif 'JOB "relation_check"' in system:
        task = "relate_verify"
    elif 'JOB "relation"' in system:
        task = "relate"
    else:
        task = "other"
    return task, json.loads(raw)


def _words(text: str, n: int = 4) -> str:
    return " ".join(text.split()[:n])


@dataclass
class Oracle:
    """``relations[(new_title, existing_title)] = (relation, supersedes, confidence)``; unknown
    pairs are ``none``. ``placement[title] = (importance, stability)``. ``verify(new_title,
    existing_title) -> dict`` overrides the verifier (default: agree with the primary)."""

    relations: dict[tuple[str, str], tuple[str, str, str]] = field(default_factory=dict)
    placement: dict[str, tuple[int, str]] = field(default_factory=dict)
    verify: Callable[[str, str], dict[str, Any] | None] | None = None
    quote_ok: bool = True
    extra_results: list[dict[str, Any]] = field(default_factory=list)
    seen: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        task, inp = parse_input(body)
        self.seen.append((task, inp))
        if task == "place":
            return {
                "items": [
                    {
                        "id": it["id"],
                        "importance": self.placement.get(it["title"], (5, "stable"))[0],
                        "stability": self.placement.get(it["title"], (5, "stable"))[1],
                        "topic_hint": "test topic",
                        "tags_add": ["w2b", *it.get("tags", [])[:1]],
                    }
                    for it in inp["items"]
                ]
            }
        if task == "relate":
            new = inp["new"]
            out = []
            for ex in inp["existing"]:
                rel, sup, conf = self.relations.get((new["title"], ex["title"]), ("none", "none", "high"))
                out.append(
                    {
                        "id": ex["id"],
                        "relation": rel,
                        "supersedes": sup,
                        "confidence": conf,
                        "new_quote": _words(new["text"]) if rel != "none" and self.quote_ok else "",
                        "old_quote": _words(ex["text"]) if rel != "none" and self.quote_ok else "",
                        "reason": "scripted",
                    }
                )
            return {"results": out + self.extra_results}
        if task == "relate_verify":
            results = []
            for p in inp["pairs"]:
                a, b = p["A"], p["B"]
                custom = self.verify(a["title"], b["title"]) if self.verify else None
                if custom is None:  # agree: find the primary's relation for either order
                    rel = self.relations.get((b["title"], a["title"])) or self.relations.get(
                        (a["title"], b["title"])
                    )
                    rel = rel or ("none", "none", "high")
                    if rel[0] == "contradicts":
                        new_is_b = (b["title"], a["title"]) in self.relations
                        current = (
                            ("B" if new_is_b else "A") if rel[1] == "new" else ("A" if new_is_b else "B")
                        )
                        custom = {"same_subject": True, "conflict": True, "current": current}
                    else:
                        custom = {"same_subject": True, "conflict": False, "current": "both"}
                results.append({"id": p["id"], **custom})
            return {"results": results}
        return {}


async def write_items(
    connect: Any, ctx: Any, project: str, items: list[dict[str, Any]], deps: Any = None
) -> list[Any]:
    async with await connect() as conn:
        res = await write(
            conn,
            ctx,
            {"project": project, "request_id": str(uuid.uuid4()), "client": "pytest/0", "items": items},
            deps=deps or review_deps(),
        )
        await conn.commit()
    return list(res.versions)


async def embed(connect: Any, embedder: Any) -> None:
    from hlmemo.worker.main import drain

    await drain(connect, embedder)


async def dump_w2b(conn: psycopg.AsyncConnection) -> dict[str, list[str]]:
    from tests.integration._librarian_fixtures import dump_full_jobs_and_questions
    from tests.integration._write_fixtures import dump_projections

    out = {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)}
    # Phase 0: an embed job's completion is not an event (the worker derives vectors; replay
    # re-queues the job and the worker re-embeds). Compare those rows without their run state.
    cur = await conn.execute(
        """
        SELECT CASE WHEN kind IN ('embed', 'reembed')
                    THEN (kind, dedupe_key, payload::text, source_event_id, priority, run_after)::text
                    ELSE j::text END
          FROM jobs j ORDER BY dedupe_key
        """
    )
    out["jobs"] = [r[0] for r in await cur.fetchall()]
    for table, key in (("version_signals", "version_id"), ("librarian_batches", "batch_id")):
        cur = await conn.execute(f"SELECT t::text FROM {table} t ORDER BY {key}")
        out[table] = [r[0] for r in await cur.fetchall()]
    return out


async def resolved_of(conn: psycopg.AsyncConnection, op: str = "write_review") -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT payload->'resolved' FROM events WHERE kind = 'librarian'"
        " AND payload->'request'->>'op' = %s ORDER BY event_id",
        (op,),
    )
    return [r[0] for r in await cur.fetchall()]


__all__ = ["Oracle", "dump_w2b", "embed", "parse_input", "resolved_of", "review_deps", "write_items"]
