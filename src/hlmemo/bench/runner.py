"""Run bench items through the PRODUCTION librarian path (W2f).

Every call goes through ``hlmemo.librarian.provider.Provider``: the profile's request shape, the
redactor (applied to every user message), JSON parsing + schema validation with the production
retry-once rule, transient backoff, the per-call lineage ceiling and, before every network attempt,
the atomic worst-case reservation of the budget guard. A reservation the guard refuses is the
``--max-usd`` cap: the run stops scheduling calls and reports ``budget_deferred`` (G-B2).

Each item gets its own ``job_id`` so the ledger attributes every attempt (and its cost) to one
bench call. Results are returned in item order, independent of completion order, so a replayed run
produces a byte-identical report (G-B1).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from hlmemo.librarian.budget import BudgetGuard
from hlmemo.librarian.errors import BudgetDeferred, CassetteMiss, LibrarianError, SchemaFail
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.prompts import TaskSpec
from hlmemo.librarian.provider import Provider

Validator = Callable[[dict[str, Any]], str | None]
Scorer = Callable[[dict[str, Any]], tuple[float, dict[str, Any]]]


@dataclass(slots=True)
class Item:
    """One planned bench call (a case × a repetition)."""

    suite: str  # "v1" | "v2"
    task: str  # v1: production task name; v2: family T5..T12
    job: str  # wire job name (the JOB line)
    case_id: str
    tier: str | None
    pack: str  # "v1" | "public" | "private"
    rep: int
    spec: TaskSpec
    user: str
    validate: Validator | None
    scorer: Scorer


@dataclass(slots=True)
class Outcome:
    item: Item
    status: str  # ok | json_fail | infra_error | budget_deferred | not_run
    score: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] | None = None
    latency_ms: int | None = None
    cost_usd: Decimal = Decimal(0)
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    outcomes: list[str] = field(default_factory=list)
    first_json_fail: bool = False
    error: str | None = None

    @property
    def scored(self) -> bool:
        """Counts toward accuracy (a model answer or a final JSON failure; infra and cap do not)."""
        return self.status in ("ok", "json_fail")


class ChainBudget:
    """Reserve against every guard in order (e.g. the run's ``--max-usd`` in-process cap, then the
    deployment's Postgres hour/day/month windows); a refusal releases the earlier reservations."""

    def __init__(self, guards: list[BudgetGuard]) -> None:
        self.guards = guards

    async def reserve(self, call_id: uuid.UUID, worst_usd: Decimal, job_id: int | None) -> bool:
        done: list[BudgetGuard] = []
        for g in self.guards:
            if not await g.reserve(call_id, worst_usd, job_id):
                for d in done:
                    await d.settle(call_id, Decimal(0))
                return False
            done.append(g)
        return True

    async def settle(self, call_id: uuid.UUID, actual_usd: Decimal | None) -> None:
        for g in self.guards:
            await g.settle(call_id, actual_usd)


class RunAborted(RuntimeError):
    """A strict-replay miss (or another run-fatal condition): the report would be meaningless."""


async def run_items(
    items: list[Item],
    provider: Provider,
    ledger: MemoryLedger,
    *,
    concurrency: int = 4,
    progress: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    """Run ``items`` with at most ``concurrency`` calls in flight. After the first refused
    reservation no further call starts (the cap is a hard stop, never a skip-and-continue)."""
    results: list[Outcome | None] = [None] * len(items)
    stop = asyncio.Event()
    sem = asyncio.Semaphore(max(1, concurrency))
    fatal: list[BaseException] = []

    async def one(idx: int, it: Item) -> None:
        async with sem:
            if stop.is_set():
                results[idx] = Outcome(it, "not_run", error="stopped: budget cap reached")
                return
            out = await _call(idx + 1, it, provider, ledger)
            if out.status == "budget_deferred":
                stop.set()
            results[idx] = out
            if progress is not None:
                progress(out)

    async def guarded(idx: int, it: Item) -> None:
        try:
            await one(idx, it)
        except RunAborted as exc:
            fatal.append(exc)
            stop.set()
            results[idx] = Outcome(it, "not_run", error=str(exc))

    await asyncio.gather(*(guarded(i, it) for i, it in enumerate(items)))
    if fatal:
        raise fatal[0]
    return [r if r is not None else Outcome(items[i], "not_run") for i, r in enumerate(results)]


async def _call(job_id: int, it: Item, provider: Provider, ledger: MemoryLedger) -> Outcome:
    out = Outcome(it, "ok")
    res = None
    try:
        res = await provider.complete(it.spec, it.user, job_id=job_id, validate=it.validate)
    except BudgetDeferred as exc:
        out.status, out.error = "budget_deferred", str(exc)[:200]
    except SchemaFail as exc:
        out.status, out.error, out.score = "json_fail", str(exc)[:200], 0.0
    except CassetteMiss as exc:
        raise RunAborted(f"strict replay miss for {it.task}/{it.case_id} rep{it.rep}: {exc}") from exc
    except LibrarianError as exc:
        out.status, out.error = "infra_error", f"{type(exc).__name__}: {str(exc)[:200]}"
    rows = [r for r in ledger.rows if r.job_id == job_id]
    out.outcomes = [r.outcome for r in rows]
    out.attempts = sum(1 for r in rows if r.outcome not in ("budget_deferred", "breaker_open"))
    out.first_json_fail = "schema_fail" in out.outcomes
    out.cost_usd = sum((r.cost_usd for r in rows), Decimal(0))
    if res is not None:
        out.output = res.output
        out.latency_ms = res.latency_ms
        out.usage = dict(res.usage)
        if provider.mode == "replay":  # replay settles nothing: use the recorded usage cost
            cost = res.usage.get("cost")
            out.cost_usd = Decimal(str(cost)) if isinstance(cost, int | float) else Decimal(0)
        out.score, out.detail = it.scorer(res.output)
    elif out.status == "json_fail" and rows:
        out.latency_ms = rows[-1].latency_ms
    return out


__all__ = ["ChainBudget", "Item", "Outcome", "RunAborted", "run_items"]
