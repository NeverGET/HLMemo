"""Transcript reduction: Claude Code JSONL -> a compact, decision-bearing text (<= ~40k chars).

Keeps: the owner's messages, the assistant's texts (the last text of a turn in full, other narration
only when substantial), commit subjects (from ``git commit`` results) and the files touched by
Edit/Write. Drops: tool outputs (big, noisy), system reminders, hook text, sidechain (subagent)
records, thinking blocks. Pure functions; no I/O except ``read_segment``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OWNER_MSG_CLIP = 1500
FINAL_TEXT_CLIP = 2500
NARRATION_MIN = 400
NARRATION_CLIP = 800
SUMMARY_CLIP = 4000
TAIL_KEEP = 8  # the most recent segments are always kept

_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_TAG_BLOCK_RE = re.compile(
    r"<(task-notification|local-command-stdout|local-command-stderr|command-name|command-message|"
    r"command-args|user-prompt-submit-hook|bash-input|bash-stdout|bash-stderr)>.*?</\1>",
    re.S,
)
_NOT_OWNER_PREFIXES = (
    "Caveat:",
    "[Request interrupted",
    "Stop hook feedback",
    "<command-name>",
    "<local-command-",
    "Base directory for this skill",
)
_COMMIT_RESULT_RE = re.compile(r"^\[([\w./-]+)(?: \(root-commit\))? ([0-9a-f]{7,12})\] (.+)$", re.M)
_TRAILER_RE = re.compile(r"^(Co-Authored-By|Claude-Session|Signed-off-by):.*$", re.M | re.I)
_DECISION_RE = re.compile(
    r"(?i)\b(decid|decision|karar|chose|choose|rejected|reject|root cause|bug|mistake|lesson|"
    r"because|instead of|reverted|shipped|released|supersed|D-\d{3,4}|PASS|FAIL|must not|never|"
    r"always|hata|yanl[ıi]s)\b"
)
_FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_MCP_FILE_TOOL_RE = re.compile(r"^mcp__.*__(?:edit_file|write_file|create_file|str_replace\w*)$")
_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|(]\s*|\n\s*)git\s+(?:-C\s+\S+\s+)?commit(?![-\w])")


@dataclass
class Segment:
    idx: int
    kind: str  # owner | assistant | summary
    text: str
    ts: str | None = None
    final: bool = False
    score: float = 0.0


@dataclass
class Reduced:
    session_id: str = ""
    cwd: str = ""
    text: str = ""
    owner_messages: int = 0
    assistant_texts: int = 0
    commits: list[tuple[str, str]] = field(default_factory=list)  # (hash, subject), in order
    commit_messages: list[str] = field(default_factory=list)  # from the `git commit` command lines
    files: list[str] = field(default_factory=list)
    first_ts: str | None = None
    last_ts: str | None = None
    end_line: int = 0  # the line index AFTER the last line read (next segment starts here)
    segments_total: int = 0
    segments_kept: int = 0


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def clean_owner_text(text: str) -> str:
    t = _REMINDER_RE.sub("", text)
    t = _TAG_BLOCK_RE.sub("", t).strip()
    if not t or t.startswith(_NOT_OWNER_PREFIXES):
        return ""
    return t


def clip(text: str, n: int, tail: int = 0) -> str:
    if len(text) <= n:
        return text
    if tail:
        return text[: n - tail - 12].rstrip() + " [...] " + text[-tail:]
    return text[: n - 6].rstrip() + " [...]"


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _commit_message_from_command(cmd: str) -> str | None:
    cm = _GIT_COMMIT_RE.search(cmd)
    if cm is None:
        return None
    i = cm.start()
    m = re.search(
        r"-m\s+(?:\"\$\(cat <<'?(\w+)'?\n(.*?)\n\s*\1|\"((?:[^\"\\]|\\.)*)\"|'([^']*)')", cmd[i:], re.S
    )
    if not m:
        return None
    msg = m.group(2) or m.group(3) or m.group(4) or ""
    msg = _TRAILER_RE.sub("", msg).strip()
    return msg or None


def _rel(path: str, cwd: str) -> str:
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1 :]
    return path


def _parses(line: str) -> bool:
    try:
        json.loads(line)
    except json.JSONDecodeError:
        return False
    return True


def parse_lines(lines: list[str], *, start: int = 0, cwd_hint: str = "") -> Reduced:
    """Parse transcript JSONL ``lines[start:]`` into segments + facts, then pack them (see ``pack``)."""
    red = Reduced(cwd=cwd_hint)
    segs: list[Segment] = []
    turn_assist: list[Segment] = []
    files: dict[str, None] = {}

    def close_turn() -> None:
        if turn_assist:
            turn_assist[-1].final = True
            turn_assist.clear()

    for i in range(start, len(lines)):
        raw = lines[i].strip()
        if not raw:
            continue
        try:
            o = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(o, dict) or o.get("isSidechain"):
            continue
        ts = o.get("timestamp")
        if isinstance(ts, str):
            red.first_ts = red.first_ts or ts
            red.last_ts = ts
        if not red.session_id and isinstance(o.get("sessionId"), str):
            red.session_id = o["sessionId"]
        if not red.cwd and isinstance(o.get("cwd"), str):
            red.cwd = o["cwd"]
        typ = o.get("type")
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        content = msg.get("content")
        if typ == "user":
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        for m in _COMMIT_RESULT_RE.finditer(_tool_result_text(b.get("content"))[:20000]):
                            red.commits.append((m.group(2), m.group(3).strip()))
            if o.get("isMeta"):
                continue
            text = clean_owner_text(_content_text(content))
            if not text:
                continue
            if o.get("isCompactSummary") or text.startswith("This session is being continued"):
                close_turn()
                if start > 0:
                    continue  # a later segment: the summary restates what the earlier segment captured
                segs.append(Segment(len(segs), "summary", clip(text, SUMMARY_CLIP), ts))
                continue
            close_turn()
            red.owner_messages += 1
            segs.append(Segment(len(segs), "owner", clip(text, OWNER_MSG_CLIP, tail=400), ts))
        elif typ == "assistant":
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    t = (b.get("text") or "").strip()
                    if t and t not in ("No response requested.",):
                        s = Segment(len(segs), "assistant", t, ts)
                        segs.append(s)
                        turn_assist.append(s)
                        red.assistant_texts += 1
                elif b.get("type") == "tool_use":
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    name = b.get("name")
                    if name in _FILE_TOOLS or (isinstance(name, str) and _MCP_FILE_TOOL_RE.match(name)):
                        p = inp.get("file_path") or inp.get("notebook_path") or inp.get("path")
                        if isinstance(p, str) and p:
                            files[_rel(p, red.cwd)] = None
                    elif name == "Bash" and isinstance(inp.get("command"), str):
                        cm = _commit_message_from_command(inp["command"])
                        if cm:
                            red.commit_messages.append(clip(cm, 500))
    close_turn()
    red.end_line = len(lines)
    if lines and lines[-1].strip() and not _parses(lines[-1]):
        red.end_line -= 1  # the live session is still writing this line: leave it for the next segment
    red.files = list(files)
    red.segments_total = len(segs)
    red._segs = segs  # type: ignore[attr-defined]
    return red


def _prepare(seg: Segment) -> None:
    if seg.kind == "assistant":
        if seg.final:
            seg.text = clip(seg.text, FINAL_TEXT_CLIP, tail=600)
        elif len(seg.text) >= NARRATION_MIN:
            seg.text = clip(seg.text, NARRATION_CLIP)
        else:
            seg.text = ""  # short narration ("now running tests") carries no decision
    seg.score = {"owner": 100.0, "summary": 60.0}.get(seg.kind, 12.0 if seg.final else 2.0)
    if seg.kind == "assistant" and seg.text:
        seg.score += min(8.0, 2.0 * len(_DECISION_RE.findall(seg.text)))


def _render(seg: Segment) -> str:
    who = {"owner": "OWNER", "assistant": "ASSISTANT", "summary": "EARLIER-SUMMARY"}[seg.kind]
    stamp = f" {seg.ts[:16]}" if seg.ts else ""
    return f"[{who}{stamp}] {seg.text}"


def pack(segs: list[Segment], cap: int) -> tuple[str, int]:
    """Choose segments under ``cap`` chars: owner messages + the tail + the first owner message are
    mandatory; the rest by score (decision-bearing first, then recency). Returns (text, kept)."""
    live = [s for s in segs if s.text]
    sizes = {s.idx: len(_render(s)) + 2 + 55 for s in live}  # 55: worst-case gap marker
    keep: set[int] = set()
    used = 0
    first_owner = next((s for s in live if s.kind == "owner"), None)
    mandatory = [s for s in live[-TAIL_KEEP:]] + ([first_owner] if first_owner else [])
    for s in mandatory:
        if s.idx not in keep:
            keep.add(s.idx)
            used += sizes[s.idx]
    n = len(live)
    rank = {s.idx: pos for pos, s in enumerate(live)}
    rest = sorted(
        (s for s in live if s.idx not in keep),
        key=lambda s: (-(s.score + 6.0 * rank[s.idx] / max(n, 1)), -s.idx),
    )
    for s in rest:
        if used + sizes[s.idx] <= cap:
            keep.add(s.idx)
            used += sizes[s.idx]
    while used > cap:  # the mandatory tail alone is too big: drop its lowest-scored assistant text
        victims = [s for s in live if s.idx in keep and s.kind == "assistant" and s is not live[-1]]
        if not victims:
            break
        v = min(victims, key=lambda s: (s.score, s.idx))
        keep.discard(v.idx)
        used -= sizes[v.idx]
    out: list[str] = []
    prev: int | None = None
    chosen = [s for s in live if s.idx in keep]
    for s in chosen:
        if prev is not None:
            gap = sum(1 for x in live if prev < x.idx < s.idx)
            if gap:
                out.append(f"[... {gap} earlier/later turns omitted ...]")
        out.append(_render(s))
        prev = s.idx
    return "\n\n".join(out), len(chosen)


def commit_rows(red: Reduced, limit: int = 40) -> list[str]:
    """Commit lines for the facts: results carry hashes; ``-q`` commits only have their message."""
    rows: list[str] = []
    seen: set[str] = set()
    subjects: set[str] = set()
    for h, subj in red.commits:
        if h not in seen:
            seen.add(h)
            subjects.add(subj.strip()[:60])
            rows.append(f"- {h} {clip(subj, 160)}")
    for m in red.commit_messages:
        subj = m.strip().split("\n", 1)[0].strip()
        if subj and subj[:60] not in subjects:
            subjects.add(subj[:60])
            rows.append(f"- (no hash) {clip(subj, 160)}")
    if len(rows) > limit:
        return [f"- (... {len(rows) - limit} earlier commits omitted)", *rows[-limit:]]
    return rows


def facts_header(red: Reduced) -> str:
    """Programmatic, transcript-true facts (commits, files): not LLM output, so always exact."""
    parts: list[str] = []
    rows = commit_rows(red)
    if rows:
        parts.append(
            "COMMITS (hash = from git results; 'no hash' = from the commit command):\n" + "\n".join(rows)
        )
    if red.files:
        shown = red.files[:60]
        more = f" (+{len(red.files) - 60} more)" if len(red.files) > 60 else ""
        parts.append(
            "FILES EDITED/WRITTEN (editor tools only; shell edits are not listed):\n"
            + "\n".join(f"- {f}" for f in shown)
            + more
        )
    return "\n\n".join(parts)


def reduce_lines(lines: list[str], *, start: int = 0, cap: int = 40_000, cwd_hint: str = "") -> Reduced:
    red = parse_lines(lines, start=start, cwd_hint=cwd_hint)
    segs: list[Segment] = red._segs  # type: ignore[attr-defined]
    for s in segs:
        _prepare(s)
    header = facts_header(red)
    budget = max(4000, cap - len(header) - 300)
    body, kept = pack(segs, budget)
    red.segments_kept = kept
    red.text = (header + "\n\n" if header else "") + "CONVERSATION:\n" + body if body else header
    del red._segs  # type: ignore[attr-defined]
    return red


def read_segment(
    path: str | os.PathLike[str], *, start: int = 0, cap: int = 40_000, cwd_hint: str = ""
) -> Reduced:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return reduce_lines(lines, start=start, cap=cap, cwd_hint=cwd_hint)
