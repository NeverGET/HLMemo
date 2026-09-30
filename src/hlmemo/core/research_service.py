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
kept sentence again over the excerpts still citable. D-165: the attribution is a configured
strategy (``HLM_RESEARCH_ATTRIBUTION``, ``research.attribute``): ``sources`` (default, V16: the model's
sources, literal + word scoring); ``wide`` (every shown excerpt, literals > words > the multilingual
similarity of the server's own embedder, the one the hybrid search embeds queries with: one
embedding of the kept sentences and the excerpt lines per answer, ``research.LineSim``, within
``research.ATTR_EMBED_S``); ``llm`` (ONE more call after an answered prose, JOB ``attribute``: the ids
that state each numbered kept sentence; a failed call, or a sentence given none, falls back to
``sources``). There is no polarity flag. D-170 ``HLM_RESEARCH_EXPAND``: after an answered prose, ONE
JOB ``expand`` (the kept sentences numbered, the same excerpts) adds up to 6 new sentences; they are
appended and pass the same literal check, then the attribution runs over all sentences. It is
skipped with less than ``EXPAND_MIN_S`` left (``expand_skipped``); a failure keeps the answer.
D-171 ``HLM_RESEARCH_WRITER_PROFILE``: the JOBs prose and expand run on that named profile (its
fallback: the research profile; ``research.Researcher.chain_for_job``), the others on the task's;
``meta.writer_profile`` names the profile that writes. D-172: a writer attempt ends after
``HLM_RESEARCH_WRITER_TIMEOUT_S`` and the task profile writes instead (the writer jobs' call cap
grows by that timeout so the fallback keeps its own; the question deadline still binds);
``meta.flags.writer_timeout`` / ``writer_used`` say what happened. R4 (Astra 90 N-1, R-6): every
answer carries ``meta.flags.writer_fallback`` (bool, False by default; an ``E_UNAVAILABLE`` after
the loop started carries it in ``details``): True when ANY writer JOB call of the question (the
first prose, its retries, the prose after a refine, the expand) moved past the configured writer
(``Researcher.writer_profile``), whether that fallback answered or failed (``_Run.note_writer``). It
is the PER-QUESTION fallback signal; ``ops status`` counts calls and ledger lineages.

