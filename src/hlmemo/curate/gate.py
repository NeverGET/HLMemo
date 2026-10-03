"""The deterministic parts of ``hlm curate``: candidates, record builder, gate, FIXes, pass-2
combination and the authority filter. Pure functions over an ``exportdir.Export``; no LLM, no DB.

Record contract: ``ops/backfill_links`` (``hlm links backfill --proposals``). Extra keys (``cid``,
``src_clue``, ``dst_clue``, ``why``, ``origin`` ...) are annotations the backfill ignores.
"""

from __future__ import annotations

import fnmatch
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from hlmemo.core.explicit_supersession import _cyclic
from hlmemo.curate.exportdir import Export, Item

SPAN_MIN, SPAN_MAX = 20, 300
REQUIRED = (
    "status",
    "project",
    "src_logical_id",
    "src_vid",
    "dst_logical_id",
    "dst_vid",
    "scope",
    "older_span",
    "newer_quote",
    "relation",
    "confidence",
)
PASS1_VERDICTS = ("CONTRADICTION", "SUPERSESSION", "REFINES_OK", "NO_CONFLICT", "UNCLEAR")
PASS2_VERDICTS = ("KEEP", "FIX", "DROP")
# Owner policy (D-244): only a decision row, a plan or decision doc, the RUNBOOK, USAGE or CLAUDE.md
# may be the NEWER side of an applied link; everything else is held for re-anchoring. Globs match
# ``source.path`` without its ``#anchor`` (fnmatch: ``*`` also crosses ``/``).
DEFAULT_AUTHORITY = (
    "docs/decisions/DECISIONS.md",
    "docs/decisions/*",
    "deploy/RUNBOOK.md",
    "docs/USAGE.md",
    "CLAUDE.md",
)


@dataclass(frozen=True)
class Labels:
    """Provenance labels written into every record (configuration, never a hard-coded vendor)."""

    model: str = "agent"
    profile: str = "curate-agent"
    prompt_version: str = "curate-v1"
    generator: str = "hlm-curate"
    confidence: float = 0.8
    relation: str = "updates"


# ------------------------------------------------------------------ candidates
def subject(it: Item) -> dict[str, Any]:
    return {
        "logical_id": it.logical_id,
        "version_id": it.version_id,
        "clue": it.clue,
        "title": it.title,
        "file": it.file,
        "valid_from": it.valid_from,
        "source_path": it.source_path,
    }


def _raw_candidates(data: Any) -> list[tuple[str, dict[str, Any]]]:
    """``(origin, raw)`` from a librarian audit (``{"proposals": [...]}`` or a list of proposals with
    ``actions``) or a pairs file (``{"pairs": [...]}``, a mapping pass's output shape)."""
    if isinstance(data, dict) and isinstance(data.get("proposals"), list):
        return [("librarian", p) for p in data["proposals"] if isinstance(p, dict)]
    if isinstance(data, dict) and isinstance(data.get("pairs"), list):
        return [(str(data.get("origin") or "file"), p) for p in data["pairs"] if isinstance(p, dict)]
    if isinstance(data, list):
        out: list[tuple[str, dict[str, Any]]] = []
        for p in data:
            if isinstance(p, dict):
                out.append(("librarian" if "actions" in p else "file", p))
        return out
    raise ValueError("candidates: expected {'proposals': [...]}, {'pairs': [...]} or a list")


