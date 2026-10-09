"""Read-only fetch for the brief: memory.query + memory.raw through the hlm client (direct path only).

SUPERSESSION (D-207 defect #5, closed server-side by the B3 read side). The brief PREFERS the server's
own supersession status, computed from LIVE ``supersedes`` links whose superseder the caller can see:

* a ``memory.query`` hit carries ``superseded: true`` and ``superseded_by: [{clue, scope}]`` when a live
  link targets its item (absent otherwise, and on an older server);
* ``memory.raw`` carries the INCOMING ``superseded_by: [{logical_id, version_id, scope, valid_from,
  valid_to}]`` of the addressed version (always present on a current server, so its absence tells an
  older server apart).

A candidate is excluded as ``superseded`` when a live entry has ``scope=whole``, and as
``superseded-part`` when only part-scope (fact-level, D-076) entries are live: the brief shows verbatim
lines it cannot match against the quoted outdated statement, so a partly superseded item stays out
(safe; the reason now says it was only part).

A current server's ``superseded_by`` is AUTHORITATIVE (review 98 Sol #3): it already applies a pinned
link only to its pinned version (and body-identical copies), so the brief never second-guesses it by
logical id. The OLD FALLBACK runs only for a candidate whose ``memory.raw`` has no ``superseded_by``
(an older server): ``memory.raw``'s ``links`` (OUTGOING edges of each pool item) give the pool's own
live ``supersedes`` targets (``superseded_pool_ids``); a candidate one of them names is excluded too —
an unpinned target names every version of the item, a pinned one (``dst_version_id``) only that
version. Remaining gaps on an older server, reported not guessed:

* on an older server the superseder must be in the pool (the newest session notes and lessons), and a
  ``scope=part`` link looks whole there (``props`` is not exposed by the outgoing view);
* a candidate whose ``memory.raw`` failed cannot be verified and is excluded.

BODY (review 96 Sol #5). The body is the verbatim ``payload_item.body`` (or its ``payload_body``
pages). A version a D-118 mutation wrote (a span revision's new version, a reversal's restored copy)
has no request item of its own (``payload_item == {}``): its body is rebuilt from its ``chunks``
(exact ``char_start``/``char_end`` offsets; overlapping chunks must agree, a gap is never filled —
``body_from_chunks``). A body that cannot be rebuilt leaves the candidate unverified (excluded),
never shown empty.
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
    #: ``(logical id, pinned version id or None)`` of each live ``supersedes`` target of this item
    live_supersedes: set[tuple[int, int | None]] = field(default_factory=set)
    #: memory.raw carried ``superseded_by``: a current server's authoritative incoming view (no fallback)
    server_incoming: bool = False
    current: bool = False  # verified current by memory.raw
    verified: bool = False  # memory.raw (body + links) was read completely
    #: the server's own status (query hit ``superseded_by`` / raw ``superseded_by``): "whole", "part"
    #: or None (not superseded, or an older server without the field)
    superseded: str | None = None

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
    card_date: datetime | None = None  # recorded_at of the card version (None: unknown)
    as_of: datetime | None = None  # newest recorded_at among the items read
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: how far the reads got (``gather_snapshot``): session (opening it) -> queries -> details
    #: (memory.raw of the card and the candidates) -> done
    stage: str = "session"
    #: the session-note and lesson candidates (``details`` on): ``settle`` keeps the verified ones
    pools: tuple[list[Item], list[Item]] = field(default_factory=lambda: ([], []))


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


def _worst(a: str | None, b: str | None) -> str | None:
    """The stronger of two supersession scopes (``whole`` > ``part`` > None)."""
    order = {None: 0, "part": 1, "whole": 2}
    return a if order.get(a, 2) >= order.get(b, 2) else b


def hit_superseded(h: dict[str, Any]) -> str | None:
    """The server's status of a ``memory.query`` hit: ``whole`` when a ``superseded_by`` entry is
    whole-scope (or the flag has no usable entries), ``part`` when every entry is part-scope."""
    if h.get("superseded") is not True:
        return None
    entries = [e for e in (h.get("superseded_by") or []) if isinstance(e, dict)]
    if entries and all(e.get("scope") == "part" for e in entries):
        return "part"
    return "whole"


def raw_superseded(entries: Any, now: datetime) -> str | None:
    """The server's status of a ``memory.raw`` version: the strongest scope among its incoming
    ``superseded_by`` entries that apply NOW (``valid_from <= now < valid_to``)."""
    out: str | None = None
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        vf, vt = parse_ts(e.get("valid_from")), parse_ts(e.get("valid_to"))
        if (vf is not None and vf > now) or (vt is not None and vt <= now):
            continue
        out = _worst(out, "part" if e.get("scope") == "part" else "whole")
    return out


def candidates(hits: list[dict[str, Any]], limit: int) -> list[Item]:
    """One item per version (chunk hits deduplicated, their supersession status merged), newest
    ``valid_from`` first."""
    seen: dict[int, Item] = {}
    for h in hits:
        vid = _vid(h.get("clue"))
        if vid is None:
            continue
        if vid in seen:
            seen[vid].superseded = _worst(seen[vid].superseded, hit_superseded(h))
            continue
        seen[vid] = Item(
            version_id=vid,
            kind=str(h.get("kind") or ""),
            title=str(h.get("title") or ""),
            valid_from=parse_ts(h.get("valid_from")),
            tags=[str(t) for t in (h.get("tags") or [])],
            superseded=hit_superseded(h),
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


def body_from_chunks(chunks: list[Any]) -> str | None:
    """The text a version's ``chunks`` tile (``text == body[char_start:char_end]``; neighbouring
    chunks overlap and must agree on the overlap), from the first chunk's start to the last one's
    end; ``None`` when there is no chunk, one is malformed, two disagree or a gap lies between two
    (never guessed). Leading/trailing whitespace outside the chunks is not part of it (the chunker
    trims window edges)."""
    spans: list[tuple[int, int, str]] = []
    for c in chunks:
        if not isinstance(c, dict):
            return None
        a, b, t = c.get("char_start"), c.get("char_end"), c.get("text")
        if (
            isinstance(a, bool)
            or isinstance(b, bool)
            or not isinstance(a, int)
            or not isinstance(b, int)
            or not isinstance(t, str)
            or not 0 <= a < b
            or len(t) != b - a
        ):
            return None
        spans.append((a, b, t))
    if not spans:
        return None
    spans.sort()
    base, end, text = spans[0][0], spans[0][1], spans[0][2]
    for a, b, t in spans[1:]:
        if a > end:  # a gap: the text between is unknown
            return None
        known = text[a - base : min(b, end) - base]
        if t[: len(known)] != known:
            return None
        if b > end:
            text += t[end - a :]
            end = b
    return text


async def read_raw(call: Call, project: str, item: Item, budget: int, now: datetime) -> None:
    """memory.raw -> body, logical id, currency and live supersedes edges (all pages); raises on error.
    A version without a verbatim request item (a D-118 mutation's) gets its body from its chunks
    (``body_from_chunks``); when that fails too the item stays unverified."""
    args: dict[str, Any] = {"project": project, "version_id": item.version_id, "token_budget": budget}
    body: str | None = None
    paged = False
    segments: list[str] = []
    links: list[dict[str, Any]] = []
    chunks: list[Any] = []
    for _ in range(RAW_PAGES):
        r = await call("memory.raw", args)
        if body is None:
            pi = r.get("payload_item") or {}
            paged = paged or pi.get("body_paged") is True
            if isinstance(pi.get("body"), str):
                body = pi["body"]
            item.logical_id = r.get("logical_id") if isinstance(r.get("logical_id"), int) else None
            item.recorded_at = parse_ts(r.get("recorded_at"))
            vt, sa = parse_ts(r.get("valid_to")), parse_ts(r.get("superseded_at"))
            item.current = (
                (vt is None or vt > now) and (sa is None or sa > now) and r.get("kind") == item.kind
            )
            # B3 (D-207 #5): the server's incoming view; when present it is authoritative (review 98)
            item.server_incoming = item.server_incoming or isinstance(r.get("superseded_by"), list)
            item.superseded = _worst(item.superseded, raw_superseded(r.get("superseded_by"), now))
        segments += [s.get("text", "") for s in (r.get("payload_body") or []) if isinstance(s, dict)]
        links += [x for x in (r.get("links") or []) if isinstance(x, dict)]
        chunks += list(r.get("chunks") or [])
        cur = r.get("next_cursor")
        if not cur:
            item.verified = True
            break
        args = {**args, "cursor": cur}
    if body is None and not paged and not segments:  # review 96 Sol #5: no request item of its own
        body = body_from_chunks(chunks)
        if body is None:
            item.verified = False  # an unknown body is never shown (empty or partial)
    item.body = body if body is not None else "".join(segments)
    # a link from the item to ITSELF is a span revision's record (D-118): it supersedes the item's
    # OLD version, never the one read here (review 96 Sol #5: it hid every revised lesson)
    item.live_supersedes = {
        (int(x["dst_logical_id"]), _pinned(x.get("dst_version_id")))
        for x in links
        if x.get("rel") == "supersedes"
        and isinstance(x.get("dst_logical_id"), int)
        and x["dst_logical_id"] != item.logical_id
        and _live(x, now)
    }


def _pinned(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def superseded_pool_ids(pool: list[Item]) -> set[tuple[int, int | None]]:
    """The ``(logical id, pinned version id or None)`` targets of every LIVE ``supersedes`` link from
    a verified item of the pool."""
    out: set[tuple[int, int | None]] = set()
    for it in pool:
        if it.verified:
            out |= it.live_supersedes
    return out


def pool_supersedes(it: Item, targets: set[tuple[int, int | None]]) -> bool:
    """The older-server fallback: an unpinned target names every version of its item, a pinned one
    (``dst_version_id``) only its own version (review 98 Sol #3)."""
    lid = it.logical_id
    return lid is not None and ((lid, None) in targets or (lid, it.version_id) in targets)


async def _bounded(sem: asyncio.Semaphore, coro: Awaitable[Any]) -> Any:
    async with sem:
        return await coro


async def _card_date(call: Call, project: str, card: dict[str, Any] | None) -> datetime | None:
    """When the card version was recorded (memory.raw); None when unknown. Never raises."""
    vid = _vid(card.get("clue")) if isinstance(card, dict) else None
    if vid is None:
        return None
    try:
        r = await call("memory.raw", {"project": project, "version_id": vid, "token_budget": 4000})
    except Exception:  # noqa: BLE001
        return None
    return parse_ts(r.get("recorded_at"))


async def gather_snapshot(
    call: Call, project: str, *, now: datetime | None = None, into: Snapshot | None = None
) -> Snapshot:
    """Everything the brief shows, read-only. Raises only if NOTHING could be read.

    ``into`` is filled as the reads come back (``stage``, the card, the candidate pools, each verified
    item), so a caller whose deadline cancels this can still ``settle`` what was read in time."""
    snap = into if into is not None else Snapshot(project=project)
    snap.now = now = now or datetime.now(UTC)
    snap.stage = "queries"

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
    snap.pools = (s_pool, l_pool)
    snap.stage = "details"

    async def card_date() -> None:  # in parallel with the candidates' reads, not one round trip before
        snap.card_date = await _card_date(call, project, snap.card)

    sem = asyncio.Semaphore(CONCURRENCY)
    jobs = [(it, RAW_BUDGET_SESSION) for it in s_pool] + [(it, RAW_BUDGET_LESSON) for it in l_pool]
    _, *outcomes = await asyncio.gather(
        card_date(),
        *[_bounded(sem, read_raw(call, project, it, b, now)) for it, b in jobs],
        return_exceptions=True,
    )
    for (it, _b), res in zip(jobs, outcomes, strict=True):
        if isinstance(res, BaseException):
            it.verified = False
    settle(snap)
    snap.stage = "done"
    return snap


def settle(snap: Snapshot) -> Snapshot:
    """The shown sessions and lessons, the exclusions and ``as_of`` from the candidate pools. A candidate
    whose memory.raw did not (completely) come back, e.g. one a deadline cut, is ``unverified`` and never
    shown. (On an older server a cut pool also knows fewer superseders: the fallback gap above.)"""
    s_pool, l_pool = snap.pools
    dead = superseded_pool_ids(s_pool + l_pool)

    def keep(pool: list[Item]) -> list[Item]:
        out: list[Item] = []
        for it in pool:
            if not it.verified:
                snap.excluded.append((it.handle, "unverified"))
            elif not it.current:
                snap.excluded.append((it.handle, "not-current"))
            elif it.superseded == "whole":  # the server's own status (preferred)
                snap.excluded.append((it.handle, "superseded"))
            elif it.superseded == "part":
                snap.excluded.append((it.handle, "superseded-part"))
            elif not it.server_incoming and pool_supersedes(it, dead):  # older server only
                snap.excluded.append((it.handle, "superseded"))
            elif it.logical_id is None:
                snap.excluded.append((it.handle, "unverified"))
            else:
                out.append(it)
        return out

    snap.excluded = []
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
