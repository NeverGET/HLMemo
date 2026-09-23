"""Write-path rules for the W1.5 item fields ``source`` and ``describes`` (PHASE2-4-ROADMAP W1.5).

* ``source`` is provenance (``{system, path, sha256, mtime?, commit?, commit_date?}``); the
  database derives ``source_key = system || ':' || path``. At most one CURRENT logical item per
  (home project, source_key) — enforced by EXCLUDE ``mv_one_source_owner`` (migration 0007, Sol 40
  #2) for every write path; :func:`check_source_owners` reports it first with details, after
  authorization and the head comparison, so an importer can re-classify.
* ``describes`` (≤ 16 paths ≤ 256 chars) projects to ``code_refs(version_id, path, commit)``;
  ``commit`` is the item's ``source.commit`` (the tree the importer read), else NULL. Surviving
  valid-time segments copy their base version's rows, like their text.

``write_service`` calls these from a handful of lines; replay derives the same rows from the
recorded items, so ``payload.resolved`` needs no new fields.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection
from psycopg import errors as pgerrors

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.errors import ToolError, invalid_arg
from hlmemo.core.write_models import Item
from hlmemo.db import import_queries as iq

SOURCE_OWNER_CONSTRAINT = "mv_one_source_owner"


def source_json(item: Item | dict[str, Any]) -> dict[str, Any] | None:
    """The stored ``source`` of an item: exactly its validated JSON form (``exclude_none``), which
    is also what ``resolved.write.items[i].source`` records for replay."""
    if isinstance(item, dict):
        src = item.get("source")
        return dict(src) if isinstance(src, dict) else None
    return item.source.model_dump(mode="json", exclude_none=True) if item.source is not None else None


def source_key(src: dict[str, Any] | None) -> str | None:
    return None if src is None else f"{src['system']}:{src['path']}"


def code_ref_rows(version_id: int, item: Item | dict[str, Any]) -> list[tuple[int, str, str | None]]:
    """``code_refs`` rows of a new version (live path: an ``Item``; replay: the recorded dict)."""
    if isinstance(item, dict):
        describes = item.get("describes") or []
    else:
        describes = item.describes or []
    src = source_json(item)
    commit = src.get("commit") if src else None
    return [(version_id, path, commit) for path in describes]


def _visible(ctx: AuthContext, home_id: int, owner: iq.SourceOwner) -> bool:
    return (
        home_id in owner.project_ids
        and ctx.has(home_id, Role.READ)
        and owner.device_scope in ctx.scope_values()
    )


async def check_source_owners(
    conn: AsyncConnection, ctx: AuthContext, home_id: int, plans: list[Any]
) -> None:
    """E_INVALID_ARG for a key repeated inside the batch; E_VERSION_CONFLICT when another current
    logical item owns an item's key (``details`` name the owner only if the caller can see it)."""
    keyed: list[tuple[Any, str]] = []
    seen: dict[str, int] = {}
    for p in plans:
        key = source_key(source_json(p.item))
        if key is None:
            continue
        if key in seen:
            raise invalid_arg(
                f"items[{p.index}].source: source key already used by items[{seen[key]}] of this batch",
                index=p.index,
                reason="duplicate_source",
            )
        seen[key] = p.index
        keyed.append((p, key))
    if not keyed:
        return
    owners = await iq.source_owners(conn, home_id, [k for _, k in keyed])
    by_key: dict[str, list[iq.SourceOwner]] = {}
    for o in owners:
        by_key.setdefault(o.source_key, []).append(o)
    for p, key in keyed:
        for o in by_key.get(key, []):
            if o.logical_id == p.logical_id:
                continue
            details: dict[str, Any] = {"index": p.index, "reason": "source_owned"}
            if _visible(ctx, home_id, o):
                details.update(logical_id=o.logical_id, current_version_id=o.head_version_id)
            raise ToolError(
                "E_VERSION_CONFLICT",
                f"items[{p.index}].source: another current item owns this source; revise it instead",
                **details,
            )


def owner_violation(exc: BaseException) -> ToolError | None:
    """The EXCLUDE backstop (a concurrent writer won the race) as the same client error."""
    if isinstance(exc, pgerrors.ExclusionViolation) and exc.diag.constraint_name == SOURCE_OWNER_CONSTRAINT:
        return ToolError(
            "E_VERSION_CONFLICT",
            "another current item owns this source (concurrent write); re-read and revise it",
            reason="source_owned",
        )
    return None


async def survivor_code_refs(
    conn: AsyncConnection, pairs: list[tuple[int, int]]
) -> list[tuple[int, str, str | None]]:
    """``(survivor_version_id, base_version_id)`` → the base's rows re-keyed to the survivor."""
    if not pairs:
        return []
    base = await iq.code_refs_of(conn, [b for _, b in pairs])
    return [(svid, path, commit) for svid, b in pairs for path, commit in base.get(b, [])]


__all__ = [
    "SOURCE_OWNER_CONSTRAINT",
    "check_source_owners",
    "code_ref_rows",
    "owner_violation",
    "source_json",
    "source_key",
    "survivor_code_refs",
]
