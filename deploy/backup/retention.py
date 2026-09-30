#!/usr/bin/env python3
"""Calendar rotation excludes deployment snapshots; those require explicit pruning.

R4 R-2 (review 77): neither rotation nor pruning ever deletes a dump the release state still needs
(``--release-state FILE``, the deploy runner's release-state.json): the open rollback's safety dump
(``rollback_safety``), the rollback pair's dump (``previous_dump``) and a recorded deploy attempt's
dump (``deploy_attempt.previous_dump``). The rollback safety dump itself is written outside every
rotated tier (``backup.sh --rollback-safety``: ``rollback/``); this also covers a journal recorded by
an older runner in ``daily/``.
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


def protected_dumps(state_file: Path | None) -> frozenset[Path]:
    """The dumps the release state still references (resolved). An unreadable state file stops the
    rotation (fail closed: a dump it might protect is never guessed away)."""
    if state_file is None or not state_file.is_file():
        return frozenset()
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"retention: unreadable release state {state_file} ({type(exc).__name__}); nothing deleted"
        ) from None
    attempt = state.get("deploy_attempt") or {}
    values = (state.get("rollback_safety"), state.get("previous_dump"), attempt.get("previous_dump"))
    return frozenset(Path(v).resolve() for v in values if isinstance(v, str) and v)


def rotate(dump: Path, root: Path, protected: frozenset[Path] = frozenset()) -> Path:
    if dump.parent.resolve() != (root / "daily").resolve():
        raise ValueError("Calendar rotation accepts only daily snapshots")
    match = re.fullmatch(r"hlmemo-(\d{4}-\d{2}-\d{2}T\d{6}Z)(?:-[A-Za-z0-9]+)?", dump.stem)
    if match is None:
        raise ValueError("Invalid daily snapshot filename")
    timestamp = datetime.strptime(match[1], "%Y-%m-%dT%H%M%SZ")
    year, week, _ = timestamp.isocalendar()
    weekly = root / "weekly" / f"hlmemo-{year}-W{week:02d}.dump"
    temp = weekly.with_suffix(".partial")
    shutil.copyfile(dump, temp)
    os.chmod(temp, 0o600)
    os.replace(temp, weekly)
    days = set()
    # Random suffixes must not decide which same-second snapshot survives.
    # The snapshot completing this rotation is newest among timestamp ties.
    # R4 R-2: a dump the release state references is neither counted nor deleted.
    snapshots = sorted(
        (p for p in (root / "daily").glob("hlmemo-*.dump") if p.resolve() not in protected),
        key=lambda path: (
            path.name.partition("Z")[0],
            path.name == dump.name,
            path.stat().st_mtime_ns,
            path.name,
        ),
        reverse=True,
    )
    for path in snapshots:
        day = path.name[7:17]
        if day in days or len(days) >= 7:
            path.unlink()
        else:
            days.add(day)
    for path in sorted((root / "weekly").glob("hlmemo-*.dump"), reverse=True)[4:]:
        if path.resolve() not in protected:
            path.unlink()
    return weekly


def prune(root: Path, keep: int, protected: frozenset[Path] = frozenset()) -> int:
    if keep < 1:
        raise ValueError("pre-upgrade retention must keep at least one dump")
    snapshots = sorted(
        (root / "pre-upgrade").glob("*.dump"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    removed = 0
    for path in snapshots[keep:]:
        if path.resolve() in protected:  # R4 R-2: still the rollback pair's (or an attempt's) dump
            continue
        path.unlink()
        removed += 1
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-state", type=Path, help="release-state.json: its dumps are never deleted")
    if sys.argv[1:2] == ["--prune-pre-upgrade"]:
        parser.add_argument("--prune-pre-upgrade", type=Path, required=True)
        parser.add_argument("--keep", type=int, default=5)
        args = parser.parse_args()
        removed = prune(args.prune_pre_upgrade, args.keep, protected_dumps(args.release_state))
        print(f"Pre-upgrade prune completed: removed {removed}; keeping latest {args.keep}")
    else:
        parser.add_argument("dump", type=Path)
        parser.add_argument("root", type=Path)
        args = parser.parse_args()
        print(rotate(args.dump, args.root, protected_dumps(args.release_state)))


if __name__ == "__main__":
    main()
