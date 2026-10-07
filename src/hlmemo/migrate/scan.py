"""The personal-data and credential scan of a curated tree (`tools/migrate/scan.py`, PLAYBOOK §10).

gitleaks and the importer's filter look for credentials. The second migration's real exposure was personal
data: mail addresses, device ids, IP addresses, account and tax ids, local key paths (kit feedback #5).
This scan covers both, plus a known-value compare: a 0600 file of literal values (the owner's own address, an
id, a token) that must not appear anywhere. Every report line names the rule and file:line and MASKS the
value, so the scan's own output can be pasted into a chat or a review package.

Accepting a hit: `[scan].allow = ["<rule>:<path>:<line>"]` for one place, `[scan].allow_values = [...]` for a
value accepted wherever it occurs (a public support address). Project rules: `[[scan.patterns]] id, regex,
mask`.
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from hlmemo.core.secret_guard import strong_secret_rule
from hlmemo.migrate.redact import redact
from hlmemo.migrate.spec import MigrationSpec

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
#: a 40-hex string: a legacy Apple device UDID (a full git commit sha matches too: allow it or shorten it)
HEX40_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])")
#: the newer Apple UDID shape `00008030-001A35E22E38802E`
UDID_RE = re.compile(r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}(?![0-9A-Fa-f-])")
#: four dotted octets, also at the end of a sentence ("… on 8.8.4.4."), but not inside a longer dotted number
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)")
IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
TCKN_RE = re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")
KEY_PATH_RE = re.compile(
    r"(?:~|/Users/[^/\s]+|/home/[^/\s]+)/\.ssh/[^\s'\"`)\]]*"
    r"|[^\s'\"`(\[]+\.(?:p8|p12|pfx|pem|keystore|jks)\b"
)
NOT_PERSONAL_IPS = frozenset({"127.0.0.1", "0.0.0.0", "255.255.255.255", "::1", "::"})
MAX_FILE_BYTES = 2_000_000


@dataclass(frozen=True)
class Hit:
    """One finding. `context` is the MASKED line: a hit never carries the raw value."""

    rule: str
    path: str
    line: int
    context: str
    mask: str

    @property
    def where(self) -> str:
        return f"{self.rule}:{self.path}:{self.line}"


def tckn_valid(s: str) -> bool:
    """The Turkish national id checksum (11 digits, first not 0, digits 10 and 11 are checks)."""
    if len(s) != 11 or not s.isdigit() or s[0] == "0":
        return False
    d = [int(c) for c in s]
    if ((sum(d[0:9:2]) * 7) - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def _ip_hits(line: str) -> Iterator[tuple[str, str, str]]:
    for m in IPV4_RE.finditer(line):
        try:
            ip = ipaddress.ip_address(m.group(0))
        except ValueError:
            continue
        if m.group(0) not in NOT_PERSONAL_IPS:
            yield ("ipv4-private" if ip.is_private else "ipv4"), m.group(0), "<IP>"
    for m in IPV6_RE.finditer(line):
        v = m.group(0)
        if v.count(":") < 2:
            continue
        try:
            ip = ipaddress.ip_address(v)
        except ValueError:
            continue
        if v not in NOT_PERSONAL_IPS and not ip.is_loopback and not ip.is_unspecified:
            yield ("ipv6-private" if ip.is_private else "ipv6"), v, "<IP>"


def line_hits(spec: MigrationSpec, line: str, known: tuple[str, ...] = ()) -> list[tuple[str, str, str]]:
    """(rule, value, mask) for every finding in one line, before any allow-list."""
    out: list[tuple[str, str, str]] = []
    out += [("email", m.group(0), "<EMAIL>") for m in EMAIL_RE.finditer(line)]
    out += [("hex40", m.group(0), "<HEX40>") for m in HEX40_RE.finditer(line)]
    out += [("udid", m.group(0), "<UDID>") for m in UDID_RE.finditer(line)]
    out += list(_ip_hits(line))
    out += [
        ("tr-national-id", m.group(0), "<TCKN>") for m in TCKN_RE.finditer(line) if tckn_valid(m.group(0))
    ]
    out += [("key-path", m.group(0), "<KEYPATH>") for m in KEY_PATH_RE.finditer(line)]
    rule = strong_secret_rule(line)
    if rule:
        masked = redact(line)
        if masked != line:  # placeholders and documented examples stay unmasked: not a finding
            out.append((f"secret-{rule}", line, f"<SECRET:{rule}>"))
    for p in spec.scan_patterns:
        out += [(p.id, m.group(0), p.mask) for m in p.regex.finditer(line)]
    out += [("known", v, "<KNOWN>") for v in known if v and v in line]
    return out


def mask_line(line: str, hits: list[tuple[str, str, str]], width: int = 160) -> str:
    """The line with every finding replaced by its mask (longest values first), then the secret redaction."""
    out = line
    for _rule, value, mask in sorted(hits, key=lambda h: -len(h[1])):
        if value == line:  # a whole-line secret: mask its token shapes instead
            continue
        out = out.replace(value, mask)
    out = redact(out).strip()
    return out if len(out) <= width else out[: width - 1] + "…"


def load_known_values(path: Path) -> tuple[str, ...]:
    """A 0600 file of literal values, one per line (`#` comments, blank lines skipped). A file that others can
    read is refused: it holds exactly the values that must stay private."""
    st = path.stat()
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{path} must be readable by its owner only (chmod 600)")
    values = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        v = raw.strip()
        if v and not v.startswith("#"):
            values.append(v)
    return tuple(values)


def _text_files(spec: MigrationSpec) -> Iterator[tuple[str, Path]]:
    root = spec.curated_dir
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part.startswith(".") for part in p.relative_to(root).parts) or spec.excluded(rel):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        yield rel, p


def scan(spec: MigrationSpec, known: tuple[str, ...] = ()) -> tuple[list[Hit], dict[str, int]]:
    """Every finding in the curated tree that no allow entry accepts, plus counts (files, lines, allowed)."""
    hits: list[Hit] = []
    stats = {"files": 0, "binary": 0, "lines": 0, "allowed": 0}
    for rel, p in _text_files(spec):
        data = p.read_bytes()
        if b"\x00" in data:
            stats["binary"] += 1
            continue
        stats["files"] += 1
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            stats["lines"] += 1
            found = line_hits(spec, line, known)
            if not found:
                continue
            masked = mask_line(line, found)
            for rule, value, mask in found:
                if f"{rule}:{rel}:{n}" in spec.scan_allow or value in spec.scan_allow_values:
                    stats["allowed"] += 1
                    continue
                hits.append(Hit(rule, rel, n, masked, mask))
    return hits, stats


def default_known_values() -> Path | None:
    """`HLM_SCAN_KNOWN_VALUES`, when set."""
    v = os.environ.get("HLM_SCAN_KNOWN_VALUES")
    return Path(v).expanduser() if v else None


__all__ = [
    "Hit",
    "default_known_values",
    "line_hits",
    "load_known_values",
    "mask_line",
    "scan",
    "tckn_valid",
]
