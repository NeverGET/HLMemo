"""``memory.query`` / ``memory.drilldown`` / ``memory.raw`` (PHASE0-SPEC §3, §4).

Each function takes an idle connection, opens one transaction, authorizes the call against the
project (``read`` grant) and returns a plain dict that is already budget-packed (``budget.used``
is the exact o200k measure of its canonical JSON and never exceeds ``limit``). Errors are
``ToolError`` (``core/errors.py``) with the spec's codes.

* query: no write. Steps 1-12 of §4.
* drilldown / raw: record an ``access`` event and touch ``last_access_at`` (D-012) in the same
  transaction; cursors are ``hlmemo.auth.cursors`` HMAC tokens bound to device + generation, and a
  continuation re-runs the full authorization inside its own transaction (§2).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.cursors import load_cursor_secret, sign_cursor, verify_cursor
from hlmemo.auth.errors import HlmError
from hlmemo.config import get_settings
from hlmemo.core import MODEL_ID, MODEL_REVISION
from hlmemo.core.budget import BudgetError, Meter, canonical, validate_budget
from hlmemo.core.clues import Clue, InvalidClue, decode_clue, encode_clue
from hlmemo.core.embedder import Embedder, default_model_dir
from hlmemo.core.errors import ToolError
from hlmemo.core.read_models import DrilldownRequest, QueryRequest, RawRequest, parse_request
from hlmemo.core.retrieval import (
    L_MAX,
    T_MAX,
    TI_MAX,
    TRGM_WORD_SIMILARITY_THRESHOLD,
    V_MAX,
    CardInput,
    dedupe_and_order,
    pack_prefix,
    pack_query,
    rrf_fuse,
    split_terms,
)
from hlmemo.core.supersession import newer_first_on_ties
from hlmemo.core.temporal import fmt_ts, parse_opt_ts
from hlmemo.core.term_stats import StatsCache
from hlmemo.db import import_queries as iq
from hlmemo.db import librarian_queries as lq
from hlmemo.db import read_queries as q
from hlmemo.db.write_queries import ProjectRef
from hlmemo.librarian.questions import pending_block

PREPROC_VERSION = 1
QUERY_CONTRACT = "query/2"  # W2b: optional `librarian` block + D-057 supersession (CC-4)
LIBRARIAN_SHARE = 0.10  # the `librarian` block may take at most this share of token_budget (after hits)
MIN_HIT_TOKENS = 40  # lower bound of a rendered hit; bounds how many rows are fetched for packing
CURSOR_TOKENS = 70  # additive estimate for a signed ``next_cursor`` (exact measure decides)
RAW_BODY_SEGMENT = 512  # characters per ``payload_body`` unit when raw pages the verbatim body (D-026)
TOOL_QUERY = "memory.query"
TOOL_DRILLDOWN = "memory.drilldown"
TOOL_RAW = "memory.raw"


# --------------------------------------------------------------------------- dependencies
@dataclass(slots=True)
class ReadDeps:
    meter: Meter
    model_dir: Path
    cursor_secret: bytes
    _embedder: Embedder | None = field(default=None, repr=False)
    term_stats: StatsCache = field(default_factory=StatsCache, repr=False)  # D-055 DF cache

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder(self.model_dir, threads=get_settings().embed_intra_op_num_threads)
        return self._embedder


@lru_cache(maxsize=4)
def _deps_for(model_dir: str) -> ReadDeps:
    return ReadDeps(meter=Meter(), model_dir=Path(model_dir), cursor_secret=load_cursor_secret())


def default_read_deps(model_dir: str | Path | None = None) -> ReadDeps:
    """Standalone service dependencies; HTTP uses the app lifespan's injected ReadDeps."""
    return _deps_for(str(Path(model_dir) if model_dir else default_model_dir()))


# --------------------------------------------------------------------------- shared helpers
def _budget(raw: object) -> int:
    try:
        return validate_budget(raw)
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc


async def _read_project(conn: AsyncConnection, ctx: AuthContext, slug: str) -> ProjectRef:
    """§2: the slug resolves and the device holds ``read`` (unknown slug → same error, no enumeration)."""
    ref = await q.resolve_project(conn, slug)
    if ref is None or not ctx.has(ref.project_id, Role.READ):
        raise ToolError("E_FORBIDDEN_PROJECT", f"no read grant on project {slug!r}", project=slug)
    return ref


