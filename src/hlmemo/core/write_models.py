"""Pydantic request/response models for ``memory.write`` and ``memory.call_the_day`` (PHASE0-SPEC §3).

Shape validation (types, ranges, enums) lives here and surfaces as ``E_INVALID_ARG`` through
``parse_request``; semantic rules (project integrity, authorization, temporal, card size) live in
``core/write_service.py`` so that their error order matches the spec.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from hlmemo.core.errors import ToolError

SLUG_RE = r"^[a-z0-9][a-z0-9-]{1,63}$"
DEVICE_SCOPE_RE = r"^(all|class:(personal|work|server|ci|other)|device:[0-9]+)$"

Kind = Literal["fact", "episode", "lesson", "experience", "project_card", "session_note", "doc_chunk"]
Rel = Literal["relates_to", "contradicts", "supersedes", "derived_from", "depends_on"]
Stability = Literal["stable", "volatile"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)


class LinkSpec(_Strict):
    rel: Rel
    target: int | str = Field(description='logical_id or "$<item_index>"')
    target_version_id: int | None = Field(default=None, ge=1)

    @field_validator("target")
    @classmethod
    def _target_shape(cls, v: int | str) -> int | str:
        if isinstance(v, bool):
            raise ValueError("target must be an integer logical_id or '$<index>'")
        if isinstance(v, str):
            if not (v.startswith("$") and v[1:].isdigit()):
                raise ValueError("string target must be '$<item_index>'")
            return v
        if v < 1:
            raise ValueError("target logical_id must be >= 1")
        return v


class Item(_Strict):
    kind: Kind
    logical_id: int | None = Field(default=None, ge=1)
    expected_version_id: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=64000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    pinned: bool = False
    stability: Stability = "volatile"
    importance: int | None = Field(default=None, ge=1, le=10)
    project_ids: list[str] | None = Field(default=None, min_length=1, max_length=16)
    device_scope: str = Field(default="all", pattern=DEVICE_SCOPE_RE)
    valid_from: str | None = None
    valid_to: str | None = None
    links: list[LinkSpec] = Field(default_factory=list, max_length=32)

    @field_validator("project_ids", mode="before")
    @classmethod
    def _no_null_elements(cls, v: Any) -> Any:
        # Pydantic would reject ``None`` elements anyway; this makes the message explicit (§1).
        if isinstance(v, list) and any(x is None for x in v):
            raise ValueError("project_ids must not contain null")
        return v


class WriteRequest(_Strict):
    project: str = Field(pattern=SLUG_RE)
    request_id: str
    client: str = Field(min_length=1, max_length=200)
    occurred_at: str | None = None
    items: list[Item] = Field(min_length=1, max_length=50)
    token_budget: int | None = None

    @field_validator("request_id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        return str(uuid.UUID(v))


class LessonSpec(_Strict):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=64000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    device_scope: str = Field(default="all", pattern=DEVICE_SCOPE_RE)


class CardUpdate(_Strict):
    body: str = Field(min_length=1, max_length=64000)
    expected_version_id: int | None = Field(default=None, ge=1)  # None: no card exists yet (first close)


class ExpectedVersion(_Strict):
    logical_id: int = Field(ge=1)
    version_id: int = Field(ge=1)


class CloseRequest(_Strict):
    project: str = Field(pattern=SLUG_RE)
    request_id: str
    session_id: str
    client: str = Field(min_length=1, max_length=200)
    occurred_at: str | None = None
    notes: str = Field(min_length=1, max_length=64000)
    decisions: list[str] = Field(default_factory=list, max_length=32)
    lessons: list[LessonSpec] = Field(default_factory=list, max_length=16)
    card_update: CardUpdate | None = None
    expected_versions: list[ExpectedVersion] = Field(default_factory=list, max_length=64)
    token_budget: int | None = None

    @field_validator("request_id", "session_id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        return str(uuid.UUID(v))


class VersionAck(BaseModel):
    index: int
    logical_id: int
    version_id: int
    chunk_count: int
    embedding_status: Literal["queued", "done"]


class BudgetOut(BaseModel):
    limit: int
    used: int
    tokenizer: Literal["o200k_base"] = "o200k_base"


class WriteResult(BaseModel):
    request_id: str
    replayed: bool
    versions: list[VersionAck]
    budget: BudgetOut


class CloseResult(WriteResult):
    session_note_clue: str


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts)


def parse_request[T: BaseModel](model: type[T], raw: T | dict[str, Any]) -> T:
    """Validate ``raw`` as ``model``; a Pydantic error becomes ``E_INVALID_ARG``."""
    if isinstance(raw, model):
        return raw
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ToolError(
            "E_INVALID_ARG", _format_validation_error(exc), errors=exc.errors(include_url=False)
        ) from exc


__all__ = [
    "BudgetOut",
    "CardUpdate",
    "CloseRequest",
    "CloseResult",
    "ExpectedVersion",
    "Item",
    "Kind",
    "LessonSpec",
    "LinkSpec",
    "Rel",
    "VersionAck",
    "WriteRequest",
    "WriteResult",
    "parse_request",
]
