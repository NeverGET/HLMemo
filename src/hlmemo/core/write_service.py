"""``memory.write`` / ``memory.call_the_day`` (PHASE0-SPEC §1.1, §3, §6): one transaction per batch.

Order inside the transaction (§3 *Authorization order* / *Transaction*):

1. shape validation (``E_INVALID_ARG``), budget range, §1 ``project_ids`` integrity;
2. authorization (1) home project write grant, (2) every listed project, (3) revisions: home is
   immutable and equals ``project`` (else ``E_NOT_FOUND``), write on old ∪ new project sets;
3. idempotency: same ``(project, device, request_id)`` → stored result (``replayed:true``, after the
   re-authorization above) or ``E_REQUEST_ID_CONFLICT``. The key is ``events.payload_sha256`` =
   sha256 of the canonical JSON of the arguments **as received** (``raw``), never of the
   validated model — omitted vs explicit defaults (``device_scope:"all"``) or ``null`` vs absent
   are different requests. ``payload.request`` preserves those arguments; ``resolved.write``
   records validated items, including defaults and coercions, for deterministic replay. The
   event's ``resolved.hash_version`` selects retry hashing; unversioned historical events use
   the compatibility inference documented in ``_event_hash_version``;
4. ``events_one_close`` guard (call_the_day), head comparison (``E_VERSION_CONFLICT``), content
   rules (card size, temporal, device scope, link targets), ack-size budget check. A link target
   is authorized with §4.4 (a) on the *selected* endpoint (home-project membership + read grant +
   ``device_scope``) before any existence / membership distinction — a hidden, foreign or unknown
   target is one uniform ``E_NOT_FOUND``;
5. resolve ``T``, pre-allocate every id, build ``payload.resolved``, insert the event with its
   stored ``result``, supersede (every version *and every link segment* overlapping the new
   interval), insert versions / chunks / links / embed jobs.

The caller passes an *idle* connection; the service opens the transaction and commits it (an
outer transaction, if any, turns it into a savepoint and the caller commits). The MCP handler
passes the verbatim tool arguments as ``req`` (a dict is used as-is for hashing) or explicitly as
``raw=``; a model instance without ``raw`` is hashed from ``model_dump(exclude_unset=True)``.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core import (
    CHUNKER_VERSION,
    EMBEDDING_DIMS,
    MODEL_ID,
    MODEL_REVISION,
    NORMALIZER_VERSION,
)
from hlmemo.core.budget import DEFAULT_WRITE_BUDGET, BudgetError, Meter, canonical, validate_budget
from hlmemo.core.chunker import CHUNK_OVERLAP, CHUNK_TOK, Chunker
from hlmemo.core.embedder import default_model_dir, sha256_file
from hlmemo.core.errors import ToolError, invalid_arg
from hlmemo.core.normalize import normalize
from hlmemo.core.temporal import (
    Interval,
    fmt_ts,
    overlaps,
    parse_opt_ts,
    select_T,
    surviving_segments,
    validate_interval,
)
from hlmemo.core.write_models import (
    CloseRequest,
    CloseResult,
    Item,
    LinkSpec,
    VersionAck,
    WriteRequest,
    WriteResult,
    parse_request,
)
from hlmemo.db import write_queries as q

PROJECTION_VERSION = 1
PREPROC_VERSION = 1
HASH_VERSION_NORMALIZED = 1
HASH_VERSION_VERBATIM = 2
CARD_MAX_TOKENS = 512
CHUNKER_NAME = "e5-window"
JOB_KIND_EMBED = "embed"
_PESSIMISTIC_ID = 10**15
#: Bodies at least this long are tokenized/chunked/normalized OFF the event loop (Sol 35 #3): a
#: 64k-character body costs tens of milliseconds of pure CPU that would otherwise stall every
#: concurrent query on the API loop. Short bodies stay inline (a thread hop costs more).
OFFLOAD_MIN_CHARS = 2048
#: Bounded: at most this many write-path CPU tasks run at once; more wait in the executor queue
#: (the queue itself is bounded by the API's request admission).
OFFLOAD_WORKERS = 2
_CPU_POOL: ThreadPoolExecutor | None = None


def _cpu_pool() -> ThreadPoolExecutor:
    global _CPU_POOL
    if _CPU_POOL is None:
        _CPU_POOL = ThreadPoolExecutor(max_workers=OFFLOAD_WORKERS, thread_name_prefix="hlm-write-cpu")
    return _CPU_POOL


async def _cpu(fn: Callable[..., Any], text: str, *args: Any) -> Any:
    """Run a pure CPU step inline for short text, else on the bounded write-CPU pool."""
    if len(text) < OFFLOAD_MIN_CHARS:
        return fn(text, *args)
    return await asyncio.get_running_loop().run_in_executor(_cpu_pool(), fn, text, *args)


def _chunk_and_normalize(body: str, chunker: Chunker) -> tuple[list[Any], list[str]]:
    chunks = chunker.chunk(body)
    return chunks, [normalize(c.text) for c in chunks]


# --------------------------------------------------------------------------- dependencies
@dataclass(slots=True)
class WriteDeps:
    meter: Meter
    chunker: Chunker
    tokenizer_sha256: str
    #: W2b: enqueue ``librarian_write:<event_id>`` with every write (``HLM_LIBRARIAN_ENABLED``)
    librarian_enqueue: bool = False

    def chunker_descriptor(self) -> dict[str, Any]:
        return {
            "name": CHUNKER_NAME,
            "version": CHUNKER_VERSION,
            "chunk_tok": self.chunker.chunk_tok,
            "overlap": self.chunker.overlap,
            "tokenizer_sha256": self.tokenizer_sha256,
        }


@lru_cache(maxsize=4)
def _deps_for(model_dir: str) -> WriteDeps:
    d = Path(model_dir)
    from hlmemo.config import get_settings

    return WriteDeps(
        meter=Meter(),
        chunker=Chunker(d, chunk_tok=CHUNK_TOK, overlap=CHUNK_OVERLAP),
        tokenizer_sha256=sha256_file(d / "onnx" / "tokenizer.json"),
        librarian_enqueue=get_settings().librarian_enabled,
    )


def default_deps(model_dir: str | Path | None = None) -> WriteDeps:
    """Process-wide Meter + Chunker (the 17 MB tokenizer is hashed once)."""
    return _deps_for(str(Path(model_dir) if model_dir else default_model_dir()))


def embedder_descriptor() -> dict[str, Any]:
    return {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "preproc_version": PREPROC_VERSION,
        "dims": EMBEDDING_DIMS,
    }


def embed_dedupe_key(version_id: int, model: str = MODEL_ID, revision: str = MODEL_REVISION) -> str:
    """§6 outbox key ``embed:<version_id>:<model>@<rev>`` (``rev`` = first 8 hex chars, as in §1.1)."""
    return f"{JOB_KIND_EMBED}:{version_id}:{model}@{revision[:8]}"


def embed_job_payload(version_id: int, embedder: dict[str, Any]) -> dict[str, Any]:
    return {
        "version_id": version_id,
        "model": embedder["model"],
        "model_revision": embedder["revision"],
        "preproc_version": embedder["preproc_version"],
        "dims": embedder["dims"],
    }


def payload_sha256(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(request).encode("utf-8")).hexdigest()


def _legacy_request_v1(request: dict[str, Any], kind: str) -> dict[str, Any]:
    """Historical validated JSON, including defaults and omitting nulls (pre-round-8).

    Keep this serializer's field set/defaults frozen when request models evolve. Validation
    supplies coercions; the explicit v1 defaults below must not inherit later model defaults.
    """
    model = WriteRequest if kind == "write" else CloseRequest
    validated = parse_request(model, request).model_dump(mode="json", exclude_none=True)

    def fields(raw: dict[str, Any], values: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        return {
            key: values[key] if key in raw else default
            for key, default in defaults.items()
            if (key in raw and key in values) or (key not in raw and default is not None)
        }

    top_defaults = {
        "project": None,
        "request_id": None,
        "client": None,
        "occurred_at": None,
        "token_budget": None,
    }
    if kind == "write":
        result = fields(request, validated, top_defaults)
        item_defaults = {
            "kind": None,
            "logical_id": None,
            "expected_version_id": None,
            "title": None,
            "body": None,
            "tags": [],
            "pinned": False,
            "stability": "volatile",
            "importance": None,
            "project_ids": None,
            "device_scope": "all",
            "valid_from": None,
            "valid_to": None,
            "links": [],
        }
        result["items"] = [
            fields(raw, value, item_defaults)
            for raw, value in zip(request["items"], validated["items"], strict=True)
        ]
        for raw, value in zip(request["items"], result["items"], strict=True):
            value["links"] = [
                fields(link, validated_link, {"rel": None, "target": None, "target_version_id": None})
                for link, validated_link in zip(raw.get("links", []), value["links"], strict=True)
            ]
        return result
    result = fields(
        request,
        validated,
        dict(
            top_defaults,
            session_id=None,
            notes=None,
            decisions=[],
            lessons=[],
            card_update=None,
            expected_versions=[],
        ),
    )
    result["lessons"] = [
        fields(raw, value, {"title": None, "body": None, "tags": [], "device_scope": "all"})
        for raw, value in zip(request.get("lessons", []), result["lessons"], strict=True)
    ]
    if "card_update" in result:
        result["card_update"] = fields(
            request["card_update"], result["card_update"], {"body": None, "expected_version_id": None}
        )
    result["expected_versions"] = [
        {key: value[key] for key in ("logical_id", "version_id")} for value in result["expected_versions"]
    ]
    return result


def _event_hash_version(prior: q.EventRef) -> int | None:
    """Explicit versions are authoritative; never try another algorithm after a mismatch.

    Round 8 introduced resolved.write for writes alongside verbatim hashing. Historical close
    events already had resolved.write, so their exact old normalized request shape is the only
    available discriminator. An unversioned round-8 close with every default explicitly supplied
    is indistinguishable from a legacy close; retain legacy retry behavior in that ambiguous case.
    New events always carry a version, eliminating that ambiguity without rewriting old events.
    """
    resolved = prior.payload.get("resolved", {})
    if "hash_version" in resolved:
        version = resolved["hash_version"]
        return version if type(version) is int else None
    if prior.kind == "write":
        return HASH_VERSION_VERBATIM if "write" in resolved else HASH_VERSION_NORMALIZED
    request = prior.payload["request"]
    if canonical(request) == canonical(_legacy_request_v1(request, prior.kind)):
        return HASH_VERSION_NORMALIZED
    return HASH_VERSION_VERBATIM


def verbatim_args(req: Any, raw: dict[str, Any] | None) -> dict[str, Any]:
    """The tool arguments as received, for the idempotency hash (codex C5). ``raw`` wins; a dict
    ``req`` *is* the verbatim payload; a model instance falls back to ``exclude_unset`` (the
    closest reconstruction — callers holding the original dict should pass it)."""
    if raw is not None:
        return raw
    if isinstance(req, dict):
        return req
    return req.model_dump(mode="json", exclude_unset=True)


# --------------------------------------------------------------------------- per-item plan
@dataclass(slots=True)
class _Plan:
    index: int
    item: Item
    project_ids: list[int]
    logical_id: int | None = None  # None until allocated (new item)
    is_revision: bool = False
    is_card: bool = False
    old_rows: list[q.VersionRow] = field(default_factory=list)
    head: int | None = None
    interval: Interval | None = None
    superseded: list[q.VersionRow] = field(default_factory=list)
    survivors: list[tuple[q.VersionRow, Interval]] = field(default_factory=list)
    version_id: int | None = None
    token_count: int = 0
    chunks: list[Any] = field(default_factory=list)  # core.chunker.Chunk
    chunk_norms: list[str] = field(default_factory=list)  # normalize(chunk.text), computed with the chunks
    links: list[dict[str, Any]] = field(default_factory=list)  # resolved link specs (pre-id)


@dataclass(slots=True)
class _Batch:
    kind: str
    project: q.ProjectRef
    request_id: str
    client: str
    session_id: str | None
    request_payload: dict[str, Any]  # verbatim arguments; the hash and provenance use this copy
    items: list[Item]
    budget: int
    occurred_at_raw: str | None
    expected_versions: list[tuple[int, int]]
    resolved_extra: dict[str, Any]


# --------------------------------------------------------------------------- public API
async def write(
    conn: AsyncConnection,
    ctx: AuthContext,
    req: WriteRequest | dict[str, Any],
    *,
    deps: WriteDeps | None = None,
    raw: dict[str, Any] | None = None,
) -> WriteResult:
    request_payload = verbatim_args(req, raw)
    request = parse_request(WriteRequest, req)
    deps = deps or default_deps()
    try:
        budget = validate_budget(request.token_budget, default=DEFAULT_WRITE_BUDGET)
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc
    async with conn.transaction():
        result = await _execute(
            conn,
            ctx,
            deps,
            _Batch(
                kind="write",
                project=await _home_project(conn, ctx, request.project),
                request_id=request.request_id,
                client=request.client,
                session_id=None,
                request_payload=request_payload,
                items=list(request.items),
                budget=budget,
                occurred_at_raw=request.occurred_at,
                expected_versions=[],
                resolved_extra={
                    "write": {
                        "items": [it.model_dump(mode="json", exclude_none=True) for it in request.items]
                    }
                },
            ),
        )
    return WriteResult.model_validate(result)


async def call_the_day(
    conn: AsyncConnection,
    ctx: AuthContext,
    req: CloseRequest | dict[str, Any],
    *,
    deps: WriteDeps | None = None,
    raw: dict[str, Any] | None = None,
) -> CloseResult:
    request_payload = verbatim_args(req, raw)
    request = parse_request(CloseRequest, req)
    deps = deps or default_deps()
    try:
        budget = validate_budget(request.token_budget, default=DEFAULT_WRITE_BUDGET)
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc
    items = _close_items(request)
    async with conn.transaction():
        result = await _execute(
            conn,
            ctx,
            deps,
            _Batch(
                kind="call_the_day",
                project=await _home_project(conn, ctx, request.project),
                request_id=request.request_id,
                client=request.client,
                session_id=request.session_id,
                request_payload=request_payload,
                items=items,
                budget=budget,
                occurred_at_raw=request.occurred_at,
                expected_versions=[(ev.logical_id, ev.version_id) for ev in request.expected_versions],
                resolved_extra={
                    "write": {"items": [it.model_dump(mode="json", exclude_none=True) for it in items]}
                },
            ),
        )
    return CloseResult.model_validate(result)


def _close_items(request: CloseRequest) -> list[Item]:
    """§3: one write batch = session_note + lessons + optional card (derived_from note + pinned versions)."""
    body = request.notes
    if request.decisions:
        body += "\n\n## Decisions\n" + "\n".join(f"- {d}" for d in request.decisions)
    items = [Item(kind="session_note", title=f"Session {request.session_id}", body=body, tags=["session"])]
    for lesson in request.lessons:
        items.append(
            Item(
                kind="lesson",
                title=lesson.title,
                body=lesson.body,
                tags=list(lesson.tags),
                device_scope=lesson.device_scope,
            )
        )
    if request.card_update is not None:
        links = [LinkSpec(rel="derived_from", target="$0")]
        links += [
            LinkSpec(rel="derived_from", target=ev.logical_id, target_version_id=ev.version_id)
            for ev in request.expected_versions
        ]
        items.append(
            Item(
                kind="project_card",
                title="Project card",
                body=request.card_update.body,
                expected_version_id=request.card_update.expected_version_id,
                stability="stable",
                pinned=True,
                links=links,
            )
        )
    return items


# --------------------------------------------------------------------------- authorization
def _forbidden(slug: str) -> ToolError:
    return ToolError("E_FORBIDDEN_PROJECT", f"no write grant on project {slug!r}", project=slug)


async def _home_project(conn: AsyncConnection, ctx: AuthContext, slug: str) -> q.ProjectRef:
    """§3 (1): the home project resolves and the device holds ``write`` (unknown slug is the same error)."""
    found = await q.resolve_projects(conn, [slug])
    ref = found.get(slug)
    if ref is None or ref.archived or not ctx.has(ref.project_id, Role.WRITE):
        raise _forbidden(slug)
    return ref


async def _resolve_item_projects(conn: AsyncConnection, ctx: AuthContext, batch: _Batch) -> list[list[int]]:
    """§1 integrity (null/duplicate/missing home → E_INVALID_ARG) then §3 (2): every slug resolves
    with a write grant (else E_FORBIDDEN_PROJECT). Returns home-first project id lists."""
    home = batch.project
    per_item: list[list[str]] = []
    for i, item in enumerate(batch.items):
        slugs = list(item.project_ids) if item.project_ids is not None else [home.slug]
        if len(set(slugs)) != len(slugs):
            raise invalid_arg(
                f"items[{i}].project_ids contains duplicates", index=i, reason="duplicate_project"
            )
        if home.slug not in slugs:
            raise invalid_arg(
                f"items[{i}].project_ids must contain the home project", index=i, reason="missing_home"
            )
        per_item.append(slugs)
    refs = await q.resolve_projects(conn, [s for slugs in per_item for s in slugs])
    out: list[list[int]] = []
    for slugs in per_item:
        ids: list[int] = []
        for s in slugs:
            ref = refs.get(s)
            if ref is None or ref.archived or not ctx.has(ref.project_id, Role.WRITE):
                raise _forbidden(s)
            ids.append(ref.project_id)
        # home first (§2 dual-axis scope)
        ids.remove(home.project_id)
        out.append([home.project_id, *ids])
    return out


def _check_shapes(batch: _Batch, plans: list[_Plan]) -> None:
    card_lid = batch.project.card_logical_id
    seen_logical: set[int] = set()
    n = len(plans)
    for p in plans:
        it, i = p.item, p.index
        if it.kind == "project_card":
            if it.device_scope != "all":
                raise invalid_arg(f"items[{i}]: project_card device_scope must be all", index=i)
            if it.logical_id is not None and it.logical_id != card_lid:
                raise invalid_arg(f"items[{i}]: project_card logical_id must be the project's card", index=i)
            p.is_card = True
            p.logical_id = card_lid
        else:
            if it.logical_id == card_lid:
                raise invalid_arg(f"items[{i}]: logical_id {card_lid} is the project card", index=i)
            if (it.logical_id is None) != (it.expected_version_id is None):
                raise invalid_arg(
                    f"items[{i}]: logical_id and expected_version_id must be given together", index=i
                )
            if it.logical_id is not None:
                p.is_revision = True
                p.logical_id = it.logical_id
        if p.logical_id is not None:
            if p.logical_id in seen_logical:
                raise invalid_arg(
                    f"items[{i}]: logical_id {p.logical_id} appears twice in the batch", index=i
                )
            seen_logical.add(p.logical_id)
        seen_links: set[tuple[str, str]] = set()
        for ln in it.links:
            if isinstance(ln.target, str):
                idx = int(ln.target[1:])
                if idx >= n:
                    raise invalid_arg(f"items[{i}].links: target {ln.target!r} is out of range", index=i)
                if idx == i:
                    raise invalid_arg(f"items[{i}].links: an item cannot link to itself", index=i)
                if ln.target_version_id is not None:
                    raise invalid_arg(
                        f"items[{i}].links: target_version_id cannot be combined with {ln.target!r}", index=i
                    )
            key = (ln.rel, str(ln.target))
            if key in seen_links:
                raise invalid_arg(f"items[{i}].links: duplicate {ln.rel} → {ln.target}", index=i)
            seen_links.add(key)


async def _authorize_revisions(
    conn: AsyncConnection, ctx: AuthContext, batch: _Batch, plans: list[_Plan]
) -> dict[int, list[q.VersionRow]]:
    """§3 (3): every current segment must pass §4.4 (a), including cards and pins.

    The home is immutable and must equal ``project``. Hidden and missing targets have the same
    error, before old-project grants, replay or head checks can disclose anything about them.
    Once every target is visible, require write on home ∪ old ∪ new.
    """
    home_id = batch.project.project_id
    cache: dict[int, list[q.VersionRow]] = {}
    write_grants: list[tuple[set[int], str]] = []

    async def load(lid: int) -> list[q.VersionRow]:
        if lid not in cache:
            cache[lid] = await q.current_versions(conn, lid)
        return cache[lid]

    for p in plans:
        if p.logical_id is None:
            continue
        rows = await load(p.logical_id)
        p.old_rows = rows
        if p.is_card and not rows:
            continue  # first card version: create
        if not rows or any(r.project_id != home_id or not _endpoint_visible(ctx, home_id, r) for r in rows):
            raise ToolError("E_NOT_FOUND", f"items[{p.index}]: unknown logical_id", index=p.index)
        needed = {home_id, *p.project_ids}
        for r in rows:
            needed.update(r.project_ids)
        write_grants.append((needed, "missing write grant on a project of the revised item"))
        p.head = max(r.version_id for r in rows) if rows else None
    for lid, _vid in batch.expected_versions:
        rows = await load(lid)
        if not rows or any(r.project_id != home_id or not _endpoint_visible(ctx, home_id, r) for r in rows):
            raise ToolError("E_NOT_FOUND", "expected_versions: unknown logical_id", logical_id=lid)
        write_grants.append(
            (
                {home_id, *(pid for r in rows for pid in r.project_ids)},
                "missing write grant on a project of a pinned item",
            )
        )
    for needed, message in write_grants:
        for pid in sorted(needed):
            if not ctx.has(pid, Role.WRITE):
                raise ToolError("E_FORBIDDEN_PROJECT", message)
    return cache


# --------------------------------------------------------------------------- content rules
def _endpoint_visible(ctx: AuthContext, home_id: int, row: q.VersionRow) -> bool:
    """§4.4 (a) on a link endpoint, evaluated for the batch's home project: membership in
    ``project_ids``, a read grant on it, and a ``device_scope`` the calling device matches."""
    return (
        home_id in row.project_ids and ctx.has(home_id, Role.READ) and row.device_scope in ctx.scope_values()
    )


async def _resolve_link_targets(
    conn: AsyncConnection,
    ctx: AuthContext,
    home: q.ProjectRef,
    plans: list[_Plan],
    cache: dict[int, list[q.VersionRow]],
) -> None:
    """Link targets: ``$<index>`` → the new version of that item (resolved after id allocation);
    integer → an existing logical item. ``dst_version_id`` = ``target_version_id`` if given, else
    head for ``derived_from``, else NULL.

    Authorization (codex S2): the *selected* endpoint — the pinned version, the head for an
    unpinned ``derived_from``, any current segment otherwise — must pass §4.4 (a) for the home
    project *before* anything else is decided. A target that fails (a), does not exist, or whose
    pinned version is not visible is one uniform ``E_NOT_FOUND``; only when both the logical
    target and the pinned version are visible may "not a version of the target" be reported
    (``E_INVALID_ARG``), so success/validation errors never reveal hidden relationships."""
    home_id = home.project_id
    for p in plans:
        for ln in p.item.links:
            spec: dict[str, Any] = {"rel": ln.rel}
            if isinstance(ln.target, str):
                spec["target_index"] = int(ln.target[1:])
                p.links.append(spec)
                continue
            lid = ln.target
            if lid not in cache:
                cache[lid] = await q.current_versions(conn, lid)
            rows = cache[lid]
            hidden = ToolError("E_NOT_FOUND", f"items[{p.index}].links: unknown target", index=p.index)
            if ln.target_version_id is not None:
                v = await q.get_version(conn, ln.target_version_id)
                if v is None or not _endpoint_visible(ctx, home_id, v):
                    raise hidden
                if v.logical_id != lid:
                    if not any(_endpoint_visible(ctx, home_id, r) for r in rows):
                        raise hidden
                    raise invalid_arg(
                        f"items[{p.index}].links: target_version_id is not a version of the target",
                        index=p.index,
                    )
                dst_version: int | None = ln.target_version_id
            else:
                if not any(_endpoint_visible(ctx, home_id, r) for r in rows):
                    raise hidden
                if ln.rel == "derived_from":
                    head = max(rows, key=lambda r: r.version_id)
                    if not _endpoint_visible(ctx, home_id, head):
                        raise hidden
                    dst_version = head.version_id
                else:
                    dst_version = None
            spec["dst_logical_id"] = lid
            spec["dst_version_id"] = dst_version
            p.links.append(spec)


async def _check_content(conn: AsyncConnection, deps: WriteDeps, plans: list[_Plan]) -> None:
    for p in plans:
        it = p.item
        p.token_count = await _cpu(deps.meter.count_text, it.body)
        if p.is_card and p.token_count > CARD_MAX_TOKENS:
            raise ToolError(
                "E_CARD_TOO_LARGE",
                f"items[{p.index}]: project card body is {p.token_count} tokens (max {CARD_MAX_TOKENS})",
                index=p.index,
                tokens=p.token_count,
                max=CARD_MAX_TOKENS,
            )
        if it.device_scope.startswith("device:"):
            if not await q.device_scope_target_ok(conn, int(it.device_scope.split(":", 1)[1])):
                raise invalid_arg(
                    f"items[{p.index}]: device_scope names an unknown or revoked device", index=p.index
                )


def _pessimistic_ack(batch: _Batch, deps: WriteDeps) -> None:
    """§3 budget rule: the ack size is computed from the item count before any mutation."""
    ack: dict[str, Any] = {
        "request_id": batch.request_id,
        "replayed": False,
        "versions": [
            {
                "index": i,
                "logical_id": _PESSIMISTIC_ID,
                "version_id": _PESSIMISTIC_ID,
                "chunk_count": 10_000,
                "embedding_status": "queued",
            }
            for i in range(len(batch.items))
        ],
    }
    if batch.kind == "call_the_day":
        ack["session_note_clue"] = f"v{_PESSIMISTIC_ID}"
    used = deps.meter.settle(ack, batch.budget)
    if used > batch.budget:
        raise ToolError("E_BUDGET_TOO_SMALL", f"token_budget {batch.budget} cannot hold the ack", min=used)


# --------------------------------------------------------------------------- main path
async def _execute(conn: AsyncConnection, ctx: AuthContext, deps: WriteDeps, batch: _Batch) -> dict[str, Any]:
    home = batch.project
    project_ids = await _resolve_item_projects(conn, ctx, batch)  # §1 integrity + §3 (2)
    plans = [
        _Plan(index=i, item=it, project_ids=pids)
        for i, (it, pids) in enumerate(zip(batch.items, project_ids, strict=True))
    ]
    _check_shapes(batch, plans)

    # Hidden targets must fail before *any* contended key can disclose their existence.
    await _authorize_revisions(conn, ctx, batch, plans)
    # Serialise same-key requests and revisions of the same logical items (§1.1 head check).
    await q.lock_request_key(conn, home.project_id, ctx.device_id, batch.request_id)
    if batch.session_id is not None:
        await q.lock_session_key(conn, home.project_id, batch.session_id)
    await q.lock_logical_ids(
        conn,
        [p.logical_id for p in plans if p.logical_id is not None]
        + [lid for lid, _ in batch.expected_versions],
    )

    # Reload after waiting: scope/head may have changed while acquiring the locks.
    cache = await _authorize_revisions(conn, ctx, batch, plans)  # §3 (3)

    # New events hash verbatim arguments; retries must use the stored event's algorithm.
    sha = payload_sha256(batch.request_payload)
    prior = await q.find_event(conn, home.project_id, ctx.device_id, batch.request_id)
    if prior is not None:
        version = _event_hash_version(prior) if prior.kind == batch.kind else None
        retry_sha = (
            payload_sha256(_legacy_request_v1(batch.request_payload, batch.kind))
            if version == HASH_VERSION_NORMALIZED
            else sha
        )
        if (
            version not in (HASH_VERSION_NORMALIZED, HASH_VERSION_VERBATIM)
            or prior.kind != batch.kind
            or prior.payload_sha256 != retry_sha
            or prior.result is None
        ):
            raise ToolError("E_REQUEST_ID_CONFLICT", "request_id was already used with a different payload")
        replay = dict(prior.result)
        replay["replayed"] = True
        deps.meter.settle(replay, batch.budget)
        return replay
    if batch.session_id is not None and await q.session_closed(conn, home.project_id, batch.session_id):
        raise ToolError(
            "E_SESSION_CLOSED", "this session has already been closed", session_id=batch.session_id
        )

    # Version comparison only after authorization (no disclosure to unauthorized callers).
    for p in plans:
        if p.is_card:
            if p.item.expected_version_id != p.head:
                raise ToolError(
                    "E_VERSION_CONFLICT",
                    f"items[{p.index}]: project card head changed",
                    current_version_id=p.head,
                )
        elif p.is_revision and p.item.expected_version_id != p.head:
            raise ToolError(
                "E_VERSION_CONFLICT",
                f"items[{p.index}]: expected_version_id is not the head",
                current_version_id=p.head,
            )
    for lid, vid in batch.expected_versions:
        head = max(r.version_id for r in cache[lid])
        if head != vid:
            raise ToolError(
                "E_VERSION_CONFLICT",
                "expected_versions: pinned version is not the head",
                logical_id=lid,
                current_version_id=head,
            )

    await _resolve_link_targets(conn, ctx, home, plans, cache)
    await _check_content(conn, deps, plans)
    _pessimistic_ack(batch, deps)

    # ---- temporal resolution -------------------------------------------------------------
    now = await q.clock_now(conn)
    occurred_at = parse_opt_ts(batch.occurred_at_raw, field="occurred_at") or now
    superseded_recorded: list[datetime] = []
    # (src, dst, rel) → every current link segment overlapping the new interval (codex C3: an
    # edge may be split into adjacent segments; a spanning correction supersedes all of them).
    link_supersedes: dict[tuple[int, int, str], list[q.LinkRow]] = {}
    link_survivors: list[tuple[q.LinkRow, Interval]] = []
    for p in plans:
        it = p.item
        vf = parse_opt_ts(it.valid_from, field=f"items[{p.index}].valid_from")
        vt = parse_opt_ts(it.valid_to, field=f"items[{p.index}].valid_to")
        p.interval = validate_interval(vf if vf is not None else occurred_at, vt, now=now, index=p.index)
        for r in p.old_rows:
            if overlaps(r.valid_from, r.valid_to, p.interval.start, p.interval.end):
                p.superseded.append(r)
                superseded_recorded.append(r.recorded_at)
                p.survivors.extend(
                    (r, seg)
                    for seg in surviving_segments(r.valid_from, r.valid_to, p.interval.start, p.interval.end)
                )
        if p.logical_id is not None and (p.links or p.is_card):
            existing = await q.current_links_from(
                conn, p.logical_id, valid_from=p.interval.start, valid_to=p.interval.end
            )
            matching = set()
            for spec in p.links:
                dst = spec.get("dst_logical_id")
                if "target_index" in spec:  # "$i" pointing at a revised item: its logical id is known
                    dst = plans[spec["target_index"]].logical_id
                if dst is None:
                    continue
                matching.add((dst, spec["rel"]))
            for old in existing:
                # A card revision replaces its entire source set, including dropped sources.
                if (
                    not (p.is_card and old.rel == "derived_from")
                    and (old.dst_logical_id, old.rel) not in matching
                ):
                    continue
                if overlaps(old.valid_from, old.valid_to, p.interval.start, p.interval.end):
                    key = (p.logical_id, old.dst_logical_id, old.rel)
                    link_supersedes.setdefault(key, []).append(old)
                    superseded_recorded.append(old.recorded_at)
                    link_survivors.extend(
                        (old, seg)
                        for seg in surviving_segments(
                            old.valid_from, old.valid_to, p.interval.start, p.interval.end
                        )
                    )
    T = select_T(now, *superseded_recorded)

    # ---- chunking + id allocation ---------------------------------------------------------
    old_chunks: dict[int, list[q.ChunkRow]] = {}
    for p in plans:
        p.chunks, p.chunk_norms = await _cpu(_chunk_and_normalize, p.item.body, deps.chunker)
        for r, _seg in p.survivors:
            if r.version_id not in old_chunks:
                old_chunks[r.version_id] = await q.chunks_of_version(conn, r.version_id)

    (event_id,) = await q.allocate_ids(conn, "events", 1)
    new_logical = await q.allocate_logical_ids(conn, sum(1 for p in plans if p.logical_id is None))
    for p in plans:
        if p.logical_id is None:
            p.logical_id = new_logical.pop(0)
    # survivors before the replacement so the replacement is the greatest (= head, §1.1)
    n_versions = sum(len(p.survivors) + 1 for p in plans)
    version_ids = await q.allocate_ids(conn, "memory_versions", n_versions)
    n_chunks = sum(len(p.chunks) + sum(len(old_chunks[r.version_id]) for r, _ in p.survivors) for p in plans)
    chunk_ids = await q.allocate_ids(conn, "chunks", n_chunks)
    link_ids = await q.allocate_ids(conn, "links", len(link_survivors) + sum(len(p.links) for p in plans))

    survivor_vids: dict[int, list[int]] = {}
    for p in plans:
        survivor_vids[p.index] = [version_ids.pop(0) for _ in p.survivors]
        p.version_id = version_ids.pop(0)
    by_index = {p.index: p for p in plans}

    # ---- build rows + resolved payload ---------------------------------------------------
    T_json = fmt_ts(T)
    versions: list[q.VersionRow] = []
    chunks: list[q.ChunkRow] = []
    links: list[q.LinkRow] = []
    link_survivors_json: list[dict[str, Any]] = []
    for old, seg in link_survivors:
        lid = link_ids.pop(0)
        links.append(
            replace(
                old,
                link_id=lid,
                valid_from=seg.start,
                valid_to=seg.end,
                recorded_at=T,
                source_event_id=event_id,
                supersedes_link_id=old.link_id,
            )
        )
        link_survivors_json.append({"link_id": lid, "from_link_id": old.link_id, **seg.as_json()})
    jobs: list[dict[str, Any]] = []
    resolved_items: list[dict[str, Any]] = []
    superseded_link_ids = sorted({old.link_id for olds in link_supersedes.values() for old in olds})
    embedder = embedder_descriptor()

    for p in plans:
        it = p.item
        assert p.logical_id is not None and p.version_id is not None and p.interval is not None
        survivors_json: list[dict[str, Any]] = []
        for (r, seg), svid in zip(p.survivors, survivor_vids[p.index], strict=True):
            versions.append(
                q.VersionRow(
                    version_id=svid,
                    logical_id=r.logical_id,
                    project_id=r.project_id,
                    project_ids=list(r.project_ids),
                    device_scope=r.device_scope,
                    kind=r.kind,
                    status=r.status,
                    title=r.title,
                    body=r.body,
                    tags=list(r.tags),
                    pinned=r.pinned,
                    stability=r.stability,
                    importance=r.importance,
                    token_count=r.token_count,
                    valid_from=seg.start,
                    valid_to=seg.end,
                    recorded_at=T,
                    source_event_id=event_id,
                    supersedes_version_id=r.version_id,
                    last_access_at=r.last_access_at,
                )
            )
            sv_chunks: list[dict[str, Any]] = []
            for oc in old_chunks[r.version_id]:
                cid = chunk_ids.pop(0)
                chunks.append(
                    q.ChunkRow(
                        chunk_id=cid,
                        version_id=svid,
                        project_ids=list(r.project_ids),
                        device_scope=r.device_scope,
                        ordinal=oc.ordinal,
                        char_start=oc.char_start,
                        char_end=oc.char_end,
                        text=oc.text,
                        text_norm=oc.text_norm,
                        e5_tokens=oc.e5_tokens,
                    )
                )
                sv_chunks.append(
                    {
                        "chunk_id": cid,
                        "ordinal": oc.ordinal,
                        "char_start": oc.char_start,
                        "char_end": oc.char_end,
                        "e5_tokens": oc.e5_tokens,
                    }
                )
            if sv_chunks:
                jobs.append({"dedupe_key": embed_dedupe_key(svid), "version_id": svid})
            survivors_json.append(
                {
                    "version_id": svid,
                    "from_version_id": r.version_id,
                    **seg.as_json(),
                    "chunks": sv_chunks,
                    "last_access_at": fmt_ts(r.last_access_at) if r.last_access_at is not None else None,
                }
            )

        versions.append(
            q.VersionRow(
                version_id=p.version_id,
                logical_id=p.logical_id,
                project_id=home.project_id,
                project_ids=list(p.project_ids),
                device_scope=it.device_scope,
                kind=it.kind,
                status="active",
                title=it.title,
                body=it.body,
                tags=list(it.tags),
                pinned=it.pinned,
                stability=it.stability,
                importance=it.importance,
                token_count=p.token_count,
                valid_from=p.interval.start,
                valid_to=p.interval.end,
                recorded_at=T,
                source_event_id=event_id,
                supersedes_version_id=p.head,
            )
        )
        chunks_json: list[dict[str, Any]] = []
        for c, c_norm in zip(p.chunks, p.chunk_norms, strict=True):
            cid = chunk_ids.pop(0)
            chunks.append(
                q.ChunkRow(
                    chunk_id=cid,
                    version_id=p.version_id,
                    project_ids=list(p.project_ids),
                    device_scope=it.device_scope,
                    ordinal=c.ordinal,
                    char_start=c.char_start,
                    char_end=c.char_end,
                    text=c.text,
                    text_norm=c_norm,
                    e5_tokens=c.e5_tokens,
                )
            )
            chunks_json.append(
                {
                    "chunk_id": cid,
                    "ordinal": c.ordinal,
                    "char_start": c.char_start,
                    "char_end": c.char_end,
                    "e5_tokens": c.e5_tokens,
                }
            )
        if chunks_json:
            jobs.append({"dedupe_key": embed_dedupe_key(p.version_id), "version_id": p.version_id})

        links_json: list[dict[str, Any]] = []
        for spec in p.links:
            lid = link_ids.pop(0)
            if "target_index" in spec:
                tgt = by_index[spec["target_index"]]
                dst_logical, dst_version = tgt.logical_id, tgt.version_id
                if spec["rel"] != "derived_from":
                    dst_version = None
            else:
                dst_logical, dst_version = spec["dst_logical_id"], spec["dst_version_id"]
            assert dst_logical is not None
            # supersedes_link_id names the latest superseded segment; all of them are closed at T
            olds = link_supersedes.get((p.logical_id, dst_logical, spec["rel"]), [])
            old = max(olds, key=lambda o: o.link_id) if olds else None
            links.append(
                q.LinkRow(
                    link_id=lid,
                    project_id=home.project_id,
                    project_ids=list(p.project_ids),
                    device_scope=it.device_scope,
                    src_logical_id=p.logical_id,
                    dst_logical_id=dst_logical,
                    dst_version_id=dst_version,
                    rel=spec["rel"],
                    props={},
                    valid_from=p.interval.start,
                    valid_to=p.interval.end,
                    recorded_at=T,
                    source_event_id=event_id,
                    supersedes_link_id=old.link_id if old else None,
                )
            )
            links_json.append(
                {
                    "link_id": lid,
                    "rel": spec["rel"],
                    "dst_logical_id": dst_logical,
                    "dst_version_id": dst_version,
                    "supersedes_link_id": old.link_id if old else None,
                    **p.interval.as_json(),
                }
            )

        resolved_items.append(
            {
                "index": p.index,
                "logical_id": p.logical_id,
                "version_id": p.version_id,
                "project_ids": list(p.project_ids),
                "device_scope": it.device_scope,
                **p.interval.as_json(),
                "token_count": p.token_count,
                "supersedes_version_id": p.head,
                "supersedes": [r.version_id for r in p.superseded],
                "survivors": survivors_json,
                "chunks": chunks_json,
                "links": links_json,
            }
        )

    resolved: dict[str, Any] = {
        "hash_version": HASH_VERSION_VERBATIM,
        "recorded_at": T_json,
        "occurred_at": fmt_ts(occurred_at),
        "projection_version": PROJECTION_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "chunker": deps.chunker_descriptor(),
        "meter": {"tokenizer": deps.meter.tokenizer, "tiktoken": _tiktoken_version()},
        "embedder": embedder,
        "items": resolved_items,
        "superseded_links": superseded_link_ids,
        "link_survivors": link_survivors_json,
        "jobs": jobs,
        **batch.resolved_extra,
    }

    librarian_jobs = await _librarian_jobs(conn, ctx, deps, batch, plans, event_id)
    if librarian_jobs:  # W2b: recorded with the event; replay re-creates the rows
        resolved["librarian_jobs"] = librarian_jobs

    ack: dict[str, Any] = {
        "request_id": batch.request_id,
        "replayed": False,
        "versions": [
            VersionAck(
                index=p.index,
                logical_id=p.logical_id,  # type: ignore[arg-type]
                version_id=p.version_id,  # type: ignore[arg-type]
                chunk_count=len(p.chunks),
                embedding_status="queued" if p.chunks else "done",
            ).model_dump()
            for p in plans
        ],
    }
    if batch.kind == "call_the_day":
        ack["session_note_clue"] = f"v{plans[0].version_id}"
    used = deps.meter.settle(ack, batch.budget)
    if used > batch.budget:  # cannot happen after _pessimistic_ack; belt and braces
        raise ToolError("E_BUDGET_TOO_SMALL", f"token_budget {batch.budget} cannot hold the ack", min=used)

    # ---- persist (one transaction) ------------------------------------------------------
    await q.insert_event(
        conn,
        event_id=event_id,
        project_id=home.project_id,
        device_id=ctx.device_id,
        client=batch.client,
        request_id=batch.request_id,
        session_id=batch.session_id,
        kind=batch.kind,
        projection_version=PROJECTION_VERSION,
        payload={
            "request": batch.request_payload,
            "resolved": resolved,
        },
        payload_sha256=sha,
        occurred_at=occurred_at,
        result=ack,
    )
    sup_ids = [r.version_id for p in plans for r in p.superseded]
    if await q.supersede_versions(conn, sup_ids, T) != len(sup_ids):
        raise ToolError("E_VERSION_CONFLICT", "a superseded version changed concurrently")
    if await q.supersede_links(conn, superseded_link_ids, T) != len(superseded_link_ids):
        raise ToolError("E_VERSION_CONFLICT", "a superseded link changed concurrently")
    for v in versions:
        await q.insert_version(conn, v)
    await q.insert_chunks(conn, chunks)
    for ln in links:
        await q.insert_link(conn, ln)
    for job in jobs:
        await q.insert_job(
            conn,
            kind=JOB_KIND_EMBED,
            dedupe_key=job["dedupe_key"],
            source_event_id=event_id,
            payload=embed_job_payload(job["version_id"], embedder),
            run_after=T,
        )
    if librarian_jobs:
        from hlmemo.librarian.jobs import insert_recorded_jobs

        await insert_recorded_jobs(conn, librarian_jobs, event_id, T)
    return ack


async def _librarian_jobs(
    conn: AsyncConnection, ctx: AuthContext, deps: WriteDeps, batch: _Batch, plans: list[_Plan], event_id: int
) -> list[dict[str, Any]]:
    """W2b: the ``librarian_write:<event_id>`` job descriptors for this batch (``[]`` when the
    librarian is disabled or the project's policy is off). ``client_*`` say which placement fields
    the client set itself (the librarian never overwrites them)."""
    if not deps.librarian_enqueue:
        return []
    from hlmemo.librarian.trigger import plan_jobs

    raw_items = batch.request_payload.get("items") if batch.kind == "write" else None
    versions = []
    for p in plans:
        raw = raw_items[p.index] if isinstance(raw_items, list) and p.index < len(raw_items) else {}
        raw = raw if isinstance(raw, dict) else {}
        versions.append(
            {
                "version_id": p.version_id,
                "kind": p.item.kind,
                "client_importance": p.item.importance if "importance" in raw else None,
                "client_stability": "stability" in raw or p.is_card,
                "project_ids": list(p.project_ids),
            }
        )
    return await plan_jobs(
        conn,
        ctx,
        enabled=True,
        kind=batch.kind,
        event_id=event_id,
        project_id=batch.project.project_id,
        versions=versions,
    )


@lru_cache(maxsize=1)
def _tiktoken_version() -> str:
    from importlib.metadata import version

    return version("tiktoken")


__all__ = [
    "CARD_MAX_TOKENS",
    "PROJECTION_VERSION",
    "WriteDeps",
    "call_the_day",
    "default_deps",
    "embed_dedupe_key",
    "embed_job_payload",
    "embedder_descriptor",
    "payload_sha256",
    "verbatim_args",
    "write",
]
