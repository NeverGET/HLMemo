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
    "place": "placement",
    "relate": "relation",
    "relate_verify": "relation_check",
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
class Proposal:
    """One proposal (W2b): the unmaterialized ``actions`` that belong together (e.g. contradicts +
    supersedes links + the bi-temporal close of the older item), applied or asked as ONE question.

    ``auto_ok``: the proposal is in the W2b auto-rule class (only then may ``autonomous`` apply it
    without an owner decision). ``assessed`` maps every subject logical id (str) to the version the
    model assessed (the apply-time staleness check). ``project_ids``: every project it touches."""

    kind: str  # contradiction | link | widen_scope (librarian_questions.kind)
    actions: list[dict[str, Any]]
    auto_ok: bool
    assessed: dict[str, int]
    subject_clues: list[str]
    project_ids: list[int]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Plan:
    op: str
    outcome: (
        str  # "proposed" | "approved" | "policy_off" | "skipped_device_scope" | "stale_subject" | "no_change"
    )
    capabilities: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)
    #: proposals (questions, or direct mutations in the auto-rule class under ``autonomous``)
    proposals: list[Proposal] = field(default_factory=list)
    #: placement signals (``signal_upsert``): written in EVERY role, never a proposal (W2b)
    signals: list[dict[str, Any]] = field(default_factory=list)
    #: ``apply_batch``: the owner-approved questions ``[(question_id, proposal row dict)]``
    approved: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    request_extra: dict[str, Any] = field(default_factory=dict)
    #: follow-up jobs to enqueue with the result event (recorded in resolved.jobs)
    jobs: list[dict[str, Any]] = field(default_factory=list)


class Handler(Protocol):
    op: str

    async def plan(self, w: Any, job: Any) -> Plan: ...


__all__ = ["JOB_NAMES", "Handler", "Plan", "Proposal", "user_message"]
