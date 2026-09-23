"""Atomic worst-case cost reservation (W2a, Sol #7; D-058: a runaway guard, not a budget).

Before each provider call the caller reserves ``worst = ceil(input × 1.10) × price_in +
max_tokens × price_out`` against three windows (``hour``, ``day``, ``month``, UTC). One short
transaction locks the window rows in a fixed order (hour → day → month) and runs
``UPDATE … SET reserved_usd = reserved_usd + :worst WHERE spent_usd + reserved_usd + :worst <=
cap_usd`` on each; if any affects 0 rows the transaction rolls back and the call is not made
(``budget_deferred``). After the call ``settle`` moves the reservation to ``spent`` at the actual
cost (worst when unknown). ``sweep`` settles reservations older than their ``expires_at`` AS
WORST-CASE SPENT (a crash between reserve and settle is never free). Caps come from settings each
time a row is touched, so a changed cap applies to the current window immediately.

``MemoryBudget`` is the same contract in process memory (the live-gate runner's ``MAX_USD``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from psycopg import AsyncConnection

Q = Decimal("0.00000001")
PERIODS = ("hour", "day", "month")

ConnCtx = Callable[[], AbstractAsyncContextManager[AsyncConnection]]


def q8(v: Decimal | float | int) -> Decimal:
    return Decimal(str(v)).quantize(Q)


class BudgetGuard(Protocol):
    async def reserve(self, call_id: uuid.UUID, worst_usd: Decimal, job_id: int | None) -> bool: ...

    async def settle(self, call_id: uuid.UUID, actual_usd: Decimal | None) -> None: ...


@dataclass(frozen=True, slots=True)
class Caps:
    hour: Decimal
    day: Decimal
    month: Decimal

    @classmethod
    def from_settings(cls, settings: Any) -> Caps:
        return cls(
            q8(settings.llm_budget_hour_usd),
            q8(settings.llm_budget_day_usd),
            q8(settings.llm_budget_month_usd),
        )

    def of(self, period: str) -> Decimal:
        return getattr(self, period)


class DbBudget:
    """``llm_budget`` + ``llm_reservations``. ``conn`` yields a connection for one transaction."""

    def __init__(self, conn: ConnCtx, caps: Caps, *, ttl_s: int = 600) -> None:
        self._conn = conn
        self.caps = caps
        self.ttl_s = ttl_s

    async def reserve(self, call_id: uuid.UUID, worst_usd: Decimal, job_id: int | None) -> bool:
        worst = q8(worst_usd)
        async with self._conn() as conn:
            try:
                async with conn.transaction():
                    cur = await conn.execute(
                        "SELECT date_trunc('hour', now()), date_trunc('day', now()),"
                        " date_trunc('month', now())"
                    )
                    starts = dict(zip(PERIODS, await cur.fetchone(), strict=True))
                    for period in PERIODS:  # fixed lock order: hour -> day -> month
                        await conn.execute(
                            "INSERT INTO llm_budget (period_kind, period_start, cap_usd) VALUES (%s, %s, %s)"
                            " ON CONFLICT (period_kind, period_start)"
                            " DO UPDATE SET cap_usd = EXCLUDED.cap_usd",
                            (period, starts[period], self.caps.of(period)),
                        )
                        cur = await conn.execute(
                            "UPDATE llm_budget SET reserved_usd = reserved_usd + %(w)s"
                            " WHERE period_kind = %(k)s AND period_start = %(s)s"
                            "   AND spent_usd + reserved_usd + %(w)s <= cap_usd",
                            {"w": worst, "k": period, "s": starts[period]},
                        )
                        if cur.rowcount != 1:
                            raise _Denied
                    await conn.execute(
                        "INSERT INTO llm_reservations (call_id, job_id, hour_start, day_start, month_start,"
                        " worst_usd, expires_at) VALUES (%s, %s, %s, %s, %s, %s,"
                        " now() + make_interval(secs => %s))",
                        (call_id, job_id, starts["hour"], starts["day"], starts["month"], worst, self.ttl_s),
                    )
            except _Denied:
                return False
            finally:
                if not conn.autocommit:
                    await conn.commit()
        return True

    async def settle(self, call_id: uuid.UUID, actual_usd: Decimal | None) -> None:
        async with self._conn() as conn:
            async with conn.transaction():
                await _settle(conn, call_id, None if actual_usd is None else q8(actual_usd))
            if not conn.autocommit:
                await conn.commit()

    async def sweep(self) -> int:
        """Settle every expired reservation as worst-case spent. Returns the number swept."""
        async with self._conn() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT call_id FROM llm_reservations WHERE expires_at < now() ORDER BY call_id"
                )
                ids = [r[0] for r in await cur.fetchall()]
                for cid in ids:
                    await _settle(conn, cid, None)
            if not conn.autocommit:
                await conn.commit()
        return len(ids)


class _Denied(Exception):
    pass


async def _settle(conn: AsyncConnection, call_id: uuid.UUID, actual: Decimal | None) -> None:
    cur = await conn.execute(
        "DELETE FROM llm_reservations WHERE call_id = %s"
        " RETURNING hour_start, day_start, month_start, worst_usd",
        (call_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return  # already settled (or swept as worst case): never charge twice
    starts = dict(zip(PERIODS, row[:3], strict=True))
    worst: Decimal = row[3]
    spent = worst if actual is None else actual
    for period in PERIODS:  # same lock order as reserve
        await conn.execute(
            "UPDATE llm_budget SET reserved_usd = GREATEST(reserved_usd - %(w)s, 0),"
            " spent_usd = spent_usd + %(s)s"
            " WHERE period_kind = %(k)s AND period_start = %(p)s",
            {"w": worst, "s": spent, "k": period, "p": starts[period]},
        )


async def budget_snapshot(conn: AsyncConnection) -> dict[str, float]:
    """Current-window spend for the heartbeat (``spend_today_usd``, ``spend_hour_usd``, ``reserved_usd``)."""
    cur = await conn.execute(
        """
        SELECT COALESCE(sum(spent_usd) FILTER (WHERE period_kind = 'day'
                                                 AND period_start = date_trunc('day', now())), 0),
               COALESCE(sum(spent_usd) FILTER (WHERE period_kind = 'hour'
                                                 AND period_start = date_trunc('hour', now())), 0),
               (SELECT COALESCE(sum(worst_usd), 0) FROM llm_reservations)
          FROM llm_budget
        """
    )
    day, hour, reserved = await cur.fetchone()
    return {"spend_today_usd": float(day), "spend_hour_usd": float(hour), "reserved_usd": float(reserved)}


class MemoryBudget:
    """In-process reservation with one cap (the live gate's ``MAX_USD`` runaway guard)."""

    def __init__(self, cap_usd: Decimal | float) -> None:
        self.cap = q8(cap_usd)
        self.reserved = Decimal(0)
        self.spent = Decimal(0)
        self.denied = 0
        self._open: dict[uuid.UUID, Decimal] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, call_id: uuid.UUID, worst_usd: Decimal, job_id: int | None) -> bool:
        worst = q8(worst_usd)
        async with self._lock:
            if self.spent + self.reserved + worst > self.cap:
                self.denied += 1
                return False
            self.reserved += worst
            self._open[call_id] = worst
            return True

    async def settle(self, call_id: uuid.UUID, actual_usd: Decimal | None) -> None:
        async with self._lock:
            worst = self._open.pop(call_id, None)
            if worst is None:
                return
            self.reserved -= worst
            self.spent += worst if actual_usd is None else q8(actual_usd)


class NoBudget:
    """``HLM_LLM_BUDGET_DISABLED=true``: every reservation succeeds, nothing is tracked."""

    async def reserve(self, call_id: uuid.UUID, worst_usd: Decimal, job_id: int | None) -> bool:
        return True

    async def settle(self, call_id: uuid.UUID, actual_usd: Decimal | None) -> None:
        return None


__all__ = ["BudgetGuard", "Caps", "DbBudget", "MemoryBudget", "NoBudget", "budget_snapshot", "q8"]
