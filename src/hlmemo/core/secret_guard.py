"""PV-1 ``secret_pattern`` (protocol R7): the strong secret shapes no memory text needs.

History is append-only, so a secret that reaches memory can only be erased by an owner-run database
procedure. The write path therefore refuses a request in which ANY string value or dict key of the
raw arguments matches one of these shapes (``find_secret``): the event log stores the arguments
verbatim, so a field the server never indexes (``updates[].replacement``, ``source.path``,
``client``) would keep a secret just as well as a body.

The shapes start from the importer's strong refusal set (``importers/common.py`` ``SECRET_PATTERNS``)
and the capture scrub's, which stay unchanged. On the write path, where documentation and
placeholders are ordinary memory text, they are narrower (reviews 111 and 112):
- ``sk-api-key`` needs a known key prefix, so ``sk-learn`` or ``sk-...`` passes;
- ``slack-token`` needs Slack's token structure (``xoxb-<digits>-<digits>-<secret>``), so
  ``xoxb-<placeholder>`` or ``xoxb-<your-token>`` passes;
- ``private-key`` needs key material after the header (a pasted PEM/OpenSSH block), so a sentence
  that names ``-----BEGIN ... PRIVATE KEY-----`` passes;
- AWS's documented example access keys (``AKIA`` + ``IOSFODNN7EXAMPLE``,
  ``AKIA`` + ``I44QH8DHBEXAMPLE``) pass;
- a match whose random part is a placeholder (fewer than ``PLACEHOLDER_DISTINCT`` distinct
  characters in its last ``PLACEHOLDER_TAIL`` characters, e.g. ``ghp_xxxx…``, ``AIzaSyXXXX…``,
  ``sk-proj-xxxx…``) passes: a real key is random, a placeholder is not;
- ``jwt`` stays out: documentation examples (the jwt.io sample, test fixtures) are legitimate memory
  text and have the same shape as a live token.
The importer's broader rules (``assigned-secret``, ``dsn-with-password``, env-style assignments,
credential pairs) stay out as well: they reject legitimate writes too often (D-219).

The matched value is never returned: callers name the rule and a field path only. ``redact_for_log``
gives log lines the same protection for caller-supplied labels (the ``X-HLM-Client`` header).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

#: base64 key material right after a private-key header: the header, then spaces or line breaks
#: (real or ``\n`` escapes), optional RFC 1421 header lines (``Proc-Type: …``, each ending at its own
#: line break), then at least 40 base64 characters. Every piece consumes up to a fixed terminator, so
#: the pattern does not backtrack polynomially on long lines.
_BREAK = r"(?:\r?\n|\\[nr])"
_PEM_BODY = (
    rf"[ \t]*(?:{_BREAK}[ \t]*)*"
    rf"(?:[A-Za-z][A-Za-z0-9-]*:[^\n\\]*{_BREAK}[ \t]*(?:{_BREAK}[ \t]*)*)*"
    r"[A-Za-z0-9+/=]{40,}"
)

STRONG_SECRET_RULES: dict[str, re.Pattern[str]] = {
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----" + _PEM_BODY),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "slack-token": re.compile(r"\bxox[abprs]-\d{6,}-\d{6,}-[A-Za-z0-9-]{10,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "hlm-token": re.compile(r"\bhlm_[A-Za-z0-9_-]{43}\b"),
    "sk-api-key": re.compile(r"\bsk-(?:or-v1-|ant-|proj-)[A-Za-z0-9_-]{24,}"),
}

#: documented example values that are never live credentials (exact literals)
DOCUMENTED_EXAMPLES: dict[str, frozenset[str]] = {
    # the literals are split so that push-protection secret scanners do not flag this allow-list
    "aws-access-key": frozenset({"AKIA" + "IOSFODNN7EXAMPLE", "AKIA" + "I44QH8DHBEXAMPLE"}),
}

PLACEHOLDER_TAIL = 24
PLACEHOLDER_DISTINCT = 6
_SEPARATORS = re.compile(r"[-_\s=/+]|\\[nr]")

#: a reported field path is clipped to this length (paths are built from key names, never values)
PATH_MAX = 200


def _placeholder(match: str) -> bool:
    tail = _SEPARATORS.sub("", match)[-PLACEHOLDER_TAIL:]
    return len(set(tail)) < PLACEHOLDER_DISTINCT


def strong_secret_rule(text: str) -> str | None:
    """The id of the first strong secret rule that ``text`` matches with a value that is neither a
    documented example nor a placeholder, else None."""
    for rule, pattern in STRONG_SECRET_RULES.items():
        allowed = DOCUMENTED_EXAMPLES.get(rule, frozenset())
        for m in pattern.finditer(text):
            value = m.group(0)
            if value not in allowed and not _placeholder(value):
                return rule
    return None


def redact_for_log(value: Any) -> Any:
    """A caller-supplied label for a log line: ``<redacted:<rule>>`` when it is secret-shaped."""
    if isinstance(value, str):
        rule = strong_secret_rule(value)
        if rule is not None:
            return f"<redacted:{rule}>"
    return value


_CONTAINERS = (dict, list, tuple)


def _entries(obj: dict[Any, Any] | list[Any] | tuple[Any, ...]) -> Iterator[tuple[Any, Any]]:
    return iter(obj.items()) if isinstance(obj, dict) else enumerate(obj)


def find_secret(args: Any) -> tuple[str, str] | None:
    """``(field_path, rule)`` of the first string value or dict key in ``args`` (any nesting of
    dicts, lists and tuples) that matches a strong secret rule, else None.

    The walk is lazy: one iterator per open container and a path string per container entered, so a
    huge request costs no copy of its leaves, and the walk stops at the first match. The path names
    keys and list positions (``items[0].updates[0].replacement``, ``project``, ``decisions[1]``); a
    key that is itself secret-shaped is reported as ``<key>`` under its parent, so the path never
    carries the value."""
    if isinstance(args, str):
        rule = strong_secret_rule(args)
        return ("<value>", rule) if rule is not None else None
    if not isinstance(args, _CONTAINERS):
        return None
    stack: list[tuple[bool, Iterator[tuple[Any, Any]], str]] = [(isinstance(args, dict), _entries(args), "")]
    while stack:
        is_dict, entries, path = stack[-1]
        for key, value in entries:  # resumes where it stopped when a child container was entered
            if is_dict:
                rule = strong_secret_rule(str(key))
                if rule is not None:
                    return _clip(f"{path}.<key>" if path else "<key>"), rule
            if isinstance(value, str):
                rule = strong_secret_rule(value)
                if rule is not None:
                    return _clip(_child(path, key, is_dict)), rule
            elif isinstance(value, _CONTAINERS):
                stack.append((isinstance(value, dict), _entries(value), _child(path, key, is_dict)))
                break
        else:
            stack.pop()
    return None


def _child(path: str, key: Any, is_dict: bool) -> str:
    """The path of one entry; built only for a match or a container that is entered."""
    if is_dict:
        return f"{path}.{key}" if path else str(key)
    return f"{path}[{key}]"


def _clip(path: str) -> str:
    return path if len(path) <= PATH_MAX else path[: PATH_MAX - 1] + "…"


__all__ = [
    "DOCUMENTED_EXAMPLES",
    "STRONG_SECRET_RULES",
    "find_secret",
    "redact_for_log",
    "strong_secret_rule",
]
