"""`hlm migrate markers`: the supersession markers `hlm links explicit` reacts to, generated from the code.

A curator writes decision rows and notes in plain language. Some phrases are markers: after the import, the
link pass turns them into `supersedes` links, which mark the named item as outdated. A curator who writes "X
instead of D-110" in a decision row has declared that D-110 is replaced, whether that was meant or not (kit
feedback #11). This table is generated from `core/explicit_supersession.MARKERS`, so the PLAYBOOK and the
curation-spec template can never drift from what the code does (a test compares them).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from hlmemo.core.explicit_supersession import LEAD_WORDS, MARKERS

#: regex pattern -> (language, the wording a curator writes). Keyed by the pattern so a marker added to the
#: code without a wording here fails `test_every_marker_has_a_wording` instead of silently missing from the
#: docs.
WORDINGS: dict[str, tuple[str, str]] = {
    r"\bmerged\b(?:[ \t]+[^\s,;|()]+){0,3}?[ \t]+(?:from|out\s+of)\b": (
        "English",
        "merged … from / merged … out of",
    ),
    r"\bmerged\s+into\b": ("English", "merged into"),
    r"\bsuperseded\s+(?:by|through)\b": ("English", "superseded by / superseded through"),
    r"\bsupersed(?:es|e|ing)\b": ("English", "supersedes / supersede / superseding"),
    r"\breplaced\s+(?:by|with)\b": ("English", "replaced by / replaced with"),
    r"\breplac(?:es|ed|e|ing)\b(?!\s+(?:by|with)\b)": (
        "English",
        "replaces / replaced / replace / replacing",
    ),
    r"\b(?:instead\s+of|rather\s+than|in\s+place\s+of)\b": (
        "English",
        "instead of / rather than / in place of",
    ),
    r"\bzusammengeführt\s+(?:aus|von)\b": ("German", "zusammengeführt aus / von"),
    r"\b(?:ersetzt|abgelöst|überholt)\s+(?:durch|von)\b": (
        "German",
        "ersetzt / abgelöst / überholt durch / von",
    ),
    r"\bersetzt\b(?!\s+(?:durch|von)\b)": ("German", "ersetzt"),
    r"\b(?:anstelle\s+von|anstatt|statt)\b": ("German", "anstelle von / anstatt / statt"),
    r"\bbirleştiril(?:di|miştir|erek)\b": ("Turkish", "birleştirildi / birleştirilmiştir / birleştirilerek"),
    r"\btarafından\s+(?:geçersiz\s+kılın(?:dı|mıştır)|değiştiril(?:di|miştir))": (
        "Turkish",
        "tarafından geçersiz kılındı / kılınmıştır, tarafından değiştirildi / değiştirilmiştir",
    ),
    r"\bgeçersiz\s+kıl(?:ar|ıyor|dı|mıştır)\b": ("Turkish", "geçersiz kılar / kılıyor / kıldı / kılmıştır"),
    r"\byerini\s+al(?:ır|dı|mıştır|ıyor)\b": ("Turkish", "yerini alır / aldı / almıştır / alıyor"),
    r"(?<![\w])yerine\b": ("Turkish", "yerine"),
}

#: wordings that state a PARTIAL change without declaring a replacement (no marker fires on them)
SAFE_PARTIAL = (
    "narrows D-xxx / complements D-xxx / extends D-xxx",
    "D-xxx'in … kısmını değiştirir / D-xxx'i daraltır / D-xxx'e ek olarak",
    "ergänzt D-xxx / schränkt D-xxx ein",
)


@dataclass(frozen=True)
class MarkerRow:
    language: str
    relation: str
    wording: str
    where: str
    declaring: str


def _where(row_only: bool, before: bool) -> str:
    """Where the marker counts and how close its target must be (core/explicit_supersession:
    `_targets_after` takes the first ref within LEAD_WORDS filler words, 1 for the row-only markers;
    `_targets_before` takes the refs that end right before a Turkish marker)."""
    scope = "decision rows" if row_only else "anywhere"
    if before:
        return f"{scope}; D-id right before" if row_only else f"{scope}; target right before"
    return f"{scope}; D-id right after" if row_only else f"{scope}; target within {LEAD_WORDS} words"


def rows() -> list[MarkerRow]:
    out: list[MarkerRow] = []
    for m in MARKERS:
        language, wording = WORDINGS[m.regex.pattern]
        declaring = "older (the target replaces it)" if m.reverse else "newer (it replaces the target)"
        out.append(MarkerRow(language, m.name, wording, _where(m.row_only, m.before), declaring))
    return out


def markdown() -> str:
    """The table and its notes, exactly as the PLAYBOOK carries them between the markers comments."""
    lines = [
        "| language | relation | wording that triggers it | where it counts | the declaring item is |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {r.language} | `{r.relation}` | {r.wording} | {r.where} | {r.declaring} |" for r in rows()]
    lines += [
        "",
        f'- "within {LEAD_WORDS} words": the D-id or file path follows the phrase after at most {LEAD_WORDS} '
        'filler words. "right after" / "right before": nothing but a case suffix or a list joiner in between '
        '("X instead of Y (D-110)" does not link; "X instead of D-110" does; "D-110 ve D-111\'i geçersiz '
        'kılar" links both).',
        '- No link comes from a question, from a marker after a negation or a hypothetical word ("would", '
        '"if", "proposes", "eğer", "belki"…), from fenced or inline code, or from quoted text.',
        "- A partial change needs a wording that is not a marker: " + "; ".join(SAFE_PARTIAL) + ".",
    ]
    return "\n".join(lines) + "\n"


def as_json() -> str:
    return json.dumps([r.__dict__ for r in rows()], ensure_ascii=False, indent=1)


__all__ = ["SAFE_PARTIAL", "WORDINGS", "MarkerRow", "as_json", "markdown", "rows"]
