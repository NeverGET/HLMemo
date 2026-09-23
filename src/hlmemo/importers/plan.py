"""Classification against the server manifest and the deterministic import report (W1.5).

``classify(parsed, manifest)`` decides, per record: ``new`` (no current item owns its source key),
``changed`` (the owner's ``source.sha256`` differs → a revision with ``expected_version_id`` =
head) or ``unchanged`` (same content hash → 0 writes, whatever the mtime). Export-format records
map back by ``logical_id`` first and compare a content fingerprint. The report lists
new/changed/unchanged/skipped/rejected/missing, duplicate groups and a token estimate; it holds no
clock values or mtimes, so a dry-run on a fixture tree is golden (G-I1).

``request_id = uuid5(NS_IMPORT, project ‖ source_key ‖ sha256)``: re-sending the same content
is a replay; a changed file gets a new request id.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from hlmemo.importers.common import ImportRecord, ParseResult

NS_IMPORT = uuid.UUID("0c6f3c1e-5b7a-4f2e-9d41-7a2b8e6c1d09")
CLIENT = "hlm-import/1"
FINGERPRINT_KEYS = (
    "kind",
    "title",
    "body_sha256",
    "tags",
    "valid_from",
    "valid_to",
    "source",
    "describes",
    "pinned",
    "stability",
    "importance",
    "device_scope",
)


@dataclass(slots=True)
class Entry:
    record: ImportRecord
    action: str  # new | changed | unchanged
    logical_id: int | None = None
    head_version_id: int | None = None
    tokens: int = 0


@dataclass(slots=True)
class Plan:
    project: str
    system: str
    entries: list[Entry] = field(default_factory=list)
    parsed: ParseResult = field(default_factory=ParseResult)
    missing: list[str] = field(default_factory=list)


def request_id(project: str, key: str, sha: str, salt: str = "") -> str:
    return str(uuid.uuid5(NS_IMPORT, f"{project}\n{key}\n{sha}{salt}"))


def source_key(src: dict[str, Any] | None) -> str | None:
    return None if not src else f"{src.get('system')}:{src.get('path')}"


def _canon(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_fingerprint(rec: ImportRecord) -> str:
    ex = rec.export or {}
    return _canon(
        {
            "kind": rec.kind_guess,
            "title": rec.title,
            "body_sha256": rec.sha256,
            "tags": list(rec.tags),
            "valid_from": ex.get("valid_from"),
            "valid_to": ex.get("valid_to"),
            "source": ex.get("source"),
            "describes": list(rec.describes),
            "pinned": bool(ex.get("pinned", False)),
            "stability": ex.get("stability") or "volatile",
            "importance": ex.get("importance"),
            "device_scope": ex.get("device_scope") or "all",
        }
    )


def item_fingerprint(it: dict[str, Any]) -> str:
    return _canon({k: it.get(k) for k in FINGERPRINT_KEYS} | {"pinned": bool(it.get("pinned"))})


def classify(
    project: str, system: str, parsed: ParseResult, manifest: list[dict[str, Any]], meter: Any
) -> Plan:
    plan = Plan(project=project, system=system, parsed=parsed)
    by_key: dict[str, dict[str, Any]] = {}
    by_lid: dict[int, dict[str, Any]] = {}
    card: dict[str, Any] | None = None
    for it in manifest:
        # several current valid-time segments of one logical item: the head (greatest version) wins
        if by_lid.get(it["logical_id"], {}).get("version_id", 0) < it["version_id"]:
            by_lid[it["logical_id"]] = it
        if it["kind"] == "project_card":
            card = it if card is None or card["version_id"] < it["version_id"] else card
    for it in by_lid.values():
        k = source_key(it.get("source"))
        if k is not None:
            by_key[k] = it
    for rec in parsed.records:
        target: dict[str, Any] | None = None
        if rec.export is not None:
            if rec.kind_guess == "project_card":
                target = card
            elif isinstance(rec.export.get("logical_id"), int) and rec.export["logical_id"] in by_lid:
                cand = by_lid[rec.export["logical_id"]]
                target = cand if cand["kind"] == rec.kind_guess else None
            if target is None and rec.export.get("source") is not None:
                target = by_key.get(rec.key)
            same = target is not None and item_fingerprint(target) == record_fingerprint(rec)
        else:
            target = by_key.get(rec.key)
            same = target is not None and (target.get("source") or {}).get("sha256") == rec.sha256
        if target is None:
            action = "new"
        else:
            action = "unchanged" if same else "changed"
        tokens = 0 if action == "unchanged" else meter.count_text(rec.body)
        plan.entries.append(
            Entry(
                rec,
                action,
                logical_id=target["logical_id"] if target else None,
                head_version_id=target["version_id"] if target else None,
                tokens=tokens,
            )
        )
    # Items this source imported earlier under the covered paths that the run no longer produces
    # (file deleted/renamed, section anchor changed, now skipped): reported, never closed here.
    produced = {r.key for r in parsed.records} | {r.key for r in parsed.rejected}
    for k, it in sorted(by_key.items()):
        src = it.get("source") or {}
        path = str(src.get("path", ""))
        if src.get("system") != system or k in produced:
            continue
        if any(path.startswith(scope) for scope in parsed.scopes):
            plan.missing.append(k)
    return plan


def report(plan: Plan, *, dry_run: bool) -> dict[str, Any]:
    """The deterministic report (``--json``); no clock values, no mtimes, no ids of new items."""
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    items = []
    for e in sorted(plan.entries, key=lambda e: e.record.key):
        counts[e.action] += 1
        row: dict[str, Any] = {
            "key": e.record.key,
            "action": e.action,
            "kind": e.record.kind_guess,
            "title": e.record.title,
            "valid_from": e.record.evidenced_valid_from,
            "evidence": e.record.evidence,
            "sha256": e.record.sha256,
            "tokens": e.tokens,
        }
        if e.record.describes:
            row["describes"] = list(e.record.describes)
        if e.logical_id is not None:
            row["logical_id"] = e.logical_id
        items.append(row)
    parsed = plan.parsed
    return {
        "project": plan.project,
        "source": plan.system,
        "dry_run": dry_run,
        "counts": {
            **counts,
            "skipped": len(parsed.skipped),
            "rejected": len(parsed.rejected),
            "missing": len(plan.missing),
        },
        "items": items,
        "skipped": [{"path": s.path, "reason": s.reason} for s in parsed.skipped],
        "rejected": [{"key": r.key, "reason": r.reason, "date": r.date} for r in parsed.rejected],
        "duplicate_groups": parsed.duplicate_groups,
        "missing": plan.missing,
        "token_estimate": {
            "items": counts["new"] + counts["changed"],
            "tokens": sum(e.tokens for e in plan.entries),
            "tokenizer": "o200k_base",
        },
    }


__all__ = ["CLIENT", "NS_IMPORT", "Entry", "Plan", "classify", "record_fingerprint", "report", "request_id"]
