#!/usr/bin/env python3
"""llm.env as part of the release state (D-108/D-111 #7). Never prints a file's content.

    llm_env_release.py snapshot TARGET REF   # TARGET present: copy it to TARGET.release-<REF>
                                             # (0600, atomic) and print that path; else "absent"
    llm_env_release.py restore SOURCE TARGET # SOURCE "absent": remove TARGET; else replace TARGET
                                             # atomically with a 0600 copy of SOURCE

remote-deploy.sh snapshots the llm.env the PREVIOUS release runs with (release-state.json
previous_llm_env); rollback.sh restores it before the previous image starts, and on a failed
rollback step puts the newer env back (rollback_llm_env) before the current release restarts.
"""

import os
import re
import sys
import tempfile
from pathlib import Path

ABSENT = "absent"
_REF = re.compile(r"[a-f0-9]{40}|[a-f0-9]{64}")


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_copy(source: Path, target: Path) -> None:
    data = source.read_bytes()
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    _fsync_dir(target.parent)


def snapshot(target: Path, ref: str) -> str:
    if not _REF.fullmatch(ref):
        raise SystemExit(f"llm_env_release: invalid release ref {ref!r}")
    if not target.is_file():
        return ABSENT
    copy = target.with_name(f"{target.name}.release-{ref}")
    _atomic_copy(target, copy)
    return str(copy)


def restore(source: str, target: Path) -> str:
    if source == ABSENT:
        if target.exists() or target.is_symlink():
            target.unlink()
            _fsync_dir(target.parent)
        return f"llm.env: removed {target} (the target release ran without one)"
    path = Path(source)
    if not path.is_file():
        raise SystemExit(f"llm_env_release: snapshot {source} is missing")
    _atomic_copy(path, target)
    return f"llm.env: restored {target} from {path.name}"


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "snapshot":
        print(snapshot(Path(argv[1]), argv[2]))
        return 0
    if len(argv) == 3 and argv[0] == "restore":
        print(restore(argv[1], Path(argv[2])))
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
