#!/usr/bin/env python3
"""llm.env as part of the release state (D-108/D-111 #7). Never prints a file's content.

    llm_env_release.py snapshot TARGET REF   # TARGET present: copy it to TARGET.release-<REF>
                                             # (0600, atomic) and print that path; else "absent"
    llm_env_release.py restore SOURCE TARGET # SOURCE "absent": remove TARGET; else replace TARGET
                                             # atomically with a 0600 copy of SOURCE
    llm_env_release.py fingerprint -         # stdin: a container's env as docker inspect's JSON
                                             # list ("K=V"); prints its non-secret FINGERPRINT (JSON)
    llm_env_release.py provenance TARGET [--require api[,librarian]] SERVICE=FINGERPRINT_JSON...
                                             # exit 1 unless every running service's fingerprint
                                             # matches the llm.env on disk (TARGET or "absent") and
                                             # every --require service reported (default both, R-4:
                                             # the callers pass the services their model defines)

remote-deploy.sh snapshots the llm.env the PREVIOUS release runs with (release-state.json
previous_llm_env); rollback.sh restores it before the previous image starts, and on a failed
rollback step puts the newer env back (rollback_llm_env) before the current release restarts.

D-116 #1 (review 75): a snapshot is recorded as "the env the previous release runs with" only after
its PROVENANCE is proven: the non-secret fingerprint of the file on disk (the release marker, the
D-094 profile mapping, the switches the release manifests keep off or pin on, the per-question
limits and, R4 R-4, memory.ask's behaviour keys, the spend caps and the guard switch) equals what
the api AND the librarian containers were created with; both must report (R-4). Otherwise the disk
env is not what runs (an install that did not recreate the services, a half-finished switch) and the
runner STOPS before anything changes.
The fingerprint never holds a key: only these names are read.
"""

import json
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


#: the non-secret keys of the fingerprint: the release marker, the D-094 mapping (every per-task
#: fallback too, R4's research and map-summary ones included), the switches and (review 79 T5) the
#: research switches and per-question limits
FINGERPRINT_KEYS = (
    "HLM_ENV_RELEASE",
    "HLM_PROFILE",
    "HLM_FALLBACK_PROFILE",
    "HLM_QUERY_REWRITE",
    "HLM_RETRIEVAL_SOURCE_CAP",
    "HLM_RESEARCH_ENABLED",
    "HLM_MAP_SUMMARY_ENABLED",
    "HLM_RESEARCH_MAX_USD",
    "HLM_RESEARCH_MAX_TOKENS",
    # R4 R-4 (consult 89; review 77): memory.ask's behaviour keys (the writer, the answer settings,
    # every timeout and the prose limit) and the guard. A disk env that differed from the running one
    # only in these used to pass (the running api on luna / 20 s, the disk env on Gemini / 150 s).
    "HLM_RESEARCH_ANSWER_MODE",
    "HLM_RESEARCH_ATTRIBUTION",
    "HLM_RESEARCH_RERANK",
    "HLM_RESEARCH_EXPAND",
    "HLM_RESEARCH_SELECT",
    "HLM_RESEARCH_PROSE_PROMPT",
    "HLM_RESEARCH_WRITER_PROFILE",
    "HLM_RESEARCH_WRITER_TIMEOUT_S",
    "HLM_RESEARCH_HTTP_TIMEOUT_S",
    "HLM_DETACHED_HOLD_MAX_S",
    "HLM_RESEARCH_TIMEOUT_S",
    "HLM_RESEARCH_PROSE_MAX_TOKENS",
    "HLM_LLM_TIMEOUT_S",
    "HLM_LLM_JOB_CALL_CAP",
)
TASK_FALLBACK_PREFIX = "HLM_FALLBACK_PROFILE__"
#: R4 R-4: the spend guard, whatever it grows: the caps (HOUR/DAY/MONTH_USD) and HLM_LLM_BUDGET_DISABLED
BUDGET_PREFIX = "HLM_LLM_BUDGET_"
SWITCHES = (
    "HLM_QUERY_REWRITE",
    "HLM_RETRIEVAL_SOURCE_CAP",
    "HLM_RESEARCH_ENABLED",
    "HLM_MAP_SUMMARY_ENABLED",
    "HLM_LLM_BUDGET_DISABLED",
)
#: keys another env file (app.env) may also set: compared only when llm.env sets them
SHARED = ("HLM_PROFILE", "HLM_FALLBACK_PROFILE")
#: R4 R-4 (review 77): the services that read llm.env; each must report the env it was created with
REQUIRED_SERVICES = ("api", "librarian")
_FALSE = frozenset({"", "0", "false", "no", "off"})
_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*")


