"""``memory.query`` with ``synthesize:true`` (PHASE2-4-ROADMAP W2e; CC-4 ``query/2``; D-014, D-062,
D-066, D-067, D-071).

Without the flag nothing here runs: the tool handler calls ``read_service.query`` exactly as
before (same code path, byte-identical output). With the flag:

1. **Fast path** — ``read_service.query`` unchanged (its parts: the librarian block is held back
   and packed last, step 7), in the caller's transaction.
2. **Weak?** — the fast path is weak when it has hits and either its evidence is not
   ``"matched"`` (near misses) or the top hit's RRF score is below ``TAU_S``, calibrated on the
   ``cal`` split of ``tests/fixtures/synthesis`` (procedure and values in its README;
   ``tests/integration/test_w2e_synthesis_calibration.py``). Strong or empty → no LLM call.
3. **Excerpts** — the full text of the top ``MAX_EXCERPTS`` hit chunks, read in the same
   transaction as the fast path (only chunks it has just authorized and returned).
4. **D-062** — the synthesis never runs inside a transaction: over the API the request
   transaction (device ``FOR SHARE``) is committed and its connection returned (``detach``); a
   request whose transaction cannot be released answers ``synthesis_reason:"no_detach"``.
   ``librarian.tasks.synthesis`` then applies the privacy gate before every attempt, redaction,
   the spend guard, the 6 s cap and the citation validator.
5. **Re-check** — after the call, ONE short transaction on a fresh connection re-checks the device
   (revoked / expired / rebound → ``E_AUTH``: the whole result is discarded), the home-project read
   grant (else ``E_FORBIDDEN_PROJECT``) and the visibility of every cited item (invisible → the
   citation is removed; a sentence left without one is dropped).
6. **Pack** — ``synthesis:{status, text ≤ 400 tokens, clues[], tier[, dropped]}`` is added next to
   the hits. Every sentence of ``text`` carries its citations (``[v12.3, v40.0]``) and must cite
   at least one clue of the RETURNED hits: tail hits are dropped until the result fits
   ``token_budget``, and a sentence whose cited hits were all dropped goes with them, so
   ``clues ⊆ hits`` always holds. ``budget.used`` is the exact count, ≤ the limit.
7. **Librarian** — the query/2 ``librarian`` block (W2b), when the device has open questions, goes
   into what the hits and the synthesis left (the same rule as without the flag).

Every answer to a ``synthesize:true`` call carries ``contract_version:"query/2"`` and either
``synthesis`` (``status:"answered"``, or ``"insufficient_evidence"`` — abstention is a result, not
a failure, D-067) or ``synthesis_unavailable:true`` with ``synthesis_reason`` (``strong_evidence``,
``no_hits``, ``disabled``, ``busy``, ``unavailable``, ``timeout``, ``budget``, ``schema_fail``,
``privacy``, ``withheld``, ``guard``, ``error``, ``no_detach``, ``budget_room``). ``tier`` is
``"fallback"`` when a qualified fallback profile answered (D-066); a profile that failed the
G-LIVE-D bar is not qualified at all (D-071, ``disabled_tasks``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.errors import HlmError
from hlmemo.core import read_service
from hlmemo.core.budget import Meter
from hlmemo.core.clues import InvalidClue, decode_clue
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import ReadDeps, _read_project
from hlmemo.db import auth_queries
from hlmemo.db import read_queries as rq
from hlmemo.db import risk_queries as kq
from hlmemo.db import synthesis_queries as sq
from hlmemo.librarian.tasks import synthesis as syn

TOOL = "memory.query"
CONTRACT_VERSION = "query/2"

# ---- calibrated constant (cal split of tests/fixtures/synthesis; see its README) ---------------
#: the fast path is weak below this top-hit RRF score (K_RRF=60: one rank-1 list = 0.0164)
TAU_S = 0.0434

MAX_EXCERPTS = syn.MAX_EXCERPTS
TEXT_MAX_TOKENS = 400
#: hit kinds a synthesis may cite (every kind a query returns; the card is never a hit)
HIT_KINDS = ("fact", "episode", "lesson", "experience", "session_note", "doc_chunk")

# reasons (``synthesis_reason``) decided here; the rest are ``librarian.tasks.synthesis`` statuses
STRONG = "strong_evidence"
NO_HITS = "no_hits"
NO_DETACH = "no_detach"
BUDGET_ROOM = "budget_room"  # token_budget cannot hold one cited sentence next to its hit


def weak(result: dict[str, Any], tau: float = TAU_S) -> str | None:
    """``None`` when the fast path is weak (synthesize), else the reason not to."""
    hits = result.get("hits") or []
    if not hits:
        return NO_HITS
    if result.get("evidence") != "matched":
        return None  # near misses: hits exist, but the evidence signal says none matched
    return None if float(hits[0].get("score") or 0.0) < tau else STRONG


async def _excerpts(conn: AsyncConnection, hits: list[dict[str, Any]]) -> tuple[list[syn.Excerpt], list[int]]:
    """The top hit chunks' full text (+ the projects they belong to, for the capability set)."""
    wanted: list[tuple[int, int, dict[str, Any]]] = []
    for h in hits[:MAX_EXCERPTS]:
        try:
            clue = decode_clue(h.get("clue"))
        except InvalidClue:
            continue
        if clue.ordinal is not None:
            wanted.append((clue.version_id, clue.ordinal, h))
    rows = await sq.excerpt_rows(conn, [(v, o) for v, o, _ in wanted])
    out: list[syn.Excerpt] = []
    pids: set[int] = set()
    for vid, ordinal, h in wanted:
        r = rows.get((vid, ordinal))
        if r is None:
            continue
        pids.update(r.project_ids)
        out.append(
            syn.Excerpt(vid, h["clue"], str(h.get("title") or ""), r.valid_from.date().isoformat(), r.text)
        )
    return out, sorted(pids)


