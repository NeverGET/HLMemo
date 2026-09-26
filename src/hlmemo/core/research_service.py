"""``memory.ask``: the research librarian (D-130, D-136). Read-only.

The caller sends a question (and optionally the project); the librarian answers LLM-to-LLM with a
refined answer, up to 3 primary sources (each with a verbatim quote) and up to 5 related sources.
Every source is a handle the caller can drill (``memory.drilldown``) or pull raw (``memory.raw``).

The loop (the W-B V5 prototype, ``eval/research/research_loop.py`` @ be3956b, plus the D-136
completeness pass):

1. **Map** (request transaction): authorize the project (``read``), load the caller's VIEW and
   render the Memory Map (``core/memory_map``); then ``detach`` commits and returns the request
   connection (D-062: no transaction is ever open during an LLM call) and extends the request
   deadline to ``HLM_RESEARCH_TIMEOUT_S`` (nothing is held any more).
2. **Plan** (LLM, JOB ``plan``): map + question → 2–4 queries + ≤ 6 map sections. (The ORIGINAL
   question is searched in step 1, inside the request transaction: no DB work overlaps an LLM call.)
3. **Retrieve** (DB, a fresh short transaction that re-resolves the caller's authority first): every
   query through the internal ``memory.query`` path (``read_service.query_parts``, the caller's
   scope), reciprocal-rank fusion (D-165: the top items fused per ITEM, their best in-document chunks
   drilled first), ±1-chunk collapse, then an INTERNAL drill of ≤ 12 handles (sections first):
   ``version_live`` + ``chunk_spans`` exactly like ``memory.drilldown`` but WITHOUT its ``access``
   event. A hit or section whose version is not in the VIEW (a device-scoped item of
   the caller, an item co-owned by a project the caller cannot read or with ``policy.librarian=off``,
   an item written after step 1, an item the D-083 isolation keeps out of the asked project) is
   dropped before any prompt.
4. **Answer** (LLM, JOB ``answer``) → deterministic validation (``librarian.tasks.research``).
5. **Refine** — only when the answer abstained: JOB ``refine`` (map + what was tried and read) →
   one more retrieval (new handles only) → JOB ``answer`` again.
6. **Completeness + repair** (LLM, JOB ``check``, when no refinement ran): the question's sub-asks
   against the draft and the same excerpts → the full revised answer, a missing fact added only as a
   new claim with its own quotes (the D-136 fix for the dominant W-B failure); the draft claims that
   the deterministic copy-through / attribution checks flagged are repaired in the same call. At most
   4 sequential LLM steps (addendum 7). The planned queries run in parallel.
7. **Attribution** (deterministic): a claim still naming a subject that neither its quotes nor its
   cited excerpts state is dropped.
8. **Re-check** (a fresh short transaction): the device is still trusted, unexpired and on the
   bearer's token generation (else ``E_AUTH``) and still reads the project (else
   ``E_FORBIDDEN_PROJECT``); a returned handle that is no longer citable (current, active, visible,
   read grant + librarian policy on every project) is removed; when ANY version whose text reached
   the provider lost the caller's authority meanwhile, the answer and the queries are withheld
   (they may carry its content) and the result is an abstention.

D-156 ``HLM_RESEARCH_ANSWER_MODE=cite`` (V14 "write, then cite"): step 4 is the JOB ``write`` (complete
sentences citing excerpt handles, checked by ``research.validate_cited``), a refinement re-answers
with ``write`` too, and steps 6 and 7 do not run (no completeness call, no attribution pass: each
sentence is verified against its cited excerpts' full text); the re-check (8) verifies each kept
sentence again against the excerpts still citable.

D-159 ``HLM_RESEARCH_SELECT`` (cite mode only, "select, then write"): before each ``write`` the JOB
``select`` sees the same excerpts and picks the ≤ 6 that state the answer; the ``write`` sees, cites
and is verified against those only (the re-check too), and the retrieved excerpts it left out follow
the write's own ``related`` (≤ 5). An empty, unknown-only or failed select, or too little time left
for select AND write, writes over every excerpt (``meta.flags.select_fallback``); after a refinement
the select runs again over the widened set.

D-162 ``HLM_RESEARCH_ANSWER_MODE=prose`` (V16): step 4 is the JOB ``prose`` (free prose and the sources
it draws on, checked by ``research.validate_prose``: a sentence is dropped only for a hard literal no
shown excerpt states; every other one is kept and attributed to its best source lines), a refinement
re-answers with ``prose``, and steps 6 and 7 do not run; the re-check (8) checks and attributes each
kept sentence again over the excerpts still citable. D-165: the attribution scores every shown
excerpt's lines by shared hard literals, then content words, then the multilingual similarity of the
server's own embedder (the one the hybrid search embeds queries with: one embedding of the kept
sentences and the excerpt lines per answer, ``research.LineSim``, within ``research.ATTR_EMBED_S``; no
embedder or no time → literals and words alone); the model's ``sources`` are a tie-break only; there
is no polarity flag.

At most ``research.MAX_CALLS_NO_SELECT`` (4) logical LLM calls, ``research.MAX_CALLS`` (6) with the
D-159 select (plan, select, write, refine, select, write); provider requests (schema retries,
fallback) are capped per question by the lineage ceiling (``research.MAX_ATTEMPTS``). Before every
call the strict privacy gate runs over exactly the version ids whose text is in the prompt: denied
items are removed and the prompt rebuilt; an item that was already SENT and is now denied for a
privacy reason aborts the question (``E_UNAVAILABLE``, retryable). The provider re-runs the gate
before each attempt.

memory.ask writes nothing but the spend guard's ledger (``llm_calls``, ``llm_budget``,
``llm_reservations``, ``llm_lineage_calls``): no event, no version, no access event, no job, no map
cache row (over MCP the middleware refreshes ``devices.last_seen_at`` as for every tool call; that is
auth telemetry, not memory). Systemic failures (disabled, busy, breaker open, budget stop, no answer
in time) are ``E_UNAVAILABLE`` with ``details.reason``; an abstention is a successful answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.errors import HlmError
from hlmemo.config import get_settings
from hlmemo.core import memory_map as mm
from hlmemo.core import read_service
from hlmemo.core.budget import BudgetError, Meter, validate_budget
from hlmemo.core.clues import InvalidClue, decode_clue
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import ReadDeps, _read_project
from hlmemo.core.write_models import SLUG_RE
from hlmemo.db import auth_queries
from hlmemo.db import read_queries as rq
from hlmemo.db import risk_queries as kq
from hlmemo.db import synthesis_queries as sq
from hlmemo.db.librarian_queries import cross_project_excluded
from hlmemo.librarian.errors import (
    AuthorityLost,
    BudgetDeferred,
    CassetteMiss,
    DeadlineExceeded,
    JobCallCapExceeded,
    LlmConfigError,
    LlmDisabled,
    PrivacyDenied,
    ProviderUnavailable,
    SchemaFail,
)
from hlmemo.librarian.tasks import research as rs

TOOL = "memory.ask"
DEFAULT_BUDGET = 3000
QUESTION_MAX = 2000
QUERY_BUDGET = 2000  # token budget of each internal memory.query (about 15-25 hits)
MAX_DRILL = 12
#: addendum 7: planned queries run in parallel, at most this many at once per question
PARALLEL_QUERIES = 3
#: D-165: the top fused ITEMS whose best in-document chunk takes a drill slot (after the sections)
DOC_TOP = 4
REFINE_MAX_NEW = 8
RRF_K = 60
#: per-call caps (seconds) inside the whole request's HLM_RESEARCH_TIMEOUT_S
PLAN_CAP_S = 10.0
ANSWER_CAP_S = 18.0
CHECK_CAP_S = 16.0
REFINE_CAP_S = 9.0
#: D-159: the select call's cap, and the time it must leave for the write (else no select)
SELECT_CAP_S = 8.0
SELECT_WRITE_RESERVE_S = 8.0
#: an optional step starts only with this much time left (the re-check keeps RECHECK_RESERVE_S)
MIN_REFINE_S = 16.0
MIN_CHECK_S = 7.0
MIN_CALL_S = 1.5
#: D-165: the prose attribution embeds only with at least this much time left
ATTR_MIN_LEFT_S = 1.0
RECHECK_RESERVE_S = 1.0
DB_PHASE_TIMEOUT_MS = 8000
CONTEXT = (
    "The memory of one software project, written by and for coding agents: imported project documents "
    "(decision logs, specifications, design consults and reviews, research reports, status notes, "
    "runbooks), the agents' notes and lessons, session notes and, when imported, one git-log item per day."
)
_ARGS = frozenset({"question", "project", "token_budget"})


# --------------------------------------------------------------------------- request
@dataclass(slots=True)
class AskRequest:
    question: str
    project: str | None
    token_budget: int


def parse_request(args: dict[str, Any]) -> AskRequest:
    extra = sorted(set(args) - _ARGS)
    if extra:
        raise ToolError("E_INVALID_ARG", f"unknown argument(s): {', '.join(extra)}", fields=extra)
    q = args.get("question")
    if not isinstance(q, str) or not q.strip():
        raise ToolError("E_INVALID_ARG", "question must be a non-empty string", field="question")
    if len(q) > QUESTION_MAX:
        raise ToolError("E_INVALID_ARG", f"question longer than {QUESTION_MAX} characters", field="question")
    project = args.get("project")
    if project is not None:
        if not isinstance(project, str) or not re.match(SLUG_RE, project):
            raise ToolError("E_INVALID_ARG", "project must be a project slug", field="project")
    try:
        budget = validate_budget(args.get("token_budget", DEFAULT_BUDGET))
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc
    return AskRequest(" ".join(q.split()), project, budget)


async def default_project(conn: AsyncConnection, ctx: AuthContext) -> str:
    """The device's only readable (non-reserved) project; otherwise the caller must name one."""
    if ctx.is_admin:
        raise ToolError("E_INVALID_ARG", "project is required", field="project")
    ids = sorted(ctx.grants)
    cur = await conn.execute(
        "SELECT slug FROM projects WHERE project_id = ANY(%s)"
        " AND COALESCE(policy->>'reserved', 'false') <> 'true' AND archived_at IS NULL ORDER BY slug",
        (ids,),
    )
    slugs = [r[0] for r in await cur.fetchall()]
    if len(slugs) != 1:
        raise ToolError(
            "E_INVALID_ARG", f"project is required (this device reads {len(slugs)} projects)", field="project"
        )
    return str(slugs[0])


