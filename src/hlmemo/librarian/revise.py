"""The span-bound revision of a partially outdated memory item: the deterministic part of D-110
(B-real) that the D-118 write-time updates (``core/write_updates.py``) reuse.

B3 port (R4: a MINIMAL port of D-118, not the v3/B-real/pilot stack): only what a writer's own
``revise`` needs lives here — no relate/v3 judgement, no ``revise_span`` prompt, no librarian
REVISE_SPAN proposal. The writer quotes both sides itself (``old_span`` from the memory it read, the
replacement verbatim from the item it writes), so the two relate/v3 judged-statement guards of B-real
do not apply; every other D-110 guard holds unchanged (``WRITE_GUARDS``). Pure, no database:

* ``check`` — the guards, run at write time against the CURRENT head (the caller holds its lock);
* ``apply_span`` — the new body: ``old_body`` with ONLY ``[start, end)`` replaced (every other
  character checked identical), used live and on replay (``actor._apply_revise``);
* ``revisable`` — D-113: historical records (episodes, session notes, decision/ADR rows) keep
  their text; for them a write-time update only adds a ``supersedes`` link.

Matching is byte-exact after Unicode NFC, with NO whitespace or case normalisation. The old body
must itself be NFC (span offsets are offsets into the stored text). The inserted replacement is the
NFC form of the writer's words.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: the ``op`` of the recorded mutation
OP = "version_revise"
#: D-110 length sanity: replacement ≤ 3 × old_span and ≤ 1,000 characters
MAX_RATIO = 3
MAX_REPLACEMENT = 1000
#: fewer words than this outside the span = the span is the whole item (use supersede)
MIN_OUTSIDE_WORDS = 3

#: D-118: the D-110 guards a write-time revise must pass (B-real's two relate/v3 judged-statement
#: guards are not part of this port: the writer quotes both sides itself)
WRITE_GUARDS = (
    "old_body_nfc",
    "old_span_unique",
    "span_word_boundary",
    "span_not_whole",
    "replacement_found",
    "replacement_differs",
    "length_ratio",
    "replacement_visibility",
)
#: D-113 + consult 74: historical records whatever ``HLM_LIBRARIAN_REVISE_KINDS`` says — a session
#: log is never revised or closed, even under a misconfigured kinds list
PROTECTED_KINDS = frozenset({"episode", "session_note"})

_WORD_CHAR = re.compile(r"\w")
_WORDS = re.compile(r"\w+")
#: sentence-ending punctuation (ASCII + full-width); a full-width one ends a sentence even without
#: following whitespace (CJK text); closing quotes/brackets after it belong to the sentence
_SENTENCE_END = ".!?;。．！？；"
_FULLWIDTH_END = "。．！？；"
_CLOSERS = "\"'”’»)]}›」』"


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def occurrences(hay: str, needle: str) -> list[int]:
    """Every start offset of ``needle`` in ``hay``, overlapping ones included."""
    if not needle:
        return []
    out: list[int] = []
    i = hay.find(needle)
    while i != -1:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def cuts_word(text: str, start: int, end: int) -> bool:
    """``[start, end)`` starts or ends inside a word of ``text``."""
    left = (
        0 < start < len(text)
        and bool(_WORD_CHAR.match(text[start - 1]))
        and bool(_WORD_CHAR.match(text[start]))
    )
    right = (
        0 < end < len(text) and bool(_WORD_CHAR.match(text[end - 1])) and bool(_WORD_CHAR.match(text[end]))
    )
    return left or right


def _boundaries(text: str) -> list[int]:
    """Every statement boundary of ``text``, sorted: its start and end, each line break (the end
    of one line, the start of the next) and each sentence end — ``.!?;`` (+ closing quotes or
    brackets) before whitespace or the end, or a full-width end anywhere."""
    out = {0, len(text)}
    for j, ch in enumerate(text):
        if ch == "\n":
            out.update((j, j + 1))
        elif ch in _SENTENCE_END:
            k = j + 1
            while k < len(text) and text[k] in _CLOSERS:
                k += 1
            if ch in _FULLWIDTH_END or k == len(text) or text[k].isspace():
                out.add(k)
    return sorted(out)


def statement_count(text: str) -> int:
    """How many statements ``text`` holds: the non-blank pieces between its statement boundaries
    (sentence ends and line breaks). D-118: a write-time revise without an explicit ``replacement``
    may use the whole body only when this is at most 1."""
    bounds = _boundaries(text)
    return sum(1 for a, b in zip(bounds, bounds[1:], strict=False) if text[a:b].strip())


def visible_to(
    old_projects: Iterable[int], old_scope: str, new_projects: Iterable[int], new_scope: str
) -> bool:
    """Copying the newer item's words into the old item never widens who can read them: every
    project of the old item holds the newer one, and the newer one's device scope is ``all`` or
    the same scope."""
    return set(int(p) for p in old_projects) <= set(int(p) for p in new_projects) and new_scope in (
        "all",
        old_scope,
    )


@dataclass(slots=True)
class Check:
    """The verdict of ``check``: every guard's result (``None`` = not evaluable because an earlier
    guard failed), the span offsets into the stored old body and the NFC replacement. ``quote_in``
    is where the span was found: ``body``, or ``title`` for a write-time supersede that quotes the
    outdated title (``write_updates.update_guards``; its offsets index the NFC title and are never
    used to change text)."""

    guards: dict[str, bool | None] = field(default_factory=dict)
    start: int | None = None
    end: int | None = None
    replacement: str = ""
    quote_in: str = "body"

    @property
    def ok(self) -> bool:
        return all(self.guards.get(g) is True for g in WRITE_GUARDS)

    def failed_of(self, guards: tuple[str, ...]) -> list[str]:
        """The failed guards among ``guards``, in their order."""
        return [g for g in guards if self.guards.get(g) is not True]


def check(
    *,
    old_body: str,
    old_span: str,
    replacement: str,
    new_body: str,
    old_projects: Iterable[int],
    old_scope: str,
    new_projects: Iterable[int],
    new_scope: str,
) -> Check:
    """The D-110 guards of a write-time revise (``WRITE_GUARDS``): ``old_span`` occurs exactly
    once in the NFC ``old_body``, on word boundaries, with at least ``MIN_OUTSIDE_WORDS`` words
    outside it; the replacement occurs verbatim in ``new_body`` (the carrying item's body only),
    differs from the span and fits the length ratio; copying it never widens its readers."""
    c = Check()
    g = c.guards
    span_n, rep_n = nfc(old_span), nfc(replacement)
    g["old_body_nfc"] = unicodedata.is_normalized("NFC", old_body)
    hits = occurrences(old_body, span_n) if g["old_body_nfc"] and span_n.strip() else []
    g["old_span_unique"] = len(hits) == 1
    if g["old_span_unique"]:
        c.start, c.end = hits[0], hits[0] + len(span_n)
        g["span_word_boundary"] = not cuts_word(old_body, c.start, c.end)
        outside = f"{old_body[: c.start]} {old_body[c.end :]}"
        g["span_not_whole"] = len(_WORDS.findall(outside)) >= MIN_OUTSIDE_WORDS
    else:
        g["span_word_boundary"] = g["span_not_whole"] = None
    g["replacement_found"] = bool(rep_n.strip()) and bool(occurrences(nfc(new_body), rep_n))
    if g["replacement_found"]:
        c.replacement = rep_n
    g["replacement_differs"] = bool(rep_n) and rep_n != span_n
    g["length_ratio"] = (
        bool(span_n) and len(rep_n) <= MAX_RATIO * len(span_n) and len(rep_n) <= MAX_REPLACEMENT
    )
    g["replacement_visibility"] = visible_to(old_projects, old_scope, new_projects, new_scope)
    return c


def apply_span(body: str, start: int, end: int, replacement: str) -> str:
    """``body`` with ONLY ``[start, end)`` replaced by ``replacement``; every character outside the
    span is checked identical (raises ``ValueError`` otherwise, never silently)."""
    if not 0 <= start < end <= len(body):
        raise ValueError("span outside the body")
    new = body[:start] + replacement + body[end:]
    tail = len(body) - end
    if new[:start] != body[:start] or new[len(new) - tail :] != body[end:]:  # pragma: no cover
        raise ValueError("text outside the span changed")
    return new


def revise_kinds(settings: Any = None) -> frozenset[str]:
    """D-113: the kinds a span revision (or a write-time close) may change
    (``HLM_LIBRARIAN_REVISE_KINDS``; the default and its validation live in ``hlmemo.config``)."""
    if settings is None:
        from hlmemo.config import get_settings

        settings = get_settings()
    raw = str(getattr(settings, "librarian_revise_kinds", "") or "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def decision_record(body: str, source: dict[str, Any] | None) -> bool:
    """D-113: a decision/ADR record, whatever its kind (the importer stores decision rows as
    ``fact``): its first line is a decision-log row (``D-047 | 2026-09-23 | …``, the importer's
    ``DECISION_ROW_RE``), or it was imported from an ADR directory (``importers.markdown.ADR_DIRS``)."""
    from hlmemo.importers.common import DECISION_ROW_RE
    from hlmemo.importers.markdown import ADR_DIRS

    first = next((ln.strip() for ln in (body or "").splitlines() if ln.strip()), "")
    if DECISION_ROW_RE.match(first):
        return True
    path = str((source or {}).get("path") or "").split("#", 1)[0].lower()
    return any(d in ADR_DIRS for d in path.split("/")[:-1])


def revisable(kind: str, body: str, source: dict[str, Any] | None, kinds: frozenset[str]) -> str | None:
    """D-113: ``None`` when the item may be revised or closed, else the reason: ``historical_kind``
    (its kind is not in ``HLM_LIBRARIAN_REVISE_KINDS``, e.g. an episode) or ``decision_record``.
    ``PROTECTED_KINDS`` and decision records are refused whatever the configured list holds
    (consult 74)."""
    if kind in PROTECTED_KINDS or kind not in kinds:
        return "historical_kind"
    if decision_record(body, source):
        return "decision_record"
    return None


__all__ = [
    "MAX_RATIO",
    "MAX_REPLACEMENT",
    "MIN_OUTSIDE_WORDS",
    "OP",
    "PROTECTED_KINDS",
    "WRITE_GUARDS",
    "Check",
    "apply_span",
    "check",
    "cuts_word",
    "decision_record",
    "nfc",
    "occurrences",
    "revisable",
    "revise_kinds",
    "sha256_text",
    "statement_count",
    "visible_to",
]
