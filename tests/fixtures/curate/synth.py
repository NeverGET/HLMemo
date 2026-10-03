"""A small SYNTHETIC ``hlm export`` (project ``demo``) and librarian-audit candidates for the
``hlm curate`` tests. Rendered with the real export writer, so the format is the real one."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hlmemo.importers.exportfmt import file_names, render_index, render_item

PROJECT = "demo"


def _src(path: str, system: str = "markdown") -> dict[str, Any]:
    return {"system": system, "path": path, "sha256": "0" * 64}


# logical_id: (version_id, kind, title, valid_from, source, body, supersedes-targets)
ITEMS: dict[int, tuple[int, str, str, str, dict[str, Any] | None, str, list[int]]] = {
    1: (101, "project_card", "demo card", "2026-09-01", None, "Project card: caps are HOUR 1 / DAY 2.\n", []),
    10: (
        110,
        "fact",
        "D-010 prod caps",
        "2026-09-01",
        _src("docs/decisions/DECISIONS.md#D-010"),
        "D-010 | 2026-09-01 | ACCEPTED | Prod caps are HOUR 1 / DAY 2 / MONTH 10 USD for the spend guard.\n",
        [],
    ),
    20: (
        220,
        "fact",
        "D-020 caps raised",
        "2026-09-20",
        _src("docs/decisions/DECISIONS.md#D-020"),
        "D-020 | 2026-09-20 | ACCEPTED | The owner raised the caps: prod caps are now HOUR 3 / DAY 8 / "
        "MONTH 60 USD.\n",
        [],
    ),
    30: (
        330,
        "doc_chunk",
        "STATUS · Now",
        "2026-09-05",
        _src("docs/status/STATUS.md#now"),
        "Status: the project is PAUSED by the owner until the budget review is done.\n"
        "On 2026-09-05 we measured precision .63 on the dev set.\n",
        [],
    ),
    40: (
        440,
        "doc_chunk",
        "RUNBOOK · Status",
        "2026-09-25",
        _src("deploy/RUNBOOK.md#status"),
        "The project is ACTIVE again since 2026-09-25; resume the weekly curation runs.\n",
        [],
    ),
    50: (
        550,
        "session_note",
        "Session note 09-26",
        "2026-09-26",
        _src("notes.md#rule-1", system="automemory"),
        "Session note: the writer is model-b from now on, replacing model-a.\n",
        [],
    ),
    60: (
        660,
        "fact",
        "D-005 writer",
        "2026-08-20",
        _src("docs/decisions/DECISIONS.md#D-005"),
        "D-005 | 2026-08-20 | ACCEPTED | The writer is model-a for every research answer.\n",
        [],
    ),
    70: (
        770,
        "fact",
        "D-030 review record",
        "2026-09-28",
        _src("docs/decisions/DECISIONS.md#D-030"),
        "D-030 | 2026-09-28 | ACCEPTED | Review record: on 2026-09-10 the reviewer found 3 HIGH issues; "
        "all were fixed.\n",
        [],
    ),
    80: (880, "lesson", "Preview first", "2026-09-02", None, "Lesson: always preview before an apply.\n", []),
    90: (
        990,
        "fact",
        "D-040 key place",
        "2026-09-29",
        _src("docs/decisions/DECISIONS.md#D-040"),
        "D-040 | 2026-09-29 | ACCEPTED | The key now lives in the prod env file, not in the shell profile.\n",
        [100],
    ),
    100: (
        1000,
        "fact",
        "D-001 key place",
        "2026-08-01",
        _src("docs/decisions/DECISIONS.md#D-001"),
        "D-001 | 2026-08-01 | ACCEPTED | The key lives in the shell profile of the operator account.\n",
        [],
    ),
}


def make_export(root: Path) -> Path:
    items: list[dict[str, Any]] = []
    for lid, (vid, kind, title, vf, src, body, sup) in ITEMS.items():
        items.append(
            {
                "logical_id": lid,
                "version_id": vid,
                "kind": kind,
                "title": title,
                "valid_from": f"{vf}T00:00:00.000000Z",
                "valid_to": None,
                "tags": [],
                "source": src,
                "describes": [],
                "links": [{"rel": "supersedes", "dst_logical_id": d} for d in sup],
                "body": body,
            }
        )
    names = file_names(items)
    root.mkdir(parents=True, exist_ok=True)
    for it in items:
        p = root / names[it["logical_id"]]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_item(it, names, PROJECT), encoding="utf-8")
    (root / "INDEX.md").write_text(render_index(items, names), encoding="utf-8")
    return root


def _proposal(qid: str, src: int, dst: int, rel: str = "contradicts", **kw: Any) -> dict[str, Any]:
    return {
        "question_id": qid,
        "kind": "contradiction",
        "status": "accepted_pending",
        "relation": rel,
        "confidence": "high",
        "reason": f"synthetic proposal {qid}",
        "subjects": [{"clue": f"v{src}"}, {"clue": f"v{dst}"}],
        "actions": [{"op": "link_insert", "rel": rel, "src_logical_id": src, "dst_logical_id": dst}],
        "flags": [],
        **kw,
    }


def audit() -> dict[str, Any]:
    """The ``ops librarian audit --json`` shape: 9 proposals exercising every candidate path."""
    return {
        "batches": [],
        "proposals": [
            _proposal("q1", 10, 20),  # reversed direction -> SUPERSESSION 20 -> 10, KEEP, final
            _proposal("q2", 30, 40),  # SUPERSESSION 40 -> 30, pass-2 FIX narrows the span, final
            _proposal("q3", 60, 50),  # SUPERSESSION 50 -> 60, src is an auto-memory note -> held
            _proposal("q4", 10, 70),  # NO_CONFLICT
            _proposal("q5", 90, 100),  # already linked in the export -> gate1 fails it
            _proposal("q6", 20, 30),  # dated measurement -> pass-2 DROP
            _proposal("q7", 10, 999),  # not in the export -> skipped
            {**_proposal("q8", 10, 20), "actions": []},  # no link action -> skipped
            _proposal("q9", 20, 10),  # the same pair as q1 -> skipped as a duplicate
        ],
    }


def answers() -> dict[str, Any]:
    """Canned verdicts for ``fake_agent.py`` keyed by the unordered pair ``"<min>-<max>"``."""
    return {
        "pass1": {
            "10-20": {
                "verdict": "SUPERSESSION",
                "direction_ok": False,
                "newer_logical_id": 20,
                "older_logical_id": 10,
                "older_span": "Prod caps are HOUR 1 / DAY 2 / MONTH 10 USD for the spend guard.",
                "newer_quote": "prod caps are now HOUR 3 / DAY 8 / MONTH 60 USD",
                "why": "caps raised by D-020",
            },
            "30-40": {
                "verdict": "SUPERSESSION",
                "direction_ok": True,
                "newer_logical_id": 40,
                "older_logical_id": 30,
                "older_span": "the project is PAUSED by the owner until the budget review is done.",
                "newer_quote": "The project is ACTIVE again since 2026-09-25",
                "why": "pause ended",
            },
            "50-60": {
                "verdict": "SUPERSESSION",
                "direction_ok": False,
                "newer_logical_id": 50,
                "older_logical_id": 60,
                "older_span": "The writer is model-a for every research answer.",
                "newer_quote": "the writer is model-b from now on, replacing model-a.",
                "why": "writer changed",
            },
            "90-100": {
                "verdict": "SUPERSESSION",
                "direction_ok": True,
                "newer_logical_id": 90,
                "older_logical_id": 100,
                "older_span": "The key lives in the shell profile of the operator account.",
                "newer_quote": "The key now lives in the prod env file, not in the shell profile.",
                "why": "key moved",
            },
            "20-30": {
                "verdict": "SUPERSESSION",
                "direction_ok": False,
                "newer_logical_id": 20,
                "older_logical_id": 30,
                "older_span": "On 2026-09-05 we measured precision .63 on the dev set.",
                "newer_quote": "The owner raised the caps: prod caps are now HOUR 3",
                "why": "drafted wrongly on purpose",
            },
        },
        "pass1_default": {"verdict": "NO_CONFLICT", "direction_ok": None, "why": "canned: compatible"},
        "pass2": {
            "40-30": {
                "verdict": "FIX",
                "failed_tests": [4],
                "reason": "narrow the span",
                "fix": {"older_span": "the project is PAUSED by the owner", "newer_quote": None},
            },
            "20-30": {"verdict": "DROP", "failed_tests": [1, 3], "reason": "dated measurement is history"},
        },
        "pass2_default": {"verdict": "KEEP", "failed_tests": [], "reason": "canned: all 5 tests pass"},
        "map": {
            "pairs": [
                {"newer_logical_id": 20, "older_logical_id": 10, "area": "caps", "why": "caps raised"},
                {"newer_logical_id": 40, "older_logical_id": 30, "area": "status", "why": "pause ended"},
            ]
        },
        "fail": {},
    }


def write_inputs(tmp: Path, *, fail: dict[str, int] | None = None) -> dict[str, Path]:
    exp = make_export(tmp / "export")
    cand = tmp / "audit.json"
    cand.write_text(json.dumps(audit()), encoding="utf-8")
    ans = answers()
    ans["fail"] = fail or {}
    ans_path = tmp / "answers.json"
    ans_path.write_text(json.dumps(ans), encoding="utf-8")
    return {"export": exp, "candidates": cand, "answers": ans_path}
