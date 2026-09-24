"""``memory.risk_check`` (PHASE2-4-ROADMAP W2d; report D.3 #7, D.4(b); D-014, D-062, D-067).

``risk_check(conn, ctx, args, deps=, judge=, detach=, reconnect=)`` → ``{project, verdict, judged,
judge, reason?, warnings, omitted, candidates_considered, guard_dropped?, budget}``;
``verdict ∈ {"warn", "no_matching_evidence"}``. Per D-014 it never says "no risk": the absence of
a warning only means no stored lesson matched.

1. **Deterministic stage** (one read transaction, no LLM, ≤ 300 ms): the caller's CURRENT grants
   and device scope (``AuthContext``, resolved under FOR SHARE by the middleware) select every
   current lesson/experience it may read, in the home project, in other granted projects and in
   ``hlm-global`` if granted. The query path's four RRF lists (lexical over DF-filtered terms,
   titles, trigram for identifiers, exact vector) are fused over that universe; the top
   ``TOP_K`` are the candidates.
2. **Deterministic verdict**: each candidate gets ``det_score`` = its RRF over the lists in which it
   qualifies (the vector leg only counts below ``VEC_MAX_DIST``); warn iff ``det_score ≥ TAU``.
   The constants are calibrated on the cal split of ``tests/fixtures/risk`` (procedure and values
   in its README; ``tests/integration/test_w2d_risk_calibration.py``).
3. **LLM judge** (``mode=auto``, librarian enabled): ``librarian.risk_judge`` gets the
   candidates under the privacy gate, a 4 s cap and the spend guard. Judged warnings are the
   judge's matches, each citing a candidate clue (D-067 guard). Candidates the privacy gate
   withheld (``device:*``, ``policy.librarian=off``, co-owned by an ungranted project) are never
   sent; they warn only deterministically, at the stricter ``TAU_STRICT``. On any judge failure
   (timeout, outage, disabled or unqualified fallback, budget, schema, an all-uncited ``warn``)
   the result is the deterministic verdict: ``judged:false``, ``judge:"retrieval_only"``, and
   ``reason`` names the cause. Judged results carry ``judge:"ok"`` (``"ok_fallback"``: the
   fallback tier judged, D-066).
4. **D-062**: the judge never runs inside a transaction. Over the API the request transaction
   (device FOR SHARE) is committed and its connection returned (``detach``) before the judge;
   afterwards ONE short transaction on a fresh connection re-checks the device (revoked / expired /
   rebound → ``E_AUTH``, the result is discarded), the home-project read grant and the visibility
   of every warned item (invisible → dropped).

The output is packed like every read result: ``budget.used`` is the exact o200k count of the
canonical JSON; warnings are added in order until the next one would not fit (``omitted`` counts
the rest); ``E_BUDGET_TOO_SMALL`` only if the envelope without warnings does not fit.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus
from pydantic import Field

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.errors import HlmError
from hlmemo.core import MODEL_ID, MODEL_REVISION
from hlmemo.core.budget import BudgetError, validate_budget
from hlmemo.core.clues import encode_clue
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import PREPROC_VERSION, ReadDeps, _read_project
from hlmemo.core.retrieval import (
    K_RRF,
    TRGM_WORD_SIMILARITY_THRESHOLD,
    W_L,
    W_T,
    W_TI,
    W_V,
    Fused,
    dedupe_and_order,
    pack_prefix,
    rrf_fuse,
    split_terms,
)
from hlmemo.core.write_models import SLUG_RE, _Strict, parse_request
from hlmemo.db import auth_queries
from hlmemo.db import read_queries as rq
from hlmemo.db import risk_queries as q
from hlmemo.librarian import risk_judge as rj

TOOL = "memory.risk_check"
TOP_K = 10
LIST_LIMIT = 50  # per RRF list over the lesson universe
MAX_WARNINGS = 3
WHY_MAX = rj.WHY_MAX

# ---- calibrated constants (cal split of tests/fixtures/risk; see its README) --------------------
#: the vector leg counts toward ``det_score`` only for cosine distance (1 - cos, e5-small) at most:
VEC_MAX_DIST = 0.16
#: deterministic warn threshold on ``det_score`` (RRF, K_RRF=60: one rank-1 list = 0.0164):
TAU = 0.045
#: stricter threshold for candidates the privacy gate never lets reach the judge:
TAU_STRICT = 0.045

VERDICT_WARN = "warn"
VERDICT_NONE = "no_matching_evidence"
NO_DETACH = "no_detach"  # reason: an API request transaction could not be released (D-062)


Ranks = tuple[int | None, int | None, int | None, int | None]  # lexical, trigram, title, vector


class RiskRequest(_Strict):
    project: str = Field(pattern=SLUG_RE)
    task: str = Field(min_length=1, max_length=4000)
    token_budget: Any
    mode: Literal["auto", "deterministic"] = "auto"


@dataclass(slots=True)
class RiskCandidate:
    version_id: int
    logical_id: int
    project_id: int
    project_ids: list[int]
    project: str
    device_scope: str
    kind: str
    title: str
    rrf: float
    det_score: float
    vector_dist: float | None
    ranks: Ranks  # (lexical, trigram, title, vector) ranks, None = not in that list

    @property
    def lists(self) -> str:  # e.g. "LTV" = lexical + title + vector (diagnostics)
        return _lists(self.ranks)

    @property
    def clue(self) -> str:
        return encode_clue(self.version_id)


def ranks_of(f: Fused) -> Ranks:
    return (f.lexical_rank, f.trigram_rank, f.title_rank, f.vector_rank)


def det_score(ranks: Ranks, dist: float | None, vec_max: float = VEC_MAX_DIST) -> float:
    """RRF over the lists in which the candidate qualifies (the vector leg only when close)."""
    lexical, trigram, title, vector = ranks
    s = 0.0
    for weight, rank in ((W_L, lexical), (W_T, trigram), (W_TI, title)):
        if rank is not None:
            s += weight / (K_RRF + rank)
    if vector is not None and dist is not None and dist <= vec_max:
        s += W_V / (K_RRF + vector)
    return s


def _lists(ranks: Ranks) -> str:
    return "".join(tag for tag, rank in zip("LGTV", ranks, strict=True) if rank is not None)


async def candidates(
    conn: AsyncConnection, ctx: AuthContext, home_pid: int, task: str, deps: ReadDeps, *, top_k: int = TOP_K
) -> tuple[list[RiskCandidate], int]:
    """Deterministic stage (inside the caller's read transaction). Returns (top-k, universe size)."""
    pids = (
        sorted(await q.all_projects(conn))
        if ctx.is_admin
        else sorted(pid for pid in ctx.grants if ctx.has(pid, Role.READ))
    )
    if home_pid not in pids:
        pids.append(home_pid)
    now = await rq.clock_now(conn)
    f = q.RiskFilter(pids=pids, scopes=list(ctx.scope_values()), at=now)
    stats = await deps.term_stats.get(conn, home_pid)
    terms = split_terms(task, stats)
    qvec = deps.embedder.embed_query(task)
    lexical = await q.lexical(conn, f, terms.lexical_text, LIST_LIMIT)
    title = await q.title(conn, f, terms.title, LIST_LIMIT)
    trigram: list[rq.Candidate] = []
    if terms.identifiers:
        await rq.set_trigram_threshold(conn, TRGM_WORD_SIMILARITY_THRESHOLD)
        trigram = await q.trigram(conn, f, terms.identifiers, LIST_LIMIT)
    vector = await q.vector(
        conn,
        f,
        qvec,
        model=MODEL_ID,
        revision=MODEL_REVISION,
        preproc_version=PREPROC_VERSION,
        limit=LIST_LIMIT,
    )
    dist = {c.chunk_id: float(c.score) for c in vector}
    ordered = dedupe_and_order(rrf_fuse(lexical, trigram, vector, title))[:top_k]
    if not ordered:
        return [], 0
    rows = await q.rows(conn, [x.version_id for x in ordered])
    slugs = await q.project_slugs(conn, sorted({rows[x.version_id].project_id for x in ordered}))
    out = []
    for x in ordered:
        r = rows[x.version_id]
        d = dist.get(x.chunk_id)
        out.append(
            RiskCandidate(
                version_id=r.version_id,
                logical_id=r.logical_id,
                project_id=r.project_id,
                project_ids=r.project_ids,
                project=slugs.get(r.project_id, str(r.project_id)),
                device_scope=r.device_scope,
                kind=r.kind,
                title=r.title,
                rrf=x.score,
                det_score=det_score(ranks_of(x), d),
                vector_dist=d,
                ranks=ranks_of(x),
            )
        )
    return out, len(ordered)


def _warning(c: RiskCandidate, why: str) -> dict[str, Any]:
    return {"clue": c.clue, "title": c.title, "why": why[:WHY_MAX], "source_project": c.project}


def _det_why(c: RiskCandidate, reason: str) -> str:
    return (
        f"Retrieval-only match on this {c.kind} ({c.lists} lists, score {c.det_score:.3f}); "
        f"not checked by the librarian LLM ({reason}). Drill the clue before relying on it."
    )


def deterministic_warnings(
    cands: list[RiskCandidate], reason: str, *, tau: float = TAU
) -> list[dict[str, Any]]:
    """Retrieval-only warnings: candidates with ``det_score ≥ tau``, best first, at most 3."""
    return [w for _, w in _det(cands, reason, tau)]


def _pack(
    deps: ReadDeps, envelope: dict[str, Any], budget: int, warnings: list[dict[str, Any]]
) -> dict[str, Any]:
    meter = deps.meter
    prefix = [0]
    base = meter.settle(envelope, budget)
    for w in warnings:
        prefix.append(prefix[-1] + meter.count(w) + 1)

    def apply(n: int) -> None:
        envelope["warnings"] = warnings[:n]
        envelope["omitted"] = len(warnings) - n

    try:
        pack_prefix(meter, envelope, budget, len(warnings), apply, lambda n: base + prefix[n])
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc
    return envelope


async def _recheck(conn: AsyncConnection, ctx: AuthContext, slug: str, version_ids: list[int]) -> set[int]:
    """D-062: after the judge, ONE short transaction: the device is still trusted, unexpired and on
    the same token generation (else ``E_AUTH``: the result is discarded), still reads the home
    project (else ``E_FORBIDDEN_PROJECT``); returns which warned items it can still see now."""
    async with conn.transaction():
        dev = await q.device_now(conn, ctx.device_id)
        if (
            dev is None
            or dev.status != "trusted"
            or dev.expired
            or dev.token_generation != ctx.token_generation
        ):
            raise HlmError("E_AUTH", "device revoked or rebound during the request")
        grants = await auth_queries.select_active_grants(conn, ctx.device_id)
        fresh = AuthContext(
            device_id=ctx.device_id,
            device_class=dev.device_class,
            is_admin=dev.is_admin,
            token_generation=dev.token_generation,
            grants={pid: Role(role) for pid, role in grants},
            client=ctx.client,
        )
        await _read_project(conn, fresh, slug)
        pids = await q.all_projects(conn) if fresh.is_admin else sorted(fresh.grants)
        pids = [p for p in pids if fresh.has(p, Role.READ)]
        f = q.RiskFilter(pids=pids, scopes=list(fresh.scope_values()), at=await rq.clock_now(conn))
        return await q.visible_versions(conn, f, version_ids)


async def risk_check(
    conn: AsyncConnection,
    ctx: AuthContext,
    req: RiskRequest | dict[str, Any],
    *,
    deps: ReadDeps,
    judge: rj.RiskJudge | None = None,
    detach: Callable[[], Awaitable[bool]] | None = None,
    reconnect: Callable[[], AbstractAsyncContextManager[AsyncConnection]] | None = None,
) -> dict[str, Any]:
    """``conn`` is the caller's connection. Over the API it is the request transaction:
    ``detach`` commits it and returns it to the pool before the judge, and ``reconnect`` yields a
    fresh pooled connection for the post-judge re-check. A direct caller passes an idle ``conn``
    (the deterministic stage then commits its own transaction and the re-check reuses ``conn``).
    The judge never runs while a transaction of ``conn`` is open (D-062)."""
    request = parse_request(RiskRequest, req)
    try:
        budget = validate_budget(request.token_budget)
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc
    async with conn.transaction():
        project = await _read_project(conn, ctx, request.project)
        cands, considered = await candidates(conn, ctx, project.project_id, request.task, deps)

    judged = False
    guard_dropped = 0
    all_withheld = False
    released = False
    called = False
    if request.mode == "deterministic":
        status = rj.NOT_REQUESTED
    elif judge is None:
        status = rj.DISABLED
    elif not cands:
        status = rj.NO_CANDIDATES
    elif (why_not := judge.unavailable()) is not None:
        status = why_not
    else:
        if detach is not None:
            released = await detach()
        if not released and (reconnect is not None or conn.info.transaction_status != TransactionStatus.IDLE):
            # an API request whose transaction could not be released: never judge inside it
            status = NO_DETACH
        else:
            called = True
            caps = {
                "trigger_device_id": ctx.device_id,
                "question": sorted({p for c in cands for p in c.project_ids if ctx.has(p, Role.READ)}),
            }
            items = [rj.JudgeItem(c.version_id, c.project) for c in cands]
            res = await judge.judge(request.task, items, caps)
            status = res.status
            guard_dropped = res.dropped
            all_withheld = res.status == rj.NO_CANDIDATES and len(res.denied) == len(cands)
    by_vid = {c.version_id: c for c in cands}
    warned: list[tuple[int, dict[str, Any]]]
    if called and res.judged:
        judged = True
        warned = [(vid, _warning(by_vid[vid], why or "Applies to this task.")) for vid, why in res.matches]
        # withheld from the LLM by the privacy gate: deterministic, stricter threshold
        withheld = [c for c in cands if c.version_id in res.denied]
        warned += _det(withheld, "withheld by the privacy policy", TAU_STRICT)
        warned = warned[:MAX_WARNINGS]
    elif all_withheld:  # nothing could be sent: the privacy-withheld rule applies to every candidate
        warned = _det(cands, "withheld by the privacy policy", TAU_STRICT)
    else:
        warned = _det(cands, status.replace("_", " "), TAU)

    if called:  # time passed without a transaction: authority and visibility are re-checked
        if released:
            assert reconnect is not None
            async with reconnect() as fresh_conn:
                visible = await _recheck(fresh_conn, ctx, request.project, [v for v, _ in warned])
        else:
            visible = await _recheck(conn, ctx, request.project, [v for v, _ in warned])
        warned = [(v, w) for v, w in warned if v in visible]

    envelope: dict[str, Any] = {
        "project": project.slug,
        "verdict": VERDICT_WARN if warned else VERDICT_NONE,
        "judged": judged,
        "judge": status if judged else rj.RETRIEVAL_ONLY,
        "warnings": [],
        "omitted": 0,
        "candidates_considered": considered,
    }
    if not judged:
        envelope["reason"] = "withheld" if all_withheld else status
    if guard_dropped:
        envelope["guard_dropped"] = guard_dropped
    return _pack(deps, envelope, budget, [w for _, w in warned])


def _det(cands: list[RiskCandidate], reason: str, tau: float) -> list[tuple[int, dict[str, Any]]]:
    hits = sorted((c for c in cands if c.det_score >= tau), key=lambda c: -c.det_score)
    return [(c.version_id, _warning(c, _det_why(c, reason))) for c in hits[:MAX_WARNINGS]]


__all__ = [
    "TAU",
    "TAU_STRICT",
    "TOOL",
    "TOP_K",
    "VEC_MAX_DIST",
    "VERDICT_NONE",
    "VERDICT_WARN",
    "RiskCandidate",
    "RiskRequest",
    "candidates",
    "det_score",
    "deterministic_warnings",
    "risk_check",
]