# --------------------------------------------------------------------------- retrieval helpers
def rrf(lists: list[list[dict[str, Any]]], k: int = RRF_K) -> list[str]:
    """Reciprocal-rank fusion of hit lists by clue; ties keep first-seen order."""
    score: dict[str, float] = {}
    order: dict[str, int] = {}
    for hits in lists:
        for r, h in enumerate(hits, start=1):
            c = str(h.get("clue"))
            score[c] = score.get(c, 0.0) + 1.0 / (k + r)
            order.setdefault(c, len(order))
    return sorted(score, key=lambda c: (-score[c], order[c]))


def rrf_items(lists: list[list[dict[str, Any]]], k: int = RRF_K) -> list[str]:
    """D-165: reciprocal-rank fusion by ITEM (version): one vote per item per hit list (its best rank
    there), so a long document whose chunks rank in different queries is not split into weak chunk
    votes (D-163: ROADMAP #1 in 4 of 5 queries was fused #7 by chunk). Each item is returned as its
    best chunk-fused handle (``rrf``); items in fused order, ties first-seen."""
    score: dict[int, float] = {}
    order: dict[int, int] = {}
    for hits in lists:
        voted: set[int] = set()
        for r, h in enumerate(hits, start=1):
            try:
                vid = decode_clue(h.get("clue")).version_id
            except InvalidClue:
                continue
            if vid in voted:
                continue
            voted.add(vid)
            score[vid] = score.get(vid, 0.0) + 1.0 / (k + r)
            order.setdefault(vid, len(order))
    rep: dict[int, str] = {}
    for c in rrf(lists):
        with contextlib.suppress(InvalidClue):
            rep.setdefault(decode_clue(c).version_id, c)
    return [rep[v] for v in sorted(score, key=lambda v: (-score[v], order[v]))]


def collapse(handles: list[str], limit: int) -> list[str]:
    """Keep handles in order, skipping a chunk handle already covered by the ±1 neighbourhood of a
    kept chunk handle of the same item (a drilled chunk comes with its neighbours) or by a kept
    whole-item handle."""
    kept: list[str] = []
    covered: set[tuple[int, int]] = set()
    whole: set[int] = set()
    for h in handles:
        try:
            c = decode_clue(h)
        except InvalidClue:
            continue
        v, o = c.version_id, c.ordinal
        if v in whole or h in kept or (o is not None and (v, o) in covered):
            continue
        kept.append(h)
        if o is None:
            whole.add(v)
        else:
            covered.update({(v, o - 1), (v, o), (v, o + 1)})
        if len(kept) >= limit:
            break
    return kept


