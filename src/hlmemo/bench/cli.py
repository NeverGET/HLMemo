"""``hlm bench``: benchmark a librarian model on the production path (W2f, D-017).

    hlm bench --suite v2 --profile openrouter-gpt6-luna --runs 1 --max-usd 0.5 --env-file .env
    hlm bench --suite v2 --model openai/gpt-6-luna --price-in 0.2 --price-out 0.75 \
        --pack docs/private/bench-v2
    hlm bench --suite v1 --profile openrouter --mode replay --cassette-dir tests/cassettes/w2a
    hlm bench --compare bench/results/A.json bench/results/B.json#openai/gpt-6-luna
    hlm bench rescore bench/results/20260923-194459-v2.json --gold adjusted --pack docs/private/bench-v2
    hlm bench leaderboard            # re-render eval/results/LEADERBOARD.md from leaderboard.json

Heavy imports happen inside the commands, so ``hlm --help`` stays fast.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

bench_app = typer.Typer(
    help="Benchmark a librarian model (production prompts, provider, redaction, --max-usd reservation).",
    invoke_without_command=True,
    no_args_is_help=False,
    rich_markup_mode=None,
)

EXIT_CAP = 2  # the --max-usd reservation stopped the run (a FAIL, never a skip)


def _load_env_file(path: str) -> None:
    """KEY=VALUE lines; existing environment wins; nothing is printed."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _default_out() -> Path:
    return Path("bench/results") if Path("bench").is_dir() else Path("hlm-bench-results")


def _stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")


def _progress(out: Any) -> None:
    it = out.item
    score = "-" if out.score is None else f"{out.score:.2f}"
    typer.echo(
        f"  {out.status:<15} {it.task}/{it.case_id} rep{it.rep} score={score} "
        f"{out.latency_ms if out.latency_ms is not None else '-'}ms ${float(out.cost_usd):.5f}",
        err=True,
    )


