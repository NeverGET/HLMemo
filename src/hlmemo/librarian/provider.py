"""OpenAI-compatible librarian provider (W2a; D-017, D-019, CC-5).

``Provider.complete(task, user)`` sends ``[system prompt, redacted user message]`` to the profile
chain ``[primary, fallback?]`` and returns schema-valid JSON. Per profile:

* every request sets ``max_tokens`` from the task bound; ``response_format`` is ``json_object``
  from the profile's ``extra``, or ``json_schema`` when the profile declares support;
* transient failures (HTTP 408/429/5xx, timeouts, transport errors, a provider ``finish_reason``
  of ``error``) are retried with backoff 1/2/4/8 s, at most 5 attempts;
* a schema-invalid answer (unparseable, not an object, schema or task-level violation) is retried
  once; a second one raises ``SchemaFail`` (no fallback: the provider is up, the model is wrong);
* an exhausted or fatally failing profile falls through to the fallback profile once;
* a per-profile circuit breaker opens after N consecutive exhausted calls for 60 s, doubling on
  each failed half-open trial up to 15 min; an open breaker costs a ``breaker_open`` ledger row
  and no network;
* before every network attempt the worst case is reserved atomically (``budget``) and the
  per-job call ceiling is checked; after it the reservation is settled at the actual cost.

Every attempt writes exactly one ``llm_calls`` row. ``HLM_LLM_MODE`` selects live / record /
replay (strict cassettes) / off. The raw provider response is never stored anywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
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
    ProviderUnavailable,
    SchemaFail,
)
from hlmemo.librarian.ledger import DbLedger, Ledger, LedgerRow
from hlmemo.librarian.profiles import LlmProfile, profile_chain
from hlmemo.librarian.prompts import TaskSpec
from hlmemo.librarian.redact import Redactor

BACKOFF_S = (1, 2, 4, 8)
MAX_TRANSIENT_ATTEMPTS = 5
TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})
_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$", re.S)

Validator = Callable[[dict[str, Any]], str | None]
#: loop lineage of the call being made (per asyncio task; concurrent calls never share it)
_LINEAGE: contextvars.ContextVar[str | None] = contextvars.ContextVar("hlm_llm_lineage", default=None)
#: privacy/authority re-check run before every network attempt of the current call (Sol 37 #1)
_PRECHECK: contextvars.ContextVar[Callable[[], Awaitable[None]] | None] = contextvars.ContextVar(
    "hlm_llm_precheck", default=None
)
#: the caller's deadline on the event-loop clock (``complete(deadline=)``, e.g. the API's 4 s risk
#: judge cap): each HTTP timeout is budgeted to end DEADLINE_MARGIN_S before it, and no attempt,
#: reservation or backoff starts without MIN_ATTEMPT_S of room (DeadlineExceeded instead)
_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("hlm_llm_deadline", default=None)
DEADLINE_MARGIN_S = 0.3
MIN_ATTEMPT_S = 0.2


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

    def _open(self) -> None:
        self.state = "open"
        self.open_until = self.clock.monotonic() + self.window_s
        self._trial = False


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
    def __init__(self, reason: str, *, fatal: bool = False) -> None:
        super().__init__(reason)
        self.fatal = fatal


@dataclass(slots=True)
class _Attempt:
    kind: str  # "response" | "transient" | "fatal"
    content: str | None = None
    row: LedgerRow | None = None
    usage: dict[str, Any] | None = None
    latency_ms: int = 0


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
    def breaker(self, profile: str) -> Breaker:
        b = self._breakers.get(profile)
        if b is None:
            b = self._breakers[profile] = Breaker(self.clock, **self._breaker_args)
        return b

    def breaker_state(self) -> str:
        """``closed`` unless every profile's breaker is open (``open``) or some are (``degraded``)."""
        states = [self.breaker(p.name).state for p in self.chain]
        if all(s == "open" for s in states):
            return "open"
        if any(s != "closed" for s in states):
            return "degraded"
        return "closed"

    def retry_after_s(self) -> float:
        rem = [self.breaker(p.name).remaining_s() for p in self.chain]
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
    ) -> LlmResult:
        """``deadline`` (event-loop time): the caller's hard cap. HTTP timeouts are budgeted to end
        before it and no attempt starts without room (``DeadlineExceeded``), so an outer
        ``asyncio.timeout`` at the same deadline never has to cut an attempt mid-flight."""
        token = _LINEAGE.set(lineage) if lineage is not None else None
        ptoken = _PRECHECK.set(precheck)
        dtoken = _DEADLINE.set(deadline)
        try:
            return await self._complete(task, user, job_id=job_id, validate=validate, chain=chain)
        finally:
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
    ) -> LlmResult:
        if self.mode == "off":
            raise LlmDisabled("HLM_LLM_MODE=off")
        profiles = chain or self.chain
        user_redacted = self.redactor.redact(user).text
        attempted = False
        reasons: list[str] = []
        for profile in profiles:
            breaker = self.breaker(profile.name)
            if self.mode != "replay" and not breaker.allow():
                await self.ledger.record(self._row(profile, task, job_id, "breaker_open"))
                reasons.append(f"{profile.name}: breaker open")
                continue
            attempted = True
            try:
                result = await self._run_profile(profile, task, user_redacted, job_id, validate)
            except _Exhausted as exc:
                breaker.failure()
                reasons.append(f"{profile.name}: {exc}")
                continue
            except SchemaFail:
                breaker.success()  # the endpoint answered; the model output was the problem
                raise
            breaker.success()
            return result
        if not attempted:
            raise BreakerOpen("; ".join(reasons), retry_after_s=self.retry_after_s())
        raise ProviderUnavailable("; ".join(reasons), retry_after_s=self.retry_after_s())

    # ------------------------------------------------------------------ internals
    def _row(
        self, profile: LlmProfile, task: TaskSpec, job_id: int | None, outcome: str, **kw: Any
    ) -> LedgerRow:
        return LedgerRow(
            task=task.name,
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
    ) -> LlmResult:
        messages = [
            {"role": "system", "content": task.system_for(profile.prompt_overrides)},
            {"role": "user", "content": user_redacted},
        ]
        body = self.build_body(profile, task, messages)
        params = {k: v for k, v in body.items() if k not in ("model", "messages")}
        key = cassette_key(profile.model_id, task.prompt_version, task.schema_version, messages, params)
        input_digest = _sha(canonical(messages))
        transient = 0
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
            att = await self._attempt(profile, task, body, att_key, messages, params, job_id, legacy_key=key)
            if att.kind == "transient":
                transient += 1
                if transient >= MAX_TRANSIENT_ATTEMPTS:
                    raise _Exhausted(f"{transient} transient failures")
                backoff = BACKOFF_S[min(transient, len(BACKOFF_S)) - 1]
                remaining = _remaining_s()
                if remaining is not None and remaining - backoff < MIN_ATTEMPT_S:
                    raise DeadlineExceeded(f"no room for a retry of task {task.name} before the deadline")
                await self.clock.sleep(backoff)
                continue
            if att.kind == "fatal":
                raise _Exhausted("non-retryable HTTP error", fatal=True)
            assert att.row is not None
            obj, err = parse_json_object(att.content)
            if obj is not None:
                err = task.schema_errors(obj) or (validate(obj) if validate else None)
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
    ) -> _Attempt:
        request_sha = _sha(canonical(body))
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
            http_timeout = self.timeout_s
            remaining = _remaining_s()
            if remaining is not None:  # the prechecks used time: budget this request to the deadline
                if remaining < MIN_ATTEMPT_S:
                    raise DeadlineExceeded(f"prechecks left no room for task {task.name}")
                http_timeout = min(self.timeout_s, remaining)
        except BaseException:
            # nothing was sent: release the reservation (to completion, even when cancelled)
            await _finalize(self.budget.settle(call_id, Decimal(0)))
            raise

        t0 = time.perf_counter()
        try:
            resp = await self._client(profile).post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {profile.api_key or ''}"},
                timeout=http_timeout,
            )
        except asyncio.CancelledError:
            # cut mid-flight by the caller: billing unknown -> worst case; the ledger row is written
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
        except httpx.TimeoutException:
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
            return _Attempt("transient")
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
            await _finalize(self.budget.settle(call_id, charged))
            await self.ledger.record(
                self._row(
                    profile,
                    task,
                    job_id,
                    "http_error",
                    call_id=call_id,
                    request_sha256=request_sha,
                    response_sha256=_sha(raw_bytes),
                    reserved_usd=worst,
                    cost_usd=charged,
                    latency_ms=latency,
                )
            )
            transient = resp.status_code in TRANSIENT_STATUS or resp.status_code == 200
            return _Attempt("transient" if transient else "fatal")

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
        actual = self._actual_cost(profile, usage)
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

    def _actual_cost(self, profile: LlmProfile, usage: dict[str, Any]) -> Decimal | None:
        cost = usage.get("cost")
        if isinstance(cost, int | float) and not isinstance(cost, bool):
            return Decimal(str(cost))
        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if isinstance(pt, int) and isinstance(ct, int) and profile.priced:
            return profile.cost_usd(pt, ct)
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
        row = self._row(
            profile,
            task,
            job_id,
            "ok",
            request_sha256=request_sha,
            response_sha256=_sha(raw if raw is not None else canonical(data)),
            input_tokens=usage.get("prompt_tokens"),
            cached_input_tokens=details.get("cached_tokens"),
            output_tokens=usage.get("completion_tokens"),
            reserved_usd=worst,
            latency_ms=latency,
        )
        return _Attempt("response", content=content, row=row, usage=usage, latency_ms=latency)

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
    "BACKOFF_S",
    "MAX_TRANSIENT_ATTEMPTS",
    "Breaker",
    "Clock",
    "LlmResult",
    "Provider",
    "lineage_scope",
    "parse_json_object",
]
