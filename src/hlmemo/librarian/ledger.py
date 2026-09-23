"""``llm_calls`` ledger (W2a): one row per provider attempt, never any content.

Rows are written in their own short transaction so they survive a rolled-back job. ``job_calls``
counts the attempts that reached (or would have reached) the network for a job: the per-job call
ceiling (``HLM_LLM_JOB_CALL_CAP``) is enforced on it.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Protocol

from hlmemo.librarian.budget import ConnCtx, q8

OUTCOMES = (
    "ok",
    "schema_retry_ok",
    "schema_fail",
    "http_error",
    "timeout",
    "budget_deferred",
    "breaker_open",
)
#: outcomes that consumed a network attempt (count toward the per-job ceiling)
NETWORK_OUTCOMES = ("ok", "schema_retry_ok", "schema_fail", "http_error", "timeout")


@dataclass(slots=True)
class LedgerRow:
    task: str
    profile: str
    model_id: str
    prompt_version: str
    schema_version: str
    mode: str
    outcome: str
    job_id: int | None = None
    request_sha256: str | None = None
    response_sha256: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reserved_usd: Decimal = Decimal(0)
    cost_usd: Decimal = Decimal(0)
    latency_ms: int | None = None
    call_id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown ledger outcome {self.outcome!r}")


class Ledger(Protocol):
    async def record(self, row: LedgerRow) -> None: ...

    async def job_calls(self, job_id: int) -> int: ...


class DbLedger:
    def __init__(self, conn: ConnCtx) -> None:
        self._conn = conn

    async def record(self, row: LedgerRow) -> None:
        async with self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO llm_calls (call_id, job_id, task, profile, model_id, prompt_version,
                                       schema_version, mode, request_sha256, response_sha256,
                                       input_tokens, cached_input_tokens, output_tokens, reserved_usd,
                                       cost_usd, latency_ms, outcome)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row.call_id,
                    row.job_id,
                    row.task,
                    row.profile,
                    row.model_id,
                    row.prompt_version,
                    row.schema_version,
                    row.mode,
                    row.request_sha256,
                    row.response_sha256,
                    row.input_tokens,
                    row.cached_input_tokens,
                    row.output_tokens,
                    q8(row.reserved_usd),
                    q8(row.cost_usd),
                    row.latency_ms,
                    row.outcome,
                ),
            )
            if not conn.autocommit:
                await conn.commit()

    async def job_calls(self, job_id: int) -> int:
        async with self._conn() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM llm_calls WHERE job_id = %s AND outcome = ANY(%s)",
                (job_id, list(NETWORK_OUTCOMES)),
            )
            (n,) = await cur.fetchone()
            if not conn.autocommit:
                await conn.commit()
        return int(n)


class MemoryLedger:
    def __init__(self) -> None:
        self.rows: list[LedgerRow] = []

    async def record(self, row: LedgerRow) -> None:
        self.rows.append(row)

    async def job_calls(self, job_id: int) -> int:
        return sum(1 for r in self.rows if r.job_id == job_id and r.outcome in NETWORK_OUTCOMES)

    def as_dicts(self) -> list[dict]:
        out = []
        for r in self.rows:
            d = asdict(r)
            d["call_id"] = str(r.call_id)
            d["reserved_usd"] = str(r.reserved_usd)
            d["cost_usd"] = str(r.cost_usd)
            out.append(d)
        return out


__all__ = ["NETWORK_OUTCOMES", "OUTCOMES", "DbLedger", "Ledger", "LedgerRow", "MemoryLedger"]
