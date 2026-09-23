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
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from hlmemo.core.budget import Meter
from hlmemo.librarian.budget import BudgetGuard, Caps, ConnCtx, DbBudget, NoBudget
from hlmemo.librarian.cassette import CassetteStore, canonical, cassette_key
from hlmemo.librarian.errors import (
    BreakerOpen,
    BudgetDeferred,
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
            cassettes = CassetteStore(settings.llm_cassette_dir)
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
            att = await self._attempt(profile, task, body, key, messages, params, job_id)
            if att.kind == "transient":
                transient += 1
                if transient >= MAX_TRANSIENT_ATTEMPTS:
                    raise _Exhausted(f"{transient} transient failures")
                await self.clock.sleep(BACKOFF_S[min(transient, len(BACKOFF_S)) - 1])
                continue
            if att.kind == "fatal":
                raise _Exhausted("non-retryable HTTP error", fatal=True)
            assert att.row is not None
            obj, err = parse_json_object(att.content)
            if obj is not None:
                err = task.schema_errors(obj) or (validate(obj) if validate else None)
            if err is None:
                att.row.outcome = "ok" if schema_fails == 0 else "schema_retry_ok"
                await self.ledger.record(att.row)
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
            await self.ledger.record(att.row)
            schema_fails += 1
            if schema_fails >= 2:
                raise SchemaFail(f"{task.name}: {err}")

    async def _check_job_cap(self, job_id: int | None) -> None:
        if job_id is not None and await self.ledger.job_calls(job_id) >= self.job_call_cap:
            raise JobCallCapExceeded(f"job {job_id} reached {self.job_call_cap} provider calls")

    async def _attempt(
        self,
        profile: LlmProfile,
        task: TaskSpec,
        body: dict[str, Any],
        key: str,
        messages: list[dict[str, str]],
        params: dict[str, Any],
        job_id: int | None,
    ) -> _Attempt:
        await self._check_job_cap(job_id)
        request_sha = _sha(canonical(body))
        if self.mode == "replay":
            assert self.cassettes is not None
            data = self.cassettes.get(key)  # CassetteMiss propagates: strict replay
            return self._response(profile, task, job_id, data, request_sha, Decimal(0), 0, replay=True)

        worst = Decimal(0)
        if not self.budget_disabled:
            if not profile.priced:
                raise LlmConfigError(f"profile {profile.name!r} has no prices; live calls refused")
            worst = profile.worst_usd(self.estimate_input_tokens(messages), task.max_tokens)
        call_id = uuid.uuid4()
        if not await self.budget.reserve(call_id, worst, job_id):
            await self.ledger.record(
                self._row(
                    profile, task, job_id, "budget_deferred", call_id=call_id, request_sha256=request_sha
                )
            )
            raise BudgetDeferred(f"reservation of ${worst} refused for task {task.name}")

        t0 = time.perf_counter()
        try:
            resp = await self._client(profile).post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {profile.api_key or ''}"},
                timeout=self.timeout_s,
            )
        except httpx.TimeoutException:
            await self.budget.settle(call_id, None)  # unknown whether billed: charge the worst case
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
            return _Attempt("transient")
        except httpx.TransportError:
            await self.budget.settle(call_id, Decimal(0))
            await self.ledger.record(
                self._row(
                    profile,
                    task,
                    job_id,
                    "http_error",
                    call_id=call_id,
                    request_sha256=request_sha,
                    reserved_usd=worst,
                    latency_ms=_ms(t0),
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
            await self.budget.settle(call_id, Decimal(0))
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
                response=data,
            )
        usage = data.get("usage") or {}
        actual = self._actual_cost(profile, usage)
        await self.budget.settle(call_id, actual)
        att = self._response(
            profile, task, job_id, data, request_sha, worst, latency, replay=False, raw=raw_bytes
        )
        assert att.row is not None
        att.row.call_id = call_id
        att.row.cost_usd = worst if actual is None else actual
        return att

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
        content = (choice.get("message") or {}).get("content")
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
    "parse_json_object",
]
