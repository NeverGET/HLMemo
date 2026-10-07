"""Mask secret-shaped values in every diagnostic the kit prints or writes (lint, seal, recall titles).

A lint run exists to find a token before it reaches memory; its own report must not then carry the token to
a terminal, a chat transcript or a report file. Each match of a strong secret rule (``core/secret_guard``)
that is not a documented example or a placeholder becomes ``<redacted:<rule>>``.
"""

from __future__ import annotations

from hlmemo.core.secret_guard import DOCUMENTED_EXAMPLES, STRONG_SECRET_RULES, _placeholder


def redact(text: str) -> str:
    out = text
    for rule, pattern in STRONG_SECRET_RULES.items():
        allowed = DOCUMENTED_EXAMPLES.get(rule, frozenset())

        def mask(m: object, rule: str = rule, allowed: frozenset[str] = allowed) -> str:
            value = m.group(0)  # type: ignore[attr-defined]
            return value if value in allowed or _placeholder(value) else f"<redacted:{rule}>"

        out = pattern.sub(mask, out)
    return out


__all__ = ["redact"]
