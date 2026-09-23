"""Pydantic request/response models for ``memory.write`` and ``memory.call_the_day`` (PHASE0-SPEC §3).

Shape validation (types, ranges, enums) lives here and surfaces as ``E_INVALID_ARG`` through
``parse_request``; semantic rules (project integrity, authorization, temporal, card size) live in
``core/write_service.py`` so that their error order matches the spec.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from hlmemo.core.errors import ToolError

SLUG_RE = r"^[a-z0-9][a-z0-9-]{1,63}$"
ITEM_BODY_MAX = 64000  # memory.write ``items[].body`` limit; call_the_day's derived items obey it too
ITEM_LINKS_MAX = 32  # memory.write ``items[].links`` limit
DEVICE_SCOPE_RE = r"^(all|class:(personal|work|server|ci|other)|device:[0-9]+)$"

Kind = Literal["fact", "episode", "lesson", "experience", "project_card", "session_note", "doc_chunk"]
Rel = Literal["relates_to", "contradicts", "supersedes", "derived_from", "depends_on"]
Stability = Literal["stable", "volatile"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)


def canonical_device_scope(v: str) -> str:
    """F08: ``device:<id>`` must be spelled canonically (no leading zeros). Readers match the
    stored string against ``'device:' || device_id``, so ``device:02`` would be acked on write and
    then be visible to nobody, including the writer. Rejected, never silently rewritten."""
    if v.startswith("device:"):
        digits = v[len("device:") :]
        if digits.startswith("0"):
            raise ValueError(f"device_scope {v!r} is not canonical: use 'device:<id>' without leading zeros")
    return v


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


SOURCE_SYSTEM_RE = r"^[a-z][a-z0-9_-]{0,31}$"
SOURCE_PATH_MAX = 512  # W1.5 contract: source.path <= 512
DESCRIBES_MAX = 16  # W1.5 contract: describes <= 16 paths
DESCRIBES_PATH_MAX = 256  # each describes path <= 256
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _ts_or_none(v: str | None, field: str) -> str | None:
    if v is None:
        return v
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    return v


class SourceSpec(_Strict):
    """W1.5 provenance of an imported item (PHASE2-4-ROADMAP W1.5 *Contract (Item)*).

    ``mtime`` and ``commit_date`` are provenance only: they never set ``valid_from`` or
    ``recorded_at`` (D-020 deviation, Sol #6). ``source_key = system || ':' || path`` is derived
    by the database (migration 0007) and at most one current logical item per project owns it."""

    system: str = Field(pattern=SOURCE_SYSTEM_RE)
    path: str = Field(min_length=1, max_length=SOURCE_PATH_MAX)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mtime: str | None = None
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,64}$")
    commit_date: str | None = None

    @field_validator("path")
    @classmethod
    def _path_text(cls, v: str) -> str:
        if _CONTROL.search(v):
            raise ValueError("source.path must not contain control characters")
        return v

    @field_validator("mtime")
    @classmethod
    def _mtime(cls, v: str | None) -> str | None:
        return _ts_or_none(v, "source.mtime")

    @field_validator("commit_date")
    @classmethod
    def _commit_date(cls, v: str | None) -> str | None:
        return _ts_or_none(v, "source.commit_date")


class Item(_Strict):
    kind: Kind
    logical_id: int | None = Field(default=None, ge=1)
    expected_version_id: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=ITEM_BODY_MAX)
    tags: list[str] = Field(default_factory=list, max_length=32)
    pinned: bool = False
    stability: Stability = "volatile"
    importance: int | None = Field(default=None, ge=1, le=10)
    project_ids: list[str] | None = Field(default=None, min_length=1, max_length=16)
    device_scope: str = Field(default="all", pattern=DEVICE_SCOPE_RE)
    valid_from: str | None = None
    valid_to: str | None = None
    links: list[LinkSpec] = Field(default_factory=list, max_length=ITEM_LINKS_MAX)
    # W1.5 (optional; absent → the item is byte-identical to a Phase-0 item in resolved.write)
    source: SourceSpec | None = None
    describes: list[str] | None = Field(default=None, max_length=DESCRIBES_MAX)
    # W1.5 `close`: the fact stopped being true at valid_to (default: occurred_at, i.e. server now).
    # A correction of [valid_from, valid_to) whose superseded segments keep NO part after valid_to
    # (invalidate, never delete). Revisions only; None (not False) when unset so resolved.write of
    # ordinary items is unchanged.
    close: bool | None = None

    _device_scope = field_validator("device_scope")(canonical_device_scope)

    @field_validator("describes")
    @classmethod
    def _describes(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for p in v:
            if not 1 <= len(p) <= DESCRIBES_PATH_MAX:
                raise ValueError(f"describes paths must be 1..{DESCRIBES_PATH_MAX} characters")
            if _CONTROL.search(p):
                raise ValueError("describes paths must not contain control characters")
        if len(set(v)) != len(v):
            raise ValueError("describes must not contain duplicates")
        return v

    @model_validator(mode="after")
    def _shared_card(self) -> Item:
        if self.kind == "project_card" and self.device_scope != "all":
            raise ValueError('project_card device_scope must be "all"')
        if self.close:
            if self.kind == "project_card":
                raise ValueError("a project card cannot be closed")
            if self.logical_id is None or self.valid_from is None:
                raise ValueError("close needs logical_id, expected_version_id and valid_from")
        return self

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

    _device_scope = field_validator("device_scope")(canonical_device_scope)


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

    @model_validator(mode="after")
    def _derived_items_fit(self) -> CloseRequest:
        """F12: the close is expanded into ordinary write items (session note, lessons, card);
        each derived item must satisfy the ``memory.write`` item limits, checked here so that an
        oversized close is ``E_INVALID_ARG`` naming the limit, never an internal error."""
        n = len(session_note_body(self.notes, self.decisions))
        if n > ITEM_BODY_MAX:
            raise ValueError(
                f"session note body (notes + decisions) is {n} characters; the item body limit is "
                f"{ITEM_BODY_MAX}"
            )
        if self.card_update is not None and 1 + len(self.expected_versions) > ITEM_LINKS_MAX:
            raise ValueError(
                f"card_update with {len(self.expected_versions)} expected_versions needs "
                f"{1 + len(self.expected_versions)} links; the item link limit is {ITEM_LINKS_MAX} "
                f"(at most {ITEM_LINKS_MAX - 1} expected_versions with a card_update)"
            )
        return self


def session_note_body(notes: str, decisions: list[str]) -> str:
    """The session-note body ``call_the_day`` writes (§3): notes, then a ``## Decisions`` list.
    Must stay identical to ``write_service._close_items`` (pinned by a unit test)."""
    body = notes
    if decisions:
        body += "\n\n## Decisions\n" + "\n".join(f"- {d}" for d in decisions)
    return body


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


MAX_ERRORS = 20  # validation errors echoed in ``details.errors`` (F06: the envelope stays bounded)
MAX_ERROR_TEXT = 300  # chars per echoed message / loc / ctx value


def _clip(text: str) -> str:
    return text if len(text) <= MAX_ERROR_TEXT else text[: MAX_ERROR_TEXT - 1] + "…"


def bounded_validation_errors(exc: ValidationError) -> tuple[list[dict[str, Any]], int]:
    """Pydantic errors without ``input``/``url`` (F06: never echo the request back), at most
    ``MAX_ERRORS`` of them, every string clipped; returns ``(errors, total_count)``."""
    raw = exc.errors(include_input=False, include_url=False)
    out: list[dict[str, Any]] = []
    for err in raw[:MAX_ERRORS]:
        item: dict[str, Any] = {
            "type": _clip(str(err.get("type", ""))),
            "loc": [p if isinstance(p, int) else _clip(str(p)) for p in err.get("loc", ())],
            "msg": _clip(str(err.get("msg", ""))),
        }
        ctx = err.get("ctx")
        if isinstance(ctx, dict):
            item["ctx"] = {
                _clip(str(k)): (v if isinstance(v, int | float | bool) or v is None else _clip(str(v)))
                for k, v in list(ctx.items())[:8]
            }
        out.append(item)
    return out, len(raw)


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_input=False, include_url=False)[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = str(err.get("msg"))
        parts.append(_clip(f"{loc}: {msg}" if loc else msg))
    return "; ".join(parts)


def parse_request[T: BaseModel](model: type[T], raw: T | dict[str, Any]) -> T:
    """Validate ``raw`` as ``model``; a Pydantic error becomes ``E_INVALID_ARG`` with a bounded
    ``details.errors`` list (no input echo) and ``details.error_count`` (the full count)."""
    if isinstance(raw, model):
        return raw
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        errors, total = bounded_validation_errors(exc)
        raise ToolError(
            "E_INVALID_ARG", _format_validation_error(exc), errors=errors, error_count=total
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
    "SourceSpec",
    "VersionAck",
    "WriteRequest",
    "WriteResult",
    "bounded_validation_errors",
    "parse_request",
    "session_note_body",
]
