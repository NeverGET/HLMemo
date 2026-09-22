#!/usr/bin/env python3
"""Maintain 7 distinct daily snapshots and 4 distinct ISO-week snapshots."""
from datetime import datetime
from pathlib import Path
import os
import shutil
import sys

dump, root = map(Path, sys.argv[1:])
timestamp = datetime.strptime(dump.stem.removeprefix("hlmemo-"), "%Y-%m-%dT%H%M%SZ")
year, week, _ = timestamp.isocalendar()
weekly = root / "weekly" / f"hlmemo-{year}-W{week:02d}.dump"
temp = weekly.with_suffix(".partial")
shutil.copyfile(dump, temp)
os.chmod(temp, 0o600)
os.replace(temp, weekly)

days = set()
for path in sorted((root / "daily").glob("hlmemo-*.dump"), reverse=True):
    day = path.name[7:17]
    if day in days or len(days) >= 7:
        path.unlink()
    else:
        days.add(day)
for path in sorted((root / "weekly").glob("hlmemo-*.dump"), reverse=True)[4:]:
    path.unlink()
print(weekly)
