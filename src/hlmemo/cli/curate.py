"""``hlm curate``: the owner-run, LOCAL supersession-curation pipeline (D-240, D-244).

    hlm curate --project P (--candidates FILE | --map) [--export DIR] [--run-dir DIR]
               [--agent-cmd CMD] [--workers N] [--refuters R] [--pass2-mode cross|split]
               [--authority GLOB ...] [--reference DIR ...] [--stop-after STAGE] [--redo STAGE]
    hlm curate --run-dir DIR | --resume --project P            # resume: completed stages are skipped
    hlm curate --run-dir DIR --apply --state DIR [--execute preview|apply]

Stages: export, [map], candidates, pass1, build, gate1, pass2, refine, gate2, authority, bundle.
``--map`` (agents find the candidates) is EXPERIMENTAL: not yet measured on real data. Pass 2 is
``cross`` by default: every refuter judges every record, and a record survives only when every
refuter says KEEP or FIX with an identical fix.
The agent stages (map, pass1, pass2) run the configured agent command (``HLM_CURATE_AGENT_CMD``,
the prompt on STDIN); everything else is deterministic. Nothing touches prod: ``--apply`` writes and
prints the RUNBOOK commands, and only ``--apply --execute preview|apply`` runs them.

Exit codes: 0 ok; 64 usage; 65 data (a changed export, a final.jsonl that no longer passes the gate);
75 completed with failed worker slices (re-run the same command to retry them); the apply script's own
exit code with ``--execute``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

EX_USAGE = 64
EX_DATAERR = 65
EX_TEMPFAIL = 75
AUTHORITY_ENV = "HLM_CURATE_AUTHORITY"
STATE_ENV = "HLM_CURATE_STATE"
DEFAULT_SUBDIR = Path("docs/private/curate")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _existing_parent(path: Path) -> Path:
    p = path.resolve()
    while not p.exists():
        p = p.parent
    return p


def git_toplevel(path: Path) -> Path | None:
    try:
        res = _git(["rev-parse", "--show-toplevel"], _existing_parent(path))
    except OSError:
        return None
    return Path(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else None


# one probe per kind of file a run writes (consult 105 #2: a *.json rule alone is not enough)
RUN_PROBES = ("REVIEW.md", "final.jsonl", "summary.json", "export/items/fact/probe.md", "pass1/w01/out.json")


def privacy_problem(run_dir: Path) -> str | None:
    """A run directory holds owner data. Inside a git work tree, the DIRECTORY ITSELF and every kind
    of file the run writes must be gitignored, and nothing under it may be tracked."""
    top = git_toplevel(run_dir)
    if top is None:
        return None
    rd = run_dir.resolve()
    hint = f"use {DEFAULT_SUBDIR}/ (ignored) or a directory outside the repo"
    tracked = _git(["ls-files", "--", str(rd)], top)
    if tracked.returncode != 0 or tracked.stdout.strip():
        return f"run directory {run_dir} holds files TRACKED by the git work tree {top}; {hint}"
    for probe in ("", *RUN_PROBES):
        path = f"{rd}/" if not probe else str(rd / probe)
        if _git(["check-ignore", "-q", path], top).returncode != 0:
            what = probe or "the directory itself"
            return (
                f"run directory {run_dir} is inside the git work tree {top} and NOT gitignored "
                f"({what}); {hint}"
            )
    return None


def default_root(cwd: Path | None = None) -> Path:
    cwd = cwd or Path.cwd()
    top = git_toplevel(cwd)
    if top is not None:
        return top / DEFAULT_SUBDIR
    from hlmemo.cli.client_config import config_dir

    return config_dir() / "curate"


def latest_run(root: Path, project: str) -> Path | None:
    runs = sorted(p for p in root.glob(f"{project}-*") if (p / "config.json").is_file())
    return runs[-1] if runs else None


def _authority(flags: list[str] | None) -> list[str] | None:
    if flags:
        return list(flags)
    env = os.environ.get(AUTHORITY_ENV, "").strip()
    return [g.strip() for g in env.split(",") if g.strip()] or None


def _fail(msg: str, code: int) -> None:
    typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(code)


def curate_command(
    ctx: typer.Context,
    project: Annotated[
        str | None, typer.Option("--project", help="Project slug (default: root --project)")
    ] = None,
    run_dir: Annotated[
        Path | None, typer.Option("--run-dir", help="Run directory (resume if it exists)")
    ] = None,
    resume: Annotated[bool, typer.Option("--resume", help="Resume the newest run of --project")] = False,
    candidates: Annotated[
        Path | None,
        typer.Option(
            "--candidates", help="Librarian audit JSON (ops librarian audit --json) or {'pairs': [...]}"
        ),
    ] = None,
    map_: Annotated[
        bool,
        typer.Option(
            "--map", help="EXPERIMENTAL (unmeasured): find candidates with a mapping pass of agents"
        ),
    ] = False,
    librarian_status: Annotated[
        str | None, typer.Option("--librarian-status", help="Only audit proposals with this status")
    ] = None,
    export: Annotated[
        Path | None, typer.Option("--export", help="Use this hlm export dir (copied) instead of exporting")
    ] = None,
    agent_cmd: Annotated[
        str | None,
        typer.Option(
            "--agent-cmd", envvar="HLM_CURATE_AGENT_CMD", help="Worker command template (prompt on STDIN)"
        ),
    ] = None,
    workers: Annotated[
        int | None, typer.Option("--workers", min=1, max=16, help="Map/pass-1 workers (3)")
    ] = None,
    refuters: Annotated[
        int | None, typer.Option("--refuters", min=1, max=8, help="Pass-2 refuters (2)")
    ] = None,
    pass2_mode: Annotated[
        str | None,
        typer.Option(
            "--pass2-mode",
            help="cross (default): every refuter judges every record; split: each record by one refuter",
        ),
    ] = None,
    authority: Annotated[
        list[str] | None,
        typer.Option("--authority", help=f"Allowlisted src source.path glob (repeatable; {AUTHORITY_ENV})"),
    ] = None,
    reference: Annotated[
        list[str] | None,
        typer.Option("--reference", help="Read-only reference dir named in the briefs (repeatable)"),
    ] = None,
    worker_timeout: Annotated[
        float | None, typer.Option("--worker-timeout", help="Seconds per worker (1800)")
    ] = None,
    model_label: Annotated[
        str | None,
        typer.Option("--model-label", envvar="HLM_CURATE_MODEL_LABEL", help="Records' model label"),
    ] = None,
    stop_after: Annotated[str | None, typer.Option("--stop-after", help="Stop after this stage")] = None,
    redo: Annotated[str | None, typer.Option("--redo", help="Forget this stage and every later one")] = None,
    apply_: Annotated[bool, typer.Option("--apply", help="Write and print the prod apply commands")] = False,
    state: Annotated[
        Path | None, typer.Option("--state", envvar=STATE_ENV, help="Deploy state dir (deploy/.local/<host>)")
    ] = None,
    ssh_config: Annotated[
        Path | None, typer.Option("--ssh-config", help="Default: <state>/ssh_config")
    ] = None,
    ssh_host: Annotated[str, typer.Option("--ssh-host", help="SSH host alias")] = "hlm-deploy",
    remote_app: Annotated[str, typer.Option("--remote-app", help="App dir on the host")] = "/opt/hlmemo/app",
    remote_env: Annotated[
        str, typer.Option("--remote-env", help="Env file on the host")
    ] = "/etc/hlmemo/prod.env",
    execute: Annotated[
        str | None,
        typer.Option("--execute", help="With --apply: run the script in this mode (preview|apply)"),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Print summary.json at the end")] = False,
) -> None:
    """Curate supersession links: candidates -> verifier agents -> gate -> refuters -> preview bundle."""
    from hlmemo.curate import bundle
    from hlmemo.curate import gate as g
    from hlmemo.curate.pipeline import PASS2_MODES, STAGES, Config, CurateError, Run, read_json

    root = ctx.find_root().obj
    project = project or getattr(root, "project", None)
    as_json = json_out or bool(getattr(root, "as_json", False))
    if pass2_mode is not None and pass2_mode not in PASS2_MODES:
        _fail(f"--pass2-mode must be one of {PASS2_MODES}", EX_USAGE)
    for name, value in (("--stop-after", stop_after), ("--redo", redo)):
        if value is not None and value not in STAGES:
            _fail(f"{name} must be one of {', '.join(STAGES)}", EX_USAGE)
    if execute is not None and (execute not in ("preview", "apply") or not apply_):
        _fail("--execute needs --apply and is preview or apply", EX_USAGE)
    # consult 105 #1: no control character (newline, NUL, ...) in any path or name the run uses
    for name, value in (
        ("--project", project),
        ("--run-dir", run_dir),
        ("--candidates", candidates),
        ("--export", export),
        ("--state", state),
        ("--ssh-config", ssh_config),
        *(("--reference", r) for r in reference or []),
        *(("--authority", a) for a in authority or []),
    ):
        problem = bundle.control_problem(name, str(value)) if value is not None else None
        if problem:
            _fail(problem, EX_USAGE)

    if run_dir is None and resume:
        if not project:
            _fail("--resume needs --project", EX_USAGE)
        run_dir = latest_run(default_root(), project)
        if run_dir is None:
            _fail(f"no run of {project} under {default_root()}", EX_USAGE)
    existing = run_dir is not None and (run_dir / "config.json").is_file()
    if existing:
        assert run_dir is not None
        cfg = Config.load(run_dir / "config.json")
        if project and project != cfg.project:
            _fail(f"{run_dir} is a run of project {cfg.project!r}, not {project!r}", EX_USAGE)
        if candidates is not None or map_ or export is not None:
            _fail(
                "this run already has its candidates and export; start a new --run-dir to change them",
                EX_USAGE,
            )
    else:
        if not project:
            _fail("--project is required", EX_USAGE)
        if (candidates is None) == (not map_):
            _fail("a new run needs exactly one of --candidates FILE or --map", EX_USAGE)
        if candidates is not None and not candidates.is_file():
            _fail(f"--candidates: no such file: {candidates}", EX_USAGE)
        if export is not None and not export.is_dir():
            _fail(f"--export: no such directory: {export}", EX_USAGE)
        now = datetime.now(UTC)
        run_dir = run_dir or default_root() / f"{project}-{now.strftime('%Y%m%d-%H%M%S')}"
        cfg = Config(
            project=project,
            source="map" if map_ else "candidates",
            today=now.date().isoformat(),
            created_at=now.isoformat(timespec="seconds"),
            export_from=str(export.resolve()) if export is not None else None,
            librarian_status=librarian_status,
        )
    assert run_dir is not None
    run_dir = run_dir.resolve()  # workers run in their own cwd: every path they get is absolute
    problem = bundle.control_problem("run directory", str(run_dir)) or privacy_problem(run_dir)
    if problem:
        _fail(problem, EX_USAGE)
    # explicit flags override the stored configuration (identity keys excepted, checked above)
    for key, value in (
        ("workers", workers),
        ("refuters", refuters),
        ("pass2_mode", pass2_mode),
        ("authority", _authority(authority)),
        ("references", [str(Path(r).resolve()) for r in reference] if reference else None),
        ("worker_timeout_s", worker_timeout),
        ("model_label", model_label),
    ):
        if value is not None:
            setattr(cfg, key, value)
    if agent_cmd:
        cfg.agent_cmd_sha256 = hashlib.sha256(agent_cmd.encode()).hexdigest()
    run_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(run_dir, 0o700)
    if not existing and candidates is not None:
        (run_dir / "input").mkdir(exist_ok=True)
        shutil.copyfile(candidates, run_dir / "input" / "candidates.json")
    cfg.save(run_dir / "config.json")

    def exporter(out: Path) -> dict[str, Any]:
        from hlmemo.importers.cli import run_export_command

        return run_export_command(project=cfg.project, out=out, kinds=None, as_of=None, memory=root.memory())

    log = (lambda m: typer.echo(m, err=True)) if as_json else typer.echo
    run = Run(
        root=run_dir, cfg=cfg, agent_cmd=agent_cmd, exporter=exporter if root is not None else None, log=log
    )
    log(f"run dir: {run_dir}")
    try:
        if redo:
            run.redo(redo)
        states = run.run(stop_after=stop_after)
    except CurateError as exc:
        _fail(exc.message, exc.exit_code)
    except (OSError, ValueError) as exc:
        _fail(f"{type(exc).__name__}: {exc}", EX_DATAERR)
    summary = read_json(run_dir / "summary.json") if "bundle" in states else None
    partial = [s for s, st in states.items() if st and st.get("status") == "partial"]
    if summary is not None:
        log(
            f"bundle: final={summary['final']} held={summary['held']} dropped={summary['dropped']}  "
            f"review: {run_dir / 'REVIEW.md'}"
        )
    if partial:
        log(f"WARNING: failed worker slices in {partial}; re-run the same command to retry them")

    if apply_:
        if summary is None:
            _fail("--apply needs a finished bundle (do not combine it with --stop-after)", EX_USAGE)
        rc = _apply(run, bundle, g, state, ssh_config, ssh_host, remote_app, remote_env, execute, log)
        if as_json:
            typer.echo(json.dumps(summary, sort_keys=True))
        raise typer.Exit(rc)
    if as_json and summary is not None:
        typer.echo(json.dumps(summary, sort_keys=True))
    if partial:
        raise typer.Exit(EX_TEMPFAIL)


def _apply(
    run: Any,
    bundle: Any,
    g: Any,
    state: Path | None,
    ssh_config: Path | None,
    ssh_host: str,
    remote_app: str,
    remote_env: str,
    execute: str | None,
    log: Any,
) -> int:
    """Re-gate final.jsonl (the owner may have removed lines), write apply/apply.sh, print it; with
    ``--execute`` run it."""
    from hlmemo.curate.pipeline import read_jsonl

    final = run.p("final.jsonl")
    recs = read_jsonl(final)
    if not recs:
        log("final.jsonl is empty: nothing to apply")
        return 0
    res = g.gate(recs, run.export(), run.cfg.project)
    if res.failed:
        _fail(f"final.jsonl no longer passes the gate: {res.failed[:3]}", EX_DATAERR)
    held = g.authority_split(recs, run.export(), run.cfg.authority)[1]
    if held:
        _fail(f"final.jsonl has {len(held)} record(s) outside the authority allowlist", EX_DATAERR)
    if state is None:
        _fail(
            f"--apply needs --state (or {STATE_ENV}): the deploy state dir, e.g. deploy/.local/<host>",
            EX_USAGE,
        )
    assert state is not None
    sshc = (ssh_config or state / "ssh_config").resolve()
    if not sshc.is_file():
        _fail(f"no ssh config at {sshc} (pass --ssh-config)", EX_USAGE)
    out = run.p("apply")
    out.mkdir(exist_ok=True)
    try:
        text = bundle.apply_script(
            run_name=run.root.name,
            project=run.cfg.project,
            final=final.resolve(),
            out=out.resolve(),
            ssh_config=sshc,
            ssh_host=ssh_host,
            remote_app=remote_app,
            remote_env=remote_env,
        )
    except ValueError as exc:
        _fail(str(exc), EX_USAGE)
    script = out / "apply.sh"
    script.write_text(text, encoding="utf-8")
    script.chmod(0o700)
    log(f"apply script for {len(recs)} link(s): {script}")
    log("  1. preview (rolls back; PASS = exact count, counts unchanged):")
    log(f"     bash {script} preview")
    log("  2. apply (runs the preview again first, then ONE event):")
    log(f"     bash {script} apply")
    log("  commands:")
    for line in text.splitlines():
        if line.lstrip().startswith("ssh ") or line.split("=", 1)[0] in (
            "SSHC",
            "HOST",
            "P",
            "OUT",
            "APPROVED",
        ):
            log(f"     {line.strip()}")
    if execute is None:
        return 0
    log(f"executing: bash {script} {execute}")
    return subprocess.run(["bash", str(script), execute], check=False).returncode


__all__ = ["curate_command", "default_root", "latest_run", "privacy_problem"]
