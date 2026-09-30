"""OpenAI-compatible librarian provider (W2a; D-017, D-019, CC-5).

``Provider.complete(task, user)`` sends ``[system prompt, redacted user message]`` to the profile
chain ``[primary, fallback?]`` and returns schema-valid JSON. The fallback is the TASK's
(``chain_for(task)``, D-094): ``HLM_FALLBACK_PROFILE__<TASK>`` when configured (carried by the
chain's primary, see ``librarian.profiles``), else the default ``HLM_FALLBACK_PROFILE``; a fallback
whose file lists the task in ``disabled_tasks`` is never used for it. Per profile:

* every request sets ``max_tokens`` from the task bound; ``response_format`` is ``json_object``
  from the profile's ``extra``, or ``json_schema`` when the profile declares support;
* transient failures (HTTP 408/429/5xx, timeouts, transport errors, a provider ``finish_reason``
  of ``error``) are retried with backoff 1/2/4/8 s, at most 5 attempts;
* a schema-invalid answer (unparseable, not an object, schema or task-level violation) is retried
  once; a second one raises ``SchemaFail`` (no fallback: the provider is up, the model is wrong).
  R4 (R-9) ``complete(schema_fallback=True)`` (the research writer): a schema failure falls through
  to the NEXT profile of the chain instead: at once for a TRUNCATED answer (``finish_reason`` length,
  a retry would truncate again), after the one retry otherwise, and without that retry when the
  caller's ``attempt_affordable`` says its worst case does not fit (it is never reserved). Only the
  chain's last profile raises ``SchemaFail``. Every profile given up is in ``LlmResult.fallbacks``;
* R4 (R-5): a profile past its ``price_valid_until`` is unusable for live calls: skipped without any
  network or reservation (``price_expired`` in ``LlmResult.fallbacks``, counted per profile in
  ``Provider.price_expired_skips``, logged) and the next profile answers; a chain with no usable
  profile raises ``PriceExpired`` (fail closed). Not an ``llm_calls`` outcome: the row's outcome is
  a closed set (migration 0006), so the skip writes no row;
* an exhausted or fatally failing profile falls through to the fallback profile once;
* a per-profile circuit breaker opens after N consecutive exhausted calls for 60 s, doubling on
  each failed half-open trial up to 15 min; an open breaker costs a ``breaker_open`` ledger row
  and no network;
* before every network attempt the worst case is reserved atomically (``budget``) and the
  per-job call ceiling is checked; after it the reservation is settled at the actual cost.

Attempt policies (``complete(attempt_policy=)``):

* ``background`` (the librarian worker, the default): the retry/backoff above, per profile.
* ``latency`` (deadline-bounded API callers: W2e synthesis 7 s / 6 s cap, W2d risk judge 4 s;
  BACKLOG, D-084 bake-off): ONE bounded attempt per profile, no backoff sleeps. While a later
  profile of the chain is still available (its breaker not open), an attempt may use at most
  ``LATENCY_PRIMARY_SHARE`` of the remaining deadline; the last available profile gets the rest
  (minus the margin). A timeout, 5xx/429/408, transport error or non-retryable HTTP error goes
  straight to the next profile, so a stalled or failing primary leaves the fallback real time.
  Every attempt still runs the privacy precheck (D-062), the reservation/settle and its ledger
  row (shielded) and the per-lineage ceiling; the chain is the task's (``chain_for``: the risk
  judge uses its own qualified fallback or none, D-071/D-094). When every attempted profile timed
  out the call raises ``DeadlineExceeded`` (the caller's ``timeout``), otherwise
  ``ProviderUnavailable``. Breaker failures are counted per PROFILE (``ChainBreakers`` is an API
  caller's view of them), so a failing primary never suppresses a working fallback. D-173: an
  attempt CUT by its profile's per-role cap (``LlmProfile.attempt_timeout_s``: a read or wall-clock
  timeout, not a connect timeout) is not a breaker failure (and no success); ``CUT_VALVE_COUNT``
  cuts of one profile within ``CUT_VALVE_WINDOW_S`` open its breaker (logged).

Every attempt writes exactly one ``llm_calls`` row. ``HLM_LLM_MODE`` selects live / record /
replay (strict cassettes) / off. The raw provider response is never stored anywhere.

B1/R-8 cost and usage (``normalize_usage``): ``usage.cost``, when the endpoint reports it, is the USD
authority. The billed OUTPUT tokens are normalized per profile convention (``usage_reasoning``):
``included`` (OpenAI/OpenRouter) books ``completion_tokens``; ``excluded`` (Google's
OpenAI-compatible API, whose ``completion_tokens`` leave thinking out) books ``completion_tokens`` +
``completion_tokens_details.reasoning_tokens``, else ``total_tokens`` − ``prompt_tokens``, cross-checked
against ``total_tokens`` when both are there. Usage that is missing, contradictory (e.g. zero
reasoning with a total gap) or cannot show the thinking is UNRELIABLE: the attempt settles at its
worst case and books ``max_tokens`` output tokens, never less. The ledger's ``output_tokens``, the
USD settlement and a caller's per-question tally all use the same normalized number.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from hlmemo.core.budget import Meter
from hlmemo.librarian.budget import BudgetGuard, Caps, ConnCtx, DbBudget, NoBudget
from hlmemo.librarian.cassette import (
    CassetteStore,
    canonical,
    cassette_key,
    normalize_content,
    sanitize_response,
)
from hlmemo.librarian.errors import (
    BreakerOpen,
    BudgetDeferred,
    DeadlineExceeded,
    JobCallCapExceeded,
    LlmConfigError,
    LlmDisabled,
    PriceExpired,
    ProviderUnavailable,
    SchemaFail,
)
from hlmemo.librarian.ledger import DbLedger, Ledger, LedgerRow
from hlmemo.librarian.profiles import LlmProfile, for_task, profile_chain
from hlmemo.librarian.prompts import TaskSpec
from hlmemo.librarian.redact import Redactor

BACKOFF_S = (1, 2, 4, 8)
MAX_TRANSIENT_ATTEMPTS = 5
#: D-173: an attempt CUT by its profile's per-role cap (``LlmProfile.attempt_timeout_s``, the research
#: writer's timeout) is expected tail latency, not an outage: it never counts toward the breaker
#: (nor resets it). The safety valve: this many cuts of one profile within the window open its
#: breaker (a hard outage of that profile is still cut off)
CUT_VALVE_COUNT = 8
CUT_VALVE_WINDOW_S = 300.0

log = logging.getLogger("hlmemo.librarian.provider")
TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})
_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$", re.S)

#: D-212: the reason and the ledger outcome (migration 0010) of a provider failure that means the
#: account is out of credit or quota (an ops problem, not a model problem)
BILLING_OR_QUOTA = "billing_or_quota"
#: (R4.1 review F-4) words match as WHOLE words (``balancer`` is not ``balance``).
#: HARD: the account is out of money or of a daily/monthly allowance; decisive even when a rate-limit
#: word is also present. ``insufficient_quota`` is OpenAI's code for an exhausted plan.
_HARD_BILLING = re.compile(
    r"(?<![a-z0-9])(?:prepa(?:y|id|yment)\w*|credits?|insufficient[_ ](?:funds|quota|credits?)"
    r"|payment|balance|(?:daily|monthly)\s+(?:quota|limit|allowance|budget)"
    r"|quota\w*\s+(?:per\s+)?(?:day|month))(?![a-z0-9])"
    r"|per[\s_-]?(?:day|month)"  # also inside a quota id: ...PerDayPerProjectPerModel
)
#: a normal rate limit: a per-minute/second quota (RPM/TPM), "rate limit", "slow down", "try again in"
_RATE_LIMIT = re.compile(
    r"per[\s_-]?(?:minute|second)"  # also inside a quota id: ...PerMinutePerProjectPerModel
    r"|(?<![a-z0-9])(?:rpm|tpm|rate[\s_-]?limit\w*|too many requests|slow down"
    r"|requests per (?:minute|second)|try again in|retry[\s_-]?(?:after|in))(?![a-z0-9])"
)
#: WEAK: a billing/quota word that is billing only when nothing says it is a rate limit
_WEAK_BILLING = re.compile(r"(?<![a-z0-9])(?:resource_exhausted|quota|billing)(?![a-z0-9])")


def is_billing_or_quota(
    status: int | None, body: bytes | str | dict[str, Any] | None, retry_after: str | None = None
) -> bool:
    """D-212: does a provider failure (HTTP ``status`` and error ``body``) mean billing or quota
    exhaustion? 402 always; 403 and 429 when the error body names it. (R4.1 review F-4) Only
    billing, prepay/credit or balance exhaustion and a daily/monthly allowance are billing (whole
    words); a per-minute/RPM quota, a ``Retry-After`` header, "rate limit" and similar are a normal
    rate limit unless a hard billing word is also there. A quota / RESOURCE_EXHAUSTED / billing word
    alone (no rate-limit hint) still counts. A 401, a 5xx and everything else stay what they were."""
    if status == 402:
        return True
    if status not in (403, 429):
        return False
    if isinstance(body, dict):
        text = json.dumps(body, ensure_ascii=False)
    elif isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = body or ""
    text = text.lower()
    if _HARD_BILLING.search(text):
        return True
    if retry_after or _RATE_LIMIT.search(text):
        return False
    return _WEAK_BILLING.search(text) is not None


Validator = Callable[[dict[str, Any]], str | None]
#: loop lineage of the call being made (per asyncio task; concurrent calls never share it)
_LINEAGE: contextvars.ContextVar[str | None] = contextvars.ContextVar("hlm_llm_lineage", default=None)
#: privacy/authority re-check run before every network attempt of the current call (Sol 37 #1)
_PRECHECK: contextvars.ContextVar[Callable[[], Awaitable[None]] | None] = contextvars.ContextVar(
    "hlm_llm_precheck", default=None
)
#: the caller's per-attempt guard (review 79 T4): called before EVERY attempt (retries and the
#: fallback included, before any reservation or byte) with the profile, that attempt's worst-case USD
#: (its own prices) and worst-case tokens; it raises to stop the call (a per-question budget)
AttemptGuard = Callable[[LlmProfile, Decimal, int], Awaitable[None]]
_GUARD: contextvars.ContextVar[AttemptGuard | None] = contextvars.ContextVar("hlm_llm_guard", default=None)
#: R4 (R-9): a caller's side-effect-free check whether one more attempt (its worst case) still fits:
#: a schema retry that does not fit is never reserved (``complete(schema_fallback=True)``)
AttemptAffordable = Callable[[LlmProfile, Decimal, int], Awaitable[bool]]
_AFFORD: contextvars.ContextVar[AttemptAffordable | None] = contextvars.ContextVar(
    "hlm_llm_afford", default=None
)
#: D-189: a caller's read-only observer of each attempt (the messages sent, the raw output)
_OBSERVE: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = contextvars.ContextVar(
    "hlm_llm_observe", default=None
)


def _emit(event: dict[str, Any]) -> None:
    """D-189: hand one attempt's facts to the caller's observer; an observer failure is logged and
    never changes the call."""
    observe = _OBSERVE.get()
    if observe is None:
        return
    try:
        observe(event)
    except Exception:  # noqa: BLE001 - an observer is diagnostics only
        log.warning("llm observer failed", exc_info=True)


#: the caller's deadline on the event-loop clock (``complete(deadline=)``, e.g. the API's 4 s risk
#: judge cap): each HTTP timeout is budgeted to end DEADLINE_MARGIN_S before it, and no attempt,
#: reservation or backoff starts without MIN_ATTEMPT_S of room (DeadlineExceeded instead)
_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("hlm_llm_deadline", default=None)
DEADLINE_MARGIN_S = 0.3
MIN_ATTEMPT_S = 0.2
#: ``latency`` policy: the share of the remaining deadline an attempt may use while a later
#: profile is still available (the rest is left for it)
LATENCY_PRIMARY_SHARE = 0.55
ATTEMPT_POLICIES = ("background", "latency")


def _remaining_s() -> float | None:
    """Seconds an attempt may still use before the caller's deadline (minus the margin)."""
    deadline = _DEADLINE.get()
    if deadline is None:
        return None
    return deadline - asyncio.get_running_loop().time() - DEADLINE_MARGIN_S


