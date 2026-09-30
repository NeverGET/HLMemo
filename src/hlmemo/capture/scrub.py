"""Secret + PII scrub. Runs BEFORE any LLM call and again BEFORE writing (the summary is model output).

Reuses the importer secret rules (D-213: ``SECRET_PATTERNS`` + env-assignment + credential-pair) and
adds capture-specific shapes (bearer tokens, high-entropy tokens). A line that matches is DROPPED
(never partially redacted: a half-redacted secret is still a leak). Emails are masked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from hlmemo.importers.common import SECRET_CHECKS, SECRET_PATTERNS, secret_hit

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
EMAIL_MASK = "[email]"

_BEARER_RE = re.compile(
    r"(?i)\b(?:bearer|authorization:?\s*(?:bearer|basic|token))\s+[A-Za-z0-9._~+/=-]{16,}"
)
_AUTH_HEADER_RE = re.compile(r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic|token)\s+\S{8,}")
_TOKEN_CAND_RE = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")
_LONG_LINE = 4000  # minified blobs / base64 dumps are not conversation; dropped, not scanned piecemeal


def _token_like(s: str) -> bool:
    """A long mixed-case-and-digit string that is not a path, a hex hash, a slug or an identifier."""
    if "/" in s and s.count("/") >= 2:
        return False
    if re.fullmatch(r"[0-9a-fA-F]+", s):
        return False  # git / content hashes
    has_lower = any(c.islower() for c in s)
    has_upper = any(c.isupper() for c in s)
    has_digit = any(c.isdigit() for c in s)
    if not (has_lower and has_upper and has_digit):
        return False
    return len(set(s)) >= 14  # low-variety strings (aaaa..., 1212...) are not secrets


def line_secret(line: str) -> str | None:
    """The rule id that flags ``line`` as containing a secret, or None."""
    hit = secret_hit(line)
    if hit:
        return hit
    if _BEARER_RE.search(line) or _AUTH_HEADER_RE.search(line):
        return "bearer"
    if len(line) > _LONG_LINE:
        return "blob"
    for m in _TOKEN_CAND_RE.finditer(line):
        if _token_like(m.group(0)):
            return "high-entropy-token"
    return None


@dataclass
class ScrubReport:
    lines_dropped: int = 0
    emails_masked: int = 0
    rules: dict[str, int] = field(default_factory=dict)

    def note(self, rule: str) -> None:
        self.lines_dropped += 1
        self.rules[rule] = self.rules.get(rule, 0) + 1


def scrub_text(text: str, report: ScrubReport | None = None) -> str:
    """Drop secret-bearing lines (also on adjacent-pair windows for the credential-pair rule), then
    mask emails. Deterministic, idempotent."""
    rep = report if report is not None else ScrubReport()
    lines = text.split("\n")
    drop = [False] * len(lines)
    why: list[str | None] = [None] * len(lines)
    for i, line in enumerate(lines):
        rule = line_secret(line)
        if rule:
            drop[i], why[i] = True, rule
    pair = SECRET_CHECKS["credential-pair"]
    for i in range(len(lines) - 1):
        if not (drop[i] and drop[i + 1]) and pair(f"{lines[i]}\n{lines[i + 1]}"):
            for j in (i, i + 1):
                if not drop[j]:
                    drop[j], why[j] = True, "credential-pair"
    out: list[str] = []
    for i, line in enumerate(lines):
        if drop[i]:
            rep.note(why[i] or "secret")
            continue
        masked, n = EMAIL_RE.subn(EMAIL_MASK, line)
        rep.emails_masked += n
        out.append(masked)
    return "\n".join(out)


def residual_findings(text: str) -> list[str]:
    """What is STILL in ``text`` after scrubbing (must be empty): secret rule ids and ``email``."""
    found: list[str] = []
    for line in text.split("\n"):
        rule = line_secret(line)
        if rule:
            found.append(rule)
        if EMAIL_RE.search(line):
            found.append("email")
    lines = text.split("\n")
    pair = SECRET_CHECKS["credential-pair"]
    for i in range(len(lines) - 1):
        if pair(f"{lines[i]}\n{lines[i + 1]}"):
            found.append("credential-pair")
    return found


__all__ = [
    "EMAIL_RE",
    "SECRET_PATTERNS",
    "ScrubReport",
    "line_secret",
    "residual_findings",
    "scrub_text",
]
