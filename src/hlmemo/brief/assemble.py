"""Assemble the brief: pure, deterministic, LLM-free. Verbatim memory lines only.

Sections, in this fixed order (each skipped when empty): Now (card) / Recent session decisions / Open /
Lessons / Pending review / the "as of" footer. Every item line starts with its handle (``[v123]``) so
the agent can drill down (``memory.drilldown`` / ``memory.raw``). Lines are cut with an ellipsis, never
rewritten. The whole text is held to ``budget`` o200k_base tokens (the repo's meter, ``core/budget.py``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from hlmemo.brief.fetch import Item, Snapshot
from hlmemo.core import METER_VERSION

SKELETON_MARK = "Skeleton card (D-015)"  # core/skeleton_card.skeleton_body
DECISIONS_TITLE = "Recent session decisions (auto-captured, unreviewed)"  # include_auto = true
DECISIONS_TITLE_REVIEWED = "Recent session decisions"  # default: auto-captured notes are hidden
SECTIONS = ("Now", DECISIONS_TITLE, "Open", "Lessons", "Pending review")

MAX_SESSIONS = 3
MAX_LESSONS = 5
DECISIONS_PER_NOTE = 4
OPEN_PER_NOTE = 3
CARD_STALE_DAYS = 3
LINE_CHARS = 220
CARD_TOKENS = 420
MIN_CARD_TOKENS = 120

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+(.*\S)\s*$")
_DECISION_HEAD = ("decision",)
_OPEN_HEAD = ("open", "uncertain", "unverified", "unresolved", "to do", "todo", "next step", "blocked")

_enc = None


def _encoder():
    global _enc
    if _enc is None:
        import tiktoken

        _enc = tiktoken.get_encoding(METER_VERSION)
    return _enc


def count_tokens(text: str) -> int:
    """o200k_base tokens; if the vocabulary is unavailable offline, a pessimistic chars/3 estimate."""
    try:
        return len(_encoder().encode(text, disallowed_special=()))
    except Exception:  # noqa: BLE001
        return len(text) // 3 + 1


def truncate_tokens(text: str, n: int) -> str:
    """First ``n`` tokens of ``text`` plus an ellipsis when cut."""
    try:
        ids = _encoder().encode(text, disallowed_special=())
        if len(ids) <= n:
            return text
        return _encoder().decode(ids[:n]).rstrip() + "…"
    except Exception:  # noqa: BLE001
        return text if len(text) <= n * 3 else text[: n * 3].rstrip() + "…"


def cut(text: str, limit: int = LINE_CHARS) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def note_sections(body: str) -> tuple[list[str], list[str]]:
    """(decision lines, open/uncertain lines): the bullets under the note's ``Decisions`` and
    ``Open``/``Uncertain`` headings, verbatim."""
    decisions: list[str] = []
    opens: list[str] = []
    bucket: list[str] | None = None
    for line in body.splitlines():
        m = _HEADING.match(line)
        if m:
            h = m.group(1).lower()
            if any(k in h for k in _DECISION_HEAD):
                bucket = decisions
            elif any(k in h for k in _OPEN_HEAD):
                bucket = opens
            else:
                bucket = None
            continue
        b = _BULLET.match(line)
        if b and bucket is not None:
            bucket.append(b.group(1))
    return decisions, opens


def _tag(it: Item) -> str:
    return f"{it.handle} auto" if it.auto else it.handle


def note_stamp(it: Item) -> datetime | None:
    return it.recorded_at or it.valid_from


def note_tag(it: Item) -> str:
    """``YYYY-MM-DD v123 auto``: the note date, the handle, the auto-captured marker."""
    t = note_stamp(it)
    return f"{t.date().isoformat()} {_tag(it)}" if t else _tag(it)


def lesson_line(it: Item) -> str:
    """Title + the first prose line of the body (verbatim, cut)."""
    first = next((ln.strip() for ln in it.body.splitlines() if ln.strip()), "")
    if first.lower() == it.title.strip().lower():
        first = ""
    text = cut(it.title, 90) + (f" — {cut(first, 130)}" if first else "")
    return f"- [{_tag(it)}] {text}"


def pending_line(snap: Snapshot, auto_n: int = 0) -> str | None:
    """Questions + auto-captured items awaiting review. ``auto_n`` counts only what the brief fetched
    (the newest session notes and lessons), so it is shown as "at least"."""
    if snap.pending <= 0 and auto_n <= 0:
        return None
    parts = []
    if snap.pending > 0:
        parts.append(f"{snap.pending} librarian question{'s' if snap.pending != 1 else ''}")
    if auto_n > 0:
        parts.append(f"at least {auto_n} auto-captured item{'s' if auto_n != 1 else ''}")
    line = f"- {' + '.join(parts)} await review (memory.answer)"
    n = snap.notices[0] if snap.pending > 0 and snap.notices else None
    if n and isinstance(n.get("text"), str):
        clues = " ".join(str(c) for c in (n.get("clues") or [])[:3])
        line += f"; newest: {cut(n['text'], 100)}" + (f" [{clues}]" if clues else "")
    return line


@dataclass
class Brief:
    text: str
    tokens: int
    sections: list[str] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    truncated: bool = False


def _render(
    slug: str,
    dec_title: str,
    card: tuple[str, str, bool] | None,
    dec: list[str],
    opn: list[str],
    les: list[str],
    pend: str | None,
    as_of: str,
) -> tuple[str, list[str]]:
    out = [f"# Memory brief: project {slug} (read-only, verbatim lines from HLMemo, not model-written)"]
    names: list[str] = []
    if card:
        handle, text, stale = card
        out += ["", f"## Now ({handle})", text]
        if stale:
            out.append("(the card is flagged stale: a pinned source changed since it was written; verify)")
        names.append("Now")
    for name, lines in ((dec_title, dec), ("Open", opn), ("Lessons", les)):
        if lines:
            out += ["", f"## {name}", *lines]
            names.append(name)
    if pend:
        out += ["", "## Pending review", pend]
        names.append("Pending review")
    out += [
        "",
        f"as of {as_of}; auto-captured items (auto) are unreviewed. Drill down with the [vN] handles.",
    ]
    return "\n".join(out), names


def assemble(
    snap: Snapshot,
    *,
    budget: int = 1500,
    max_age_days: int = 7,
    include_auto: bool = False,
    counter: Callable[[str], int] = count_tokens,
) -> Brief | None:
    """The brief, or None when there is nothing trustworthy to say (no card beyond the skeleton and no
    decisions, open items or lessons)."""
    card: tuple[str, str, bool] | None = None
    c = snap.card
    if isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"].strip():
        if SKELETON_MARK not in c["text"]:
            label = f"project card {c.get('clue') or '?'}"
            if snap.card_date is not None:
                age = (snap.now - snap.card_date).days
                label += f", updated {snap.card_date.date().isoformat()}"
                label += ", may be stale" if age > CARD_STALE_DAYS else ""
            card = (label, c["text"].strip(), bool(c.get("stale")))

    dec: list[str] = []
    opn: list[str] = []
    seen: set[str] = set()
    auto_n = 0 if include_auto else sum(1 for it in snap.sessions + snap.lessons if it.auto)  # hidden ones
    sessions = [it for it in snap.sessions if include_auto or not it.auto]
    lessons = [it for it in snap.lessons if include_auto or not it.auto]
    cutoff = snap.now - timedelta(days=max_age_days)
    for it in sessions[:MAX_SESSIONS]:
        stamp = note_stamp(it)
        if stamp is None or stamp < cutoff:  # old or undated notes contribute nothing
            continue
        d, o = note_sections(it.body)
        for src, dst, cap in ((d, dec, DECISIONS_PER_NOTE), (o, opn, OPEN_PER_NOTE)):
            for ln in src[:cap]:
                key = " ".join(ln.split()).lower()
                if key in seen:
                    continue
                seen.add(key)
                dst.append(f"- [{note_tag(it)}] {cut(ln)}")
    les = [lesson_line(it) for it in lessons[:MAX_LESSONS]]
    if card is None and not (dec or opn or les):
        return None

    as_of = snap.as_of.date().isoformat() if snap.as_of else "unknown"
    if card:
        card = (card[0], truncate_tokens(card[1], CARD_TOKENS), card[2])
    pend = pending_line(snap, auto_n)
    dec_title = DECISIONS_TITLE if include_auto else DECISIONS_TITLE_REVIEWED

    text, names = _render(snap.project, dec_title, card, dec, opn, les, pend, as_of)
    # Over budget: drop from the tail of Lessons, then Open, then Decisions, then shrink the card.
    while counter(text) > budget:
        if les:
            les.pop()
        elif opn:
            opn.pop()
        elif dec:
            dec.pop()
        elif card and counter(card[1]) > MIN_CARD_TOKENS:
            card = (card[0], truncate_tokens(card[1].rstrip("…"), int(counter(card[1]) * 0.8)), card[2])
        elif pend:
            pend = None
        else:
            break
        text, names = _render(snap.project, dec_title, card, dec, opn, les, pend, as_of)
    truncated = False
    if counter(text) > budget:  # last resort: a hard cut (cannot happen with the caps above)
        text, truncated = truncate_tokens(text, budget), True
    if card is None and not (dec or opn or les):
        return None
    return Brief(
        text=text, tokens=counter(text), sections=names, excluded=list(snap.excluded), truncated=truncated
    )
