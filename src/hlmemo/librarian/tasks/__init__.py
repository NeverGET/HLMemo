"""Librarian job handlers (W2a registers the foundation ops; W2b+ add placement, contradiction, …).

A handler's ``plan`` does the reads (short transaction, committed) and the provider calls (never
inside a transaction); the worker then applies the plan in ONE transaction: capability recheck,
role gate, audit event, mutations, fenced ``done``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

#: user-message job names (the prompts' ``JOB "<name>"`` lines; kept from the D-019 bench)
JOB_NAMES = {
    "placement": "placement",
    "contradiction": "contradiction",
    "summary": "summarization",
    "risk": "risk_check",
}


def user_message(task: str, payload: dict[str, Any], rules: list[dict[str, Any]] | None = None) -> str:
    """``JOB: <name>`` + ``INPUT: <json>`` (+ ``RULES: <json>`` from working memory).

    The static system prompt comes first in the request; everything that varies is here.
    """
    msg = f"JOB: {JOB_NAMES[task]}\nINPUT: {json.dumps(payload, ensure_ascii=False)}"
    if rules:
        hint = "librarian working memory; follow unless they contradict the JOB"
        msg += f"\nRULES ({hint}): {json.dumps(rules, ensure_ascii=False)}"
    return msg


@dataclass(slots=True)
class Plan:
    op: str
    outcome: (
        str  # "proposed" | "approved" | "policy_off" | "skipped_device_scope" | "stale_subject" | "no_change"
    )
    capabilities: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)
    #: candidate mutations (unmaterialized) with their proposal metadata
    mutations: list[dict[str, Any]] = field(default_factory=list)
    auto_ok: list[bool] = field(default_factory=list)
    meta: list[dict[str, Any]] = field(default_factory=list)
    request_extra: dict[str, Any] = field(default_factory=dict)
    #: follow-up jobs to enqueue with the result event (recorded in resolved.jobs)
    jobs: list[dict[str, Any]] = field(default_factory=list)


class Handler(Protocol):
    op: str

    async def plan(self, w: Any, job: Any) -> Plan: ...


__all__ = ["JOB_NAMES", "Handler", "Plan", "user_message"]
