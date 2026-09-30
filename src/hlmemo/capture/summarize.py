"""The summarizer: ``claude -p`` (Haiku) with a strict JSON schema, then validation + grounding checks.

Failure anywhere (non-zero exit, timeout, bad JSON, schema violation) -> ``None``: the caller writes
only the minimal, programmatic session note. Garbage is never written.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

MAX_NOTE_WORDS = 1500
MAX_DECISIONS = 12
MAX_DECISION_CHARS = 300
MAX_LESSONS = 8
MAX_UNCERTAIN = 10
MAX_TITLE = 200
MAX_TAGS = 6
TIMEOUT_S = 300

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["notes", "decisions", "lessons", "uncertain"],
    "properties": {
        "notes": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "lessons": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "body", "tags", "evidence"],
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                },
            },
        },
        "uncertain": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = """You are the session-capture summarizer of a long-term memory system for coding agents.
You receive a REDUCED transcript of one coding session (the owner's messages, the assistant's texts,
git commits, files edited). Output ONLY the JSON object required by the schema.

Non-negotiable rules (a wrong memory is worse than no memory):
1. Use ONLY what is in the transcript. No outside knowledge, no speculation, no "probably", no
   inferred intent. If something is unclear, unfinished, contradicted later, or only proposed and
   never confirmed, put it in "uncertain" instead of stating it as fact.
2. "notes": a factual session note in plain Markdown, English (keep Turkish terms or quotes as they
   are). Be concise: usually 200-600 words; go up to 1500 only for a very large multi-day session.
   Say what was DECIDED, CHANGED, DONE and what is still OPEN or
   BLOCKED. Every concrete claim should point to evidence: a file path, a commit hash from the
   COMMITS list, a decision id (D-xxx) or a date that appears in the transcript. Never invent a hash,
   path, id, number or date. Prefer the state at the END of the session; if an early decision was
   reversed later, report only the final state and say it was reversed.
3. "decisions": at most 12 one-line statements of decisions that were EXPLICITLY made or approved
   (by the owner, or by the assistant and accepted). Each one self-contained and under 300 chars.
   Not plans, not ideas, not options that were only discussed.
4. "lessons": ONLY where a mistake, failure, surprise or learning is EXPLICIT in the transcript
   (something went wrong and the cause was identified, or the owner corrected the assistant, or a
   rule was stated as a lesson). Each: a short title, a body stating the rule, the reason and how to
   apply it using ONLY what the transcript says (add no advice or details of your own), up to 6
   lowercase tags, and "evidence": a VERBATIM quote (under 200 chars) copied from the
   transcript that supports it. Zero lessons is the normal, expected outcome for most sessions.
5. "uncertain": things a reader must not treat as settled (unverified claims, open questions,
   results reported but not confirmed, conflicts between statements).
6. Ignore pleasantries, tool chatter and anything that is only process noise. Do not include
   secrets, tokens, passwords or email addresses even if you see them."""


def build_user_prompt(reduced_text: str, *, cwd: str, date: str) -> str:
    return (
        f"Session span (UTC dates): {date}\nProject directory: {cwd}\n\n"
        "REDUCED TRANSCRIPT (owner = the human; earlier parts may be omitted where marked):\n"
        "<<<TRANSCRIPT\n" + reduced_text + "\nTRANSCRIPT>>>\n"
        "(The lines <<<TRANSCRIPT and TRANSCRIPT>>> are delimiters, not part of the session.)\n\n"
        "Produce the JSON object now."
    )


@dataclass
class LlmRun:
    data: dict[str, Any] | None = None
    error: str | None = None
    latency_s: float = 0.0
    cost_usd: float | None = None
    input_tokens: int = 0  # incl. cache creation/read
    output_tokens: int = 0
    raw_chars: int = 0


def _extract(envelope: dict[str, Any]) -> dict[str, Any] | None:
    so = envelope.get("structured_output")
    if isinstance(so, dict):
        return so
    res = envelope.get("result")
    if isinstance(res, dict):
        return res
    if isinstance(res, str):
        s = res.strip()
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
        try:
            v = json.loads(s)
        except json.JSONDecodeError:
            return None
        return v if isinstance(v, dict) else None
    return None


def run_claude(prompt: str, *, model: str, claude_bin: str = "claude", timeout_s: int = TIMEOUT_S) -> LlmRun:
    """One ``claude -p`` call. cwd is a scratch dir (never a mapped project) and HLM_CAPTURE=off so the
    child's own SessionEnd hook is a no-op (no recursion)."""
    out = LlmRun()
    cmd = [
        claude_bin, "-p", "--model", model,
        "--system-prompt", SYSTEM_PROMPT,
        "--tools", "",
        "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
        "--setting-sources", "",
        "--effort", "low", "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
    ]  # fmt: skip
    env = dict(os.environ)
    env["HLM_CAPTURE"] = "off"
    t0 = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="hlm-capture-") as tmp:
            proc = subprocess.run(  # noqa: S603
                cmd, input=prompt, capture_output=True, text=True, timeout=timeout_s, cwd=tmp, env=env
            )
    except (OSError, subprocess.SubprocessError) as exc:
        out.error = f"claude_failed:{type(exc).__name__}"
        out.latency_s = time.monotonic() - t0
        return out
    out.latency_s = time.monotonic() - t0
    if proc.returncode != 0:
        out.error = f"claude_exit_{proc.returncode}"
        return out
    out.raw_chars = len(proc.stdout)
    try:
        env_json = json.loads(proc.stdout)
    except json.JSONDecodeError:
        out.error = "envelope_not_json"
        return out
    if not isinstance(env_json, dict) or env_json.get("is_error"):
        out.error = "claude_reported_error"
        return out
    usage = env_json.get("usage") or {}
    out.cost_usd = (
        env_json.get("total_cost_usd") if isinstance(env_json.get("total_cost_usd"), (int, float)) else None
    )
    out.input_tokens = int(
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )
    out.output_tokens = int(usage.get("output_tokens") or 0)
    out.data = _extract(env_json)
    if out.data is None:
        out.error = "no_json_payload"
    return out