async def _finalize(coro: Awaitable[Any]) -> None:
    """Run ledger/reservation bookkeeping to completion even if the caller is being cancelled
    (a cancelled attempt must still settle its reservation and write its llm_calls row)."""
    task = asyncio.ensure_future(coro)
    await asyncio.shield(task)


@contextlib.contextmanager
def lineage_scope(lineage: str | None) -> Iterator[None]:
    """Every provider call made inside (by any handler) counts against ``lineage``'s ceiling;
    the worker wraps each job's plan in it, so a handler cannot forget to pass it."""
    token = _LINEAGE.set(lineage)
    try:
        yield
    finally:
        _LINEAGE.reset(token)


class Clock:
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()


class Breaker:
    """Consecutive-failure circuit breaker with a doubling open window (one half-open trial)."""

    def __init__(self, clock: Clock, *, threshold: int, base_s: float, max_s: float) -> None:
        self.clock = clock
        self.threshold = threshold
        self.base_s = base_s
        self.max_s = max_s
        self.window_s = base_s
        self.failures = 0
        self.state = "closed"
        self.open_until = 0.0
        self._trial = False

    def allow(self) -> bool:
        if self.state == "open":
            if self.clock.monotonic() < self.open_until:
                return False
            self.state = "half_open"
            self._trial = False
        if self.state == "half_open":
            if self._trial:
                return False
            self._trial = True
        return True

    def remaining_s(self) -> float:
        return max(0.0, self.open_until - self.clock.monotonic()) if self.state == "open" else 0.0

    def available(self) -> bool:
        """Would ``allow()`` let a call through right now? (No side effect: no half-open trial.)"""
        if self.state == "open":
            return self.clock.monotonic() >= self.open_until
        return not (self.state == "half_open" and self._trial)

    def success(self) -> None:
        self.failures = 0
        self.state = "closed"
        self.window_s = self.base_s
        self._trial = False

    def failure(self) -> None:
        if self.state == "half_open":
            self.window_s = min(self.window_s * 2, self.max_s)
            self._open()
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self._open()

    def trip(self) -> None:
        """D-173: open now (the cut valve), whatever the consecutive-failure count."""
        self._open()

    def _open(self) -> None:
        self.state = "open"
        self.open_until = self.clock.monotonic() + self.window_s
        self._trial = False


