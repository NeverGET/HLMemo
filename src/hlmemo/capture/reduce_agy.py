"""Transcript reduction for Google's Antigravity CLI (``agy``): agy steps -> the same ``Reduced`` form.

agy logs one JSON object per *step*: ``{step_index, type, source, status, created_at, content?,
thinking?, tool_calls?: [{name, args}], truncated_fields?}``. Mapping onto the Claude reducer's form:

- ``USER_INPUT`` (source ``USER_*``) -> an owner message: the text inside ``<USER_REQUEST>``; the
  ``<ADDITIONAL_METADATA>`` / ``<USER_SETTINGS_CHANGE>`` wrappers are dropped;
- ``PLANNER_RESPONSE.content`` -> assistant text (the last one before the next owner message is the
  turn's final text); ``thinking`` is dropped;
- ``PLANNER_RESPONSE.tool_calls``: ``write_to_file`` / ``replace_file_content`` /
  ``multi_replace_file_content`` (``TargetFile``) -> files edited; ``run_command`` (``CommandLine``)
  with ``git commit -m`` -> commit messages; MCP edit tools via ``call_mcp_tool`` -> files;
- ``RUN_COMMAND.content`` (the command output) -> commit hashes from ``[branch abc1234] subject`` lines;
- ``CHECKPOINT`` (agy's own summary of earlier context) -> an EARLIER-SUMMARY only in the first
  captured segment and only before any owner message (i.e. when the log window starts mid-conversation);
- everything else (tool outputs: VIEW_FILE, GREP_SEARCH, SEARCH_WEB, SYSTEM_MESSAGE, ...) is dropped.

agy writes two logs side by side: ``transcript.jsonl`` (the hook's ``transcriptPath``; long fields
truncated) and ``transcript_full.jsonl``. Observed on real logs: either one can start later than the
other (its head is missing), so neither is a superset. ``read_segment`` merges both by ``step_index``,
the full record winning.

The cursor is the ``step_index``, not a line number: a log whose head is dropped shifts every line, so
a line offset is not stable. ``Reduced.end_line`` therefore holds the NEXT step index to capture.

Late steps: agy sometimes writes a step after its successor, and it also skips indices for good (gaps
are normal), so neither "highest index + 1" alone nor "contiguous prefix" is safe. A segment therefore
takes every step at or above the cursor PLUS every step in the ``LATE_WINDOW`` indices below it that is
not in ``seen`` (the indices already captured there, kept in the state as ``recent_steps``).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from hlmemo.capture import reduce as R

FULL_NAME = "transcript_full.jsonl"
LATE_WINDOW = 64  # observed inversions are 1-5 steps; a step this far below the cursor is not waited for
_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)
_EDIT_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}
_MCP_EDIT_RE = re.compile(r"^(?:edit_file|write_file|create_file|str_replace\w*)$")


def transcript_files(path: str | os.PathLike[str]) -> list[Path]:
    """The hook's transcript plus, when it is ``transcript.jsonl``, its ``transcript_full.jsonl``
    sibling (read last, so its untruncated records win the merge)."""
    p = Path(path)
    full = p.with_name(FULL_NAME)
    if p.name == "transcript.jsonl" and full.is_file():
        return [p, full]
    return [p]


def user_text(content: Any) -> str:
    if not isinstance(content, str):
        return ""
    # Fail closed (review 136 B1): only the text agy marks as the user's request is owner text. A step
    # without <USER_REQUEST> may carry injected blocks (environment, attached file contents) that the
    # scrub would not remove; all 197 USER_INPUT steps in the local logs of 2026-11-26 carry the tag.
    reqs = _USER_REQUEST_RE.findall(content)
    return R.clean_owner_text("\n\n".join(r.strip() for r in reqs if r.strip()))


def _owner_source(src: Any) -> bool:
    return src is None or (isinstance(src, str) and src.upper().startswith("USER"))


def _args(tc: dict[str, Any]) -> dict[str, Any]:
    a = tc.get("args")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except json.JSONDecodeError:
            return {}
    return a if isinstance(a, dict) else {}


def _tool_facts(tc: dict[str, Any], red: R.Reduced, files: dict[str, None]) -> None:
    name = tc.get("name")
    args = _args(tc)
    if name in _EDIT_TOOLS:
        p = args.get("TargetFile")
        if isinstance(p, str) and p:
            files[R._rel(p, red.cwd)] = None
    elif name == "run_command" and isinstance(args.get("CommandLine"), str):
        cm = R._commit_message_from_command(args["CommandLine"])
        if cm:
            red.commit_messages.append(R.clip(cm, 500))
    elif name == "call_mcp_tool" and _MCP_EDIT_RE.match(str(args.get("ToolName") or "")):
        inner = args.get("Arguments")
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                inner = None
        if isinstance(inner, dict):
            p = inner.get("path") or inner.get("file_path")
            if isinstance(p, str) and p:
                files[R._rel(p, red.cwd)] = None


def _wanted(idx: int, start: int, seen: frozenset[int]) -> bool:
    return idx >= start or (idx >= start - LATE_WINDOW and idx not in seen)


def parse_steps(
    lines: list[str], *, start: int = 0, seen: Iterable[int] = (), cwd_hint: str = ""
) -> R.Reduced:
    """Parse the agy steps with ``step_index >= start``, plus the not-yet-``seen`` steps of the late
    window below ``start`` (unparsable lines, e.g. a half-written last line, are skipped and picked up
    by a later run because the cursor only covers parsed steps). A step index seen twice keeps its LAST
    record (the merge order puts the full log last). ``Reduced.steps`` lists the indices covered."""
    red = R.Reduced(cwd=cwd_hint)
    seen_set = frozenset(seen)
    by_index: dict[int, dict[str, Any]] = {}
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            o = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(o, dict)
            and isinstance(o.get("step_index"), int)
            and _wanted(o["step_index"], start, seen_set)
        ):
            by_index[o["step_index"]] = o
    steps = [by_index[k] for k in sorted(by_index)]  # agy occasionally writes a step after its successor
    segs: list[R.Segment] = []
    turn_assist: list[R.Segment] = []
    files: dict[str, None] = {}

    def close_turn() -> None:
        if turn_assist:
            turn_assist[-1].final = True
            turn_assist.clear()

    for o in steps:
        ts = o.get("created_at") if isinstance(o.get("created_at"), str) else None
        if ts:
            red.first_ts = red.first_ts or ts
            red.last_ts = ts
        typ = o.get("type")
        content = o.get("content")
        if typ == "USER_INPUT":
            if not _owner_source(o.get("source")):
                continue
            text = user_text(content)
            if not text:
                continue
            close_turn()
            red.owner_messages += 1
            segs.append(R.Segment(len(segs), "owner", R.clip(text, R.OWNER_MSG_CLIP, tail=400), ts))
        elif typ == "CHECKPOINT":
            if start == 0 and red.owner_messages == 0 and isinstance(content, str) and content.strip():
                close_turn()
                segs.append(R.Segment(len(segs), "summary", R.clip(content.strip(), R.SUMMARY_CLIP), ts))
        elif typ == "PLANNER_RESPONSE":
            t = content.strip() if isinstance(content, str) else ""
            if t:
                s = R.Segment(len(segs), "assistant", t, ts)
                segs.append(s)
                turn_assist.append(s)
                red.assistant_texts += 1
            for tc in o.get("tool_calls") or []:
                if isinstance(tc, dict):
                    _tool_facts(tc, red, files)
        elif typ == "RUN_COMMAND" and isinstance(content, str):
            for m in R._COMMIT_RESULT_RE.finditer(content[:20000]):
                red.commits.append((m.group(2), m.group(3).strip()))
    close_turn()
    red.steps = [o["step_index"] for o in steps]
    red.end_line = max(start, steps[-1]["step_index"] + 1) if steps else start
    red.files = list(files)
    red.segments_total = len(segs)
    red._segs = segs  # type: ignore[attr-defined]
    return red


def reduce_steps(
    lines: list[str], *, start: int = 0, seen: Iterable[int] = (), cap: int = 40_000, cwd_hint: str = ""
) -> R.Reduced:
    return R.finish(parse_steps(lines, start=start, seen=seen, cwd_hint=cwd_hint), cap)


def recent_after(seen: Iterable[int], captured: Iterable[int], end: int) -> list[int]:
    """The ``seen`` set to store after capturing ``captured`` up to cursor ``end``: only the indices
    inside the late window below ``end`` are kept."""
    return sorted(i for i in {*seen, *captured} if end - LATE_WINDOW <= i < end)


def newest_mtime(path: str | os.PathLike[str]) -> float | None:
    """The newest modification time of the conversation's logs (None when none exists)."""
    times = []
    for f in transcript_files(path):
        try:
            times.append(f.stat().st_mtime)
        except OSError:
            continue
    return max(times) if times else None


def read_segment(
    path: str | os.PathLike[str],
    *,
    start: int = 0,
    seen: Iterable[int] = (),
    cap: int = 40_000,
    cwd_hint: str = "",
) -> R.Reduced:
    lines: list[str] = []
    for f in transcript_files(path):
        lines += f.read_text(encoding="utf-8", errors="replace").split("\n")
    return reduce_steps(lines, start=start, seen=seen, cap=cap, cwd_hint=cwd_hint)
