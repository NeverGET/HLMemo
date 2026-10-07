"""``hlm migrate``: the migration kit's commands (see ``hlmemo.migrate`` for the spec and the safety rules).

    hlm migrate plan   --spec S [--json]
    hlm migrate lint   --spec S [--json]
    hlm migrate seal   --spec S [--expect JSON] [--force] | --verify
    hlm migrate run    --spec S --target local|prod [--batch B] [--apply] [--resume] [--json]
    hlm migrate verify --spec S --target local|prod [--json]
    hlm migrate recall --spec S --truthset T.jsonl [--target local|prod] [--k 5] [--min-rate R] [--json]

Exit codes: 0 ok · 1 lint errors, a seal mismatch or recall below --min-rate · 2 hard stop during a run ·
64 bad spec or arguments · 65 refused before anything was sent (target, seal, environment).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

EX_FAIL, EX_STOP, EX_USAGE, EX_REFUSED = 1, 2, 64, 65

migrate_app = typer.Typer(
    help="Migration kit: plan, lint, seal and import a curated legacy-memory tree (PLAYBOOK).",
    no_args_is_help=True,
    rich_markup_mode=None,
)

SpecOpt = Annotated[Path, typer.Option("--spec", help="the migration.toml of this migration")]
JsonOpt = Annotated[bool, typer.Option("--json", help="print JSON")]
TargetOpt = Annotated[str, typer.Option("--target", help="local | prod")]


def _load(spec_path: Path) -> tuple[Any, Any]:
    from hlmemo.migrate.batches import load
    from hlmemo.migrate.spec import SpecError, load_spec

    try:
        spec = load_spec(spec_path)
    except SpecError as exc:
        typer.echo(f"spec: {exc}", err=True)
        raise typer.Exit(EX_USAGE) from None
    # neutral cwd: nothing below may pick up a ./hlm.toml by accident (all spec paths are absolute now)
    os.chdir(tempfile.gettempdir())
    try:
        loaded = load(spec)
    except ValueError as exc:
        typer.echo(f"parse: {exc}", err=True)
        raise typer.Exit(EX_USAGE) from None
    return spec, loaded


def _write_private(spec: Any, name: str, obj: Any) -> Path:
    out = spec.private_dir / "runs"
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = out / name
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return p


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


@migrate_app.command("plan")
def plan_cmd(spec: SpecOpt, as_json: JsonOpt = False) -> None:
    """The batch table: per-file month buckets, oldest first, undated last."""
    from hlmemo.migrate.batches import plan

    s, loaded = _load(spec)
    rows = plan(loaded)
    if as_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    for r in rows:
        typer.echo(
            f"{r['batch']:8} {r['source']:10} files={r['files']:4} items={r['items']:4} "
            f"est={r['estimated']:3} kinds={r['kinds']} vf={r['valid_from_min']}..{r['valid_from_max']}"
        )
    total = sum(r["items"] for r in rows)
    undated = sum(r["items"] for r in rows if r["batch"] == "undated")
    typer.echo(f"total items {total} | undated {undated} | project {s.slug}")


@migrate_app.command("lint")
def lint_cmd(spec: SpecOpt, as_json: JsonOpt = False) -> None:
    """Layout, dates, tags, lessons, secrets and decision-row wording, plus the real importer's parse."""
    from hlmemo.migrate.lint import lint

    s, loaded = _load(spec)
    res = lint(s, loaded)
    if as_json:
        typer.echo(
            json.dumps(
                {"summary": res.summary, "errors": res.errors, "warnings": res.warnings, "links": res.links},
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        typer.echo(json.dumps(res.summary, ensure_ascii=False))
        for x in res.errors[:80]:
            typer.echo(f"E {x}")
        for x in res.warnings[:40]:
            typer.echo(f"W {x}")
        for ln in res.links[:40]:
            typer.echo(f"L {ln['newer']} -> {ln['older']} ({ln['scope']}, {ln['marker']})")
    raise typer.Exit(0 if res.ok else EX_FAIL)


@migrate_app.command("seal")
def seal_cmd(
    spec: SpecOpt,
    verify: Annotated[
        bool, typer.Option("--verify", help="check the tree against the seal; write nothing")
    ] = False,
    expect: Annotated[
        str | None,
        typer.Option("--expect", help='batch counts the review package states, e.g. {"2026-07": 40}'),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="replace an existing seal (after a new review)")
    ] = False,
) -> None:
    """Seal the reviewed tree (per-file sha256, tree digest, per-batch counts), or verify it."""
    from hlmemo.migrate import seal as sealmod

    s, loaded = _load(spec)
    if verify:
        v = sealmod.verify(s, loaded)
        typer.echo(
            f"seal {'ok' if v.ok else 'MISMATCH'}: tree {v.tree_sha256[:16]}…, batches {v.batch_items}"
        )
        for p in v.problems:
            typer.echo(f"  {p}")
        raise typer.Exit(0 if v.ok else EX_FAIL)
    want = None
    if expect is not None:
        try:
            want = {str(k): int(v) for k, v in json.loads(expect).items()}
        except (ValueError, AttributeError):
            typer.echo("--expect must be a JSON object of batch -> count", err=True)
            raise typer.Exit(EX_USAGE) from None
    try:
        out = sealmod.write(s, loaded, expect=want, force=force)
    except sealmod.SealError as exc:
        typer.echo(f"seal refused: {exc}", err=True)
        raise typer.Exit(EX_REFUSED) from None
    typer.echo(
        f"sealed {len(out['files'])} files, {out['total_items']} items, tree {out['tree_sha256'][:16]}…, "
        f"batches {out['batch_items']} -> {s.seal_path}"
    )


def _run(
    spec: Path, target: str, *, batch: str | None, apply: bool, resume: bool, verify: bool, as_json: bool
) -> None:
    from hlmemo.migrate.runner import HardStop, RunRefused, run

    s, loaded = _load(spec)
    mode = "verify" if verify else ("apply" if apply else "dry")

    def log(line: dict[str, Any]) -> None:
        typer.echo(json.dumps(line, ensure_ascii=False))

    try:
        report = asyncio.run(
            run(
                s,
                loaded,
                target,
                batch=batch,
                apply=apply,
                resume=resume,
                verify=verify,
                log=None if as_json else log,
            )
        )
    except RunRefused as exc:
        typer.echo(f"refused: {exc}", err=True)
        raise typer.Exit(EX_REFUSED) from None
    except HardStop as exc:
        p = _write_private(s, f"{_stamp()}-{target}-{mode}-STOPPED.json", exc.report)
        typer.echo(f"HARD STOP: {exc} (report {p})", err=True)
        raise typer.Exit(EX_STOP) from None
    p = _write_private(s, f"{_stamp()}-{target}-{mode}.json", report)
    written = sum(r.get("written") or 0 for r in report if r["mode"] == "apply")
    if as_json:
        typer.echo(json.dumps({"report": report, "file": str(p)}, ensure_ascii=False, indent=1))
    else:
        typer.echo(f"# {mode} done on {target}: {len(report)} runs, written {written}; report {p}")


@migrate_app.command("run")
def run_cmd(
    spec: SpecOpt,
    target: TargetOpt = "local",
    batch: Annotated[
        str | None, typer.Option("--batch", help="only this batch (e.g. 2026-07, undated)")
    ] = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="WRITE (after the same batch's dry run passes)")
    ] = False,
    resume: Annotated[
        bool, typer.Option("--resume", help="continue a batch an interrupted apply partly wrote")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Import the batches oldest first; a dry run unless --apply."""
    _run(spec, target, batch=batch, apply=apply, resume=resume, verify=False, as_json=as_json)


@migrate_app.command("verify")
def verify_cmd(spec: SpecOpt, target: TargetOpt = "prod", as_json: JsonOpt = False) -> None:
    """Dry run of every batch; each must classify as unchanged (everything is present)."""
    _run(spec, target, batch=None, apply=False, resume=False, verify=True, as_json=as_json)


@migrate_app.command("recall")
def recall_cmd(
    spec: SpecOpt,
    truthset: Annotated[Path, typer.Option("--truthset", help="the sealed truth set (JSONL)")],
    target: TargetOpt = "local",
    k: Annotated[int, typer.Option("--k", help="top-k hits to search")] = 5,
    min_rate: Annotated[
        float, typer.Option("--min-rate", help="exit 1 below this hit rate (default 0: report only)")
    ] = 0.0,
    as_json: JsonOpt = False,
) -> None:
    """Per truth-set question: is an item of the quoted file among the top-k query hits? (retrieval only)"""
    from hlmemo.migrate.recall import load_truthset, recall
    from hlmemo.migrate.runner import RunRefused, default_session

    truth = truthset.expanduser().resolve()
    s, _loaded = _load(spec)
    try:
        rows = load_truthset(truth)
        t = s.target(target)
    except (ValueError, OSError) as exc:
        typer.echo(f"recall: {exc}", err=True)
        raise typer.Exit(EX_USAGE) from None
    if target == "local" and not t.is_loopback:
        typer.echo("refused: the local target is not a loopback URL", err=True)
        raise typer.Exit(EX_REFUSED)

    async def go() -> dict[str, Any]:
        async with default_session(t) as call:
            return await recall(call, s.slug, rows, k=k)

    try:
        out = asyncio.run(go())
    except RunRefused as exc:
        typer.echo(f"refused: {exc}", err=True)
        raise typer.Exit(EX_REFUSED) from None
    p = _write_private(s, f"{_stamp()}-{target}-recall-k{k}.json", out)
    if as_json:
        typer.echo(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for r in out["rows"]:
            typer.echo(
                f"{r['id']} {r.get('lang') or '-'} {str(r.get('category') or '-'):20} rank={r['rank']}"
            )
        typer.echo(f"hit@{k}: {out['hit']}/{out['scored']} (negatives not scored); report {p}")
    raise typer.Exit(EX_FAIL if out["rate"] is not None and out["rate"] < min_rate else 0)


__all__ = ["migrate_app"]
