"""bench v1 (T1-T4) on the PRODUCTION prompts: fixtures, payloads, semantic checks, D-019 rubric.

The four librarian jobs (placement, contradiction, summary, risk) use the versioned production
prompts (``hlmemo.librarian.prompts``) and the production user message (``JOB: …`` + ``INPUT: …``,
``hlmemo.librarian.tasks.user_message``), so a v1 run measures exactly what the librarian sends.
The live gate (``eval/live/run.py``, CC-5 G-LIVE-A) runs these same fixtures through this module.
Scoring is the D-019 rubric (``eval/live/RUBRIC.md``). Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TASK_DIR = HERE / "tasks" / "v1"

#: production task name -> fixture file (bench/README: T1..T4)
FIXTURE_FILES: dict[str, str] = {
    "placement": "t1_placement.json",
    "contradiction": "t2_contradiction.json",
    "summary": "t3_summarization.json",
    "risk": "t4_risk_check.json",
}
TASKS = list(FIXTURE_FILES)
TASK_LABEL = {"placement": "T1", "contradiction": "T2", "summary": "T3", "risk": "T4"}
CORRECT_AT = 0.8


def fixture_path(task: str) -> Path:
    return TASK_DIR / FIXTURE_FILES[task]


def fixture_sha256(task: str) -> str:
    return hashlib.sha256(fixture_path(task).read_bytes()).hexdigest()


def load_fixture(task: str, raw: bytes | None = None) -> dict[str, Any]:
    """The fixture with the placement candidates expanded to ``{topic_id, summary}``."""
    cfg = json.loads(raw if raw is not None else fixture_path(task).read_bytes())
    if task == "placement":
        for c in cfg["cases"]:
            c["_candidates"] = [{"topic_id": t, "summary": cfg["topics"][t]} for t in c["candidates"]]
    return cfg


def case_payload(task: str, case: dict[str, Any]) -> dict[str, Any]:
    if task == "placement":
        return {"memory": case["memory"], "candidates": case["_candidates"]}
    if task == "contradiction":
        return {"A": case["a"], "B": case["b"]}
    if task == "summary":
        return {"items": case["items"]}
    if task == "risk":
        return {"task": case["task"], "lessons": case["lessons"]}
    raise ValueError(task)


def semantic_check(task: str, case: dict[str, Any]) -> Callable[[dict[str, Any]], str | None]:
    """Task-level checks on top of the production JSON schema (ids must exist in the input)."""

    def check(obj: dict[str, Any]) -> str | None:
        if task == "placement" and obj.get("topic_id") not in case["candidates"]:
            return f"topic_id not a candidate: {obj.get('topic_id')!r}"
        if task == "summary":
            known = {i["id"] for i in case["items"]}
            if unknown := [x for x in obj.get("clue_ids", []) if x not in known]:
                return f"clue_ids unknown: {unknown}"
        if task == "risk":
            known = {lesson["id"] for lesson in case["lessons"]}
            if unknown := [x for x in obj.get("matched_lesson_ids", []) if x not in known]:
                return f"matched_lesson_ids unknown: {unknown}"
        return None

    return check


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return 1.0 if not sa and not sb else len(sa & sb) / len(sa | sb)


def score(
    task: str, obj: dict[str, Any], case: dict[str, Any], cfg: dict[str, Any]
) -> tuple[float, dict[str, Any]]:
    g = case["gold"]
    if task == "placement":
        d = {
            "layer_ok": obj["layer"] == g["layer"],
            "topic_ok": obj["topic_id"] == g["topic_id"],
            "importance_ok": abs(obj["importance"] - g["importance"]) <= 2,
            "stability_ok": obj["stability"] == g["stability"],  # recorded, not scored
        }
        return float(d["layer_ok"] and d["topic_ok"] and d["importance_ok"]), d
    if task == "contradiction":
        d = {
            "contradicts_ok": obj["contradicts"] == g["contradicts"],
            "supersedes_ok": obj["supersedes"] == g["supersedes"],
        }
        return float(d["contradicts_ok"] and d["supersedes_ok"]), d
    if task == "summary":
        words = len(obj["summary"].split())
        j = jaccard(obj["clue_ids"], g["clue_ids"])
        ok = words <= cfg.get("max_words", 120)
        return round(0.7 * j + 0.3 * float(ok), 4), {"jaccard": j, "words": words, "length_ok": ok}
    if task == "risk":
        warn_ok = obj["warn"] == g["warn"]
        j = jaccard(obj["matched_lesson_ids"], g["matched_lesson_ids"])
        return round(0.5 * float(warn_ok) + 0.5 * j, 4), {"warn_ok": warn_ok, "jaccard": j}
    raise ValueError(task)


__all__ = [
    "CORRECT_AT",
    "FIXTURE_FILES",
    "TASKS",
    "TASK_LABEL",
    "case_payload",
    "fixture_path",
    "fixture_sha256",
    "load_fixture",
    "score",
    "semantic_check",
]
