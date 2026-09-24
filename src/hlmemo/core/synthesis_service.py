"""``memory.query`` with ``synthesize:true`` (PHASE2-4-ROADMAP W2e; CC-4 ``query/2``; D-014, D-062,
D-066, D-067, D-071).

Without the flag nothing here runs: the tool handler calls ``read_service.query`` exactly as
before (same code path, byte-identical output). With the flag:

1. **Fast path** — ``read_service.query`` unchanged, in the caller's transaction.
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
5. **Re-check** (Sol 51 #1/#2) — after the call, ONE short transaction re-checks the full D-062
   authority: the device is trusted, unexpired and still on the bearer's token generation (else
   ``E_AUTH``: the whole result is discarded) and still reads the home project (else
   ``E_FORBIDDEN_PROJECT``). Then NOTHING returned may be unreadable now: a hit (or the card) whose
   item the device can no longer see (home project + device scope) is removed, and a sentence is
   DROPPED as a whole when ANY item it cites is no longer citable (current, active, not
   ``device:*``, visible scope, and a live read grant on EVERY project of the item, co-owned ones
   included) or is no longer among the returned hits.
6. **Pack** — ``synthesis:{status, text ≤ 400 tokens, clues[], tier[, dropped]}`` is added next to
   the hits. Every sentence of ``text`` carries its citations (``[v12.3, v40.0]``), and ALL of them
   must be clues of the RETURNED hits: tail hits are dropped until the result fits
   ``token_budget``, and a sentence citing a dropped hit goes with it, so ``clues ⊆ hits`` always
   holds. ``budget.used`` is the exact count, ≤ the limit.
7. **Deadline** (Sol 51 #4) — the whole synthesizing request is bounded by ``TOTAL_DEADLINE_S``:
   the LLM gets what is left minus a reserve for the re-check; the re-check acquires a pooled
   connection within the deadline and falls back to a short unpooled one. When no connection can be
   had in time, the fast-path result is returned with ``synthesis_reason:"recheck_unavailable"``
   (no synthesis), never an error.

Every answer to a ``synthesize:true`` call carries ``contract_version:"query/2"`` and either
``synthesis`` (``status:"answered"``, or ``"insufficient_evidence"`` — abstention is a result, not
a failure, D-067) or ``synthesis_unavailable:true`` with ``synthesis_reason`` (``strong_evidence``,
``no_hits``, ``disabled``, ``busy``, ``unavailable``, ``timeout``, ``budget``, ``schema_fail``,
``privacy``, ``withheld``, ``guard``, ``error``, ``no_detach``, ``budget_room``,
``recheck_unavailable``). ``tier`` is
``"fallback"`` when a qualified fallback profile answered (D-066); a profile that failed the
G-LIVE-D bar is not qualified at all (D-071, ``disabled_tasks``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from psycopg import AsyncConnection
from psycopg import errors as pgerrors
from psycopg.pq import TransactionStatus
from psycopg_pool import PoolTimeout

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

# reasons (``synthesis_reason``) decided here; the rest are ``librarian.tasks.synthesis`` statuses
STRONG = "strong_evidence"
NO_HITS = "no_hits"
NO_DETACH = "no_detach"
BUDGET_ROOM = "budget_room"  # token_budget cannot hold one cited sentence next to its hit
NO_RECHECK = "recheck_unavailable"  # no connection for the post-call re-check within the deadline
#: the whole synthesizing request (fast path, LLM, re-check), seconds from the handler's start
TOTAL_DEADLINE_S = 7.0
#: time kept back from the LLM for the post-call re-check
RECHECK_RESERVE_S = 0.6


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


class _NoRecheck(Exception):
    """No connection for the post-call re-check within the request deadline."""


async def _recheck(
    conn: AsyncConnection, ctx: AuthContext, slug: str, home_pid: int, version_ids: list[int]
) -> tuple[set[int], set[int]]:
    """D-062 (Sol 51 #1/#2): after the LLM, ONE short transaction: the device is still trusted,
    unexpired and on the bearer's token generation (else ``E_AUTH``: the result is discarded) and
    still reads the home project (else ``E_FORBIDDEN_PROJECT``). Returns ``(readable, citable)``:
    the versions the device can still read as query results (home project + device scope), and the
    subset a synthesis may still cite (current, active, not ``device:*``, visible scope, a live read
    grant on EVERY project of the item)."""
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
        rows = await sq.version_access(conn, version_ids)
    scopes = set(fresh.scope_values())
    readable = {r.version_id for r in rows if home_pid in r.project_ids and r.device_scope in scopes}
    citable = {
        r.version_id
        for r in rows
        if r.version_id in readable
        and r.current
        and r.status == "active"
        and not r.device_scope.startswith("device:")
        and all(fresh.has(p, Role.READ) for p in r.project_ids)
    }
    return readable, citable


async def _recheck_in_time(
    conn: AsyncConnection,
    ctx: AuthContext,
    slug: str,
    home_pid: int,
    version_ids: list[int],
    *,
    released: bool,
    reconnect: Callable[[], AbstractAsyncContextManager[AsyncConnection]] | None,
    direct: Callable[[], Any] | None,
    remaining_s: float,
) -> tuple[set[int], set[int]]:
    """The re-check on the caller's idle ``conn`` (direct callers), or after a detach on a pooled
    connection acquired within the deadline, falling back to a short unpooled one (the pool may be
    exhausted after a long LLM call); ``_NoRecheck`` when neither is had in time."""
    if not released:
        return await _recheck(conn, ctx, slug, home_pid, version_ids)
    loop = asyncio.get_running_loop()
    end = loop.time() + max(0.2, remaining_s)
    if reconnect is not None:
        cm = reconnect()
        try:
            async with asyncio.timeout(max(0.05, min(1.0, (end - loop.time()) / 2))):
                fresh_conn = await cm.__aenter__()
        except (TimeoutError, PoolTimeout, pgerrors.OperationalError):
            pass
        else:
            try:
                result = await _recheck(fresh_conn, ctx, slug, home_pid, version_ids)
            except BaseException as exc:
                await cm.__aexit__(type(exc), exc, exc.__traceback__)
                raise
            await cm.__aexit__(None, None, None)
            return result
    if direct is not None:
        try:
            async with asyncio.timeout(max(0.05, end - loop.time())):
                own = await direct()
        except (TimeoutError, OSError, pgerrors.OperationalError):
            raise _NoRecheck from None
        async with own:
            return await _recheck(own, ctx, slug, home_pid, version_ids)
    raise _NoRecheck


def _filter_returned(env: dict[str, Any], readable: set[int]) -> None:
    """Remove every hit (and the card) the device can no longer read at return time."""
    env["hits"] = [h for h in env["hits"] if decode_clue(h["clue"]).version_id in readable]
    card = env.get("card")
    if card is not None and decode_clue(card["clue"]).version_id not in readable:
        env["card"] = None


def _returned_versions(env: dict[str, Any]) -> list[int]:
    vids = [decode_clue(h["clue"]).version_id for h in env["hits"]]
    if env.get("card") is not None:
        vids.append(decode_clue(env["card"]["clue"]).version_id)
    return vids


# --------------------------------------------------------------------------- packing
def render(meter: Meter, sentences: list[syn.Sentence], keep: set[str]) -> tuple[str, list[str], int]:
    """``(text, clues, n)``: the sentences ALL of whose citations are in ``keep`` (Sol 51: a sentence
    citing a hit that is not returned is dropped, never trimmed), each followed by its markers, in
    order, until ``TEXT_MAX_TOKENS`` would be exceeded."""
    parts: list[str] = []
    clues: list[str] = []
    for s in sentences:
        cited = list(s.clues)
        if not cited or not all(c in keep for c in cited):
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
        if res.dropped:  # unsupported sentences were dropped (Sol 51 #3): the abstention says so
            env["synthesis"]["dropped"] = res.dropped
        return _fit(meter, env, budget)
    hits = list(env["hits"])
    n0 = len(hits)
    base_omitted = int(env["omitted"]) + n0
    dropped = res.dropped

    def state(n: int, k: int | None = None) -> int | None:
        """Apply hits[:n] with its valid sentences (the first ``k`` of them); None: no sentence."""
        keep = {h["clue"] for h in hits[:n]}
        valid = [s for s in sentences if all(c in keep for c in s.clues)]
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
        k_max = sum(1 for s in sentences if all(c in keep for c in s.clues))
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
    deadline_s: float = TOTAL_DEADLINE_S,
) -> dict[str, Any]:
    """``args`` are the query arguments WITHOUT ``synthesize``. ``conn`` is the caller's connection:
    over the API the request transaction (``detach`` commits it and returns it to the pool before the
    LLM; ``reconnect`` yields a fresh pooled connection for the re-check); a direct caller passes an
    idle ``conn`` (the reads then commit their own transactions and the re-check reuses ``conn``).
    The synthesis never runs while a transaction of ``conn`` is open (D-062)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s
    async with conn.transaction():
        env = await read_service.query(conn, ctx, args, deps=deps)
    meter = deps.meter
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
        home = await rq.resolve_project(conn, env["project"])
    if not excerpts or home is None:
        return _unavailable(meter, env, budget, syn.WITHHELD)
    released = await detach() if detach is not None else False
    if not released and (reconnect is not None or conn.info.transaction_status != TransactionStatus.IDLE):
        # an API request whose transaction could not be released: never call the LLM inside it
        return _unavailable(meter, env, budget, NO_DETACH)
    caps = {
        "trigger_device_id": ctx.device_id,
        "token_generation": ctx.token_generation,  # a rotated bearer loses authority (D-062)
        "question": [p for p in pids if ctx.has(p, Role.READ)],
    }
    res = await synth.synthesize(
        str(args.get("query") or ""), excerpts, caps, timeout_s=end - loop.time() - RECHECK_RESERVE_S
    )
    cited = {decode_clue(c).version_id for s in res.sentences for c in s.clues}
    # time passed without a transaction: authority, and everything returned, are re-checked
    try:
        readable, citable = await _recheck_in_time(
            conn,
            ctx,
            env["project"],
            home.project_id,
            sorted(set(_returned_versions(env)) | cited),
            released=released,
            reconnect=reconnect,
            direct=synth.connect,
            remaining_s=end - loop.time(),
        )
    except _NoRecheck:
        return _unavailable(meter, env, budget, NO_RECHECK)
    _filter_returned(env, readable)
    if not res.ok:
        return _unavailable(meter, env, budget, res.status)
    # Sol 51 #1: a sentence citing ANY item that is no longer citable is dropped as a whole
    sentences = [s for s in res.sentences if all(decode_clue(c).version_id in citable for c in s.clues)]
    lost = len(res.sentences) - len(sentences)
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
