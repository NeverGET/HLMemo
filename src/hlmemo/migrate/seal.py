"""Seal the reviewed tree: per-file sha256, a tree digest and the per-batch item counts of the parse.

The owner approves a review package that describes one exact tree. The seal pins it: a prod run refuses when
any source file changed, appeared or vanished, or when the parse no longer yields the sealed batch counts.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hlmemo.migrate.batches import Loaded, batch_counts
from hlmemo.migrate.spec import MigrationSpec

SEAL_VERSION = 1


class SealError(RuntimeError):
    """The tree does not match its seal, or a seal cannot be written."""


def _source_files(spec: MigrationSpec) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for i, src in enumerate(spec.sources):
        if src.path.is_file():
            out.append((f"{i}:{src.path.name}", src.path))
            continue
        for p in sorted(src.path.rglob("*")):
            rel = p.relative_to(src.path)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts):
                out.append((f"{i}:{rel.as_posix()}", p))
    return out


def file_hashes(spec: MigrationSpec) -> dict[str, str]:
    return {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in _source_files(spec)}


def tree_digest(files: dict[str, str]) -> str:
    return hashlib.sha256("".join(f"{k}\t{v}\n" for k, v in sorted(files.items())).encode()).hexdigest()


def build(spec: MigrationSpec, loaded: list[Loaded]) -> dict[str, Any]:
    files = file_hashes(spec)
    counts = batch_counts(loaded)
    return {
        "seal_version": SEAL_VERSION,
        "slug": spec.slug,
        "tree_sha256": tree_digest(files),
        "files": files,
        "batch_items": counts,
        "total_items": sum(counts.values()),
    }


def write(
    spec: MigrationSpec, loaded: list[Loaded], *, expect: dict[str, int] | None = None, force: bool = False
) -> dict[str, Any]:
    """Seal the current tree. `expect` (the batch counts the review package states) must equal the parse; an
    existing seal is only replaced with `force`, so a changed tree is never re-sealed by accident."""
    seal = build(spec, loaded)
    if expect is not None and expect != seal["batch_items"]:
        raise SealError(f"expected batch counts {expect} != parsed {seal['batch_items']}")
    if spec.seal_path.exists() and not force:
        raise SealError(
            f"{spec.seal_path} exists: verify it, or pass --force to replace it after a new review"
        )
    seal["sealed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    spec.private_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(spec.seal_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(seal, f, ensure_ascii=False, indent=1)
    return seal


@dataclass
class Verdict:
    ok: bool
    problems: list[str]
    tree_sha256: str
    batch_items: dict[str, int]


def verify(spec: MigrationSpec, loaded: list[Loaded]) -> Verdict:
    if not spec.seal_path.exists():
        return Verdict(False, [f"no seal at {spec.seal_path}"], "", {})
    sealed = json.loads(spec.seal_path.read_text(encoding="utf-8"))
    now = build(spec, loaded)
    problems: list[str] = []
    if sealed.get("slug") != spec.slug:
        problems.append(f"seal is for slug {sealed.get('slug')!r}, the spec says {spec.slug!r}")
    if now["tree_sha256"] != sealed.get("tree_sha256"):
        old, new = sealed.get("files") or {}, now["files"]
        changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
        added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
        problems.append(f"tree differs: {len(changed)} changed, {len(added)} added, {len(removed)} removed")
        problems += [f"changed {k}" for k in changed[:10]]
        problems += [f"added {k}" for k in added[:10]] + [f"removed {k}" for k in removed[:10]]
    if now["batch_items"] != sealed.get("batch_items"):
        problems.append(f"batch counts {now['batch_items']} != sealed {sealed.get('batch_items')}")
    return Verdict(not problems, problems, now["tree_sha256"], now["batch_items"])


__all__ = ["SealError", "Verdict", "build", "file_hashes", "tree_digest", "verify", "write"]
