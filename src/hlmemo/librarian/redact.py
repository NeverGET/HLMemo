"""Secret/PII filter applied before every LLM call and to audit-payload free text (D-016, CC-5).

Rule set: the ``.githooks/pre-commit`` gate patterns (OpenRouter/OpenAI ``sk-`` keys, GitHub
``gh[pousr]_``, AWS ``AKIA``, Google ``AIza``, Slack ``xox?-``, PEM private keys) plus HLMemo
bearer tokens (``hlm_`` + 43 base64url chars), GitHub fine-grained PATs, Anthropic/Stripe keys,
Google OAuth access tokens, JWTs, ``Authorization: Bearer`` values, DSNs/URLs carrying a password,
IBANs (mod-97 checked) and ``<secret-ish name> = <value>`` assignments. E-mail addresses and phone
numbers are optional (``HLM_LLM_REDACT_EMAIL`` / ``HLM_LLM_REDACT_PHONE``).

A match becomes ``⟦REDACTED:<type>:<sha8>⟧`` (sha8 = first 8 hex chars of sha256(secret)), so the
same secret maps to the same placeholder (the model can still tell two secrets apart) and a
redacted prompt is deterministic (cassette keys, CC-5). The placeholder → secret mapping exists
only in memory on the ``Redaction`` result; it is never logged, persisted or sent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

REDACTION_VERSION = "redact/1"

_SECRET_NAME = (
    r"(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|auth[_-]?key"
    r"|private[_-]?key|client[_-]?secret|credentials?|parola|sifre|şifre|passwort|kennwort|schluessel|schlüssel)"
)

# (type, pattern, group-with-the-secret). Order matters: specific rules before generic ones.
_RULES: list[tuple[str, str, int]] = [
    (
        "private_key",
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
        0,
    ),
    ("hlm_token", r"(?<![A-Za-z0-9_])hlm_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", 0),
    ("openrouter_key", r"sk-or-v1-[A-Za-z0-9]{20,}", 0),
    ("anthropic_key", r"sk-ant-[A-Za-z0-9_-]{20,}", 0),
    ("openai_key", r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}", 0),
    ("stripe_key", r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}", 0),
    ("github_pat", r"github_pat_[A-Za-z0-9_]{40,}", 0),
    ("github_token", r"gh[pousr]_[A-Za-z0-9]{30,}", 0),
    ("aws_access_key", r"(?:AKIA|ASIA)[0-9A-Z]{16}", 0),
    ("google_api_key", r"AIza[0-9A-Za-z_-]{30,}", 0),
    ("google_oauth", r"ya29\.[0-9A-Za-z_-]{20,}", 0),
    ("slack_token", r"xox[baprs]-[0-9A-Za-z-]{10,}", 0),
    ("slack_webhook", r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}", 0),
    ("jwt", r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", 0),
    ("bearer", r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{16,})", 1),
    ("dsn_password", r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]+:([^\s@/]{1,256})@[^\s]+", 1),
    (
        "assignment",
        r"(?i)(?<![A-Za-z0-9])[A-Za-z0-9_.-]*" + _SECRET_NAME + r"(?:s?[iı])?(?![A-Za-z0-9])\s*"
        r"(?:[:=]|=>|\bis\b|\bist\b|\blautet\b|\bdır\b|\bdir\b)\s*"
        r"[\"'`]?([^\s\"'`,;]{8,})",
        1,
    ),
]
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<![\w+])\+?\d{1,3}[ .-]?\(?\d{2,4}\)?[ .-]?\d{3,4}[ .-]?\d{2,4}(?![\w])")
_PLACEHOLDER = re.compile(r"⟦REDACTED:[a-z_]+:[0-9a-f]{8}⟧")
# Values that are references, not secrets (`env:NAME`, `${NAME}`, `<fill-me>`, file paths).
_NOT_A_VALUE = re.compile(r"^(?:env:|\$\{|<|/|\.{0,2}/|\*+$|x+$|changeme$|none$|null$|true$|false$)", re.I)

_COMPILED = [(t, re.compile(p), g) for t, p, g in _RULES]


def _iban_ok(candidate: str) -> bool:
    s = candidate.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in moved)
    return int(digits) % 97 == 1


def _overlaps_placeholder(s: str, start: int, end: int) -> bool:
    """A match that touches an existing placeholder is never redacted again (no nesting)."""
    return any(p.start() < end and start < p.end() for p in _PLACEHOLDER.finditer(s))


def _token(kind: str, secret: str) -> str:
    return f"⟦REDACTED:{kind}:{hashlib.sha256(secret.encode('utf-8')).hexdigest()[:8]}⟧"


@dataclass(slots=True)
class Redaction:
    text: str
    #: placeholder -> original secret; in-memory only, never serialised.
    mapping: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def count(self) -> int:
        return len(self.mapping)


class Redactor:
    def __init__(self, *, email: bool = False, phone: bool = False) -> None:
        self.email = email
        self.phone = phone

    @classmethod
    def from_settings(cls, settings: Any) -> Redactor:
        return cls(email=bool(settings.llm_redact_email), phone=bool(settings.llm_redact_phone))

    def redact(self, text: str) -> Redaction:
        mapping: dict[str, str] = {}

        def sub_span(s: str, start: int, end: int, kind: str) -> tuple[str, int]:
            secret = s[start:end]
            tok = _token(kind, secret)
            mapping[tok] = secret
            return s[:start] + tok + s[end:], len(tok) - (end - start)

        out = text
        for kind, rx, group in _COMPILED:
            pos = 0
            while True:
                m = rx.search(out, pos)
                if m is None:
                    break
                start, end = m.span(group)
                if start < 0 or start == end:
                    pos = m.end()
                    continue
                value = out[start:end]
                if (
                    _overlaps_placeholder(out, start, end)
                    or _PLACEHOLDER.search(value)
                    or (kind == "assignment" and _NOT_A_VALUE.match(value))
                ):
                    pos = m.end()
                    continue
                if kind == "assignment" and not (re.search(r"\d", value) or len(value) >= 16):
                    pos = m.end()  # prose like "password: required" is not a secret
                    continue
                out, delta = sub_span(out, start, end, kind)
                pos = m.end() + delta
        for m in reversed(list(_IBAN.finditer(out))):
            if _iban_ok(m.group(0)) and not _overlaps_placeholder(out, m.start(), m.end()):
                out, _ = sub_span(out, m.start(), m.end(), "iban")
        if self.email:
            for m in reversed(list(_EMAIL.finditer(out))):
                out, _ = sub_span(out, m.start(), m.end(), "email")
        if self.phone:
            for m in reversed(list(_PHONE.finditer(out))):
                if len(re.sub(r"\D", "", m.group(0))) >= 9:
                    out, _ = sub_span(out, m.start(), m.end(), "phone")
        return Redaction(out, mapping)

    def text(self, text: str) -> str:
        return self.redact(text).text

    def value(self, obj: Any) -> Any:
        """Redact every string inside a JSON-like value (audit payload free text, CC-5)."""
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, list):
            return [self.value(v) for v in obj]
        if isinstance(obj, dict):
            return {k: self.value(v) for k, v in obj.items()}
        return obj


__all__ = ["REDACTION_VERSION", "Redaction", "Redactor"]
