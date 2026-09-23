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
  the request transaction is never used for it, and no transaction is held by the judge across
  the call. Deviation from D-062's "no transaction across an LLM call" (which governs the worker's
  apply path): the API middleware's read-only request transaction (device row FOR SHARE) stays
  open while the judge runs. It is bounded by the cap (kept below the server's idle-in-transaction
  timeout) and by ``MAX_IN_FLIGHT``; a revocation of that device waits at most that long.
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
  ``MAX_IN_FLIGHT`` judged calls run at once (the rest answer deterministically, ``busy``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
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
IDLE_MARGIN_S = 0.75
MAX_CANDIDATES = 10
LESSON_TEXT_CHARS = 1200
WHY_MAX = 300
MAX_MATCHES = 3
#: judged calls in flight per process: each holds its API request's pooled connection for up to the
#: cap, so a stalled provider can occupy at most this many connections (others answer
#: deterministically with ``judge:"busy"``; risk_check runs about once per CLI session start)
MAX_IN_FLIGHT = 2
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
ERROR = "error"
JUDGED = frozenset({OK, OK_FALLBACK})

ConnectFactory = Callable[[], Any]  # () -> awaitable AsyncConnection (``async with await f()``)


@dataclass(slots=True)
class JudgeItem:
    version_id: int
    project: str  # slug shown to the model next to the lesson text


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


def _text(title: str, body: str) -> str:
    """Title + body; a register_lesson title is the mistake's first line, so it is not repeated."""
    head = title.rstrip("…").strip()
    t = body.strip() if head and head in body else f"{title}\n{body}".strip()
    return t if len(t) <= LESSON_TEXT_CHARS else t[:LESSON_TEXT_CHARS] + " …"


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
        # the API request transaction idles while the judge runs: stay clear of the server's
        # idle_in_transaction_session_timeout (5 s by default), whatever the configured cap
        idle_s = float(getattr(settings, "db_idle_in_transaction_timeout_ms", 5000)) / 1000
        self.timeout_s = max(0.1, min(timeout_s, idle_s - IDLE_MARGIN_S))
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
        try:
            async with asyncio.timeout(self.timeout_s):
                result = await self._judge(task, items, capabilities)
        except TimeoutError:
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

    async def _judge(self, task: str, items: list[JudgeItem], capabilities: dict[str, Any]) -> JudgeResult:
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
                "text": _text(loaded[it.version_id].title, loaded[it.version_id].body),
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
            self.spec, user_message(task, lessons), validate=_consistency, precheck=precheck
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
    "user_message",
]
