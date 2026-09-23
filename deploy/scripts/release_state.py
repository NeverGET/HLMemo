#!/usr/bin/env python3
"""Atomic release state (Sol 36 M1): one JSON document is the source of truth for rollback.

    release_state.py publish DIR --current SHA [--previous SHA --previous-dump PATH
                     --previous-image REPO:SHA --previous-image-id ID] [--env-backup FILE=BACKUP ...]
    release_state.py rolled-back DIR          # after a rollback: current <- previous, no rollback pair
    release_state.py accept DIR               # delete the recorded env backups; mark accepted
    release_state.py get DIR KEY              # one field ('' if absent); env_backups: FILE=BACKUP lines

`publish` writes DIR/release-state.json through tmp + fsync + rename + directory fsync, then derives
the legacy markers (current-ref, previous-ref, previous-dump) from it, each via tmp + rename. A crash
before the rename leaves the old state intact; a crash while deriving legacy markers leaves a
consistent state file (rollback reads only the state file) and `derive` repairs the markers.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

STATE = "release-state.json"
LEGACY = {"current-ref": "current_ref", "previous-ref": "previous_ref", "previous-dump": "previous_dump"}


def load(directory: Path) -> dict:
    path = directory / STATE
    return json.loads(path.read_text()) if path.exists() else {}


def _atomic_write(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def store(directory: Path, state: dict) -> None:
    _atomic_write(directory / STATE, json.dumps(state, indent=2, sort_keys=True) + "\n")
    derive(directory)


def derive(directory: Path) -> None:
    state = load(directory)
    for marker, key in LEGACY.items():
        if state.get(key):
            _atomic_write(directory / marker, f"{state[key]}\n")


def publish(directory: Path, args: argparse.Namespace) -> None:
    state = load(directory)
    state["current_ref"] = args.current
    if args.previous:
        state.update(
            previous_ref=args.previous,
            previous_dump=args.previous_dump,
            previous_image=args.previous_image,
            previous_image_id=args.previous_image_id,
            env_backups=dict(pair.split("=", 1) for pair in args.env_backup),
            accepted=False,
        )
    store(directory, state)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish")
    p.add_argument("dir", type=Path)
    p.add_argument("--current", required=True)
    p.add_argument("--previous")
    p.add_argument("--previous-dump")
    p.add_argument("--previous-image")
    p.add_argument("--previous-image-id")
    p.add_argument("--env-backup", action="append", default=[])
    for name in ("rolled-back", "accept", "derive"):
        sub.add_parser(name).add_argument("dir", type=Path)
    g = sub.add_parser("get")
    g.add_argument("dir", type=Path)
    g.add_argument("key")
    args = ap.parse_args(argv)
    directory = args.dir
    if args.cmd == "publish":
        publish(directory, args)
    elif args.cmd == "derive":
        derive(directory)
    elif args.cmd == "get":
        value = load(directory).get(args.key)
        if isinstance(value, dict):
            print("\n".join(f"{k}={v}" for k, v in value.items()))
        elif value not in (None, ""):
            print(value)
    elif args.cmd == "accept":
        state = load(directory)
        for backup in (state.get("env_backups") or {}).values():
            Path(backup).unlink(missing_ok=True)
            print(f"deleted env backup {backup}")
        state.update(accepted=True, env_backups={})
        store(directory, state)
    elif args.cmd == "rolled-back":
        state = load(directory)
        new = {
            "current_ref": state["previous_ref"],
            "rolled_back_from": state["current_ref"],
            "accepted": True,
            "env_backups": {},
        }
        _atomic_write(directory / STATE, json.dumps(new, indent=2, sort_keys=True) + "\n")
        # The consumed rollback pair must not be reused by the legacy manual procedure.
        for marker in ("previous-ref", "previous-dump"):
            (directory / marker).unlink(missing_ok=True)
        derive(directory)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