async def _recheck(conn: AsyncConnection, ctx: AuthContext, slug: str, version_ids: list[int]) -> set[int]:
    """D-062 (as ``risk_service``): after the LLM, ONE short transaction: the device is still
    trusted, unexpired and on the same token generation (else ``E_AUTH``: the result is discarded),
    still reads the home project (else ``E_FORBIDDEN_PROJECT``); returns which cited items it can
    still see now."""
    async with conn.transaction():
        dev = await kq.device_now(conn, ctx.device_id)
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
        pids = await kq.all_projects(conn) if fresh.is_admin else sorted(fresh.grants)
        pids = [p for p in pids if fresh.has(p, Role.READ)]
        f = kq.RiskFilter(
            pids=pids, scopes=list(fresh.scope_values()), at=await rq.clock_now(conn), kinds=HIT_KINDS
        )
        return await kq.visible_versions(conn, f, version_ids)


# --------------------------------------------------------------------------- packing
def render(meter: Meter, sentences: list[syn.Sentence], keep: set[str]) -> tuple[str, list[str], int]:
    """``(text, clues, n)``: the sentences citing at least one clue in ``keep`` (other citations
    removed), each followed by its markers, in order, until ``TEXT_MAX_TOKENS`` would be exceeded."""
    parts: list[str] = []
    clues: list[str] = []
    for s in sentences:
        cited = [c for c in s.clues if c in keep]
        if not cited:
            continue
        part = f"{s.text} [{', '.join(cited)}]"
        if meter.count_text(" ".join([*parts, part])) > TEXT_MAX_TOKENS:
            break
        parts.append(part)
        clues.extend(c for c in cited if c not in clues)
    return " ".join(parts), clues, len(parts)