class ChainBreakers:
    """An API caller's view of its provider's PER-PROFILE breakers: the call is refused only while
    EVERY profile of the chain is unavailable, so an open primary never suppresses a working
    fallback. The provider counts the failures (per profile, per attempt chain); ``success()``
    closes them all (an operator or test reset). Without a provider (disabled) nothing is open.
    ``task``: the caller's task, whose chain (``Provider.chain_for``, D-094) is the one watched."""

    def __init__(self, provider: Callable[[], Provider | None], *, task: str | None = None) -> None:
        self._provider = provider
        self._task = task

    def _all(self) -> list[Breaker]:
        p = self._provider()
        if p is None:
            return []
        return [p.breaker(x.name) for x in (p.chain_for(self._task) if self._task else p.chain)]

    def allow(self) -> bool:
        breakers = self._all()
        return not breakers or any(b.available() for b in breakers)

    def remaining_s(self) -> float:
        breakers = self._all()
        if not breakers or any(b.available() for b in breakers):
            return 0.0
        return min(b.remaining_s() for b in breakers)

    def success(self) -> None:
        for b in self._all():
            b.success()

    @property
    def state(self) -> str:
        p = self._provider()
        return "closed" if p is None else p.breaker_state(self._task)


@dataclass(slots=True)
class LlmResult:
    output: dict[str, Any]
    task: str
    profile: str
    model_id: str
    prompt_version: str
    schema_version: str
    input_digest: str
    cost_usd: Decimal
    latency_ms: int
    usage: dict[str, Any]
    #: R4 (R-9, R-6): ``(profile, reason)`` of every profile given up before the one that answered
    #: (reason: schema_fail | truncated | retry_unaffordable | timeout | cut | unavailable | breaker_open)
    fallbacks: list[tuple[str, str]] = field(default_factory=list)

    def audit(self, redactor: Redactor) -> dict[str, Any]:
        """One call of the ``llm/1`` audit record (CC-5): schema-valid output AFTER redaction."""
        return {
            "task": self.task,
            "profile": self.profile,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "input_digest": self.input_digest,
            "output": redactor.value(self.output),
        }


class _Exhausted(Exception):
    def __init__(
        self,
        reason: str,
        *,
        fatal: bool = False,
        timeout: bool = False,
        cut: bool = False,
        billing: bool = False,
    ) -> None:
        super().__init__(reason)
        self.billing = billing  # D-212: the provider refused for billing or quota
        self.fatal = fatal
        self.timeout = timeout  # the profile's last attempt timed out (latency policy)
        self.cut = cut  # D-173: ... cut by its profile's per-role cap (not a breaker failure)


@dataclass(slots=True)
class _Attempt:
    kind: str  # "response" | "transient" | "fatal"
    timeout: bool = False  # a transient that was an HTTP timeout
    cut: bool = False  # D-173: a timeout of a capped profile's attempt (read or wall clock)
    content: str | None = None
    row: LedgerRow | None = None
    usage: dict[str, Any] | None = None
    latency_ms: int = 0
    finish: str | None = None  # the choice's finish_reason (R-9: "length" = a truncated answer)
    billing: bool = False  # D-212: a failure that means billing or quota exhaustion


#: D-178: a response parser: content -> (object, None) or (None, why it is not one)
Parser = Callable[[str | None], tuple[dict[str, Any] | None, str | None]]
#: D-178: per profile, the ``(user message, parser)`` of a call's own protocol, or None (JSON)
Variant = Callable[[LlmProfile], tuple[str, Parser] | None]