def drill_order(sections: list[str], best: list[str], fused: list[str], skip: set[str]) -> list[str]:
    """What one retrieval drills (≤ ``MAX_DRILL``, ±1-chunk collapse, never a handle in ``skip``): the
    plan's sections first, then (D-165) the top items' best in-document chunks (``_doc_best``), TAKING
    slots, then the chunk-fused hits in their order."""
    return collapse([h for h in [*sections, *best, *fused] if h not in skip], MAX_DRILL)


class _AuthorityChanged(Exception):
    """An item whose text already reached the provider is now denied to the caller."""


# --------------------------------------------------------------------------- the run
@dataclass(slots=True)
class _Run:
    conn: AsyncConnection
    ctx: AuthContext
    researcher: rs.Researcher
    deps: ReadDeps
    settings: Any
    question: str
    slug: str
    project_id: int
    end: float
    reconnect: Callable[[], AbstractAsyncContextManager[AsyncConnection]] | None
    released: bool = False
    view: dict[int, mm.ViewItem] = field(default_factory=dict)
    entries: dict[int, list[mm.Entry]] = field(default_factory=dict)
    summaries: dict[str, tuple[list[int], str]] = field(default_factory=dict)
    excluded: set[int] = field(default_factory=set)
    sent: set[int] = field(default_factory=set)
    lineage: str = field(default_factory=lambda: str(uuid.uuid4()))
    #: D-165: the last prose answer's sentence/line similarity (the re-check reads its cache)
    sim: rs.LineSim | None = None
    calls: int = 0
    steps: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    map_tokens: int = 0
    caps: dict[str, Any] = field(default_factory=dict)
    #: addendum 2 (meta.flags): claims saved by a re-quote / completed with a literal's sentence
    #: (summed over the answer and check calls), claims not kept and the main (first) claim not kept
    #: in the LAST validated answer, claims the self-check dropped
    flags: dict[str, Any] = field(
        default_factory=lambda: {
            "requoted": 0,
            "completed": 0,
            "dropped_claims": 0,
            "main_dropped": False,
            "budget_stop": False,
            "rewrites_asked": 0,
        }
    )

    # ---- db phases
    @contextlib.asynccontextmanager
    async def db(self) -> Any:
        """A short transaction for one DB phase: after ``detach`` on a fresh pooled connection,
        otherwise (direct callers) on the caller's idle connection."""
        if self.released:
            assert self.reconnect is not None
            async with self.reconnect() as c, c.transaction():
                await _phase_timeouts(c)
                yield c
        else:
            async with self.conn.transaction():
                await _phase_timeouts(self.conn)
                yield self.conn

    async def fresh_ctx(self, c: AsyncConnection) -> AuthContext:
        """The caller's authority NOW (D-062): trusted, unexpired, same token generation, current
        grants, still reading the project."""
        dev = await kq.device_now(c, self.ctx.device_id)
        if (
            dev is None
            or dev.status != "trusted"
            or dev.expired
            or dev.token_generation != self.ctx.token_generation
        ):
            raise HlmError("E_AUTH", "device revoked or rebound during the request")
        grants = await auth_queries.select_active_grants(c, self.ctx.device_id)
        fresh = AuthContext(
            device_id=self.ctx.device_id,
            device_class=dev.device_class,
            is_admin=dev.is_admin,
            token_generation=dev.token_generation,
            grants={pid: Role(role) for pid, role in grants},
            client=self.ctx.client,
        )
        await _read_project(c, fresh, self.slug)
        return fresh

    @property
    def cite(self) -> bool:
        """D-156: the V14 "write, then cite" answer mode."""
        return self.researcher.answer_mode == "cite"

    @property
    def prose(self) -> bool:
        """D-162: the V16 "prose" answer mode."""
        return self.researcher.answer_mode == "prose"

    @property
    def claims_mode(self) -> bool:
        """The default answer mode: quoted claims, the completeness pass and the attribution pass."""
        return not (self.cite or self.prose)

    def redact(self, text: str) -> str:
        """The provider's redactor (its configured rules), applied to every prompt value before the
        JSON serialisation (review 79 T2)."""
        return self.researcher.redactor.text(text)

    def in_view(self, vid: int) -> bool:
        return vid in self.view and vid not in self.excluded

    async def retrieve(
        self, queries: list[str], sections: list[str], prior: list[list[dict[str, Any]]], skip: set[str]
    ) -> tuple[list[rs.Excerpt], list[list[dict[str, Any]]]]:
        """One DB phase: run ``queries``, fuse with ``prior`` hit lists, drill ≤ MAX_DRILL handles
        (``sections`` first) that are in the view and not in ``skip``."""
        lists = list(prior)
        if self.released and len(queries) > 1:
            # addendum 7: the planned queries run in PARALLEL, each in its own short transaction on
            # a pooled connection (at most PARALLEL_QUERIES at once); the drill follows
            sem = asyncio.Semaphore(PARALLEL_QUERIES)

            async def one(query: str) -> list[dict[str, Any]]:
                async with sem, self.db() as c1:
                    return await self._search(c1, await self.fresh_ctx(c1), query)

            lists += await asyncio.gather(*(one(q) for q in queries))
            queries_done = True
        else:
            queries_done = False
        async with self.db() as c:
            fresh = await self.fresh_ctx(c)
            if not queries_done:
                for q in queries:
                    lists.append(await self._search(c, fresh, q))
            fused = [h for h in rrf(lists) if self._handle_in_view(h)]
            # D-165 (D-163 what-if: gold-in-excerpts 20 -> 21 of 25, no losses): the top DOC_TOP
            # ITEMS (fused per item) drill their best in-document chunk right after the sections,
            # TAKING slots; the remaining slots keep the chunk-fused order
            items = [h for h in rrf_items(lists) if self._handle_in_view(h)]
            best = await self._doc_best(c, items, [self.question, *queries])
            excerpts = await self._drill(c, drill_order(sections, best, fused, skip))
        return excerpts, lists

    async def _search(self, c: AsyncConnection, fresh: AuthContext, query: str) -> list[dict[str, Any]]:
        return await _search(c, fresh, self.slug, query, self.deps)

    async def _doc_best(self, c: AsyncConnection, ranked: list[str], texts: list[str]) -> list[str]:
        """For each of the ``DOC_TOP`` top-ranked items of ``ranked`` (handles, D-165: one per item
        in item-fused order), its best-matching chunk (most of the question's and queries' content
        words, + 2 per literal of the question; ties: the earliest) as a chunk handle (addendum 6
        #3). An item of one chunk, or none that matches, keeps its own ranked handle."""
        words = set().union(*(rs.content_words(t) for t in texts))
        lits = [x for t in texts[:1] for x in rs.literals(t)]
        docs: dict[int, str] = {}
        for h in ranked:
            docs.setdefault(decode_clue(h).version_id, h)
            if len(docs) >= DOC_TOP:
                break
        out: list[str] = []
        for vid, hit in docs.items():
            item = self.view.get(vid)
            best = hit
            if item is not None and item.n_chunks > 1:
                scored = []
                for sp in await rq.chunk_spans(c, vid):
                    cw = rs.content_words(sp.text)
                    hay = rs.lit_hay(sp.text)
                    score = len(words & cw) + 2 * sum(1 for x in lits if rs.literal_supported(x, hay))
                    scored.append((score, -sp.ordinal, sp.ordinal))
                if scored and max(scored)[0] > 0:
                    best = f"v{vid}.{max(scored)[2]}"
            out.append(best)
        return out

    def _handle_in_view(self, handle: str) -> bool:
        try:
            return self.in_view(decode_clue(handle).version_id)
        except InvalidClue:
            return False

    async def _drill(self, c: AsyncConnection, handles: list[str]) -> list[rs.Excerpt]:
        """The internal drill: what ``memory.drilldown`` returns for each handle (a chunk with its
        ±1 neighbours, or a whole item), under the caller's scope, WITHOUT its access event."""
        now = await rq.clock_now(c)
        scopes = mm.view_scopes(self.ctx)
        redact = self.researcher.redactor.text
        out: list[rs.Excerpt] = []
        for h in handles:
            clue = decode_clue(h)
            item = self.view.get(clue.version_id)
            if item is None or clue.version_id in self.excluded:
                continue
            v = await rq.version_live(c, clue.version_id, self.project_id, scopes, now, now, ["active"])
            if v is None:
                continue
            if clue.ordinal is not None:
                spans = await rq.chunk_spans(c, v.version_id, clue.ordinal - 1, clue.ordinal + 1)
                if not any(s.ordinal == clue.ordinal for s in spans):
                    continue
                text = v.body[spans[0].char_start : spans[-1].char_end]
            else:
                text = v.body
            out.append(
                rs.Excerpt(
                    h,
                    v.version_id,
                    v.title,
                    item.path,
                    v.valid_from.date().isoformat(),
                    redact(rs.clip(text)),
                )
            )
        return out

    # ---- the map
    def render_map(self) -> mm.MemoryMap:
        items = [v for v in self.view.values() if v.version_id not in self.excluded]
        summaries = {k: s for k, s in self.summaries.items() if not (set(s[0]) & self.excluded)}
        m = mm.build_map(
            items,
            self.entries,
            summaries,
            budget_tokens=int(self.settings.research_map_tokens),
            project=self.slug,
            meter=self.deps.meter,
        )
        self.map_tokens = m.tokens
        return m

    # ---- llm calls
    def remaining(self) -> float:
        return self.end - asyncio.get_running_loop().time() - RECHECK_RESERVE_S

    async def call(
        self, job: str, build: Callable[[], tuple[str, list[int]]], cap_s: float
    ) -> dict[str, Any] | None:
        """One logical LLM call. ``build()`` → ``(user message, version ids whose text it carries)``;
        it is re-run after denied items are excluded. Returns the model output, or None when the
        call could not be made in time or at all (``rs.ResearchUnavailable`` carries the reason)."""
        if self.calls >= self.researcher.max_calls:
            return None
        if self.flags["budget_stop"]:  # a per-question budget stop is final: nothing more is sent
            raise rs.ResearchUnavailable("question_budget")
        loop = asyncio.get_running_loop()
        for _ in range(3):
            left = self.remaining()
            if left < MIN_CALL_S:
                raise rs.ResearchUnavailable("timeout")
            user, ids = build()
            verdict = await self.researcher.gate(self.caps, ids)
            if not verdict.device_ok:
                raise HlmError("E_AUTH", "device revoked or rebound during the request")
            if self.sent:
                # text already sent may ride along in derived form (queries, a draft, an answer): its
                # sources must still pass the privacy rules; a superseded one is judged on them too
                prior = await self.researcher.gate_carried(self.caps, sorted(self.sent))
                if not prior.device_ok:
                    raise HlmError("E_AUTH", "device revoked or rebound during the request")
                if prior.denied:
                    raise _AuthorityChanged
            denied = set(verdict.denied) & set(ids)
            if denied:
                self.excluded |= denied
                continue
            # addendum 5: the per-question budget (actual spend so far + this call's worst case)
            usd, tokens = self.researcher.spent(self.lineage)
            worst_usd, worst_tokens = self.researcher.worst_case(job, user)
            if usd + worst_usd > Decimal(str(self.settings.research_max_usd)) or tokens + worst_tokens > int(
                self.settings.research_max_tokens
            ):
                self.flags["budget_stop"] = True
                raise rs.ResearchUnavailable("question_budget")
            deadline = loop.time() + min(cap_s, left)
            try:
                async with asyncio.timeout_at(deadline + 0.5):
                    res = await self.researcher.complete(
                        job,
                        user,
                        capabilities=self.caps,
                        gate_ids=ids,
                        carried_ids=sorted(self.sent),
                        deadline=deadline,
                        lineage=self.lineage,
                        attempt_guard=self.attempt_guard,
                    )
            except PrivacyDenied:
                continue  # changed between our gate and the attempt's: gate (and rebuild) again
            except AuthorityLost as exc:
                raise HlmError("E_AUTH", "device revoked or rebound during the request") from exc
            except (TimeoutError, DeadlineExceeded) as exc:
                raise rs.ResearchUnavailable("timeout") from exc
            except BudgetDeferred as exc:
                raise rs.ResearchUnavailable("budget") from exc
            except JobCallCapExceeded as exc:
                raise rs.ResearchUnavailable("call_cap") from exc
            except SchemaFail as exc:
                raise rs.ResearchUnavailable("schema_fail") from exc
            except (ProviderUnavailable, httpx.HTTPError, OSError) as exc:
                raise rs.ResearchUnavailable("unavailable") from exc
            except (LlmDisabled, LlmConfigError) as exc:
                raise rs.ResearchUnavailable("disabled") from exc
            except CassetteMiss as exc:
                raise rs.ResearchUnavailable("error") from exc
            self.calls += 1
            self.steps.append(job)
            self.sent |= set(ids)
            return res.output
        raise rs.ResearchUnavailable("privacy")

    async def attempt_guard(self, _profile: Any, worst_usd: Decimal, worst_tokens: int) -> None:
        """Review 79 T4: before EVERY provider attempt (a schema retry and the fallback included),
        the question's actual spend so far (every attempt is tallied when its ledger row is written,
        before the next one starts: the attempts of one question are sequential) plus THIS attempt's
        worst case (its own profile's prices) must stay within the per-question USD and token
        budget; otherwise the question stops (budget_stop) before anything is reserved or sent."""
        usd, tokens = self.researcher.spent(self.lineage)
        if usd + worst_usd > Decimal(str(self.settings.research_max_usd)) or tokens + worst_tokens > int(
            self.settings.research_max_tokens
        ):
            self.flags["budget_stop"] = True
            raise rs.ResearchUnavailable("question_budget")

    async def plan(self, m: mm.MemoryMap) -> tuple[list[str], list[str]]:
        def build() -> tuple[str, list[int]]:
            cur = self.render_map() if self.excluded else m
            return rs.plan_user(self.question, CONTEXT, cur.text, self.redact), cur.gate_ids()

        try:
            obj = await self.call("plan", build, PLAN_CAP_S)
        except rs.ResearchUnavailable:
            return [], []  # the loop still runs on the question alone
        queries, sections = rs.parse_plan(obj, self.question)
        return queries, sections

    def prune(self, v: rs.Validated, excerpts: list[rs.Excerpt]) -> rs.Validated:
        """``v`` restricted to the claims whose every supporting excerpt is still admitted (an
        excerpt excluded mid-request must not ride into a later prompt inside a draft or a quote)."""
        vid = {e.handle: e.version_id for e in excerpts}
        ok = {h for h, x in vid.items() if x not in self.excluded}
        if not v.answered or all(h in ok for c in v.kept for h, _q in c.support):
            return v
        claims = [c for c in v.kept if all(h in ok for h, _q in c.support)]
        shown = {e.handle: e for e in excerpts if e.handle in ok}
        return rs.assemble(
            rs.ANSWERED,
            v.answer,
            claims,
            [h for h in v.related if h in ok],
            v.confidence,
            shown,
            self.researcher.redactor.text,
            missing=v.missing,
            sub_asks=v.sub_asks,
        )

    async def answer(
        self, excerpts: list[rs.Excerpt], job: str = "answer", draft: rs.Validated | None = None
    ) -> rs.Validated:
        if self.cite and draft is None:
            return await self.write(excerpts)
        if self.prose and draft is None:
            return await self.write_prose(excerpts)

        def build() -> tuple[str, list[int]]:
            ex = [e for e in excerpts if e.version_id not in self.excluded]
            if draft is None:
                return rs.answer_user(self.question, ex, redact=self.redact), [e.version_id for e in ex]
            cur = self.prune(draft, excerpts)  # the draft's quotes come only from admitted excerpts
            fixes = rs.claim_fixes(cur, self.question)  # addendum 7: the repair rides on this call
            self.flags["rewrites_asked"] = len(fixes)
            user = rs.check_user(self.question, cur.draft(), ex, fixes, self.redact)
            return user, [e.version_id for e in ex]

        cap = ANSWER_CAP_S if draft is None else CHECK_CAP_S
        obj = await self.call(job, build, cap)
        shown = {e.handle: e for e in excerpts if e.version_id not in self.excluded}
        v = rs.validate_answer(obj, shown, self.researcher.redactor.text)
        if obj is not None:
            self.flags["requoted"] += v.requoted
            self.flags["completed"] += v.completed
            self.flags["dropped_claims"] = v.dropped_claims
            self.flags["main_dropped"] = v.main_dropped
        return v

    async def select(self, excerpts: list[rs.Excerpt]) -> list[rs.Excerpt]:
        """D-159: the JOB ``select`` over the admitted excerpts → the ones the ``write`` sees, in the
        order it picked them (``rs.parse_select``). ``[]`` (nothing picked, unknown ids only, a failed
        call, or too little time left for select AND write) means the write sees every excerpt
        (``select_fallback``): a question never abstains because the select found nothing. A budget
        stop here is raised by the write's own call."""

        def admitted() -> list[rs.Excerpt]:
            return [e for e in excerpts if e.version_id not in self.excluded]

        def build() -> tuple[str, list[int]]:
            ex = admitted()
            return rs.select_user(self.question, ex, redact=self.redact), [e.version_id for e in ex]

        ids: list[str] = []
        left = self.remaining() - SELECT_WRITE_RESERVE_S
        if left >= MIN_CALL_S and admitted():
            try:
                obj = await self.call("select", build, min(SELECT_CAP_S, left))
            except rs.ResearchUnavailable:
                obj = None
            ids = rs.parse_select(obj, [e.handle for e in admitted()])
        by_handle = {e.handle: e for e in excerpts}
        picked = [by_handle[h] for h in ids]
        self.flags["selected"] = len(picked)
        self.flags["select_fallback"] = not picked
        return picked

    async def write(self, excerpts: list[rs.Excerpt]) -> rs.Validated:
        """D-156 cite mode: the JOB ``write`` over the admitted excerpts (D-159: only the selected
        ones when the select picked any), checked sentence by sentence against exactly the excerpts
        it was shown (``rs.validate_cited``); the flags describe the LAST validated answer."""
        picked = await self.select(excerpts) if self.researcher.select else []
        chosen = picked or excerpts

        def build() -> tuple[str, list[int]]:
            ex = [e for e in chosen if e.version_id not in self.excluded]
            return rs.write_user(self.question, ex, redact=self.redact), [e.version_id for e in ex]

        obj = await self.call("write", build, ANSWER_CAP_S)
        shown = {e.handle: e for e in chosen if e.version_id not in self.excluded}
        v = rs.validate_cited(obj, shown, self.researcher.redactor.text)
        if picked:
            # D-159: verified (now and at the re-check) against the selected excerpts only; the
            # retrieved ones the select left out stay drillable after the write's own related
            v.written_over = list(shown)
            if v.answered:
                rest = [e.handle for e in excerpts if e.version_id not in self.excluded]
                rest = [h for h in rest if h not in shown]
                v.related = list(dict.fromkeys([*v.related, *rest]))[: rs.MAX_RELATED]
        if obj is not None:
            self.flags["dropped_claims"] = v.dropped_claims
            self.flags["main_dropped"] = v.main_dropped
            self.flags["dropped_sentences"] = v.dropped_sentences
            self.flags["uncited"] = v.uncited
            for why in rs.DROP_REASONS:
                self.flags[f"dropped_{why}"] = v.drop_reasons.get(why, 0)
        return v

    async def write_prose(self, excerpts: list[rs.Excerpt]) -> rs.Validated:
        """D-162 prose mode: the JOB ``prose`` over the admitted excerpts, checked and attributed
        sentence by sentence against exactly the excerpts it was shown (``rs.validate_prose``, off the
        event loop: D-165 embeds its sentences and lines); the flags describe the LAST validated
        answer."""

        def build() -> tuple[str, list[int]]:
            ex = [e for e in excerpts if e.version_id not in self.excluded]
            return rs.prose_user(self.question, ex, redact=self.redact), [e.version_id for e in ex]

        obj = await self.call("prose", build, ANSWER_CAP_S)
        shown = {e.handle: e for e in excerpts if e.version_id not in self.excluded}
        if obj is None:
            return rs.validate_prose(obj, shown, self.researcher.redactor.text)
        self.sim = self.line_sim()
        v = await asyncio.to_thread(rs.validate_prose, obj, shown, self.researcher.redactor.text, self.sim)
        self.flags["dropped_claims"] = v.dropped_claims
        self.flags["main_dropped"] = v.main_dropped
        self.flags["dropped_literal"] = v.drop_reasons.get("literal", 0)
        self.flags["attr_embed"] = self.sim.state if self.sim is not None else "off"
        self.flags["attr_embedded"] = self.sim.embedded if self.sim is not None else 0
        self.flags["attr_embed_ms"] = int(self.sim.seconds * 1000) if self.sim is not None else 0
        return v

    def line_sim(self) -> rs.LineSim | None:
        """D-165: the attribution's similarity over the server's own embedder (``deps.embedder``, the
        one the hybrid search embeds queries with; D-017: whatever model is configured), within
        ``rs.ATTR_EMBED_S`` and half the time left. None without an embedder or without time."""
        left = self.remaining()
        if self.deps is None or left < ATTR_MIN_LEFT_S:
            return None
        try:
            embedder = self.deps.embedder
        except Exception:  # noqa: BLE001 - no model files, a closed session: literal + word scoring
            return None
        return rs.LineSim(embedder.embed_queries, budget_s=min(rs.ATTR_EMBED_S, left / 2))