def _unavailable(meter: Meter, env: dict[str, Any], budget: int, reason: str) -> dict[str, Any]:
    """The fast-path result + the ``query/2`` markers, tail hits dropped if the markers need room."""
    env["contract_version"] = CONTRACT_VERSION
    env["synthesis_unavailable"] = True
    env["synthesis_reason"] = reason
    return _fit(meter, env, budget)


def _fit(meter: Meter, env: dict[str, Any], budget: int) -> dict[str, Any]:
    hits = list(env["hits"])
    base_omitted = int(env["omitted"]) + len(hits)
    used = meter.settle(env, budget)
    while used > budget and hits:
        hits.pop()
        env["hits"] = hits
        env["omitted"] = base_omitted - len(hits)
        used = meter.settle(env, budget)
    if used > budget:
        raise ToolError("E_BUDGET_TOO_SMALL", f"token_budget {budget} cannot hold the result", min=used)
    return env


def pack(
    meter: Meter, env: dict[str, Any], budget: int, res: syn.SynthResult, sentences: list[syn.Sentence]
) -> dict[str, Any]:
    """Add ``synthesis`` to the fast-path result within ``budget`` (module docstring, step 6)."""
    tier = "fallback" if res.fallback else "primary"
    env["contract_version"] = CONTRACT_VERSION
    if res.answer == syn.INSUFFICIENT:
        env["synthesis"] = {"status": syn.INSUFFICIENT, "text": "", "clues": [], "tier": tier}
        return _fit(meter, env, budget)
    hits = list(env["hits"])
    n0 = len(hits)
    base_omitted = int(env["omitted"]) + n0
    dropped = res.dropped

    def state(n: int, k: int | None = None) -> int | None:
        """Apply hits[:n] with its valid sentences (the first ``k`` of them); None: no sentence."""
        keep = {h["clue"] for h in hits[:n]}
        valid = [s for s in sentences if any(c in keep for c in s.clues)]
        if k is not None:
            valid = valid[:k]
        text, clues, m = render(meter, valid, keep)
        if not m:
            return None
        block: dict[str, Any] = {"status": syn.ANSWERED, "text": text, "clues": clues, "tier": tier}
        lost = dropped + (len(sentences) - m)
        if lost:
            block["dropped"] = lost
        env["hits"] = hits[:n]
        env["omitted"] = base_omitted - n
        env["synthesis"] = block
        return meter.settle(env, budget)

    # 1. every sentence that stays cited, with as many hits as fit. Dropping a hit shrinks the
    # result by at least its additive size, so states that clearly cannot fit are jumped over;
    # the landing state is then filled upward exactly (pack_prefix's rule).
    sizes = [meter.count(h) + 1 for h in hits]
    n, ceiling = n0, n0 + 1
    while n >= 1:
        used = state(n)
        if used is None:
            break
        if used <= budget:
            while n + 1 < ceiling:
                trial = state(n + 1)
                if trial is None or trial > budget:
                    state(n)
                    break
                n += 1
            return env
        ceiling = n
        over, step, saved = used - budget, 1, sizes[n - 1]
        while n - step > 1 and saved < over:
            step += 1
            saved += sizes[n - step]
        n -= step
    for n in range(n0, 0, -1):  # 2. a small budget: fewer sentences
        keep = {h["clue"] for h in hits[:n]}
        k_max = sum(1 for s in sentences if any(c in keep for c in s.clues))
        for k in range(k_max - 1, 0, -1):
            used2 = state(n, k)
            if used2 is not None and used2 <= budget:
                return env
    env.pop("synthesis", None)
    env["hits"] = hits
    env["omitted"] = base_omitted - n0
    return _unavailable(meter, env, budget, BUDGET_ROOM)