def parse_json_object(text: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if text is None:
        return None, "empty response"
    cleaned = _FENCE.sub("", text.strip()).strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return None, f"json decode: {exc.msg} at {exc.pos}"
    if not isinstance(obj, dict):
        return None, "top-level JSON is not an object"
    return obj, None


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    """B1/R-8: one response's billed tokens. ``output_tokens`` counts every generated token (the
    answer and the thinking); unreliable usage books the call's ``max_tokens`` (the worst case)."""

    input_tokens: int | None
    output_tokens: int | None
    reliable: bool
    basis: str  # completion_tokens | reasoning_tokens | total_tokens | missing | contradictory | no_thinking


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_usage(
    usage: dict[str, Any] | None, *, reasoning: str = "included", max_tokens: int | None = None
) -> NormalizedUsage:
    """The billed input/output tokens of ``usage`` under the profile's convention (module doc)."""
    usage = usage if isinstance(usage, dict) else {}
    pt = _count(usage.get("prompt_tokens"))
    ct = _count(usage.get("completion_tokens"))
    total = _count(usage.get("total_tokens"))
    details = usage.get("completion_tokens_details")
    rt = _count(details.get("reasoning_tokens")) if isinstance(details, dict) else None

    def unreliable(basis: str) -> NormalizedUsage:
        return NormalizedUsage(pt, max_tokens if max_tokens is not None else ct, False, basis)

    if pt is None or ct is None:
        return unreliable("missing")
    if reasoning == "excluded":  # Google OpenAI-compatible: completion_tokens leaves thinking out
        if rt is not None:
            if total is not None and pt + ct + rt != total:
                return unreliable("contradictory")
            return NormalizedUsage(pt, ct + rt, True, "reasoning_tokens")
        if total is not None:
            if total < pt + ct:
                return unreliable("contradictory")
            return NormalizedUsage(pt, total - pt, True, "total_tokens")
        return unreliable("no_thinking")  # the thinking cannot be counted: never under-book
    # included (OpenAI/OpenRouter): completion_tokens already counts every generated token
    if (total is not None and total != pt + ct) or (rt is not None and rt > ct):
        return unreliable("contradictory")
    return NormalizedUsage(pt, ct, True, "completion_tokens")


def _sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


class Provider:
    def __init__(
        self,
        chain: list[LlmProfile],
        *,
        mode: str = "live",
        budget: BudgetGuard | None = None,
        ledger: Ledger,
        cassettes: CassetteStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        redactor: Redactor | None = None,
        timeout_s: float = 60.0,
        breaker_threshold: int = 5,
        breaker_open_s: float = 60.0,
        breaker_max_open_s: float = 900.0,
        job_call_cap: int = 20,
        budget_disabled: bool = False,
    ) -> None:
        if not chain:
            raise LlmConfigError("empty profile chain")
        if mode not in ("live", "record", "replay", "off"):
            raise LlmConfigError(f"unknown HLM_LLM_MODE {mode!r}")
        if mode in ("record", "replay") and cassettes is None:
            raise LlmConfigError(f"HLM_LLM_MODE={mode} needs HLM_LLM_CASSETTE_DIR")
        self.chain = chain
        self.mode = mode
        self.budget = budget or NoBudget()
        self.ledger = ledger
        self.cassettes = cassettes
        self.transport = transport
        self.clock = clock or Clock()
        self.redactor = redactor or Redactor()
        self.timeout_s = timeout_s
        self.job_call_cap = job_call_cap
        self.budget_disabled = budget_disabled
        self.meter = Meter()
        self._breaker_args = {
            "threshold": breaker_threshold,
            "base_s": breaker_open_s,
            "max_s": breaker_max_open_s,
        }
        self._breakers: dict[str, Breaker] = {}
        #: D-173: the recent attempt-cap cuts per profile (monotonic times), for the valve
        self._cuts: dict[str, deque[float]] = {}
        #: R4 (R-5): per profile, the calls that skipped it because its price_valid_until had passed
        self.price_expired_skips: dict[str, int] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        conn: ConnCtx,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        chain: list[LlmProfile] | None = None,
    ) -> Provider:
        mode = settings.llm_mode
        cassettes = None
        if mode in ("record", "replay"):
            if settings.llm_cassette_dir is None:
                raise LlmConfigError(f"HLM_LLM_MODE={mode} needs HLM_LLM_CASSETTE_DIR")
            cassettes = CassetteStore(settings.llm_cassette_dir, redactor=Redactor.from_settings(settings))
        budget: BudgetGuard = (
            NoBudget()
            if settings.llm_budget_disabled
            else DbBudget(conn, Caps.from_settings(settings), ttl_s=settings.llm_reservation_ttl_s)
        )
        return cls(
            chain if chain is not None else profile_chain(settings),
            mode=mode,
            budget=budget,
            ledger=DbLedger(conn),
            cassettes=cassettes,
            transport=transport,
            clock=clock,
            redactor=Redactor.from_settings(settings),
            timeout_s=settings.llm_timeout_s,
            breaker_threshold=settings.llm_breaker_threshold,
            breaker_open_s=settings.llm_breaker_open_s,
            breaker_max_open_s=settings.llm_breaker_max_open_s,
            job_call_cap=settings.llm_job_call_cap,
            budget_disabled=settings.llm_budget_disabled,
        )

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    # ------------------------------------------------------------------ state
    def chain_for(self, task: str) -> list[LlmProfile]:
        """The chain a call of ``task`` uses: the primary and the task's qualified fallback
        (``HLM_FALLBACK_PROFILE__<TASK>`` when configured, else the default one; D-094)."""
        return for_task(self.chain, task)

    def breaker(self, profile: str) -> Breaker:
        """The breaker of ONE profile (keyed by its name: per model, whichever task uses it)."""
        b = self._breakers.get(profile)
        if b is None:
            b = self._breakers[profile] = Breaker(self.clock, **self._breaker_args)
        return b

    def _cut(self, profile: str) -> None:
        """D-173: one attempt of ``profile`` was cut by its attempt cap. Not a breaker failure and no
        success either; a half-open trial that is cut did not prove the profile healthy (it
        re-opens, as a failure would). ``CUT_VALVE_COUNT`` cuts within ``CUT_VALVE_WINDOW_S`` open
        the breaker (logged): a hard outage of the profile is still cut off."""
        breaker = self.breaker(profile)
        if breaker.state == "half_open":
            breaker.failure()
            return
        now = self.clock.monotonic()
        cuts = self._cuts.setdefault(profile, deque())
        cuts.append(now)
        while cuts and cuts[0] < now - CUT_VALVE_WINDOW_S:
            cuts.popleft()
        if len(cuts) >= CUT_VALVE_COUNT:
            log.warning(
                "profile %s: %d attempt-cap cuts within %.0f s: breaker opened (%.0f s)",
                profile,
                len(cuts),
                CUT_VALVE_WINDOW_S,
                breaker.window_s,
            )
            cuts.clear()
            breaker.trip()

    def breaker_state(self, task: str | None = None) -> str:
        """``closed`` unless every profile's breaker is open (``open``) or some are (``degraded``).
        Over ``task``'s chain, or (no task: the heartbeat) the default chain plus every task-specific
        fallback this process has called."""
        if task is not None:
            names = [p.name for p in self.chain_for(task)]
        else:
            names = [p.name for p in self.chain]
            names += [n for n in self._breakers if n not in names]
        states = [self.breaker(n).state for n in names]
        if all(s == "open" for s in states):
            return "open"
        if any(s != "closed" for s in states):
            return "degraded"
        return "closed"

    def retry_after_s(self, profiles: list[LlmProfile] | None = None) -> float:
        rem = [self.breaker(p.name).remaining_s() for p in (profiles if profiles is not None else self.chain)]
        return max(1.0, min(rem)) if rem else 1.0

    # ------------------------------------------------------------------ request building
    def build_body(
        self, profile: LlmProfile, task: TaskSpec, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": profile.model_id, "messages": messages}
        body.update(profile.extra)
        body["max_tokens"] = task.max_tokens
        if profile.reasoning is not None:
            body["reasoning"] = profile.reasoning
        if profile.supports_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": f"{task.name}_{task.schema_version}",
                    "strict": True,
                    "schema": task.schema,
                },
            }
        return body

    def estimate_input_tokens(self, messages: list[dict[str, str]]) -> int:
        return sum(self.meter.count_text(m["content"]) + 4 for m in messages) + 3

    # ------------------------------------------------------------------ public API
    async def complete(
        self,
        task: TaskSpec,
        user: str,
        *,
        job_id: int | None = None,
        validate: Validator | None = None,
        chain: list[LlmProfile] | None = None,
        lineage: str | None = None,
        precheck: Callable[[], Awaitable[None]] | None = None,
        deadline: float | None = None,
        attempt_policy: str = "background",
        attempt_guard: AttemptGuard | None = None,
        variant: Variant | None = None,
        observe: Callable[[dict[str, Any]], None] | None = None,
        schema_fallback: bool = False,
        attempt_affordable: AttemptAffordable | None = None,
    ) -> LlmResult:
        """``deadline`` (event-loop time): the caller's hard cap. HTTP timeouts are budgeted to end
        before it and no attempt starts without room (``DeadlineExceeded``), so an outer
        ``asyncio.timeout`` at the same deadline never has to cut an attempt mid-flight.
        ``attempt_policy``: ``background`` (retry/backoff) or ``latency`` (one bounded attempt per
        profile, then the next one; see the module doc). ``attempt_guard``: see ``AttemptGuard``.
        ``variant`` (D-178): per PROFILE of the chain, ``(user message, parser)`` to use instead of
        ``user`` and the JSON parser (a text protocol for a profile without JSON mode), or None.
        ``observe`` (D-189): a read-only callback per attempt (profile, attempt, the messages sent,
        the raw output, its outcome); it never changes the call. ``schema_fallback`` and
        ``attempt_affordable`` (R4, R-9): a schema failure moves on to the next profile (module doc)."""
        if attempt_policy not in ATTEMPT_POLICIES:
            raise LlmConfigError(f"unknown attempt_policy {attempt_policy!r}")
        token = _LINEAGE.set(lineage) if lineage is not None else None
        ptoken = _PRECHECK.set(precheck)
        dtoken = _DEADLINE.set(deadline)
        gtoken = _GUARD.set(attempt_guard)
        otoken = _OBSERVE.set(observe)
        atoken = _AFFORD.set(attempt_affordable)
        try:
            return await self._complete(
                task,
                user,
                job_id=job_id,
                validate=validate,
                chain=chain,
                latency=attempt_policy == "latency",
                variant=variant,
                schema_fallback=schema_fallback,
            )
        finally:
            _AFFORD.reset(atoken)
            _OBSERVE.reset(otoken)
            _GUARD.reset(gtoken)
            _DEADLINE.reset(dtoken)
            _PRECHECK.reset(ptoken)
            if token is not None:
                _LINEAGE.reset(token)

    async def _complete(
        self,
        task: TaskSpec,
        user: str,
        *,
        job_id: int | None,
        validate: Validator | None,
        chain: list[LlmProfile] | None,
        latency: bool = False,
        variant: Variant | None = None,
        schema_fallback: bool = False,
    ) -> LlmResult:
        if self.mode == "off":
            raise LlmDisabled("HLM_LLM_MODE=off")
        profiles = chain or self.chain_for(task.name)  # an explicit chain (the verifier's) as given
        user_redacted = self.redactor.redact(user).text
        attempted = False
        timeouts_only = True
        reasons: list[str] = []
        fallbacks: list[tuple[str, str]] = []  # R4 (R-9, R-6): every profile given up, and why
        schema_exc: SchemaFail | None = None
        tried_after_schema = False
        expired = 0
        for i, profile in enumerate(profiles):
            if self.mode in ("live", "record") and profile.price_expired():
                # R4 (R-5): past price_valid_until: never priced with stale prices (no network, counted)
                self.price_expired_skips[profile.name] = self.price_expired_skips.get(profile.name, 0) + 1
                log.warning(
                    "profile %s: price_valid_until %s passed, unusable for live calls: skipped",
                    profile.name,
                    profile.price_valid_until,
                )
                reasons.append(f"{profile.name}: price_valid_until {profile.price_valid_until} passed")
                fallbacks.append((profile.name, "price_expired"))
                expired += 1
                continue
            breaker = self.breaker(profile.name)
            if self.mode != "replay" and not breaker.allow():
                await self.ledger.record(self._row(profile, task, job_id, "breaker_open"))
                reasons.append(f"{profile.name}: breaker open")
                fallbacks.append((profile.name, "breaker_open"))
                continue
            attempted = True
            tried_after_schema = schema_exc is not None
            share = None
            if latency and any(self.breaker(p.name).available() for p in profiles[i + 1 :]):
                remaining = _remaining_s()
                if remaining is None or remaining * LATENCY_PRIMARY_SHARE >= MIN_ATTEMPT_S:
                    share = LATENCY_PRIMARY_SHARE  # leave the rest of the deadline to the next profile
            own = variant(profile) if variant is not None else None  # D-178: its protocol
            onward = schema_fallback and i < len(profiles) - 1  # R-9: a later profile may answer
            try:
                result = await self._run_profile(
                    profile,
                    task,
                    self.redactor.redact(own[0]).text if own is not None else user_redacted,
                    job_id,
                    validate,
                    latency=latency,
                    share=share,
                    parse=own[1] if own is not None else parse_json_object,
                    schema_onward=onward,
                )
            except _Exhausted as exc:
                if exc.cut:
                    self._cut(profile.name)  # D-173: tail latency, not an outage (the valve only)
                else:
                    breaker.failure()  # per PROFILE: a failing primary never closes the fallback's way
                reasons.append(f"{profile.name}: {exc}")
                timeouts_only = timeouts_only and exc.timeout
                why = (
                    BILLING_OR_QUOTA
                    if exc.billing  # D-212: an ops problem, named before the generic reasons
                    else "cut"
                    if exc.cut
                    else ("timeout" if exc.timeout else "unavailable")
                )
                if exc.billing:
                    log.warning("profile %s: provider refused for billing or quota", profile.name)
                fallbacks.append((profile.name, why))
                continue
            except SchemaFail as exc:
                breaker.success()  # the endpoint answered; the model output was the problem
                if not onward:
                    raise
                # R4 (R-9): the next profile answers instead (counted, never silent)
                schema_exc = exc
                timeouts_only = False
                reasons.append(f"{profile.name}: {exc}")
                fallbacks.append((profile.name, exc.why))
                log.warning(
                    "task %s: %s gave up on %s, next profile", task.name, profile.name, fallbacks[-1][1]
                )
                continue
            breaker.success()
            result.fallbacks = fallbacks
            return result
        if schema_exc is not None and not tried_after_schema:
            raise schema_exc  # no later profile was even tried: the model output was the problem
        if not attempted and expired == len(profiles):
            raise PriceExpired("; ".join(reasons))  # R4 (R-5): fail closed
        if not attempted:
            raise BreakerOpen("; ".join(reasons), retry_after_s=self.retry_after_s(profiles))
        if latency and timeouts_only:  # every bounded attempt ran out of time: the caller's timeout
            raise DeadlineExceeded("every profile timed out: " + "; ".join(reasons))
        raise ProviderUnavailable("; ".join(reasons), retry_after_s=self.retry_after_s(profiles))

    # ------------------------------------------------------------------ internals
    def _row(
        self, profile: LlmProfile, task: TaskSpec, job_id: int | None, outcome: str, **kw: Any
    ) -> LedgerRow:
        return LedgerRow(
            task=task.ledger_task or task.name,  # R4 (R-6): research.prose for the writer JOB
            profile=profile.name,
            model_id=profile.model_id,
            prompt_version=task.prompt_version,
            schema_version=task.schema_version,
            mode="replay" if self.mode == "replay" else self.mode,
            outcome=outcome,
            job_id=job_id,
            lineage=_LINEAGE.get(),
            **kw,
        )

    async def _run_profile(
        self,
        profile: LlmProfile,
        task: TaskSpec,
        user_redacted: str,
        job_id: int | None,
        validate: Validator | None,
        *,
        latency: bool = False,
        share: float | None = None,
        parse: Parser | None = None,
        schema_onward: bool = False,
    ) -> LlmResult:
        parse = parse or parse_json_object
        messages = [
            {"role": "system", "content": task.system_for(profile.prompt_overrides)},
            {"role": "user", "content": user_redacted},
        ]
        body = self.build_body(profile, task, messages)
        params = {k: v for k, v in body.items() if k not in ("model", "messages")}
        key = cassette_key(profile.model_id, task.prompt_version, task.schema_version, messages, params)
        input_digest = _sha(canonical(messages))
        transient = 0
        billing_seen = False  # D-212
        schema_fails = 0
        while True:
            # the schema retry is a distinct cassette entry (attempt 2); attempt 1 keeps the legacy key
            att_key = (
                key
                if schema_fails == 0
                else cassette_key(
                    profile.model_id,
                    task.prompt_version,
                    task.schema_version,
                    messages,
                    params,
                    attempt=schema_fails + 1,
                )
            )
            att = await self._attempt(
                profile, task, body, att_key, messages, params, job_id, legacy_key=key, share=share
            )
            if att.kind == "transient" and latency:  # no retry, no backoff: straight to the next profile
                reason = (
                    "cut at the profile's attempt cap" if att.cut else "transient failure (latency policy)"
                )
                raise _Exhausted(reason, timeout=att.timeout, cut=att.cut, billing=att.billing)
            if att.kind == "transient":
                transient += 1
                billing_seen = billing_seen or att.billing
                if transient >= MAX_TRANSIENT_ATTEMPTS:
                    raise _Exhausted(f"{transient} transient failures", billing=billing_seen)
                backoff = BACKOFF_S[min(transient, len(BACKOFF_S)) - 1]
                remaining = _remaining_s()
                if remaining is not None and remaining - backoff < MIN_ATTEMPT_S:
                    raise DeadlineExceeded(f"no room for a retry of task {task.name} before the deadline")
                await self.clock.sleep(backoff)
                continue
            if att.kind == "fatal":
                raise _Exhausted("non-retryable HTTP error", fatal=True, billing=att.billing)
            assert att.row is not None
            obj, err = parse(att.content)
            if obj is not None:
                err = task.schema_errors(obj) or (validate(obj) if validate else None)
            if err is None and schema_onward and att.finish == "length":
                # R4 (R-9): a cut-off answer is not kept even when it parses (a text layout does)
                err = "truncated: finish_reason length"
            if _OBSERVE.get() is not None:  # D-189: diagnostics only
                _emit(
                    {
                        "profile": profile.name,
                        "model": profile.model_id,
                        "attempt": schema_fails + 1,
                        "outcome": ("ok" if schema_fails == 0 else "schema_retry_ok")
                        if err is None
                        else "schema_fail",
                        "error": err,
                        "system_sha256": _sha(messages[0]["content"].encode("utf-8")),
                        "user": messages[1]["content"],
                        "content": att.content,
                        "latency_ms": att.latency_ms,
                        "usage": dict(att.usage or {}),
                    }
                )
            if err is None:
                att.row.outcome = "ok" if schema_fails == 0 else "schema_retry_ok"
                await _finalize(self.ledger.record(att.row))
                assert obj is not None
                return LlmResult(
                    output=obj,
                    task=task.name,
                    profile=profile.name,
                    model_id=profile.model_id,
                    prompt_version=task.prompt_version,
                    schema_version=task.schema_version,
                    input_digest=input_digest,
                    cost_usd=att.row.cost_usd,
                    latency_ms=att.latency_ms,
                    usage=att.usage or {},
                )
            att.row.outcome = "schema_fail"
            await _finalize(self.ledger.record(att.row))
            schema_fails += 1
            if schema_onward:  # R4 (R-9): a later profile answers instead of a doubtful retry
                why = None
                if att.finish == "length":
                    why = "truncated"  # the same max_tokens would truncate again
                elif schema_fails >= 2:
                    why = "schema_fail"
                elif not await self._affordable(profile, task, messages):
                    why = "retry_unaffordable"  # never reserved
                if why is not None:
                    raise SchemaFail(f"E_SCHEMA_FAIL task={task.name} ({why})", why=why)
                continue
            if schema_fails >= 2:
                raise SchemaFail(f"E_SCHEMA_FAIL task={task.name}")  # never the model output

    async def _claim_call(self, job_id: int | None) -> None:
        """Atomically claim one provider attempt for the loop LINEAGE (a re-enqueued job inherits
        its parent's; a job without one counts on its own id). The counter row is incremented only
        while below the ceiling (one statement, row-locked), so concurrent calls on one lineage can
        never exceed it (Sol 37 #7)."""
        lineage = _LINEAGE.get()
        if lineage is None and job_id is not None:
            lineage = str(uuid.uuid5(uuid.NAMESPACE_URL, f"hlm-job-id:{job_id}"))
        if lineage is not None and not await self.ledger.claim(lineage, self.job_call_cap):
            raise JobCallCapExceeded(f"E_CALL_CAP lineage reached {self.job_call_cap} calls")

    async def _run_guard(self, profile: LlmProfile, task: TaskSpec, messages: list[dict[str, str]]) -> None:
        """The caller's per-attempt guard (``AttemptGuard``), with THIS attempt's worst case priced by
        THIS profile (whether or not the global spend guard is enabled)."""
        guard = _GUARD.get()
        if guard is None:
            return
        tokens_in = self.estimate_input_tokens(messages)
        worst = profile.worst_usd(tokens_in, task.max_tokens) if profile.priced else Decimal(0)
        await guard(profile, worst, -(-tokens_in * 11 // 10) + task.max_tokens)

    async def _affordable(self, profile: LlmProfile, task: TaskSpec, messages: list[dict[str, str]]) -> bool:
        """R4 (R-9): the caller's ``attempt_affordable`` for one more attempt of ``profile`` (its worst
        case, priced by this profile); True when the caller has none."""
        check = _AFFORD.get()
        if check is None:
            return True
        tokens_in = self.estimate_input_tokens(messages)
        worst = profile.worst_usd(tokens_in, task.max_tokens) if profile.priced else Decimal(0)
        return bool(await check(profile, worst, -(-tokens_in * 11 // 10) + task.max_tokens))

    async def _run_precheck(self) -> None:
        """The caller's privacy/authority gate, re-run before EVERY attempt (retries after backoff
        and the fallback profile included): it raises to abort before any byte is sent."""
        check = _PRECHECK.get()
        if check is not None:
            await check()

    async def _attempt(
        self,
        profile: LlmProfile,
        task: TaskSpec,
        body: dict[str, Any],
        key: str,
        messages: list[dict[str, str]],
        params: dict[str, Any],
        job_id: int | None,
        legacy_key: str | None = None,
        share: float | None = None,
    ) -> _Attempt:
        """One network attempt (or its replay). ``share``: the fraction of the remaining deadline
        this attempt may use (``latency`` policy while a later profile is available)."""
        request_sha = _sha(canonical(body))
        await self._run_guard(profile, task, messages)  # review 79 T4: before every attempt
        if self.mode == "replay":  # stands in for the HTTP attempt: same gate and ceiling
            await self._run_precheck()
            await self._claim_call(job_id)
            assert self.cassettes is not None
            if legacy_key is not None and key != legacy_key and not self.cassettes.has(key):
                key = legacy_key  # a cassette recorded before retries were keyed: legacy behaviour
            data = self.cassettes.get(key)  # CassetteMiss propagates: strict replay
            return self._response(profile, task, job_id, data, request_sha, Decimal(0), 0, replay=True)

        worst = Decimal(0)
        if not self.budget_disabled:
            if not profile.priced:
                raise LlmConfigError(f"profile {profile.name!r} has no prices; live calls refused")
            worst = profile.worst_usd(self.estimate_input_tokens(messages), task.max_tokens)
        remaining = _remaining_s()
        if remaining is not None and remaining < MIN_ATTEMPT_S:  # before any reservation
            raise DeadlineExceeded(f"no room for an attempt of task {task.name} before the deadline")
        call_id = uuid.uuid4()
        if not await self.budget.reserve(call_id, worst, job_id):
            await self.ledger.record(
                self._row(
                    profile, task, job_id, "budget_deferred", call_id=call_id, request_sha256=request_sha
                )
            )
            raise BudgetDeferred(f"reservation of ${worst} refused for task {task.name}")
        # D-062: the privacy/authority precheck runs (and commits) immediately before the send;
        # the lineage slot is claimed only for an attempt that really goes out (Sol 38 #5):
        # budget denials, an open breaker and privacy denials never consume the ceiling. An
        # attempt in flight when a revocation commits may complete; its result is never applied
        # (the apply transaction re-checks under FOR SHARE and records authority_lost).
        try:
            await self._run_precheck()
            await self._claim_call(job_id)
            cap = profile.attempt_timeout_s  # D-172: a per-role cap (the research writer)
            http_timeout = self.timeout_s if cap is None else min(self.timeout_s, cap)
            remaining = _remaining_s()
            if remaining is not None:  # the prechecks used time: budget this request to the deadline
                if remaining < MIN_ATTEMPT_S:
                    raise DeadlineExceeded(f"prechecks left no room for task {task.name}")
                budget_s = remaining * share if share is not None else remaining
                if cap is not None:  # its own cap replaces the share (the fallback gets the rest)
                    budget_s = min(cap, remaining)
                http_timeout = min(http_timeout, max(budget_s, MIN_ATTEMPT_S))
        except BaseException:
            # nothing was sent: release the reservation (to completion, even when cancelled)
            await _finalize(self.budget.settle(call_id, Decimal(0)))
            raise

        t0 = time.perf_counter()
        try:
            # D-172: a capped profile's attempt also ends on the WALL clock (an HTTP read timeout
            # restarts with every byte, e.g. a keep-alive trickle); that is this attempt's timeout
            async with asyncio.timeout(http_timeout if profile.attempt_timeout_s is not None else None):
                resp = await self._client(profile).post(
                    "/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {profile.api_key or ''}"},
                    timeout=http_timeout,
                )
        except asyncio.CancelledError:
            # cut mid-flight by the caller: billing unknown -> worst case; the ledger row is written.
            # Cut AT the caller's deadline, the profile did not answer in time: a failure of THIS
            # profile's breaker (per profile, never the whole task; a disconnect is not counted)
            deadline = _DEADLINE.get()
            if deadline is not None and asyncio.get_running_loop().time() >= deadline - 0.05:
                self.breaker(profile.name).failure()

            async def abandoned() -> None:
                await self.budget.settle(call_id, None)
                await self.ledger.record(
                    self._row(
                        profile,
                        task,
                        job_id,
                        "timeout",
                        call_id=call_id,
                        request_sha256=request_sha,
                        reserved_usd=worst,
                        cost_usd=worst,
                        latency_ms=_ms(t0),
                    )
                )

            await _finalize(abandoned())
            raise
        except (httpx.TimeoutException, TimeoutError) as exc:
            # unknown whether billed: charge the worst case
            await _finalize(self.budget.settle(call_id, None))
            await _finalize(
                self.ledger.record(
                    self._row(
                        profile,
                        task,
                        job_id,
                        "timeout",
                        call_id=call_id,
                        request_sha256=request_sha,
                        reserved_usd=worst,
                        cost_usd=worst,
                        latency_ms=_ms(t0),
                    )
                )
            )
            # D-173: a capped profile's read/wall-clock timeout is a cut; a connect or pool timeout
            # (the endpoint unreachable) stays a real failure
            cut = profile.attempt_timeout_s is not None and not isinstance(
                exc, httpx.ConnectTimeout | httpx.PoolTimeout
            )
            return _Attempt("transient", timeout=True, cut=cut)
        except httpx.TransportError:
            # the request may have reached the provider: billing is uncertain -> worst case
            await _finalize(self.budget.settle(call_id, None))
            await _finalize(
                self.ledger.record(
                    self._row(
                        profile,
                        task,
                        job_id,
                        "http_error",
                        call_id=call_id,
                        request_sha256=request_sha,
                        reserved_usd=worst,
                        cost_usd=worst,
                        latency_ms=_ms(t0),
                    )
                )
            )
            return _Attempt("transient")
        latency = _ms(t0)
        raw_bytes = resp.content
        data: dict[str, Any] | None = None
        if resp.status_code == 200:
            try:
                parsed = resp.json()
                data = parsed if isinstance(parsed, dict) else None
            except ValueError:
                data = None
        choice = ((data or {}).get("choices") or [{}])[0] or {}
        provider_error = data is not None and (
            (data.get("error") and not data.get("choices")) or choice.get("finish_reason") == "error"
        )
        if resp.status_code != 200 or data is None or provider_error:
            # Only a 4xx is a definitive no-charge answer (rejected before generation). A 5xx,
            # an unparseable 200 or a mid-generation provider error may have been billed.
            no_charge = 400 <= resp.status_code < 500
            charged = Decimal(0) if no_charge else worst
            err_status = resp.status_code
            if err_status == 200 and isinstance((data or {}).get("error"), dict):
                code = data["error"].get("code")  # a 200 whose body is the provider's error
                err_status = code if isinstance(code, int) else err_status
            billing = is_billing_or_quota(
                err_status,
                data if data is not None else raw_bytes,
                resp.headers.get("retry-after"),
            )
            await _finalize(self.budget.settle(call_id, charged))
            await self.ledger.record(
                self._row(
                    profile,
                    task,
                    job_id,
                    BILLING_OR_QUOTA if billing else "http_error",
                    call_id=call_id,
                    request_sha256=request_sha,
                    response_sha256=_sha(raw_bytes),
                    reserved_usd=worst,
                    cost_usd=charged,
                    latency_ms=latency,
                )
            )
            transient = resp.status_code in TRANSIENT_STATUS or resp.status_code == 200
            return _Attempt("transient" if transient else "fatal", billing=billing)

        if self.mode == "record":
            assert self.cassettes is not None
            self.cassettes.put(
                key,
                task=task.name,
                model_id=profile.model_id,
                prompt_version=task.prompt_version,
                schema_version=task.schema_version,
                messages=messages,
                params=params,
                response=self._redacted_response(data),
            )
        usage = data.get("usage") or {}
        norm = normalize_usage(usage, reasoning=profile.usage_reasoning, max_tokens=task.max_tokens)
        actual = self._actual_cost(profile, usage, norm)
        await _finalize(self.budget.settle(call_id, actual))
        att = self._response(
            profile, task, job_id, data, request_sha, worst, latency, replay=False, raw=raw_bytes
        )
        assert att.row is not None
        att.row.call_id = call_id
        att.row.cost_usd = worst if actual is None else actual
        return att

    def _redacted_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """Record mode: any content shape is normalized to text and redacted before persisting."""
        return sanitize_response(data, redact=self.redactor.text)

    def _actual_cost(
        self, profile: LlmProfile, usage: dict[str, Any], norm: NormalizedUsage | None = None
    ) -> Decimal | None:
        """The attempt's USD: ``usage.cost`` when reported (the authority), else the NORMALIZED tokens
        priced by the profile; None (settle at the worst case) when the usage is unreliable."""
        cost = usage.get("cost")
        if isinstance(cost, int | float) and not isinstance(cost, bool) and cost >= 0:
            return Decimal(str(cost))
        norm = norm or normalize_usage(usage, reasoning=profile.usage_reasoning)
        if norm.reliable and profile.priced:  # reliable: both counts are known
            return profile.cost_usd(int(norm.input_tokens or 0), int(norm.output_tokens or 0))
        if not norm.reliable:
            log.warning("profile %s: usage %s, settled at the worst case", profile.name, norm.basis)
        return None  # unknown: settle as worst case

    def _response(
        self,
        profile: LlmProfile,
        task: TaskSpec,
        job_id: int | None,
        data: dict[str, Any],
        request_sha: str,
        worst: Decimal,
        latency: int,
        *,
        replay: bool,
        raw: bytes | None = None,
    ) -> _Attempt:
        choice = (data.get("choices") or [{}])[0] or {}
        msg = choice.get("message") or {}
        content = normalize_content(msg if isinstance(msg, dict) else {"content": msg})
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        norm = normalize_usage(usage, reasoning=profile.usage_reasoning, max_tokens=task.max_tokens)
        row = self._row(
            profile,
            task,
            job_id,
            "ok",
            request_sha256=request_sha,
            response_sha256=_sha(raw if raw is not None else canonical(data)),
            input_tokens=norm.input_tokens,
            cached_input_tokens=details.get("cached_tokens"),
            output_tokens=norm.output_tokens,  # B1: thinking included, the worst case when unreliable
            reserved_usd=worst,
            latency_ms=latency,
        )
        finish = choice.get("finish_reason")
        return _Attempt(
            "response",
            content=content,
            row=row,
            usage=usage,
            latency_ms=latency,
            finish=finish if isinstance(finish, str) else None,
        )

    def _client(self, profile: LlmProfile) -> httpx.AsyncClient:
        client = self._clients.get(profile.name)
        if client is None:
            client = httpx.AsyncClient(
                base_url=profile.base_url,
                transport=self.transport,
                timeout=self.timeout_s,
                follow_redirects=False,
            )
            self._clients[profile.name] = client
        return client


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


__all__ = [
    "ATTEMPT_POLICIES",
    "BACKOFF_S",
    "ChainBreakers",
    "LATENCY_PRIMARY_SHARE",
    "MAX_TRANSIENT_ATTEMPTS",
    "Breaker",
    "NormalizedUsage",
    "normalize_usage",
    "Clock",
    "LlmResult",
    "AttemptAffordable",
    "AttemptGuard",
    "Provider",
    "lineage_scope",
    "parse_json_object",
]
