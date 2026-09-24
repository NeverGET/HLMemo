"""Classification against the server manifest and the deterministic import report (W1.5).

``classify(parsed, manifest)`` decides, per record:

* ``new`` — no item owns its source key;
* ``changed`` — the owner's content differs → a revision with ``expected_version_id`` = head;
* ``unchanged`` — same content hash → 0 writes, whatever the mtime.

Export-format records (``exportfmt``) are identified by their ``origin`` (``<project>/<logical_id>``
of the item that was exported) or, when the exported item had a real provenance, by that source.
An export record selects an existing item only through a server-held identity: an item whose own
``source`` is the record's (``hlm:<origin>`` — the item was created by importing that export — or
the real provenance it carries). Otherwise it becomes a NEW item with that source (a persistent,
ownership-checked mapping, UNIQUE ``mv_source_owner``), so re-imports are idempotent and neither a
crafted ``logical_id`` nor a forged ``origin`` can revise an unrelated item (Sol 42 #5, 43 #1). An
unedited export re-imported into its own project is ``unchanged`` (identical content, no write).
The project card is revised only while it is the skeleton or when it came from that export.

Items this source imported earlier under the run's paths that are no longer produced are *missing*:
:func:`remap` pairs them with ``new`` records by body similarity (a renamed heading or a moved file
keeps its logical item: one revision that also moves the source key, Sol 42 #6); the rest are closed
(``close``: validity ends now) unless their file was skipped for a read reason (kept, reported).

``request_id = uuid5(NS_IMPORT, project ‖ source_key ‖ sha256 ‖ expected_version_id ‖ action)``:
re-sending the same step is a replay, and an A→B→A cycle never reuses an id (Sol 42 #4).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from hlmemo.importers.common import ImportRecord, ParseResult, Reject

NS_IMPORT = uuid.UUID("0c6f3c1e-5b7a-4f2e-9d41-7a2b8e6c1d09")
CLIENT = "hlm-import/1"
ORIGIN_SYSTEM = "hlm"
SKELETON_TAG = "skeleton-card"  # core/skeleton_card.SKELETON_TAG (no server import on the client)
#: files skipped for these reasons keep their earlier items open (the content may still be valid)
KEEP_REASONS = ("unreadable", "binary", "too-large", "secret-pattern")
#: body similarity (word 3-shingle Jaccard, heading line excluded) needed to re-map a missing item
REMAP_SAME_FILE = 0.6
REMAP_OTHER_FILE = 0.9
#: closing more than this share of the run's in-scope items needs an explicit confirmation, whatever
#: the scope's size (a wrong --base, a moved tree, a mostly deleted directory: Sol 43 #5)
MASS_CLOSE_SHARE = 0.5
#: a re-map needs real content on both sides (heading-only / empty sections never match)
MIN_REMAP_WORDS = 8


@dataclass(slots=True)
class Entry:
    record: ImportRecord
    action: str  # new | changed | unchanged
    logical_id: int | None = None
    head_version_id: int | None = None
    expected: int | None = None  # expected_version_id of the write (None: a new item)
    remapped_from: str | None = None
    tokens: int = 0


@dataclass(slots=True)
class Plan:
    project: str
    system: str
    entries: list[Entry] = field(default_factory=list)
    parsed: ParseResult = field(default_factory=ParseResult)
    missing: list[dict[str, Any]] = field(default_factory=list)  # manifest items no longer produced
    closes: list[dict[str, Any]] = field(default_factory=list)  # full items to close
    kept: list[dict[str, Any]] = field(default_factory=list)  # {key, reason}: missing, not closed
    remapped: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)  # {from, candidates}: no re-map
    rejected: list[Reject] = field(default_factory=list)
    in_scope: int = 0  # open items of this source under the run's paths (the mass-close base)


def request_id(project: str, key: str, sha: str, expected: int | None = None, action: str = "write") -> str:
    tail = "new" if expected is None else str(expected)
    return str(uuid.uuid5(NS_IMPORT, f"{project}\n{key}\n{sha}\n{tail}\n{action}"))


def source_key(src: dict[str, Any] | None) -> str | None:
    return None if not src else f"{src.get('system')}:{src.get('path')}"


def _canon(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def split_origin(origin: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"([a-z0-9][a-z0-9-]{1,63})/([1-9][0-9]{0,18})", origin or "")
    return (m.group(1), int(m.group(2))) if m else None


def _identity_of_item(it: dict[str, Any], project: str) -> Any:
    src = it.get("source")
    if src is None:
        return {"origin": f"{project}/{it['logical_id']}"}
    if src.get("system") == ORIGIN_SYSTEM:
        return {"origin": src.get("path")}
    return src


def _identity_of_record(rec: ImportRecord) -> Any:
    ex = rec.export or {}
    if ex.get("source") is not None:
        return ex["source"]
    return {"origin": ex.get("origin")}


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
            "identity": _identity_of_record(rec),
            "describes": list(rec.describes),
            "pinned": bool(ex.get("pinned", False)),
            "stability": ex.get("stability") or "volatile",
            "importance": ex.get("importance"),
            "device_scope": ex.get("device_scope") or "all",
        }
    )


def item_fingerprint(it: dict[str, Any], project: str) -> str:
    return _canon(
        {
            "kind": it.get("kind"),
            "title": it.get("title"),
            "body_sha256": it.get("body_sha256"),
            "tags": list(it.get("tags") or []),
            "valid_from": it.get("valid_from"),
            "valid_to": it.get("valid_to"),
            "identity": _identity_of_item(it, project),
            "describes": list(it.get("describes") or []),
            "pinned": bool(it.get("pinned")),
            "stability": it.get("stability") or "volatile",
            "importance": it.get("importance"),
            "device_scope": it.get("device_scope") or "all",
        }
    )


def _in_scope(path: str, scopes: list[str]) -> bool:
    for scope in scopes:
        if scope == "" or (scope.endswith("/") and path.startswith(scope)):
            return True
        if path == scope or path.startswith(scope + "#"):  # a file scope and its sections
            return True
    return False


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
        entry = _classify_one(project, rec, by_key, by_lid, card, plan)
        if entry is None:
            continue
        entry.tokens = 0 if entry.action == "unchanged" else meter.count_text(rec.body)
        plan.entries.append(entry)

    # Items this source imported earlier under the run's paths that the run no longer produces
    produced = {r.key for r in parsed.records} | {r.key for r in parsed.rejected}
    keep_files = {s.path: s.reason for s in parsed.skipped if s.reason.split(":", 1)[0] in KEEP_REASONS}
    for k, it in sorted(by_key.items()):
        src = it.get("source") or {}
        path = str(src.get("path", ""))
        if src.get("system") != system or not _in_scope(path, parsed.scopes):
            continue
        if it.get("valid_to") is not None:  # already ended (e.g. closed by an earlier run)
            continue
        plan.in_scope += 1
        if k in produced:
            continue
        reason = keep_files.get(path.split("#", 1)[0])
        if reason is not None:
            plan.kept.append({"key": k, "reason": f"file-skipped:{reason}"})
        else:
            plan.missing.append(it)
    return plan


def _classify_one(
    project: str,
    rec: ImportRecord,
    by_key: dict[str, dict[str, Any]],
    by_lid: dict[int, dict[str, Any]],
    card: dict[str, Any] | None,
    plan: Plan,
) -> Entry | None:
    ex = rec.export
    if ex is None:
        target = by_key.get(rec.key)
        if target is None:
            return Entry(rec, "new")
        # a kind change alone is a revision too (e2e 2026-09-24 #1: feedback files imported as facts
        # become lessons on the next run)
        same = (target.get("source") or {}).get("sha256") == rec.sha256 and target.get(
            "kind", rec.kind_guess
        ) == rec.kind_guess
        return Entry(
            rec,
            "unchanged" if same else "changed",
            target["logical_id"],
            target["version_id"],
            expected=target["version_id"],
        )
    lid_of = None
    if rec.kind_guess == "project_card":
        target = card
        if target is not None and item_fingerprint(target, project) != record_fingerprint(rec):
            # the project's one card is revised only if it is still the skeleton or was itself
            # imported from this very export; a file never overwrites a curated card (Sol 43 #1)
            skeleton = target.get("source") is None and SKELETON_TAG in (target.get("tags") or [])
            if not skeleton and source_key(target.get("source")) != rec.key:
                plan.rejected.append(Reject(rec.key, "card_not_from_this_export"))
                return None
    else:
        # Sol 43 #1: an export file selects an existing item ONLY through a server-held identity:
        # the item's own source equals this file's source/origin (it was created by importing
        # this export). A bare `origin:` naming an item never selects it; the file becomes a new
        # sourced item instead. The one exception writes nothing: an origin item of this project
        # whose content is identical is `unchanged`.
        target = by_key.get(rec.key)
        origin = split_origin(ex.get("origin") or "") if ex.get("source") is None else None
        if target is None and origin is not None and origin[0] == project:
            cand = by_lid.get(origin[1])
            if cand is not None and item_fingerprint(cand, project) == record_fingerprint(rec):
                lid_of = cand
    if lid_of is not None:
        return Entry(
            rec, "unchanged", lid_of["logical_id"], lid_of["version_id"], expected=lid_of["version_id"]
        )
    if target is None:
        return Entry(rec, "new")
    lid, head = target["logical_id"], target["version_id"]
    if item_fingerprint(target, project) == record_fingerprint(rec):
        return Entry(rec, "unchanged", lid, head, expected=head)
    return Entry(rec, "changed", lid, head, expected=head)


# --------------------------------------------------------------------------- remap + close
_HEADING_LINE = re.compile(r"\A\s*#{1,6}[^\n]*\n?")
_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(body: str) -> list[str]:
    return _WORD.findall(_HEADING_LINE.sub("", body, count=1).casefold())


def body_similarity(a: str, b: str) -> float:
    """Word 3-shingle Jaccard of two bodies without their heading line (token-set Jaccard for
    bodies shorter than 3 words)."""
    ta, tb = _tokens(a), _tokens(b)
    if len(ta) < 3 or len(tb) < 3:
        sa, sb = set(ta), set(tb)
    else:
        sa = {tuple(ta[i : i + 3]) for i in range(len(ta) - 2)}
        sb = {tuple(tb[i : i + 3]) for i in range(len(tb) - 2)}
    if not sa or not sb:  # an empty / heading-only body matches nothing (Sol 43 #4)
        return 0.0
    return len(sa & sb) / len(sa | sb)


def remap(plan: Plan, old_items: list[dict[str, Any]], *, confirm_close: bool = False) -> None:
    """Pair missing items with ``new`` records: same file needs a body similarity >= 0.6, another
    file >= 0.9, and both bodies need >= 8 words (a heading-only or empty section never re-maps).
    A pair is taken only when it is UNAMBIGUOUS — the old item has exactly one candidate and that
    candidate has exactly one old item; ambiguous items stay open and are reported (Sol 43 #4).
    Matched records become revisions of the old item (their new source key moves with them). The
    other missing items are closed — unless that closes more than half of the run's in-scope items,
    whatever the scope's size, and ``confirm_close`` was not given (Sol 43 #5)."""
    olds = {it["logical_id"]: it for it in old_items}
    news = [e for e in plan.entries if e.action == "new" and e.record.export is None]
    by_old: dict[int, list[tuple[float, int]]] = {}
    by_new: dict[int, list[int]] = {}
    for lid, old in olds.items():
        if len(_tokens(old.get("body") or "")) < MIN_REMAP_WORDS:
            continue
        old_file = str((old.get("source") or {}).get("path", "")).split("#", 1)[0]
        for n, e in enumerate(news):
            if len(_tokens(e.record.body)) < MIN_REMAP_WORDS:
                continue
            sim = body_similarity(old.get("body") or "", e.record.body)
            need = REMAP_SAME_FILE if e.record.file == old_file else REMAP_OTHER_FILE
            if sim >= need:
                by_old.setdefault(lid, []).append((sim, n))
                by_new.setdefault(n, []).append(lid)
    used_old: set[int] = set()
    ambiguous: set[int] = set()
    for lid in sorted(by_old):
        cands = by_old[lid]
        old_key = source_key(olds[lid].get("source")) or ""
        if len(cands) != 1 or len(by_new[cands[0][1]]) != 1:
            ambiguous.add(lid)
            keys = sorted({news[n].record.key for _s, n in cands})
            for _s, n in cands:  # the other old items competing for the same record, too
                for other in by_new[n]:
                    ambiguous.add(other)
            plan.ambiguous.append({"from": old_key, "candidates": keys})
            continue
        sim, n = cands[0]
        used_old.add(lid)
        e = news[n]
        e.action, e.logical_id, e.head_version_id = "changed", lid, olds[lid]["version_id"]
        e.expected, e.remapped_from = olds[lid]["version_id"], old_key
        plan.remapped.append({"from": old_key, "to": e.record.key, "score": round(sim, 3)})
    for lid in sorted(ambiguous - used_old):
        plan.kept.append({"key": source_key(olds[lid].get("source")), "reason": "remap-ambiguous"})
    rest = [olds[lid] for lid in sorted(olds) if lid not in used_old and lid not in ambiguous]
    if rest and len(rest) > MASS_CLOSE_SHARE * max(plan.in_scope, 1) and not confirm_close:
        reason = f"mass-close-guard: {len(rest)} of {plan.in_scope} in-scope items; confirm to close"
        plan.kept += [{"key": source_key(it.get("source")), "reason": reason} for it in rest]
        return
    plan.closes = rest


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
        if e.remapped_from is not None:
            row["remapped_from"] = e.remapped_from
        items.append(row)
    parsed = plan.parsed
    rejected = sorted([*parsed.rejected, *plan.rejected], key=lambda r: (r.key, r.reason))
    return {
        "project": plan.project,
        "source": plan.system,
        "dry_run": dry_run,
        "counts": {
            **counts,
            "closed": len(plan.closes),
            "skipped": len(parsed.skipped),
            "rejected": len(rejected),
            "missing": len(plan.kept),
        },
        "items": items,
        "skipped": [{"path": s.path, "reason": s.reason} for s in parsed.skipped],
        "rejected": [{"key": r.key, "reason": r.reason, "date": r.date} for r in rejected],
        "duplicate_groups": parsed.duplicate_groups,
        "remapped": sorted(plan.remapped, key=lambda r: r["to"]),
        "remap_ambiguous": sorted(plan.ambiguous, key=lambda r: r["from"]),
        "closed": sorted(source_key(it.get("source")) or "" for it in plan.closes),
        "missing": sorted(plan.kept, key=lambda k: str(k["key"])),
        "token_estimate": {
            "items": counts["new"] + counts["changed"],
            "tokens": sum(e.tokens for e in plan.entries),
            "tokenizer": "o200k_base",
        },
    }


__all__ = [
    "CLIENT",
    "NS_IMPORT",
    "ORIGIN_SYSTEM",
    "Entry",
    "Plan",
    "body_similarity",
    "classify",
    "record_fingerprint",
    "remap",
    "report",
    "request_id",
    "split_origin",
]