async def _search(
    c: AsyncConnection, ctx: AuthContext, slug: str, query: str, deps: ReadDeps
) -> list[dict[str, Any]]:
    """One internal ``memory.query`` (the caller's scope; it writes nothing): its hits."""
    packed, _librarian = await read_service.query_parts(
        c, ctx, {"project": slug, "query": query[:QUESTION_MAX], "token_budget": QUERY_BUDGET}, deps=deps
    )
    return list(packed.get("hits") or [])


async def _phase_timeouts(c: AsyncConnection) -> None:
    await c.execute(
        "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)",
        (f"{DB_PHASE_TIMEOUT_MS}ms", f"{DB_PHASE_TIMEOUT_MS // 2}ms"),
    )


# --------------------------------------------------------------------------- entry point
async def ask(
    conn: AsyncConnection,
    ctx: AuthContext,
    args: dict[str, Any],
    *,
    deps: ReadDeps,
    researcher: rs.Researcher | None,
    detach: Callable[..., Awaitable[bool]] | None = None,
    reconnect: Callable[[], AbstractAsyncContextManager[AsyncConnection]] | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    """``conn``: over the API the request transaction (``detach`` commits and releases it after the
    map phase; ``reconnect`` yields fresh pooled connections for the later DB phases); a direct
    caller passes an idle connection and no ``detach`` (each phase then commits its own short
    transaction on it). No transaction is open during any LLM call (D-062)."""
    t0 = time.perf_counter()
    settings = settings or get_settings()
    req = parse_request(args)
    if researcher is None or not researcher.enabled:
        raise ToolError("E_UNAVAILABLE", "memory.ask is disabled on this server", reason="disabled")
    why = researcher.unavailable()
    if why is not None:
        raise ToolError("E_UNAVAILABLE", f"memory.ask is unavailable: {why}", reason=why)
    loop = asyncio.get_running_loop()
    end = loop.time() + float(settings.research_timeout_s)
    try:
        slot = researcher.slot()
        slot.__enter__()
    except rs.ResearchUnavailable as exc:
        raise ToolError(
            "E_UNAVAILABLE", f"memory.ask is unavailable: {exc.reason}", reason=exc.reason
        ) from None
    try:
        # 1. the map, in the request transaction (a savepoint over the API)
        async with conn.transaction():
            slug = req.project or await default_project(conn, ctx)
            project = await _read_project(conn, ctx, slug)
            view = await mm.load_view(conn, ctx, project.project_id)
            entries = await mm.load_entries(conn, view)
            summaries = await mm.load_summaries(conn, project.project_id, {v.version_id for v in view})
            t_start = await rq.clock_now(conn)
            # the ORIGINAL question's search, here: no DB work may overlap a provider call (D-062,
            # review 80 #2), and this transaction is released before the first one
            first = [await _search(conn, ctx, project.slug, req.question, deps)]
        run = _Run(
            conn=conn,
            ctx=ctx,
            researcher=researcher,
            deps=deps,
            settings=settings,
            question=req.question,
            slug=project.slug,
            project_id=project.project_id,
            end=end,
            reconnect=reconnect,
            view={v.version_id: v for v in view},
            entries=entries,
            summaries=summaries,
        )
        if run.cite:  # D-156: the cite mode's own counts (meta.flags)
            run.flags.update({"dropped_sentences": 0, "uncited": 0})
            run.flags.update({f"dropped_{why}": 0 for why in rs.DROP_REASONS})
        if researcher.select:  # D-159
            run.flags.update({"selected": 0, "select_fallback": False})
        if run.prose:  # D-162: the prose mode's own counts (meta.flags); D-165: the embedding's
            run.flags.update(
                {"dropped_literal": 0, "attr_embed": "off", "attr_embedded": 0, "attr_embed_ms": 0}
            )
        readable = (
            {p for v in view for p in v.project_ids} | {project.project_id}
            if ctx.is_admin
            else {p for p in ctx.grants if ctx.has(p, Role.READ)}
        )
        run.caps = {
            "trigger_device_id": ctx.device_id,
            "token_generation": ctx.token_generation,  # a rotated bearer loses authority (D-062)
            "question": sorted(readable),  # the caller's readable projects: an upper bound only
            # review 79 T1: every gate also enforces the D-083 isolation for the asked project
            "isolation_home": project.project_id,
        }
        if detach is not None:
            released = await detach(hold_s=max(1.0, end - loop.time()))
            if not released:
                raise ToolError(
                    "E_UNAVAILABLE", "the request transaction could not be released", reason="no_detach"
                )
            if reconnect is None:
                raise ToolError("E_UNAVAILABLE", "no connection pool for memory.ask", reason="no_pool")
            run.released = True
        elif conn.info.transaction_status != TransactionStatus.IDLE:
            # D-062: a direct caller's connection inside an open transaction would hold its locks
            # (the device FOR SHARE) across every LLM call: refused, like the W2e synthesis
            raise ToolError("E_UNAVAILABLE", "memory.ask needs an idle connection", reason="no_detach")
        try:
            result = await _loop(run, t_start, first)
        except _AuthorityChanged:
            raise ToolError(
                "E_UNAVAILABLE",
                "access to an item changed during the request; ask again",
                reason="authority_changed",
            ) from None
        except rs.ResearchUnavailable as exc:
            raise ToolError(
                "E_UNAVAILABLE", f"memory.ask could not answer: {exc.reason}", reason=exc.reason
            ) from None
        finally:
            attempts, cost = researcher.tally(run.lineage)
    finally:
        slot.__exit__(None, None, None)
    result["meta"]["attempts"] = attempts
    result["meta"]["cost_usd"] = float(round(cost, 6))
    result["meta"]["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return _pack(deps.meter, result, req.token_budget)


async def _loop(run: _Run, t_start: Any, first: list[list[dict[str, Any]]]) -> dict[str, Any]:
    q = run.question
    m = run.render_map()
    # 2. plan (the original question was searched in the map phase)
    queries, sections = await run.plan(m)
    m = run.render_map() if run.excluded else m
    sections = [s for s in sections if m.drillable(s) and run._handle_in_view(s)][: rs.MAX_SECTIONS]
    run.queries = [q, *queries]
    # 3. retrieve
    excerpts, lists = await run.retrieve(queries, sections, first, set())
    # 4. answer (a per-question budget stop here leaves nothing to return but the flag)
    try:
        v = await run.answer(excerpts)
    except rs.ResearchUnavailable as exc:
        if exc.reason != "question_budget":
            raise
        v = rs.validate_answer(None, {})
    # 5. refine (only when the answer abstained, addendum 7), time permitting
    refined = False
    need = 3 if run.researcher.select else 2  # refine, (select,) answer
    if not v.answered and run.remaining() >= MIN_REFINE_S and run.calls + need <= run.researcher.max_calls:
        refined = True
        read = list(excerpts)

        def build_refine() -> tuple[str, list[int]]:
            cur = run.render_map() if run.excluded else m
            rd = [e for e in read if e.version_id not in run.excluded]
            return rs.refine_user(q, run.queries, rd, cur.text, run.redact), sorted(
                set(cur.gate_ids()) | {e.version_id for e in rd}
            )

        try:
            obj = await run.call("refine", build_refine, REFINE_CAP_S)
        except rs.ResearchUnavailable:
            obj = None
        if obj is not None:
            q2, s2 = rs.parse_plan(obj, q)
            q2 = [x for x in q2 if x not in run.queries]
            s2 = [s for s in s2 if m.drillable(s) and run._handle_in_view(s)][: rs.MAX_SECTIONS]
            seen = {e.handle for e in excerpts}
            if q2 or s2:
                new, _ = await run.retrieve(q2, s2, [], seen)
                new = [e for e in new if e.handle not in seen][:REFINE_MAX_NEW]
                run.queries += q2
                if new:
                    widened = excerpts + new
                    try:
                        v2 = await run.answer(widened)
                    except rs.ResearchUnavailable:
                        v2 = None
                    if v2 is not None and (v2.answered or not v.answered):
                        v, excerpts = v2, widened
    # 6. completeness + repair on an answer (not after a refinement: at most 4 sequential steps; not
    # in the cite or prose modes, D-156/D-162: their sentences are complete prose, checked one by one)
    if v.answered and not refined and run.claims_mode and run.remaining() >= MIN_CHECK_S:
        try:
            checked = await run.answer(excerpts, job="check", draft=v)
        except rs.ResearchUnavailable:
            checked = None
        if checked is not None:
            v = rs.merge_check(
                v, checked, {e.handle: e for e in excerpts if e.version_id not in run.excluded}
            )
    # 7. a claim still naming a subject that neither its quotes nor its sources state is dropped
    if v.answered and run.claims_mode:
        shown = {e.handle: e for e in excerpts if e.version_id not in run.excluded}
        v = rs.enforce_attribution(v, shown, run.researcher.redactor.text)
    # 8. re-check and assemble
    return await _finish(run, v, excerpts, t_start)


async def _finish(run: _Run, v: rs.Validated, excerpts: list[rs.Excerpt], t_start: Any) -> dict[str, Any]:
    shown = {e.handle: e for e in excerpts}
    handles = [*v.primary, *v.related, *(h for c in v.kept for h, _q in c.support)]
    vids = sorted(run.sent | {shown[h].version_id for h in handles if h in shown})
    returned = sorted({shown[h].version_id for h in handles if h in shown})
    async with run.db() as c:
        fresh = await run.fresh_ctx(c)  # E_AUTH / E_FORBIDDEN_PROJECT
        # review 80 (MED): the versions returned and their projects are read FOR SHARE (device first,
        # then projects, then versions), so a concurrent policy change, supersession or tombstone
        # either committed before these reads or waits until this answer is decided
        await c.execute(
            "SELECT 1 FROM projects WHERE project_id = %s OR project_id IN (SELECT unnest(project_ids)"
            " FROM memory_versions WHERE version_id = ANY(%s)) ORDER BY project_id FOR SHARE",
            (run.project_id, returned),
        )
        await c.execute(
            "SELECT 1 FROM memory_versions WHERE version_id = ANY(%s) ORDER BY version_id FOR SHARE",
            (returned,),
        )
        rows = await sq.version_access(c, vids, t_start)
        touched = {p for r in rows for p in r.project_ids} | {run.project_id}
        policies = await mm.project_policies(c, touched)
        excluded = await cross_project_excluded(c, touched)
    scopes = set(mm.view_scopes(fresh))

    def authz(r: sq.VersionAccess) -> bool:
        return (
            run.project_id in r.project_ids
            and r.device_scope in scopes
            and all(p in policies and policies[p] != "off" and fresh.has(p, Role.READ) for p in r.project_ids)
            and mm.isolation_ok(r.project_ids, run.project_id, excluded)  # D-083 (review 79 T1)
        )

    by_vid = {r.version_id: r for r in rows}
    lost = [x for x in run.sent if x not in by_vid or not authz(by_vid[x])]
    citable = {r.version_id for r in rows if authz(r) and r.current and r.status == "active"}
    abstain_reason = None
    if lost:
        # derived text (answer, claims, queries) may carry what the caller can no longer read
        v = rs.Validated(rs.INSUFFICIENT, v.model_status, "", [], [], [], "low")
        run.queries = [run.question]
        abstain_reason = "authority_changed"
    ok = {h: e for h, e in shown.items() if e.version_id in citable}
    if v.answered and run.cite:
        # D-156: each kept sentence is checked again against its cited excerpts that are still
        # citable (a sentence whose every cited source is gone is dropped; an uncited one against
        # every citable excerpt); the answer is the sentences left. D-159: only against the
        # excerpts its write was shown (the selected ones)
        pool = ok if v.written_over is None else {h: e for h, e in ok.items() if h in v.written_over}
        cited_claims = []
        cache: dict[str, Any] = {}
        for cl in v.kept:
            cites = [h for h in cl.cited if h in pool]
            if cl.cited and not cites:
                continue
            why, sup = rs.cite_check(cl.text, cites, pool, cache)
            if why is None and sup:
                cited_claims.append(rs.Claim(cl.text, sup, "kept", cites))
        before = len(v.kept)
        v = rs.assemble_cited(
            rs.ANSWERED,
            cited_claims,
            [h for h in v.related if h in ok],
            v.confidence,
            ok,
            run.researcher.redactor.text,
        )
        if not v.answered and before:
            abstain_reason = "sources_changed"
    elif v.answered and run.prose:
        # D-162: each kept sentence is checked again against the excerpts still citable (a hard
        # literal none of them states drops it) and attributed again over them (D-165: with the
        # answer's cached similarity; nothing is embedded again)
        sources = [h for h in v.sources if h in ok]
        prose_claims, _reasons = await asyncio.to_thread(
            rs.prose_check, [(cl.text, cl.line_end) for cl in v.kept], sources, ok, run.sim
        )
        before, settled = len(v.kept), v.confidence
        v = rs.assemble_prose(
            rs.ANSWERED,
            prose_claims,
            sources,
            [h for h in v.related if h in ok],
            settled,
            ok,
            run.researcher.redactor.text,
        )
        if v.answered and len(v.kept) == before:
            v.confidence = settled  # its drops already lowered it: nothing new was dropped
        if not v.answered and before:
            abstain_reason = "sources_changed"
    elif v.answered:
        # a quote whose version is no longer citable is removed; a claim its remaining quotes no
        # longer fully support is dropped; the answer is grounded again on what is left
        claims = []
        for cl in v.kept:
            sup = [(h, q) for h, q in cl.support if h in ok]
            if (
                sup
                and rs.literals_ok(cl.text, rs.support_hay(sup, ok))
                and rs.polarity_ok(cl.text, [q for _h, q in sup])
            ):
                claims.append(rs.Claim(cl.text, sup, "kept", [h for h in cl.cited if h in ok]))
        before = len(v.kept)
        v = rs.assemble(
            rs.ANSWERED,
            v.answer,
            claims,
            [h for h in v.related if h in ok],
            v.confidence,
            ok,
            run.researcher.redactor.text,
        )
        if not v.answered and before:
            abstain_reason = "sources_changed"
    related = [h for h in v.related if h in ok]
    answered = v.answered and bool(v.primary)
    if not answered and abstain_reason is None:
        abstain_reason = "budget" if run.flags["budget_stop"] else ("guard" if v.guard else "no_evidence")

    def path(h: str) -> str:
        item = run.view.get(shown[h].version_id)
        return item.path if item is not None else shown[h].title

    out: dict[str, Any] = {
        "project": run.slug,
        "answer": v.answer if answered else "",
        "abstained": not answered,
        "confidence": v.confidence if answered else "low",
        "claims": [cl.out() for cl in v.kept] if answered else [],
        "primary": [{"handle": h, "path": path(h), "quote": v.quote_of(h) or ""} for h in v.primary]
        if answered
        else [],
        "related": [{"handle": h, "path": path(h)} for h in (related if answered else related[:3])],
        "meta": {
            "queries": run.queries,
            "calls": run.calls,
            "steps": run.steps,
            "excerpts": len(excerpts),
            "map_tokens": run.map_tokens,
            "flags": run.flags,
            "answer_mode": run.researcher.answer_mode,
        },
    }
    if not answered:
        out["meta"]["abstain_reason"] = abstain_reason
    return out


def _pack(meter: Meter, out: dict[str, Any], budget: int) -> dict[str, Any]:
    """Fit ``token_budget``: the query list becomes a count, then related sources, claims and
    primary sources are dropped from the tail (at least one claim and one primary stay); the answer
    is never cut (``E_BUDGET_TOO_SMALL`` with ``min`` instead)."""
    used = meter.settle(out, budget)
    if used > budget and isinstance(out["meta"].get("queries"), list):
        out["meta"]["queries"] = len(out["meta"]["queries"])
        used = meter.settle(out, budget)
    while used > budget and out["related"]:
        out["related"].pop()
        used = meter.settle(out, budget)
    while used > budget and len(out["claims"]) > 1:
        out["claims"].pop()
        used = meter.settle(out, budget)
    while used > budget and len(out["primary"]) > 1:
        out["primary"].pop()
        used = meter.settle(out, budget)
    if used > budget:
        raise ToolError("E_BUDGET_TOO_SMALL", f"token_budget {budget} cannot hold the answer", min=used)
    return out


__all__ = [
    "CONTEXT",
    "DEFAULT_BUDGET",
    "MAX_DRILL",
    "TOOL",
    "AskRequest",
    "ask",
    "collapse",
    "default_project",
    "drill_order",
    "parse_request",
    "rrf",
    "rrf_items",
]
