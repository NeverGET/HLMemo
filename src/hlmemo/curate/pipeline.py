"""The ``hlm curate`` stages, resumable: every stage writes into the run directory and records
``<stage>/stage.json`` with the sha256 of its inputs; a re-run skips a ``complete`` stage whose inputs
are unchanged. Agent stages are also resumable per slice (a valid output for the same slice is
reused), so a re-run retries only the failed slices.

Run directory::

    config.json               run identity + effective configuration (no secrets)
    input/candidates.json     the candidates file given with --candidates (copied)
    export/items/             the hlm export the whole run (and every worker) reads
    export/export.json        its item count and fingerprint
    map/                      (--map) w01/... worker dirs, pairs.json, failed.json
    candidates/               candidates.jsonl, skipped.jsonl
    pass1/                    w01/... worker dirs, verdicts.jsonl, failed.jsonl
    build/                    records.jsonl, skipped.jsonl
    gate1/                    passed.jsonl, report.json
    pass2/                    r1/ r2/ worker dirs, verdicts.jsonl, failed.jsonl
    refine/                   kept.jsonl, held.jsonl, dropped.jsonl
    gate2/                    passed.jsonl, held.jsonl, dropped.jsonl, report.json
    authority/                final.jsonl, held.jsonl
    final.jsonl held.jsonl REVIEW.md summary.json     the preview bundle
    apply/                    apply.sh (--apply), preview.json, apply.json
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hlmemo import __version__
from hlmemo.curate import bundle, exportdir, workers
from hlmemo.curate import gate as g

STAGES = (
    "export",
    "map",
    "candidates",
    "pass1",
    "build",
    "gate1",
    "pass2",
    "refine",
    "gate2",
    "authority",
    "bundle",
)
AGENT_STAGES = ("map", "pass1", "pass2")
PASS2_MODES = ("split", "cross")


class CurateError(Exception):
    def __init__(self, message: str, exit_code: int = 65) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


@dataclass
class Config:
    project: str
    source: str  # "candidates" | "map"
    today: str
    created_at: str
    export_from: str | None = None  # an existing export dir (copied), else `hlm export`
    librarian_status: str | None = None
    workers: int = 3
    refuters: int = 2
    pass2_mode: str = "split"
    authority: list[str] = field(default_factory=lambda: list(g.DEFAULT_AUTHORITY))
    references: list[str] = field(default_factory=list)
    worker_timeout_s: float = 1800.0
    model_label: str = "agent"
    confidence: float = 0.8
    agent_cmd_sha256: str | None = None  # which command ran (the command itself is not stored)

    def labels(self) -> g.Labels:
        return g.Labels(
            model=self.model_label,
            profile="curate-agent",
            prompt_version="curate-v1",
            generator=f"hlm-curate/{__version__}",
            confidence=self.confidence,
        )

    @classmethod
    def load(cls, path: Path) -> Config:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=1, sort_keys=True) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ file helpers
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


# ------------------------------------------------------------------ the run
@dataclass
class Run:
    root: Path
    cfg: Config
    agent_cmd: str | None = None
    exporter: Callable[[Path], dict[str, Any]] | None = None
    log: Callable[[str], None] = print
    _export: exportdir.Export | None = None

    def p(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    @property
    def export_dir(self) -> Path:
        return self.p("export", "items")

    def export(self) -> exportdir.Export:
        if self._export is None:
            self._export = exportdir.load(self.export_dir)
        return self._export

    def check_export(self) -> None:
        meta = read_json(_export_meta(self))
        if meta is None:
            raise CurateError("no export in the run directory (the export stage did not finish)")
        if exportdir.fingerprint(self.export_dir) != meta["fingerprint"]:
            raise CurateError(
                "the export changed after it was taken (did a worker write into it?); "
                "re-run with --redo export"
            )

    # ---------------------------------------------------------- stage bookkeeping
    def stages(self) -> list[str]:
        return [s for s in STAGES if s != "map" or self.cfg.source == "map"]

    def inputs_sha(self, stage: str) -> str:
        files, extra = _INPUTS[stage](self)
        h = hashlib.sha256(json.dumps(extra, sort_keys=True, default=str).encode())
        for f in files:
            h.update(f.name.encode())
            h.update(f.read_bytes() if f.is_file() else b"<missing>")
        return h.hexdigest()

    def stage_state(self, stage: str) -> dict[str, Any] | None:
        return read_json(self.p(stage, "stage.json"))

    def is_complete(self, stage: str) -> bool:
        st = self.stage_state(stage)
        return bool(
            st and st.get("status") == "complete" and st.get("inputs_sha256") == self.inputs_sha(stage)
        )

    def redo(self, stage: str) -> None:
        """Forget ``stage`` and every later stage (their files are removed)."""
        order = self.stages()
        if stage not in order:
            raise CurateError(f"unknown stage for this run: {stage}", 64)
        for s in order[order.index(stage) :]:
            d = self.p(s)
            if d.is_dir():
                shutil.rmtree(d)
        for name in ("final.jsonl", "held.jsonl", "REVIEW.md", "summary.json"):
            self.p(name).unlink(missing_ok=True)

    def run(self, *, stop_after: str | None = None) -> dict[str, Any]:
        """Run every stage that is not complete; returns ``{stage: state}``."""
        states: dict[str, Any] = {}
        for stage in self.stages():
            if stage != "export":
                self.check_export()
            if self.is_complete(stage):
                self.log(f"skip  {stage:<10} (complete)")
            else:
                sha = self.inputs_sha(stage)
                self.p(stage).mkdir(parents=True, exist_ok=True)
                status, counts = _RUN[stage](self)
                state = {
                    "stage": stage,
                    "status": status,
                    "inputs_sha256": sha,
                    "counts": counts,
                    "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                write_json(self.p(stage, "stage.json"), state)
                self.log(f"{'done' if status == 'complete' else status:<5} {stage:<10} {_short(counts)}")
            states[stage] = self.stage_state(stage)
            if stage == stop_after:
                break
        return states

    # ---------------------------------------------------------- agents
    def run_agents(self, stage: str, jobs: list[workers.Job]) -> list[workers.JobResult]:
        if jobs and not self.agent_cmd:
            raise CurateError(f"no agent command: set {workers.AGENT_ENV} or pass --agent-cmd", 64)
        self.log(f"run   {stage:<10} {len(jobs)} worker(s)")
        res = workers.run_jobs(
            jobs,
            self.agent_cmd or "",
            self.export_dir,
            parallel=len(jobs),
            timeout_s=self.cfg.worker_timeout_s,
        )
        for r in res:
            if r.status == "failed":
                self.log(f"  FAILED {stage}/{r.wid} after {r.attempts} attempt(s): {r.errors[:3]}")
            if r.stray_files:
                self.log(f"  note {stage}/{r.wid} wrote extra files: {r.stray_files[:5]}")
        self.check_export()
        return res

    def brief_values(self, slice_path: Path, out_path: Path) -> dict[str, str]:
        refs = self.cfg.references
        if refs:
            ref = (
                "- **Read-only references** (only for understanding a chain; quotes MUST come from the "
                "export bodies, because these may differ): " + ", ".join(f"`{r}`" for r in refs)
            )
        else:
            ref = "- No other files are available."
        return {
            "PROJECT": self.cfg.project,
            "TODAY": self.cfg.today,
            "EXPORT_DIR": str(self.export_dir),
            "SLICE_FILE": str(slice_path),
            "OUTPUT_FILE": str(out_path),
            "REFERENCES": ref,
        }

    def job(self, stage: str, wid: str, slice_obj: dict[str, Any]) -> workers.Job:
        wd = self.p(stage, wid)
        prompt = workers.render_brief(
            stage, self.brief_values(wd / workers.SLICE_NAME, wd / workers.OUT_NAME)
        )
        return workers.Job(stage=stage, wid=wid, workdir=wd, slice_obj=slice_obj, prompt=prompt)


def _short(counts: dict[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in counts.items() if not isinstance(v, dict | list))


# ------------------------------------------------------------------ stage inputs
def _export_meta(r: Run) -> Path:
    return r.p("export", "export.json")


_INPUTS: dict[str, Callable[[Run], tuple[list[Path], dict[str, Any]]]] = {
    "export": lambda r: ([], {"export_from": r.cfg.export_from, "project": r.cfg.project}),
    "map": lambda r: ([_export_meta(r)], {"workers": r.cfg.workers}),
    "candidates": lambda r: (
        [
            _export_meta(r),
            r.p("map", "pairs.json") if r.cfg.source == "map" else r.p("input", "candidates.json"),
        ],
        {"status": r.cfg.librarian_status},
    ),
    "pass1": lambda r: ([_export_meta(r), r.p("candidates", "candidates.jsonl")], {"workers": r.cfg.workers}),
    "build": lambda r: (
        [_export_meta(r), r.p("candidates", "candidates.jsonl"), r.p("pass1", "verdicts.jsonl")],
        asdict(r.cfg.labels()),
    ),
    "gate1": lambda r: ([_export_meta(r), r.p("build", "records.jsonl")], {}),
    "pass2": lambda r: (
        [_export_meta(r), r.p("gate1", "passed.jsonl")],
        {"refuters": r.cfg.refuters, "mode": r.cfg.pass2_mode},
    ),
    "refine": lambda r: (
        [r.p("gate1", "passed.jsonl"), r.p("pass2", "verdicts.jsonl")],
        {"refuters": r.cfg.refuters, "mode": r.cfg.pass2_mode},
    ),
    "gate2": lambda r: ([_export_meta(r), r.p("refine", "kept.jsonl")], {}),
    "authority": lambda r: ([_export_meta(r), r.p("gate2", "passed.jsonl")], {"globs": r.cfg.authority}),
    "bundle": lambda r: (
        [
            _export_meta(r),
            *(r.p(s, "stage.json") for s in r.stages() if s not in ("export", "bundle")),
            r.p("authority", "final.jsonl"),
        ],
        {},
    ),
}


# ------------------------------------------------------------------ stages
def _st_export(r: Run) -> tuple[str, dict[str, Any]]:
    if r.export_dir.exists():
        shutil.rmtree(r.export_dir)
    if r.cfg.export_from:
        src = Path(r.cfg.export_from)
        if not src.is_dir():
            raise CurateError(f"--export: no such directory: {src}", 64)
        shutil.copytree(src, r.export_dir)
        how = "copied"
    else:
        if r.exporter is None:
            raise CurateError(
                "no export: pass --export DIR or run where `hlm export` can reach the server", 64
            )
        r.exporter(r.export_dir)
        how = "hlm export"
    r._export = None
    ex = r.export()
    if not ex.items:
        raise CurateError(f"the export at {r.export_dir} holds no items")
    meta = {
        "items": len(ex.items),
        "live_supersedes": len(ex.live_supersedes),
        "fingerprint": exportdir.fingerprint(r.export_dir),
        "how": how,
        "taken_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    write_json(_export_meta(r), meta)
    return "complete", {k: meta[k] for k in ("items", "live_supersedes", "how")}


def _worker_status(results: list[workers.JobResult]) -> str:
    return "partial" if any(x.status == "failed" for x in results) else "complete"


def _st_map(r: Run) -> tuple[str, dict[str, Any]]:
    files = sorted(it.file for it in r.export().items.values())
    jobs = [
        r.job("map", f"w{k + 1:02d}", {"files": part})
        for k, part in enumerate(g.slices(files, r.cfg.workers))
    ]
    res = r.run_agents("map", jobs)
    pairs: list[dict[str, Any]] = []
    fixes: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for job, x in zip(jobs, res, strict=True):
        if x.status == "failed":
            failed.append({"wid": x.wid, "errors": x.errors, "files": job.slice_obj["files"]})
            continue
        pairs += [{**p, "wid": x.wid} for p in x.output["pairs"]]
        fixes += [{**f, "wid": x.wid} for f in x.output.get("file_fixes") or []]
    write_json(r.p("map", "pairs.json"), {"origin": "map", "pairs": pairs, "file_fixes": fixes})
    write_json(r.p("map", "failed.json"), failed)
    counts = {
        "slices": len(jobs),
        "failed_slices": len(failed),
        "unmapped_files": sum(len(f["files"]) for f in failed),
        "pairs": len(pairs),
        "file_fixes": len(fixes),
    }
    return _worker_status(res), counts


def _st_candidates(r: Run) -> tuple[str, dict[str, Any]]:
    src = r.p("map", "pairs.json") if r.cfg.source == "map" else r.p("input", "candidates.json")
    data = read_json(src)
    if data is None:
        raise CurateError(f"no candidates input at {src}")
    try:
        cands, skipped = g.normalize_candidates(data, r.export(), status=r.cfg.librarian_status)
    except ValueError as exc:
        raise CurateError(str(exc), 64) from None
    write_jsonl(r.p("candidates", "candidates.jsonl"), cands)
    write_jsonl(r.p("candidates", "skipped.jsonl"), skipped)
    reasons = Counter(s["reason"] for s in skipped)
    return "complete", {
        "input": len(cands) + len(skipped),
        "candidates": len(cands),
        "skipped": dict(sorted(reasons.items())),
    }


def _st_pass1(r: Run) -> tuple[str, dict[str, Any]]:
    cands = read_jsonl(r.p("candidates", "candidates.jsonl"))
    jobs = [
        r.job("pass1", f"w{k + 1:02d}", {"candidates": part})
        for k, part in enumerate(g.slices(cands, r.cfg.workers))
    ]
    res = r.run_agents("pass1", jobs)
    verdicts: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for job, x in zip(jobs, res, strict=True):
        if x.status == "failed":
            failed += [
                {"cid": c["cid"], "wid": x.wid, "errors": x.errors} for c in job.slice_obj["candidates"]
            ]
            continue
        verdicts += [{**v, "wid": x.wid} for v in x.output["verdicts"]]
    write_jsonl(r.p("pass1", "verdicts.jsonl"), verdicts)
    write_jsonl(r.p("pass1", "failed.jsonl"), failed)
    counts = {
        "slices": len(jobs),
        "failed_slices": sum(1 for x in res if x.status == "failed"),
        "unverified": len(failed),
        "verdicts": dict(sorted(Counter(v["verdict"] for v in verdicts).items())),
        "direction_false": sum(1 for v in verdicts if v.get("direction_ok") is False),
    }
    return _worker_status(res), counts


def _st_build(r: Run) -> tuple[str, dict[str, Any]]:
    cands = read_jsonl(r.p("candidates", "candidates.jsonl"))
    unverified = {f["cid"] for f in read_jsonl(r.p("pass1", "failed.jsonl"))}
    verdicts = {v["cid"]: v for v in read_jsonl(r.p("pass1", "verdicts.jsonl"))}
    records, skipped = g.build_records(
        [c for c in cands if c["cid"] not in unverified],
        verdicts,
        r.export(),
        project=r.cfg.project,
        labels=r.cfg.labels(),
    )
    write_jsonl(r.p("build", "records.jsonl"), records)
    write_jsonl(r.p("build", "skipped.jsonl"), skipped)
    return "complete", {
        "records": len(records),
        "skipped": dict(sorted(Counter(s["reason"] for s in skipped).items())),
    }


def _gate_stage(r: Run, stage: str, records: list[dict[str, Any]]) -> g.GateResult:
    res = g.gate(records, r.export(), r.cfg.project)
    write_jsonl(r.p(stage, "passed.jsonl"), res.passed)
    write_json(r.p(stage, "report.json"), {"counts": res.counts(), "rows": res.rows})
    return res


def _st_gate1(r: Run) -> tuple[str, dict[str, Any]]:
    return "complete", _gate_stage(r, "gate1", read_jsonl(r.p("build", "records.jsonl"))).counts()


def pass2_record(i: int, rec: dict[str, Any], ex: exportdir.Export) -> dict[str, Any]:
    s, d = ex.head(int(rec["src_logical_id"])), ex.head(int(rec["dst_logical_id"]))
    return {
        "i": i,
        "cid": rec.get("cid"),
        "src_logical_id": rec["src_logical_id"],
        "src_vid": rec["src_vid"],
        "src_clue": rec.get("src_clue"),
        "src_file": s.file if s else None,
        "src_title": s.title if s else None,
        "dst_logical_id": rec["dst_logical_id"],
        "dst_vid": rec["dst_vid"],
        "dst_clue": rec.get("dst_clue"),
        "dst_file": d.file if d else None,
        "dst_title": d.title if d else None,
        "older_span": rec["older_span"],
        "newer_quote": rec["newer_quote"],
        "why": rec.get("why"),
    }


def _st_pass2(r: Run) -> tuple[str, dict[str, Any]]:
    recs = read_jsonl(r.p("gate1", "passed.jsonl"))
    ex = r.export()
    items = [pass2_record(i, rec, ex) for i, rec in enumerate(recs)]
    if r.cfg.pass2_mode == "cross":
        parts = [items] * r.cfg.refuters if items else []
    else:
        parts = g.slices(items, r.cfg.refuters)
    jobs = [r.job("pass2", f"r{k + 1}", {"records": part}) for k, part in enumerate(parts)]
    res = r.run_agents("pass2", jobs)
    verdicts: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    cid_of = {it["i"]: it["cid"] for it in items}
    for job, x in zip(jobs, res, strict=True):
        if x.status == "failed":
            failed += [
                {"i": it["i"], "cid": it["cid"], "wid": x.wid, "errors": x.errors}
                for it in job.slice_obj["records"]
            ]
            continue
        verdicts += [{**v, "cid": cid_of.get(v["i"]), "reviewer": x.wid} for v in x.output["verdicts"]]
    write_jsonl(r.p("pass2", "verdicts.jsonl"), verdicts)
    write_jsonl(r.p("pass2", "failed.jsonl"), failed)
    counts = {
        "records": len(items),
        "mode": r.cfg.pass2_mode,
        "slices": len(jobs),
        "failed_slices": sum(1 for x in res if x.status == "failed"),
        "verdicts": dict(sorted(Counter(v["verdict"] for v in verdicts).items())),
    }
    return _worker_status(res), counts


def _st_refine(r: Run) -> tuple[str, dict[str, Any]]:
    recs = read_jsonl(r.p("gate1", "passed.jsonl"))
    by_i: dict[int, list[dict[str, Any]]] = {}
    for v in read_jsonl(r.p("pass2", "verdicts.jsonl")):
        by_i.setdefault(int(v["i"]), []).append(v)
    expected = r.cfg.refuters if r.cfg.pass2_mode == "cross" else 1
    kept, held, dropped = [], [], []
    decisions: Counter[str] = Counter()
    for i, rec in enumerate(recs):
        decision, fix = g.combine(by_i.get(i, []), expected)
        decisions[decision] += 1
        if decision == "KEEP":
            kept.append(rec)
        elif decision == "FIX":
            assert fix is not None
            kept.append(g.apply_fix(rec, fix))
        elif decision == "DROP":
            dropped.append({**rec, "dropped_by": "pass2"})
        else:
            held.append({**rec, "held_reason": f"pass2_{decision.lower()}"})
    write_jsonl(r.p("refine", "kept.jsonl"), kept)
    write_jsonl(r.p("refine", "held.jsonl"), held)
    write_jsonl(r.p("refine", "dropped.jsonl"), dropped)
    return "complete", {
        "kept": len(kept),
        "held": len(held),
        "dropped": len(dropped),
        "decisions": dict(sorted(decisions.items())),
    }


def _st_gate2(r: Run) -> tuple[str, dict[str, Any]]:
    recs = read_jsonl(r.p("refine", "kept.jsonl"))
    res = _gate_stage(r, "gate2", recs)
    held, dropped = [], []
    for row in res.failed:
        rec = recs[row["k"]]
        if rec.get("fixed"):
            held.append({**rec, "held_reason": "fix_failed_gate", "gate_errors": row["errors"]})
        else:
            dropped.append({**rec, "dropped_by": "gate2", "gate_errors": row["errors"]})
    write_jsonl(r.p("gate2", "held.jsonl"), held)
    write_jsonl(r.p("gate2", "dropped.jsonl"), dropped)
    return "complete", {**res.counts(), "held": len(held), "dropped": len(dropped)}


def _st_authority(r: Run) -> tuple[str, dict[str, Any]]:
    final, held = g.authority_split(read_jsonl(r.p("gate2", "passed.jsonl")), r.export(), r.cfg.authority)
    write_jsonl(r.p("authority", "final.jsonl"), final)
    write_jsonl(r.p("authority", "held.jsonl"), held)
    return "complete", {"final": len(final), "held": len(held)}


def _st_bundle(r: Run) -> tuple[str, dict[str, Any]]:
    out = bundle.write_bundle(r)
    return "complete", out


_RUN: dict[str, Callable[[Run], tuple[str, dict[str, Any]]]] = {
    "export": _st_export,
    "map": _st_map,
    "candidates": _st_candidates,
    "pass1": _st_pass1,
    "build": _st_build,
    "gate1": _st_gate1,
    "pass2": _st_pass2,
    "refine": _st_refine,
    "gate2": _st_gate2,
    "authority": _st_authority,
    "bundle": _st_bundle,
}


__all__ = [
    "AGENT_STAGES",
    "PASS2_MODES",
    "STAGES",
    "Config",
    "CurateError",
    "Run",
    "pass2_record",
    "read_json",
    "read_jsonl",
    "write_json",
    "write_jsonl",
]
