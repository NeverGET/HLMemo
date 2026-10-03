"""Agent workers for ``hlm curate`` (D-017: the agent command is configuration, never code).

The command is a shell-style template (``HLM_CURATE_AGENT_CMD`` / ``--agent-cmd``), split with
``shlex`` and run once per slice WITHOUT a shell. The rendered brief goes on STDIN (CLI arguments get
swallowed by some agents). Placeholders, replaced inside each argument: ``{export}`` (the export
dir), ``{workdir}`` (the worker's own dir, its cwd), ``{slice}`` (the slice file) and ``{out}`` (the
output file). The same values are in the environment as ``HLM_CURATE_EXPORT``, ``HLM_CURATE_WORKDIR``,
``HLM_CURATE_SLICE``, ``HLM_CURATE_OUT``, plus ``HLM_CURATE_STAGE`` and ``HLM_CURATE_ATTEMPT``.

Every output is validated (JSON Schema + coverage of the slice). A failing worker is retried ONCE
with the validation errors appended to its prompt; a second failure marks the slice ``failed``. A
failed slice is reported in the summary and held back, never dropped silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from hlmemo.curate.gate import PASS1_VERDICTS, PASS2_VERDICTS

AGENT_ENV = "HLM_CURATE_AGENT_CMD"
OUT_NAME = "out.json"
SLICE_NAME = "slice.json"
STATUS_NAME = "status.json"
LOG_TAIL = 20000  # bytes of agent stdout/stderr kept per attempt

_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_INT = {"type": ["integer", "null"]}

PASS1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["cid", "verdict", "why"],
                "properties": {
                    "cid": {"type": "string"},
                    "verdict": {"enum": list(PASS1_VERDICTS)},
                    "direction_ok": {"type": ["boolean", "null"]},
                    "newer_logical_id": _NULLABLE_INT,
                    "older_logical_id": _NULLABLE_INT,
                    "older_span": _NULLABLE_STR,
                    "newer_quote": _NULLABLE_STR,
                    "why": {"type": "string"},
                },
            },
        },
        "notes": {"type": ["string", "null"]},
    },
}

PASS2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["i", "verdict", "failed_tests", "reason"],
                "properties": {
                    "i": {"type": "integer"},
                    "verdict": {"enum": list(PASS2_VERDICTS)},
                    "failed_tests": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1, "maximum": 5},
                    },
                    "reason": {"type": "string"},
                    "fix": {
                        "type": ["object", "null"],
                        "properties": {
                            "older_span": _NULLABLE_STR,
                            "newer_quote": _NULLABLE_STR,
                            "src_logical_id": _NULLABLE_INT,
                            "src_vid": _NULLABLE_INT,
                        },
                    },
                },
            },
        },
        "missed": {"type": ["string", "null"]},
    },
}

MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["pairs"],
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["newer_logical_id", "older_logical_id", "why"],
                "properties": {
                    "newer_logical_id": {"type": "integer"},
                    "older_logical_id": {"type": "integer"},
                    "area": _NULLABLE_STR,
                    "why": {"type": "string"},
                },
            },
        },
        "file_fixes": {"type": "array", "items": {"type": "object"}},
        "notes": {"type": ["string", "null"]},
    },
}


def _schema_errors(obj: Any, schema: dict[str, Any]) -> list[str]:
    v = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in sorted(v.iter_errors(obj), key=lambda e: list(map(str, e.absolute_path)))
    ][:20]


def _coverage(got: list[Any], want: list[Any], key: str) -> list[str]:
    errs: list[str] = []
    seen: set[Any] = set()
    for g in got:
        if g in seen:
            errs.append(f"{key} {g!r} answered twice")
        seen.add(g)
    missing = [w for w in want if w not in seen]
    extra = [g for g in seen if g not in set(want)]
    if missing:
        errs.append(f"missing {key}(s): {missing[:20]}")
    if extra:
        errs.append(f"{key}(s) not in your slice: {sorted(extra, key=str)[:20]}")
    return errs


def validate_pass1(obj: Any, slice_obj: dict[str, Any]) -> list[str]:
    errs = _schema_errors(obj, PASS1_SCHEMA)
    if errs:
        return errs
    errs = _coverage([v["cid"] for v in obj["verdicts"]], [c["cid"] for c in slice_obj["candidates"]], "cid")
    for v in obj["verdicts"]:
        if v["verdict"] == "SUPERSESSION" and not (v.get("newer_logical_id") and v.get("older_logical_id")):
            errs.append(f"cid {v['cid']}: a SUPERSESSION needs newer_logical_id and older_logical_id")
    return errs


def validate_pass2(obj: Any, slice_obj: dict[str, Any]) -> list[str]:
    errs = _schema_errors(obj, PASS2_SCHEMA)
    if errs:
        return errs
    errs = _coverage([v["i"] for v in obj["verdicts"]], [r["i"] for r in slice_obj["records"]], "i")
    for v in obj["verdicts"]:
        fix = v.get("fix") or {}
        if v["verdict"] == "FIX":
            if not any(fix.get(k) for k in ("older_span", "newer_quote", "src_logical_id")):
                errs.append(f"i {v['i']}: a FIX needs older_span, newer_quote and/or src_logical_id")
            if fix.get("src_logical_id") is not None and fix.get("src_vid") is None:
                errs.append(f"i {v['i']}: a FIX with src_logical_id needs src_vid")
        if v["verdict"] == "DROP" and not v["failed_tests"]:
            errs.append(f"i {v['i']}: a DROP names its failed_tests")
    return errs


def validate_map(obj: Any, slice_obj: dict[str, Any]) -> list[str]:
    return _schema_errors(obj, MAP_SCHEMA)


VALIDATORS: dict[str, Callable[[Any, dict[str, Any]], list[str]]] = {
    "map": validate_map,
    "pass1": validate_pass1,
    "pass2": validate_pass2,
}
BRIEFS = {"map": "MAP.md", "pass1": "PASS1.md", "pass2": "PASS2.md"}


# ------------------------------------------------------------------ briefs
def brief_text(stage: str) -> str:
    return resources.files("hlmemo.curate").joinpath("briefs", BRIEFS[stage]).read_text(encoding="utf-8")


def render_brief(stage: str, values: dict[str, str]) -> str:
    """The brief with every ``{{NAME}}`` placeholder filled; an unfilled one is an error."""
    text = brief_text(stage)
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    if "{{" in text:
        start = text.index("{{")
        raise ValueError(f"brief {stage}: unfilled placeholder {text[start : start + 30]!r}")
    return text


# ------------------------------------------------------------------ running
@dataclass
class Job:
    stage: str  # map | pass1 | pass2
    wid: str  # w01, r1-w02 ...
    workdir: Path
    slice_obj: dict[str, Any]
    prompt: str  # the rendered brief (its paths name this job's slice/out files)


@dataclass
class JobResult:
    wid: str
    status: str  # ok | failed | skipped (a valid earlier output was reused)
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    output: Any = None
    stray_files: list[str] = field(default_factory=list)


def slice_text(slice_obj: dict[str, Any]) -> str:
    return json.dumps(slice_obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_argv(cmd: str, values: dict[str, str]) -> list[str]:
    argv = shlex.split(cmd)
    if not argv:
        raise ValueError(f"no agent command: set {AGENT_ENV} or --agent-cmd")
    out = []
    for a in argv:
        for k, v in values.items():
            a = a.replace("{" + k + "}", v)
        out.append(a)
    return out


def _read_output(path: Path) -> tuple[Any, list[str]]:
    if not path.is_file():
        return None, [f"no output file at {path}"]
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except (ValueError, UnicodeDecodeError) as exc:
        return None, [f"output is not valid JSON: {exc}"]


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _tail(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return text[-LOG_TAIL:]


def run_job(job: Job, cmd: str, export_dir: Path, *, timeout_s: float) -> JobResult:
    """Run one slice (resumable: a valid output for the SAME slice is reused)."""
    job.workdir.mkdir(parents=True, exist_ok=True)
    slice_path, out_path, status_path = (job.workdir / n for n in (SLICE_NAME, OUT_NAME, STATUS_NAME))
    stext = slice_text(job.slice_obj)
    validate = VALIDATORS[job.stage]
    if status_path.is_file() and slice_path.is_file() and slice_path.read_text(encoding="utf-8") == stext:
        prev = json.loads(status_path.read_text(encoding="utf-8"))
        obj, errs = _read_output(out_path)
        if prev.get("status") == "ok" and not errs and not validate(obj, job.slice_obj):
            return JobResult(job.wid, "skipped", prev.get("attempts", 0), [], obj)
    slice_path.write_text(stext, encoding="utf-8")
    values = {
        "export": str(export_dir),
        "workdir": str(job.workdir),
        "slice": str(slice_path),
        "out": str(out_path),
    }
    argv = render_argv(cmd, values)
    allowed = {SLICE_NAME, OUT_NAME, STATUS_NAME}
    errors: list[str] = []
    result = JobResult(job.wid, "failed")
    for attempt in (1, 2):
        result.attempts = attempt
        if out_path.exists():
            out_path.unlink()
        prompt = job.prompt
        if attempt == 2:
            prompt += (
                "\n\n## RETRY: your previous attempt was rejected\n"
                + "\n".join(f"- {e}" for e in errors[:20])
                + f"\nWrite the COMPLETE output file `{out_path}` again, exactly as the output "
                + "contract says.\n"
            )
        (job.workdir / f"prompt-{attempt}.md").write_text(prompt, encoding="utf-8")
        allowed |= {f"prompt-{attempt}.md", f"agent-{attempt}.log"}
        env = {
            **os.environ,
            "HLM_CURATE_STAGE": job.stage,
            "HLM_CURATE_ATTEMPT": str(attempt),
            "HLM_CURATE_EXPORT": values["export"],
            "HLM_CURATE_WORKDIR": values["workdir"],
            "HLM_CURATE_SLICE": values["slice"],
            "HLM_CURATE_OUT": values["out"],
        }
        errors = []
        started = datetime.now(UTC)
        log = job.workdir / f"agent-{attempt}.log"
        try:
            proc = subprocess.run(
                argv,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=job.workdir,
                env=env,
                timeout=timeout_s,
                check=False,
            )
            log.write_text(
                f"# exit {proc.returncode}  started {started.isoformat(timespec='seconds')}\n"
                f"## stdout\n{_tail(proc.stdout)}\n## stderr\n{_tail(proc.stderr)}\n",
                encoding="utf-8",
            )
            if proc.returncode != 0:
                errors.append(f"agent exited {proc.returncode}")
        except subprocess.TimeoutExpired as exc:
            log.write_text(
                f"# timeout after {timeout_s}s\n## stdout\n{_tail(exc.stdout)}\n", encoding="utf-8"
            )
            errors.append(f"agent timed out after {timeout_s:.0f}s")
        except OSError as exc:
            log.write_text(f"# could not start the agent: {type(exc).__name__}: {exc}\n", encoding="utf-8")
            errors.append(f"could not start the agent ({type(exc).__name__}: {argv[0]})")
        obj, read_errs = _read_output(out_path)
        errors += read_errs if read_errs else validate(obj, job.slice_obj)
        if not errors:
            result.status, result.output = "ok", obj
            break
    result.errors = errors
    result.stray_files = sorted(
        p.relative_to(job.workdir).as_posix()
        for p in job.workdir.rglob("*")
        if p.is_file() and p.relative_to(job.workdir).as_posix() not in allowed
    )
    _write_json(
        status_path,
        {
            "wid": job.wid,
            "stage": job.stage,
            "status": result.status,
            "attempts": result.attempts,
            "errors": result.errors,
            "slice_sha256": sha(stext),
            "stray_files": result.stray_files,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    return result


def run_jobs(
    jobs: list[Job], cmd: str, export_dir: Path, *, parallel: int, timeout_s: float
) -> list[JobResult]:
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        return list(pool.map(lambda j: run_job(j, cmd, export_dir, timeout_s=timeout_s), jobs))


__all__ = [
    "AGENT_ENV",
    "MAP_SCHEMA",
    "PASS1_SCHEMA",
    "PASS2_SCHEMA",
    "Job",
    "JobResult",
    "brief_text",
    "render_argv",
    "render_brief",
    "run_job",
    "run_jobs",
    "validate_map",
    "validate_pass1",
    "validate_pass2",
]
