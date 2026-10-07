"""`hlm migrate roundtrip`: is every curated body stored verbatim on the target?

The second migration found that its legacy store had cut many notes after their first `<` at write time,
and nobody noticed for months (kit feedback #6/#13). This check proves the opposite for HLMemo on the
real data: it parses the curated tree with the importer (the exact bodies `hlm migrate run` sends), reads the
target's current items for the same source keys, and compares the stored body with the parsed one byte for
byte. Read-only on the target.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from hlmemo.migrate.batches import Loaded
from hlmemo.migrate.redact import redact

Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
CONTEXT = 40  # characters of masked context shown around a first difference


@dataclass
class RoundTrip:
    records: int = 0
    equal: int = 0
    missing: list[str] = field(default_factory=list)
    mismatched: list[dict[str, Any]] = field(default_factory=list)
    angle: int = 0  # equal bodies that hold a `<`, the case the legacy store cut
    non_ascii: int = 0  # equal bodies with non-ASCII letters (Turkish and others)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.mismatched and self.equal == self.records

    def as_dict(self, limit: int = 20) -> dict[str, Any]:
        return {
            "records": self.records,
            "equal": self.equal,
            "missing": len(self.missing),
            "mismatched": len(self.mismatched),
            "equal_with_angle_brackets": self.angle,
            "equal_with_non_ascii": self.non_ascii,
            "first_missing": self.missing[:limit],
            "first_mismatches": self.mismatched[:limit],
            "ok": self.ok,
        }


def first_difference(a: str, b: str) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def compare(key: str, sent: str, stored: str) -> dict[str, Any] | None:
    """None when equal; else the key, both lengths and masked context around the first difference."""
    if sent == stored:
        return None
    i = first_difference(sent, stored)
    lo = max(0, i - CONTEXT)
    return {
        "key": redact(key),
        "sent_chars": len(sent),
        "stored_chars": len(stored),
        "first_difference_at": i,
        "sent_context": redact(sent[lo : i + CONTEXT]),
        "stored_context": redact(stored[lo : i + CONTEXT]),
    }


async def roundtrip(call: Call, slug: str, loaded: list[Loaded]) -> RoundTrip:
    from hlmemo.importers.plan import source_key
    from hlmemo.importers.runner import fetch_full, fetch_items

    records = {r.key: r for ld in loaded for r in ld.parsed.records}
    manifest, _as_of = await fetch_items(call, slug)
    lid_of: dict[str, int] = {}
    for it in manifest:
        k = source_key(it.get("source"))
        if k in records and it.get("valid_to") is None:
            lid_of[k] = int(it["logical_id"])
    heads: dict[int, dict[str, Any]] = {}
    if lid_of:
        for it in await fetch_full(call, slug, sorted(set(lid_of.values()))):
            lid = int(it["logical_id"])
            if heads.get(lid, {}).get("version_id", 0) < it["version_id"]:
                heads[lid] = it
    res = RoundTrip(records=len(records))
    for key in sorted(records):
        sent = records[key].body
        lid = lid_of.get(key)
        stored = heads.get(lid, {}).get("body") if lid is not None else None
        if stored is None:
            res.missing.append(redact(key))
            continue
        diff = compare(key, sent, stored)
        if diff:
            res.mismatched.append(diff)
            continue
        res.equal += 1
        res.angle += "<" in sent
        res.non_ascii += any(ord(ch) > 127 for ch in sent)
    return res


__all__ = ["RoundTrip", "compare", "first_difference", "roundtrip"]