# --------------------------------------------------------------------------- entry point
async def query_synthesize(
    conn: AsyncConnection,
    ctx: AuthContext,
    args: dict[str, Any],
    *,
    deps: ReadDeps,
    synth: syn.Synthesizer | None,
    detach: Callable[[], Awaitable[bool]] | None = None,
    reconnect: Callable[[], AbstractAsyncContextManager[AsyncConnection]] | None = None,
    tau: float = TAU_S,
) -> dict[str, Any]:
    """``args`` are the query arguments WITHOUT ``synthesize``. ``conn`` is the caller's connection:
    over the API the request transaction (``detach`` commits it and returns it to the pool before the
    LLM; ``reconnect`` yields a fresh pooled connection for the re-check); a direct caller passes an
    idle ``conn`` (the reads then commit their own transactions and the re-check reuses ``conn``).
    The synthesis never runs while a transaction of ``conn`` is open (D-062).

    The optional query/2 ``librarian`` block (W2b) is packed LAST, after the hits and the synthesis
    (``read_service.add_librarian_block``: what they left, at most its share of the budget), so the
    one exact ``budget.used`` covers hits + synthesis + librarian (G2); the D-057 supersession rule
    has already shaped the hits the synthesis reads and cites."""
    async with conn.transaction():
        env, librarian = await read_service.query_parts(conn, ctx, args, deps=deps)
    out = await _synthesize(conn, ctx, args, env, deps.meter, synth, detach, reconnect, tau)
    if librarian is not None:
        read_service.add_librarian_block(deps.meter, out, librarian, int(out["budget"]["limit"]))
    return out


async def _synthesize(
    conn: AsyncConnection,
    ctx: AuthContext,
    args: dict[str, Any],
    env: dict[str, Any],
    meter: Meter,
    synth: syn.Synthesizer | None,
    detach: Callable[[], Awaitable[bool]] | None,
    reconnect: Callable[[], AbstractAsyncContextManager[AsyncConnection]] | None,
    tau: float,
) -> dict[str, Any]:
    """Steps 2-6 of the module docstring on the fast-path result ``env`` (no librarian block)."""
    budget = int(env["budget"]["limit"])
    reason = weak(env, tau)
    if reason is not None:
        return _unavailable(meter, env, budget, reason)
    if synth is None:
        return _unavailable(meter, env, budget, syn.DISABLED)
    if (why_not := synth.unavailable()) is not None:
        return _unavailable(meter, env, budget, why_not)
    async with conn.transaction():
        excerpts, pids = await _excerpts(conn, env["hits"])
    if not excerpts:
        return _unavailable(meter, env, budget, syn.WITHHELD)
    released = await detach() if detach is not None else False
    if not released and (reconnect is not None or conn.info.transaction_status != TransactionStatus.IDLE):
        # an API request whose transaction could not be released: never call the LLM inside it
        return _unavailable(meter, env, budget, NO_DETACH)
    caps = {"trigger_device_id": ctx.device_id, "question": [p for p in pids if ctx.has(p, Role.READ)]}
    res = await synth.synthesize(str(args.get("query") or ""), excerpts, caps)
    cited = sorted({decode_clue(c).version_id for s in res.sentences for c in s.clues})
    # time passed without a transaction: authority (always) and cited visibility are re-checked
    if released:
        assert reconnect is not None
        async with reconnect() as fresh_conn:
            visible = await _recheck(fresh_conn, ctx, env["project"], cited)
    else:
        visible = await _recheck(conn, ctx, env["project"], cited)
    if not res.ok:
        return _unavailable(meter, env, budget, res.status)
    sentences = []
    lost = 0
    for s in res.sentences:
        clues = [c for c in s.clues if decode_clue(c).version_id in visible]
        if clues:
            sentences.append(syn.Sentence(s.text, clues))
        else:
            lost += 1
    if res.answer == syn.ANSWERED and not sentences:
        return _unavailable(meter, env, budget, syn.GUARD)
    res.dropped += lost
    return pack(meter, env, budget, res, sentences)


__all__ = [
    "CONTRACT_VERSION",
    "MAX_EXCERPTS",
    "TAU_S",
    "TEXT_MAX_TOKENS",
    "pack",
    "query_synthesize",
    "render",
    "weak",
]