@bench_app.callback()
def bench(
    ctx: typer.Context,
    profile: Annotated[str | None, typer.Option(help="Profile file name (profiles/<name>.toml).")] = None,
    model: Annotated[str | None, typer.Option(help="Override the profile's model id.")] = None,
    reasoning: Annotated[
        str | None, typer.Option(help="Override the profile's reasoning object (JSON) or 'none'.")
    ] = None,
    price_in: Annotated[float | None, typer.Option(help="USD per 1M input tokens (for --model).")] = None,
    price_out: Annotated[float | None, typer.Option(help="USD per 1M output tokens (for --model).")] = None,
    suite: Annotated[str, typer.Option(help="v1 (T1-T4, production prompts) or v2 (T5-T12).")] = "v2",
    tasks: Annotated[str, typer.Option(help="Comma-separated subset (v1: placement,...; v2: T5,T9).")] = "",
    runs: Annotated[int, typer.Option(min=1, help="Repetitions per case.")] = 1,
    limit: Annotated[int, typer.Option(min=0, help="First K cases per task (smoke).")] = 0,
    max_usd: Annotated[float, typer.Option(help="Run cap, enforced by worst-case reservation.")] = 1.0,
    pack: Annotated[
        list[str] | None, typer.Option(help="v2: extra pack file or directory (repeatable).")
    ] = None,
    no_builtin: Annotated[
        bool, typer.Option("--no-builtin", help="v2: skip the bundled public packs.")
    ] = False,
    gold: Annotated[str, typer.Option(help="v2 gold: adjusted (default) or raw (D-066).")] = "adjusted",
    mode: Annotated[str, typer.Option(help="live | record | replay (strict cassettes).")] = "live",
    cassette_dir: Annotated[Path | None, typer.Option(help="Cassette directory (record/replay).")] = None,
    record_name: Annotated[str, typer.Option(help="Cassette file name when recording.")] = "bench",
    concurrency: Annotated[int, typer.Option(min=1, help="Calls in flight.")] = 4,
    calls_per_day: Annotated[int, typer.Option(min=1, help="$/month projection basis.")] = 60,
    budget: Annotated[
        str,
        typer.Option(
            help="db (default): reserve every attempt against the SHARED Postgres llm_budget, like the"
            " librarian; local: an in-process --max-usd cap that is NOT shared across runs."
        ),
    ] = "db",
    budget_dsn: Annotated[
        str | None, typer.Option(help="--budget db: the database (default HLM_DB_DSN / hlm.toml).")
    ] = None,
    env_file: Annotated[str | None, typer.Option(help="Load KEY=VALUE secrets without echoing.")] = None,
    out: Annotated[str | None, typer.Option(help="Result directory ('' = do not write).")] = None,
    compare: Annotated[
        tuple[str, str] | None,
        typer.Option(help="A B: result files (path.json[#model]) or profiles/models to run; McNemar."),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", help="No per-call progress on stderr.")] = False,
) -> None:
    """Run the bench (no subcommand), or --compare two runs."""
    if ctx.invoked_subcommand is not None:
        return
    from hlmemo.bench.engine import BenchConfig

    if env_file:
        _load_env_file(env_file)
    cfg = BenchConfig(
        suite=suite,
        profile=profile,
        model=model,
        reasoning=reasoning,
        price_in=price_in,
        price_out=price_out,
        tasks=[t.strip() for t in tasks.split(",") if t.strip()] or None,
        runs=runs,
        limit=limit,
        max_usd=max_usd,
        packs=list(pack or []),
        builtin=not no_builtin,
        gold=gold,
        mode=mode,
        cassette_dir=cassette_dir,
        record_name=record_name,
        concurrency=concurrency,
        calls_per_day=calls_per_day,
        budget=budget,
        budget_dsn=budget_dsn,
    )
    out_dir = _default_out() if out is None else (Path(out) if out.strip() else None)
    if compare and compare[0] is not None:
        raise typer.Exit(_compare(cfg, compare, out_dir, quiet))
    raise typer.Exit(_run_one(cfg, out_dir, quiet))


def _run_one(cfg: Any, out_dir: Path | None, quiet: bool) -> int:
    from hlmemo.bench.engine import run_bench, write_result
    from hlmemo.librarian.errors import LibrarianError

    try:
        res = asyncio.run(run_bench(cfg, progress=None if quiet else _progress))
    except (LibrarianError, ValueError) as exc:
        typer.echo(f"hlm bench: {exc}", err=True)
        return 1
    typer.echo(res.markdown, nl=False)  # stdout == the written .md, byte for byte
    if out_dir is not None:
        jp, mp = write_result(res, out_dir, _stamp())
        typer.echo(f"results -> {jp} , {mp}", err=True)
    typer.echo(f"spend ${res.spent_usd} (cap ${cfg.max_usd})", err=True)
    if res.aborted:
        typer.echo("FAIL: the --max-usd reservation stopped the run", err=True)
        return EXIT_CAP
    return 0


def _rows_for(spec: str, cfg: Any, quiet: bool) -> tuple[list[dict], dict, str]:
    from dataclasses import replace

    from hlmemo.bench import report
    from hlmemo.bench.engine import run_bench
    from hlmemo.config import load_profile

    if Path(spec.partition("#")[0]).is_file():
        rows, meta = report.load_rows(spec)
        return rows, meta, spec
    run_cfg = replace(cfg, profile=spec) if load_profile(spec) else replace(cfg, model=spec)
    res = asyncio.run(run_bench(run_cfg, progress=None if quiet else _progress))
    return res.rows, res.meta, spec


def _compare(cfg: Any, pair: tuple[str, str], out_dir: Path | None, quiet: bool) -> int:
    from hlmemo.bench import report

    rows_a, meta_a, name_a = _rows_for(pair[0], cfg, quiet)
    rows_b, meta_b, name_b = _rows_for(pair[1], cfg, quiet)
    suite = meta_a.get("suite") or cfg.suite
    cmp = report.compare(rows_a, rows_b, suite=suite)
    md = report.render_compare_md(cmp, name_a, name_b)
    typer.echo(md)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{_stamp()}-hlm-compare.md").write_text(md, encoding="utf-8")
    return 0


@bench_app.command("rescore")
def rescore(
    result: Annotated[str, typer.Argument(help="Saved result: path.json or path.json#model.")],
    gold: Annotated[str, typer.Option(help="raw | adjusted")] = "adjusted",
    pack: Annotated[
        list[str] | None, typer.Option(help="Extra v2 pack file/dir (e.g. the private pack).")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the summary JSON.")] = False,
) -> None:
    """Re-score a saved v2 result offline (no LLM) under raw or adjusted gold."""
    from hlmemo.bench import leaderboard, report, v2

    rows, meta = report.load_rows(result)
    packs = v2.load_packs(v2.default_packs() + v2.expand_pack_paths(pack or []), gold=gold)
    rescored, skipped = report.rescore_rows(rows, packs)
    if skipped["missing_case"]:
        typer.echo(
            f"hlm bench rescore: {skipped['missing_case']} rows reference cases outside the loaded packs"
            " (pass the private pack with --pack); refusing to report a silently partial score",
            err=True,
        )
        raise typer.Exit(1)
    summary = report.summarize(rescored, suite="v2")
    incomplete = leaderboard.coverage(rescored, packs, meta.get("reps") or 1, meta)
    meta = {**meta, "gold": gold if gold == "raw" else v2.overlay_version(), "mode": "rescore"}
    if as_json:
        typer.echo(json.dumps({"meta": meta, "incomplete": incomplete, "summary": summary}, indent=1))
    else:
        typer.echo(report.render_md(summary, meta), nl=False)
        if incomplete:
            typer.echo("INCOMPLETE: " + "; ".join(incomplete))


leaderboard_app = typer.Typer(
    help="The W-E leaderboard (eval/results/): append-only history, rules enforced.",
    invoke_without_command=True,
    no_args_is_help=False,
    rich_markup_mode=None,
)
bench_app.add_typer(leaderboard_app, name="leaderboard")
LB_PATH = Path("eval/results/leaderboard.json")


def _lb_write(path: Path, doc: dict) -> None:
    from hlmemo.bench import leaderboard

    leaderboard.save(path, doc)
    md = path.with_name("LEADERBOARD.md")
    md.write_text(leaderboard.render_md(doc), encoding="utf-8")
    typer.echo(f"rendered {md}", err=True)


@leaderboard_app.callback()
def leaderboard_render(
    ctx: typer.Context,
    path: Annotated[Path, typer.Option(help="leaderboard.json")] = LB_PATH,
) -> None:
    """Re-render LEADERBOARD.md from leaderboard.json (no subcommand)."""
    ctx.obj = path
    if ctx.invoked_subcommand is None:
        from hlmemo.bench import leaderboard

        _lb_write(path, leaderboard.load(path))


@leaderboard_app.command("seed-baseline")
def seed_baseline(
    ctx: typer.Context,
    baseline: Annotated[Path, typer.Argument(help="W-E baseline JSON (eval/baselines/phase0.json).")],
) -> None:
    """Create the retrieval boards from a baseline. Refused if any board exists (history is never
    rewritten; later iterations go through add-candidate)."""
    from hlmemo.bench import leaderboard

    path: Path = ctx.obj
    doc = leaderboard.load(path)
    try:
        boards = leaderboard.seed_baseline(doc, json.loads(baseline.read_text(encoding="utf-8")))
    except leaderboard.LeaderboardError as exc:
        typer.echo(f"refused: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"seeded {boards}", err=True)
    _lb_write(path, doc)


def _librarian_candidate(
    doc: dict, spec: str, gold: str, pack: list[str], run: str | None, commit: str | None, board: str | None
) -> list[str]:
    from hlmemo.bench import leaderboard, report, v1, v2

    rows, meta = report.load_rows(spec)
    suite = meta.get("suite", "v2")
    label = run or Path(spec.partition("#")[0]).stem
    notes = []
    golds = (["raw", "adjusted"] if gold == "both" else [gold]) if suite == "v2" else ["v1"]
    for g in golds:
        if suite == "v2":
            paths = v2.default_packs() + v2.expand_pack_paths(pack)
            packs = v2.load_packs(paths, gold=g)
            scored, skipped = report.rescore_rows(rows, packs)
            if skipped["missing_case"]:
                raise leaderboard.LeaderboardError(
                    [
                        f"{spec}: {skipped['missing_case']} rows reference cases outside the loaded packs"
                        " (pass the private pack with --pack)"
                    ]
                )
            hashes = leaderboard.pack_hashes(paths)
            gold_label = "raw" if g == "raw" else v2.overlay_version()
        else:
            packs = [(t, v1.load_fixture(t)) for t in v1.TASKS]
            scored = rows
            hashes = {v1.FIXTURE_FILES[t]: v1.fixture_sha256(t) for t in v1.TASKS}
            gold_label = "v1"
        incomplete = leaderboard.coverage(scored, packs, meta.get("reps") or 1, meta)
        ran = meta.get("pack_sha256")  # recorded at run time by hlm bench (absent in legacy files)
        if ran is not None and ran != hashes:
            incomplete.append("run-time pack hashes differ from the packs scored here")
        entry = leaderboard.librarian_entry(
            report.summarize(scored, suite=suite),
            meta,
            gold=gold_label,
            run=label,
            source=Path(spec.partition("#")[0]).name,
            commit=commit or meta.get("commit"),
            packs=hashes,
            incomplete=incomplete,
        )
        added = leaderboard.add_librarian(doc, board or f"bench_{suite}", entry)
        state = "ranked" if entry["complete"] else "NOT ranked: " + "; ".join(incomplete)
        notes.append(f"{label} gold {gold_label}: {'added' if added else 'unchanged'} ({state})")
    return notes


@leaderboard_app.command("add-candidate")
def add_candidate(
    ctx: typer.Context,
    result: Annotated[
        list[str] | None,
        typer.Option(help="Librarian: an hlm bench result (path.json) or bench/run.py path.json#model."),
    ] = None,
    retrieval: Annotated[
        Path | None, typer.Option(help="Retrieval: a candidate JSON (see hlmemo.bench.leaderboard).")
    ] = None,
    decision: Annotated[
        str | None, typer.Option(help="D-xxx that logs an exception to rule 1/2 (required to override).")
    ] = None,
    merge: Annotated[
        bool, typer.Option("--merge", help="Retrieval: this config is being merged (rule 2).")
    ] = False,
    confirmation: Annotated[
        bool, typer.Option("--confirmation", help="Retrieval: record a sealed-corpus confirmation (rule 4).")
    ] = False,
    gold: Annotated[str, typer.Option(help="Librarian v2: raw | adjusted | both.")] = "both",
    pack: Annotated[list[str] | None, typer.Option(help="Extra v2 pack file/dir (the private pack).")] = None,
    run: Annotated[str | None, typer.Option(help="Run label (default: the result file's stem).")] = None,
    commit: Annotated[str | None, typer.Option(help="Commit of the code that produced the run.")] = None,
    board: Annotated[str | None, typer.Option(help="Librarian board (default bench_<suite>).")] = None,
) -> None:
    """Append one iteration (retrieval) or run (librarian). Refused, with nothing written, when the
    W-E rules or the history rules do not hold."""
    from hlmemo.bench import leaderboard

    path: Path = ctx.obj
    doc = leaderboard.load(path)
    try:
        if retrieval is not None:
            cand = json.loads(retrieval.read_text(encoding="utf-8"))
            moved = leaderboard.add_retrieval_candidate(
                doc, cand, decision=decision, merge=merge, confirmation=confirmation, source=retrieval.name
            )
            typer.echo(f"appended {cand.get('label')!r}; new best on {moved or 'no corpus'}", err=True)
        for spec in result or []:
            for note in _librarian_candidate(doc, spec, gold, list(pack or []), run, commit, board):
                typer.echo(note, err=True)
    except leaderboard.LeaderboardError as exc:
        typer.echo("refused (nothing written):\n  - " + "\n  - ".join(exc.problems), err=True)
        raise typer.Exit(1) from None
    _lb_write(path, doc)


def main() -> None:  # python -m hlmemo.bench.cli
    bench_app(prog_name="hlm bench")


if __name__ == "__main__":
    sys.exit(main())
