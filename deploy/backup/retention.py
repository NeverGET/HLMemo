#!/usr/bin/env python3
"""Calendar rotation excludes deployment snapshots; those require explicit pruning."""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


def rotate(dump: Path, root: Path) -> Path:
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
    snapshots = sorted(
        (root / "daily").glob("hlmemo-*.dump"),
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
        path.unlink()
    return weekly


def prune(root: Path, keep: int) -> int:
    if keep < 1:
        raise ValueError("pre-upgrade retention must keep at least one dump")
    snapshots = sorted(
        (root / "pre-upgrade").glob("*.dump"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for path in snapshots[keep:]:
        path.unlink()
    return len(snapshots[keep:])


def main() -> None:
    if sys.argv[1:2] == ["--prune-pre-upgrade"]:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--prune-pre-upgrade", type=Path, required=True)
        parser.add_argument("--keep", type=int, default=5)
        args = parser.parse_args()
        removed = prune(args.prune_pre_upgrade, args.keep)
        print(f"Pre-upgrade prune completed: removed {removed}; keeping latest {args.keep}")
    else:
        dump, root = map(Path, sys.argv[1:])
        print(rotate(dump, root))


if __name__ == "__main__":
    main()
