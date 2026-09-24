"""The ``memory.risk_check`` LLM judge (PHASE2-4-ROADMAP W2d, T4; D-062, D-066, D-067).

Runs IN THE API PROCESS, bounded by a hard wall-clock cap (``JUDGE_TIMEOUT_S`` = 4 s): the core
never waits for the LLM beyond it. Any timeout, breaker, budget stop, privacy denial or model
failure returns a status instead of raising; ``core/risk_service`` then answers deterministically
(``judged:false``).

Same guards as the librarian worker:

* **Privacy default-deny** (``librarian.privacy``): the gate runs over the candidates at plan time
  and AGAIN before every provider attempt (retries and fallback included). Only the candidates it
  allows enter the prompt; ``device:*`` items, projects with ``policy.librarian=off`` and items
  co-owned by an ungranted project never leave the host. The gate uses its own short connections:
  the request transaction is never used for it, and no transaction is held across the call:
  ``core/risk_service`` detaches (commits and returns) the API request transaction before the
  judge and re-checks authority and item visibility in a fresh short transaction afterwards.
* **Redaction** of the prompt (provider) and of every ``why`` returned (CC-5 free text).
* **Spend guard**: the provider's atomic hour/day/month reservation (``llm_budget``) and the
  ``llm_calls`` ledger, exactly as for librarian jobs (no per-job lineage: a risk_check is no job).
* **D-067 deterministic guards**: schema + enum validation, verdict/matches consistency (a
  mismatch is a schema failure, retried once), and every match must cite one of the candidate ids
  it was shown; any other id is DROPPED and counted. Abstention (``"none"``) is first-class.
* **Qualification** (D-017): a profile whose file lists ``disabled_tasks = ["risk_judge"]`` is left
  out of the judge's chain (a model that fails the risk gate never judges; see D-066/G-LIVE-C).
* A judge-level circuit breaker (3 consecutive timeouts/outages → skip the LLM for 30 s, doubling
  to 15 min) keeps a stalled provider from costing every call the full cap, and at most
  ``MAX_IN_FLIGHT`` judged calls run at once (the rest answer retrieval-only, ``busy``).
* A model ``warn`` whose matches ALL cite ids it was not shown is a judge failure (``guard``):
  the result falls back to retrieval-only, it never turns into a judged "no matching evidence".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from psycopg import AsyncConnection

from hlmemo.config import load_profile
from hlmemo.librarian import privacy
from hlmemo.librarian.budget import Caps, DbBudget, NoBudget
from hlmemo.librarian.cassette import CassetteStore
from hlmemo.librarian.errors import (
    AuthorityLost,
    BudgetDeferred,
    CassetteMiss,
    DeadlineExceeded,
    LlmConfigError,
    LlmDisabled,
    PrivacyDenied,
    ProviderUnavailable,
    SchemaFail,
)
from hlmemo.librarian.ledger import DbLedger
from hlmemo.librarian.profiles import LlmProfile, profile_chain
from hlmemo.librarian.prompts import TaskSpec, load_task
from hlmemo.librarian.provider import Breaker, Clock, Provider
from hlmemo.librarian.redact import Redactor

log = logging.getLogger("hlmemo.librarian.risk_judge")

TASK = "risk_judge"
JOB_NAME = "risk_check"
JUDGE_TIMEOUT_S = 4.0
#: per-request HTTP timeout inside the cap, so a stalled attempt settles its reservation and writes
#: its ledger row (instead of being cancelled mid-flight and swept later as worst case)
HTTP_TIMEOUT_S = 3.5
MAX_CANDIDATES = 10
LESSON_TEXT_CHARS = 1200  # per lesson in the prompt (title + best-matching window when longer)
WHY_MAX = 300
MAX_MATCHES = 3
#: judged calls in flight per process (a provider-load bound; the API request's connection is
#: released before the judge runs, D-062). Beyond it risk_check answers retrieval-only ("busy").
MAX_IN_FLIGHT = 4
BREAKER_THRESHOLD = 3
BREAKER_OPEN_S = 30.0
BREAKER_MAX_OPEN_S = 900.0

# statuses (the ``judge`` field of the risk_check result)
OK = "ok"
OK_FALLBACK = "ok_fallback"  # judged by the fallback profile (D-066: the caller is told)
NOT_REQUESTED = "not_requested"
DISABLED = "disabled"
NO_CANDIDATES = "no_candidates"
TIMEOUT = "timeout"
UNAVAILABLE = "unavailable"
BUDGET = "budget"
SCHEMA_FAIL = "schema_fail"
PRIVACY = "privacy"
BUSY = "busy"
GUARD = "guard"  # D-067: the model warned, but every match cited an id it was not shown
ERROR = "error"
JUDGED = frozenset({OK, OK_FALLBACK})
#: the ``judge`` label of every result the LLM did not judge (the status becomes ``reason``)
RETRIEVAL_ONLY = "retrieval_only"

ConnectFactory = Callable[[], Any]  # () -> awaitable AsyncConnection (``async with await f()``)


@dataclass(slots=True)
class JudgeItem:
    version_id: int
    project: str  # slug shown to the model next to the lesson text
    #: ``(char_start, char_end)`` in the body of the chunks that matched the task in the
    #: deterministic stage, best first: a lesson longer than the cap is sent as its title plus
    #: the best-matching window of these (``lesson_text``), not its first characters
    spans: tuple[tuple[int, int], ...] = ()


@dataclass(slots=True)
class JudgeResult:
    status: str
    matches: list[tuple[int, str]] = field(default_factory=list)  # (version_id, why), ranked
    denied: set[int] = field(default_factory=set)  # version ids the privacy gate withheld
    dropped: int = 0  # D-067: matches citing an id that was not shown to the model
    profile: str | None = None
    latency_ms: int = 0

    @property
    def judged(self) -> bool:
        return self.status in JUDGED


def disabled_tasks(profile_name: str) -> set[str]:
    """``disabled_tasks`` of a profile FILE (qualification is configuration, D-017)."""
    raw = load_profile(profile_name)
    value = raw.get("disabled_tasks") or raw.get("DISABLED_TASKS") or []
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    return {str(v) for v in value}


def judge_chain(settings: Any) -> list[LlmProfile]:
    """The provider chain minus the profiles not qualified for the risk judge."""
    try:
        chain = profile_chain(settings)
    except LlmConfigError as exc:
        log.warning("risk judge disabled: %s", exc)
        return []
    return [p for p in chain if TASK not in disabled_tasks(p.name)]


def direct_connector(dsn: str, settings: Any) -> tuple[ConnectFactory, Callable[[], Any]]:
    """Unpooled short connections for the gate/budget/ledger (the API's request pool is never
    used here, so a burst of judged calls cannot starve ordinary requests of connections).
    Returns ``(connect, ctx)``: ``async with await connect() as c`` and ``async with ctx() as c``."""
    options = (
        f"-c lock_timeout={settings.db_lock_timeout_ms}ms"
        f" -c statement_timeout={settings.db_statement_timeout_ms}ms"
        f" -c idle_in_transaction_session_timeout={settings.db_idle_in_transaction_timeout_ms}ms"
        " -c TimeZone=UTC"
    )

    async def connect() -> AsyncConnection:
        return await AsyncConnection.connect(dsn, autocommit=False, options=options, connect_timeout=3)

    @contextlib.asynccontextmanager
    async def ctx() -> AsyncIterator[AsyncConnection]:
        conn = await connect()
        try:
            yield conn
        finally:
            await conn.close()

    return connect, ctx


_TERM_RE = re.compile(r"[\w][\w./-]{2,}", re.UNICODE)
_GAP = "\n…\n"


def _terms(task: str) -> set[str]:
    return {t.strip("./-") for t in _TERM_RE.findall(task.casefold()) if len(t.strip("./-")) >= 3}


def _best_start(body: str, a: int, b: int, width: int, terms: set[str]) -> int:
    """Start of the ``width``-character window of ``body[a:b]`` holding the most distinct task
    terms (then the most occurrences; ties: the earliest), at a line or sentence start."""
    if b - a <= width:
        return a
    hits: list[tuple[int, str]] = []
    low = body[a:b].casefold()
    for term in terms:
        for m in re.finditer(re.escape(term), low):
            hits.append((a + m.start(), term))
    starts = {a, b - width}
    starts.update(a + m.end() for m in re.finditer(r"\n+|(?<=[.!?;:])\s+", body[a : b - width]))
    best, best_key = a, (-1, -1, 0)
    for s in sorted(starts):
        inside = [t for p, t in hits if s <= p and p + len(t) <= s + width]
        key = (len(set(inside)), len(inside), -s)
        if key > best_key:
            best, best_key = s, key
    return best


def _merge(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(windows):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def lesson_text(
    title: str,
    body: str,
    spans: tuple[tuple[int, int], ...] | list[tuple[int, int]] = (),
    task: str = "",
    cap: int = LESSON_TEXT_CHARS,
) -> str:
    """The lesson as the judge sees it, at most ``cap`` characters (e2e 2026-09-24 #6).

    A lesson that fits is sent whole (title + body; a register_lesson title is the mistake's first
    line, so it is not repeated). A longer one is sent as its title plus the best-matching window:
    the chunks that matched the task in the deterministic stage (``spans``, best first), whole
    while they fit, the rest of the room from the next chunk's densest part in task terms; the
    pieces keep their order in the body and gaps are marked ``…``. Without spans: the first
    ``cap`` characters (the pre-fix behaviour)."""
    head = title.rstrip("…").strip()
    full = body.strip() if head and head in body else f"{title}\n{body}".strip()
    if len(full) <= cap:
        return full
    valid = [(max(0, a), min(len(body), b)) for a, b in spans if min(len(body), b) > max(0, a)]
    if not valid:
        return full[:cap] + " …"
    prefix = f"{title.strip()}\n"
    terms = _terms(task)
    windows: list[tuple[int, int]] = []
    for a, b in valid:
        merged = _merge(windows)
        used = sum(e - s for s, e in merged) + len(_GAP) * len(merged)  # a gap before each new piece
        room = cap - len(prefix) - 4 - used  # 4: the leading/trailing "… " markers
        if room < 80:  # a sliver is noise, not evidence
            break
        covered = [(max(s, a), min(e, b)) for s, e in merged if min(e, b) > max(s, a)]
        if sum(e - s for s, e in covered) >= b - a:  # an earlier window holds it already
            continue
        if b - a <= room:
            windows.append((a, b))
            continue
        start = _best_start(body, a, b, room, terms)
        windows.append((start, start + room))
        break
    pieces = _merge(windows)
    text = _GAP.join(body[s:e].strip() for s, e in pieces)
    if pieces[0][0] > 0:
        text = "… " + text
    if pieces[-1][1] < len(body.rstrip()):
        text += " …"
    return (prefix + text)[: cap + 2]


def user_message(task: str, lessons: list[dict[str, str]]) -> str:
    payload = {"task": task, "lessons": lessons}
    return f"JOB: {JOB_NAME}\nINPUT: {json.dumps(payload, ensure_ascii=False)}"


def _consistency(obj: dict[str, Any]) -> str | None:
    """D-067: verdict and matches must agree (a mismatch is a schema failure: retried once)."""
    if (obj.get("verdict") == "warn") != bool(obj.get("matches")):
        return "verdict/matches mismatch"
    return None


class RiskJudge:
    def __init__(
        self,
        settings: Any,
        *,
        provider: Provider | None = None,
        chain: list[LlmProfile] | None = None,
        connect: ConnectFactory | None = None,
        clock: Clock | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = JUDGE_TIMEOUT_S,
        cassette_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.timeout_s = timeout_s
        self.in_flight = 0
        self.clock = clock or Clock()
        self.breaker = Breaker(
            self.clock, threshold=BREAKER_THRESHOLD, base_s=BREAKER_OPEN_S, max_s=BREAKER_MAX_OPEN_S
        )
        default_connect, conn_ctx = direct_connector(settings.db_dsn, settings)
        self.connect = connect or default_connect
        if provider is not None:
            self.chain = list(provider.chain)
        else:
            self.chain = list(chain) if chain is not None else judge_chain(settings)
        self.spec: TaskSpec = load_task(TASK)
        self._enabled = bool(settings.librarian_enabled) and settings.llm_mode != "off" and bool(self.chain)
        if provider is None and self._enabled:
            provider = self._build_provider(conn_ctx, transport, cassette_dir)
        self.provider = provider

    def _build_provider(
        self,
        conn_ctx: Callable[[], Any],
        transport: httpx.AsyncBaseTransport | None,
        cassette_dir: Path | None,
    ) -> Provider:
        s = self.settings
        cassettes = None
        if s.llm_mode in ("record", "replay"):
            directory = cassette_dir or s.llm_cassette_dir
            if directory is None:
                raise LlmConfigError(f"HLM_LLM_MODE={s.llm_mode} needs HLM_LLM_CASSETTE_DIR")
            cassettes = CassetteStore(Path(directory), record_name=TASK, redactor=Redactor.from_settings(s))
        budget = (
            NoBudget()
            if s.llm_budget_disabled
            else DbBudget(conn_ctx, Caps.from_settings(s), ttl_s=s.llm_reservation_ttl_s)
        )
        return Provider(
            self.chain,
            mode=s.llm_mode,
            budget=budget,
            ledger=DbLedger(conn_ctx),
            cassettes=cassettes,
            transport=transport,
            clock=self.clock,
            redactor=Redactor.from_settings(s),
            timeout_s=min(float(s.llm_timeout_s), HTTP_TIMEOUT_S),
            breaker_threshold=s.llm_breaker_threshold,
            breaker_open_s=s.llm_breaker_open_s,
            breaker_max_open_s=s.llm_breaker_max_open_s,
            job_call_cap=s.llm_job_call_cap,
            budget_disabled=s.llm_budget_disabled,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and self.provider is not None

    async def aclose(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()

    def unavailable(self) -> str | None:
        """Why a judge call would not reach the provider right now (None: it would try). Lets the
        caller skip releasing its request transaction for a call that cannot happen."""
        if not self.enabled:
            return DISABLED
        if self.in_flight >= MAX_IN_FLIGHT:
            return BUSY
        if self.breaker.remaining_s() > 0:
            return UNAVAILABLE
        return None

    # ------------------------------------------------------------------ judging
    async def judge(self, task: str, items: list[JudgeItem], capabilities: dict[str, Any]) -> JudgeResult:
        t0 = time.perf_counter()
        if not self.enabled:
            return JudgeResult(DISABLED)
        items = items[:MAX_CANDIDATES]
        if not items:
            return JudgeResult(NO_CANDIDATES)
        if self.in_flight >= MAX_IN_FLIGHT:
            return JudgeResult(BUSY)
        if not self.breaker.allow():
            return JudgeResult(UNAVAILABLE)
        self.in_flight += 1  # no await since the check: atomic on the event loop
        # One deadline for the whole call. The provider budgets every HTTP timeout to end before
        # it (the privacy gates and DB bookkeeping use part of the cap) and starts no attempt,
        # reservation or backoff without room, so the backstop below never has to cut a request
        # mid-flight; if it ever does, the provider still settles the reservation and writes the
        # ledger row (shielded).
        deadline = asyncio.get_running_loop().time() + self.timeout_s
        try:
            async with asyncio.timeout_at(deadline):
                result = await self._judge(task, items, capabilities, deadline)
        except (TimeoutError, DeadlineExceeded):
            self.breaker.failure()
            result = JudgeResult(TIMEOUT)
        except (ProviderUnavailable, httpx.HTTPError, OSError) as exc:
            log.warning("risk judge unavailable: %s", type(exc).__name__)
            self.breaker.failure()
            result = JudgeResult(UNAVAILABLE)
        except BudgetDeferred:
            result = JudgeResult(BUDGET)
        except SchemaFail:
            self.breaker.success()  # the provider answered; the model output was the problem
            result = JudgeResult(SCHEMA_FAIL)
        except (PrivacyDenied, AuthorityLost):
            result = JudgeResult(PRIVACY)
        except (LlmDisabled, LlmConfigError) as exc:
            log.warning("risk judge disabled: %s", exc)
            result = JudgeResult(DISABLED)
        except CassetteMiss as exc:  # strict replay (CI): surfaced as an error status, never a crash
            log.error("risk judge cassette miss: %s", exc)
            result = JudgeResult(ERROR)
        except Exception:  # noqa: BLE001 - the judge is advisory: any failure degrades, never raises
            log.exception("risk judge failed")
            result = JudgeResult(ERROR)
        finally:
            self.in_flight -= 1
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        return result

    async def _judge(
        self, task: str, items: list[JudgeItem], capabilities: dict[str, Any], deadline: float
    ) -> JudgeResult:
        assert self.provider is not None
        ids = [it.version_id for it in items]
        verdict, loaded = await privacy.gate(self.connect, capabilities, ids)
        if not verdict.device_ok:
            return JudgeResult(PRIVACY, denied=set(ids))
        allowed = [it for it in items if verdict.allowed(it.version_id)]
        denied = {it.version_id for it in items if not verdict.allowed(it.version_id)}
        if not allowed:
            return JudgeResult(NO_CANDIDATES, denied=denied)
        local = {f"R{i + 1}": it for i, it in enumerate(allowed)}
        lessons = [
            {
                "id": lid,
                "project": it.project,
                "text": lesson_text(loaded[it.version_id].title, loaded[it.version_id].body, it.spans, task),
            }
            for lid, it in local.items()
        ]
        sent = [it.version_id for it in allowed]

        async def precheck() -> None:  # before EVERY provider attempt (D-062)
            again, _ = await privacy.gate(self.connect, capabilities, sent)
            if not again.device_ok:
                raise AuthorityLost("E_AUTHORITY_LOST")
            if not all(again.allowed(v) for v in sent):
                raise PrivacyDenied("E_PRIVACY_DENIED")

        res = await self.provider.complete(
            self.spec,
            user_message(task, lessons),
            validate=_consistency,
            precheck=precheck,
            deadline=deadline,
        )
        self.breaker.success()
        matches: list[tuple[int, str]] = []
        dropped = 0
        seen: set[int] = set()
        for m in res.output.get("matches") or []:
            it = local.get(str(m.get("id", "")).strip())
            if it is None:  # D-067: a claim must cite a clue that was shown
                dropped += 1
                continue
            if it.version_id in seen:
                continue
            seen.add(it.version_id)
            why = " ".join(self.provider.redactor.text(str(m.get("why", ""))).split())
            matches.append((it.version_id, why[:WHY_MAX]))
        if res.output.get("matches") and not matches:  # D-067: every claim was uncited
            return JudgeResult(GUARD, denied=denied, dropped=dropped, profile=res.profile)
        status = OK if res.profile == self.chain[0].name else OK_FALLBACK
        return JudgeResult(
            status, matches=matches[:MAX_MATCHES], denied=denied, dropped=dropped, profile=res.profile
        )


def app_judge(app: Any) -> RiskJudge:
    """The app's judge, built on first use (the provider keeps breakers and HTTP clients)."""
    judge = getattr(app.state, "risk_judge", None)
    if judge is None:
        judge = RiskJudge(app.state.settings)
        app.state.risk_judge = judge
    return judge


async def close_app_judge(app: Any) -> None:
    judge = getattr(app.state, "risk_judge", None)
    if judge is not None:
        app.state.risk_judge = None
        await judge.aclose()


__all__ = [
    "JUDGED",
    "JUDGE_TIMEOUT_S",
    "RETRIEVAL_ONLY",
    "MAX_CANDIDATES",
    "TASK",
    "JudgeItem",
    "JudgeResult",
    "RiskJudge",
    "app_judge",
    "close_app_judge",
    "direct_connector",
    "disabled_tasks",
    "judge_chain",
    "lesson_text",
    "user_message",
]