def _fingerprint_key(key: str) -> bool:
    return key in FINGERPRINT_KEYS or key.startswith((TASK_FALLBACK_PREFIX, BUDGET_PREFIX))


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def fingerprint_of_dotenv(text: str) -> dict[str, str]:
    """The fingerprint of an llm.env file's text (only the fingerprint keys are kept)."""
    out = {}
    for line in text.splitlines():
        m = _LINE.fullmatch(line)
        if m and not line.lstrip().startswith("#") and _fingerprint_key(m.group(1)):
            out[m.group(1)] = _unquote(m.group(2))
    return out


def fingerprint_of_env_list(entries: list[str]) -> dict[str, str]:
    """The fingerprint of a container's environment (docker inspect .Config.Env)."""
    out = {}
    for entry in entries:
        key, sep, value = str(entry).partition("=")
        if sep and _fingerprint_key(key):
            out[key] = value
    return out


def fingerprint_of_file(target: str) -> dict[str, str]:
    path = Path(target)
    return {} if target == ABSENT or not path.is_file() else fingerprint_of_dotenv(path.read_text())


def _on(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _FALSE


def mismatches(disk: dict[str, str], running: dict[str, str]) -> list[str]:
    """The fingerprint keys where the RUNNING env differs from the env on DISK (names only)."""
    out = []
    for key in sorted(set(disk) | set(running)):
        if key in SWITCHES:
            differs = _on(disk.get(key)) != _on(running.get(key))
        elif key in SHARED:
            differs = key in disk and disk[key] != running.get(key)
        else:
            differs = disk.get(key) != running.get(key)
        if differs:
            out.append(f"{key}: disk={disk.get(key, '-')} running={running.get(key, '-')}")
    return out


def provenance(
    target: str, running: dict[str, dict[str, str]], required: tuple[str, ...] = REQUIRED_SERVICES
) -> list[str]:
    """The problems (names only) that stop a snapshot: a service of ``required`` (the services of the
    model that reads llm.env; default api AND librarian, R4 R-4) reported nothing, or a running env
    differs from the disk env."""
    disk = fingerprint_of_file(target)
    missing = [
        f"{service}: no {service} container reported the env it was created with (every llm.env"
        f" service of the model must report: {','.join(required)}; R-4)"
        for service in required
        if service not in running
    ]
    return missing + [
        f"{service} {m}" for service, fp in sorted(running.items()) for m in mismatches(disk, fp)
    ]


def main(argv: list[str]) -> int:
    if argv == ["fingerprint", "-"]:
        print(json.dumps(fingerprint_of_env_list(json.loads(sys.stdin.read() or "[]")), sort_keys=True))
        return 0
    if len(argv) >= 2 and argv[0] == "provenance":
        running, pairs, required = {}, argv[2:], REQUIRED_SERVICES
        if pairs[:1] == ["--require"] and len(pairs) >= 2:
            required = tuple(s for s in pairs[1].split(",") if s)
            pairs = pairs[2:]
        if not set(required) <= set(REQUIRED_SERVICES) or "api" not in required:
            print(f"llm_env_release: --require {','.join(required)}: api[,librarian]", file=sys.stderr)
            return 64
        for pair in pairs:
            service, _, fp = pair.partition("=")
            running[service] = json.loads(fp or "{}")
        problems = provenance(argv[1], running, required)
        for problem in problems:
            print(f"llm.env provenance: {problem}", file=sys.stderr)
        return 1 if problems else 0
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
