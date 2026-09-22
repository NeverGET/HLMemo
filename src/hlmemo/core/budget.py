"""Token metering and greedy packing (PHASE0-SPEC §3 budget rule, §4 step 12).

Meter = ``tiktoken`` ``o200k_base`` over the canonical compact JSON
``json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)``.
The single ``TextContent`` on the wire *is* this serialisation, so wire bytes ==
metered bytes.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

import tiktoken

from hlmemo.core import METER_VERSION

BUDGET_MIN = 256
BUDGET_MAX = 32000
DEFAULT_WRITE_BUDGET = 2000

T = TypeVar("T")


class BudgetError(ValueError):
    code: str = "E_INVALID_ARG"
    retryable = False

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details

    def as_error(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "retryable": self.retryable, "details": self.details}


class BudgetTooSmall(BudgetError):
    code = "E_BUDGET_TOO_SMALL"

    def __init__(self, given: int, min_needed: int = BUDGET_MIN) -> None:
        super().__init__(f"token_budget {given} is below the minimum {min_needed}", min=min_needed)
        self.given = given
        self.min = min_needed


class BudgetTooLarge(BudgetError):
    code = "E_BUDGET_TOO_LARGE"

    def __init__(self, given: int, max_allowed: int = BUDGET_MAX) -> None:
        super().__init__(f"token_budget {given} exceeds the maximum {max_allowed}", max=max_allowed)
        self.given = given
        self.max = max_allowed


def validate_budget(n: object, *, default: int | None = None) -> int:
    """Return a validated budget or raise ``BudgetTooSmall``/``BudgetTooLarge``.

    ``None`` with a ``default`` (write/call_the_day) returns the default.
    Non-integers (bool, float, str) are ``E_INVALID_ARG`` (``BudgetError``).
    """
    if n is None and default is not None:
        n = default
    if isinstance(n, bool) or not isinstance(n, int):
        raise BudgetError(f"token_budget must be an integer, got {type(n).__name__}", type=type(n).__name__)
    if n < BUDGET_MIN:
        raise BudgetTooSmall(n)
    if n > BUDGET_MAX:
        raise BudgetTooLarge(n)
    return n


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(slots=True)
class PackResult:
    packed: list[Any]
    omitted: int
    used: int
    envelope: dict[str, Any] = field(repr=False)


class Meter:
    """o200k_base token meter over the canonical JSON serialisation."""

    tokenizer = METER_VERSION

    def __init__(self) -> None:
        self._enc = tiktoken.get_encoding(METER_VERSION)

    # -- counting -----------------------------------------------------------
    @staticmethod
    def canonical(obj: Any) -> str:
        return canonical(obj)

    def count_text(self, text: str) -> int:
        return len(self._enc.encode(text, disallowed_special=()))

    def count(self, obj: Any) -> int:
        """Token count of ``canonical(obj)``."""
        return self.count_text(canonical(obj))

    def truncate(self, text: str, max_tokens: int) -> tuple[str, bool]:
        """First ``max_tokens`` o200k tokens of ``text`` (decoded), and whether it was cut.

        Used for previews (``PREVIEW_TOK``/``PREVIEW_EXT``) and the card cut at ``CARD_ALLOW``.
        """
        ids = self._enc.encode(text, disallowed_special=())
        if len(ids) <= max_tokens:
            return text, False
        return self._enc.decode(ids[:max_tokens]), True

    # -- packing ------------------------------------------------------------
    def settle(self, envelope: dict[str, Any], limit: int, *, budget_key: str | None = "budget") -> int:
        """Set ``envelope[budget_key] = {limit, used, tokenizer}`` with ``used`` equal to the
        measured size of the *final* serialisation (``used`` is itself part of it, so this is a
        fixed point; when the digit boundary makes it oscillate, the larger value is reported).
        Returns ``used``.
        """
        if budget_key is None:
            return self.count(envelope)
        used = 0
        seen: list[int] = []
        for _ in range(6):
            envelope[budget_key] = {"limit": limit, "used": used, "tokenizer": self.tokenizer}
            measured = self.count(envelope)
            if measured == used:
                return used
            if measured in seen:  # oscillation: report the overestimate
                used = max(seen[-1], measured)
                envelope[budget_key] = {"limit": limit, "used": used, "tokenizer": self.tokenizer}
                return used
            seen.append(measured)
            used = measured
        envelope[budget_key] = {"limit": limit, "used": used, "tokenizer": self.tokenizer}
        return used

    def pack(
        self,
        envelope: dict[str, Any],
        candidates: Sequence[T] | Iterable[T],
        budget: int,
        render: Callable[[T], Any],
        *,
        key: str = "hits",
        omitted_key: str | None = "omitted",
        budget_key: str | None = "budget",
    ) -> PackResult:
        """Greedy measure-after-each-append packer (§4 step 12).

        ``envelope`` is mutated in place: ``envelope[key]`` receives the rendered candidates
        that fit (in order), ``envelope[omitted_key]`` the number left out and
        ``envelope[budget_key]`` the ``{limit, used, tokenizer}`` block. Packing stops at the
        first candidate that does not fit; the returned ``used`` is the size of the exact
        object left in ``envelope`` and never exceeds ``budget``.

        Raises ``BudgetTooSmall(min=<needed>)`` if even the empty envelope does not fit.
        """
        cands = list(candidates)
        packed: list[Any] = []

        def _state(items: list[Any]) -> int:
            envelope[key] = items
            if omitted_key is not None:
                envelope[omitted_key] = len(cands) - len(items)
            return self.settle(envelope, budget, budget_key=budget_key)

        used = _state(packed)
        if used > budget:
            raise BudgetTooSmall(budget, used)

        for cand in cands:
            trial = packed + [render(cand)]
            trial_used = _state(trial)
            if trial_used > budget:
                used = _state(packed)  # restore the last committed state
                break
            packed, used = trial, trial_used

        assert used <= budget
        return PackResult(packed=packed, omitted=len(cands) - len(packed), used=used, envelope=envelope)


__all__ = [
    "BUDGET_MIN",
    "BUDGET_MAX",
    "DEFAULT_WRITE_BUDGET",
    "BudgetError",
    "BudgetTooSmall",
    "BudgetTooLarge",
    "Meter",
    "PackResult",
    "canonical",
    "validate_budget",
]