def normalize_candidates(
    data: Any, export: Export, *, status: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Candidates (``cid`` c0001...) and skipped rows ``{reason, ...}``. A candidate is an UNORDERED
    pair of two current, linkable heads; the proposed direction is kept only as a hint."""
    cands: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[frozenset[int]] = set()
    for n, (origin, raw) in enumerate(_raw_candidates(data)):
        ref = {"input_index": n, "origin": origin, "question_id": raw.get("question_id")}
        if origin == "librarian":
            if status is not None and raw.get("status") != status:
                skipped.append({**ref, "reason": "status_filtered"})
                continue
            acts = [
                a
                for a in raw.get("actions") or []
                if isinstance(a, dict)
                and a.get("op") == "link_insert"
                and a.get("src_logical_id") is not None
                and a.get("dst_logical_id") is not None
            ]
            if not acts:
                skipped.append({**ref, "reason": "no_link_action"})
                continue
            a = acts[0]
            src, dst = int(a["src_logical_id"]), int(a["dst_logical_id"])
            extra = {
                "relation": a.get("rel") or raw.get("relation"),
                "reason": str(raw.get("reason") or ""),
                "confidence": raw.get("confidence"),
                "flags": list(raw.get("flags") or []),
                "verification": raw.get("verification"),
                "status": raw.get("status"),
            }
        else:
            try:
                src, dst = int(raw["newer_logical_id"]), int(raw["older_logical_id"])
            except (KeyError, TypeError, ValueError):
                skipped.append({**ref, "reason": "bad_pair"})
                continue
            extra = {
                "relation": "supersedes",
                "reason": str(raw.get("why") or raw.get("reason") or ""),
                "area": raw.get("area"),
            }
        ref.update(proposed_src=src, proposed_dst=dst)
        if src == dst:
            skipped.append({**ref, "reason": "self_pair"})
            continue
        missing = [lid for lid in (src, dst) if export.head(lid) is None]
        if missing:
            skipped.append({**ref, "reason": "not_in_export", "missing": missing})
            continue
        key = frozenset((src, dst))
        if key in seen:
            skipped.append({**ref, "reason": "duplicate_pair"})
            continue
        seen.add(key)
        s, d = export.head(src), export.head(dst)
        assert s is not None and d is not None
        cands.append(
            {
                "cid": f"c{len(cands) + 1:04d}",
                **{k: v for k, v in ref.items() if k != "input_index"},
                **extra,
                "subjects": [subject(s), subject(d)],
            }
        )
    return cands, skipped


def slices(items: list[Any], n: int) -> list[list[Any]]:
    """``n`` contiguous, near-equal, non-empty slices (fewer when there are fewer items)."""
    n = max(1, min(n, len(items)))
    if not items:
        return []
    size, extra = divmod(len(items), n)
    out, i = [], 0
    for k in range(n):
        j = i + size + (1 if k < extra else 0)
        out.append(items[i:j])
        i = j
    return out


# ------------------------------------------------------------------ build (pass-1 verdicts -> records)
def build_records(
    cands: list[dict[str, Any]],
    verdicts: dict[str, dict[str, Any]],
    export: Export,
    *,
    project: str,
    labels: Labels,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One ``hlm links backfill`` record per SUPERSESSION verdict with ids and spans. Returns
    ``(records, skipped)``; skipped rows carry ``cid`` and ``reason``. No verbatim check here:
    that is the gate's job."""
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    pairs: set[tuple[int, int]] = set()
    for c in cands:
        cid = c["cid"]
        v = verdicts.get(cid)
        if v is None:
            skipped.append({"cid": cid, "reason": "no_verdict"})
            continue
        if v["verdict"] != "SUPERSESSION":
            skipped.append({"cid": cid, "reason": f"verdict_{v['verdict'].lower()}"})
            continue
        n, o = v.get("newer_logical_id"), v.get("older_logical_id")
        if not (n and o and v.get("older_span") and v.get("newer_quote")):
            skipped.append({"cid": cid, "reason": "no_spans_or_ids"})
            continue
        n, o = int(n), int(o)
        if {n, o} != {s["logical_id"] for s in c["subjects"]}:
            skipped.append({"cid": cid, "reason": "ids_not_in_candidate"})
            continue
        src, dst = export.head(n), export.head(o)
        if src is None or dst is None:
            skipped.append({"cid": cid, "reason": "not_in_export"})
            continue
        if (n, o) in pairs:
            skipped.append({"cid": cid, "reason": "duplicate_pair"})
            continue
        pairs.add((n, o))
        rec: dict[str, Any] = {
            "status": "proposed",
            "project": project,
            "src_logical_id": n,
            "src_vid": src.version_id,
            "dst_logical_id": o,
            "dst_vid": dst.version_id,
            "scope": "part",
            "older_span": v["older_span"],
            "newer_quote": v["newer_quote"],
            "relation": labels.relation,
            "confidence": labels.confidence,
            "model": labels.model,
            "profile": labels.profile,
            "prompt_version": labels.prompt_version,
            "generator": labels.generator,
            "cid": cid,
            "origin": c.get("origin"),
            "src_clue": src.clue,
            "dst_clue": dst.clue,
            "why": str(v.get("why") or "")[:200],
        }
        if c.get("question_id"):
            rec["question_id"] = c["question_id"]
        records.append(rec)
    return records, skipped


# ------------------------------------------------------------------ gate
@dataclass
class GateResult:
    passed: list[dict[str, Any]]
    rows: list[dict[str, Any]] = field(default_factory=list)  # one per input record, input order

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["errors"]]

    def counts(self) -> dict[str, Any]:
        errs: Counter[str] = Counter()
        for r in self.rows:
            for e in r["errors"]:
                errs[e.split("(")[0]] += 1
        return {
            "records": len(self.rows),
            "passed": len(self.passed),
            "failed": len(self.failed),
            "errors": dict(sorted(errs.items())),
            "warnings": sum(1 for r in self.rows if r["warnings"]),
        }


