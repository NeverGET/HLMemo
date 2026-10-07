"""`hlm migrate recall`: a cheap retrieval gate before the paid blind check (TEMPLATE §7).

For each sealed truth-set question: does `memory.query` return an item from the quoted source file among
its top-k hits? It measures retrieval only (no librarian, no answer). Negative questions are listed but not
scored, because a query's `evidence` field carries no abstain signal.

Truth-set rows (JSONL): {"id", "question", "category", "lang", "quotes": [{"file", "text"}], ...}; `category`
`negative` marks a question the memory must not answer.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from hlmemo.migrate.redact import redact

Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def load_truthset(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    for i, r in enumerate(rows):
        if not isinstance(r.get("question"), str) or "id" not in r:
            raise ValueError(f"truth set row {i + 1}: needs `id` and `question`")
    return rows


def rank_of(hits: list[dict[str, Any]], files: set[str]) -> int | None:
    """1-based rank of the first hit whose title ends in a quoted file (the importer appends ` · <file>`)."""
    for i, h in enumerate(hits):
        title = str(h.get("title") or "")
        tail = title.rsplit(" · ", 1)[-1]
        if any(
            f and (tail == f or tail.endswith("/" + f) or f.endswith("/" + tail) or f == tail) for f in files
        ):
            return i + 1
    return None


async def recall(
    call: Call, slug: str, rows: list[dict[str, Any]], *, k: int = 5, mask: Callable[..., str] = redact
) -> dict[str, Any]:
    per: list[dict[str, Any]] = []
    hit = scored = 0
    for r in rows:
        res = await call("memory.query", {"project": slug, "query": r["question"], "token_budget": 3000})
        hits = (res.get("hits") or [])[:k]
        files = {str(q.get("file") or "") for q in (r.get("quotes") or []) if isinstance(q, dict)}
        rank = rank_of(hits, files)
        negative = r.get("category") == "negative"
        if not negative:
            scored += 1
            hit += rank is not None
        per.append(
            {
                "id": r["id"],
                "lang": r.get("lang"),
                "category": r.get("category"),
                "rank": rank,
                "evidence": res.get("evidence"),
                "top": [mask(str(h.get("title") or ""), width=90) for h in hits[:3]],
            }
        )
    return {
        "k": k,
        "hit": hit,
        "scored": scored,
        "rate": round(hit / scored, 3) if scored else None,
        "rows": per,
    }


__all__ = ["load_truthset", "rank_of", "recall"]
