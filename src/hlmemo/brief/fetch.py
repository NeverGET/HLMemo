"""Read-only fetch for the brief: memory.query + memory.raw through the hlm client (direct path only).

SUPERSESSION (D-207 defect #5). ``memory.query`` hits carry no superseded flag; the server only hides a
superseded item when its superseder is ALSO among the same query's hits (``read_service.query_parts`` ->
``librarian_queries.supersession_among``). The only per-item field a client can read is
``memory.raw``'s ``links`` (OUTGOING edges of the item: ``rel``, ``dst_logical_id``, ``valid_to``,
``superseded_at``) and its own ``logical_id``. So the brief reads ``memory.raw`` for every candidate
(it needs the verbatim body anyway) and excludes a candidate when ANY item of the candidate pool has a
LIVE ``supersedes`` link to its ``logical_id`` (``superseded_pool_ids``). Gaps, reported not guessed:

* the superseder must be in the pool (the newest session notes and lessons); a superseder of another
  kind, or older than the pool, is not seen (``memory.raw`` has no incoming-link view);
* ``scope=part`` (fact-level, D-076) links look like whole links here (``props`` is not exposed), so a
  partly superseded item is excluded as a whole: safe, but it also drops its still-valid statements;
* a candidate whose ``memory.raw`` failed cannot be verified and is excluded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

POOL_SESSIONS = 8  # newest session-note candidates read (3 are shown)
POOL_LESSONS = 14  # newest lesson candidates read (5 are shown)
QUERY_BUDGET = 8000
RAW_BUDGET_SESSION = 16000
RAW_BUDGET_LESSON = 6000
RAW_PAGES = 3
CONCURRENCY = 8
SESSION_QUERIES = ("session notes decisions open uncertain", "Session")
LESSON_QUERY = "lesson rule mistake"


@dataclass
class Item:
    version_id: int
    kind: str
    title: str
    valid_from: datetime | None
    tags: list[str]
    body: str = ""
    logical_id: int | None = None
    recorded_at: datetime | None = None
    live_supersedes: set[int] = field(default_factory=set)  # logical ids this item supersedes
    current: bool = False  # verified current by memory.raw
    verified: bool = False  # memory.raw (body + links) was read completely

    @property
    def handle(self) -> str:
        return f"v{self.version_id}"

    @property
    def auto(self) -> bool:
        """Auto-captured (the capture hook marks its notes and tags its lessons): unreviewed."""
        return "auto-capture" in self.tags or "AUTO-CAPTURED" in self.body[:400].upper()


@dataclass
class Snapshot:
    project: str
    card: dict[str, Any] | None = None  # the query envelope's card: clue, text, stale, ...
    pending: int = 0
    notices: list[dict[str, Any]] = field(default_factory=list)
    sessions: list[Item] = field(default_factory=list)  # verified, current, not superseded; newest first
    lessons: list[Item] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)  # (handle, reason)
    as_of: datetime | None = None  # newest recorded_at among the items read
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_ts(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _vid(clue: Any) -> int | None:
    if not isinstance(clue, str) or not clue.startswith("v"):
        return None
    head = clue[1:].split(".")[0]
    return int(head) if head.isdigit() and int(head) > 0 else None


def candidates(hits: list[dict[str, Any]], limit: int) -> list[Item]:
    """One item per version (chunk hits deduplicated), newest ``valid_from`` first."""
    seen: dict[int, Item] = {}
    for h in hits:
        vid = _vid(h.get("clue"))
        if vid is None or vid in seen:
            continue
        seen[vid] = Item(
            version_id=vid,
            kind=str(h.get("kind") or ""),
            title=str(h.get("title") or ""),
            valid_from=parse_ts(h.get("valid_from")),
            tags=[str(t) for t in (h.get("tags") or [])],
        )
    floor = datetime.min.replace(tzinfo=UTC)
    ordered = sorted(seen.values(), key=lambda i: (i.valid_from or floor, i.version_id), reverse=True)
    return ordered[:limit]


def _live(link: dict[str, Any], now: datetime) -> bool:
    """A link is live unless it expired (valid_to) or was retracted/replaced (superseded_at)."""
    for k in ("valid_to", "superseded_at"):
        t = parse_ts(link.get(k))
        if t is not None and t <= now:
            return False
    return True


async def read_raw(call: Call, project: str, item: Item, budget: int, now: datetime) -> None:
    """memory.raw -> body, logical id, currency and live supersedes edges (all pages); raises on error."""
    args: dict[str, Any] = {"project": project, "version_id": item.version_id, "token_budget": budget}
    body: str | None = None
    segments: list[str] = []
    links: list[dict[str, Any]] = []
    for _ in range(RAW_PAGES):
        r = await call("memory.raw", args)
        if body is None:
            pi = r.get("payload_item") or {}
            if isinstance(pi.get("body"), str):
                body = pi["body"]
            item.logical_id = r.get("logical_id") if isinstance(r.get("logical_id"), int) else None
            item.recorded_at = parse_ts(r.get("recorded_at"))
            vt, sa = parse_ts(r.get("valid_to")), parse_ts(r.get("superseded_at"))
            item.current = (
                (vt is None or vt > now) and (sa is None or sa > now) and r.get("kind") == item.kind
            )
        segments += [s.get("text", "") for s in (r.get("payload_body") or []) if isinstance(s, dict)]
        links += [x for x in (r.get("links") or []) if isinstance(x, dict)]
        cur = r.get("next_cursor")
        if not cur:
            item.verified = True
            break
        args = {**args, "cursor": cur}
    item.body = body if body is not None else "".join(segments)
    item.live_supersedes = {
        int(x["dst_logical_id"])
        for x in links
        if x.get("rel") == "supersedes" and isinstance(x.get("dst_logical_id"), int) and _live(x, now)
    }


def superseded_pool_ids(pool: list[Item]) -> set[int]:
    """Logical ids superseded by a LIVE ``supersedes`` link from any verified item of the pool."""
    out: set[int] = set()
    for it in pool:
        if it.verified:
            out |= it.live_supersedes
    return out


async def _bounded(sem: asyncio.Semaphore, coro: Awaitable[Any]) -> Any:
    async with sem:
        return await coro


async def gather_snapshot(call: Call, project: str, *, now: datetime | None = None) -> Snapshot:
    """Everything the brief shows, read-only. Raises only if NOTHING could be read."""
    now = now or datetime.now(UTC)
    snap = Snapshot(project=project, now=now)

    def q(query: str, kind: str) -> Awaitable[dict[str, Any]]:
        return call(
            "memory.query",
            {"project": project, "query": query, "token_budget": QUERY_BUDGET, "kinds": [kind]},
        )

    results = await asyncio.gather(
        q(LESSON_QUERY, "lesson"), *[q(s, "session_note") for s in SESSION_QUERIES], return_exceptions=True
    )
    ok = [r for r in results if isinstance(r, dict)]
    if not ok:
        err = next((r for r in results if isinstance(r, BaseException)), RuntimeError("no result"))
        raise err
    for r in ok:  # the card and the librarian block ride on every query envelope
        if isinstance(r.get("card"), dict) and snap.card is None:
            snap.card = r["card"]
        lib = r.get("librarian")
        if isinstance(lib, dict) and not snap.pending:
            n = lib.get("pending_questions")
            snap.pending = n if isinstance(n, int) and n > 0 else 0
            snap.notices = [x for x in (lib.get("notices") or []) if isinstance(x, dict)]
    lesson_res = results[0] if isinstance(results[0], dict) else {}
    sess_hits = [h for r in results[1:] if isinstance(r, dict) for h in (r.get("hits") or [])]
    s_pool = candidates(sess_hits, POOL_SESSIONS)
    l_pool = candidates(lesson_res.get("hits") or [], POOL_LESSONS)

    sem = asyncio.Semaphore(CONCURRENCY)
    jobs = [(it, RAW_BUDGET_SESSION) for it in s_pool] + [(it, RAW_BUDGET_LESSON) for it in l_pool]
    outcomes = await asyncio.gather(
        *[_bounded(sem, read_raw(call, project, it, b, now)) for it, b in jobs], return_exceptions=True
    )
    for (it, _b), res in zip(jobs, outcomes, strict=True):
        if isinstance(res, BaseException):
            it.verified = False

    dead = superseded_pool_ids(s_pool + l_pool)

    def keep(pool: list[Item]) -> list[Item]:
        out: list[Item] = []
        for it in pool:
            if not it.verified:
                snap.excluded.append((it.handle, "unverified"))
            elif not it.current:
                snap.excluded.append((it.handle, "not-current"))
            elif it.logical_id is not None and it.logical_id in dead:
                snap.excluded.append((it.handle, "superseded"))
            elif it.logical_id is None:
                snap.excluded.append((it.handle, "unverified"))
            else:
                out.append(it)
        return out

    snap.sessions, snap.lessons = keep(s_pool), keep(l_pool)
    stamps = [i.recorded_at for i in snap.sessions + snap.lessons if i.recorded_at is not None]
    snap.as_of = max(stamps) if stamps else None
    return snap


# --------------------------------------------------------------------------- the real client


def open_call_factory(project_cfg: Any, timeout_s: float):
    """``(async context manager yielding call, project)`` via the hlm client stack, or None when no token
    is available. Direct path only: the relay fallback of capture runs an LLM and cannot fit the budget."""
    from hlmemo.cli import credentials
    from hlmemo.cli.client_config import default_device_name, resolve_client_config
    from hlmemo.cli.mcp_client import MemoryClient

    ccfg = resolve_client_config(server_url=project_cfg.server_url, device_name=project_cfg.device_name)
    token = credentials.load_token(ccfg.server_url, ccfg.device_name or default_device_name())
    if not token:
        return None
    return MemoryClient(ccfg.mcp, token, timeout_s=timeout_s).session()
