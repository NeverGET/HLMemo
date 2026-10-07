"""Mask personal data and secrets in everything the migration kit prints or writes into a report.

A kit check exists to find a token or a personal value before it reaches memory; its own output must not then
carry that value to a terminal, a chat transcript or a report file. `redact` is the ONE masking function of
the kit (lint, seal, recall, roundtrip, nlm-check, containment, the personal-data scan and the blind check's
printed lines). It finds every shape the scan knows on the FULL text:

- e-mail addresses, 40-hex legacy device ids, `8-16` hex device ids, IPv4 and IPv6 addresses (loopback and
  unspecified excepted), checksum-valid Turkish national ids, private-key paths;
- the strong secret rules of the write path (``core/secret_guard``), documented examples and placeholders
  excepted;
- a spec's `[[scan.patterns]]` and the literal known values (a 0600 file; HLM_SCAN_KNOWN_VALUES by default).

Every finding becomes its mask (`<EMAIL>`, `<redacted:github-token>`, ...), and only THEN is the text cut to
`width`: a cut can never turn a value into a fragment that no longer matches and so stays readable (review
117). Every pattern runs in linear time, so a 2 MB line costs milliseconds.
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from hlmemo.core.secret_guard import DOCUMENTED_EXAMPLES, STRONG_SECRET_RULES, _placeholder

if TYPE_CHECKING:
    from hlmemo.migrate.spec import MigrationSpec, ScanPattern

KNOWN_VALUES_ENV = "HLM_SCAN_KNOWN_VALUES"

#: the local part starts only where no address character precedes it: one attempt per run, linear time
EMAIL_RE = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
#: a 40-hex string: a legacy Apple device UDID (a full git commit sha matches too: allow it or shorten it)
HEX40_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])")
#: the newer Apple UDID shape `00008030-001A35E22E38802E`
UDID_RE = re.compile(r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}(?![0-9A-Fa-f-])")
#: four dotted octets, also at the end of a sentence ("… on 8.8.4.4."), but not inside a longer dotted number
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)")
IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
TCKN_RE = re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")
#: an ssh directory path; bounded parts, and it can only start at `~` or `/Users/` or `/home/`
SSH_PATH_RE = re.compile(r"(?:~|/Users/[^/\s]{1,64}|/home/[^/\s]{1,64})/\.ssh/[^\s'\"`)\]]{0,256}")
#: a key-file extension; the file name is found by walking back from it (at most KEY_NAME_MAX characters),
#: never by a pattern that retries every start position of a long line (review 117: quadratic backtracking)
KEY_EXT_RE = re.compile(r"\.(?:p8|p12|pfx|pem|keystore|jks)\b")
KEY_NAME_MAX = 256
_NAME_STOP = frozenset(" \t\r\n'\"`([")
NOT_PERSONAL_IPS = frozenset({"127.0.0.1", "0.0.0.0", "255.255.255.255", "::1", "::"})


@dataclass(frozen=True)
class Found:
    """One finding in a text: the rule, its span and the mask that replaces it."""

    rule: str
    start: int
    end: int
    mask: str


def tckn_valid(s: str) -> bool:
    """The Turkish national id checksum (11 digits, first not 0, digits 10 and 11 are checks)."""
    if len(s) != 11 or not s.isdigit() or s[0] == "0":
        return False
    d = [int(c) for c in s]
    if ((sum(d[0:9:2]) * 7) - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def _ips(text: str) -> Iterator[Found]:
    if "." in text:
        for m in IPV4_RE.finditer(text):
            try:
                ip = ipaddress.ip_address(m.group(0))
            except ValueError:
                continue
            if m.group(0) not in NOT_PERSONAL_IPS:
                yield Found("ipv4-private" if ip.is_private else "ipv4", m.start(), m.end(), "<IP>")
    if text.count(":") >= 2:
        for m in IPV6_RE.finditer(text):
            v = m.group(0)
            if v.count(":") < 2:
                continue
            try:
                ip = ipaddress.ip_address(v)
            except ValueError:
                continue
            if v not in NOT_PERSONAL_IPS and not ip.is_loopback and not ip.is_unspecified:
                yield Found("ipv6-private" if ip.is_private else "ipv6", m.start(), m.end(), "<IP>")


def _key_paths(text: str) -> Iterator[Found]:
    if ".ssh/" in text:
        for m in SSH_PATH_RE.finditer(text):
            yield Found("key-path", m.start(), m.end(), "<KEYPATH>")
    for m in KEY_EXT_RE.finditer(text):
        start, floor = m.start(), max(0, m.start() - KEY_NAME_MAX)
        while start > floor and text[start - 1] not in _NAME_STOP and not text[start - 1].isspace():
            start -= 1
        if start < m.start():  # a bare ".pem" names no file
            yield Found("key-path", start, m.end(), "<KEYPATH>")


def _secrets(text: str) -> Iterator[Found]:
    for rule, pattern in STRONG_SECRET_RULES.items():
        allowed = DOCUMENTED_EXAMPLES.get(rule, frozenset())
        for m in pattern.finditer(text):
            if m.group(0) not in allowed and not _placeholder(m.group(0)):
                yield Found(f"secret-{rule}", m.start(), m.end(), f"<redacted:{rule}>")


def _literals(text: str, values: Iterable[str]) -> Iterator[Found]:
    for v in values:
        if not v:
            continue
        i = text.find(v)
        while i >= 0:
            yield Found("known", i, i + len(v), "<KNOWN>")
            i = text.find(v, i + len(v))


def read_values(path: Path) -> tuple[str, ...]:
    """The literal values of a known-values file, one per line (`#` comments and blank lines skipped)."""
    values = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        v = raw.strip()
        if v and not v.startswith("#"):
            values.append(v)
    return tuple(values)


def load_known_values(path: Path) -> tuple[str, ...]:
    """A 0600 known-values file. A file that others can read is refused: it holds exactly the values that
    must stay private."""
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{path} must be readable by its owner only (chmod 600)")
    return read_values(path)


@lru_cache(maxsize=4)
def _env_values(path: str, _mtime_ns: int) -> tuple[str, ...]:
    return read_values(Path(path))


def default_known_values() -> tuple[str, ...]:
    """The values of the HLM_SCAN_KNOWN_VALUES file, when it is set and readable (masking reads it whatever
    its mode: masking more is never the leak; the scan itself refuses a file that others can read)."""
    v = os.environ.get(KNOWN_VALUES_ENV)
    if not v:
        return ()
    p = Path(v).expanduser()
    try:
        return _env_values(str(p), p.stat().st_mtime_ns)
    except (OSError, UnicodeDecodeError):
        return ()


def find(
    text: str, *, patterns: Iterable[ScanPattern] = (), known: Iterable[str] | None = None
) -> list[Found]:
    """Every finding in `text`, before any allow-list. `known=None` reads HLM_SCAN_KNOWN_VALUES."""
    out: list[Found] = []
    if "@" in text:
        out += [Found("email", m.start(), m.end(), "<EMAIL>") for m in EMAIL_RE.finditer(text)]
    out += [Found("hex40", m.start(), m.end(), "<HEX40>") for m in HEX40_RE.finditer(text)]
    if "-" in text:
        out += [Found("udid", m.start(), m.end(), "<UDID>") for m in UDID_RE.finditer(text)]
    out += _ips(text)
    out += [
        Found("tr-national-id", m.start(), m.end(), "<TCKN>")
        for m in TCKN_RE.finditer(text)
        if tckn_valid(m.group(0))
    ]
    out += _key_paths(text)
    out += _secrets(text)
    for p in patterns:
        out += [
            Found(p.id, m.start(), m.end(), p.mask) for m in p.regex.finditer(text) if m.end() > m.start()
        ]
    out += _literals(text, default_known_values() if known is None else known)
    return out


def apply(text: str, found: Iterable[Found], width: int | None = None) -> str:
    """`text` with every finding replaced by its mask (overlaps merged into one mask), then cut to `width`."""
    spans: list[tuple[int, int, str]] = []
    for f in sorted(found, key=lambda f: (f.start, -f.end)):
        if spans and f.start < spans[-1][1]:
            start, end, mask = spans[-1]
            spans[-1] = (start, max(end, f.end), mask)
            continue
        spans.append((f.start, f.end, f.mask))
    parts: list[str] = []
    pos = 0
    for start, end, mask in spans:
        parts += [text[pos:start], mask]
        pos = end
    parts.append(text[pos:])
    out = "".join(parts)
    if width is not None and len(out) > width:
        out = out[: max(0, width - 1)] + "…"
    return out


def redact(
    text: str,
    *,
    patterns: Iterable[ScanPattern] = (),
    known: Iterable[str] | None = None,
    width: int | None = None,
) -> str:
    """Mask every personal-data and secret shape in the FULL text, then cut it to `width` characters."""
    patterns = tuple(patterns)
    known = default_known_values() if known is None else tuple(known)
    return apply(text, find(text, patterns=patterns, known=known), width)


def masker(spec: MigrationSpec | None = None, known: Iterable[str] | None = None) -> Callable[..., str]:
    """`redact` bound to a spec's `[[scan.patterns]]` and to the known values (HLM_SCAN_KNOWN_VALUES by
    default), for a command that prints many lines."""
    patterns = tuple(spec.scan_patterns) if spec is not None else ()
    values = default_known_values() if known is None else tuple(known)

    def mask(text: str, width: int | None = None) -> str:
        return redact(text, patterns=patterns, known=values, width=width)

    return mask


__all__ = [
    "Found",
    "apply",
    "default_known_values",
    "find",
    "load_known_values",
    "masker",
    "read_values",
    "redact",
    "tckn_valid",
]