D-184 (prose mode, the temporal layer; links are only READ): each excerpt the answer step is shown
carries a ``status`` when a live ``supersedes`` link (valid at the question's time) targets its item
(whole scope) or quotes its text (part scope, ``research.quote_overlaps``): ``superseded by vN
(path): «quote»`` (``research.status_label``); a current excerpt carries none. D-184 fix (a): every
part-scope link whose quote is in the excerpt's text renders its own line (deduped by the superseding
item, newest first, ≤ ``research.STATUS_MAX_LINES``; a whole-scope link keeps its single status); fix
(b): the quote's words match whole with punctuation stripped at their edges (``research.quote_tokens``).
A superseded excerpt whose superseders are not shown pulls the newest ones' best in-document chunks in
(≤ ``SUPERSEDER_EXTRA`` beyond the cap, replacing the lowest-ranked current excerpts when the excerpt
budget binds). A chunk excerpt carries a read-time ``context`` label (``research.context_label``).
D-188 (prose mode): the D-ids and repo paths the shown excerpts mention, whose row or item is not
shown, pull their row chunk / best chunk in (≤ ``XREF_EXTRA``, the same budget rule; ``xref_pulled``);
a long top item (> ``LONG_ITEM_CHUNKS`` chunks) shows its best chunk for the question instead of its
other hit chunks; an excerpt longer than ``research.EXCERPT_CHARS`` is clipped around its
best-matching part (``research.clip`` with the question's words). D-193 (5) "K4" (prose mode): a
retrieval drills the ``DOC_TOP`` top items' best chunks FIRST and the planner's sections after them
(``candidate_order``; the other modes keep the sections first). D-193 (5b)
``HLM_RESEARCH_RERANK=llm`` (prose mode, the PLAN retrieval only): between two DB phases (no
transaction is held across the call) ONE call of the task ``rerank`` sees the question and the top
``research.RERANK_CANDIDATES`` candidates of the K4 order (``_rerank_rows``) and returns the most
useful handles; the offered, in-view ones (≤ ``research.RERANK_KEEP``) are drilled first and the K4
order fills the cap. A timeout (``HLM_RESEARCH_RERANK_TIMEOUT_S``, a cut, not a breaker failure),
an error or an answer with no offered handle keeps the K4 order (``meta.flags.rerank``). It counts
as one more logical call (``steps`` shows ``rerank``) under the same privacy gate, spend guard and
per-question budget; the trace records it under ``rerank``.

At most ``research.MAX_CALLS_NO_SELECT`` (4) logical LLM calls, ``research.MAX_CALLS`` (6) with the
D-159 select (plan, select, write, refine, select, write), ``research.MAX_CALLS_ATTRIBUTE`` (5) with
the D-165 llm attribution (plan, prose, refine, prose, attribute), one more with the D-170 expand
(plan, prose, refine, prose, expand, attribute); provider requests (schema retries,
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
import copy
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
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
from hlmemo.core.research_trace import TraceRecorder, sha256
from hlmemo.core.write_models import SLUG_RE
from hlmemo.db import auth_queries
from hlmemo.db import librarian_queries as lq
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
    PriceExpired,
    PrivacyDenied,
    ProviderUnavailable,
    SchemaFail,
)
from hlmemo.librarian.tasks import research as rs

TOOL = "memory.ask"
#: D-209: the RESPONSE budget (answer + claims + sources in the envelope), not an LLM max_tokens or a
#: cost setting; raised from 3000 because a procedural answer with its claims overflowed it (D-202)
DEFAULT_BUDGET = 6000
QUESTION_MAX = 2000
QUERY_BUDGET = 2000  # token budget of each internal memory.query (about 15-25 hits)
MAX_DRILL = 12
#: addendum 7: planned queries run in parallel, at most this many at once per question
PARALLEL_QUERIES = 3
#: D-165: the top fused ITEMS whose best in-document chunk takes a drill slot (after the sections)
DOC_TOP = 4
#: D-184: superseders pulled into one retrieval's excerpts beyond MAX_DRILL, and the excerpt budget
#: (characters) past which a pulled superseder replaces the lowest-ranked current excerpts instead
SUPERSEDER_EXTRA = 2
EXCERPT_BUDGET_CHARS = MAX_DRILL * rs.EXCERPT_CHARS
#: D-188 (prose mode): the cross-references (D-ids, repo paths) of the shown excerpts pulled in
#: beyond the cap, the candidates considered for it, and an item long enough that its best chunk
#: for the question replaces its other hit chunks
XREF_EXTRA = 3
XREF_CANDIDATES = 8
LONG_ITEM_CHUNKS = 3
#: D-184 fix (a): the sort key of a superseder without a valid_from (oldest)
_EPOCH = datetime.min.replace(tzinfo=UTC)
_DID = re.compile(r"(?<![\w-])D-\d{3}(?!\d)")
_REPO_PATH = re.compile(r"(?<![\w/.-])((?:docs/[\w./-]*?\.md)|(?:deploy/[\w./-]*\w))(?![\w/])")
REFINE_MAX_NEW = 8
RRF_K = 60
#: per-call caps (seconds) inside the whole request's HLM_RESEARCH_TIMEOUT_S
PLAN_CAP_S = 10.0
ANSWER_CAP_S = 18.0
CHECK_CAP_S = 16.0
REFINE_CAP_S = 9.0
#: D-165: the JOB attribute's cap (llm attribution)
ATTRIBUTE_CAP_S = 10.0
#: D-170: the JOB expand's cap; it starts only with EXPAND_MIN_S left (for it and the attribution),
#: leaving EXPAND_ATTRIBUTE_RESERVE_S to an llm attribute call
EXPAND_CAP_S = 8.0
EXPAND_MIN_S = 6.0
EXPAND_ATTRIBUTE_RESERVE_S = 3.0
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
    return collapse_explain(handles, limit)[0]


def collapse_explain(handles: list[str], limit: int) -> tuple[list[str], list[tuple[str, str]]]:
    """``collapse`` and (D-189, the trace) every handle it left out with its reason: ``invalid``,
    ``dedupe`` (the same handle, or covered by a kept chunk's ±1 or a kept whole item) or ``cap``."""
    kept: list[str] = []
    dropped: list[tuple[str, str]] = []
    covered: set[tuple[int, int]] = set()
    whole: set[int] = set()
    for i, h in enumerate(handles):
        if len(kept) >= limit:
            dropped += [(x, "cap") for x in handles[i:]]
            break
        try:
            c = decode_clue(h)
        except InvalidClue:
            dropped.append((h, "invalid"))
            continue
        v, o = c.version_id, c.ordinal
        if v in whole or h in kept or (o is not None and (v, o) in covered):
            dropped.append((h, "dedupe"))
            continue
        kept.append(h)
        if o is None:
            whole.add(v)
        else:
            covered.update({(v, o - 1), (v, o), (v, o + 1)})
    return kept, dropped


def rrf_explain(lists: list[list[dict[str, Any]]], k: int = RRF_K) -> dict[str, Any]:
    """D-189 (the trace): the chunk fusion (``rrf``) and the item fusion (``rrf_items``) with each
    handle's / item's rank in every hit list and its fused score, in fused order."""
    chunks: dict[str, dict[str, Any]] = {}
    items: dict[int, dict[str, Any]] = {}
    for li, hits in enumerate(lists):
        voted: set[int] = set()
        for r, h in enumerate(hits, start=1):
            c = str(h.get("clue"))
            e = chunks.setdefault(c, {"handle": c, "score": 0.0, "ranks": [None] * len(lists)})
            e["score"] += 1.0 / (k + r)
            e["ranks"][li] = r
            try:
                vid = decode_clue(c).version_id
            except InvalidClue:
                continue
            if vid in voted:
                continue
            voted.add(vid)
            it = items.setdefault(vid, {"version_id": vid, "score": 0.0, "ranks": [None] * len(lists)})
            it["score"] += 1.0 / (k + r)
            it["ranks"][li] = r
    order = {c: i for i, c in enumerate(rrf(lists))}
    rep = {decode_clue(h).version_id: h for h in rrf_items(lists)}
    item_order = {vid: i for i, vid in enumerate(rep)}
    return {
        "chunks": sorted(chunks.values(), key=lambda e: order.get(e["handle"], 1 << 30)),
        "items": [
            {**it, "handle": rep.get(it["version_id"])}
            for it in sorted(items.values(), key=lambda e: item_order.get(e["version_id"], 1 << 30))
        ],
    }


def candidate_order(
    sections: list[str], best: list[str], fused: list[str], *, best_first: bool = False
) -> list[str]:
    """The ordered drill candidates of one retrieval: the plan's sections, then (D-165) the top items'
    best in-document chunks (``_doc_best``), then the chunk-fused hits. D-193 (5) "K4" (``best_first``,
    the prose mode): the ``DOC_TOP`` best chunks FIRST, the sections after them (the ranking ceiling
    test on the real memory: gold@1 15 -> 29 of 50, all facts in the top 4 28 -> 34)."""
    return [*best, *sections, *fused] if best_first else [*sections, *best, *fused]


def drill_order(
    sections: list[str], best: list[str], fused: list[str], skip: set[str], *, best_first: bool = False
) -> list[str]:
    """What one retrieval drills (≤ ``MAX_DRILL``, ±1-chunk collapse, never a handle in ``skip``): the
    ``candidate_order`` (the plan's sections first, then the best chunks TAKING slots, then the fused
    order; D-193 K4 with ``best_first``: the best chunks, then the sections, then the fused order)."""
    cands = candidate_order(sections, best, fused, best_first=best_first)
    return collapse([h for h in cands if h not in skip], MAX_DRILL)


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
    #: D-189: the request's trace recorder (None: off); it records copies only
    trace: TraceRecorder | None = None
    #: D-189: the last call's LlmResult (the trace's model/usage/cost)
    last_result: Any = None
    #: D-189: which retrieval phase runs ("plan" or "refine"), for the trace's query records
    phase: str = "plan"
    #: D-184: the question's time (the map phase's clock): supersedes links valid at it apply
    t_start: Any = None
    view: dict[int, mm.ViewItem] = field(default_factory=dict)
    entries: dict[int, list[mm.Entry]] = field(default_factory=dict)
    summaries: dict[str, tuple[list[int], str]] = field(default_factory=dict)
    excluded: set[int] = field(default_factory=set)
    sent: set[int] = field(default_factory=set)
    lineage: str = field(default_factory=lambda: str(uuid.uuid4()))
    #: D-165: the last prose answer's attribution (the strategy used, ``wide``'s similarity cache and
    #: ``llm``'s ids per kept sentence text): the re-check attributes the same way
    attribution: str = "sources"
    sim: rs.LineSim | None = None
    llm_cites: dict[str, list[str]] | None = None
    #: D-172: the profile that answered the last call (``LlmResult.profile``)
    last_profile: str | None = None
    #: D-165 (meta.excerpts_shown): the excerpt handles the LAST answer step (answer/check, write,
    #: prose) was shown, in prompt order
    excerpts_shown: list[str] = field(default_factory=list)
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
            # R4 (Astra 90 N-1): a writer JOB call of this question moved past the configured writer
            "writer_fallback": False,
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
        # D-193 (5b): the prose mode's rerank of the PLAN retrieval (the refine keeps the K4 order)
        rerank = self.prose and self.phase == "plan" and self.researcher.rerank
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
            tp: dict[str, Any] | None = None
            if self.trace is not None:  # D-189: this phase's fusion, drill and drops
                tp = {
                    "phase": self.phase,
                    "queries": list(queries),
                    "sections": list(sections),
                    "dropped": [],
                }
                tp.update(rrf_explain(lists))
                tp["dropped"] += [
                    {
                        "handle": h,
                        "stage": "fusion",
                        "reason": "excluded" if self._excluded(h) else "not_in_view",
                    }
                    for h in rrf(lists)
                    if not self._handle_in_view(h)
                ]
            fused = [h for h in rrf(lists) if self._handle_in_view(h)]
            # D-165 (D-163 what-if: gold-in-excerpts 20 -> 21 of 25, no losses): the top DOC_TOP
            # ITEMS (fused per item) drill their best in-document chunk right after the sections,
            # TAKING slots; the remaining slots keep the chunk-fused order
            items = [h for h in rrf_items(lists) if self._handle_in_view(h)]
            best = await self._doc_best(c, items, [self.question, *queries])
            focus = None
            if self.prose:  # D-188: a long top item shows its best chunk, not its other hit chunks
                before = fused
                fused = self._long_items_best_only(items, best, fused)
                if tp is not None:
                    tp["dropped"] += [
                        {"handle": h, "stage": "fusion", "reason": "long_item_best_only"}
                        for h in before
                        if h not in fused
                    ]
                focus = set().union(*(rs.content_words(t) for t in [self.question, *queries]))
            # D-193 (5) K4 (prose mode): the top items' best chunks before the planner's sections
            cands = candidate_order(sections, best, fused, best_first=self.prose)
            if tp is not None:
                tp["doc_best"] = [{"item_hit": hit, "best": b} for hit, b in zip(items, best, strict=False)]
            if not rerank:
                excerpts = await self._drill_phase(c, fresh, cands, skip, focus, queries, tp)
                return excerpts, lists
            offered = [h for h in dict.fromkeys(cands) if h not in skip][: rs.RERANK_CANDIDATES]
            rows = await self._rerank_rows(c, offered)
        # D-193 (5b): the rerank call runs between two DB phases (no transaction is held across it)
        head = await self.rerank(rows, tp)
        async with self.db() as c:
            fresh = await self.fresh_ctx(c)
            excerpts = await self._drill_phase(c, fresh, [*head, *cands], skip, focus, queries, tp)
        return excerpts, lists

    async def _drill_phase(
        self,
        c: AsyncConnection,
        fresh: AuthContext,
        cands: list[str],
        skip: set[str],
        focus: set[str] | None,
        queries: list[str],
        tp: dict[str, Any] | None,
    ) -> list[rs.Excerpt]:
        """The drill of one retrieval: ``cands`` (ordered) minus ``skip``, ±1-chunk collapse, ≤
        ``MAX_DRILL`` (``drill_order``); then (prose mode) the D-184/D-188 temporal layer."""
        wanted = collapse([h for h in cands if h not in skip], MAX_DRILL)
        if tp is not None:
            tp["drill_order"] = wanted
            tp["dropped"] += [
                {"handle": h, "stage": "drill_order", "reason": "already_read"} for h in cands if h in skip
            ]
            _kept, cut = collapse_explain([h for h in cands if h not in skip], MAX_DRILL)
            tp["dropped"] += [{"handle": h, "stage": "drill_order", "reason": why} for h, why in cut]
        excerpts = await self._drill(c, wanted, focus, tp)
        if self.prose:  # D-184: supersession status, the superseder pull-in; D-188: xrefs
            texts = [self.question, *queries]
            excerpts = await self._temporal(c, fresh, excerpts, texts, skip, focus, tp)
        if tp is not None:
            tp["excerpts"] = [
                {
                    "handle": e.handle,
                    "version_id": e.version_id,
                    "status": e.status,
                    "status_vid": e.status_vid,
                    "status_vids": list(e.superseders()),
                }
                for e in excerpts
            ]
            self.trace.append("retrieval", tp, key="phases")
        return excerpts

    async def _rerank_rows(self, c: AsyncConnection, handles: list[str]) -> list[list[str]]:
        """D-193 (5b): ``[handle, title, text]`` of each candidate, as the measured ceiling showed
        them: the item's title and the first ``rs.RERANK_TEXT_CHARS`` characters of the handle's chunk
        (chunk 0 for a whole-item handle)."""
        rows: list[list[str]] = []
        for h in handles:
            clue = decode_clue(h)
            item = self.view.get(clue.version_id)
            if item is None:
                continue
            ordinal = clue.ordinal or 0
            spans = await rq.chunk_spans(c, clue.version_id, ordinal, ordinal)
            rows.append([h, item.title, rs.rerank_text(spans[0].text if spans else "")])
        return rows

    async def rerank(self, rows: list[list[str]], tp: dict[str, Any] | None = None) -> list[str]:
        """D-193 (5b) ``HLM_RESEARCH_RERANK=llm``: ONE call of the task ``rerank`` over ``rows`` (the
        plan retrieval's top candidates); returns its handles that were offered and are still in the
        view (≤ ``rs.RERANK_KEEP``, drilled first; the K4 order fills the cap after them). A timeout,
        an error, a call cap or an answer with no offered handle returns ``[]`` (the K4 order);
        ``meta.flags.rerank`` says which. A budget stop here stays final for the question."""
        record: dict[str, Any] = {"phase": self.phase, "candidates": [r[0] for r in rows]}
        if len(rows) < 2:
            self.flags["rerank"] = record["fallback"] = "few_candidates"
            if self.trace is not None:
                self.trace.append("rerank", record)
            return []

        def admitted() -> list[list[str]]:
            return [r for r in rows if decode_clue(r[0]).version_id not in self.excluded]

        def build() -> tuple[str, list[int]]:
            keep = admitted()
            ids = [decode_clue(r[0]).version_id for r in keep]
            return rs.rerank_user(self.question, keep, self.redact), ids

        fallback: str | None = None
        obj: dict[str, Any] | None = None
        try:
            obj = await self.call(rs.RERANK_TASK, build, self.researcher.rerank_timeout_s)
            if obj is None:
                fallback = "call_cap"
        except rs.ResearchUnavailable as exc:
            fallback = exc.reason
        offered = [r[0] for r in admitted() if self._handle_in_view(r[0])]
        order = rs.parse_rerank(obj, offered) if obj is not None else []
        if obj is not None and not order:
            fallback = "invalid"
        self.flags["rerank"] = fallback or "ok"
        if self.trace is not None:  # the call's record (input, attempts, output) is the last one
            self.trace.enrich_last(**record, kept=order, fallback=fallback)
        if tp is not None:
            tp["rerank"] = {"kept": order, "fallback": fallback}
        return order

    def _excluded(self, handle: str) -> bool:
        try:
            return decode_clue(handle).version_id in self.excluded
        except InvalidClue:
            return False

    def _long_items_best_only(self, items: list[str], best: list[str], fused: list[str]) -> list[str]:
        """D-188: for a top item of more than ``LONG_ITEM_CHUNKS`` chunks whose best chunk for the
        question (``_doc_best`` over the WHOLE item) is not its top hit, the fused list keeps only
        that best chunk of it: its other hit chunks give their slots to other items."""
        only: dict[int, str] = {}
        for hit, b in zip(items, best, strict=False):
            vid = decode_clue(b).version_id
            item = self.view.get(vid)
            if item is not None and item.n_chunks > LONG_ITEM_CHUNKS and b != hit:
                only[vid] = b
        if not only:
            return fused
        out = []
        for h in fused:
            vid = decode_clue(h).version_id
            if vid not in only or h == only[vid]:
                out.append(h)
        return out

    async def _statuses(self, c: AsyncConnection, fresh: AuthContext, excerpts: list[rs.Excerpt]) -> None:
        """D-184: set ``status``/``status_vid`` of each excerpt from the live ``supersedes`` links
        (valid at the question's time, authz (a), ``lq.supersessions_of``) that target its item: a
        whole-scope link (the newest one wins), else the part-scope links whose quote overlaps ITS
        text (D-184 fix (a): one line each, deduped by the superseding item, newest superseder first,
        at most ``rs.STATUS_MAX_LINES``; ``status_vids`` names each line's superseder, ``status_vid``
        the first). The superseder must be a current item of the caller's view (it is named to the
        writer)."""
        if not excerpts:
            return
        vids = sorted({e.version_id for e in excerpts})
        cur = await c.execute(
            "SELECT version_id, logical_id FROM memory_versions WHERE version_id = ANY(%s)", (vids,)
        )
        lid_of = {int(v): int(lid) for v, lid in await cur.fetchall()}
        at = self.t_start if self.t_start is not None else await rq.clock_now(c)
        links = await lq.supersessions_of(
            c,
            sorted(set(lid_of.values())),
            pid=self.project_id,
            scopes=mm.view_scopes(fresh),
            valid_at=at,
            known_at=at,
        )
        if not links:
            return
        visible = [v for v in self.view if v not in self.excluded]
        cur = await c.execute(
            "SELECT logical_id, version_id FROM memory_versions"
            " WHERE logical_id = ANY(%s) AND version_id = ANY(%s)",
            (sorted({src for src, *_ in links}), visible),
        )
        current = {int(lid): int(v) for lid, v in await cur.fetchall()}
        redact = self.researcher.redactor.text
        for e in excerpts:
            lid = lid_of.get(e.version_id)
            mine = [x for x in links if x[1] == lid and x[0] in current and current[x[0]] != e.version_id]
            whole = next((x for x in mine if not x[2]), None)
            if whole is not None:  # a whole-scope link: its single status, as before
                hits = [whole]
            else:  # every part-scope link whose quote is in THIS excerpt, one per superseding item
                seen: dict[int, tuple[int, int, bool, str]] = {}
                for x in mine:
                    if x[2] and current[x[0]] not in seen and rs.quote_overlaps(x[3], e.text):
                        seen[current[x[0]]] = x
                newest = sorted(seen, key=lambda v: (self.view[v].valid_from or _EPOCH, v), reverse=True)
                hits = [seen[v] for v in newest[: rs.STATUS_MAX_LINES]]
            if not hits:
                continue
            vids = tuple(current[x[0]] for x in hits)
            e.status = "\n".join(
                redact(rs.status_label(f"v{v}", self.view[v].path, x[3], x[2]))
                for v, x in zip(vids, hits, strict=True)
            )
            e.status_vid, e.status_vids = vids[0], vids

    async def _temporal(
        self,
        c: AsyncConnection,
        fresh: AuthContext,
        excerpts: list[rs.Excerpt],
        texts: list[str],
        skip: set[str],
        focus: set[str] | None = None,
        tp: dict[str, Any] | None = None,
    ) -> list[rs.Excerpt]:
        """D-184: the statuses of ``excerpts``, then the superseder pull-in: for superseded excerpts
        whose superseding item is not shown (nor in ``skip``), that item's best in-document chunk
        for the question (``_doc_best``, as item fusion) joins the set, at most ``SUPERSEDER_EXTRA``
        beyond the cap. D-188: then the cross-reference pull-in (``_xrefs``, at most
        ``XREF_EXTRA``). The pulled excerpts follow the ranked ones (``_with_extra``: past
        ``EXCERPT_BUDGET_CHARS`` they replace the lowest-ranked current excerpts)."""
        await self._statuses(c, fresh, excerpts)
        shown = {e.version_id for e in excerpts}
        for h in skip:
            with contextlib.suppress(InvalidClue):
                shown.add(decode_clue(h).version_id)
        pulled: list[rs.Excerpt] = []
        # D-184 fix (a): each excerpt's superseders, newest first; the first SUPERSEDER_EXTRA not shown
        want = list(dict.fromkeys(v for e in excerpts for v in e.superseders() if v not in shown))
        if want:
            best = await self._doc_best(c, [f"v{vid}.0" for vid in want[:SUPERSEDER_EXTRA]], texts)
            pulled = await self._drill(c, [h for h in best if h not in skip], focus, tp)
            await self._statuses(c, fresh, pulled)
            if tp is not None:
                tp["superseders_pulled"] = [e.handle for e in pulled]
            self.flags["superseders_pulled"] = self.flags.get("superseders_pulled", 0) + len(pulled)
        seen = shown | {e.version_id for e in pulled}
        xrefs = await self._xrefs(c, [*excerpts, *pulled], texts, skip, seen, focus, tp)
        if xrefs:
            await self._statuses(c, fresh, xrefs)
            self.flags["xref_pulled"] = self.flags.get("xref_pulled", 0) + len(xrefs)
        return self._with_extra(excerpts, [*pulled, *xrefs], tp["dropped"] if tp is not None else None)

    @staticmethod
    def _with_extra(
        excerpts: list[rs.Excerpt], extra: list[rs.Excerpt], dropped: list[dict[str, Any]] | None = None
    ) -> list[rs.Excerpt]:
        """``excerpts`` and then the pulled ``extra``; past ``EXCERPT_BUDGET_CHARS`` of text, the
        lowest-ranked CURRENT excerpts (no status) of ``excerpts`` make room for them (D-189:
        noted in ``dropped``, the trace's)."""
        if not extra:
            return excerpts
        out = list(excerpts)
        size = sum(len(e.text) for e in out) + sum(len(e.text) for e in extra)
        while size > EXCERPT_BUDGET_CHARS:
            drop = next((i for i in range(len(out) - 1, -1, -1) if not out[i].status), None)
            if drop is None:
                break
            gone = out.pop(drop)
            size -= len(gone.text)
            if dropped is not None:
                dropped.append({"handle": gone.handle, "stage": "extra", "reason": "budget"})
        return out + extra

    async def _xrefs(
        self,
        c: AsyncConnection,
        excerpts: list[rs.Excerpt],
        texts: list[str],
        skip: set[str],
        shown: set[int],
        focus: set[str] | None,
        tp: dict[str, Any] | None = None,
    ) -> list[rs.Excerpt]:
        """D-188: the cross-reference pull-in. The D-ids (``D-026``) and repo paths (``docs/….md``,
        ``deploy/…``) the shown ``excerpts`` mention whose row or item is not shown, ranked by mention
        count plus the question words their target chunk shares (≤ ``XREF_CANDIDATES`` considered);
        the top ``XREF_EXTRA`` are drilled: a D-id's row chunk (``_did_row``), a path's item at its
        best chunk for the question (``_doc_best``)."""
        counts: dict[str, int] = {}
        for e in excerpts:
            for ref in [*_DID.findall(e.text), *_REPO_PATH.findall(e.text)]:
                counts[ref] = counts.get(ref, 0) + 1
        if not counts:
            return []
        visible = {vid: item for vid, item in self.view.items() if vid not in self.excluded}
        by_path: dict[str, list[int]] = {}
        for vid, item in visible.items():
            by_path.setdefault(item.path.split("#", 1)[0], []).append(vid)
        shown_paths = {visible[v].path.split("#", 1)[0] for v in shown if v in visible}

        def row_shown(did: str) -> bool:
            row = re.compile(rf"(?m)^\|?[ \t]*{did}[ \t]*\|")
            return any(row.search(e.text) or e.path.endswith(f"#{did}") for e in excerpts)

        refs = []
        for ref in sorted(counts, key=lambda r: -counts[r]):
            if ref.startswith("D-") and not row_shown(ref):
                refs.append(ref)
            elif not ref.startswith("D-") and ref in by_path and ref not in shown_paths:
                refs.append(ref)
        qwords = rs.content_words(self.question)
        cands: list[tuple[int, int, str]] = []  # (-score, order, handle)
        for i, ref in enumerate(refs[:XREF_CANDIDATES]):
            if ref.startswith("D-"):
                found = await self._did_row(c, ref, sorted(visible))
                if found is None or found[0] in shown:
                    continue
                handle, text = f"v{found[0]}.{found[1]}", found[2]
            else:
                vid = next((v for v in by_path[ref] if v not in shown), None)
                if vid is None:
                    continue
                handle = (await self._doc_best(c, [f"v{vid}.0"], texts))[0]
                clue = decode_clue(handle)
                spans = await rq.chunk_spans(c, vid, clue.ordinal or 0, clue.ordinal or 0)
                text = spans[0].text if spans else ""
            if handle not in skip:
                overlap = len(qwords & rs.content_words(text))
                cands.append((-(counts[ref] + overlap), i, handle))
                if tp is not None:
                    tp.setdefault("xrefs", []).append(
                        {"ref": ref, "mentions": counts[ref], "question_overlap": overlap, "handle": handle}
                    )
        chosen = list(dict.fromkeys(h for _s, _i, h in sorted(cands)))[:XREF_EXTRA]
        if tp is not None:
            tp["xrefs_found"] = counts
            tp["xrefs_pulled"] = chosen
        return await self._drill(c, chosen, focus, tp) if chosen else []

    async def _did_row(self, c: AsyncConnection, did: str, vids: list[int]) -> tuple[int, int, str] | None:
        """D-188: ``(version_id, ordinal, text)`` of the chunk of the caller's view holding the row
        START of decision ``did`` (``D-026 | …`` at a line start, a leading pipe allowed); an item
        whose path names DECISIONS first."""
        cur = await c.execute(
            "SELECT version_id, ordinal, text FROM chunks WHERE version_id = ANY(%s) AND text ~ %s"
            " ORDER BY version_id, ordinal LIMIT 20",
            (vids, rf"(^|\n)\|?[ \t]*{did}[ \t]*\|"),
        )
        rows = [(int(v), int(o), str(t)) for v, o, t in await cur.fetchall()]
        rows.sort(key=lambda r: "DECISIONS" not in (self.view[r[0]].path if r[0] in self.view else ""))
        return rows[0] if rows else None

    async def _search(self, c: AsyncConnection, fresh: AuthContext, query: str) -> list[dict[str, Any]]:
        if self.trace is None:
            return await _search(c, fresh, self.slug, query, self.deps)
        explain: dict[str, Any] = {}
        hits = await _search(c, fresh, self.slug, query, self.deps, explain)
        self.trace_query(query, self.phase, hits, explain)
        return hits

    def trace_query(
        self, query: str, phase: str, hits: list[dict[str, Any]], explain: dict[str, Any]
    ) -> None:
        """D-189: one query's ordered hit list, with its component ranks and view membership."""
        if self.trace is None:
            return
        comp = {h.get("clue"): h for h in explain.get("head", [])}
        rows = []
        for rank, h in enumerate(hits, start=1):
            clue = str(h.get("clue"))
            try:
                vid: int | None = decode_clue(clue).version_id
            except InvalidClue:
                vid = None
            item = self.view.get(vid) if vid is not None else None
            c = comp.get(clue, {})
            rows.append(
                {
                    "rank": rank,
                    "handle": clue,
                    "version_id": vid,
                    "title": h.get("title"),
                    "path": item.path if item is not None else None,
                    "in_view": self._handle_in_view(clue),
                    "score": h.get("score"),
                    **{k: c.get(k) for k in ("lexical_rank", "trigram_rank", "vector_rank", "title_rank")},
                }
            )
        self.trace.append(
            "retrieval",
            {
                "query": query,
                "phase": phase,
                "hits": rows,
                **{
                    k: explain.get(k)
                    for k in ("candidates", "ordered", "hidden_superseded", "partial_superseded")
                },
            },
            key="queries",
        )

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

    async def _drill(
        self,
        c: AsyncConnection,
        handles: list[str],
        focus: set[str] | None = None,
        tp: dict[str, Any] | None = None,
    ) -> list[rs.Excerpt]:
        """The internal drill: what ``memory.drilldown`` returns for each handle (a chunk with its
        ±1 neighbours, or a whole item), under the caller's scope, WITHOUT its access event. D-188:
        with ``focus`` (the question's words, prose mode) a text longer than an excerpt is clipped to
        a window centred on its best-matching part instead of its head."""
        now = await rq.clock_now(c)
        scopes = mm.view_scopes(self.ctx)
        redact = self.researcher.redactor.text
        out: list[rs.Excerpt] = []
        for h in handles:
            clue = decode_clue(h)
            item = self.view.get(clue.version_id)
            if item is None or clue.version_id in self.excluded:
                if tp is not None:
                    why = "not_in_view" if item is None else "excluded"
                    tp["dropped"].append({"handle": h, "stage": "drill", "reason": why})
                continue
            v = await rq.version_live(c, clue.version_id, self.project_id, scopes, now, now, ["active"])
            if v is None:
                if tp is not None:
                    tp["dropped"].append({"handle": h, "stage": "drill", "reason": "not_live"})
                continue
            context = ""
            if clue.ordinal is not None:
                spans = await rq.chunk_spans(c, v.version_id, clue.ordinal - 1, clue.ordinal + 1)
                if not any(s.ordinal == clue.ordinal for s in spans):
                    if tp is not None:
                        tp["dropped"].append({"handle": h, "stage": "drill", "reason": "no_chunk"})
                    continue
                text = v.body[spans[0].char_start : spans[-1].char_end]
                if self.prose:  # D-184: where the excerpt sits, from the stored body (read time)
                    context = redact(rs.context_label(item.path, v.body, spans[0].char_start))
            else:
                text = v.body
            out.append(
                rs.Excerpt(
                    h,
                    v.version_id,
                    v.title,
                    item.path,
                    v.valid_from.date().isoformat(),
                    redact(rs.clip(text, focus=focus)),
                    context=context,
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
        """One logical LLM call (``_call``); D-189: with a trace, recorded with every attempt. R4
        (Astra 90 N-1): a writer JOB's call, answered or failed, updates ``flags.writer_fallback``."""
        if job not in rs.WRITER_JOBS:
            return await self._traced(job, build, cap_s)
        since = len(self.researcher.attempts(self.lineage))
        before = self.last_result
        try:
            return await self._traced(job, build, cap_s)
        finally:
            self.note_writer(since, None if self.last_result is before else self.last_result)

    def note_writer(self, since: int, res: Any) -> None:
        """R4 (Astra 90 N-1, R-6): ``flags.writer_fallback`` becomes True (for the whole question)
        when the writer JOB call that made the attempts after the first ``since`` of the lineage
        moved past the configured writer (``Researcher.writer_profile``): it was answered by another
        profile or gave the writer up (``res``: its ``LlmResult``, None when it failed or was not
        made), or any of its attempts (``Researcher.attempts``: ok, schema_fail, http_error,
        timeout, breaker_open) was another profile's, whether that fallback answered or failed."""
        configured = self.researcher.writer_profile
        other = any(p != configured for p, _o in self.researcher.attempts(self.lineage)[since:])
        if other or (res is not None and (res.profile != configured or getattr(res, "fallbacks", None))):
            self.flags["writer_fallback"] = True

    async def _traced(
        self, job: str, build: Callable[[], tuple[str, list[int]]], cap_s: float
    ) -> dict[str, Any] | None:
        """``_call``; D-189: with a trace, recorded with every attempt."""
        if self.trace is None:
            return await self._call(job, build, cap_s)
        last: dict[str, Any] = {}

        def traced() -> tuple[str, list[int]]:
            user, ids = build()
            last.update(user=user, gate_ids=list(ids))
            return user, ids

        rows0 = len(self.researcher.attempt_rows(self.lineage))
        events0 = len(self.trace.events)
        record: dict[str, Any] = {"job": job, "cap_s": cap_s}
        try:
            out = await self._call(job, traced, cap_s)
            if out is None and not last:
                record["skipped"] = "call_cap"
            else:
                res = self.last_result
                record.update(
                    profile=res.profile,
                    model=res.model_id,
                    latency_ms=res.latency_ms,
                    cost_usd=res.cost_usd,
                    usage=res.usage,
                    output=out,
                )
            return out
        except BaseException as exc:
            record["error"] = f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
            raise
        finally:
            with contextlib.suppress(Exception):
                rows = self.researcher.attempt_rows(self.lineage)[rows0:]
                record.update(
                    system_sha256=sha256(self.researcher.job_spec(job).system),
                    **last,
                    attempts=[{"attempt": i + 1, **r} for i, r in enumerate(rows)],
                    outputs=self.trace.events[events0:],
                )
                self.trace.call(record)

    async def _call(
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
                        observe=self.trace.observe if self.trace is not None else None,
                        attempt_affordable=self.attempt_affordable,
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
            except PriceExpired as exc:  # R4 (R-5): every profile's prices expired (fail closed)
                raise rs.ResearchUnavailable("price_expired") from exc
            except (ProviderUnavailable, httpx.HTTPError, OSError) as exc:
                raise rs.ResearchUnavailable("unavailable") from exc
            except (LlmDisabled, LlmConfigError) as exc:
                raise rs.ResearchUnavailable("disabled") from exc
            except CassetteMiss as exc:
                raise rs.ResearchUnavailable("error") from exc
            self.calls += 1
            self.steps.append(job)
            self.sent |= set(ids)
            self.last_profile = res.profile
            self.last_result = res
            return res.output
        raise rs.ResearchUnavailable("privacy")

    def admitted(self, excerpts: list[rs.Excerpt]) -> list[rs.Excerpt]:
        """The excerpts a prompt may carry (not excluded); D-184: a status naming an excluded
        superseder is left out (its title, path and quote must not reach the provider)."""
        out = []
        for e in excerpts:
            if e.version_id in self.excluded:
                continue
            named = e.superseders()
            if any(v in self.excluded for v in named):  # D-184 fix (a): only the lines naming them
                lines = e.status.split("\n") if len(named) > 1 else [e.status]
                keep = [(v, ln) for v, ln in zip(named, lines, strict=False) if v not in self.excluded]
                e = replace(
                    e,
                    status="\n".join(ln for _v, ln in keep),
                    status_vid=keep[0][0] if keep else None,
                    status_vids=tuple(v for v, _ln in keep),
                )
            out.append(e)
        return out

    @staticmethod
    def gate_ids(excerpts: list[rs.Excerpt]) -> list[int]:
        """The version ids whose text a prompt of ``excerpts`` carries: theirs and (D-184) the
        superseders their statuses name (a title, a path and a link quote)."""
        return list(
            dict.fromkeys([e.version_id for e in excerpts] + [v for e in excerpts for v in e.superseders()])
        )

    def cap_for(self, job: str, cap_s: float) -> float:
        """D-172: a writer job's call cap grows by the writer's attempt timeout, so the task profile
        that writes after a timed-out writer attempt keeps its own ``cap_s`` (the deadline binds)."""
        chain = self.researcher.chain_for_job(job)
        extra = chain[0].attempt_timeout_s if chain else None
        return cap_s + extra if extra else cap_s

    def count_fallbacks(self) -> None:
        """R4 (R-9, R-6): every profile the last call gave up (``LlmResult.fallbacks``) counts in
        ``meta.flags.writer_fallbacks`` and ``writer_fallback_reasons`` ("profile:reason"); the keys
        appear only once a fallback happened."""
        res = self.last_result
        for profile, why in getattr(res, "fallbacks", None) or []:
            self.flags["writer_fallbacks"] = int(self.flags.get("writer_fallbacks", 0)) + 1
            self.flags.setdefault("writer_fallback_reasons", []).append(f"{profile}:{why}")

    def writer_timed_out(self, since: int) -> bool:
        """D-172: a writer-profile attempt of this question timed out after its first ``since``
        attempts."""
        chain = self.researcher.chain_for_job("prose")
        if not chain:
            return False
        name = chain[0].name
        return any(p == name and o == "timeout" for p, o in self.researcher.attempts(self.lineage)[since:])

    async def attempt_affordable(self, _profile: Any, worst_usd: Decimal, worst_tokens: int) -> bool:
        """R4 (R-9): would one more attempt (its worst case) still fit the per-question USD and token
        budget? No side effect: a writer's schema retry that does not fit is never reserved and the
        writer's fallback answers instead (``attempt_guard`` still checks that one)."""
        usd, tokens = self.researcher.spent(self.lineage)
        return usd + worst_usd <= Decimal(
            str(self.settings.research_max_usd)
        ) and tokens + worst_tokens <= int(self.settings.research_max_tokens)

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
            if self.trace is not None:  # D-189: the map exactly as the planner gets it
                self.trace.set("map", {"text": self.redact(cur.text), "tokens": cur.tokens})
            return rs.plan_user(self.question, CONTEXT, cur.text, self.redact), cur.gate_ids()

        try:
            obj = await self.call("plan", build, PLAN_CAP_S)
        except rs.ResearchUnavailable:
            return [], []  # the loop still runs on the question alone
        queries, sections = rs.parse_plan(obj, self.question)
        if self.trace is not None:
            self.trace.enrich_last(queries=queries, sections=sections)
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
            if self.trace is not None:
                self.trace.set("excerpts", [e.shown() for e in ex])
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
            self.excerpts_shown = list(shown)
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
            if self.trace is not None:
                self.trace.set("excerpts", [e.shown() for e in ex])
            return rs.write_user(self.question, ex, redact=self.redact), [e.version_id for e in ex]

        obj = await self.call("write", build, ANSWER_CAP_S)
        shown = {e.handle: e for e in chosen if e.version_id not in self.excluded}
        v = rs.validate_cited(obj, shown, self.researcher.redactor.text)
        if obj is not None:
            self.excerpts_shown = list(shown)
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
            ex = self.admitted(excerpts)
            if self.trace is not None:  # D-189: exactly as the writer sees them
                self.trace.set("excerpts", [e.shown(temporal=True) for e in ex])
            return rs.prose_user(self.question, ex, redact=self.redact), self.gate_ids(ex)

        since = len(self.researcher.attempts(self.lineage))
        try:
            obj = await self.call("prose", build, self.cap_for("prose", ANSWER_CAP_S))
        finally:
            self.flags["writer_timeout"] = self.writer_timed_out(since)
        shown = {e.handle: e for e in excerpts if e.version_id not in self.excluded}
        if obj is None:
            return rs.validate_prose(obj, shown, self.researcher.redactor.text)
        self.flags["writer_used"] = self.last_profile
        self.count_fallbacks()
        if self.trace is not None:
            self.trace.enrich_last(
                parsed={k: obj.get(k) for k in ("status", "answer", "sources", "related", "confidence")}
            )
        self.excerpts_shown = list(shown)
        self.flags["superseded_shown"] = sum(1 for e in self.admitted(excerpts) if e.status)
        if self.researcher.expand:
            since = len(self.researcher.attempts(self.lineage))
            added = await self.expand_call(obj, shown)
            self.flags["writer_timeout"] = self.flags["writer_timeout"] or self.writer_timed_out(since)
        else:
            added = None
        strategy, self.sim, self.llm_cites = self.researcher.attribution, None, None
        if strategy == "llm":
            self.llm_cites = await self.attribute_call(obj, shown, added)
            self.flags["attr_fallback"] = self.llm_cites is None
            if self.llm_cites is None:
                strategy = "sources"
        elif strategy == "wide":
            self.sim = self.line_sim()
        self.attribution = strategy
        explain: list[dict[str, Any]] | None = [] if self.trace is not None else None
        v = await asyncio.to_thread(
            rs.validate_prose,
            obj,
            shown,
            self.researcher.redactor.text,
            strategy,
            embed=self.sim,
            llm_cites=self.llm_cites,
            added=added,
            explain=explain,
            question=self.question,  # D-190 (b): the question's own literals are not fabrications
        )
        if self.trace is not None:  # D-189: the validation and attribution verdicts
            self.trace.update(
                "validation",
                units=explain,
                drop_reasons=v.drop_reasons,
                dropped_claims=v.dropped_claims,
                expand_added=v.expand_added,
                expand_dropped=v.expand_dropped,
            )
            self.trace.update(
                "attribution",
                configured=self.researcher.attribution,
                strategy=strategy,
                fallback=self.flags.get("attr_fallback", False),
                llm_cites=self.llm_cites,
                embedding=(
                    {"state": self.sim.state, "embedded": self.sim.embedded, "seconds": self.sim.seconds}
                    if self.sim is not None
                    else None
                ),
                support=[{"text": c.text, "support": c.support} for c in v.kept],
            )
        if self.researcher.expand:
            self.flags["expand_added"] = v.expand_added
            self.flags["expand_dropped"] = v.expand_dropped
        self.flags["dropped_claims"] = v.dropped_claims
        self.flags["main_dropped"] = v.main_dropped
        self.flags["dropped_literal"] = v.drop_reasons.get("literal", 0)
        self.flags["attribution"] = strategy
        if self.researcher.attribution == "wide":
            self.flags["attr_embed"] = self.sim.state if self.sim is not None else "off"
            self.flags["attr_embedded"] = self.sim.embedded if self.sim is not None else 0
            self.flags["attr_embed_ms"] = int(self.sim.seconds * 1000) if self.sim is not None else 0
        if self.llm_cites is not None:
            self.flags["attr_llm_cited"] = sum(1 for ids in self.llm_cites.values() if ids)
        return v

    async def expand_call(self, obj: dict[str, Any], shown: dict[str, rs.Excerpt]) -> list[str] | None:
        """D-170: the JOB ``expand`` over the kept sentences of the prose output (numbered) and the
        excerpts the prose job saw, through the same gate, budget and attempt guards → the new
        sentences to append (``rs.parse_expand``; ``[]``: complete). None keeps the answer as is:
        nothing kept, less than ``EXPAND_MIN_S`` left or the call cap reached (``expand_skipped``),
        or a failed call (``expand_failed``)."""
        self.flags["expand_skipped"] = self.flags["expand_failed"] = False
        sentences = rs.prose_kept(obj, shown, question=self.question)
        if not sentences:
            return None
        left = self.remaining()
        if left < EXPAND_MIN_S:
            self.flags["expand_skipped"] = True
            return None
        reserve = EXPAND_ATTRIBUTE_RESERVE_S if self.researcher.attribution == "llm" else 0.0
        excerpts = list(shown.values())

        def build() -> tuple[str, list[int]]:
            ex = self.admitted(excerpts)
            return rs.expand_user(self.question, sentences, ex, self.redact), self.gate_ids(ex)

        try:
            out = await self.call("expand", build, min(self.cap_for("expand", EXPAND_CAP_S), left - reserve))
        except rs.ResearchUnavailable:
            self.flags["expand_failed"] = True
            return None
        if out is None:
            self.flags["expand_skipped"] = True
            return None
        self.count_fallbacks()  # R4 (R-9): the expand is a writer JOB too
        added = rs.parse_expand(out, sentences)
        if self.trace is not None:
            self.trace.enrich_last(numbered=sentences, added=added)
        return added

    async def attribute_call(
        self, obj: dict[str, Any], shown: dict[str, rs.Excerpt], added: list[str] | None = None
    ) -> dict[str, list[str]] | None:
        """D-165 ``llm``: the JOB ``attribute`` over the kept sentences of the prose output (D-170: and
        its added ones) and the excerpts the prose job saw (through the same gate, budget and attempt
        guards as every call) → the excerpt ids per kept sentence text; None (the caller falls back
        to ``sources``) when nothing was kept, or the call failed, timed out or was not made (call
        cap, question budget)."""
        sentences = rs.prose_kept(obj, shown, added, question=self.question)
        if not sentences:
            return None
        excerpts = list(shown.values())

        def build() -> tuple[str, list[int]]:
            ex = [e for e in excerpts if e.version_id not in self.excluded]
            return rs.attribute_user(self.question, sentences, ex, self.redact), [e.version_id for e in ex]

        try:
            out = await self.call("attribute", build, ATTRIBUTE_CAP_S)
        except rs.ResearchUnavailable:
            return None
        if out is None:
            return None
        admitted = [e.handle for e in excerpts if e.version_id not in self.excluded]
        cites = rs.parse_attribute(out, sentences, admitted)
        if self.trace is not None:
            self.trace.enrich_last(numbered=sentences, cites=cites)
        return cites

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
    c: AsyncConnection,
    ctx: AuthContext,
    slug: str,
    query: str,
    deps: ReadDeps,
    explain: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """One internal ``memory.query`` (the caller's scope; it writes nothing): its hits. ``explain``
    (D-189, the trace): its component ranks (``read_service.query_parts``), read-only."""
    packed, _librarian = await read_service.query_parts(
        c,
        ctx,
        {"project": slug, "query": query[:QUESTION_MAX], "token_budget": QUERY_BUDGET},
        deps=deps,
        explain=explain,
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
    transaction on it). No transaction is open during any LLM call (D-062). D-189: with
    ``HLM_RESEARCH_TRACE_DIR`` the request is traced (``research_trace``): the same answer, plus one
    trace file; a trace failure never fails the request."""
    settings = settings or get_settings()
    trace = TraceRecorder.maybe(settings, str(args.get("question") or ""))
    kw = {"deps": deps, "researcher": researcher, "detach": detach, "reconnect": reconnect}
    if trace is None:
        return await _ask(conn, ctx, args, settings=settings, trace=None, **kw)
    try:
        out = await _ask(conn, ctx, args, settings=settings, trace=trace, **kw)
        trace.set("response", out)
        return out
    except BaseException as exc:
        trace.update("response", error=f"{type(exc).__name__}: {exc}", details=getattr(exc, "details", None))
        raise
    finally:
        await asyncio.to_thread(trace.write)


async def _ask(
    conn: AsyncConnection,
    ctx: AuthContext,
    args: dict[str, Any],
    *,
    deps: ReadDeps,
    researcher: rs.Researcher | None,
    detach: Callable[..., Awaitable[bool]] | None,
    reconnect: Callable[[], AbstractAsyncContextManager[AsyncConnection]] | None,
    settings: Any,
    trace: TraceRecorder | None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
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
            explain: dict[str, Any] | None = {} if trace is not None else None
            first = [await _search(conn, ctx, project.slug, req.question, deps, explain)]
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
            t_start=t_start,
            trace=trace,
        )
        if trace is not None:  # D-189: the request, and the original question's hit list
            trace.set(
                "request",
                {
                    "question": req.question,
                    "project": project.slug,
                    "token_budget": req.token_budget,
                    "answer_mode": researcher.answer_mode,
                    "attribution": researcher.attribution,
                    "writer_profile": researcher.writer_profile,
                    "expand": researcher.expand,
                    "select": researcher.select,
                    "lineage": run.lineage,
                    "started_at": trace.started.isoformat(),
                    "db_time": t_start,
                    "view_items": len(view),
                },
            )
            run.trace_query(req.question, "question", first[0], explain or {})
        if run.cite:  # D-156: the cite mode's own counts (meta.flags)
            run.flags.update({"dropped_sentences": 0, "uncited": 0})
            run.flags.update({f"dropped_{why}": 0 for why in rs.DROP_REASONS})
        if researcher.select:  # D-159
            run.flags.update({"selected": 0, "select_fallback": False})
        if run.prose:  # D-162: the prose mode's own counts (meta.flags); D-165: its attribution's
            run.flags.update({"dropped_literal": 0, "attribution": researcher.attribution})
            if researcher.attribution == "wide":
                run.flags.update({"attr_embed": "off", "attr_embedded": 0, "attr_embed_ms": 0})
            if researcher.attribution == "llm":
                run.flags.update({"attr_fallback": False, "attr_llm_cited": 0})
            # D-172: whether a writer attempt timed out, and the profile that wrote the answer
            run.flags.update({"writer_timeout": False, "writer_used": None})
            # D-184: superseded excerpts the writer was shown, superseders pulled in
            run.flags.update({"superseded_shown": 0, "superseders_pulled": 0})
            run.flags["xref_pulled"] = 0  # D-188: cross-referenced rows/items pulled in
            if researcher.expand:  # D-170
                run.flags.update(
                    {"expand_added": 0, "expand_dropped": 0, "expand_skipped": False, "expand_failed": False}
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
                writer_fallback=bool(run.flags["writer_fallback"]),
            ) from None
        except rs.ResearchUnavailable as exc:
            raise ToolError(
                "E_UNAVAILABLE",
                f"memory.ask could not answer: {exc.reason}",
                reason=exc.reason,
                writer_fallback=bool(run.flags["writer_fallback"]),  # R4 (N-1): per question
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
            if run.trace is not None:
                run.trace.enrich_last(queries=q2, sections=s2)
            seen = {e.handle for e in excerpts}
            if q2 or s2:
                run.phase = "refine"
                new, _ = await run.retrieve(q2, s2, [], seen)
                fresh_new = [e for e in new if e.handle not in seen]
                new = fresh_new[:REFINE_MAX_NEW]
                if run.trace is not None and len(fresh_new) > len(new):
                    run.trace.update("retrieval", refine_cut=[e.handle for e in fresh_new[REFINE_MAX_NEW:]])
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
    readable = {r.version_id for r in rows if authz(r)}
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
        # literal none of them states drops it) and attributed again over them (D-165: the same
        # strategy; wide reads the answer's cached similarity, llm its ids still citable)
        sources = [h for h in v.sources if h in ok]
        recheck: list[dict[str, Any]] | None = [] if run.trace is not None else None
        prose_claims, _reasons = await asyncio.to_thread(
            rs.prose_check,
            [(cl.text, cl.line_end) for cl in v.kept],
            sources,
            ok,
            run.attribution,
            embed=run.sim,
            llm_cites=run.llm_cites,
            explain=recheck,
            question=run.question,
        )
        if run.trace is not None:
            run.trace.update("validation", recheck=recheck, recheck_excerpts=sorted(ok))
        before, settled = len(v.kept), v.confidence
        v = rs.assemble_prose(
            rs.ANSWERED,
            prose_claims,
            sources,
            [h for h in v.related if h in ok],
            settled,
            ok,
            run.researcher.redactor.text,
            truncated=v.truncated,  # D-209: carried over the re-check
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
            # D-171: the profile that writes the answer (HLM_RESEARCH_WRITER_PROFILE, else the task's)
            "writer_profile": run.researcher.writer_profile,
            # D-165: the excerpts the answer step saw (handles only; one the caller can no longer
            # read is left out)
            "excerpts_shown": [
                h for h in run.excerpts_shown if h in shown and shown[h].version_id in readable
            ],
        },
    }
    run.flags["truncated"] = bool(answered and v.truncated)  # D-209 (_pack may also set it)
    # D-211: the horizon of the project's memory (the newest recorded_at of the view the question was
    # answered over: loaded once per request, no extra query); a stale one ends the answer with a line
    as_of = memory_as_of(run.view.values())
    out["meta"]["memory_as_of"] = as_of.isoformat() if as_of is not None else None
    if answered:
        line = freshness_line(as_of, t_start, f"{run.question}\n{out['answer']}")
        if line:
            out["answer"] = f"{out['answer']}\n{line}"
    if not answered:
        out["meta"]["abstain_reason"] = abstain_reason
    return out


#: D-211: the memory counts as stale for an answer when its newest record is older than this
FRESH_WITHIN = timedelta(hours=24)
#: letters that mark Turkish text (ö, ü, ç are shared with German and French)
_TR_LETTERS = frozenset("ığşİĞŞ")


def memory_as_of(items: Iterable[Any]) -> datetime | None:
    """D-211: the newest ``recorded_at`` among the project's current items (None: none or unknown)."""
    times = [it.recorded_at for it in items if getattr(it, "recorded_at", None) is not None]
    return max(times) if times else None


def _is_turkish(text: str) -> bool:
    """At least 2% of the letters are Turkish-only ones (a quoted Turkish term in English stays English)."""
    letters = sum(ch.isalpha() for ch in text)
    return letters > 0 and sum(ch in _TR_LETTERS for ch in text) * 50 >= letters


def freshness_line(as_of: datetime | None, now: datetime | None, text: str) -> str:
    """D-211: ``(Memory records for this project end on <YYYY-MM-DD>.)`` (Turkish: ``(Bu projenin
    bellek kayıtları <YYYY-MM-DD> tarihinde bitiyor.)``, chosen by ``text``'s language, else English)
    when ``as_of`` is older than ``FRESH_WITHIN`` before ``now``; "" otherwise."""
    if as_of is None or now is None or now - as_of <= FRESH_WITHIN:
        return ""
    day = as_of.astimezone(UTC).date().isoformat()
    if _is_turkish(text):
        return f"(Bu projenin bellek kayıtları {day} tarihinde bitiyor.)"
    return f"(Memory records for this project end on {day}.)"


def _fit(meter: Meter, out: dict[str, Any], budget: int) -> bool:
    """Shrink ``out`` to ``budget`` (see ``_pack``); True when claims or primary sources were dropped
    (D-209: related sources alone are a trim, not a truncation). Raises E_BUDGET_TOO_SMALL."""
    cut = False
    used = meter.settle(out, budget)
    for key in ("queries", "excerpts_shown"):
        if used > budget and isinstance(out["meta"].get(key), list):
            out["meta"][key] = len(out["meta"][key])
            used = meter.settle(out, budget)
    while used > budget and out["related"]:
        out["related"].pop()
        used = meter.settle(out, budget)
    while used > budget and len(out["claims"]) > 1:
        out["claims"].pop()
        cut = True
        used = meter.settle(out, budget)
    while used > budget and len(out["primary"]) > 1:
        out["primary"].pop()
        cut = True
        used = meter.settle(out, budget)
    if used > budget:
        raise ToolError("E_BUDGET_TOO_SMALL", f"token_budget {budget} cannot hold the answer", min=used)
    return cut


def truncation_marker(budget: int, budget_cut: bool) -> str:
    """D-209: the visible last line of an answer that lost content."""
    if budget_cut:
        return f"[answer truncated at token_budget={budget}; re-ask with a larger token_budget]"
    return (
        f"[answer truncated at the {rs.ANSWER_MAX_CHARS}-character answer limit; "
        "ask a narrower or split question]"
    )


def _pack(meter: Meter, out: dict[str, Any], budget: int) -> dict[str, Any]:
    """Fit ``token_budget``: the query list, then the excerpts_shown list become counts, then related
    sources, claims and primary sources are dropped from the tail (at least one claim and one primary
    stay); the answer text is never cut (``E_BUDGET_TOO_SMALL`` with ``min`` instead). D-209: when
    claims or primary sources were dropped, or the answer hit its length limit, ``meta.flags.truncated``
    is set and the answer ends with a visible marker line (its tokens counted in the budget)."""
    pristine = copy.deepcopy(out)
    budget_cut = _fit(meter, out, budget)
    flags = out["meta"].get("flags")
    cap_cut = isinstance(flags, dict) and bool(flags.get("truncated")) and not out.get("abstained")
    if not (budget_cut or cap_cut) or out.get("abstained"):
        return out

    def marked(by_budget: bool) -> tuple[dict[str, Any], bool]:
        res = copy.deepcopy(pristine)
        res["answer"] = f"{res['answer']}\n{truncation_marker(budget, by_budget)}"
        if isinstance(res["meta"].get("flags"), dict):
            res["meta"]["flags"]["truncated"] = True
        else:
            res["meta"]["flags"] = {"truncated": True}
        return res, _fit(meter, res, budget)  # the marker's tokens are inside the budget

    res, cut = marked(budget_cut)
    if cut and not budget_cut:  # the marker itself pushed content out: name the budget
        res, _cut = marked(True)
    return res


__all__ = [
    "CONTEXT",
    "DEFAULT_BUDGET",
    "MAX_DRILL",
    "TOOL",
    "AskRequest",
    "ask",
    "candidate_order",
    "collapse",
    "default_project",
    "drill_order",
    "parse_request",
    "rrf",
    "rrf_items",
]
