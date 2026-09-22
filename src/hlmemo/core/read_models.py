"""Pydantic request models for ``memory.query`` / ``memory.drilldown`` / ``memory.raw`` (PHASE0-SPEC §3).

Shape validation only (→ ``E_INVALID_ARG`` via ``parse_request``); budget range, authorization,
temporal parsing and clue resolution live in ``core/read_service.py``. Responses are plain dicts
(the canonical JSON of the dict is what the meter counts and what goes on the wire).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from hlmemo.core.clues import is_valid_clue
from hlmemo.core.write_models import SLUG_RE, Kind, _Strict, parse_request

Evidence = Literal["matched", "none"]


class _ReadRequest(_Strict):
    project: str = Field(pattern=SLUG_RE)
    # Range (256..32000) is checked by ``validate_budget`` so that the spec's dedicated codes
    # (E_BUDGET_TOO_SMALL / E_BUDGET_TOO_LARGE) are raised, not E_INVALID_ARG.
    token_budget: Any


class QueryRequest(_ReadRequest):
    query: str = Field(min_length=1, max_length=2000)
    valid_at: str | None = None
    known_at: str | None = None
    include_archived: bool = False
    kinds: list[Kind] | None = Field(default=None, min_length=1, max_length=7)

    @field_validator("kinds")
    @classmethod
    def _unique_kinds(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(set(v)) != len(v):
            raise ValueError("kinds must be unique")
        return v


class DrilldownRequest(_ReadRequest):
    clue_ids: list[str] = Field(min_length=1, max_length=20)
    cursor: str | None = None
    valid_at: str | None = None
    known_at: str | None = None
    include_archived: bool = False  # not in the spec's list; mirrors query (archived clues drill down)

    @field_validator("clue_ids")
    @classmethod
    def _clues(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("clue_ids must be unique")
        for c in v:
            # Canonical spelling only (F01): ``v0``/``v01`` pass the wire pattern but name no
            # version; they are a caller error (E_INVALID_ARG), never a retryable crash.
            if not is_valid_clue(c):
                raise ValueError(f"malformed clue {c!r}")
        return v


class RawRequest(_ReadRequest):
    version_id: int = Field(ge=1)
    cursor: str | None = None

    @field_validator("version_id", mode="before")
    @classmethod
    def _not_bool(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("version_id must be an integer")
        return v


__all__ = ["DrilldownRequest", "Evidence", "QueryRequest", "RawRequest", "parse_request"]