def _record_errors(r: dict[str, Any], export: Export, project: str) -> tuple[list[str], list[str]]:
    errs = [f"missing:{k}" for k in REQUIRED if k not in r]
    warns: list[str] = []
    if errs:
        return errs, warns
    try:
        sl, sv, dl, dv = (int(r[k]) for k in ("src_logical_id", "src_vid", "dst_logical_id", "dst_vid"))
    except (TypeError, ValueError):
        return ["bad_id"], warns
    heads: dict[str, Item | None] = {}
    for side, lid, vid in (("src", sl, sv), ("dst", dl, dv)):
        it = export.head(lid)
        heads[side] = it
        if lid in export.card_ids:
            errs.append(f"{side}_is_project_card")
        elif it is None:
            errs.append(f"{side}_not_current")
        elif it.version_id != vid:
            errs.append(f"{side}_vid_stale(head v{it.version_id})")
    if sl == dl:
        errs.append("self_link")
    if r["scope"] != "part":
        errs.append("scope_not_part")
    if r["status"] != "proposed":
        errs.append("status_not_proposed")
    if r["project"] != project:
        errs.append("project_mismatch")
    for key, side in (("older_span", "dst"), ("newer_quote", "src")):
        q = r[key]
        if not isinstance(q, str):
            errs.append(f"{key}_not_text")
            continue
        if not (SPAN_MIN <= len(q) <= SPAN_MAX):
            errs.append(f"{key}_len({len(q)})")
        it = heads[side]
        if it is not None:
            n = it.body.count(q)
            if n == 0:
                errs.append(f"{key}_not_verbatim")
            elif n > 1:
                errs.append(f"{key}_ambiguous({n})")
    if (sl, dl) in export.live_supersedes or (dl, sl) in export.live_supersedes:
        errs.append("already_linked")
    s, d = heads["src"], heads["dst"]
    if s is not None and d is not None and s.valid_from and d.valid_from and s.valid_from < d.valid_from:
        warns.append("WARN_src_older_valid_from")
    return errs, warns


def gate(records: list[dict[str, Any]], export: Export, project: str) -> GateResult:
    """Verbatim + unique quotes (20-300 chars) in the CURRENT head bodies, current vids, no self link,
    no duplicate (the first clean record of a pair wins), no pair in both directions, no cycle with
    the other records or the export's live ``supersedes`` links, not already linked; a WARN when the
    src's valid_from is older than the dst's."""
    rows: list[dict[str, Any]] = []
    for k, r in enumerate(records):
        errs, warns = _record_errors(r, export, project)
        rows.append(
            {
                "k": k,
                "cid": r.get("cid"),
                "src": r.get("src_clue") or r.get("src_vid"),
                "dst": r.get("dst_clue") or r.get("dst_vid"),
                "errors": errs,
                "warnings": warns,
            }
        )

    def pair(k: int) -> tuple[int, int]:
        return int(records[k]["src_logical_id"]), int(records[k]["dst_logical_id"])

    clean: list[int] = []
    seen: set[tuple[int, int]] = set()
    for row in rows:
        if row["errors"]:
            continue
        p = pair(row["k"])
        if p in seen:
            row["errors"].append("duplicate_pair")
            continue
        seen.add(p)
        clean.append(row["k"])
    both = {k for k in clean if pair(k)[::-1] in seen}
    for k in both:
        rows[k]["errors"].append("both_directions")
    clean = [k for k in clean if k not in both]
    cyclic = _cyclic([*(pair(k) for k in clean), *export.live_supersedes])
    for k in clean:
        if pair(k) in cyclic:
            rows[k]["errors"].append("cycle")
    passed = [records[r["k"]] for r in rows if not r["errors"]]
    return GateResult(passed=passed, rows=rows)


