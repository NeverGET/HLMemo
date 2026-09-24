"""Lesson splitting at import (e2e 2026-09-24 §7.1; risk_check caught 4/12 footguns with atomic
lessons vs 1/12 with whole files).

``split(body, meta, doc_title)`` turns a lesson file (auto-memory ``feedback``, a serena/markdown
``lesson``/``footgun`` file) that holds SEVERAL independent rules into one ``Section`` per rule, or
returns ``None`` (the file stays one item, unchanged). Deterministic, no LLM:

* **Rules.** Headings first: a file with rule headings below its document title (``# Title``;
  label headings such as ``## Why`` / ``## Fix`` belong to the rule above them) is split only at
  them — ≥ 2 at the shallowest level make one rule per heading section, bullets inside a section
  stay in it (details of that rule), and ONE rule heading means one rule: no split (Sol 54 #4).
  Without rule headings: ≥ 2 top-level ``-``/``*``/``+`` bullets, each of at least
  ``RULE_MIN_WORDS`` words, make one rule per bullet (its indented and lazy continuation lines
  included). Never split: numbered lists, checklists and procedure lists (an intro such as
  "steps:" / "in this order:", items led by first/then/next/finally…, or a list whose items are
  ALL short (≤ ``STEP_MAX_WORDS`` words) and carry no rule marker — never/always/must/because/
  otherwise/instead, a ``:`` explanation, a bold label… — i.e. plain sequential instructions,
  Sol 55) — ordered steps of ONE procedure — and a list with a short item (``- disk``), which is a
  list inside one rule.
* **Shared context.** The file's other text (preamble, ``**Why:**`` paragraphs between the lists,
  the section text around a bullet list) plus the frontmatter ``description`` is copied into
  every rule's ``## Context`` (at most ``CONTEXT_MAX`` characters), so each rule stands alone.
* **Body.** ``## Mistake / ## Fix / ## Context`` (the ``memory.register_lesson`` layout) when the
  rule's own text makes it derivable from explicit labels (``**Mistake:**``, ``**Fix:**``,
  ``**Why:**`` → mistake, ``**How to apply:**`` → context, …; the unlabelled statement fills the
  missing mistake or fix); otherwise the rule's text is kept verbatim, followed by ``## Context``.
* **Keys (revision-safe).** The anchor is the slug of the rule's NORMALIZED heading (leading
  numbering such as ``3.`` / ``Rule 3:`` dropped), or of a bullet's explicit name label
  (``- **Stash is shared:** …``; role labels like ``**Mistake:**`` are not names), and only for an
  unlabelled bullet its SEQUENCE position (``#rule-3``, counted over the unlabelled bullets): a
  wording fix inside a rule keeps its key and is one revision. A renamed heading or label is
  re-mapped by body similarity on the rule text (``plan.remap``, the shared context cut): one
  revision, not a close + new item. Keys are unique per file (``-2``…).
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Any

from hlmemo.importers.common import FENCE_RE, HEADING_RE, Section, clip, slug

RULE_MIN_WORDS = 5
#: a list of unmarked items this short (all of them) is a procedure, not a set of rules
STEP_MAX_WORDS = 12
CONTEXT_MAX = 600
LEAD_MAX = 100
ANCHOR_MAX = 48

BULLET_RE = re.compile(r"^[-*+]\s+\S")  # numbered items are steps of one procedure: never split
#: a list introduced like this is a procedure (ordered steps of one rule): never split
_PROCEDURE_INTRO_RE = re.compile(
    r"\b(?:steps?|in (?:this|the following|that) order|procedure|sequence|checklist|runbook|how to"
    r"|adım(?:lar|ları)?|sırayla|schritte?|reihenfolge)\b",
    re.I,
)
#: list items led like this are steps (two of them make the list a procedure); ``[ ]`` checkboxes too
_STEP_LEAD_RE = re.compile(
    r"^(?:\[[ xX]\]\s|step\s*\d+\b|first(?:ly)?\b|second(?:ly)?\b|third(?:ly)?\b|then\b|next\b"
    r"|after(?:wards| that)?\b|finally\b|lastly\b|önce\b|sonra\b|ardından\b|zuerst\b|dann\b"
    r"|danach\b|schließlich\b)",
    re.I,
)
#: what makes a bullet read as a RULE rather than as one step of a procedure (EN / TR / DE)
_RULE_MARKER_RE = re.compile(
    r"\b(?:never|always|don'?t|do not|doesn'?t|must(?:n'?t)?|should(?:n'?t)?|avoid|instead|because|"
    r"otherwise|or (?:it|else)|unless|careful|beware|asla|her zaman|mutlaka|kesinlikle|yoksa|çünkü|"
    r"sakın|nie(?:mals)?|immer|nicht|muss|sonst|weil)\b|[:;!→⇒]|\*\*|\s[—–]\s|\s-\s",
    re.I,
)
#: a bullet's explicit name: a leading bold phrase with a separator (``**Stash is shared:**``,
#: ``**Stash**: …``, ``**Stash** — …``); a bold emphasis word (``**Never** run …``) is not a name
_NAME_LABEL_RE = re.compile(r"^\*\*\s*([^*\n]{2,80}?)\s*(?::\s*\*\*|\*\*\s*(?::|—|–|-\s))")
#: leading numbering of a heading (``3.``, ``3)``, ``Rule 3:``, ``Step 2 -``): not part of its key
_HEADING_NUMBER_RE = re.compile(r"^(?:(?:rule|lesson|step|kural|ders|regel)\s*)?\d+[.):\-–—]*\s*", re.I)
_WORD_RE = re.compile(r"\w+", re.UNICODE)

#: label (lower-case; a label may continue with more words: ``why it matters``) → body section
ROLES: dict[str, str] = {
    **dict.fromkeys(
        (
            "mistake",
            "problem",
            "symptom",
            "pitfall",
            "footgun",
            "gotcha",
            "why",
            "reason",
            "incident",
            "cause",
        ),
        "mistake",
    ),
    **dict.fromkeys(("fix", "solution", "rule", "remedy", "workaround", "instead", "do this"), "fix"),
    **dict.fromkeys(
        ("context", "how to apply", "when", "scope", "note", "notes", "applies to", "where"), "context"
    ),
}
_ROLE_ALT = "|".join(sorted((re.escape(k) for k in ROLES), key=len, reverse=True))
#: ``**Label:**`` / ``**Label**:`` anywhere (the known labels only in running text)
_INLINE_LABEL_RE = re.compile(rf"\*\*\s*((?:{_ROLE_ALT})(?:\s+[^*:\n]{{1,30}})?)\s*(?::\*\*|\*\*\s*:)", re.I)
#: at a line start: any bold label ending in a colon (``**Update 2026-09-23:**`` → context), a plain
#: known label (``Why: …``) or a label heading (``### Fix``, exactly a known label)
_LINE_LABEL_RE = re.compile(
    rf"^(?:\*\*\s*([^*\n]{{1,60}}?)\s*(?::\*\*|\*\*\s*:)|((?:{_ROLE_ALT}))\s*:(?=\s)"
    rf"|#{{1,6}}\s+((?:{_ROLE_ALT}))\s*:?\s*$)",
    re.I,
)
_LEAD_LABEL_RE = re.compile(rf"^(?:{_ROLE_ALT})\s*:\s*", re.I)


def _norm(label: str) -> str:
    return " ".join(label.lower().replace("_", " ").split()).rstrip(":").strip()


def role_of(label: str) -> str | None:
    """The body section of a label (``Why``, ``Why it matters`` → mistake), None if unknown."""
    norm = _norm(label)
    for key, role in ROLES.items():
        if norm == key or norm.startswith(key + " "):
            return role
    return None


def is_label_heading(title: str) -> bool:
    """A heading that is exactly a label (``## Why``, ``### How to apply:``) belongs to the rule
    above it; ``## Rule 3: never …`` is a rule of its own."""
    return _norm(title) in ROLES


# --------------------------------------------------------------------------- blocks
@dataclass(slots=True)
class _Block:
    bullet: bool
    lines: list[str]

    def text(self) -> str:
        return "\n".join(self.lines).strip("\n")


def _blocks(text: str) -> list[_Block]:
    """Top-level blocks outside code fences: a bullet (its marker line, indented lines and lazy
    continuation lines) or a run of other text (paragraphs, labels, headings, numbered lists)."""
    out: list[_Block] = []
    cur: _Block | None = None
    fence: str | None = None
    blank = False  # a blank line since the current bullet's last line
    for line in text.splitlines():
        if fence is not None:
            assert cur is not None
            cur.lines.append(line)
            m = FENCE_RE.match(line)
            if m and m.group(1) == fence:
                fence = None
            continue
        if not line.strip():
            if cur is not None:
                cur.lines.append(line)
            blank = True
            continue
        indented = line[:1].isspace()
        heading = HEADING_RE.match(line) is not None
        if BULLET_RE.match(line):
            cur = _Block(True, [line])
            out.append(cur)
        elif cur is not None and cur.bullet and (indented or (not blank and not heading)):
            cur.lines.append(line)  # an indented line, or markdown's lazy continuation
        elif cur is not None and not cur.bullet:
            cur.lines.append(line)
        else:
            cur = _Block(False, [line])
            out.append(cur)
        m = FENCE_RE.match(line)
        if m:
            fence = m.group(1)
        blank = False
    return out


def _words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _bullet_text(block: _Block) -> str:
    """The bullet without its marker, continuation lines dedented."""
    first = re.sub(r"^[-*+]\s+", "", block.lines[0])
    rest = textwrap.dedent("\n".join(block.lines[1:]))
    return (first + ("\n" + rest if rest.strip() else "")).strip()


def _intro(block: _Block) -> str | None:
    """The last paragraph of a text block when it introduces the list below it (ends with ``:``)."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", block.text()) if p.strip()]
    return paragraphs[-1] if paragraphs and paragraphs[-1].endswith(":") else None


def _is_procedure(blocks: list[_Block]) -> bool:
    """A list of ordered steps of one procedure (Sol 54 #4): an intro that announces steps, items
    led by sequence words, or checkboxes."""
    for n, b in enumerate(blocks):
        if not b.bullet and n + 1 < len(blocks) and blocks[n + 1].bullet:
            intro = _intro(b)
            if intro is not None and _PROCEDURE_INTRO_RE.search(intro):
                return True
    texts = [_bullet_text(b) for b in blocks if b.bullet]
    leads = [re.sub(r"\*\*|__", "", t).lstrip() for t in texts]
    if sum(1 for lead in leads if _STEP_LEAD_RE.match(lead)) >= 2 or any(
        lead.startswith(("[ ]", "[x]", "[X]")) for lead in leads
    ):
        return True
    # plain sequential instructions: every item short and none reads as a rule (Sol 55)
    return all(_words(t) <= STEP_MAX_WORDS and not _RULE_MARKER_RE.search(t) for t in texts)


def _bullet_rules(text: str) -> tuple[list[tuple[str, str]], str] | None:
    """``([(rule, its list's intro)], shared context)`` when ``text`` is ≥ 2 independent bullet
    rules, else None. A list intro (``Found later:``) is context for its own list only."""
    blocks = _blocks(text)
    bullets = [b for b in blocks if b.bullet]
    if len(bullets) < 2 or any(_words(_bullet_text(b)) < RULE_MIN_WORDS for b in bullets):
        return None
    if _is_procedure(blocks):
        return None
    rules: list[tuple[str, str]] = []
    shared: list[str] = []
    intro = ""
    for n, b in enumerate(blocks):
        if b.bullet:
            rules.append((_bullet_text(b), intro))
            continue
        own = _intro(b) if n + 1 < len(blocks) and blocks[n + 1].bullet else None
        body = b.text().strip()
        if own is not None:
            body = body[: body.rfind(own)].strip()
        if body:
            shared.append(body)
        intro = own or ""
    return rules, "\n\n".join(shared)


def _heading_rules(text: str) -> tuple[str, list[tuple[str, str]]] | None:
    """``(preamble, [(heading, section text without its heading line)])`` of the rule headings at
    the shallowest level (a leading ``# title`` and label headings excluded); ``None`` when there is
    no rule heading. ONE rule heading is one rule (``(preamble, [one])``): the caller never splits
    it, and never splits the bullets under it."""
    hs: list[tuple[int, int, str, int]] = []  # (offset, level, title, end of the heading line)
    pos, fence = 0, None
    for line in text.splitlines(keepends=True):
        m = FENCE_RE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
        elif fence is None:
            h = HEADING_RE.match(line.rstrip("\n"))
            if h:
                hs.append((pos, len(h.group(1)), h.group(2).strip(), pos + len(line)))
        pos += len(line)
    lead = text.lstrip()
    if hs and hs[0][1] == 1 and hs[0][0] == len(text) - len(lead):  # the document title
        hs = hs[1:]
    rules = [h for h in hs if not is_label_heading(h[2])]
    if not rules:
        return None
    level = min(h[1] for h in rules)
    marks = [h for h in rules if h[1] == level]
    preamble = text[: marks[0][0]]
    out: list[tuple[str, str]] = []
    for n, (_off, _lvl, title, body_start) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(text)
        out.append((title, text[body_start:end]))
    return preamble, out


def _strip_title(text: str) -> str:
    """Drop a leading ``# title`` line (it is the item title already)."""
    stripped = text.lstrip()
    h = HEADING_RE.match(stripped.split("\n", 1)[0])
    if h and len(h.group(1)) == 1:
        return stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return text


# --------------------------------------------------------------------------- body
def _segments(text: str) -> tuple[str, list[tuple[str, str]]]:
    """``(statement, [(role, text)])``: the text before the first label, then each labelled part.
    Unknown line-start labels become context parts that keep their label."""
    marks: list[tuple[int, int, str]] = []  # (start, end of the label, role)
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        m = _LINE_LABEL_RE.match(stripped)
        if m:
            label = next(g for g in m.groups() if g)
            role = role_of(label)
            start = pos + (len(line) - len(stripped))
            if role is not None:
                marks.append((start, start + m.end(), role))
            elif m.group(1):  # an unknown bold label keeps its text, as context
                marks.append((start, start, "context"))
        pos += len(line)
    for m in _INLINE_LABEL_RE.finditer(text):
        role = role_of(m.group(1))
        if role is not None and not any(s <= m.start() < max(e, s + 1) for s, e, _r in marks):
            marks.append((m.start(), m.end(), role))
    marks.sort()
    statement = text[: marks[0][0]] if marks else text
    parts: list[tuple[str, str]] = []
    for n, (_start, end, role) in enumerate(marks):
        stop = marks[n + 1][0] if n + 1 < len(marks) else len(text)
        chunk = text[end:stop].strip()
        if chunk:
            parts.append((role, chunk))
    return statement.strip(), parts


def derive(text: str, heading: str | None = None) -> tuple[str, str, str] | None:
    """``(mistake, fix, context)`` from explicit labels, or None when the rule has no mistake/fix
    structure (the statement fills the one side that is not labelled)."""
    statement, parts = _segments(text)
    by: dict[str, list[str]] = {"mistake": [], "fix": [], "context": []}
    for role, chunk in parts:
        by[role].append(chunk)
    mistake, fix = "\n\n".join(by["mistake"]), "\n\n".join(by["fix"])
    statement = statement or (heading or "")
    if not mistake and not fix:
        return None
    if fix and not mistake:
        mistake = statement
    elif mistake and not fix:
        fix = statement
    elif statement:
        mistake = f"{statement}\n\n{mistake}"
    if not mistake.strip() or not fix.strip():
        return None
    return mistake.strip(), fix.strip(), "\n\n".join(by["context"]).strip()


def rule_body(text: str, context: str, heading: str | None = None) -> str:
    derived = derive(text, heading)
    ctx: list[str] = []
    if derived is not None:
        mistake, fix, own = derived
        parts = [f"## Mistake\n{mistake}", f"## Fix\n{fix}"]
        ctx.append(own)
    else:
        parts = [(f"## {heading}\n{text.strip()}" if heading else text.strip()).strip()]
    ctx.append(context)
    joined = "\n\n".join(c for c in ctx if c.strip())
    if joined:
        parts.append(f"## Context\n{joined}")
    return "\n\n".join(p for p in parts if p) + "\n"


def _lead(text: str) -> str:
    first = next((ln for ln in text.strip().splitlines() if ln.strip()), "")
    first = re.sub(r"\*\*|__", "", first)
    first = _LEAD_LABEL_RE.sub("", " ".join(first.split())).strip(" -:")
    if len(first) <= LEAD_MAX:
        return first
    cut = first[: LEAD_MAX - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space >= LEAD_MAX // 2 else cut).rstrip(" ,;:.") + "…"


def heading_key(heading: str) -> str:
    """The anchor of a heading rule: its slug without leading numbering (renumbering keeps it)."""
    return slug(_HEADING_NUMBER_RE.sub("", heading.strip()) or heading, ANCHOR_MAX)


def name_label(rule: str) -> str | None:
    """A bullet's explicit name (a leading bold phrase that is not a role label), else None."""
    m = _NAME_LABEL_RE.match(rule.lstrip())
    if m is None or role_of(m.group(1)) is not None:
        return None
    return m.group(1).strip()


def _context(*parts: str) -> str:
    text = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return clip(text, CONTEXT_MAX) if text else ""


# --------------------------------------------------------------------------- split
def split(body: str, meta: dict[str, Any], doc_title: str) -> list[Section] | None:
    """One ``Section`` (kind ``lesson``) per independent rule of ``body`` (the text after the
    frontmatter), or None: the file stays one item."""
    description = meta.get("description") if isinstance(meta.get("description"), str) else ""
    rules: list[tuple[str, str, str]] = []  # (anchor base, lead, body)
    by_heading = _heading_rules(body)
    if by_heading is not None:
        preamble, sections = by_heading
        if len(sections) < 2:  # one rule heading: one rule, its bullets are its details
            return None
        file_ctx = _context(description, _strip_title(preamble))
        for heading, inner in sections:
            rules.append((heading_key(heading), heading, rule_body(inner, file_ctx, heading)))
    else:
        bullets = _bullet_rules(body)
        if bullets is None:
            return None
        items, shared = bullets
        position = 0
        for item, intro in items:
            ctx = _context(description, intro, _strip_title(shared))
            name = name_label(item)
            if name is None:
                position += 1
                key = f"rule-{position}"
            else:
                key = slug(name, ANCHOR_MAX)
            rules.append((key, _lead(item), rule_body(item, ctx)))
    seen: dict[str, int] = {}
    out: list[Section] = []
    for base, lead, text in rules:
        seen[base] = seen.get(base, 0) + 1
        anchor = base if seen[base] == 1 else f"{base}-{seen[base]}"
        out.append(Section(anchor, text, lead=f"{doc_title} › {lead}" if doc_title else lead, kind="lesson"))
    return out


__all__ = [
    "CONTEXT_MAX",
    "RULE_MIN_WORDS",
    "derive",
    "heading_key",
    "name_label",
    "role_of",
    "rule_body",
    "split",
]
