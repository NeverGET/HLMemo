#!/usr/bin/env python3
"""Atomic release state (Sol 36 M1): one JSON document is the source of truth for rollback.

    release_state.py publish DIR --current SHA [--previous SHA --previous-dump PATH
                     --previous-image REPO:SHA --previous-image-id ID [--previous-llm-env PATH|absent]]
                     [--env-backup FILE=BACKUP ...]
    release_state.py rolled-back DIR          # after a rollback: current <- previous, no rollback pair
    release_state.py add-retired DIR PATH...  # record retired-secret backups NOW (before cutover)
    release_state.py accept DIR [--sweep-dir D ...]  # delete every recorded env backup (and any
                                              # unrecorded D/*.pre-w0-*); mark accepted
    release_state.py begin-rollback DIR SHA [--llm-env PATH|absent]  # record an attempt (an
                                              # interrupted rollback re-runs); --llm-env: the copy of
                                              # the CURRENT llm.env, recorded only once per attempt
    release_state.py end-rollback DIR         # clear an aborted attempt
    release_state.py rollback-mark DIR [--safety PATH] [--destructive]
                                              # the attempt's FIRST safety dump / the start of its
                                              # destructive phase (recorded before the DB changes)
    release_state.py begin-deploy DIR --revision SHA --previous SHA --previous-dump PATH
                     --previous-image REPO:SHA --previous-image-id ID --previous-llm-env PATH|absent
                     --model PATH [--env-backup FILE=BACKUP ...]   # the deploy-attempt journal
    release_state.py end-deploy DIR           # clear the journal (the attempt was recovered)
    release_state.py begin-env-switch DIR / end-env-switch DIR    # install_llm_env.sh's journal
    release_state.py record-pending DIR PATH...  # secret-bearing copies to delete (cleanup journal)
    release_state.py cleanup DIR              # delete every journalled copy no state field needs
    release_state.py get DIR KEY              # one field ('' if absent; "a.b" for a nested one);
                                              # env_backups: FILE=BACKUP lines

`publish` writes DIR/release-state.json through tmp + fsync + rename + directory fsync, then derives
the legacy markers (current-ref, previous-ref, previous-dump) from it, each via tmp + rename. A crash
before the rename leaves the old state intact; a crash while deriving legacy markers leaves a
consistent state file (rollback reads only the state file) and `derive` repairs the markers.

D-111 #7: llm.env is part of the release state. ``previous_llm_env`` is the snapshot of the llm.env
the previous release ran with (llm_env_release.py snapshot; "absent" when it had none), restored by
rollback.sh before the previous image starts; a new publication deletes the superseded snapshot
(it holds the provider key). ``rollback_llm_env`` is the copy of the newer llm.env taken when a
rollback begins, put back if a rollback step fails.

D-116 (review 75): every multi-step operation is journalled in this ONE atomic document first, so a
kill at any step converges on a re-run: ``deploy_attempt`` (written before the new stack starts: a
re-run completes the publish or recovers with the recorded tuple), ``rollback_safety`` /
``rollback_destructive`` (the first safety dump and the destructive phase, recorded before the
database changes: a retry reuses that dump), ``env_switch`` (install_llm_env.sh: the env, both
services and the check), and ``pending_cleanup`` (secret-bearing llm.env copies are journalled here
in the same write that stops needing them, then deleted idempotently by ``cleanup``). A rollback
pair of a release to itself is never published (a same-ref re-run keeps the pair it has).
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

STATE = "release-state.json"
ABSENT = "absent"
#: the deploy-attempt journal's copy of the rendered previous model (it inlines env values)
ATTEMPT_MODEL = "deploy-attempt-model.json"
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


def _journal(directory: Path, state: dict) -> None:
    """A journal-only write: the state file, atomically, WITHOUT deriving the legacy markers (a host
    still on legacy markers has no current_ref in the state yet; deriving would delete them)."""
    _atomic_write(directory / STATE, json.dumps(state, indent=2, sort_keys=True) + "\n")


def store(directory: Path, state: dict) -> None:
    _atomic_write(directory / STATE, json.dumps(state, indent=2, sort_keys=True) + "\n")
    derive(directory)


def derive(directory: Path) -> None:
    """Legacy markers mirror the state exactly: written when present, DELETED when absent, so a
    crash between the state rename and this step is repaired by simply deriving again."""
    state = load(directory)
    for marker, key in LEGACY.items():
        if state.get(key):
            _atomic_write(directory / marker, f"{state[key]}\n")
        else:
            (directory / marker).unlink(missing_ok=True)


def _pending(state: dict, *paths: object) -> None:
    """Journal secret-bearing copies for deletion (in the caller's next atomic write)."""
    extra = [str(p) for p in paths if _snapshot_file(p) is not None]
    state["pending_cleanup"] = sorted(set(state.get("pending_cleanup") or []) | set(extra))


def cleanup(directory: Path) -> None:
    """Delete every journalled copy that no live state field refers to; idempotent (a crash between
    a state commit and its unlink is finished by the next lock holder)."""
    state = load(directory)
    pending = list(state.get("pending_cleanup") or [])
    if not pending:
        return
    live = {state.get("previous_llm_env"), state.get("rollback_llm_env")}
    attempt = state.get("deploy_attempt") or {}
    live |= {attempt.get("previous_llm_env"), attempt.get("model")}
    keep = []
    for value in pending:
        if value in live:  # still a rollback target: kept until a later write releases it
            keep.append(value)
            continue
        path = _snapshot_file(value)
        if path is not None:
            path.unlink(missing_ok=True)
            print(f"deleted {path.name} (journalled secret-bearing copy)")
    state["pending_cleanup"] = sorted(keep)
    _journal(directory, state)


def _snapshot_file(value: object) -> Path | None:
    """A recorded llm.env snapshot/copy path (never the "absent" marker)."""
    if not isinstance(value, str) or not value or value == ABSENT:
        return None
    path = Path(value)
    return path if path.is_absolute() and (".release-" in path.name or path.name == ATTEMPT_MODEL) else None


def publish(directory: Path, args: argparse.Namespace) -> None:
    state = load(directory)
    if args.previous and args.previous == args.current:
        # D-116 #2: never R3 -> R3 (a same-ref re-run keeps its pair, and the R2 env snapshot)
        raise SystemExit(f"release_state: refusing a rollback pair of {args.current} to itself")
    superseded = _snapshot_file(state.get("previous_llm_env"))
    attempt = state.pop("deploy_attempt", None) or {}
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
        if args.previous_llm_env:
            state["previous_llm_env"] = args.previous_llm_env
        else:  # an older runner's pair: rollback leaves llm.env as it is (with a warning)
            state.pop("previous_llm_env", None)
    # Every retired-secret backup ever recorded stays listed until --accept-release deletes it.
    retired = set(state.get("retired_backups") or []) | set((state.get("env_backups") or {}).values())
    state["retired_backups"] = sorted(retired)
    # D-111 #7 / D-116 #9: the older pair's snapshot and the attempt's model copy are no rollback
    # target any more (they hold the key): journalled in THIS write, deleted by cleanup
    if superseded is not None and str(superseded) != state.get("previous_llm_env"):
        _pending(state, str(superseded))
    _pending(state, attempt.get("model"))
    state["pending_cleanup"] = [p for p in state["pending_cleanup"] if p != state.get("previous_llm_env")]
    store(directory, state)
    cleanup(directory)


def add_retired(directory: Path, paths: list[str]) -> None:
    """Record retired-secret backups the moment migrate_env_w0 creates them, so a failed (and
    auto-recovered) deployment can never leave an untracked copy of the retired secrets behind.
    Only the state file is written (no derive): on a host still on legacy markers, a state without
    current_ref must not delete them. publish/rolled-back keep the list; accept deletes it."""
    state = load(directory)
    state["retired_backups"] = sorted(set(state.get("retired_backups") or []) | set(paths))
    _atomic_write(directory / STATE, json.dumps(state, indent=2, sort_keys=True) + "\n")


def accept(directory: Path, sweep_dirs: list[Path]) -> None:
    state = load(directory)
    backups = set((state.get("env_backups") or {}).values()) | set(state.get("retired_backups") or [])
    for backup in sorted(backups):
        Path(backup).unlink(missing_ok=True)
        print(f"deleted env backup {backup}")
    # Defensive sweep (the caller passes --sweep-dir only for a W0+ current release, D-065): a
    # backup nobody recorded (crash between copy and record, older runner) still holds secrets.
    for sweep in sweep_dirs:
        for stray in sorted(sweep.glob("*.pre-w0-*")):
            if str(stray) not in backups and (stray.is_file() or stray.is_symlink()):
                stray.unlink()
                print(f"deleted unrecorded env backup {stray}")
    state.update(accepted=True, env_backups={}, retired_backups=[])
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
    p.add_argument("--previous-llm-env")
    for name in (
        "rolled-back",
        "derive",
        "end-rollback",
        "end-deploy",
        "begin-env-switch",
        "end-env-switch",
        "cleanup",
    ):
        sub.add_parser(name).add_argument("dir", type=Path)
    m = sub.add_parser("rollback-mark")
    m.add_argument("dir", type=Path)
    m.add_argument("--safety")
    m.add_argument("--destructive", action="store_true")
    d = sub.add_parser("begin-deploy")
    d.add_argument("dir", type=Path)
    for flag in (
        "--revision",
        "--previous",
        "--previous-dump",
        "--previous-image",
        "--previous-image-id",
        "--previous-llm-env",
        "--model",
    ):
        d.add_argument(flag, required=True)
    d.add_argument("--env-backup", action="append", default=[])
    q = sub.add_parser("record-pending")
    q.add_argument("dir", type=Path)
    q.add_argument("paths", nargs="+")
    a = sub.add_parser("accept")
    a.add_argument("dir", type=Path)
    a.add_argument("--sweep-dir", type=Path, action="append", default=[])
    r = sub.add_parser("add-retired")
    r.add_argument("dir", type=Path)
    r.add_argument("paths", nargs="+")
    b = sub.add_parser("begin-rollback")
    b.add_argument("dir", type=Path)
    b.add_argument("target")
    b.add_argument("--llm-env")
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
        value: object = load(directory)
        for part in args.key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, dict):
            print("\n".join(f"{k}={v}" for k, v in value.items()))
        elif value not in (None, ""):
            print(value)
    elif args.cmd == "accept":
        accept(directory, args.sweep_dir)
    elif args.cmd == "add-retired":
        add_retired(directory, args.paths)
    elif args.cmd == "begin-rollback":
        state = load(directory)
        state["rollback_in_progress"] = args.target
        # the NEWER env is copied once per attempt: a re-run finds llm.env already restored
        if args.llm_env and not state.get("rollback_llm_env"):
            state["rollback_llm_env"] = args.llm_env
        store(directory, state)
    elif args.cmd == "rollback-mark":
        state = load(directory)
        if args.safety and not state.get("rollback_safety"):  # the FIRST safety dump only
            state["rollback_safety"] = args.safety
        if args.destructive:
            state["rollback_destructive"] = True
        _journal(directory, state)
    elif args.cmd == "end-rollback":
        state = load(directory)
        state.pop("rollback_in_progress", None)
        _pending(state, state.pop("rollback_llm_env", None))
        state.pop("rollback_safety", None)
        state.pop("rollback_destructive", None)
        store(directory, state)
        cleanup(directory)
    elif args.cmd == "rolled-back":
        state = load(directory)
        new = {
            "current_ref": state["previous_ref"],
            "rolled_back_from": state["current_ref"],
            "accepted": True,
            "env_backups": {},
            "retired_backups": state.get("retired_backups") or [],
            "pending_cleanup": state.get("pending_cleanup") or [],
        }
        # D-116 #9: both llm.env copies are spent (llm.env itself is the restored one)
        _pending(new, state.get("rollback_llm_env"), state.get("previous_llm_env"))
        # ONE step: the new state (pair consumed, attempt cleared, copies journalled) and the derived
        # markers; the consumed previous-ref/previous-dump markers disappear in derive.
        store(directory, new)
        cleanup(directory)
    elif args.cmd == "begin-deploy":
        state = load(directory)
        state["deploy_attempt"] = {
            "revision": args.revision,
            "previous_ref": args.previous,
            "previous_dump": args.previous_dump,
            "previous_image": args.previous_image,
            "previous_image_id": args.previous_image_id,
            "previous_llm_env": args.previous_llm_env,
            "model": args.model,
            "env_backups": dict(pair.split("=", 1) for pair in args.env_backup),
        }
        _journal(directory, state)
    elif args.cmd == "end-deploy":
        state = load(directory)
        attempt = state.pop("deploy_attempt", None) or {}
        _pending(state, attempt.get("model"))
        if attempt.get("previous_llm_env") != state.get("previous_llm_env"):
            _pending(state, attempt.get("previous_llm_env"))
        _journal(directory, state)
        cleanup(directory)
    elif args.cmd == "begin-env-switch":
        state = load(directory)
        state.setdefault("env_switch", {"started": True})
        _journal(directory, state)
    elif args.cmd == "end-env-switch":
        state = load(directory)
        state.pop("env_switch", None)
        _journal(directory, state)
    elif args.cmd == "record-pending":
        state = load(directory)
        _pending(state, *args.paths)
        _journal(directory, state)
    elif args.cmd == "cleanup":
        cleanup(directory)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