# ------------------------------------------------------------------ pass 2: combination + FIXes
def _merge_text(values: list[str]) -> tuple[str | None, bool]:
    """``(value, conflict)``: equal values merge; nested values take the SHORTER (the minimal span);
    two different, non-nested values are a conflict."""
    uniq = list(dict.fromkeys(values))
    if not uniq:
        return None, False
    if len(uniq) == 1:
        return uniq[0], False
    shortest = min(uniq, key=len)
    if all(shortest in u for u in uniq):
        return shortest, False
    return None, True


def combine(verdicts: list[dict[str, Any]], expected: int) -> tuple[str, dict[str, Any] | None]:
    """Precision first. ``INCOMPLETE`` when fewer than ``expected`` reviewers answered (a failed
    slice: held, never silently kept or dropped); any DROP -> ``DROP``; any FIX -> ``FIX`` with the
    merged fix, or ``CONFLICT`` when the FIXes disagree; else ``KEEP``."""
    if len(verdicts) < expected:
        return "INCOMPLETE", None
    if any(v["verdict"] == "DROP" for v in verdicts):
        return "DROP", None
    fixes = [v.get("fix") or {} for v in verdicts if v["verdict"] == "FIX"]
    if not fixes:
        return "KEEP", None
    merged: dict[str, Any] = {}
    for key in ("older_span", "newer_quote"):
        value, conflict = _merge_text([f[key] for f in fixes if f.get(key)])
        if conflict:
            return "CONFLICT", None
        if value is not None:
            merged[key] = value
    srcs = list(
        dict.fromkeys(
            (int(f["src_logical_id"]), int(f["src_vid"]))
            for f in fixes
            if f.get("src_logical_id") is not None and f.get("src_vid") is not None
        )
    )
    if len(srcs) > 1:
        return "CONFLICT", None
    if srcs:
        merged["src_logical_id"], merged["src_vid"] = srcs[0]
    return "FIX", merged


def apply_fix(record: dict[str, Any], fix: dict[str, Any]) -> dict[str, Any]:
    """The record with the FIX's span, quote and/or better src (re-gated afterwards)."""
    out = dict(record)
    for key in ("older_span", "newer_quote"):
        if fix.get(key):
            out[key] = fix[key]
    if fix.get("src_logical_id") is not None:
        out["src_logical_id"] = int(fix["src_logical_id"])
        out["src_vid"] = int(fix["src_vid"])
        out["src_clue"] = f"v{int(fix['src_vid'])}"
    out["fixed"] = True
    return out


# ------------------------------------------------------------------ authority filter
def is_authoritative(item: Item | None, globs: Iterable[str]) -> bool:
    if item is None or not item.source_path:
        return False
    path = item.source_path.split("#", 1)[0]
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def authority_split(
    records: list[dict[str, Any]], export: Export, globs: Iterable[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(authoritative, held)``: a record is applied only when its NEWER item (src) comes from an
    allowlisted source; every other record is HELD (``held_reason: authority``) for re-anchoring."""
    globs = tuple(globs)
    keep: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for r in records:
        it = export.head(int(r["src_logical_id"]))
        if is_authoritative(it, globs):
            keep.append(r)
        else:
            held.append({**r, "held_reason": "authority", "src_source": it.source_path if it else None})
    return keep, held


__all__ = [
    "DEFAULT_AUTHORITY",
    "PASS1_VERDICTS",
    "PASS2_VERDICTS",
    "REQUIRED",
    "SPAN_MAX",
    "SPAN_MIN",
    "GateResult",
    "Labels",
    "apply_fix",
    "authority_split",
    "build_records",
    "combine",
    "gate",
    "is_authoritative",
    "normalize_candidates",
    "slices",
    "subject",
]