async def _as_of(
    conn: AsyncConnection, valid_at: str | None, known_at: str | None
) -> tuple[datetime, datetime]:
    now = await q.clock_now(conn)
    va = parse_opt_ts(valid_at, field="valid_at") or now
    ka = parse_opt_ts(known_at, field="known_at") or now
    return va, ka


def _statuses(include_archived: bool) -> list[str]:
    return ["active", "archived"] if include_archived else ["active"]


def _sha(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def _verify_cursor(deps: ReadDeps, ctx: AuthContext, cursor: str) -> dict[str, Any]:
    try:
        return verify_cursor(deps.cursor_secret, cursor, ctx)
    except HlmError as exc:
        raise ToolError("E_INVALID_CURSOR", exc.message) from exc


def _not_found(what: str) -> ToolError:
    return ToolError("E_NOT_FOUND", f"{what} not found")


def _pack_page(
    meter: Meter,
    envelope: dict[str, Any],
    budget: int,
    n_total: int,
    apply: Callable[[int], None],
    estimate: Callable[[int], int],
) -> int:
    """Pack a paged drilldown/raw result; returns the number of units in the page.

    The size is not monotonic in the unit count: every partial page carries a signed
    ``next_cursor``, the complete page does not. So the complete page is measured exactly first
    and wins whenever it fits; only otherwise is the largest cursor-bearing prefix
    ``1 ≤ n < n_total`` packed. A page must hold at least one unit (or be complete), else
    ``E_BUDGET_TOO_SMALL`` with ``min`` = the smallest exact size that would be served
    (F17: the first partial page or the complete page, whichever is smaller)."""
    apply(n_total)
    full = meter.settle(envelope, budget)
    if full <= budget:
        return n_total
    n = 0
    if n_total > 1:
        try:
            n, _used = pack_prefix(meter, envelope, budget, n_total - 1, apply, estimate)
        except BudgetError:
            n = 0
    if n > 0:
        return n
    need = full
    if n_total > 1:
        apply(1)
        need = min(need, meter.settle(envelope, budget))
    what = "the first chunk" if n_total else "the result envelope"
    raise ToolError("E_BUDGET_TOO_SMALL", f"token_budget {budget} cannot hold {what}", min=need)


# --------------------------------------------------------------------------- memory.query
async def query(
    conn: AsyncConnection,
    ctx: AuthContext,
    req: QueryRequest | dict[str, Any],
    *,
    deps: ReadDeps | None = None,
) -> dict[str, Any]:
    request = parse_request(QueryRequest, req)
    deps = deps or default_read_deps()
    packed, librarian = await query_parts(conn, ctx, request, deps=deps)
    if librarian is not None:
        add_librarian_block(deps.meter, packed, librarian, int(packed["budget"]["limit"]))
    return packed


async def query_parts(
    conn: AsyncConnection,
    ctx: AuthContext,
    req: QueryRequest | dict[str, Any],
    *,
    deps: ReadDeps,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """``memory.query`` without its optional ``librarian`` block: ``(packed result, block | None)``.
    ``query`` packs the block after the hits; the W2e synthesis (``synthesis_service``) packs it
    after the hits AND the synthesis, so one exact ``budget.used`` covers everything (G2)."""
    request = parse_request(QueryRequest, req)
    budget = _budget(request.token_budget)
    async with conn.transaction():
        project = await _read_project(conn, ctx, request.project)
        now = await q.clock_now(conn)
        valid_at = parse_opt_ts(request.valid_at, field="valid_at") or now
        known_at = parse_opt_ts(request.known_at, field="known_at") or now
        scopes = list(ctx.scope_values())
        filters = q.QueryFilters(
            pid=project.project_id,
            scopes=scopes,
            valid_at=valid_at,
            known_at=known_at,
            statuses=_statuses(request.include_archived),
            kinds=list(request.kinds) if request.kinds is not None else None,
        )

        # D-055: DF describes today's corpus; a historical query (valid_at/known_at in the past)
        # is not filtered by it, so a term distinctive back then is never dropped using later data.
        historical = valid_at < now or known_at < now
        stats = None if historical else await deps.term_stats.get(conn, project.project_id)
        terms = split_terms(request.query, stats)
        qvec = deps.embedder.embed_query(request.query)

        lexical = await q.lexical_candidates(conn, filters, terms.lexical_text, L_MAX)
        title = await q.title_candidates(conn, filters, terms.title, TI_MAX)
        trigram: list[q.Candidate] = []
        if terms.identifiers:
            await q.set_trigram_threshold(conn, TRGM_WORD_SIMILARITY_THRESHOLD)
            trigram = await q.trigram_candidates(conn, filters, terms.identifiers, T_MAX)
        vector = await q.vector_candidates(
            conn,
            filters,
            qvec,
            model=MODEL_ID,
            revision=MODEL_REVISION,
            preproc_version=PREPROC_VERSION,
            limit=V_MAX,
        )
        pending = await q.indexing_pending(conn, project.project_id)

        ordered = dedupe_and_order(rrf_fuse(lexical, trigram, vector, title))
        # D-057 (query/2): an applied `supersedes` link between two hits hides the superseded one
        hidden = await lq.superseded_among(
            conn,
            [f.logical_id for f in ordered],
            pid=project.project_id,
            scopes=scopes,
            valid_at=valid_at,
            known_at=known_at,
        )
        if hidden:
            ordered = [f for f in ordered if f.logical_id not in hidden]
        n_fetch = min(len(ordered), budget // MIN_HIT_TOKENS + 3)
        head = ordered[:n_fetch]
        rows = await q.hit_rows(conn, [f.chunk_id for f in head])
        for f in head:
            f.row = rows[f.chunk_id]
        head = newer_first_on_ties(head)  # D-057: exact RRF tie, same title -> newer first
        librarian = await pending_block(conn, ctx, project.project_id, now)

        card: CardInput | None = None
        found = await q.card_version(
            conn, project.project_id, scopes, project.card_logical_id, valid_at, known_at
        )
        if found is not None:
            sources = await q.pinned_sources(
                conn, project.card_logical_id, project.project_id, scopes, valid_at, known_at
            )
            card = CardInput(version_id=found[0], body=found[1], sources=sources)

    envelope: dict[str, Any] = {
        "project": project.slug,
        "as_of": {"valid_at": fmt_ts(valid_at), "known_at": fmt_ts(known_at)},
        "device_class": ctx.device_class,
        "evidence": "matched" if ordered else "none",
        "indexing_pending": pending,
        "contract_version": QUERY_CONTRACT,
    }
    try:
        packed = pack_query(
            deps.meter, envelope, budget, card, head, total=len(ordered), terms=terms.preview_terms
        )
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc
    return packed, librarian


def add_librarian_block(meter: Meter, envelope: dict[str, Any], block: dict[str, Any], budget: int) -> None:
    """query/2: the optional ``librarian`` block is packed AFTER the hits (roadmap W2b; Sol 44)
    and, with ``synthesize``, after the synthesis too (W2e): into what they left, at most
    ``LIBRARIAN_SHARE`` of the budget, dropping notices from the end, then the whole block;
    ``budget.used`` stays the exact measure of the whole result."""
    notices = list(block["notices"])
    while True:
        candidate = {**block, "notices": notices}
        if meter.count(candidate) <= budget * LIBRARIAN_SHARE:
            envelope["librarian"] = candidate
            if meter.settle(envelope, budget) <= budget:
                return
            del envelope["librarian"]
        if not notices:
            meter.settle(envelope, budget)
            return
        notices = notices[:-1]


# --------------------------------------------------------------------------- memory.drilldown
@dataclass(slots=True)
class _Unit:
    clue_index: int
    clue: str
    version: q.ReadVersion
    span: q.ChunkSpan
    link: q.DrillLink | None = None


async def _expand_clues(
    conn: AsyncConnection,
    clues: list[Clue],
    raw_clues: list[str],
    pid: int,
    scopes: list[str],
    valid_at: datetime,
    known_at: datetime,
    statuses: list[str],
) -> list[_Unit]:
    """A chunk clue → the chunk ±1 neighbour; an item clue → the body in ordinal order.
    Unknown, out-of-scope, superseded/expired or foreign clue → ``E_NOT_FOUND`` (no distinction)."""
    units: list[_Unit] = []
    versions: dict[int, q.ReadVersion] = {}
    for i, clue in enumerate(clues):
        v = versions.get(clue.version_id)
        if v is None:
            v = await q.version_live(conn, clue.version_id, pid, scopes, valid_at, known_at, statuses)
            if v is None:
                raise _not_found("clue")
            versions[clue.version_id] = v
        if clue.is_chunk:
            assert clue.ordinal is not None
            spans = await q.chunk_spans(conn, v.version_id, clue.ordinal - 1, clue.ordinal + 1)
            if not any(s.ordinal == clue.ordinal for s in spans):
                raise _not_found("clue")
        else:
            spans = await q.chunk_spans(conn, v.version_id)
            if not spans:  # a version without chunks: the body as one span
                spans = [q.ChunkSpan(0, 0, len(v.body), v.body)]
        units.extend(_Unit(i, raw_clues[i], v, s) for s in spans)
    return units


def _render_items(units: list[_Unit]) -> list[dict[str, Any]]:
    """A page may contain body spans, edges, or both; each atom appears exactly once."""
    items: list[dict[str, Any]] = []
    for u in units:
        if not items or items[-1]["_ci"] != u.clue_index:
            v = u.version
            items.append(
                {
                    "_ci": u.clue_index,
                    "_start": None,
                    "clue": u.clue,
                    "kind": v.kind,
                    "title": v.title,
                    "text": "",
                    "ordinal_range": [u.span.ordinal, u.span.ordinal],
                    "device_scope": v.device_scope,
                    "links": [],
                }
            )
        it = items[-1]
        if u.link is not None:
            it["links"].append(
                {"rel": u.link.rel, "clue": encode_clue(u.link.clue_version_id), "stale": u.link.stale}
            )
        else:
            if it["_start"] is None:
                it["_start"] = u.span.char_start
                it["ordinal_range"][0] = u.span.ordinal
            it["ordinal_range"][1] = u.span.ordinal
            it["text"] = u.version.body[it["_start"] : u.span.char_end]
    return [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]


async def drilldown(
    conn: AsyncConnection,
    ctx: AuthContext,
    req: DrilldownRequest | dict[str, Any],
    *,
    deps: ReadDeps | None = None,
) -> dict[str, Any]:
    request = parse_request(DrilldownRequest, req)
    deps = deps or default_read_deps()
    budget = _budget(request.token_budget)
    try:
        clues = [decode_clue(c) for c in request.clue_ids]
    except InvalidClue as exc:  # defence in depth; the request model already rejects these
        raise ToolError("E_INVALID_ARG", str(exc)) from exc
    clue_hash = _sha(
        {
            "project": request.project,
            "clues": request.clue_ids,
            "valid_at": request.valid_at,
            "known_at": request.known_at,
            "include_archived": request.include_archived,
        }
    )
    async with conn.transaction():
        project = await _read_project(conn, ctx, request.project)
        resume = 0
        if request.cursor is not None:
            p = _verify_cursor(deps, ctx, request.cursor)
            if p.get("tool") != TOOL_DRILLDOWN or p.get("h") != clue_hash:
                raise ToolError("E_INVALID_CURSOR", "cursor does not belong to this drilldown")
            valid_at, known_at = await _as_of(conn, p.get("valid_at"), p.get("known_at"))
            resume = int(p.get("i", 0))
        else:
            valid_at, known_at = await _as_of(conn, request.valid_at, request.known_at)
        scopes = list(ctx.scope_values())
        statuses = _statuses(request.include_archived)
        units = await _expand_clues(
            conn, clues, request.clue_ids, project.project_id, scopes, valid_at, known_at, statuses
        )
        # Group each clue's chunks followed by its edges. Edges are independent packing
        # units, so a long list cannot make every page fail before any progress is possible.
        links: dict[int, list[q.DrillLink]] = {}
        expanded: list[_Unit] = []
        for i, u in enumerate(units):
            expanded.append(u)
            if i + 1 < len(units) and units[i + 1].clue_index == u.clue_index:
                continue
            lid = u.version.logical_id
            if lid not in links:
                links[lid] = await q.drilldown_links(
                    conn, lid, project.project_id, scopes, valid_at, known_at
                )
            expanded.extend(_Unit(u.clue_index, u.clue, u.version, u.span, ln) for ln in links[lid])
        units = expanded
        if request.cursor is not None:
            if not (0 <= resume < len(units)) or (
                units[resume].version.version_id != p.get("version_id")
                or units[resume].span.ordinal != p.get("ordinal")
            ):
                raise ToolError("E_INVALID_CURSOR", "cursor position is not valid any more")
        page = units[resume:]
        meter = deps.meter
        envelope: dict[str, Any] = {"items": [], "next_cursor": None}
        base = meter.settle(envelope, budget) + CURSOR_TOKENS
        prefix = [0]
        for u in page:
            prefix.append(prefix[-1] + meter.count(_render_items([u])))

        def cursor_for(n: int) -> str | None:
            if n >= len(page):
                return None
            nxt = page[n]
            return sign_cursor(
                deps.cursor_secret,
                ctx,
                {
                    "tool": TOOL_DRILLDOWN,
                    "h": clue_hash,
                    "i": resume + n,
                    "version_id": nxt.version.version_id,
                    "ordinal": nxt.span.ordinal,
                    "valid_at": fmt_ts(valid_at),
                    "known_at": fmt_ts(known_at),
                },
            )

        def apply(n: int) -> None:
            envelope["items"] = _render_items(page[:n])
            envelope["next_cursor"] = cursor_for(n)

        n = _pack_page(meter, envelope, budget, len(page), apply, lambda k: base + prefix[k])

        at = await q.clock_now(conn)
        await q.record_access(
            conn,
            pid=project.project_id,
            device_id=ctx.device_id,
            client=ctx.client,
            tool=TOOL_DRILLDOWN,
            request=request.model_dump(mode="json", exclude_none=True),
            version_ids=[u.version.version_id for u in page[:n]],
            at=at,
            payload_sha256=_sha(request.model_dump(mode="json", exclude_none=True)),
        )
    return envelope


# --------------------------------------------------------------------------- memory.raw
def _locate(payload: dict[str, Any], version_id: int) -> tuple[int | None, int | None]:
    """``(index, from_version_id)`` of ``version_id`` in an event's ``payload.resolved.items``:
    ``index`` when the version is an item's own row, ``from_version_id`` when it is a surviving
    segment (§1.1 (3)) copied unchanged from that version by this (correcting) event."""
    for it in (payload.get("resolved") or {}).get("items", []):
        if it.get("version_id") == version_id:
            return it.get("index"), None
        for s in it.get("survivors", []):
            if s.get("version_id") == version_id:
                return None, s.get("from_version_id")
    return None, None


def _request_items(payload: dict[str, Any]) -> list[Any]:
    items = (payload.get("request") or {}).get("items")
    if not isinstance(items, list):  # call_the_day: the derived write batch is recorded in resolved.write
        items = ((payload.get("resolved") or {}).get("write") or {}).get("items", [])
    return items if isinstance(items, list) else []


def _resolved_links(payload: dict[str, Any], index: int) -> list[Any]:
    for it in (payload.get("resolved") or {}).get("items", []):
        if it.get("index") == index:
            links = it.get("links")
            return links if isinstance(links, list) else []
    return []


async def _filter_item_links(
    conn: AsyncConnection, item: dict[str, Any], resolved_links: list[Any], pid: int, scopes: list[str]
) -> dict[str, Any]:
    """S1b: every link target named in the verbatim item is authorized with the endpoint rule of
    ``raw_links`` (§4.4 (a): the pinned ``dst_version_id`` when the write pinned one, else any
    version of ``dst_logical_id``); unauthorized targets are omitted, never named."""
    links = item.get("links")
    if not isinstance(links, list) or not links:
        return item
    targets: list[tuple[int | None, int | None]] = []
    for j, ln in enumerate(links):
        r = resolved_links[j] if j < len(resolved_links) and isinstance(resolved_links[j], dict) else {}
        vid, lid = r.get("dst_version_id"), r.get("dst_logical_id")
        if vid is None and lid is None and isinstance(ln, dict):  # no resolved record: the request's ids
            tv, t = ln.get("target_version_id"), ln.get("target")
            if isinstance(tv, int) and not isinstance(tv, bool):
                vid = tv
            elif isinstance(t, int) and not isinstance(t, bool):
                lid = t
        targets.append((vid if isinstance(vid, int) else None, lid if isinstance(lid, int) else None))
    lids_ok, vids_ok = await q.endpoint_authz(
        conn,
        pid,
        scopes,
        logical_ids=[lid for vid, lid in targets if vid is None and lid is not None],
        version_ids=[vid for vid, _ in targets if vid is not None],
    )
    kept = [
        ln
        for ln, (vid, lid) in zip(links, targets, strict=True)
        if ((vid in vids_ok) if vid is not None else (lid is not None and lid in lids_ok))
    ]
    return {**item, "links": kept}


async def _provenance(
    conn: AsyncConnection, v: q.ReadVersion, pid: int, scopes: list[str]
) -> tuple[q.SourceEvent | None, dict[str, Any]]:
    """The content provenance of ``v``: the event that wrote its content and that event's verbatim
    request item (S1).

    A surviving segment (§1.1 (3)) is stored under the *correcting* event, whose request item is
    the replacement content — possibly under a narrower ``device_scope``/``project_ids`` than the
    survivor keeps. Returning that item through a readable survivor would leak it, so the walk
    follows ``survivors[].from_version_id`` to the version the content was actually written as,
    authorizing every hop with (a) exactly like the addressed row; a hop failing (a) is
    ``E_NOT_FOUND`` (uniform, no distinction from an unknown version)."""
    cur = v
    visited: set[int] = set()
    while cur.version_id not in visited:
        visited.add(cur.version_id)
        ev = await q.source_event(conn, cur.source_event_id)
        if ev is None:
            return None, {}
        index, origin_vid = _locate(ev.payload, cur.version_id)
        if index is not None:
            items = _request_items(ev.payload)
            item = items[index] if 0 <= index < len(items) else {}
            if not isinstance(item, dict):
                item = {}
            return ev, await _filter_item_links(conn, item, _resolved_links(ev.payload, index), pid, scopes)
        if origin_vid is None:  # not recorded by its own event: nothing verbatim to show
            return ev, {}
        origin = await q.version_authz(conn, origin_vid, pid, scopes)
        if origin is None:
            raise _not_found("version")
        cur = origin
    raise _not_found("version")


async def raw(
    conn: AsyncConnection,
    ctx: AuthContext,
    req: RawRequest | dict[str, Any],
    *,
    deps: ReadDeps | None = None,
) -> dict[str, Any]:
    request = parse_request(RawRequest, req)
    deps = deps or default_read_deps()
    budget = _budget(request.token_budget)
    async with conn.transaction():
        project = await _read_project(conn, ctx, request.project)
        scopes = list(ctx.scope_values())
        v = await q.version_authz(conn, request.version_id, project.project_id, scopes)
        if v is None:
            raise _not_found("version")
        resume = 0
        body_paged: bool | None = None  # None: decided on the first page (D-026)
        read_hash = _sha({"project": request.project, "version_id": request.version_id})
        if request.cursor is not None:
            p = _verify_cursor(deps, ctx, request.cursor)
            if p.get("tool") != TOOL_RAW or p.get("h") != read_hash:
                raise ToolError("E_INVALID_CURSOR", "cursor does not belong to this version")
            resume = int(p.get("i", 0))
            body_paged = bool(p.get("pb", 0))
            _, known_at = await _as_of(conn, None, p.get("known_at"))
        else:
            known_at = await q.clock_now(conn)
        links = await q.raw_links(conn, v, project.project_id, scopes, known_at)
        slugs = await q.project_slugs(conn, v.project_ids)
        ev, payload_item = await _provenance(conn, v, project.project_id, scopes)
        spans = await q.chunk_spans(conn, v.version_id)

        envelope: dict[str, Any] = {
            "version_id": v.version_id,
            "logical_id": v.logical_id,
            "kind": v.kind,
            "project_ids": [slugs[pid] for pid in v.project_ids if pid in slugs],
            "device_scope": v.device_scope,
            "valid_from": fmt_ts(v.valid_from),
            "valid_to": fmt_ts(v.valid_to),
            "recorded_at": fmt_ts(v.recorded_at),
            "superseded_at": fmt_ts(v.superseded_at),
            "supersedes_version_id": v.supersedes_version_id,
            "source_event": None
            if ev is None
            else {
                "event_id": ev.event_id,
                "request_id": ev.request_id,
                "device": {"id": ev.device_id, "name": ev.device_name, "class": ev.device_class},
                "client": ev.client,
                "occurred_at": fmt_ts(ev.occurred_at),
                "recorded_at": fmt_ts(ev.recorded_at),
            },
            "payload_item": payload_item,
            # W1.5 (G-I2, additive): the row's own provenance and its describes projection
            "source": await iq.version_source(conn, v.version_id),
            "code_refs": [
                {"path": path, "commit": commit}
                for path, commit in (await iq.code_refs_of(conn, [v.version_id]))[v.version_id]
            ],
            "links": [],
            "chunks": [],
            "next_cursor": None,
        }
        meter = deps.meter
        rendered = [
            {"ordinal": s.ordinal, "char_start": s.char_start, "char_end": s.char_end, "text": s.text}
            for s in spans
        ]
        rendered_links = [
            {
                "rel": ln.rel,
                "dst_logical_id": ln.dst_logical_id,
                "dst_version_id": ln.dst_version_id,
                "valid_from": fmt_ts(ln.valid_from),
                "valid_to": fmt_ts(ln.valid_to),
                "recorded_at": fmt_ts(ln.recorded_at),
                "superseded_at": fmt_ts(ln.superseded_at),
            }
            for ln in links
        ]
        # D-026: a verbatim payload_item too large for the budget is no longer E_BUDGET_TOO_SMALL.
        # Its body is moved into the cursor stream as `payload_body` segments (fixed character
        # windows, deterministic across pages); `payload_item` keeps every other field plus
        # `"body_paged": true`. The mode is decided on the first page and frozen in the cursor.
        if body_paged is None:
            body_paged = (
                isinstance(payload_item.get("body"), str)
                and meter.settle(envelope, budget) + CURSOR_TOKENS > budget
            )
        body_units: list[tuple[str, dict[str, Any]]] = []
        if body_paged and isinstance(payload_item.get("body"), str):
            body = payload_item["body"]
            envelope["payload_item"] = {
                **{k: v for k, v in payload_item.items() if k != "body"},
                "body_paged": True,
            }
            envelope["payload_body"] = []
            body_units = [
                (
                    "payload_body",
                    {
                        "char_start": a,
                        "char_end": min(a + RAW_BODY_SEGMENT, len(body)),
                        "text": body[a : a + RAW_BODY_SEGMENT],
                    },
                )
                for a in range(0, len(body), RAW_BODY_SEGMENT)
            ]
        # Chunks and links share a single ordered cursor stream; metadata repeats on each
        # page, while the potentially unbounded collections do not.
        units = body_units + [("chunks", r) for r in rendered] + [("links", r) for r in rendered_links]
        if request.cursor is not None and not 0 <= resume < len(units):
            raise ToolError("E_INVALID_CURSOR", "cursor position is not valid any more")
        page = units[resume:]
        base = meter.settle(envelope, budget) + CURSOR_TOKENS
        prefix = [0]
        for _, r in page:
            prefix.append(prefix[-1] + meter.count(r) + 1)

        def apply(n: int) -> None:
            if body_units:
                envelope["payload_body"] = [r for kind, r in page[:n] if kind == "payload_body"]
            envelope["chunks"] = [r for kind, r in page[:n] if kind == "chunks"]
            envelope["links"] = [r for kind, r in page[:n] if kind == "links"]
            cursor_payload: dict[str, Any] = {
                "tool": TOOL_RAW,
                "h": read_hash,
                "i": resume + n,
                "known_at": fmt_ts(known_at),
            }
            if body_units:
                cursor_payload["pb"] = 1
            envelope["next_cursor"] = (
                None if n >= len(page) else sign_cursor(deps.cursor_secret, ctx, cursor_payload)
            )

        _pack_page(meter, envelope, budget, len(page), apply, lambda k: base + prefix[k])

        at = await q.clock_now(conn)
        await q.record_access(
            conn,
            pid=project.project_id,
            device_id=ctx.device_id,
            client=ctx.client,
            tool=TOOL_RAW,
            request=request.model_dump(mode="json", exclude_none=True),
            version_ids=[v.version_id],
            at=at,
            payload_sha256=_sha(request.model_dump(mode="json", exclude_none=True)),
        )
    return envelope


__all__ = [
    "ReadDeps",
    "add_librarian_block",
    "default_read_deps",
    "drilldown",
    "query",
    "query_parts",
    "raw",
]