# --------------------------------------------------------------------------- validation


class SummaryInvalid(ValueError):
    pass


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _str_list(v: Any, what: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise SummaryInvalid(f"{what} must be a list of strings")
    return [x.strip() for x in v if x.strip()]


@dataclass
class Summary:
    notes: str
    decisions: list[str]
    lessons: list[dict[str, Any]]
    uncertain: list[str]
    dropped: dict[str, int] = field(default_factory=dict)


_HASH_RE = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
_DID_RE = re.compile(r"\bD-\d{3,4}\b")


def ungrounded_refs(text: str, transcript: str) -> list[str]:
    """Commit hashes and decision ids named in ``text`` that do not occur in the transcript."""
    hay = transcript.lower()
    known = set(_HASH_RE.findall(hay))
    bad: list[str] = []
    for m in _HASH_RE.finditer(text.lower()):
        x = m.group(0)
        if x not in hay and not any(h.startswith(x) or x.startswith(h) for h in known):
            bad.append(x)
    for m in _DID_RE.finditer(text):
        if m.group(0).lower() not in hay:
            bad.append(m.group(0))
    return sorted(set(bad))


def validate_summary(obj: Any, transcript: str) -> Summary:
    """Strict validation. Shape errors raise ``SummaryInvalid`` (-> minimal fallback note). Soft problems
    are repaired or dropped and counted: over-long lists are clipped, lessons without a verbatim
    evidence quote are dropped, unverifiable hashes/ids are moved to ``uncertain``."""
    if not isinstance(obj, dict):
        raise SummaryInvalid("not an object")
    extra = set(obj) - {"notes", "decisions", "lessons", "uncertain"}
    if extra:
        raise SummaryInvalid(f"unexpected keys {sorted(extra)}")
    notes = obj.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        raise SummaryInvalid("notes missing or empty")
    dropped: dict[str, int] = {}
    notes = notes.strip()
    words = notes.split()
    if len(words) > MAX_NOTE_WORDS:
        notes = " ".join(words[:MAX_NOTE_WORDS]) + "\n\n[note clipped at 1500 words]"
        dropped["notes_clipped"] = len(words) - MAX_NOTE_WORDS
    decisions = _str_list(obj.get("decisions", []), "decisions")
    decisions = [d[: MAX_DECISION_CHARS - 1] + "…" if len(d) > MAX_DECISION_CHARS else d for d in decisions]
    if len(decisions) > MAX_DECISIONS:
        dropped["decisions_over_cap"] = len(decisions) - MAX_DECISIONS
        decisions = decisions[:MAX_DECISIONS]
    uncertain = _str_list(obj.get("uncertain", []), "uncertain")[:MAX_UNCERTAIN]
    raw_lessons = obj.get("lessons", [])
    if not isinstance(raw_lessons, list):
        raise SummaryInvalid("lessons must be a list")
    hay = _norm_ws(transcript)
    lessons: list[dict[str, Any]] = []
    for ls in raw_lessons:
        if not isinstance(ls, dict):
            raise SummaryInvalid("lesson is not an object")
        title, body, ev = ls.get("title"), ls.get("body"), ls.get("evidence")
        tags = ls.get("tags", [])
        if not (isinstance(title, str) and isinstance(body, str) and isinstance(ev, str)):
            raise SummaryInvalid("lesson fields must be strings")
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise SummaryInvalid("lesson tags must be strings")
        title, body, ev = title.strip(), body.strip(), ev.strip()
        if not title or not body:
            dropped["lessons_empty"] = dropped.get("lessons_empty", 0) + 1
            continue
        if len(ev) < 12 or _norm_ws(ev).strip(".…") not in hay:
            dropped["lessons_ungrounded"] = dropped.get("lessons_ungrounded", 0) + 1
            continue
        lessons.append(
            {
                "title": title[:MAX_TITLE],
                "body": body,
                "tags": [t.strip().lower()[:40] for t in tags if t.strip()][:MAX_TAGS],
                "evidence": ev[:300],
            }
        )
    lessons = lessons[:MAX_LESSONS]
    allt = "\n".join([notes, *decisions, *(f"{x['title']} {x['body']}" for x in lessons)])
    bad = ungrounded_refs(allt, transcript)
    if bad:
        uncertain.append(
            "References in this note that could not be found in the transcript: " + ", ".join(bad)
        )
        dropped["ungrounded_refs"] = len(bad)
    return Summary(notes, decisions, lessons, uncertain, dropped)
